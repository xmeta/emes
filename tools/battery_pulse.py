#!/usr/bin/env python3
"""Evaluate source-conditioned battery pulse-power capability."""

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
from power import OPERATORS, quantity_value
from validate import canonical_digest, load_json, validate_semantics


def evidence_metric(power_evidence: dict[str, Any], metric_id: str, unit: str) -> float:
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


def context_value(config: dict[str, Any], name: str, unit: str) -> float:
    context = config["context"]
    if name not in context:
        raise ValueError(f"missing pulse-analysis context value {name!r}")
    return quantity_value(context[name], unit, f"pulse context {name}")


def candidate_match(
    config: dict[str, Any], part: dict[str, Any], property_name: str
) -> tuple[bool, dict[str, Any]]:
    try:
        item = part["properties"][property_name]
    except KeyError as exc:
        raise ValueError(f"{part['id']}: missing pulse property {property_name}") from exc

    power = quantity_value(item, "W", f"{part['id']}.{property_name}")
    conditions = item.get("conditions", [])
    if not conditions:
        raise ValueError(f"{part['id']}.{property_name}: pulse property must be source-conditioned")

    checks: list[dict[str, Any]] = []
    matched = True
    for condition in conditions:
        name = condition["parameter"]
        unit = condition["unit"]
        actual = context_value(config, name, unit)
        target = float(condition["value"])
        if not math.isfinite(target):
            raise ValueError(f"{part['id']}.{property_name}.{name}: non-finite condition target")
        op = condition["op"]
        passed = OPERATORS[op](actual, target)
        matched = matched and passed
        checks.append(
            {
                "condition_parameter": name,
                "condition_op": op,
                "condition_value": target,
                "condition_unit": unit,
                "context_value": actual,
                "status": "pass" if passed else "fail",
            }
        )

    return matched, {
        "property": property_name,
        "property_value": power,
        "property_unit": "W",
        "source": item.get("source"),
        "condition_results": checks,
    }


def evaluate(
    document: dict[str, Any],
    config: dict[str, Any],
    power_evidence: dict[str, Any],
    repo_root: Path,
) -> dict[str, Any]:
    design_digest = canonical_digest(document)
    if power_evidence.get("design_digest") != design_digest:
        raise ValueError("power evidence design digest does not match mechanism")

    topology = power_evidence.get("power_topology", {})
    if topology.get("source_kind") != "battery_pack":
        raise ValueError("battery pulse analysis requires battery-pack power evidence")
    if topology.get("pack_id") != config["pack_id"]:
        raise ValueError("battery pulse pack_id does not match power evidence")

    series = int(topology["series"])
    parallel = int(topology["parallel"])
    if series < 1 or parallel < 1:
        raise ValueError("invalid series/parallel counts in power evidence")
    cell_count = series * parallel
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

    evaluated = [candidate_match(config, cell, name) for name in config["power_properties"]]
    matches = [record for matched, record in evaluated if matched]
    if not matches:
        raise ValueError("no source-conditioned battery pulse-power point matches the requested context")
    if len(matches) != 1:
        names = ", ".join(record["property"] for record in matches)
        raise ValueError(f"ambiguous pulse-power context matched multiple properties: {names}")
    selected_point = matches[0]

    cell_power = float(selected_point["property_value"])
    pack_cell_envelope = cell_power * cell_count
    pack_current = evidence_metric(power_evidence, "M_LOAD_EQUIV_PACK_CURRENT", "A")
    pack_nominal_voltage = evidence_metric(power_evidence, "M_PACK_NOMINAL_VOLTAGE", "V")
    reference_input_power = pack_current * pack_nominal_voltage
    margin = pack_cell_envelope - reference_input_power

    target = quantity_value(config["constraint"]["target"], "W", "battery pulse constraint target")
    op = config["constraint"]["op"]
    passed = OPERATORS[op](margin, target)

    metrics = [
        {"id": "M_CELL_PULSE_POWER_LIMIT", "value": cell_power, "unit": "W", "method": "manufacturer_point"},
        {"id": "M_PACK_CELL_ENVELOPE_PULSE_POWER", "value": pack_cell_envelope, "unit": "W", "method": "analytic"},
        {"id": "M_PULSE_REFERENCE_PACK_INPUT_POWER", "value": reference_input_power, "unit": "W", "method": "analytic"},
        {"id": "M_PACK_CELL_ENVELOPE_PULSE_POWER_MARGIN", "value": margin, "unit": "W", "method": "analytic"},
    ]

    return {
        "emes_battery_pulse_evidence_version": "0.1",
        "analysis_id": config["analysis_id"],
        "design_id": document["design"]["id"],
        "design_digest": design_digest,
        "analysis_request_digest": canonical_digest(config),
        "input_power_evidence_digest": canonical_digest(power_evidence),
        "method": "exact_source_conditioned_cell_power_point_with_ideal_pack_sum",
        "limitations": [
            "Selects only an exact source-conditioned manufacturer pulse-power point; no SOC or duration interpolation is performed.",
            "Aggregate pack cell power is an ideal sum across identical cells and excludes BMS, interconnect, contactor, fuse, connector, thermal, imbalance, and aging limits.",
            "This is not a system-level pulse rating, fabrication approval, charging approval, or energization approval.",
        ],
        "pack": {
            "pack_id": config["pack_id"],
            "series": series,
            "parallel": parallel,
            "cell_count": cell_count,
            "cell_component": cell_component,
            "cell_part": cell["id"],
            "cell_part_digest": catalog_digest(cell),
        },
        "context": config["context"],
        "selected_power_point": selected_point,
        "candidate_results": [record for _, record in evaluated],
        "metrics": metrics,
        "constraint_result": {
            "id": config["constraint"]["id"],
            "metric": "M_PACK_CELL_ENVELOPE_PULSE_POWER_MARGIN",
            "status": "pass" if passed else "fail",
            "value": margin,
            "unit": "W",
            "op": op,
            "target": target,
        },
    }


