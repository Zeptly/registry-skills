## What changes

- [ ] New candidate skill (`candidates/...`)
- [ ] Evolution of a canonical skill (version bump)
- [ ] Promotion of an approved candidate
- [ ] Deprecation / retirement
- [ ] Protocol, schema, vocab or tooling change

**Skill ID(s) and version(s):**

## Why (evidence)

Link evidence: `provenance/evidence.yaml` entries (crowd / compute), incident, or external source.

## Checklist

- [ ] `zskill validate` passes locally; `pytest` passes
- [ ] Version bumped per SPECIFICATION §4 (security change = MAJOR)
- [ ] `zskill release <id>` run for canonical changes; `zskill index` regenerated
- [ ] Evals updated; eval report bound to the new digest (or explicit time-boxed waiver)
- [ ] No secrets, credentials or personal data
- [ ] For external capabilities: `provenance/assessment.yaml` present, sources pinned
- [ ] Security block reviewed (permissions, side effects, HITL, egress)
