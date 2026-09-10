#!/usr/bin/env python3
"""EMES catalog snapshot validation, import, and binding utilities."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
from typing import Any

import jsonschema


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def canonical_digest(value: Any) -> str:
    payload = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def file_digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _property_sources(properties: dict[str, Any]) -> set[str]:
    return {
        value["source"]
        for value in properties.values()
        if isinstance(value, dict) and "source" in value
    }


def validate_catalog(
    catalog: dict[str, Any],
    schema: dict[str, Any],
    *,
    root: Path | None = None,
) -> None:
    validator_cls = jsonschema.validators.validator_for(schema)
    validator_cls.check_schema(schema)
    validator_cls(schema).validate(catalog)

    sources = catalog["sources"]
    source_ids = {item["id"] for item in sources}
    if len(source_ids) != len(sources):
        raise ValueError("duplicate catalog source id")

    parts = catalog["parts"]
    part_ids = {item["id"] for item in parts}
    if len(part_ids) != len(parts):
        raise ValueError("duplicate catalog part id")

    used_sources: set[str] = set()
    for part in parts:
        used_sources |= _property_sources(part.get("properties", {}))
        for interface in part.get("interfaces", []):
            used_sources |= _property_sources(interface.get("properties", {}))
        for curve in part.get("performance", {}).get("curves", []):
            if "source" in curve:
                used_sources.add(curve["source"])
            if len(curve["x"]["values"]) != len(curve["y"]["values"]):
                raise ValueError(
                    f"{part['id']}/{curve['id']}: curve axis lengths differ"
                )
            if not all(
                math.isfinite(value)
                for value in curve["x"]["values"] + curve["y"]["values"]
            ):
                raise ValueError(f"{part['id']}/{curve['id']}: non-finite curve value")

    missing = used_sources - source_ids
    if missing:
        raise ValueError("unknown catalog source refs: " + ", ".join(sorted(missing)))

    if root is not None:
        for source in sources:
            if "path" not in source or "raw_digest" not in source:
                continue
            raw_path = root / source["path"]
            if not raw_path.is_file():
                raise ValueError(f"{source['id']}: missing raw source {raw_path}")
            actual = file_digest(raw_path)
            if actual != source["raw_digest"]:
                raise ValueError(
                    f"{source['id']}: raw source digest mismatch: {actual}"
                )


def part_by_id(catalog: dict[str, Any], part_id: str) -> dict[str, Any]:
    for part in catalog["parts"]:
        if part["id"] == part_id:
            return part
    raise ValueError(f"catalog part not found: {part_id}")


def resolve_catalog_parts(
    mechanism: dict[str, Any],
    *,
    repo_root: Path,
    catalog_schema_path: Path,
) -> dict[str, dict[str, Any]]:
    schema = load_json(catalog_schema_path)
    bindings = mechanism.get("extensions", {}).get("org.emes.catalogs", [])
    by_binding_id: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}

    for binding in bindings:
        path = repo_root / binding["path"]
        catalog = load_json(path)
        validate_catalog(catalog, schema, root=repo_root)
        actual = canonical_digest(catalog)
        if actual != binding["digest"]:
            raise ValueError(
                f"{binding['id']}: catalog digest mismatch: expected "
                f"{binding['digest']} got {actual}"
            )
        by_binding_id[binding["id"]] = (binding, catalog)

    resolved: dict[str, dict[str, Any]] = {}
    for component in mechanism.get("components", []):
        ref = component.get("metadata", {}).get("org.emes.catalog_ref")
        if ref is None:
            continue
        if ref["catalog"] not in by_binding_id:
            raise ValueError(
                f"{component['id']}: unknown catalog binding {ref['catalog']}"
            )
        _, catalog = by_binding_id[ref["catalog"]]
        part = part_by_id(catalog, ref["part"])
        actual = canonical_digest(part)
        if actual != ref["digest"]:
            raise ValueError(
                f"{component['id']}: part digest mismatch: expected "
                f"{ref['digest']} got {actual}"
            )
        resolved[component["id"]] = part
    return resolved


def property_number(part: dict[str, Any], name: str, unit: str) -> float:
    try:
        value = part["properties"][name]
    except KeyError as exc:
        raise ValueError(f"{part['id']}: missing property {name}") from exc
    if value["unit"] != unit:
        raise ValueError(
            f"{part['id']}.{name}: expected unit {unit}, got {value['unit']}"
        )
    if not isinstance(value["value"], (int, float)):
        raise ValueError(f"{part['id']}.{name}: property is not numeric")
    return float(value["value"])


def _q(value: float, unit: str, source: str) -> dict[str, Any]:
    return {"value": value, "unit": unit, "source": source}


def import_motor_csv(input_path: Path, output_path: Path) -> dict[str, Any]:
    source_id = "SRC_REFERENCE_CSV"
    parts: list[dict[str, Any]] = []
    with input_path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            rpm = float(row["no_load_speed_rpm"])
            speed = rpm * 2.0 * math.pi / 60.0
            peak = float(row["peak_torque_nm"])
            parts.append(
                {
                    "id": row["part_id"],
                    "kind": "motor",
                    "identity": {
                        "manufacturer": row["manufacturer"],
                        "part_number": row["part_number"],
                    },
                    "properties": {
                        "mass": _q(float(row["mass_kg"]), "kg", source_id),
                        "nominal_voltage": _q(float(row["nominal_voltage_v"]), "V", source_id),
                        "continuous_torque": _q(float(row["continuous_torque_nm"]), "N*m", source_id),
                        "peak_torque": _q(peak, "N*m", source_id),
                        "no_load_speed": _q(speed, "rad/s", source_id),
                        "rotor_inertia": _q(float(row["rotor_inertia_kg_m2"]), "kg*m^2", source_id),
                        "torque_constant": _q(float(row["torque_constant_nm_a"]), "N*m/A", source_id),
                        "body_diameter": _q(float(row["body_diameter_m"]), "m", source_id),
                        "body_length": _q(float(row["body_length_m"]), "m", source_id),
                        "shaft_diameter": _q(float(row["shaft_diameter_m"]), "m", source_id),
                        "shaft_length": _q(float(row["shaft_length_m"]), "m", source_id),
                    },
                    "interfaces": [
                        {
                            "id": "IF_MOUNT",
                            "kind": "mechanical_mount",
                            "properties": {
                                "pilot_diameter": _q(float(row["pilot_diameter_m"]), "m", source_id),
                                "bolt_circle_diameter": _q(float(row["bolt_circle_diameter_m"]), "m", source_id),
                                "hole_count": _q(float(row["hole_count"]), "1", source_id),
                            },
                        },
                        {
                            "id": "IF_SHAFT",
                            "kind": "mechanical_shaft",
                            "properties": {
                                "diameter": _q(float(row["shaft_diameter_m"]), "m", source_id),
                                "length": _q(float(row["shaft_length_m"]), "m", source_id),
                            },
                        },
                    ],
                    "performance": {
                        "curves": [
                            {
                                "id": "CURVE_TORQUE_SPEED",
                                "x": {
                                    "quantity": "angular_speed",
                                    "unit": "rad/s",
                                    "values": [0.0, speed / 2.0, speed],
                                },
                                "y": {
                                    "quantity": "torque",
                                    "unit": "N*m",
                                    "values": [peak, peak / 2.0, 0.0],
                                },
                                "source": source_id,
                            }
                        ]
                    },
                }
            )

    catalog = {
        "emes_catalog_version": "0.1",
        "catalog": {
            "id": "CAT_REFERENCE_MOTORS",
            "name": "EMES reference motor fixture",
            "description": (
                "Synthetic motor data used only to exercise catalog import "
                "and binding semantics."
            ),
        },
        "sources": [
            {
                "id": source_id,
                "authority": "synthetic",
                "format": "csv",
                "path": input_path.as_posix(),
                "license": "CC0-1.0",
                "raw_digest": file_digest(input_path),
            }
        ],
        "parts": parts,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(catalog, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return catalog


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    validate = sub.add_parser("validate")
    validate.add_argument("catalog", type=Path)
    validate.add_argument(
        "--schema", type=Path, default=Path("spec/emes-catalog-v0.schema.json")
    )
    validate.add_argument("--repo-root", type=Path, default=Path("."))

    imp = sub.add_parser("import-motor-csv")
    imp.add_argument("input", type=Path)
    imp.add_argument("output", type=Path)
    imp.add_argument(
        "--schema", type=Path, default=Path("spec/emes-catalog-v0.schema.json")
    )

    args = parser.parse_args()
    if args.command == "validate":
        catalog = load_json(args.catalog)
        validate_catalog(catalog, load_json(args.schema), root=args.repo_root)
        print(f"VALID {args.catalog}")
        print(f"catalog_digest={canonical_digest(catalog)}")
        return 0

    catalog = import_motor_csv(args.input, args.output)
    validate_catalog(catalog, load_json(args.schema), root=Path("."))
    print(f"IMPORTED {args.input} -> {args.output}")
    print(f"catalog_digest={canonical_digest(catalog)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
