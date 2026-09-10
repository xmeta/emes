#!/usr/bin/env python3
"""Verify the auxiliary Molicel P45B manufacturer performance page snapshot."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from molicel import fetch, load_snapshot, sha256


def verify_snapshot(path: Path) -> dict[str, object]:
    snapshot = load_snapshot(path)
    upstream = snapshot["upstream"]["performance_page"]
    body = fetch(upstream["uri"])
    if b"INR21700-P45B" not in body and b"INR-21700-P45B" not in body:
        raise RuntimeError("Molicel performance page does not identify P45B")
    actual = sha256(body)
    expected = upstream["sha256"]
    expected_bytes = int(upstream["bytes"])
    if actual != expected or len(body) != expected_bytes:
        raise RuntimeError(
            "Molicel performance page changed: "
            f"expected {expected} bytes={expected_bytes}, "
            f"got {actual} bytes={len(body)}"
        )
    return {
        "provider": "molicel-performance",
        "part_number": snapshot["part_number"],
        "performance_page": {"sha256": actual, "bytes": len(body)},
        "snapshot_version": snapshot["snapshot_version"],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("snapshot", type=Path)
    args = parser.parse_args()
    result = verify_snapshot(args.snapshot)
    print("VALID molicel-performance-snapshot")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
