"""Registry validation. Every rule maps to a documented requirement in docs/.

No rule needs network access: references to other registries are validated
structurally only; skills-registry references are resolved against this checkout.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry as SchemaRegistry, Resource
from referencing.jsonschema import DRAFT202012

from .bundle import (DIGEST_ALGORITHM, Bundle, is_payload, RegistryError, effective_lifecycle, load_bundles, load_yaml, sha256_hex)
from .semver import Version, bump_level, satisfies, validate_range

CLASSIFICATIONS = {"low": 0, "moderate": 1, "high": 2, "critical": 3}
EFFECT_RANK = {"none": 0, "read-external": 1, "write-external": 2, "destructive": 3}
EGRESS_RANK = {"none": 0, "allowlist": 1, "open": 2}
TIER_MATURITY = {"skills": "canonical", "candidates": "candidate", "synthetic": "candidate"}
REQUIRED_SECTIONS = ["When to use", "Procedure", "Output", "Guardrails"]
MAX_DEP_DEPTH = 3
MAX_CLOSURE = 20
TINY_MAX_CHARS = 8000
MAX_FILE_BYTES = 256 * 1024
MAX_BUNDLE_BYTES = 2 * 1024 * 1024
MAX_BUNDLE_FILES = 200
STAGES = ("discovered", "inspected", "drafted", "evaluating", "approved")
EXCEPTION_CEILING = Version("1.1.0")   # the six seed artifacts may rely on the exception only until 1.1.0
SYNTHETIC_PREFIX = "example."          # reserved example namespace selected by the Skills registry (Protocol v0.2 section 10)
SYNTHETIC_EVIDENCE = "evidence://example/"
TRANSCRIPT_LINE = re.compile(r"(?im)^\s*(user|assistant|system|human|ai|tool)\s*:\s")
ROLE_JSON = re.compile(r'(?i)"role"\s*:\s*"(user|assistant|tool|system)"')
FORBIDDEN_SUFFIXES = {".tape", ".trace", ".jsonl", ".ndjson", ".har", ".pcap", ".sqlite", ".db", ".parquet", ".pkl"}
FORBIDDEN_NAME = re.compile(r"(?i)(^|[._-])(tapes?|trajector(y|ies)|traces?|transcripts?|rollouts?)([._-]|$)")

SECRET_PATTERNS = [
    ("AWS access key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("GitHub token", re.compile(r"\b(ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{20,}")),
    ("API secret key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}")),
    ("Slack token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}")),
    ("private key block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("URL with embedded credentials", re.compile(r"\b[a-z][a-z0-9+.-]{1,15}://[^/\s:@]{1,64}:[^/\s:@]{3,64}@")),
    ("assigned secret", re.compile(r"(?i)\b(api[_-]?key|secret|token|passwd|password)\b\s*[:=]\s*['\"]?[A-Za-z0-9/+_\-]{16,}")),
]

SCHEMA_NAMES = ("envelope", "skill-blueprint", "evidence", "eval-suite", "eval-report", "approval",
                "assessment", "release-ledger", "lifecycle-overlay", "registry-index", "runtime-lock")


_FAILED = object()  # sentinel: a file could not be loaded (already reported); distinct from an empty document


@dataclass(frozen=True)
class Issue:
    level: str  # error | warning
    where: str
    code: str
    msg: str

    def __str__(self):
        return f"{self.level.upper():7} {self.where}: [{self.code}] {self.msg}"


def load_schemas(root: Path):
    """Load the registry's own JSON Schemas. A broken schema file is a controlled, fatal configuration error."""
    schemas = {}
    for n in SCHEMA_NAMES:
        path = root / "schemas" / f"{n}.schema.json"
        sch = load_yaml(path)  # YamlError names the file
        if not isinstance(sch, dict) or not isinstance(sch.get("$id"), str):
            raise RegistryError(f"{path}: a JSON Schema object with a string $id is required")
        try:
            Draft202012Validator.check_schema(sch)
        except Exception as e:  # noqa: BLE001
            raise RegistryError(f"{path}: not a valid JSON Schema ({getattr(e, 'message', e)})") from e
        schemas[n] = sch
    reg = SchemaRegistry()
    for sch in schemas.values():
        reg = reg.with_resource(sch["$id"], Resource.from_contents(sch, default_specification=DRAFT202012))
    return schemas, reg


def load_vocab(root: Path, rel: str, key: str) -> set:
    path = root / rel
    data = load_yaml(path)
    if not isinstance(data, dict) or not isinstance(data.get(key), dict):
        raise RegistryError(f"{path}: expected a mapping with a '{key}' mapping")
    return set(data[key])


def is_runtime_artifact_name(name: str) -> bool:
    suffix = ("." + name.rsplit(".", 1)[-1].lower()) if "." in name else ""
    return suffix in FORBIDDEN_SUFFIXES or bool(FORBIDDEN_NAME.search(name))


