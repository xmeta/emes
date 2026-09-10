#!/usr/bin/env python3
"""Regression checks for EMES execution/verification/acceptance semantics."""

from __future__ import annotations

from validate import summarize_verification


def expect(
    results: list[dict[str, str]],
    decision: str,
    verification: str,
    required: list[str] | None = None,
) -> None:
    summary = summarize_verification(results, required)
    assert summary["design_decision"] == decision, summary
    assert summary["verification_status"] == verification, summary


def main() -> int:
    expect([{"id": "C1", "status": "pass"}], "accepted", "complete")
    expect([{"id": "C1", "status": "fail"}], "rejected", "complete")
    expect(
        [{"id": "C1", "status": "not_evaluated"}],
        "not_decidable",
        "incomplete",
    )
    expect(
        [{"id": "C1", "status": "pass"}],
        "not_decidable",
        "incomplete",
        ["C1", "C2"],
    )
    failed = summarize_verification(
        [{"id": "C1", "status": "pass"}], execution_status="failed"
    )
    assert failed["design_decision"] == "not_decidable", failed
    assert failed["verification_status"] == "incomplete", failed
    print("PASS acceptance semantics")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
