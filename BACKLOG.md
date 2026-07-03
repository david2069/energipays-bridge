# Backlog — energipays-bridge

Release-sized work packages, in priority order. One package = one version bump
(never two bumps in the same session — HA update caching breaks).
Dev-test everything on the dev instance first
(`docker compose -f docker-compose.dev.yml up -d --build` → port 8081;
`down -v` resets to a fresh first-install), then promote to prod.

Items are tagged `[defect]` (broken behaviour) or `[enh]` (new capability).
Defects and enhancements share one queue on purpose — priority is the only
decision that matters, and most items here are both. New, not-yet-scheduled
problems go under **Defects (untriaged)** below until they're pulled into a
package. (If external users ever start reporting bugs, intake moves to GitHub
Issues; this file stays the release-packaging layer.)

---

## Package 1 — v1.1.2: remove Safe Mode, add READ_ONLY flag, fix unconfigured 500s

**Status: approved, queued** (task chip exists; this file is the durable spec)

### 1a. [defect] Delete legacy Safe Mode (approved 2026-07-04)
It default-blocks ALL writes on fresh installs with no UI to disable (prod only
works because an old DB row has `safe_mode=0`), and MQTT bypasses it anyway.
- `main.py:63-65` — remove the safe_mode DB load from the lifespan
- `api/devices.py:251-254` — remove `_require_writes()` + its 8 call sites
  (`/api/device/set`, `/api/device/switch`, `/api/boost`, `/api/boost/cancel`,
  rule create/update/rename/delete)
- `api/admin.py` — drop `safe_mode` from allowed config keys (~249), the
  immediate-apply branch (~256), and the GET /api/config payload (~87)
- `api/points.py:26` — drop `safe_mode` from `/api/points/latest`
- `static/js/app.js:71` dead `safeMode: false` + stale comment ~225;
  `templates/tabs/raw.html:77` stale Safe Mode help text
- Remove readers + writer in the SAME commit (admin.py/points.py read
  `app.state.safe_mode` directly → AttributeError if only main.py changes)

### 1b. [enh] Add READ_ONLY env flag (approved design)
- `settings.py`: `read_only: bool = False` — env-driven only, NEVER DB-persisted
- Enforce at the single choke point where all device writes converge, covering
  REST + `mqtt_publisher._handle_command` + future scheduler
- Blocked writes log the exact would-be request:
  `DRY RUN: would POST /boost {period: 2, boost_power: 3}` (debugging aid)
- UI: topbar "read-only" badge; write controls disabled with tooltip
- `docker-compose.dev.yml`: `READ_ONLY=true` by default (dev observes, never
  actuates the real hot-water system); prod/HA default false. Optional HA
  config.yaml field via ha_options.py.

### 1c. [defect] Graceful "not connected yet" instead of 500s
Routes using `app.state.client` crash with `AttributeError('NoneType')` before
poller init (fresh install pre-credentials, or the window between wizard
"Save & connect" and `_start_poller` completing). Observed live on v1.1.1.
- Shared guard → 503 `{"error": "bridge not connected yet"}` on all cloud
  routes (`devices.py:79` list_rules etc., `cloud_stats.py`)
- `rules_tab.js refresh()` (~425): `if (!r.ok) return` silently swallows errors
  leaving "No rules found" forever → visible connecting/error state + retry
  (or refetch on tab activation)

---

## Package 3 — HA-native MQTT setup: Supervisor auto-discovery + working wizard step

**Status: approved — PRIORITY: HIGH** (user, 2026-07-04; from HA add-on install feedback)

### 3a. [defect] Wizard MQTT step is decorative and its guidance is wrong for HA
- The step 2 fields (MQTT_HOST/MQTT_PORT) are never persisted — `advance()`
  in `setup_modal.html` just steps through; `wantMqtt/mqttHost/mqttPort` go
  nowhere. The "Setup complete" screen then claims MQTT is configured.
