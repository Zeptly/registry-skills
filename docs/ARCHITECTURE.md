# Architecture

## 1. Research inputs

**Agent Skills format** (agentskills.io; adopted by several agent products): a skill is a directory with `SKILL.md` = YAML frontmatter (`name`, `description` required; `license`, `compatibility`, `metadata`, `allowed-tools` optional) + Markdown body, with optional supporting files. Names are lowercase-hyphenated, <= 64 chars; descriptions <= 1024 chars and must say what the skill does *and when to use it*. Unknown frontmatter keys are ignored by compliant runtimes. Strengths: portable, human-readable, progressive disclosure. Gaps for Zeptly: no stable ID beyond name, no immutable versions, no lifecycle, no security/permission model, no evidence or eval linkage, no composition semantics.

**Agent-Git** (MAS-Infra-Layer/Agent-Git): version control for agent state. Concepts: *external session* (a user's whole conversation scope) containing *internal sessions* (agent instances); *checkpoints/commits* (snapshots of context + tool usage) with integer IDs; rollbacks create *new branches* instead of destroying history; *state revert* and *tool revert* (registered reverse functions for side effects). Relevance: it is a natural producer of Wisdom-of-Compute evidence (trajectories, retries, branches from failures). Note its IDs are integers local to its database, so they are unsuitable as durable cross-system references without a namespace.

**Design consequence:** adopt the Agent Skills bundle as the portable core (so any compliant runtime can read `SKILL.md`), and add a Zeptly *manifest layer* beside it for identity, versioning, lifecycle, security, evidence and composition. Couple to AgentGit only through an opaque evidence URI.

## 2. Canonical Skill Blueprint architecture

```
                     ┌──────────────── Git (canonical ledger) ────────────────┐
 crowd wisdom ──┐    │ candidates/  ──promote──▶ skills/  ──release──▶ ledger │
 (humans, PRs)  ├──▶ PR ─▶ CI (zskill validate) ─▶ review ─▶ merge ─▶ tag     │
 compute wisdom ┘    └───────────────▲──────────────────────────┬─────────────┘
 (Zep proposes)                      │ evidence refs            │ index.json / tags
 external discovery ─▶ assessment ───┘                          ▼
 (MCP, APIs, tools)                       consumers: registry-execution-agents, registry-qb-agents,
                                          registry-tiny-agents, Timesavers, runtime-trigger, Zep
```

Three layers per skill:

1. **Procedure** (`SKILL.md`): portable, what an agent reads.
2. **Contract** (`manifest.yaml`): identity, version, compatibility, requirements, I/O, dependencies, security, evaluation requirements.
3. **Ledger of trust** (`provenance/`, `registry/releases/`): assessments, approvals, eval reports, evidence pointers, release digests.

Identity and immutability:

* `metadata.id` (`web-research`, with `registry: skills`) is permanent. Path and domain are classification, not identity, so a skill can be re-filed without breaking any reference. `spec.compatibility.supersedes` and lifecycle-overlay `replacedBy` express merges/splits.
* A **skill ref** is `id@semver` (exact) or `id` + range (dependencies). `id@version` + `sha256` digest is what execution evidence carries.
* The **digest** covers `SKILL.md`, `manifest.yaml` (minus governance state: maturity, stage, lifecycle, attestations, approvals), `evals/`, `examples/`. It excludes `provenance/` and `CHANGELOG.md` so evidence can accumulate *about* a version without creating a new one, and excludes governance state so promotion or deprecation does not change what a version *is*.
* The **ledger** (`registry/releases/<id>.yaml`) is append-only. CI rejects modified released content and (via `zskill ledger-check`) rewritten ledger history.

## 3. Repository tree

See README. Design notes:

* Two top-level tiers (`skills/`, `candidates/`) make the "never canonical until promoted" rule structural and reviewable via CODEOWNERS.
* Evolution of an *existing* skill is a PR against `skills/<domain>/<name>` that bumps `version`; there is no shadow copy in `candidates/`. The immutability ledger guarantees the old version stays reproducible (git tag `skill/<id>/v<version>` plus digest).
* Per-skill ledger files avoid merge conflicts between unrelated skills.
* `registry/index.json` is generated and CI-verified; consumers use it to map ID to path, so paths are never part of any contract.

## 4. Interoperability requirements

| Consumer | Needs | Provided by |
|---|---|---|
| Any Agent Skills runtime | Read `SKILL.md` | Standard frontmatter; extra keys only in manifest |
| registry-execution-agents / qb / tiny | Filter by agent class, capability, size | `compatibility.agent_classes`, `requires`, tiny size cap |
| Timesavers (separate runtime) | Stable IDs, no path coupling | `id`, index, digests |
| runtime-trigger | Resolve a skill to a pinned version | `zskill resolve`, index `released_versions` |
| Zep | Read evidence, propose candidates safely | Evidence schema, candidate tier, PR-only writes, no direct canonical write |
| AgentGit / any evidence store | Reference exact skill used | structured `subject {registry, id, version, digest}` in evidence; opaque `evidence://` `uri` back |

**Direction of dependency:** consumers depend on this registry; this registry depends on none of them. Agent-class names are strings in a vocabulary file, not imports. There are no circular dependencies: the only inputs are PRs and evidence *pointers*.

Consumers should: (1) pin `id@version` + digest in agent registries, (2) verify the digest after checkout, (3) record the resolution (`zskill resolve` output) with each run, (4) never execute a skill whose status is not `active`/`deprecated`.

## 5. Governance of change

No signal (human or machine) writes to `skills/` directly. Every path ends in a PR that passes CI and review by the owners of the path (`CODEOWNERS`). See LIFECYCLE.md, EVIDENCE.md and SECURITY-MODEL.md.

## 6. Unresolved decisions

Tracked in [OPEN-DECISIONS.md](OPEN-DECISIONS.md).
