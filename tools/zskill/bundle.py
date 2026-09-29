"""Loading skill bundles from the repository and computing digests."""
from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

TIERS = ("skills", "candidates")
# Files excluded from the version digest: evidence and approvals accumulate
# *about* a version without changing it; the changelog is human commentary.
DIGEST_EXCLUDED_TOP = {"provenance", "CHANGELOG.md"}
# Manifest keys excluded: lifecycle status must not alter a version's identity.
DIGEST_EXCLUDED_MANIFEST_KEYS = ("status", "deprecation")

_FM = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n?(.*)\Z", re.S)


def repo_root(start: Path | None = None) -> Path:
    p = (start or Path.cwd()).resolve()
    for cand in [p, *p.parents]:
        if (cand / "schemas" / "manifest.schema.json").exists():
            return cand
    raise SystemExit("not inside a registry-skills checkout (schemas/manifest.schema.json not found)")


class _StrictLoader(yaml.SafeLoader):
    """SafeLoader that keeps ISO dates as strings (JSON Schema `format: date` operates on strings)."""


_StrictLoader.yaml_implicit_resolvers = {
    k: [(tag, rx) for tag, rx in v if tag != "tag:yaml.org,2002:timestamp"]
    for k, v in yaml.SafeLoader.yaml_implicit_resolvers.items()
}


def load_yaml(path: Path):
    with open(path, encoding="utf-8") as fh:
        return yaml.load(fh, Loader=_StrictLoader)  # noqa: S506 - safe subclass


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_json(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


@dataclass
class Bundle:
    root: Path            # repository root
    path: Path            # bundle directory
    tier: str             # "skills" | "candidates"
    manifest: dict = field(default_factory=dict)
    manifest_error: str | None = None

    @property
    def rel(self) -> str:
        return self.path.relative_to(self.root).as_posix()

    @property
    def id(self) -> str | None:
        return self.manifest.get("id")

    @property
    def version(self) -> str | None:
        return self.manifest.get("version")

    @property
    def status(self) -> str | None:
        return self.manifest.get("status")

    def skill_md(self) -> tuple[dict | None, str]:
        p = self.path / "SKILL.md"
        if not p.exists():
            return None, ""
        text = p.read_text(encoding="utf-8").replace("\r\n", "\n")
        m = _FM.match(text)
        if not m:
            return None, text
        try:
            fm = yaml.load(m.group(1), Loader=_StrictLoader)
        except yaml.YAMLError:
            return None, m.group(2)
        return (fm if isinstance(fm, dict) else None), m.group(2)

    def files(self) -> list[Path]:
        out = []
        for p in sorted(self.path.rglob("*")):
            if p.is_file():
                out.append(p)
        return out

    # ---- digests -------------------------------------------------------
    def digest(self) -> str:
        lines = []
        for p in self.files():
            rel = p.relative_to(self.path).as_posix()
            if rel.split("/")[0] in DIGEST_EXCLUDED_TOP:
                continue
            if rel == "manifest.yaml":
                m = copy.deepcopy(self.manifest)
                for k in DIGEST_EXCLUDED_MANIFEST_KEYS:
                    m.pop(k, None)
                h = sha256_hex(canonical_json(m))
            else:
                data = p.read_bytes()
                if p.suffix in (".md", ".yaml", ".yml", ".json", ".txt"):
                    data = data.replace(b"\r\n", b"\n")
                h = sha256_hex(data)
            lines.append(f"{rel}\0{h}\n")
        return "sha256:" + sha256_hex("".join(lines).encode())

    def contract_digest(self) -> str:
        m = self.manifest
        subset = {
            "inputs": m.get("inputs"), "outputs": m.get("outputs"),
            "requires": m.get("requires"), "dependencies": m.get("dependencies"),
            "agent_classes": (m.get("compatibility") or {}).get("agent_classes"),
            "protocol": (m.get("compatibility") or {}).get("protocol"),
        }
        return "sha256:" + sha256_hex(canonical_json(subset))

    def security_digest(self) -> str:
        return "sha256:" + sha256_hex(canonical_json(self.manifest.get("security")))


def load_bundles(root: Path) -> list[Bundle]:
    bundles: list[Bundle] = []
    for tier in TIERS:
        base = root / tier
        if not base.exists():
            continue
        for mf in sorted(base.glob("*/*/manifest.yaml")):
            b = Bundle(root=root, path=mf.parent, tier=tier)
            try:
                data = load_yaml(mf)
                if not isinstance(data, dict):
                    raise ValueError("manifest is not a mapping")
                b.manifest = data
            except Exception as e:  # noqa: BLE001 - reported as a validation error
                b.manifest_error = str(e)
            bundles.append(b)
    return bundles


def load_ledger(root: Path, skill_id: str) -> dict | None:
    p = root / "registry" / "releases" / f"{skill_id}.yaml"
    return load_yaml(p) if p.exists() else None
