"""Mutating/derived operations: release, promote, lifecycle, index, resolve, ledger-check, scaffold.

Failure semantics (Protocol v0.2 section 9): RegistryError -> exit 2 (malformed input / validation error);
UnsatisfiedRequest -> exit 1 (a valid request that cannot be satisfied, e.g. an unresolved reference)."""
from __future__ import annotations

import datetime as dt
import json
import re
import shutil
import subprocess
from pathlib import Path

import yaml

from .bundle import (DIGEST_ALGORITHM, PRODUCTION_TIERS, Bundle, RegistryError, UnsatisfiedRequest, cp_key,
                     effective_lifecycle, load_yaml, load_yaml_text)
from .semver import Version, satisfies, validate_range
from .validate import SYNTHETIC_PREFIX, Registry

API_VERSION = "registry.zeptly.dev/v1alpha1"
_EXACT = re.compile(r"^\d+\.\d+\.\d+(-[0-9A-Za-z.-]+)?$")


def find(reg: Registry, ref: str, tier: str | None = None) -> Bundle:
    matches = [b for b in reg.bundles if not b.manifest_error and (b.id == ref or b.rel == ref.rstrip("/"))
               and (tier is None or b.tier == tier)]
    if not matches:
        broken = next((b for b in reg.bundles if b.manifest_error and (b.rel == ref.rstrip("/") or b.path.name == ref)
                       and (tier is None or b.tier == tier)), None)
        if broken is not None:     # malformed input, not an unsatisfiable request
            raise RegistryError(broken.manifest_error, code="manifest-parse", file=broken.rel)
        raise UnsatisfiedRequest(f"no skill matching {ref!r}" + (f" in {tier}/" if tier else ""), code="not-found")
    # prefer canonical when an id exists in several tiers
    matches.sort(key=lambda b: {"skills": 0, "candidates": 1, "synthetic": 2}[b.tier])
    return matches[0]


def dump(obj) -> str:
    return yaml.safe_dump(obj, sort_keys=False, default_flow_style=False, width=100)


def ledger_path(root: Path, sid: str) -> Path:
    return root / "registry" / "releases" / f"{sid}.yaml"


def overlay_path(root: Path, sid: str) -> Path:
    return root / "registry" / "lifecycle" / f"{sid}.yaml"


def release(root: Path, ref: str, notes: str | None = None, promoted_from: str | None = None) -> str:
    reg = Registry(root)
    b = find(reg, ref)
    if b.tier != "skills":
        raise UnsatisfiedRequest("only canonical skills (skills/) can be released; use `zskill promote` for candidates",
                                 code="not-canonical")
    entry = {"version": b.version, "digest": b.digest(), "directory_seal": b.directory_seal(),
             "contract_digest": b.contract_digest(), "security_digest": b.security_digest(),
             "released_at": dt.date.today().isoformat()}
    if promoted_from:
        entry["promoted_from"] = promoted_from
    if notes:
        entry["notes"] = notes
    p = ledger_path(root, b.id)
    led = load_yaml(p) if p.exists() else {"schema": "zeptly.ledger/v1", "registry": "skills",
                                           "digestAlgorithm": DIGEST_ALGORITHM, "skill": b.id, "releases": []}
    for r in led["releases"]:
        if r["version"] == b.version:
            if r["digest"] == entry["digest"] and r["directory_seal"] == entry["directory_seal"]:
                return f"{b.id}@{b.version} already released (unchanged)"
            raise RegistryError(f"{b.id}@{b.version} is already released with a different digest or directory seal. "
                                "Released versions are immutable; bump the version.", code="release-immutable",
                                file=f"registry/releases/{b.id}.yaml")
    led["releases"].append(entry)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(dump(led), encoding="utf-8")
    return f"released {b.id}@{b.version} {entry['digest']}"


def _rewrite_line(manifest_path: Path, pattern: str, replacement: str | None):
    text = manifest_path.read_text(encoding="utf-8")
    new, n = re.subn(pattern, replacement if replacement is not None else "", text, count=1, flags=re.M)
    if n != 1:
        raise RegistryError(f"could not rewrite {pattern!r} in {manifest_path.name}", code="rewrite-failed", file=str(manifest_path))
    manifest_path.write_text(new, encoding="utf-8")


