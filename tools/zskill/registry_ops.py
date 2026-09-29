"""Mutating/derived operations: release, promote, index, resolve, ledger-check, scaffold."""
from __future__ import annotations

import datetime as dt
import json
import re
import shutil
import subprocess
from pathlib import Path

import yaml

from .bundle import Bundle, load_bundles, load_yaml
from .semver import Version, satisfies
from .validate import Registry


def find(reg: Registry, ref: str) -> Bundle:
    for b in reg.bundles:
        if b.id == ref or b.rel == ref.rstrip("/") or b.manifest.get("name") == ref:
            return b
    raise SystemExit(f"no skill matching {ref!r}")


def ledger_path(root: Path, sid: str) -> Path:
    return root / "registry" / "releases" / f"{sid}.yaml"


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
    led = load_yaml(p) if p.exists() else {"schema": "zeptly.ledger/v1", "skill": b.id, "releases": []}
    for r in led["releases"]:
        if r["version"] == b.version:
            if r["digest"] == entry["digest"]:
                return f"{b.id}@{b.version} already released (unchanged)"
            raise SystemExit(f"{b.id}@{b.version} is already released with a different digest. Released versions are immutable; bump the version.")
    led["releases"].append(entry)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.safe_dump(led, sort_keys=False), encoding="utf-8")
    return f"released {b.id}@{b.version} {entry['digest']}"


def _set_status(manifest_path: Path, status: str):
    text = manifest_path.read_text(encoding="utf-8")
    new, n = re.subn(r"(?m)^status:.*$", f"status: {status}", text, count=1)
    if n != 1:
        raise SystemExit("could not rewrite status in manifest.yaml")
    manifest_path.write_text(new, encoding="utf-8")


def promote(root: Path, ref: str) -> str:
    """candidate(status=approved) -> canonical (status=active) + release. Content digest is unchanged by design."""
    reg = Registry(root)
    b = find(reg, ref)
    if b.tier != "candidates":
        raise SystemExit(f"{b.id} is not a candidate")
    if b.status != "approved":
        raise SystemExit(f"{b.id} has status {b.status!r}; only 'approved' candidates can be promoted")
    before = b.digest()
    dest = root / "skills" / b.manifest["domain"] / b.manifest["name"]
    if dest.exists():
        raise SystemExit(f"{dest} already exists")
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(b.path), str(dest))
    _set_status(dest / "manifest.yaml", "active")
    src = f"candidates/{b.manifest['domain']}/{b.manifest['name']}"
    reg2 = Registry(root)
    nb = find(reg2, b.id)
    assert nb.digest() == before, "digest changed during promotion"
    msg = release(root, b.id, promoted_from=src)
    return f"promoted {src} -> {nb.rel}; {msg}"


def index(root: Path) -> dict:
    reg = Registry(root)
    entries = []
    for b in sorted((x for x in reg.bundles if not x.manifest_error and x.id), key=lambda x: x.id):
        m = b.manifest
        rel_versions = (load_yaml(ledger_path(root, b.id)) or {}).get("releases", []) if ledger_path(root, b.id).exists() else []
        ev = "n/a"
        ap = b.path / "provenance" / "approval.yaml"
        if ap.exists():
            ev = "evaluated" if (load_yaml(ap) or {}).get("basis") == "eval-report" else "unevaluated"
        entries.append({
            "id": b.id, "name": m["name"], "domain": m["domain"], "path": b.rel,
            "status": m["status"], "version": m["version"], "digest": b.digest(),
            "evidence_level": ev,
            "agent_classes": m["compatibility"]["agent_classes"],
            "dependencies": [{"id": d["id"], "version": d["version"]} for d in m.get("dependencies", [])],
            "security": {"classification": m["security"]["classification"], "side_effects": m["security"]["side_effects"],
                         "hitl_required": m["security"]["hitl"]["required"]},
            "released_versions": [{"version": r["version"], "digest": r["digest"]} for r in rel_versions],
        })
    return {"schema": "zeptly.index/v1", "generated_by": "zskill index", "skills": entries}


def index_text(root: Path) -> str:
    return json.dumps(index(root), indent=2, sort_keys=False) + "\n"


