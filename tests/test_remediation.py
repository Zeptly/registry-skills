"""Regression tests for the registry-local defect remediation (duplicate keys, unhashable entries, index
generation, output schemas, scaffolds, malformed input)."""
import json
import os
import shutil
import stat

import pytest
import yaml

from conftest import REPO, codes, edit_manifest, load, save
from zskill import registry_ops as ops
from zskill.bundle import (Bundle, BundleError, CanonicalizationError, RegistryError, YamlError, _StrictLoader,
                           load_yaml, load_yaml_text, walk_bundle)
from zskill.cli import main
from zskill.validate import Registry, validate

WR = "skills/research/web-research"
CAND = "candidates/integrations/github-pr-triage"


def issues(root, code=None):
    return [i for i in validate(root) if i.level == "error" and (code is None or i.code == code)]


def web_research(root):
    return next(b for b in Registry(root).bundles if b.rel == WR)


# ============================================================ 1. duplicate YAML keys ===
def test_duplicate_top_level_key_reports_file_key_path_and_lines(tmp_path):
    p = tmp_path / "doc.yaml"
    p.write_text("a: 1\nb: 2\na: 3\n")
    with pytest.raises(YamlError) as e:
        load_yaml(p)
    msg = str(e.value)
    assert str(p) in msg and "duplicate mapping key 'a'" in msg and "line 3" in msg and "first defined at line 1" in msg


def test_duplicate_nested_key_reports_full_path():
    text = "spec:\n  inputs:\n    - name: a\n      name: b\n"
    with pytest.raises(YamlError, match=r"spec\.inputs\[0\]\.name"):
        load_yaml_text(text, "m.yaml")


def test_duplicate_keys_detected_inside_flow_mappings_and_json():
    with pytest.raises(YamlError, match="duplicate mapping key 'k'"):
        load_yaml_text("x: {k: 1, k: 2}\n", "m.yaml")
    with pytest.raises(YamlError, match="duplicate mapping key"):
        load_yaml_text('{"a": 1, "a": 2}', "s.json")


def test_keys_equal_after_resolution_are_duplicates():
    with pytest.raises(YamlError, match="duplicate"):
        load_yaml_text("1: a\n1: b\n", "m.yaml")


def test_merge_key_overrides_are_not_duplicates():
    """Valid today, must stay valid: an explicit key overriding a merged one."""
    assert load_yaml_text("base: &b {k: 1, z: 0}\nchild: {<<: *b, k: 2}\n", "m.yaml") == {
        "base": {"k": 1, "z": 0}, "child": {"k": 2, "z": 0}}


def test_same_key_in_different_mappings_is_fine():
    assert load_yaml_text("a: {k: 1}\nb: {k: 2}\n- x\n".split("- x")[0], "m.yaml") == {"a": {"k": 1}, "b": {"k": 2}}


def test_parsing_of_valid_documents_is_unchanged():
    """Every YAML/JSON document in the repository parses exactly as the previous loader parsed it."""
    files = [p for d in ("schemas", "vocab", "skills", "candidates", "registry") for p in (REPO / d).rglob("*")
             if p.suffix in (".yaml", ".yml", ".json")]
    assert len(files) > 40
    for p in files:
        with open(p, encoding="utf-8") as fh:
            assert load_yaml(p) == yaml.load(fh, Loader=_StrictLoader), p


def test_empty_document_and_scalars_still_parse():
    assert load_yaml_text("", "e.yaml") is None
    assert load_yaml_text("123\n", "e.yaml") == 123


@pytest.mark.parametrize("rel,text,code", [
    (WR + "/manifest.yaml", None, "manifest-parse"),
    (WR + "/provenance/approval.yaml", "basis: a\nbasis: b\n", "attestation-parse"),
    (WR + "/evals/suite.yaml", "a: 1\na: 2\n", "eval-parse"),
    (CAND + "/provenance/stage.yaml", "stage: inspected\nstage: approved\n", "stage-parse"),
    (CAND + "/provenance/assessment.yaml", "verdict: a\nverdict: b\n", "assessment-parse"),
    ("registry/releases/web-research.yaml", "skill: a\nskill: b\n", "ledger-parse"),
])
def test_duplicate_keys_are_reported_by_validate_with_file_and_path(reg, rel, text, code):
    p = reg / rel
    if text is None:  # manifest: duplicate a nested key
        text = p.read_text().replace("  version: 1.0.0", "  version: 1.0.0\n  version: 1.0.1", 1)
    p.write_text(text)
    found = [i for i in validate(reg) if i.code == code]
    assert found and "duplicate mapping key" in found[0].msg and rel.split("/")[-1] in found[0].msg


