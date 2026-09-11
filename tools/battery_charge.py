#!/usr/bin/env python3
"""Evaluate static charger compatibility for a catalog-backed battery pack."""

from __future__ import annotations

import argparse
import json
import math
import tempfile
from pathlib import Path
from typing import Any

import jsonschema

from catalog import (
    canonical_digest as catalog_digest,
    load_json as load_catalog_json,
    part_by_id,
    property_number,
    resolve_catalog_parts,
    validate_catalog,
)
from evidence import envelope_fields, input_record, no_design_decision, validate_evidence
from power import positive_integer_parameter, quantity_value
from validate import canonical_digest, load_json, validate_semantics


def one_by_id(items: list[dict[str, Any]], item_id: str, context: str) -> dict[str, Any]:
    matches = [item for item in items if item.get("id") == item_id]
    if len(matches) != 1:
        raise ValueError(f"{context}: expected exactly one {item_id}")
    return matches[0]


def evaluate(
    document: dict[str, Any],
    request: dict[str, Any],
    repo_root: Path,
) -> dict[str, Any]:
    selected = resolve_catalog_parts(
        document,
        repo_root=repo_root,
        catalog_schema_path=repo_root / "spec/emes-catalog-v0.schema.json",
    )
    power = document.get("extensions", {}).get("org.emes.power")
    if not isinstance(power, dict):
        raise ValueError("missing extensions.org.emes.power")
    pack = one_by_id(power.get("battery_packs", []), request["pack_id"], "battery charge")

    cell_component = pack["cell_component"]
    bms_component = pack["bms_component"]
    if cell_component not in selected or bms_component not in selected:
        raise ValueError("battery charge requires catalog-backed cell and BMS components")
    cell = selected[cell_component]
    bms = selected[bms_component]
    if cell.get("kind") != "battery_cell":
        raise ValueError(f"{cell_component}: expected battery_cell")
    if bms.get("kind") != "bms":
        raise ValueError(f"{bms_component}: expected bms")

    series = positive_integer_parameter(document, pack["series_parameter"])
    parallel = positive_integer_parameter(document, pack["parallel_parameter"])

    charger_binding = request["charger"]
    charger_path = repo_root / charger_binding["catalog_path"]
    charger_catalog = load_catalog_json(charger_path)
    catalog_schema = load_catalog_json(repo_root / "spec/emes-catalog-v0.schema.json")
    validate_catalog(charger_catalog, catalog_schema, root=repo_root)
    actual_catalog_digest = catalog_digest(charger_catalog)
    if actual_catalog_digest != charger_binding["catalog_digest"]:
        raise ValueError(
            "charger catalog digest mismatch: "
            f"expected {charger_binding['catalog_digest']} got {actual_catalog_digest}"
        )
    charger = part_by_id(charger_catalog, charger_binding["part_id"])
    actual_part_digest = catalog_digest(charger)
    if actual_part_digest != charger_binding["part_digest"]:
        raise ValueError(
            "charger part digest mismatch: "
            f"expected {charger_binding['part_digest']} got {actual_part_digest}"
        )
    if charger.get("kind") != "charger":
        raise ValueError(f"{charger['id']}: expected charger catalog part")

    target_v = quantity_value(request["target_voltage"], "V", "charge.target_voltage")
    charge_a = quantity_value(request["charge_current"], "A", "charge.charge_current")
    cell_max_v = property_number(cell, "max_charge_voltage", "V")
    cell_standard_a = property_number(cell, "standard_charge_current", "A")
    cell_temp_min = property_number(cell, "charge_temperature_min", "degC")
    cell_temp_max = property_number(cell, "charge_temperature_max", "degC")
    bms_charge_a = property_number(bms, "max_charge_current", "A")
    bms_cutoff_c = property_number(bms, "default_cell_charge_cutoff_temperature", "degC")
    charger_min_v = property_number(charger, "charge_voltage_min", "V")
    charger_max_v = property_number(charger, "charge_voltage_max", "V")
    charger_max_a = property_number(charger, "max_output_current", "A")
    charger_max_w = property_number(charger, "max_output_power", "W")

    pack_max_charge_v = series * cell_max_v
    pack_standard_charge_a = parallel * cell_standard_a
    requested_power_w = target_v * charge_a
    charger_power_current_a = charger_max_w / target_v

    if not math.isclose(target_v, pack_max_charge_v, rel_tol=0.0, abs_tol=1e-12):
        raise ValueError(
            "charge target voltage must equal the source-backed pack maximum charge voltage: "
            f"target={target_v:g}V pack={pack_max_charge_v:g}V"
        )
    if target_v < charger_min_v or target_v > charger_max_v:
        raise ValueError(
            "charge target voltage is outside charger range: "
            f"target={target_v:g}V charger=[{charger_min_v:g},{charger_max_v:g}]V"
        )
    if charge_a > pack_standard_charge_a:
        raise ValueError(
            "charge current exceeds source-backed pack standard charge current: "
            f"requested={charge_a:g}A pack={pack_standard_charge_a:g}A"
        )
    if charge_a > charger_max_a:
        raise ValueError(
            "charge current exceeds charger current limit: "
            f"requested={charge_a:g}A charger={charger_max_a:g}A"
        )
    if requested_power_w > charger_max_w:
        raise ValueError(
            "charge power exceeds charger power limit: "
            f"requested={requested_power_w:g}W charger={charger_max_w:g}W"
        )
    if charge_a > bms_charge_a:
        raise ValueError(
            "charge current exceeds BMS charge-current limit: "
            f"requested={charge_a:g}A bms={bms_charge_a:g}A"
        )

    thermal_guard_margin_c = cell_temp_max - bms_cutoff_c
    thermal_guard_sufficient = thermal_guard_margin_c >= 0.0
    metrics = [
        {"id": "M_CHARGE_TARGET_VOLTAGE", "value": target_v, "unit": "V", "method": "analytic"},
        {"id": "M_PACK_MAX_CHARGE_VOLTAGE", "value": pack_max_charge_v, "unit": "V", "method": "analytic"},
        {"id": "M_CHARGER_MIN_VOLTAGE_MARGIN", "value": target_v - charger_min_v, "unit": "V", "method": "analytic"},
        {"id": "M_CHARGER_MAX_VOLTAGE_MARGIN", "value": charger_max_v - target_v, "unit": "V", "method": "analytic"},
        {"id": "M_CHARGE_CURRENT", "value": charge_a, "unit": "A", "method": "analytic"},
        {"id": "M_PACK_STANDARD_CHARGE_CURRENT_MARGIN", "value": pack_standard_charge_a - charge_a, "unit": "A", "method": "analytic"},
        {"id": "M_CHARGER_CURRENT_MARGIN", "value": charger_max_a - charge_a, "unit": "A", "method": "analytic"},
        {"id": "M_CHARGER_POWER_CURRENT_MARGIN", "value": charger_power_current_a - charge_a, "unit": "A", "method": "analytic"},
        {"id": "M_CHARGER_POWER_MARGIN", "value": charger_max_w - requested_power_w, "unit": "W", "method": "analytic"},
        {"id": "M_BMS_CHARGE_CURRENT_MARGIN", "value": bms_charge_a - charge_a, "unit": "A", "method": "analytic"},
        {"id": "M_CELL_CHARGE_TEMPERATURE_MIN", "value": cell_temp_min, "unit": "degC", "method": "source"},
        {"id": "M_CELL_CHARGE_TEMPERATURE_MAX", "value": cell_temp_max, "unit": "degC", "method": "source"},
        {"id": "M_BMS_DEFAULT_CHARGE_CUTOFF_TEMPERATURE", "value": bms_cutoff_c, "unit": "degC", "method": "source"},
        {"id": "M_CHARGE_THERMAL_GUARD_MARGIN", "value": thermal_guard_margin_c, "unit": "degC", "method": "analytic"},
    ]
    return {
        **envelope_fields(
            producer_id="battery_charge_compatibility",
            design_id=document["design"]["id"],
            design_digest=canonical_digest(document),
            inputs=[
                input_record("charge_request", catalog_digest(request), "analysis_request"),
                input_record("charger_catalog", actual_catalog_digest, "artifact"),
            ],
            metrics=metrics,
            constraint_results=[],
            verification=no_design_decision(),
        ),
        "charge_compatibility": {
            "scope": "static_electrical_compatibility_only",
            "electrical_compatible": True,
            "thermal_guard_sufficient": thermal_guard_sufficient,
            "automatic_charging_approval": False,
        },
        "pack": {
            "pack_id": request["pack_id"],
            "series": series,
            "parallel": parallel,
            "cell_component": cell_component,
            "bms_component": bms_component,
        },
        "charger": {
            "part_id": charger["id"],
            "part_digest": actual_part_digest,
            "catalog_digest": actual_catalog_digest,
            "target_voltage": target_v,
            "charge_current": charge_a,
            "power_current_limit": charger_power_current_a,
        },
        "temperature_guard": {
            "cell_charge_temperature_min": cell_temp_min,
            "cell_charge_temperature_max": cell_temp_max,
            "bms_factory_default_charge_cutoff": bms_cutoff_c,
            "margin": thermal_guard_margin_c,
            "sufficient": thermal_guard_sufficient,
        },
        "limitations": [
            "Static compatibility evidence only; it does not authorize charging, fabrication, or energization.",
            "The current JKBMS factory-default 70 degC charge cutoff is above the P45B 60 degC charge operating maximum and is not treated as a sufficient automatic thermal guard.",
            "The JKBMS manual states the threshold is user-configurable, but this evidence does not invent or certify an exact 60 degC setting range.",
            "Charger thermal derating, charge termination dynamics, balancing, communications, connectors, enclosure, and system EMC are not evaluated."
        ],
    }


