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

## Package 2 — Solar forecast scaling + cloud-default charts + throttling leftovers

**Status: approved, queued — priority: normal.** Package 3 (MQTT setup)
shipped as v1.1.3 on 2026-07-04 — see Shipped below. This is now the next
package to build.

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

## Deferred (not scheduled — needs a trigger to activate)

### [enh] Apple Web Push (installed-PWA) as an HA-independent notification channel
Proposed 2026-07-04. **Status: DEFERRED — do not schedule until the trigger
condition below is met.**

**Use case this solves:** an Apple-device user who does NOT run Home
Assistant (or doesn't want to install its Companion App) currently has no
way to receive push notifications from the bridge at all — today's only
delivery path (`notifications/dispatcher.py` → an `ha_instances` row →
`notify.mobile_app_*`) requires HA end-to-end. This would add a second,
parallel delivery channel that needs neither HA nor the Companion App: the
bridge's own installed PWA receiving native push directly.

**Why deferred, explicitly:** every current user of this bridge already runs
Home Assistant (it's the reason the bridge exists — MQTT Discovery, the
dashboard, the whole point). The HA Instance → Companion App path already
covers 100% of today's userbase, including Apple users, as long as they have
the HA app installed (which is virtually certain if they're using Home
Assistant seriously enough to have added a community add-on). Building this
now would be effort spent on a hypothetical user who doesn't yet exist in
the reported userbase. **Trigger condition to un-defer:** a real user
reports "I use this bridge without Home Assistant" or "I don't want to
install the HA Companion App on iOS" and asks for notifications. Until that
happens, this stays parked here as a ready-to-execute plan rather than
consuming a release slot.

**How it works (for whoever picks this up):**
- Since iOS/Safari 16.4, standard Web Push (VAPID + `PushManager`) works,
  but ONLY from an installed PWA (Share → Add to Home Screen) — it does
  NOT work from a normal Safari tab. Apple's browser proxies the standard
  Web Push protocol through APNs internally; no APNs certificates or Apple
  Developer account needed server-side, same as Chrome/Firefox Web Push.
  Notification permission can only be requested from inside the installed,
  standalone PWA context.
- Prerequisites already in place: `static/manifest.json` and `static/sw.js`
  exist (from the v1.0.0 PWA work) but `sw.js` (30 lines) has no push
  handling yet.

**Implementation shape (unchanged scope if/when un-deferred):**
1. Generate a VAPID key pair (one-time, server-side; store private key in
   `app_config` or `.secret_key`-style file, never in the repo)
2. Extend `sw.js`: handle `push` (render notification from payload) and
   `notificationclick` (focus/open the app) — roughly +15-20 lines
3. Client: after permission grant (only reachable from the installed PWA),
   `PushManager.subscribe()` with the VAPID public key; POST the resulting
   subscription (endpoint + p256dh/auth keys) to a new backend endpoint
4. New table `push_subscriptions` (endpoint, p256dh_key, auth_key,
   created_at) — one row per installed device/browser
5. Add a third delivery path in `notifications/dispatcher.py` (parallel to
   the existing HA-instance path) that POSTs an encrypted payload to each
   stored subscription via `pywebpush`, signed with the VAPID private key
6. Settings UI: its own enable/manage section, independent of the existing
   HA Instances card
7. **Known UX cost, unavoidable**: no way to detect "opened in a normal
   Safari tab, not installed as a PWA" and show a helpful prompt beforehand
   — iOS just silently fails the permission request outside the installed
   context. This needs to be documented for users, not solved in code.

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

**Update 2026-07-04 — corroborating evidence for hypothesis 1, plus a
related (now-fixed) bug found along the way.** Confirmed live and shipped in
v1.1.5: Energipays now also refuses to BOOST while a rule is active (cloud
returns a misleading generic error rather than naming the conflict) — the
same pattern as hypothesis 1's "vendor blocks operations on the active rule
that our bridge naively allowed through." This makes hypothesis 1 more
likely to be the actual cause here too, not just a possibility.
Also found and fixed in the same pass: `rules_tab.js`'s Enable/Disable
buttons could BOTH show for a non-active rule with a slot scheduled today,
and clicking Disable in that state cleared a DIFFERENT (the actually-active)
rule, not the one clicked — genuinely dangerous, not just a display bug.
Fixed by restricting Disable to `isActive(rule)` only. This is now DONE and
should not be re-investigated — what's still open is exactly what's
described above: write-back verification after PUT, and the
everyday/single-day editor day-key state.

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

### [defect] Boost Duration: three different mappings across app/MQTT/backend — TRUE mapping still unconfirmed
Reported 2026-07-04 (screenshot: MQTT entity shows 30 min / 1 hour /
2 hours; app shows 1 h / 2 h / 3 h). **Correction 2026-07-04**: my first
pass here concluded MQTT was ground truth and the app was wrong. That was
wrong — the user then showed a screenshot of the actual energipays.com
website's own "Main load" boost control, which shows exactly **1 h / 2 h /
3 h, no 30-minute option at all**. That directly contradicts the
`1=30min, 2=1h, 3=2h` mapping asserted in `energipays-client/cli.py:13,577`
and mirrored in `mqtt_publisher.py`'s `period_map` — those comments were
apparently never HAR-verified and may be stale (matching this session's
broader pattern of Energipays changing platform behavior).
**What the HAR captures actually show** (`Boost 1hour 100 and reduce power
then Stop.har`): two real `POST .../boost` calls in one recorded session —
`period=2` at 11:58:47, then several `boostPower` (encrypted, unreadable)
adjustments, then `period=1` at 12:00:04. This does NOT cleanly resolve
which period value means which duration — the filename's "1hour" could
refer to either call, and there's no captured evidence at all for a
"3 hour" click. **Do not trust either the CLI comment or the app's current
button values as ground truth without a fresh, clean capture.**
**What's confirmed, independent of the mapping question**: `api/devices.py
boost()` validates `if body.period not in (1,2,3)` — the app's own "3 h"
button (`templates/tabs/dashboard.html` ~146, `val:4`) sends `period=4` and
is **rejected with 400 every time**, confirmed via isolated test (not just
static reading). That part of the finding stands regardless of which
mapping is correct: the app's 3rd Duration button is dead right now.
**Fix shape — do this FIRST, before changing any code:** capture a clean
HAR from energipays.com — one isolated boost click per duration button
(1 h, 2 h, 3 h), nothing else happening in between, no power changes mixed
in — to get an unambiguous period-value-to-duration mapping. Only then
correct whichever of {app buttons, MQTT `BOOST_DURATION_OPTIONS`, backend's
`(1,2,3)` validation range} is actually wrong — likely the backend's
validation range needs to extend to allow a real 3rd/4th period value once
the mapping is confirmed, and MQTT's option labels may need to drop
"30 min" entirely if the vendor removed it. Also cross-check
`dashboard_tab.js`'s toast label map (`{2:'1h', 3:'2h', 4:'3h'}` — a THIRD,
separate mapping found while investigating) against whatever the HAR
confirms.

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
- **v1.1.3 (2026-07-04) — Package 3 complete**: MQTT is now actually
  configurable from the UI. Phase 0 extracted the publisher lifecycle into
  `mqtt_lifecycle.reconfigure_mqtt()` (callable any time, not just at boot)
  with DB-override > env > default resolution (`store/db.get_mqtt_settings`/
  `set_mqtt_override`); Phase 1 added Supervisor auto-discovery
  (`GET /api/mqtt/discover`) and a real connect test (`POST /api/mqtt/test`);
  Phase 2 made the wizard's MQTT step persist and test for real; Phase 3 made
  the Settings → MQTT Discovery card fully self-service (enable/edit/test/
  save); Phase 4 added a "Run setup wizard" re-entry button with correct
  step-reset (the wizard's x-data instance persists across x-show, so a
  naive reopen would have shown stale "Setup complete" state). Verified
  end-to-end in the dev container against a real local Mosquitto broker:
  discovery found it via TCP probe, test performed a genuine CONNECT/CONNACK,
  enabling via the API started a publisher that actually connected, and
  toggling off/on twice showed zero subscriber leak. Known gap: the
  Supervisor discovery branch itself needs a live HA check — no fake
  Supervisor exists to simulate it in the dev container.
- **v1.1.4 (2026-07-04) — Auto-detect this Home Assistant for push
  notifications**: sending a notification previously always required a
  manually-typed HA URL + manually-generated long-lived token, even when the
  add-on runs inside the exact HA instance it wants to notify. Added
  `homeassistant_api: true` to config.yaml (Supervisor Core-API proxy grant,
  parallel to MQTT's `hassio_api` grant from v1.1.3);
  `ha_supervisor.sync_supervisor_ha_instance()` auto-registers a "This Home
  Assistant" instance every boot using `http://supervisor/core` +
  `SUPERVISOR_TOKEN`, zero user input, auto-defaulted if nothing else is
  default. New `ha_instances.source` column (migration v6) distinguishes it
  from user-added instances; Settings renders it with a 🏠 badge and only an
  Enable/Disable control (no Edit/Delete — enforced server-side too, not
  just hidden in the UI). Verified via an isolated test simulating the HA
  add-on env: first-sync creates + auto-defaults correctly, re-sync preserves
  a user's "disabled" choice, a crafted edit request is correctly restricted
  to the enabled field only, delete is correctly rejected. Known gap: same
  shape as v1.1.3's MQTT Supervisor path — the actual reachability check
  needs a live HA check, no fake Supervisor to simulate here.
- **v1.1.5 (2026-07-04) — Boost-vs-active-rule conflict, logging
  correctness, single-state rule buttons**: diagnosed live (via Chrome +
  direct REST calls against the user's real HA instance) that Energipays now
  refuses to boost while a rule is active, mislabeling the rejection as
  `"Heater is disabled"`. `POST /api/boost` gained a `clear_rule` flag that
  clears the active PD rule and only proceeds to boost if the clear
  succeeds; the "Active Rule Conflict" modal is renamed "Clear Rule & Boost"
  with honest copy (the rule stays cleared — no vendor "temporary override"
  concept exists). Also fixed: boost/cancel-boost/set-device-status logging
  (WARNING → ERROR for failures; `set_device_status` was logging "OK"
  unconditionally even on failure; `cancel_boost` had no failure log at
  all); a doubled "Boost failed: Boost failed: ..." toast; and rule cards
  showing both Enable AND Disable simultaneously — traced to a real bug
  (`disableRule()` clears whichever rule is active for the circuit, not the
  clicked rule, so the old dual-button state could silently clear the wrong
  rule). Verified via a mocked-client test (correct clear→boost ordering, a
  failed clear stops before ever attempting the boost) and a dev-container
  template render; Package 1 and Package 3 guards re-checked with zero
  regressions.
