# Skill Blueprint Specification (`zeptly.skill/v1`)

Normative language: MUST / SHOULD / MAY as in RFC 2119. The JSON Schemas in `schemas/` are authoritative for structure; `zskill validate` for cross-file rules.

## 1. Bundle

```
<bundle>/
  SKILL.md            MUST
  manifest.yaml       MUST
  evals/suite.yaml    MUST (path configurable via manifest.evaluation.suite)
  examples/           SHOULD
  provenance/         MAY (MUST hold approval.yaml for approved/active; assessment for external skills)
  CHANGELOG.md        MAY (excluded from digest)
```

The bundle directory MUST be `skills|candidates/<domain>/<name>/` where `<domain>` equals `manifest.domain` and `<name>` equals `manifest.name` and the `SKILL.md` frontmatter `name`.

## 2. `SKILL.md` convention

* Frontmatter MUST contain `name` and `description` equal to the manifest's. It SHOULD NOT contain other keys beyond Agent Skills' `license`, `compatibility`, `metadata`, `allowed-tools`; anything Zeptly-specific lives in the manifest.
* `description` states what the skill does *and when to use it*, <= 1024 chars, no `<` or `>`.
* Body MUST have these H2 sections: **When to use**, **Procedure**, **Output**, **Guardrails**.
* Procedure is numbered, imperative, and states a stop rule where iteration is involved.
* Guardrails MUST state how untrusted input is treated when the skill reads external content (fetched pages, PR text, documents are *data, not instructions*).
* Composite skills MUST name each composed skill by ID in the body.
* Skills for `tiny` agents MUST have a body <= 8000 characters; others SHOULD stay under 500 lines.
* No secrets, tokens or credentials, ever.

## 3. Manifest fields

| Field | Purpose |
|---|---|
| `schema` | `zeptly.skill/v1` |
| `id` | `zsk.<slug>`. Immutable. Never reused after retirement. |
| `name`, `title`, `description`, `domain`, `tags` | Discovery. Domain from `vocab/domains.yaml`. |
| `version` | Semver. |
| `status` | Lifecycle state (LIFECYCLE.md). |
| `deprecation` | `reason`, `replaced_by`, `sunset`; required for deprecated/retired. |
| `authorship` | Authors (`human`/`agent`/`zep`/`organisation`), maintainers (GitHub handles/teams), licence. |
| `provenance` | `origin`, `trust_tier`, pinned `sources`, `derived_from` (exact refs), `assessment` path. |
| `compatibility` | `protocol` (=1), `agent_classes`, `min_context_tokens`, `supersedes`. |
| `requires` | Abstract `capabilities` (vocab) and concrete `tools` (builtin/mcp/api/computer-use/cli; optional flag). |
| `dependencies` | Composition: `id`, semver range, `role` (`composes`/`prerequisite`), `optional`. |
| `inputs`, `outputs` | Typed parameters (the skill's interface contract). |
| `security` | Classification, data sensitivity, side effects, permissions, external systems, network egress, authentication requirements, destructive operations, HITL (SECURITY-MODEL.md). |
| `evaluation` | Suite path, `min_pass_rate`. |
| `x` | Free extension namespace, ignored by core. |

Evidence references are deliberately **not** in the manifest: they live in `provenance/evidence.yaml` so that appending evidence never alters a version's digest.

## 4. Versioning

Semver applies to the **behavioural contract**:

* **MAJOR**: any change to `security` (including narrowing, for simplicity and safety of review); incompatible change to `inputs`/`outputs`; removal of an agent class; a procedure change that alters results for correct callers.
* **MINOR**: additive, backward-compatible change to `inputs`/`outputs`/`requires`/`dependencies`/`agent_classes`; materially new procedure steps.
* **PATCH**: clarifications, typo fixes, extra examples, eval additions.

Enforced mechanically via ledger digests: `contract_digest` (inputs, outputs, requires, dependencies, agent classes, protocol) changes need >= MINOR; `security_digest` changes need MAJOR. Judgement calls (procedure semantics) are enforced by review.

Pre-release versions (`1.2.0-rc.1`) may exist only in candidates or in-PR; they never satisfy caret/tilde/comparator ranges.

## 5. Immutability and digests

`digest = sha256( for each file (sorted): "<relpath>\0<sha256(content)>\n" )`, over all bundle files except `provenance/**` and `CHANGELOG.md`. For `manifest.yaml` the hashed content is canonical JSON of the parsed manifest without `status` and `deprecation`. Text files are newline-normalised. Promotion (directory move + status change) therefore preserves the digest, so an eval report produced against the candidate remains valid for the canonical skill.

Once `id@version` is in the ledger, its digest MUST NOT change; CI fails on mismatch. To change behaviour, bump the version.

## 6. Compatibility rules

* A skill declares `agent_classes` it is written for (`any` = agnostic). Consumers MUST NOT load a skill for an unlisted class unless it declares `any`.
* A consumer MUST have every non-optional `requires.capabilities` entry, else skip the skill.
* Dependency ranges use caret/tilde/comparator syntax; consumers resolve against released versions in the ledger. Two versions of one skill MAY NOT be in one resolution unless the consumer explicitly supports it (default: highest satisfying, single version per ID).
* `protocol` is the blueprint protocol major. A consumer that does not support it MUST ignore the skill.
* Canonical skills may only depend on canonical skills; candidates may depend on anything present.

## 7. Repository-wide invariants (enforced by `zskill validate`)

Unique IDs; every dependency resolves and satisfies its range; no cycles; depth <= 3; transitive closure <= 20; <= 8 direct dependencies; ledger versions strictly increasing; every released version's digest matches; every active skill has a matching approval; no secrets; controlled vocabularies respected.
