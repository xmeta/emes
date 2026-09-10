#!/usr/bin/env python3
"""Authoritative maxon provider adapter for the first real EMES motor catalog."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import re
import urllib.request
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

PRODUCT_URL = "https://www.maxongroup.com/maxon/view/product/motor/ecmotor/EC-i/496661"
CATALOG_URL = "https://www.maxongroup.com/medias/sys_master/root/9406692261918/Cataloge-Page-EN-310.pdf"
CAD_URL = "https://www.maxongroup.com/medias/sys_master/root/8824729010206/496660-ec-i40-100w.zip"
USER_AGENT = "EMES/0.1 (+https://github.com/xmeta/emes)"


class TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=30) as response:
        return response.read()


def sha256(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def canonical_digest(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return sha256(payload)


def html_text(data: bytes) -> str:
    parser = TextExtractor()
    parser.feed(data.decode("utf-8", errors="replace"))
    return re.sub(r"\s+", " ", " ".join(parser.parts)).strip()


def verify_product_page(data: bytes) -> dict[str, bool]:
    text = html_text(data)
    patterns = {
        "part_number": r"Part number\s+496661",
        "nominal_voltage": r"Nominal voltage\s+36\s*V",
        "no_load_speed": r"No load speed\s+4550\s*rpm",
        "continuous_torque": r"Nominal torque \(max\. continuous torque\)\s+208\s*mNm",
        "rotor_inertia": r"Rotor inertia\s+44\s*gcm",
        "weight": r"Weight\s+390\s*g",
    }
    result = {name: bool(re.search(pattern, text, flags=re.IGNORECASE)) for name, pattern in patterns.items()}
    missing = [name for name, ok in result.items() if not ok]
    if missing:
        raise RuntimeError("maxon product page no longer matches expected facts: " + ", ".join(missing))
    return result


def probe() -> dict[str, Any]:
    product = fetch(PRODUCT_URL)
    catalog = fetch(CATALOG_URL)
    cad_zip = fetch(CAD_URL)
    if not catalog.startswith(b"%PDF"):
        raise RuntimeError("maxon catalog URL did not return PDF content")

    with zipfile.ZipFile(io.BytesIO(cad_zip)) as archive:
        members = []
        for info in archive.infolist():
            if info.is_dir():
                continue
            payload = archive.read(info.filename)
            members.append(
                {
                    "name": info.filename,
                    "bytes": len(payload),
                    "sha256": sha256(payload),
                    "is_step": info.filename.lower().endswith((".step", ".stp")),
                }
            )
    if not any(item["is_step"] for item in members):
        raise RuntimeError("official maxon CAD archive contained no STEP/STP member")

    return {
        "provider": "maxon",
        "part_number": "496661",
        "product": {"url": PRODUCT_URL, "bytes": len(product), "sha256": sha256(product)},
        "catalog_pdf": {"url": CATALOG_URL, "bytes": len(catalog), "sha256": sha256(catalog)},
        "cad_archive": {"url": CAD_URL, "bytes": len(cad_zip), "sha256": sha256(cad_zip), "members": members},
        "verified_facts": verify_product_page(product),
    }


def load_snapshot(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def verify_snapshot(path: Path) -> dict[str, Any]:
    expected = load_snapshot(path)
    actual = probe()
    if expected["part_number"] != actual["part_number"]:
        raise RuntimeError("provider part-number mismatch")

    upstream = expected["upstream"]
    stable_pairs = [
        ("catalog_pdf", upstream["catalog_pdf"]["sha256"], actual["catalog_pdf"]["sha256"]),
        ("cad_archive", upstream["cad_archive"]["sha256"], actual["cad_archive"]["sha256"]),
    ]
    for name, wanted, got in stable_pairs:
        if wanted != got:
            raise RuntimeError(f"maxon {name} changed: expected {wanted}, got {got}")

    expected_members = {item["name"]: item["sha256"] for item in upstream["cad_archive"]["members"]}
    actual_members = {item["name"]: item["sha256"] for item in actual["cad_archive"]["members"]}
    if expected_members != actual_members:
        raise RuntimeError(f"maxon CAD archive members changed: expected {expected_members}, got {actual_members}")
    return actual


def fact(snapshot: dict[str, Any], name: str) -> dict[str, Any]:
    try:
        return snapshot["facts"][name]
    except KeyError as exc:
        raise ValueError(f"missing maxon snapshot fact: {name}") from exc


def numeric_fact(snapshot: dict[str, Any], name: str, unit: str) -> float:
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
    s = load_snapshot(snapshot_path)
    product_source = "SRC_MAXON_PRODUCT"
    catalog_source = "SRC_MAXON_CATALOG_2025"
    cad_source = "SRC_MAXON_CAD_2017"
    snapshot_source = "SRC_EMES_MAXON_SNAPSHOT"
    upstream = s["upstream"]

    rpm_to_rad_s = 2.0 * math.pi / 60.0
    continuous_torque = numeric_fact(s, "continuous_torque", "mN*m") / 1000.0
    catalog = {
        "emes_catalog_version": "0.1",
        "catalog": {
            "id": "CAT_MAXON_496661",
            "name": "maxon EC-i 40 100 W part 496661",
            "description": "Authoritative-source snapshot normalized for EMES; upstream files are referenced, not redistributed.",
        },
        "sources": [
            {
                "id": product_source,
                "authority": "manufacturer",
                "format": "other",
                "uri": upstream["product"]["uri"],
                "retrieved_at": s["captured_at"],
                "license": "maxon upstream terms; redistribution not asserted",
                "raw_digest": upstream["product"]["sha256"],
            },
            {
                "id": catalog_source,
                "authority": "manufacturer",
                "format": "datasheet",
                "uri": upstream["catalog_pdf"]["uri"],
                "retrieved_at": s["captured_at"],
                "license": "maxon upstream terms; redistribution not asserted",
                "raw_digest": upstream["catalog_pdf"]["sha256"],
            },
            {
                "id": cad_source,
                "authority": "manufacturer",
                "format": "other",
                "uri": upstream["cad_archive"]["uri"],
                "retrieved_at": s["captured_at"],
                "license": "maxon upstream terms; redistribution not asserted",
                "raw_digest": upstream["cad_archive"]["sha256"],
            },
            {
                "id": snapshot_source,
                "authority": "derived",
                "format": "json",
                "path": snapshot_path.as_posix(),
                "retrieved_at": s["captured_at"],
                "license": "EMES factual metadata; upstream rights remain with maxon",
                "raw_digest": sha256(snapshot_path.read_bytes()),
            },
        ],
        "parts": [
            {
                "id": "MAXON_496661",
                "kind": "motor",
                "identity": {"manufacturer": "maxon", "part_number": "496661"},
                "properties": {
                    "mass": q(numeric_fact(s, "mass", "g") / 1000.0, "kg", product_source),
                    "nominal_voltage": q(numeric_fact(s, "nominal_voltage", "V"), "V", product_source),
                    "continuous_torque": q(continuous_torque, "N*m", product_source),
                    "stall_torque": q(numeric_fact(s, "stall_torque", "mN*m") / 1000.0, "N*m", product_source),
                    "continuous_current": q(numeric_fact(s, "continuous_current", "A"), "A", product_source),
                    "stall_current": q(numeric_fact(s, "stall_current", "A"), "A", product_source),
                    "no_load_speed": q(numeric_fact(s, "no_load_speed", "rpm") * rpm_to_rad_s, "rad/s", product_source),
                    "nominal_speed": q(numeric_fact(s, "nominal_speed", "rpm") * rpm_to_rad_s, "rad/s", product_source),
                    "max_speed": q(numeric_fact(s, "max_speed", "rpm") * rpm_to_rad_s, "rad/s", product_source),
                    "max_efficiency": q(numeric_fact(s, "max_efficiency", "%") / 100.0, "1", product_source),
                    "rotor_inertia": q(numeric_fact(s, "rotor_inertia", "g*cm^2") * 1e-7, "kg*m^2", product_source),
                    "torque_constant": q(numeric_fact(s, "torque_constant", "mN*m/A") / 1000.0, "N*m/A", product_source),
                    "speed_constant": q(numeric_fact(s, "speed_constant", "rpm/V") * rpm_to_rad_s, "rad/s/V", product_source),
                    "terminal_resistance": q(numeric_fact(s, "terminal_resistance", "ohm"), "ohm", product_source),
                    "terminal_inductance": q(numeric_fact(s, "terminal_inductance", "mH") / 1000.0, "H", product_source),
                    "mechanical_time_constant": q(numeric_fact(s, "mechanical_time_constant", "ms") / 1000.0, "s", product_source),
                    "body_diameter": q(numeric_fact(s, "body_diameter", "mm") / 1000.0, "m", catalog_source, tolerance=float(fact(s, "body_diameter")["tolerance"]) / 1000.0),
                    "body_length_max": q(numeric_fact(s, "body_length_max", "mm") / 1000.0, "m", catalog_source),
                },
                "interfaces": [
                    {
                        "id": "IF_OUTPUT_SHAFT",
                        "kind": "mechanical_shaft",
                        "properties": {
                            "diameter": q(numeric_fact(s, "output_shaft_diameter", "mm") / 1000.0, "m", catalog_source),
                            "length": q(numeric_fact(s, "output_shaft_length", "mm") / 1000.0, "m", catalog_source),
                        },
                    },
                    {
                        "id": "IF_MOUNTING_PILOT",
                        "kind": "mechanical_mount",
                        "properties": {
                            "pilot_diameter": q(numeric_fact(s, "pilot_diameter", "mm") / 1000.0, "m", catalog_source),
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
                        "source": catalog_source,
                        "license": "maxon upstream download; redistribution not asserted",
                    },
                ],
            }
        ],
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(catalog, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return catalog


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("probe")
    verify = sub.add_parser("verify-snapshot")
    verify.add_argument("snapshot", type=Path)
    normal = sub.add_parser("normalize")
    normal.add_argument("snapshot", type=Path)
    normal.add_argument("output", type=Path)
    args = parser.parse_args()

    if args.command == "probe":
        print(json.dumps(probe(), indent=2, sort_keys=True))
        return 0
    if args.command == "verify-snapshot":
        result = verify_snapshot(args.snapshot)
        print(f"VALID maxon-snapshot part={result['part_number']}")
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
