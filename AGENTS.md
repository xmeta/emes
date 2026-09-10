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
- Do not create a custom DSL until examples show a concrete need.
- Prefer existing engineering kernels and standards over reimplementation.
- Physical fabrication/actuation is outside automatic approval.

## Verification

Use the cheapest relevant check first. Before merging a Phase 1 slice, run the repository validation workflow and any adapter-specific smoke tests.
