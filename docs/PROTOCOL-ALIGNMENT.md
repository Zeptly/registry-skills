# Zeptly Registry Protocol v0.1: alignment of the Skills registry

Scope: this file states what the Skills registry implements from the protocol and where it had to interpret or extend it. Only the protocol document was used; nothing was inferred from sibling registries. **Everything marked "interpretation" needs reconciliation against the other registries.**

## Rule-by-rule

| Protocol rule | Implementation |
|---|---|
| Common envelope | `manifest.yaml` is the envelope (`schemas/envelope.schema.json`); `schemas/skill-blueprint.schema.json` layers the skills `spec`. |
| `kind`, `metadata.id/version/registry` on every artifact | Required; `kind: SkillBlueprint`, `registry: skills`. |
| Published versions immutable, addressed by exact digest | Release ledger + digest (`release-mutated`); index and resolution lock carry digests. |
| SemVer; candidate for existing identity exceeds canonical | SemVer enforced via ledger; `candidate-version` rule. |
| Candidates are registry objects; Git/PRs transport only | `candidates/` tier + `metadata.maturity`. |
| `maturity`, `origin`, `lifecycle` independent | Separate fields; only location/maturity agreement is validated. |
| Lifecycle append-only overlay (active/deprecated/revoked) | `registry/lifecycle/<id>.yaml`; append-only checked in CI; `revoked` terminal. |
| Structured `{registry,id,version,digest?}` references | `references[]`; `origin.evolution.sourceRefs`; evidence/approval/report `subject`. Cross-registry refs are structural only (no network). |
| Runtime resolution converts ranges to exact version+digest, recorded as lock | `zskill resolve` -> `ResolutionLock`. |
| Attestations bind to exact subject digest | `attestation-stale` error; report/approval subject digests re-checked. |
| Raw tapes/trajectories/sensitive artifacts outside Git | `no-runtime-artifacts`, `file-too-large`, pointer-only evidence schema. |
| Runtime must not alter declared security classification | Classification is in the manifest and in the `security_digest`; changes force MAJOR. (Runtime behaviour is outside this repo.) |
| Deterministic generated indexes with identity, version, digest, maturity, lifecycle, origin, location | `registry/index.json` (`registry-index.schema.json`); no timestamps, sorted; `zskill index --check` in CI. |
| Promotion requires schema, semantic checks, evaluations, provenance, security review, digest-bound attestations, governed approval | Promotion gate in LIFECYCLE.md. |
| Synthetic examples isolated and marked | `synthetic/` tier, `origin.type: synthetic`, separate index, leak/collision checks. |
| Runtime code, capability namespace ownership, gateways, Evidence Protocol out of scope | Not implemented; capability ids are opaque strings; evidence is pointers only. |
| Class-specific preservation (skills: `SKILL.md`, composition, skill evaluation) | Kept under `spec`, `SKILL.md`, `evals/`, `composition`. Tiny's adaptation contract and runtime tapes deliberately not imposed. |

## Interpretations and extensions requiring reconciliation

1. **`metadata.id` grammar.** Protocol examples show both `research.web-fact-check` and `source-evaluation`. Envelope grammar accepts `[a-z0-9]+([.-][a-z0-9]+)*`; the Skills registry restricts its own ids to slugs. The earlier `zsk.` prefix was dropped because `registry` disambiguates.
2. **Vocabularies.** Protocol defines lifecycle "at least" active/deprecated/revoked; only those three are used (prior `retired` dropped). `origin.type` values, `origin.evolution.kind` (`generalised` is used for the Zep-derived case), `maturity` (`candidate|canonical`), and attestation `type` values (`evaluation`, `governance`) are registry-local.
3. **`security.classification`** vocabulary is undefined by the protocol (its example says `restricted`). The Skills registry uses `low|moderate|high|critical`.
4. **`security.approvals` vs `attestations`** shapes are unspecified; both use the same `{type, ref, subjectDigest}` shape.
5. **`sourceRefs`** appear twice in the example envelope. Interpreted as: `origin.evolution.sourceRefs` = structured artifact references; `provenance.sourceRefs` = external sources (uri, pinned ref/digest).
6. **Digest scope.** Governance state (maturity, stage, lifecycle, attestations, approvals) is excluded from the digest so promotion/lifecycle don't change identity and attestations aren't circular.
7. **Attestation `ref`** forms: `evidence://...` (opaque) and `bundle:<path>`.
8. **Evaluation waiver (transitional exception).** The protocol has no waiver concept. The six seed skills are canonical only by a time-boxed waiver of the evaluation requirement (expiry 1.1.0) and were never security-reviewed beyond PR-level review. This is a deliberate registry-local deviation and is surfaced as CI warnings and `evidenceLevel: unevaluated`. If reconciliation rejects waivers, these skills must return to `candidate`.
9. **Migration.** The `zsk.` ids and the pre-envelope 1.0.0 ledger entries existed only on this unmerged draft PR and were never published from `main`; the ledger was regenerated for the envelope format at 1.0.0 rather than adding phantom releases. Once anything is merged to `main`, this is no longer permitted.
10. **Digest binding of in-bundle records** uses `subject.digest` (structured reference) instead of the earlier `skill_digest`.

