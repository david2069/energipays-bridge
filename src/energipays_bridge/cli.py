"""energipays-bridge CLI entry point."""
from __future__ import annotations

import argparse
import json
import logging
import sys


# ── helpers ───────────────────────────────────────────────────────────────────

async def _load_creds_from_db(settings) -> tuple[str, str]:
    """Load credentials from the bridge DB (same path as the web app)."""
    import aiosqlite
    import pathlib
    from .store.credentials import load_credentials

    db_path = settings.db_path
    # If relative path doesn't exist from cwd, try resolving relative to this file
    # (handles running the CLI from a different working directory)
    if not db_path.is_absolute() and not db_path.exists():
        repo_root = pathlib.Path(__file__).parent.parent.parent
        db_path = repo_root / db_path
    if not db_path.exists():
        return "", ""
    data_path = db_path.parent
    async with aiosqlite.connect(db_path) as db:
        return await load_credentials(db, data_path)


def _make_client(settings, verbose: bool = False):
    """Login and return (client, device_id, data_server)."""
    import asyncio
    import os

    if verbose:
        logging.basicConfig(level=logging.DEBUG,
                            format="%(levelname)s %(name)s: %(message)s")
    else:
        logging.basicConfig(level=logging.WARNING,
                            format="%(levelname)s: %(message)s")

    # Load from DB into env vars before importing energipays (it reads them at import time)
    if not (os.environ.get("ENERGIPAYS_EMAIL") and os.environ.get("ENERGIPAYS_PASSWORD")):
        email, password = asyncio.run(_load_creds_from_db(settings))
        if not (email and password):
            _die("No credentials found — configure via the web UI or set ENERGIPAYS_EMAIL/ENERGIPAYS_PASSWORD")
        os.environ["ENERGIPAYS_EMAIL"] = email
        os.environ["ENERGIPAYS_PASSWORD"] = password

    from energipays import EnergipaysClient

    email = os.environ["ENERGIPAYS_EMAIL"]
    print(f"Logging in as {email} …")
    client = EnergipaysClient(auto_login=False)
    client.login()

    device_id = settings.energipays_device_id
    devices_raw = client.devices()
    dev_list = devices_raw.get("data") or devices_raw
    if isinstance(dev_list, list) and not isinstance(dev_list, dict):
        pass  # already a list
    elif isinstance(dev_list, dict):
        dev_list = [dev_list]
    else:
        dev_list = []

    if not device_id:
        if dev_list:
            device_id = dev_list[0].get("id", "")
        if not device_id:
            _die("Could not auto-discover device ID — set ENERGIPAYS_DEVICE_ID")
        print(f"Auto-discovered device: {device_id}")

    data_server = ""
    for d in dev_list:
        if d.get("id") == device_id:
            data_server = d.get("server", "")
            break
    if not data_server:
        _die("Could not resolve data_server for device — check device list")

    return client, device_id, data_server


def _die(msg: str) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


# ── power map ─────────────────────────────────────────────────────────────────

BOOST_POWER_MAP = {25: 1, 50: 2, 75: 3, 100: 4}   # % → API index
BOOST_POWER_LABEL = {1: "25%", 2: "50%", 3: "75%", 4: "100%"}
BOOST_PERIOD_LABEL = {1: "30 min", 2: "1 hour", 3: "2 hours"}


# ── subcommands ───────────────────────────────────────────────────────────────

