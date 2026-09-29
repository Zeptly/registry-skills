# Security Model

Skills can instruct agents to touch external systems, so risk is *declared, validated and reviewed* before a skill becomes canonical. The manifest describes what a skill **needs and does**; the runtime decides what to **grant**. A manifest never grants anything.

## Declared in `security`

| Field | Values / meaning |
|---|---|
| `classification` | `low` / `moderate` / `high` / `critical` overall risk tier. |
| `data_sensitivity` | `public`, `internal`, `confidential`, `personal`, `regulated` (set). |
| `side_effects` | `none` < `read-external` < `write-external` < `destructive`. |
| `permissions` | Abstract `scope` + `access` (`read`/`write`/`delete`/`execute`) + `reason`. |
| `external_systems` | Systems touched (web, api, mcp, saas, database, filesystem). |
| `network` | `egress`: `none` / `allowlist` (+ `allowed_domains`) / `open`. |
| `authentication` | Whether required, acceptable methods; `credential_handling` is always `runtime-injected`. |
| `destructive_operations` | Each operation and whether it is reversible. |
| `hitl` | Required flag + explicit triggers; needs `cap.human.confirm`. |

## Validator-enforced consistency (see `validate.py`)

* Destructive => classification >= high, HITL required, operations listed.
* `write`/`delete` permissions imply matching side effects; write-external => >= moderate.
* Personal/regulated data => >= high (and `security_reviewed_by` at approval); confidential => >= moderate.
* Critical => HITL. HITL needs triggers and the confirm capability, and an eval case expecting `hitl-requested`.
* Network use (web capabilities, MCP/API tools, external effects) must declare egress; `none` may not contradict it; `allowlist` needs domains.
* Authentication required => methods + runtime-injected credentials.
* Skills with external effects or classification >= moderate need a `safety` or `adversarial` eval case.
* Composition cannot hide privilege: a composite's classification, side effects, HITL, sensitivity, permissions, capabilities and egress must cover every child's.
* Secret scan over every text file (cloud keys, GitHub/Slack/API tokens, private keys, credentialed URLs, assigned secrets).

## Trust and provenance

`trust_tier`: `first-party` (authored/reviewed by Zeptly), `reviewed-third-party` (external, assessed), `unreviewed` (never valid beyond `inspected`). External sources MUST be pinned (ref or digest) before approval. Skills derived from external documents inherit prompt-injection risk: each such skill must state in Guardrails that fetched content is data.

## Human review

CODEOWNERS routes `security` blocks, `vocab/`, `schemas/`, `tools/`, ledgers and approvals to maintainers. Any security-block change is a MAJOR version (mechanically enforced).

## Runtime expectations (out of scope here, required of consumers)

Enforce declared egress and permissions as an allowlist; inject credentials from a secret store; prompt the human at `hitl.triggers`; log the resolved `id@version`+digest with each run. Vulnerability reports: see `/SECURITY.md`.
