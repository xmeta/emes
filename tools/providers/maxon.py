#!/usr/bin/env python3
"""Probe authoritative maxon product sources without redistributing upstream assets."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import urllib.request
import zipfile
from html.parser import HTMLParser
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["probe"])
    args = parser.parse_args()
    if args.command == "probe":
        print(json.dumps(probe(), indent=2, sort_keys=True))
        return 0
    raise AssertionError(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