class Registry:
    def __init__(self, root: Path):
        self.root = root
        self.issues: list[Issue] = []
        self.schemas, self._sreg = load_schemas(root)
        self.domains = load_vocab(root, "vocab/domains.yaml", "domains")
        self.classes = load_vocab(root, "vocab/agent-classes.yaml", "agent_classes")
        self.caps = load_vocab(root, "vocab/capabilities.yaml", "capabilities")
        self.bundles = load_bundles(root)
        self.by_id: dict[str, Bundle] = {}     # production resolution target (canonical preferred)
        self.canonical: dict[str, Bundle] = {}
        self.synthetic_ids: set[str] = set()
        self.ledgers: dict[str, dict] = {}
        self.overlays: dict[str, dict] = {}
        self.valid: set[str] = set()           # bundles whose manifest passed schema validation
        self.unhashable: set[str] = set()      # bundles with symlinks/unsupported entries or non-canonicalizable values

    # ---- helpers -------------------------------------------------------
    def err(self, where, code, msg):
        self.issues.append(Issue("error", where, code, msg))

    def warn(self, where, code, msg):
        self.issues.append(Issue("warning", where, code, msg))

    def schema_check(self, name, data, where, code):
        v = Draft202012Validator(self.schemas[name], registry=self._sreg, format_checker=FormatChecker())
        ok = True
        for e in sorted(v.iter_errors(data), key=lambda e: list(map(str, e.path))):
            self.err(where, code, f"{'/'.join(map(str, e.path)) or '<root>'}: {e.message}")
            ok = False
        return ok

    def _load(self, path: Path, where: str, code: str):
        """load_yaml with a controlled diagnostic instead of an exception. Returns _FAILED on failure
        (an empty document loads as None and is left to schema validation)."""
        try:
            return load_yaml(path)
        except RegistryError as e:
            self.err(where, code, str(e))
            return _FAILED

    def validate_output(self, name: str, data, label: str) -> list[str]:
        """Schema errors (as strings) for a generated artefact (index, resolution lock); empty when valid."""
        v = Draft202012Validator(self.schemas[name], registry=self._sreg, format_checker=FormatChecker())
        return [f"{label}: {'/'.join(map(str, e.path)) or '<root>'}: {e.message}"
                for e in sorted(v.iter_errors(data), key=lambda e: list(map(str, e.path)))]

    def released_versions(self, skill_id: str) -> list[str]:
        return [r["version"] for r in (self.ledgers.get(skill_id) or {}).get("releases", [])]

    def lifecycle_of(self, skill_id: str, version: str) -> str:
        return effective_lifecycle(self.overlays.get(skill_id), version)

    # ---- top level -----------------------------------------------------
    def run(self) -> list[Issue]:
        self.check_ledgers()
        self.check_overlays()
        seen: dict[tuple, Bundle] = {}
        orphans: list[Bundle] = []
        for b in self.bundles:
            if b.manifest_error:
                self.err(b.rel, "manifest-parse", b.manifest_error)
                continue
            if not (b.id and b.version):
                orphans.append(b)  # still schema-checked below: a manifest without id/version must not vanish silently
                continue
            key = (b.id, b.version)
            if key in seen:
                self.err(b.rel, "duplicate-id", f"{b.id}@{b.version} also defined by {seen[key].rel}")
                continue
            seen[key] = b
        prod = [b for b in seen.values() if b.tier != "synthetic"]
        for b in prod:
            if b.tier == "skills":
                if b.id in self.canonical:
                    self.err(b.rel, "duplicate-id", f"canonical id {b.id} defined twice ({self.canonical[b.id].rel})")
                self.canonical[b.id] = b
        for b in prod:
            cur = self.by_id.get(b.id)
            if cur is None or (b.tier == "skills" and cur.tier != "skills"):
                self.by_id[b.id] = b
        self.synthetic_ids = {b.id for b in seen.values() if b.tier == "synthetic"}
        for sid in self.synthetic_ids & set(self.by_id):
            self.err(f"synthetic/{sid}", "synthetic-collision", f"synthetic id {sid} collides with a production id")
        for b in [*seen.values(), *orphans]:
            self.check_bundle(b)
        for b in seen.values():
            self.check_references(b)
        self.check_orphans()
        return self.issues

    # ---- ledgers / overlays -------------------------------------------
    def check_ledgers(self):
        d = self.root / "registry" / "releases"
        for p in sorted(d.glob("*.yaml")) if d.exists() else []:
            where = p.relative_to(self.root).as_posix()
            try:
                data = load_yaml(p)
            except Exception as e:  # noqa: BLE001
                self.err(where, "ledger-parse", str(e))
                continue
            if not self.schema_check("release-ledger", data, where, "ledger-schema"):
                continue
            if data["skill"] != p.stem:
                self.err(where, "ledger-name", f"file name must be <id>.yaml (skill={data['skill']})")
            self.ledgers[data["skill"]] = data
            prev, seen = None, set()
            for r in data["releases"]:
                if r["version"] in seen:
                    self.err(where, "ledger-duplicate", f"version {r['version']} appears twice")
                seen.add(r["version"])
                if prev:
                    pv, cv = Version(prev["version"]), Version(r["version"])
                    if cv <= pv:
                        self.err(where, "ledger-order", f"{r['version']} must be greater than {prev['version']}")
                    else:
                        lvl = bump_level(pv, cv)
                        if r["security_digest"] != prev["security_digest"] and lvl != "major":
                            self.err(where, "semver-security",
                                     f"{r['version']}: security changed vs {prev['version']} but bump is {lvl}; security changes require a MAJOR bump")
                        elif r["contract_digest"] != prev["contract_digest"] and lvl not in ("major", "minor"):
                            self.err(where, "semver-contract",
                                     f"{r['version']}: contract changed vs {prev['version']} but bump is {lvl}; requires MINOR or MAJOR")
                prev = r

    def check_overlays(self):
        d = self.root / "registry" / "lifecycle"
        for p in sorted(d.glob("*.yaml")) if d.exists() else []:
            where = p.relative_to(self.root).as_posix()
            try:
                data = load_yaml(p)
            except Exception as e:  # noqa: BLE001
                self.err(where, "lifecycle-parse", str(e))
                continue
            if not self.schema_check("lifecycle-overlay", data, where, "lifecycle-schema"):
                continue
            if data["id"] != p.stem:
                self.err(where, "lifecycle-name", "file name must be <id>.yaml")
            self.overlays[data["id"]] = data
            terminal, last = set(), ""
            for ev in data["events"]:
                if ev["at"] < last:
                    self.err(where, "lifecycle-order", f"event dated {ev['at']} precedes earlier event {last}")
                last = ev["at"]
                if ev["version"] in terminal:
                    self.err(where, "lifecycle-revoked-terminal", f"{ev['version']} was revoked; no further events allowed")
                if ev["state"] == "revoked":
                    terminal.add(ev["version"])

    def check_orphans(self):
        for sid in self.ledgers:
            if sid not in self.canonical:
                self.err(f"registry/releases/{sid}.yaml", "ledger-orphan", "ledger exists but no canonical skill has this id")
        for sid, ov in self.overlays.items():
            known = {b.version for b in self.bundles if b.id == sid} | set(self.released_versions(sid))
            if not known:
                self.err(f"registry/lifecycle/{sid}.yaml", "lifecycle-orphan", "overlay exists but no artifact has this id")
            for ev in ov["events"]:
                if ev["version"] not in known:
                    self.err(f"registry/lifecycle/{sid}.yaml", "lifecycle-version", f"event names unknown version {ev['version']}")

    # ---- per bundle ----------------------------------------------------
    def check_bundle(self, b: Bundle):
        w, m = b.rel, b.manifest
        if not self.schema_check("skill-blueprint", m, w + "/manifest.yaml", "manifest-schema"):
            return
        self.valid.add(b.rel)
        self.check_hashable(b)
        meta, spec = m["metadata"], m["spec"]
        parts = b.path.relative_to(b.root).parts  # tier/domain/name
        if meta["id"] != parts[2]:
            self.err(w, "layout-name", f"metadata.id {meta['id']!r} != directory {parts[2]!r}")
        if spec["domain"] != parts[1]:
            self.err(w, "layout-domain", f"spec.domain {spec['domain']!r} != directory {parts[1]!r}")
        if spec["domain"] not in self.domains:
            self.err(w, "vocab-domain", f"unknown domain {spec['domain']!r} (vocab/domains.yaml)")
        self.check_state_model(b)
        self.check_skill_md(b)
        self.check_compat_and_requires(b)
        self.check_security(b)
        self.check_origin_and_trust(b)
        self.check_evals(b)
        self.check_files(b)
        self.check_evidence(b)
        if b.rel in self.unhashable:
            return  # digest-dependent checks (attestations, release ledger) cannot run; the cause is reported above
        self.check_attestations(b)
        if b.tier == "skills":
            self.check_release(b)

    def check_hashable(self, b: Bundle):
        """Everything that forbids hashing (symlinks, special files, unreadable entries, disallowed or case-colliding
        paths, non-LF/BOM/NUL/non-UTF-8 payload text, non-canonicalizable values) is reported explicitly, and
        digest-dependent checks stay away from the bundle: never a silent partial digest."""
        problems = b.hash_problems()
        for pr in problems:
            if pr.code == "file-not-allowed" and is_runtime_artifact_name(pr.path.rsplit("/", 1)[-1]):
                continue  # reported once, as no-runtime-artifacts, by check_files
            self.err(pr.path, pr.code, pr.message)
        if problems:
            self.unhashable.add(b.rel)
            return
        try:
            b.digest(), b.directory_seal(), b.contract_digest(), b.security_digest()
        except RegistryError as e:
            self.err(b.rel + "/manifest.yaml", "canonicalization", str(e))
            self.unhashable.add(b.rel)

    def check_state_model(self, b: Bundle):
        """maturity, origin and lifecycle are independent fields; location must agree with maturity."""
        w, meta, spec = b.rel, b.meta, b.spec
        want = TIER_MATURITY[b.tier]
        if meta["maturity"] != want:
            self.err(w, "maturity-location", f"maturity {meta['maturity']!r} but artifact lives under {b.tier}/ (expects {want!r})")
        stage, stage_err = b.stage_with_error()
        if stage_err:
            self.err(w, "stage-parse", stage_err)
        if meta["maturity"] == "candidate":
            if stage is None and not stage_err:
                self.err(w, "stage-missing", "candidate artifacts require provenance/stage.yaml with a valid stage")
            elif stage not in STAGES:
                self.err(w, "stage-invalid", f"stage {stage!r} not in {list(STAGES)}")
        elif (b.path / "provenance" / "stage.yaml").exists():
            self.err(w, "stage-canonical", "provenance/stage.yaml is only valid while maturity is candidate")
        otype = meta["origin"]["type"]
        if meta["id"].startswith("zsk."):
            self.err(w, "id-prefix", "ids must not carry the legacy zsk. prefix; registry: skills disambiguates")
        markers = spec.get("markers", {})
        if (markers.get("namespace") == "synthetic") != (b.tier == "synthetic"):
            self.err(w, "synthetic-marking", "spec.markers.namespace: synthetic is required in, and only valid under, synthetic/")
        if (b.tier == "synthetic") != meta["id"].startswith(SYNTHETIC_PREFIX):
            self.err(w, "synthetic-namespace",
                     f"synthetic artifacts must use the reserved id prefix {SYNTHETIC_PREFIX!r}, and no other artifact may")
        evo = meta["origin"].get("evolution")
        if otype in ("refined", "evolved") and evo is None:
            self.err(w, "origin-evolution", f"origin.type {otype!r} requires metadata.origin.evolution")
        if otype in ("native", "upstream-seed") and evo is not None:
            self.err(w, "origin-evolution", f"origin.type {otype!r} must not carry metadata.origin.evolution")
        eff = self.lifecycle_of(meta["id"], meta["version"])
        if meta["lifecycle"] != eff:
            self.err(w, "lifecycle-mirror",
                     f"metadata.lifecycle is {meta['lifecycle']!r} but the lifecycle overlay says {eff!r}; use `zskill lifecycle`")
        if b.tier == "candidates":
            top = [Version(v) for v in self.released_versions(meta["id"])]
            if top and Version(meta["version"]) <= max(top):
                self.err(w, "candidate-version", f"candidate {meta['version']} must exceed every canonical version (highest: {max(top).text})")
        if b.tier != "skills" and meta["version"] in self.released_versions(meta["id"]):
            self.err(w, "candidate-released", f"version {meta['version']} is already in the release ledger")

    def check_skill_md(self, b: Bundle):
        w, spec = b.rel + "/SKILL.md", b.spec
        fm, body, problem = b.skill_md_full()
        if fm is None:
            self.err(w, "skillmd-frontmatter", problem or "SKILL.md missing or lacks valid YAML frontmatter (--- ... ---)")
            return
        portable = b.id.replace(".", "-")  # Agent Skills names are lowercase-hyphen only
        if fm.get("name") != portable:
            self.err(w, "skillmd-mismatch", f"frontmatter name must equal metadata.id with dots as hyphens ({portable!r})")
        if len(portable) > 64:
            self.warn(w, "skillmd-name-length", "portable SKILL.md name exceeds 64 characters (Agent Skills limit)")
        if fm.get("description") != spec["description"]:
            self.err(w, "skillmd-mismatch", "frontmatter description must equal spec.description")
        extra = set(fm) - {"name", "description", "license", "compatibility", "metadata", "allowed-tools"}
        if extra:
            self.warn(w, "skillmd-frontmatter-extra", f"non-portable frontmatter keys {sorted(extra)}")
        heads = {h.strip().lower() for h in re.findall(r"^##\s+(.+)$", body, re.M)}
        for sec in REQUIRED_SECTIONS:
            if sec.lower() not in heads:
                self.err(w, "skillmd-section", f"missing required section '## {sec}'")
        if "tiny" in spec["compatibility"]["agent_classes"] and len(body) > TINY_MAX_CHARS:
            self.err(w, "skillmd-size-tiny", f"body is {len(body)} chars; skills for 'tiny' agents must be <= {TINY_MAX_CHARS}")
        elif len(body.splitlines()) > 500:
            self.warn(w, "skillmd-size", "SKILL.md exceeds 500 lines")

    def check_compat_and_requires(self, b: Bundle):
        w, m, spec = b.rel, b.manifest, b.spec
        c = spec["compatibility"]
        if c["protocol"] != 1:
            self.err(w, "protocol", f"unsupported skill spec protocol {c['protocol']} (this tooling supports 1)")
        for k in c["agent_classes"]:
            if k not in self.classes:
                self.err(w, "vocab-agent-class", f"unknown agent class {k!r}")
        caps = [c_["id"] for c_ in spec["requires"]["capabilities"]]
        for cap in caps:
            if cap not in self.caps:
                self.err(w, "vocab-capability", f"unknown capability {cap!r} (vocab/capabilities.yaml)")
        if set(m["security"]["capabilities"]) != set(caps):
            self.err(w, "security-capabilities", "security.capabilities must equal the capability ids in spec.requires.capabilities")
        if m["security"]["classification"] not in CLASSIFICATIONS:
            self.err(w, "security-classification", f"classification must be one of {sorted(CLASSIFICATIONS)} in the skills registry")
        # references <-> composition
        skills_refs = [r for r in m["references"] if r["registry"] == "skills"]
        comp = spec.get("composition", [])
        ids_refs = [r["id"] for r in skills_refs]
        if len(ids_refs) != len(set(ids_refs)):
            self.err(w, "ref-duplicate", "duplicate skills-registry references")
        if set(ids_refs) != {c_["id"] for c_ in comp}:
            self.err(w, "ref-composition",
                     "every skills-registry reference needs a spec.composition entry with the same id, and vice versa")
        for r in m["references"]:
            if r["id"] == b.id and r["registry"] == "skills":
                self.err(w, "ref-self", "skill references itself")
            if r["registry"] == "skills":
                if not validate_range(r["version"]):
                    self.err(w, "ref-range", f"invalid version/range {r['version']!r} for {r['id']}")
                elif r.get("digest") and not re.fullmatch(r"\d+\.\d+\.\d+(-[0-9A-Za-z.-]+)?", r["version"]):
                    self.err(w, "ref-digest-range", f"{r['id']}: a digest may only accompany an exact version, not {r['version']!r}")
            elif r.get("digest") and not re.fullmatch(r"\d+\.\d+\.\d+(-[0-9A-Za-z.-]+)?", r["version"]):
                self.err(w, "ref-digest-range", f"{r['registry']}:{r['id']}: a digest may only accompany an exact version")

    def check_security(self, b: Bundle):
        w, m = b.rel + "/manifest.yaml", b.manifest
        s = b.spec["security_profile"]
        cls = CLASSIFICATIONS.get(m["security"]["classification"], 0)
        eff = EFFECT_RANK[s["side_effects"]]
        caps = {c["id"] for c in b.spec["requires"]["capabilities"] if not c.get("optional")}
        allcaps = {c["id"] for c in b.spec["requires"]["capabilities"]}
        if s["side_effects"] == "destructive":
            if cls < CLASSIFICATIONS["high"]:
                self.err(w, "sec-destructive-class", "destructive side effects require classification >= high")
            if not s["hitl"]["required"]:
                self.err(w, "sec-destructive-hitl", "destructive side effects require hitl.required: true")
            if not s.get("destructive_operations"):
                self.err(w, "sec-destructive-ops", "destructive side effects require destructive_operations to be listed")
        elif s.get("destructive_operations"):
            self.err(w, "sec-destructive-decl", "destructive_operations listed but side_effects is not 'destructive'")
        if eff >= EFFECT_RANK["write-external"] and cls < CLASSIFICATIONS["moderate"]:
            self.err(w, "sec-write-class", "write-external side effects require classification >= moderate")
        for p in s["permissions"]:
            if p["access"] == "write" and eff < EFFECT_RANK["write-external"] and not p["scope"].startswith("fs:workspace"):
                self.err(w, "sec-perm-effect", f"permission {p['scope']}:write implies side_effects >= write-external")
            if p["access"] == "delete" and eff < EFFECT_RANK["destructive"]:
                self.err(w, "sec-perm-effect", f"permission {p['scope']}:delete implies side_effects: destructive")
        sens = set(s["data_sensitivity"])
        if sens & {"personal", "regulated"} and cls < CLASSIFICATIONS["high"]:
            self.err(w, "sec-sensitivity-class", "personal/regulated data requires classification >= high")
        if "confidential" in sens and cls < CLASSIFICATIONS["moderate"]:
            self.err(w, "sec-sensitivity-class", "confidential data requires classification >= moderate")
        if m["security"]["classification"] == "critical" and not s["hitl"]["required"]:
            self.err(w, "sec-critical-hitl", "critical classification requires hitl.required: true")
        if s["hitl"]["required"] and not s["hitl"].get("triggers"):
            self.err(w, "sec-hitl-triggers", "hitl.required is true but no triggers are listed")
        if s["hitl"]["required"] and "cap.human.confirm" not in allcaps:
            self.err(w, "sec-hitl-cap", "hitl.required needs capability cap.human.confirm in requires.capabilities")
        if "cap.vcs.write" in caps and eff < EFFECT_RANK["write-external"]:
            self.err(w, "sec-cap-effect", "cap.vcs.write implies side_effects >= write-external")
        net = s.get("network")
        needs_net = any(c.startswith("cap.web.") for c in allcaps) or eff > 0 or \
            any(t["kind"] in ("mcp", "api") for t in b.spec["requires"].get("tools", []))
        if needs_net and net is None:
            self.err(w, "sec-network-missing", "network egress must be declared (capabilities/tools/side effects imply network use)")
        if net:
            if net["egress"] == "none" and needs_net:
                self.err(w, "sec-network-none", "egress: none contradicts declared network-using capabilities/tools/side effects")
            if net["egress"] == "allowlist" and not net.get("allowed_domains"):
                self.err(w, "sec-network-allowlist", "egress: allowlist requires allowed_domains")
        au = s["authentication"]
        if au["required"] and (not au.get("methods") or au.get("credential_handling") != "runtime-injected"):
            self.err(w, "sec-auth", "authentication.required needs methods and credential_handling: runtime-injected")

    def check_origin_and_trust(self, b: Bundle):
        w, m, meta, spec = b.rel, b.manifest, b.meta, b.spec
        trust, otype = spec["trust"], meta["origin"]["type"]
        sources = m["provenance"]["sourceRefs"]
        stage = b.stage()
        promoted = b.tier == "skills" or stage == "approved"
        if trust["tier"] == "unreviewed" and promoted:
            self.err(w, "prov-unreviewed", "trust tier 'unreviewed' cannot be approved or canonical")
        for r in meta["origin"].get("evolution", {}).get("sourceRefs", []):
            if r["registry"] == "skills" and r["id"] == b.id and r["version"] == b.version:
                self.err(w, "origin-self", "evolution sourceRefs may not reference the artifact itself")
        if otype in ("upstream-seed", "discovered"):
            if not sources:
                self.err(w, "prov-sources", f"origin {otype} requires provenance.sourceRefs")
            if trust["tier"] == "first-party":
                self.err(w, "prov-tier", f"origin {otype} cannot have trust tier first-party")
            rel = trust.get("assessment")
            if not rel:
                if stage != "discovered":
                    self.err(w, "prov-assessment", "external artifacts beyond stage 'discovered' require spec.trust.assessment")
            else:
                ap = b.path / rel
                if not ap.is_file():
                    self.err(w, "prov-assessment-missing", f"assessment file {rel} not found")
                else:
                    data = self._load(ap, f"{b.rel}/{rel}", "assessment-parse")
                    if data is not _FAILED and self.schema_check("assessment", data, f"{b.rel}/{rel}", "assessment-schema"):
                        if data["verdict"] in ("reject", "needs-more-inspection") and (promoted or stage in ("drafted", "evaluating")):
                            self.err(w, "prov-verdict", f"assessment verdict {data['verdict']!r} blocks stage {stage or 'canonical'!r}")
                        if data["verdict"] == "proceed-with-restrictions" and not data.get("restrictions"):
                            self.err(w, "prov-restrictions", "verdict proceed-with-restrictions requires restrictions")
            for s in sources:
                if promoted and not s.get("ref") and not s.get("digest"):
                    self.err(w, "prov-pin", f"source {s['uri']} must be pinned (ref or digest) before approval")
        if meta["origin"].get("evolution", {}).get("kind") == "generalised":
            ev = self._load_evidence(b)
            if not ev or not any(r["wisdom"] == "compute" for r in ev.get("refs", [])):
                self.err(w, "prov-generalised-evidence", "generalised evolution must cite >=1 compute evidence ref in provenance/evidence.yaml")

    def check_evals(self, b: Bundle):
        w, spec = b.rel, b.spec
        sp = b.path / spec["evaluation"]["suite"]
        if not sp.is_file():
            self.err(w, "eval-missing", f"eval suite {spec['evaluation']['suite']} not found")
            return
        data = self._load(sp, f"{b.rel}/{spec['evaluation']['suite']}", "eval-parse")
        if data is _FAILED or not self.schema_check("eval-suite", data, f"{b.rel}/{spec['evaluation']['suite']}", "eval-schema"):
            return
        if data["skill"] != b.id:
            self.err(w, "eval-skill", f"suite.skill {data['skill']!r} != metadata.id {b.id!r}")
        ids = [c["id"] for c in data["cases"]]
        if len(ids) != len(set(ids)):
            self.err(w, "eval-dup", "duplicate eval case ids")
        kinds = {c.get("kind", "capability") for c in data["cases"]}
        s = spec["security_profile"]
        cls = CLASSIFICATIONS.get(b.manifest["security"]["classification"], 0)
        if (EFFECT_RANK[s["side_effects"]] > 0 or cls >= 1) and not kinds & {"safety", "adversarial"}:
            self.err(w, "eval-safety", "skills with external effects or classification >= moderate need at least one 'safety' or 'adversarial' eval case")
        if s["hitl"]["required"] and not any(e["type"] == "hitl-requested" for c in data["cases"] for e in c["expect"]):
            self.err(w, "eval-hitl", "hitl.required skills need a case expecting 'hitl-requested'")
        for c in data["cases"]:
            for f in c.get("fixtures", []):
                if not (b.path / "evals" / f).exists():
                    self.err(w, "eval-fixture", f"case {c['id']}: fixture evals/{f} not found")

    def check_files(self, b: Bundle):
        """Explicit filename allow-list, no symlinks, size limits, no runtime tapes/traces/transcripts, no secrets."""
        files = b.files()
        not_allowed = {pr.path for pr in b.hash_problems() if pr.code == "file-not-allowed"}
        if len(files) > MAX_BUNDLE_FILES:
            self.err(b.rel, "bundle-too-many-files", f"{len(files)} files exceeds {MAX_BUNDLE_FILES}")
        if sum(p.stat().st_size for p in files) > MAX_BUNDLE_BYTES:
            self.err(b.rel, "bundle-too-large", f"bundle exceeds {MAX_BUNDLE_BYTES} bytes in total")
        for p in files:
            parts = p.relative_to(b.path).parts
            rel = "/".join(parts)
            where = f"{b.rel}/{rel}"
            if p.suffix.lower() in FORBIDDEN_SUFFIXES or FORBIDDEN_NAME.search(p.name):
                self.err(where, "no-runtime-artifacts",
                         "runtime tapes, trajectories, traces and transcripts stay outside registry Git; reference them via evidence:// pointers")
                continue
            if where in not_allowed:
                continue  # reported by check_hashable
            if p.stat().st_size > MAX_FILE_BYTES:
                self.err(where, "file-too-large", f"{p.stat().st_size} bytes exceeds {MAX_FILE_BYTES}; registry Git holds procedures, not payloads")
                continue
            try:
                text = p.read_bytes().decode("utf-8")
            except UnicodeDecodeError:
                if not is_payload(rel):   # payload files are reported by check_hashable
                    self.err(where, "file-not-text", "allow-listed files must be UTF-8 text")
                continue
            lines = [ln for ln in text.splitlines() if ln.strip()]
            jl = 0
            for ln in lines:
                if ln.lstrip().startswith("{") and ln.rstrip().endswith("}"):
                    try:
                        if isinstance(__import__("json").loads(ln), dict):
                            jl += 1
                    except ValueError:
                        pass
            if (len(lines) >= 5 and jl / len(lines) >= 0.8) or len(TRANSCRIPT_LINE.findall(text)) >= 6 or len(ROLE_JSON.findall(text)) >= 3:
                self.err(where, "runtime-artifact-content", "content looks like a runtime tape/trace/transcript (JSON-lines events or chat turns); keep it out of Git")
            for name, rx in SECRET_PATTERNS:
                mt = rx.search(text)
                if mt and "EXAMPLE" not in mt.group(0).upper() and "<" not in mt.group(0):
                    self.err(f"{where}:{text[:mt.start()].count(chr(10)) + 1}", "secret", f"possible {name}; skills must never contain credentials")

    def _load_evidence(self, b: Bundle):
        p = b.path / "provenance" / "evidence.yaml"
        if not p.exists():
            return None
        try:
            data = load_yaml(p)
        except RegistryError:
            return None  # reported by check_evidence
        return data if isinstance(data, dict) and isinstance(data.get("refs"), list) else None

    def check_evidence(self, b: Bundle):
        p = b.path / "provenance" / "evidence.yaml"
        if not p.exists():
            return
        w = f"{b.rel}/provenance/evidence.yaml"
        data = self._load(p, w, "evidence-parse")
        if data is _FAILED or not self.schema_check("evidence", data, w, "evidence-schema"):
            return
        if data["subject"]["id"] != b.id:
            self.err(w, "evidence-skill", "evidence.subject.id must be this skill")
        seen, known = set(), set(self.released_versions(b.id)) | {b.version}
        for r in data["refs"]:
            if r["evidenceId"] in seen:
                self.err(w, "evidence-dup", f"duplicate evidenceId {r['evidenceId']}")
            seen.add(r["evidenceId"])
            self.check_evidence_domain(b, r["uri"], w, r["evidenceId"])
            if r["subject"]["id"] != b.id:
                self.err(w, "evidence-ref-id", f"{r['evidenceId']}: subject must name this skill")
            elif r["subject"]["version"] not in known and b.tier == "skills":
                self.warn(w, "evidence-ref-version", f"{r['evidenceId']}: version {r['subject']['version']} is not a released version")

    def check_evidence_domain(self, b: Bundle, uri: str, where: str, label: str):
        """Synthetic artifacts may only use synthetic evidence pointers; production artifacts may never use them."""
        synthetic_uri = uri.startswith(SYNTHETIC_EVIDENCE)
        if b.tier == "synthetic" and not synthetic_uri:
            self.err(where, "synthetic-evidence", f"{label}: synthetic artifacts may only use {SYNTHETIC_EVIDENCE}... pointers, not {uri!r}")
        elif b.tier != "synthetic" and synthetic_uri:
            self.err(where, "synthetic-evidence", f"{label}: {SYNTHETIC_EVIDENCE}... pointers are reserved for synthetic artifacts")

    # ---- attestations, approvals, gates ---------------------------------
    def _att_target(self, b: Bundle, att: dict, where: str):
        """Digest- and seal-binding check plus in-bundle ref resolution. Returns the loaded record or None.

        An attestation must name the artifact digest (projection) AND the directory seal: SKILL.md, evals and
        examples are outside the artifact digest, so binding the seal keeps an attestation from surviving a change to
        the procedure it assessed (Skills-registry extension of the v0.2 attestation shape)."""
        digest, seal = b.digest(), b.directory_seal()
        if att["subjectDigest"] != digest:
            self.err(where, "attestation-stale",
                     f"{att['type']} attestation binds digest {att['subjectDigest'][:19]}... but the artifact digest is {digest[:19]}...; re-issue it")
            return None
        if att.get("subjectSeal") is None:
            self.err(where, "attestation-seal-missing", f"{att['type']} attestation must carry subjectSeal (Skills payload is outside the artifact digest)")
            return None
        if att["subjectSeal"] != seal:
            self.err(where, "attestation-stale",
                     f"{att['type']} attestation binds seal {att['subjectSeal'][:19]}... but the directory seal is {seal[:19]}...; the payload changed; re-issue it")
            return None
        ref = att["ref"]
        if ref.startswith("evidence://"):
            self.check_evidence_domain(b, ref, where, att["type"])
            return None  # evidence:// pointers are opaque here (Evidence Protocol deferred)
        rel = ref[len("bundle:"):]
        fp = (b.path / rel).resolve()
        if ".." in Path(rel).parts or not str(fp).startswith(str(b.path.resolve())) or not fp.is_file():
            self.err(where, "attestation-ref", f"{ref} does not resolve to a file inside the bundle")
            return None
        rec = self._load(fp, f"{b.rel}/{rel}", "attestation-parse")
        if rec is _FAILED:
            return None
        if not isinstance(rec, dict):
            self.err(f"{b.rel}/{rel}", "attestation-parse", "attestation record must be a mapping (document is empty or not a mapping)")
            return None
        return rec

    def suite_digest(self, b: Bundle) -> str | None:
        p = b.path / b.spec["evaluation"]["suite"]
        return "sha256:" + sha256_hex(p.read_bytes()) if p.is_file() else None

    def check_attestations(self, b: Bundle):
        w, m = b.rel + "/manifest.yaml", b.manifest
        records: dict[str, dict] = {}
        for i, att in enumerate([*m["attestations"], *m["security"]["approvals"]]):
            rec = self._att_target(b, att, f"{w}#attestation[{i}]")
            if rec is not None:
                records[att["ref"]] = rec
        # evaluation attestations: suite identity/digest must match this artifact's suite
        suite_digest = self.suite_digest(b)
        for i, att in enumerate(m["attestations"]):
            if att["type"] != "evaluation":
                continue
            suite = att["suite"]
            exp = (b.spec["evaluation"]["suite"], b.version, suite_digest)
            if (suite["id"], suite["version"], suite["digest"]) != exp:
                self.err(f"{w}#attestation[{i}]", "eval-attestation-suite",
                         f"suite must be {{id: {exp[0]}, version: {exp[1]}, digest: {exp[2]}}} (the suite is versioned with the artifact)")
        promoted = b.tier == "skills" or b.stage() == "approved"
        if not promoted:
            return
        gov = [a for a in m["security"]["approvals"] if a["type"] == "governance"]
        if not gov:
            self.err(w, "gate-approval", "promotion requires a digest-bound `governance` entry in security.approvals")
            return
        ap_att = gov[0]
        if not ap_att["ref"].startswith("bundle:"):
            return
        a = records.get(ap_att["ref"])
        if a is None:
            return
        aw = f"{b.rel}/{ap_att['ref'][7:]}"
        if not self.schema_check("approval", a, aw, "approval-schema"):
            return
        sub = a["subject"]
        if (sub["registry"], sub["id"], sub["version"], sub["digest"], sub["directorySeal"]) != \
                ("skills", b.id, b.version, b.digest(), b.directory_seal()):
            self.err(aw, "approval-subject", f"approval subject is not {b.id}@{b.version} at the current digest and directory seal")
        if b.manifest["security"]["classification"] in ("high", "critical") and not a.get("securityReviewedBy"):
            self.err(aw, "approval-security", "high/critical skills require securityReviewedBy")
        passing = [x for x in m["attestations"] if x["type"] == "evaluation" and x.get("result") == "pass"]
        if a["basis"] == "evaluation":
            if "exception" in a:
                self.err(aw, "approval-basis", "basis evaluation must not carry an exception block")
            ref = a.get("evaluationRef")
            ev_att = next((x for x in m["attestations"] if x["type"] == "evaluation" and x["ref"] == ref), None)
            if not ref or ev_att is None:
                self.err(aw, "approval-evaluation", "basis evaluation requires evaluationRef matching an `evaluation` attestation in attestations")
                return
            if ev_att["result"] != "pass":
                self.err(aw, "eval-attestation-result", f"promotion requires a passing evaluation, not result {ev_att['result']!r}")
            r = records.get(ref)
            if r is not None:
                self.check_eval_report(b, r, ref[7:] if ref.startswith("bundle:") else ref)
        else:
            ex = a.get("exception")
            if not ex:
                self.err(aw, "approval-exception", "basis protocol-exception requires an exception block")
                return
            if passing:
                self.err(aw, "exception-claims-evaluation",
                         "a protocol exception must not be accompanied by a passing evaluation attestation: an exception is never an evaluation")
            try:
                expires = Version(ex["expiresOnVersion"])
            except ValueError:
                self.err(aw, "exception-version", f"expiresOnVersion {ex['expiresOnVersion']!r} is not a valid SemVer version")
                return
            if expires > EXCEPTION_CEILING:
                self.err(aw, "exception-version",
                         f"expiresOnVersion {ex['expiresOnVersion']} is later than {EXCEPTION_CEILING.text}: the transitional exception cannot be extended (Protocol v0.2 section 11)")
            if Version(b.version) >= expires:
                self.err(aw, "exception-expired", f"protocol exception expired at {ex['expiresOnVersion']}; executed evaluations required")
            else:
                self.warn(aw, "protocol-exception",
                          f"{b.id}@{b.version} is canonical under a TEMPORARY PROTOCOL EXCEPTION ({ex['rule']}, expires at {ex['expiresOnVersion']}); no executed evaluations: {ex['reason']}")

    def check_eval_report(self, b: Bundle, r: dict, rel: str):
        rw = f"{b.rel}/{rel}"
        if not self.schema_check("eval-report", r, rw, "report-schema"):
            return
        sub = r["subject"]
        if (sub["id"], sub["version"], sub["digest"], sub["directorySeal"]) != (b.id, b.version, b.digest(), b.directory_seal()):
            self.err(rw, "report-digest", "evaluation report is not bound to this exact artifact digest and directory seal")
        if r["suiteDigest"] != self.suite_digest(b):
            self.err(rw, "report-suite", "suiteDigest does not match the current suite file")
        s = r["summary"]
        if s["passed"] > s["cases"] or abs(s["passed"] / s["cases"] - s["passRate"]) > 0.005:
            self.err(rw, "report-consistency", "summary passRate inconsistent with passed/cases")
        if s["passRate"] < b.spec["evaluation"]["min_pass_rate"]:
            self.err(rw, "report-threshold", f"passRate {s['passRate']} < required {b.spec['evaluation']['min_pass_rate']}")
        for c in r.get("caseResults", []):
            if c.get("evidenceUri"):
                self.check_evidence_domain(b, c["evidenceUri"], rw, c["id"])

    def check_release(self, b: Bundle):
        w = b.rel
        rel = next((r for r in (self.ledgers.get(b.id) or {}).get("releases", []) if r["version"] == b.version), None)
        if rel is None:
            self.err(w, "release-missing", f"version {b.version} has no ledger entry; run `zskill release {b.id}`")
        else:
            for key, cur in (("digest", b.digest()), ("directory_seal", b.directory_seal()),
                             ("contract_digest", b.contract_digest()), ("security_digest", b.security_digest())):
                if rel[key] != cur:
                    self.err(w, "release-mutated",
                             f"released version {b.version} content changed ({key} mismatch). Released versions are immutable: bump the version instead.")
                    break
        led = self.ledgers.get(b.id)
        if led and led["releases"]:
            latest = max(led["releases"], key=lambda r: Version(r["version"]))["version"]
            if Version(b.version) < Version(latest):
                self.err(w, "release-stale", f"manifest version {b.version} is older than latest released {latest}")

    # ---- references / composition --------------------------------------
    def check_references(self, b: Bundle):
        if b.rel not in self.valid:
            return
        m, w = b.manifest, b.rel
        comp = {c["id"]: c for c in b.spec.get("composition", [])}
        _, body = b.skill_md()
        refs = list(m["references"]) + list(b.meta.get("origin", {}).get("evolution", {}).get("sourceRefs", []))
        for r in refs:
            if r["registry"] != "skills":
                continue  # other registries: structural validation only, never network
            if r["id"] in self.synthetic_ids and b.tier != "synthetic":
                self.err(w, "synthetic-leak", f"production artifact references synthetic {r['id']}")
                continue
            if b.tier == "synthetic" and r["id"] in self.synthetic_ids:
                continue
            t = self.by_id.get(r["id"])
            if t is None:
                self.err(w, "ref-unresolved", f"reference skills:{r['id']} not found in this registry")
                continue
            avail = set(self.released_versions(t.id))
            if t.tier == "skills" or b.tier != "skills":
                avail.add(t.version)
            if not validate_range(r["version"]):
                continue
            ok = [v for v in avail if satisfies(v, r["version"])]
            if not ok:
                self.err(w, "ref-version", f"no available version of {r['id']} satisfies {r['version']} (available: {sorted(avail)})")
            if r.get("digest") and ok:
                rel = next((x for x in (self.ledgers.get(t.id) or {}).get("releases", []) if x["version"] == r["version"]), None)
                pinned = rel["digest"] if rel else (t.digest() if t.version == r["version"] and t.rel not in self.unhashable else None)
                if pinned and pinned != r["digest"]:
                    self.err(w, "ref-digest", f"{r['id']}@{r['version']} digest {r['digest'][:19]}... does not match registry digest {pinned[:19]}...")
        if b.tier == "synthetic":
            return
        for r in m["references"]:
            if r["registry"] != "skills":
                continue
            t = self.by_id.get(r["id"])
            if t is None or r["id"] in self.synthetic_ids or t.rel not in self.valid:
                continue
            c = comp.get(r["id"], {})
            if b.tier == "skills" and t.tier != "skills":
                self.err(w, "ref-tier", f"canonical skill cannot depend on non-canonical {r['id']}")
            elif b.tier == "skills":
                state = self.lifecycle_of(t.id, t.version)
                if state == "revoked":
                    self.err(w, "ref-revoked", f"depends on revoked skill {r['id']}")
                elif state == "deprecated":
                    self.warn(w, "ref-deprecated", f"depends on deprecated skill {r['id']}")
            if c.get("role", "composes") == "composes" and f"skills:{r['id']}" not in body:
                self.err(w + "/SKILL.md", "ref-unreferenced",
                         f"composed skill must be named as `skills:{r['id']}` in SKILL.md; composition must be visible in the procedure")
            self.check_envelope(b, t, c)
        self.check_graph(b)

    def check_envelope(self, b: Bundle, t: Bundle, comp: dict):
        """Parent must declare at least the privileges of every child (no privilege hiding)."""
        w, pm, cm = b.rel, b.manifest, t.manifest
        ps, cs = pm["spec"]["security_profile"], cm["spec"]["security_profile"]
        pc, cc = pm["security"]["classification"], cm["security"]["classification"]
        if CLASSIFICATIONS.get(pc, 0) < CLASSIFICATIONS.get(cc, 0):
            self.err(w, "env-class", f"classification {pc} < composed {t.id} ({cc})")
        if EFFECT_RANK[ps["side_effects"]] < EFFECT_RANK[cs["side_effects"]]:
            self.err(w, "env-effects", f"side_effects {ps['side_effects']} < composed {t.id} ({cs['side_effects']})")
        if cs["hitl"]["required"] and not ps["hitl"]["required"]:
            self.err(w, "env-hitl", f"composed {t.id} requires HITL; parent must too")
        missing = set(cs["data_sensitivity"]) - set(ps["data_sensitivity"])
        if missing:
            self.err(w, "env-sensitivity", f"parent must declare data sensitivity {sorted(missing)} of composed {t.id}")
        pperm = {(p["scope"], p["access"]) for p in ps["permissions"]}
        for p in cs["permissions"]:
            if (p["scope"], p["access"]) not in pperm:
                self.err(w, "env-permission", f"parent must declare permission {p['scope']}:{p['access']} used by composed {t.id}")
        pn, cn = ps.get("network"), cs.get("network")
        if cn and EGRESS_RANK[cn["egress"]] > EGRESS_RANK[(pn or {"egress": "none"})["egress"]]:
            self.err(w, "env-network", f"parent egress narrower than composed {t.id}")
        elif cn and pn and cn["egress"] == "allowlist" and pn["egress"] == "allowlist" \
                and not set(cn.get("allowed_domains", [])) <= set(pn.get("allowed_domains", [])):
            self.err(w, "env-network-domains", f"parent allowed_domains must include those of {t.id}")
        if not comp.get("optional"):
            pcaps = set(pm["security"]["capabilities"])
            for c in cm["spec"]["requires"]["capabilities"]:
                if not c.get("optional") and c["id"] not in pcaps:
                    self.err(w, "env-capability", f"parent must require capability {c['id']} needed by {t.id}")
            pcl, ccl = set(pm["spec"]["compatibility"]["agent_classes"]), set(cm["spec"]["compatibility"]["agent_classes"])
            if "any" not in ccl and not ("any" in pcl and ccl) and not pcl <= ccl:
                self.err(w, "env-agent-class", f"parent agent classes {sorted(pcl)} not all supported by {t.id} {sorted(ccl)}")

    def check_graph(self, b: Bundle):
        w = b.rel

        def children(bid):
            bb = self.by_id.get(bid)
            return [r["id"] for r in (bb.manifest["references"] if bb and bb.rel in self.valid else []) if r["registry"] == "skills"]
        closure: set[str] = set()
        depth = 0

        def dfs(n, path):
            nonlocal depth
            if n in path:
                self.err(w, "ref-cycle", "reference cycle: " + " -> ".join([*path[path.index(n):], n]))
                return
            depth = max(depth, len(path))
            for c in children(n):
                closure.add(c)
                dfs(c, [*path, n])
        dfs(b.id, [])
        if depth > MAX_DEP_DEPTH:
            self.err(w, "ref-depth", f"composition depth {depth} exceeds {MAX_DEP_DEPTH}")
        if len(closure) > MAX_CLOSURE:
            self.err(w, "ref-closure", f"transitive closure has {len(closure)} skills; max {MAX_CLOSURE}")


def validate(root: Path) -> list[Issue]:
    return Registry(root).run()
