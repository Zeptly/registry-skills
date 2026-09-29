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
  id: web-research            # unique within the skills registry; = directory = SKILL.md name
  version: 1.0.0              # SemVer
  registry: skills
  origin: {type: authored}    # authored | evolved | discovered | imported | synthetic  (+ evolution for evolved)
  maturity: canonical         # candidate | canonical
  lifecycle: active           # active | deprecated | revoked  (mirror of the lifecycle overlay)
spec: {...}                   # skills-specific, §3
references: []                # structured {registry, id, version, digest?}
provenance: {createdAt, authors, sourceRefs, transformations}
security: {classification, capabilities, approvals}
attestations: []              # digest-bound
```

* `maturity`, `origin` and `lifecycle` are **independent**. Location must agree with maturity (validator), but nothing derives one from another.
* `origin.evolution` (`{kind, sourceRefs}`) is required for, and only valid with, `origin.type: evolved`. `sourceRefs` are structured references to the artifacts evolved from. `provenance.sourceRefs` are external sources (`{kind, uri, ref?, digest?, retrievedAt?, license?}`), pinned before approval.
* `references` use `{registry, id, version, digest}`. `version` is exact or a range; `digest` is `null` unless pinned and MAY only accompany an exact version. References to registries other than `skills` are validated structurally only; this repository never fetches other repositories.
* `security.classification` is a string in the envelope; the Skills registry restricts it to `low|moderate|high|critical`. `security.capabilities` MUST equal the capability ids in `spec.requires.capabilities`. Capability namespace ownership is a platform decision deferred by the protocol; ids are opaque strings validated against `vocab/capabilities.yaml`.
* `attestations` and `security.approvals` are lists of `{type, ref, subjectDigest, issuedAt?}`. `ref` is `evidence://...` (opaque, resolved outside this repository) or `bundle:<path>` (a file inside the bundle). `subjectDigest` MUST equal the artifact's current digest; otherwise validation fails (`attestation-stale`).

## 3. `spec` (skills-specific semantics, unchanged in meaning)

`title`, `description`, `domain`, `tags`, `stewardship` (maintainers, licence), `trust` (`tier`, `assessment`), `compatibility` (`protocol`, `agent_classes`, `min_context_tokens`, `supersedes`), `requires` (capabilities, tools), `composition` (per-reference `role`/`optional`), `inputs`, `outputs`, `security_profile` (data sensitivity, side effects, permissions, external systems, network egress, authentication, destructive operations, HITL), `evaluation` (`suite`, `min_pass_rate`), and `stage` (candidates only: `discovered|inspected|drafted|evaluating|approved`), `x` extension namespace.

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

`digest = sha256( "<relpath>\0<sha256(content)>\n" ... )` over bundle files sorted by path, excluding `provenance/**` and `CHANGELOG.md`. `manifest.yaml` is hashed as canonical JSON with these governance paths removed: `metadata.maturity`, `metadata.lifecycle`, `spec.stage`, `attestations`, `security.approvals`. Consequently promotion, lifecycle changes, and issuing attestations never change a version's digest (and attestations cannot be circular), while any change to procedure, contract, origin, provenance, evals or examples does.

`registry/releases/<id>.yaml` is the append-only ledger (`version`, `digest`, `contract_digest`, `security_digest`). Canonical artifacts MUST have a ledger entry whose digests match; otherwise `release-mutated`/`release-missing`. Git tags `skill/<id>/v<version>` mark released versions.

## 7. Lifecycle overlay

`registry/lifecycle/<id>.yaml`: append-only events `{version, state, at, reason, replacedBy?, sunset?}` with `state` in `active|deprecated|revoked`. The effective state of a version is its latest event (default `active`). `revoked` is terminal. `metadata.lifecycle` mirrors the effective state (validated; outside the digest). `zskill lifecycle` appends the event and updates the mirror. Resolvers MUST NOT select revoked versions; canonical artifacts referencing a revoked skill fail validation, and deprecated targets warn.

## 8. Synthetic namespace

Synthetic examples live only under `synthetic/`, MUST have `origin.type: synthetic`, MUST NOT reuse a production id, MUST NOT be referenced by production artifacts, and appear only in `registry/index.synthetic.json`, never `registry/index.json`.

## 9. What never gets committed

Runtime tapes, trajectories, traces, transcripts and other sensitive execution payloads stay outside registry Git. The validator rejects such files by name/extension, files over 256 KiB, and evidence pointers that are not `evidence://` or `https://` URIs (no `file:`/`data:`); evidence summaries are capped at 500 characters; secrets are scanned in every text file.

## 10. Repository-wide invariants (`zskill validate`)

Schema validity; unique identity per `(id, version)`; location/maturity/stage/origin/lifecycle consistency; references resolve and satisfy their ranges (skills registry) or validate structurally (others); no cycles, depth <= 3, closure <= 20, <= 8 direct composition entries; ledger versions strictly increasing; released digests match; attestations current; controlled vocabularies respected; deterministic indexes current (`zskill index --check`).
