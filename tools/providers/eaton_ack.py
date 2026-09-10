#!/usr/bin/env python3
"""Eaton Bussmann ACK provider for a hash-pinned manufacturer datasheet."""

from __future__ import annotations

import argparse
import hashlib
import json
import urllib.request
from pathlib import Path
from typing import Any

USER_AGENT = "EMES/0.1 (+https://github.com/xmeta/emes)"


def fetch(url: str) -> bytes:
    request = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": "application/pdf,*/*"}
    )
    with urllib.request.urlopen(request, timeout=30) as response:
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
        raise RuntimeError("Eaton ACK datasheet URL did not return a PDF")
    actual = sha256(pdf)
    expected = upstream["sha256"]
    expected_bytes = int(upstream["bytes"])
    if actual != expected or len(pdf) != expected_bytes:
        raise RuntimeError(
            "Eaton ACK datasheet changed: "
            f"expected {expected} bytes={expected_bytes}, "
            f"got {actual} bytes={len(pdf)}"
        )
    return {
        "provider": "eaton_ack",
        "part_number": snapshot["part_number"],
        "datasheet_pdf": {"sha256": actual, "bytes": len(pdf)},
        "snapshot_version": snapshot["snapshot_version"],
    }


def fact(snapshot: dict[str, Any], name: str) -> dict[str, Any]:
    try:
        return snapshot["facts"][name]
    except KeyError as exc:
        raise ValueError(f"missing Eaton ACK snapshot fact: {name}") from exc


def number(snapshot: dict[str, Any], name: str, unit: str) -> float:
    item = fact(snapshot, name)
    if item.get("unit") != unit:
        raise ValueError(f"{name}: expected {unit}, got {item.get('unit')}")
    return float(item["value"])


def conditions(snapshot: dict[str, Any], name: str) -> list[dict[str, Any]]:
    return [dict(item) for item in fact(snapshot, name).get("conditions", [])]


def q(
    value: float | str,
    unit: str,
    source: str,
    *,
    rating_conditions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {"value": value, "unit": unit, "source": source}
    if rating_conditions:
        result["conditions"] = rating_conditions
    return result


def normalize(snapshot_path: Path, output_path: Path) -> dict[str, Any]:
    snapshot = load_snapshot(snapshot_path)
    upstream = snapshot["upstream"]["datasheet_pdf"]
    source_id = "SRC_EATON_ACK_TD1040"
    part = {
        "id": "EATON_ACK_40",
        "kind": "fuse",
        "identity": {
            "manufacturer": "Eaton Bussmann series",
            "part_number": "ACK-40",
        },
        "properties": {
            "rated_current": q(number(snapshot, "rated_current", "A"), "A", source_id),
            "rated_voltage_dc": q(
                number(snapshot, "rated_voltage_dc", "V"), "V", source_id
            ),
            "interrupt_rating": q(
                number(snapshot, "interrupt_rating", "A"), "A", source_id
            ),
            "opening_time_approx_200pct": q(
                number(snapshot, "opening_time_approx_200pct", "s"),
                "s",
                source_id,
                rating_conditions=conditions(snapshot, "opening_time_approx_200pct"),
            ),
            "opening_time_approx_300pct": q(
                number(snapshot, "opening_time_approx_300pct", "s"),
                "s",
                source_id,
                rating_conditions=conditions(snapshot, "opening_time_approx_300pct"),
            ),
            "opening_time_approx_500pct": q(
                number(snapshot, "opening_time_approx_500pct", "s"),
                "s",
                source_id,
                rating_conditions=conditions(snapshot, "opening_time_approx_500pct"),
            ),
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
                "license": "Eaton upstream terms; redistribution not asserted",
            }
        ],
    }
    catalog = {
        "emes_catalog_version": "0.1",
        "catalog": {
            "id": "CAT_EATON_ACK40",
            "name": "Eaton Bussmann ACK-40 fuse",
            "description": (
                "Manufacturer Technical Data 1040 normalized for EMES; sparse "
                "approximate opening-time facts are preserved but not converted into "
                "an inferred pulse-current curve."
            ),
        },
        "sources": [
            {
                "id": source_id,
                "authority": "manufacturer",
                "format": "datasheet",
                "uri": upstream["uri"],
                "retrieved_at": snapshot["captured_at"],
                "license": "Eaton upstream terms; redistribution not asserted",
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
        print("VALID eaton-ack-authoritative-snapshot")
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
