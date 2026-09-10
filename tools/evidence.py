#!/usr/bin/env python3
"""Shared EMES evidence envelope helpers."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import platform
from pathlib import Path
from typing import Any, Iterable

import jsonschema

EVIDENCE_VERSION = "0.1"
PRODUCER_VERSION = "0.1"


def digest_json(value: Any) -> str:
    payload = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def runtime_record(packages: Iterable[str] = ()) -> dict[str, Any]:
    return {
        "python": platform.python_version(),
        "packages": {
            package: importlib.metadata.version(package) for package in sorted(packages)
        },
    }


def input_record(role: str, digest: str, kind: str = "evidence") -> dict[str, str]:
    return {"role": role, "kind": kind, "digest": digest}


def no_design_decision() -> dict[str, Any]:
    return {
        "execution_status": "succeeded",
        "verification_status": "complete",
        "design_decision": "not_decidable",
        "required_constraints": [],
        "failed_constraints": [],
        "incomplete_constraints": [],
    }


def envelope_fields(
    *,
    producer_id: str,
    design_id: str,
    design_digest: str,
    inputs: list[dict[str, str]],
    metrics: list[dict[str, Any]],
    constraint_results: list[dict[str, Any]],
    verification: dict[str, Any],
    packages: Iterable[str] = ("jsonschema",),
) -> dict[str, Any]:
    runtime = runtime_record(packages)
    return {
        "emes_evidence_version": EVIDENCE_VERSION,
        "producer": {"id": producer_id, "version": PRODUCER_VERSION},
        "design_id": design_id,
        "design_digest": design_digest,
        "runtime": runtime,
        "runtime_digest": digest_json(runtime),
        "inputs": inputs,
        "metrics": metrics,
        "constraint_results": constraint_results,
        "verification": verification,
    }


def validate_evidence(
    value: dict[str, Any],
    repo_root: Path,
    *,
    expected_producer: str | None = None,
) -> None:
    schema_path = repo_root / "spec/emes-evidence-v0.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    validator_cls = jsonschema.validators.validator_for(schema)
    validator_cls.check_schema(schema)
    validator_cls(schema).validate(value)

    if value["runtime_digest"] != digest_json(value["runtime"]):
        raise ValueError("evidence runtime digest does not match runtime record")
    if expected_producer is not None and value["producer"]["id"] != expected_producer:
        raise ValueError(
            f"expected {expected_producer} evidence, got {value['producer']['id']}"
        )

    roles = [item["role"] for item in value["inputs"]]
    if len(roles) != len(set(roles)):
        raise ValueError("evidence input roles must be unique")


def require_input_digest(
    value: dict[str, Any], role: str, expected_digest: str
) -> None:
    matches = [item for item in value["inputs"] if item["role"] == role]
    if len(matches) != 1:
        raise ValueError(f"evidence must contain exactly one input role {role}")
    if matches[0]["digest"] != expected_digest:
        raise ValueError(f"evidence input {role} digest does not match supplied input")