def resolve(root: Path, sid: str, rng: str | None = None) -> dict:
    """Resolve a skill and its transitive closure to exact versions + digests.

    Agents log this resolution alongside execution evidence so a run can be
    reproduced against the exact content it used.
    """
    reg = Registry(root)
    reg.by_id = {b.id: b for b in reg.bundles if b.id}
    reg.check_ledgers()
    out: dict[str, dict] = {}

    def pick(t: Bundle, r: str | None):
        cands = {r_["version"] for r_ in (reg.ledgers.get(t.id) or {}).get("releases", [])}
        if t.tier == "skills":
            cands.add(t.version)
        ok = [v for v in cands if r is None or satisfies(v, r)]
        if not ok:
            raise SystemExit(f"cannot resolve {t.id} {r}: available {sorted(cands)}")
        return max(ok, key=Version)

    def walk(i, r):
        t = reg.by_id.get(i)
        if t is None:
            raise SystemExit(f"unknown skill {i}")
        if i in out:
            return
        v = pick(t, r)
        rel = next((x for x in (reg.ledgers.get(i) or {}).get("releases", []) if x["version"] == v), None)
        if rel is None and t.version == v:
            digest = t.digest()
        elif rel:
            digest = rel["digest"]
        else:
            raise SystemExit(f"no digest for {i}@{v}")
        out[i] = {"ref": f"{i}@{v}", "digest": digest, "status": t.status}
        for d in t.manifest.get("dependencies", []):
            walk(d["id"], d["version"])
    walk(sid, rng)
    return {"schema": "zeptly.resolution/v1", "root": out[sid]["ref"], "skills": list(out.values())}


def ledger_check(root: Path, base: str) -> list[str]:
    """Verify release ledgers are append-only relative to a base git ref."""
    problems = []
    d = root / "registry" / "releases"
    res = subprocess.run(["git", "-C", str(root), "ls-tree", "-r", "--name-only", base, "registry/releases/"],
                         capture_output=True, text=True)
    if res.returncode != 0:
        raise SystemExit(f"git ls-tree failed for {base}: {res.stderr.strip()}")
    for name in filter(None, res.stdout.splitlines()):
        show = subprocess.run(["git", "-C", str(root), "show", f"{base}:{name}"], capture_output=True, text=True)
        old = yaml.safe_load(show.stdout) or {}
        cur_p = root / name
        if not cur_p.exists():
            problems.append(f"{name}: ledger deleted (ledgers are append-only)")
            continue
        cur = load_yaml(cur_p)
        old_r, new_r = old.get("releases", []), cur.get("releases", [])
        if new_r[: len(old_r)] != old_r:
            problems.append(f"{name}: existing release entries were modified or removed")
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

TODO: shape of the result, matching manifest outputs.

## Guardrails

- TODO: what the agent must not do; when to stop or ask a human.
"""

TEMPLATE_MANIFEST = """schema: zeptly.skill/v1
id: zsk.{name}
name: {name}
title: {title}
description: "TODO: one or two sentences on what this skill does and when an agent should use it."
domain: {domain}
version: 0.1.0
status: candidate
tags: []
authorship:
  authors:
    - {{name: TODO, kind: human}}
  maintainers: ["@TODO"]
provenance:
  origin: human-authored
  trust_tier: first-party
compatibility:
  protocol: 1
  agent_classes: [execution]
requires:
  capabilities: []
inputs:
  - {{name: task, type: string, description: What the agent is asked to do.}}
outputs:
  - {{name: result, type: markdown, description: TODO}}
security:
  classification: low
  data_sensitivity: [public]
  side_effects: none
  permissions: []
  authentication: {{required: false}}
  hitl: {{required: false}}
evaluation:
  suite: evals/suite.yaml
  min_pass_rate: 0.8
"""

TEMPLATE_SUITE = """schema: zeptly.eval-suite/v1
skill: zsk.{name}
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


def scaffold(root: Path, domain: str, name: str) -> Path:
    if not re.match(r"^[a-z0-9]+(-[a-z0-9]+)*$", name):
        raise SystemExit("name must be lowercase-hyphenated")
    d = root / "candidates" / domain / name
    if d.exists():
        raise SystemExit(f"{d} exists")
    title = name.replace("-", " ").title()
    for rel, tpl in (("SKILL.md", TEMPLATE_SKILL), ("manifest.yaml", TEMPLATE_MANIFEST), ("evals/suite.yaml", TEMPLATE_SUITE)):
        p = d / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(tpl.format(name=name, title=title, domain=domain), encoding="utf-8")
    (d / "examples").mkdir(exist_ok=True)
    (d / "provenance").mkdir(exist_ok=True)
    (d / "examples" / ".gitkeep").write_text("")
    (d / "provenance" / ".gitkeep").write_text("")
    return d
