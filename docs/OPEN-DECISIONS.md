# Open Decisions

Baseline choices were made to keep the protocol usable; these need owner input.

| # | Decision | Baseline chosen | Question |
|---|---|---|---|
| 1 | **ID scheme** | `zsk.<slug>` (flat namespace) | Need org/publisher namespaces for third-party contributions (`zsk.<org>.<slug>`)? |
| 2 | **Evidence URI scheme** | Opaque `scheme:` pointer | Agree canonical schemes (`agentgit://`, `zep://`) and namespacing of AgentGit integer IDs. |
| 3 | **Eval harness** | Schemas + gates only | Who owns the runner? Where do reports run (per agent class)? Grader trust (LLM-as-judge calibration). |
| 4 | **Seed skills approved by waiver** | Time-boxed waiver, warns in CI | Ratify or replace with executed evals before 1.1.0. Waiver approver is a placeholder team handle. |
| 5 | **Capability vocabulary** | Small starter set | Governance for growth; mapping to each runtime's actual tools. |
| 6 | **Agent-class governance** | Five classes | Should classes have capability profiles (e.g. `tiny` = no `cap.web.fetch`)? |
| 7 | **Signing** | Content digests only | Sign release tags / ledger entries (sigstore/gitsign)? Required for third-party trust tiers. |
| 8 | **Semver for procedure changes** | Mechanical for contract/security; review for the rest | Add eval-delta-based guidance (e.g. pass-rate drop => MAJOR)? |
| 9 | **Multiple versions in one resolution** | Not allowed | Needed for gradual roll-outs / canary versions? Channel (`stable`/`canary`) model? |
| 10 | **Licensing of contributed skills** | `authorship.license` optional; repo has no LICENSE yet | Choose repo licence and contribution terms (DCO/CLA). |
| 11 | **Non-text assets** | Digest covers bytes; secret scan skips binaries | Policy for images/PDF fixtures and size limits. |
| 12 | **Deletion/erasure** | Retired IDs are kept forever | Procedure for legal takedowns that must alter history. |
| 13 | **Zep write identity** | PR from a bot account | Required signing/branch protections and rate limits for automated proposals. |
| 14 | **Consumers pinning strategy** | Recommend id+version+digest | Do agent registries pin exact versions or ranges with a lockfile? |

## Added by Registry Protocol v0.1 alignment (all need cross-registry reconciliation)

| # | Decision | Baseline chosen | Question |
|---|---|---|---|
| 15 | **Evaluation waiver** | Transitional, time-boxed waiver lets the six seed skills be canonical | Does the protocol permit waivers at all? If not, seeds return to `candidate`. |
| 16 | **`metadata.id` grammar** | Envelope: `[a-z0-9]+([.-][a-z0-9]+)*`; skills: slug only | Shared grammar across registries; is a dot-namespaced form (`research.web-fact-check`) expected for skills? |
| 17 | **Classification vocabulary** | `low/moderate/high/critical` for skills | Shared vocabulary (protocol example shows `restricted`)? |
| 18 | **Vocabularies** | `origin.type`, `evolution.kind`, attestation types are registry-local | Shared enums? |
| 19 | **Attestation and approval shapes** | Same `{type, ref, subjectDigest}` shape for both | Separate shapes? Who may issue which type? |
| 20 | **Candidate storage** | Separate `candidates/` tier | Candidates as in-tree objects vs. a shared candidate store across registries? |
| 21 | **Digest scope** | Governance state excluded from digest | Same rule in every registry, or must attestation-bearing fields be included? |
| 22 | **Security review for lower classifications** | PR-level CODEOWNERS review | Explicit `securityReviewedBy` for every promotion? |
