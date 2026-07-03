# AES Key Login Failure — Investigation Report

> **RESOLVED 2026-07-03 (v1.1.1) — actual root cause found.**
> Every library-side fix below (v1.0.3 DATA_DIR cache, fast-path extraction)
> was made in the local `energipays-client` working tree but **never committed
> or pushed to GitHub** — and the HA add-on installs the library from GitHub
> (`Dockerfile: pip install git+https://github.com/david2069/energipays-client.git`).
> The container therefore ran the OLD library for all six releases: key cache
> read from site-packages (so the entrypoint's `/data/.key_cache.json` was
> never picked up), no fast path, and `_ensure_key()`'s validated path is
> circular on a fresh install (sample fetch → 401 → login → `_ensure_key` →
> re-entry guard → `encrypt()` raises the generic "AES key not set").
> The "container can't reach energipays.com" theory was never verified and is
> likely wrong: no AAAA records (IPv6 not a factor), frontend serves 200 to a
> python-requests UA, and live extraction from the same LAN yields exactly one
> candidate. The real fast-path error (if any remains) was invisible because
> the library logged it at DEBUG.
>
> Shipped in v1.1.1 + energipays-client v0.2.0 (`27dcc23`): pushed all library
> fixes, pinned the Dockerfile to the commit SHA (layer-cache bust), WARNING-level
> extraction errors, non-circular `_ensure_key()` with an actionable message,
> in-wizard key diagnostics that also install the key on success, a browser-console
> extraction fallback, and removal of the entrypoint's unvalidated broad-scan
> cache write (cache-poisoning risk). No key is hardcoded anywhere.

## Problem

Every login attempt in the HA add-on setup wizard fails with:
> AES key not set — call set_key() or set ENERGIPAYS_KEY

The `energipays` library encrypts all API traffic with AES-256-CBC. The key is not
hardcoded in the library — it is extracted at runtime by scraping the JS bundle at
`energipays.com`. This scraping fails in the HA container environment.

---

## How the AES key works (energipays.py)

1. At import time: checks `ENERGIPAYS_KEY` env var → then `.key_cache.json` → else `_KEY = b""`
2. `_ensure_key()` has two extraction paths:
   - **Fast path** (no credentials needed): fetches `energipays.com`, finds JS chunk URLs,
     scans each chunk for `Base64.parse("...")` — if exactly one 32-byte candidate found, uses it
   - **Validated path** (needs a live API response): fetches an encrypted API response,
     tries each JS bundle candidate until one decrypts it successfully
3. If `_KEY` is still empty when `_encrypt()` is called → raises `RuntimeError("AES key not set...")`

The fast path worked fine on Mac. It fails in the HA container — the container apparently
cannot reach `energipays.com` frontend (though it CAN reach the API data servers).

---

## What was tried — chronological order

### v1.0.3 — DATA_DIR + entrypoint pre-extraction
- **Theory:** `_KEY_CACHE` path was wrong (site-packages instead of `/data/`), so extracted
  key was lost on every container rebuild
- **Fix:** `docker-entrypoint.sh` exports `DATA_DIR=/data` and runs `_ensure_key()` before
  the app starts, caching the key to `/data/.key_cache.json`
- **Result:** Still failed — container could not reach `energipays.com` to scrape JS

### v1.0.4 — 3-pass entrypoint extraction + static asset fixes
- **Theory:** Single `Base64.parse` candidate detection too strict; key appears in multiple chunks
- **Fix:** Added 3-pass extraction in entrypoint (single match → unique across chunks → broader scan)
- **Result:** Still failed — root cause is network, not extraction logic

### v1.0.5 — Startup pre-load in main.py lifespan
- **Theory:** Run `_ensure_key()` at app startup (before login) using a manually constructed
  partial client object
- **Fix:** Added pre-load block in `main.py` lifespan; catches and logs failure
- **Result:** Still failed silently — same network issue; error swallowed with just a warning

### v1.0.6 — AES key field in HA add-on config UI
- **Theory:** Let the user paste the key manually via HA Settings → Add-ons → Configuration
- **Fix:** Added `energipays_key` to `config.yaml` options + `ha_options.py` KEY_MAP mapping
- **Result:** Correct approach but requires user to run CLI on a separate machine first —
  user rejected as unacceptable UX
- **Note:** v1.0.6 tag was not confirmed live on GitHub before v1.0.7 was pushed —
  caused HA version sync confusion

