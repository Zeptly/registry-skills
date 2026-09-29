"""Registry validation. Every rule maps to a documented requirement in docs/."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

from .bundle import Bundle, load_bundles, load_yaml, sha256_hex
from .semver import Version, bump_level, satisfies, validate_range

CANDIDATE_STATUSES = {"discovered", "inspected", "candidate", "evaluating", "approved", "rejected"}
CANONICAL_STATUSES = {"active", "deprecated", "retired"}
RELEASED_STATUSES = CANONICAL_STATUSES
REQUIRED_SECTIONS = ["When to use", "Procedure", "Output", "Guardrails"]
MAX_DEP_DEPTH = 3
MAX_CLOSURE = 20
TINY_MAX_CHARS = 8000

CLASS_RANK = {"low": 0, "moderate": 1, "high": 2, "critical": 3}
EFFECT_RANK = {"none": 0, "read-external": 1, "write-external": 2, "destructive": 3}
EGRESS_RANK = {"none": 0, "allowlist": 1, "open": 2}

SECRET_PATTERNS = [
    ("AWS access key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("GitHub token", re.compile(r"\b(ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{20,}")),
    ("API secret key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}")),
    ("Slack token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}")),
    ("private key block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("URL with embedded credentials", re.compile(r"[a-z]+://[^/\s:@]+:[^/\s:@]{3,}@")),
    ("assigned secret", re.compile(r"(?i)\b(api[_-]?key|secret|token|passwd|password)\b\s*[:=]\s*['\"]?[A-Za-z0-9/+_\-]{16,}")),
]


@dataclass(frozen=True)
class Issue:
    level: str  # error | warning
    where: str
    code: str
    msg: str

    def __str__(self):
        return f"{self.level.upper():7} {self.where}: [{self.code}] {self.msg}"


class Registry:
    def __init__(self, root: Path):
        self.root = root
        self.issues: list[Issue] = []
        self.schemas = {n: load_yaml(root / "schemas" / f"{n}.schema.json") for n in
                        ("manifest", "evidence", "eval-suite", "eval-report", "approval", "assessment", "release-ledger")}
        self.domains = set((load_yaml(root / "vocab/domains.yaml") or {}).get("domains", {}))
        self.classes = set((load_yaml(root / "vocab/agent-classes.yaml") or {}).get("agent_classes", {}))
        self.caps = set((load_yaml(root / "vocab/capabilities.yaml") or {}).get("capabilities", {}))
        self.bundles = load_bundles(root)
        self.by_id: dict[str, Bundle] = {}
        self.ledgers: dict[str, dict] = {}

    # ---- helpers -------------------------------------------------------
    def err(self, where, code, msg):
        self.issues.append(Issue("error", where, code, msg))

    def warn(self, where, code, msg):
        self.issues.append(Issue("warning", where, code, msg))

    def schema_check(self, name, data, where, code):
        v = Draft202012Validator(self.schemas[name], format_checker=FormatChecker())
        ok = True
        for e in sorted(v.iter_errors(data), key=lambda e: list(map(str, e.path))):
            loc = "/".join(map(str, e.path)) or "<root>"
            self.err(where, code, f"{loc}: {e.message}")
            ok = False
        return ok

    def released_versions(self, skill_id: str) -> list[str]:
        led = self.ledgers.get(skill_id) or {}
        return [r["version"] for r in led.get("releases", [])]

    # ---- top level -----------------------------------------------------
    def run(self) -> list[Issue]:
        self.check_ledgers()
        seen: dict[str, Bundle] = {}
        for b in self.bundles:
            if b.manifest_error:
                self.err(b.rel, "manifest-parse", b.manifest_error)
                continue
            if b.id in seen:
                self.err(b.rel, "duplicate-id", f"id {b.id} also used by {seen[b.id].rel}")
                continue
            if b.id:
                seen[b.id] = b
        self.by_id = seen
        for b in self.bundles:
            if b.manifest_error:
                continue
            self.check_bundle(b)
        for b in self.bundles:
            if not b.manifest_error and b.id in self.by_id and self.by_id[b.id] is b:
                self.check_dependencies(b)
        self.check_orphan_ledgers()
        return self.issues

    # ---- ledgers -------------------------------------------------------
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
                self.err(where, "ledger-name", f"file name must be <skill-id>.yaml (skill={data['skill']})")
            self.ledgers[data["skill"]] = data
            prev = None
            seen = set()
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
                                     f"{r['version']}: security block changed vs {prev['version']} but bump is {lvl}; security changes require a MAJOR bump")
                        elif r["contract_digest"] != prev["contract_digest"] and lvl not in ("major", "minor"):
                            self.err(where, "semver-contract",
                                     f"{r['version']}: contract (inputs/outputs/requires/dependencies/agent classes) changed vs {prev['version']} but bump is {lvl}; requires MINOR or MAJOR")
                prev = r

    def check_orphan_ledgers(self):
        for sid in self.ledgers:
            b = self.by_id.get(sid)
            if b is None or b.tier != "skills":
                self.err(f"registry/releases/{sid}.yaml", "ledger-orphan", "ledger exists but no canonical skill has this id")

    # ---- per-bundle ----------------------------------------------------
    def check_bundle(self, b: Bundle):
        w, m = b.rel, b.manifest
        if not self.schema_check("manifest", m, w + "/manifest.yaml", "manifest-schema"):
            return
        parts = b.path.relative_to(b.root).parts  # tier/domain/name
        if m["domain"] != parts[1]:
            self.err(w, "layout-domain", f"manifest domain {m['domain']!r} != directory {parts[1]!r}")
        if m["name"] != parts[2]:
            self.err(w, "layout-name", f"manifest name {m['name']!r} != directory {parts[2]!r}")
        if m["domain"] not in self.domains:
            self.err(w, "vocab-domain", f"unknown domain {m['domain']!r} (vocab/domains.yaml)")
        allowed = CANDIDATE_STATUSES if b.tier == "candidates" else CANONICAL_STATUSES
        if m["status"] not in allowed:
            self.err(w, "layout-status", f"status {m['status']!r} is not valid under {b.tier}/ (allowed: {sorted(allowed)})")
        if m["status"] in ("deprecated", "retired") and "deprecation" not in m:
            self.err(w, "deprecation-missing", f"status {m['status']} requires a deprecation block")
        if "deprecation" in m and m["status"] not in ("deprecated", "retired"):
            self.err(w, "deprecation-status", "deprecation block only valid for deprecated/retired skills")
        self.check_skill_md(b)
        self.check_compat_and_requires(b)
        self.check_security(b)
        self.check_provenance(b)
        self.check_evals(b)
        self.check_secrets(b)
        self.check_evidence(b)
        if m["status"] in RELEASED_STATUSES:
            self.check_release(b)
        elif m["status"] == "approved":
            self.check_approval(b, require_release=False)
        if b.tier == "candidates" and m["version"] in self.released_versions(m["id"]):
            self.err(w, "candidate-released", f"version {m['version']} is already in the release ledger")

    def check_skill_md(self, b: Bundle):
        w, m = b.rel + "/SKILL.md", b.manifest
        fm, body = b.skill_md()
        if fm is None:
            self.err(w, "skillmd-frontmatter", "SKILL.md missing or lacks valid YAML frontmatter (--- ... ---)")
            return
        for k in ("name", "description"):
            if fm.get(k) != m[k]:
                self.err(w, "skillmd-mismatch", f"frontmatter {k} must equal manifest {k}")
        extra = set(fm) - {"name", "description", "license", "compatibility", "metadata", "allowed-tools"}
        if extra:
            self.warn(w, "skillmd-frontmatter-extra", f"non-portable frontmatter keys {sorted(extra)} (Agent Skills allows name, description, license, compatibility, metadata, allowed-tools)")
        heads = {h.strip().lower() for h in re.findall(r"^##\s+(.+)$", body, re.M)}
        for sec in REQUIRED_SECTIONS:
            if sec.lower() not in heads:
                self.err(w, "skillmd-section", f"missing required section '## {sec}'")
        if "tiny" in m["compatibility"]["agent_classes"] and len(body) > TINY_MAX_CHARS:
            self.err(w, "skillmd-size-tiny", f"body is {len(body)} chars; skills for 'tiny' agents must be <= {TINY_MAX_CHARS}")
        elif len(body.splitlines()) > 500:
            self.warn(w, "skillmd-size", "SKILL.md exceeds 500 lines; move detail into examples/ or referenced files")

    def check_compat_and_requires(self, b: Bundle):
        w, m = b.rel, b.manifest
        c = m["compatibility"]
        if c["protocol"] != 1:
            self.err(w, "protocol", f"unsupported protocol {c['protocol']} (this tooling supports 1)")
        for k in c["agent_classes"]:
            if k not in self.classes:
                self.err(w, "vocab-agent-class", f"unknown agent class {k!r}")
        for cap in m["requires"]["capabilities"]:
            if cap["id"] not in self.caps:
                self.err(w, "vocab-capability", f"unknown capability {cap['id']!r} (vocab/capabilities.yaml)")
        for d in m.get("dependencies", []):
            if not validate_range(d["version"]):
                self.err(w, "dep-range", f"invalid version range {d['version']!r} for {d['id']}")

    def check_security(self, b: Bundle):
        w, m = b.rel + "/manifest.yaml", b.manifest
        s = m["security"]
        cls, eff = CLASS_RANK[s["classification"]], EFFECT_RANK[s["side_effects"]]
        caps = {c["id"] for c in m["requires"]["capabilities"] if not c.get("optional")}
        allcaps = {c["id"] for c in m["requires"]["capabilities"]}
        if s["side_effects"] == "destructive":
            if cls < CLASS_RANK["high"]:
                self.err(w, "sec-destructive-class", "destructive side effects require classification >= high")
            if not s["hitl"]["required"]:
                self.err(w, "sec-destructive-hitl", "destructive side effects require hitl.required: true")
            if not s.get("destructive_operations"):
                self.err(w, "sec-destructive-ops", "destructive side effects require destructive_operations to be listed")
        elif s.get("destructive_operations"):
            self.err(w, "sec-destructive-decl", "destructive_operations listed but side_effects is not 'destructive'")
        if eff >= EFFECT_RANK["write-external"] and cls < CLASS_RANK["moderate"]:
            self.err(w, "sec-write-class", "write-external side effects require classification >= moderate")
        for p in s["permissions"]:
            if p["access"] == "write" and eff < EFFECT_RANK["write-external"] and not p["scope"].startswith("fs:workspace"):
                self.err(w, "sec-perm-effect", f"permission {p['scope']}:write implies side_effects >= write-external")
            if p["access"] == "delete" and eff < EFFECT_RANK["destructive"]:
                self.err(w, "sec-perm-effect", f"permission {p['scope']}:delete implies side_effects: destructive")
        sens = set(s["data_sensitivity"])
        if sens & {"personal", "regulated"} and cls < CLASS_RANK["high"]:
            self.err(w, "sec-sensitivity-class", "personal/regulated data requires classification >= high")
        if "confidential" in sens and cls < CLASS_RANK["moderate"]:
            self.err(w, "sec-sensitivity-class", "confidential data requires classification >= moderate")
        if s["classification"] == "critical" and not s["hitl"]["required"]:
            self.err(w, "sec-critical-hitl", "critical classification requires hitl.required: true")
        if s["hitl"]["required"] and not s["hitl"].get("triggers"):
            self.err(w, "sec-hitl-triggers", "hitl.required is true but no triggers are listed")
        if s["hitl"]["required"] and "cap.human.confirm" not in allcaps:
            self.err(w, "sec-hitl-cap", "hitl.required needs capability cap.human.confirm in requires.capabilities")
        if "cap.vcs.write" in caps and eff < EFFECT_RANK["write-external"]:
            self.err(w, "sec-cap-effect", "cap.vcs.write implies side_effects >= write-external")
        net = s.get("network")
        needs_net = any(c.startswith("cap.web.") for c in allcaps) or eff > 0 or \
            any(t["kind"] in ("mcp", "api") for t in m["requires"].get("tools", []))
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
        if not au["required"] and any(t["kind"] == "mcp" for t in m["requires"].get("tools", [])) and eff >= EFFECT_RANK["write-external"]:
            self.warn(w, "sec-auth-mcp", "writes via MCP declared without authentication; confirm this is intended")

    def check_provenance(self, b: Bundle):
        w, m = b.rel, b.manifest
        p = m["provenance"]
        if p["trust_tier"] == "unreviewed" and m["status"] in ("approved", *CANONICAL_STATUSES):
            self.err(w, "prov-unreviewed", "trust_tier 'unreviewed' cannot be approved or canonical")
        if p["origin"] in ("external-discovery", "imported"):
            if not p.get("sources"):
                self.err(w, "prov-sources", f"origin {p['origin']} requires provenance.sources")
            if p["trust_tier"] == "first-party":
                self.err(w, "prov-tier", f"origin {p['origin']} cannot be trust_tier first-party")
            rel = p.get("assessment")
            if not rel:
                if m["status"] != "discovered":
                    self.err(w, "prov-assessment", "external skills beyond 'discovered' require provenance.assessment")
            else:
                ap = b.path / rel
                if not ap.exists():
                    self.err(w, "prov-assessment-missing", f"assessment file {rel} not found")
                else:
                    data = load_yaml(ap)
                    if self.schema_check("assessment", data, f"{b.rel}/{rel}", "assessment-schema"):
                        if data["verdict"] in ("reject", "needs-more-inspection") and m["status"] not in ("discovered", "inspected", "rejected"):
                            self.err(w, "prov-verdict", f"assessment verdict {data['verdict']!r} blocks status {m['status']!r}")
                        if data["verdict"] == "proceed-with-restrictions" and not data.get("restrictions"):
                            self.err(w, "prov-restrictions", "verdict proceed-with-restrictions requires restrictions")
            for s in p.get("sources", []):
                if m["status"] in ("approved", *CANONICAL_STATUSES) and not s.get("ref") and not s.get("digest"):
                    self.err(w, "prov-pin", f"source {s['uri']} must be pinned (ref or digest) before approval")
        if p["origin"] == "zep-generalised":
            ev = self._load_evidence(b)
            if not ev or not any(r["wisdom"] == "compute" for r in ev.get("refs", [])):
                self.err(w, "prov-zep-evidence", "zep-generalised skills must cite >=1 compute evidence ref in provenance/evidence.yaml")

    def check_evals(self, b: Bundle):
        w, m = b.rel, b.manifest
        sp = b.path / m["evaluation"]["suite"]
        if not sp.exists():
            self.err(w, "eval-missing", f"eval suite {m['evaluation']['suite']} not found")
            return
        data = load_yaml(sp)
        if not self.schema_check("eval-suite", data, f"{b.rel}/{m['evaluation']['suite']}", "eval-schema"):
            return
        if data["skill"] != m["id"]:
            self.err(w, "eval-skill", f"suite.skill {data['skill']!r} != manifest id {m['id']!r}")
        ids = [c["id"] for c in data["cases"]]
        if len(ids) != len(set(ids)):
            self.err(w, "eval-dup", "duplicate eval case ids")
        kinds = {c.get("kind", "capability") for c in data["cases"]}
        s = m["security"]
        if (EFFECT_RANK[s["side_effects"]] > 0 or CLASS_RANK[s["classification"]] >= 1) and not kinds & {"safety", "adversarial"}:
            self.err(w, "eval-safety", "skills with external effects or classification >= moderate need at least one 'safety' or 'adversarial' eval case")
        if s["hitl"]["required"] and not any(e["type"] == "hitl-requested" for c in data["cases"] for e in c["expect"]):
            self.err(w, "eval-hitl", "hitl.required skills need a case expecting 'hitl-requested'")
        for c in data["cases"]:
            for f in c.get("fixtures", []):
                if not (b.path / "evals" / f).exists():
                    self.err(w, "eval-fixture", f"case {c['id']}: fixture evals/{f} not found")

    def check_secrets(self, b: Bundle):
        for p in b.files():
            if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".gif", ".pdf", ".zip"):
                continue
            try:
                text = p.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                self.warn(f"{b.rel}/{p.relative_to(b.path)}", "secret-binary", "non-text file cannot be scanned for secrets")
                continue
            for name, rx in SECRET_PATTERNS:
                mt = rx.search(text)
                if mt and "EXAMPLE" not in mt.group(0).upper() and "<" not in mt.group(0):
                    line = text[: mt.start()].count("\n") + 1
                    self.err(f"{b.rel}/{p.relative_to(b.path)}:{line}", "secret", f"possible {name}; skills must never contain credentials")

    def _load_evidence(self, b: Bundle):
        p = b.path / "provenance" / "evidence.yaml"
        return load_yaml(p) if p.exists() else None

    def check_evidence(self, b: Bundle):
        p = b.path / "provenance" / "evidence.yaml"
        if not p.exists():
            return
        w = f"{b.rel}/provenance/evidence.yaml"
        data = load_yaml(p)
        if not self.schema_check("evidence", data, w, "evidence-schema"):
            return
        if data["skill"] != b.id:
            self.err(w, "evidence-skill", f"evidence.skill {data['skill']!r} != manifest id")
        seen = set()
        known = set(self.released_versions(b.id)) | {b.version}
        for r in data["refs"]:
            if r["evidence_id"] in seen:
                self.err(w, "evidence-dup", f"duplicate evidence_id {r['evidence_id']}")
            seen.add(r["evidence_id"])
            sid, ver = r["skill_ref"].split("@")
            if sid != b.id:
                self.err(w, "evidence-ref-id", f"{r['evidence_id']}: skill_ref must name this skill")
            elif ver not in known and b.tier == "skills":
                self.warn(w, "evidence-ref-version", f"{r['evidence_id']}: references version {ver} which is not a released version")
            if r["wisdom"] == "compute" and "skill_digest" not in r and b.tier == "skills":
                self.warn(w, "evidence-digest", f"{r['evidence_id']}: compute evidence should record skill_digest for exact reproducibility")

    # ---- release / approval ------------------------------------------
    def check_release(self, b: Bundle):
        w, m = b.rel, b.manifest
        rel = next((r for r in (self.ledgers.get(m["id"]) or {}).get("releases", []) if r["version"] == m["version"]), None)
        if rel is None:
            self.err(w, "release-missing", f"version {m['version']} has no ledger entry; run `zskill release {m['id']}`")
        else:
            for key, cur in (("digest", b.digest()), ("contract_digest", b.contract_digest()), ("security_digest", b.security_digest())):
                if rel[key] != cur:
                    self.err(w, "release-mutated",
                             f"released version {m['version']} content changed ({key} mismatch). Released versions are immutable: bump the version instead.")
                    break
        led = self.ledgers.get(m["id"])
        if led and led["releases"]:
            latest = max(led["releases"], key=lambda r: Version(r["version"]))["version"]
            if Version(m["version"]) < Version(latest):
                self.err(w, "release-stale", f"manifest version {m['version']} is older than latest released {latest}")
        if m["status"] == "active":
            self.check_approval(b, require_release=True)

    def check_approval(self, b: Bundle, require_release: bool):
        w, m = b.rel, b.manifest
        ap = b.path / "provenance" / "approval.yaml"
        if not ap.exists():
            self.err(w, "approval-missing", "provenance/approval.yaml required")
            return
        aw = f"{b.rel}/provenance/approval.yaml"
        a = load_yaml(ap)
        if not self.schema_check("approval", a, aw, "approval-schema"):
            return
        if a["skill_ref"] != f"{m['id']}@{m['version']}":
            self.err(aw, "approval-ref", f"approval is for {a['skill_ref']}, not {m['id']}@{m['version']}")
        digest = b.digest()
        if a["basis"] == "eval-report":
            rp = b.path / "provenance" / a.get("eval_report", "")
            if not a.get("eval_report") or not rp.is_file():
                self.err(aw, "approval-report", "basis eval-report requires eval_report pointing to an existing file under provenance/")
                return
            r = load_yaml(rp)
            rw = f"{b.rel}/provenance/{a['eval_report']}"
            if not self.schema_check("eval-report", r, rw, "report-schema"):
                return
            if r["skill_ref"] != a["skill_ref"] or r["skill_digest"] != digest:
                self.err(rw, "report-digest", "eval report is not bound to this exact skill content (skill_ref/skill_digest mismatch)")
            suite = (b.path / m["evaluation"]["suite"]).read_bytes().replace(b"\r\n", b"\n")
            if r["suite_digest"] != "sha256:" + sha256_hex(suite):
                self.err(rw, "report-suite", "suite_digest does not match current evals/suite.yaml")
            s = r["summary"]
            if s["passed"] > s["cases"] or abs(s["passed"] / s["cases"] - s["pass_rate"]) > 0.005:
                self.err(rw, "report-consistency", "summary pass_rate inconsistent with passed/cases")
            if s["pass_rate"] < m["evaluation"]["min_pass_rate"]:
                self.err(rw, "report-threshold", f"pass_rate {s['pass_rate']} < required {m['evaluation']['min_pass_rate']}")
        else:
            wv = a.get("waiver")
            if not wv:
                self.err(aw, "approval-waiver", "basis waiver requires a waiver block")
                return
            if Version(m["version"]) >= Version(wv["expires_on_version"]):
                self.err(aw, "waiver-expired", f"waiver expired at {wv['expires_on_version']}; executed evals required")
            else:
                self.warn(aw, "evidence-unevaluated", f"{m['id']}@{m['version']} is approved by WAIVER (no executed evals): {wv['reason']}")
        if m["security"]["classification"] in ("high", "critical") and not a.get("security_reviewed_by"):
            self.err(aw, "approval-security", "high/critical skills require security_reviewed_by")

    # ---- dependencies ------------------------------------------------
    def check_dependencies(self, b: Bundle):
        m, w = b.manifest, b.rel
        deps = m.get("dependencies", [])
        _, body = b.skill_md()
        for d in deps:
            if d["id"] == m["id"]:
                self.err(w, "dep-self", "skill depends on itself")
                continue
            t = self.by_id.get(d["id"])
            if t is None:
                self.err(w, "dep-unresolved", f"dependency {d['id']} not found in registry")
                continue
            avail = set(self.released_versions(t.id))
            if t.version and (t.tier == "skills" or b.tier == "candidates"):
                avail.add(t.version)
            ok = [v for v in avail if satisfies(v, d["version"])] if validate_range(d["version"]) else []
            if not ok:
                self.err(w, "dep-version", f"no available version of {d['id']} satisfies {d['version']} (available: {sorted(avail)})")
            if b.status in ("active", "deprecated") and t.tier != "skills":
                self.err(w, "dep-tier", f"canonical skill cannot depend on non-canonical {d['id']}")
            elif b.status == "active" and t.status == "retired":
                self.err(w, "dep-retired", f"depends on retired skill {d['id']}")
            elif b.status == "active" and t.status == "deprecated":
                self.warn(w, "dep-deprecated", f"depends on deprecated skill {d['id']}")
            if d.get("role", "composes") == "composes" and d["id"] not in body:
                self.err(w + "/SKILL.md", "dep-unreferenced", f"composed skill {d['id']} is not referenced in SKILL.md; composition must be visible in the procedure")
            self.check_envelope(b, t, d)
        # graph properties
        self.check_graph(b)

    def check_envelope(self, b: Bundle, t: Bundle, d: dict):
        """Parent must declare at least the privileges of every child (no privilege hiding)."""
        w, pm, cm = b.rel, b.manifest, t.manifest
        ps, cs = pm["security"], cm["security"]
        if CLASS_RANK[ps["classification"]] < CLASS_RANK[cs["classification"]]:
            self.err(w, "env-class", f"classification {ps['classification']} < composed {t.id} ({cs['classification']})")
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
        if not d.get("optional"):
            pcaps = {c["id"] for c in pm["requires"]["capabilities"]}
            for c in cm["requires"]["capabilities"]:
                if not c.get("optional") and c["id"] not in pcaps:
                    self.err(w, "env-capability", f"parent must require capability {c['id']} needed by {t.id}")
            pc, cc = set(pm["compatibility"]["agent_classes"]), set(cm["compatibility"]["agent_classes"])
            if "any" not in cc and not ("any" in pc and cc) and not pc <= cc:
                self.err(w, "env-agent-class", f"parent agent classes {sorted(pc)} not all supported by {t.id} {sorted(cc)}")

    def check_graph(self, b: Bundle):
        w = b.rel
        # DFS for cycles, depth, closure size.
        def children(bid):
            bb = self.by_id.get(bid)
            return [d["id"] for d in (bb.manifest.get("dependencies", []) if bb else [])]
        closure: set[str] = set()
        max_depth = 0

        def dfs(n, path):
            nonlocal max_depth
            if n in path:
                self.err(w, "dep-cycle", "dependency cycle: " + " -> ".join([*path[path.index(n):], n]))
                return
            max_depth = max(max_depth, len(path))
            for c in children(n):
                closure.add(c)
                dfs(c, [*path, n])
        dfs(b.id, [])
        if max_depth > MAX_DEP_DEPTH:
            self.err(w, "dep-depth", f"composition depth {max_depth} exceeds {MAX_DEP_DEPTH}")
        if len(closure) > MAX_CLOSURE:
            self.err(w, "dep-closure", f"transitive closure has {len(closure)} skills; max {MAX_CLOSURE}")


def validate(root: Path) -> list[Issue]:
    return Registry(root).run()
