# QA Sweeps — a Claude Code plugin

Three multi-agent QA sweeps for a **running web app**, plus the browser tooling to drive it.

| Command | Finds | Mutates data? |
|---|---|---|
| `/qa:bug-sweep` | Functional bugs — wrong numbers, broken flows, permission and tenancy leaks, 500s, races | Yes (snapshots + restores) |
| `/qa:layout-sweep` | Measured responsive defects across 13 viewports — overflow, clipping, off-screen controls, tiny tap targets | No (read-only) |
| `/qa:ux-review` | Design, UX, copy, a11y, performance, quality-of-life, guardrail and role-gap **suggestions** | Yes (snapshots + restores) |
| `/qa:setup` | Configures the plugin for your project | Writes config only |

None of them fix anything. They find, verify, and write a dated report you triage.

## Install

```
/plugin marketplace add BnanaSkully/claude-app-qa
/plugin install qa@qa-marketplace
```

Then, in the project you want to test:

```
/qa:setup
```

That inspects the codebase, writes `.claude/qa.json`, scaffolds two companion files, and smoke-tests
the browser tooling. It takes a few minutes and everything afterwards depends on it.

The probes need Python and one package:

```
pip install -r <plugin>/tools/requirements.txt
```

## What makes this different from "ask Claude to look for bugs"

**Every sweep runs an adversarial verification pass.**

An agent that has just spent its whole budget trying to *confirm* bugs is the worst-placed agent
alive to catch its own false positives. So the serious findings get handed to a **fresh agent that
never saw the first one's reasoning**, whose only job is to **refute** them. It must independently
reproduce the bug, rule out by-design behaviour and test-data residue, redo the arithmetic itself,
and trace a gapless chain from trigger to symptom down to a `file:line`. If it can reproduce the
symptom but can't explain the mechanism, the finding is downgraded to *uncertain* — not confirmed.

Anything refuted lands in a visible **"Withdrawn on verification"** section rather than being
silently dropped, so you can see what was cleared and why.

That independence is the whole design. A QA tool that cries wolf gets ignored within two runs, and
then it doesn't matter how many real bugs it could have found.

**Everything else follows from the same principle:**

- **Coverage is a deliverable.** Agents enumerate every interactive element from the *code* first,
  then tick each one off. Anything unreachable is reported as `not_tested` with a reason. Silent
  omission is treated as a failure, because "found nothing" and "didn't look" must never look alike.
- **Findings must be grounded in observed behaviour** — a response body, a log line, a measurement, a
  screenshot the agent actually opened. Code-reading alone produces a suspicion, never a finding.
- **The database is snapshotted and restored** around the mutating sweeps, and the restore is
  *verified* rather than trusted. (A `pg_restore --clean` can report "errors ignored", exit
  successfully, and still leave records behind. Ask me how I know.)
- **Layout findings are measured, not eyeballed.** The probe injects JS at each viewport and returns
  hard numbers, and it already excludes intended scroll regions and parked off-canvas drawers, so it
  doesn't cry wolf about deliberate design.

## The one file that makes this work: `by-design.md`

`/qa:setup` scaffolds `.claude/qa/by-design.md`. It is the catalogue of **deliberate behaviours that
must never be reported as bugs or suggested away** — read by both the bug sweep and the UX review, so
they cannot drift apart and start disagreeing about what counts as a defect.

**Grow it every run.** Every finding you triage as "no, that's intended" belongs in it. Ten minutes
spent there after a sweep is what stops the same false positive arriving every single time, and it
is the difference between a tool that gets sharper each run and one you quietly stop running.

## Configuration

`.claude/qa.json` — full schema and a minimum-viable example in
[`plugins/qa/reference/config.md`](plugins/qa/reference/config.md).

The single highest-value field is **`app.coreValue`**: one sentence naming the thing that must never
be wrong in your product, and why. For a billing app it's the totals; for scheduling it might be
never double-booking. Every agent weights its findings against it. Get this right and the reports are
about *your* product; leave it vague and you get generic web-design opinions.

Environment variables `CLAUDE_QA_WEB_URL`, `CLAUDE_QA_API_URL`, `CLAUDE_QA_OUTPUT_DIR` and
`CLAUDE_QA_BROWSER` override the config for one-off runs.

## The browser tools

Standalone CDP-driven Python scripts in [`plugins/qa/tools/`](plugins/qa/tools/) — useful on their
own, not just via the agents. Cross-platform, auto-discovering Chrome, Edge, Chromium or Brave, and
each picks a free debug port so they are safe to run in parallel.

| Tool | Does |
|---|---|
| `ui_crawl.py` | Clicks every visible control on a page, safely cancels confirm dialogs, reports console errors, exceptions, 400+ responses |
| `viewport_probe.py` | 13-viewport device x display-scaling matrix, measures overflow / clipping / tap targets, screenshots only the broken ones |
| `page_shot.py` | Screenshot a page, optionally as a given user identity |
| `responsive_audit.py` | Capture several paths at several widths in one browser session |

## Cost

`/qa:bug-sweep quick` and `/qa:ux-review quick` are cheap (~15 min, one agent). The `full` modes fan
out roughly 6 to 12 agents each, all driving headless browsers, and are genuinely token-hungry —
start with `quick` to confirm the chain works before committing to a full sweep.

## Scope and limits

- Built for a **web app with a local dev environment**. It drives a real running app; there is no
  static-analysis mode.
- **Per-role coverage needs some form of dev impersonation** — a header the API trusts, or a
  localStorage key. Without one, everything still works but role and permission coverage is skipped.
  `/qa:setup` will tell you if it can't find one.
- **The mutating sweeps need a working snapshot and restore.** If `/qa:setup` can't determine a safe
  one, it leaves that config out and the sweeps refuse their mutating passes, which is the correct
  outcome.
- **Never point this at production.** It creates records, clicks destructive-looking controls, and
  restores databases.

## Licence

MIT.