def run(
    source: Path,
    analysis_request: Path,
    power_evidence_path: Path,
    out: Path,
    repo_root: Path,
) -> dict[str, Any]:
    ir_schema = load_json(repo_root / "spec/emes-ir-v0.schema.json")
    document = load_json(source)
    validator_cls = jsonschema.validators.validator_for(ir_schema)
    validator_cls.check_schema(ir_schema)
    validator_cls(ir_schema).validate(document)
    validate_semantics(document)

    config = load_json(analysis_request)
    pulse_schema = load_json(repo_root / "spec/emes-battery-pulse-v0.schema.json")
    pulse_validator_cls = jsonschema.validators.validator_for(pulse_schema)
    pulse_validator_cls.check_schema(pulse_schema)
    pulse_validator_cls(pulse_schema).validate(config)

    power_evidence = load_json(power_evidence_path)
    result = evaluate(document, config, power_evidence, repo_root)
    if result["constraint_result"]["status"] == "fail":
        raise RuntimeError(f"battery pulse constraint failed: {result['constraint_result']['id']}")

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("--analysis-request", type=Path, required=True)
    parser.add_argument("--power-evidence", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument("--check-determinism", action="store_true")
    args = parser.parse_args()

    first = run(args.source, args.analysis_request, args.power_evidence, args.out, args.repo_root)
    if args.check_determinism:
        with tempfile.TemporaryDirectory(prefix="emes-battery-pulse-") as temp_dir:
            second = run(
                args.source,
                args.analysis_request,
                args.power_evidence,
                Path(temp_dir) / "evidence.json",
                args.repo_root,
            )
        if first != second:
            raise RuntimeError("non-deterministic battery pulse evidence")
        print("DETERMINISTIC battery-pulse-evidence")

    point = first["selected_power_point"]
    print(f"VALID battery-pulse property={point['property']} value={point['property_value']} W")
    for metric in first["metrics"]:
        print(f"{metric['id']}={metric['value']} {metric['unit']}")
    print(f"EVIDENCE {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
