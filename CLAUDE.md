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

## Safe Mode

Default: ON (read-only). Toggle in Settings tab or:
```
PUT /api/config/safe_mode  {"value": "0"}   # enable writes
```
When off, boost / device-on/off / heater commands are live.
