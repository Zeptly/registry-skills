# Evidence References

The full Evidence Protocol (ownership, schema, transport) is deferred by Registry Protocol v0.1. This registry stores only **pointers** and digest-bound attestations.

* **Crowd wisdom**: human corrections, preferences, best practice, incidents.
* **Compute wisdom**: execution summaries, failure clusters, cost/latency, eval results.

Neither writes to a canonical skill. Raw tapes, trajectories, transcripts and sensitive runtime artifacts NEVER enter registry Git; the validator rejects them by name/extension/size, and evidence pointer URIs must be `evidence://` or `https://`.

```yaml
apiVersion: registry.zeptly.dev/v1alpha1
kind: EvidenceReferences
subject: {registry: skills, id: web-research}
refs:
  - evidenceId: ev-cluster-2026-10-fetch-retry
    wisdom: compute
    kind: failure-cluster
    subject: {registry: skills, id: web-research, version: 1.0.0, digest: "sha256:..."}   # exact artifact observed
    uri: evidence://store/cluster/1234           # opaque pointer, resolved outside this repo
    contentDigest: "sha256:..."                  # optional integrity of the referenced artefact
    recordedAt: "2026-10-02T09:00:00Z"
    summary: 31% of runs across 4 agent classes stall after step 3 when pages need JS rendering.   # <= 500 chars, no payloads
    supports: failure-pattern
    agentClassesObserved: [execution, qb, timesaver]
    nAgents: 14
    nRuns: 212
    metrics: {failureRate: 0.31}
```

Stored at `provenance/evidence.yaml` (outside the digest, so evidence accumulates without new versions). `subject` is the structured reference `{registry, id, version, digest}`.

## Resolution locks

`declared range -> resolver -> exact version -> content digest -> runtime lock -> evidence`. `zskill resolve <id> [--range r]` produces the `ResolutionLock` (`schemas/resolution-lock.schema.json`): exact version, digest and directory seal for the skill and its composed skills (skipping revoked versions), plus an explicit `unresolved` list for references to other registries. Foreign references are never silently omitted and never fetched. Runtimes record this lock in their evidence so a run can be tied to exact content. No network access to other registries is needed or attempted.

## Attestations

Manifest `attestations` (e.g. `evaluation`) and `security.approvals` (e.g. `governance`) bind `subjectDigest` to the exact artifact digest. In-bundle records (`bundle:provenance/...`) are eval reports and approvals whose own `subject.digest` must also match. A stale digest fails validation.

## Cross-agent learning

`agentClassesObserved`, `nAgents`, `nRuns` let a steward distinguish a single misbehaving agent from a shared blueprint defect. Evidence never edits a skill: a reviewed PR proposing a new version does.

## Rules

* Evidence entries are append-only in practice (stable `evidenceId`; review enforces).
* Generalised evolutions MUST cite >= 1 compute evidence pointer.
* No raw prompts, personal data, credentials or transcripts in summaries.
* Evidence-store scheme names beyond `evidence://` are not defined here (deferred).
