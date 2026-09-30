# Zeptly Skills Registry

Canonical Git ledger of **Skill Blueprints**: versioned, evaluable, evolving procedural knowledge that Zeptly agents apply when performing operations.

> Agents execute. Blueprints persist. Evidence accumulates. Zep generalises. Git canonises.

A Skill Blueprint is *not* a prompt snippet. It is a bundle of portable human-readable procedure (`SKILL.md`), machine-readable metadata (`manifest.yaml`), an eval suite, examples and provenance, identified by a stable ID and released as immutable, content-addressed versions.

## Layout

```
skills/<domain>/<id>/          canonical artifacts (maturity: canonical)
candidates/<domain>/<id>/      candidate artifacts (maturity: candidate)
synthetic/<domain>/<id>/       isolated synthetic examples (never in the production index)
  SKILL.md                     portable procedure (Agent Skills-compatible frontmatter)
  manifest.yaml                Registry Protocol v0.1 envelope + skills spec (schemas/skill-blueprint.schema.json)
  evals/suite.yaml             evaluation cases (+ fixtures)
  examples/                    worked examples
  provenance/                  evidence refs, assessments, approvals, eval reports (append-only, outside the digest)
registry/releases/<id>.yaml    append-only ledger: id@version -> content digest
registry/lifecycle/<id>.yaml   append-only lifecycle overlay (active | deprecated | revoked)
registry/index.json            deterministic generated index (identity, version, digest, maturity, lifecycle, origin, location)
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
zskill resolve competitive-intelligence   # range -> exact version + digest lock
pytest
```

## Key ideas

| Idea | Mechanism |
|---|---|
| Stable identity | `metadata.id` + `registry: skills`; structured references `{registry, id, version, digest?}`; consumers resolve via the index, never paths. |
| Reproducibility | Released versions are immutable; each has a `sha256` digest in the ledger. Evidence cites `id@version` + digest. |
| Safe evolution | Improvements are new versions. Semver is *enforced* on contract/security changes. |
| Independent state | `maturity` (candidate/canonical), `origin` and `lifecycle` (append-only overlay) are separate fields. Discovery never grants access; external capabilities need a recorded assessment. |
| Digest-bound attestations | Attestations name the exact artifact digest and fail validation when stale. |
| Two kinds of wisdom | Crowd (human) and compute (machine) evidence are referenced, never applied directly; only a reviewed PR changes a canonical skill. |
| Composition without hiding | Skills compose other skills via bounded, acyclic, version-ranged dependencies; a composite must declare at least its children's privileges. |
| No secrets | Manifests describe permissions and auth *requirements*; credentials are runtime-injected. CI scans for secrets. |

Read next: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), [docs/SPECIFICATION.md](docs/SPECIFICATION.md), [docs/LIFECYCLE.md](docs/LIFECYCLE.md), [CONTRIBUTING.md](CONTRIBUTING.md).

## Status

Implements Zeptly Registry Protocol v0.1 (`apiVersion: registry.zeptly.dev/v1alpha1`); see [docs/PROTOCOL-ALIGNMENT.md](docs/PROTOCOL-ALIGNMENT.md) for interpretations needing cross-registry reconciliation. Draft PR only, not merged. The six seed skills are canonical only under an explicit, expiring **temporary protocol exception** (`basis: protocol-exception`, expiry at version 1.1.0; no executed evaluations yet); the index reports them as `evidenceLevel: unevaluated` and CI emits a `protocol-exception` warning for each. See [docs/OPEN-DECISIONS.md](docs/OPEN-DECISIONS.md).
