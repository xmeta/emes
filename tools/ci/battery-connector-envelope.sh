#!/usr/bin/env bash
set -euo pipefail

if ! command -v python >/dev/null 2>&1; then
  python() { python3 "$@"; }
fi

python tools/providers/anderson_sb50.py catalogs/sources/anderson-sb50-992g4-5900.source.json /tmp/anderson-sb50.catalog.json
python tools/providers/littelfuse_dcnhf60.py catalogs/sources/littelfuse-dcnhf60ng12-f.source.json /tmp/littelfuse-dcnhf60.catalog.json
python tools/providers/southwire_soow.py catalogs/sources/southwire-soow-569973.source.json /tmp/southwire-soow.catalog.json

python - <<'PY'
import json
pairs = [
    ('catalogs/anderson-sb50-992g4-5900.catalog.json', '/tmp/anderson-sb50.catalog.json'),
    ('catalogs/littelfuse-dcnhf60ng12-f.catalog.json', '/tmp/littelfuse-dcnhf60.catalog.json'),
    ('catalogs/southwire-soow-569973.catalog.json', '/tmp/southwire-soow.catalog.json'),
]
for committed_path, generated_path in pairs:
    committed = json.load(open(committed_path))
    generated = json.load(open(generated_path))
    assert committed == generated
print('VALID deterministic-connected-path-catalog-content')
PY

python tools/catalog.py validate catalogs/anderson-sb50-992g4-5900.catalog.json
python tools/catalog.py validate catalogs/littelfuse-dcnhf60ng12-f.catalog.json
python tools/catalog.py validate catalogs/southwire-soow-569973.catalog.json

python tools/validate.py examples/power-pack-molicel-p45b-connected/mechanism.json

python tools/power.py examples/power-pack-molicel-p45b-connected/mechanism.json --out /tmp/connected-power.json --check-determinism

python tools/battery_pulse.py examples/power-pack-molicel-p45b-connected/mechanism.json --analysis-request examples/power-pack-molicel-p45b/pulse-10s-soc50.json --power-evidence /tmp/connected-power.json --out /tmp/connected-pulse.json --check-determinism

python tools/battery_known_envelope.py examples/power-pack-molicel-p45b-connected/mechanism.json --power-evidence /tmp/connected-power.json --pulse-evidence /tmp/connected-pulse.json --out /tmp/connected-known.json --check-determinism

python tools/battery_connector_envelope.py examples/power-pack-molicel-p45b-connected/mechanism.json --power-evidence /tmp/connected-power.json --known-envelope-evidence /tmp/connected-known.json --out /tmp/connected-path.json --check-determinism

python - <<'PY'
import json, math
e = json.load(open('/tmp/connected-path.json'))
m = {x['id']: x['value'] for x in e['metrics']}
assert e['limiting_candidate'] == 'upstream_known_component_envelope'
assert len(e['conductors']) == 1
wire = e['conductors'][0]
assert wire['component'] == 'main_conductor'
assert wire['part'] == 'SOUTHWIRE_SOOW_569973_6AWG_2C'
assert math.isclose(wire['allowable_ampacity'], 55.0)
assert math.isclose(wire['conductor_size'], 6.0)
assert math.isclose(wire['rated_voltage'], 600.0)
assert math.isclose(wire['max_pack_current'], 21.05263157894737, rel_tol=1e-12)
assert math.isclose(m['M_KNOWN_CONDUCTOR_AMPACITY_LOAD_SCALE'], 2.6125, rel_tol=1e-12)
assert math.isclose(m['M_KNOWN_CONDUCTOR_VOLTAGE_MARGIN'], 558.0, rel_tol=1e-12)
assert len(e['connectors']) == 1
c = e['connectors'][0]
assert c['component'] == 'main_connector'
assert c['part'] == 'ANDERSON_SB50_992G4_5900_WTW'
assert math.isclose(c['rated_current'], 50.0)
assert math.isclose(c['rated_voltage_dc'], 250.0)
assert math.isclose(c['max_pack_current'], 21.05263157894737, rel_tol=1e-12)
assert math.isclose(m['M_KNOWN_CONNECTOR_RATED_LOAD_SCALE'], 2.375, rel_tol=1e-12)
assert math.isclose(m['M_KNOWN_CONNECTOR_VOLTAGE_MARGIN'], 208.0, rel_tol=1e-12)
contactor = e['contactor']
assert contactor['component'] == 'main_contactor'
assert contactor['part'] == 'LITTELFUSE_DCNHF60NG12_F'
assert math.isclose(contactor['continuous_current'], 60.0)
assert math.isclose(contactor['operating_voltage_min_dc'], 12.0)
assert math.isclose(contactor['operating_voltage_max_dc'], 1000.0)
assert math.isclose(contactor['max_pack_current'], 21.05263157894737, rel_tol=1e-12)
assert math.isclose(m['M_KNOWN_CONTACTOR_CONTINUOUS_LOAD_SCALE'], 2.85, rel_tol=1e-12)
assert math.isclose(m['M_KNOWN_CONTACTOR_MIN_VOLTAGE_MARGIN'], 13.0, rel_tol=1e-12)
assert math.isclose(m['M_KNOWN_CONTACTOR_MAX_VOLTAGE_MARGIN'], 958.0, rel_tol=1e-12)
assert math.isclose(m['M_KNOWN_PATH_LOAD_SCALE_LIMIT'], 1.9, rel_tol=1e-12)
assert math.isclose(m['M_KNOWN_PATH_OUTPUT_POWER_ENVELOPE'], 950.0, rel_tol=1e-12)
assert e['termination_compatibility'] == [{'conductor_component': 'main_conductor', 'connector_component': 'main_connector', 'conductor_size': 6.0, 'contact_wire_size': 6.0, 'unit': 'AWG', 'compatible': True}]
assert math.isclose(c['contact_wire_size'], 6.0)
print('VALID source-coherent-6awg-sb50-reference-path-envelope')
PY

