#!/usr/bin/env python3
"""EMES electrical power and battery-pack evaluator."""

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
from validate import canonical_digest, load_json, summarize_verification, validate_semantics


OPERATORS = {
    ">=": lambda value, target: value >= target,
    "<=": lambda value, target: value <= target,
    ">": lambda value, target: value > target,
    "<": lambda value, target: value < target,
    "==": lambda value, target: value == target,
}


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
    parameters = index_by_id(document["parameters"])
    if parameter_id not in parameters:
        raise ValueError(f"unknown power parameter {parameter_id}")
    parameter = parameters[parameter_id]
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


def load_power_watts(
    document: dict[str, Any], load_case_id: str, voltage: float
) -> tuple[float, float]:
    cases = index_by_id(document["load_cases"])
    if load_case_id not in cases:
        raise ValueError(f"unknown power load case {load_case_id}")
    watts = 0.0
    direct_amps = 0.0
    for load in cases[load_case_id].get("loads", []):
        unit = load["value"]["unit"]
        if unit == "W":
            watts += quantity_value(load["value"], "W", f"{load_case_id}.load")
        elif unit == "A":
            direct_amps += quantity_value(load["value"], "A", f"{load_case_id}.load")
        else:
            raise ValueError(
                f"{load_case_id}: power adapter does not support load unit {unit}"
            )
    return watts + direct_amps * voltage, direct_amps + watts / voltage


def condition_value(item: dict[str, Any], unit: str, context: str) -> float | str | bool:
    if item.get("unit") != unit:
        raise ValueError(f"{context}: expected {unit}, got {item.get('unit')}")
    value = item["value"]
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        numeric = float(value)
        if not math.isfinite(numeric):
            raise ValueError(f"{context}: non-finite value")
        return numeric
    if isinstance(value, (str, bool)):
        return value
    raise ValueError(f"{context}: unsupported condition value {value!r}")


def parameter_condition_value(
    document: dict[str, Any], parameter_id: str, unit: str
) -> float:
    parameters = index_by_id(document["parameters"])
    if parameter_id not in parameters:
        raise ValueError(f"unknown condition parameter {parameter_id}")
    return quantity_value(parameters[parameter_id]["value"], unit, parameter_id)


def conditioned_property_number(
    document: dict[str, Any],
    part: dict[str, Any],
    name: str,
    unit: str,
    *,
    rated_component: str,
    controls: dict[str, dict[str, Any]],
    condition_bindings: dict[str, dict[str, str]] | None = None,
) -> tuple[float, list[dict[str, Any]]]:
    try:
        item = part["properties"][name]
    except KeyError as exc:
        raise ValueError(f"{part['id']}: missing property {name}") from exc
    value = quantity_value(item, unit, f"{part['id']}.{name}")

    bindings = condition_bindings or {}
    results: list[dict[str, Any]] = []
    for condition in item.get("conditions", []):
        parameter = condition["parameter"]
        condition_unit = condition["unit"]
        binding = bindings.get(parameter)
        if binding is None:
            raise ValueError(
                f"{part['id']}.{name}: conditional rating requires explicit binding "
                f"for {parameter!r}"
            )
        control_component = binding["control_component"]
        if control_component not in controls:
            raise ValueError(
                f"{part['id']}.{name}: bound control component {control_component!r} "
                "is not selected for this rating"
            )
        control_part = controls[control_component]
        control_property = binding["control_property"]
        try:
            source_item = control_part["properties"][control_property]
        except KeyError as exc:
            raise ValueError(
                f"{control_part['id']}: missing source-backed control property "
                f"{control_property!r}"
            ) from exc
        source_value = condition_value(
            source_item, condition_unit, f"{control_part['id']}.{control_property}"
        )
        design_parameter = binding["parameter"]
        control_value = parameter_condition_value(
            document, design_parameter, condition_unit
        )
        if control_value != source_value:
            raise ValueError(
                f"{design_parameter}: selected condition value {control_value:g} "
                f"{condition_unit} is not backed by {control_part['id']}."
                f"{control_property}={source_value:g} {condition_unit}"
            )
        control_record = {
            "control_component": control_component,
            "control_part": control_part["id"],
            "control_property": control_property,
            "design_parameter": design_parameter,
            "control_value": control_value,
        }

        target_value = condition_value(
            condition, condition_unit, f"{part['id']}.{name}.condition"
        )
        op = condition["op"]
        if op != "==" and (
            isinstance(control_value, (str, bool))
            or isinstance(target_value, (str, bool))
        ):
            raise ValueError(
                f"{part['id']}.{name}: ordered condition {op} requires numeric values"
            )
        passed = OPERATORS[op](control_value, target_value)
        result = {
            "rated_component": rated_component,
            "rated_part": part["id"],
            "rated_property": name,
            "rated_value": value,
            "rated_unit": unit,
            "condition_parameter": parameter,
            "condition_op": op,
            "condition_value": target_value,
            "condition_unit": condition_unit,
            **control_record,
            "status": "pass" if passed else "fail",
        }
        if not passed:
            raise ValueError(
                f"{part['id']}.{name}: rating condition failed: "
                f"{parameter}={control_value} {condition_unit} {op} "
                f"{target_value} {condition_unit}"
            )
        results.append(result)
    return value, results

