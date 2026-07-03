#!/bin/sh
set -e

# Dev mode: copy energipays-client source files into site-packages
if [ -d /energipays-client ]; then
    SITE=$(python3 -c "import site; print(site.getsitepackages()[0])")
    for f in /energipays-client/*.py; do
        [ -f "$f" ] && cp "$f" "$SITE/"
    done
fi

# HA Add-on mode: convert /data/options.json → /data/ha_options.env
# pydantic-settings will pick this file up automatically.
if [ -f /data/options.json ]; then
    python3 /app/src/energipays_bridge/ha_options.py /data/options.json /data/ha_options.env
fi

# Ensure DATA_DIR is exported so energipays.py caches the AES key to the
# persistent data volume (not site-packages, which is wiped on each rebuild).
export DATA_DIR="${DATA_DIR:-/data}"

# Pre-extract the Energipays AES key on first run so the login flow has a key
# available without needing a chicken-and-egg authenticated request.
# Writes to $DATA_DIR/.key_cache.json; skips if already cached.
python3 - <<'PYEOF'
import json, os, pathlib, sys

data_dir = pathlib.Path(os.environ.get("DATA_DIR", "/data"))
cache = data_dir / ".key_cache.json"

if cache.exists():
    sys.exit(0)

try:
    import requests
    from extract_key import discover_chunk_urls, context_candidates_from_js
    session = requests.Session()
    session.headers["User-Agent"] = "Mozilla/5.0"
    for url in discover_chunk_urls("https://energipays.com", session):
        try:
            r = session.get(url, timeout=30)
            if not r.ok:
                continue
            ctx = context_candidates_from_js(r.text)
            if len(ctx) == 1:
                cache.write_text(json.dumps({"key": ctx[0]}))
                print(f"[entrypoint] AES key cached to {cache}")
                sys.exit(0)
        except Exception:
            continue
    print("[entrypoint] AES key pre-extraction: no single candidate found — will retry on first login", file=sys.stderr)
except Exception as e:
    print(f"[entrypoint] AES key pre-extraction failed: {e}", file=sys.stderr)
PYEOF

exec "$@"
