#!/usr/bin/env python3
"""Normalize a hash-pinned MEAN WELL NPB-450-24NFC snapshot into an EMES catalog."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


def canonical_digest(value: Any) -> str:
    payload = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def fact(snapshot: dict[str, Any], name: str, unit: str) -> float:
    item = snapshot["facts"][name]
    if item["unit"] != unit:
        raise ValueError(f"{name}: expected {unit}, got {item['unit']}")
    return float(item["value"])


def q(value: Any, unit: str, source: str) -> dict[str, Any]:
    return {"value": value, "unit": unit, "source": source}


def normalize(snapshot_path: Path, output_path: Path) -> dict[str, Any]:
    snapshot = load(snapshot_path)
    upstream = snapshot["upstream"]["datasheet_pdf"]
    source_id = "SRC_MEANWELL_NPB450_24NFC"
    part = {
        "id": "MEANWELL_NPB_450_24NFC",
        "kind": "charger",
        "identity": {
            "manufacturer": snapshot["manufacturer"],
            "part_number": snapshot["part_number"],
        },
        "properties": {
            "charge_voltage_min": q(fact(snapshot, "charge_voltage_min", "V"), "V", source_id),
            "charge_voltage_max": q(fact(snapshot, "charge_voltage_max", "V"), "V", source_id),
            "max_output_current": q(fact(snapshot, "max_output_current", "A"), "A", source_id),
            "max_output_power": q(fact(snapshot, "max_output_power", "W"), "W", source_id),
            "working_temperature_min": q(fact(snapshot, "working_temperature_min", "degC"), "degC", source_id),
            "working_temperature_max": q(fact(snapshot, "working_temperature_max", "degC"), "degC", source_id),
        },
        "interfaces": [
            {"id": "IF_AC_IN", "kind": "electrical_ac", "properties": {}},
            {
                "id": "IF_CHARGE_OUT",
                "kind": "electrical_dc",
                "properties": {"role": q("charger_output", "1", source_id)},
            },
        ],
    }
    catalog = {
        "emes_catalog_version": "0.1",
        "catalog": {
            "id": "CAT_MEANWELL_NPB450_24NFC",
            "name": "MEAN WELL NPB-450-24NFC charger",
            "description": (
                "Hash-pinned manufacturer charger facts for static 10S Li-ion "
                "charge compatibility; no system charging approval."
            ),
        },
        "sources": [
            {
                "id": source_id,
                "authority": "manufacturer",
                "format": "datasheet",
                "uri": upstream["uri"],
                "retrieved_at": snapshot["captured_at"],
                "license": "MEAN WELL upstream terms; redistribution not asserted",
                "raw_digest": upstream["sha256"],
            }
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