- The copy says "set MQTT_ENABLED=true in your environment" — HA add-on users
  have no env vars. (MQTT is actually configurable today via the add-on
  Configuration page: config.yaml options → ha_options.py → env. The wizard
  should say/do that, or better, do 3b.)

### 3b. [enh] Auto-discover the HA Mosquitto broker via the Supervisor services API
HA provides broker discovery incl. credentials — no user input needed when
the Mosquitto add-on is installed:
- `config.yaml`: add `hassio_api: true` and `services: ["mqtt:want"]`
- Backend `GET /api/mqtt/discover`: in `ha_addon` runtime, call
  `GET http://supervisor/services/mqtt` with header
  `Authorization: Bearer $SUPERVISOR_TOKEN` → `{host, port, username,
  password, ssl}`; in docker/dev runtime, probe candidates
  (`core-mosquitto`, `host.docker.internal:1883/1886`, `localhost:1883`)
- Backend `POST /api/mqtt/test`: real broker connect attempt (paho) with the
  candidate settings → `{ok, error}`
- Wizard step 2: on load, call discover → pre-fill + "Found HA broker
  (credentials supplied by Supervisor)" banner; **Test** button; on save,
  PERSIST the settings and apply live (restart publisher)
- Persistence design decision: MQTT settings currently come only from env at
  boot (`MqttSettings`). Store wizard-entered values in DB `app_config` with
  resolution order DB-override > env > default — `/data/ha_options.env` is
  regenerated from options.json every boot, so it is NOT a writable target.
- Wizard copy: branch on `runtime` from `/api/setup/status` (ha_addon vs
  docker) — never mention env vars in the HA path.

### 3d. [enh] "Re-run setup wizard" affordance
The wizard opens ONLY on first run — `app.js:238` sets `setupModal = true`
solely when `needsSetup` (no credentials). There is no way to reopen it, so
anything skipped during setup (MQTT, integrations, notifications) is
unreachable-by-wizard forever.
- Add a "Run setup wizard" button in Settings (and/or the user menu) that
  sets `$store.app.setupModal = true` — the wizard's `init()` already
  pre-fills from existing config, so re-entry is safe
- With 3a/3b, the re-run wizard's MQTT step becomes a working
  discover → pre-fill → Test → Save path, giving users a second chance at
  everything they skipped

### 3c. [defect] Settings → MQTT Discovery card has no way to enable/configure
Reported 2026-07-04 (HA add-on): card shows "Disabled" + "Set
MQTT_ENABLED=true in env to activate" (`templates/tabs/settings.html` ~626)
with read-only broker fields and no enable control. Enable is env-gated;
the existing Settings ON/OFF toggle is only a runtime pause once enabled.
- With 3b's DB-override persistence, make the card fully self-service:
  enable toggle + editable host/port/credentials + Test + Save, live-applied
- Runtime-aware copy: in ha_addon mode never mention env vars (until 3b
  ships, at least point to Settings → Add-ons → Configuration → mqtt_enabled)
- (Hardcoded "Same broker as the FranklinWH Modbus Bridge." sentence already
  removed on main, 2026-07-04)

---

## Package 2 — Solar forecast scaling + cloud-default charts + throttling leftovers

**Status: approved, queued — priority: normal, after Package 3** (task chip exists)

### 2a. [defect] Solar PV forecast is unscaled irradiance (confirmed too low)
`api/solar.py` returns raw horizontal `shortwave_radiation` W/m²;
`dashboard_tab.js` `_solarSummarize` (~167-176) shows W/m²÷1000 as "kW" — i.e.
a 1 kWp horizontal array. No system size or orientation stored anywhere.
- New config: `solar_kwp`, `solar_tilt_deg` (~22 default), `solar_azimuth_deg`
  — app_config storage like weather_lat/lon; UI fields near weather settings.
  Open-meteo azimuth convention: 0=south, −90=east, 90=west, ±180=north
  (Sydney roofs typically north → 180)
