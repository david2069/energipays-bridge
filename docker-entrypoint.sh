#!/bin/sh
# Copy all .py files from the mounted energipays-client directory into site-packages.
# The client is a plain script dir (not a proper package).
CLIENT_DIR=/energipays-client
SITE=/usr/local/lib/python3.12/site-packages

if [ -d "$CLIENT_DIR" ]; then
    for f in "$CLIENT_DIR"/*.py; do
        [ -f "$f" ] && cp "$f" "$SITE/"
    done
fi

exec "$@"