def evaluate_constraints(
    document: dict[str, Any], metrics: dict[str, tuple[float, str]]
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for constraint in document["constraints"]:
        metric_id = constraint["metric"]
        if metric_id not in metrics:
            results.append(
                {
                    "id": constraint["id"],
                    "status": "not_evaluated",
                    "reason": "metric unavailable",
                }
            )
            continue
        value, unit = metrics[metric_id]
        target = quantity_value(
            constraint["target"], unit, f"{constraint['id']}.target"
        )
        passed = OPERATORS[constraint["op"]](value, target)
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


def selected_part_record(component_id: str, part: dict[str, Any]) -> dict[str, Any]:
    return {
        "component": component_id,
        "part_id": part["id"],
        "kind": part["kind"],
        "manufacturer": part["identity"]["manufacturer"],
        "part_number": part["identity"]["part_number"],
        "part_digest": catalog_digest(part),
    }


def evidence(
    document: dict[str, Any],
    topology: dict[str, Any],
    selected: dict[str, dict[str, Any]],
    selected_ids: list[str],
    metrics: dict[str, tuple[float, str]],
    *,
    rating_condition_results: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    constraint_results = evaluate_constraints(document, metrics)
    return {
        "emes_evidence_version": "0.1",
        "design_id": document["design"]["id"],
        "design_digest": canonical_digest(document),
        "power_topology": topology,
        "selected_parts": [
            selected_part_record(component_id, selected[component_id])
            for component_id in sorted(selected_ids)
        ],
        "rating_condition_results": rating_condition_results or [],
        "metrics": [
            {
                "id": metric_id,
                "value": value,
                "unit": unit,
                "method": "analytic",
            }
            for metric_id, (value, unit) in metrics.items()
        ],
        "verification": summarize_verification(constraint_results),
        "constraint_results": constraint_results,
    }


def evaluate_fixed_supply(
    document: dict[str, Any],
    selected: dict[str, dict[str, Any]],
    path: dict[str, Any],
) -> dict[str, Any]:
    source_component = path["source_component"]
    if "converter_component" in path:
        raise ValueError(
            "Phase-1 fixed power-supply path does not yet support a converter"
        )
    if source_component not in selected:
        raise ValueError(f"{source_component}: power source must be catalog-backed")
    source = selected[source_component]
    require_part_kind(source, "power_supply", source_component)

    voltage = property_number(source, "output_voltage", "V")
    current_limit = property_number(source, "continuous_output_current", "A")
    declared_power = property_number(source, "continuous_output_power", "W")
    current_power = voltage * current_limit
    power_limit = min(declared_power, current_power)
    load_power, load_current = load_power_watts(
        document, path["load_case"], voltage
    )

    metrics: dict[str, tuple[float, str]] = {
        "M_BUS_VOLTAGE": (voltage, "V"),
        "M_LOAD_POWER": (load_power, "W"),
        "M_LOAD_OUTPUT_CURRENT": (load_current, "A"),
        "M_SOURCE_CONTINUOUS_CURRENT": (current_limit, "A"),
        "M_SOURCE_CONTINUOUS_POWER": (power_limit, "W"),
        "M_SOURCE_CURRENT_MARGIN": (current_limit - load_current, "A"),
        "M_SOURCE_POWER_MARGIN": (power_limit - load_power, "W"),
    }
    return evidence(
        document,
        {
            "path_id": path["id"],
            "source_kind": "power_supply",
            "source_component": source_component,
            "load_case": path["load_case"],
        },
        selected,
        [source_component],
        metrics,
    )


def evaluate_battery_pack(
    document: dict[str, Any],
    selected: dict[str, dict[str, Any]],
    pack: dict[str, Any],
    path: dict[str, Any],
) -> dict[str, Any]:
    if path["source_pack"] != pack["id"]:
        raise ValueError("power path references an unknown battery pack")
    if "bms_component" not in pack:
        raise ValueError("Phase-1 battery pack requires an explicit BMS component")
    if "converter_component" not in path:
        raise ValueError(
            "Phase-1 battery pack path requires an explicit power converter"
        )

    cell_component = pack["cell_component"]
    bms_component = pack["bms_component"]
    converter_component = path["converter_component"]
    component_ids = {item["id"] for item in document["components"]}
    for component_id in (
        pack["component"],
        cell_component,
        bms_component,
        converter_component,
    ):
        if component_id not in component_ids:
            raise ValueError(f"{pack['id']}: unknown component {component_id}")
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
    cell_current_a, rating_condition_results = conditioned_property_number(
        document,
        cell,
        "continuous_discharge_current",
        "A",
        rated_component=cell_component,
        controls={bms_component: bms},
        condition_bindings=pack.get("condition_bindings"),
    )
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
    bms_properties = bms.get("properties", {})
    bms_min_series = (
        property_number(bms, "min_series_cells", "1")
        if "min_series_cells" in bms_properties
        else None
    )
    bms_min_v = (
        property_number(bms, "min_pack_voltage", "V")
        if "min_pack_voltage" in bms_properties
        else None
    )
    if bms_min_series is not None and series < bms_min_series:
        raise ValueError(
            f"BMS minimum series count not met: pack={series}S bms={bms_min_series:g}S"
        )
    if series > bms_series_limit:
        raise ValueError(
            f"BMS series limit exceeded: pack={series}S bms={bms_series_limit:g}S"
        )
    if bms_min_v is not None and pack_min_v < bms_min_v:
        raise ValueError(
            f"BMS minimum pack voltage not met: pack={pack_min_v:g}V bms={bms_min_v:g}V"
        )
    if pack_max_v > bms_max_v:
        raise ValueError(
            f"BMS voltage limit exceeded: pack={pack_max_v:g}V bms={bms_max_v:g}V"
        )
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
            f"pack=[{pack_min_v:g},{pack_max_v:g}]V "
            f"converter=[{converter_min_v:g},{converter_max_v:g}]V"
        )

    load_power_w, load_output_a = load_power_watts(
        document, path["load_case"], converter_output_v
    )
    converter_output_limit_w = converter_output_v * converter_output_a
    converter_power_margin_w = converter_output_limit_w - load_power_w
    input_power_w = load_power_w / efficiency
    # Preserve a nominal-point reference current for SOC-conditioned analyses.
    equivalent_pack_current_a = input_power_w / pack_nominal_v
    if pack_min_v <= 0:
        raise ValueError(f"pack minimum voltage must be positive, got {pack_min_v:g}V")
    # Protection/path current sizing must cover the full modeled pack voltage range.
    max_pack_current_a = input_power_w / pack_min_v
    current_margin_a = pack_current_a - max_pack_current_a

    overhead_mass = quantity_value(
        pack["overhead_mass"], "kg", f"{pack['id']}.overhead_mass"
    )
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
        "M_LOAD_MAX_PACK_CURRENT": (max_pack_current_a, "A"),
        "M_PACK_CURRENT_MARGIN": (current_margin_a, "A"),
        "M_BMS_SERIES_MARGIN": (bms_series_limit - series, "1"),
        "M_BMS_VOLTAGE_MARGIN": (bms_max_v - pack_max_v, "V"),
        "M_CONVERTER_OUTPUT_VOLTAGE": (converter_output_v, "V"),
        "M_CONVERTER_OUTPUT_CURRENT": (load_output_a, "A"),
        "M_CONVERTER_POWER_MARGIN": (converter_power_margin_w, "W"),
    }
    if bms_min_series is not None:
        metrics["M_BMS_MIN_SERIES_MARGIN"] = (series - bms_min_series, "1")
    if bms_min_v is not None:
        metrics["M_BMS_MIN_VOLTAGE_MARGIN"] = (pack_min_v - bms_min_v, "V")

    return evidence(
        document,
        {
            "path_id": path["id"],
            "source_kind": "battery_pack",
            "pack_id": pack["id"],
            "series": series,
            "parallel": parallel,
            "cell_count": cell_count,
            "cell_component": cell_component,
            "bms_component": bms_component,
            "converter_component": converter_component,
            "load_case": path["load_case"],
        },
        selected,
        [cell_component, bms_component, converter_component],
        metrics,
        rating_condition_results=rating_condition_results,
    )


