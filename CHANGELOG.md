# Changelog

Notable changes to the `qa` plugin. Versions follow [semver](https://semver.org/); the version in
`plugin.json` is what installers pin to, so it is bumped on every user-visible change.

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
