# Maturity, Lifecycle and Promotion

Protocol v0.1 requires `maturity`, `origin` and `lifecycle` to be independent. This registry separates them physically as well as logically.

| Field | Values | Meaning | Where it lives | In digest? |
|---|---|---|---|---|
| `metadata.maturity` | `candidate`, `canonical` | Has the version passed the promotion gate? | manifest (mirrors location `candidates/` vs `skills/`) | no |
| candidate stage (candidates only) | `discovered`, `inspected`, `drafted`, `evaluating`, `approved` | Workflow progress inside candidate | `provenance/stage.yaml` (not in `spec`) | no |
| `metadata.lifecycle` | `active`, `deprecated`, `revoked` | Append-only overlay state | `registry/lifecycle/<id>.yaml`, mirrored in manifest | no |
| `metadata.origin.type` | `native`, `evolved`, `upstream-seed` (common); `discovered`/`synthetic` are registry-local markers in `spec.markers`, not origin values | How it came to exist | manifest | **yes** |

A rejected candidate is recorded as lifecycle `revoked` (with a reason) rather than as a maturity or stage. The former `retired` status is expressed as `deprecated` with a `sunset` date, then `revoked` if it must no longer resolve.

## Promotion gate (candidate -> canonical)

Required by the protocol: schema validation, semantic checks, required evaluations, provenance, security review, digest-bound attestations, governed approval.

Implemented: `zskill validate` (schema + semantic); an `evaluation` attestation whose report binds to the exact digest and suite and meets `min_pass_rate`; provenance (external sources pinned, assessment verdict permits progress, trust tier not `unreviewed`); `security.approvals[type=governance]` referencing `provenance/approval.yaml`, bound to the current digest, with `securityReviewedBy` for high/critical classification (for lower tiers, CODEOWNERS review of the security block in the PR is the governed security review); `zskill promote`.

**Temporary protocol exception (explicit decision):** the protocol requires evaluations for promotion and defines no waiver. The six seed skills are therefore canonical only under an explicit, expiring exception: approval `basis: protocol-exception` with `exception: {rule: promotion.required-evaluations, reason, expiresOnVersion}`. It is *not* an evaluation, emits the CI warning `protocol-exception` (surfaced as a GitHub warning annotation), marks the index `evidenceLevel: unevaluated`, and becomes the error `exception-expired` when the artifact reaches `expiresOnVersion` (currently 1.1.0). It must be ratified by protocol owners or replaced by executed evaluations; if rejected, the seeds return to `candidate`.

`zskill promote <id>` moves the bundle from `candidates/` to `skills/`, sets `maturity: canonical`, removes `provenance/stage.yaml`, and appends the ledger entry. The digest is unchanged, so existing attestations remain valid.

## Pipelines

Git branches and pull requests are governance transport only; the candidate is the registry object under `candidates/`.

* **Internal discovery**: evidence pointers -> Zep proposes `origin: {type: evolved, evolution: {kind: generalised, sourceRefs: [...]}}` (must cite >= 1 compute evidence pointer) or a new version of an existing identity (candidate version must exceed every canonical version).
* **External discovery**: `origin.type: upstream-seed` with `spec.markers.provenance: discovered`, pinned `provenance.sourceRefs`, `provenance/assessment.yaml`; nothing beyond `inspected` until the verdict permits. Discovery never grants runtime access.
* **Crowd wisdom**: human corrections enter as `wisdom: crowd` evidence pointers plus a reviewed PR.

## Lifecycle changes

`zskill lifecycle <id> <version> deprecated --reason "..." [--replaced-by id@ver] [--sunset YYYY-MM-DD]` appends an event and updates the mirror. `revoked` is terminal; resolvers skip revoked versions; dependents of a revoked skill fail validation, and of a deprecated skill warn.

## Evolving a canonical skill

Bump `version`, update evals, obtain fresh attestations for the new digest, `zskill release <id>`, `zskill index`, open a PR. Old versions stay in the ledger and at their tag.
