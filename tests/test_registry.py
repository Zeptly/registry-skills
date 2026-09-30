import copy
import hashlib
import json
import subprocess

import pytest
import yaml

from conftest import REPO, codes, edit_manifest, load, rebind, save
from zskill import registry_ops as ops
from zskill.validate import Registry

CI = "skills/research/competitive-intelligence"
WR = "skills/research/web-research"
CR = "skills/engineering/code-review"
CAND = "candidates/integrations/github-pr-triage"


def digest(root, sid):
    return ops.find(Registry(root), sid).digest()


# ---------------------------------------------------------------- repository ---
def test_repository_is_valid():
    errs = [i for i in Registry(REPO).run() if i.level == "error"]
    assert not errs, "\n".join(map(str, errs))


def test_indexes_are_current():
    assert (REPO / "registry/index.json").read_text() == ops.index_text(REPO)
    assert not (REPO / "registry/index.synthetic.json").exists()


def test_seed_exceptions_are_explicit_and_preserved():
    """Decision: the six seeds stay canonical ONLY under an explicit, temporary protocol exception."""
    for sid in ("web-research", "source-evaluation", "structured-comparison", "competitive-intelligence",
                "code-review", "structured-data-extraction"):
        b = ops.find(Registry(REPO), sid)
        ap = yaml.safe_load((b.path / "provenance/approval.yaml").read_text())
        assert ap["basis"] == "protocol-exception"
        assert ap["exception"]["rule"] == "promotion.required-evaluations" and ap["exception"]["expiresOnVersion"] == "1.1.0"
        assert ap["exception"]["reason"].startswith("Registry bootstrap seed skill; suite is structurally validated")
    assert "protocol-exception" in codes(REPO, "warning")  # CI-visible
    idx = json.loads((REPO / "registry/index.json").read_text())
    assert {e["extensions"]["skills"]["evidenceLevel"] for e in idx["entries"] if e["maturity"] == "canonical"} == {"unevaluated"}


def test_candidate_not_promoted_and_verdict_preserved():
    b = ops.find(Registry(REPO), "github-pr-triage")
    assert b.tier == "candidates" and b.stage() == "inspected"
    a = yaml.safe_load((b.path / "provenance/assessment.yaml").read_text())
    assert a["verdict"] == "needs-more-inspection"


# -------------------------------------------------------------- envelope / schema ---
def test_envelope_required_fields(reg):
    edit_manifest(reg, WR, lambda m: m.pop("attestations"))
    assert "manifest-schema" in codes(reg)


def test_wrong_api_version_and_kind(reg):
    edit_manifest(reg, WR, lambda m: m.update(apiVersion="registry.zeptly.dev/v9"))
    assert "manifest-schema" in codes(reg)
    edit_manifest(reg, WR, lambda m: m.update(apiVersion="registry.zeptly.dev/v1alpha1", kind="TinyAgentBlueprint"))
    assert "manifest-schema" in codes(reg)


def test_registry_must_be_skills(reg):
    edit_manifest(reg, WR, lambda m: m["metadata"].update(registry="tiny-agents"))
    assert "manifest-schema" in codes(reg)


def test_id_name_layout_and_frontmatter(reg):
    edit_manifest(reg, WR, lambda m: m["metadata"].update(id="other-name"))
    c = codes(reg)
    assert "layout-name" in c and "skillmd-mismatch" in c


# ------------------------------------------- maturity / origin / lifecycle independence ---
def test_maturity_must_match_location(reg):
    edit_manifest(reg, WR, lambda m: m["metadata"].update(maturity="candidate"))
    assert "maturity-location" in codes(reg)


def test_candidate_needs_stage_canonical_forbids_it(reg):
    (reg / CAND / "provenance/stage.yaml").unlink()
    assert "stage-missing" in codes(reg)
    (reg / CAND / "provenance/stage.yaml").write_text("stage: nonsense\n")
    assert "stage-invalid" in codes(reg)
    (reg / WR / "provenance/stage.yaml").write_text("stage: approved\n")
    assert "stage-canonical" in codes(reg)


def test_maturity_origin_lifecycle_are_independent_fields(reg):
    """Each can vary without touching the others (and none changes the digest)."""
    d0 = digest(reg, "web-research")
    ops.set_lifecycle(reg, "web-research", "1.0.0", "deprecated", "superseded in test")
    b = ops.find(Registry(reg), "web-research")
    assert (b.meta["maturity"], b.meta["origin"]["type"], b.meta["lifecycle"]) == ("canonical", "native", "deprecated")
    assert b.digest() == d0
    assert codes(reg) == []


def test_digest_excludes_only_governance_state_and_version(reg):
    d0 = digest(reg, "web-research")
    edit_manifest(reg, WR, lambda m: m["attestations"].append({"type": "evaluation", "ref": "evidence://x/1", "subjectDigest": d0}))
    edit_manifest(reg, WR, lambda m: m["metadata"].update(lifecycle="active", version="1.0.7"))
    (reg / WR / "provenance/notes.md").write_text("evidence note")
    (reg / WR / "CHANGELOG.md").write_text("changes")
    assert digest(reg, "web-research") == d0


