#!/usr/bin/env bash
set -euo pipefail

if ! command -v python >/dev/null 2>&1; then
  python() { python3 "$@"; }
fi

python tools/validate.py

python tools/check_acceptance.py

python tools/check_evidence.py

python tools/check_normalization.py

python tools/catalog.py validate catalogs/reference-motors.catalog.json

python tools/catalog.py import-motor-csv catalogs/raw/reference-motors.csv /tmp/reference-motors.catalog.json

python - <<'PY'
import json
from pathlib import Path
from tools.catalog import canonical_digest
committed = json.loads(Path('catalogs/reference-motors.catalog.json').read_text())
regenerated = json.loads(Path('/tmp/reference-motors.catalog.json').read_text())
assert canonical_digest(committed) == canonical_digest(regenerated)
print('DETERMINISTIC catalog-import')
PY

python tools/catalog.py validate catalogs/maxon-496661.catalog.json

python tools/providers/maxon.py normalize catalogs/sources/maxon-496661.source.json /tmp/maxon-496661.catalog.json

python - <<'PY'
import json
from pathlib import Path
from tools.catalog import canonical_digest
committed = json.loads(Path('catalogs/maxon-496661.catalog.json').read_text())
regenerated = json.loads(Path('/tmp/maxon-496661.catalog.json').read_text())
assert canonical_digest(committed) == canonical_digest(regenerated)
print('DETERMINISTIC maxon-normalization')
PY

python tools/catalog.py validate catalogs/molicel-p45b.catalog.json

python tools/providers/molicel.py normalize catalogs/sources/molicel-p45b.source.json /tmp/molicel-p45b.catalog.json

python - <<'PY'
import json
from pathlib import Path
from tools.catalog import canonical_digest
committed = json.loads(Path('catalogs/molicel-p45b.catalog.json').read_text())
regenerated = json.loads(Path('/tmp/molicel-p45b.catalog.json').read_text())
assert canonical_digest(committed) == canonical_digest(regenerated)
part = committed['parts'][0]
props = part['properties']
assert props['continuous_discharge_current']['conditions'] == [
    {'parameter': 'cell_discharge_cutoff_temperature', 'op': '<=', 'value': 80.0, 'unit': 'degC'}
]
assert props['maximum_charge_current']['conditions'] == [
    {'parameter': 'cell_charge_cutoff_temperature', 'op': '<=', 'value': 70.0, 'unit': 'degC'}
]
assert props['ac_impedance_typical']['conditions'][0]['value'] == 30.0
assert props['dc_impedance_typical']['conditions'][0]['value'] == 50.0
assert props['power_output_10s_soc50'] == {
    'value': 168.0,
    'unit': 'W',
    'source': 'SRC_MOLICEL_P45B_PERFORMANCE_PAGE',
    'conditions': [
        {'parameter': 'duration', 'op': '==', 'value': 10.0, 'unit': 's'},
        {'parameter': 'soc', 'op': '==', 'value': 50.0, 'unit': '%'}
    ]
}
assert props['power_output_10s_soc90'] == {
    'value': 184.0,
    'unit': 'W',
    'source': 'SRC_MOLICEL_P45B_PERFORMANCE_PAGE',
    'conditions': [
        {'parameter': 'duration', 'op': '==', 'value': 10.0, 'unit': 's'},
        {'parameter': 'soc', 'op': '==', 'value': 90.0, 'unit': '%'}
    ]
}
assert canonical_digest(committed) == 'sha256:bce19e9220c1d77091310da16dd91d64f3689cae80277e356a66a45ad0847b56'
assert canonical_digest(part) == 'sha256:b98c1e3f071b945fbbd3bfc8f44fb0faa17b17ec01017e1716ae72e6d7e6bff6'
print('DETERMINISTIC molicel-normalization with conditional ratings and SOC power points')
PY

python tools/catalog.py validate catalogs/jkbms-bd6a20s6p.catalog.json

python tools/providers/jkbms.py normalize catalogs/sources/jkbms-bd6a20s6p.source.json /tmp/jkbms-bd6a20s6p.catalog.json