python - <<'PY'
import copy, json, sys
from pathlib import Path
sys.path.insert(0, 'tools')
from catalog import canonical_digest

mechanism = json.load(open('examples/power-pack-molicel-p45b-connected/mechanism.json'))
connector_catalog = json.load(open('catalogs/anderson-sb50-992g4-5900.catalog.json'))
contactor_catalog = json.load(open('catalogs/littelfuse-dcnhf60ng12-f.catalog.json'))
conductor_catalog = json.load(open('catalogs/southwire-soow-569973.catalog.json'))

def write_variant(name, catalog_id, component_id, catalog):
    part = catalog['parts'][0]
    catalog_path = Path(f'/tmp/{name}.catalog.json')
    catalog_path.write_text(json.dumps(catalog, indent=2) + '\n')
    doc = copy.deepcopy(mechanism)
    for binding in doc['extensions']['org.emes.catalogs']:
        if binding['id'] == catalog_id:
            binding['path'] = str(catalog_path)
            binding['digest'] = canonical_digest(catalog)
    for component in doc['components']:
        if component['id'] == component_id:
            component['metadata']['org.emes.catalog_ref']['digest'] = canonical_digest(part)
    Path(f'/tmp/{name}.mechanism.json').write_text(json.dumps(doc, indent=2) + '\n')

catalog = copy.deepcopy(connector_catalog)
catalog['parts'][0]['properties']['rated_current']['value'] = 10.0
write_variant('connector10a', 'CAT_ANDERSON_SB50', 'main_connector', catalog)

catalog = copy.deepcopy(connector_catalog)
catalog['parts'][0]['properties']['rated_voltage_dc']['value'] = 30.0
write_variant('connector30v', 'CAT_ANDERSON_SB50', 'main_connector', catalog)

catalog = copy.deepcopy(contactor_catalog)
catalog['parts'][0]['properties']['continuous_current']['value'] = 10.0
write_variant('contactor10a', 'CAT_LITTELFUSE_DCNHF60', 'main_contactor', catalog)

catalog = copy.deepcopy(contactor_catalog)
catalog['parts'][0]['properties']['operating_voltage_max_dc']['value'] = 30.0
write_variant('contactor30v', 'CAT_LITTELFUSE_DCNHF60', 'main_contactor', catalog)

catalog = copy.deepcopy(conductor_catalog)
catalog['parts'][0]['properties']['allowable_ampacity']['value'] = 15.0
write_variant('conductor15a', 'CAT_SOUTHWIRE_SOOW_569973', 'main_conductor', catalog)

catalog = copy.deepcopy(conductor_catalog)
catalog['parts'][0]['properties']['rated_voltage']['value'] = 30.0
write_variant('conductor30v', 'CAT_SOUTHWIRE_SOOW_569973', 'main_conductor', catalog)

catalog = copy.deepcopy(conductor_catalog)
catalog['parts'][0]['properties']['conductor_size']['value'] = 10.0
write_variant('conductor10awg', 'CAT_SOUTHWIRE_SOOW_569973', 'main_conductor', catalog)
print('CREATED connector, contactor, and conductor counterexamples')
PY

