"""Mutating/derived operations: release, promote, lifecycle, index, resolve, ledger-check, scaffold."""
from __future__ import annotations

import datetime as dt
import json
import re
import shutil
import subprocess
from pathlib import Path

import yaml

from .bundle import PRODUCTION_TIERS, Bundle, effective_lifecycle, load_yaml
from .semver import Version, satisfies
from .validate import Registry

API_VERSION = "registry.zeptly.dev/v1alpha1"


def find(reg: Registry, ref: str, tier: str | None = None) -> Bundle:
    matches = [b for b in reg.bundles if not b.manifest_error and (b.id == ref or b.rel == ref.rstrip("/"))
               and (tier is None or b.tier == tier)]
    if not matches:
        raise SystemExit(f"no skill matching {ref!r}")
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
        raise SystemExit("only canonical skills (skills/) can be released; use `zskill promote` for candidates")
    entry = {"version": b.version, "digest": b.digest(), "contract_digest": b.contract_digest(),
             "security_digest": b.security_digest(), "released_at": dt.date.today().isoformat()}
    if promoted_from:
        entry["promoted_from"] = promoted_from
    if notes:
        entry["notes"] = notes
    p = ledger_path(root, b.id)
    led = load_yaml(p) if p.exists() else {"schema": "zeptly.ledger/v1", "registry": "skills", "skill": b.id, "releases": []}
    for r in led["releases"]:
        if r["version"] == b.version:
            if r["digest"] == entry["digest"]:
                return f"{b.id}@{b.version} already released (unchanged)"
            raise SystemExit(f"{b.id}@{b.version} is already released with a different digest. Released versions are immutable; bump the version.")
    led["releases"].append(entry)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(dump(led), encoding="utf-8")
    return f"released {b.id}@{b.version} {entry['digest']}"


def _rewrite_line(manifest_path: Path, pattern: str, replacement: str | None):
    text = manifest_path.read_text(encoding="utf-8")
    new, n = re.subn(pattern, replacement if replacement is not None else "", text, count=1, flags=re.M)
    if n != 1:
        raise SystemExit(f"could not rewrite {pattern!r} in {manifest_path.name}")
    manifest_path.write_text(new, encoding="utf-8")


def promote(root: Path, ref: str) -> str:
    """candidate(stage=approved) -> canonical + release. The content digest is unchanged by design
    (maturity, stage, lifecycle and attestations are outside the digest), so existing attestations stay valid."""
    reg = Registry(root)
    b = find(reg, ref, tier="candidates")
    if b.spec.get("stage") != "approved":
        raise SystemExit(f"{b.id} has stage {b.spec.get('stage')!r}; only 'approved' candidates can be promoted")
    if any(i.level == "error" and i.where.startswith(b.rel) for i in reg.run()):
        raise SystemExit("candidate has validation errors; run `zskill validate` and fix them first")
    before = b.digest()
    dest = root / "skills" / b.spec["domain"] / b.id
    if dest.exists():
        raise SystemExit(f"{dest} already exists")
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(b.path), str(dest))
    mp = dest / "manifest.yaml"
    _rewrite_line(mp, r"^  maturity:.*$", "  maturity: canonical")
    _rewrite_line(mp, r"^  stage:.*\n", None)
    nb = find(Registry(root), b.id, tier="skills")
    assert nb.digest() == before, "digest changed during promotion"
    return f"promoted candidates/{b.spec['domain']}/{b.id} -> {nb.rel}; " + release(root, b.id, promoted_from=f"candidates/{b.spec['domain']}/{b.id}")


def set_lifecycle(root: Path, sid: str, version: str, state: str, reason: str,
                  replaced_by: str | None = None, sunset: str | None = None) -> str:
    """Append a lifecycle event and mirror the effective state into the manifest (outside the digest)."""
    reg = Registry(root)
    b = next((x for x in reg.bundles if x.id == sid and x.version == version), None)
    released = version in reg.released_versions(sid)
    if b is None and not released:
        raise SystemExit(f"unknown artifact {sid}@{version}")
    p = overlay_path(root, sid)
    ov = load_yaml(p) if p.exists() else {"apiVersion": API_VERSION, "kind": "LifecycleOverlay", "registry": "skills", "id": sid, "events": []}
    if effective_lifecycle(ov, version) == "revoked":
        raise SystemExit(f"{sid}@{version} is revoked; revocation is terminal")
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


def _entry(reg: Registry, b: Bundle) -> dict:
    ledger = reg.ledgers.get(b.id) or {}
    released = [{"version": r["version"], "digest": r["digest"],
                 "lifecycle": effective_lifecycle(reg.overlays.get(b.id), r["version"]),
                 "tag": f"skill/{b.id}/v{r['version']}"} for r in ledger.get("releases", [])]
    ev = "n/a"
    for att in b.manifest["security"]["approvals"]:
        if att["type"] == "governance" and att["ref"].startswith("bundle:"):
            rec = load_yaml(b.path / att["ref"][7:])
            ev = "evaluated" if rec.get("basis") == "evaluation" else "unevaluated"
    return {
        "kind": b.manifest["kind"], "id": b.id, "version": b.version, "digest": b.digest(),
        "maturity": b.meta["maturity"], "lifecycle": effective_lifecycle(reg.overlays.get(b.id), b.version),
        "origin": b.meta["origin"], "location": {"path": b.rel},
        "extensions": {"skills": {
            "domain": b.spec["domain"], "stage": b.spec.get("stage"), "evidenceLevel": ev,
            "agentClasses": b.spec["compatibility"]["agent_classes"],
            "references": [{"registry": r["registry"], "id": r["id"], "version": r["version"]} for r in b.manifest["references"]],
            "classification": b.manifest["security"]["classification"],
            "sideEffects": b.spec["security_profile"]["side_effects"],
            "hitlRequired": b.spec["security_profile"]["hitl"]["required"],
            "releasedVersions": released}},
    }


