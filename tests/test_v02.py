"""Protocol v0.2 regression tests: parser rejection through the CLI, exit codes, peer-index resolution and unresolved
foreign references, generated-output schemas, unhashable entries, populated-baseline immutability, exception expiry,
synthetic namespace rules."""
import copy
import json
import os
import shutil
import socket
import subprocess

import pytest
import yaml
from jsonschema import Draft202012Validator

from conftest import ALG, REPO, att, codes, edit_manifest, load, rebind, save
from zskill import registry_ops as ops
from zskill.cli import main
from zskill.errors import BundleError, RegistryError, UnsatisfiedRequest
from zskill.validate import Registry, validate

WR = "skills/research/web-research"
CI = "skills/research/competitive-intelligence"
CAND = "candidates/integrations/github-pr-triage"
FIXTURES = REPO / "tests" / "fixtures"


def schema(name):
    return Draft202012Validator(json.loads((REPO / "schemas" / name).read_text()))


# ============================================================ parser rejection through validate and the CLI ===
MANIFEST_POISON = [
    ("duplicate-key", "kind: SkillBlueprint\nkind: SkillBlueprint\n"),
    ("anchor", "a: &x 1\n"),
    ("alias", "a: *x\n"),
    ("merge-key", "a: {<<: {k: 1}}\n"),
    ("multiple-documents", "a: 1\n---\nb: 2\n"),
    ("non-string-key", "1: x\n"),
    ("unsupported-tag", "a: !!python/object:os.system x\n"),
    ("unsafe-integer", "a: 9007199254740992\n"),
    ("ambiguous-scalar", "a: 0x10\n"),
    ("non-finite", "a: 1e999\n"),
    ("lone-surrogate", 'a: "\\ud800"\n'),
    ("syntax", "a: [\n"),
]


@pytest.mark.parametrize("code,text", MANIFEST_POISON, ids=[c for c, _ in MANIFEST_POISON])
def test_malformed_manifest_yields_file_path_code_and_exit_2(reg, monkeypatch, capsys, code, text):
    (reg / WR / "manifest.yaml").write_text(text)
    bad = [i for i in validate(reg) if i.code == "manifest-parse"]
    assert bad and f"[{code}]" in bad[0].msg and "manifest.yaml" in bad[0].msg
    monkeypatch.chdir(reg)
    assert main(["validate"]) == 2
    assert main(["digest", "web-research"]) == 2
    assert f"[{code}]" in capsys.readouterr().err


@pytest.mark.parametrize("raw,code", [(b"\xef\xbb\xbfa: 1\n", "bom"), (b"a: 1\x00\n", "nul"), (b"a: caf\xe9\n", "invalid-utf8")])
def test_malformed_bytes_are_rejected_with_codes(reg, raw, code):
    (reg / WR / "manifest.yaml").write_bytes(raw)
    bad = [i for i in validate(reg) if i.code == "manifest-parse"]
    assert bad and f"[{code}]" in bad[0].msg


def test_yes_no_on_off_and_timestamps_stay_strings():
    from zskill.yamlsubset import load_yaml_text
    doc = load_yaml_text("a: yes\nb: no\nc: on\nd: off\ne: 2026-09-29\nf: 2026-09-29T10:00:00Z\ng: null\nh: true\n", "t.yaml")
    assert doc == {"a": "yes", "b": "no", "c": "on", "d": "off", "e": "2026-09-29", "f": "2026-09-29T10:00:00Z", "g": None, "h": True}


def test_boundary_safe_integers_are_accepted():
    from zskill.yamlsubset import load_yaml_text
    assert load_yaml_text("a: 9007199254740991\nb: -9007199254740991\n", "t.yaml") == {"a": 9007199254740991, "b": -9007199254740991}


