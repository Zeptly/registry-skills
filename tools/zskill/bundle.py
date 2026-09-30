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
import stat
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


class RegistryError(Exception):
    """A controlled, user-facing failure: the message is the diagnostic. CLI prints it without a traceback."""


class YamlError(RegistryError):
    """Unreadable/malformed YAML (including duplicate mapping keys), with file and path information."""


class BundleError(RegistryError):
    """A bundle cannot be hashed: it holds symlinks, unsupported filesystem entries or unreadable files."""


class CanonicalizationError(RegistryError, ValueError):
    """A value cannot be represented in canonical JSON (range, type or key violation), with its path."""


class _StrictLoader(yaml.SafeLoader):
    """SafeLoader that keeps ISO dates as strings (JSON Schema `format: date` operates on strings)."""


_StrictLoader.yaml_implicit_resolvers = {
    k: [(tag, rx) for tag, rx in v if tag != "tag:yaml.org,2002:timestamp"]
    for k, v in yaml.SafeLoader.yaml_implicit_resolvers.items()
}


_MERGE_TAG = "tag:yaml.org,2002:merge"


def _reject_duplicate_keys(loader: "_StrictLoader", root, source: str):
    """Walk the composed node graph and reject duplicate mapping keys with file/line/path diagnostics.

    `<<` merge keys are skipped (an explicit key legitimately overrides a merged one), so documents that are
    valid today parse exactly as before."""
    seen: set[int] = set()

    def label(path: list) -> str:
        out = ""
        for p in path:
            out += f"[{p}]" if isinstance(p, int) else (("." if out else "") + str(p))
        return out or "<document root>"

    def walk(node, path):
        if id(node) in seen:
            return
        seen.add(id(node))
        if isinstance(node, yaml.MappingNode):
            keys: dict = {}
            for knode, vnode in node.value:
                if knode.tag == _MERGE_TAG:
                    walk(vnode, path)
                    continue
                try:
                    key = loader.construct_object(knode, deep=True)
                    hash(key)
                except Exception:  # noqa: BLE001 - unhashable/complex keys are reported by the normal constructor
                    walk(vnode, path)
                    continue
                if key in keys:
                    m, first = knode.start_mark, keys[key]
                    raise YamlError(
                        f"{source}: duplicate mapping key {key!r} at {label([*path, key])} "
                        f"(line {m.line + 1}, column {m.column + 1}; first defined at line {first.line + 1})")
                keys[key] = knode.start_mark
                walk(vnode, [*path, key])
        elif isinstance(node, yaml.SequenceNode):
            for i, item in enumerate(node.value):
                walk(item, [*path, i])

    walk(root, [])


def load_yaml_text(text: str, source: str):
    """Parse YAML text with the registry's SafeLoader variant. Duplicate mapping keys are an error;
    otherwise parsing behaviour is unchanged. Every failure is a YamlError naming `source`."""
    loader = _StrictLoader(text)
    try:
        node = loader.get_single_node()
        if node is None:
            return None
        _reject_duplicate_keys(loader, node, source)
        return loader.construct_document(node)
    except YamlError:
        raise
    except yaml.YAMLError as e:
        raise YamlError(f"{source}: {str(e).strip().splitlines()[0] if str(e).strip() else type(e).__name__}"
                        f"{_mark(e)}") from e
    except (RecursionError, ValueError, OverflowError) as e:
        raise YamlError(f"{source}: cannot parse ({type(e).__name__}: {e})") from e
    finally:
        loader.dispose()


def _mark(e) -> str:
    m = getattr(e, "problem_mark", None)
    return f" (line {m.line + 1}, column {m.column + 1})" if m is not None else ""


def load_yaml(path: Path):
    p = Path(path)
    try:
        text = p.read_bytes().decode("utf-8")
    except UnicodeDecodeError as e:
        raise YamlError(f"{p}: not valid UTF-8 ({e.reason} at byte {e.start})") from e
    except OSError as e:
        raise YamlError(f"{p}: cannot read ({e.strerror or e})") from e
    return load_yaml_text(text, str(p))


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _jcs_number(v, where: str) -> str:
    if isinstance(v, int):
        if abs(v) > 2**53:
            raise CanonicalizationError(f"{where}: integer {v} is outside the +/-2^53 range accepted by the canonical form")
        return str(v)
    if math.isnan(v) or math.isinf(v):
        raise CanonicalizationError(f"{where}: {v} is not representable in canonical JSON")
    if v == 0:
        return "0"
    a = abs(v)
    if a < 1e-6 or a >= 1e16:
        raise CanonicalizationError(f"{where}: float {v!r} is outside the accepted magnitude range [1e-6, 1e16)")
    r = repr(v)
    if "e" in r or "E" in r:  # ES6 Number::toString uses plain decimals for 1e-6 <= |v| < 1e21
        r = format(Decimal(r), "f")
    return r[:-2] if r.endswith(".0") else r


