# Changelog

Notable changes to the `qa` plugin. Versions follow [semver](https://semver.org/); the version in
`plugin.json` is what installers pin to, so it is bumped on every user-visible change.

## [0.4.0] — 2026-07-19

First run against a real, unfamiliar project (a single-tenant asset register on non-default
ports, DB-backed session auth). The command procedure held up; two defects it exposed did not.

### Fixed
- **`readySelector` did not do what it promised.** When set, the readiness check was
  `selector matches OR body has any content` — so the generic fallback fired regardless and the
  selector added nothing. Verified in the field: an auth guard rendering `null` mid-redirect
  produced a **blank screenshot reported as `rendered: true`, exit 0**. The selector is now
  authoritative; if it never appears the page is not ready, and the run exits 4. A genuinely
  rendered page still exits 0.
- **No tool reported the URL it actually landed on**, only the one requested — so the
  privileged-path redirect trap that `environment.md` warns about at length was undetectable
  from tool output. Reproduced: requesting `/add` as a staff user reported `url: /add`,
  `rendered: true`, and screenshotted a different page entirely. All tools now report
  `landed_url` and a `redirected` flag.

### Changed
- The annotated `jsonc` schema block now warns that comments must be stripped; the loader is a
  strict JSON parser and copying the schema verbatim fails.
- `database.verify` guidance now recommends a query over an endpoint when the API needs auth —
  an anonymous GET returning 401 is indistinguishable from a failed restore, permanently.
- Probe error messages name `urls.web`, the documented key, rather than the internal one.

### Verified in this run
The `/qa:setup` database round trip — snapshot, insert a throwaway row, `DROP SCHEMA CASCADE`,
restore, confirm the row is gone and the fingerprint matches — executed end to end against a
real project's development database, which came back byte-identical. That procedure was added
untested in 0.2.0; it now has a real run behind it.

## [0.3.0] — 2026-07-18

Second review pass, on the Python probes. Several of these were reproduced doing real damage, so
update before running `/qa:bug-sweep` against anything you care about.

### Fixed — safety
- **The confirm-dialog canceller clicked destructive buttons.** `ui_crawl` dismisses dialogs by
  clicking the first button matching `cancel|close|keep|dismiss`. A dialog whose *destructive*
  action reads "Cancel plan", "Cancel subscription" or "Close account" matches that regex, so the
  safety mechanism performed the destructive action. Reproduced against a fixture. It now presses
  Escape first, and only falls back to a click on an aria-label close, `value="cancel"`, or an
  exact-word match — every candidate gated through the danger lists.
- **The destructive skip list missed about 35 labels**, including `Send invoice` and
  `Email customer`, which mail a real person from a test run — something no database restore
  undoes. The list is broadened, and a separate irreversible-external category (send, email, SMS,
  notify, invite, webhook, charge) is skipped **even under `--all`**.
- **The stale-profile sweep deleted unrelated directories.** The glob was `qa-cdp-*`, which matched
  anything a user happened to name that way; a seeded `qa-cdp-my-important-notes` was recursively
  deleted. Now an anchored match on the shape the launcher actually writes, plus a PID-liveness
  check — a live run's profile is identified by a fact rather than guessed at from mtime.

### Fixed — correctness
- **Every `position: fixed` element was invisible to the crawler**, because all four visibility
  tests used `offsetParent !== null`, which is null by spec for fixed positioning. Fixed FABs went
  unclicked, fixed error toasts went unreported, and worst, a fixed modal was not detected as open
  *while its buttons were still enumerated* — so the crawler clicked into modals it did not know
  were there.
- **A failed measurement was reported as a clean result with exit 0.** `m = cdp.js(MEASURE_JS) or {}`
  turned a JS timeout into a defect-free viewport, making a timed-out probe and a genuinely clean
  page indistinguishable — the exact false clean this plugin exists to prevent. Failures are now
  flagged per viewport, counted, warned about on stderr, and exit **4** ("evidence not
  trustworthy"). A genuinely clean run still exits 0.
- Environment overrides permanently poisoned the module-level `DEFAULTS`, so a second
  `load_config()` in the same process returned the old override even after the variable was unset.
- A malformed config produced raw tracebacks instead of a clean exit 2 — including the quiet case
  where `"web": "a-string"` silently fell back to `localhost:3000` and drove the wrong app.
- An output filename with no extension created a *directory* and then crashed.
- A full URL passed as the page argument was mangled into `http://localhost:3000/https://...`.
- `shutdown()` ran twice on every error path, doubling the sweep's exposure window.

### Fixed — portability
- On macOS, none of the browser's helper processes were ever killed: the filter matched exact
  process names and macOS uses `Google Chrome Helper (Renderer)` and friends. Since those
  reparented helpers are precisely what leak, the cleanup was inoperative there.
- The POSIX kill was `pkill -f <leaf>` with no process-name filter, so anything whose command line
  contained the profile path died — including a wrapper shell. Now filtered, with SIGTERM then
  SIGKILL, and an explicit error when `ps` is absent.

### Known behaviour change
The broadened destructive list reduces crawl coverage on a normal app: Approve, Publish, Merge,
Restore, Clear and Archive are now skipped by default. That is the intended trade — `--all` restores
them, except for the irreversible-external category, which it deliberately does not.

## [0.2.0] — 2026-07-18

Pre-publication review pass. One defect here made most of the configuration inert, so anyone who
installed 0.1.0 should update.

### Fixed
- **The documented config did nothing.** `reference/config.md` documents `urls.web` / `paths.output`;
  the loader only ever read `web.url` / `output.dir`. A correct config parsed without error, set
  nothing, and every probe silently targeted `http://localhost:3000` writing to `./checks` — with no
  error message anywhere to explain it. Both shapes are now accepted, and `scripts/validate.py`
  loads the documented example through the real loader on every CI run so the two cannot drift again.
- **Agents were told to resolve `${CLAUDE_PLUGIN_ROOT}`, which nothing ever set.** Subagent prompts
  are not shell contexts, so it stayed a literal string and every tool call would have failed on a
  path like `/tools/ui_crawl.py`. Orchestrators now resolve the absolute path in preflight and pass
  it in every brief.
- **`/qa:setup` wrote a `DROP SCHEMA` restore command it had never executed.** It must now prove the
  whole snapshot → change → restore → verify cycle against a local database before writing the
  `database` block at all, and refuse outright for a non-local host.
- **`ux-reviewer` asserted a database snapshot existed even in `quick` mode, which never takes one.**
  The snapshot state is now an explicit flag in the brief, and the agent is read-only without it.
- `auth.header` was documented and promised in the shared environment notes but implemented by no
  tool. Now stated accurately: it applies to agents' direct API calls, not to browser navigation.
  Signed-session-cookie apps are documented as out of scope for browser role coverage.
- `layout-hunter` cited a screenshot directory the probe does not write to, and its self-check
  referred to a severity rubric the file never defined.
- `environment.md`'s "never seed" contradicted the hunters' own `QA-` throwaway-record hygiene.
- Browser profile directories were leaking. `shutdown()` finished with a single
  `shutil.rmtree(..., ignore_errors=True)`, which loses a race against the OS releasing the
  profile's file handles and then returns silently — leaving the whole profile behind, once per
  run. Now retries with backoff, and sweeps profiles orphaned by hard-killed runs after 6 hours.
  The 6 hour floor keeps concurrent probes safe.

### Added
- `readySelector` documented and determined during setup. Without it the probes fall back to "the
  body has any text", which a loading skeleton satisfies instantly — so every viewport measures the
  spinner and reports the page clean. That is the exact false clean these sweeps exist to prevent.
- A wall budget, a suggestion cap and a role-priority order for `ux-reviewer`, which previously had
  none of the three while being told to drive every control as every role.
- `/qa:ux-review full` now states the agent count and gets confirmation before fanning out.
- `scripts/validate.py` — structural self-check for agent references, tool flags, config keys and
  leftover project-specific strings. Runs in CI on every push.
- CI across Python 3.9 / 3.11 / 3.13 and Linux / macOS / Windows, including a check that an
  unreachable app fails with a clean message rather than a traceback.
- `.gitattributes` normalising line endings to LF. Without it, committing from Windows shipped
  CRLF to POSIX users, where a shebanged script fails with `bad interpreter: python3^M`.
- `CONTRIBUTING.md`.

## [0.1.0] — 2026-07-18

First public release. Generalised from a private toolkit built for a single application.

### Added
- `/qa:setup` — inspects a project, writes `.claude/qa.json`, scaffolds the by-design, role-matrix
  and scenario files, and smoke-tests the browser tooling.
- `/qa:bug-sweep` — functional bug hunt across the app's areas, with an adversarial verification
  pass over every Critical and High finding.
- `/qa:layout-sweep` — measured responsive defects across a 13-viewport device × display-scaling
  matrix. Read-only.
- `/qa:ux-review` — exhaustive per-page review driving every control as every role, with a
  skeptic pass over the High-impact suggestions.
- `/qa:drive-app` — persona-driven scenario runs that use the app as a real team would across a
  run of periods, catching workflow failures the surface-level sweeps cannot.
- Six agents: `bug-hunter`, `bug-verifier`, `layout-hunter`, `ux-reviewer`, `ux-verifier`,
  `app-driver`.
- Cross-platform CDP browser tools: `ui_crawl.py`, `viewport_probe.py`, `page_shot.py`,
  `responsive_audit.py`, discovering Chrome, Edge, Chromium or Brave.

### Fixed during generalisation
- `viewport_probe` measured overflow against `window.innerWidth`, which under mobile emulation
  reports the *visual* viewport after Chrome's shrink-to-fit — an emulated 360px phone measured as
  1440px. On a page with 3000px of overflow, both tablet rows reported completely clean. Now
  measures `documentElement.clientWidth`, the true layout viewport.
- Git Bash path recovery mangled a bare `/` into the Git install root, so probing the app root
  navigated to the wrong URL entirely.
