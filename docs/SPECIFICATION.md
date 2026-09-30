# Skill Blueprint Specification (Skills registry, Zeptly Registry Protocol v0.1)

Normative words (MUST/SHOULD/MAY) follow RFC 2119. `schemas/*.json` are authoritative for structure; `zskill validate` for cross-file rules. The common envelope is defined by the Registry Protocol; this document defines the Skills registry's `spec` and its bundle/release mechanics. See [PROTOCOL-ALIGNMENT.md](PROTOCOL-ALIGNMENT.md) for how each protocol rule is implemented and where interpretation was required.

## 1. Bundle

```
<tier>/<domain>/<id>/
  SKILL.md            MUST  portable procedure
  manifest.yaml       MUST  common envelope + skills spec
  evals/suite.yaml    MUST  (path set by spec.evaluation.suite)
  examples/           SHOULD
  provenance/         MAY   approvals, assessments, eval reports, evidence pointers (outside the digest)
  CHANGELOG.md        MAY   (outside the digest)
```

Tiers: `skills/` (maturity `canonical`), `candidates/` (maturity `candidate`), `synthetic/` (isolated; see §8). The directory name MUST equal `metadata.id`; the domain directory MUST equal `spec.domain`.

## 2. Common envelope (`manifest.yaml`)

```yaml
apiVersion: registry.zeptly.dev/v1alpha1
kind: SkillBlueprint
metadata:
  id: web-research            # shared grammar: lowercase dotted/hyphenated slug, no `zsk.` prefix; = directory name
  version: 1.0.0              # SemVer
  registry: skills
  origin: {type: native}      # native | evolved | upstream-seed  (evolved carries origin.evolution.{kind, sourceRefs})
  maturity: canonical         # candidate | canonical
  lifecycle: active           # active | deprecated | revoked  (mirror of the lifecycle overlay)
spec: {...}                   # skills-specific, §3
references: []                # structured {registry, id, version, digest?}
provenance: {createdAt, authors, sourceRefs, transformations}
security: {classification, capabilities, approvals}
attestations: []              # digest-bound
```

* **Common `origin.type` values are exactly `native`, `evolved`, `upstream-seed`.** `discovered` (`spec.markers.provenance`) and `synthetic` (`spec.markers.namespace`) are registry-local markers, never origin values. The evolution kind lives only at `metadata.origin.evolution.kind`.
* `id` follows the shared grammar `^[a-z0-9]+([.-][a-z0-9]+)*$` (<= 96 chars); the legacy `zsk.` prefix is rejected. The `SKILL.md` frontmatter `name` is the id with dots replaced by hyphens (Agent Skills names are hyphen-only).
* `maturity`, `origin` and `lifecycle` are **independent**. Location must agree with maturity (validator), but nothing derives one from another.
* `origin.evolution` (`{kind, sourceRefs}`) is required for, and only valid with, `origin.type: evolved`. `sourceRefs` are structured references to the artifacts evolved from. `provenance.sourceRefs` are external sources (`{kind, uri, ref?, digest?, retrievedAt?, license?}`), pinned before approval.
* `references` use `{registry, id, version, digest}`. `version` is exact or a range; `digest` is `null` unless pinned and MAY only accompany an exact version. References to registries other than `skills` are validated structurally only; this repository never fetches other repositories.
* `security.classification` is a string in the envelope; the Skills registry restricts it to `low|moderate|high|critical`. `security.capabilities` MUST equal the capability ids in `spec.requires.capabilities`. Capability namespace ownership is a platform decision deferred by the protocol; ids are opaque strings validated against `vocab/capabilities.yaml`.
* `attestations` and `security.approvals` are lists of `{type, ref, subjectDigest, issuedAt?}`. `ref` is `evidence://...` (opaque, resolved outside this repository) or `bundle:<path>` (a file inside the bundle). `subjectDigest` MUST equal the artifact's current digest; otherwise validation fails (`attestation-stale`).

## 3. `spec` (skills-specific semantics, unchanged in meaning)