def promote(root: Path, ref: str) -> str:
    """candidate(stage=approved) -> canonical + release. The artifact digest and the directory seal are unchanged by
    design (maturity, stage, lifecycle and attestations are outside both), so existing attestations stay valid."""
    reg = Registry(root)
    b = find(reg, ref, tier="candidates")
    if b.stage() != "approved":
        raise UnsatisfiedRequest(f"{b.id} has stage {b.stage()!r}; only 'approved' candidates can be promoted", code="not-approved")
    if any(i.level == "error" and i.where.startswith(b.rel) for i in reg.run()):
        raise RegistryError("candidate has validation errors; run `zskill validate` and fix them first", code="validation-failed", file=b.rel)
    before = (b.digest(), b.directory_seal())
    dest = root / "skills" / b.spec["domain"] / b.id
    if dest.exists():
        raise RegistryError(f"{dest} already exists", code="exists", file=str(dest))
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(b.path), str(dest))
    mp = dest / "manifest.yaml"
    _rewrite_line(mp, r"^  maturity:.*$", "  maturity: canonical")
    (dest / "provenance" / "stage.yaml").unlink()  # stage is candidate-only workflow state
    nb = find(Registry(root), b.id, tier="skills")
    assert (nb.digest(), nb.directory_seal()) == before, "digest or seal changed during promotion"
    return f"promoted candidates/{b.spec['domain']}/{b.id} -> {nb.rel}; " + release(root, b.id, promoted_from=f"candidates/{b.spec['domain']}/{b.id}")


def set_lifecycle(root: Path, sid: str, version: str, state: str, reason: str,
                  replaced_by: str | None = None, sunset: str | None = None) -> str:
    """Append a lifecycle event and mirror the effective state into the manifest (outside digest and seal)."""
    reg = Registry(root)
    b = next((x for x in reg.bundles if x.id == sid and x.version == version), None)
    released = version in reg.released_versions(sid)
    if b is None and not released:
        raise UnsatisfiedRequest(f"unknown artifact {sid}@{version}", code="not-found")
    p = overlay_path(root, sid)
    ov = load_yaml(p) if p.exists() else {"apiVersion": API_VERSION, "kind": "LifecycleOverlay", "registry": "skills", "id": sid, "events": []}
    if effective_lifecycle(ov, version) == "revoked":
        raise UnsatisfiedRequest(f"{sid}@{version} is revoked; revocation is terminal", code="revoked-terminal")
    ev = {"version": version, "state": state, "at": dt.date.today().isoformat(), "reason": reason}
    if replaced_by:
        rid, _, rver = replaced_by.partition("@")
        ev["replacedBy"] = {"registry": "skills", "id": rid, "version": rver}
    if sunset:
        ev["sunset"] = sunset
    ov["events"].append(ev)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(dump(ov), encoding="utf-8")
    if b is not None:
        _rewrite_line(b.path / "manifest.yaml", r"^  lifecycle:.*$", f"  lifecycle: {state}")
    return f"{sid}@{version} lifecycle -> {state}"


def _evidence_level(b: Bundle) -> str:
    ev = "n/a"
    for att in b.manifest["security"]["approvals"]:
        if att["type"] == "governance" and att["ref"].startswith("bundle:"):
            try:
                rec = load_yaml(b.path / att["ref"][7:])
            except RegistryError:
                rec = None
            if not isinstance(rec, dict):
                ev = "unknown"  # unreadable approval record: reported by `zskill validate`
            else:
                ev = "evaluated" if rec.get("basis") == "evaluation" else "unevaluated"  # protocol-exception => unevaluated
    return ev


def _domain_of(b: Bundle) -> str:
    return "synthetic" if b.tier == "synthetic" else "production"