python - <<'PY'
import json
from pathlib import Path
from tools.catalog import canonical_digest
committed = json.loads(Path('catalogs/jkbms-bd6a20s6p.catalog.json').read_text())
regenerated = json.loads(Path('/tmp/jkbms-bd6a20s6p.catalog.json').read_text())
assert canonical_digest(committed) == canonical_digest(regenerated)
part = committed['parts'][0]
props = part['properties']
assert part['id'] == 'JKBMS_JK_BD6A20S_6P'
assert props['min_series_cells']['value'] == 7.0
assert props['max_series_cells']['value'] == 20.0
assert props['min_pack_voltage']['value'] == 20.0
assert props['max_pack_voltage']['value'] == 100.0
assert props['continuous_discharge_current']['value'] == 60.0
assert props['default_cell_discharge_cutoff_temperature']['value'] == 70.0
assert canonical_digest(committed) == 'sha256:9e9dfc04fe5ca80cba10e0202b75c5e40a6fea3c35c68691763c2f41641f26c3'
assert canonical_digest(part) == 'sha256:9d6d60ff9d22ccc3646736098296108f86b6baaaaefd1c42695ac4a5c37fb068'
print('DETERMINISTIC jkbms-normalization with source-backed default settings')
PY

python tools/catalog.py validate catalogs/reference-bms-thermal.catalog.json

python tools/catalog.py validate catalogs/reference-power.catalog.json

python tools/catalog.py validate catalogs/synqor-nq60w60hgc40.catalog.json

python tools/providers/synqor_nq60.py catalogs/sources/synqor-nq60w60hgc40.source.json /tmp/synqor-nq60w60hgc40.catalog.json

python - <<'PY2'
import json
from pathlib import Path
from tools.catalog import canonical_digest
committed = json.loads(Path('catalogs/synqor-nq60w60hgc40.catalog.json').read_text())
regenerated = json.loads(Path('/tmp/synqor-nq60w60hgc40.catalog.json').read_text())
assert committed == regenerated
part = committed['parts'][0]
props = part['properties']
assert part['id'] == 'SYNQOR_NQ60W60HGC40NRF_G'
assert props['input_voltage_min']['value'] == 9.0
assert props['input_voltage_max']['value'] == 60.0
assert props['output_voltage_min']['value'] == 0.0
assert props['output_voltage_max']['value'] == 60.0
assert props['continuous_input_current']['value'] == 40.0
assert props['continuous_output_current']['value'] == 40.0
assert props['external_input_capacitance_min']['value'] == 1500.0
assert props['external_input_capacitance_min']['unit'] == 'uF'
assert props['external_input_capacitance_esr_min']['value'] == 0.05
assert props['external_input_capacitance_esr_min']['unit'] == 'ohm'
assert props['output_voltage']['value'] == 36.0
assert props['efficiency']['value'] == 0.95
assert props['efficiency']['source'] == 'SRC_EMES_NQ60_REFERENCE_CONFIGURATION'
assert props['efficiency_basis']['value'] == 'analysis_assumption'
assert canonical_digest(committed) == 'sha256:7251fb11baab05158b654341a69fce7baa6f713eed4e13dabe8888ee1525753b'
assert canonical_digest(part) == 'sha256:85d7c67b99a5201b7564ffd842957ad11984e24deb5043ba449aecbc2b1d0bbe'
print('DETERMINISTIC SynQor NQ60 normalization with explicit EMES efficiency assumption')
PY2

python tools/validate.py examples/power-pack/mechanism.json

python tools/power.py examples/power-pack/mechanism.json --out generated/power-pack/evidence.json --check-determinism

python tools/check_design_iteration.py

python tools/validate.py examples/power-supply/mechanism.json

python tools/power.py examples/power-supply/mechanism.json --out generated/power-supply/evidence.json --check-determinism

python tools/validate.py examples/power-pack-molicel-p45b/mechanism.json

python tools/power.py examples/power-pack-molicel-p45b/mechanism.json --out generated/power-pack-molicel-p45b/evidence.json --check-determinism

python tools/battery_sag.py examples/power-pack-molicel-p45b/mechanism.json --power-evidence generated/power-pack-molicel-p45b/evidence.json --out generated/power-pack-molicel-p45b/sag-evidence.json --check-determinism