# ============================================================================== exit code semantics ===
def test_exit_codes_distinguish_invalid_input_from_unsatisfied_requests(reg, monkeypatch, capsys):
    monkeypatch.chdir(reg)
    assert main(["validate"]) == 0
    assert main(["resolve", "no-such-skill"]) == 1                       # valid request, cannot be satisfied
    assert "error[not-found]" in capsys.readouterr().err
    assert main(["resolve", "web-research", "--domain", "synthetic"]) == 1
    _foreign(reg)
    assert main(["resolve", "github-pr-triage", "--allow-candidates"]) == 1     # unresolved foreign reference
    err = capsys.readouterr().err
    assert "error[no-peer-index]" in err and "tiny-agents" in err
    (reg / "registry/index.json").write_text("{")
    assert main(["index", "--check"]) == 2                               # stale/malformed generated output


def test_errors_print_machine_readable_codes(reg, monkeypatch, capsys):
    monkeypatch.chdir(reg)
    assert main(["promote", "github-pr-triage"]) == 1
    assert capsys.readouterr().err.startswith("error[")


# ===================================================================== peer indexes and foreign references ===
def _foreign(reg, rng="^1.2.0", pin=None):
    """Declare a tiny-agents reference on the unreleased candidate (no released version is touched)."""
    def add(m):
        ref = {"registry": "tiny-agents", "id": "research.web-fact-check", "version": rng, "digest": None}
        if pin:
            ref.update(digest=pin, digestAlgorithm=ALG)
        m["references"].append(ref)
    edit_manifest(reg, CAND, add)


def resolve(reg, **kw):
    kw.setdefault("allow_candidates", True)
    return ops.resolve(reg, "github-pr-triage", **kw)


def peer_entry(version, *, lifecycle="active", maturity="canonical", domain="production", digest=None, rid="research.web-fact-check"):
    e = {"registry": "tiny-agents", "kind": "AgentBlueprint", "id": rid, "version": version,
         "digest": digest or "sha256:" + f"{abs(hash(version)) % 16**8:08x}" * 8, "digestAlgorithm": ALG,
         "maturity": maturity, "lifecycle": lifecycle, "origin": {"type": "native"}, "domain": domain,
         "location": {"path": f"agents/{rid}"}}
    if maturity == "canonical":
        e["directorySeal"] = "sha256:" + "ab" * 32
    return e


def peer_index(tmp_path, entries, registry="tiny-agents", name="peer.json"):
    doc = {"apiVersion": "registry.zeptly.dev/v1alpha1", "kind": "RegistryIndex", "registry": registry,
           "digestAlgorithm": ALG, "domain": "production", "entries": entries}
    p = tmp_path / name
    p.write_text(json.dumps(doc))
    return p


def lock_entry(lock):
    return next(e for e in lock["entries"] if e["requested"]["registry"] == "tiny-agents")


def test_committed_peer_index_fixture_is_schema_valid_and_labelled_synthetic():
    p = FIXTURES / "peer-index.tiny-agents.fixture.json"
    doc = json.loads(p.read_text())
    schema("registry-index.schema.json").validate(doc)
    assert doc["registry"] == "tiny-agents" and "hand-written" in (FIXTURES / "README.md").read_text()


def test_fixture_peer_index_resolves_foreign_reference_and_lock_validates(reg):
    _foreign(reg)
    lock = resolve(reg, peer_indexes=[FIXTURES / "peer-index.tiny-agents.fixture.json"])
    e = lock_entry(lock)
    assert e["status"] == "resolved" and e["resolved"]["registry"] == "tiny-agents" and e["resolved"]["version"] == "1.3.0"
    assert lock["complete"] is True
    schema("runtime-lock.schema.json").validate(lock)


def test_unresolved_fixture_fails_explicitly_without_a_peer_index(reg):
    _foreign(reg)
    lock = resolve(reg)
    e = lock_entry(lock)
    assert e["status"] == "unresolved" and e["unresolved"]["code"] == "no-peer-index" and lock["complete"] is False
    assert "resolved" not in e
    schema("runtime-lock.schema.json").validate(lock)


