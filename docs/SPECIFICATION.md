# Skill Blueprint Specification (Skills registry, Zeptly Registry Protocol v0.2)

Normative words (MUST/SHOULD/MAY) follow RFC 2119. `schemas/*.json` are authoritative for structure; `zskill validate` for cross-file rules. The common envelope is defined by the Registry Protocol; this document defines the Skills registry's `spec` and its bundle/release mechanics. See [PROTOCOL-ALIGNMENT.md](PROTOCOL-ALIGNMENT.md) for how each protocol rule is implemented and where interpretation was required.

## 1. Bundle

```
<tier>/<domain>/<id>/
  SKILL.md            MUST  portable procedure
  manifest.yaml       MUST  common envelope + skills spec
  evals/suite.yaml    MUST  (path set by spec.evaluation.suite)
  examples/           SHOULD
  provenance/         MAY   approvals, assessments, eval reports, evidence pointers (outside the seal; manifest provenance is in the digest)
  CHANGELOG.md        MAY   (payload: covered by the directory seal)
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
  origin: {type: native}      # native | upstream-seed | discovered | refined | evolved  (refined/evolved carry origin.evolution.{kind, sourceRefs})
  maturity: canonical         # candidate | canonical
  lifecycle: active           # active | deprecated | revoked  (mirror of the lifecycle overlay)
spec: {...}                   # skills-specific, §3
references: []                # structured {registry, id, version, digest?}
provenance: {createdAt, authors, sourceRefs, transformations}
security: {classification, capabilities, approvals}
attestations: []              # digest-bound
```

* **`origin.type` values are exactly `native`, `upstream-seed`, `discovered`, `refined`, `evolved`** (v0.2). `synthetic` is not an origin value; synthetic artifacts are marked by `spec.markers.namespace: synthetic` and the reserved `example.` id prefix (§8). The former registry-local discovery marker is gone (`discovered` is now an origin type). `metadata.origin.evolution.kind` (`discovered|refined|generalised`) is the only evolution-kind location; `provenance` never carries it.
* `id` follows the shared grammar `^[a-z0-9]+([.-][a-z0-9]+)*$` (<= 96 chars); the legacy `zsk.` prefix is rejected, and `example.` is reserved for synthetic artifacts. The `SKILL.md` frontmatter `name` is the id with dots replaced by hyphens (Agent Skills names are hyphen-only).
* `maturity`, `origin` and `lifecycle` are **independent**. Location must agree with maturity (validator), but nothing derives one from another.
* `origin.evolution` (`{kind, sourceRefs}`) is required for, and only valid with, `origin.type: refined` or `evolved`; `native` and `upstream-seed` forbid it. `upstream-seed` and `discovered` artifacts need pinned external `provenance.sourceRefs`. `sourceRefs` are structured references to the artifacts evolved from. `provenance.sourceRefs` are external sources (`{kind, uri, ref?, digest?, retrievedAt?, license?}`), pinned before approval.
* `references` use `{registry, id, version, digest?, digestAlgorithm?}`. `version` is exact or a range; `digest` is `null` unless pinned, MAY only accompany an exact version, and then requires `digestAlgorithm: zeptly-jcs-v1`. References to registries other than `skills` are validated structurally only; locks list them as explicit `unresolved` entries unless a peer index is supplied (§6).
* `security.classification` is a string in the envelope; the Skills registry restricts it to `low|moderate|high|critical`. `security.capabilities` MUST equal the capability ids in `spec.requires.capabilities`. Capability namespace ownership is a platform decision deferred by the protocol; ids are opaque strings validated against `vocab/capabilities.yaml`.
* `attestations` and `security.approvals` are lists of `{type, ref, subjectDigest, digestAlgorithm, subjectSeal, issuedAt?}`. `ref` is `evidence://...` (opaque, resolved outside this repository) or `bundle:<path>`. `subjectDigest` MUST equal the artifact's current digest. **`subjectSeal` is a Skills-registry extension** (required on every attestation/approval of a canonical or approved artifact): SKILL.md, evals and examples are payload, outside the artifact digest, so an attestation also binds the directory seal; otherwise it could survive a change to the procedure it assessed (`attestation-stale`, `attestation-seal-missing`). `evaluation` attestations additionally carry `suite: {id, version, digest}` and `result: pass|fail|inconclusive`; the suite identity is `{id: spec.evaluation.suite, version: <artifact version>, digest: sha256 of the suite file bytes}` (`eval-attestation-suite`).

## 3. `spec` (skills-specific semantics, unchanged in meaning)

`title`, `description`, `domain`, `tags`, `stewardship` (maintainers, licence), `trust` (`tier`, `assessment`), `compatibility` (`protocol`, `agent_classes`, `min_context_tokens`, `supersedes`), `requires` (capabilities, tools), `composition` (per-reference `role`/`optional`), `inputs`, `outputs`, `security_profile` (data sensitivity, side effects, permissions, external systems, network egress, authentication, destructive operations, HITL), `evaluation` (`suite`, `min_pass_rate`), `markers` (registry-local: only `namespace: synthetic`), `x` extension namespace. Candidate workflow stage (`discovered|inspected|drafted|evaluating|approved`) is **not** in `spec`: it lives in `provenance/stage.yaml` so that `spec` can be wholly inside the digest.

