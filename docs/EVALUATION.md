# Evaluation Framework

The registry defines **what** must be evaluated and **how results are bound to versions**; harnesses (outside this repo) execute cases.

1. **Suite** (`evals/suite.yaml`, schema `eval-suite.schema.json`): >= 2 cases with `inputs`, optional `fixtures/` (recorded tool outputs, documents), `kind` (`capability`, `regression`, `adversarial`, `safety`), `weight`, and expectations: `rubric` (single checkable criterion), `contains`/`not-contains`/`regex`, `json-schema`, `tool-called`/`tool-not-called`, `hitl-requested`. `environment.mode`: `recorded` (replayed tool results, deterministic), `sandbox`, or `live`.
2. **Requirements from security**: skills with external effects or classification >= moderate need `safety`/`adversarial` cases; HITL skills need a `hitl-requested` case.
3. **Report** (`provenance/eval-reports/<version>.yaml`, kind `EvaluationReport`, schema `eval-report.schema.json`): produced by a harness run; names runner/model/agent class, binds to the exact artifact via `subject: {registry, id, version, digest}` **and** `suiteDigest` (sha256 of the line-ending-normalized suite file), and gives `summary.passRate` (+ optional cost/latency). The manifest lists it as an `evaluation` attestation (`ref: bundle:provenance/eval-reports/<version>.yaml`) whose `subjectDigest` must equal the artifact's current digest.
4. **Gate**: an approval with `basis: evaluation` (and `evaluationRef`) requires the report to match the exact digest and suite and `summary.passRate >= spec.evaluation.min_pass_rate`. A **temporary protocol exception** (`basis: protocol-exception`, rule `promotion.required-evaluations`, `expiresOnVersion`) is the only alternative: it is not an evaluation, is surfaced as the CI warning `protocol-exception` and as `evidenceLevel: unevaluated` in the index, and becomes an error when the artifact reaches `expiresOnVersion` (a version threshold, not a calendar deadline).
5. **Regression discipline**: failures reported by evidence should become new `regression` cases in the next version (a PATCH), so the fix is locked in.
6. **Cross-agent evaluation**: run the suite per compatible agent class; report one file per class if results differ.

The repository validates suite structure and report binding; it does not run agents.
