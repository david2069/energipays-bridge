## 1.0.6 — AES key via HA add-on configuration field

### Added
- **AES Key field in HA add-on config** — paste the base64 key from `energipays key` CLI
  into Settings → Add-ons → Energipays Bridge → Configuration → AES Key. Flows through
  `ha_options.py` → `ENERGIPAYS_KEY` env var → used at startup before any login attempt.
  Blank field is ignored so it stays optional.

### How to get the key
On a Mac/Linux machine with energipays-client installed:
```
energipays -e your@email.com -p yourpassword key
```
Copy the 44-character base64 string and paste it into the HA config field.

---

## 1.0.5 — Pre-extract AES key at startup

### Fixed
- **AES key not set on login** — app now calls the same JS-bundle extraction
  that the client uses internally, during startup, before credentials are loaded.
  Failures appear in the add-on log instead of surfacing as a login error.

---

## 1.0.4 — Fix all static assets under Ingress + manual AES key fallback

### Fixed
- **All icons and images broken under HA Ingress** — every `/static/...` absolute path
  in templates and JS was resolved against HA's origin rather than the add-on container.
  Changed all static asset paths to relative (`static/...`) so `<base href>` routes them
  correctly through the Ingress proxy to the container.
- **AES key extraction** — entrypoint now tries three progressively broader passes:
  (1) single `Base64.parse()` match per chunk, (2) unique match across all chunks,
  (3) broader 32-byte base64 scan as last resort. Covers sites where the key appears
  in more than one chunk.
- **add-on description** updated to correctly describe the Powerdiverter / Energipays scope.

### Added
- **Version shown in setup wizard** header (Step N of 5 · v1.0.4).
- **Manual AES key entry** in setup wizard Step 1 — collapsible advanced section with
  instructions and a `POST /api/setup/set-key` endpoint. Escape hatch for when automatic
  extraction fails; user pastes the 44-char base64 key from browser DevTools.

---

## 1.0.3 — Fix AES key extraction on first HA add-on run

### Fixed
- **"AES key not set" on first login** — `DATA_DIR` was written to the
  pydantic-settings .env file but never exported to the OS environment.
  `energipays.py` therefore cached the extracted key to site-packages (wiped
  on every container rebuild) instead of the persistent `/data/` volume.
  `docker-entrypoint.sh` now exports `DATA_DIR` and runs a pre-extraction
  step that caches the key to `$DATA_DIR/.key_cache.json` before the app
  starts, avoiding the chicken-and-egg where the validated path needs a
  login token to validate the key.

---

## 1.0.2 — Fix Ingress static file 404s

### Fixed
- **Static files 404 via HA Ingress** — setting `scope["root_path"]` caused
  Starlette's `StaticFiles` to prepend the ingress prefix to the on-disk file
  lookup path. HA already strips the ingress prefix before forwarding to the
  container, so no path rewriting is needed. Middleware simplified to a
  pass-through; `X-Ingress-Path` header is now read directly in `ui.py` to
  inject the `<base href>` tag.

---

## 1.0.1 — HA Ingress + startup fix

### Added
- **HA Ingress support** — add-on UI now opens inside the HA sidebar via the Ingress
  button; no need to open port 8080 directly. `_HAIngressMiddleware` strips the
  `X-Ingress-Path` prefix; `<base href>` tag injected in the SPA shell; fetch()
  interceptor in app.js rewrites `/api/...` calls to the ingress-prefixed path

### Fixed
- **Startup crash without credentials** — `device_id` and `data_server` are now
  initialised before the credentials block so the app starts cleanly on first run
  (e.g. HA add-on install before entering credentials)

---

## 1.0.0 — External integrations, setup wizard, MQTT toggle, dashboard UX

### Added
- **External integrations framework** — REST, Modbus TCP, HA WebSocket, MQTT pollers;
  `IntegrationRegistry` manages active pollers; `ext.*` metrics merged into
  `/api/points/latest`; CRUD + test + probe API
- **FranklinWH battery register mappings** — home_load_w (15506), operating_mode
  (15507), self_reserve_soc (15508), tou_reserve_soc (15509)
- **Push notification framework** — `NotificationTrigger` SampleBus subscriber;
  HA instances + companion devices + settings API; Settings cards for all three
- **Weather / NEM price API** — location-aware current weather + spot price strip
  in topbar and dashboard
- **5-step setup wizard** — credentials → MQTT Discovery → integrations → push
  notifications → done; pre-fills from existing config; auto-launches on first run
- **MQTT runtime pause toggle** — ON/OFF button in Settings with amber confirmation
  modal; persists across restarts; MQTT sidebar tab hidden when disabled/paused
- **House SVG "Home Loads" label** — house icon in power flow now shows label +
  `ext.home_load_w` kW value below it
- **Active rule 24h timeline** — when a rule is running, the dashboard shows the
  full colored slot bar (blue=Disable, red=Boost, green Now marker) with time axis
  labels and legend instead of the simple progress bar

### Fixed
- **Battery tab light mode** — replaced `bg-slate-800/50` card boxes with compact
  divider-row layout (`divide-y`) that renders correctly in both light and dark mode;
  font sizes reduced to match other sub-tabs
- **Rules tab** — everyday-identical detection; full 24h timeline bar on rule list

