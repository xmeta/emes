#!/usr/bin/env python3
"""Regression check for the first complete EMES design iteration."""

from __future__ import annotations

import tempfile
from pathlib import Path

import power_pack_iteration as iteration
from validate import load_json


def main() -> int:
    repo_root = Path(".")
    source = Path("examples/power-pack/mechanism.json")
    with tempfile.TemporaryDirectory(prefix="emes-design-iteration-") as temp_dir:
        out_dir = Path(temp_dir)
        result = iteration.run_iteration(source, out_dir, repo_root)
        iteration.check_determinism(source, out_dir, repo_root, result)

        assert result["decision"] == "reject"
        assert result["objective"]["improved"] is True
        assert result["baseline"]["verification"]["design_decision"] == "accepted"
        candidate_verification = result["candidate"]["verification"]
        assert candidate_verification["design_decision"] == "rejected"
        assert candidate_verification["failed_constraints"] == [
            "C_CURRENT_MARGIN",
            "C_ENERGY",
        ]

        baseline = load_json(source)
        candidate = load_json(out_dir / "candidate.mechanism.json")
        candidate_parameter = iteration.by_id(candidate["parameters"], iteration.MUTABLE_PARAMETER)
        baseline_parameter = iteration.by_id(baseline["parameters"], iteration.MUTABLE_PARAMETER)
        candidate_parameter["value"] = baseline_parameter["value"]
        assert candidate == baseline

        assert (out_dir / "candidate.patch.json").exists()
        assert (out_dir / "baseline.evidence.json").exists()
        assert (out_dir / "candidate.evidence.json").exists()
        assert (out_dir / "iteration.json").exists()

    print("PASS first design iteration")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
