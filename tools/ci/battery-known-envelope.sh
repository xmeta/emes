#!/usr/bin/env bash
set -euo pipefail

if ! command -v python >/dev/null 2>&1; then
  python() { python3 "$@"; }
fi

python tools/providers/synqor_nq60.py catalogs/sources/synqor-nq60w60hgc40.source.json /tmp/synqor-nq60.catalog.json
python - <<'PY2'
import json
assert json.load(open('catalogs/synqor-nq60w60hgc40.catalog.json')) == json.load(open('/tmp/synqor-nq60.catalog.json'))
print('VALID deterministic-synqor-nq60-catalog-content')
PY2
python tools/catalog.py validate catalogs/synqor-nq60w60hgc40.catalog.json

python tools/power.py examples/power-pack-molicel-p45b/mechanism.json --out /tmp/power.json --check-determinism

python tools/battery_pulse.py examples/power-pack-molicel-p45b/mechanism.json --analysis-request examples/power-pack-molicel-p45b/pulse-10s-soc50.json --power-evidence /tmp/power.json --out /tmp/pulse.json --check-determinism

python tools/battery_known_envelope.py examples/power-pack-molicel-p45b/mechanism.json --power-evidence /tmp/power.json --pulse-evidence /tmp/pulse.json --out /tmp/known-envelope.json --check-determinism

python - <<'PY'
import json
import math
from pathlib import Path
evidence = json.loads(Path('/tmp/known-envelope.json').read_text())
power = json.loads(Path('/tmp/power.json').read_text())
power_metrics = {item['id']: item['value'] for item in power['metrics']}
assert math.isclose(power_metrics['M_LOAD_EQUIV_PACK_CURRENT'], 14.619883040935674)
assert math.isclose(power_metrics['M_LOAD_MAX_PACK_CURRENT'], 21.05263157894737)
assert power_metrics['M_LOAD_MAX_PACK_CURRENT'] > power_metrics['M_LOAD_EQUIV_PACK_CURRENT']
assert evidence['limiting_candidate'] == 'converter_continuous_input_current'
candidates = {item['id']: item for item in evidence['candidates']}
assert math.isclose(candidates['cell_pulse_power']['scale_factor'], 6.384)
assert math.isclose(candidates['bms_continuous_current']['scale_factor'], 2.85)
assert math.isclose(candidates['converter_continuous_output_current']['scale_factor'], 2.88)
assert math.isclose(candidates['converter_continuous_input_current']['scale_factor'], 1.9)
metrics = {item['id']: item['value'] for item in evidence['metrics']}
assert math.isclose(metrics['M_KNOWN_CONVERTER_INPUT_CONTINUOUS_LOAD_SCALE'], 1.9)
assert math.isclose(metrics['M_KNOWN_COMPONENT_LOAD_SCALE_LIMIT'], 1.9)
assert math.isclose(metrics['M_KNOWN_COMPONENT_OUTPUT_POWER_ENVELOPE'], 950.0)
assert math.isclose(metrics['M_KNOWN_COMPONENT_INPUT_POWER_ENVELOPE'], 1000.0)
converter = next(item for item in power['selected_parts'] if item['component'] == 'dc_converter')
assert converter['part_id'] == 'SYNQOR_NQ60W60HGC40NRF_G'
assert converter['analysis_assumptions'] == [{
    'property': 'efficiency', 'value': 0.95, 'unit': '1',
    'source': 'SRC_EMES_NQ60_REFERENCE_CONFIGURATION',
    'basis': 'analysis_assumption',
}]
damping = power['power_topology']['converter_input_damping']
assert math.isclose(damping['capacitance'], 0.0015)
assert math.isclose(damping['minimum_capacitance'], 0.0015)
assert math.isclose(damping['esr'], 0.06)
assert math.isclose(damping['minimum_esr_exclusive'], 0.05)
assert math.isclose(damping['voltage_rating'], 63.0)
assert damping['check_scope'] == 'datasheet_minimums_only'
assert damping['system_stability_verified'] is False
assert math.isclose(power_metrics['M_CONVERTER_INPUT_DAMPING_CAPACITANCE_MARGIN'], 0.0)
assert math.isclose(power_metrics['M_CONVERTER_INPUT_DAMPING_ESR_MARGIN'], 0.01)
assert math.isclose(power_metrics['M_CONVERTER_INPUT_DAMPING_VOLTAGE_MARGIN'], 21.0)
assert any('not a manufacturer-guaranteed' in item for item in evidence['limitations'])
print('VERIFIED manufacturer-backed NQ60 current limits and required input damping declaration')
PY

python - <<'PY'
import copy, json, sys
from pathlib import Path
sys.path.insert(0, 'tools')
from catalog import canonical_digest

