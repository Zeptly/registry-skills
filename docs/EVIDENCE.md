# Evidence Reference Model

Two streams feed skill improvement:

* **Wisdom of the crowd (`wisdom: crowd`)**: human corrections, preferences, best practice, incidents.
* **Wisdom of compute (`wisdom: compute`)**: execution trajectories, failure clusters, retries, tool sequences, cost/latency, eval results.

Neither mutates a canonical skill. Evidence is **referenced, not embedded**: raw trajectories stay in the evidence system; the registry stores small pointers in `provenance/evidence.yaml` (schema `evidence.schema.json`).

```yaml
schema: zeptly.evidence/v1
skill: zsk.web-research
refs:
  - evidence_id: ev-cluster-2026-09-fetch-retry
    wisdom: compute
    kind: failure-cluster
    skill_ref: zsk.web-research@1.0.0        # exact version observed
    skill_digest: sha256:...                  # exact content observed
    uri: agentgit://<namespace>/cluster/1234  # opaque pointer into the evidence store
    content_digest: sha256:...                # optional integrity of the referenced artefact
    recorded_at: 2026-10-02T09:00:00Z
    summary: 31% of runs across 4 agent classes stall after step 3 when pages need JS rendering.
    supports: failure-pattern                 # improvement | regression | failure-pattern | confirmation | new-skill
    agent_classes_observed: [execution, qb, timesaver]
    n_agents: 14
    n_runs: 212
    metrics: {failure_rate: 0.31}
```

## Why version + digest

Reproducibility requires knowing *exactly* what an agent was told. `skill_ref` + `skill_digest` identify the bytes; `zskill resolve` extends this to composed skills. The ledger proves the digest belonged to the version.

## Cross-agent learning

`agent_classes_observed` / `n_agents` let a steward (Zep) distinguish "one agent misbehaved" from "the shared blueprint is at fault". A failure pattern across many independent agents is the trigger to propose a new version (MINOR/PATCH) or, if the contract must change, MAJOR.

## Coupling to AgentGit

None by code. `uri` is opaque (`agentgit://`, `zep://`, `https://`). Mapping guidance: AgentGit external/internal session and checkpoint identifiers, being local integers, MUST be namespaced in the URI. Branch-on-rollback histories are useful compute evidence (they show where a run diverged from the skill's procedure). Tool-revert records document reversibility, which can inform `destructive_operations.reversible`.

## Rules

* Evidence files are append-only in practice (review enforces; entries have stable `evidence_id`).
* Compute evidence on canonical skills SHOULD carry `skill_digest`.
* `zep-generalised` candidates MUST cite at least one compute ref.
* Never put raw prompts, personal data or credentials in evidence summaries.
