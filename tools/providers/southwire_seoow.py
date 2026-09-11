#!/usr/bin/env python3
"""Normalize a reviewed Southwire SEOOW cable snapshot into an EMES catalog."""

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


def fact(snapshot: dict[str, Any], name: str, unit: str) -> Any:
    item = snapshot["facts"][name]
    if item.get("unit") != unit:
        raise ValueError(f"{name}: expected {unit}, got {item.get('unit')}")
    return item["value"]


def q(value: Any, unit: str, source: str) -> dict[str, Any]:
    return {"value": value, "unit": unit, "source": source}


def normalize(snapshot_path: Path, output_path: Path) -> dict[str, Any]:
    snapshot = load_snapshot(snapshot_path)
    upstream = snapshot["upstream"]["spec_pdf"]
    source_id = "SRC_SOUTHWIRE_SEOOW_550431"

    part = {
        "id": "SOUTHWIRE_SEOOW_550431_10AWG_2C",
        "kind": "conductor",
        "identity": {
            "manufacturer": snapshot["manufacturer"],
            "part_number": snapshot["part_number"],
        },
        "properties": {
            "cci_part_number": q(snapshot["cci_part_number"], "1", source_id),
            "allowable_ampacity": q(float(fact(snapshot, "allowable_ampacity", "A")), "A", source_id),
            "rated_voltage": q(float(fact(snapshot, "rated_voltage", "V")), "V", source_id),
            "conductor_size": q(float(fact(snapshot, "conductor_size", "AWG")), "AWG", source_id),
            "conductor_count": q(float(fact(snapshot, "conductor_count", "1")), "1", source_id),
            "strand_count": q(float(fact(snapshot, "strand_count", "1")), "1", source_id),
            "operating_temperature_min": q(float(fact(snapshot, "operating_temperature_min", "degC")), "degC", source_id),
            "operating_temperature_max": q(float(fact(snapshot, "operating_temperature_max", "degC")), "degC", source_id),
            "outer_diameter": q(float(fact(snapshot, "outer_diameter", "mm")), "mm", source_id),
            "mass_per_length": q(float(fact(snapshot, "mass_per_length", "kg/km")), "kg/km", source_id),
            "ampacity_basis": q(str(fact(snapshot, "ampacity_basis", "1")), "1", source_id),
        },
        "interfaces": [
            {"id": "IF_IN", "kind": "electrical_dc", "properties": {}},
            {"id": "IF_OUT", "kind": "electrical_dc", "properties": {}},
        ],
        "assets": [
            {
                "id": "ASSET_SPEC_PDF",
                "kind": "datasheet",
                "uri": upstream["uri"],
                "digest": upstream["sha256"],
                "source": source_id,
                "license": "Southwire upstream terms; redistribution not asserted",
            }
        ],
    }

    catalog = {
        "emes_catalog_version": "0.1",
        "catalog": {
            "id": "CAT_SOUTHWIRE_SEOOW_550431",
            "name": "Southwire SEOOW stock 550431 10 AWG 2-conductor cable",
            "description": (
                "Manufacturer SPEC 70070 cable construction normalized for EMES. "
                "The published 30 A ampacity remains tied to this exact two-conductor "
                "SEOOW configuration and its stated NEC/CEC table basis."
            ),
        },
        "sources": [
            {
                "id": source_id,
                "authority": "manufacturer",
                "format": "datasheet",
                "uri": upstream["uri"],
                "retrieved_at": snapshot["captured_at"],
                "license": "Southwire upstream terms; redistribution not asserted",
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
