"""Detect which runtime environment the bridge is running in."""
from __future__ import annotations

import pathlib


def _detect() -> str:
    if pathlib.Path("/data/options.json").exists():
        return "ha_addon"
    if pathlib.Path("/.dockerenv").exists():
        return "docker"
    return "dev"


RUNTIME: str = _detect()
IS_HA_ADDON: bool = RUNTIME == "ha_addon"
IS_DOCKER: bool   = RUNTIME in ("ha_addon", "docker")
IS_DEV: bool      = RUNTIME == "dev"
