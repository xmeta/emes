#!/usr/bin/env python3
"""Evaluate source-conditioned first-order battery resistive voltage drop."""

from __future__ import annotations

import argparse
import json
import math
import tempfile
from pathlib import Path
from typing import Any

import jsonschema

from catalog import canonical_digest as catalog_digest
from catalog import resolve_catalog_parts
from evidence import envelope_fields, input_record, no_design_decision, validate_evidence
from power import OPERATORS, evaluate_constraints, index_by_id, quantity_value
from validate import canonical_digest, load_json, summarize_verification, validate_semantics


def evidence_metric(
    power_evidence: dict[str, Any], metric_id: str, unit: str
) -> float:
    matches = [item for item in power_evidence.get("metrics", []) if item.get("id") == metric_id]
    if len(matches) != 1:
        raise ValueError(f"power evidence must provide exactly one {metric_id}")
    item = matches[0]
    if item.get("unit") != unit:
        raise ValueError(f"{metric_id}: expected unit {unit}, got {item.get('unit')}")
    value = float(item["value"])
    if not math.isfinite(value):
        raise ValueError(f"{metric_id}: non-finite value")
    return value


def parameter_value(
    document: dict[str, Any], parameter_id: str, unit: str
) -> float:
    parameters = index_by_id(document["parameters"])
    if parameter_id not in parameters:
        raise ValueError(f"unknown analysis parameter {parameter_id}")
    return quantity_value(parameters[parameter_id]["value"], unit, parameter_id)


def conditioned_context_property(
    document: dict[str, Any],
    part: dict[str, Any],
    property_name: str,
    unit: str,
    condition_bindings: dict[str, str],
) -> tuple[float, list[dict[str, Any]]]:
    try:
        item = part["properties"][property_name]
    except KeyError as exc:
        raise ValueError(f"{part['id']}: missing property {property_name}") from exc
    value = quantity_value(item, unit, f"{part['id']}.{property_name}")

    results: list[dict[str, Any]] = []
    for condition in item.get("conditions", []):
        condition_name = condition["parameter"]
        if condition_name not in condition_bindings:
            raise ValueError(
                f"{part['id']}.{property_name}: no design binding for condition {condition_name!r}"
            )
        condition_unit = condition["unit"]
        parameter_id = condition_bindings[condition_name]
        actual = parameter_value(document, parameter_id, condition_unit)
        target = float(condition["value"])
        if not math.isfinite(target):
            raise ValueError(
                f"{part['id']}.{property_name}.{condition_name}: non-finite target"
            )
        op = condition["op"]
        passed = OPERATORS[op](actual, target)
        result = {
            "part": part["id"],
            "property": property_name,
            "property_value": value,
            "property_unit": unit,
            "condition_parameter": condition_name,
            "condition_op": op,
            "condition_value": target,
            "condition_unit": condition_unit,
            "design_parameter": parameter_id,
            "design_value": actual,
            "status": "pass" if passed else "fail",
        }
        if not passed:
            raise ValueError(
                f"{part['id']}.{property_name}: condition failed: "
                f"{condition_name}={actual} {condition_unit} {op} {target} {condition_unit}"
            )
        results.append(result)
    return value, results