def test_duplicate_key_in_skill_md_frontmatter(reg):
    p = reg / WR / "SKILL.md"
    p.write_text(p.read_text().replace("---\nname: web-research\n", "---\nname: web-research\nname: x\n", 1))
    found = issues(reg, "skillmd-frontmatter")
    assert found and "duplicate mapping key 'name'" in found[0].msg and "frontmatter" in found[0].msg


def test_duplicate_key_in_evidence_and_overlay(reg):
    (reg / WR / "provenance/evidence.yaml").write_text("kind: a\nkind: b\n")
    (reg / "registry/lifecycle").mkdir()
    (reg / "registry/lifecycle/web-research.yaml").write_text("id: a\nid: b\n")
    cs = {i.code for i in issues(reg)}
    assert {"evidence-parse", "lifecycle-parse"} <= cs


def test_duplicate_key_in_schema_file_is_a_controlled_error(reg):
    p = reg / "schemas/envelope.schema.json"
    p.write_text(p.read_text().replace('"kind":', '"kind": {}, "kind":', 1))
    with pytest.raises(RegistryError, match="duplicate mapping key"):
        Registry(reg)


# ================================================ 2. unhashable entries (symlinks, FIFOs, ...) ===
def _entries(tmp):
    return {
        "file-symlink": lambda d: (d / "examples/l.md").symlink_to("/etc/hostname"),
        "dir-symlink": lambda d: (d / "examples/dl").symlink_to("/etc"),
        "broken-symlink": lambda d: (d / "examples/broken.md").symlink_to("/nonexistent/target"),
        "fifo": lambda d: os.mkfifo(d / "examples/fifo.md"),
    }


@pytest.mark.parametrize("kind", ["file-symlink", "dir-symlink", "broken-symlink", "fifo"])
def test_seal_and_digest_refuse_unhashable_entries(reg, kind):
    b0 = web_research(reg)
    before = (b0.directory_seal(), b0.digest())
    _entries(reg)[kind](reg / WR)
    b = web_research(reg)
    for fn in (b.directory_seal, b.digest):
        with pytest.raises(BundleError) as e:
            fn()
        assert WR + "/examples/" in str(e.value) and "cannot compute seal/digest" in str(e.value)
    # the valid bundle hashes identically once the entry is gone again
    (reg / WR / "examples" / {"file-symlink": "l.md", "dir-symlink": "dl", "broken-symlink": "broken.md", "fifo": "fifo.md"}[kind]).unlink()
    b = web_research(reg)
    assert (b.directory_seal(), b.digest()) == before


@pytest.mark.parametrize("kind,code", [("file-symlink", "symlink"), ("dir-symlink", "symlink"), ("broken-symlink", "symlink"),
                                       ("fifo", "unsupported-entry")])
def test_validate_reports_unhashable_entries_explicitly(reg, kind, code):
    _entries(reg)[kind](reg / WR)
    cs = {i.code for i in issues(reg)}
    assert code in cs
    assert "release-mutated" not in cs and "attestation-stale" not in cs      # no misleading digest noise
    assert any(WR + "/examples/" in i.where for i in issues(reg, code))


def test_symlinked_bundle_directory_is_rejected(reg):
    real = reg / "outside-bundle"
    shutil.copytree(reg / WR, real)
    shutil.rmtree(reg / WR)
    (reg / WR).symlink_to(real)
    found = [i for i in issues(reg) if i.code == "symlink"]
    assert found and found[0].where == WR
    with pytest.raises(BundleError, match="web-research"):
        web_research(reg).digest()