def cmd_status(args, settings) -> int:
    client, device_id, _ = _make_client(settings, args.verbose)
    print("Fetching device status …")
    result = client.device_status([device_id])

    data = result.get("data") or result
    dev = data[0] if isinstance(data, list) and data else (data if isinstance(data, dict) else {})
    td_raw = dev.get("status") or dev.get("telemetryData") or {}
    td: dict = td_raw if isinstance(td_raw, dict) else {}

    online = dev.get("is_online") or td.get("is_online")
    t1 = td.get("waterTemperature1", "—")
    t2 = td.get("waterTemperature2", "—")
    t3 = td.get("waterTemperature3", "—")
    boost = td.get("boostStatus", 0)
    heater = td.get("heaterStatus", 0)
    power_w = td.get("divertedPowerHeater", "—")
    boost_pwr_idx = td.get("boost_power")
    boost_pwr_pct = BOOST_POWER_LABEL.get(int(boost_pwr_idx), f"idx={boost_pwr_idx}") if boost_pwr_idx else "—"

    print(f"\nDevice:       {device_id}")
    print(f"Online:       {'yes' if online else 'no'}")
    print(f"Temperatures: T1={t1}°C  T2={t2}°C  T3={t3}°C")
    print(f"Heater:       {'ON' if heater else 'OFF'}")
    print(f"Boost active: {'YES' if boost else 'no'}")
    print(f"Boost power:  {boost_pwr_pct}")
    print(f"Heater power: {power_w} W")

    if args.verbose:
        print("\n--- raw device_status response ---")
        print(json.dumps(result, indent=2, default=str))

    return 0


def _get_boost_status(client, device_id) -> tuple[bool, dict]:
    """Return (boost_active, telemetry_dict)."""
    result = client.device_status([device_id])
    data = result.get("data") or result
    dev = data[0] if isinstance(data, list) and data else (data if isinstance(data, dict) else {})
    td_raw = dev.get("status") or dev.get("telemetryData") or {}
    td: dict = td_raw if isinstance(td_raw, dict) else {}
    return bool(td.get("boostStatus")), td


def cmd_boost(args, settings) -> int:
    client, device_id, data_server = _make_client(settings, args.verbose)

    # Fail fast if boost already active
    boost_active, td = _get_boost_status(client, device_id)
    if boost_active:
        t1 = td.get("waterTemperature1", "—")
        t2 = td.get("waterTemperature2", "—")
        t3 = td.get("waterTemperature3", "—")
        power_w = td.get("divertedPowerHeater", "—")
        boost_pwr_idx = td.get("boost_power")
        boost_pwr_pct = BOOST_POWER_LABEL.get(int(boost_pwr_idx), f"idx={boost_pwr_idx}") if boost_pwr_idx else "—"
        print("ERROR: boost is already active — use 'boost stop' first", file=sys.stderr)
        print(f"\n  Temperatures: T1={t1}°C  T2={t2}°C  T3={t3}°C")
        print(f"  Boost power:  {boost_pwr_pct}")
        print(f"  Heater power: {power_w} W")
        if args.verbose:
            print("\n--- raw telemetry ---")
            print(json.dumps(td, indent=2, default=str))
        return 1

    # Optionally set power first
    if args.power is not None:
        power_idx = BOOST_POWER_MAP[args.power]
        print(f"Setting boost power to {args.power}% (index={power_idx}) …")
        r = client.set_boost_power(device_id, power_idx)
        if args.verbose:
            print("set_boost_power response:", json.dumps(r, indent=2, default=str))
        if isinstance(r, dict) and "error" in r:
            _die(f"Failed to set boost power: {r.get('body') or r['error']}")
        print(f"  boost power set to {args.power}%")

    duration_label = BOOST_PERIOD_LABEL[args.period]
    print(f"Sending boost: {duration_label} …")
    result = client.boost_device(device_id, data_server, period=args.period)

    if args.verbose:
        print("boost_device response:", json.dumps(result, indent=2, default=str))

    if isinstance(result, dict) and "error" in result:
        _die(f"Boost failed: {result.get('body') or result['error']}")

    pct_label = f" @ {args.power}%" if args.power else ""
    print(f"Boost started: {duration_label}{pct_label}")
    print("  Run 'energipays-bridge status' in ~30s to confirm boostStatus=1")
    return 0