def _jcs(v, out: list, where: str):
    if v is None:
        out.append("null")
    elif v is True:
        out.append("true")
    elif v is False:
        out.append("false")
    elif isinstance(v, str):
        try:
            out.append(json.dumps(v, ensure_ascii=False))
            out[-1].encode("utf-8")
        except UnicodeEncodeError as e:
            raise CanonicalizationError(f"{where}: string contains a lone surrogate and cannot be encoded as UTF-8") from e
    elif isinstance(v, (int, float)):
        out.append(_jcs_number(v, where))
    elif isinstance(v, (list, tuple)):
        out.append("[")
        for i, x in enumerate(v):
            if i:
                out.append(",")
            _jcs(x, out, f"{where}[{i}]")
        out.append("]")
    elif isinstance(v, dict):
        bad = [k for k in v if not isinstance(k, str)]
        if bad:
            raise CanonicalizationError(f"{where}: object key {bad[0]!r} ({type(bad[0]).__name__}) is not a string")
        out.append("{")
        # RFC 8785 §3.2.3: sort by UTF-16 code units of the property names (NOT by code point)
        try:
            keys = sorted(v, key=lambda k: k.encode("utf-16-be"))
        except UnicodeEncodeError as e:
            raise CanonicalizationError(f"{where}: object key contains a lone surrogate") from e
        for i, k in enumerate(keys):
            if i:
                out.append(",")
            out.append(json.dumps(k, ensure_ascii=False))
            out.append(":")
            _jcs(v[k], out, f"{where}.{k}")
        out.append("}")
    else:
        raise CanonicalizationError(f"{where}: unsupported type {type(v).__name__} for canonical JSON")