`title`, `description`, `domain`, `tags`, `stewardship` (maintainers, licence), `trust` (`tier`, `assessment`), `compatibility` (`protocol`, `agent_classes`, `min_context_tokens`, `supersedes`), `requires` (capabilities, tools), `composition` (per-reference `role`/`optional`), `inputs`, `outputs`, `security_profile` (data sensitivity, side effects, permissions, external systems, network egress, authentication, destructive operations, HITL), `evaluation` (`suite`, `min_pass_rate`), `markers` (registry-local: `namespace: synthetic`, `provenance: discovered`), `x` extension namespace. Candidate workflow stage (`discovered|inspected|drafted|evaluating|approved`) is **not** in `spec`: it lives in `provenance/stage.yaml` so that `spec` can be wholly inside the digest.

Skills-registry specifics preserved: procedural `SKILL.md`, composition, skill evaluation suites, provenance/approvals, release tagging.

## 4. `SKILL.md` convention

* Frontmatter MUST contain `name` (= `metadata.id`) and `description` (= `spec.description`); other keys only from Agent Skills (`license`, `compatibility`, `metadata`, `allowed-tools`).
* Body MUST have H2 sections **When to use**, **Procedure**, **Output**, **Guardrails**; Guardrails MUST treat fetched/external content as data, not instructions.
* Composed skills MUST be named as `` `skills:<id>` `` in the procedure.
* `tiny`-class skills: body <= 8000 characters. No secrets, ever.

## 5. Versioning

SemVer on the behavioural contract. **MAJOR**: any change to `security.*` or `spec.security_profile`; incompatible I/O change; removal of an agent class. **MINOR**: additive change to inputs/outputs/requires/references/agent classes. **PATCH**: clarification, examples, eval additions. Mechanically enforced through ledger digests (`contract_digest` change => >= MINOR; `security_digest` change => MAJOR). Procedure-semantics changes rely on review.

A candidate for an existing identity MUST have a version greater than every canonical version of that identity (`candidate-version`).

## 6. Immutability, digests, release

**Canonical JSON.** RFC 8785 (JCS): UTF-8; object keys sorted by UTF-16 code units; no insignificant whitespace; minimal string escaping; numbers in ES6 form. Only integers within +/-2^53 and plain-decimal floats (1e-6 <= |v| < 1e16) are accepted; NaN/Infinity/non-string keys are errors.

**Line endings.** For payload files, CRLF and lone CR become LF before hashing. No BOM handling, no trimming, no re-encoding.

**Directory seal** (separate value). `seal = sha256( "<relpath>\0<sha256(normalized bytes)>\n" ... )` over canonical payload files sorted by code-point order of the posix relative path. Payload = every bundle file except `manifest.yaml`, `provenance/**`, `CHANGELOG.md` and `.gitkeep`. Symlinks and unsupported entries (FIFO, socket, device) are never followed or hashed: seal and digest computation **fail** with an explicit diagnostic if the bundle contains any (the validator reports them as `symlink` / `unsupported-entry`). Hashes of valid bundles are unaffected.

**Artifact digest.** `digest = sha256( JCS({"directorySeal": <seal>, "manifest": <projection>}) )` where the projection is an explicit include-list: `apiVersion`, `kind`, `metadata.{id, registry, origin}`, `spec`, `references`, `provenance`, `security.{classification, capabilities}`. **Excluded**: `metadata.version`, `metadata.maturity`, `metadata.lifecycle`, `attestations`, `security.approvals`. Consequently promotion, lifecycle changes, version bumps of identical content and issuing attestations never change the digest, while any change to identity, spec, references, provenance, classification, capabilities or payload does. Digest, seal, `contract_digest` and `security_digest` are recorded per release in `registry/releases/<id>.yaml` (append-only); canonical artifacts MUST match all four (`release-mutated`). Golden vectors: `tests/test_digest_vectors.py`.

Git tags `skill/<id>/v<version>` mark released versions.

## 7. Lifecycle overlay

`registry/lifecycle/<id>.yaml`: append-only events `{version, state, at, reason, replacedBy?, sunset?}` with `state` in `active|deprecated|revoked`. The effective state of a version is its latest event (default `active`). `revoked` is terminal. `metadata.lifecycle` mirrors the effective state (validated; outside the digest). `zskill lifecycle` appends the event and updates the mirror. Resolvers MUST NOT select revoked versions; canonical artifacts referencing a revoked skill fail validation, and deprecated targets warn.