def evaluate(
    document: dict[str, Any],
    power_evidence: dict[str, Any],
    repo_root: Path,
) -> dict[str, Any]:
    design_digest = canonical_digest(document)
    validate_evidence(power_evidence, repo_root, expected_producer="power")
    if power_evidence.get("design_digest") != design_digest:
        raise ValueError("power evidence design digest does not match mechanism")

    extension = document.get("extensions", {}).get("org.emes.battery_sag")
    if not isinstance(extension, dict):
        raise ValueError("missing extensions.org.emes.battery_sag")
    sag_schema = load_json(repo_root / "spec/emes-battery-sag-v0.schema.json")
    validator_cls = jsonschema.validators.validator_for(sag_schema)
    validator_cls.check_schema(sag_schema)
    validator_cls(sag_schema).validate(extension)

    topology = power_evidence.get("power_topology", {})
    if topology.get("source_kind") != "battery_pack":
        raise ValueError("battery sag requires battery-pack power evidence")
    if topology.get("pack_id") != extension["pack_id"]:
        raise ValueError("battery sag pack_id does not match power evidence")

    series = int(topology["series"])
    parallel = int(topology["parallel"])
    if series < 1 or parallel < 1:
        raise ValueError("invalid series/parallel counts in power evidence")
    cell_component = topology["cell_component"]

    selected = resolve_catalog_parts(
        document,
        repo_root=repo_root,
        catalog_schema_path=repo_root / "spec/emes-catalog-v0.schema.json",
    )
    if cell_component not in selected:
        raise ValueError(f"{cell_component}: battery cell must be catalog-backed")
    cell = selected[cell_component]
    if cell.get("kind") != "battery_cell":
        raise ValueError(f"{cell_component}: expected battery_cell catalog part")

    cell_resistance, condition_results = conditioned_context_property(
        document,
        cell,
        extension["impedance_property"],
        "ohm",
        extension["condition_bindings"],
    )
    pack_current = evidence_metric(power_evidence, "M_LOAD_EQUIV_PACK_CURRENT", "A")
    pack_nominal_voltage = evidence_metric(power_evidence, "M_PACK_NOMINAL_VOLTAGE", "V")

    cell_current = pack_current / parallel
    pack_resistance = cell_resistance * series / parallel
    resistive_drop = pack_current * pack_resistance
    sag_fraction = resistive_drop / pack_nominal_voltage

    metrics: dict[str, tuple[float, str]] = {
        "M_SAG_REFERENCE_PACK_CURRENT": (pack_current, "A"),
        "M_SAG_REFERENCE_CELL_CURRENT": (cell_current, "A"),
        "M_CELL_DC_IMPEDANCE_TYPICAL": (cell_resistance, "ohm"),
        "M_PACK_REFERENCE_DC_RESISTANCE": (pack_resistance, "ohm"),
        "M_PACK_REFERENCE_RESISTIVE_DROP": (resistive_drop, "V"),
        "M_PACK_REFERENCE_SAG_FRACTION": (sag_fraction, "1"),
    }
    local_document = dict(document)
    local_document["constraints"] = [
        item for item in document["constraints"] if item["metric"] in metrics
    ]
    constraint_results = evaluate_constraints(local_document, metrics)

    metric_records = [
        {"id": metric_id, "value": value, "unit": unit, "method": "analytic"}
        for metric_id, (value, unit) in metrics.items()
    ]
    return {
        **envelope_fields(
            producer_id="battery_sag",
            design_id=document["design"]["id"],
            design_digest=design_digest,
            inputs=[input_record("power", canonical_digest(power_evidence))],
            metrics=metric_records,
            constraint_results=constraint_results,
            verification=(
                summarize_verification(constraint_results)
                if constraint_results
                else no_design_decision()
            ),
        ),
        "method": "first_order_resistive_drop_at_nominal_point",
        "limitations": [
            "Uses a source-conditioned typical DC impedance as a first-order resistance.",
            "Does not infer open-circuit voltage from SOC or predict loaded terminal voltage.",
            "Does not model temperature rise, transient impedance, aging, imbalance, or certification safety limits."
        ],
        "pack": {
            "pack_id": extension["pack_id"],
            "series": series,
            "parallel": parallel,
            "cell_component": cell_component,
            "cell_part": cell["id"],
            "cell_part_digest": catalog_digest(cell),
        },
        "condition_results": condition_results,
    }


def run(
    source: Path,
    power_evidence_path: Path,
    out: Path,
    repo_root: Path,
) -> dict[str, Any]:
    schema = load_json(repo_root / "spec/emes-ir-v0.schema.json")
    document = load_json(source)
    validator_cls = jsonschema.validators.validator_for(schema)
    validator_cls.check_schema(schema)
    validator_cls(schema).validate(document)
    validate_semantics(document)
    power_evidence = load_json(power_evidence_path)
    result = evaluate(document, power_evidence, repo_root)
    validate_evidence(result, repo_root, expected_producer="battery_sag")
    failures = [
        item["id"] for item in result["constraint_results"] if item["status"] == "fail"
    ]
    if failures:
        raise RuntimeError("battery sag constraints failed: " + ", ".join(failures))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("--power-evidence", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument("--check-determinism", action="store_true")
    args = parser.parse_args()

    first = run(args.source, args.power_evidence, args.out, args.repo_root)
    if args.check_determinism:
        with tempfile.TemporaryDirectory(prefix="emes-battery-sag-") as temp_dir:
            second = run(
                args.source,
                args.power_evidence,
                Path(temp_dir) / "evidence.json",
                args.repo_root,
            )
        if first != second:
            raise RuntimeError("non-deterministic battery sag evidence")
        print("DETERMINISTIC battery-sag-evidence")

    print(f"VALID battery-sag design={first['design_digest']}")
    for metric in first["metrics"]:
        print(f"{metric['id']}={metric['value']} {metric['unit']}")
    print(f"EVIDENCE {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
