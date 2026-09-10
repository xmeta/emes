#!/usr/bin/env bash
set -euo pipefail

if ! command -v python >/dev/null 2>&1; then
  python() { python3 "$@"; }
fi

python tools/providers/jkbms.py verify-snapshot catalogs/sources/jkbms-bd6a20s6p.source.json

python tools/providers/jkbms.py normalize catalogs/sources/jkbms-bd6a20s6p.source.json /tmp/jkbms.catalog.json

python - <<'PY'
import json
committed = json.load(open('catalogs/jkbms-bd6a20s6p.catalog.json'))
generated = json.load(open('/tmp/jkbms.catalog.json'))
assert committed == generated
print('VALID deterministic-jkbms-catalog-content')
PY

python tools/catalog.py validate catalogs/jkbms-bd6a20s6p.catalog.json
