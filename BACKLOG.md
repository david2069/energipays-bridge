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

**Status: approved, queued — priority: normal.** 2a shipped as v1.1.7 on
2026-07-08 — see Shipped below. 2b/2c remain.

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
should not be re-investigated.

**Update 2026-07-08 — write-back verification shipped in v1.1.6, hypothesis
1 further confirmed live.** Tested directly: a 2-slot edit to an INACTIVE
rule ("New PD Test") persisted exactly as sent (confirmed via re-GET, then
reverted) — the silent-discard is specific to editing the ACTIVE rule, not
edits in general. Per the earlier "test against an inactive rule" plan,
this result is strong confirmation of hypothesis 1; testing directly
against the real active rule was deliberately skipped (it controls a real
physical heater's live schedule) since the write-back-verification fix
below makes root-cause certainty less urgent — it catches the failure
regardless of cause. `saveRule()` now always re-fetches and diffs
what was sent vs. what persisted, surfacing a real visible error instead
of a false "saved" message. **What's still open, NOT done**: (a) any
proactive UI warning/block when opening the editor on the currently active
rule (we now detect and report the failure after the fact, but don't yet
warn beforehand or block/redirect to a disable-then-edit flow); (b) the
everyday/single-day editor day-key mismatch (hypothesis 3) — separate from
the discard-detection work, still unconfirmed and unfixed.

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

### [defect] Battery SoC ring never fills past ~75% — stale dash-offset truncates the arc
Reported 2026-09-21. Power Flow card's FranklinWH Battery ring renders short:
at 100% SoC it looks roughly 90% full; the missing wedge sits in the
**upper-left quadrant**.

Cause is arithmetic, in `templates/tabs/dashboard.html:568-575`. The arc is
`r=20` (circumference 2*pi*20 = 125.66), drawn with:
```
:stroke-dasharray="((soc||0)/100*125.7).toFixed(1)+' 125.7'"
stroke-dashoffset="31.4"
transform="rotate(-90)"
```
The gap length equals the full circumference, so a non-zero `stroke-dashoffset`
does not merely rotate the start — it pulls the trailing gap back into the
visible path. With offset 31.4 (= a quarter of 125.7) the dash covers path
positions `[0, 94.3]` only, i.e. **75% of the ring, hard-capped for any SoC
>= 75%**. `rotate(-90)` already moves the start to 12 o'clock, so the 31.4
offset is a leftover double-rotation: visible arc runs 12 o'clock -> 9 o'clock
clockwise, which is exactly where the observed gap is.

**Fix shape:** drop `stroke-dashoffset` (or set it to `0`) and keep
`transform="rotate(-90)"`. Optionally use 125.66 rather than 125.7 so a true
100% cannot round short. Verify at 100% *and* at a mid value (the bug is
invisible below 75%, which is why it survived this long) — the dev container
can be forced with a fake `ext.battery_soc` point.

Note the sibling ring above it (`temp-ring-fill`, line 82) computes its offset
through `tempRingOffset()` and is NOT affected — do not "fix" that one to match.

### [defect] Inverter State: template enum map is off-by-one, and FAULT renders as neutral while STANDBY renders red
Found 2026-09-21 during the full register cross-reference sweep. The official
SunSpec definition (`model_701.json`, vendored in
`~/dev/modbus/venv/.../sunspec2/models/json/`) gives `InvSt` as:
```
0=OFF  1=SLEEPING  2=STARTING  3=RUNNING  4=THROTTLED
5=SHUTTING_DOWN  6=FAULT  7=STANDBY
```
Our two Python maps are **correct** — `modbus_poller.py:157` and
`api/integrations.py:17` both match the spec exactly. The template
(`dashboard.html:1176`) does **not**:
```
{1:'Off',2:'Sleeping',3:'Running',4:'Starting',5:'Throttled',6:'Shutting Down',7:'Fault',8:'Standby'}
```
Shifted +1 against the spec, and Running/Starting additionally swapped. Every
value maps to the wrong label: raw 7 (STANDBY) renders as **"Fault"**, raw 4
(THROTTLED) renders as "Starting", raw 1 (SLEEPING) renders as "Off".

**The colour binding on line 1175 is wrong the same way and matters more:**
it paints `==3` green and `==7` red. Per spec 3=RUNNING (green, correct) but
7=STANDBY — so normal standby is styled as an alarm, while **6=FAULT, the one
state that should scream, gets neutral grey.**

**Currently masked, not currently harmless.** The poller decodes to a string
before the template sees it, so the map lookup misses and
`|| points['ext.inverter_state']` falls through to the decoded text — which is
why the screen reads "Running" correctly today. The bug is one integration
away from surfacing: any REST/MQTT/HA-WS source mapped to `inverter_state`
supplies a raw integer, and every label and colour then goes wrong at once.

**Fix shape:** delete the template's map and colour on the decoded string, the
same shape as the Connection State fix. This is the FOURTH instance of one
enum decoded in multiple places with divergent mappings (see also Boost
Duration, Grid Mode, Connection State) — the systemic fix is one decode at the
poller, labels only in the UI.

### [defect] "Grid Mode" is derived from a register FranklinWH certified as UNIMPLEMENTED
Reported 2026-09-21, found while checking the FranklinWH Modbus docs for the
battery-sign question. The Battery sub-tab renders **Grid Mode: Grid Following**
in confident blue (`templates/tabs/dashboard.html:1192`), decoded from
`701.DERMode` (`sunspec_scanner.py:79`, `modbus_poller.py:164-173`).

**FranklinWH's own signed SunSpec conformance certificate (SM-000028) lists
`DERMode` — all three states, `GRID_FOLLOWING`, `GRID_FORMING`, `PV_CLIPPED`
— as `unimplemented`.** Not partially supported: explicitly unimplemented, by
the vendor, about their own hardware. Source:
`franklinwh-modbus-bridge/docs/reference/FranklinWH_Modbus_PICS_SM-000028.tsv`
row 78, analysed in that repo's `docs/vendor-issues.md` Issue 12d.

That project reached the conclusion empirically before finding the
certificate: of 13 "PV Clipped" events over 30 days, **12 occurred with solar
output at 0-100 W** — night, dawn, midnight — when PV clipping is physically
impossible. Their verdict: "Whatever value comes back should be treated as
**meaningless noise, not a real signal**." The bit *ordering* we assume
(0/1/2 = Following/Forming/Clipped) does match the official model spec, so the
decode is right; the register behind it just does not do anything.

We are currently presenting that noise to the user as fact, styled identically
to the genuinely-supported rows beside it. Worse, our decode differs from the
neighbouring one: `modbus_poller.py:166-173` treats DERMode as a *bitfield*
(`v & 2`, `v & 1`, `v & 4`) while `dashboard.html:1192` treats it as an *enum*
(`{1:'Grid Following',2:'Grid Forming',3:'Island',4:'Microgrid'}`) — two
different readings of the same meaningless number, with different labels
("Island"/"Microgrid" appear nowhere in the SunSpec spec for this point).

**Fix shape (decide which):** (a) drop the Grid Mode row entirely — the
honest option, since no amount of decoding makes an unimplemented register
informative; or (b) keep it behind an explicit "derived, vendor-certified
unimplemented" disclaimer, never styled as authoritative. Either way delete
one of the two contradictory decodes. Do NOT spend time "fixing" the mapping
— that work has already been done twice and the register is the problem.

### [defect] Battery tab "Battery State" shows Charging while actually discharging (sign inverted)
Reported 2026-09-21. Battery sub-tab shows **Battery State: Charging** (green)
at the same moment the Power Flow node directly above it shows
**"↓ Discharging 0.10 kW"**. Two renderings of one register, disagreeing
on screen simultaneously.

Ground truth says discharging, two ways. Live sample:
`ext.battery_power_w = +200.0`, `ext.battery_state = "charging"`. Energy
balance from the same frame: Grid 0.00 kW + Solar 0.05 kW in vs Home Loads
0.24 kW out — the ~0.19 kW deficit can only come from the battery. A second
frame agrees (Grid 0.20 + Solar 0.02 in, Loads 0.70 + Thermino 0.25 out,
battery 0.5 kW). So for this device **positive `battery_power_w` = discharging**.

`modbus_poller.py:153` has it backwards:
```
result["battery_state"] = "charging" if pw > 50 else "discharging" if pw < -50 else "idle"
```
Both UI renderings use the opposite (correct) convention —
`dashboard.html:541` and `dashboard.html:1108` both map `pw > 50` to
Discharging. The poller is the outlier, 1 against 2.

**Smoking gun:** the flow node at `dashboard.html:536-541` does not trust
`ext.battery_state` at all. It checks only whether the string *contains*
"charg" (true for both words) and then recomputes the label from the power
sign. That is an undocumented workaround for this very bug, applied in one
place and not the other — which is exactly why the flow node reads correctly
and the Battery tab row (1160, renders the raw string verbatim) does not.

**Fix shape:** invert the poller's derivation so `battery_state` matches the
power-sign convention the rest of the app already uses. Fixing the poller does
NOT double-flip the flow node — it discards the string's meaning either way —
but once the poller is trusted, the 536-541 workaround should be deleted and
the label read straight, otherwise the next person hits the same puzzle.

**CONFIRMED 2026-09-21 by the vendor library's own register documentation.**
`~/dev/modbus/SUNSPEC_MODEL_REFERENCE.md:132`, the M714 point table:
```
| DCW | DC Power (W) (Positive = Discharge, Negative = Charge) | OK |
```
That is the primary source — the `franklinwh-modbus` library that owns this
register map. Our `modbus_poller.py:153` says the opposite. Three independent
layers now agree against us: the library docs, the sibling bridge's code
(below), and the physical energy balance in the two reported frames.

**Corroborated by `franklinwh-modbus-bridge`.** That project reads the *same register on the same
hardware* and derives the opposite labels from an otherwise identical
expression (`gateway/aggregator.py:100-110`):
```
# positive = discharging, matching the per-gateway convention
if bp > 50:     result["battery_state"] = "Discharging"
elif bp < -50:  result["battery_state"] = "Charging"
else:           result["battery_state"] = "Standby"
```
Same 50 W deadband, same source point (`714.DCW` — `sunspec_scanner.py:94`
here, `static/js/app.js:286` there), inverted labels. This reads as a copy
that got flipped. Four further sites in that repo document the same
convention: `docs/reporting-sankey-design.md:40` ("+ = discharge,
− = charge"), `gateway/energy_flow.py:62`, `gateway/scheduler_sensors.py:277`,
`gateway/aggregator.py:100`. **Positive `714.DCW` = discharging is settled.**

Note `modbus_poller.py:151` only derives the field when a REST integration has
not already supplied `battery_state`; the live prod value is the derived one.
Consider also renaming the idle state to "Standby" to match the sibling.

### [defect] Battery tab "Connection State" always reads Disconnected (string compared as number)
Reported 2026-09-21. Battery sub-tab shows **Connection State: Disconnected**
in red while Battery State = Charging and Inverter State = Running — i.e. the
system is demonstrably connected.

The data is correct; only the rendering is broken. `/api/points/latest`
returns `ext.connection_state = "Connected"` — `modbus_poller.py:161-162`
already decodes the SunSpec enum to a human string. But
`templates/tabs/dashboard.html:1185-1186` still compares it numerically:
```
x-text="...points['ext.connection_state']>0?'Connected':'Disconnected'"
```
`"Connected" > 0` coerces to `NaN > 0` -> **false**, so the row renders
"Disconnected" in red 100% of the time, whatever the device reports.

This is the only row in the Battery tab that does this. Inverter State (1173)
and Grid Mode (1192) use the `({map})[v] || v` pattern, which falls through
harmlessly when handed an already-decoded string — that is why they display
correctly and this one does not.

**Fix shape:** render the string directly and colour on equality
(`=== 'Connected'`), matching the neighbouring rows. While there: the same
enum is decoded in THREE places with two different predicates —
`modbus_poller.py:162` and `api/integrations.py:29` use `v == 1`,
`settings_tab.js:536` uses `v === 1`, and this template used `> 0`. Collapse
to one decode at the poller and let the UI render the label.

**What the field actually means** (asked at report time): it is SunSpec
model 701 `ConnSt` (`sunspec_scanner.py:78`) — the DER's **grid connection**
state at the point of common coupling, i.e. whether the inverter's AC output
is tied to the grid. It is neither the Modbus/TCP link health (if that were
down, no rows would populate at all) nor the inverter run state — that is the
separate `InvSt` "Inverter State: Running" row. Grid *mode* (`DERMode` ->
"Grid Following") is a third, distinct field. Consider relabelling to
"Grid Connection" so it stops reading as "the bridge lost its link" —
`franklinwh-modbus-bridge` independently landed on exactly that name for this
register (`publish/entities.py:362-370`: slug `grid_connection_state`, name
"Grid Connection", icon `mdi:transmission-tower-import`).

**The register itself is trustworthy:** FranklinWH's own SunSpec PICS
certificate SM-000028 lists `ConnSt` as `supported` (row 75 of
`franklinwh-modbus-bridge/docs/reference/FranklinWH_Modbus_PICS_SM-000028.tsv`),
and it appears in none of that project's 12 catalogued vendor defects. So this
is purely our rendering bug — the underlying data is fine, unlike Grid Mode
below.

## Notes / smaller items (unscheduled)

- **NOT A DEFECT — prod container is stale, do not re-investigate the
  Enable/Disable double-button.** Reported again 2026-09-21 (both Enable and
  Disable rendering on non-active rules, the exact v1.1.5 symptom). Verified
  the same day: the fix IS in the repo — `templates/tabs/rules.html:171`
  carries the "single state, matching isActive(rule) exactly" guard — and is
  NOT in the running container, whose copy still has the old
  `<!-- Enable/Disable toggle -->` markup at line 163. The prod image was
  built 2026-07-03, before v1.1.0; `/api/health` still returns the
  pre-v1.1.2 `safe_mode` key as a second tell. Every UI defect observed on
  :8080 must be re-checked against the dev container before being logged —
  ten releases of fixes are not deployed there. Rebuild is the fix.
- **Register cross-reference sweep, 2026-09-21 — CLEAN results, do not
  re-check.** All 13 Modbus points in `sunspec_scanner.py` were checked
  against the official SunSpec model JSON + the FranklinWH PICS certificate.
  Verified correct and needing no action: **`701.TmpAmb` / `701.TmpCab`**
  (int16 + `Tmp_SF`, matches our sf index 120; live 21.3 / 30.4 °C plausible);
  **`713.SoH`** (a real point in `model_713.json`, uint16 + `Pct_SF` — it is
  merely absent from the library's own summary table, which lists only
  WHRtg/WHAvail/SoC, so its absence there is not a red flag);
  **`713.WHRtg` / `WHAvail` / `SoC`** (all PICS `supported`);
  **`701.ConnSt` raw values** (0=DISCONNECTED, 1=CONNECTED — so `v == 1` in
  the Python decoders is right and only the template's `> 0` is wrong);
  **`701.DERMode` bit order** (0/1/2 = Following/Forming/Clipped — the
  poller's bitfield test `v&1`/`v&2`/`v&4` is structurally correct; the
  register is still unimplemented, see the defect above).
  Models **101/102/103/111/112/113** map `W`→`solar_power_w` legitimately for
  real PV inverters and are irrelevant on FranklinWH hardware — leave them.
  Also worth knowing: **`713.Sta` is a health enum (0=OK, 1=WARNING, 2=ERROR),
  NOT battery activity** — we correctly do not read it; do not "add battery
  state from 713.Sta" later, that was `franklinwh-modbus-bridge`'s Issue 4.
- **[unconfirmed] M714 DCW is read at a single fixed offset — breaks if a
  second battery is ever added.** `sunspec_scanner.py:94` reads
  `(4, "DCW", ..., "battery_power_w")` as one fixed offset. Per the library's
  own docs (`~/dev/modbus/SUNSPEC_MODEL_REFERENCE.md:118-122`), M714 is a
  **fixed block followed by repeating port blocks, one per parallel battery
  stack** — on `NPrt > 1` the library "sums DC power and energy values,
  extracts peak temperature, and averages DC voltage". We do none of that.
  Harmless today (one aPower, 13.6 kWh, NPrt=1, and the live value tracks
  reality) but it would silently under-report the moment a second stack is
  added. Same doc notes `DCA` is unimplemented and reads 0 — we don't read it,
  keep it that way, and derive current from `DCW / DCV` if ever needed.
- **[unconfirmed] `701.W` may be mislabelled as `solar_power_w`.**
  `sunspec_scanner.py:76` maps SunSpec `701.W` to `solar_power_w`.
  `franklinwh-modbus-bridge` maps the same point to **`grid_power_w`**
  (`static/js/app.js:290`, `publish/entities.py:288`) — 701 is *DER AC
  Measurement*, so W is the aGate's AC active power at the connection point,
  not PV — the library documents `701.80 W` as **"Total Active Power (W)"**
  (`~/dev/modbus/SUNSPEC_MODEL_REFERENCE.md:72`), and its M701 table lists only
  St/W/Var/PF/LNV/Hz, with no solar point anywhere in the model.
  On FranklinWH hardware solar is not a standard SunSpec point at all:
  it lives in the non-standard **extension registers 15502-15505**
  (total/proximal/remote1/remote2), which this bridge does not read.
  **Latent, not live:** `/api/points/latest` currently publishes no
  `ext.solar_power_w`, and the dashboard's Solar figure comes from the
  Energipays cloud, so nothing on screen is wrong today — but the mapping is
  a trap for whoever wires up Modbus solar next. Confirm what (if anything)
  701.W is producing before changing it; models 101/102/103/111/112/113 in the
  same table map W to solar legitimately for real PV inverters, so the fix is
  to special-case 701, not to rename globally.
- **FranklinWH Modbus reference lives in two sibling repos — check both
  before reverse-engineering any register.**
  **(1) `~/dev/modbus`** — the `franklinwh-modbus` library itself, the primary
  source: `SUNSPEC_MODEL_REFERENCE.md` (per-point compliance tables with sign
  conventions and workarounds), `docs/FRANKLINWH_MODBUS_EXTENSIONS.md` +
  `FRANKLINWH_EXTENSIONS_MANIFEST.md` (the 15500+ proprietary zone),
  `src/franklinwh_modbus/types.py:201` (`PICS_STATUS` — each point tagged
  functional / cosmetic / blocked), and
  `PICS_CONFORMANCE_CROSS_REFERENCE.md`.
  **(2) `~/dev/Claude/Projects/franklinwh-modbus-bridge`** — field analysis:
  `docs/vendor-issues.md` (12 catalogued vendor defects) and `docs/reference/`
  (the signed PICS certificate SM-000028 + SPAN's internal conformance review). Check there before reverse-engineering any FranklinWH
  register — it already answered the battery-sign and Grid Mode questions
  above, and documents quirks we have not hit yet (writes silently discarded
  on 6 registers, no M704 auto-revert safety net, reserve-SoC writes blocked
  in firmware, M713.Sta emitting an out-of-range `State400`).
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
- **v1.1.6 (2026-07-08) — P1 rule-save write-back verification**:
  `saveRule()` no longer trusts `r.ok` — it re-fetches `/api/rules` after
  every save and diffs the actual persisted day-keys/slots against what
  was sent (`_ruleDataMatches()`), surfacing a real visible error instead
  of a false "saved successfully" message when the server silently
  discards a change. Confirmed live: a 2-slot edit to an inactive test
  rule persisted correctly (then reverted), narrowing the silent-discard
  specifically to editing the currently ACTIVE rule — consistent with
  Energipays' own web UI blocking that operation, and with v1.1.5's
  finding that boosting is now blocked the same way. Testing directly
  against the real active rule was deliberately skipped (it drives a real
  physical heater's live schedule); this fix catches the failure
  regardless of root cause instead. Verified via 5 isolated diff-logic
  test cases (exact match, fewer slots persisted, an entire day-key
  ignored, correctly-collapsed everyday rules, key-order independence —
  all correct, no false positives) plus a dev-container render with
  Package 1 guards re-checked. Still open: proactive UI warning when
  opening the editor on the active rule, and the everyday/single-day
  editor day-key mismatch (hypothesis 3) — tracked separately in the
  Defects section.
- **v1.1.7 (2026-07-08) — Package 2a: solar forecast actually reflects your
  system**: `api/solar.py` switched from raw horizontal `shortwave_radiation`
  to `global_tilted_irradiance` with `&tilt=&azimuth=`, scaled by new
  `solar_kwp`/`solar_tilt_deg`/`solar_azimuth_deg` config
  (`estimated_kw = GTI/1000 × kWp × 0.85`); cache keyed on lat/lon/tilt/
  azimuth only so changing just system size never re-fetches from
  Open-Meteo. Settings card renamed "Location & PV System" with
  Azimuth°/Tilt°/Size(kWp) fields (matching a similar FWHAI panel's layout,
  with our own correct Open-Meteo-convention helper text) and a free "Sync
  Location from Cloud" button (device lat/lon already live in the Alpine
  store via the poller — no new API call). Dashboard shows a "set your
  system size" hint instead of wrong numbers when kWp is unset. Verified
  live: unset kWp → `estimated_kw: null` for every hour (hint shown); a
  6.6 kWp/22°/180° config → ~3.7 kW midday / ~4.2 kW peak in Sydney winter
  (66-75% of the 5.6 kW theoretical ceiling — physically sane, vs. the old
  bug's flat ~0.5 kW regardless of system size); changing only kWp
  (6.6→10.0) confirmed as a cache hit with an exactly-proportional peak
  (0.6394 ratio both times). Dev-container render clean, `radiation_wm2`
  fully gone codebase-wide, Package 1/3 guards re-checked with zero
  regressions.
- **v1.1.8 (2026-07-08) — Add Companion Device form fixes**: reported live
  from the HA add-on — `crypto.randomUUID()` only exists in secure
  contexts (HTTPS/localhost) and is `undefined` when the add-on is reached
  over plain HTTP on a LAN IP, a completely normal setup, causing "Test
  Notification" to fail outright. All 4 client-side ID-generation sites
  now go through a `uuidv4()` helper (native when available, a
  `Math.random()`-based v4 fallback otherwise — fine, since these are only
  local DB primary keys). Also fixed, all from one screenshot: Notify
  Service showed the same value in two widgets at once (select + input
  both bound to `service_target`) — now only one renders; Alias/Notify-
  Service placeholders used a realistic name/device-id that looked like
  real pre-filled data — changed to unambiguous "e.g. ..." examples; the
  "Enabled" checkbox appeared even when adding a brand-new device — now
  only shown when editing an existing one. Verified: isolated test with
  `crypto.randomUUID` explicitly undefined produces valid unique
  v4-format UUIDs; dev-container render clean; Package 1 guards
  re-checked with zero regressions.