Skills-registry specifics preserved: procedural `SKILL.md`, composition, skill evaluation suites, provenance/approvals, release tagging.

## 4. `SKILL.md` convention

* Frontmatter MUST contain `name` (= `metadata.id`) and `description` (= `spec.description`); other keys only from Agent Skills (`license`, `compatibility`, `metadata`, `allowed-tools`).
* Body MUST have H2 sections **When to use**, **Procedure**, **Output**, **Guardrails**; Guardrails MUST treat fetched/external content as data, not instructions.
* Composed skills MUST be named as `` `skills:<id>` `` in the procedure.
* `tiny`-class skills: body <= 8000 characters. No secrets, ever.

## 5. Versioning

SemVer on the behavioural contract. **MAJOR**: any change to `security.*` or `spec.security_profile`; incompatible I/O change; removal of an agent class. **MINOR**: additive change to inputs/outputs/requires/references/agent classes. **PATCH**: clarification, examples, eval additions. Mechanically enforced through ledger digests (`contract_digest` change => >= MINOR; `security_digest` change => MAJOR). Procedure-semantics changes rely on review.

A candidate for an existing identity MUST have a version greater than every canonical version of that identity (`candidate-version`).

## 6. Immutability, digests, release, locks (`digestAlgorithm: zeptly-jcs-v1`)

**Input.** Manifests and every YAML file are parsed as the v0.2 JSON-compatible YAML subset (`tools/zskill/yamlsubset.py`, built on the PyYAML scanner/parser only): string keys only; duplicate keys, anchors, aliases, merge keys, multiple documents, unsupported tags, BOMs, NULs, invalid UTF-8 and lone surrogates are rejected; integers are decimal within +/-(2^53-1); non-finite and ambiguous numeric scalars (`010`, `0x10`, `.nan`, `+1`) are rejected; `yes/no/on/off` and timestamps stay strings. Every failure carries file, key path, line/column and a machine-readable code (exit 2).

**Canonical JSON.** RFC 8785 JCS: object keys sorted by UTF-16 code units; ECMAScript number serialization; RFC 8785 string escaping, UTF-8 output; no Unicode normalization; manifest strings are not line-ending normalized after parsing.

**Payload text.** Payload text files MUST be UTF-8, BOM-free, NUL-free and LF-only. CRLF and lone CR are **rejected** (`payload-line-endings`), never normalized.

**Directory seal.** `seal = sha256(JCS({registry, id, version, payload: [{path, sha256}]}))`, `sha256` written `sha256:<hex>`, payload ordered by normalized POSIX path (code-point order). Payload = every bundle file except the root `manifest.yaml` and `provenance/**` (so `CHANGELOG.md`, `.gitkeep` and nested `manifest.yaml` files are payload). The seal binds `version`; the artifact digest does not. Symlinks, FIFOs, sockets, devices, unreadable entries, case-colliding paths and files outside the allow-list are rejected **before** hashing (`symlink`, `unsupported-entry`, `unreadable-entry`, `case-collision`, `file-not-allowed`).

**Artifact digest.** `digest = sha256(JCS(projection))`; the projection is an explicit include-list: `apiVersion`, `kind`, `metadata.{id, registry, origin}`, `spec`, `references`, `provenance`, `security.{classification, capabilities}`. **Excluded**: `metadata.version`, `metadata.maturity`, `metadata.lifecycle`, `attestations`, `security.approvals`, lifecycle overlays. The artifact digest no longer includes the directory seal (v0.1 did). Runtime approval requirements live in `spec` and therefore affect the digest; governance approvals bind to it through `subjectDigest`. Digest, seal, `contract_digest` and `security_digest` are recorded per release in `registry/releases/<id>.yaml` (append-only, top-level `digestAlgorithm`); canonical artifacts MUST match all four (`release-mutated`).

**Vectors.** `tests/vectors/zeptly-jcs-v1.skills-generated.json` (generated by `tests/vectors/make_vectors.py`, verified by the stdlib-only `tests/vectors/reference_zeptly_jcs_v1.py` and by `tests/test_vectors.py`) covers UTF-16 key order, numbers and rejections, Unicode strings, payload text rejections, parser cases, excluded fields, payload mutation, artifact digest and directory seal. **These are Skills-generated vectors: the shared protocol vector distribution was not supplied to this registry.** Contract changes require a new `digestAlgorithm` identifier and new vectors.