def _entry(reg: Registry, b: Bundle) -> dict:
    ledger = reg.ledgers.get(b.id) or {}
    released = [{"version": r["version"], "digest": r["digest"], "directorySeal": r["directory_seal"],
                 "digestAlgorithm": DIGEST_ALGORITHM, "lifecycle": effective_lifecycle(reg.overlays.get(b.id), r["version"]),
                 "tag": f"skill/{b.id}/v{r['version']}"} for r in ledger.get("releases", [])]
    e = {
        "registry": "skills", "kind": b.manifest["kind"], "id": b.id, "version": b.version, "digest": b.digest(),
        "digestAlgorithm": DIGEST_ALGORITHM,
        "maturity": b.meta["maturity"], "lifecycle": effective_lifecycle(reg.overlays.get(b.id), b.version),
        "origin": b.meta["origin"], "location": {"path": b.rel}, "domain": _domain_of(b),
        "extensions": {"skills": {
            "domain": b.spec["domain"], "stage": b.stage(), "evidenceLevel": _evidence_level(b),
            "agentClasses": b.spec["compatibility"]["agent_classes"],
            "references": [{"registry": r["registry"], "id": r["id"], "version": r["version"]} for r in b.manifest["references"]],
            "classification": b.manifest["security"]["classification"],
            "sideEffects": b.spec["security_profile"]["side_effects"],
            "hitlRequired": b.spec["security_profile"]["hitl"]["required"],
            "releasedVersions": released}},
    }
    if b.meta["maturity"] == "canonical":
        e["directorySeal"] = b.directory_seal()      # canonical versions carry the seal
    return e


def index(root: Path, domain: str = "production") -> dict:
    """Deterministic derived index (Protocol v0.2 section 8). Synthetic artifacts appear ONLY in the synthetic index.

    Generation FAILS (RegistryError with per-bundle diagnostics) if any bundle of the requested domain is invalid or
    unhashable: an index never silently omits an artifact. The result is validated against
    schemas/registry-index.schema.json before it is returned. Ordering: id by Unicode code points, then SemVer
    precedence, then digest."""
    reg = Registry(root)
    issues = reg.run()
    tiers = PRODUCTION_TIERS if domain == "production" else ("synthetic",)
    in_scope = [b for b in reg.bundles if b.tier in tiers]
    skipped = [b for b in in_scope if b.manifest_error or b.rel not in reg.valid or b.rel in reg.unhashable]
    if skipped:
        lines = []
        for b in skipped:
            msgs = [i for i in issues if i.level == "error" and (i.where == b.rel or i.where.startswith(b.rel + "/"))]
            lines.append(f"  {b.rel}: " + ("; ".join(f"{i.where}: [{i.code}] {i.msg}" if i.where != b.rel else f"[{i.code}] {i.msg}" for i in msgs[:5]) if msgs else "invalid"))
        raise RegistryError(f"cannot generate the {domain} index: {len(skipped)} bundle(s) are invalid and would be "
                            "omitted; fix them (see `zskill validate`):\n" + "\n".join(lines), code="index-invalid-bundles")
    entries = sorted((_entry(reg, b) for b in in_scope),
                     key=lambda e: (cp_key(e["id"]), Version(e["version"]), cp_key(e["digest"])))
    doc = {"apiVersion": API_VERSION, "kind": "RegistryIndex", "registry": "skills", "digestAlgorithm": DIGEST_ALGORITHM,
           "domain": domain, "entries": entries}
    errs = reg.validate_output("registry-index", doc, f"generated {domain} index")
    if errs:
        raise RegistryError("generated index does not match schemas/registry-index.schema.json:\n  " + "\n  ".join(errs[:10]),
                            code="index-schema")
    return doc


def index_text(root: Path, domain: str = "production") -> str:
    try:
        return json.dumps(index(root, domain), indent=2, allow_nan=False) + "\n"
    except ValueError as e:
        raise RegistryError(f"index cannot be serialized: {e}", code="index-serialize") from e


def index_path(root: Path, domain: str = "production") -> Path:
    return root / "registry" / ("index.json" if domain == "production" else "index.synthetic.json")