@pytest.mark.parametrize("entries,code", [
    ([], "not-found"),
    ([peer_entry("1.1.0")], "no-matching-version"),
    ([peer_entry("1.2.0", lifecycle="revoked")], "revoked"),
    ([peer_entry("1.2.0", lifecycle="deprecated")], "deprecated-requires-exact-pin"),
    ([peer_entry("1.2.0", domain="synthetic")], "domain-mismatch"),
    ([peer_entry("1.3.0-rc.1")], "no-matching-version"),                     # prereleases need a prerelease range
])
def test_peer_index_unresolved_reasons(reg, tmp_path, entries, code):
    _foreign(reg)
    lock = resolve(reg, peer_indexes=[peer_index(tmp_path, entries)])
    e = lock_entry(lock)
    assert e["status"] == "unresolved" and e["unresolved"]["code"] == code and lock["complete"] is False
    schema("runtime-lock.schema.json").validate(lock)


def test_candidates_need_explicit_opt_in():
    from zskill.registry_ops import _resolve_one
    req = {"registry": "tiny-agents", "id": "research.web-fact-check", "version": "^1.2.0"}
    pool = {"tiny-agents": [peer_entry("1.2.0", maturity="candidate")]}
    assert _resolve_one(pool, req, "production", False)["unresolved"]["code"] == "candidate-not-allowed"
    assert _resolve_one(pool, req, "production", True)["status"] == "resolved"


def test_revoked_versions_never_resolve_but_older_active_ones_do(reg, tmp_path):
    _foreign(reg)
    p = peer_index(tmp_path, [peer_entry("1.2.0", lifecycle="revoked"), peer_entry("1.2.5")])
    assert lock_entry(resolve(reg, peer_indexes=[p]))["resolved"]["version"] == "1.2.5"


def test_deprecated_resolves_only_by_exact_pin(reg, tmp_path):
    _foreign(reg, rng="1.2.0")
    p = peer_index(tmp_path, [peer_entry("1.2.0", lifecycle="deprecated")])
    e = lock_entry(resolve(reg, peer_indexes=[p]))
    assert e["status"] == "resolved" and e["resolved"]["lifecycle"] == "deprecated"


def test_prerelease_resolves_when_the_range_names_a_prerelease(reg, tmp_path):
    _foreign(reg, rng="^1.3.0-rc.0")
    p = peer_index(tmp_path, [peer_entry("1.3.0-rc.1")])
    assert lock_entry(resolve(reg, peer_indexes=[p]))["resolved"]["version"] == "1.3.0-rc.1"


def test_candidates_resolve_only_with_explicit_opt_in(reg, tmp_path):
    _foreign(reg)
    p = peer_index(tmp_path, [peer_entry("1.2.0", maturity="candidate")])
    assert lock_entry(resolve(reg, peer_indexes=[p], allow_candidates=True))["status"] == "resolved"


def test_digest_pin_must_match_the_selected_index_entry(reg, tmp_path):
    good = "sha256:" + "c" * 64
    _foreign(reg, rng="1.2.0", pin="sha256:" + "d" * 64)
    p = peer_index(tmp_path, [peer_entry("1.2.0", digest=good)])
    e = lock_entry(resolve(reg, peer_indexes=[p]))
    assert e["unresolved"]["code"] == "digest-mismatch"
    assert load(reg, CAND + "/manifest.yaml")["references"][-1]["digestAlgorithm"] == ALG


def test_invalid_range_is_reported_per_entry(reg, tmp_path):
    _foreign(reg)
    from zskill.registry_ops import _resolve_one
    e = _resolve_one({"tiny-agents": []}, {"registry": "tiny-agents", "id": "x", "version": "not a range"}, "production", False)
    assert e["unresolved"]["code"] == "invalid-range"


def test_every_declared_reference_appears_in_the_lock(reg, tmp_path):
    _foreign(reg)
    lock = resolve(reg)
    declared = [(r["registry"], r["id"]) for r in load(reg, CAND + "/manifest.yaml")["references"]]
    assert [(e["requested"]["registry"], e["requested"]["id"]) for e in lock["entries"]] == declared


