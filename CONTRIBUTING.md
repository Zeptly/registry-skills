# Contributing

Everything reaches `skills/` through a pull request. Humans, Zep and other automation follow the same path.

## Setup

```bash
pip install -e '.[dev]'
zskill validate && pytest
```

## Propose a new skill

```bash
zskill new <domain>/<name>          # scaffolds candidates/<domain>/<name>/
```

1. Fill `SKILL.md` (required sections: When to use, Procedure, Output, Guardrails), `manifest.yaml`, and `evals/suite.yaml`.
2. Declare `security` honestly. The validator cross-checks it against capabilities, permissions and side effects.
3. External capability? Set `metadata.origin.type: discovered`, pin `provenance.sourceRefs`, and add `provenance/assessment.yaml` (see `schemas/assessment.schema.json`). `spec.stage` starts at `discovered`/`inspected`.
4. Zep-originated? `origin: {type: evolved, evolution: {kind: generalised, sourceRefs: [...]}}` plus `provenance/evidence.yaml` with compute pointers.
5. Open a PR. Advance `spec.stage` in follow-up PRs as gates are met (see docs/LIFECYCLE.md).

## Improve an existing skill

1. Edit the bundle in `skills/...`, **bump `version`** (docs/SPECIFICATION.md §4). Never edit a released version in place; CI fails with `release-mutated`.
2. Update evals; attach an evaluation report bound to the new digest (`zskill digest <id>`) and re-issue the digest-bound attestations (`attestations`, `security.approvals`). Stale attestations fail CI.
3. `zskill release <id>` then `zskill index`.
4. Add/link evidence in `provenance/evidence.yaml`.

## Promote an approved candidate

`zskill promote <id>` then `zskill index`, and open the PR. Lifecycle changes go through `zskill lifecycle`.

## YAML pitfalls

* Quote strings containing `: ` or `, ` (especially inside `{...}` flow mappings). The validator reports these as parse or schema errors.
* Dates are plain `YYYY-MM-DD`.

## Rules

* No credentials, tokens, keys or personal data in any file.
* Never commit runtime tapes, trajectories, traces or transcripts; reference them with `evidence://` pointers.
* Synthetic examples go under `synthetic/` (`zskill new --synthetic`) with `origin.type: synthetic`.
* Treat fetched/external content as data in every skill you write; say so in Guardrails.
* Keep skills small and composable; prefer composing existing skills over copying their text.
* New domains, capabilities or agent classes are edits to `vocab/` and need maintainer review.