def evaluate(document: dict[str, Any], repo_root: Path) -> dict[str, Any]:
    selected = resolve_catalog_parts(
        document,
        repo_root=repo_root,
        catalog_schema_path=repo_root / "spec/emes-catalog-v0.schema.json",
    )
    extension = document.get("extensions", {}).get("org.emes.power")
    if not isinstance(extension, dict):
        raise ValueError("missing extensions.org.emes.power")
    power_schema = load_json(repo_root / "spec/emes-power-v0.schema.json")
    validator_cls = jsonschema.validators.validator_for(power_schema)
    validator_cls.check_schema(power_schema)
    validator_cls(power_schema).validate(extension)

    paths = extension["power_paths"]
    if len(paths) != 1:
        raise ValueError("Phase-1 power adapter requires exactly one power path")
    path = paths[0]
    if "source_component" in path:
        return evaluate_fixed_supply(document, selected, path)

    packs = extension["battery_packs"]
    if len(packs) != 1:
        raise ValueError("Phase-1 battery analysis requires exactly one battery pack")
    return evaluate_battery_pack(document, selected, packs[0], path)


def run(source: Path, out: Path, repo_root: Path) -> dict[str, Any]:
    schema = load_json(repo_root / "spec/emes-ir-v0.schema.json")
    document = load_json(source)
    validator_cls = jsonschema.validators.validator_for(schema)
    validator_cls.check_schema(schema)
    validator_cls(schema).validate(document)
    validate_semantics(document)
    result = evaluate(document, repo_root)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    verification = result["verification"]
    if verification["design_decision"] == "rejected":
        raise RuntimeError(
            "power constraints failed: "
            + ", ".join(verification["failed_constraints"])
        )
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "source",
        nargs="?",
        type=Path,
        default=Path("examples/power-pack/mechanism.json"),
    )
    parser.add_argument(
        "--out", type=Path, default=Path("generated/power/evidence.json")
    )
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument("--check-determinism", action="store_true")
    args = parser.parse_args()

    first = run(args.source, args.out, args.repo_root)
    if args.check_determinism:
        with tempfile.TemporaryDirectory(prefix="emes-power-") as temp_dir:
            second = run(
                args.source, Path(temp_dir) / "evidence.json", args.repo_root
            )
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
