---
name: structured-comparison
description: Compare two or more entities (products, vendors, options) across explicit criteria and produce a normalised comparison matrix with a justified recommendation. Use when a decision needs like-for-like comparison.
---

# Structured Comparison

## When to use

- You have facts about several entities and must compare them for a decision.
- Do not use to gather the facts; that is research. This skill works on supplied material.

## Procedure

1. **Fix the criteria first.** Derive 5-10 criteria from the decision context (or the user's list). For each define what "better" means and whether it is measurable. Assign weights only if the user supplied priorities; otherwise weight equally and say so.
2. **Normalise.** Put every entity through every criterion using the same units and time basis. Mark cells `unknown` where the facts are missing; never impute.
3. **Attach evidence.** Each cell carries a short source reference from the supplied material.
4. **Compare.** Identify where entities are tied, where one dominates, and where the trade-offs are genuine.
5. **Recommend conditionally.** State which entity fits under which priorities ("if X matters most choose A"), plus the single most decision-relevant unknown.

## Output

Markdown containing: criteria definitions, the matrix (entities as columns, criteria as rows, evidence in cells), a trade-off summary, and a conditional recommendation. Optionally a JSON version of the matrix when `cap.llm.structured` is available.

## Guardrails

- Never fill an unknown cell with a plausible guess.
- Keep criteria independent of the entities being compared (no criteria invented to favour a preferred option).
- Do not present a weighted score as objective if the weights were assumed.
