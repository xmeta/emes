#!/usr/bin/env bash
set -euo pipefail

if ! command -v python >/dev/null 2>&1; then
  python() { python3 "$@"; }
fi

SOURCE=catalogs/sources/meanwell-npb-450-24nfc.source.json
CATALOG=catalogs/meanwell-npb-450-24nfc.catalog.json
MECHANISM=examples/power-pack-molicel-p45b/mechanism.json
REQUEST=examples/power-pack-molicel-p45b/charge-42v-9a.json

python tools/providers/meanwell_npb450.py "$SOURCE" /tmp/meanwell-npb450.catalog.json
python - <<'PY_PROVIDER'
import json
from pathlib import Path
from tools.catalog import canonical_digest

committed = json.loads(Path("catalogs/meanwell-npb-450-24nfc.catalog.json").read_text())
regenerated = json.loads(Path("/tmp/meanwell-npb450.catalog.json").read_text())
assert committed == regenerated
part = committed["parts"][0]
assert canonical_digest(committed) == "sha256:f48de6e0a1090c82db738a7203a39d495bafdb6b784f152be8f842f06fedc489"
assert canonical_digest(part) == "sha256:d39be9a2ff265120137801c14674c6d86ed108c03b48e7c1b045579ed769b125"
assert part["kind"] == "charger"
assert part["properties"]["charge_voltage_min"]["value"] == 21.0
assert part["properties"]["charge_voltage_max"]["value"] == 42.0
assert part["properties"]["max_output_current"]["value"] == 13.5
assert part["properties"]["max_output_power"]["value"] == 453.6
print("DETERMINISTIC meanwell-npb450-normalization")
PY_PROVIDER
python tools/catalog.py validate "$CATALOG"

python tools/battery_charge.py "$MECHANISM" \
  --request "$REQUEST" \
  --out /tmp/battery-charge.json \
  --check-determinism

python - <<'PY_POSITIVE'
import json
import math
from pathlib import Path

evidence = json.loads(Path("/tmp/battery-charge.json").read_text())
metrics = {item["id"]: item["value"] for item in evidence["metrics"]}
assert math.isclose(metrics["M_CHARGE_TARGET_VOLTAGE"], 42.0)
assert math.isclose(metrics["M_PACK_MAX_CHARGE_VOLTAGE"], 42.0)
assert math.isclose(metrics["M_CHARGE_CURRENT"], 9.0)
assert math.isclose(metrics["M_PACK_STANDARD_CHARGE_CURRENT_MARGIN"], 0.0)
assert math.isclose(metrics["M_CHARGER_CURRENT_MARGIN"], 4.5)
assert math.isclose(metrics["M_CHARGER_POWER_CURRENT_MARGIN"], 1.8)
assert math.isclose(metrics["M_CHARGER_POWER_MARGIN"], 75.6)
assert math.isclose(metrics["M_BMS_CHARGE_CURRENT_MARGIN"], 51.0)
assert math.isclose(metrics["M_CELL_MAX_CHARGE_VOLTAGE"], 4.2)
assert math.isclose(metrics["M_BMS_DEFAULT_CELL_OVERCHARGE_PROTECTION"], 4.2)
assert math.isclose(metrics["M_BMS_DEFAULT_CELL_OVERCHARGE_RECOVERY"], 4.18)
assert math.isclose(metrics["M_BMS_CELL_VOLTAGE_ACQUISITION_ACCURACY_ABS"], 0.003)
assert math.isclose(metrics["M_BMS_CELL_OVERCHARGE_PROTECTION_MARGIN"], 0.0)
assert math.isclose(metrics["M_BMS_CELL_OVERCHARGE_RECOVERY_HYSTERESIS"], 0.02)
assert math.isclose(metrics["M_BMS_WORST_CASE_CELL_VOLTAGE_AT_TRIP"], 4.203)
assert math.isclose(metrics["M_BMS_MEASUREMENT_AWARE_OVERCHARGE_MARGIN"], -0.003)
assert math.isclose(metrics["M_CELL_CHARGE_TEMPERATURE_MIN"], 0.0)
assert math.isclose(metrics["M_CELL_CHARGE_TEMPERATURE_MAX"], 60.0)
assert math.isclose(metrics["M_BMS_DEFAULT_CHARGE_CUTOFF_TEMPERATURE"], 70.0)
assert math.isclose(metrics["M_CHARGE_THERMAL_GUARD_MARGIN"], -10.0)
compat = evidence["charge_compatibility"]
assert compat["electrical_compatible"] is True
assert compat["cell_overcharge_guard_compatible"] is True
assert compat["measurement_aware_cell_overcharge_guard_sufficient"] is False
assert compat["thermal_guard_sufficient"] is False
assert evidence["cell_voltage_guard"]["measurement_aware_sufficient"] is False
assert evidence["cell_voltage_guard"]["configured_at_runtime"] is False
assert compat["automatic_charging_approval"] is False
assert evidence["verification"]["design_decision"] == "not_decidable"
assert any("does not authorize charging" in item for item in evidence["limitations"])
print("VERIFIED static 42V/9A charge compatibility without charging approval")
PY_POSITIVE