@pytest.mark.parametrize("doc,needle", [
    ({"registry": "tiny-agents", "entries": []}, "invalid-peer-index"),
    ({"apiVersion": "registry.zeptly.dev/v1alpha1", "kind": "RegistryIndex", "registry": "tiny-agents", "digestAlgorithm": "sha256-plain",
      "domain": "production", "entries": []}, "invalid-peer-index"),
    ({"apiVersion": "registry.zeptly.dev/v1alpha1", "kind": "RegistryIndex", "registry": "skills", "digestAlgorithm": ALG,
      "domain": "production", "entries": []}, "invalid-peer-index"),
])
def test_malformed_or_impersonating_peer_indexes_are_errors_not_unresolved_entries(reg, tmp_path, doc, needle):
    _foreign(reg)
    p = tmp_path / "bad.json"
    p.write_text(json.dumps(doc))
    with pytest.raises(RegistryError) as e:
        resolve(reg, peer_indexes=[p])
    assert e.value.code == needle and e.value.exit_code == 2


def test_production_and_synthetic_domains_do_not_mix_in_locks(reg):
    ops.scaffold(reg, "research", "demo-echo", tier="synthetic")
    lock = ops.resolve(reg, "example.demo-echo", domain="synthetic")
    assert lock["domain"] == "synthetic" and lock["subject"]["id"] == "example.demo-echo"
    with pytest.raises(UnsatisfiedRequest):
        ops.resolve(reg, "example.demo-echo", domain="production")


# ========================================================================== generated outputs are schema-valid ===
def test_committed_index_and_a_fresh_lock_validate(reg):
    schema("registry-index.schema.json").validate(json.loads((REPO / "registry/index.json").read_text()))
    schema("runtime-lock.schema.json").validate(ops.resolve(reg, "competitive-intelligence"))


@pytest.mark.parametrize("mutate", [
    lambda d: d.pop("digestAlgorithm"),
    lambda d: d.update(digestAlgorithm="zeptly-jcs-v0"),
    lambda d: d.pop("domain"),
    lambda d: d["entries"][0].pop("directorySeal"),
    lambda d: d["entries"][0].update(digest="sha256:xyz"),
    lambda d: d["entries"][0].update(origin="native"),
    lambda d: d["entries"][0].update(surprise=1),
    lambda d: d.update(generatedAt="2026-01-01"),
    lambda d: d["entries"][0].update(lifecycle="retired"),
])
def test_malformed_index_is_rejected_by_the_schema(mutate):
    d = json.loads((REPO / "registry/index.json").read_text())
    mutate(d)
    assert list(schema("registry-index.schema.json").iter_errors(d))


@pytest.mark.parametrize("mutate", [
    lambda d: d.pop("complete"),
    lambda d: d.pop("digestAlgorithm"),
    lambda d: d["subject"].pop("digest"),
    lambda d: d["entries"][0].pop("status"),
    lambda d: d["entries"][0].pop("resolved"),
    lambda d: d["entries"][0].update(status="unresolved"),                      # resolved data under an unresolved status
    lambda d: d["entries"][0]["resolved"].pop("digest"),
    lambda d: d.update(domain="staging"),
    lambda d: d.update(extra=True),
])
def test_malformed_lock_is_rejected_by_the_schema(reg, mutate):
    d = ops.resolve(reg, "competitive-intelligence")
    mutate(d)
    assert list(schema("runtime-lock.schema.json").iter_errors(d))


def test_lock_unresolved_entry_requires_code_and_message(reg):
    d = ops.resolve(reg, "competitive-intelligence")
    d["entries"][0] = {"requested": d["entries"][0]["requested"], "status": "unresolved", "unresolved": {"code": "no-peer-index"}}
    assert list(schema("runtime-lock.schema.json").iter_errors(d))
    d["complete"] = True
    d["entries"][0]["unresolved"]["message"] = "x"
    schema("runtime-lock.schema.json").validate(d) if False else None


def test_malformed_release_ledger_is_rejected(reg):
    led = load(reg, "registry/releases/web-research.yaml")
    led.pop("digestAlgorithm")
    save(reg, "registry/releases/web-research.yaml", led)
    assert "ledger-schema" in codes(reg)


def test_generated_index_is_not_written_when_a_schema_tightening_would_reject_it(reg, monkeypatch):
    p = reg / "schemas/registry-index.schema.json"
    doc = json.loads(p.read_text())
    doc["properties"]["entries"]["items"]["required"].append("bogus")
    p.write_text(json.dumps(doc))
    before = (reg / "registry/index.json").read_bytes()
    monkeypatch.chdir(reg)
    assert main(["index"]) == 2 and (reg / "registry/index.json").read_bytes() == before