def canonical_json(obj, label: str = "$") -> bytes:
    """RFC 8785 (JCS) serialization, UTF-8 encoded. Failures raise CanonicalizationError naming the value path."""
    out: list = []
    _jcs(obj, out, label)
    try:
        return "".join(out).encode("utf-8")
    except UnicodeEncodeError as e:
        raise CanonicalizationError(f"{label}: value cannot be encoded as UTF-8") from e


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
    """Classify every entry below `root` without following symlinks.

    Returns a list of (relpath, path, kind) ordered by code points of the posix relative path, where kind is
    'file' (regular file), 'symlink', 'other' (FIFO, socket, device, ...) or 'unreadable' (a directory that
    could not be listed). Nothing is silently omitted."""
    found = []

    def onerror(e: OSError):
        fn = Path(e.filename) if e.filename else root
        try:
            rel = fn.relative_to(root).as_posix()
        except ValueError:
            rel = str(fn)
        found.append((rel, fn, "unreadable"))

    for dp, dns, fns in os.walk(root, followlinks=False, onerror=onerror):
        for name in [*dns, *fns]:
            p = Path(dp) / name
            try:
                mode = os.lstat(p).st_mode
            except OSError:
                found.append((p.relative_to(root).as_posix(), p, "unreadable"))
                continue
            if stat.S_ISLNK(mode):
                kind = "symlink"
            elif stat.S_ISREG(mode):
                kind = "file"
            elif stat.S_ISDIR(mode):
                continue
            else:
                kind = "other"
            found.append((p.relative_to(root).as_posix(), p, kind))
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

    def skill_md_full(self) -> tuple[dict | None, str, str | None]:
        """(frontmatter, body, problem). `problem` is a diagnostic when SKILL.md is missing/unreadable/malformed."""
        p = self.path / "SKILL.md"
        if not p.exists():
            return None, "", "SKILL.md is missing"
        try:
            text = normalize_text(p.read_bytes()).decode("utf-8")
        except UnicodeDecodeError as e:
            return None, "", f"{self.rel}/SKILL.md: not valid UTF-8 ({e.reason} at byte {e.start})"
        except OSError as e:
            return None, "", f"{self.rel}/SKILL.md: cannot read ({e.strerror or e})"
        m = _FM.match(text)
        if not m:
            return None, text, "SKILL.md lacks YAML frontmatter (--- ... ---)"
        try:
            fm = load_yaml_text(m.group(1), f"{self.rel}/SKILL.md (frontmatter)")
        except YamlError as e:
            return None, m.group(2), str(e)
        if not isinstance(fm, dict):
            return None, m.group(2), "SKILL.md frontmatter is not a mapping"
        return fm, m.group(2), None

    def skill_md(self) -> tuple[dict | None, str]:
        fm, body, _ = self.skill_md_full()
        return fm, body

    def entries(self) -> list:
        return walk_bundle(self.path)

    def files(self) -> list[Path]:
        """Regular files only."""
        return [p for _, p, kind in self.entries() if kind == "file"]

    def invalid_entries(self) -> list[tuple[str, str]]:
        """(repo-relative path, kind) for everything that makes the bundle unhashable: symlinks (also in the bundle's own
        path below the repository root), unsupported filesystem entries and unreadable directories."""
        bad = []
        try:
            parts = self.path.relative_to(self.root).parts
        except ValueError:
            parts = ()
        cur = self.root
        for part in parts:
            cur = cur / part
            if cur.is_symlink():
                bad.append((cur.relative_to(self.root).as_posix(), "symlink"))
        bad += [(f"{self.rel}/{rel}", kind) for rel, _, kind in self.entries() if kind != "file"]
        return bad

    def stage_with_error(self) -> tuple[str | None, str | None]:
        """Candidate workflow stage from provenance/stage.yaml (outside the artifact digest)."""
        p = self.path / "provenance" / "stage.yaml"
        if p.is_symlink() or not p.is_file():
            return None, None
        try:
            data = load_yaml(p)
        except YamlError as e:
            return None, str(e)
        if not isinstance(data, dict) or not isinstance(data.get("stage"), str):
            return None, f"{self.rel}/provenance/stage.yaml: expected a mapping with a string 'stage'"
        return data["stage"], None

    def stage(self) -> str | None:
        return self.stage_with_error()[0]

    # ---- digests -------------------------------------------------------
    def _require_hashable(self):
        bad = self.invalid_entries()
        if bad:
            what = ", ".join(f"{rel} ({kind})" for rel, kind in bad[:10])
            more = f" and {len(bad) - 10} more" if len(bad) > 10 else ""
            raise BundleError(f"{self.rel}: cannot compute seal/digest: bundle contains symlinks or unsupported "
                              f"filesystem entries: {what}{more}")

    def payload_files(self) -> list[tuple[str, Path]]:
        self._require_hashable()
        out = []
        for rel, p, kind in self.entries():
            if rel.split("/")[0] in SEAL_EXCLUDED_TOP or rel.split("/")[-1] in SEAL_EXCLUDED_NAMES:
                continue
            out.append((rel, p))
        return out

    def directory_seal(self) -> str:
        """sha256 over canonical payload files: lines of `<relpath>\\0<sha256(normalized bytes)>\\n`."""
        lines = []
        for rel, p in self.payload_files():
            try:
                data = p.read_bytes()
            except OSError as e:
                raise BundleError(f"{self.rel}/{rel}: cannot read file ({e.strerror or e})") from e
            lines.append(f"{rel}\0{sha256_hex(normalize_text(data))}\n")
        return "sha256:" + sha256_hex("".join(lines).encode("utf-8"))

    def digest(self) -> str:
        proj = manifest_projection(self.manifest)
        canonical_json(proj, f"{self.rel}/manifest.yaml#$")  # fail first, with a path into the manifest
        doc = {"manifest": proj, "directorySeal": self.directory_seal()}
        return "sha256:" + sha256_hex(canonical_json(doc))

    def contract_digest(self) -> str:
        sp = self.spec
        subset = {
            "inputs": sp.get("inputs"), "outputs": sp.get("outputs"), "requires": sp.get("requires"),
            "composition": sp.get("composition"), "references": self.manifest.get("references"),
            "agent_classes": (sp.get("compatibility") or {}).get("agent_classes"),
            "protocol": (sp.get("compatibility") or {}).get("protocol"),
        }
        return "sha256:" + sha256_hex(canonical_json(subset, f"{self.rel}/manifest.yaml#$"))

    def security_digest(self) -> str:
        sec = self.manifest.get("security") or {}
        subset = {"profile": self.spec.get("security_profile"),
                  "classification": sec.get("classification"), "capabilities": sec.get("capabilities")}
        return "sha256:" + sha256_hex(canonical_json(subset, f"{self.rel}/manifest.yaml#$"))


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
                    raise YamlError(f"{mf}: manifest is not a mapping")
                b.manifest = data
            except RegistryError as e:
                b.manifest_error = str(e)
            except Exception as e:  # noqa: BLE001 - reported as a validation error, never an uncaught exception
                b.manifest_error = f"{mf}: {type(e).__name__}: {e}"
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
