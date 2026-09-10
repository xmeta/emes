#!/usr/bin/env python3
"""maxon provider adapter using hash-pinned manufacturer PDF and CAD assets."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

USER_AGENT = "EMES/0.1 (+https://github.com/xmeta/emes)"


def fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()


def sha256(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def canonical_digest(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return sha256(payload)


def load_snapshot(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def verify_snapshot(path: Path) -> dict[str, Any]:
    snapshot = load_snapshot(path)
    upstream = snapshot["upstream"]

    pdf_expected = upstream["catalog_pdf"]["sha256"]
    pdf = fetch(upstream["catalog_pdf"]["uri"])
    if not pdf.startswith(b"%PDF"):
        raise RuntimeError("maxon catalog URL did not return a PDF")
    pdf_actual = sha256(pdf)
    if pdf_actual != pdf_expected:
        raise RuntimeError(f"maxon catalog PDF changed: expected {pdf_expected}, got {pdf_actual}")

    archive_expected = upstream["cad_archive"]["sha256"]
    archive = fetch(upstream["cad_archive"]["uri"])
    archive_actual = sha256(archive)
    if archive_actual != archive_expected:
        raise RuntimeError(f"maxon CAD archive changed: expected {archive_expected}, got {archive_actual}")

    expected_members = {item["name"]: item["sha256"] for item in upstream["cad_archive"]["members"]}
    actual_members: dict[str, str] = {}
    with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
        for info in bundle.infolist():
            if info.is_dir():
                continue
            actual_members[info.filename] = sha256(bundle.read(info.filename))
    if actual_members != expected_members:
        raise RuntimeError(f"maxon CAD members changed: expected {expected_members}, got {actual_members}")

    return {
        "provider": "maxon",
        "part_number": snapshot["part_number"],
        "catalog_pdf": {"sha256": pdf_actual, "bytes": len(pdf)},
        "cad_archive": {"sha256": archive_actual, "bytes": len(archive), "members": actual_members},
        "snapshot_version": snapshot["snapshot_version"],
    }


def fact(snapshot: dict[str, Any], name: str) -> dict[str, Any]:
    try:
        return snapshot["facts"][name]
    except KeyError as exc:
        raise ValueError(f"missing maxon snapshot fact: {name}") from exc


def number(snapshot: dict[str, Any], name: str, unit: str) -> float:
    item = fact(snapshot, name)
    if item.get("unit") != unit:
        raise ValueError(f"{name}: expected {unit}, got {item.get('unit')}")
    return float(item["value"])


def q(value: float, unit: str, source: str, *, tolerance: float | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {"value": value, "unit": unit, "source": source}
    if tolerance is not None:
        result["tolerance"] = tolerance
    return result


def normalize(snapshot_path: Path, output_path: Path) -> dict[str, Any]:
    snapshot = load_snapshot(snapshot_path)
    upstream = snapshot["upstream"]
    pdf_source = "SRC_MAXON_CATALOG_2025"
    cad_source = "SRC_MAXON_CAD"
    rpm = 2.0 * math.pi / 60.0

    part = {
        "id": "MAXON_496661",
        "kind": "motor",
        "identity": {"manufacturer": "maxon", "part_number": "496661"},
        "properties": {
            "mass": q(number(snapshot, "mass", "g") * 1e-3, "kg", pdf_source),
            "nominal_voltage": q(number(snapshot, "nominal_voltage", "V"), "V", pdf_source),
            "continuous_torque": q(number(snapshot, "continuous_torque", "mN*m") * 1e-3, "N*m", pdf_source),
            "stall_torque": q(number(snapshot, "stall_torque", "mN*m") * 1e-3, "N*m", pdf_source),
            "no_load_current": q(number(snapshot, "no_load_current", "mA") * 1e-3, "A", pdf_source),
            "continuous_current": q(number(snapshot, "continuous_current", "A"), "A", pdf_source),
            "stall_current": q(number(snapshot, "stall_current", "A"), "A", pdf_source),
            "no_load_speed": q(number(snapshot, "no_load_speed", "rpm") * rpm, "rad/s", pdf_source),
            "nominal_speed": q(number(snapshot, "nominal_speed", "rpm") * rpm, "rad/s", pdf_source),
            "max_speed": q(number(snapshot, "max_speed", "rpm") * rpm, "rad/s", pdf_source),
            "max_efficiency": q(number(snapshot, "max_efficiency", "%") * 0.01, "1", pdf_source),
            "terminal_resistance": q(number(snapshot, "terminal_resistance", "ohm"), "ohm", pdf_source),
            "terminal_inductance": q(number(snapshot, "terminal_inductance", "mH") * 1e-3, "H", pdf_source),
            "torque_constant": q(number(snapshot, "torque_constant", "mN*m/A") * 1e-3, "N*m/A", pdf_source),
            "speed_constant": q(number(snapshot, "speed_constant", "rpm/V") * rpm, "rad/s/V", pdf_source),
            "speed_torque_gradient": q(number(snapshot, "speed_torque_gradient", "rpm/mN*m") * rpm * 1000.0, "rad/s/N*m", pdf_source),
            "mechanical_time_constant": q(number(snapshot, "mechanical_time_constant", "ms") * 1e-3, "s", pdf_source),
            "rotor_inertia": q(number(snapshot, "rotor_inertia", "g*cm^2") * 1e-7, "kg*m^2", pdf_source),
            "number_of_pole_pairs": q(number(snapshot, "number_of_pole_pairs", "1"), "1", pdf_source),
            "number_of_phases": q(number(snapshot, "number_of_phases", "1"), "1", pdf_source),
            "body_diameter": q(number(snapshot, "body_diameter", "mm") * 1e-3, "m", pdf_source, tolerance=float(fact(snapshot, "body_diameter")["tolerance"]) * 1e-3),
            "body_length_max": q(number(snapshot, "body_length_max", "mm") * 1e-3, "m", pdf_source),
        },
        "interfaces": [
            {
                "id": "IF_OUTPUT_SHAFT",
                "kind": "mechanical_shaft",
                "properties": {
                    "diameter": q(number(snapshot, "output_shaft_diameter", "mm") * 1e-3, "m", pdf_source),
                    "length": q(number(snapshot, "output_shaft_length", "mm") * 1e-3, "m", pdf_source),
                },
            },
            {
                "id": "IF_MOUNTING_PILOT",
                "kind": "mechanical_mount",
                "properties": {
                    "pilot_diameter": q(number(snapshot, "pilot_diameter", "mm") * 1e-3, "m", pdf_source)
                },
            },
        ],
        "assets": [
            {
                "id": "ASSET_STEP",
                "kind": "stp",
                "uri": upstream["cad_archive"]["uri"],
                "digest": upstream["cad_archive"]["members"][0]["sha256"],
                "source": cad_source,
                "license": "maxon upstream download; redistribution not asserted",
            },
            {
                "id": "ASSET_DATASHEET",
                "kind": "datasheet",
                "uri": upstream["catalog_pdf"]["uri"],
                "digest": upstream["catalog_pdf"]["sha256"],
                "source": pdf_source,
                "license": "maxon upstream download; redistribution not asserted",
            },
        ],
    }

    catalog = {
        "emes_catalog_version": "0.1",
        "catalog": {
            "id": "CAT_MAXON_496661",
            "name": "maxon EC-i 40 100 W part 496661",
            "description": "Hash-pinned March 2025 manufacturer catalog snapshot normalized for EMES; upstream files are referenced, not redistributed.",
        },
        "sources": [
            {
                "id": pdf_source,
                "authority": "manufacturer",
                "format": "datasheet",
                "uri": upstream["catalog_pdf"]["uri"],
                "retrieved_at": snapshot["captured_at"],
                "license": "maxon upstream terms; redistribution not asserted",
                "raw_digest": upstream["catalog_pdf"]["sha256"],
            },
            {
                "id": cad_source,
                "authority": "manufacturer",
                "format": "other",
                "uri": upstream["cad_archive"]["uri"],
                "retrieved_at": snapshot["captured_at"],
                "license": "maxon upstream terms; redistribution not asserted",
                "raw_digest": upstream["cad_archive"]["sha256"],
            },
        ],
        "parts": [part],
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(catalog, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
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
        print("VALID maxon-authoritative-snapshot")
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