def index(root: Path, namespace: str = "production") -> dict:
    """Deterministic derived index. Synthetic artifacts appear ONLY in the synthetic index."""
    reg = Registry(root)
    tiers = PRODUCTION_TIERS if namespace == "production" else ("synthetic",)
    bundles = [b for b in reg.bundles if b.tier in tiers and not b.manifest_error and b.rel in _valid(reg)]
    entries = sorted((_entry(reg, b) for b in bundles), key=lambda e: (e["id"], Version(e["version"]), e["maturity"]))
    return {"apiVersion": API_VERSION, "kind": "RegistryIndex", "registry": "skills", "namespace": namespace, "entries": entries}


def _valid(reg: Registry) -> set[str]:
    if not reg.valid:
        reg.run()
    return reg.valid


def index_text(root: Path, namespace: str = "production") -> str:
    return json.dumps(index(root, namespace), indent=2) + "\n"


def index_path(root: Path, namespace: str = "production") -> Path:
    return root / "registry" / ("index.json" if namespace == "production" else "index.synthetic.json")


def resolve(root: Path, sid: str, rng: str | None = None) -> dict:
    """Range -> exact version -> content digest -> lock. Agents record this lock in evidence."""
    reg = Registry(root)
    reg.run()
    out: dict[str, dict] = {}

    def pick(t: Bundle, r: str | None):
        cands = set(reg.released_versions(t.id))
        if t.tier == "skills":
            cands.add(t.version)
        cands = {v for v in cands if reg.lifecycle_of(t.id, v) != "revoked"}
        ok = [v for v in cands if r is None or satisfies(v, r)]
        if not ok:
            raise SystemExit(f"cannot resolve {t.id} {r}: available (non-revoked) {sorted(cands)}")
        return max(ok, key=Version)

    def walk(i, r):
        t = reg.by_id.get(i)
        if t is None or t.rel not in reg.valid:
            raise SystemExit(f"unknown or invalid skill {i}")
        if i in out:
            return
        v = pick(t, r)
        rel = next((x for x in (reg.ledgers.get(i) or {}).get("releases", []) if x["version"] == v), None)
        digest = rel["digest"] if rel else (t.digest() if t.version == v else None)
        if digest is None:
            raise SystemExit(f"no digest for {i}@{v}")
        out[i] = {"registry": "skills", "id": i, "version": v, "digest": digest, "maturity": t.meta["maturity"],
                  "lifecycle": reg.lifecycle_of(i, v)}
        for ref in t.manifest["references"]:
            if ref["registry"] == "skills":
                walk(ref["id"], ref["version"])
    walk(sid, rng)
    return {"apiVersion": API_VERSION, "kind": "ResolutionLock", "root": {k: out[sid][k] for k in ("registry", "id", "version", "digest")},
            "resolved": list(out.values())}


def ledger_check(root: Path, base: str) -> list[str]:
    """Verify release ledgers and lifecycle overlays are append-only relative to a base git ref."""
    problems = []
    for sub, key in (("registry/releases/", "releases"), ("registry/lifecycle/", "events")):
        res = subprocess.run(["git", "-C", str(root), "ls-tree", "-r", "--name-only", base, sub], capture_output=True, text=True)
        if res.returncode != 0:
            raise SystemExit(f"git ls-tree failed for {base}: {res.stderr.strip()}")
        for name in filter(None, res.stdout.splitlines()):
            show = subprocess.run(["git", "-C", str(root), "show", f"{base}:{name}"], capture_output=True, text=True)
            old = yaml.safe_load(show.stdout) or {}
            cur_p = root / name
            if not cur_p.exists():
                problems.append(f"{name}: deleted (append-only)")
                continue
            old_r, new_r = old.get(key, []), load_yaml(cur_p).get(key, [])
            if new_r[: len(old_r)] != old_r:
                problems.append(f"{name}: existing entries were modified or removed")
    return problems


TEMPLATE_SKILL = """---
name: {name}
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
    type: authored
  maturity: candidate
  lifecycle: active
spec:
  title: {title}
  description: "TODO: one or two sentences on what this skill does and when an agent should use it."
  domain: {domain}
  stage: drafted
  tags: []
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
    if not re.match(r"^[a-z0-9]+(-[a-z0-9]+)*$", name):
        raise SystemExit("name must be lowercase-hyphenated")
    d = root / tier / domain / name
    if d.exists():
        raise SystemExit(f"{d} exists")
    title = name.replace("-", " ").title()
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for rel, tpl in (("SKILL.md", TEMPLATE_SKILL), ("manifest.yaml", TEMPLATE_MANIFEST), ("evals/suite.yaml", TEMPLATE_SUITE)):
        p = d / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(tpl.format(name=name, title=title, domain=domain, now=now), encoding="utf-8")
    for sub in ("examples", "provenance"):
        (d / sub).mkdir(exist_ok=True)
        (d / sub / ".gitkeep").write_text("")
    return d