python - <<'PY_VARIANTS'
import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, "tools")
from catalog import canonical_digest

request = json.loads(Path("examples/power-pack-molicel-p45b/charge-42v-9a.json").read_text())
charger_catalog = json.loads(Path("catalogs/meanwell-npb-450-24nfc.catalog.json").read_text())
mechanism = json.loads(Path("examples/power-pack-molicel-p45b/mechanism.json").read_text())

def request_variant(name, mutate):
    value = copy.deepcopy(request)
    mutate(value)
    Path(f"/tmp/{name}.request.json").write_text(json.dumps(value, indent=2) + "\n")

def charger_variant(name, mutate):
    catalog = copy.deepcopy(charger_catalog)
    mutate(catalog["parts"][0])
    catalog_path = Path(f"/tmp/{name}.catalog.json")
    catalog_path.write_text(json.dumps(catalog, indent=2) + "\n")
    value = copy.deepcopy(request)
    value["charger"]["catalog_path"] = str(catalog_path)
    value["charger"]["catalog_digest"] = canonical_digest(catalog)
    value["charger"]["part_digest"] = canonical_digest(catalog["parts"][0])
    Path(f"/tmp/{name}.request.json").write_text(json.dumps(value, indent=2) + "\n")

request_variant("charge-overvoltage", lambda value: value["target_voltage"].__setitem__("value", 43.0))
request_variant("charge-overcurrent", lambda value: value["charge_current"].__setitem__("value", 10.0))
charger_variant("charge-range41", lambda part: part["properties"]["charge_voltage_max"].__setitem__("value", 41.0))
charger_variant("charge-power350", lambda part: part["properties"]["max_output_power"].__setitem__("value", 350.0))

base_bms_catalog = json.loads(Path("catalogs/jkbms-bd6a20s6p.catalog.json").read_text())

def bms_variant(name, mutate):
    catalog = copy.deepcopy(base_bms_catalog)
    mutate(catalog["parts"][0])
    catalog_path = Path(f"/tmp/{name}.catalog.json")
    catalog_path.write_text(json.dumps(catalog, indent=2) + "\n")
    value = copy.deepcopy(mechanism)
    for binding in value["extensions"]["org.emes.catalogs"]:
        if binding["id"] == "CAT_JKBMS_BD6A20S6P":
            binding["path"] = str(catalog_path)
            binding["digest"] = canonical_digest(catalog)
    for component in value["components"]:
        if component["id"] == "bms":
            component["metadata"]["org.emes.catalog_ref"]["digest"] = canonical_digest(catalog["parts"][0])
    Path(f"/tmp/{name}.mechanism.json").write_text(json.dumps(value, indent=2) + "\n")

