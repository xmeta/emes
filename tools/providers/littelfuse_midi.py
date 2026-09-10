#!/usr/bin/env python3
"""Littelfuse MIDI HP 70V provider for a hash-pinned manufacturer datasheet."""

from __future__ import annotations

import argparse
import hashlib
import json
import urllib.request
from pathlib import Path
from typing import Any

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/152.0.0.0 Safari/537.36"
)


def fetch(url: str) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/pdf,application/octet-stream;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://www.littelfuse.com/",
        },
    )
    with urllib.request.urlopen(request, timeout=45) as response:
        return response.read()


def sha256(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def canonical_digest(value: Any) -> str:
    payload = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return sha256(payload)


def load_snapshot(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def verify_snapshot(path: Path) -> dict[str, Any]:
    snapshot = load_snapshot(path)
    upstream = snapshot["upstream"]["datasheet_pdf"]
    pdf = fetch(upstream["uri"])
    if not pdf.startswith(b"%PDF"):
        raise RuntimeError("Littelfuse MIDI datasheet URL did not return a PDF")
    actual = sha256(pdf)
    expected = upstream["sha256"]
    expected_bytes = int(upstream["bytes"])
    if actual != expected or len(pdf) != expected_bytes:
        raise RuntimeError(
            "Littelfuse MIDI datasheet changed: "
            f"expected {expected} bytes={expected_bytes}, "
            f"got {actual} bytes={len(pdf)}"
        )
    return {
        "provider": "littelfuse_midi",
        "part_number": snapshot["part_number"],
        "datasheet_pdf": {"sha256": actual, "bytes": len(pdf)},
        "snapshot_version": snapshot["snapshot_version"],
    }


def fact(snapshot: dict[str, Any], name: str) -> dict[str, Any]:
    try:
        return snapshot["facts"][name]
    except KeyError as exc:
        raise ValueError(f"missing Littelfuse MIDI snapshot fact: {name}") from exc


def number(snapshot: dict[str, Any], name: str, unit: str) -> float:
    item = fact(snapshot, name)
    if item.get("unit") != unit:
        raise ValueError(f"{name}: expected {unit}, got {item.get('unit')}")
    return float(item["value"])


def q(
    value: float | str,
    unit: str,
    source: str,
    *,
    conditions: list[dict[str, Any]] | None = None,
    tolerance: float | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {"value": value, "unit": unit, "source": source}
    if conditions:
        result["conditions"] = [dict(item) for item in conditions]
    if tolerance is not None:
        result["tolerance"] = tolerance
    return result


def normalize(snapshot_path: Path, output_path: Path) -> dict[str, Any]:
    snapshot = load_snapshot(snapshot_path)
    upstream = snapshot["upstream"]["datasheet_pdf"]
    source_id = "SRC_LITTELFUSE_MIDI_HP_70V"

    derated_20c = fact(snapshot, "max_allowed_current_20c")
    mounting_torque = fact(snapshot, "mounting_torque_m6")
    part = {
        "id": "LITTELFUSE_4998040_M_M6",
        "kind": "fuse",
        "identity": {
            "manufacturer": "Littelfuse",
            "part_number": snapshot["part_number"],
        },
        "properties": {
            "rated_current": q(number(snapshot, "rated_current", "A"), "A", source_id),
            "rated_voltage_dc": q(
                number(snapshot, "rated_voltage_dc", "V"), "V", source_id
            ),
            "interrupt_rating": q(
                number(snapshot, "interrupt_rating", "A"), "A", source_id
            ),
            "typical_voltage_drop": q(
                number(snapshot, "typical_voltage_drop", "V"), "V", source_id
            ),
            "typical_cold_resistance": q(
                number(snapshot, "typical_cold_resistance", "ohm"), "ohm", source_id
            ),
            "typical_i2t": q(
                number(snapshot, "typical_i2t", "A^2*s"), "A^2*s", source_id
            ),
            "max_allowed_current_20c": q(
                number(snapshot, "max_allowed_current_20c", "A"),
                "A",
                source_id,
                conditions=derated_20c.get("conditions", []),
            ),
            "mounting_torque_m6": q(
                number(snapshot, "mounting_torque_m6", "N*m"),
                "N*m",
                source_id,
                tolerance=float(mounting_torque["tolerance"]),
            ),
            "mass": q(number(snapshot, "typical_mass", "kg"), "kg", source_id),
        },
        "interfaces": [
            {"id": "IF_IN", "kind": "electrical_dc", "properties": {}},
            {"id": "IF_OUT", "kind": "electrical_dc", "properties": {}},
        ],
        "assets": [
            {
                "id": "ASSET_DATASHEET",
                "kind": "datasheet",
                "uri": upstream["uri"],
                "digest": upstream["sha256"],
                "source": source_id,
                "license": "Littelfuse upstream terms; redistribution not asserted",
            }
        ],
    }
    catalog = {
        "emes_catalog_version": "0.1",
        "catalog": {
            "id": "CAT_LITTELFUSE_MIDI70V",
            "name": "Littelfuse MIDI HP 70V 40A fuse",
            "description": (
                "Manufacturer MIDI High Performance 70V-SF36 datasheet normalized "
                "for EMES; nominal ratings and the explicit 20 C derating point are "
                "kept distinct, and no pulse ampacity is inferred from I2t/time-current data."
            ),
        },
        "sources": [
            {
                "id": source_id,
                "authority": "manufacturer",
                "format": "datasheet",
                "uri": upstream["uri"],
                "retrieved_at": snapshot["captured_at"],
                "license": "Littelfuse upstream terms; redistribution not asserted",
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
    sub = parser.add_subparsers(dest="command", required=True)
    verify = sub.add_parser("verify-snapshot")
    verify.add_argument("snapshot", type=Path)
    normal = sub.add_parser("normalize")
    normal.add_argument("snapshot", type=Path)
    normal.add_argument("output", type=Path)
    args = parser.parse_args()

    if args.command == "verify-snapshot":
        result = verify_snapshot(args.snapshot)
        print("VALID littelfuse-midi-authoritative-snapshot")
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    if args.command == "normalize":
        catalog = normalize(args.snapshot, args.output)
        print(f"NORMALIZED {args.snapshot} -> {args.output}")
        print(f"catalog_digest={canonical_digest(catalog)}")
        return 0
    raise AssertionError(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
