# Evaluation Framework

The registry defines **what** must be evaluated and **how results are bound to versions**; harnesses (outside this repo) execute cases.

1. **Suite** (`evals/suite.yaml`, schema `eval-suite.schema.json`): >= 2 cases with `inputs`, optional `fixtures/` (recorded tool outputs, documents), `kind` (`capability`, `regression`, `adversarial`, `safety`), `weight`, and expectations: `rubric` (single checkable criterion), `contains`/`not-contains`/`regex`, `json-schema`, `tool-called`/`tool-not-called`, `hitl-requested`. `environment.mode`: `recorded` (replayed tool results, deterministic), `sandbox`, or `live`.
2. **Requirements from security**: skills with external effects or classification >= moderate need `safety`/`adversarial` cases; HITL skills need a `hitl-requested` case.
3. **Report** (`provenance/eval-reports/<version>.yaml`, schema `eval-report.schema.json`): produced by a harness run; names runner/model/agent class, binds to `skill_digest` **and** `suite_digest`, and gives pass rate (+ optional cost/latency).
4. **Gate**: approval (`basis: eval-report`) requires the report to match the exact digest and suite and `pass_rate >= manifest.evaluation.min_pass_rate`. A **waiver** is permitted only as a time-boxed exception (`expires_on_version`), is surfaced as a CI warning and as `evidence_level: unevaluated` in the index.
5. **Regression discipline**: failures reported by evidence should become new `regression` cases in the next version (a PATCH), so the fix is locked in.
6. **Cross-agent evaluation**: run the suite per compatible agent class; report one file per class if results differ.

The repository validates suite structure and report binding; it does not run agents.