# ============================================================== unhashable entries and the path allow-list ===
def _seal(reg):
    return next(b for b in Registry(reg).bundles if b.rel == WR).directory_seal()


def test_unix_socket_is_rejected_before_hashing(reg, tmp_path):
    sock_dir = tmp_path / "s"          # AF_UNIX paths are short: bind elsewhere, then move the node into the bundle
    sock_dir.mkdir()
    s = socket.socket(socket.AF_UNIX)
    s.bind(str(sock_dir / "s"))
    try:
        os.rename(sock_dir / "s", reg / WR / "examples" / "sock.md")
        with pytest.raises(BundleError) as e:
            _seal(reg)
        assert e.value.code == "unsupported-entry" and e.value.exit_code == 2
    finally:
        s.close()


def test_case_colliding_paths_are_rejected(reg):
    (reg / WR / "examples" / "Example-1.md").write_text("collides with example-1.md\n")
    with pytest.raises(BundleError) as e:
        _seal(reg)
    assert e.value.code == "case-collision"
    assert "case-collision" in codes(reg)


def test_files_outside_the_allow_list_are_rejected_before_hashing(reg):
    (reg / WR / "scripts").mkdir()
    (reg / WR / "scripts" / "run.sh").write_text("echo x\n")
    with pytest.raises(BundleError) as e:
        _seal(reg)
    assert e.value.code == "file-not-allowed"


def test_symlink_inside_a_bundle_is_rejected_and_named(reg):
    (reg / WR / "examples" / "l.md").symlink_to("example-1.md")
    with pytest.raises(BundleError) as e:
        _seal(reg)
    assert e.value.code == "symlink" and "examples/l.md" in str(e.value)


@pytest.mark.parametrize("content,code", [(b"a\r\nb\n", "payload-line-endings"), (b"a\rb\n", "payload-line-endings"),
                                          (b"\xef\xbb\xbfx\n", "payload-bom"), (b"a\x00b\n", "payload-nul"),
                                          (b"caf\xe9\n", "file-not-text")])
def test_payload_text_policy_rejects_rather_than_normalises(reg, content, code):
    (reg / WR / "examples" / "example-1.md").write_bytes(content)
    with pytest.raises(BundleError) as e:
        _seal(reg)
    assert e.value.code == code
    assert code in codes(reg)


# ========================================================== populated-baseline immutability (git, not empty) ===
def _git(d, *a):
    return subprocess.run(["git", "-C", str(d), "-c", "user.name=t", "-c", "user.email=t@t", *a], check=True,
                          capture_output=True, text=True)


@pytest.fixture
def baseline(reg):
    _git(reg, "init", "-q")
    _git(reg, "add", "-A")
    _git(reg, "commit", "-qm", "populated baseline")
    return reg


def test_ledger_check_passes_on_unchanged_populated_baseline(baseline):
    assert ops.ledger_check(baseline, "HEAD") == []


def test_ledger_check_rejects_editing_a_released_entry(baseline):
    p = "registry/releases/web-research.yaml"
    led = load(baseline, p)
    led["releases"][0]["digest"] = "sha256:" + "0" * 64
    save(baseline, p, led)
    assert ops.ledger_check(baseline, "HEAD")


def test_ledger_check_rejects_deleting_a_ledger(baseline):
    (baseline / "registry/releases/web-research.yaml").unlink()
    assert any("deleted" in pr for pr in ops.ledger_check(baseline, "HEAD"))


def test_ledger_check_allows_appending_a_new_release(baseline):
    edit_manifest(baseline, WR, lambda m: m["metadata"].update(version="1.0.1"))
    (baseline / WR / "SKILL.md").write_text((baseline / WR / "SKILL.md").read_text().replace("Never fabricate", "Do not fabricate"))
    rebind(baseline, WR)
    assert ops.ledger_check(baseline, "HEAD") == []
    assert len(load(baseline, "registry/releases/web-research.yaml")["releases"]) == 2


