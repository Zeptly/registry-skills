# Composition

**Decision: supported, deliberately constrained.** Real procedures (competitive intelligence = research + source vetting + comparison) reuse sub-procedures, and improving one shared blueprint should improve every composite. Uncontrolled graphs would make behaviour irreproducible and privilege review impossible, so:

1. **Explicit, versioned edges.** `references: [{registry: skills, id, version: <range>, digest: null}]` plus `spec.composition: [{id, role, optional}]` (a validated 1:1 pairing). Only `registry: skills` references participate in composition; other registries' references are validated structurally and never fetched. Ranges resolve against *released* versions in the ledger; the resolution (exact versions + digests) is what a run records (`zskill resolve`).
2. **Acyclic.** Cycles are validation errors.
3. **Bounded.** Depth <= 3, direct deps <= 8, transitive closure <= 20.
4. **Canonical depends on canonical.** A canonical skill may not depend on a candidate or a revoked skill; deprecated dependencies warn (lifecycle overlay).
5. **Visible.** `composes` dependencies must be named as `` `skills:<id>` `` in the composite's `SKILL.md` procedure, so a plain-text runtime that ignores the manifest still sees the composition.
6. **No privilege hiding.** The composite's declared security envelope MUST cover each child's (classification, side effects, HITL, sensitivity, permissions, capabilities, egress, agent classes). Widening a child's privilege therefore forces a review of, and version bump for, its dependents.
7. **Roles.** `composes` = the parent invokes the child as a sub-procedure. `prerequisite` = the child must have been run/satisfied first. Optional dependencies are skipped when unavailable and do not constrain agent classes/capabilities.
8. **Semver ripple.** A dependency's MAJOR bump is outside `^` ranges, so parents keep resolving the old version until deliberately upgraded. A MINOR/PATCH improvement flows to every dependent automatically, which is the desired cross-agent learning effect; evidence records the resolved closure so regressions can be attributed to the child.

Not supported in v1: dependency on non-skill artefacts, conditional/dynamic dependencies, per-dependency parameter binding (procedure text does that), diamond version conflicts (highest satisfying version wins; single version per ID).
