"""Pick the device and its data server from the account's device list (#8)."""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)

DEFAULT_DATA_SERVER = "https://data-au-1.energipays.com"


def resolve_device(devices_resp: object, device_id: str | None,
                   default_server: str = DEFAULT_DATA_SERVER) -> tuple[str, str]:
    """Return ``(device_id, data_server)``.

    With no configured id, the first device is used. A configured id still takes its
    data server from the list; before, it stayed on data-au-1 whatever the device used.
    """
    devs = devices_resp if isinstance(devices_resp, list) else (
        devices_resp.get("data", []) if isinstance(devices_resp, dict) else [])
    devs = [d for d in devs if isinstance(d, dict)] if isinstance(devs, list) else []
    if not device_id and devs:
        device_id = devs[0].get("id", "")
    match = next((d for d in devs if d.get("id") == device_id), None)
    if match:
        return device_id, match.get("server") or default_server
    if device_id:
        log.warning("device %s is not in the account's device list; using %s",
                    device_id, default_server)
    return device_id or "", default_server
