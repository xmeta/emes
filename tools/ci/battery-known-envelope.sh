#!/usr/bin/env bash
set -euo pipefail

if ! command -v python >/dev/null 2>&1; then
  python() { python3 "$@"; }
fi

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
assert evidence['limiting_candidate'] == 'converter_continuous_output_current'
candidates = {item['id']: item for item in evidence['candidates']}
assert math.isclose(candidates['cell_pulse_power']['scale_factor'], 6.384)
assert math.isclose(candidates['bms_continuous_current']['scale_factor'], 2.85)
assert math.isclose(candidates['converter_continuous_output_current']['scale_factor'], 1.44)
metrics = {item['id']: item['value'] for item in evidence['metrics']}
assert math.isclose(metrics['M_KNOWN_COMPONENT_LOAD_SCALE_LIMIT'], 1.44)
assert math.isclose(metrics['M_KNOWN_COMPONENT_OUTPUT_POWER_ENVELOPE'], 720.0)
assert math.isclose(metrics['M_KNOWN_COMPONENT_INPUT_POWER_ENVELOPE'], 757.8947368421053)
print('VERIFIED real JKBMS leaves converter as weakest currently-known component')
PY

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
