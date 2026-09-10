#!/usr/bin/env python3
"""Regression checks for canonical EMES units, frames, and semantic digests."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import jsonschema

import power
import reference_loop
from validate import canonical_digest, load_json, normalize_document, validate_semantics


def expect_rejected(fn) -> None:
    try:
        fn()
    except Exception:
        return
    raise AssertionError("invalid canonical input unexpectedly accepted")


def by_id(items, item_id):
    return next(item for item in items if item["id"] == item_id)


def main() -> int:
    source = load_json(Path("examples/two-link-arm/mechanism.json"))
    baseline_digest = canonical_digest(source)

    millimetres = copy.deepcopy(source)
    p_l1 = by_id(millimetres["parameters"], "P_L1")
    p_l1["value"] = {"value": 350, "unit": "mm"}
    assert canonical_digest(millimetres) == baseline_digest

    normalized = normalize_document(millimetres)
    normalized_l1 = by_id(normalized["parameters"], "P_L1")
    assert normalized_l1["value"] == {"value": 0.35, "unit": "m"}
    validate_semantics(millimetres)
    assert reference_loop.parameter(millimetres, "P_L1", "m") == 0.35

    power_doc = load_json(Path("examples/power-pack/mechanism.json"))
    load = power_doc["load_cases"][0]["loads"][0]
    load["value"] = {"value": 0.5, "unit": "kW"}
    validate_semantics(power_doc)
    assert power.load_power_watts(power_doc, "LC_POWER", 36.0)[0] == 500.0

    wrong_dimension = copy.deepcopy(source)
    p_l1 = by_id(wrong_dimension["parameters"], "P_L1")
    p_l1["lower"] = {"value": 1.0, "unit": "kg"}
    expect_rejected(lambda: validate_semantics(wrong_dimension))

    unknown_unit = copy.deepcopy(source)
    by_id(unknown_unit["parameters"], "P_L1")["value"]["unit"] = "furlong"
    expect_rejected(lambda: validate_semantics(unknown_unit))

    missing_frame = copy.deepcopy(source)
    by_id(missing_frame["connections"], "J1").pop("frame")
    expect_rejected(lambda: validate_semantics(missing_frame))

    wrong_frame = copy.deepcopy(source)
    by_id(wrong_frame["connections"], "J1")["frame"] = "link2"
    expect_rejected(lambda: validate_semantics(wrong_frame))

    directional_load = copy.deepcopy(source)
    load = directional_load["load_cases"][0]["loads"][0]
    load["direction"] = [0, 0, -1]
    expect_rejected(lambda: validate_semantics(directional_load))

    schema = json.loads(Path("spec/emes-ir-v0.schema.json").read_text())
    validator = jsonschema.validators.validator_for(schema)(schema)
    expect_rejected(lambda: validator.validate(missing_frame))

    print("PASS canonical normalization and frame semantics")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
