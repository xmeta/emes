#!/usr/bin/env python3
"""Compose catalog-backed pack-side connector limits with known battery-path evidence."""

from __future__ import annotations

import argparse
import json
import math
import tempfile
from pathlib import Path
from typing import Any

from catalog import canonical_digest as catalog_digest
from catalog import property_number, resolve_catalog_parts
from validate import canonical_digest, load_json


def evidence_metric(evidence: dict[str, Any], metric_id: str, unit: str) -> float:
    matches = [item for item in evidence.get("metrics", []) if item.get("id") == metric_id]
    if len(matches) != 1:
        raise ValueError(f"evidence must provide exactly one {metric_id}")
    item = matches[0]
    if item.get("unit") != unit:
        raise ValueError(f"{metric_id}: expected {unit}, got {item.get('unit')}")
    value = float(item["value"])
    if not math.isfinite(value):
        raise ValueError(f"{metric_id}: non-finite value")
    return value


def matching_power_path(document: dict[str, Any], path_id: str) -> dict[str, Any]:
    extension = document.get("extensions", {}).get("org.emes.power", {})
    paths = extension.get("power_paths", []) if isinstance(extension, dict) else []
    matches = [item for item in paths if item.get("id") == path_id]
    if len(matches) != 1:
        raise ValueError(f"mechanism must provide exactly one power path {path_id}")
    return matches[0]


def positive_ratio(limit: float, demand: float, context: str) -> float:
    if not math.isfinite(limit) or not math.isfinite(demand):
        raise ValueError(f"{context}: non-finite limit or demand")
    if limit <= 0 or demand <= 0:
        raise ValueError(f"{context}: limit and demand must both be positive")
    return limit / demand


