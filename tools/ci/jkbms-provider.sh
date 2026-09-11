#!/usr/bin/env bash
set -euo pipefail

if ! command -v python >/dev/null 2>&1; then
  python() { python3 "$@"; }
fi


python - <<'PY_RETRY'
import sys
import urllib.error

sys.path.insert(0, "tools/providers")
import jkbms

original_fetch = jkbms.fetch
events = [urllib.error.URLError("temporary"), b"<html>retry</html>", b"%PDF-test"]
calls = 0

def flaky_fetch(_url):
    global calls
    calls += 1
    event = events.pop(0)
    if isinstance(event, Exception):
        raise event
    return event

try:
    jkbms.fetch = flaky_fetch
    assert jkbms.fetch_pdf("unused", attempts=3, retry_delay=0) == b"%PDF-test"
    assert calls == 3

    jkbms.fetch = lambda _url: b"<html>still not pdf</html>"
    try:
        jkbms.fetch_pdf("unused", attempts=2, retry_delay=0)
    except RuntimeError as exc:
        assert "after 2 attempts" in str(exc)
        assert "did not return a PDF" in str(exc)
    else:
        raise AssertionError("persistent non-PDF response must fail")
finally:
    jkbms.fetch = original_fetch

print("VALID jkbms-source-retry-bounded")
PY_RETRY

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