def load_peer_index(reg: Registry, path: str | Path) -> dict:
    """Read and validate an explicit peer index. Malformed input is a RegistryError (exit 2), never an unresolved entry."""
    doc = load_yaml(path)
    errs = reg.validate_output("registry-index", doc, str(path))
    if errs:
        raise RegistryError(f"{path}: not a valid registry index:\n  " + "\n  ".join(errs[:10]), code="invalid-peer-index", file=str(path))
    if doc["digestAlgorithm"] != DIGEST_ALGORITHM:
        raise RegistryError(f"{path}: unsupported digestAlgorithm {doc['digestAlgorithm']!r}", code="unsupported-digest-algorithm", file=str(path))
    return doc


def _own_pool(reg: Registry, domain: str, own_index: dict) -> list[dict]:
    pool = [dict(e) for e in own_index["entries"]]
    have = {(e["id"], e["version"]) for e in pool}
    if domain == "production":   # historical released versions resolve from the ledger
        for sid, led in reg.ledgers.items():
            for r in led["releases"]:
                if (sid, r["version"]) in have:
                    continue
                pool.append({"registry": "skills", "id": sid, "version": r["version"], "digest": r["digest"],
                             "directorySeal": r["directory_seal"], "digestAlgorithm": DIGEST_ALGORITHM,
                             "maturity": "canonical", "lifecycle": effective_lifecycle(reg.overlays.get(sid), r["version"]),
                             "domain": "production"})
    return pool


def _resolve_one(pool: dict, req: dict, domain: str, allow_candidates: bool) -> dict:
    """One declared reference -> lock entry. Rules: Protocol v0.2 section 6."""
    def unresolved(code, message):
        return {"requested": req, "status": "unresolved", "unresolved": {"code": code, "message": message}}
    reg_name, rid, rng = req["registry"], req["id"], req["version"]
    if reg_name not in pool:
        return unresolved("no-peer-index", f"no index was supplied for registry {reg_name!r}")
    if not validate_range(rng):
        return unresolved("invalid-range", f"{rng!r} is not a valid version or range")
    same_id = [e for e in pool[reg_name] if e["id"] == rid]
    if not same_id:
        return unresolved("not-found", f"{rid!r} is not in the {reg_name} index")
    in_domain = [e for e in same_id if e["domain"] == domain]
    if not in_domain:
        return unresolved("domain-mismatch", f"{rid!r} exists only in the {same_id[0]['domain']} domain; this lock is {domain}")
    cands = [e for e in in_domain if satisfies(e["version"], rng)]
    if not cands:
        return unresolved("no-matching-version", f"no version of {rid} satisfies {rng!r} (available: {sorted((e['version'] for e in in_domain), key=Version)})")
    cands = [e for e in cands if e["lifecycle"] != "revoked"]
    if not cands:
        return unresolved("revoked", f"every version of {rid} satisfying {rng!r} is revoked")
    if not _EXACT.match(rng):
        kept = [e for e in cands if e["lifecycle"] != "deprecated"]
        if not kept:
            return unresolved("deprecated-requires-exact-pin", f"only deprecated versions of {rid} satisfy {rng!r}; deprecated versions resolve only by exact pin")
        cands = kept
    if not allow_candidates:
        kept = [e for e in cands if e["maturity"] == "canonical"]
        if not kept:
            return unresolved("candidate-not-allowed", f"only candidate versions of {rid} satisfy {rng!r}; candidates require explicit opt-in")
        cands = kept
    pick = max(cands, key=lambda e: Version(e["version"]))
    if req.get("digest") and req["digest"] != pick["digest"]:
        return unresolved("digest-mismatch", f"pinned digest {req['digest'][:19]}... does not match index entry {pick['digest'][:19]}... for {rid}@{pick['version']}")
    res = {"registry": reg_name, "id": rid, "version": pick["version"], "digest": pick["digest"], "digestAlgorithm": DIGEST_ALGORITHM,
           "maturity": pick["maturity"], "lifecycle": pick["lifecycle"], "domain": pick["domain"]}
    if pick.get("directorySeal"):
        res["directorySeal"] = pick["directorySeal"]
    return {"requested": req, "status": "resolved", "resolved": res}


