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
import json, os, pathlib, signal, sys

data_dir = pathlib.Path(os.environ.get("DATA_DIR", "/data"))
cache = data_dir / ".key_cache.json"

if cache.exists():
    sys.exit(0)

def _timeout(sig, frame):
    raise TimeoutError("AES key pre-extraction timed out")

signal.signal(signal.SIGALRM, _timeout)
signal.alarm(60)  # hard 60s cap so a hung network call never blocks startup

try:
    import requests
    from extract_key import candidates_from_js, context_candidates_from_js, discover_chunk_urls
    session = requests.Session()
    session.headers["User-Agent"] = "Mozilla/5.0"

    # Pass 1: look for a single unambiguous Base64.parse("...") match per chunk.
    all_ctx: list = []
    chunks: list = []
    for url in discover_chunk_urls("https://energipays.com", session):
        try:
            r = session.get(url, timeout=15)
            if not r.ok:
                continue
            chunks.append(r.text)
            ctx = context_candidates_from_js(r.text)
            if len(ctx) == 1:
                cache.write_text(json.dumps({"key": ctx[0]}))
                print(f"[entrypoint] AES key cached (single Base64.parse candidate) to {cache}")
                sys.exit(0)
            all_ctx.extend(c for c in ctx if c not in all_ctx)
        except Exception:
            continue

    # Pass 2: if exactly one unique Base64.parse match across all chunks, use it.
    if len(all_ctx) == 1:
        cache.write_text(json.dumps({"key": all_ctx[0]}))
        print(f"[entrypoint] AES key cached (unique cross-chunk context match) to {cache}")
        sys.exit(0)

    # NOTE: no broad-scan fallback. The main chunk contains dozens of coincidental
    # 32-byte base64 strings (inline PNGs, config), so caching an unvalidated
    # candidate poisons the cache and breaks login worse than having no key.
    print(f"[entrypoint] AES key pre-extraction: {len(all_ctx)} Base64.parse candidates "
          f"across {len(chunks)} chunks (need exactly 1) — will retry on first login",
          file=sys.stderr)
except Exception as e:
    print(f"[entrypoint] AES key pre-extraction failed: {e}", file=sys.stderr)
finally:
    signal.alarm(0)
PYEOF

exec "$@"
