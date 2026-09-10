#!/usr/bin/env python3
"""Stable executable entrypoint for the first EMES engineering loop.

STEP is retained as an interoperability artifact. Exact OpenCascade BREP plus
MJCF are used as the reproducibility gate because STEP entity serialization is
not guaranteed to be byte-stable even when the represented geometry is equal.
"""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path
from typing import Any

from build123d import export_brep, import_step

import reference_loop as impl


def add_exact_brep(evidence: dict[str, Any], out_dir: Path) -> dict[str, Any]:
    step_path = out_dir / "arm.step"
    brep_path = out_dir / "arm.brep"
    shape = import_step(step_path)
    if not export_brep(shape, brep_path):
        raise RuntimeError("build123d export_brep returned failure")

    brep_digest = impl.sha256_file(brep_path)
    evidence["geometry"]["exact_brep_sha256"] = brep_digest
    evidence["geometry"]["determinism_basis"] = "exact_brep"
    evidence["artifacts"].append(
        {
            "id": "A_BREP",
            "path": "arm.brep",
            "sha256": brep_digest,
            "kind": "brep",
            "source_design_digest": evidence["design_digest"],
            "toolchain_digest": evidence["toolchain_digest"],
        }
    )
    (out_dir / "evidence.json").write_text(
        json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return evidence


def run_once(
    source: Path,
    out_dir: Path,
    load_case: str,
    repo_root: Path,
    required_constraints: list[str] | None = None,
) -> dict[str, Any]:
    return add_exact_brep(
        impl.run_bundle(source, out_dir, load_case, repo_root, required_constraints),
        out_dir,
    )


def artifact_hash(evidence: dict[str, Any], kind: str) -> str:
    return next(item["sha256"] for item in evidence["artifacts"] if item["kind"] == kind)


def stable_projection(evidence: dict[str, Any]) -> dict[str, Any]:
    """Fields required to be repeatable inside the pinned toolchain."""
    return {
        "design_digest": evidence["design_digest"],
        "catalog_parts": [item["part_digest"] for item in evidence["selected_parts"]],
        "brep": artifact_hash(evidence, "brep"),
        "mjcf": artifact_hash(evidence, "mjcf"),
        "metrics": evidence["metrics"],
        "constraints": evidence["constraint_results"],
        "verification": evidence["verification"],
        "dynamics_smoke": evidence["dynamics_smoke"],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "source", nargs="?", type=Path, default=Path("examples/two-link-arm/mechanism.json")
    )
    parser.add_argument("--out-dir", type=Path, default=Path("generated/two-link-arm"))
    parser.add_argument("--load-case", default="LC_FULL")
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument("--check-determinism", action="store_true")
    parser.add_argument("--require-constraint", action="append", default=None)
    args = parser.parse_args()

    first = run_once(
        args.source, args.out_dir, args.load_case, args.repo_root, args.require_constraint
    )
    if args.check_determinism:
        with tempfile.TemporaryDirectory(prefix="emes-determinism-") as temp_dir:
            second = run_once(
                args.source,
                Path(temp_dir),
                args.load_case,
                args.repo_root,
                args.require_constraint,
            )
        if stable_projection(first) != stable_projection(second):
            raise RuntimeError(
                "non-deterministic exact geometry/dynamics projection: "
                f"first={stable_projection(first)} second={stable_projection(second)}"
            )
        print("DETERMINISTIC brep,mjcf,metrics,dynamics")

    print(f"VALID executable-loop design={first['design_digest']}")
    print(f"BREP {artifact_hash(first, 'brep')}")
    print(f"STEP {artifact_hash(first, 'step')} (interchange bytes; not determinism key)")
    print(f"MJCF {artifact_hash(first, 'mjcf')}")
    print(f"EVIDENCE {args.out_dir / 'evidence.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