python - <<'PY'
import json
import math
from pathlib import Path
evidence = json.loads(Path('generated/power-pack-molicel-p45b/evidence.json').read_text())
checks = evidence['rating_condition_results']
assert len(checks) == 1
check = checks[0]
assert check['rated_part'] == 'MOLICEL_INR_21700_P45B'
assert check['rated_property'] == 'continuous_discharge_current'
assert check['condition_parameter'] == 'cell_discharge_cutoff_temperature'
assert check['condition_op'] == '<='
assert check['condition_value'] == 80.0
assert check['control_component'] == 'bms'
assert check['control_part'] == 'JKBMS_JK_BD6A20S_6P'
assert check['control_property'] == 'default_cell_discharge_cutoff_temperature'
assert check['design_parameter'] == 'P_BMS_DISCHARGE_OVERTEMP'
assert check['control_value'] == 70.0
assert check['status'] == 'pass'
metrics = {item['id']: item['value'] for item in evidence['metrics']}
assert metrics['M_PACK_CONTINUOUS_CURRENT'] == 60.0
assert metrics['M_PACK_NOMINAL_ENERGY'] == 324.0
assert math.isclose(metrics['M_PACK_TOTAL_MASS'], 2.114)
assert metrics['M_BMS_MIN_SERIES_MARGIN'] == 3.0
assert metrics['M_BMS_SERIES_MARGIN'] == 10.0
assert metrics['M_BMS_MIN_VOLTAGE_MARGIN'] == 5.0
assert metrics['M_BMS_VOLTAGE_MARGIN'] == 58.0

sag = json.loads(Path('generated/power-pack-molicel-p45b/sag-evidence.json').read_text())
assert sag['method'] == 'first_order_resistive_drop_at_nominal_point'
assert len(sag['condition_results']) == 1
soc = sag['condition_results'][0]
assert soc['condition_parameter'] == 'soc'
assert soc['design_parameter'] == 'P_SAG_SOC'
assert soc['design_value'] == 50.0
assert soc['condition_value'] == 50.0
assert soc['status'] == 'pass'
sag_metrics = {item['id']: item['value'] for item in sag['metrics']}
assert math.isclose(sag_metrics['M_SAG_REFERENCE_PACK_CURRENT'], 14.619883040935674)
assert math.isclose(sag_metrics['M_SAG_REFERENCE_CELL_CURRENT'], 7.309941520467837)
assert math.isclose(sag_metrics['M_CELL_DC_IMPEDANCE_TYPICAL'], 0.015)
assert math.isclose(sag_metrics['M_PACK_REFERENCE_DC_RESISTANCE'], 0.075)
assert math.isclose(sag_metrics['M_PACK_REFERENCE_RESISTIVE_DROP'], 1.0964912280701755)
assert math.isclose(sag_metrics['M_PACK_REFERENCE_SAG_FRACTION'], 0.030458089668615986)
print('VERIFIED real-cell conditional rating, BMS clamp, and 50% SOC resistive-drop evidence')
PY

python - <<'PY'
import json
from pathlib import Path
source = Path('examples/power-pack-molicel-p45b/mechanism.json')
mechanism = json.loads(source.read_text())
for parameter in mechanism['parameters']:
    if parameter['id'] == 'P_SAG_SOC':
        parameter['value']['value'] = 40.0
Path('/tmp/p45b-sag-soc40.json').write_text(json.dumps(mechanism))
PY
python tools/power.py /tmp/p45b-sag-soc40.json --out /tmp/p45b-power-soc40.json
if python tools/battery_sag.py /tmp/p45b-sag-soc40.json --power-evidence /tmp/p45b-power-soc40.json --out /tmp/p45b-sag-soc40-evidence.json; then
  echo 'ERROR: 50% SOC impedance unexpectedly accepted at 40% SOC'
  exit 1
fi
echo 'EXPECTED-FAIL source-conditioned impedance rejected at unsupported 40% SOC'

python - <<'PY'
import json
from pathlib import Path
mechanism = json.loads(Path('examples/power-pack-molicel-p45b/mechanism.json').read_text())
for parameter in mechanism['parameters']:
    if parameter['id'] == 'P_BMS_DISCHARGE_OVERTEMP':
        parameter['value']['value'] = 75.0
Path('/tmp/p45b-unbacked-cutoff.json').write_text(json.dumps(mechanism))
PY
if python tools/power.py /tmp/p45b-unbacked-cutoff.json --out /tmp/p45b-unbacked-cutoff-evidence.json; then
  echo 'ERROR: unbacked 75 degC BMS setting unexpectedly accepted'
  exit 1
