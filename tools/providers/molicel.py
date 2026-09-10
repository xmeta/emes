#!/usr/bin/env python3
"""Molicel provider adapter for hash-pinned INR-21700-P45B sources."""

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
    expected = upstream["sha256"]
    pdf = fetch(upstream["uri"])
    if not pdf.startswith(b"%PDF"):
        raise RuntimeError("Molicel datasheet URL did not return a PDF")
    actual = sha256(pdf)
    if actual != expected:
        raise RuntimeError(
            f"Molicel datasheet changed: expected {expected}, got {actual}; bytes={len(pdf)}"
        )
    return {
        "provider": "molicel",
        "part_number": snapshot["part_number"],
        "datasheet_pdf": {"sha256": actual, "bytes": len(pdf)},
        "snapshot_version": snapshot["snapshot_version"],
    }


def fact(snapshot: dict[str, Any], name: str) -> dict[str, Any]:
    try:
        return snapshot["facts"][name]
    except KeyError as exc:
        raise ValueError(f"missing Molicel snapshot fact: {name}") from exc


def number(snapshot: dict[str, Any], name: str, unit: str) -> float:
    item = fact(snapshot, name)
    if item.get("unit") != unit:
        raise ValueError(f"{name}: expected {unit}, got {item.get('unit')}")
    return float(item["value"])


def scalar(snapshot: dict[str, Any], name: str) -> str:
    return str(fact(snapshot, name)["value"])


def conditions(snapshot: dict[str, Any], name: str) -> list[dict[str, Any]]:
    return [dict(item) for item in fact(snapshot, name).get("conditions", [])]


