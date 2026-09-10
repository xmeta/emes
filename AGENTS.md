# AGENTS.md

## Mission

Advance EMES toward an end-to-end executable mechanical-engineering specification system.

## Working method

Use GitHub Flow. Keep `main` usable.

Prioritize a first complete vertical slice over broad partial implementation:

1. semantic source,
2. validation,
3. one generated engineering artifact,
4. one external analysis,
5. deterministic evidence,
6. one AI/optimizer iteration.

Classify discoveries:

- BLOCKER: prevents the current end-to-end slice; fix now.
- IMPORTANT: valuable but non-blocking; record as an Issue.
- LATER: defer unless it becomes necessary.

## Architectural invariants

- CAD is derived; the semantic EMES source is authoritative.
- LLM output is a proposal, never verification evidence.
- Units, frames, IDs, and references must be explicit.
- Generated artifacts must be attributable to source and toolchain digests.
- Adapters do not mutate canonical IR.
- Catalog-backed purchased parts must bind to explicit snapshots and part digests; never let a live provider silently alter an existing design.
- Keep volatile sourcing facts such as price, stock, and lead time separate from pinned technical properties.
- AI-extracted catalog values are derived evidence until attributable source material supports them.
- A catalog rating and its stated operating conditions are one engineering fact; consumers must not silently treat a conditional rating as unconditional.
- A source-conditioned battery impedance value may be used only when the analysis context satisfies its stated conditions; do not infer OCV or loaded terminal voltage from impedance alone.
- Source-conditioned pulse-power points require exact supported analysis conditions unless an explicit interpolation model with evidence exists.
- Compare heterogeneous weakest-link ratings through dimensionless utilization/load-scale factors while preserving each native rating unit; do not invent a common power rating merely for comparison.
- Battery-pack current capability is limited by the weakest applicable cell, BMS, protection, interconnect, and converter constraint; never multiply cell capability while ignoring downstream limits.
- Passing analytic battery sizing is not fabrication, charging, or energization approval; physical battery work requires explicit safety evidence and human approval.
- Do not create a custom DSL until examples show a concrete need.
- Prefer existing engineering kernels and standards over reimplementation.
- Physical fabrication/actuation is outside automatic approval.

## Verification

Use the cheapest relevant check first. Before merging a Phase 1 slice, run the repository validation workflow and any adapter-specific smoke tests.
