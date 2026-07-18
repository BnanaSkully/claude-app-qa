# `qa` project configuration

Every command in this plugin reads **`.claude/qa.json`** from the project root. The file is
optional — with no config the commands fall back to auto-discovery and sensible defaults
(`http://localhost:3000`, output to `./checks`) — but a config makes every run sharper, because the
orchestrator stops guessing your area map, your roles, and how to snapshot your database.

Generate one with **`/qa:setup`**, which inspects the project and fills in what it can find.

## Full schema

> **The block below is annotated with `//` comments for readability. JSON does not allow
> comments and the loader uses a strict parser — strip them before use, or start from the
> minimum config further down, which is plain JSON and is what CI actually verifies.**

```jsonc
{
  // What the app IS. Handed to every agent so its judgement is grounded in your product,
  // not generic web-app taste. The single highest-value field in this file.
  "app": {
    "name": "Acme Billing",
    "description": "A B2B invoicing SaaS. Multi-tenant: Account > Workspace > members.",
    "stack": "Django + React + Postgres, local via docker compose",
    // The thing that must never be wrong. Agents weight findings against this.
    "coreValue": "Invoice totals must be exactly right. A wrong amount loses a customer."
  },

  "urls": {
    "web":     "http://localhost:3000",
    "api":     "http://localhost:8000",
    "health":  "http://localhost:8000/health",   // expected to return 2xx when up
    "openapi": "http://localhost:8000/openapi.json" // optional; enumerates real endpoints
  },

  // Shell commands. Written for YOUR stack — the plugin never assumes docker.
  "commands": {
    "start": "docker compose up -d",
    "logs":  "docker compose logs --since 10m backend",
    "test":  "docker compose exec backend pytest -q"
  },

  "paths": {
    "output":       "checks",                      // reports + screenshots land here
    "frontendPages":"frontend/app/**/page.tsx",    // glob: used to sanity-check the area map
    "backendRoutes":"backend/app/routers/*.py",    // glob: same, for the API side
    "solutions":    "docs/solutions"               // optional: past fix write-ups, read as "known"
  },

  // How a probe becomes a specific user. Omit the whole block to run anonymously.
  "auth": {
    // localStorage keys set BEFORE first paint. Values are templates over the identity dict.
    "localStorage": {
      "actingUserId":   "{userId}",
      "activeTenantId": "{tenantId}"
    },
    // Optional: cookies set the same way.
    "cookies": {},
    // Header sent on direct API calls when acting as a user.
    "header": { "X-User-Id": "{userId}" },
    // How the orchestrator DISCOVERS real ids each run instead of hardcoding them.
    "discover": {
      "tenants": "GET /api/workspaces",
      "users":   "GET /api/workspaces/{tenantId}/users"
    },
    // Pages only a privileged role can render. Probing these as a normal user often
    // silently redirects to the dashboard and reports them "clean" — see reference/environment.md.
    "privilegedPaths": ["/admin", "/dev"],
    "privilegedRole":  "admin"
  },

  // Snapshot/restore around mutating sweeps. Omit to skip snapshotting entirely
  // (the commands will then refuse to run their mutating passes and say why).
  "database": {
    "snapshot": "docker compose exec -T db pg_dump -U app -Fc -f /tmp/qa.dump appdb",
    "fetch":    "docker compose cp db:/tmp/qa.dump {dest}",
    "restore":  "docker compose cp {src} db:/tmp/restore.dump; docker compose stop backend; docker compose exec -T db psql -U app -d appdb -c \"DROP SCHEMA public CASCADE; CREATE SCHEMA public;\"; docker compose exec -T db pg_restore -U app -d appdb --no-owner /tmp/restore.dump; docker compose start backend",
    // A cheap query or endpoint whose result proves the restore actually landed.
    // Prefer a QUERY if your API needs auth: an anonymous GET that returns 401 looks
    // identical to a failed restore, forever. e.g.
    //   "docker compose exec -T db psql -U app -d appdb -t -c \"select count(*) from item\""
    "verify":   "GET /api/workspaces",
    // What that verify should look like on a clean seed, e.g. "invoice=402 user=13".
    "fingerprint": ""
  },

  // The area map. Each area becomes ONE agent in a full sweep.
  // Omit and the commands derive areas from paths.frontendPages instead.
  "areas": [
    {
      "name": "invoices",
      "prefix": "IN",                                    // stable id prefix for suggestions
      "pages": ["/invoices", "/invoices/{id}"],
      "code":  ["frontend/app/invoices", "backend/api/invoices.py"],
      // The riskiest things here — where agents spend their budget first.
      "attackFirst": "money totals, tax basis, the approve/reopen state machine, duplicate guard"
    }
  ],

  // Roles to drive. The capability grid lets agents spot 'shown but server-refused'
  // and 'granted but never surfaced' gaps. Omit if the app has no role model.
  "roles": {
    "ranks": ["admin", "owner", "manager", "member", "viewer"],
    // Authoritative source in code — agents defer to this over the grid below.
    "source": "backend/permissions.py",
    "matrix": ".claude/qa/role-matrix.md"
  },

  // Project-specific deliberate behaviours. The single best defence against false
  // positives — anything listed here is refuted rather than reported. See the template.
  "byDesign": ".claude/qa/by-design.md",

  "browser": {
    // null = auto-discover Chrome/Edge/Chromium/Brave. Set an absolute path to force one.
    "executable": null,
    // Extra command-line flags passed to the browser, e.g. ["--lang=en-GB"].
    "args": []
  },

  // A CSS selector that means "the app has finished rendering". WORTH SETTING.
  //
  // Without it the probes fall back to "the body has any text or any child", which a
  // loading spinner or an empty nav shell satisfies instantly. On an app whose data
  // arrives in more than a second or so, every viewport then measures the SKELETON and
  // the page is reported clean across the whole matrix — the exact false clean these
  // sweeps exist to avoid.
  //
  // When you DO set it, it is AUTHORITATIVE: if the selector never appears, the page is
  // reported not-ready, the run exits 4 ("evidence not trustworthy") and nothing is
  // recorded as clean. So pick something that appears only once real content is on
  // screen — and not something that also matches the page's own loading state.
  // Good: "[data-loaded]", "main table tbody tr", ".dashboard-kpi".
  // Bad:  a wrapper like ".app" or ".page" that exists while "Loading…" is showing.
  "readySelector": null
}
```

