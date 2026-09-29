# Zeptly Skills Registry

Canonical Git ledger of **Skill Blueprints**: versioned, evaluable, evolving procedural knowledge that Zeptly agents apply when performing operations.

> Agents execute. Blueprints persist. Evidence accumulates. Zep generalises. Git canonises.

A Skill Blueprint is *not* a prompt snippet. It is a bundle of portable human-readable procedure (`SKILL.md`), machine-readable metadata (`manifest.yaml`), an eval suite, examples and provenance, identified by a stable ID and released as immutable, content-addressed versions.

## Layout

```
skills/<domain>/<name>/        canonical skills (active | deprecated | retired)
candidates/<domain>/<name>/    proposals in flight (discovered ... approved | rejected)
  SKILL.md                     portable procedure (Agent Skills-compatible frontmatter)
  manifest.yaml                machine-readable contract (schemas/manifest.schema.json)
  evals/suite.yaml             evaluation cases (+ fixtures)
  examples/                    worked examples
  provenance/                  evidence refs, assessments, approvals, eval reports (append-only, outside the digest)
registry/releases/<id>.yaml    append-only ledger: id@version -> content digest
registry/index.json            generated discovery index (ID -> path, latest version, digests)
schemas/                       JSON Schemas for every file type
vocab/                         controlled vocabularies: domains, agent classes, capabilities
tools/zskill/                  validator / release / promote / resolve CLI
docs/                          specification and architecture
```

## Quick start

```bash
pip install -e '.[dev]'
zskill validate            # everything CI checks
zskill new research/my-skill   # scaffold a candidate
zskill resolve zsk.competitive-intelligence   # pin a skill + its closure to exact versions/digests
pytest
```

## Key ideas

| Idea | Mechanism |
|---|---|
| Stable identity | `id: zsk.<slug>` is independent of path and domain; consumers resolve via `registry/index.json`. |
| Reproducibility | Released versions are immutable; each has a `sha256` digest in the ledger. Evidence cites `id@version` + digest. |
| Safe evolution | Improvements are new versions. Semver is *enforced* on contract/security changes. |
| Discovery is not access | `discovered → inspected → candidate → evaluating → approved → active`. External capabilities need a recorded assessment. |
| Two kinds of wisdom | Crowd (human) and compute (machine) evidence are referenced, never applied directly; only a reviewed PR changes a canonical skill. |
| Composition without hiding | Skills compose other skills via bounded, acyclic, version-ranged dependencies; a composite must declare at least its children's privileges. |
| No secrets | Manifests describe permissions and auth *requirements*; credentials are runtime-injected. CI scans for secrets. |

Read next: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), [docs/SPECIFICATION.md](docs/SPECIFICATION.md), [docs/LIFECYCLE.md](docs/LIFECYCLE.md), [CONTRIBUTING.md](CONTRIBUTING.md).

## Status

Protocol `zeptly.skill/v1`. The six seed skills ship approved **by waiver** (no executed evals yet); the index reports them as `evidence_level: unevaluated` and CI emits a warning for each until an eval harness produces reports. See [docs/OPEN-DECISIONS.md](docs/OPEN-DECISIONS.md).
