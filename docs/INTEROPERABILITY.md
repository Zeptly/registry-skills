# Interoperability

* **Agent Skills compatible.** `SKILL.md` frontmatter uses only `name`/`description` (+ optionally `license`, `compatibility`, `metadata`, `allowed-tools`). Any runtime that loads Agent Skills can load a Zeptly bundle by ignoring the extra files. The validator warns on non-portable frontmatter keys.
* **MCP.** MCP servers appear as `requires.tools` entries of `kind: mcp`. Skills reference the server by identifier only, never endpoints with credentials.
* **Registries.** `registry-execution-agents`, `registry-qb-agents`, `registry-tiny-agents` should reference skills by `id` + version (range or exact) + digest, and read `registry/index.json`. Suggested handshake: agent registry pins `zsk.x@1.2.0#sha256:...`; CI there runs `zskill resolve` (or reads the index) to confirm the pin exists and matches.
* **Timesavers / runtime-trigger.** Consume the index and ledger over raw Git; no library dependency required. `zskill` is a convenience, not a runtime requirement.
* **Zep.** Read-only on `skills/`; write only by PR into `candidates/` (new skills) or version-bump PRs (evolution). Writes must pass the same CI.
* **Evidence stores (AgentGit etc.).** Opaque URIs plus `skill_ref`/`skill_digest`.
* **Git tags.** `skill/<id>/v<version>` mark released versions (created by workflow) so consumers can fetch a historical bundle without walking history.
