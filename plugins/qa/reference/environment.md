# Run environment & hard guardrails (shared)

**Single source of truth** for the environment every agent in this plugin shares (`bug-hunter`,
`bug-verifier`, `layout-hunter`, `ux-reviewer`, `ux-verifier`). Read it at the start of a run.

**Failure mode of skipping it:** hardcoding stale ids, probing a privileged page as an ordinary user
(silently measuring the redirect target and reporting the page "clean" without ever rendering it), or
leaving orphaned browser processes behind.

## Stack & project

- Read **`.claude/qa.json`** at the project root for URLs, shell commands, area map, roles and
  the by-design list. The full schema is in [`config.md`](config.md). If there is no config, fall
  back to `http://localhost:3000` for the web app and `./checks` for output, and say in your coverage
  notes that you ran unconfigured.
- **Check the app is up before anything else** — `GET` the configured `urls.health` (or the web root
  if none is set). If it's down, run `commands.start` and re-check for up to ~90 seconds. **If it
  will not come up, STOP and say so.** These tools need the running app; reading the code is never a
  substitute for observing behaviour.
- **Shell:** you may be on PowerShell, where `&&` does not chain commands — use `;` or separate
  lines. A Bash tool is generally also available for `curl` and heredocs. Check before assuming.

## Discovering ids — never hardcode

- If `urls.openapi` is configured, fetch it: it enumerates every real endpoint. Pull your area's
  endpoints from it rather than guessing paths.
- Use `auth.discover.tenants` and `auth.discover.users` from the config to look up real ids **every
  run**. Ids from a previous run, a prior report, or an example in a document are stale by
  definition — a run built on them tests nothing.

## The identity shim and the privileged-page trap

- The probe tools take `--as-user` and `--as-tenant` and apply whatever `auth.localStorage`,
  `auth.cookies` and `auth.header` the config declares, before first paint. With no `auth` block
  they run anonymously, which is fine for a public app.
- **For any path listed in `auth.privilegedPaths`, act as a user holding `auth.privilegedRole`.**
  Apps routinely redirect an under-privileged identity away from an admin route to the dashboard.
  A probe that follows that redirect measures the *dashboard*, sees nothing wrong, and records the
  admin page as clean — a false clean, which is worse than no coverage at all.
  - Always confirm the landed URL is the URL you asked for. If it isn't, that's either a finding
    (wrong redirect) or a signal you're using the wrong identity.
  - If no user with the privileged role exists, record those paths under `not_tested` /
    `not_probed` with the reason. **Never report a page clean you never rendered.**
  - Confirming the page is correctly *invisible and refused* to under-privileged roles is still
    required — that's a separate, real test.

## The probe tools

CDP-driven scripts live in the plugin's `tools/` directory. Resolve their absolute path from the
plugin root you were given; do not assume a location.

| Tool | What it does |
|---|---|
| `ui_crawl.py <path>` | Visits a page, clicks every visible control, cancels confirm dialogs, returns JSON of faults |
| `viewport_probe.py <path>` | Drives a 13-viewport device x display-scaling matrix, measures layout defects |
| `page_shot.py <path> <out.png>` | Screenshot, optionally as a given identity |
| `responsive_audit.py --widths ... <paths>` | Multi-width capture across several paths in one browser session |

All of them pick a **free debug port** per run and are safe to run in parallel across agents.

First run in a project needs `pip install -r <tools>/requirements.txt`.

**The CDP path can be flaky** — a stuck headless tab, a slow page. If a tool errors or returns
something truncated, re-run it **once**. If it still won't cooperate, record the page under
`not_tested` / `not_probed` and fall back to the API plus a screenshot. Do not get stuck, and do not
fabricate a result.

## Hard guardrails — all agents

- **Read-only on code, config and git.** You have no Edit or Write tool. Never modify product code,
  migrations, or configuration. If you need a repro script, write it to a temp file via a Bash
  heredoc and run it from there.
- **Never** `git commit` or `git push`. **Never** run a command that destroys the development
  database (`docker compose down -v` and equivalents). **Never** run a production build inside a
  running dev container — it corrupts the dev server's build cache. No destructive raw SQL.
- **Never seed.** Use the data that is already there. If your page needs data that doesn't exist,
  record the gap rather than manufacturing it.
- **Ground every finding in observed behaviour** — a response body, a log line, a crawl result, a
  screenshot, or a measurement. Code-reading alone identifies a *suspicion*, never a finding.
- **Clean up after yourself.** Name anything you create with a `QA-` prefix so a later run can
  recognise residue, and list anything you couldn't remove.
