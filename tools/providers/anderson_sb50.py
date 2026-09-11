#!/usr/bin/env python3
"""Anderson Power Products SB50 provider for a reviewed connector assembly snapshot."""

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


def fact(snapshot: dict[str, Any], name: str) -> dict[str, Any]:
    try:
        return snapshot["facts"][name]
    except KeyError as exc:
        raise ValueError(f"missing Anderson SB50 snapshot fact: {name}") from exc


def number(snapshot: dict[str, Any], name: str, unit: str) -> float:
    item = fact(snapshot, name)
    if item.get("unit") != unit:
        raise ValueError(f"{name}: expected {unit}, got {item.get('unit')}")
    return float(item["value"])


def text(snapshot: dict[str, Any], name: str, unit: str) -> str:
    item = fact(snapshot, name)
    if item.get("unit") != unit:
        raise ValueError(f"{name}: expected {unit}, got {item.get('unit')}")
    return str(item["value"])


def q(value: float | str, unit: str, source: str) -> dict[str, Any]:
    return {"value": value, "unit": unit, "source": source}


def normalize(snapshot_path: Path, output_path: Path) -> dict[str, Any]:
    snapshot = load_snapshot(snapshot_path)
    upstream = snapshot["upstream"]["datasheet_pdf"]
    technical_reference = snapshot["upstream"]["technical_reference"]
    configuration = snapshot["configuration"]
    datasheet_source_id = "SRC_ANDERSON_SB50_DATASHEET"
    technical_source_id = "SRC_ANDERSON_SB50_TECHREF"

    part = {
        "id": "ANDERSON_SB50_992G4_5900_WTW",
        "kind": "connector",
        "identity": {
            "manufacturer": snapshot["manufacturer"],
            "part_number": (
                f"{configuration['housing_part_number']} + "
                f"{configuration['contact_part_number']} wire-to-wire mated assembly"
            ),
        },
        "properties": {
            "rated_current": q(
                number(snapshot, "reference_current_6awg", "A"),
                "A",
                technical_source_id,
            ),
            "rated_voltage_dc": q(
                number(snapshot, "rated_voltage_dc_iec", "V"),
                "V",
                datasheet_source_id,
            ),
            "contact_wire_size": q(
                number(snapshot, "contact_wire_size", "AWG"),
                "AWG",
                datasheet_source_id,
            ),
            "reference_temperature_rise_at_rated_current": q(
                number(snapshot, "reference_temperature_rise_6awg_at_50a", "degC"),
                "degC",
                technical_source_id,
            ),
            "reference_ambient_temperature": q(
                number(snapshot, "reference_ambient_temperature", "degC"),
                "degC",
                technical_source_id,
            ),
            "wire_cross_section_tested": q(
                number(snapshot, "wire_cross_section_tested", "mm^2"),
                "mm^2",
                datasheet_source_id,
            ),
            "contact_series_tested": q(
                text(snapshot, "contact_series_tested", "1"), "1", datasheet_source_id
            ),
            "operating_temperature_min": q(
                number(snapshot, "operating_temperature_min", "degC"),
                "degC",
                datasheet_source_id,
            ),
            "operating_temperature_max": q(
                number(snapshot, "operating_temperature_max", "degC"),
                "degC",
                datasheet_source_id,
            ),
            "cycle_life_iec": q(
                number(snapshot, "mating_cycles_iec", "1"),
                "1",
                datasheet_source_id,
            ),
        },
        "interfaces": [
            {"id": "IF_IN", "kind": "electrical_dc", "properties": {}},
            {"id": "IF_OUT", "kind": "electrical_dc", "properties": {}},
        ],
    }

    source_record: dict[str, Any] = {
        "id": datasheet_source_id,
        "authority": "manufacturer",
        "format": "datasheet",
        "uri": upstream["uri"],
        "retrieved_at": snapshot["captured_at"],
        "license": "Anderson Power Products upstream terms; redistribution not asserted",
    }
    raw_digest = upstream.get("sha256")
    if raw_digest is not None:
        source_record["raw_digest"] = raw_digest
        part["assets"] = [
            {
                "id": "ASSET_DATASHEET",
                "kind": "datasheet",
                "uri": upstream["uri"],
                "digest": raw_digest,
                "source": datasheet_source_id,
                "license": "Anderson Power Products upstream terms; redistribution not asserted",
            }
        ]

    technical_source_record: dict[str, Any] = {
        "id": technical_source_id,
        "authority": "manufacturer",
        "format": "datasheet",
        "uri": technical_reference["uri"],
        "retrieved_at": snapshot["captured_at"],
        "license": "Anderson Power Products upstream terms; redistribution not asserted",
    }

    catalog = {
        "emes_catalog_version": "0.1",
        "catalog": {
            "id": "CAT_ANDERSON_SB50",
            "name": "Anderson Power Products SB50 wire-to-wire connector assembly",
            "description": (
                "Reviewed manufacturer SB50 IEC configuration normalized for EMES as "
                "a mated wire-to-wire assembly using 992G4 housings and 5900 contacts; "
                "50 A pack-side current is tied to the 6 AWG / 50 A / 23 degC-rise "
                "technical reference; direct 5900 wire size is 6 AWG."
            ),
        },
        "sources": [source_record, technical_source_record],
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
