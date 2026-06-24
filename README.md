# Energipays Bridge

Self-hosted web dashboard for [Energipays](https://energipays.com) hot water and grid telemetry. Polls the Energipays cloud API on a configurable interval, stores metrics locally in SQLite, and serves a responsive web UI with live data, historical charts, and device controls.

Installable as a **PWA** on iOS and Android. Runs as a Docker container or bare Python.

---

## Features

- **Dashboard** — live water temperatures (T1/T2/T3), grid import/export, heating status, boost controls
- **Analytics** — Chart.js historical charts: grid power, import/export, diverted energy
- **Rules** — view automation rules (write support coming once endpoints confirmed)
- **Raw metrics** — tree view and terminal view of the full live API payload
- **Settings** — safe mode toggle, poll interval, data retention
- **Logs** — live server log viewer
- **PWA** — installable on iOS Safari / Android Chrome, works offline for cached assets
- **Safe Mode** — all device commands blocked by default; must be explicitly enabled in Settings

---

## Requirements

- Python 3.11+ **or** Docker
- An [Energipays](https://energipays.com) account with at least one device

---

## Quick start (Python)

### 1. Clone and set up

```bash
git clone https://github.com/david2069/energipays-bridge.git
cd energipays-bridge

python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
```

### 2. Install dependencies

This project uses [energipays-client](https://github.com/david2069/energipays-client) as its API library. Clone it alongside this repo, then install both:

```bash
# From the parent directory:
git clone https://github.com/david2069/energipays-client.git

# Back in energipays-bridge:
pip install -r ../energipays-client/requirements.txt
pip install -e .
```

### 3. Configure credentials

Copy the example env file and fill in your details:

```bash
cp .env.example .env
```

Edit `.env`:

```env
ENERGIPAYS_EMAIL=you@example.com
ENERGIPAYS_PASSWORD=your_password
ENERGIPAYS_POLL_INTERVAL=60        # seconds between API polls (default: 60)
ADMIN_PORT=8080
LOG_LEVEL=INFO
```

> **Security:** `.env` is gitignored. Credentials are never stored in the database.

### 4. Run

```bash
PYTHONPATH=../energipays-client energipays-bridge run
```

Open **http://localhost:8080** in your browser.

The server will:
1. Connect and authenticate to Energipays
2. Auto-discover your first device
3. Start polling every 60 seconds (configurable)
4. Serve the dashboard at `http://localhost:8080`

---

## Quick start (Docker)

### 1. Clone both repos

```bash
git clone https://github.com/david2069/energipays-bridge.git
git clone https://github.com/david2069/energipays-client.git  # must be sibling directory
cd energipays-bridge
```

### 2. Configure credentials

```bash
cp .env.example .env
# Edit .env with your credentials
```

### 3. Build and start

```bash
docker-compose up --build
```

Open **http://localhost:8080**

### Stop / restart

```bash
docker-compose down        # stop
docker-compose up -d       # start in background
docker-compose logs -f     # follow logs
```

Data (SQLite database) is stored in `./data/` and survives container restarts.

---

## Configuration reference

All settings are read from environment variables or a `.env` file in the project root.

| Variable | Default | Description |
|----------|---------|-------------|
| `ENERGIPAYS_EMAIL` | *(required)* | Energipays account email |
| `ENERGIPAYS_PASSWORD` | *(required)* | Energipays account password |
| `ENERGIPAYS_POLL_INTERVAL` | `60` | Seconds between API polls |
| `ENERGIPAYS_DEVICE_ID` | *(auto)* | Device UUID — auto-discovered from account if blank |
| `ENERGIPAYS_KEY` | *(auto)* | AES encryption key override (base64) — auto-extracted from JS bundle if blank |
| `ADMIN_PORT` | `8080` | Web UI port |
| `ADMIN_HOST` | `0.0.0.0` | Bind address |
| `DATA_DIR` | `./data` | Directory for SQLite database and caches |
| `RAW_AGE_DAYS` | `7` | Full-resolution metric retention (days) |
| `RETENTION_DAYS` | `30` | Downsampled archive retention (days) |
| `LOG_LEVEL` | `INFO` | Logging level: `DEBUG`, `INFO`, `WARNING`, `ERROR` |

---

## Safe Mode

Safe Mode is **enabled by default**. In Safe Mode, all device write commands (boost, device on/off, heater on/off) are blocked — the dashboard is read-only.

To enable commands, go to **Settings → Safe Mode** and toggle it off, or via the API:

```bash
curl -X PUT http://localhost:8080/api/config/safe_mode \
     -H 'Content-Type: application/json' \
     -d '{"value": "0"}'
```

Safe Mode state persists across restarts (stored in SQLite).

---

## Install as PWA (mobile)

### iOS Safari
1. Open `http://<your-server-ip>:8080` in Safari
2. Tap the Share button → **Add to Home Screen**
3. The app opens without browser chrome, like a native app

### Android Chrome
1. Open `http://<your-server-ip>:8080` in Chrome
2. Tap the menu → **Add to Home screen** (or the install prompt)

---

## API reference

The REST API is used internally by the UI and can be called directly.

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/points/latest` | Latest polled data snapshot |
| `GET` | `/api/metrics/history?point=phasePower&range=24h&bucket=5m` | Historical chart data |
| `GET` | `/api/devices` | List Energipays devices |
| `GET` | `/api/device/status` | Live device status (proxied from Energipays) |
| `GET` | `/api/rules` | List automation rules |
| `POST` | `/api/boost` | Trigger a timed boost `{"period": 1}` (1=1h, 2=2h, 3=3h) |
| `POST` | `/api/device/set` | Toggle a device switch `{"fields": {"status": 1}}` |
| `GET` | `/api/health` | Server health + poller status |
| `GET` | `/api/logs` | Server log buffer (last 500 entries) |
| `GET` | `/api/config/{key}` | Read a config value |
| `PUT` | `/api/config/{key}` | Update a config value |

**Write endpoints require Safe Mode to be disabled.** They return `403` otherwise.

### `point` values for `/api/metrics/history`

| Point | Description |
|-------|-------------|
| `phasePower` | Grid import/export power (kW, negative = export) |
| `waterTemperature1` | Water temp T1 — bottom (hottest) |
| `waterTemperature2` | Water temp T2 — middle |
| `waterTemperature3` | Water temp T3 — top |
| `waterTemperatureAvg` | Average of T1/T2/T3 |
| `heaterStatus` | Immersion heater on/off (1/0) |
| `boostStatus` | Boost active (1/0) |
| `today.IEct` | Grid import today (kWh) |
| `today.EEct` | Grid export today (kWh) |
| `today.DE_h` | Diverted heating energy today (kWh) |

---

## Data retention

Metrics are stored at full resolution for 7 days, then downsampled to 5-minute averages and retained for 30 days. Both thresholds are configurable in Settings or via `RAW_AGE_DAYS` / `RETENTION_DAYS` env vars.

---

## Troubleshooting

**Dashboard shows no data / "Offline"**
- Check logs tab or `GET /api/health` for the last error
- Verify credentials are correct: `python3 ../energipays-client/cli.py me`
- The Energipays JWT expires every 5 minutes — the client auto-refreshes, but initial login must succeed

**"Safe Mode" banner on boost/controls**
- Go to Settings → toggle Safe Mode off

**Port already in use**
- Change `ADMIN_PORT` in `.env` or pass `--port 9090` to `energipays-bridge run`

**Stale AES key (encrypted API calls fail)**
- Delete `.key_cache.json` in the energipays-client directory — it will be re-extracted automatically

---

## Project structure

```
energipays-bridge/
├── src/energipays_bridge/
│   ├── main.py              # FastAPI app + startup lifespan
│   ├── poller.py            # Background Energipays API poll loop
│   ├── sample.py            # SampleBus pub/sub (poller → storage/MQTT)
│   ├── cli.py               # energipays-bridge run
│   ├── api/                 # REST route handlers
│   ├── config/              # Pydantic settings
│   ├── store/               # SQLite init, migrations, metrics
│   ├── publish/             # MQTT publisher (Phase 2)
│   ├── templates/           # Jinja2 SPA shell + tab partials
│   └── static/              # Alpine.js, Chart.js, Tailwind, CSS, PWA
├── data/                    # SQLite database (gitignored)
├── .env                     # Credentials (gitignored)
├── docker-compose.yml
└── Dockerfile
```

---

## Roadmap

- **Phase 2 — MQTT + HA Discovery:** publish Thermino sensors, grid power, boost status, and heating controls as Home Assistant entities via MQTT Discovery
- **Phase 3 — HA Add-on:** run as a Home Assistant supervisor add-on
- **Rule editor:** create and edit automation rules (write endpoint shape pending HAR capture)
