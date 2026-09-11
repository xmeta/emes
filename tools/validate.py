#!/usr/bin/env python3
"""Validate EMES v0 examples structurally and semantically."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import jsonschema


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


# Current canonical engineering units. Spatial/mechanical quantities use SI;
# domain-native units already used by executable adapters remain explicit.
_UNIT_NORMALIZATION: dict[str, tuple[str, str, str]] = {
    "1": ("dimensionless", "1", "1"),
    "%": ("percent", "%", "1"),
    "m": ("length", "m", "1"),
    "cm": ("length", "m", "0.01"),
    "mm": ("length", "m", "0.001"),
    "kg": ("mass", "kg", "1"),
    "g": ("mass", "kg", "0.001"),
    "V": ("voltage", "V", "1"),
    "mV": ("voltage", "V", "0.001"),
    "A": ("current", "A", "1"),
    "mA": ("current", "A", "0.001"),
    "W": ("power", "W", "1"),
    "kW": ("power", "W", "1000"),
    "W*h": ("energy", "W*h", "1"),
    "kW*h": ("energy", "W*h", "1000"),
    "A*h": ("capacity", "A*h", "1"),
    "mA*h": ("capacity", "A*h", "0.001"),
    "F": ("capacitance", "F", "1"),
    "uF": ("capacitance", "F", "0.000001"),
    "rad": ("angle", "rad", "1"),
    "s": ("time", "s", "1"),
    "ms": ("time", "s", "0.001"),
    "degC": ("temperature", "degC", "1"),
    "MPa": ("stress", "MPa", "1"),
    "kPa": ("stress", "MPa", "0.001"),
    "Pa": ("stress", "MPa", "0.000001"),
    "ohm": ("resistance", "ohm", "1"),
}


def normalize_quantity(item: dict[str, Any]) -> dict[str, Any]:
    unit = item["unit"]
    if unit not in _UNIT_NORMALIZATION:
        raise ValueError(f"unsupported quantity unit {unit!r}")
    _, canonical_unit, factor = _UNIT_NORMALIZATION[unit]
    normalized = copy.deepcopy(item)
    normalized["value"] = float(Decimal(str(item["value"])) * Decimal(factor))
    normalized["unit"] = canonical_unit
    return normalized


def normalize_document(document: dict[str, Any]) -> dict[str, Any]:
    def walk(value: Any) -> Any:
        if isinstance(value, dict):
            if (
                "value" in value
                and "unit" in value
                and isinstance(value["value"], (int, float))
                and isinstance(value["unit"], str)
            ):
                return {key: walk(item) for key, item in normalize_quantity(value).items()}
            return {key: walk(item) for key, item in value.items()}
        if isinstance(value, list):
            return [walk(item) for item in value]
        return copy.deepcopy(value)

    return walk(document)


def canonical_digest(document: dict[str, Any]) -> str:
    payload = json.dumps(
        normalize_document(document),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def collect_ids(document: dict[str, Any]) -> dict[str, str]:
    groups = {
        "requirements": document.get("requirements", []),
        "design_intent": document.get("design_intent", []),
        "parameters": document.get("parameters", []),
        "components": document.get("components", []),
        "connections": document.get("connections", []),
        "functional_edges": document.get("functional_edges", []),
        "load_cases": document.get("load_cases", []),
        "constraints": document.get("constraints", []),
        "objectives": document.get("objectives", []),
        "analyses": document.get("analyses", []),
    }
    ids: dict[str, str] = {document["design"]["id"]: "design"}
    for group_name, items in groups.items():
        for item in items:
            item_id = item["id"]
            if item_id in ids:
                raise ValueError(
                    f"duplicate id {item_id!r}: {group_name} conflicts with {ids[item_id]}"
                )
            ids[item_id] = group_name

    for analysis in document.get("analyses", []):
        for metric in analysis.get("produces", []):
            metric_id = metric["id"]
            if metric_id in ids:
                raise ValueError(
                    f"duplicate id {metric_id!r}: metric conflicts with {ids[metric_id]}"
                )
            ids[metric_id] = "metric"
    return ids


def require_ref(ref: str, ids: dict[str, str], context: str) -> None:
    if ref not in ids:
        raise ValueError(f"{context}: unresolved reference {ref!r}")


def validate_semantics(document: dict[str, Any]) -> None:
    normalized = normalize_document(document)
    document.clear()
    document.update(normalized)
    ids = collect_ids(document)

    parameter_ids = {item["id"] for item in document.get("parameters", [])}
    component_ids = {item["id"] for item in document.get("components", [])}
    load_case_ids = {item["id"] for item in document.get("load_cases", [])}
    constraint_ids = {item["id"] for item in document.get("constraints", [])}
    requirement_ids = {item["id"] for item in document.get("requirements", [])}
    metric_ids = {
        metric["id"]
        for analysis in document.get("analyses", [])
        for metric in analysis.get("produces", [])
    }

    for parameter in document.get("parameters", []):
        value = parameter["value"]
        lower = parameter.get("lower")
        upper = parameter.get("upper")
        if lower and lower["unit"] != value["unit"]:
            raise ValueError(f"{parameter['id']}: lower bound unit differs from value unit")
        if upper and upper["unit"] != value["unit"]:
            raise ValueError(f"{parameter['id']}: upper bound unit differs from value unit")
        if lower and value["value"] < lower["value"]:
            raise ValueError(f"{parameter['id']}: value below lower bound")
        if upper and value["value"] > upper["value"]:
            raise ValueError(f"{parameter['id']}: value above upper bound")
        if lower and upper and lower["value"] > upper["value"]:
            raise ValueError(f"{parameter['id']}: lower bound exceeds upper bound")

    for intent in document.get("design_intent", []):
        for ref in intent.get("protects", []):
            if ref not in requirement_ids:
                raise ValueError(f"{intent['id']}: protects unknown requirement {ref!r}")

    for component in document.get("components", []):
        for ref in component.get("parameters", []):
            if ref not in parameter_ids:
                raise ValueError(f"{component['id']}: unknown parameter {ref!r}")
        geometry = component.get("geometry")
        if isinstance(geometry, dict) and "frame" in geometry:
            if geometry["frame"] != component["id"]:
                raise ValueError(
                    f"{component['id']}: geometry frame must be the component-local frame"
                )

    for connection in document.get("connections", []):
        for side in ("a", "b"):
            ref = connection[side]["component"]
            if ref not in component_ids:
                raise ValueError(f"{connection['id']}: endpoint {side} references {ref!r}")
        if "axis" in connection:
            frame = connection.get("frame")
            if frame is None:
                raise ValueError(f"{connection['id']}: axis requires an explicit frame")
            child = connection["b"]["component"]
            if frame != child:
                raise ValueError(
                    f"{connection['id']}: axis frame must be child component {child!r}"
                )

    for edge in document.get("functional_edges", []):
        require_ref(edge["from"], ids, edge["id"])
        require_ref(edge["to"], ids, edge["id"])

    for load_case in document.get("load_cases", []):
        for load in load_case.get("loads", []):
            if load["target"] not in component_ids:
                raise ValueError(
                    f"{load_case['id']}: load target {load['target']!r} is not a component"
                )
            if "direction" in load:
                frame = load.get("frame")
                if frame is None:
                    raise ValueError(
                        f"{load_case['id']}: directional load requires an explicit frame"
                    )
                if frame != load["target"]:
                    raise ValueError(
                        f"{load_case['id']}: load frame must be target component {load['target']!r}"
                    )
            elif "frame" in load and load["frame"] != load["target"]:
                raise ValueError(
                    f"{load_case['id']}: load frame must be target component {load['target']!r}"
                )

    for constraint in document.get("constraints", []):
        if constraint["metric"] not in metric_ids:
            raise ValueError(
                f"{constraint['id']}: unknown metric {constraint['metric']!r}"
            )
        if "load_case" in constraint and constraint["load_case"] not in load_case_ids:
            raise ValueError(
                f"{constraint['id']}: unknown load case {constraint['load_case']!r}"
            )

    for objective in document.get("objectives", []):
        if objective["metric"] not in metric_ids:
            raise ValueError(f"{objective['id']}: unknown metric {objective['metric']!r}")

    for analysis in document.get("analyses", []):
        for ref in analysis.get("load_cases", []):
            if ref not in load_case_ids:
                raise ValueError(f"{analysis['id']}: unknown load case {ref!r}")

    traced_requirements: set[str] = set()
    for trace in document.get("traceability", []):
        requirement = trace["requirement"]
        if requirement not in requirement_ids:
            raise ValueError(f"traceability: unknown requirement {requirement!r}")
        traced_requirements.add(requirement)
        for verifier in trace["verified_by"]:
            if verifier not in constraint_ids:
                raise ValueError(
                    f"traceability {requirement}: verifier {verifier!r} is not a constraint"
                )

    missing = requirement_ids - traced_requirements
    if missing:
        raise ValueError(
            "requirements without executable traceability: " + ", ".join(sorted(missing))
        )


def summarize_verification(
    constraint_results: list[dict[str, Any]],
    required_constraint_ids: list[str] | None = None,
    *,
    execution_status: str = "succeeded",
) -> dict[str, Any]:
    if execution_status not in {"succeeded", "failed"}:
        raise ValueError(f"unsupported execution status {execution_status!r}")

    by_id = {item["id"]: item for item in constraint_results}
    if len(by_id) != len(constraint_results):
        raise ValueError("duplicate constraint result id")

    required = list(by_id) if required_constraint_ids is None else required_constraint_ids
    if len(set(required)) != len(required):
        raise ValueError("duplicate required constraint id")

    statuses = {
        constraint_id: by_id.get(constraint_id, {}).get("status", "missing")
        for constraint_id in required
    }
    failed = sorted(
        constraint_id for constraint_id, status in statuses.items() if status == "fail"
    )
    incomplete = sorted(
        constraint_id
        for constraint_id, status in statuses.items()
        if status not in {"pass", "fail"}
    )

    if execution_status == "failed":
        verification_status = "incomplete"
        decision = "not_decidable"
    else:
        verification_status = "incomplete" if incomplete else "complete"
        if failed:
            decision = "rejected"
        elif incomplete:
            decision = "not_decidable"
        else:
            decision = "accepted"

    return {
        "execution_status": execution_status,
        "verification_status": verification_status,
        "design_decision": decision,
        "required_constraints": required,
        "failed_constraints": failed,
        "incomplete_constraints": incomplete,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "document",
        nargs="?",
        default="examples/two-link-arm/mechanism.json",
        type=Path,
    )
    parser.add_argument(
        "--schema",
        default=Path("spec/emes-ir-v0.schema.json"),
        type=Path,
    )
    args = parser.parse_args()

    schema = load_json(args.schema)
    document = load_json(args.document)

    validator_cls = jsonschema.validators.validator_for(schema)
    validator_cls.check_schema(schema)
    validator_cls(schema).validate(document)
    validate_semantics(document)

    print(f"VALID {args.document}")
    print(f"design_digest={canonical_digest(document)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