def evaluate(
    document: dict[str, Any],
    power_evidence: dict[str, Any],
    known_evidence: dict[str, Any],
    repo_root: Path,
) -> dict[str, Any]:
    design_digest = canonical_digest(document)
    if power_evidence.get("design_digest") != design_digest:
        raise ValueError("power evidence design digest does not match mechanism")
    if known_evidence.get("design_digest") != design_digest:
        raise ValueError("known-envelope evidence design digest does not match mechanism")
    if known_evidence.get("input_power_evidence_digest") != canonical_digest(power_evidence):
        raise ValueError("known-envelope evidence is not derived from the supplied power evidence")

    topology = power_evidence.get("power_topology", {})
    if topology.get("source_kind") != "battery_pack":
        raise ValueError("connector-envelope analysis requires battery-pack power evidence")
    path_id = topology.get("path_id")
    if not isinstance(path_id, str):
        raise ValueError("power topology must expose a path id")
    path = matching_power_path(document, path_id)
    connector_ids = path.get("pack_connector_components", [])
    if not isinstance(connector_ids, list) or not connector_ids:
        raise ValueError("power path must declare at least one pack_connector_components entry")
    if len(set(connector_ids)) != len(connector_ids):
        raise ValueError("power path contains duplicate pack-side connector components")

    selected = resolve_catalog_parts(
        document,
        repo_root=repo_root,
        catalog_schema_path=repo_root / "spec/emes-catalog-v0.schema.json",
    )
    pack_current_a = evidence_metric(power_evidence, "M_LOAD_EQUIV_PACK_CURRENT", "A")
    pack_max_voltage_v = evidence_metric(power_evidence, "M_PACK_MAX_VOLTAGE", "V")
    reference_output_w = evidence_metric(power_evidence, "M_LOAD_POWER", "W")
    parent_scale = evidence_metric(
        known_evidence, "M_KNOWN_COMPONENT_LOAD_SCALE_LIMIT", "1"
    )

    candidates: list[dict[str, Any]] = [
        {
            "id": "upstream_known_component_envelope",
            "rating_kind": "parent_known_component_load_scale",
            "scale_factor": parent_scale,
            "unit": "1",
            "parent_evidence_digest": canonical_digest(known_evidence),
        }
    ]
    connector_records: list[dict[str, Any]] = []
    for component_id in connector_ids:
        if component_id not in selected:
            raise ValueError(f"{component_id}: connector must be catalog-backed")
        connector = selected[component_id]
        if connector.get("kind") != "connector":
            raise ValueError(
                f"{component_id}: expected connector catalog part, got {connector.get('kind')}"
            )
        rated_current_a = property_number(connector, "rated_current", "A")
        rated_voltage_v = property_number(connector, "rated_voltage_dc", "V")
        voltage_margin_v = rated_voltage_v - pack_max_voltage_v
        if voltage_margin_v < 0:
            raise ValueError(
                "connector DC voltage rating is below maximum pack voltage: "
                f"component={component_id} pack={pack_max_voltage_v:g}V "
                f"connector={rated_voltage_v:g}V"
            )
        scale = positive_ratio(
            rated_current_a, pack_current_a, f"{component_id} rated current"
        )
        record = {
            "component": component_id,
            "part": connector["id"],
            "part_digest": catalog_digest(connector),
            "rated_current": rated_current_a,
            "rated_current_unit": "A",
            "rated_voltage_dc": rated_voltage_v,
            "rated_voltage_unit": "V",
            "voltage_margin": voltage_margin_v,
            "voltage_margin_unit": "V",
            "reference_pack_current": pack_current_a,
            "reference_pack_current_unit": "A",
            "scale_factor": scale,
        }
        connector_records.append(record)
        candidates.append(
            {
                "id": f"connector_rated_current:{component_id}",
                "component": component_id,
                "part": connector["id"],
                "part_digest": catalog_digest(connector),
                "rating_kind": "connector_rated_current_used_as_conservative_known_cap",
                "limit": rated_current_a,
                "demand": pack_current_a,
                "unit": "A",
                "scale_factor": scale,
            }
        )

    min_connector_scale = min(item["scale_factor"] for item in connector_records)
    min_voltage_margin = min(item["voltage_margin"] for item in connector_records)
    limiting = min(candidates, key=lambda item: item["scale_factor"])
    final_scale = float(limiting["scale_factor"])
    final_output_w = reference_output_w * final_scale

    return {
        "emes_connector_envelope_evidence_version": "0.1",
        "design_id": document["design"]["id"],
        "design_digest": design_digest,
        "path_id": path_id,
        "input_power_evidence_digest": canonical_digest(power_evidence),
        "input_known_envelope_evidence_digest": canonical_digest(known_evidence),
        "method": "minimum_parent_and_pack_connector_native_rating_load_scale",
        "limiting_candidate": limiting["id"],
        "connectors": connector_records,
        "candidates": candidates,
        "metrics": [
            {
                "id": "M_KNOWN_CONNECTOR_RATED_LOAD_SCALE",
                "value": min_connector_scale,
                "unit": "1",
                "method": "minimum_connector_rated_current_over_reference_pack_current",
            },
            {
                "id": "M_KNOWN_CONNECTOR_VOLTAGE_MARGIN",
                "value": min_voltage_margin,
                "unit": "V",
                "method": "minimum_connector_rated_dc_voltage_minus_pack_max_voltage",
            },
            {
                "id": "M_KNOWN_PATH_LOAD_SCALE_LIMIT",
                "value": final_scale,
                "unit": "1",
                "method": "minimum_parent_and_connector_load_scale",
            },
            {
                "id": "M_KNOWN_PATH_OUTPUT_POWER_ENVELOPE",
                "value": final_output_w,
                "unit": "W",
                "method": "reference_output_load_scaled_by_weakest_known_path_limit",
            },
        ],
        "limitations": [
            "Connector current is evaluated in the pack-side native current domain; no arbitrary voltage is used to manufacture a power rating.",
            "The selected SB50 rating is tied to the reviewed manufacturer assembly configuration recorded in the catalog snapshot; it is not a rating for a bare housing in isolation.",
            "Conductor thermal ampacity, crimp quality, installation method, contact aging, contamination, enclosure temperature rise, and contactor limits are not yet modeled.",
            "The result composes with parent weakest-known evidence and is not approval to fabricate, charge, or energize a battery pack.",
        ],
    }


def run(
    source: Path,
    power_evidence_path: Path,
    known_evidence_path: Path,
    out: Path,
    repo_root: Path,
) -> dict[str, Any]:
    result = evaluate(
        load_json(source),
        load_json(power_evidence_path),
        load_json(known_evidence_path),
        repo_root,
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("--power-evidence", type=Path, required=True)
    parser.add_argument("--known-envelope-evidence", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument("--check-determinism", action="store_true")
    args = parser.parse_args()

    first = run(
        args.source,
        args.power_evidence,
        args.known_envelope_evidence,
        args.out,
        args.repo_root,
    )
    if args.check_determinism:
        with tempfile.TemporaryDirectory(prefix="emes-connector-envelope-") as temp_dir:
            second = run(
                args.source,
                args.power_evidence,
                args.known_envelope_evidence,
                Path(temp_dir) / "evidence.json",
                args.repo_root,
            )
        if first != second:
            raise RuntimeError("non-deterministic connector-envelope evidence")
        print("DETERMINISTIC connector-envelope")

    print(f"VALID connector-envelope limiting={first['limiting_candidate']}")
    for metric in first["metrics"]:
        print(f"{metric['id']}={metric['value']} {metric['unit']}")
    print(f"EVIDENCE {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