## 8. Synthetic namespace

Synthetic examples live only under `synthetic/`, MUST carry `spec.markers.namespace: synthetic` (required in, and only valid under, `synthetic/`; `synthetic` is not an origin value), MUST NOT reuse a production id, MUST NOT be referenced by production artifacts, and appear only in `registry/index.synthetic.json`, never `registry/index.json`.

## 9. Bundle contents (allow-list) and what never gets committed

Allowed files (names match `[A-Za-z0-9][A-Za-z0-9._-]*`, depth <= 4, UTF-8 text): top level `SKILL.md`, `manifest.yaml`, `CHANGELOG.md`; `evals/**` and `examples/**` with extensions `.md .yaml .yml .json .txt .csv`; `provenance/{approval,assessment,evidence,stage}.yaml`, `provenance/*.md|*.txt`, `provenance/eval-reports/*.yaml`; `.gitkeep`. Everything else is `file-not-allowed`. Symlinks and unsupported filesystem entries are rejected. Limits: 256 KiB per file, 2 MiB and 200 files per bundle.

Runtime tapes, trajectories, traces, transcripts and other sensitive execution payloads stay outside registry Git. Detection: file names/suffixes (`.tape .trace .jsonl .ndjson .har .pcap .sqlite .db .parquet .pkl`; tape/trajectory/trace/transcript/rollout names) and content heuristics (JSON-lines event streams, chat-turn transcripts, role/message JSON). Evidence pointers must be `evidence://` or `https://` (never `file:`/`data:`), summaries are capped at 500 characters, and every text file is scanned for secrets. There is no endpoint/URL scan beyond pointer schemes.

## 10. Repository-wide invariants (`zskill validate`)

Schema validity; unique identity per `(id, version)`; location/maturity/stage/origin/lifecycle consistency; references resolve and satisfy their ranges (skills registry) or validate structurally (others); no cycles, depth <= 3, closure <= 20, <= 8 direct composition entries; ledger versions strictly increasing; released digests match; attestations current; controlled vocabularies respected; deterministic indexes current (`zskill index --check`).

## 11. Tooling guarantees (input handling and failure diagnostics)

* **Duplicate YAML mapping keys are an error** in every YAML/JSON file the tooling reads (manifests, `SKILL.md` frontmatter, ledgers, overlays, evidence, approvals, eval suites, vocabularies, schemas). The diagnostic names the file, key, key path (for example `spec.inputs[0].name`) and line. `<<` merge overrides are not duplicates. Parsing of all other valid documents is unchanged.
* **Unhashable bundles fail loudly.** `Bundle.directory_seal()` / `Bundle.digest()` raise `BundleError` for symlinks, unsupported filesystem entries and unreadable directories (including a symlinked bundle or domain directory); they never return a digest that silently omitted something. `zskill validate` reports the entries and skips digest-dependent checks for that bundle.
* **Canonicalization failures are diagnostics**, not exceptions: a value outside the accepted number/key/string profile reports its path, for example `skills/x/y/manifest.yaml#$.spec.x.bad: float 1e-07 is outside the accepted magnitude range [1e-6, 1e16)` (validator code `canonicalization`).
* **Index generation never skips.** `zskill index` fails (exit 2, per-bundle diagnostics, existing files untouched) if any bundle of the requested namespace is invalid or unhashable. Schema-valid bundles with only semantic findings are still indexed; `zskill validate` reports those.
* **Generated outputs are schema-validated before success**: the index against `schemas/registry-index.schema.json`, resolution locks against `schemas/resolution-lock.schema.json`.
* **Exit codes:** `0` success, `1` validation errors / stale index / append-only violation, `2` controlled failure (malformed or unreadable input, invalid bundle, unhashable content, output that fails its schema), printed as `error: ...` with no traceback.
* `zskill new <domain>/<id>` rejects domains outside `vocab/domains.yaml` (which also blocks path traversal); scaffolds of both tiers validate without errors.
