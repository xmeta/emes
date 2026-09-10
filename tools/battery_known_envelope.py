#!/usr/bin/env python3
"""Conservatively combine known battery-path limits using load-scale factors."""

from __future__ import annotations

import argparse
import json
import math
import tempfile
from pathlib import Path
from typing import Any

import jsonschema

from catalog import canonical_digest as catalog_digest
from catalog import property_number, resolve_catalog_parts
from power import OPERATORS, quantity_value
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


def positive_ratio(limit: float, demand: float, context: str) -> float:
    if not math.isfinite(limit) or not math.isfinite(demand):
        raise ValueError(f"{context}: non-finite limit or demand")
    if limit <= 0 or demand <= 0:
        raise ValueError(f"{context}: limit and demand must both be positive")
    return limit / demand


def matching_power_path(document: dict[str, Any], path_id: str) -> dict[str, Any]:
    extension = document.get("extensions", {}).get("org.emes.power", {})
    paths = extension.get("power_paths", []) if isinstance(extension, dict) else []
    matches = [item for item in paths if item.get("id") == path_id]
    if len(matches) != 1:
        raise ValueError(f"mechanism must provide exactly one power path {path_id}")
    return matches[0]


def context_value(config: dict[str, Any], name: str, unit: str) -> float:
    context = config["context"]
    if name not in context:
        raise ValueError(f"missing known-envelope context value {name!r}")
    return quantity_value(context[name], unit, f"known-envelope context {name}")


def conditioned_fuse_current_match(
    config: dict[str, Any], fuse: dict[str, Any], property_name: str
) -> tuple[bool, dict[str, Any]]:
    try:
        item = fuse["properties"][property_name]
    except KeyError as exc:
        raise ValueError(f"{fuse['id']}: missing fuse current property {property_name}") from exc

    current = quantity_value(item, "A", f"{fuse['id']}.{property_name}")
    conditions = item.get("conditions", [])
    if not conditions:
        raise ValueError(
            f"{fuse['id']}.{property_name}: source-conditioned fuse property must declare conditions"
        )

    checks: list[dict[str, Any]] = []
    matched = True
    for condition in conditions:
        name = condition["parameter"]
        unit = condition["unit"]
        actual = context_value(config, name, unit)
        target = float(condition["value"])
        if not math.isfinite(target):
            raise ValueError(f"{fuse['id']}.{property_name}.{name}: non-finite condition target")
        op = condition["op"]
        passed = OPERATORS[op](actual, target)
        matched = matched and passed
        checks.append(
            {
                "condition_parameter": name,
                "condition_op": op,
                "condition_value": target,
                "condition_unit": unit,
                "context_value": actual,
                "status": "pass" if passed else "fail",
            }
        )

    return matched, {
        "property": property_name,
        "property_value": current,
        "property_unit": "A",
        "source": item.get("source"),
        "condition_results": checks,
    }


