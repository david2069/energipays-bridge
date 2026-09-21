# CLAUDE.md — energipays-bridge

## Project

Self-hosted web dashboard for Energipays hot water & grid telemetry.
Wraps `energipays-client` with a FastAPI backend, Alpine.js + Chart.js frontend,
SQLite metrics storage, PWA support, and (Phase 2) MQTT HA Discovery.

Architecture mirrors `franklinwh-modbus-bridge` exactly.

## Setup

```bash
cd ~/dev/Claude/Projects/energipays-bridge
python3 -m venv venv && source venv/bin/activate
pip install -e ../energipays-client   # local editable
pip install -e ".[dev]"
cp .env.example .env                  # add credentials
energipays-bridge run
```

Open http://localhost:8080

## Environment

```
ENERGIPAYS_EMAIL=you@example.com
ENERGIPAYS_PASSWORD=secret
ENERGIPAYS_POLL_INTERVAL=60        # seconds (default 60)
ENERGIPAYS_DEVICE_ID=              # auto-discovered if blank
ADMIN_PORT=8080
LOG_LEVEL=INFO
DATA_DIR=./data
READ_ONLY=false                    # true = refuse every device-modifying command
```

## Commands

```bash
energipays-bridge run              # start server (default port 8080)
energipays-bridge run --reload     # dev mode with auto-reload
energipays-bridge run --port 9090  # custom port
```

## Docker

```bash
docker-compose up --build
```

## Architecture

```
EnergipaysPoller (60s) → SampleBus → MetricsRecorder → SQLite
FastAPI → Jinja2 SPA → Alpine.js + Chart.js (no build step)
```

## Working rules

- **Never change UI layout, behaviour, or any existing feature without explicit user approval.**
- If a change is needed beyond the stated task, describe it and wait for a yes before touching anything.
- One increment at a time: propose → get approval → implement → deploy.
- **After every JS/HTML change: check browser console errors before reporting done.** Use grep on templates and JS files for common Alpine pitfalls (`x-if` inside SVG, undefined store keys, missing methods) if a browser is not available.
- **Never reverse-engineer a FranklinWH Modbus register. Look it up first** — see
  FranklinWH Modbus reference below.

## FranklinWH Modbus reference (read before touching any register)

Anything under `integrations/sunspec_scanner.py`, `modbus_poller.py`, or a
`ext.*` point in the Battery tab is FranklinWH SunSpec data. It is already
documented in three places outside this repo. Check them **in this order**
before writing code or guessing an enum:

| Source | Path | What it settles |
|--------|------|-----------------|
| Official SunSpec model JSON | `~/dev/modbus/venv/lib/python3.*/site-packages/sunspec2/models/json/model_7*.json` | Ground truth for point types, scale factors, and **every enum/bitfield symbol** |
| `franklinwh-modbus` library | `~/dev/modbus/SUNSPEC_MODEL_REFERENCE.md`, `docs/FRANKLINWH_MODBUS_EXTENSIONS.md`, `src/franklinwh_modbus/types.py:201` (`PICS_STATUS`) | Sign conventions, per-point compliance, the 15500+ proprietary zone, and which points are functional / cosmetic / blocked |
| `franklinwh-modbus-bridge` | `~/dev/Claude/Projects/franklinwh-modbus-bridge/docs/vendor-issues.md` + `docs/reference/` | 12 catalogued vendor defects, the signed PICS certificate SM-000028, and 30-day field observations |

Why this rule exists: the Battery tab shipped a "Grid Mode" row decoded from
`701.DERMode`, a register FranklinWH's own signed certificate lists as
**unimplemented** — the answer was one directory away the whole time. A single
cross-reference pass in 2026-09 found four defects, three of them the same
shape: one enum decoded in several places with mappings that disagree.

Two conventions worth memorising, both already gotten wrong once:
- **`714.DCW`: positive = DISCHARGE, negative = CHARGE.**
- **Decode each enum exactly once, at the poller.** The UI renders the decoded
  string — it must never re-map raw ints, and never compare a decoded string
  numerically (`"Connected" > 0` is `NaN > 0`, i.e. always false).

Record every cross-reference result in `BACKLOG.md` — clean ones too, so the
next session does not repeat the sweep.

## Conventions

- Python ≥ 3.11, type hints on public functions
- `asyncio` throughout; blocking I/O via `asyncio.to_thread`
- Pydantic v2 for API schemas
- SQLite via `aiosqlite`, WAL mode, forward-only migrations in `store/db.py`
- Never store credentials in SQLite — env vars / `.env` only
- Chart.js stored in closure variable, NOT Alpine reactive (avoids Proxy stack overflow)

## Key Files

| File | Purpose |
|------|---------|
| `src/energipays_bridge/main.py` | FastAPI app + lifespan |
| `src/energipays_bridge/poller.py` | Background poll loop |
| `src/energipays_bridge/sample.py` | SampleBus pub/sub |
| `src/energipays_bridge/store/db.py` | SQLite init + migrations |
| `src/energipays_bridge/store/metrics.py` | Time-series storage |
| `src/energipays_bridge/api/` | REST routes |
| `src/energipays_bridge/templates/` | Jinja2 SPA shell + tabs |
| `src/energipays_bridge/static/js/app.js` | Alpine store + 10s poll |

## Known Behaviour

### Poll lag (by design)
The bridge polls Energipays every **60 seconds** (configurable, but do not go below 60s).
This means state changes visible in the energipays.com app may take up to 60s to appear here.
This is intentional — we must not hammer the Energipays API.

The UI polls `/api/points/latest` every **10 seconds** for display refresh, but that data is
only as fresh as the last 60s device poll.

Commands (UI or MQTT) are sent immediately and acknowledged in real time (log + toast +
Last Command Result sensor in HA). The 60s lag only applies to passive state polling.

### Command audit trail
Every write command is logged with source prefix:
- `cmd (UI): <action> → <result>` — sent from the web dashboard
- `cmd (MQTT): <action> → <result>` — sent from Home Assistant

Failures are logged as `WARNING`. Both UI and MQTT commands publish to the
`Last Command Result` and `Last Command Time` HA sensors for track & trace.

## READ_ONLY

Default: **off** — boost / device-on-off / heater commands are live out of the box.
Set `READ_ONLY=true` to refuse every device-modifying command.

This replaced the old Safe Mode toggle in v1.1.2. There is no
`PUT /api/config/safe_mode` endpoint and no Settings-tab switch any more:
`read_only` is **env-only, never DB-persisted** (`config/settings.py`), so
changing it means changing the environment and restarting. HA add-on users set
the `read_only` option in `config.yaml`, which `ha_options.py` maps to the same
env var.

Enforcement, when it is on:
- **REST** — `_check_writable()` in `api/devices.py` logs
  `READ_ONLY: blocked <description>` and raises `403`.
- **MQTT** — `mqtt_publisher.py` logs the same prefix, skips the write, and
  publishes `<cmd> → BLOCKED (bridge is read-only)` to Last Command Result.
- **Startup** — `main.py` logs a `WARNING` that the mode is active.

Both paths log the exact write they would have sent, which is the intended
dry-run aid for this reverse-engineered API. Current state is exposed as
`read_only` on `/api/health` and `/api/points/latest`.
