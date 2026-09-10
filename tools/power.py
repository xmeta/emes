#!/usr/bin/env python3
"""EMES Phase-1 electrical power and battery-pack evaluator."""

from __future__ import annotations

import argparse
import json
import math
import tempfile
from pathlib import Path
from typing import Any

import jsonschema

from catalog import canonical_digest as catalog_digest
from catalog import property_number, resolve_catalog_parts
from validate import canonical_digest, load_json, validate_semantics


def quantity_value(item: dict[str, Any], unit: str, context: str) -> float:
    if item.get("unit") != unit:
        raise ValueError(f"{context}: expected {unit}, got {item.get('unit')}")
    value = float(item["value"])
    if not math.isfinite(value):
        raise ValueError(f"{context}: non-finite value")
    return value


def index_by_id(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {item["id"]: item for item in items}


def positive_integer_parameter(document: dict[str, Any], parameter_id: str) -> int:
    parameter = index_by_id(document["parameters"])[parameter_id]
    value = quantity_value(parameter["value"], "1", parameter_id)
    integer = int(value)
    if value != integer or integer < 1:
        raise ValueError(f"{parameter_id}: expected positive integer, got {value}")
    return integer


def require_part_kind(part: dict[str, Any], expected: str, component_id: str) -> None:
    if part.get("kind") != expected:
        raise ValueError(
            f"{component_id}: expected catalog part kind {expected}, got {part.get('kind')}"
        )


def load_power_watts(document: dict[str, Any], load_case_id: str, voltage: float) -> tuple[float, float]:
    cases = index_by_id(document["load_cases"])
    case = cases[load_case_id]
    watts = 0.0
    direct_amps = 0.0
    for load in case.get("loads", []):
        unit = load["value"]["unit"]
        if unit == "W":
            watts += quantity_value(load["value"], "W", f"{load_case_id}.load")
        elif unit == "A":
            direct_amps += quantity_value(load["value"], "A", f"{load_case_id}.load")
        else:
            raise ValueError(f"{load_case_id}: power adapter does not support load unit {unit}")
    return watts + direct_amps * voltage, direct_amps + watts / voltage


def evaluate_constraints(
    document: dict[str, Any], metrics: dict[str, tuple[float, str]]
) -> list[dict[str, Any]]:
    operators = {
        ">=": lambda value, target: value >= target,
        "<=": lambda value, target: value <= target,
        ">": lambda value, target: value > target,
        "<": lambda value, target: value < target,
        "==": lambda value, target: value == target,
    }
    results: list[dict[str, Any]] = []
    for constraint in document["constraints"]:
        metric_id = constraint["metric"]
        if metric_id not in metrics:
            results.append(
                {"id": constraint["id"], "status": "not_evaluated", "reason": "metric unavailable"}
            )
            continue
        value, unit = metrics[metric_id]
        target = quantity_value(constraint["target"], unit, f"{constraint['id']}.target")
        passed = operators[constraint["op"]](value, target)
        results.append(
            {
                "id": constraint["id"],
                "metric": metric_id,
                "status": "pass" if passed else "fail",
                "value": value,
                "unit": unit,
                "op": constraint["op"],
                "target": target,
            }
        )
    return results


def evaluate(document: dict[str, Any], repo_root: Path) -> dict[str, Any]:
    selected = resolve_catalog_parts(
        document,
        repo_root=repo_root,
        catalog_schema_path=repo_root / "spec/emes-catalog-v0.schema.json",
    )
    extension = document.get("extensions", {}).get("org.emes.power")
    if not isinstance(extension, dict):
        raise ValueError("missing extensions.org.emes.power")
    packs = extension.get("battery_packs", [])
    paths = extension.get("power_paths", [])
    if len(packs) != 1 or len(paths) != 1:
        raise ValueError("Phase-1 power adapter requires exactly one battery pack and one power path")

    pack = packs[0]
    path = paths[0]
    if path["source_pack"] != pack["id"]:
        raise ValueError("power path references an unknown battery pack")

    cell_component = pack["cell_component"]
    bms_component = pack["bms_component"]
    converter_component = path["converter_component"]
    for component_id in (cell_component, bms_component, converter_component):
        if component_id not in selected:
            raise ValueError(f"{component_id}: power component must be catalog-backed")

    cell = selected[cell_component]
    bms = selected[bms_component]
    converter = selected[converter_component]
    require_part_kind(cell, "battery_cell", cell_component)
    require_part_kind(bms, "bms", bms_component)
    require_part_kind(converter, "power_converter", converter_component)

    series = positive_integer_parameter(document, pack["series_parameter"])
    parallel = positive_integer_parameter(document, pack["parallel_parameter"])
    cell_count = series * parallel

    cell_nominal_v = property_number(cell, "nominal_voltage", "V")
    cell_max_v = property_number(cell, "max_charge_voltage", "V")
    cell_min_v = property_number(cell, "min_discharge_voltage", "V")
    cell_capacity_ah = property_number(cell, "nominal_capacity", "A*h")
    cell_current_a = property_number(cell, "continuous_discharge_current", "A")
    cell_mass_kg = property_number(cell, "mass", "kg")

    pack_nominal_v = series * cell_nominal_v
    pack_max_v = series * cell_max_v
    pack_min_v = series * cell_min_v
    pack_capacity_ah = parallel * cell_capacity_ah
    pack_energy_wh = pack_nominal_v * pack_capacity_ah
    raw_pack_current_a = parallel * cell_current_a

    bms_series_limit = property_number(bms, "max_series_cells", "1")
    bms_current_a = property_number(bms, "continuous_discharge_current", "A")
    bms_max_v = property_number(bms, "max_pack_voltage", "V")
    if series > bms_series_limit:
        raise ValueError(f"BMS series limit exceeded: pack={series}S bms={bms_series_limit:g}S")
    if pack_max_v > bms_max_v:
        raise ValueError(f"BMS voltage limit exceeded: pack={pack_max_v:g}V bms={bms_max_v:g}V")
    pack_current_a = min(raw_pack_current_a, bms_current_a)

    converter_min_v = property_number(converter, "input_voltage_min", "V")
    converter_max_v = property_number(converter, "input_voltage_max", "V")
    converter_output_v = property_number(converter, "output_voltage", "V")
    converter_output_a = property_number(converter, "continuous_output_current", "A")
    efficiency = property_number(converter, "efficiency", "1")
    if not 0 < efficiency <= 1:
        raise ValueError(f"{converter_component}.efficiency must be in (0, 1]")
    if pack_min_v < converter_min_v or pack_max_v > converter_max_v:
        raise ValueError(
            "converter input range incompatible with full pack voltage range: "
            f"pack=[{pack_min_v:g},{pack_max_v:g}]V converter=[{converter_min_v:g},{converter_max_v:g}]V"
        )

    load_power_w, load_output_a = load_power_watts(
        document, path["load_case"], converter_output_v
    )
    converter_output_limit_w = converter_output_v * converter_output_a
    converter_power_margin_w = converter_output_limit_w - load_power_w
    input_power_w = load_power_w / efficiency
    equivalent_pack_current_a = input_power_w / pack_nominal_v
    current_margin_a = pack_current_a - equivalent_pack_current_a

    overhead_mass = quantity_value(pack["overhead_mass"], "kg", f"{pack['id']}.overhead_mass")
    bms_mass = property_number(bms, "mass", "kg")
    cell_mass_total = cell_count * cell_mass_kg
    total_mass = cell_mass_total + bms_mass + overhead_mass

    metrics: dict[str, tuple[float, str]] = {
        "M_PACK_CELL_COUNT": (float(cell_count), "1"),
        "M_PACK_NOMINAL_VOLTAGE": (pack_nominal_v, "V"),
        "M_PACK_MAX_VOLTAGE": (pack_max_v, "V"),
        "M_PACK_MIN_VOLTAGE": (pack_min_v, "V"),
        "M_PACK_CAPACITY": (pack_capacity_ah, "A*h"),
        "M_PACK_NOMINAL_ENERGY": (pack_energy_wh, "W*h"),
        "M_PACK_CONTINUOUS_CURRENT": (pack_current_a, "A"),
        "M_PACK_CONTINUOUS_POWER": (pack_nominal_v * pack_current_a, "W"),
        "M_PACK_CELL_MASS": (cell_mass_total, "kg"),
        "M_PACK_TOTAL_MASS": (total_mass, "kg"),
        "M_LOAD_POWER": (load_power_w, "W"),
        "M_LOAD_EQUIV_PACK_CURRENT": (equivalent_pack_current_a, "A"),
        "M_PACK_CURRENT_MARGIN": (current_margin_a, "A"),
        "M_BMS_SERIES_MARGIN": (bms_series_limit - series, "1"),
        "M_BMS_VOLTAGE_MARGIN": (bms_max_v - pack_max_v, "V"),
        "M_CONVERTER_OUTPUT_VOLTAGE": (converter_output_v, "V"),
        "M_CONVERTER_OUTPUT_CURRENT": (load_output_a, "A"),
        "M_CONVERTER_POWER_MARGIN": (converter_power_margin_w, "W"),
    }
    constraints = evaluate_constraints(document, metrics)
    return {
        "emes_evidence_version": "0.1",
        "design_id": document["design"]["id"],
        "design_digest": canonical_digest(document),
        "power_topology": {
            "pack_id": pack["id"],
            "series": series,
            "parallel": parallel,
            "cell_count": cell_count,
            "cell_component": cell_component,
            "bms_component": bms_component,
            "converter_component": converter_component,
        },
        "selected_parts": [
            {
                "component": component_id,
                "part_id": selected[component_id]["id"],
                "kind": selected[component_id]["kind"],
                "manufacturer": selected[component_id]["identity"]["manufacturer"],
                "part_number": selected[component_id]["identity"]["part_number"],
                "part_digest": catalog_digest(selected[component_id]),
            }
            for component_id in sorted((cell_component, bms_component, converter_component))
        ],
        "metrics": [
            {"id": metric_id, "value": value, "unit": unit, "method": "analytic"}
            for metric_id, (value, unit) in metrics.items()
        ],
        "constraint_results": constraints,
    }


def run(source: Path, out: Path, repo_root: Path) -> dict[str, Any]:
    schema = load_json(repo_root / "spec/emes-ir-v0.schema.json")
    document = load_json(source)
    validator_cls = jsonschema.validators.validator_for(schema)
    validator_cls.check_schema(schema)
    validator_cls(schema).validate(document)
    validate_semantics(document)
    evidence = evaluate(document, repo_root)
    failures = [item["id"] for item in evidence["constraint_results"] if item["status"] == "fail"]
    if failures:
        raise RuntimeError("power constraints failed: " + ", ".join(failures))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return evidence


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", nargs="?", type=Path, default=Path("examples/power-pack/mechanism.json"))
    parser.add_argument("--out", type=Path, default=Path("generated/power-pack/evidence.json"))
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument("--check-determinism", action="store_true")
    args = parser.parse_args()

    first = run(args.source, args.out, args.repo_root)
    if args.check_determinism:
        with tempfile.TemporaryDirectory(prefix="emes-power-") as temp_dir:
            second = run(args.source, Path(temp_dir) / "evidence.json", args.repo_root)
        if first != second:
            raise RuntimeError("non-deterministic power evidence")
        print("DETERMINISTIC power-evidence")

    print(f"VALID power design={first['design_digest']}")
    for metric in first["metrics"]:
        print(f"{metric['id']}={metric['value']} {metric['unit']}")
    print(f"EVIDENCE {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