def test_symlinked_domain_directory_is_rejected(reg):
    real = reg / "outside-domain"
    shutil.copytree(reg / "skills/analysis", real)
    shutil.rmtree(reg / "skills/analysis")
    (reg / "skills/analysis").symlink_to(real)
    found = [i for i in issues(reg) if i.code == "symlink"]
    assert found and found[0].where == "skills/analysis"


def test_symlinked_manifest_is_rejected(reg):
    m = reg / WR / "manifest.yaml"
    body = m.read_text()
    (reg / "elsewhere.yaml").write_text(body)
    m.unlink()
    m.symlink_to(reg / "elsewhere.yaml")
    assert "symlink" in {i.code for i in issues(reg)}


def test_unreadable_directory_is_reported_not_skipped(tmp_path, monkeypatch):
    d = tmp_path / "b"
    (d / "sub").mkdir(parents=True)
    real_walk = os.walk

    def walk(top, topdown=True, onerror=None, followlinks=False):
        if onerror:
            onerror(PermissionError(13, "Permission denied", str(d / "sub")))
        yield from real_walk(top, topdown, onerror, followlinks)
    monkeypatch.setattr(os, "walk", walk)
    assert ("sub", d / "sub", "unreadable") in walk_bundle(d)


def test_walk_bundle_classifies_every_entry(tmp_path):
    d = tmp_path / "b"
    (d / "x").mkdir(parents=True)
    (d / "x/f.md").write_text("f")
    (d / "x/l").symlink_to(d / "x/f.md")
    os.mkfifo(d / "x/p")
    assert [(r, k) for r, _, k in walk_bundle(d)] == [("x/f.md", "file"), ("x/l", "symlink"), ("x/p", "other")]
    assert stat.S_ISFIFO(os.lstat(d / "x/p").st_mode)


def test_valid_bundles_keep_their_published_hashes():
    reg = Registry(REPO)
    reg.run()
    canonical = [b for b in reg.bundles if b.tier == "skills"]
    assert len(canonical) == 6
    for b in canonical:
        rel = next(r for r in reg.ledgers[b.id]["releases"] if r["version"] == b.version)
        assert (b.digest(), b.directory_seal()) == (rel["digest"], rel["directory_seal"]), b.rel


def run_cli(argv):
    """main() result as (exit code, stderr text) whether it returns or exits with a message."""
    try:
        return main(argv), ""
    except SystemExit as e:                      # legacy message-style exits: printed by the interpreter, no traceback
        return (e.code if isinstance(e.code, int) else 1), str(e.code)


@pytest.mark.parametrize("argv", [["digest", "web-research"], ["release", "web-research"], ["resolve", "web-research"],
                                  ["index"], ["index", "--check"]])
def test_cli_reports_unhashable_bundle_without_traceback(reg, monkeypatch, capsys, argv):
    monkeypatch.chdir(reg)
    os.mkfifo(reg / WR / "examples/fifo.md")
    rc, msg = run_cli(argv)
    text = capsys.readouterr().err + msg
    assert rc in (1, 2) and "Traceback" not in text and "web-research" in text and "fifo.md" in text


# ====================================================================== 3. index generation ===
def test_index_generation_fails_instead_of_skipping_invalid_bundles(reg):
    (reg / WR / "manifest.yaml").write_text("not: a manifest\n")
    with pytest.raises(RegistryError) as e:
        ops.index(reg)
    msg = str(e.value)
    assert "cannot generate the production index" in msg and WR in msg and "manifest-schema" in msg


def test_index_generation_fails_on_duplicate_key_manifest(reg):
    p = reg / WR / "manifest.yaml"
    p.write_text(p.read_text().replace("kind: SkillBlueprint", "kind: SkillBlueprint\nkind: SkillBlueprint", 1))
    with pytest.raises(RegistryError, match="duplicate mapping key 'kind'"):
        ops.index(reg)


def test_index_generation_fails_on_unhashable_bundle(reg):
    os.mkfifo(reg / WR / "examples/fifo.md")
    with pytest.raises(RegistryError, match="unsupported-entry"):
        ops.index(reg)