def normalized_rating_conditions(
    snapshot: dict[str, Any], name: str
) -> list[dict[str, Any]]:
    raw = conditions(snapshot, name)
    if name not in {"continuous_discharge_current", "maximum_charge_current"}:
        return raw

    if len(raw) != 1:
        raise ValueError(f"{name}: expected exactly one manufacturer cutoff condition")
    cutoff = raw[0]
    expected_temperature = 80.0 if name == "continuous_discharge_current" else 70.0
    if (
        cutoff.get("parameter") != "cutoff_temperature"
        or cutoff.get("op") != "=="
        or cutoff.get("unit") != "degC"
        or float(cutoff.get("value")) != expected_temperature
    ):
        raise ValueError(f"{name}: unexpected manufacturer cutoff condition {cutoff}")

    control_parameter = (
        "cell_discharge_cutoff_temperature"
        if name == "continuous_discharge_current"
        else "cell_charge_cutoff_temperature"
    )
    return [
        {
            "parameter": control_parameter,
            "op": "<=",
            "value": expected_temperature,
            "unit": "degC",
        }
    ]


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
    datasheet = snapshot["upstream"]["datasheet_pdf"]
    performance_page = snapshot["upstream"]["performance_page"]
    datasheet_source = "SRC_MOLICEL_P45B_DATASHEET_V1_4"
    performance_source = "SRC_MOLICEL_P45B_PERFORMANCE_PAGE"

    part = {
        "id": "MOLICEL_INR_21700_P45B",
        "kind": "battery_cell",
        "identity": {"manufacturer": "Molicel", "part_number": "INR-21700-P45B"},
        "properties": {
            "form_factor": q(scalar(snapshot, "form_factor"), "1", datasheet_source),
            "chemistry_family": q(scalar(snapshot, "chemistry_family"), "1", datasheet_source),
            "nominal_capacity": q(
                number(snapshot, "nominal_capacity_typical", "A*h"), "A*h", datasheet_source
            ),
            "minimum_capacity": q(
                number(snapshot, "nominal_capacity_minimum", "A*h"), "A*h", datasheet_source
            ),
            "nominal_energy": q(
                number(snapshot, "nominal_energy_typical", "W*h"), "W*h", datasheet_source
            ),
            "minimum_energy": q(
                number(snapshot, "nominal_energy_minimum", "W*h"), "W*h", datasheet_source
            ),
            "nominal_voltage": q(number(snapshot, "nominal_voltage", "V"), "V", datasheet_source),
            "max_charge_voltage": q(
                number(snapshot, "max_charge_voltage", "V"), "V", datasheet_source
            ),
            "min_discharge_voltage": q(
                number(snapshot, "min_discharge_voltage", "V"), "V", datasheet_source
            ),
            "standard_charge_current": q(
                number(snapshot, "standard_charge_current", "A"), "A", datasheet_source
            ),
            "maximum_charge_current": q(
                number(snapshot, "maximum_charge_current", "A"),
                "A",
                datasheet_source,
                rating_conditions=normalized_rating_conditions(
                    snapshot, "maximum_charge_current"
                ),
            ),
            "continuous_discharge_current": q(
                number(snapshot, "continuous_discharge_current", "A"),
                "A",
                datasheet_source,
                rating_conditions=normalized_rating_conditions(
                    snapshot, "continuous_discharge_current"
                ),
            ),
            "charge_temperature_min": q(
                number(snapshot, "charge_temperature_min", "degC"), "degC", datasheet_source
            ),
            "charge_temperature_max": q(
                number(snapshot, "charge_temperature_max", "degC"), "degC", datasheet_source
            ),
            "discharge_temperature_min": q(
                number(snapshot, "discharge_temperature_min", "degC"), "degC", datasheet_source
            ),
            "discharge_temperature_max": q(
                number(snapshot, "discharge_temperature_max", "degC"), "degC", datasheet_source
            ),
            "ac_impedance_typical": q(
                number(snapshot, "ac_impedance_typical", "ohm"),
                "ohm",
                datasheet_source,
                rating_conditions=conditions(snapshot, "ac_impedance_typical"),
            ),
            "dc_impedance_typical": q(
                number(snapshot, "dc_impedance_typical", "ohm"),
                "ohm",
                datasheet_source,
                rating_conditions=conditions(snapshot, "dc_impedance_typical"),
            ),
            "power_output_10s_soc50": q(
                number(snapshot, "power_output_10s_soc50", "W"),
                "W",
                performance_source,
                rating_conditions=conditions(snapshot, "power_output_10s_soc50"),
            ),
            "power_output_10s_soc90": q(
                number(snapshot, "power_output_10s_soc90", "W"),
                "W",
                performance_source,
                rating_conditions=conditions(snapshot, "power_output_10s_soc90"),
            ),
            "diameter_max": q(
                number(snapshot, "diameter_max", "mm") * 1e-3, "m", datasheet_source
            ),
            "height_max": q(
                number(snapshot, "height_max", "mm") * 1e-3, "m", datasheet_source
            ),
            "mass": q(number(snapshot, "mass_max", "g") * 1e-3, "kg", datasheet_source),
        },
        "interfaces": [
            {
                "id": "IF_POSITIVE_TERMINAL",
                "kind": "electrical_terminal",
                "properties": {},
            },
            {
                "id": "IF_NEGATIVE_TERMINAL",
                "kind": "electrical_terminal",
                "properties": {},
            },
        ],
        "assets": [
            {
                "id": "ASSET_DATASHEET",
                "kind": "datasheet",
                "uri": datasheet["uri"],
                "digest": datasheet["sha256"],
                "source": datasheet_source,
                "license": "Molicel upstream terms; redistribution not asserted",
            },
            {
                "id": "ASSET_PERFORMANCE_PAGE",
                "kind": "other",
                "uri": performance_page["uri"],
                "digest": performance_page["sha256"],
                "source": performance_source,
                "license": "Molicel upstream terms; redistribution not asserted",
            },
        ],
    }

    catalog = {
        "emes_catalog_version": "0.1",
        "catalog": {
            "id": "CAT_MOLICEL_P45B",
            "name": "Molicel INR-21700-P45B",
            "description": (
                "Manufacturer Product Data Sheet v1.4 plus hash-pinned official "
                "P45B performance-page points normalized for EMES; conditions are "
                "preserved and upstream bytes are referenced, not redistributed."
            ),
        },
        "sources": [
            {
                "id": datasheet_source,
                "authority": "manufacturer",
                "format": "datasheet",
                "uri": datasheet["uri"],
                "retrieved_at": snapshot["captured_at"],
                "license": "Molicel upstream terms; redistribution not asserted",
                "raw_digest": datasheet["sha256"],
            },
            {
                "id": performance_source,
                "authority": "manufacturer",
                "format": "other",
                "uri": performance_page["uri"],
                "retrieved_at": snapshot["captured_at"],
                "license": "Molicel upstream terms; redistribution not asserted",
                "raw_digest": performance_page["sha256"],
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
    sub = parser.add_subparsers(dest="command", required=True)
    verify = sub.add_parser("verify-snapshot")
    verify.add_argument("snapshot", type=Path)
    normal = sub.add_parser("normalize")
    normal.add_argument("snapshot", type=Path)
    normal.add_argument("output", type=Path)
    args = parser.parse_args()

    if args.command == "verify-snapshot":
        result = verify_snapshot(args.snapshot)
        print("VALID molicel-authoritative-snapshot")
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
