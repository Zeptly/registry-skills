"""Loading skill bundles from the repository and computing digests."""
from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

TIERS = ("skills", "candidates", "synthetic")
PRODUCTION_TIERS = ("skills", "candidates")
# Files excluded from the version digest: evidence and approvals accumulate
# *about* a version without changing it; the changelog is human commentary.
DIGEST_EXCLUDED_TOP = {"provenance", "CHANGELOG.md"}
# Manifest paths excluded from the digest. These are governance state that changes
# *about* a fixed version (promotion, lifecycle, attestations that bind to the digest);
# including them would make the digest circular or make promotion change identity.
DIGEST_EXCLUDED_MANIFEST_PATHS = (
    ("metadata", "maturity"), ("metadata", "lifecycle"), ("spec", "stage"),
    ("attestations",), ("security", "approvals"),
)

_FM = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n?(.*)\Z", re.S)


def repo_root(start: Path | None = None) -> Path:
    p = (start or Path.cwd()).resolve()
    for cand in [p, *p.parents]:
        if (cand / "schemas" / "envelope.schema.json").exists():
            return cand
    raise SystemExit("not inside a registry-skills checkout (schemas/envelope.schema.json not found)")


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
    def meta(self) -> dict:
        return self.manifest.get("metadata") or {}

    @property
    def spec(self) -> dict:
        return self.manifest.get("spec") or {}

    @property
    def id(self) -> str | None:
        return self.meta.get("id")

    @property
    def version(self) -> str | None:
        return self.meta.get("version")

    @property
    def maturity(self) -> str | None:
        return self.meta.get("maturity")

    @property
    def lifecycle(self) -> str | None:
        return self.meta.get("lifecycle")

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
                h = sha256_hex(canonical_json(digest_view(self.manifest)))
            else:
                data = p.read_bytes()
                if p.suffix in (".md", ".yaml", ".yml", ".json", ".txt"):
                    data = data.replace(b"\r\n", b"\n")
                h = sha256_hex(data)
            lines.append(f"{rel}\0{h}\n")
        return "sha256:" + sha256_hex("".join(lines).encode())

    def contract_digest(self) -> str:
        sp = self.spec
        subset = {
            "inputs": sp.get("inputs"), "outputs": sp.get("outputs"), "requires": sp.get("requires"),
            "composition": sp.get("composition"), "references": self.manifest.get("references"),
            "agent_classes": (sp.get("compatibility") or {}).get("agent_classes"),
            "protocol": (sp.get("compatibility") or {}).get("protocol"),
        }
        return "sha256:" + sha256_hex(canonical_json(subset))

    def security_digest(self) -> str:
        sec = self.manifest.get("security") or {}
        subset = {"profile": self.spec.get("security_profile"),
                  "classification": sec.get("classification"), "capabilities": sec.get("capabilities")}
        return "sha256:" + sha256_hex(canonical_json(subset))


def digest_view(manifest: dict) -> dict:
    """Manifest with governance-state paths removed (see DIGEST_EXCLUDED_MANIFEST_PATHS)."""
    m = copy.deepcopy(manifest)
    for path in DIGEST_EXCLUDED_MANIFEST_PATHS:
        node = m
        for k in path[:-1]:
            node = node.get(k) if isinstance(node, dict) else None
            if node is None:
                break
        if isinstance(node, dict):
            node.pop(path[-1], None)
    return m


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


def load_lifecycle(root: Path, skill_id: str) -> dict | None:
    p = root / "registry" / "lifecycle" / f"{skill_id}.yaml"
    return load_yaml(p) if p.exists() else None


def effective_lifecycle(overlay: dict | None, version: str) -> str:
    """Latest event for the version wins; no events means active."""
    state = "active"
    for ev in (overlay or {}).get("events", []):
        if ev["version"] == version:
            state = ev["state"]
    return state
