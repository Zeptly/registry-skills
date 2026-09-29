import copy
import subprocess
import sys

import yaml

from conftest import REPO, codes, edit_manifest
from zskill import registry_ops as ops
from zskill.validate import Registry

CI = "skills/research/competitive-intelligence"
WR = "skills/research/web-research"
CR = "skills/engineering/code-review"


def test_repository_is_valid():
    errs = [i for i in Registry(REPO).run() if i.level == "error"]
    assert not errs, "\n".join(map(str, errs))


def test_index_is_current():
    assert (REPO / "registry/index.json").read_text() == ops.index_text(REPO)


def test_digest_stable_and_status_independent(reg):
    b = ops.find(Registry(reg), "zsk.web-research")
    d = b.digest()
    edit_manifest(reg, WR, lambda m: m.update(status="deprecated", deprecation={"reason": "testing"}))
    assert ops.find(Registry(reg), "zsk.web-research").digest() == d


def test_released_version_is_immutable(reg):
    (reg / WR / "SKILL.md").write_text((reg / WR / "SKILL.md").read_text() + "\nextra line\n")
    assert "release-mutated" in codes(reg)


def test_provenance_dir_does_not_change_digest(reg):
    d = ops.find(Registry(reg), "zsk.web-research").digest()
    (reg / WR / "provenance" / "notes.md").write_text("new evidence note")
    assert ops.find(Registry(reg), "zsk.web-research").digest() == d


def test_release_refuses_mutated_version(reg):
    (reg / WR / "SKILL.md").write_text((reg / WR / "SKILL.md").read_text() + "\nx\n")
    try:
        ops.release(reg, "zsk.web-research")
        assert False, "should refuse"
    except SystemExit as e:
        assert "immutable" in str(e)


def test_security_change_requires_major(reg):
    def widen(m):
        m["version"] = "1.1.0"
        m["security"]["permissions"].append({"scope": "web:public", "access": "read", "reason": "another read reason"})
    edit_manifest(reg, WR, widen)
    (reg / WR / "provenance/approval.yaml").write_text(
        (reg / WR / "provenance/approval.yaml").read_text().replace("@1.0.0", "@1.1.0").replace("expires_on_version: 1.1.0", "expires_on_version: 1.2.0"))
    ops.release(reg, "zsk.web-research")
    assert "semver-security" in codes(reg)


def test_contract_change_requires_minor(reg):
    def add_input(m):
        m["version"] = "1.0.1"
        m["inputs"].append({"name": "extra", "type": "string", "description": "new input"})
    edit_manifest(reg, WR, add_input)
    (reg / WR / "provenance/approval.yaml").write_text(
        (reg / WR / "provenance/approval.yaml").read_text().replace("@1.0.0", "@1.0.1"))
    ops.release(reg, "zsk.web-research")
    assert "semver-contract" in codes(reg)


def test_layout_and_names(reg):
    edit_manifest(reg, WR, lambda m: m.update(name="other-name"))
    c = codes(reg)
    assert "layout-name" in c and "skillmd-mismatch" in c


def test_unknown_vocab(reg):
    def bad(m):
        m["compatibility"]["agent_classes"].append("mystery")
        m["requires"]["capabilities"].append({"id": "cap.made.up"})
    edit_manifest(reg, WR, bad)
    c = codes(reg)
    assert {"vocab-agent-class", "vocab-capability"} <= set(c)


def test_destructive_requires_hitl_and_class(reg):
    def bad(m):
        m["security"]["side_effects"] = "destructive"
    edit_manifest(reg, CR, bad)
    c = codes(reg)
    assert {"sec-destructive-class", "sec-destructive-hitl", "sec-destructive-ops"} <= set(c)


def test_network_must_be_declared(reg):
    edit_manifest(reg, WR, lambda m: m["security"].pop("network"))
    assert "sec-network-missing" in codes(reg)


def test_secret_detected(reg):
    (reg / WR / "examples/leak.md").write_text("token = ghp_" + "a" * 36)
    assert "secret" in codes(reg)


def test_secret_placeholder_allowed(reg):
    (reg / WR / "examples/ok.md").write_text("Authorization: Bearer <token>")
    assert "secret" not in codes(reg)


def test_missing_required_section(reg):
    p = reg / WR / "SKILL.md"
    p.write_text(p.read_text().replace("## Guardrails", "## Notes"))
    assert "skillmd-section" in codes(reg)


