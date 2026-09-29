"""Loading skill bundles from the repository; canonical JSON, directory seal and artifact digest.

Digest model (Registry Protocol v0.1 normalization):

  directory_seal  = sha256 over the canonical payload files (SKILL.md, evals/, examples/, ...)
  artifact digest = sha256( JCS({"manifest": <projection>, "directorySeal": <seal>}) )

The manifest projection INCLUDES identity (apiVersion, kind, metadata.id/registry/origin), spec, references,
provenance and security.classification/capabilities. It EXCLUDES metadata.version, metadata.maturity,
metadata.lifecycle, attestations and security.approvals (governance state that changes about, or binds to,
a fixed artifact). Files under provenance/ and CHANGELOG.md are outside the seal.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

import yaml

TIERS = ("skills", "candidates", "synthetic")
PRODUCTION_TIERS = ("skills", "candidates")
# Not part of the canonical payload seal: evidence/approvals accumulate *about* an artifact, the changelog
# is commentary, .gitkeep is a placeholder, and manifest.yaml is covered by the manifest projection.
SEAL_EXCLUDED_TOP = {"provenance", "CHANGELOG.md"}
SEAL_EXCLUDED_NAMES = {".gitkeep", "manifest.yaml"}
# Manifest projection is an explicit include-list (see module docstring).
PROJECTION_TOP = ("apiVersion", "kind", "spec", "references", "provenance")


def normalize_text(data: bytes) -> bytes:
    """Line-ending policy: CRLF and lone CR become LF. No other transformation (no BOM strip, no trim)."""
    return data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


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


def _jcs_number(v) -> str:
    if isinstance(v, bool):
        raise TypeError("bool is not a number")
    if isinstance(v, int):
        if abs(v) > 2**53:
            raise ValueError("integer outside the IEEE-754 safe range is not representable in JCS")
        return str(v)
    if math.isnan(v) or math.isinf(v):
        raise ValueError("NaN/Infinity are not valid JSON")
    if v == 0:
        return "0"
    a = abs(v)
    if a < 1e-6 or a >= 1e16:
        raise ValueError("float magnitude outside the supported plain-decimal range (avoid exponent forms in manifests)")
    r = repr(v)
    if "e" in r or "E" in r:  # ES6 Number::toString uses plain decimals for 1e-6 <= |v| < 1e21
        r = format(Decimal(r), "f")
    return r[:-2] if r.endswith(".0") else r


def _jcs(v, out: list):
    if v is None:
        out.append("null")
    elif v is True:
        out.append("true")
    elif v is False:
        out.append("false")
    elif isinstance(v, str):
        out.append(json.dumps(v, ensure_ascii=False))  # JCS string escaping == RFC 8259 minimal escaping
    elif isinstance(v, (int, float)):
        out.append(_jcs_number(v))
    elif isinstance(v, (list, tuple)):
        out.append("[")
        for i, x in enumerate(v):
            if i:
                out.append(",")
            _jcs(x, out)
        out.append("]")
    elif isinstance(v, dict):
        if not all(isinstance(k, str) for k in v):
            raise TypeError("object keys must be strings")
        out.append("{")
        # RFC 8785 §3.2.3: sort by UTF-16 code units of the property names (NOT by code point)
        for i, k in enumerate(sorted(v, key=lambda k: k.encode("utf-16-be"))):
            if not isinstance(k, str):
                raise TypeError("object keys must be strings")
            if i:
                out.append(",")
            out.append(json.dumps(k, ensure_ascii=False))
            out.append(":")
            _jcs(v[k], out)
        out.append("}")
    else:
        raise TypeError(f"unsupported type for canonical JSON: {type(v).__name__}")


def canonical_json(obj) -> bytes:
    """RFC 8785 (JCS) serialization, UTF-8 encoded."""
    out: list = []
    _jcs(obj, out)
    return "".join(out).encode("utf-8")


def cp_key(s: str) -> tuple:
    """Explicit, locale-independent Unicode code-point ordering key."""
    return tuple(ord(c) for c in s)


def manifest_projection(manifest: dict) -> dict:
    sec = manifest.get("security") or {}
    proj = {k: manifest.get(k) for k in PROJECTION_TOP}
    meta = manifest.get("metadata") or {}
    proj["metadata"] = {"id": meta.get("id"), "registry": meta.get("registry"), "origin": meta.get("origin")}
    proj["security"] = {"classification": sec.get("classification"), "capabilities": sec.get("capabilities")}
    return proj


def walk_bundle(root: Path):
    """Yield (relpath, path, is_symlink) for every entry below root, never following symlinks.
    Order: explicit code-point order of the posix relative path."""
    found = []
    for dp, dns, fns in os.walk(root, followlinks=False):
        for name in [*dns, *fns]:
            p = Path(dp) / name
            if p.is_symlink():
                found.append((p.relative_to(root).as_posix(), p, True))
            elif p.is_file():
                found.append((p.relative_to(root).as_posix(), p, False))
    return sorted(found, key=lambda t: cp_key(t[0]))


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
        text = normalize_text(p.read_bytes()).decode("utf-8")
        m = _FM.match(text)
        if not m:
            return None, text
        try:
            fm = yaml.load(m.group(1), Loader=_StrictLoader)
        except yaml.YAMLError:
            return None, m.group(2)
        return (fm if isinstance(fm, dict) else None), m.group(2)

    def files(self) -> list[Path]:
        """Regular files only; symlinks are never followed (the validator rejects them)."""
        return [p for _, p, link in walk_bundle(self.path) if not link]

    def symlinks(self) -> list[Path]:
        return [p for _, p, link in walk_bundle(self.path) if link]

    def stage(self) -> str | None:
        """Candidate workflow stage (sidecar provenance/stage.yaml, outside the artifact digest)."""
        p = self.path / "provenance" / "stage.yaml"
        if not p.is_file() or p.is_symlink():
            return None
        data = load_yaml(p)
        return data.get("stage") if isinstance(data, dict) else None

    # ---- digests -------------------------------------------------------
    def payload_files(self) -> list[tuple[str, Path]]:
        out = []
        for rel, p, link in walk_bundle(self.path):
            if link or rel.split("/")[0] in SEAL_EXCLUDED_TOP or rel.split("/")[-1] in SEAL_EXCLUDED_NAMES:
                continue
            out.append((rel, p))
        return out

    def directory_seal(self) -> str:
        """sha256 over canonical payload files: lines of `<relpath>\\0<sha256(normalized bytes)>\\n`."""
        lines = [f"{rel}\0{sha256_hex(normalize_text(p.read_bytes()))}\n" for rel, p in self.payload_files()]
        return "sha256:" + sha256_hex("".join(lines).encode("utf-8"))

    def digest(self) -> str:
        doc = {"manifest": manifest_projection(self.manifest), "directorySeal": self.directory_seal()}
        return "sha256:" + sha256_hex(canonical_json(doc))

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