python tools/power.py /tmp/conductor15a.mechanism.json --out /tmp/conductor15a-power.json --check-determinism
python tools/battery_pulse.py /tmp/conductor15a.mechanism.json --analysis-request examples/power-pack-molicel-p45b/pulse-10s-soc50.json --power-evidence /tmp/conductor15a-power.json --out /tmp/conductor15a-pulse.json --check-determinism
python tools/battery_known_envelope.py /tmp/conductor15a.mechanism.json --power-evidence /tmp/conductor15a-power.json --pulse-evidence /tmp/conductor15a-pulse.json --out /tmp/conductor15a-known.json --check-determinism
python tools/battery_connector_envelope.py /tmp/conductor15a.mechanism.json --power-evidence /tmp/conductor15a-power.json --known-envelope-evidence /tmp/conductor15a-known.json --out /tmp/conductor15a-path.json --check-determinism

python - <<'PY'
import json, math
e = json.load(open('/tmp/conductor15a-path.json'))
m = {x['id']: x['value'] for x in e['metrics']}
assert e['limiting_candidate'] == 'conductor_allowable_ampacity:main_conductor'
assert math.isclose(m['M_KNOWN_CONDUCTOR_AMPACITY_LOAD_SCALE'], 0.7125, rel_tol=1e-12)
assert math.isclose(m['M_KNOWN_PATH_OUTPUT_POWER_ENVELOPE'], 356.25, rel_tol=1e-12)
print('VALID conductor-becomes-weakest-link')
PY

python tools/power.py /tmp/conductor30v.mechanism.json --out /tmp/conductor30v-power.json --check-determinism
python tools/battery_pulse.py /tmp/conductor30v.mechanism.json --analysis-request examples/power-pack-molicel-p45b/pulse-10s-soc50.json --power-evidence /tmp/conductor30v-power.json --out /tmp/conductor30v-pulse.json --check-determinism
python tools/battery_known_envelope.py /tmp/conductor30v.mechanism.json --power-evidence /tmp/conductor30v-power.json --pulse-evidence /tmp/conductor30v-pulse.json --out /tmp/conductor30v-known.json --check-determinism

if python tools/battery_connector_envelope.py /tmp/conductor30v.mechanism.json --power-evidence /tmp/conductor30v-power.json --known-envelope-evidence /tmp/conductor30v-known.json --out /tmp/conductor30v-path.json; then
  echo 'expected conductor undervoltage rejection' >&2
  exit 1
fi
echo 'VALID conductor-voltage-fail-closed'

python tools/power.py /tmp/conductor10awg.mechanism.json --out /tmp/conductor10awg-power.json --check-determinism
python tools/battery_pulse.py /tmp/conductor10awg.mechanism.json --analysis-request examples/power-pack-molicel-p45b/pulse-10s-soc50.json --power-evidence /tmp/conductor10awg-power.json --out /tmp/conductor10awg-pulse.json --check-determinism
python tools/battery_known_envelope.py /tmp/conductor10awg.mechanism.json --power-evidence /tmp/conductor10awg-power.json --pulse-evidence /tmp/conductor10awg-pulse.json --out /tmp/conductor10awg-known.json --check-determinism
if python tools/battery_connector_envelope.py /tmp/conductor10awg.mechanism.json --power-evidence /tmp/conductor10awg-power.json --known-envelope-evidence /tmp/conductor10awg-known.json --out /tmp/conductor10awg-path.json; then
  echo 'expected direct 10 AWG to 5900 termination rejection' >&2
  exit 1
fi
echo 'VALID conductor-contact-wire-size-fail-closed'

python tools/power.py /tmp/connector10a.mechanism.json --out /tmp/connector10a-power.json --check-determinism
python tools/battery_pulse.py /tmp/connector10a.mechanism.json --analysis-request examples/power-pack-molicel-p45b/pulse-10s-soc50.json --power-evidence /tmp/connector10a-power.json --out /tmp/connector10a-pulse.json --check-determinism
python tools/battery_known_envelope.py /tmp/connector10a.mechanism.json --power-evidence /tmp/connector10a-power.json --pulse-evidence /tmp/connector10a-pulse.json --out /tmp/connector10a-known.json --check-determinism
python tools/battery_connector_envelope.py /tmp/connector10a.mechanism.json --power-evidence /tmp/connector10a-power.json --known-envelope-evidence /tmp/connector10a-known.json --out /tmp/connector10a-path.json --check-determinism

