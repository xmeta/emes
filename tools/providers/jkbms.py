#!/usr/bin/env python3
"""Jikong/JKBMS provider for the hash-pinned JK-BD6A20S-6P manual."""
from __future__ import annotations
import argparse, hashlib, json, urllib.request
from pathlib import Path
from typing import Any

USER_AGENT = "Mozilla/5.0 EMES/0.1"

def sha256(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()

def canonical_digest(value: Any) -> str:
    return sha256(json.dumps(value,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode())

def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))

def fetch(url: str) -> bytes:
    req=urllib.request.Request(url,headers={"User-Agent":USER_AGENT,"Accept":"application/pdf,*/*;q=0.8"})
    with urllib.request.urlopen(req,timeout=45) as response:
        return response.read()

def verify_snapshot(path: Path) -> dict[str, Any]:
    snapshot=load(path); upstream=snapshot["upstream"]["datasheet_pdf"]
    data=fetch(upstream["uri"])
    if not data.startswith(b"%PDF"):
        raise RuntimeError("JKBMS source did not return a PDF")
    actual=sha256(data)
    if actual != upstream["sha256"] or len(data) != int(upstream["bytes"]):
        raise RuntimeError(f"JKBMS datasheet changed: got {actual} bytes={len(data)}")
    return {"sha256":actual,"bytes":len(data),"manual_version":upstream["manual_version"]}

def fact(snapshot: dict[str, Any], name: str, unit: str) -> float:
    item=snapshot["facts"][name]
    if item["unit"] != unit: raise ValueError(f"{name}: expected {unit}, got {item['unit']}")
    return float(item["value"])

def q(value: float, unit: str, source: str) -> dict[str, Any]:
    return {"value":value,"unit":unit,"source":source}

def normalize(snapshot_path: Path, output_path: Path) -> dict[str, Any]:
    s=load(snapshot_path); u=s["upstream"]["datasheet_pdf"]; src="SRC_JKBMS_BD6A20S6P"
    part={
      "id":"JKBMS_JK_BD6A20S_6P","kind":"bms",
      "identity":{"manufacturer":s["manufacturer"],"part_number":s["part_number"]},
      "properties":{
        "mass":q(fact(s,"mass","kg"),"kg",src),
        "min_series_cells":q(fact(s,"min_series_cells_li_ion","1"),"1",src),
        "max_series_cells":q(fact(s,"max_series_cells_li_ion","1"),"1",src),
        "min_pack_voltage":q(fact(s,"supply_voltage_min","V"),"V",src),
        "max_pack_voltage":q(fact(s,"supply_voltage_max","V"),"V",src),
        "continuous_discharge_current":q(fact(s,"continuous_discharge_current","A"),"A",src),
        "max_charge_current":q(fact(s,"max_charge_current","A"),"A",src),
        "max_instantaneous_current":q(fact(s,"max_instantaneous_current","A"),"A",src),
        "max_balancing_current":q(fact(s,"max_balancing_current","A"),"A",src),
        "max_internal_loop_resistance":q(fact(s,"max_internal_loop_resistance","ohm"),"ohm",src),
        "operating_temperature_min":q(fact(s,"operating_temperature_min","degC"),"degC",src),
        "operating_temperature_max":q(fact(s,"operating_temperature_max","degC"),"degC",src),
        "default_cell_charge_cutoff_temperature":q(fact(s,"default_charge_overtemperature","degC"),"degC",src),
        "default_cell_discharge_cutoff_temperature":q(fact(s,"default_discharge_overtemperature","degC"),"degC",src),
      },
      "interfaces":[{"id":"IF_BATTERY","kind":"electrical_dc","properties":{}},{"id":"IF_LOAD","kind":"electrical_dc","properties":{}}],
      "assets":[{"id":"ASSET_DATASHEET","kind":"datasheet","uri":u["uri"],"digest":u["sha256"],"source":src,"license":"Jikong/JKBMS upstream terms; redistribution not asserted"}],
    }
    catalog={"emes_catalog_version":"0.1","catalog":{"id":"CAT_JKBMS_BD6A20S6P","name":"JKBMS JK-BD6A20S-6P","description":"Hash-pinned manufacturer manual normalized for EMES; configurable factory-default temperature settings are kept distinct from selected mechanism configuration."},"sources":[{"id":src,"authority":"manufacturer","format":"datasheet","uri":u["uri"],"retrieved_at":s["captured_at"],"license":"Jikong/JKBMS upstream terms; redistribution not asserted","raw_digest":u["sha256"]}],"parts":[part]}
    output_path.write_text(json.dumps(catalog,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    return catalog

def main() -> int:
    p=argparse.ArgumentParser(); sub=p.add_subparsers(dest='cmd',required=True)
    v=sub.add_parser('verify-snapshot'); v.add_argument('snapshot',type=Path)
    n=sub.add_parser('normalize'); n.add_argument('snapshot',type=Path); n.add_argument('output',type=Path)
    a=p.parse_args()
    if a.cmd=='verify-snapshot':
        print('VALID jkbms-authoritative-snapshot'); print(json.dumps(verify_snapshot(a.snapshot),indent=2,sort_keys=True)); return 0
    c=normalize(a.snapshot,a.output); print(f'NORMALIZED {a.snapshot} -> {a.output}'); print('catalog_digest='+canonical_digest(c)); return 0
if __name__=='__main__': raise SystemExit(main())