def test_index_cli_leaves_existing_index_untouched_on_failure(reg, monkeypatch, capsys):
    monkeypatch.chdir(reg)
    before = (reg / "registry/index.json").read_bytes()
    (reg / WR / "manifest.yaml").write_text("not: a manifest\n")
    assert main(["index"]) == 2 and main(["index", "--check"]) == 2
    assert (reg / "registry/index.json").read_bytes() == before
    assert "cannot generate the production index" in capsys.readouterr().err


def test_namespaces_fail_independently(reg):
    ops.scaffold(reg, "research", "demo-echo", tier="synthetic")
    (reg / "synthetic/research/demo-echo/manifest.yaml").write_text("broken: true\n")
    assert len(ops.index(reg, "production")["entries"]) == 7          # production unaffected
    with pytest.raises(RegistryError, match="synthetic index"):
        ops.index(reg, "synthetic")
    ops.scaffold(reg, "research", "demo-2", tier="synthetic")
    shutil.rmtree(reg / "synthetic/research/demo-echo")
    assert [e["id"] for e in ops.index(reg, "synthetic")["entries"]] == ["demo-2"]


def test_semantically_invalid_but_schema_valid_bundle_is_still_indexed(reg):
    """Generation refuses to OMIT artifacts; semantic findings stay `zskill validate`'s job."""
    edit_manifest(reg, CAND, lambda m: m["spec"]["security_profile"].update(side_effects="destructive"))
    assert any(e["id"] == "github-pr-triage" for e in ops.index(reg)["entries"])


# =============================================================== 4. generated outputs validated ===
def _tighten(reg, schema, prop):
    p = reg / "schemas" / schema
    doc = json.loads(p.read_text())
    doc["required"].append(prop)
    p.write_text(json.dumps(doc))


def test_index_is_validated_against_its_schema_before_success(reg, monkeypatch, capsys):
    _tighten(reg, "registry-index.schema.json", "bogusField")
    with pytest.raises(RegistryError, match=r"(?s)registry-index\.schema\.json.*bogusField"):
        ops.index(reg)
    monkeypatch.chdir(reg)
    before = (reg / "registry/index.json").read_bytes()
    assert main(["index"]) == 2 and (reg / "registry/index.json").read_bytes() == before


def test_resolution_lock_is_validated_before_success(reg, monkeypatch, capsys):
    _tighten(reg, "resolution-lock.schema.json", "bogusField")
    with pytest.raises(RegistryError, match=r"(?s)resolution-lock\.schema\.json.*bogusField"):
        ops.resolve(reg, "competitive-intelligence")
    monkeypatch.chdir(reg)
    assert main(["resolve", "competitive-intelligence"]) == 2
    assert capsys.readouterr().out == ""                               # nothing printed as a "successful" lock


def test_real_outputs_validate_against_the_committed_schemas():
    from jsonschema import Draft202012Validator as V
    V(json.load(open(REPO / "schemas/registry-index.schema.json"))).validate(ops.index(REPO))
    V(json.load(open(REPO / "schemas/resolution-lock.schema.json"))).validate(ops.resolve(REPO, "competitive-intelligence"))


def test_two_index_builds_are_byte_identical_and_schema_valid(reg):
    a, b = ops.index_text(reg), ops.index_text(reg)
    assert a == b == (REPO / "registry/index.json").read_text()


# ================================================================================ 5. scaffolds ===
@pytest.mark.parametrize("tier", ["candidates", "synthetic"])
@pytest.mark.parametrize("name", ["scaf-thing", "scaf.dotted-thing"])
def test_scaffold_validates_clean(reg, tier, name):
    d = ops.scaffold(reg, "research", name, tier=tier)
    rel = d.relative_to(reg).as_posix()
    found = [str(i) for i in validate(reg) if i.level == "error" and i.where.startswith(rel)]
    assert found == []
    m = load(reg, rel + "/manifest.yaml")
    assert (m["spec"].get("markers") == {"namespace": "synthetic"}) == (tier == "synthetic")
    assert m["metadata"]["origin"]["type"] == "native"