def resolve(root: Path, sid: str, *, domain: str = "production", peer_indexes: list | tuple = (),
            allow_candidates: bool = False) -> dict:
    """RuntimeLock for one artifact: every DECLARED reference appears (direct references only; transitive resolution
    is a runtime responsibility). Resolution is from explicit indexes: this registry's own index plus any supplied peer
    indexes, nothing is fetched. A reference whose registry has no supplied index is an explicit `no-peer-index` entry.
    The lock is schema-validated before it is returned."""
    reg = Registry(root)
    reg.run()
    tier_ok = ("synthetic",) if domain == "synthetic" else (("skills", "candidates") if allow_candidates else ("skills",))
    subject = next((b for b in reg.bundles if b.id == sid and b.tier in tier_ok and not b.manifest_error), None)
    if subject is None:
        raise UnsatisfiedRequest(f"no {domain} skill {sid!r}" + ("" if allow_candidates or domain == "synthetic" else " (candidates need --allow-candidates)"),
                                 code="not-found")
    if subject.rel not in reg.valid or subject.rel in reg.unhashable:
        why = next((f"{x.where}: [{x.code}] {x.msg}" for x in reg.issues if x.level == "error" and x.where.startswith(subject.rel)), "invalid")
        raise RegistryError(f"cannot lock {sid}: {subject.rel} is invalid or unhashable: {why}", code="invalid-subject", file=subject.rel)
    own = index(root, domain)
    pool: dict[str, list] = {"skills": _own_pool(reg, domain, own)}
    for path in peer_indexes:
        doc = load_peer_index(reg, path)
        if doc["registry"] == "skills":
            raise RegistryError(f"{path}: a peer index may not claim registry 'skills' (this registry's index is generated)", code="invalid-peer-index", file=str(path))
        pool.setdefault(doc["registry"], []).extend(doc["entries"])
    entries = []
    for ref in subject.manifest["references"]:
        req = {"registry": ref["registry"], "id": ref["id"], "version": ref["version"]}
        if ref.get("digest"):
            req["digest"] = ref["digest"]
            req["digestAlgorithm"] = ref.get("digestAlgorithm", DIGEST_ALGORITHM)
        entries.append(_resolve_one(pool, req, domain, allow_candidates))
    lock = {"apiVersion": API_VERSION, "kind": "RuntimeLock", "digestAlgorithm": DIGEST_ALGORITHM, "domain": domain,
            "subject": {"registry": "skills", "id": subject.id, "version": subject.version, "digest": subject.digest()},
            "complete": all(e["status"] == "resolved" for e in entries), "entries": entries}
    errs = reg.validate_output("runtime-lock", lock, "generated runtime lock")
    if errs:
        raise RegistryError("generated lock does not match schemas/runtime-lock.schema.json:\n  " + "\n  ".join(errs[:10]), code="lock-schema")
    return lock


def ledger_check(root: Path, base: str) -> list[str]:
    """Verify release ledgers and lifecycle overlays are append-only relative to a base git ref."""
    problems = []
    for sub, key in (("registry/releases/", "releases"), ("registry/lifecycle/", "events")):
        res = subprocess.run(["git", "-C", str(root), "ls-tree", "-r", "--name-only", base, sub], capture_output=True, text=True)
        if res.returncode != 0:
            raise RegistryError(f"git ls-tree failed for {base}: {res.stderr.strip()}", code="git-error")
        for name in filter(None, res.stdout.splitlines()):
            show = subprocess.run(["git", "-C", str(root), "show", f"{base}:{name}"], capture_output=True, text=True)
            try:
                old = load_yaml_text(show.stdout, f"{base}:{name}") or {}
            except RegistryError as e:
                problems.append(f"{name}: base version is unreadable: {e}")
                continue
            cur_p = root / name
            if not cur_p.exists():
                problems.append(f"{name}: deleted (append-only)")
                continue
            try:
                cur = load_yaml(cur_p)
            except RegistryError as e:
                problems.append(f"{name}: {e}")
                continue
            if not isinstance(old, dict) or not isinstance(cur, dict):
                problems.append(f"{name}: expected a mapping document")
                continue
            old_r, new_r = old.get(key, []), cur.get(key, [])
            if new_r[: len(old_r)] != old_r:
                problems.append(f"{name}: existing entries were modified or removed")
    return problems


