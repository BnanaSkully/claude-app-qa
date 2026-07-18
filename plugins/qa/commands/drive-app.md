---
description: Live a scripted scenario inside the running app as its real user personas — do the work the app exists for, period after period, read the numbers, then verify the live app against expected outcomes and write a dated report. Snapshots the DB first and restores it after.
argument-hint: "[scenario name] [--slices N]"
---

# /drive-app — a scenario lived through the app

Args: **$ARGUMENTS** (a scenario name, and optionally `--slices N`; both optional).

You are orchestrating a **scenario run**. Several `app-driver` agents live through a scripted
scenario **as the app's real user personas**, doing the work the product exists to do, and journal
every action with expected-versus-observed. You then verify the live app against the scenario's
expected outcomes, restore the database, and collate one dated report.

**Do not fix anything you find** — this command finds and reports; the user triages.

**What this catches that the other sweeps cannot:** `/bug-sweep` tests surfaces and `/ux-review`
tests pages, both a page at a time. This one tests **whether the app holds together over time** — 
whether period two contradicts period one, whether a number drifts across a cycle, whether a routine
task that is fine once is exhausting on the fifteenth repetition, and whether a handoff between two
people actually works. Those failures are invisible to per-page testing and are often the ones that
actually lose a user.

## 1. Preflight

Follow the shared preflight spine — `${CLAUDE_PLUGIN_ROOT}/reference/preflight.md`. Then this
command's own checks:

- **Find the scenario.** Look in `.claude/qa/scenarios/`. If `$ARGUMENTS` names one, use it;
  if there's exactly one, use it; if there are several, list them and ask which.

  **If there are none, STOP and help the user write one.** Read
  `${CLAUDE_PLUGIN_ROOT}/reference/scenario.template.md`, then work out from the codebase what the
  app's core recurring cycle actually is — the thing a user does over and over, that the product
  exists to serve — and draft a scenario for them to correct. **Do not invent a scenario and run it
  silently**: a scenario built on a wrong guess about what the app is for produces a report full of
  confident nonsense, and the user is the only one who knows the real workflow.

- **Check the scenario's prerequisites.** If it names a data-injection command, a seeded starting
  state, or a fixture generator, confirm each exists **before** starting. **If a prerequisite is
  missing, STOP and say what needs building.** Do not attempt to work around it — a run on
  improvised inputs proves nothing and the findings can't be reproduced.

- **Confirm the personas exist.** Discover real user ids for every persona the scenario names. If one
  is missing, either the run proceeds without that persona (say so, and record every step it owned as
  not-tested) or you stop — the scenario's own notes should say which.

## 2. Snapshot the database (always)

The agents write a whole scenario's worth of real records into the app. An orchestrator-owned
snapshot and restore is what guarantees existing data is left exactly as found.

Use the config's `database.snapshot` and `database.fetch`, writing to
`<output>/snapshots/pre-drive-<timestamp>.dump`. Confirm it exists and is a plausible size, and
**record the baseline fingerprint now** using `database.verify` — you compare against it after the
restore. **Keep the literal path**; shell variables do not survive between tool calls.

**If the config has no `database` block, STOP.** Unlike the other sweeps, this one has no read-only
mode worth running: the whole point is accumulating state across periods. Tell the user that
snapshot and restore must be configured first.

Tell the user you've snapshotted and will restore, so they shouldn't make real changes mid-run.

## 3. Fan out — SEQUENTIAL agents, never parallel

Create the run folder: `<output>/drive-app/run-<date>/`.

Spawn `app-driver` agents (`subagent_type: app-driver`) **one at a time, sequentially.** This is the
one command in the plugin that must not parallelise, and the reason is structural: **the scenario is
state-dependent.** Period 4 builds on period 3's saved work. Two agents running concurrently would
interleave writes, corrupt the running totals, and produce a journal that describes a state that
never actually existed.

Split the scenario into **3 slices** by default (or `--slices N`), each a contiguous run of periods,
with the closing steps in the last slice.