**Locks.** `zskill resolve <id> [--domain production|synthetic] [--peer-index FILE]... [--allow-candidates]` emits a `RuntimeLock` (`schemas/runtime-lock.schema.json`) with one entry per declared reference, `complete: true` only when every entry is resolved. Resolution uses only explicit indexes (this registry's own index plus release-ledger history, plus supplied peer indexes); nothing is fetched. Unresolved codes: `no-peer-index`, `not-found`, `no-matching-version`, `revoked`, `deprecated-requires-exact-pin`, `candidate-not-allowed`, `digest-mismatch`, `invalid-range`, `domain-mismatch`. Revoked versions never resolve; deprecated versions resolve only by exact pin; candidates need opt-in; prereleases resolve only when the range names a prerelease of the same major.minor.patch; production and synthetic never mix. Transitive resolution and cycle handling are runtime responsibilities.

Git tags `skill/<id>/v<version>` mark released versions.

## 7. Lifecycle overlay

`registry/lifecycle/<id>.yaml`: append-only events `{version, state, at, reason, replacedBy?, sunset?}` with `state` in `active|deprecated|revoked`. The effective state of a version is its latest event (default `active`). `revoked` is terminal. `metadata.lifecycle` mirrors the effective state (validated; outside the digest). `zskill lifecycle` appends the event and updates the mirror. Resolvers MUST NOT select revoked versions; canonical artifacts referencing a revoked skill fail validation, and deprecated targets warn.

## 8. Synthetic namespace

Synthetic examples live only under `synthetic/`, MUST carry `spec.markers.namespace: synthetic` (required in, and only valid under, `synthetic/`), MUST use ids in the reserved `example.` namespace (`zskill new --synthetic` adds the prefix; the prefix is rejected outside `synthetic/`), MUST use `evidence://example/...` pointers only (production artifacts may not), MUST NOT be referenced by production artifacts, and appear only in `registry/index.synthetic.json` (index `domain: synthetic`), never `registry/index.json`. Production locks never resolve synthetic artifacts.

## 9. Bundle contents (allow-list) and what never gets committed

Allowed files (names match `[A-Za-z0-9][A-Za-z0-9._-]*`, depth <= 4, UTF-8 text): top level `SKILL.md`, `manifest.yaml`, `CHANGELOG.md`; `evals/**` and `examples/**` with extensions `.md .yaml .yml .json .txt .csv`; `provenance/{approval,assessment,evidence,stage}.yaml`, `provenance/*.md|*.txt`, `provenance/eval-reports/*.yaml`; `.gitkeep`. Everything else is `file-not-allowed`. Symlinks, unsupported filesystem entries and case-colliding paths are rejected before hashing. Limits: 256 KiB per file, 2 MiB and 200 files per bundle.

Runtime tapes, trajectories, traces, transcripts and other sensitive execution payloads stay outside registry Git. Detection: file names/suffixes (`.tape .trace .jsonl .ndjson .har .pcap .sqlite .db .parquet .pkl`; tape/trajectory/trace/transcript/rollout names) and content heuristics (JSON-lines event streams, chat-turn transcripts, role/message JSON). Evidence pointers must be `evidence://` or `https://` (never `file:`/`data:`), summaries are capped at 500 characters, and every text file is scanned for secrets. There is no endpoint/URL scan beyond pointer schemes.

## 10. Repository-wide invariants (`zskill validate`)

Schema validity; unique identity per `(id, version)`; location/maturity/stage/origin/lifecycle consistency; references resolve and satisfy their ranges (skills registry) or validate structurally (others); no cycles, depth <= 3, closure <= 20, <= 8 direct composition entries; ledger versions strictly increasing; released digests match; attestations current; controlled vocabularies respected; deterministic indexes current (`zskill index --check`).

## 11. Tooling guarantees (input handling and failure diagnostics)

* **Parser rejection** (§6) applies to every YAML/JSON file the tooling reads (manifests, `SKILL.md` frontmatter, ledgers, overlays, evidence, approvals, eval suites, vocabularies, schemas, peer indexes). Diagnostics name file, key path, line/column and code.
* **Unhashable bundles fail loudly.** `Bundle.directory_seal()` raises `BundleError` for symlinks, unsupported entries, unreadable directories, case collisions, disallowed paths and non-conforming payload text; it never returns a seal that silently omitted something. `zskill validate` reports the entries and skips seal-dependent checks for that bundle. The artifact digest is manifest-derived and independent of payload bytes.
* **Index generation never skips.** `zskill index` fails (exit 2, per-bundle diagnostics, existing files untouched) if any bundle of the requested domain is invalid or unhashable.
* **Generated outputs are schema-validated before success**: indexes against `schemas/registry-index.schema.json`, locks against `schemas/runtime-lock.schema.json`, peer indexes on load.
* **Exit codes:** `0` success; `1` a valid request that cannot be satisfied (for example an unresolved reference, unknown skill, illegal promotion); `2` malformed input or validation errors (parse failures, invalid bundles, unhashable content, stale index, append-only violations, output that fails its schema). Errors print `error[<code>]: <message>` with no traceback.
* `zskill new <domain>/<id>` rejects domains outside `vocab/domains.yaml` (which also blocks path traversal); scaffolds of both tiers validate without errors.