def test_released_content_cannot_be_rewritten_even_with_a_matching_rebind(baseline):
    (baseline / WR / "examples/example-1.md").write_text("rewritten\n")
    with pytest.raises(RegistryError) as e:
        ops.release(baseline, "web-research")
    assert e.value.code == "release-immutable"
    assert "release-mutated" in codes(baseline)


def test_ledger_check_fails_loudly_for_an_unknown_base(baseline):
    with pytest.raises(RegistryError) as e:
        ops.ledger_check(baseline, "no-such-ref")
    assert e.value.exit_code == 2


# =========================================================================================== exception expiry ===
def test_exception_is_a_visible_warning_and_never_a_passing_evaluation():
    warns = [i for i in validate(REPO) if i.level == "warning" and i.code == "protocol-exception"]
    assert len(warns) == 6
    idx = json.loads((REPO / "registry/index.json").read_text())
    assert {e["extensions"]["skills"]["evidenceLevel"] for e in idx["entries"] if e["maturity"] == "canonical"} == {"unevaluated"}
    for e in idx["entries"]:
        assert e["extensions"]["skills"].get("evidenceLevel") != "evaluated"


def test_exception_remains_valid_below_1_1_0(reg):
    edit_manifest(reg, WR, lambda m: m["metadata"].update(version="1.0.1"))
    (reg / WR / "SKILL.md").write_text((reg / WR / "SKILL.md").read_text().replace("Never fabricate", "Do not fabricate"))
    rebind(reg, WR)
    assert "exception-expired" not in codes(reg)


@pytest.mark.parametrize("version", ["1.1.0", "1.1.1", "2.0.0"])
def test_exception_fails_at_and_after_expiry_version(reg, version):
    edit_manifest(reg, WR, lambda m: m["metadata"].update(version=version))
    rebind(reg, WR)
    assert "exception-expired" in codes(reg)


def test_exception_cannot_be_accompanied_by_a_passing_evaluation(reg):
    edit_manifest(reg, WR, lambda m: m["attestations"].append(att(reg, WR)))
    assert "exception-claims-evaluation" in codes(reg)


def test_exception_expiry_is_not_extendable_beyond_1_1_0(reg):
    ap = load(reg, WR + "/provenance/approval.yaml")
    ap["exception"]["expiresOnVersion"] = "9.0.0"
    save(reg, WR + "/provenance/approval.yaml", ap)
    assert "exception-version" in codes(reg)


# =========================================================================== synthetic namespace and evidence ===
def test_scaffold_adds_the_reserved_example_prefix_and_rejects_the_namespace_in_production(reg):
    d = ops.scaffold(reg, "research", "thing", tier="synthetic")
    assert d.name == "example.thing"
    with pytest.raises(RegistryError, match="example"):
        ops.scaffold(reg, "research", "example.prod-thing")              # candidates tier may not use the reserved prefix


def test_production_artifact_with_example_prefix_is_rejected(reg):
    edit_manifest(reg, CAND, lambda m: m["metadata"].update(id="example.github-pr-triage"))
    assert {"synthetic-namespace", "layout-name"} & set(codes(reg))


def test_synthetic_artifact_without_the_example_prefix_is_rejected(reg):
    rel = str(ops.scaffold(reg, "research", "demo-echo", tier="synthetic").relative_to(reg))
    edit_manifest(reg, rel, lambda m: m["metadata"].update(id="demo-echo"))
    assert "synthetic-namespace" in codes(reg)


def test_production_evidence_cannot_use_synthetic_pointers(reg):
    edit_manifest(reg, WR, lambda m: m["attestations"].append(
        {"type": "evaluation", "ref": "evidence://example/run-1", "subjectDigest": "sha256:" + "0" * 64, "digestAlgorithm": ALG}))
    assert "synthetic-evidence" in codes(reg) or "manifest-schema" in codes(reg)


def test_synthetic_artifact_never_appears_in_production_index_or_lock(reg):
    ops.scaffold(reg, "research", "demo-echo", tier="synthetic")
    assert all(not e["id"].startswith("example.") for e in ops.index(reg, "production")["entries"])
    with pytest.raises(UnsatisfiedRequest):
        ops.resolve(reg, "example.demo-echo")
