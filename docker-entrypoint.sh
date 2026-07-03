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

exec "$@"