mechanism = json.load(open('examples/power-pack-molicel-p45b/mechanism.json'))
base_catalog = json.load(open('catalogs/synqor-nq60w60hgc40.catalog.json'))

def variant(name, mutate):
    catalog = copy.deepcopy(base_catalog)
    part = catalog['parts'][0]
    mutate(part)
    catalog_path = Path(f'/tmp/{name}.catalog.json')
    catalog_path.write_text(json.dumps(catalog, indent=2) + '\n')
    doc = copy.deepcopy(mechanism)
    for binding in doc['extensions']['org.emes.catalogs']:
        if binding['id'] == 'CAT_SYNQOR_NQ60':
            binding['path'] = str(catalog_path)
            binding['digest'] = canonical_digest(catalog)
    for component in doc['components']:
        if component['id'] == 'dc_converter':
            component['metadata']['org.emes.catalog_ref']['digest'] = canonical_digest(part)
    Path(f'/tmp/{name}.mechanism.json').write_text(json.dumps(doc, indent=2) + '\n')

variant('nq60-input30v', lambda p: p['properties']['input_voltage_min'].__setitem__('value', 30.0))
variant('nq60-output61v', lambda p: p['properties']['output_voltage'].__setitem__('value', 61.0))
variant('nq60-input10a', lambda p: p['properties']['continuous_input_current'].__setitem__('value', 10.0))
def mechanism_variant(name, mutate):
    doc = copy.deepcopy(mechanism)
    path = doc['extensions']['org.emes.power']['power_paths'][0]
    mutate(path)
    Path(f'/tmp/{name}.mechanism.json').write_text(json.dumps(doc, indent=2) + '\n')

mechanism_variant('nq60-damping-missing', lambda p: p.pop('converter_input_damping'))
mechanism_variant('nq60-damping-cap1499', lambda p: p['converter_input_damping']['capacitance'].__setitem__('value', 1499.0))
mechanism_variant('nq60-damping-esr50m', lambda p: p['converter_input_damping']['esr'].__setitem__('value', 0.05))
mechanism_variant('nq60-damping-30v', lambda p: p['converter_input_damping']['voltage_rating'].__setitem__('value', 30.0))
print('CREATED NQ60 counterexamples')
PY

if python tools/power.py /tmp/nq60-input30v.mechanism.json --out /tmp/nq60-input30v.json; then
  echo 'expected NQ60 input-range rejection' >&2
  exit 1
fi
echo 'VALID NQ60-input-range-fail-closed'

if python tools/power.py /tmp/nq60-output61v.mechanism.json --out /tmp/nq60-output61v.json; then
  echo 'expected NQ60 output-setpoint rejection' >&2
  exit 1
fi
echo 'VALID NQ60-output-setpoint-fail-closed'

if python tools/power.py /tmp/nq60-input10a.mechanism.json --out /tmp/nq60-input10a.json; then
  echo 'expected NQ60 input-current rejection' >&2
  exit 1
fi
echo 'VALID NQ60-input-current-fail-closed'

if python tools/power.py /tmp/nq60-damping-missing.mechanism.json --out /tmp/nq60-damping-missing.json; then
  echo 'expected NQ60 missing input damping rejection' >&2
  exit 1
fi
echo 'VALID NQ60-input-damping-missing-fail-closed'

if python tools/power.py /tmp/nq60-damping-cap1499.mechanism.json --out /tmp/nq60-damping-cap1499.json; then
  echo 'expected NQ60 low input capacitance rejection' >&2
  exit 1
fi
echo 'VALID NQ60-input-damping-capacitance-fail-closed'

if python tools/power.py /tmp/nq60-damping-esr50m.mechanism.json --out /tmp/nq60-damping-esr50m.json; then
  echo 'expected NQ60 ESR threshold rejection' >&2
  exit 1
fi
echo 'VALID NQ60-input-damping-esr-fail-closed'

if python tools/power.py /tmp/nq60-damping-30v.mechanism.json --out /tmp/nq60-damping-30v.json; then
  echo 'expected NQ60 damping voltage-rating rejection' >&2
  exit 1
fi
echo 'VALID NQ60-input-damping-voltage-fail-closed'

python - <<'PY'
import json
from pathlib import Path
evidence = json.loads(Path('/tmp/pulse.json').read_text())
next(item for item in evidence['inputs'] if item['role'] == 'power')['digest'] = 'sha256:' + '0' * 64
Path('/tmp/tampered-pulse.json').write_text(json.dumps(evidence))
PY
if python tools/battery_known_envelope.py examples/power-pack-molicel-p45b/mechanism.json --power-evidence /tmp/power.json --pulse-evidence /tmp/tampered-pulse.json --out /tmp/invalid.json; then
  echo 'ERROR: tampered pulse evidence unexpectedly accepted'
  exit 1
fi
echo 'EXPECTED-FAIL evidence lineage mismatch rejected'