- `solar.py`: switch to `global_tilted_irradiance` with `&tilt=&azimuth=`
  (consider api.open-meteo.com forecast API for today+7d);
  estimated kW = GTI/1000 × kWp × ~0.85 performance ratio; keep temp_c for
  future temperature derating
- UI: display scaled kW/kWh; keep "Actual" overlay; if kWp unset show a
  "Set system size in Settings" hint instead of silently-wrong numbers

### 2b. [enh] History charts: default to cloud, toggle for local
`dashboard_tab.js` ~296-301: Hot Water 24h modal reads ONLY local SQLite
(`/api/metrics/history`) — empty on fresh installs, permanently empty on HA
add-ons (metrics recording is opt-in/off there).
- Cloud/Local toggle, default Cloud, via existing `/api/cloud/stats` proxy
  (confirm which data_type/series carry water temps + heater power —
  Analytics.csv + HAR files in repo root have reference payloads)
- Apply in BOTH locations: Hot Water modal (main card top-right chart icon)
  and the Heater sub-tab chart in the Power Flow card
- Persist choice to localStorage; auto-fallback with a small note when the
  chosen source errors or is empty

### 2c. [enh] Throttling leftovers
`weather_nem.py` is DONE (commit `1831f9f`: single-flight, negative cache,
stale serving, shared client, 12 KB ELEC_NEM_SUMMARY endpoint) — do not redo;
reuse its `_cached_fetch` pattern.
- `api/solar.py`: adopt the same helper (consider extracting to a shared util)
- `dashboard_tab.js init()` registers `setInterval(_fetchWeatherNem, 300000)`
  (~504) and `setInterval(fetchSolar, 3600000)` (~506) PER dashboard mount →
  move to the global Alpine app store, guarded single registration
- `api/cloud_stats.py`: ~60s TTL cache keyed by (date_from, date_to,
  data_type, phase)
- `poller.py`: exponential backoff + cap + jitter on consecutive failures
  (currently fixed 60s retry forever)

---

## Defects (untriaged)

### [defect] Rule edit silently not written — no failure surfaced (P1)
Reported 2026-07-04. Repro: edit the RUNNING rule "Daily boost & disable",
add a third slot (Disable 14:33–23:59), Save (twice, past the gap warning) →
rule card still shows the original 2 slots. No error toast — the save path
(`rules_tab.js saveRule()` ~743) trusts `r.ok`, and the PUT returns 200 even
when the server discards the change.
Hypotheses, in likelihood order:
1. **Server silently ignores edits to the ACTIVE rule.** energipays.com's own
   UI blocks editing/deleting the active rule; our bridge deliberately allows
   it (see memory/README notes) — the vendor API may accept the PUT and drop
   the data. Test: same edit against an INACTIVE rule; if that persists,
   this is confirmed.
2. **update_rule body quirk** — server ignores `data` unless the body is
   exactly `{"data": {dN: [...]}}`. Fixed in energipays-client v0.2.0
   (`27dcc23`, dev container has it via the Dockerfile pin) — but PROD (8080)
   gets the library from the `../energipays-client` mount + entrypoint copy,
   so it depends on when prod was last rebuilt. Check which instance the
   repro used.
3. **Everyday-rule day-key mismatch**: the card shows "Every day" but the
   editor opened with only Friday active (see screenshot) — refresh()
   expands d1–d7 when `active_day == 'd1'`, and saveRule() collapses back
   via `_serverKey` only when `isEverydayActive()`; a single-day editor view
   of an everyday rule may PUT the wrong day key (server stores this rule as
   `d5` with `active_day: 'd1'`).
**Fix shape:** after PUT, re-GET the rule and diff the day-keys actually
persisted — surface a clear error toast when the server ignored the write
(this also catches every future silent-discard case). Then address the root
cause per hypothesis: likely block/warn on editing the running rule (align
with vendor behaviour, or deactivate → edit → reactivate), and fix the
everyday/single-day editor state.