## Not implemented (deferred by the protocol or out of scope)

Evidence Protocol schema/ownership, capability/gateway/model namespaces, signing, peer-index distribution, workspace overrides, traffic channels, nested QB execution, runtime-trigger contract versioning, Tiny adaptation contracts, runtime tape handling.

## Final normalization pass (shared vocabulary and digest alignment)

Applied after the cross-registry audit. The shared canonicalization text itself was not supplied to this registry; the choices below (RFC 8785 JCS, LF policy, seal + digest composition) are **assumptions pinned by golden vectors** (`tests/test_digest_vectors.py`) so any mismatch with the shared definition is detectable.

| Item | Now |
|---|---|
| `origin.type` | `native` (was `authored`), `upstream-seed` (was `imported`), `evolved`. `discovered` -> `spec.markers.provenance`, `synthetic` -> `spec.markers.namespace`; neither is a common origin value. |
| Evolution kind | Only `metadata.origin.evolution.kind`; `provenance.evolution` is rejected by schema. |
| ID grammar | Shared: `^[a-z0-9]+([.-][a-z0-9]+)*$`, no `zsk.` prefix (`id-prefix`). SKILL.md `name` = id with dots as hyphens. |
| Artifact digest | JCS of `{directorySeal, manifest projection}`; includes identity, spec, references, provenance, classification, capabilities; excludes version, maturity, lifecycle, attestations, approvals. |
| Directory seal | Separate value over canonical payload files; recorded in the ledger, index and locks. |
| Line endings | CRLF and lone CR -> LF for payload files. |
| Candidate stage | Moved out of `spec` to `provenance/stage.yaml` so `spec` is wholly inside the digest. |
| Resolution lock | Schema added; foreign references listed under `unresolved` with reason `foreign-registry-not-resolved-offline`. |
| Index ordering | Explicit code-point comparator on id, SemVer precedence, then maturity, then digest; no locale-dependent sorting. |
| Bundle contents | Explicit filename allow-list, symlinks rejected and never followed, size/count limits, transcript/tape/trace detection by name and content. |
| Seed waivers | **Decision: temporary protocol exception**, explicit `basis: protocol-exception`, rule id, expiry 1.1.0 (unchanged), CI warning + index `unevaluated`; reason text unchanged. Not silently retained: see LIFECYCLE.md. |
| Ledger | Ledger entries regenerated at 1.0.0 (digest algorithm changed; nothing was ever published from `main`). Not permitted once anything is on `main`. |

## Local defect remediation (registry-local only; no shared-contract change)

Duplicate YAML keys are now rejected with file/key-path/line diagnostics; seal and digest computation refuse bundles containing symlinks or unsupported filesystem entries (hashes of valid bundles are unchanged, golden vectors unchanged); index generation fails instead of omitting invalid bundles; generated indexes and resolution locks are validated against their schemas before success; malformed input and canonicalization failures produce controlled `error:` diagnostics (exit 2). See SPECIFICATION.md section 11. One behaviour differs from the draft digest handoff (DIGEST-CONTRACT.md, gap G6): the seal function no longer silently skips symlinks; it refuses. That changes nothing for valid bundles.
