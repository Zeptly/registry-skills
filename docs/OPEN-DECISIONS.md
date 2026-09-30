# Open Decisions

Baseline choices were made to keep the protocol usable; these need owner input.

| # | Decision | Baseline chosen | Question |
|---|---|---|---|
| 1 | **ID scheme** | Shared grammar `^[a-z0-9]+([.-][a-z0-9]+)*$`, no prefix; `registry: skills` disambiguates | Need org/publisher namespaces for third-party contributions? |
| 2 | **Evidence URI scheme** | Pointers are `evidence://` or `https://` only (`file:`/`data:` rejected); Evidence Protocol deferred | Agree canonical evidence-store schemes and namespacing of AgentGit integer IDs. |
| 3 | **Eval harness** | Schemas + gates only | Who owns the runner? Where do reports run (per agent class)? Grader trust (LLM-as-judge calibration). |
| 4 | **Seed skills canonical by temporary protocol exception** | Explicit `basis: protocol-exception`, expiry at version 1.1.0 (a version threshold, not a calendar deadline), CI warning | Ratify or replace with executed evals before 1.1.0. The approver is a placeholder team handle. |
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
| 15 | **Evaluation exception** | Transitional, expiring protocol exception lets the six seed skills be canonical | Does the protocol permit exceptions at all? If not, seeds return to `candidate`. |
| 16 | **`metadata.id` grammar** | Shared `[a-z0-9]+([.-][a-z0-9]+)*` (<= 96) for envelope and skills; `SKILL.md` name uses hyphens | Shared grammar across registries; is a dot-namespaced form (`research.web-fact-check`) expected for skills? |
| 17 | **Classification vocabulary** | `low/moderate/high/critical` for skills | Shared vocabulary (protocol example shows `restricted`)? |
| 18 | **Vocabularies** | `origin.type`, `evolution.kind`, attestation types are registry-local | Shared enums? |
| 19 | **Attestation and approval shapes** | Same `{type, ref, subjectDigest}` shape for both | Separate shapes? Who may issue which type? |
| 20 | **Candidate storage** | Separate `candidates/` tier | Candidates as in-tree objects vs. a shared candidate store across registries? |
| 21 | **Digest scope** | Governance state excluded from digest | Same rule in every registry, or must attestation-bearing fields be included? |
| 22 | **Security review for lower classifications** | PR-level CODEOWNERS review | Explicit `securityReviewedBy` for every promotion? |

## Added by Registry Protocol v0.2 adoption (all need cross-registry reconciliation)

| # | Decision | Baseline chosen | Question |
|---|---|---|---|
| 23 | **Shared vectors** | Skills-generated vectors (`tests/vectors/zeptly-jcs-v1.skills-generated.json`) + stdlib reference verifier; the shared set was not supplied | Replace/reconcile with the protocol's shared vectors; every divergence is a contract bug in one side. |
| 24 | **Seal payload entry shape** | `{path, sha256}` objects, `sha256` as `sha256:<hex>` | Bare hex? Extra per-entry fields? |
| 25 | **Payload membership** | Everything except root `manifest.yaml` and `provenance/**`; `CHANGELOG.md`, `.gitkeep`, nested `manifest.yaml` are payload | Which files are excluded by name, if any? |
| 26 | **`version` in the seal** | Included (amendment lists it); excluded from the artifact digest | Confirm. |
| 27 | **`subjectSeal` on attestations** | Skills extension: attestations bind the seal too, because SKILL.md/evals/examples are outside the artifact digest | Shared attestation shape? Should payload be part of the digest instead? |
| 28 | **Evaluation suite identity** | `{id: spec.evaluation.suite path, version: artifact version, digest: sha256(suite bytes)}` | Suites as versioned objects with their own ids? |
| 29 | **Payload text policy** | UTF-8, no BOM, no NUL, LF-only for every payload file | Binary payload policy (decision 11 remains open). |
| 30 | **Resolved lock entry shape** | `{registry,id,version,digest,digestAlgorithm,maturity,lifecycle,domain,directorySeal?}` | Shared shape; the amendment shows only the unresolved form. |
| 31 | **`discovered` origin** | Replaces the local `spec.markers.provenance` marker; github-pr-triage migrated | Confirm migration of external discoveries. |
| 32 | **Peer-index fixture** | Hand-written fixture only (`tests/fixtures/`); no real peer index was available | Supply a real peer-registry index to satisfy adoption test 5. |
| 33 | **Synthetic prefix** | `example.` reserved for synthetic ids | Shared prefix across registries? |