def cmd_boost_cancel(args, settings) -> int:
    client, device_id, data_server = _make_client(settings, args.verbose)
    print("Cancelling boost …")
    result = client.cancel_boost(device_id, data_server)

    if args.verbose:
        print("cancel_boost response:", json.dumps(result, indent=2, default=str))

    if isinstance(result, dict) and "error" in result:
        if result.get("status") == 404:
            _die("No active boost to cancel (device returned 404)")
        _die(f"Cancel boost failed: {result.get('body') or result['error']}")

    print("Boost cancelled")
    return 0


def cmd_set_boost_power(args, settings) -> int:
    client, device_id, _ = _make_client(settings, args.verbose)
    power_idx = BOOST_POWER_MAP[args.power]
    print(f"Setting boost power to {args.power}% (index={power_idx}) …")
    result = client.set_boost_power(device_id, power_idx)

    if args.verbose:
        print("set_device_status response:", json.dumps(result, indent=2, default=str))

    if isinstance(result, dict) and "error" in result:
        _die(f"Failed: {result.get('body') or result['error']}")

    print(f"Boost power set to {args.power}%")
    return 0


# ── main ──────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="energipays-bridge")
    sub = p.add_subparsers(dest="command", required=True)

    # run
    run_p = sub.add_parser("run", help="start the web server")
    run_p.add_argument("--host", default=None)
    run_p.add_argument("--port", type=int, default=None)
    run_p.add_argument("--reload", action="store_true", help="enable auto-reload (dev)")

    # status
    st_p = sub.add_parser("status", help="show live device status (temperatures, boost, power)")
    st_p.add_argument("--verbose", "-v", action="store_true",
                      help="show raw API response and enable debug logging")

    # boost
    b_p = sub.add_parser("boost", help="boost start|stop|status|set-power")
    b_sub = b_p.add_subparsers(dest="boost_cmd", required=True)

    # boost start
    bs_p = b_sub.add_parser("start", help="trigger a manual boost")
    bs_p.add_argument("--period", "-p", type=int, choices=[1, 2, 3], default=2,
                      metavar="{1,2,3}",
                      help="duration: 1=30min, 2=1h (default), 3=2h")
    bs_p.add_argument("--power", "-w", type=int, choices=[25, 50, 75, 100],
                      default=None, metavar="{25,50,75,100}",
                      help="power %% — sets device setting before boosting; "
                           "omit to keep current setting")
    bs_p.add_argument("--verbose", "-v", action="store_true")

    # boost stop
    bst_p = b_sub.add_parser("stop", help="cancel an active boost")
    bst_p.add_argument("--verbose", "-v", action="store_true")

    # boost status
    bss_p = b_sub.add_parser("status", help="show boost and temperature state")
    bss_p.add_argument("--verbose", "-v", action="store_true")

    # boost set-power
    bsp_p = b_sub.add_parser("set-power",
                              help="change boost power %% without triggering a boost")
    bsp_p.add_argument("power", type=int, choices=[25, 50, 75, 100],
                       metavar="{25,50,75,100}")
    bsp_p.add_argument("--verbose", "-v", action="store_true")

    args = p.parse_args(argv)

    if args.command == "run":
        import uvicorn
        from .config.settings import BridgeSettings
        settings = BridgeSettings()
        uvicorn.run(
            "energipays_bridge.main:app",
            host=args.host or settings.admin_host,
            port=args.port or settings.admin_port,
            reload=args.reload,
            log_level=settings.log_level.lower(),
        )
        return 0

    from .config.settings import BridgeSettings
    settings = BridgeSettings()

    if args.command == "status":
        return cmd_status(args, settings)

    if args.command == "boost":
        boost_dispatch = {
            "start":     cmd_boost,
            "stop":      cmd_boost_cancel,
            "status":    cmd_status,
            "set-power": cmd_set_boost_power,
        }
        return boost_dispatch[args.boost_cmd](args, settings)


if __name__ == "__main__":
    raise SystemExit(main())
