# Energipays Bridge

An **unofficial** locally self-hosted integration for [Energipays](https://energipays.com) PowerDiverter - a Solar PV panel power diverter for hot water devices.

NOTE: This software package is **NOT affilated with Energipays and is provided "as is" with NO warranty, NO support nor is acknowledged or endorsed nor is it any way approved for use by the vendor**. Any use is at your own risk and without any support by the author(s) nor Engeripays.

This software was developed by reverse engineering the web portal and as such my cease to work at any time. This may be due to changes imposed by Energipays and/or should the Cloud service no longer be available in it's current form. 

Connects to the Energipays cloud API, stores metrics locally in SQLite, and serves a responsive web UI with live data, historical analytics, automation rule management, device controls, and push notifications via Home Assistant.

Runs as a **Home Assistant Add-on**, a **Docker container**, or a bare **Python venv**.

---

## Features

- **Dashboard** — live water temperatures, power flow, grid import/export, heating and boost status
- **Analytics** — historical charts for grid power, solar PV, water temperature, import/export
- **Rules** — view and manage automation schedules with a 24-hour timeline visualiser
- **Battery** — SoC, inverter state, grid mode, ambient and cabinet temperature (via FranklinWH Modbus integration)
- **External integrations** — pull battery/solar data from REST, Modbus TCP, HA WebSocket, or MQTT
- **MQTT Discovery** — auto-publish all sensors to Home Assistant as entities
- **Push notifications** — receive alerts (boost started, device offline, temperature threshold) via HA mobile app
- **Solar forecast** — chart solar production against actual generation
- **PWA** — installable on iOS Safari and Android Chrome, works like a native app
- **Setup wizard** — guided first-run setup (credentials → MQTT → integrations → notifications)

---

## Installation

### Option 1 — Home Assistant Add-on *(recommended)*

> Requires Home Assistant OS or Supervised with the Supervisor panel.

1. In Home Assistant go to **Settings → Add-ons → Add-on Store**
2. Click **⋮ (three dots) → Repositories**
3. Add the URL:
   ```
   https://github.com/david2069/energipays-bridge
   ```
4. Close the dialog — **Energipays Bridge** now appears in the store
5. Click **Install** (the supervisor builds the image; takes 2–3 minutes)
6. Go to the **Configuration** tab and fill in:

   | Option | Description |
   |--------|-------------|
   | `energipays_email` | Your Energipays account email |
   | `energipays_password` | Your Energipays account password |
   | `poll_interval` | Seconds between polls (default: `60`) |
   | `mqtt_enabled` | `true` to publish entities to HA via MQTT Discovery |
   | `mqtt_host` | MQTT broker host (default: `core-mosquitto` for the Mosquitto add-on) |
   | `mqtt_port` | MQTT broker port (default: `1883`) |
   | `mqtt_username` | MQTT username (leave blank if unauthenticated) |
   | `mqtt_password` | MQTT password |
   | `log_level` | `info` / `debug` / `warning` / `error` |

7. Click **Save** then **Start**
8. Click **Open Web UI** — the setup wizard opens automatically on first run

**Note:** the web UI is accessible at `http://<ha-ip>:8080`. Port 8080 must be reachable on your network.

---

### Option 2 — Docker (standalone)

#### Prerequisites

- Docker + Docker Compose
- Git

#### Steps

```bash
# Clone the bridge
git clone https://github.com/david2069/energipays-bridge.git
cd energipays-bridge
```

Create a `.env` file with your credentials:

```bash
cat > .env <<EOF
ENERGIPAYS_EMAIL=you@example.com
ENERGIPAYS_PASSWORD=your_password
ENERGIPAYS_POLL_INTERVAL=60
LOG_LEVEL=INFO

# Optional: MQTT Discovery (remove # to enable)
# MQTT_ENABLED=true
# MQTT_HOST=192.168.0.1
# MQTT_PORT=1883
# MQTT_USERNAME=
# MQTT_PASSWORD=
EOF
```

Build and start:

```bash
docker-compose build && docker-compose up -d
```

Open **http://localhost:8080** — the setup wizard opens automatically if no credentials are saved yet, otherwise the dashboard loads directly.

**Useful commands:**

```bash
docker-compose logs -f          # follow logs
docker-compose down             # stop
docker-compose build && docker-compose up -d   # rebuild after updates
```

Data (SQLite database, caches) is stored in `./data/` and survives container restarts.

#### Updating

```bash
git pull
docker-compose build && docker-compose up -d
```

---

### Option 3 — Python venv (development / bare metal)

#### Prerequisites

- Python 3.11+
- Git

#### Steps

```bash
# Clone both repos into the same parent directory
git clone https://github.com/david2069/energipays-bridge.git
git clone https://github.com/david2069/energipays-client.git

cd energipays-bridge
python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate

# Install the bridge and the client library
pip install -e .
pip install -e ../energipays-client
```

Create `.env`:

```env
ENERGIPAYS_EMAIL=you@example.com
ENERGIPAYS_PASSWORD=your_password
ENERGIPAYS_POLL_INTERVAL=60
DATA_DIR=./data
LOG_LEVEL=INFO
```

Run:

```bash
energipays-bridge run
```

Open **http://localhost:8080**.

To develop against local changes to `energipays-client`, edit files in `../energipays-client/` — the editable install picks up changes immediately without reinstalling.

---

## Configuration reference

All settings can be set via environment variables, a `.env` file (Docker/venv), or the **Configuration** tab in the HA add-on UI.

| Variable | Default | Description |
|----------|---------|-------------|
| `ENERGIPAYS_EMAIL` | *(required)* | Energipays account email |
| `ENERGIPAYS_PASSWORD` | *(required)* | Energipays account password |
| `ENERGIPAYS_POLL_INTERVAL` | `60` | Seconds between API polls |
| `ENERGIPAYS_DEVICE_ID` | *(auto)* | Device UUID — auto-discovered if blank |
| `ENERGIPAYS_KEY` | *(auto)* | AES key override (base64) — auto-extracted from JS bundle if blank |
| `ADMIN_PORT` | `8080` | Web UI and REST API port |
| `ADMIN_HOST` | `0.0.0.0` | Bind address |
| `DATA_DIR` | `./data` | Directory for SQLite database and caches |
| `RAW_AGE_DAYS` | `7` | Full-resolution metric retention (days) |
| `RETENTION_DAYS` | `30` | Downsampled archive retention (days) |
| `LOG_LEVEL` | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |
| `MQTT_ENABLED` | `false` | Enable MQTT Discovery |
| `MQTT_HOST` | `localhost` | MQTT broker host |
| `MQTT_PORT` | `1883` | MQTT broker port |
| `MQTT_USERNAME` | *(blank)* | MQTT username |
| `MQTT_PASSWORD` | *(blank)* | MQTT password |

---

## Setup wizard

The setup wizard runs automatically on first install (no credentials saved). It walks through:

1. **Account** — enter and verify Energipays credentials
2. **MQTT Discovery** — optional, configure broker details
3. **External integrations** — optional, connect a battery or solar inverter
4. **Push notifications** — optional, configure Home Assistant instances and devices
5. **Done** — summary and link to Settings for further configuration

To re-run the wizard at any time: **Settings → Re-run Setup Wizard**.

---

## MQTT Discovery

When `MQTT_ENABLED=true`, the bridge publishes all sensors as Home Assistant entities under the `homeassistant/` discovery prefix. Entities appear automatically in HA — no manual configuration needed.

Published entities include: water temperatures (T1/T2/T3/avg), grid import/export power, phase voltages, heating status, boost status, active rule, state of charge, and more.

**For the HA add-on:** set `mqtt_enabled: true` in the Configuration tab and point `mqtt_host` at your broker. If you use the [Mosquitto add-on](https://github.com/home-assistant/addons/tree/master/mosquitto), the default host `core-mosquitto` works without changes.

---

## External integrations

Connect a FranklinWH battery, solar inverter, or any other device via:

| Protocol | Use case |
|----------|----------|
| **REST** | Modbus bridge REST API, any JSON HTTP endpoint |
| **Modbus TCP** | Direct register read with address + scale mapping |
| **HA WebSocket** | Subscribe to any HA entity's state changes |
| **MQTT** | Subscribe to any MQTT topic with JSON dot-path extraction |

Configure in **Settings → External Integrations**. Mapped metrics (e.g. `ext.battery_soc`, `ext.solar_power_w`) appear in the Dashboard and Analytics tab automatically.

---

## Push notifications

Receive phone alerts when:

- Device comes online or goes offline
- Boost starts or ends
- Off-peak rule activates or deactivates
- Water temperature reaches a threshold
- Heating completes

Notifications are sent via the Home Assistant [Mobile Companion App](https://companion.home-assistant.io/) `notify` service. Configure HA instances and companion devices in **Settings → Push Notifications**.

---

## PWA (mobile install)

**iOS Safari:**
1. Open `http://<server-ip>:8080` in Safari
2. Tap **Share → Add to Home Screen**

**Android Chrome:**
1. Open `http://<server-ip>:8080` in Chrome
2. Tap **⋮ → Add to Home screen** (or accept the install prompt)

The app launches without browser chrome and works offline for cached assets.

---

## REST API

The web UI is built on a documented REST API you can call directly.

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/points/latest` | Latest polled data snapshot |
| `GET` | `/api/metrics/history?point=phasePower&range=24h` | Historical chart data |
| `GET` | `/api/rules` | List automation rules |
| `POST` | `/api/boost` | Trigger boost `{"period": 1}` (1h/2h/3h) |
| `POST` | `/api/device/set` | Toggle a device field `{"fields": {"status": 1}}` |
| `GET` | `/api/integrations` | List external integrations |
| `POST` | `/api/integrations/{id}/test` | Test integration, read live values |
| `GET` | `/api/setup/status` | Setup state + runtime environment |
| `GET` | `/api/health` | Server health and poller status |
| `GET` | `/api/logs` | Server log buffer (last 500 lines) |

---

## Troubleshooting

**Dashboard shows "Bridge disconnected"**
- Check the Logs tab for the error
- Verify credentials are correct via `GET /api/setup/status`
- Try re-running the setup wizard (Settings → Re-run Setup Wizard)

**No data after first login**
- Wait one poll cycle (default 60 s) for the first sample to arrive
- The Energipays JWT auto-refreshes every ~5 minutes; initial login must succeed

**MQTT entities not appearing in HA**
- Confirm `MQTT_ENABLED=true` and the broker host/port are correct
- Check that the Mosquitto add-on (or your broker) is running
- Use **Settings → MQTT → Republish** to re-send discovery payloads

**Port 8080 already in use**
- Change `ADMIN_PORT` in `.env` and update `docker-compose.yml` port mapping

**HA add-on fails to install / build error**
- Check the add-on log in the Supervisor panel for the full error
- Ensure your HA instance can reach `github.com` (needed to download the image)

---

## Project structure

```
energipays-bridge/
├── config.yaml                  # HA add-on manifest
├── build.yaml                   # Multi-arch base image config
├── repository.json              # HA custom repository descriptor
├── Dockerfile
├── docker-compose.yml
├── docker-entrypoint.sh
└── src/energipays_bridge/
    ├── main.py                  # FastAPI app + lifespan startup
    ├── poller.py                # Energipays API poll loop
    ├── sample.py                # SampleBus pub/sub
    ├── environment.py           # Runtime detection (ha_addon/docker/dev)
    ├── ha_options.py            # Converts HA options.json → .env at startup
    ├── api/                     # REST route handlers
    ├── config/                  # Pydantic settings (env vars + .env + ha_options.env)
    ├── integrations/            # External device pollers (REST/Modbus/HA WS/MQTT)
    ├── notifications/           # Push notification dispatcher + trigger
    ├── publish/                 # MQTT Discovery publisher
    ├── store/                   # SQLite init, migrations, metrics, credentials
    ├── templates/               # Jinja2 SPA shell + tab partials
    └── static/                  # Alpine.js, Chart.js, Tailwind CSS, PWA assets
```

---

## Related

- [energipays-client](https://github.com/david2069/energipays-client) — Python API client library used by this bridge
- [Energipays](https://energipays.com) — cloud platform for Energipays Powerdiverter - a solar PV power diverter for hot water devices