def select_source_conditioned_fuse_current(
    config: dict[str, Any], fuse: dict[str, Any]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    evaluated = [
        conditioned_fuse_current_match(config, fuse, name)
        for name in config["fuse_current_properties"]
    ]
    matches = [record for matched, record in evaluated if matched]
    if not matches:
        raise ValueError(
            "no source-conditioned fuse current point matches the requested context; "
            "interpolation or fallback to nominal current is not allowed"
        )
    if len(matches) != 1:
        names = ", ".join(record["property"] for record in matches)
        raise ValueError(
            f"ambiguous fuse current context matched multiple properties: {names}"
        )
    return matches[0], [record for _, record in evaluated]


def evaluate(
    document: dict[str, Any],
    power_evidence: dict[str, Any],
    pulse_evidence: dict[str, Any],
    repo_root: Path,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    design_digest = canonical_digest(document)
    if power_evidence.get("design_digest") != design_digest:
        raise ValueError("power evidence design digest does not match mechanism")
    if pulse_evidence.get("design_digest") != design_digest:
        raise ValueError("pulse evidence design digest does not match mechanism")
    if pulse_evidence.get("input_power_evidence_digest") != canonical_digest(power_evidence):
        raise ValueError("pulse evidence is not derived from the supplied power evidence")

    topology = power_evidence.get("power_topology", {})
    if topology.get("source_kind") != "battery_pack":
        raise ValueError("known-envelope analysis requires battery-pack power evidence")
    pulse_pack = pulse_evidence.get("pack", {})
    if pulse_pack.get("pack_id") != topology.get("pack_id"):
        raise ValueError("pulse and power evidence refer to different battery packs")
    if pulse_pack.get("cell_component") != topology.get("cell_component"):
        raise ValueError("pulse and power evidence refer to different cell components")

    bms_component = topology.get("bms_component")
    converter_component = topology.get("converter_component")
    if not isinstance(bms_component, str) or not isinstance(converter_component, str):
        raise ValueError("power topology must expose BMS and converter components")

    path_id = topology.get("path_id")
    if not isinstance(path_id, str):
        raise ValueError("power topology must expose a path id")
    if config is not None and config["path_id"] != path_id:
        raise ValueError("known-envelope analysis path_id does not match power evidence")
    power_path = matching_power_path(document, path_id)
    fuse_component = power_path.get("fuse_component")
    if fuse_component is not None and not isinstance(fuse_component, str):
        raise ValueError("fuse_component must be a component id")

    selected = resolve_catalog_parts(
        document,
        repo_root=repo_root,
        catalog_schema_path=repo_root / "spec/emes-catalog-v0.schema.json",
    )
    if bms_component not in selected or converter_component not in selected:
        raise ValueError("BMS and converter must be catalog-backed")
    bms = selected[bms_component]
    converter = selected[converter_component]
    if bms.get("kind") != "bms":
        raise ValueError(f"{bms_component}: expected bms catalog part")
    if converter.get("kind") != "power_converter":
        raise ValueError(f"{converter_component}: expected power_converter catalog part")

    fuse: dict[str, Any] | None = None
    if fuse_component is not None:
        if fuse_component not in selected:
            raise ValueError(f"{fuse_component}: fuse must be catalog-backed")
        fuse = selected[fuse_component]
        if fuse.get("kind") != "fuse":
            raise ValueError(f"{fuse_component}: expected fuse catalog part")
    if config is not None and fuse is None:
        raise ValueError("source-conditioned fuse analysis requires an explicit catalog-backed fuse")

    cell_envelope_w = evidence_metric(
        pulse_evidence, "M_PACK_CELL_ENVELOPE_PULSE_POWER", "W"
    )
    pulse_reference_input_w = evidence_metric(
        pulse_evidence, "M_PULSE_REFERENCE_PACK_INPUT_POWER", "W"
    )
    max_pack_current_a = evidence_metric(power_evidence, "M_LOAD_MAX_PACK_CURRENT", "A")
    output_current_a = evidence_metric(power_evidence, "M_CONVERTER_OUTPUT_CURRENT", "A")
    output_power_w = evidence_metric(power_evidence, "M_LOAD_POWER", "W")
    pack_max_voltage_v = evidence_metric(power_evidence, "M_PACK_MAX_VOLTAGE", "V")

    bms_continuous_a = property_number(bms, "continuous_discharge_current", "A")
    converter_continuous_a = property_number(
        converter, "continuous_output_current", "A"
    )

    candidates = [
        {
            "id": "cell_pulse_power",
            "component": topology["cell_component"],
            "part": pulse_pack.get("cell_part"),
            "rating_kind": "source_conditioned_pulse_power",
            "limit": cell_envelope_w,
            "demand": pulse_reference_input_w,
            "unit": "W",
            "scale_factor": positive_ratio(
                cell_envelope_w, pulse_reference_input_w, "cell pulse envelope"
            ),
        },
        {
            "id": "bms_continuous_current",
            "component": bms_component,
            "part": bms["id"],
            "part_digest": catalog_digest(bms),
            "rating_kind": "continuous_current_used_as_conservative_10s_cap",
            "limit": bms_continuous_a,
            "demand": max_pack_current_a,
            "unit": "A",
            "scale_factor": positive_ratio(
                bms_continuous_a, max_pack_current_a, "BMS continuous current"
            ),
        },
        {
            "id": "converter_continuous_output_current",
            "component": converter_component,
            "part": converter["id"],
            "part_digest": catalog_digest(converter),
            "rating_kind": "continuous_output_current_used_as_conservative_10s_cap",
            "limit": converter_continuous_a,
            "demand": output_current_a,
            "unit": "A",
            "scale_factor": positive_ratio(
                converter_continuous_a,
                output_current_a,
                "converter continuous output current",
            ),
        },
    ]

    fuse_voltage_margin_v: float | None = None
    conditioned_fuse_point: dict[str, Any] | None = None
    conditioned_fuse_results: list[dict[str, Any]] | None = None
    if fuse is not None and fuse_component is not None:
        fuse_rated_a = property_number(fuse, "rated_current", "A")
        fuse_rated_voltage_v = property_number(fuse, "rated_voltage_dc", "V")
        fuse_voltage_margin_v = fuse_rated_voltage_v - pack_max_voltage_v
        if fuse_voltage_margin_v < 0:
            raise ValueError(
                "fuse DC voltage rating is below maximum pack voltage: "
                f"pack={pack_max_voltage_v:g}V fuse={fuse_rated_voltage_v:g}V"
            )
        candidates.append(
            {
                "id": "fuse_rated_current",
                "component": fuse_component,
                "part": fuse["id"],
                "part_digest": catalog_digest(fuse),
                "rating_kind": "nominal_rated_current_used_as_conservative_10s_cap",
                "limit": fuse_rated_a,
                "demand": max_pack_current_a,
                "unit": "A",
                "scale_factor": positive_ratio(
                    fuse_rated_a, max_pack_current_a, "fuse nominal rated current"
                ),
            }
        )
        if config is not None:
            conditioned_fuse_point, conditioned_fuse_results = (
                select_source_conditioned_fuse_current(config, fuse)
            )
            conditioned_a = float(conditioned_fuse_point["property_value"])
            candidates.append(
                {
                    "id": "fuse_source_conditioned_current",
                    "component": fuse_component,
                    "part": fuse["id"],
                    "part_digest": catalog_digest(fuse),
                    "rating_kind": "source_conditioned_current_used_as_conservative_10s_cap",
                    "source_property": conditioned_fuse_point["property"],
                    "source": conditioned_fuse_point.get("source"),
                    "condition_results": conditioned_fuse_point["condition_results"],
                    "limit": conditioned_a,
                    "demand": max_pack_current_a,
                    "unit": "A",
                    "scale_factor": positive_ratio(
                        conditioned_a,
                        max_pack_current_a,
                        "source-conditioned fuse current",
                    ),
                }
            )

    limiting = min(candidates, key=lambda item: item["scale_factor"])
    scale = float(limiting["scale_factor"])
    output_envelope_w = output_power_w * scale
    input_envelope_w = pulse_reference_input_w * scale

    metrics = [
        {
            "id": "M_KNOWN_CELL_PULSE_LOAD_SCALE",
            "value": candidates[0]["scale_factor"],
            "unit": "1",
            "method": "native_rating_ratio",
        },
        {
            "id": "M_KNOWN_BMS_CONTINUOUS_LOAD_SCALE",
            "value": candidates[1]["scale_factor"],
            "unit": "1",
            "method": "native_rating_ratio",
        },
        {
            "id": "M_KNOWN_CONVERTER_CONTINUOUS_LOAD_SCALE",
            "value": candidates[2]["scale_factor"],
            "unit": "1",
            "method": "native_rating_ratio",
        },
    ]
    if fuse is not None and fuse_voltage_margin_v is not None:
        fuse_candidate = next(item for item in candidates if item["id"] == "fuse_rated_current")
        metrics.extend(
            [
                {
                    "id": "M_KNOWN_FUSE_RATED_LOAD_SCALE",
                    "value": fuse_candidate["scale_factor"],
                    "unit": "1",
                    "method": "native_rating_ratio",
                },
                {
                    "id": "M_KNOWN_FUSE_VOLTAGE_MARGIN",
                    "value": fuse_voltage_margin_v,
                    "unit": "V",
                    "method": "rated_dc_voltage_minus_pack_max_voltage",
                },
            ]
        )
        if conditioned_fuse_point is not None:
            conditioned_candidate = next(
                item
                for item in candidates
                if item["id"] == "fuse_source_conditioned_current"
            )
            metrics.append(
                {
                    "id": "M_KNOWN_FUSE_SOURCE_CONDITIONED_LOAD_SCALE",
                    "value": conditioned_candidate["scale_factor"],
                    "unit": "1",
                    "method": "native_rating_ratio",
                }
            )
    metrics.extend(
        [
            {
                "id": "M_KNOWN_COMPONENT_LOAD_SCALE_LIMIT",
                "value": scale,
                "unit": "1",
                "method": "minimum_native_rating_ratio",
            },
            {
                "id": "M_KNOWN_COMPONENT_OUTPUT_POWER_ENVELOPE",
                "value": output_envelope_w,
                "unit": "W",
                "method": "reference_load_scaled_by_weakest_known_limit",
            },
            {
                "id": "M_KNOWN_COMPONENT_INPUT_POWER_ENVELOPE",
                "value": input_envelope_w,
                "unit": "W",
                "method": "reference_input_scaled_by_weakest_known_limit",
            },
        ]
    )

    limitations = [
        "Cross-domain limits are compared through dimensionless load-scale factors rather than by inventing a common rating unit.",
        "BMS and converter continuous-current ratings are conservative caps for the 10 s pulse context; they are not treated as pulse ratings.",
        "The current BMS and converter records are synthetic architecture fixtures, not physical product recommendations.",
        "Interconnect, conductor, connector, contactor, temperature rise, cell imbalance, aging, and enclosure limits are not yet included.",
        "The result is a weakest-known-component envelope, not a complete system pulse rating or approval to fabricate, charge, or energize a pack.",
    ]
    if fuse is not None:
        if config is None:
            limitations.insert(
                3,
                "Fuse nominal rated current is used only as a conservative cap; time-current/I2t data and source-conditioned ambient derating are not converted into an inferred 10 s pulse ampacity.",
            )
        else:
            limitations.insert(
                3,
                "The nominal fuse rating remains a separate candidate; the source-conditioned fuse current is added only for an exact supported analysis context, with no interpolation or extrapolation.",
            )
        limitations.insert(
            4,
            "The reviewed Littelfuse manufacturer snapshot is reproducible, but the upstream PDF raw-byte digest is not yet pinned because automated source acquisition is blocked; physical safety approval therefore remains out of scope.",
        )

    result: dict[str, Any] = {
        "emes_known_power_envelope_evidence_version": "0.1",
        "design_id": document["design"]["id"],
        "design_digest": design_digest,
        "input_power_evidence_digest": canonical_digest(power_evidence),
        "input_pulse_evidence_digest": canonical_digest(pulse_evidence),
        "method": "minimum_cross_domain_native_rating_load_scale",
        "limiting_candidate": limiting["id"],
        "candidates": candidates,
        "metrics": metrics,
        "limitations": limitations,
    }
    if config is not None:
        result.update(
            {
                "analysis_id": config["analysis_id"],
                "analysis_request_digest": canonical_digest(config),
                "context": config["context"],
                "selected_fuse_current_point": conditioned_fuse_point,
                "fuse_current_condition_results": conditioned_fuse_results,
            }
        )
    return result


def run(
    source: Path,
    power_evidence_path: Path,
    pulse_evidence_path: Path,
    out: Path,
    repo_root: Path,
    analysis_request_path: Path | None = None,
) -> dict[str, Any]:
    document = load_json(source)
    power_evidence = load_json(power_evidence_path)
    pulse_evidence = load_json(pulse_evidence_path)

    config: dict[str, Any] | None = None
    if analysis_request_path is not None:
        config = load_json(analysis_request_path)
        schema = load_json(repo_root / "spec/emes-battery-known-envelope-v0.schema.json")
        validator_cls = jsonschema.validators.validator_for(schema)
        validator_cls.check_schema(schema)
        validator_cls(schema).validate(config)

    result = evaluate(document, power_evidence, pulse_evidence, repo_root, config)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("--power-evidence", type=Path, required=True)
    parser.add_argument("--pulse-evidence", type=Path, required=True)
    parser.add_argument("--analysis-request", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument("--check-determinism", action="store_true")
    args = parser.parse_args()

    first = run(
        args.source,
        args.power_evidence,
        args.pulse_evidence,
        args.out,
        args.repo_root,
        args.analysis_request,
    )
    if args.check_determinism:
        with tempfile.TemporaryDirectory(prefix="emes-known-envelope-") as temp_dir:
            second = run(
                args.source,
                args.power_evidence,
                args.pulse_evidence,
                Path(temp_dir) / "evidence.json",
                args.repo_root,
                args.analysis_request,
            )
        if first != second:
            raise RuntimeError("non-deterministic known power envelope evidence")
        print("DETERMINISTIC known-power-envelope")

    print(f"VALID known-power-envelope limiting={first['limiting_candidate']}")
    for metric in first["metrics"]:
        print(f"{metric['id']}={metric['value']} {metric['unit']}")
    print(f"EVIDENCE {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
