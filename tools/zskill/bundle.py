"""Loading skill bundles; RFC 8785 canonical JSON; artifact digest and directory seal (digestAlgorithm zeptly-jcs-v1).

Protocol v0.2 contract as implemented by the Skills registry:

  artifact digest = sha256( JCS( projection ) )
      projection INCLUDES apiVersion, kind, metadata.{id, registry, origin}, spec, references, manifest provenance,
      security.{classification, capabilities}
      projection EXCLUDES metadata.version, metadata.maturity, metadata.lifecycle, attestations, security.approvals

  directory seal  = sha256( JCS( {registry, id, version, payload: [{path, sha256}, ...]} ) )
      payload = every permitted regular file except the root manifest.yaml and provenance/**, ordered by Unicode
      code points of the POSIX relative path; each sha256 is over the exact bytes (payload text must already be
      UTF-8, BOM-free and LF-only: it is rejected, never normalized)

Hashing is refused (BundleError) for symlinks, FIFOs/sockets/devices, unreadable entries, files outside the
allow-list, case-colliding paths, and invalid payload text.
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
from typing import NamedTuple

from .errors import (BundleError, CanonicalizationError, RegistryError, UnsatisfiedRequest, YamlError,  # noqa: F401
                     label_path)
from .yamlsubset import SAFE_INT, load_yaml, load_yaml_text

DIGEST_ALGORITHM = "zeptly-jcs-v1"
TIERS = ("skills", "candidates", "synthetic")
PRODUCTION_TIERS = ("skills", "candidates")
# The only files outside the payload: the root metadata file (represented by the artifact digest) and mutable
# records (evidence, approvals, stage, eval reports) under provenance/.
PROJECTION_TOP = ("apiVersion", "kind", "spec", "references", "provenance")

MAX_DEPTH = 4
SAFE_PART = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
TEXT_EXT = {".md", ".yaml", ".yml", ".json", ".txt", ".csv"}
TOP_FILES = {"SKILL.md", "manifest.yaml", "CHANGELOG.md"}
PROVENANCE_FILES = {"approval.yaml", "assessment.yaml", "evidence.yaml", "stage.yaml"}

_FM = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n?(.*)\Z", re.S)


def repo_root(start: Path | None = None) -> Path:
    p = (start or Path.cwd()).resolve()
    for cand in [p, *p.parents]:
        if (cand / "schemas" / "envelope.schema.json").exists():
            return cand
    raise RegistryError("not inside a registry-skills checkout (schemas/envelope.schema.json not found)",
                        code="not-a-checkout")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def cp_key(s: str) -> tuple:
    """Explicit, locale-independent Unicode code-point ordering key."""
    return tuple(ord(c) for c in s)


# --------------------------------------------------------------------------- RFC 8785 canonical JSON
def es_number(v: float) -> str:
    """ECMAScript Number::toString for a finite double (shortest round-trip digits)."""
    if v == 0:
        return "0"
    sign = "-" if v < 0 else ""
    tup = Decimal(repr(abs(v))).as_tuple()
    n = len(tup.digits) + tup.exponent                     # decimal point position relative to the digit string
    digits = "".join(map(str, tup.digits)).rstrip("0") or "0"
    k = len(digits)
    if k <= n <= 21:
        body = digits + "0" * (n - k)
    elif 0 < n <= 21:
        body = digits[:n] + "." + digits[n:]
    elif -6 < n <= 0:
        body = "0." + "0" * (-n) + digits
    else:
        e = n - 1
        body = digits[0] + ("." + digits[1:] if k > 1 else "") + "e" + ("+" if e >= 0 else "-") + str(abs(e))
    return sign + body


def _jcs(v, out: list, where: str):
    if v is None:
        out.append("null")
    elif v is True:
        out.append("true")
    elif v is False:
        out.append("false")
    elif isinstance(v, str):
        if any(0xD800 <= ord(c) <= 0xDFFF for c in v):
            raise CanonicalizationError(f"{where}: [lone-surrogate] string contains a lone surrogate",
                                        code="lone-surrogate", file=where.split("#")[0], path=[])
        out.append(json.dumps(v, ensure_ascii=False))
    elif isinstance(v, int):
        if abs(v) > SAFE_INT:
            raise CanonicalizationError(f"{where}: [unsafe-integer] integer {v} is outside +/-(2^53-1)",
                                        code="unsafe-integer", file=where.split("#")[0])
        out.append(str(v))
    elif isinstance(v, float):
        if math.isnan(v) or math.isinf(v):
            raise CanonicalizationError(f"{where}: [non-finite] {v} is not representable in canonical JSON",
                                        code="non-finite", file=where.split("#")[0])
        out.append(es_number(v))
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
            raise CanonicalizationError(f"{where}: [non-string-key] object key {bad[0]!r} ({type(bad[0]).__name__}) is not a string",
                                        code="non-string-key", file=where.split("#")[0])
        for k in v:
            if any(0xD800 <= ord(c) <= 0xDFFF for c in k):
                raise CanonicalizationError(f"{where}: [lone-surrogate] object key contains a lone surrogate",
                                            code="lone-surrogate", file=where.split("#")[0])
        out.append("{")
        for i, k in enumerate(sorted(v, key=lambda k: k.encode("utf-16-be"))):  # RFC 8785 3.2.3: UTF-16 code units
            if i:
                out.append(",")
            out.append(json.dumps(k, ensure_ascii=False))
            out.append(":")
            _jcs(v[k], out, f"{where}.{k}")
        out.append("}")
    else:
        raise CanonicalizationError(f"{where}: [unsupported-type] {type(v).__name__} is not representable in canonical JSON",
                                    code="unsupported-type", file=where.split("#")[0])


def canonical_json(obj, label: str = "$") -> bytes:
    """RFC 8785 (JCS) serialization, UTF-8 encoded. No Unicode normalization. Failures name the value path."""
    out: list = []
    _jcs(obj, out, label)
    return "".join(out).encode("utf-8")


def manifest_projection(manifest: dict) -> dict:
    sec = manifest.get("security") or {}
    meta = manifest.get("metadata") or {}
    proj = {k: manifest.get(k) for k in PROJECTION_TOP}
    proj["metadata"] = {"id": meta.get("id"), "registry": meta.get("registry"), "origin": meta.get("origin")}
    proj["security"] = {"classification": sec.get("classification"), "capabilities": sec.get("capabilities")}
    return proj


# --------------------------------------------------------------------------- bundle contents
def permitted_path(parts: tuple) -> bool:
    """The Skills bundle allow-list (registry-local; the amendment leaves the list to each registry)."""
    if len(parts) > MAX_DEPTH or not all(SAFE_PART.match(x) or x == ".gitkeep" for x in parts):
        return False
    name = parts[-1]
    ext = "." + name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if len(parts) == 1:
        return name in TOP_FILES
    if name == ".gitkeep":
        return parts[0] in ("evals", "examples", "provenance")
    top = parts[0]
    if top in ("evals", "examples"):
        return ext in TEXT_EXT
    if top == "provenance":
        if len(parts) == 2:
            return name in PROVENANCE_FILES or ext in (".md", ".txt")
        return len(parts) == 3 and parts[1] == "eval-reports" and ext in (".yaml", ".yml")
    return False


def is_payload(rel: str) -> bool:
    parts = rel.split("/")
    return parts[0] != "provenance" and rel != "manifest.yaml"


def payload_text_problem(data: bytes):
    """(code, message) if payload text is not UTF-8, BOM-free, NUL-free and LF-only; else None."""
    if data.startswith(b"\xef\xbb\xbf"):
        return "payload-bom", "a UTF-8 byte order mark is not allowed"
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as e:
        return "file-not-text", f"not valid UTF-8 ({e.reason} at byte {e.start})"
    if "\x00" in text:
        return "payload-nul", "NUL characters are not allowed"
    if "\r" in text:
        i = text.index("\r")
        return "payload-line-endings", f"CR found at line {text.count(chr(10), 0, i) + 1}; payload text must be LF-only (CRLF and lone CR are rejected, not normalized)"
    return None


def walk_bundle(root: Path):
    """Classify every entry below `root` without following symlinks.

    Returns [(relpath, path, kind)] ordered by code points of the posix relative path, kind in
    'file' | 'symlink' | 'other' (FIFO, socket, device, ...) | 'unreadable'. Nothing is silently omitted."""
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


class Problem(NamedTuple):
    code: str
    path: str      # repository-relative
    message: str


@dataclass
class Bundle:
    root: Path            # repository root
    path: Path            # bundle directory
    tier: str             # "skills" | "candidates" | "synthetic"
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
            text = p.read_bytes().decode("utf-8")
        except UnicodeDecodeError as e:
            return None, "", f"{self.rel}/SKILL.md: [invalid-utf8] not valid UTF-8 ({e.reason} at byte {e.start})"
        except OSError as e:
            return None, "", f"{self.rel}/SKILL.md: [unreadable] cannot read ({e.strerror or e})"
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

    def stage_with_error(self) -> tuple[str | None, str | None]:
        """Candidate workflow stage from provenance/stage.yaml (outside the digest and the seal)."""
        p = self.path / "provenance" / "stage.yaml"
        if p.is_symlink() or not p.is_file():
            return None, None
        try:
            data = load_yaml(p)
        except YamlError as e:
            return None, str(e)
        if not isinstance(data, dict) or not isinstance(data.get("stage"), str):
            return None, f"{self.rel}/provenance/stage.yaml: [invalid-stage] expected a mapping with a string 'stage'"
        return data["stage"], None

    def stage(self) -> str | None:
        return self.stage_with_error()[0]

    # ---- hashability ---------------------------------------------------
    def hash_problems(self) -> list[Problem]:
        """Everything that forbids computing a seal/digest, reported before any hashing happens."""
        out: list[Problem] = []
        try:
            parts = self.path.relative_to(self.root).parts
        except ValueError:
            parts = ()
        cur = self.root
        for part in parts:
            cur = cur / part
            if cur.is_symlink():
                out.append(Problem("symlink", cur.relative_to(self.root).as_posix(),
                                   "symlinks are not allowed (never followed, never hashed)"))
        seen: dict[str, str] = {}
        for rel, p, kind in self.entries():
            full = f"{self.rel}/{rel}"
            if kind == "symlink":
                out.append(Problem("symlink", full, "symlinks are not allowed in bundles (never followed, never hashed)"))
                continue
            if kind == "unreadable":
                out.append(Problem("unreadable-entry", full, "entry could not be listed or read"))
                continue
            if kind == "other":
                out.append(Problem("unsupported-entry", full,
                                   "not a regular file or directory (FIFO, socket, device, ...); not allowed in bundles"))
                continue
            folded = rel.casefold()
            if folded in seen and seen[folded] != rel:
                out.append(Problem("case-collision", full, f"path collides with {seen[folded]!r} on case-insensitive filesystems"))
            seen.setdefault(folded, rel)
            if not permitted_path(tuple(rel.split("/"))):
                out.append(Problem("file-not-allowed", full, "path/name is not on the bundle allow-list"))
                continue
            if is_payload(rel):
                try:
                    data = p.read_bytes()
                except OSError as e:
                    out.append(Problem("unreadable-entry", full, f"cannot read file ({e.strerror or e})"))
                    continue
                prob = payload_text_problem(data)
                if prob:
                    out.append(Problem(prob[0], full, prob[1]))
        return out

    def _require_hashable(self):
        bad = self.hash_problems()
        if bad:
            what = "; ".join(f"{p.path} [{p.code}]" for p in bad[:10])
            more = f" and {len(bad) - 10} more" if len(bad) > 10 else ""
            raise BundleError(f"{self.rel}: cannot compute seal/digest: {what}{more}", code=bad[0].code,
                              file=bad[0].path)

    # ---- digests -------------------------------------------------------
    def payload_files(self) -> list[tuple[str, Path]]:
        self._require_hashable()
        return [(rel, p) for rel, p, kind in self.entries() if kind == "file" and is_payload(rel)]

    def seal_document(self) -> dict:
        payload = [{"path": rel, "sha256": "sha256:" + sha256_hex(p.read_bytes())} for rel, p in self.payload_files()]
        return {"registry": self.meta.get("registry"), "id": self.meta.get("id"),
                "version": self.meta.get("version"), "payload": payload}

    def directory_seal(self) -> str:
        return "sha256:" + sha256_hex(canonical_json(self.seal_document(), f"{self.rel}/manifest.yaml#$"))

    def digest(self) -> str:
        return "sha256:" + sha256_hex(canonical_json(manifest_projection(self.manifest), f"{self.rel}/manifest.yaml#$"))

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
                    raise YamlError(f"{mf}: [not-a-mapping] manifest is not a mapping", code="not-a-mapping", file=str(mf))
                b.manifest = data
            except RegistryError as e:
                b.manifest_error = str(e)
            except Exception as e:  # noqa: BLE001 - reported as a validation error, never an uncaught exception
                b.manifest_error = f"{mf}: [error] {type(e).__name__}: {e}"
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
