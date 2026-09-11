#!/usr/bin/env python3
"""Compose catalog-backed pack-side conductor/connector/contactor limits with known evidence."""

from __future__ import annotations

import argparse
import json
import math
import tempfile
from pathlib import Path
from typing import Any

from catalog import canonical_digest as catalog_digest
from catalog import property_number, resolve_catalog_parts
from evidence import (
    envelope_fields,
    input_record,
    no_design_decision,
    require_input_digest,
    validate_evidence,
)
from validate import canonical_digest, load_json, validate_semantics


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


def directly_connected(document: dict[str, Any], first: str, second: str) -> bool:
    for connection in document.get("connections", []):
        if connection.get("kind") != "electrical":
            continue
        a = connection.get("a", {}).get("component")
        b = connection.get("b", {}).get("component")
        if {a, b} == {first, second}:
            return True
    return False


def evaluate(
    document: dict[str, Any],
    power_evidence: dict[str, Any],
    known_evidence: dict[str, Any],
    repo_root: Path,
) -> dict[str, Any]:
    design_digest = canonical_digest(document)
    validate_evidence(power_evidence, repo_root, expected_producer="power")
    validate_evidence(
        known_evidence, repo_root, expected_producer="battery_known_envelope"
    )
    if power_evidence.get("design_digest") != design_digest:
        raise ValueError("power evidence design digest does not match mechanism")
    if known_evidence.get("design_digest") != design_digest:
        raise ValueError("known-envelope evidence design digest does not match mechanism")
    require_input_digest(known_evidence, "power", canonical_digest(power_evidence))

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
    conductor_ids = path.get("pack_conductor_components", [])
    if not isinstance(conductor_ids, list):
        raise ValueError("power path pack_conductor_components must be an array")
    if len(set(conductor_ids)) != len(conductor_ids):
        raise ValueError("power path contains duplicate pack-side conductor components")
    contactor_id = path.get("contactor_component")
    if contactor_id is not None and not isinstance(contactor_id, str):
        raise ValueError("power path contactor_component must be a component id")

    selected = resolve_catalog_parts(
        document,
        repo_root=repo_root,
        catalog_schema_path=repo_root / "spec/emes-catalog-v0.schema.json",
    )
    max_pack_current_a = evidence_metric(power_evidence, "M_LOAD_MAX_PACK_CURRENT", "A")
    pack_min_voltage_v = evidence_metric(power_evidence, "M_PACK_MIN_VOLTAGE", "V")
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

    conductor_records: list[dict[str, Any]] = []
    for component_id in conductor_ids:
        if component_id not in selected:
            raise ValueError(f"{component_id}: conductor must be catalog-backed")
        conductor = selected[component_id]
        if conductor.get("kind") != "conductor":
            raise ValueError(
                f"{component_id}: expected conductor catalog part, got {conductor.get('kind')}"
            )
        allowable_ampacity_a = property_number(conductor, "allowable_ampacity", "A")
        rated_voltage_v = property_number(conductor, "rated_voltage", "V")
        conductor_size_awg = property_number(conductor, "conductor_size", "AWG")
        voltage_margin_v = rated_voltage_v - pack_max_voltage_v
        if voltage_margin_v < 0:
            raise ValueError(
                "conductor voltage rating is below maximum pack voltage: "
                f"component={component_id} pack={pack_max_voltage_v:g}V "
                f"conductor={rated_voltage_v:g}V"
            )
        scale = positive_ratio(
            allowable_ampacity_a,
            max_pack_current_a,
            f"{component_id} allowable ampacity",
        )
        record = {
            "component": component_id,
            "part": conductor["id"],
            "part_digest": catalog_digest(conductor),
            "allowable_ampacity": allowable_ampacity_a,
            "allowable_ampacity_unit": "A",
            "rated_voltage": rated_voltage_v,
            "rated_voltage_unit": "V",
            "conductor_size": conductor_size_awg,
            "conductor_size_unit": "AWG",
            "voltage_margin": voltage_margin_v,
            "voltage_margin_unit": "V",
            "max_pack_current": max_pack_current_a,
            "max_pack_current_unit": "A",
            "scale_factor": scale,
        }
        conductor_records.append(record)
        candidates.append(
            {
                "id": f"conductor_allowable_ampacity:{component_id}",
                "component": component_id,
                "part": conductor["id"],
                "part_digest": catalog_digest(conductor),
                "rating_kind": "source_conditioned_conductor_allowable_ampacity",
                "limit": allowable_ampacity_a,
                "demand": max_pack_current_a,
                "unit": "A",
                "scale_factor": scale,
            }
        )

    conductor_by_component = {item["component"]: item for item in conductor_records}
    termination_records: list[dict[str, Any]] = []
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
        adjacent_conductors = [
            conductor_id
            for conductor_id in conductor_ids
            if directly_connected(document, conductor_id, component_id)
        ]
        contact_wire_size_awg: float | None = None
        if adjacent_conductors:
            contact_wire_size_awg = property_number(connector, "contact_wire_size", "AWG")
            for conductor_id in adjacent_conductors:
                conductor_size_awg = float(conductor_by_component[conductor_id]["conductor_size"])
                if not math.isclose(
                    conductor_size_awg, contact_wire_size_awg, rel_tol=0.0, abs_tol=1e-12
                ):
                    raise ValueError(
                        "direct conductor-to-connector wire size is incompatible: "
                        f"conductor={conductor_id} {conductor_size_awg:g}AWG "
                        f"connector={component_id} contact={contact_wire_size_awg:g}AWG"
                    )
                termination_records.append(
                    {
                        "conductor_component": conductor_id,
                        "connector_component": component_id,
                        "conductor_size": conductor_size_awg,
                        "contact_wire_size": contact_wire_size_awg,
                        "unit": "AWG",
                        "compatible": True,
                    }
                )
        voltage_margin_v = rated_voltage_v - pack_max_voltage_v
        if voltage_margin_v < 0:
            raise ValueError(
                "connector DC voltage rating is below maximum pack voltage: "
                f"component={component_id} pack={pack_max_voltage_v:g}V "
                f"connector={rated_voltage_v:g}V"
            )
        scale = positive_ratio(
            rated_current_a, max_pack_current_a, f"{component_id} rated current"
        )
        record = {
            "component": component_id,
            "part": connector["id"],
            "part_digest": catalog_digest(connector),
            "rated_current": rated_current_a,
            "rated_current_unit": "A",
            "rated_voltage_dc": rated_voltage_v,
            "rated_voltage_unit": "V",
            "contact_wire_size": contact_wire_size_awg,
            "contact_wire_size_unit": "AWG" if contact_wire_size_awg is not None else None,
            "voltage_margin": voltage_margin_v,
            "voltage_margin_unit": "V",
            "max_pack_current": max_pack_current_a,
            "max_pack_current_unit": "A",
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
                "demand": max_pack_current_a,
                "unit": "A",
                "scale_factor": scale,
            }
        )

    contactor_record: dict[str, Any] | None = None
    if contactor_id is not None:
        if contactor_id not in selected:
            raise ValueError(f"{contactor_id}: contactor must be catalog-backed")
        contactor = selected[contactor_id]
        if contactor.get("kind") != "contactor":
            raise ValueError(
                f"{contactor_id}: expected contactor catalog part, got {contactor.get('kind')}"
            )
        continuous_current_a = property_number(contactor, "continuous_current", "A")
        operating_min_v = property_number(contactor, "operating_voltage_min_dc", "V")
        operating_max_v = property_number(contactor, "operating_voltage_max_dc", "V")
        if operating_min_v > operating_max_v:
            raise ValueError(f"{contactor_id}: contactor operating-voltage range is invalid")
        min_voltage_margin_v = pack_min_voltage_v - operating_min_v
        max_voltage_margin_v = operating_max_v - pack_max_voltage_v
        if min_voltage_margin_v < 0:
            raise ValueError(
                "contactor minimum DC operating voltage is above minimum pack voltage: "
                f"component={contactor_id} pack={pack_min_voltage_v:g}V "
                f"contactor_min={operating_min_v:g}V"
            )
        if max_voltage_margin_v < 0:
            raise ValueError(
                "contactor maximum DC operating voltage is below maximum pack voltage: "
                f"component={contactor_id} pack={pack_max_voltage_v:g}V "
                f"contactor_max={operating_max_v:g}V"
            )
        scale = positive_ratio(
            continuous_current_a,
            max_pack_current_a,
            f"{contactor_id} continuous current",
        )
        contactor_record = {
            "component": contactor_id,
            "part": contactor["id"],
            "part_digest": catalog_digest(contactor),
            "continuous_current": continuous_current_a,
            "continuous_current_unit": "A",
            "operating_voltage_min_dc": operating_min_v,
            "operating_voltage_max_dc": operating_max_v,
            "operating_voltage_unit": "V",
            "min_voltage_margin": min_voltage_margin_v,
            "max_voltage_margin": max_voltage_margin_v,
            "voltage_margin_unit": "V",
            "max_pack_current": max_pack_current_a,
            "max_pack_current_unit": "A",
            "scale_factor": scale,
        }
        candidates.append(
            {
                "id": f"contactor_continuous_current:{contactor_id}",
                "component": contactor_id,
                "part": contactor["id"],
                "part_digest": catalog_digest(contactor),
                "rating_kind": "contactor_continuous_current",
                "limit": continuous_current_a,
                "demand": max_pack_current_a,
                "unit": "A",
                "scale_factor": scale,
            }
        )

    min_connector_scale = min(item["scale_factor"] for item in connector_records)
    min_connector_voltage_margin = min(
        item["voltage_margin"] for item in connector_records
    )
    limiting = min(candidates, key=lambda item: item["scale_factor"])
    final_scale = float(limiting["scale_factor"])
    final_output_w = reference_output_w * final_scale

    metrics: list[dict[str, Any]] = []
    if conductor_records:
        min_conductor_scale = min(item["scale_factor"] for item in conductor_records)
        min_conductor_voltage_margin = min(
            item["voltage_margin"] for item in conductor_records
        )
        metrics.extend(
            [
                {
                    "id": "M_KNOWN_CONDUCTOR_AMPACITY_LOAD_SCALE",
                    "value": min_conductor_scale,
                    "unit": "1",
                    "method": "minimum_source_conditioned_ampacity_over_max_pack_current",
                },
                {
                    "id": "M_KNOWN_CONDUCTOR_VOLTAGE_MARGIN",
                    "value": min_conductor_voltage_margin,
                    "unit": "V",
                    "method": "minimum_conductor_rated_voltage_minus_pack_max_voltage",
                },
            ]
        )
    metrics.extend(
        [
            {
                "id": "M_KNOWN_CONNECTOR_RATED_LOAD_SCALE",
                "value": min_connector_scale,
                "unit": "1",
                "method": "minimum_connector_rated_current_over_max_pack_current",
            },
            {
                "id": "M_KNOWN_CONNECTOR_VOLTAGE_MARGIN",
                "value": min_connector_voltage_margin,
                "unit": "V",
                "method": "minimum_connector_rated_dc_voltage_minus_pack_max_voltage",
            },
        ]
    )
    if contactor_record is not None:
        metrics.extend(
            [
                {
                    "id": "M_KNOWN_CONTACTOR_CONTINUOUS_LOAD_SCALE",
                    "value": contactor_record["scale_factor"],
                    "unit": "1",
                    "method": "contactor_continuous_current_over_max_pack_current",
                },
                {
                    "id": "M_KNOWN_CONTACTOR_MIN_VOLTAGE_MARGIN",
                    "value": contactor_record["min_voltage_margin"],
                    "unit": "V",
                    "method": "pack_min_voltage_minus_contactor_min_operating_voltage",
                },
                {
                    "id": "M_KNOWN_CONTACTOR_MAX_VOLTAGE_MARGIN",
                    "value": contactor_record["max_voltage_margin"],
                    "unit": "V",
                    "method": "contactor_max_operating_voltage_minus_pack_max_voltage",
                },
            ]
        )
    metrics.extend(
        [
            {
                "id": "M_KNOWN_PATH_LOAD_SCALE_LIMIT",
                "value": final_scale,
                "unit": "1",
                "method": "minimum_parent_conductor_connector_and_contactor_load_scale",
            },
            {
                "id": "M_KNOWN_PATH_OUTPUT_POWER_ENVELOPE",
                "value": final_output_w,
                "unit": "W",
                "method": "reference_output_load_scaled_by_weakest_known_path_limit",
            },
        ]
    )
    result = {
        **envelope_fields(
            producer_id="battery_connector_envelope",
            design_id=document["design"]["id"],
            design_digest=design_digest,
            inputs=[
                input_record("power", canonical_digest(power_evidence)),
                input_record("known_envelope", canonical_digest(known_evidence)),
            ],
            metrics=metrics,
            constraint_results=[],
            verification=no_design_decision(),
        ),
        "path_id": path_id,
        "method": "minimum_parent_pack_conductor_connector_and_contactor_native_rating_load_scale",
        "limiting_candidate": limiting["id"],
        "conductors": conductor_records,
        "connectors": connector_records,
        "termination_compatibility": termination_records,
        "candidates": candidates,
        "limitations": [
            "Conductor and connector current limits are evaluated against the maximum pack-side current over the modeled battery voltage range; no arbitrary voltage is used to manufacture a power rating.",
            "Conductor ampacity is consumed only as the manufacturer-published value for the exact reviewed cable construction and stated NEC/CEC table basis; EMES does not infer a universal ampacity from AWG or copper area.",
            "A conductor directly connected to a catalog-backed connector must match the connector contact's source-backed direct wire size; reducer-bushing derating is not inferred.",
            "The selected SB50 rating is tied to the reviewed manufacturer assembly configuration recorded in the catalog snapshot; it is not a rating for a bare housing in isolation.",
            "Contactor evaluation uses only source-backed main-contact continuous current and the declared DC operating-voltage range; switching life, pulse/breaking capability, coil-drive suitability, and pre-charge approval are not inferred.",
            "Conductor routing, bundling, enclosed-harness ambient/temperature rise, crimp quality, contact aging, contamination, and enclosure temperature rise are not modeled; the published flexible-cord ampacity is not fabrication approval.",
            "The result composes with parent weakest-known evidence and is not approval to fabricate, charge, or energize a battery pack.",
        ],
    }
    if contactor_record is not None:
        result["contactor"] = contactor_record
    return result


def run(
    source: Path,
    power_evidence_path: Path,
    known_evidence_path: Path,
    out: Path,
    repo_root: Path,
) -> dict[str, Any]:
    document = load_json(source)
    validate_semantics(document)
    result = evaluate(
        document,
        load_json(power_evidence_path),
        load_json(known_evidence_path),
        repo_root,
    )
    validate_evidence(
        result, repo_root, expected_producer="battery_connector_envelope"
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
