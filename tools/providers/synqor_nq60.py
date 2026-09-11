#!/usr/bin/env python3
"""Normalize a reviewed SynQor NQ60 converter snapshot into an EMES catalog."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


def sha256(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def canonical_digest(value: Any) -> str:
    payload = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return sha256(payload)


def load_snapshot(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def fact(snapshot: dict[str, Any], name: str, unit: str) -> float:
    item = snapshot["facts"][name]
    if item.get("unit") != unit:
        raise ValueError(f"{name}: expected {unit}, got {item.get('unit')}")
    return float(item["value"])


def q(value: Any, unit: str, source: str) -> dict[str, Any]:
    return {"value": value, "unit": unit, "source": source}


def normalize(snapshot_path: Path, output_path: Path) -> dict[str, Any]:
    snapshot = load_snapshot(snapshot_path)
    manufacturer_source = "SRC_SYNQOR_NQ60_DATASHEET"
    config_source = "SRC_EMES_NQ60_REFERENCE_CONFIGURATION"
    upstream = snapshot["upstream"]["datasheet_pdf"]
    config = snapshot["analysis_configuration"]

    manufacturer_record: dict[str, Any] = {
        "id": manufacturer_source,
        "authority": "manufacturer",
        "format": "datasheet",
        "uri": upstream["uri"],
        "retrieved_at": snapshot["captured_at"],
        "license": "SynQor upstream terms; redistribution not asserted",
    }
    if upstream.get("sha256") is not None:
        manufacturer_record["raw_digest"] = upstream["sha256"]

    part = {
        "id": "SYNQOR_NQ60W60HGC40NRF_G",
        "kind": "power_converter",
        "identity": {
            "manufacturer": snapshot["manufacturer"],
            "part_number": snapshot["part_number"],
        },
        "properties": {
            "mass": q(fact(snapshot, "mass", "kg"), "kg", manufacturer_source),
            "input_voltage_min": q(
                fact(snapshot, "input_voltage_min", "V"), "V", manufacturer_source
            ),
            "input_voltage_max": q(
                fact(snapshot, "input_voltage_max", "V"), "V", manufacturer_source
            ),
            "output_voltage_min": q(
                fact(snapshot, "output_voltage_min", "V"), "V", manufacturer_source
            ),
            "output_voltage_max": q(
                fact(snapshot, "output_voltage_max", "V"), "V", manufacturer_source
            ),
            "continuous_input_current": q(
                fact(snapshot, "continuous_input_current", "A"), "A", manufacturer_source
            ),
            "continuous_output_current": q(
                fact(snapshot, "continuous_output_current", "A"), "A", manufacturer_source
            ),
            "output_current_limit_dependency": q(
                snapshot["facts"]["continuous_output_current"]["qualifier"],
                "1",
                manufacturer_source,
            ),
            "external_input_capacitance_min": q(
                fact(snapshot, "external_input_capacitance_min", "uF"),
                "uF",
                manufacturer_source,
            ),
            "external_input_capacitance_esr_min": q(
                fact(snapshot, "external_input_capacitance_esr_min", "ohm"),
                "ohm",
                manufacturer_source,
            ),
            "reference_baseplate_temperature": q(
                fact(snapshot, "reference_baseplate_temperature", "degC"),
                "degC",
                manufacturer_source,
            ),
            "reference_airflow": q(
                fact(snapshot, "reference_airflow", "LFM"),
                "LFM",
                manufacturer_source,
            ),
            "output_voltage": q(
                float(config["output_voltage"]["value"]), "V", config_source
            ),
            "efficiency": q(
                float(config["efficiency"]["value"]), "1", config_source
            ),
        },
        "interfaces": [
            {
                "id": "IF_DC_IN",
                "kind": "electrical_dc",
                "properties": {"role": q("input", "1", manufacturer_source)},
            },
            {
                "id": "IF_DC_OUT",
                "kind": "electrical_dc",
                "properties": {"role": q("output", "1", manufacturer_source)},
            },
        ],
    }

    catalog = {
        "emes_catalog_version": "0.1",
        "catalog": {
            "id": "CAT_SYNQOR_NQ60",
            "name": "SynQor NQ60W60HGC40NRF-G configurable buck/boost converter",
            "description": (
                "Reviewed SynQor NiQor NQ60 half-brick hardware facts plus an "
                "explicit EMES 36 V / 95% reference-analysis configuration; "
                "the analysis efficiency is not a manufacturer rating."
            ),
        },
        "sources": [
            manufacturer_record,
            {
                "id": config_source,
                "authority": "synthetic",
                "format": "json",
                "path": str(snapshot_path),
                "license": "CC0-1.0",
            },
        ],
        "parts": [part],
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(catalog, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return catalog


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("snapshot", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    catalog = normalize(args.snapshot, args.output)
    print(f"NORMALIZED {args.snapshot} -> {args.output}")
    print(f"catalog_digest={canonical_digest(catalog)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