### [defect] Push Notifications: local integration control conflated with cloud API master
Reported 2026-07-04. The Settings card's "Notifications OFF — Master switch —
controls ALL push notifications" implies it governs everything, but it is
purely LOCAL: `setMaster()` (`settings_tab.js:917`) → PUT
`/api/notification-settings` → DB, checked only by
`notifications/dispatcher.py:75` before the bridge→HA companion-app send.
It has no relationship to the Energipays CLOUD notification parameter (the
vendor side deciding whether any notifications are emitted/sent to the
Powerdiverter app at all). User requirement: these must be two distinct,
clearly-labelled controls — the local one is essential precisely because it
is independent of the cloud master.
**Fix shape:**
- Relabel/restructure the card: "Bridge → Home Assistant notifications
  (local)" section = existing master + per-event trigger rows + devices
- Add a separate "Energipays cloud notifications" control showing/setting
  the vendor API parameter — discovery needed: find the cloud notification
  settings endpoint (check HAR captures in repo root, `client.messages()`,
  and device-status switcher fields) and whether it is writable via API
- If the cloud param turns out read-only, still DISPLAY it so users
  understand why nothing arrives despite local being ON (and vice versa)

### [defect] Rule-timeline charts: inconsistent colours/legends across the three renderings
Reported 2026-07-04 (screenshot: dashboard active-rule bar vs Solar Forecast
"Rule schedule (today)" strip). Each timeline has its own hand-rolled palette:
- **Dashboard active-rule timeline** (`templates/tabs/dashboard.html` ~270-310):
  inline hex — Disable `#3b82f6` (blue-500), Boost `#ef4444`, Solar-PV-off
  `#fbbf24` overlay, Not-set `bg-slate-600/40`; legend includes "Not set",
  "Solar PV off", "Now"
- **Solar Forecast rule strip** (`dashboard.html` ~1442-1470): Tailwind classes —
  Disable `bg-sky-400` (different blue!), Boost `bg-red-500`, not-set
  `bg-slate-200`/`bg-slate-700/60`; its legend row also mixes chart-series
  colours (Today amber / Tomorrow sky) with slot colours, so sky-blue means
  BOTH "Tomorrow" and "Disable" in the same card
- **Rules tab cards/editor** (`static/js/rules_tab.js:3` CMD_COLOR +
  `templates/tabs/rules.html` ~331-336): muted darks — `bg-blue-900/40`,
  `bg-red-900/40`
Also visible in the screenshot: the active-rule bar rendered UNFILLED (all
"not set") for the running "Daily boost & disable" rule while the solar strip
below coloured the same rule correctly — investigate the slot-fill lookup
(day-key/active_day expansion?) as part of the fix.
**Fix shape:** one shared colour token set (single JS const or CSS variables)
consumed by all three renderings + a shared legend partial; separate the
chart-series legend (Today/Tomorrow) from the slot legend in the solar card.

## Notes / smaller items (unscheduled)

- **aemo.com.au in-container TLS** — root cause was payload size + handshake
  concurrency, fixed in `1831f9f`; if timeouts ever reappear, the negative
  cache now contains the blast radius
- **Log-counting gotcha**: every httpx log line is echoed 2 extra times by the
  DB log handler — count only `INFO httpx:` lines when measuring call rates
- **Dev workflow**: dev container has NO `../energipays-client` mount — it
  installs the library from GitHub at the Dockerfile's pinned SHA, exactly
  what HA gets. When the library changes: push energipays-client first, then
  bump the SHA pin.

## Shipped

- **v1.1.1 (2026-07-03)** — AES key root cause (unpushed energipays-client
  repo; Dockerfile now pins commit SHA), in-wizard key diagnostics that
  self-repair, browser-console extraction fallback, entrypoint cache-poisoning
  removed, docker-compose.dev.yml added. Verified: fresh-install login works.
- **Unreleased on main (`1831f9f`)** — NEM/weather fetch hardening (see 2c note)