Give **every** slice: the scenario file, its **period range**, and the run folder. Give **every slice
after the first** the previous slice's `state-summary.json` **and the last ~40 lines of
`journal.md`**, so it picks up the ids and running totals exactly where the last one stopped.

**Wait for each slice to finish before spawning the next.** Read its returned summary and **confirm
`last_completed_period` actually advanced** before continuing — a slice that returned cleanly but
didn't advance means the next one would redo or skip work.

If a slice errors or returns nothing usable, re-spawn it once with the same inputs. If it fails
again, record the run as **PARTIAL** from that period onward and continue to verification with what
completed.

## 4. Verification pass — BEFORE the restore

While the live app still holds the scenario's state, compare **the live app against the scenario's
expected outcomes** yourself. Check every figure the scenario states an expectation for, plus any
headline number a user would actually look at.

**Every mismatch becomes a finding quoting BOTH numbers** — the app's value and the expected value —
with the period and persona context, and the input that produced it.

**This pass is the highest-value step in the command.** The agents were busy *being users*, and a
user reads past a wrong number far more easily than a tester does. This is the pass that catches
what they read past — and it **must** run before the restore wipes the state.

## 5. Restore the database (mandatory)

Restore the pre-run snapshot using the config's `database.restore`, from the **literal path** you
recorded in step 2.

**Then always verify the restore** with `database.verify` against the fingerprint from step 2 — the
scenario's records should be gone and the baseline back. **Never trust a restore silently:** a
`--clean` style restore can report "errors ignored", exit successfully, and still leave records
behind.

If a schema migration landed after you snapshotted, a drop-schema restore will revert it and the app
will then fail with "column does not exist" — re-apply the pending migrations directly if so.

**If the restore fails, tell the user clearly** and hand over the literal snapshot path. **Never
leave them believing the database is clean when it isn't** — this command writes more state than any
other, so a silent failed restore here is the most damaging.

## 6. Collate into one report

Write `<output>/drive-app/drive-app-<date>.md`. Structure:

- **Run parameters**, stated prominently: the scenario, any seed or variant, the periods completed,
  any PARTIAL range, and the personas driven. **Lead with whatever makes the run reproducible** — a
  finding nobody can reproduce is a finding nobody will fix.
- **Narrative** — a few lines on what the simulated team actually did across the run, drawn from the
  journal. This is worth writing properly: it's what makes the report readable by someone who wasn't
  watching, and it gives every finding its context.
- **Findings table**, one row per candidate bug or verification mismatch:
  `# | severity | area | period/persona | summary | exact repro | expected vs observed | screenshot | journal ref`
  where **exact repro** must be replayable **from scratch after the restore**: the input file or
  data, the command or UI sequence, and the persona. Expected-versus-observed quotes both numbers.
  Sort Critical to Low.
- **Friction / UX appendix** — the non-bug journal notes: confusing copy, extra steps, and every UI
  flake that forced an API fallback. **Do not discard these.** They are the richest output of the
  whole run, they don't come from any other command, and a pile of small frictions in one workflow
  usually points at one badly-designed step.
- **Passing checks** — every planted complication the app handled correctly. Recording what *worked*
  is what makes a later regression visible.
- **Not-tested list** — any period, step or persona not exercised, and why. Silent omission is how
  this job fails.
- **Handoff note** — confirm every finding is reproducible from scratch post-restore, so a
  `bug-verifier` can re-check it in a later `/bug-sweep`.

## 7. Report back

Tight summary: **confirm the database was snapshotted and restored** (or flag the failure and give
the path), what makes the run reproducible, how many periods completed and any PARTIAL, candidate-bug
counts by severity, verification mismatches, the top 3 findings one line each, and the report path.

**Call out the friction themes separately** from the bugs — they're the part of this report that
tends to change what someone builds next, and they get lost if buried under a severity table.

Recommend where to look first, but **do not start fixing.**

> Continuous: `/loop 1w /qa:drive-app`. Pair with `/qa:bug-sweep` (surfaces), `/qa:layout-sweep`
> (layout) and `/qa:ux-review` (pages). This one is the only one that tests the app as a *journey*.
