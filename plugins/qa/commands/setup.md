---
description: Set up qa for this project — inspect the codebase, write .claude/qa.json, scaffold the by-design and role-matrix files, and verify the browser probes actually run.
argument-hint: "[--force]"
---

# /setup — configure qa for this project

Args: **$ARGUMENTS** (`--force` overwrites an existing config).

You are setting up the `qa` plugin for **this** project. The goal is a `.claude/qa.json` that
makes every later sweep specific to this app instead of generic. Work the steps in order, then show
the user what you wrote and what you couldn't work out.

**If `.claude/qa.json` already exists and `--force` was not passed:** read it, report what's
there, point out anything missing or stale, and stop. Don't silently overwrite someone's config.

## 1. Work out what this app IS

Read the project's `README`, `CLAUDE.md`, `package.json`, `pyproject.toml` / `requirements.txt`,
`docker-compose.yml`, and the top-level directory layout.

You are trying to answer four things, and the **fourth is the most important field in the whole
file** — it is what stops every later sweep from producing generic web-design opinions:

1. **What the app does**, in one or two sentences.
2. **Its stack**, and how it runs locally.
3. **Its tenancy model**, if it has one (what contains what, what a "user" belongs to).
4. **`app.coreValue` — the thing that must never be wrong, and why.** For a billing product that's
   the totals. For a scheduling product it might be never double-booking. For a health app it might
   be dosage. **Ask the user directly if you cannot infer it confidently** — a wrong guess here
   mis-aims every future run.

## 2. Discover the runtime

- **URLs and ports.** Read `docker-compose.yml`, `.env.example`, `next.config.*`, `vite.config.*`,
  `Procfile`, or the dev script in `package.json`. Find the **web** URL, the **API** URL, a
  **health** endpoint, and an **OpenAPI** document if one exists (try `/openapi.json`, `/docs`,
  `/swagger.json`).
- **Shell commands.** How is the stack started (`docker compose up -d`, `npm run dev`, `make dev`)?
  How are backend logs read? How are tests run? Write the real commands for **this** project.
- **Verify rather than assume:** if the app is already running, `GET` the health URL and confirm it
  answers. If it isn't running, start it with the command you found and confirm it comes up. **A
  config full of plausible-but-wrong URLs is worse than no config**, because the first sweep will
  fail confusingly.

## 3. Derive the area map

Glob the frontend pages (`app/**/page.tsx`, `pages/**/*.tsx`, `src/routes/**`, `templates/**` —
whatever this project uses) and the backend routes. Group them into **4 to 8 coherent areas**, each
of which will become one agent in a full sweep.

For each area record: a `name`, a short stable `prefix` (2 letters, used for permanent suggestion
IDs), its `pages`, its `code` locations, and an **`attackFirst`** note — the riskiest thing in that
area, where a hunting agent should spend its budget first. Base that note on what you actually see in
the code: money arithmetic, permission checks, state machines, file uploads, imports, anything
concurrent.

Note in your summary that this map is a **starting point** the user should correct — they know which
parts of their app are actually fragile, and one sentence from them beats your inference.

## 4. Work out the identity shim

Find how the app knows who the user is in development: a header the API trusts, a `localStorage` key
the frontend reads, a session cookie, a dev-login endpoint. Grep for likely names (`X-User-Id`,
`actingUser`, `impersonat`, `devLogin`, `AUTH_MODE`).

- Fill in `auth.localStorage`, `auth.cookies` and `auth.header` as templates over `{userId}` and
  `{tenantId}`.
- Fill in `auth.discover` with the real endpoints that list tenants and users.
- **Fill in `auth.privilegedPaths` and `auth.privilegedRole`** — any route that redirects an
  under-privileged user away. This matters more than it looks: a probe run as the wrong identity
  follows that redirect, measures the page it landed on, and reports the admin page **clean without
  ever rendering it**. That single false-clean is the most common way these sweeps lie.
- **If the app has no dev impersonation at all, say so and leave `auth` out.** The tools run
  anonymously and everything still works; the sweeps just can't do role coverage. Tell the user
  that's the gap, and that adding a dev-only impersonation header would unlock it.

## 5. Work out snapshot and restore

`/bug-sweep` and `/ux-review` mutate data, so they snapshot the database first and restore it after.
Find the real commands for this project's database and fill in `database.snapshot`, `database.fetch`,
`database.restore` and `database.verify`.

Two things worth getting right, learned the hard way:

- **Prefer a drop-schema-then-restore over a `--clean` restore.** A `pg_restore --clean --if-exists`
  can *silently* half-fail: a non-cascading foreign key blocks a table drop, rows then collide on
  their primary key, and `pg_restore` reports "errors ignored" and exits successfully — leaving
  records behind while telling you it worked.
- **Always give `database.verify` a real value**, so a restore can be *checked* rather than trusted.

**If you can't work out a safe restore, leave `database` out entirely** and say so. The sweeps will
then refuse their mutating passes and explain why — which is the correct outcome. A restore command
you aren't sure about is far more dangerous than no restore command.

## 6. Write the files

- **`.claude/qa.json`** — the config. Follow the schema in the plugin's
  `reference/config.md`. Only include blocks you actually determined; omit what you couldn't work out
  rather than writing a plausible guess.
- **`.claude/qa/by-design.md`** — copy `reference/by-design.template.md` and pre-fill anything you
  can already tell is deliberate. Tell the user this file is the **single highest-leverage thing they
  own**: every finding they triage as "no, that's intended" belongs here, and then it stops coming
  back run after run.
- **`.claude/qa/role-matrix.md`** — copy `reference/role-matrix.template.md` and pre-fill the
  roles you found. Skip it entirely if the app has no role model.

## 7. Verify the toolchain actually works

Don't hand back a config you never tested.

1. `pip install -r ${CLAUDE_PLUGIN_ROOT}/tools/requirements.txt`
2. Take a screenshot of the app's main page:
   `python ${CLAUDE_PLUGIN_ROOT}/tools/page_shot.py / <output>/setup-smoke.png`
3. **Read the PNG.** Does it show the real app, or a login wall, or an error page? A login wall means
   the identity shim isn't configured correctly — fix it now rather than letting the first sweep
   discover it.
4. Run `python ${CLAUDE_PLUGIN_ROOT}/tools/viewport_probe.py /` and confirm you get parseable JSON back.

If the browser isn't found, tell the user which browsers the tools look for and that `CLAUDE_QA_BROWSER`
can point at one explicitly.

## 8. Report back

Show the user:

- The config you wrote, **highlighting `app.coreValue` and the area map** as the two things most
  worth their correction.
- Anything you **could not** determine and left out, and what that costs them (for example: no
  `auth` block means no role coverage; no `database` block means the mutating sweeps won't run).
- The smoke-test result: browser found, screenshot taken, and what the screenshot showed.
- What to run next: **`/qa:bug-sweep quick`** is the cheapest way to confirm the whole chain works
  end to end before committing to a full sweep.