@pytest.mark.parametrize("mutate", [
    lambda m: m["metadata"]["origin"].update(type="upstream-seed"),                    # identity: origin
    lambda m: m["metadata"].update(id="web-research-x"),                              # identity: id
    lambda m: m["spec"].update(title="Different Title"),                              # spec
    lambda m: m["references"].append({"registry": "tiny-agents", "id": "x.y", "version": "^1.0.0", "digest": None}),  # references
    lambda m: m["provenance"].update(createdAt="2027-01-01T00:00:00Z"),               # provenance
    lambda m: m["security"].update(classification="moderate"),                        # security classification
    lambda m: m["security"]["capabilities"].append("cap.extra.thing"),                # security capabilities
])
def test_digest_includes_identity_spec_references_provenance_security(reg, mutate):
    def at(root):
        return next(b for b in Registry(root).bundles if b.rel == WR).digest()
    d0 = at(reg)
    edit_manifest(reg, WR, mutate)
    assert at(reg) != d0


def test_directory_seal_is_separate_and_covers_payload_only(reg):
    b = ops.find(Registry(reg), "web-research")
    seal, dig = b.directory_seal(), b.digest()
    edit_manifest(reg, WR, lambda m: m["spec"].update(title="Different Title"))
    b = ops.find(Registry(reg), "web-research")
    assert b.directory_seal() == seal and b.digest() != dig          # manifest change: digest only
    (reg / WR / "provenance/notes.md").write_text("x")
    (reg / WR / "CHANGELOG.md").write_text("x")
    assert ops.find(Registry(reg), "web-research").directory_seal() == seal
    (reg / WR / "examples/example-1.md").write_text("changed payload\n")
    assert ops.find(Registry(reg), "web-research").directory_seal() != seal
    led = load(reg, "registry/releases/web-research.yaml")["releases"][0]
    assert led["directory_seal"] == seal and led["digest"] == dig


def test_release_mutated_reports_seal_or_digest(reg):
    (reg / WR / "examples/example-1.md").write_text("changed payload\n")
    assert "release-mutated" in codes(reg)


def test_line_endings_do_not_change_digest(reg):
    d0 = digest(reg, "web-research")
    for p in (reg / WR).rglob("*.md"):
        p.write_bytes(p.read_bytes().replace(b"\n", b"\r\n"))
    assert digest(reg, "web-research") == d0
    for p in (reg / WR).rglob("*.md"):
        p.write_bytes(p.read_bytes().replace(b"\r\n", b"\r"))
    assert digest(reg, "web-research") == d0


def test_lifecycle_mirror_must_match_overlay(reg):
    edit_manifest(reg, WR, lambda m: m["metadata"].update(lifecycle="deprecated"))
    assert "lifecycle-mirror" in codes(reg)


def test_lifecycle_overlay_revoked_is_terminal(reg):
    ops.set_lifecycle(reg, "web-research", "1.0.0", "revoked", "unsafe procedure found")
    with pytest.raises(SystemExit):
        ops.set_lifecycle(reg, "web-research", "1.0.0", "active", "undo")
    p = "registry/lifecycle/web-research.yaml"
    ov = load(reg, p)
    ov["events"].append({"version": "1.0.0", "state": "active", "at": "2026-12-01", "reason": "sneaky reinstatement"})
    save(reg, p, ov)
    assert "lifecycle-revoked-terminal" in codes(reg)


def test_deprecated_dependency_warns_revoked_errors_and_resolution_skips_revoked(reg):
    ops.set_lifecycle(reg, "source-evaluation", "1.0.0", "deprecated", "being replaced soon")
    assert "ref-deprecated" in codes(reg, "warning")
    ops.set_lifecycle(reg, "source-evaluation", "1.0.0", "revoked", "unsafe procedure found")
    assert "ref-revoked" in codes(reg)
    with pytest.raises(SystemExit):
        ops.resolve(reg, "competitive-intelligence")


