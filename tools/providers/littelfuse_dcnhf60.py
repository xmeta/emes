#!/usr/bin/env python3
"""Littelfuse DCNHF60 contactor provider for a reviewed manufacturer snapshot."""

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
        raise ValueError(f"missing Littelfuse DCNHF60 snapshot fact: {name}") from exc


def number(snapshot: dict[str, Any], name: str, unit: str) -> float:
    item = fact(snapshot, name)
    if item.get("unit") != unit:
        raise ValueError(f"{name}: expected {unit}, got {item.get('unit')}")
    return float(item["value"])


def q(value: float, unit: str, source: str) -> dict[str, Any]:
    return {"value": value, "unit": unit, "source": source}


def source_record(snapshot: dict[str, Any], key: str, source_id: str) -> dict[str, Any]:
    upstream = snapshot["upstream"][key]
    record: dict[str, Any] = {
        "id": source_id,
        "authority": "manufacturer",
        "format": "datasheet",
        "uri": upstream["uri"],
        "retrieved_at": snapshot["captured_at"],
        "license": "Littelfuse upstream terms; redistribution not asserted",
    }
    raw_digest = upstream.get("sha256")
    if raw_digest is not None:
        record["raw_digest"] = raw_digest
    return record


def normalize(snapshot_path: Path, output_path: Path) -> dict[str, Any]:
    snapshot = load_snapshot(snapshot_path)
    datasheet_id = "SRC_LITTELFUSE_DCNHF60_DATASHEET"
    outline_id = "SRC_LITTELFUSE_DCNHF60_OUTLINE"

    part = {
        "id": "LITTELFUSE_DCNHF60NG12_F",
        "kind": "contactor",
        "identity": {
            "manufacturer": snapshot["manufacturer"],
            "part_number": snapshot["part_number"],
        },
        "properties": {
            "continuous_current": q(
                number(snapshot, "continuous_current", "A"), "A", datasheet_id
            ),
            "operating_voltage_min_dc": q(
                number(snapshot, "operating_voltage_min_dc", "V"),
                "V",
                datasheet_id,
            ),
            "operating_voltage_max_dc": q(
                number(snapshot, "operating_voltage_max_dc", "V"),
                "V",
                datasheet_id,
            ),
            "mass": q(number(snapshot, "mass", "kg"), "kg", outline_id),
        },
        "interfaces": [
            {"id": "IF_IN", "kind": "electrical_dc", "properties": {}},
            {"id": "IF_OUT", "kind": "electrical_dc", "properties": {}},
        ],
    }

    catalog = {
        "emes_catalog_version": "0.1",
        "catalog": {
            "id": "CAT_LITTELFUSE_DCNHF60",
            "name": "Littelfuse DCNHF60NG12-F contactor",
            "description": (
                "Reviewed manufacturer DCNHF60 main-contact facts normalized for EMES: "
                "60 A continuous current, 12-1000 V DC operating range, and about "
                "160 g mass. Switching life, pulse/breaking capability, coil suitability, "
                "and pre-charge behavior are intentionally not inferred."
            ),
        },
        "sources": [
            source_record(snapshot, "datasheet_pdf", datasheet_id),
            source_record(snapshot, "outline_pdf", outline_id),
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
