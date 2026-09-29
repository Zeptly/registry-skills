# Lifecycle and Promotion

## Status model

```
discovered → inspected → candidate → evaluating → approved ──promote──▶ active ─▶ deprecated ─▶ retired
     └──────────────┴──────────┴───────────┴────────┴──▶ rejected (terminal, kept for the record)
```

| Status | Meaning | Lives in | Gate to enter |
|---|---|---|---|
| `discovered` | Idea/capability noticed (external or internal). Nothing trusted. | `candidates/` | A skeleton PR with sources listed. |
| `inspected` | Provenance and security assessment recorded. | `candidates/` | `provenance/assessment.yaml` (external origins). |
| `candidate` | Procedure drafted; assessment verdict allows proceeding. | `candidates/` | Verdict `proceed-*`; valid SKILL.md + manifest + suite. |
| `evaluating` | Eval harness is running/being reviewed. | `candidates/` | Suite passes structural validation. |
| `approved` | A human authorised release of *this exact digest*. | `candidates/` | `approval.yaml` citing an eval report >= `min_pass_rate` (or a time-boxed waiver). |
| `active` | Canonical; consumers may run it. | `skills/` | `zskill promote` (moves, releases, ledger). |
| `deprecated` | Still resolvable; replacement/sunset given. | `skills/` | `deprecation` block. |
| `retired` | No longer resolvable for new work; history kept, ID never reused. | `skills/` | `deprecation` block. |

`unreviewed` trust tier can never reach `approved`. High/critical skills need `security_reviewed_by`.

## Pipelines

**Internal discovery (wisdom of compute)**: execution evidence in an evidence store → recurring pattern detected by Zep → Zep opens a PR adding `candidates/<domain>/<name>/` with `origin: zep-generalised` and `provenance/evidence.yaml` compute refs → CI → evaluation → human approval → `zskill promote`. For an *existing* skill, Zep opens a PR against `skills/...` with a version bump and evidence refs; the same gates apply.

**External discovery**: MCP server / API / platform / model found → `discovered` skeleton with pinned `sources` → inspection recorded in `assessment.yaml` (reputation, licence, capabilities, auth, data exposure, side effects, prompt-injection surface, supply chain) → verdict → candidate → evaluate in sandbox → approve → promote. *Discovery never grants runtime access*: runtimes only load `active` skills, and the manifest declares the tools/permissions the runtime must separately grant.

**Crowd wisdom**: humans open PRs (or issues using the skill-proposal template) with corrections/best practice; evidence entries with `wisdom: crowd` link the source.

## Evolving a canonical skill

1. Branch; edit the bundle; bump `version` per SPECIFICATION §4.
2. Update evals; obtain an eval report for the new digest; update `provenance/approval.yaml`.
3. `zskill release <id>` appends the ledger entry; `zskill index` refreshes the index.
4. PR → CI (validate, tests, ledger append-only, index current) → CODEOWNER review → merge → tag `skill/<id>/v<version>` (automated).

Old versions remain in the ledger and git history at the tag. Evidence keeps pointing at the exact version it observed.

## Promotion of a candidate

1. `status: approved`, approval + report present, all validation green.
2. `zskill promote <id>`: moves the bundle to `skills/<domain>/<name>/`, sets `status: active`, writes the release entry (digest verified unchanged).
3. `zskill index`; open PR; owners review; merge.

## Deprecation and retirement

Set `status: deprecated`, add `deprecation.reason` (+ `replaced_by`, `sunset`). Dependents get a validation warning. Retire only after dependents migrate; retired IDs stay in the ledger and are never reassigned.
