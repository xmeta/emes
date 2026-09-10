#!/usr/bin/env python3
"""One narrow, auditable EMES design iteration for the reference power pack."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import tempfile
from pathlib import Path
from typing import Any

import power
from validate import load_json

MUTABLE_PARAMETER = "P_PACK_PARALLEL"
OBJECTIVE_ID = "O_PACK_MASS"


def digest_json(value: Any) -> str:
    payload = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def by_id(items: list[dict[str, Any]], item_id: str) -> dict[str, Any]:
    try:
        return next(item for item in items if item["id"] == item_id)
    except StopIteration as exc:
        raise ValueError(f"missing {item_id}") from exc


def propose(document: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    candidate = copy.deepcopy(document)
    parameter = by_id(candidate["parameters"], MUTABLE_PARAMETER)
    if parameter.get("role") != "design_variable":
        raise ValueError(f"{MUTABLE_PARAMETER} is not a design variable")

    current = float(parameter["value"]["value"])
    lower = float(parameter["lower"]["value"])
    proposed = current - 1.0
    if proposed < lower:
        raise ValueError(f"cannot decrement {MUTABLE_PARAMETER} below its lower bound")

    parameter["value"]["value"] = proposed
    patch = {
        "op": "replace_parameter_value",
        "parameter_id": MUTABLE_PARAMETER,
        "from": copy.deepcopy(by_id(document["parameters"], MUTABLE_PARAMETER)["value"]),
        "to": copy.deepcopy(parameter["value"]),
    }
    return candidate, patch


def metric(evidence: dict[str, Any], metric_id: str) -> dict[str, Any]:
    return by_id(evidence["metrics"], metric_id)


def run_power(source: Path, out: Path, repo_root: Path) -> dict[str, Any]:
    try:
        return power.run(source, out, repo_root)
    except RuntimeError:
        if not out.exists():
            raise
        result = load_json(out)
        if result.get("verification", {}).get("design_decision") != "rejected":
            raise
        return result


def compare(
    baseline: dict[str, Any], candidate: dict[str, Any], objective: dict[str, Any]
) -> tuple[str, str, dict[str, Any]]:
    baseline_metric = metric(baseline, objective["metric"])
    candidate_metric = metric(candidate, objective["metric"])
    if baseline_metric["unit"] != candidate_metric["unit"]:
        raise ValueError("objective metric unit changed between baseline and candidate")

    direction = objective["direction"]
    before = float(baseline_metric["value"])
    after = float(candidate_metric["value"])
    if direction == "minimize":
        improved = after < before
    elif direction == "maximize":
        improved = after > before
    else:
        raise ValueError(f"unsupported objective direction {direction!r}")

    verification = candidate["verification"]
    if verification["design_decision"] != "accepted":
        failed = verification["failed_constraints"]
        detail = ", ".join(failed) if failed else verification["design_decision"]
        decision = "reject"
        reason = f"candidate engineering verification is not accepted: {detail}"
    elif improved:
        decision = "keep"
        reason = "candidate is accepted and improves the objective"
    else:
        decision = "reject"
        reason = "candidate is accepted but does not improve the objective"

    return decision, reason, {
        "id": objective["id"],
        "metric": objective["metric"],
        "direction": direction,
        "unit": baseline_metric["unit"],
        "baseline": before,
        "candidate": after,
        "improved": improved,
    }


def run_iteration(source: Path, out_dir: Path, repo_root: Path) -> dict[str, Any]:
    document = load_json(source)
    objective = by_id(document["objectives"], OBJECTIVE_ID)
    candidate, patch = propose(document)

    out_dir.mkdir(parents=True, exist_ok=True)
    candidate_source = out_dir / "candidate.mechanism.json"
    write_json(candidate_source, candidate)
    write_json(out_dir / "candidate.patch.json", patch)

    baseline_evidence = run_power(source, out_dir / "baseline.evidence.json", repo_root)
    candidate_evidence = run_power(
        candidate_source, out_dir / "candidate.evidence.json", repo_root
    )

    if baseline_evidence["verification"]["design_decision"] != "accepted":
        raise RuntimeError("baseline design is not accepted under the active verification policy")

    decision, reason, objective_result = compare(
        baseline_evidence, candidate_evidence, objective
    )
    result = {
        "emes_iteration_version": "0.1",
        "proposer": {
            "kind": "deterministic",
            "policy": f"decrement {MUTABLE_PARAMETER} by 1 within its declared bound",
        },
        "proposal": patch,
        "objective": objective_result,
        "baseline": {
            "design_digest": baseline_evidence["design_digest"],
            "evidence_digest": digest_json(baseline_evidence),
            "verification": baseline_evidence["verification"],
        },
        "candidate": {
            "design_digest": candidate_evidence["design_digest"],
            "evidence_digest": digest_json(candidate_evidence),
            "verification": candidate_evidence["verification"],
        },
        "decision": decision,
        "reason": reason,
    }
    write_json(out_dir / "iteration.json", result)
    return result


def check_determinism(
    source: Path, out_dir: Path, repo_root: Path, first: dict[str, Any]
) -> None:
    with tempfile.TemporaryDirectory(prefix="emes-iteration-") as temp_dir:
        second = run_iteration(source, Path(temp_dir), repo_root)
    if first != second:
        raise RuntimeError(f"non-deterministic design iteration: first={first} second={second}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "source", nargs="?", type=Path, default=Path("examples/power-pack/mechanism.json")
    )
    parser.add_argument(
        "--out-dir", type=Path, default=Path("generated/power-pack-iteration")
    )
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument("--check-determinism", action="store_true")
    args = parser.parse_args()

    result = run_iteration(args.source, args.out_dir, args.repo_root)
    if args.check_determinism:
        check_determinism(args.source, args.out_dir, args.repo_root, result)
        print("DETERMINISTIC design-iteration")
    print(
        f"ITERATION {result['decision']} "
        f"baseline={result['baseline']['design_digest']} "
        f"candidate={result['candidate']['design_digest']}"
    )
    print(f"REASON {result['reason']}")
    print(f"EVIDENCE {args.out_dir / 'iteration.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