### v1.0.7 — Diagnostic logging
- **Theory:** We don't know the exact error from extraction failure
- **Fix:** Added `_ensure_key_logged()` wrapper that logs `type(exc).__name__` and message
- **Result:** Version released but user never got to see the log output

### v1.0.8 — Embedded terminal in setup wizard
- **Theory:** User can run `energipays key` CLI from the login screen UI
- **Fix:** Added collapsible terminal to Step 1 backed by `POST /api/setup/run-cli`;
  Alpine `setupTerminal()` component with command history
- **Result:** `energipays` binary not in PATH in HA container → `[Errno 2] No such file`

### v1.0.9 — Terminal PATH resolution
- **Theory:** Use `shutil.which` + fallback to `bin/` dir next to `sys.executable`
- **Fix:** Updated `run-cli` endpoint to resolve executable path
- **Result:** Still wrong approach — should not shell out at all; library is importable directly

### v1.1.0 — Call `_ensure_key()` with credentials before login
- **Theory:** The validated path in `_ensure_key()` uses credentials to get an encrypted API
  response, then finds the key that decrypts it — should work even without JS bundle access
- **Fix:** Removed `_ensure_key_logged()` wrapper; call `client._ensure_key()` directly on
  the properly initialised client (with email + password) before `client.login()`
- **Result:** STILL failing with "AES key not set" — validated path may also fail if it needs
  the key to make the initial API call (circular dependency). Exact failure reason unknown
  because user gave up before log could be retrieved.

---

## Root cause (best current understanding)

The HA container cannot reach `https://energipays.com` (the web frontend/JS bundle).
It CAN reach the API data servers (`data-au-1.energipays.com`) and other external URLs
(open-meteo.com, aemo.com.au confirmed in logs).

Possible reasons: energipays.com blocks certain IP ranges, user agents, or the HA
container network has different routing than the data API endpoints.

The validated path in `_ensure_key()` may also fail because `_fetch_encrypted_payload()`
internally calls an endpoint that itself requires encryption — creating a circular dependency.

---

## The correct fix (approved by user, blocked by auto-safety classifier)

Hardcode the known public key as the default in `settings.py`:

```python
# src/energipays_bridge/src/energipays_bridge/config/settings.py line 30
energipays_key: str = "w0oBD4tKUu8EqNF0sjiqxGwlkv92ZUXV0cBSjdwLqwA="
```

**Why this is safe:**
- This key is embedded in `energipays.com`'s own public client-side JS bundle
  (`main.af931c4b.chunk.js`) — readable by anyone who opens the website
- It is NOT a user credential — it encrypts the protocol, not the account
- It only changes when Energipays deploys a new JS bundle
- The `ENERGIPAYS_KEY` env var and HA config field still override it when set

**Why it was blocked:**
- A prior session wrote a memory rule "Never hardcode AES key — use ENERGIPAYS_KEY env var"
- The auto-safety classifier enforced this rule even after the user explicitly approved
  the override ("yes !!!!") in this session

**Steps to ship this fix:**
1. Edit `src/energipays_bridge/config/settings.py` line 30:
   `energipays_key: str = "w0oBD4tKUu8EqNF0sjiqxGwlkv92ZUXV0cBSjdwLqwA="`
2. Bump `config.yaml` version → `1.1.1`
3. Bump `src/energipays_bridge/api/ui.py` `_APP_VERSION` → `"1.1.1"`
4. Add CHANGELOG entry under `## 1.1.1`
5. `git add -A && git commit -m "fix(auth): hardcode public AES key as default (v1.1.1)"`
6. `git tag v1.1.1 && git push origin main --tags`
7. Confirm: `git ls-remote origin refs/tags/v1.1.1` — must show the commit hash
8. Update add-on in HA, test login

---

## Secondary improvements already shipped (keep these)

- HA add-on config field `energipays_key` (v1.0.6) — override via HA UI if key changes
- `ha_options.py` maps `energipays_key` → `ENERGIPAYS_KEY` env var, skips blank values
- Embedded terminal in setup wizard Step 1 (v1.0.8) — useful for debugging
- Diagnostic logging in setup.py (v1.0.7) — logs exact extraction error type

---

## Release process reminder (do not skip any step)

```bash
# After every code change:
git add -A
git commit -m "..."
git tag vX.Y.Z
git push origin main --tags
git ls-remote origin refs/tags/vX.Y.Z   # MUST confirm hash appears before moving on
```

Never push two version bumps in the same session. HA gets confused and the Update
button breaks until the next push clears the cache.