def test_dependency_cycle(reg):
    def dep(m):
        m["dependencies"] = [{"id": "zsk.competitive-intelligence", "version": "^1.0.0"}]
    edit_manifest(reg, WR, dep)
    p = reg / WR / "SKILL.md"
    p.write_text(p.read_text() + "\nzsk.competitive-intelligence\n")
    assert "dep-cycle" in codes(reg)


def test_dependency_unresolved_and_version(reg):
    def bad(m):
        m["dependencies"][0]["version"] = "^2.0.0"
        m["dependencies"].append({"id": "zsk.nope", "version": "^1.0.0"})
    edit_manifest(reg, CI, bad)
    c = codes(reg)
    assert {"dep-version", "dep-unresolved"} <= set(c)


def test_composition_must_be_visible_in_procedure(reg):
    p = reg / CI / "SKILL.md"
    p.write_text(p.read_text().replace("zsk.source-evaluation", "source evaluation"))
    assert "dep-unreferenced" in codes(reg)


def test_privilege_hiding_rejected(reg):
    """A composite may not declare a narrower security envelope than its children."""
    def hide(m):
        m["security"]["side_effects"] = "none"
        m["security"]["permissions"] = []
        m["security"]["network"] = {"egress": "none"}
    edit_manifest(reg, CI, hide)
    c = set(codes(reg))
    assert {"env-effects", "env-permission", "env-network"} <= c


def test_canonical_cannot_depend_on_candidate(reg):
    import shutil
    # make web-research (canonical) depend on the candidate skill
    def dep(m):
        m["dependencies"] = [{"id": "zsk.github-pr-triage", "version": "^0.1.0", "role": "prerequisite"}]
    edit_manifest(reg, WR, dep)
    assert "dep-tier" in codes(reg)


def test_unreviewed_external_cannot_be_approved(reg):
    cand = "candidates/integrations/github-pr-triage"
    edit_manifest(reg, cand, lambda m: m.update(status="approved"))
    assert "prov-unreviewed" in codes(reg)


def test_external_verdict_blocks_progress(reg):
    cand = "candidates/integrations/github-pr-triage"
    edit_manifest(reg, cand, lambda m: m.update(status="candidate"))
    assert "prov-verdict" in codes(reg)


def test_zep_generalised_requires_compute_evidence(reg):
    cand = "candidates/integrations/github-pr-triage"
    edit_manifest(reg, cand, lambda m: m["provenance"].update(origin="zep-generalised"))
    assert "prov-zep-evidence" in codes(reg)


def test_eval_report_binding_and_threshold(reg):
    d = ops.find(Registry(reg), "zsk.web-research")
    digest = d.digest()
    import hashlib
    suite = "sha256:" + hashlib.sha256((reg / WR / "evals/suite.yaml").read_bytes()).hexdigest()
    report = {"schema": "zeptly.eval-report/v1", "skill_ref": "zsk.web-research@1.0.0", "skill_digest": digest,
              "suite_digest": suite, "runner": {"name": "test", "version": "0"}, "executed_at": "2026-09-29",
              "summary": {"cases": 4, "passed": 4, "pass_rate": 1.0}}
    (reg / WR / "provenance/eval-reports").mkdir()
    (reg / WR / "provenance/eval-reports/1.0.0.yaml").write_text(yaml.safe_dump(report))
    (reg / WR / "provenance/approval.yaml").write_text(yaml.safe_dump({
        "schema": "zeptly.approval/v1", "skill_ref": "zsk.web-research@1.0.0", "approved_by": ["@a"],
        "approved_at": "2026-09-29", "basis": "eval-report", "eval_report": "eval-reports/1.0.0.yaml"}))
    assert codes(reg) == []
    # bound to different content => rejected
    report["skill_digest"] = "sha256:" + "0" * 64
    (reg / WR / "provenance/eval-reports/1.0.0.yaml").write_text(yaml.safe_dump(report))
    assert "report-digest" in codes(reg)
    report["skill_digest"] = digest
    report.update(summary={"cases": 4, "passed": 2, "pass_rate": 0.5})
    (reg / WR / "provenance/eval-reports/1.0.0.yaml").write_text(yaml.safe_dump(report))
    assert "report-threshold" in codes(reg)


def test_waiver_expires(reg):
    p = reg / WR / "provenance/approval.yaml"
    p.write_text(p.read_text().replace("expires_on_version: 1.1.0", "expires_on_version: 1.0.0"))
    assert "waiver-expired" in codes(reg)