def run(
    source: Path,
    request_path: Path,
    out: Path,
    repo_root: Path,
) -> dict[str, Any]:
    mechanism_schema = load_json(repo_root / "spec/emes-ir-v0.schema.json")
    document = load_json(source)
    validator_cls = jsonschema.validators.validator_for(mechanism_schema)
    validator_cls.check_schema(mechanism_schema)
    validator_cls(mechanism_schema).validate(document)
    validate_semantics(document)

    request_schema = load_json(repo_root / "spec/emes-battery-charge-v0.schema.json")
    request = load_json(request_path)
    request_validator_cls = jsonschema.validators.validator_for(request_schema)
    request_validator_cls.check_schema(request_schema)
    request_validator_cls(request_schema).validate(request)

    result = evaluate(document, request, repo_root)
    validate_evidence(result, repo_root, expected_producer="battery_charge_compatibility")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument("--check-determinism", action="store_true")
    args = parser.parse_args()

    first = run(args.source, args.request, args.out, args.repo_root)
    if args.check_determinism:
        with tempfile.TemporaryDirectory(prefix="emes-battery-charge-") as temp_dir:
            second = run(
                args.source,
                args.request,
                Path(temp_dir) / "evidence.json",
                args.repo_root,
            )
        if first != second:
            raise RuntimeError("non-deterministic battery charge compatibility evidence")
        print("DETERMINISTIC battery-charge-compatibility-evidence")

    print(f"VALID battery-charge-compatibility design={first['design_digest']}")
    for metric in first["metrics"]:
        print(f"{metric['id']}={metric['value']} {metric['unit']}")
    print(f"EVIDENCE {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