def test_synthetic_scaffold_is_isolated_end_to_end_via_cli(reg, monkeypatch):
    monkeypatch.chdir(reg)
    assert main(["new", "research/cli-demo", "--synthetic"]) == 0
    assert main(["validate"]) == 0 and main(["index"]) == 0
    prod = json.loads((reg / "registry/index.json").read_text())
    syn = json.loads((reg / "registry/index.synthetic.json").read_text())
    assert "cli-demo" not in {e["id"] for e in prod["entries"]}
    assert [e["id"] for e in syn["entries"]] == ["cli-demo"] and main(["index", "--check"]) == 0


@pytest.mark.parametrize("domain", ["..", "nonexistent", "../skills", "research/../x", ""])
def test_scaffold_rejects_unknown_and_traversing_domains(reg, domain):
    with pytest.raises(SystemExit, match="unknown domain"):
        ops.scaffold(reg, domain, "x-thing")
    assert not (reg / "x-thing").exists()


# ====================================================================== 6. malformed input ===
BAD_INPUTS = {
    "empty": b"", "unclosed": b"[", "list": b"- a\n- b\n", "null": b"null\n", "scalar": b"123\n",
    "binary": b"\x00\xff\xfe\x80", "dup": b"a: 1\na: 2\n", "hugeint": b"a: 99999999999999999999999\n",
    "deep": b"[" * 3000 + b"]" * 3000 + b"\n", "tab": b"a:\n\t- b\n",
}
CORPUS_TARGETS = [
    WR + "/manifest.yaml", WR + "/SKILL.md", WR + "/evals/suite.yaml", WR + "/provenance/approval.yaml",
    CAND + "/provenance/stage.yaml", CAND + "/provenance/assessment.yaml", "registry/releases/web-research.yaml",
    "vocab/domains.yaml", "vocab/agent-classes.yaml", "vocab/capabilities.yaml", "schemas/envelope.schema.json",
    "schemas/skill-blueprint.schema.json", "schemas/registry-index.schema.json",
]


@pytest.mark.parametrize("target", CORPUS_TARGETS)
def test_malformed_files_never_cause_uncaught_exceptions(reg, monkeypatch, target):
    monkeypatch.chdir(reg)
    f = reg / target
    original = f.read_bytes()
    for name, data in BAD_INPUTS.items():
        f.write_bytes(data)
        # main() converts every controlled failure (RegistryError: broken schema/vocabulary file, etc.) into a
        # return code; an uncaught exception of any other type fails the test.
        rc = main(["validate"])
        assert rc in (0, 1, 2), (target, name, rc)
        if target.endswith("/manifest.yaml") or target.startswith("schemas/"):
            rc = main(["index", "--check"])
            assert rc in (0, 1, 2), (target, name, rc)
    f.write_bytes(original)


def test_malformed_evidence_overlay_and_reports_never_crash(reg):
    (reg / WR / "provenance/eval-reports").mkdir()
    (reg / "registry/lifecycle").mkdir()
    targets = [WR + "/provenance/evidence.yaml", WR + "/provenance/eval-reports/1.0.0.yaml", "registry/lifecycle/web-research.yaml"]
    for t in targets:
        for name, data in BAD_INPUTS.items():
            (reg / t).write_bytes(data)
            assert isinstance(validate(reg), list), (t, name)
        (reg / t).unlink()


@pytest.mark.parametrize("value,needle", [
    ("1.0e-7", "float 1e-07"), (".nan", "nan"), (".inf", "inf"), ("99999999999999999999", "integer"),
    ('"\\uD800"', "lone surrogate"), ("{1: a}", "is not a string"),
])
def test_canonicalization_failures_are_controlled_diagnostics(reg, value, needle):
    p = reg / WR / "manifest.yaml"
    p.write_text(p.read_text().replace("  tags:", f"  x: {{bad: {value}}}\n  tags:", 1) if value != "{1: a}"
                 else p.read_text().replace("  tags:", "  x: {bad: {1: a}}\n  tags:", 1))
    found = issues(reg, "canonicalization")
    assert found, [str(i) for i in issues(reg)]
    assert WR + "/manifest.yaml#$.spec.x.bad" in found[0].msg and needle in found[0].msg
    with pytest.raises(CanonicalizationError):
        web_research(reg).digest()


