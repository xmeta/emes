#!/usr/bin/env python3
"""First EMES executable loop: semantic source -> STEP + MJCF -> evidence."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import math
import platform
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import xml.etree.ElementTree as ET

from build123d import Box, Compound, Cylinder, export_step
import mujoco
import numpy as np

from catalog import canonical_digest as catalog_digest
from catalog import property_number, resolve_catalog_parts
from evidence import envelope_fields, validate_evidence
from validate import canonical_digest, load_json, summarize_verification, validate_semantics

FIXED_STEP_TIMESTAMP = datetime(2000, 1, 1, tzinfo=timezone.utc)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def digest_json(value: Any) -> str:
    payload = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def index_by_id(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {item["id"]: item for item in items}


def quantity_value(item: dict[str, Any], expected_unit: str, context: str) -> float:
    if item["unit"] != expected_unit:
        raise ValueError(f"{context}: expected {expected_unit}, got {item['unit']}")
    return float(item["value"])


def parameter(document: dict[str, Any], parameter_id: str, unit: str) -> float:
    params = index_by_id(document["parameters"])
    if parameter_id not in params:
        raise ValueError(f"missing required reference parameter {parameter_id}")
    return quantity_value(params[parameter_id]["value"], unit, parameter_id)


def component_mass(document: dict[str, Any], component_id: str) -> float:
    components = index_by_id(document["components"])
    mass = components[component_id].get("mass")
    if mass is None:
        raise ValueError(f"{component_id}: reference adapter requires explicit mass")
    return quantity_value(mass, "kg", f"{component_id}.mass")


def connection(document: dict[str, Any], connection_id: str) -> dict[str, Any]:
    connections = index_by_id(document["connections"])
    try:
        return connections[connection_id]
    except KeyError as exc:
        raise ValueError(f"missing required connection {connection_id}") from exc


def reference_dimensions(document: dict[str, Any]) -> dict[str, float]:
    return {
        "l1": parameter(document, "P_L1", "m"),
        "l2": parameter(document, "P_L2", "m"),
        "width": parameter(document, "P_LINK_WIDTH", "m"),
        "thickness": parameter(document, "P_LINK_THICKNESS", "m"),
        "base_x": parameter(document, "P_BASE_X", "m"),
        "base_y": parameter(document, "P_BASE_Y", "m"),
        "base_z": parameter(document, "P_BASE_Z", "m"),
    }


def generate_step(
    document: dict[str, Any],
    selected_parts: dict[str, dict[str, Any]],
    path: Path,
) -> dict[str, Any]:
    dims = reference_dimensions(document)
    mm = 1000.0
    shapes = [
        Box(dims["base_x"] * mm, dims["base_y"] * mm, dims["base_z"] * mm),
        Box(dims["l1"] * mm, dims["width"] * mm, dims["thickness"] * mm).translate(
            (dims["l1"] * mm / 2.0, 0.0, dims["base_z"] * mm)
        ),
        Box(dims["l2"] * mm, dims["width"] * mm, dims["thickness"] * mm).translate(
            ((dims["l1"] + dims["l2"] / 2.0) * mm, 0.0, dims["base_z"] * mm)
        ),
    ]

    for index, component_id in enumerate(("motor_j1", "motor_j2")):
        part = selected_parts[component_id]
        diameter = property_number(part, "body_diameter", "m")
        length = property_number(part, "body_length", "m")
        motor = Cylinder(diameter * mm / 2.0, length * mm)
        motor = motor.translate(
            (
                -dims["base_x"] * mm / 2.0,
                (-0.06 if index == 0 else 0.06) * mm,
                dims["base_z"] * mm,
            )
        )
        shapes.append(motor)

    assembly = Compound(children=shapes)
    ok = export_step(assembly, path, timestamp=FIXED_STEP_TIMESTAMP)
    if not ok:
        raise RuntimeError("build123d export_step returned failure")

    return {
        "backend": "build123d",
        "kind": "step",
        "sha256": sha256_file(path),
        "solid_count": len(assembly.solids()),
        "shape_volume_mm3": sum(float(solid.volume) for solid in assembly.solids()),
    }


def fmt(value: float) -> str:
    return format(value, ".12g")


def payload_mass(document: dict[str, Any], load_case_id: str) -> float:
    cases = index_by_id(document["load_cases"])
    if load_case_id not in cases:
        raise ValueError(f"unknown load case {load_case_id}")
    total = 0.0
    for load in cases[load_case_id].get("loads", []):
        if load["kind"] == "mass" and load["target"] == "link2":
            total += quantity_value(load["value"], "kg", f"{load_case_id}.payload")
    return total


def generate_mjcf(
    document: dict[str, Any],
    path: Path,
    load_case_id: str,
) -> dict[str, Any]:
    dims = reference_dimensions(document)
    j1 = connection(document, "J1")
    j2 = connection(document, "J2")
    mass1 = component_mass(document, "link1")
    mass2 = component_mass(document, "link2")
    payload = payload_mass(document, load_case_id)

    root = ET.Element("mujoco", {"model": document["design"]["id"]})
    ET.SubElement(root, "compiler", {"angle": "radian", "autolimits": "true"})
    ET.SubElement(root, "option", {"timestep": "0.002", "gravity": "0 0 0"})
    world = ET.SubElement(root, "worldbody")
    ET.SubElement(
        world,
        "geom",
        {
            "name": "base",
            "type": "box",
            "size": (
                f'{fmt(dims["base_x"] / 2)} '
                f'{fmt(dims["base_y"] / 2)} '
                f'{fmt(dims["base_z"] / 2)}'
            ),
            "pos": f'{fmt(-dims["base_x"] / 2)} 0 0',
        },
    )

    body1 = ET.SubElement(world, "body", {"name": "link1", "pos": "0 0 0"})
    ET.SubElement(
        body1,
        "joint",
        {
            "name": "J1",
            "type": "hinge",
            "axis": " ".join(fmt(float(v)) for v in j1["axis"]),
            "limited": "true",
            "range": (
                f'{fmt(quantity_value(j1["lower"], "rad", "J1.lower"))} '
                f'{fmt(quantity_value(j1["upper"], "rad", "J1.upper"))}'
            ),
        },
    )
    ET.SubElement(
        body1,
        "geom",
        {
            "name": "link1_geom",
            "type": "box",
            "size": (
                f'{fmt(dims["l1"] / 2)} '
                f'{fmt(dims["width"] / 2)} '
                f'{fmt(dims["thickness"] / 2)}'
            ),
            "pos": f'{fmt(dims["l1"] / 2)} 0 0',
            "mass": fmt(mass1),
        },
    )

    body2 = ET.SubElement(
        body1, "body", {"name": "link2", "pos": f'{fmt(dims["l1"])} 0 0'}
    )
    ET.SubElement(
        body2,
        "joint",
        {
            "name": "J2",
            "type": "hinge",
            "axis": " ".join(fmt(float(v)) for v in j2["axis"]),
            "limited": "true",
            "range": (
                f'{fmt(quantity_value(j2["lower"], "rad", "J2.lower"))} '
                f'{fmt(quantity_value(j2["upper"], "rad", "J2.upper"))}'
            ),
        },
    )
    ET.SubElement(
        body2,
        "geom",
        {
            "name": "link2_geom",
            "type": "box",
            "size": (
                f'{fmt(dims["l2"] / 2)} '
                f'{fmt(dims["width"] / 2)} '
                f'{fmt(dims["thickness"] / 2)}'
            ),
            "pos": f'{fmt(dims["l2"] / 2)} 0 0',
            "mass": fmt(mass2),
        },
    )
    if payload > 0:
        ET.SubElement(
            body2,
            "geom",
            {
                "name": "payload",
                "type": "sphere",
                "size": "0.02",
                "pos": f'{fmt(dims["l2"])} 0 0',
                "mass": fmt(payload),
            },
        )
    ET.SubElement(
        body2,
        "site",
        {"name": "tip", "pos": f'{fmt(dims["l2"])} 0 0', "size": "0.005"},
    )

    ET.indent(root, space="  ")
    path.write_bytes(ET.tostring(root, encoding="utf-8", xml_declaration=True) + b"\n")
    return {
        "backend": "mujoco-mjcf",
        "kind": "mjcf",
        "sha256": sha256_file(path),
        "load_case": load_case_id,
    }


def run_mujoco_smoke(path: Path) -> dict[str, Any]:
    model = mujoco.MjModel.from_xml_path(str(path))
    data = mujoco.MjData(model)
    if model.nq != 2 or model.nv != 2:
        raise RuntimeError(f"expected 2-DOF reference model, got nq={model.nq} nv={model.nv}")

    data.qpos[:] = np.array([0.2, -0.3])
    data.qvel[:] = np.array([0.1, -0.05])
    mujoco.mj_forward(model, data)
    site_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_SITE, "tip")
    if site_id < 0:
        raise RuntimeError("generated MJCF has no tip site")
    tip_initial = [float(value) for value in data.site_xpos[site_id]]

    for _ in range(10):
        mujoco.mj_step(model, data)
    mujoco.mj_forward(model, data)
    tip_final = [float(value) for value in data.site_xpos[site_id]]

    values = list(data.qpos) + list(data.qvel) + tip_initial + tip_final
    if not all(math.isfinite(float(value)) for value in values):
        raise RuntimeError("non-finite MuJoCo state")

    return {
        "status": "pass",
        "nq": int(model.nq),
        "nv": int(model.nv),
        "steps": 10,
        "qpos_final": [float(value) for value in data.qpos],
        "qvel_final": [float(value) for value in data.qvel],
        "tip_initial_m": tip_initial,
        "tip_final_m": tip_final,
    }


def evaluate_constraints(
    document: dict[str, Any], metrics: dict[str, tuple[float, str]]
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    operators = {
        ">=": lambda value, target: value >= target,
        "<=": lambda value, target: value <= target,
        ">": lambda value, target: value > target,
        "<": lambda value, target: value < target,
        "==": lambda value, target: value == target,
    }
    for constraint_item in document["constraints"]:
        metric_id = constraint_item["metric"]
        if metric_id not in metrics:
            result.append({
                "id": constraint_item["id"],
                "status": "not_evaluated",
                "reason": "metric unavailable in reference loop",
            })
            continue
        value, unit = metrics[metric_id]
        target = quantity_value(constraint_item["target"], unit, f"{constraint_item['id']}.target")
        passed = operators[constraint_item["op"]](value, target)
        result.append({
            "id": constraint_item["id"],
            "status": "pass" if passed else "fail",
            "metric": metric_id,
            "value": value,
            "unit": unit,
            "op": constraint_item["op"],
            "target": target,
        })
    return result


def toolchain() -> dict[str, str]:
    return {
        "python": platform.python_version(),
        "build123d": importlib.metadata.version("build123d"),
        "mujoco": importlib.metadata.version("mujoco"),
    }


def selected_part_record(component_id: str, part: dict[str, Any]) -> dict[str, Any]:
    return {
        "component": component_id,
        "part_id": part["id"],
        "manufacturer": part["identity"]["manufacturer"],
        "part_number": part["identity"]["part_number"],
        "part_digest": catalog_digest(part),
        "mass_kg": property_number(part, "mass", "kg"),
        "continuous_torque_nm": property_number(part, "continuous_torque", "N*m"),
        "peak_torque_nm": property_number(part, "peak_torque", "N*m"),
        "no_load_speed_rad_s": property_number(part, "no_load_speed", "rad/s"),
        "body_diameter_m": property_number(part, "body_diameter", "m"),
        "body_length_m": property_number(part, "body_length", "m"),
    }


def run_bundle(
    source: Path,
    out_dir: Path,
    load_case: str,
    repo_root: Path,
    required_constraints: list[str] | None = None,
) -> dict[str, Any]:
    document = load_json(source)
    validate_semantics(document)
    selected_parts = resolve_catalog_parts(
        document,
        repo_root=repo_root,
        catalog_schema_path=repo_root / "spec/emes-catalog-v0.schema.json",
    )
    required_catalog_components = {"motor_j1", "motor_j2"}
    missing = required_catalog_components - selected_parts.keys()
    if missing:
        raise ValueError("reference loop requires catalog-backed motors: " + ", ".join(sorted(missing)))

    out_dir.mkdir(parents=True, exist_ok=True)
    design_digest = canonical_digest(document)
    step_path = out_dir / "arm.step"
    mjcf_path = out_dir / "arm.xml"
    step = generate_step(document, selected_parts, step_path)
    mjcf = generate_mjcf(document, mjcf_path, load_case)
    smoke = run_mujoco_smoke(mjcf_path)

    dims = reference_dimensions(document)
    metrics = {
        "M_REACH": (dims["l1"] + dims["l2"], "m"),
        "M_MOVING_MASS": (
            component_mass(document, "link1") + component_mass(document, "link2"),
            "kg",
        ),
    }
    toolchain_record = toolchain()
    toolchain_digest = digest_json(toolchain_record)
    constraint_results = evaluate_constraints(document, metrics)
    metric_records = [
        {
            "id": metric_id,
            "value": value,
            "unit": unit,
            "method": "analytic",
            "source_design_digest": design_digest,
        }
        for metric_id, (value, unit) in metrics.items()
    ]
    verification = summarize_verification(constraint_results, required_constraints)
    evidence = {
        **envelope_fields(
            producer_id="reference_loop",
            design_id=document["design"]["id"],
            design_digest=design_digest,
            inputs=[],
            metrics=metric_records,
            constraint_results=constraint_results,
            verification=verification,
            packages=("build123d", "mujoco"),
        ),
        "toolchain": toolchain_record,
        "toolchain_digest": toolchain_digest,
        "load_case": load_case,
        "selected_parts": [
            selected_part_record(component_id, selected_parts[component_id])
            for component_id in sorted(selected_parts)
        ],
        "artifacts": [
            {
                "id": "A_STEP",
                "path": "arm.step",
                "sha256": step["sha256"],
                "kind": "step",
                "source_design_digest": design_digest,
                "toolchain_digest": toolchain_digest,
            },
            {
                "id": "A_MJCF",
                "path": "arm.xml",
                "sha256": mjcf["sha256"],
                "kind": "mjcf",
                "source_design_digest": design_digest,
                "toolchain_digest": toolchain_digest,
            },
        ],
        "geometry": step,
        "dynamics_smoke": smoke,
    }
    validate_evidence(evidence, repo_root, expected_producer="reference_loop")
    (out_dir / "evidence.json").write_text(
        json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return evidence


def check_determinism(
    source: Path,
    out_dir: Path,
    load_case: str,
    repo_root: Path,
    first: dict[str, Any],
    required_constraints: list[str] | None = None,
) -> None:
    with tempfile.TemporaryDirectory(prefix="emes-determinism-") as temp_dir:
        second = run_bundle(
            source, Path(temp_dir), load_case, repo_root, required_constraints
        )
    first_hashes = {item["kind"]: item["sha256"] for item in first["artifacts"]}
    second_hashes = {item["kind"]: item["sha256"] for item in second["artifacts"]}
    if first_hashes != second_hashes:
        raise RuntimeError(
            f"non-deterministic generated artifacts: first={first_hashes} second={second_hashes}"
        )
    print("DETERMINISTIC step,mjcf")


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

    evidence = run_bundle(
        args.source, args.out_dir, args.load_case, args.repo_root, args.require_constraint
    )
    if args.check_determinism:
        check_determinism(
            args.source,
            args.out_dir,
            args.load_case,
            args.repo_root,
            evidence,
            args.require_constraint,
        )

    print(f"VALID executable-loop design={evidence['design_digest']}")
    for artifact in evidence["artifacts"]:
        print(f"{artifact['kind'].upper()} {artifact['sha256']}")
    print(f"EVIDENCE {args.out_dir / 'evidence.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