def test_safety_case_required_for_moderate(reg):
    p = reg / CR / "evals/suite.yaml"
    s = yaml.safe_load(p.read_text())
    s["cases"] = [c for c in s["cases"] if c.get("kind") not in ("safety", "adversarial")]
    p.write_text(yaml.safe_dump(s))
    assert "eval-safety" in codes(reg) or "eval-schema" in codes(reg)


def test_duplicate_id(reg):
    import shutil
    shutil.copytree(reg / WR, reg / "skills/research/web-research-copy")
    edit_manifest(reg, "skills/research/web-research-copy", lambda m: m.update(name="web-research-copy"))
    assert "duplicate-id" in codes(reg)


def test_promote_flow(reg):
    cand = "candidates/integrations/github-pr-triage"
    def ready(m):
        m["status"] = "approved"
        m["provenance"]["trust_tier"] = "reviewed-third-party"
        m["provenance"]["sources"][0]["ref"] = "v0.0.0-test"
        m["provenance"]["assessment"] = "provenance/assessment.yaml"
        m["version"] = "1.0.0"
    edit_manifest(reg, cand, ready)
    a = reg / cand / "provenance/assessment.yaml"
    a.write_text(a.read_text().replace("needs-more-inspection", "proceed-with-restrictions"))
    (reg / cand / "provenance/approval.yaml").write_text(yaml.safe_dump({
        "schema": "zeptly.approval/v1", "skill_ref": "zsk.github-pr-triage@1.0.0", "approved_by": ["@a"],
        "approved_at": "2026-09-29", "basis": "waiver",
        "waiver": {"reason": "test waiver reason", "expires_on_version": "1.1.0"}}))
    assert codes(reg) == []
    before = ops.find(Registry(reg), "zsk.github-pr-triage").digest()
    ops.promote(reg, "zsk.github-pr-triage")
    b = ops.find(Registry(reg), "zsk.github-pr-triage")
    assert b.tier == "skills" and b.status == "active" and b.digest() == before
    assert codes(reg) == []


def test_promote_refuses_unapproved(reg):
    try:
        ops.promote(reg, "zsk.github-pr-triage")
        assert False
    except SystemExit as e:
        assert "approved" in str(e)


def test_resolve_pins_closure():
    r = ops.resolve(REPO, "zsk.competitive-intelligence")
    ids = {s["ref"].split("@")[0] for s in r["skills"]}
    assert ids == {"zsk.competitive-intelligence", "zsk.web-research", "zsk.source-evaluation", "zsk.structured-comparison"}
    assert all(s["digest"].startswith("sha256:") for s in r["skills"])


def test_ledger_append_only(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "registry/releases").mkdir(parents=True)
    led = {"schema": "zeptly.ledger/v1", "skill": "zsk.x", "releases": [
        {"version": "1.0.0", "digest": "sha256:" + "a" * 64, "contract_digest": "sha256:" + "b" * 64,
         "security_digest": "sha256:" + "c" * 64, "released_at": "2026-01-01"}]}
    p = tmp_path / "registry/releases/zsk.x.yaml"
    p.write_text(yaml.safe_dump(led))
    env = ["git", "-C", str(tmp_path), "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run([*env, "add", "-A"], check=True)
    subprocess.run([*env, "commit", "-qm", "base"], check=True)
    assert ops.ledger_check(tmp_path, "HEAD") == []
    led2 = copy.deepcopy(led)
    led2["releases"].append({**led["releases"][0], "version": "1.1.0"})
    p.write_text(yaml.safe_dump(led2))
    assert ops.ledger_check(tmp_path, "HEAD") == []  # appending is fine
    led3 = copy.deepcopy(led)
    led3["releases"][0]["digest"] = "sha256:" + "d" * 64
    p.write_text(yaml.safe_dump(led3))
    assert ops.ledger_check(tmp_path, "HEAD")  # rewriting is not


def test_scaffold_produces_valid_candidate(reg):
    ops.scaffold(reg, "research", "new-thing")
    # scaffold is a starting point: only TODO-driven failures are expected, never structural crashes
    from zskill.validate import validate
    issues = [i for i in validate(reg) if "new-thing" in i.where]
    assert not any(i.code in ("manifest-parse", "layout-name", "layout-domain", "layout-status") for i in issues)


def test_cli_validate_exit_codes(reg, monkeypatch):
    monkeypatch.chdir(reg)
    from zskill.cli import main
    assert main(["validate"]) == 0
    assert main(["validate", "--strict"]) == 1  # bootstrap waivers are warnings
    (reg / WR / "SKILL.md").write_text("broken")
    assert main(["validate"]) == 1