def test_lifecycle_overlays_are_append_only(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "registry/lifecycle").mkdir(parents=True)
    ov = {"apiVersion": "registry.zeptly.dev/v1alpha1", "kind": "LifecycleOverlay", "registry": "skills", "id": "x",
          "events": [{"version": "1.0.0", "state": "deprecated", "at": "2026-10-01", "reason": "old thing"}]}
    p = tmp_path / "registry/lifecycle/x.yaml"
    p.write_text(yaml.safe_dump(ov))
    g = ["git", "-C", str(tmp_path), "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run([*g, "add", "-A"], check=True)
    subprocess.run([*g, "commit", "-qm", "base"], check=True)
    assert ops.ledger_check(tmp_path, "HEAD") == []
    ov["events"][0]["state"] = "active"
    p.write_text(yaml.safe_dump(ov))
    assert ops.ledger_check(tmp_path, "HEAD")


def test_origin_evolution_pairing(reg):
    edit_manifest(reg, WR, lambda m: m["metadata"]["origin"].update(type="evolved"))
    assert "origin-evolution" in codes(reg)


def test_generalised_evolution_requires_compute_evidence(reg):
    def ev(m):
        m["metadata"]["origin"] = {"type": "evolved", "evolution": {"kind": "generalised", "sourceRefs": []}}
    edit_manifest(reg, CAND, ev)
    assert "prov-generalised-evidence" in codes(reg)


# ------------------------------------------------------- immutability / digests ---
def test_released_version_is_immutable(reg):
    (reg / WR / "SKILL.md").write_text((reg / WR / "SKILL.md").read_text() + "\nextra line\n")
    assert "release-mutated" in codes(reg)


def test_release_refuses_mutated_version(reg):
    (reg / WR / "SKILL.md").write_text((reg / WR / "SKILL.md").read_text() + "\nx\n")
    with pytest.raises(SystemExit, match="immutable"):
        ops.release(reg, "web-research")


def test_security_change_requires_major(reg):
    def widen(m):
        m["metadata"]["version"] = "1.1.0"
        m["spec"]["security_profile"]["permissions"].append({"scope": "web:public", "access": "read", "reason": "another read reason"})
    edit_manifest(reg, WR, widen)
    rebind(reg, WR)
    assert "semver-security" in codes(reg)


def test_classification_change_counts_as_security_change(reg):
    def up(m):
        m["metadata"]["version"] = "1.0.1"
        m["security"]["classification"] = "moderate"
    edit_manifest(reg, WR, up)
    rebind(reg, WR)
    assert "semver-security" in codes(reg)


def test_contract_change_requires_minor(reg):
    def add_input(m):
        m["metadata"]["version"] = "1.0.1"
        m["spec"]["inputs"].append({"name": "extra", "type": "string", "description": "new input"})
    edit_manifest(reg, WR, add_input)
    rebind(reg, WR)
    assert "semver-contract" in codes(reg)


def test_patch_bump_for_prose_only_change_is_fine(reg):
    p = reg / WR / "SKILL.md"
    p.write_text(p.read_text().replace("Never fabricate", "Do not fabricate"))
    edit_manifest(reg, WR, lambda m: m["metadata"].update(version="1.0.1"))
    rebind(reg, WR)
    c = codes(reg)
    assert "semver-security" not in c and "semver-contract" not in c and "release-mutated" not in c


def test_resolution_by_version_plus_digest_for_old_and_new(reg):
    lock = ops.resolve(reg, "competitive-intelligence")
    assert {r["id"] for r in lock["resolved"]} == {"competitive-intelligence", "web-research", "source-evaluation", "structured-comparison"}
    assert all(r["digest"].startswith("sha256:") for r in lock["resolved"])
    old = ops.find(Registry(reg), "web-research").digest()
    edit_manifest(reg, WR, lambda m: m["metadata"].update(version="1.0.1"))
    (reg / WR / "SKILL.md").write_text((reg / WR / "SKILL.md").read_text().replace("Never fabricate", "Do not fabricate"))
    rebind(reg, WR)
    assert ops.resolve(reg, "web-research", "1.0.0")["root"]["digest"] == old  # historical version stays resolvable
    assert ops.resolve(reg, "web-research", "^1.0.0")["root"]["version"] == "1.0.1"


# --------------------------------------------------------------- references ---
def test_reference_shape_is_structured(reg):
    def bad(m):
        m["references"][0].pop("registry")
    edit_manifest(reg, CI, bad)
    assert "manifest-schema" in codes(reg)


def test_digest_only_with_exact_version(reg):
    edit_manifest(reg, CI, lambda m: m["references"][0].update(digest="sha256:" + "0" * 64))
    assert "ref-digest-range" in codes(reg)


def test_pinned_reference_digest_must_match_registry(reg):
    def pin(m):
        m["references"][0].update(version="1.0.0", digest="sha256:" + "0" * 64)
    edit_manifest(reg, CI, pin)
    rebind(reg, CI, release=False)
    assert "ref-digest" in codes(reg)
    good = ops.find(Registry(reg), "web-research").digest()
    edit_manifest(reg, CI, lambda m: m["references"][0].update(digest=good))
    assert "ref-digest" not in codes(reg)


def test_other_registry_references_validate_structurally_only(reg):
    """No cross-repository network access: unknown registries are accepted if well-formed."""
    def add(m):
        m["references"].append({"registry": "tiny-agents", "id": "research.web-fact-check", "version": "^1.2.0", "digest": None})
    edit_manifest(reg, CAND, add)
    assert not {"ref-unresolved", "manifest-schema"} & set(codes(reg))
    edit_manifest(reg, CAND, lambda m: m["references"][-1].update(id="Bad Id"))
    assert "manifest-schema" in codes(reg)


def test_references_and_composition_must_agree(reg):
    edit_manifest(reg, CI, lambda m: m["spec"]["composition"].pop())
    assert "ref-composition" in codes(reg)


def test_dependency_cycle(reg):
    def dep(m):
        m["references"] = [{"registry": "skills", "id": "competitive-intelligence", "version": "^1.0.0", "digest": None}]
        m["spec"]["composition"] = [{"id": "competitive-intelligence", "role": "composes"}]
    edit_manifest(reg, WR, dep)
    p = reg / WR / "SKILL.md"
    p.write_text(p.read_text() + "\n`skills:competitive-intelligence`\n")
    assert "ref-cycle" in codes(reg)


def test_unresolved_and_unsatisfied_reference(reg):
    def bad(m):
        m["references"][0]["version"] = "^2.0.0"
        m["references"].append({"registry": "skills", "id": "nope", "version": "^1.0.0", "digest": None})
        m["spec"]["composition"].append({"id": "nope"})
    edit_manifest(reg, CI, bad)
    assert {"ref-version", "ref-unresolved"} <= set(codes(reg))


def test_composition_must_be_visible_in_procedure(reg):
    p = reg / CI / "SKILL.md"
    p.write_text(p.read_text().replace("skills:source-evaluation", "source evaluation"))
    assert "ref-unreferenced" in codes(reg)


def test_privilege_hiding_rejected(reg):
    def hide(m):
        m["spec"]["security_profile"].update(side_effects="none", permissions=[], network={"egress": "none"})
    edit_manifest(reg, CI, hide)
    assert {"env-effects", "env-permission", "env-network"} <= set(codes(reg))


def test_canonical_cannot_depend_on_candidate(reg):
    def dep(m):
        m["references"] = [{"registry": "skills", "id": "github-pr-triage", "version": "^0.1.0", "digest": None}]
        m["spec"]["composition"] = [{"id": "github-pr-triage", "role": "prerequisite"}]
    edit_manifest(reg, WR, dep)
    assert "ref-tier" in codes(reg)


# ------------------------------------------------------------- attestations ---
def test_stale_attestation_fails(reg):
    p = reg / WR / "examples/example-1.md"
    p.write_text(p.read_text() + "\nchanged\n")
    c = codes(reg)
    assert "attestation-stale" in c and "release-mutated" in c


def test_stale_attestation_in_candidate_too(reg):
    p = reg / CAND / "SKILL.md"
    edit_manifest(reg, CAND, lambda m: m["attestations"].append(
        {"type": "evaluation", "ref": "evidence://x/1", "subjectDigest": digest(reg, "github-pr-triage")}))
    assert "attestation-stale" not in codes(reg)
    p.write_text(p.read_text() + "\nedit\n")
    assert "attestation-stale" in codes(reg)


def test_attestation_bundle_ref_cannot_escape(reg):
    d = digest(reg, "web-research")
    edit_manifest(reg, WR, lambda m: m["attestations"].append({"type": "evaluation", "ref": "bundle:../../README.md", "subjectDigest": d}))
    assert "attestation-ref" in codes(reg)


def test_attestation_ref_forms(reg):
    d = digest(reg, "web-research")
    edit_manifest(reg, WR, lambda m: m["attestations"].append({"type": "evaluation", "ref": "https://example.com/x", "subjectDigest": d}))
    assert "manifest-schema" in codes(reg)


def test_canonical_requires_governance_approval(reg):
    edit_manifest(reg, WR, lambda m: m["security"].update(approvals=[]))
    assert "gate-approval" in codes(reg)


def test_eval_report_binding_and_threshold(reg):
    d = digest(reg, "web-research")
    suite = "sha256:" + hashlib.sha256((reg / WR / "evals/suite.yaml").read_bytes()).hexdigest()
    report = {"apiVersion": "registry.zeptly.dev/v1alpha1", "kind": "EvaluationReport",
              "subject": {"registry": "skills", "id": "web-research", "version": "1.0.0", "digest": d},
              "suiteDigest": suite, "runner": {"name": "test", "version": "0"}, "executedAt": "2026-09-29T00:00:00Z",
              "summary": {"cases": 4, "passed": 4, "passRate": 1.0}}
    (reg / WR / "provenance/eval-reports").mkdir()
    rp = WR + "/provenance/eval-reports/1.0.0.yaml"
    save(reg, rp, report)
    save(reg, WR + "/provenance/approval.yaml", {
        "apiVersion": "registry.zeptly.dev/v1alpha1", "kind": "Approval",
        "subject": {"registry": "skills", "id": "web-research", "version": "1.0.0", "digest": d},
        "approvedBy": ["@a"], "approvedAt": "2026-09-29", "basis": "evaluation",
        "evaluationRef": "bundle:provenance/eval-reports/1.0.0.yaml"})
    edit_manifest(reg, WR, lambda m: m["attestations"].append(
        {"type": "evaluation", "ref": "bundle:provenance/eval-reports/1.0.0.yaml", "subjectDigest": d}))
    assert codes(reg) == []
    report["subject"]["digest"] = "sha256:" + "0" * 64
    save(reg, rp, report)
    assert "report-digest" in codes(reg)
    report["subject"]["digest"] = d
    report["summary"] = {"cases": 4, "passed": 2, "passRate": 0.5}
    save(reg, rp, report)
    assert "report-threshold" in codes(reg)


def test_protocol_exception_expires(reg):
    ap = load(reg, WR + "/provenance/approval.yaml")
    ap["exception"]["expiresOnVersion"] = "1.0.0"
    save(reg, WR + "/provenance/approval.yaml", ap)
    assert "exception-expired" in codes(reg)


def test_exception_requires_block_and_evaluation_forbids_it(reg):
    ap = load(reg, WR + "/provenance/approval.yaml")
    ap.pop("exception")
    save(reg, WR + "/provenance/approval.yaml", ap)
    assert {"approval-schema", "approval-exception"} & set(codes(reg))


def test_high_classification_needs_security_reviewer(reg):
    edit_manifest(reg, CR, lambda m: m["security"].update(classification="high"))
    rebind(reg, CR, release=False)
    assert "approval-security" in codes(reg)


# ------------------------------------------------------ candidates / promotion ---
def test_candidate_version_must_exceed_canonical(reg):
    import shutil
    shutil.copytree(reg / WR, reg / "candidates/research/web-research")
    (reg / "candidates/research/web-research/provenance/stage.yaml").write_text("stage: drafted\n")
    edit_manifest(reg, "candidates/research/web-research", lambda m: (
        m["metadata"].update(maturity="candidate", version="0.9.0"), m["security"].update(approvals=[])))
    assert "candidate-version" in codes(reg)
    edit_manifest(reg, "candidates/research/web-research", lambda m: m["metadata"].update(version="1.1.0"))
    assert "candidate-version" not in codes(reg)


def test_duplicate_identity(reg):
    import shutil
    shutil.copytree(reg / WR, reg / "skills/research/web-research-copy")
    edit_manifest(reg, "skills/research/web-research-copy", lambda m: m["metadata"].update(id="web-research-copy"))
    assert "duplicate-id" not in codes(reg)  # distinct id
    shutil.copytree(reg / WR, reg / "skills/analysis/web-research")
    edit_manifest(reg, "skills/analysis/web-research", lambda m: m["spec"].update(domain="analysis"))
    assert "duplicate-id" in codes(reg)


def test_unreviewed_external_cannot_be_approved(reg):
    (reg / CAND / "provenance/stage.yaml").write_text("stage: approved\n")
    assert "prov-unreviewed" in codes(reg)


def test_external_verdict_blocks_progress(reg):
    (reg / CAND / "provenance/stage.yaml").write_text("stage: drafted\n")
    assert "prov-verdict" in codes(reg)


def _make_promotable(reg):
    (reg / CAND / "provenance/stage.yaml").write_text("stage: approved\n")

    def ready(m):
        m["spec"]["trust"]["tier"] = "reviewed-third-party"
        m["provenance"]["sourceRefs"][0]["ref"] = "v0.0.0-test"
        m["metadata"]["version"] = "1.0.0"
    edit_manifest(reg, CAND, ready)
    a = reg / CAND / "provenance/assessment.yaml"
    a.write_text(a.read_text().replace("needs-more-inspection", "proceed-with-restrictions"))
    d = digest(reg, "github-pr-triage")
    save(reg, CAND + "/provenance/approval.yaml", {
        "apiVersion": "registry.zeptly.dev/v1alpha1", "kind": "Approval",
        "subject": {"registry": "skills", "id": "github-pr-triage", "version": "1.0.0", "digest": d},
        "approvedBy": ["@a"], "approvedAt": "2026-09-29", "basis": "protocol-exception",
        "exception": {"rule": "promotion.required-evaluations", "reason": "test exception reason", "expiresOnVersion": "1.1.0"}})
    edit_manifest(reg, CAND, lambda m: m["security"]["approvals"].append(
        {"type": "governance", "ref": "bundle:provenance/approval.yaml", "subjectDigest": d}))


def test_promote_flow_preserves_digest_and_attestations(reg):
    _make_promotable(reg)
    assert codes(reg) == []
    before = digest(reg, "github-pr-triage")
    ops.promote(reg, "github-pr-triage")
    b = ops.find(Registry(reg), "github-pr-triage")
    assert b.tier == "skills" and b.meta["maturity"] == "canonical" and b.stage() is None and not (b.path / "provenance/stage.yaml").exists() and b.digest() == before
    assert codes(reg) == []


def test_promote_refuses_unapproved(reg):
    with pytest.raises(SystemExit, match="approved"):
        ops.promote(reg, "github-pr-triage")


def test_promote_refuses_with_stale_attestation(reg):
    _make_promotable(reg)
    p = reg / CAND / "SKILL.md"
    p.write_text(p.read_text() + "\nlate edit\n")
    with pytest.raises(SystemExit, match="validation errors"):
        ops.promote(reg, "github-pr-triage")


# --------------------------------------------------------------- synthetic ---
def _synthetic(reg):
    rel = str(ops.scaffold(reg, "research", "demo-echo", tier="synthetic").relative_to(reg))
    return rel


def test_synthetic_scaffold_is_marked_and_structurally_valid(reg):
    rel = _synthetic(reg)
    m = load(reg, rel + "/manifest.yaml")
    assert m["spec"]["markers"] == {"namespace": "synthetic"} and m["metadata"]["origin"]["type"] == "native"
    assert "synthetic-marking" not in codes(reg)


def test_synthetic_marker_required_in_and_only_in_synthetic_tier(reg):
    import shutil
    rel = _synthetic(reg)
    edit_manifest(reg, rel, lambda m: m["spec"].pop("markers"))
    assert "synthetic-marking" in codes(reg)
    edit_manifest(reg, rel, lambda m: m["spec"].update(markers={"namespace": "synthetic"}))
    shutil.copytree(reg / rel, reg / "candidates/research/demo-echo")
    assert "synthetic-marking" in codes(reg)


def test_synthetic_is_not_a_common_origin_value(reg):
    rel = _synthetic(reg)
    edit_manifest(reg, rel, lambda m: m["metadata"]["origin"].update(type="synthetic"))
    assert "manifest-schema" in codes(reg)


def test_synthetic_never_enters_production_index(reg):
    _synthetic(reg)
    prod = ops.index(reg, "production")
    assert "demo-echo" not in {e["id"] for e in prod["entries"]}
    syn = ops.index(reg, "synthetic")
    assert [e["id"] for e in syn["entries"]] == ["demo-echo"] and syn["namespace"] == "synthetic"


def test_production_cannot_reference_synthetic(reg):
    _synthetic(reg)

    def dep(m):
        m["references"].append({"registry": "skills", "id": "demo-echo", "version": "^0.1.0", "digest": None})
        m["spec"].setdefault("composition", []).append({"id": "demo-echo"})
    edit_manifest(reg, CAND, dep)
    assert "synthetic-leak" in codes(reg)


def test_synthetic_id_cannot_collide_with_production(reg):
    import shutil
    rel = _synthetic(reg)
    shutil.copytree(reg / rel, reg / "synthetic/research/web-research")
    edit_manifest(reg, "synthetic/research/web-research", lambda m: m["metadata"].update(id="web-research"))
    assert "synthetic-collision" in codes(reg)


# ------------------------------------------------- runtime artifacts / secrets ---
@pytest.mark.parametrize("name", ["run.tape", "session.trace", "events.jsonl", "trajectory-01.json", "agent_transcript.md"])
def test_runtime_artifacts_rejected(reg, name):
    (reg / WR / "examples" / name).write_text("{}")
    assert "no-runtime-artifacts" in codes(reg)


def test_oversize_files_rejected(reg):
    (reg / WR / "examples/big.md").write_text("x" * (300 * 1024))
    assert "file-too-large" in codes(reg)


def test_evidence_must_be_pointers(reg):
    ev = {"apiVersion": "registry.zeptly.dev/v1alpha1", "kind": "EvidenceReferences",
          "subject": {"registry": "skills", "id": "web-research"},
          "refs": [{"evidenceId": "ev-run-0001", "wisdom": "compute", "kind": "failure-cluster",
                    "subject": {"registry": "skills", "id": "web-research", "version": "1.0.0", "digest": digest(reg, "web-research")},
                    "uri": "evidence://store/cluster/1", "recordedAt": "2026-10-02T09:00:00Z", "summary": "31% of runs stall at step 3",
                    "metrics": {"failureRate": 0.31}}]}
    save(reg, WR + "/provenance/evidence.yaml", ev)
    assert codes(reg) == []
    for bad in ("file:///tmp/tape.json", "data:application/json;base64,e30="):
        ev["refs"][0]["uri"] = bad
        save(reg, WR + "/provenance/evidence.yaml", ev)
        assert "evidence-schema" in codes(reg)
    ev["refs"][0].update(uri="evidence://store/cluster/1", summary="x" * 501)
    save(reg, WR + "/provenance/evidence.yaml", ev)
    assert "evidence-schema" in codes(reg)
    ev["refs"][0].update(summary="ok summary", payload={"prompt": "raw prompt text"})
    save(reg, WR + "/provenance/evidence.yaml", ev)
    assert "evidence-schema" in codes(reg)


def test_secret_detected_and_placeholder_allowed(reg):
    (reg / WR / "examples/leak.md").write_text("token = ghp_" + "a" * 36)
    assert "secret" in codes(reg)
    (reg / WR / "examples/leak.md").write_text("Authorization: Bearer <token>")
    assert "secret" not in codes(reg)


# ------------------------------------------------------------- other rules ---
def test_unknown_vocab(reg):
    def bad(m):
        m["spec"]["compatibility"]["agent_classes"].append("mystery")
        m["spec"]["requires"]["capabilities"].append({"id": "cap.made.up"})
        m["security"]["capabilities"].append("cap.made.up")
    edit_manifest(reg, WR, bad)
    assert {"vocab-agent-class", "vocab-capability"} <= set(codes(reg))


def test_security_capabilities_must_match_requirements(reg):
    edit_manifest(reg, WR, lambda m: m["security"]["capabilities"].pop())
    assert "security-capabilities" in codes(reg)


def test_destructive_requires_hitl_and_class(reg):
    edit_manifest(reg, CR, lambda m: m["spec"]["security_profile"].update(side_effects="destructive"))
    assert {"sec-destructive-class", "sec-destructive-hitl", "sec-destructive-ops"} <= set(codes(reg))


def test_network_must_be_declared(reg):
    edit_manifest(reg, WR, lambda m: m["spec"]["security_profile"].pop("network"))
    assert "sec-network-missing" in codes(reg)


def test_missing_required_section(reg):
    p = reg / WR / "SKILL.md"
    p.write_text(p.read_text().replace("## Guardrails", "## Notes"))
    assert "skillmd-section" in codes(reg)


def test_safety_case_required_for_moderate(reg):
    p = reg / CR / "evals/suite.yaml"
    s = yaml.safe_load(p.read_text())
    s["cases"] = [c for c in s["cases"] if c.get("kind") not in ("safety", "adversarial")]
    p.write_text(yaml.safe_dump(s))
    assert "eval-safety" in codes(reg) or "eval-schema" in codes(reg)


# --------------------------------------------------- deterministic indexes / CLI ---
def test_index_is_deterministic_and_sorted(reg):
    a = ops.index_text(reg)
    import os, time
    for p in (reg / "skills").rglob("*"):
        os.utime(p, (time.time() + 1000, time.time() + 1000))
    assert ops.index_text(reg) == a
    ids = [(e["id"], e["version"]) for e in json.loads(a)["entries"]]
    assert ids == sorted(ids)
    assert "generatedAt" not in a and "timestamp" not in a.lower()


def test_index_has_common_fields(reg):
    e = json.loads(ops.index_text(reg))["entries"][0]
    assert {"kind", "id", "version", "digest", "directorySeal", "maturity", "lifecycle", "origin", "location"} <= set(e)
    Registry(reg)  # schema-valid index
    from jsonschema import Draft202012Validator
    Draft202012Validator(json.load(open(reg / "schemas/registry-index.schema.json"))).validate(json.loads(ops.index_text(reg)))


def test_index_reflects_lifecycle_overlay(reg):
    ops.set_lifecycle(reg, "web-research", "1.0.0", "deprecated", "superseded in test", sunset="2027-01-01")
    e = next(e for e in json.loads(ops.index_text(reg))["entries"] if e["id"] == "web-research")
    assert e["lifecycle"] == "deprecated"
    assert e["extensions"]["skills"]["releasedVersions"][0]["lifecycle"] == "deprecated"


def test_ledger_append_only(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "registry/releases").mkdir(parents=True)
    led = {"schema": "zeptly.ledger/v1", "registry": "skills", "skill": "x", "releases": [
        {"version": "1.0.0", "digest": "sha256:" + "a" * 64, "contract_digest": "sha256:" + "b" * 64,
         "security_digest": "sha256:" + "c" * 64, "released_at": "2026-01-01"}]}
    p = tmp_path / "registry/releases/x.yaml"
    p.write_text(yaml.safe_dump(led))
    g = ["git", "-C", str(tmp_path), "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run([*g, "add", "-A"], check=True)
    subprocess.run([*g, "commit", "-qm", "base"], check=True)
    assert ops.ledger_check(tmp_path, "HEAD") == []
    led2 = copy.deepcopy(led)
    led2["releases"].append({**led["releases"][0], "version": "1.1.0"})
    p.write_text(yaml.safe_dump(led2))
    assert ops.ledger_check(tmp_path, "HEAD") == []
    led3 = copy.deepcopy(led)
    led3["releases"][0]["digest"] = "sha256:" + "d" * 64
    p.write_text(yaml.safe_dump(led3))
    assert ops.ledger_check(tmp_path, "HEAD")


def test_scaffold_produces_structurally_valid_candidate(reg):
    ops.scaffold(reg, "research", "new-thing")
    from zskill.validate import validate
    issues = [i for i in validate(reg) if "new-thing" in i.where]
    assert not any(i.code in ("manifest-parse", "manifest-schema", "layout-name", "layout-domain", "maturity-location",
                              "stage-missing", "skillmd-mismatch", "file-not-allowed") for i in issues), issues


def test_cli(reg, monkeypatch):
    monkeypatch.chdir(reg)
    from zskill.cli import main
    assert main(["validate"]) == 0
    assert main(["validate", "--strict"]) == 1  # bootstrap waivers are warnings
    assert main(["index", "--check"]) == 0
    (reg / WR / "SKILL.md").write_text("broken")
    assert main(["validate"]) == 1


# ------------------------------------------------ normalization: origin / ids ---
@pytest.mark.parametrize("bad", ["authored", "imported", "discovered", "synthetic", "made-up"])
def test_only_native_evolved_upstream_seed_are_origin_values(reg, bad):
    edit_manifest(reg, WR, lambda m: m["metadata"]["origin"].update(type=bad))
    assert "manifest-schema" in codes(reg)


@pytest.mark.parametrize("good", ["native", "upstream-seed"])
def test_common_origin_values_accepted(reg, good):
    def f(m):
        m["metadata"]["origin"].update(type=good)
        if good == "upstream-seed":
            m["provenance"]["sourceRefs"] = [{"kind": "repository", "uri": "https://example.com/x", "ref": "v1"}]
    edit_manifest(reg, WR, f)
    assert "manifest-schema" not in codes(reg)


def test_discovered_marker_is_registry_local_and_needs_upstream_seed(reg):
    edit_manifest(reg, WR, lambda m: m["spec"].update(markers={"provenance": "discovered"}))
    assert "marker-discovered" in codes(reg)


def test_evolution_kind_has_a_single_location(reg):
    edit_manifest(reg, WR, lambda m: m["provenance"].update(evolution={"kind": "refined"}))
    assert "manifest-schema" in codes(reg)          # provenance.evolution is not a location
    import shutil
    shutil.copy(REPO / WR / "manifest.yaml", reg / WR / "manifest.yaml")      # undo the invalid edit

    def ok(m):
        m["metadata"]["origin"] = {"type": "evolved", "evolution": {"kind": "refined", "sourceRefs": []}}
        m["spec"].pop("markers")                                               # 'discovered' marker only pairs with upstream-seed
    edit_manifest(reg, CAND, ok)
    assert "manifest-schema" not in codes(reg)


def test_shared_id_grammar_dotted_hyphenated_no_zsk_prefix(reg):
    ops.scaffold(reg, "research", "research.web-fact-check")
    issues = [i for i in Registry(reg).run() if "research.web-fact-check" in i.where]
    assert not any(i.code in ("manifest-schema", "layout-name", "skillmd-mismatch") for i in issues), issues
    md = (reg / "candidates/research/research.web-fact-check/SKILL.md").read_text()
    assert "name: research-web-fact-check" in md          # portable Agent Skills name
    with pytest.raises(SystemExit):
        ops.scaffold(reg, "research", "zsk.thing")
    edit_manifest(reg, WR, lambda m: m["metadata"].update(id="Web_Research"))
    assert "manifest-schema" in codes(reg)


def test_legacy_prefix_rejected_in_manifest(reg):
    ops.scaffold(reg, "research", "thing")
    edit_manifest(reg, "candidates/research/thing", lambda m: m["metadata"].update(id="zsk.thing"))
    assert "id-prefix" in codes(reg)


# -------------------------------------------------- normalization: bundle contents ---
@pytest.mark.parametrize("rel", ["examples/x.bin", "examples/run.py", "evals/setup.sh", "notes.txt", "examples/a b.md",
                                 "examples/.hidden", "examples/a/b/c/d/e.md", "extra/file.md", "provenance/deep/er/x.yaml"])
def test_allow_list_rejects_unlisted_paths(reg, rel):
    p = reg / WR / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("x")
    assert "file-not-allowed" in codes(reg)


@pytest.mark.parametrize("rel", ["examples/more.md", "examples/data.json", "evals/fixtures/doc.txt", "provenance/notes.md",
                                 "provenance/eval-reports/1.0.0.yaml", "CHANGELOG.md"])
def test_allow_list_accepts_listed_paths(reg, rel):
    p = reg / WR / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("x: 1\n")
    assert "file-not-allowed" not in codes(reg)


def test_symlinks_rejected_and_never_hashed(reg):
    """Symlinks are an explicit error (see tests/test_remediation.py for seal/digest refusal)."""
    (reg / WR / "examples/link.md").symlink_to("/etc/hostname")
    (reg / WR / "examples/dirlink").symlink_to("/etc")
    found = [i.where for i in Registry(reg).run() if i.code == "symlink"]
    assert sorted(found) == [WR + "/examples/dirlink", WR + "/examples/link.md"]


def test_size_limits(reg):
    (reg / WR / "examples/big.md").write_text("x" * (300 * 1024))
    assert "file-too-large" in codes(reg)
    (reg / WR / "examples/big.md").unlink()
    for i in range(9):
        (reg / WR / f"examples/chunk{i}.md").write_text("y" * (250 * 1024))
    assert "bundle-too-large" in codes(reg)


def test_file_count_limit(reg):
    for i in range(205):
        (reg / WR / f"examples/f{i}.md").write_text("z")
    assert "bundle-too-many-files" in codes(reg)


def test_non_utf8_rejected(reg):
    (reg / WR / "examples/latin.md").write_bytes(b"caf\xe9")
    assert "file-not-text" in codes(reg)


def test_transcript_content_detected(reg):
    (reg / WR / "examples/chat.md").write_text("\n".join(f"{r}: text {i}" for i in range(4) for r in ("user", "assistant")))
    assert "runtime-artifact-content" in codes(reg)


def test_json_lines_event_stream_detected(reg):
    (reg / WR / "examples/events.json").write_text("\n".join('{"step": %d, "tool": "fetch"}' % i for i in range(8)))
    assert "runtime-artifact-content" in codes(reg)


def test_role_json_detected(reg):
    (reg / WR / "examples/msgs.json").write_text('[{"role": "user"}, {"role": "assistant"}, {"role": "tool"}]')
    assert "runtime-artifact-content" in codes(reg)


def test_normal_prose_is_not_flagged(reg):
    (reg / WR / "examples/prose.md").write_text("The user asked a question. The assistant answered.\nAssistant behaviour is described above.\n")
    assert "runtime-artifact-content" not in codes(reg)


# ------------------------------------------- normalization: locks and ordering ---
def _foreign_ref_reg(reg):
    edit_manifest(reg, WR, lambda m: (m["metadata"].update(version="1.1.0"), m["references"].append(
        {"registry": "tiny-agents", "id": "research.web-fact-check", "version": "^1.2.0", "digest": None})))
    ap = load(reg, WR + "/provenance/approval.yaml")
    ap["exception"]["expiresOnVersion"] = "1.2.0"
    save(reg, WR + "/provenance/approval.yaml", ap)
    rebind(reg, WR)


def test_foreign_references_are_explicit_in_locks(reg):
    _foreign_ref_reg(reg)
    assert not [i for i in Registry(reg).run() if i.level == "error"]
    lock = ops.resolve(reg, "competitive-intelligence")
    assert lock["unresolved"] == [{"registry": "tiny-agents", "id": "research.web-fact-check", "version": "^1.2.0",
                                   "digest": None, "reason": "foreign-registry-not-resolved-offline", "requestedBy": "web-research"}]
    assert all(r["registry"] == "skills" and r["directorySeal"].startswith("sha256:") for r in lock["resolved"])
    from jsonschema import Draft202012Validator
    Draft202012Validator(json.load(open(reg / "schemas/resolution-lock.schema.json"))).validate(lock)


def test_lock_has_empty_unresolved_when_all_local(reg):
    lock = ops.resolve(reg, "competitive-intelligence")
    assert lock["unresolved"] == []


def test_explicit_code_point_comparator():
    from zskill.bundle import cp_key
    ids = ["b", "a.b", "a-b", "a", "a2", "A"]
    assert sorted(ids, key=cp_key) == ["A", "a", "a-b", "a.b", "a2", "b"]      # '-' (0x2d) < '.' (0x2e) < '2' (0x32)
    assert sorted(["\uff5e", "\U00010000"], key=cp_key) == ["\uff5e", "\U00010000"]   # code-point, unlike UTF-16 order


def test_index_order_is_semver_then_maturity(reg):
    import shutil
    shutil.copytree(reg / WR, reg / "candidates/research/web-research")
    (reg / "candidates/research/web-research/provenance/stage.yaml").write_text("stage: drafted\n")
    edit_manifest(reg, "candidates/research/web-research", lambda m: (
        m["metadata"].update(maturity="candidate", version="1.10.0"), m["security"].update(approvals=[])))
    ents = [(e["id"], e["version"], e["maturity"]) for e in ops.index(reg)["entries"] if e["id"] == "web-research"]
    assert ents == [("web-research", "1.0.0", "canonical"), ("web-research", "1.10.0", "candidate")]   # numeric, not lexical


def test_two_index_builds_are_byte_identical_via_cli(reg, monkeypatch):
    monkeypatch.chdir(reg)
    from zskill.cli import main
    assert main(["index"]) == 0
    a = (reg / "registry/index.json").read_bytes()
    assert main(["index"]) == 0
    assert (reg / "registry/index.json").read_bytes() == a and main(["index", "--check"]) == 0