bms_variant("charge-bms8", lambda part: part["properties"]["max_charge_current"].__setitem__("value", 8.0))
bms_variant("charge-bms-overvoltage", lambda part: part["properties"]["default_cell_overcharge_protection_voltage"].__setitem__("value", 4.25))
bms_variant("charge-bms-invalid-recovery", lambda part: part["properties"]["default_cell_overcharge_recovery_voltage"].__setitem__("value", 4.2))
bms_variant("charge-bms-measurement-margin", lambda part: part["properties"]["default_cell_overcharge_protection_voltage"].__setitem__("value", 4.197))
print("CREATED charge compatibility counterexamples and measurement-margin variant")
PY_VARIANTS

if python tools/battery_charge.py "$MECHANISM" --request /tmp/charge-overvoltage.request.json --out /tmp/invalid.json; then
  echo "expected charge over-voltage rejection" >&2
  exit 1
fi
echo "VALID charge-overvoltage-fail-closed"

if python tools/battery_charge.py "$MECHANISM" --request /tmp/charge-overcurrent.request.json --out /tmp/invalid.json; then
  echo "expected charge over-current rejection" >&2
  exit 1
fi
echo "VALID charge-overcurrent-fail-closed"

if python tools/battery_charge.py "$MECHANISM" --request /tmp/charge-range41.request.json --out /tmp/invalid.json; then
  echo "expected charger voltage-range rejection" >&2
  exit 1
fi
echo "VALID charger-range-fail-closed"

if python tools/battery_charge.py "$MECHANISM" --request /tmp/charge-power350.request.json --out /tmp/invalid.json; then
  echo "expected charger power-limit rejection" >&2
  exit 1
fi
echo "VALID charger-power-fail-closed"

if python tools/battery_charge.py /tmp/charge-bms8.mechanism.json --request "$REQUEST" --out /tmp/invalid.json; then
  echo "expected BMS charge-current rejection" >&2
  exit 1
fi
echo "VALID bms-charge-current-fail-closed"

if python tools/battery_charge.py /tmp/charge-bms-overvoltage.mechanism.json --request "$REQUEST" --out /tmp/invalid.json; then
  echo "expected BMS cell overcharge threshold rejection" >&2
  exit 1
fi
echo "VALID bms-cell-overcharge-threshold-fail-closed"

if python tools/battery_charge.py /tmp/charge-bms-invalid-recovery.mechanism.json --request "$REQUEST" --out /tmp/invalid.json; then
  echo "expected BMS cell overcharge recovery rejection" >&2
  exit 1
fi
echo "VALID bms-cell-overcharge-recovery-fail-closed"

python tools/battery_charge.py /tmp/charge-bms-measurement-margin.mechanism.json --request "$REQUEST" --out /tmp/charge-bms-measurement-margin.json
python - <<'PY_MEASUREMENT_MARGIN'
import json
import math
from pathlib import Path

evidence = json.loads(Path("/tmp/charge-bms-measurement-margin.json").read_text())
metrics = {item["id"]: item["value"] for item in evidence["metrics"]}
assert math.isclose(metrics["M_BMS_DEFAULT_CELL_OVERCHARGE_PROTECTION"], 4.197)
assert math.isclose(metrics["M_BMS_WORST_CASE_CELL_VOLTAGE_AT_TRIP"], 4.2)
assert math.isclose(metrics["M_BMS_MEASUREMENT_AWARE_OVERCHARGE_MARGIN"], 0.0, abs_tol=1e-12)
assert evidence["charge_compatibility"]["measurement_aware_cell_overcharge_guard_sufficient"] is True
assert evidence["cell_voltage_guard"]["configured_at_runtime"] is False
assert evidence["charge_compatibility"]["automatic_charging_approval"] is False
print("VALID measurement-aware 4.197V synthetic threshold margin")
PY_MEASUREMENT_MARGIN
