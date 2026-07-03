"""
Convert Home Assistant add-on /data/options.json to a pydantic-settings .env file.

Called by docker-entrypoint.sh when running as an HA add-on.
Usage: python3 ha_options.py [options.json] [output.env]
"""
from __future__ import annotations

import json
import sys

OPTIONS_PATH = sys.argv[1] if len(sys.argv) > 1 else "/data/options.json"
OUTPUT_PATH  = sys.argv[2] if len(sys.argv) > 2 else "/data/ha_options.env"

KEY_MAP = {
    "energipays_email":    "ENERGIPAYS_EMAIL",
    "energipays_password": "ENERGIPAYS_PASSWORD",
    "poll_interval":       "ENERGIPAYS_POLL_INTERVAL",
    "mqtt_enabled":        "MQTT_ENABLED",
    "mqtt_host":           "MQTT_HOST",
    "mqtt_port":           "MQTT_PORT",
    "mqtt_username":       "MQTT_USERNAME",
    "mqtt_password":       "MQTT_PASSWORD",
    "log_level":           "LOG_LEVEL",
}

try:
    with open(OPTIONS_PATH) as fh:
        opts = json.load(fh)
except FileNotFoundError:
    sys.exit(0)

lines: list[str] = []
for opt_key, env_key in KEY_MAP.items():
    val = opts.get(opt_key)
    if val is None:
        continue
    if isinstance(val, bool):
        val = str(val).lower()
    elif opt_key == "log_level":
        val = str(val).upper()
    else:
        val = str(val)
    escaped = val.replace("\\", "\\\\").replace('"', '\\"')
    lines.append(f'{env_key}="{escaped}"')

# HA add-on data lives at /data
lines.append('DATA_DIR="/data"')

with open(OUTPUT_PATH, "w") as fh:
    fh.write("\n".join(lines) + "\n")
