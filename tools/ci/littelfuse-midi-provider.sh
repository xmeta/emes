#!/usr/bin/env bash
set -euo pipefail

if ! command -v python >/dev/null 2>&1; then
  python() { python3 "$@"; }
fi

python tools/providers/littelfuse_midi.py normalize catalogs/sources/littelfuse-midi70v-40a.source.json /tmp/littelfuse.catalog.json

python - <<'PY'
import json
committed = json.load(open('catalogs/littelfuse-midi70v-40a.catalog.json'))
generated = json.load(open('/tmp/littelfuse.catalog.json'))
assert committed == generated
print('VALID deterministic-littelfuse-catalog-content')
PY

python tools/catalog.py validate catalogs/littelfuse-midi70v-40a.catalog.json

python - <<'PY'
import json
source = json.load(open('catalogs/sources/littelfuse-midi70v-40a.source.json'))
upstream = source['upstream']['datasheet_pdf']
assert upstream['integrity_status'] == 'raw_bytes_not_pinned'
assert upstream['sha256'] is None
assert upstream['bytes'] is None
print('VALID explicit-unpinned-raw-source-state')
PY
