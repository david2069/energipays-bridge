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

## Safe Mode

Default: ON (read-only). Toggle in Settings tab or:
```
PUT /api/config/safe_mode  {"value": "0"}   # enable writes
```
When off, boost / device-on/off / heater commands are live.
