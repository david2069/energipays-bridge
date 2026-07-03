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

## Package 3 — HA-native MQTT setup: Supervisor auto-discovery + working wizard step

**Status: approved — HIGH priority, ships next.** Package 1 (Safe Mode
removal, READ_ONLY flag, rules-race 500 fix) shipped as v1.1.2 on 2026-07-04
— see Shipped below. This is now the next package to build. Bundled as ONE
release covering wizard + Settings + re-run affordance — not split further.
Phases are dependency-ordered, not independently shippable except Phase 4.

**Root cause underlying 3a/3c looking "decorative":** MQTT config today is
boot-time-only and env-only — `main.py:177-192` builds `MqttSettings()` from
env vars ONCE during the FastAPI lifespan and only starts `MqttPublisher` if
already enabled at that instant. There is currently NO code path that can
enable/reconfigure MQTT after the app has started, from anywhere. Phase 0
below is what makes 3a/3b/3c real instead of cosmetic.

**Dependency graph:**
```
Phase 0 (runtime-reconfigurable backend) ──┬──> Phase 1 (discovery/test API) ──┬──> Phase 2 (wizard step)
                                            │                                    └──> Phase 3 (settings card)
                                            └──> Phase 3 also needs Phase 0 directly

Phase 4 (re-run wizard button) — independent, no dependency on the others
```

### Phase 0. [enh] Runtime-reconfigurable MQTT backend (foundation — build first)
- Config resolution: DB `app_config` override → env → default, for
  `mqtt_host/port/username/password/tls/enabled`. New `get_mqtt_settings(db)`
  helper in `store/db.py` overlaying `MqttSettings()` (env baseline) with any
  `mqtt_`-prefixed `app_config` rows.
- Extract `main.py:177-192` into an async `reconfigure_mqtt(app)`: stops the
  existing publisher if running, builds a fresh `MqttPublisher` from resolved
  settings, starts it, re-subscribes to the bus, restores `mqtt_paused`.
  Lifespan startup just calls this once; it becomes callable at any time.
- New `PUT /api/mqtt/config` — writes DB overrides via `set_config`, then
  calls `reconfigure_mqtt(app)`. Single choke point for every UI surface.

### Phase 1. [enh] Supervisor auto-discovery + test endpoint
HA exposes broker discovery incl. credentials via the Supervisor — no user
input needed when the Mosquitto add-on is installed:
- `config.yaml`: add `hassio_api: true` and `services: ["mqtt:want"]`
- `GET /api/mqtt/discover`: in `ha_addon` runtime
  (`environment.py` `IS_HA_ADDON`), call `GET http://supervisor/services/mqtt`
  with `Authorization: Bearer $SUPERVISOR_TOKEN` → `{host, port, username,
  password, ssl}`; in docker/dev runtime, probe candidates (`core-mosquitto`,
  `host.docker.internal:1883/1886`, `localhost:1883`) via short TCP connect,
  no credential guessing. Response: `{"found", "host", "port", "username",
  "password", "source": "supervisor"|"probe"}`.
- `POST /api/mqtt/test`: real broker connect attempt (paho, short timeout)
  with caller-supplied candidate settings → `{ok, error}`. Test-only, does
  not persist.
- **Known test gap**: the Supervisor discovery path cannot be exercised in
  the dev container (no fake Supervisor to simulate) — real verification of
  this path only happens on an actual HA install. Flag this explicitly when
  reporting dev-container test results; don't claim it's covered.

### Phase 2. [defect] Wizard MQTT step is decorative and its guidance is wrong for HA
- Step 2 fields (MQTT_HOST/MQTT_PORT) are never persisted today — `advance()`
  in `setup_modal.html` just steps through; `wantMqtt/mqttHost/mqttPort` go
  nowhere, yet "Setup complete" claims MQTT is configured.
- On step entry: call discover (Phase 1) → pre-fill + "Found HA broker (via
  Supervisor)" / "Found broker at host:port" banner, or blank fields if not
  found (no more fake `core-mosquitto` default).
- Add **Test** button (mirrors the AES-key-tools pattern already shipped in
  v1.1.1's wizard) using Phase 1's test endpoint.
- `advance()` calls `PUT /api/mqtt/config` (Phase 0) before moving to step 3.
- Copy branches on `runtime` from `/api/setup/status` — HA path never says
  "set MQTT_ENABLED=true in your environment".

### Phase 3. [defect] Settings → MQTT Discovery card has no way to enable/configure
Reported 2026-07-04 (HA add-on): card shows "Disabled" + "Set
MQTT_ENABLED=true in env to activate" (`templates/tabs/settings.html` ~626)
with read-only broker fields and no enable control; the existing ON/OFF
toggle is only a runtime pause once already enabled.
- Replace with the same controls as the wizard step: enable toggle, editable
  host/port/user/pass, Test, Save → `PUT /api/mqtt/config` (Phase 0). Mostly
  UI reuse of Phase 0/1 endpoints, minimal new backend work.
- Same runtime-aware copy rule as Phase 2.
- (Hardcoded "Same broker as the FranklinWH Modbus Bridge." sentence already
  removed on main, 2026-07-04.)

### Phase 4. [defect] No way to re-run the setup wizard — skipped steps unreachable forever
Independent of Phases 0-3; trivial standalone fix if ever needed ahead of the
rest, but bundled into this release per 2026-07-04 decision.
The wizard opens ONLY on first run — `app.js:238` sets `setupModal = true`
solely when `needsSetup` (no credentials). No way to reopen it, so anything
skipped during setup (MQTT, integrations, notifications) is unreachable by
wizard forever.
- Add a "Run setup wizard" button in Settings (and/or user menu) that sets
  `$store.app.setupModal = true` — wizard `init()` already pre-fills from
  existing config, so re-entry is safe.
- With Phases 1-2 done, the re-run wizard's MQTT step becomes a working
  discover → pre-fill → Test → Save path — a second chance at everything
  skipped the first time.

---

## Package 2 — Solar forecast scaling + cloud-default charts + throttling leftovers

**Status: approved, queued — priority: normal, after Package 3**

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
- **v1.1.2 (2026-07-04) — Package 1 complete**: Safe Mode removed entirely
  (1a); `READ_ONLY` env flag added, enforced at both REST (`_check_writable`
  in `api/devices.py`) and MQTT (`MqttPublisher._handle_command`), with
  dry-run logging and a topbar badge — `docker-compose.dev.yml` defaults it
  to `true` (1b); rules-tab 500s fixed via a shared `_get_client()` 503 guard
  + visible error/retry state in the Rules tab instead of a silent permanent
  empty list (1c). NEM/weather fetch hardening (`1831f9f`, previously
  unreleased on main) folded into this release too. Verified in the dev
  container: fresh-install `/api/rules` returns 503 not 500; `READ_ONLY`
  blocks both a simulated REST write and a simulated MQTT command without
  ever calling the underlying client; `safe_mode` config key rejected as
  unknown; zero remaining `safe_mode`/`safeMode` references anywhere in the
  codebase (`grep` swept clean).