### Changed
- Removed Safe Mode button from Settings (replaced by MQTT toggle)
- Battery tab restructured as compact rows: SoC, Power+state, Home Load, Operating
  Mode, Self Reserve SOC, TOU Reserve SOC, SoH, Capacity, Grid Status, Solar PV
- DB migrations v3 (integrations), v4 (HA instances, notification tables), v5 (weather)

---

## [2026-06-26] — MQTT command fixes + switchers + light/dark mode + rules UX

### Fixed
- **MQTT Boost Power select** (`boost_power_sel`) now calls `set_boost_power()` API;
  previously it echoed state to HA but never sent the command to the device
- **MQTT Boost State select** (`boost_active`) command handler added: "On" starts
  a boost using the current Boost Duration selection; "Off" cancels via `cancel_boost()`
- **Power % buttons** on Dashboard now route to `set_boost_power()` via a dedicated
  branch in `POST /api/device/set`; previously the field was silently ignored by
  `set_device_status()`
- **Active Rule showing "unknown"** — Energipays API returns literal string `"unknown"`
  when no rule is active; poller now normalises this to `""` so the UI shows `—`
- **MQTT Select "Unknown"** — `format_value()` now returns `None` (shows `—`) instead
  of the raw string when a select entity value cannot be parsed as int

### Added
- **Four new MQTT writable switches**: Off-Peak Switcher, Weather Boost Switcher,
  Solar Restriction, Priority Mode — converted from read-only `binary_sensor` to
  `switch` entities with command handlers calling `set_device_status()`
- **Light/dark mode toggle** — moon/sun button in topbar; `app.css` override block
  maps all dark slate colors to light equivalents when `.dark` is absent on `<html>`;
  theme persists to `localStorage` with no FOUC (inline script before Alpine)
- **Rules timeline improvements** — taller bar (20px), color legend
  (blue=Disable, red=Boost, green line=Now), current-time green vertical line
- **`isEverydayIdentical` semantic comparison** — rules with identical schedules
  across all 7 days now correctly show "Every day" instead of individual day labels

---

## [2026-06-25] — Dashboard UX + Safe Mode removal + topbar settings

### Added
- Configurable topbar stats strip (Avg Temp, Grid kW, Solar kW, Heater kW, WiFi, LoRa)
  with per-item toggle in gear dropdown; state persisted to `localStorage`
- `navCfg` + `setNavCfg()` lifted to `Alpine.store('app')` so topbar and gear share state

### Removed
- **Safe Mode** — entirely removed from UI, store, settings card, and all button guards
- Dark/light mode toggle (re-added 2026-06-26 with working light theme)

### Fixed
- `power-diverter.svg` invisible on dark background: `filter:invert(1) opacity(0.7)`
- PD device thumbnail moved from Power Flow card to Hot Water card (left of temp ring)
  with hover tooltip showing phase/SN/firmware/PCB/wireless sensor details
- Boost % SVG arc slider replaced with reliable segmented buttons (25/50/75/100%)
- Settings gear icon fixed to far-right of topbar
- Orphaned `</div>` in `dashboard.html` causing all tabs to render outside `x-show`
  scope (div balance restored: 98 opens = 98 closes)

---

## [2026-06-24] — Initial release

### Added
- FastAPI application with lifespan: DB init, poller start, MQTT publisher, metrics archival
- `EnergipaysPoller` — 60s asyncio polling loop; emits `Sample` to `SampleBus`
- `MetricsRecorder` — SQLite subscriber; raw 7-day retention → 5-min archive 30 days
- `MqttPublisher` — queue-based HA MQTT Discovery with full command handling:
  - Sensors: water temps (T1/T2/T3/avg), grid power, import/export kWh, heater power,
    diverted power, solar, voltages, phase power, wifi signal, firmware, serial number
  - Switches: Power Diverter, Immersion Heater
  - Selects: Boost State, Boost Power, Boost Duration, Active Rule
  - Button: Boost Start
  - Diagnostics: LoRa, Off-Grid, Last Command Result/Time
- REST API: `/api/points/latest`, `/api/metrics/history`, `/api/rules` (CRUD),
  `/api/boost`, `/api/boost/cancel`, `/api/device/set`, `/api/device/switch`,
  `/api/mqtt/*`, `/api/logs`, `/api/config`, `/api/health`
- Web UI (Alpine.js 3 + Tailwind 3 + Chart.js 4, no build step):
  - **Dashboard**: Hot Water card (temp ring, boost controls), Power Flow SVG with
    animated flow lines, Grid/Solar/Heater/Diverter sub-tabs
  - **Analytics**: Chart.js line charts with time range selector (1h→30d)
  - **Rules**: rule list with timeline, inline edit modal, enable/disable
  - **Raw Metrics**: collapsible JSON tree of latest API payload
  - **MQTT**: entity browser with current values, state/command topics, republish/unpublish
  - **Settings**: credential management, poll interval, MQTT config, log level, retention
  - **Logs**: ring-buffer log viewer (last 500 lines)
- Mobile-responsive layout: bottom tab nav on mobile, sidebar on desktop
- PWA: `manifest.json` + service worker (cache-first static, network-first API)
- Docker deployment: `docker-compose.yml` with `data/` volume for SQLite
- `energipays-bridge run` CLI entry point