TEMPLATE_SKILL = """---
name: {portable}
description: "TODO: one or two sentences on what this skill does and when an agent should use it."
---

# {title}

## When to use

TODO: triggers and non-triggers.

## Procedure

1. TODO

## Output

TODO: shape of the result, matching spec.outputs.

## Guardrails

- TODO: what the agent must not do; when to stop or ask a human.
"""

TEMPLATE_MANIFEST = """apiVersion: registry.zeptly.dev/v1alpha1
kind: SkillBlueprint
metadata:
  id: {name}
  version: 0.1.0
  registry: skills
  origin:
    type: native
  maturity: candidate
  lifecycle: active
spec:
  title: {title}
  description: "TODO: one or two sentences on what this skill does and when an agent should use it."
  domain: {domain}
  tags: []{markers}
  stewardship:
    maintainers: ["@TODO"]
  trust:
    tier: first-party
  compatibility:
    protocol: 1
    agent_classes: [execution]
  requires:
    capabilities: []
  inputs:
    - {{name: task, type: string, description: What the agent is asked to do.}}
  outputs:
    - {{name: result, type: markdown, description: TODO}}
  security_profile:
    data_sensitivity: [public]
    side_effects: none
    permissions: []
    authentication: {{required: false}}
    hitl: {{required: false}}
  evaluation:
    suite: evals/suite.yaml
    min_pass_rate: 0.8
references: []
provenance:
  createdAt: "{now}"
  authors:
    - {{name: TODO, kind: human}}
  sourceRefs: []
  transformations: []
security:
  classification: low
  capabilities: []
  approvals: []
attestations: []
"""

TEMPLATE_SUITE = """schema: zeptly.eval-suite/v1
skill: {name}
description: TODO
environment: {{mode: recorded}}
cases:
  - id: happy-path
    description: TODO
    inputs: {{task: TODO}}
    expect:
      - {{type: rubric, criterion: TODO one checkable statement}}
  - id: edge-case
    description: TODO
    inputs: {{task: TODO}}
    expect:
      - {{type: rubric, criterion: TODO one checkable statement}}
"""


def scaffold(root: Path, domain: str, name: str, tier: str = "candidates") -> Path:
    known = Registry(root).domains
    if domain not in known:
        raise RegistryError(f"unknown domain {domain!r}; valid domains: {', '.join(sorted(known))} (vocab/domains.yaml)", code="unknown-domain")
    if tier == "synthetic" and not name.startswith(SYNTHETIC_PREFIX):
        name = SYNTHETIC_PREFIX + name      # synthetic artifacts live in the reserved example namespace
    if tier != "synthetic" and name.startswith(SYNTHETIC_PREFIX):
        raise RegistryError(f"the id prefix {SYNTHETIC_PREFIX!r} is reserved for synthetic artifacts", code="reserved-namespace")
    if not re.match(r"^[a-z0-9]+([.-][a-z0-9]+)*$", name) or name.startswith("zsk."):
        raise RegistryError("id must be a lowercase dotted/hyphenated slug without the legacy zsk. prefix", code="invalid-id")
    d = root / tier / domain / name
    if d.exists():
        raise RegistryError(f"{d} exists", code="exists", file=str(d))
    title = name.replace("-", " ").replace(".", " ").title()
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    markers = "\n  markers:\n    namespace: synthetic" if tier == "synthetic" else ""
    for rel, tpl in (("SKILL.md", TEMPLATE_SKILL), ("manifest.yaml", TEMPLATE_MANIFEST), ("evals/suite.yaml", TEMPLATE_SUITE)):
        p = d / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(tpl.format(name=name, portable=name.replace(".", "-"), title=title, domain=domain, now=now, markers=markers), encoding="utf-8")
    (d / "examples").mkdir(exist_ok=True)
    (d / "examples" / ".gitkeep").write_text("")
    (d / "provenance").mkdir(exist_ok=True)
    (d / "provenance" / "stage.yaml").write_text("stage: drafted\n")
    return d