def test_canonicalization_failure_blocks_index_and_cli(reg, monkeypatch, capsys):
    p = reg / WR / "manifest.yaml"
    p.write_text(p.read_text().replace("  tags:", "  x: {bad: .nan}\n  tags:", 1))
    with pytest.raises(RegistryError, match="canonicalization"):
        ops.index(reg)
    monkeypatch.chdir(reg)
    assert main(["digest", "web-research"]) == 2
    assert "manifest.yaml#$.spec.x.bad" in capsys.readouterr().err


def test_invalid_exception_version_is_a_diagnostic_not_a_crash(reg):
    ap = load(reg, WR + "/provenance/approval.yaml")
    ap["exception"]["expiresOnVersion"] = "soon"
    save(reg, WR + "/provenance/approval.yaml", ap)
    assert "exception-version" in {i.code for i in issues(reg)}


def test_non_utf8_skill_md_and_manifest_are_diagnostics(reg):
    (reg / WR / "SKILL.md").write_bytes(b"\xff\xfe binary")
    f = issues(reg, "skillmd-frontmatter")
    assert f and "not valid UTF-8" in f[0].msg
    (reg / WR / "manifest.yaml").write_bytes(b"\xff\xfe")
    assert any("not valid UTF-8" in i.msg for i in issues(reg, "manifest-parse"))


def test_validate_json_output_survives_malformed_input(reg, monkeypatch, capsys):
    monkeypatch.chdir(reg)
    (reg / WR / "manifest.yaml").write_bytes(b"[")
    assert main(["validate", "--json"]) == 1
    out = json.loads(capsys.readouterr().out)
    assert any(i["code"] == "manifest-parse" for i in out)


def test_ledger_check_reports_malformed_ledgers(tmp_path):
    import subprocess
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "registry/releases").mkdir(parents=True)
    p = tmp_path / "registry/releases/x.yaml"
    p.write_text("skill: x\nreleases: []\n")
    g = ["git", "-C", str(tmp_path), "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run([*g, "add", "-A"], check=True)
    subprocess.run([*g, "commit", "-qm", "b"], check=True)
    p.write_text("skill: x\nskill: y\n")
    probs = ops.ledger_check(tmp_path, "HEAD")
    assert probs and "duplicate mapping key" in probs[0]
    p.write_text("- just\n- a list\n")
    assert "expected a mapping" in ops.ledger_check(tmp_path, "HEAD")[0]


def test_cli_turns_unreadable_repo_into_exit_2(tmp_path, monkeypatch, capsys):
    (tmp_path / "schemas").mkdir()
    (tmp_path / "schemas/envelope.schema.json").write_text("[")
    monkeypatch.chdir(tmp_path)
    assert main(["validate"]) == 2
    assert capsys.readouterr().err.startswith("error:")


def test_manifest_without_identity_is_reported_not_silently_skipped(reg):
    """Previously such a manifest produced no diagnostic at all and vanished from every report."""
    (reg / WR / "manifest.yaml").write_text("not: a manifest\n")
    found = [i for i in validate(reg) if i.where.startswith(WR) and i.level == "error"]
    assert found and all(i.code == "manifest-schema" for i in found)


@pytest.mark.parametrize("content", ["", "null\n", "123\n", "[]\n"])
def test_empty_or_non_mapping_records_are_errors_not_silent(reg, content):
    (reg / WR / "provenance/approval.yaml").write_text(content)
    cs = {i.code for i in issues(reg)}
    assert cs & {"attestation-parse", "approval-schema", "gate-approval"}, cs
    (reg / WR / "evals/suite.yaml").write_text(content)
    assert {"eval-schema", "eval-parse"} & {i.code for i in issues(reg)}


def test_index_survives_unreadable_approval_record_but_validate_reports_it(reg):
    (reg / WR / "provenance/approval.yaml").write_text("null\n")
    e = next(e for e in ops.index(reg)["entries"] if e["id"] == "web-research")
    assert e["extensions"]["skills"]["evidenceLevel"] == "unknown"
    assert issues(reg)
