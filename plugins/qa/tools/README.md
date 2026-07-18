# qa browser probes

Five standalone Python scripts that drive a headless Chromium-family browser over the
Chrome DevTools Protocol and report objective facts about a running web app. They are
app-agnostic: no framework assumptions, no hardcoded ports, no vocabulary tied to any
particular product.

Each script prints **exactly one JSON object to stdout** (so an agent can parse it) and
puts human chatter on stderr.

## Install

```
pip install -r requirements.txt
```

`websocket-client` is required. `psutil` is optional — without it, browser-process
cleanup falls back to PowerShell on Windows and `pkill` on macOS/Linux, which works
identically, just slower.

## Smoke test

Confirms a browser was found and the config resolved, without launching anything:

```
python -c "from qakit import cdp; c = cdp.load_config(); print('browser:', cdp.find_browser(c)); print('web.url:', c.get('web.url')); print('config:', c.source or '(defaults)')"
```

Expected output looks like:

```
browser: C:\Program Files\Google\Chrome\Application\chrome.exe
web.url: http://localhost:3000
config: (defaults)
```

If no browser is found the error lists every path that was tried and how to fix it.

## The scripts

| Script | Arguments | Emits |
|---|---|---|
| `ui_crawl.py` | `<path> [--as-user ID] [--as-tenant ID] [--all] [--max-clicks N] [--wall-seconds N] [--json-out FILE] [--headed]` | JSON: `on_load_problems`, `problems`, `notices_observed`, `requests_ge_400`, `clicked`, `clicked_count`, `controls_with_problems`, `auth_shim`. Optionally the same JSON to `--json-out`. |
| `viewport_probe.py` | `<path> [--as-user ID] [--as-tenant ID] [--matrix full\|phone\|desktop] [--settle SECS] [--headed]` | JSON: one entry per viewport with `overflowPx`, `offscreen[]`, `tinyTargets[]`, `vw/vh/docW`, `hasViewportMeta`. PNGs for **defective viewports only** in `<output.dir>/visual-hunts/shots/`. |
| `page_shot.py` | `<path> <out.png> [--as-user ID] [--as-tenant ID] [--width N] [--height N] [--scale F] [--mobile] [--full-page] [--settle SECS] [--headed]` | The PNG, plus JSON describing what was written (`saved`, `bytes`, `viewport`, `acting_as`, `rendered`). |
| `responsive_audit.py` | `<path> [<path> ...] [--widths 800,1280] [--height N] [--theme dark] [--as-user ID] [--as-tenant ID] [--mobile-below N] [--settle SECS] [--headed]` | PNGs at `<output.dir>/audit/<slug>_<width>.png`, plus JSON listing every `shots[]` entry and any `errors[]`. |
| `qakit/` | (support module, not run directly) | Browser discovery, config loading, CDP client, process cleanup, identity shim. |

Every `<path>` may be given with or without a leading slash (`/settings`, `settings`,
`invoices/123`, or `home` for the site root).

### What each one is for

- **`ui_crawl.py`** — clicks every visible control on one page and reports the faults that
  fall out (uncaught exceptions, console errors, 5xx responses, scary on-screen text).
  **Safe by default:** it skips controls whose label looks destructive (delete / remove /
  unmap / unlink / wipe / reset / discard / log out) and cancels any confirm dialog it
  opens. `--all` disables that skip — disposable environments only. It mutates data by
  design; do not point it at production.
- **`viewport_probe.py`** — measures responsive defects across a 13-row device x
  display-scaling matrix. Objective signals only, not taste.
- **`page_shot.py`** — one screenshot, anonymous or as an identity.
- **`responsive_audit.py`** — the same pages at several widths for eyeballing. Uses a free
  debug port per run, so several audits can run in parallel safely.

## Configuration

Optional. With no config at all the probes target `http://localhost:3000` and write to
`./checks`. To configure, create `.claude/qa.json` anywhere at or above your working
directory (the probes walk up from cwd to find it):

```json
{
  "web":    { "url": "http://localhost:3000" },
  "api":    { "url": "http://localhost:8000" },
  "output": { "dir": "./checks" },
  "browser": { "executable": null, "args": [] },
  "readySelector": ".app-shell",
  "auth": {
    "localStorage": {
      "actingUserId":   "{userId}",
      "activeTenantId": "{tenantId}"
    },
    "cookies": {}
  }
}
```

- `readySelector` — optional CSS selector meaning "the app has rendered". Purely an
  optimisation; without it the probes fall back to document readiness plus rendered
  content, which works on any app.
- `output.dir` — relative paths resolve against the folder containing `.claude/`, so the
  probes write to the same place no matter where you run them from.

### The identity shim

`--as-user` and `--as-tenant` are substituted into the `{userId}` and `{tenantId}`
placeholders in the `auth` block, and installed with
`Page.addScriptToEvaluateOnNewDocument` **before navigation**, so the app sees them on
first paint rather than booting anonymously and needing a reload. `auth.cookies` works the
same way via `Network.setCookie`.

If your config declares no `auth` block, or you pass no identity, this is a no-op and the
probes run anonymously — every script still works.

Template entries whose placeholders you did not supply are skipped rather than written as
a literal `{userId}`; the JSON `auth_shim.skipped` list tells you which.

## Environment variables

All of these override the config file.

| Variable | Overrides | Example |
|---|---|---|
| `CLAUDE_QA_WEB_URL` | `web.url` | `http://localhost:5173` |
| `CLAUDE_QA_API_URL` | `api.url` | `http://localhost:8000` |
| `CLAUDE_QA_OUTPUT_DIR` | `output.dir` | `/tmp/qa-output` |
| `CLAUDE_QA_BROWSER` | `browser.executable` | `/usr/bin/chromium` |
| `CLAUDE_QA_BROWSER_ARGS` | `browser.args` | `--lang=en-GB --force-color-profile=srgb` |

## Browser discovery

Tried in order: `CLAUDE_QA_BROWSER`, then `browser.executable` from config, then the
well-known install locations for Chrome, Edge, Chromium and Brave —
`%PROGRAMFILES%` / `%PROGRAMFILES(X86)%` / `%LOCALAPPDATA%` on Windows, `/Applications`
on macOS, and `which` against `google-chrome`, `chromium`, `chromium-browser`,
`microsoft-edge` and `brave-browser` on Linux.

## Exit codes

| Code | Meaning |
|---|---|
| `0` | Success |
| `1` | Ran, but something in the work failed (e.g. a screenshot did not capture) |
| `2` | Setup problem: no browser, bad config, bad arguments |
| `3` | The app is not reachable at the configured URL |

## Notes on two details that are easy to get wrong

**Measure against the layout viewport, not `window.innerWidth`.** Under mobile emulation
Chrome shrinks-to-fit a too-wide page, and `innerWidth` then reports the *visual* viewport
after that zoom-out — 1440 for an emulated 360px phone, or 980 on a page with no viewport
meta. Measuring overflow against that inflated number silently misses every element that
sticks out past the real viewport but lands inside the visual one. `viewport_probe.py`
uses `document.documentElement.clientWidth` and reports `innerWidth` separately as
`visualViewportPx`.

**Kill browser processes by profile directory, not by PID.** A Chromium browser spawns
GPU / utility / network / renderer processes that are not all children of the launcher, so
terminating the launcher PID leaves roughly eight processes behind holding debug ports and
profile directories. Every one of them carries the unique `--user-data-dir` path on its
command line, so `shutdown()` matches on that instead. On Windows the fallback also filters
by process *name* — the cleanup command's own command line contains the profile string, so
matching on command line alone would have it kill itself.
