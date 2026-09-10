#!/usr/bin/env python3
"""Regression checks for the shared EMES evidence envelope."""

from __future__ import annotations

import copy
from pathlib import Path

from evidence import (
    digest_json,
    envelope_fields,
    input_record,
    no_design_decision,
    require_input_digest,
    validate_evidence,
)


def expect_rejected(fn) -> None:
    try:
        fn()
    except Exception:
        return
    raise AssertionError("malformed evidence unexpectedly accepted")


def main() -> int:
    root = Path(".")
    upstream = {"kind": "fixture", "value": 1}
    upstream_digest = digest_json(upstream)
    evidence = envelope_fields(
        producer_id="fixture",
        design_id="D_TEST",
        design_digest=digest_json({"design": "test"}),
        inputs=[input_record("upstream", upstream_digest)],
        metrics=[],
        constraint_results=[],
        verification=no_design_decision(),
    )
    validate_evidence(evidence, root, expected_producer="fixture")
    require_input_digest(evidence, "upstream", upstream_digest)

    bad_runtime = copy.deepcopy(evidence)
    bad_runtime["runtime_digest"] = "sha256:" + "0" * 64
    expect_rejected(lambda: validate_evidence(bad_runtime, root))

    expect_rejected(
        lambda: validate_evidence(evidence, root, expected_producer="other")
    )

    duplicate_input = copy.deepcopy(evidence)
    duplicate_input["inputs"].append(copy.deepcopy(duplicate_input["inputs"][0]))
    expect_rejected(lambda: validate_evidence(duplicate_input, root))

    expect_rejected(
        lambda: require_input_digest(evidence, "upstream", "sha256:" + "f" * 64)
    )

    print("PASS shared evidence envelope")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