fi
echo 'EXPECTED-FAIL BMS setting rejected when it is not backed by the selected source property'

python - <<'PY'
import json
from pathlib import Path
mechanism = json.loads(Path('examples/power-pack-molicel-p45b/mechanism.json').read_text())
mechanism['extensions']['org.emes.power']['battery_packs'][0].pop('condition_bindings')
Path('/tmp/p45b-missing-control-binding.json').write_text(json.dumps(mechanism))
PY
if python tools/power.py /tmp/p45b-missing-control-binding.json --out /tmp/p45b-missing-control-binding-evidence.json; then
  echo 'ERROR: P45B conditional rating unexpectedly accepted without explicit JKBMS setting binding'
  exit 1
fi
echo 'EXPECTED-FAIL conditional cell rating rejected without an executable JKBMS setting binding'

python - <<'PY'
import json
from pathlib import Path
mechanism = json.loads(Path('examples/power-pack-molicel-p45b/mechanism.json').read_text())
for parameter in mechanism['parameters']:
    if parameter['id'] == 'P_PACK_SERIES':
        parameter['value']['value'] = 6.0
Path('/tmp/p45b-bms-min-series.json').write_text(json.dumps(mechanism))
PY
if python tools/power.py /tmp/p45b-bms-min-series.json --out /tmp/p45b-bms-min-series-evidence.json; then
  echo 'ERROR: 6S P45B pack unexpectedly accepted below JKBMS 7S Li-ion minimum'
  exit 1
fi
echo 'EXPECTED-FAIL JKBMS minimum Li-ion series count enforced'

python - <<'PY'
import json
from pathlib import Path
mechanism = json.loads(Path('examples/power-pack-molicel-p45b/mechanism.json').read_text())
for parameter in mechanism['parameters']:
    if parameter['id'] == 'P_PACK_SERIES':
        parameter['value']['value'] = 7.0
Path('/tmp/p45b-bms-undervoltage.json').write_text(json.dumps(mechanism))
PY
if python tools/power.py /tmp/p45b-bms-undervoltage.json --out /tmp/p45b-bms-undervoltage-evidence.json; then
  echo 'ERROR: 7S P45B pack unexpectedly accepted below JKBMS 20 V minimum supply voltage'
  exit 1
fi
echo 'EXPECTED-FAIL JKBMS minimum pack voltage enforced for 7S P45B pack'

python - <<'PY'
import json
from pathlib import Path
source = Path('examples/power-pack/mechanism.json')
document = json.loads(source.read_text())
for parameter in document['parameters']:
    if parameter['id'] == 'P_PACK_SERIES':
        parameter['value']['value'] = 13.0
Path('/tmp/invalid-power.json').write_text(json.dumps(document))
PY
if python tools/power.py /tmp/invalid-power.json --out /tmp/invalid-evidence.json; then
  echo 'ERROR: incompatible 13S pack unexpectedly passed 12S BMS validation'
  exit 1
fi
echo 'EXPECTED-FAIL incompatible 13S pack rejected'

python - <<'PY'
import json
from pathlib import Path
source = Path('examples/power-pack/mechanism.json')
document = json.loads(source.read_text())
document['load_cases'][0]['loads'][0]['value']['value'] = 800.0
Path('/tmp/overload-power.json').write_text(json.dumps(document))
PY
if python tools/power.py /tmp/overload-power.json --out /tmp/overload-evidence.json; then
  echo 'ERROR: 800 W load unexpectedly passed battery-path limits'
  exit 1
fi
echo 'EXPECTED-FAIL battery-path overload rejected'

python - <<'PY'
import json
from pathlib import Path
source = Path('examples/power-supply/mechanism.json')
document = json.loads(source.read_text())
document['load_cases'][0]['loads'][0]['value']['value'] = 500.0
Path('/tmp/overload-psu.json').write_text(json.dumps(document))
PY
if python tools/power.py /tmp/overload-psu.json --out /tmp/overload-psu-evidence.json; then
  echo 'ERROR: 500 W load unexpectedly passed 480 W power-supply limit'
  exit 1
fi
echo 'EXPECTED-FAIL fixed power-supply overload rejected'

python tools/run_reference_loop.py --out-dir generated/two-link-arm --check-determinism