python - <<'PY'
import json, math
e = json.load(open('/tmp/connector10a-path.json'))
m = {x['id']: x['value'] for x in e['metrics']}
assert e['limiting_candidate'] == 'connector_rated_current:main_connector'
assert math.isclose(m['M_KNOWN_CONNECTOR_RATED_LOAD_SCALE'], 0.475, rel_tol=1e-12)
assert math.isclose(m['M_KNOWN_PATH_OUTPUT_POWER_ENVELOPE'], 237.5, rel_tol=1e-12)
print('VALID connector-becomes-weakest-link')
PY

python tools/power.py /tmp/connector30v.mechanism.json --out /tmp/connector30v-power.json --check-determinism
python tools/battery_pulse.py /tmp/connector30v.mechanism.json --analysis-request examples/power-pack-molicel-p45b/pulse-10s-soc50.json --power-evidence /tmp/connector30v-power.json --out /tmp/connector30v-pulse.json --check-determinism
python tools/battery_known_envelope.py /tmp/connector30v.mechanism.json --power-evidence /tmp/connector30v-power.json --pulse-evidence /tmp/connector30v-pulse.json --out /tmp/connector30v-known.json --check-determinism

if python tools/battery_connector_envelope.py /tmp/connector30v.mechanism.json --power-evidence /tmp/connector30v-power.json --known-envelope-evidence /tmp/connector30v-known.json --out /tmp/connector30v-path.json; then
  echo 'expected connector undervoltage rejection' >&2
  exit 1
fi
echo 'VALID connector-voltage-fail-closed'

python tools/power.py /tmp/contactor10a.mechanism.json --out /tmp/contactor10a-power.json --check-determinism
python tools/battery_pulse.py /tmp/contactor10a.mechanism.json --analysis-request examples/power-pack-molicel-p45b/pulse-10s-soc50.json --power-evidence /tmp/contactor10a-power.json --out /tmp/contactor10a-pulse.json --check-determinism
python tools/battery_known_envelope.py /tmp/contactor10a.mechanism.json --power-evidence /tmp/contactor10a-power.json --pulse-evidence /tmp/contactor10a-pulse.json --out /tmp/contactor10a-known.json --check-determinism
python tools/battery_connector_envelope.py /tmp/contactor10a.mechanism.json --power-evidence /tmp/contactor10a-power.json --known-envelope-evidence /tmp/contactor10a-known.json --out /tmp/contactor10a-path.json --check-determinism

python - <<'PY'
import json, math
e = json.load(open('/tmp/contactor10a-path.json'))
m = {x['id']: x['value'] for x in e['metrics']}
assert e['limiting_candidate'] == 'contactor_continuous_current:main_contactor'
assert math.isclose(m['M_KNOWN_CONTACTOR_CONTINUOUS_LOAD_SCALE'], 0.475, rel_tol=1e-12)
assert math.isclose(m['M_KNOWN_PATH_OUTPUT_POWER_ENVELOPE'], 237.5, rel_tol=1e-12)
print('VALID contactor-becomes-weakest-link')
PY

python tools/power.py /tmp/contactor30v.mechanism.json --out /tmp/contactor30v-power.json --check-determinism
python tools/battery_pulse.py /tmp/contactor30v.mechanism.json --analysis-request examples/power-pack-molicel-p45b/pulse-10s-soc50.json --power-evidence /tmp/contactor30v-power.json --out /tmp/contactor30v-pulse.json --check-determinism
python tools/battery_known_envelope.py /tmp/contactor30v.mechanism.json --power-evidence /tmp/contactor30v-power.json --pulse-evidence /tmp/contactor30v-pulse.json --out /tmp/contactor30v-known.json --check-determinism

if python tools/battery_connector_envelope.py /tmp/contactor30v.mechanism.json --power-evidence /tmp/contactor30v-power.json --known-envelope-evidence /tmp/contactor30v-known.json --out /tmp/contactor30v-path.json; then
  echo 'expected contactor undervoltage rejection' >&2
  exit 1
fi
echo 'VALID contactor-voltage-fail-closed'

python - <<'PY'
import json
e = json.load(open('/tmp/connected-known.json'))
next(item for item in e['inputs'] if item['role'] == 'power')['digest'] = 'sha256:' + '0' * 64
json.dump(e, open('/tmp/tampered-known.json', 'w'), indent=2)
PY
if python tools/battery_connector_envelope.py examples/power-pack-molicel-p45b-connected/mechanism.json --power-evidence /tmp/connected-power.json --known-envelope-evidence /tmp/tampered-known.json --out /tmp/tampered-path.json; then
  echo 'expected parent evidence digest rejection' >&2
  exit 1
fi
echo 'VALID connector-parent-evidence-fail-closed'