## Minimum useful config

You do not need all of it. This alone materially improves a run:

```json
{
  "app": {
    "name": "Acme Billing",
    "description": "B2B invoicing SaaS, multi-tenant",
    "coreValue": "Invoice totals must be exactly right."
  },
  "urls": { "web": "http://localhost:5173", "api": "http://localhost:8000", "health": "http://localhost:8000/health" },
  "commands": { "start": "docker compose up -d", "logs": "docker compose logs --since 10m backend" },
  "paths": { "output": "qa-reports" }
}
```

> This example is deliberately written with non-default values (`5173`, `qa-reports`
> rather than the built-in `3000` and `checks`). `scripts/validate.py` loads it through the
> real config loader on every CI run and asserts those values come back out — which only
> proves anything if they differ from the defaults. Keep it that way if you edit it.

## Environment variable overrides

Every tool honours these, and they win over the config file — handy for a one-off run against
a staging URL:

| Variable | Overrides |
|---|---|
| `CLAUDE_QA_WEB_URL` | `urls.web` |
| `CLAUDE_QA_API_URL` | `urls.api` |
| `CLAUDE_QA_OUTPUT_DIR` | `paths.output` |
| `CLAUDE_QA_BROWSER` | `browser.executable` |

## The two companion files

- **`.claude/qa/by-design.md`** — deliberate behaviours that must never be reported as bugs or
  suggested away. Start from `reference/by-design.template.md`. Grow it every run: each finding you
  triage as "no, that's intended" belongs here, and it stops coming back.
- **`.claude/qa/role-matrix.md`** — who can do what, and how a probe becomes each role. Start
  from `reference/role-matrix.template.md`.
