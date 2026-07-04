"""Auto-detect and register this add-on's own Home Assistant instance for
push notifications, via the Supervisor's Core API proxy.

Today, sending a push notification to any HA instance requires a manually
entered URL + a manually generated long-lived access token. That's needless
work in the single most common case: the add-on is usually running as a
Supervisor add-on *inside* the exact HA instance it wants to notify through.
HA add-ons with `homeassistant_api: true` in config.yaml can reach that same
instance's REST API at http://supervisor/core/api/... authenticated with the
add-on's own SUPERVISOR_TOKEN (auto-injected) — no user input needed.
"""
from __future__ import annotations

import logging
import os

import httpx

from .environment import IS_HA_ADDON
from .store.db import get_ha_instance, get_ha_instances, upsert_ha_instance

log = logging.getLogger(__name__)

SUPERVISOR_INSTANCE_ID = "supervisor-local"
SUPERVISOR_CORE_URL = "http://supervisor/core"


async def sync_supervisor_ha_instance(app) -> None:
    """(Re)register the Supervisor-proxied HA instance if running as an HA add-on.

    Safe to call more than once (e.g. every boot) — SUPERVISOR_TOKEN can
    rotate across add-on restarts, so this re-verifies and re-persists each
    time. No-op on docker/dev, where there is no Supervisor to proxy through.
    """
    if not IS_HA_ADDON:
        return
    token = os.environ.get("SUPERVISOR_TOKEN", "")
    if not token:
        log.info("ha_supervisor: SUPERVISOR_TOKEN not set — is homeassistant_api enabled for this add-on?")
        return

    db = app.state.db
    existing = await get_ha_instance(db, SUPERVISOR_INSTANCE_ID)

    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.get(
                SUPERVISOR_CORE_URL + "/api/",
                headers={"Authorization": f"Bearer {token}"},
            )
        r.raise_for_status()
        reachable = True
    except Exception as exc:
        reachable = False
        log.warning("ha_supervisor: could not reach this Home Assistant via Supervisor proxy — %s", exc)

    inst = {
        "id": SUPERVISOR_INSTANCE_ID,
        "alias": "This Home Assistant",
        "host": SUPERVISOR_CORE_URL,
        "token": token,
        "enabled": bool(existing["enabled"]) if existing else True,
        "is_default": bool(existing["is_default"]) if existing else False,
        "source": "supervisor",
    }
    await upsert_ha_instance(db, inst)

    # First time this instance is ever seen: if the user hasn't already
    # picked a different default, make this the default so the common
    # single-HA case needs zero configuration to start sending notifications.
    if existing is None:
        others = await get_ha_instances(db)
        if not any(i["is_default"] for i in others if i["id"] != SUPERVISOR_INSTANCE_ID):
            await upsert_ha_instance(db, {**inst, "is_default": True})

    if reachable:
        log.info("ha_supervisor: 'This Home Assistant' instance synced and reachable")
    else:
        log.warning("ha_supervisor: instance registered but not reachable yet (will retry next boot)")
