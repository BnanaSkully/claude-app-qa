---
description: Spawn bug-hunter agents to drive the running app, stress-test each area, adversarially verify the serious findings, and write a dated bug report to triage. Snapshots the DB first and restores it after so no test residue is left behind.
argument-hint: "[full | quick | <area name> ...]"
---

# /bug-sweep — autonomous bug sweep

Scope requested: **$ARGUMENTS** (empty = `full`).

You are orchestrating a bug hunt of the running application. Work the steps in order. **Do not fix
anything you find** — this command finds and reports; the user triages and decides what to tackle.

## 1. Preflight

Follow the shared preflight spine — `<plugin>/reference/preflight.md` (read config →
app up → discover ids → read prior report → read the by-design list → read the solutions store →
one-line status). Skipping it risks a run against a dead app, hardcoded stale ids, or a false-clean
privileged page. This command's own slots:

- **Data:** hunt against whatever data already exists. If the app is empty, the hunt is mostly
  worthless — say so and suggest the user populate it first. **Never seed.**
- **Snapshot the database — always, including `quick`.** Even a crawl-only pass mutates data by
  design: a one-click approve rarely has a confirm dialog. Hunters also create `QA-` records. An
  orchestrator-owned snapshot and restore is what guarantees the data is left exactly as found.
  Use `database.snapshot` and `database.fetch` from the config, writing to
  `<output>/snapshots/pre-bughunt-<timestamp>.dump`.

  **Confirm the dump exists and is a plausible size.** Record the **literal path** — you restore
  from it in step 4, and shell variables do not survive between tool calls.

  **If the config has no `database` block, do not run the mutating passes.** Tell the user their
  options: add snapshot/restore to the config, or accept a read-only hunt (API GETs, screenshots, no
  crawl). Never mutate data you cannot roll back.

  Tell the user you've snapshotted and will restore, so they shouldn't make real changes mid-run.

- **Prior reports:** the newest `<output>/bug-reports/bug-report-*.md`. Carry its **"Ruled out / by
  design"** entries and its still-open findings per area, so collation can tag STILL-OPEN versus
  FIXED from real verdicts rather than guesswork. Also read the newest
  `<output>/ux-reviews/ux-review-*.md` **"Possible bugs"** section — each unconfirmed item
  becomes a hypothesis for the matching area's agent.
- **Solutions store:** if `paths.solutions` is configured, glob it and hand each agent the entries
  touching its area as known/ruled-out input, so a fixed bug isn't re-hunted. **A documented fix that
  reproduces again is a REGRESSION** — a real finding, tagged as such.

## 2. Pick the areas to hunt

**First, sanity-check the map.** Glob the configured `paths.frontendPages` and `paths.backendRoutes`.
Any route or router **not** in the config's `areas` gets assigned to the nearest area, and you say so
in the report header. The config is the default map, not the source of truth — apps grow between
runs, and an unmapped new route is exactly where fresh bugs live.

If the config has no `areas` block, derive 4 to 8 areas by grouping the globbed routes, and suggest
the user run `/qa:setup` to make the map permanent.

**Cross-cutting rule** — goes to every agent: if a failing request originates in a shared component
outside your area (a shared widget, the sidebar, the top bar), record it in **one line** with
`cross_area` set and move on. Do not investigate; the orchestrator routes it to the owning area.

Resolve `$ARGUMENTS`:

- **`full`** or empty → every area.
- **`quick`** → **one** bug-hunter that crawls every page as an ordinary user (using a privileged
  identity for any `auth.privilegedPaths`), reading only `on_load_problems`, `problems` and
  `requests_ge_400`; one health check; and a scan of the configured `commands.logs` output for
  tracebacks. Budget about 15 minutes. It still writes the dated report (suffix `-quick`) with the
  same triage table; per-finding screenshots may be omitted.
- **One or more named areas** → just those.

## 3. Fan out (parallel)

Spawn one **bug-hunter** subagent per selected area — Agent tool, `subagent_type: bug-hunter`, **all
calls in a single message** so they run concurrently. Give each agent:

0. **The absolute path to the plugin's `tools/` directory** (preflight step 0), and the **API base
   URL** from `urls.api`. Without the first, every tool call fails; without the second, an agent
   probing endpoints is guessing hostnames.
1. Its area name, pages, routes and `attackFirst` notes (including anything you re-assigned in
   step 2), plus the discovered ids it needs.
2. Its area's **"Ruled out / by design" entries from the last report**, verbatim — "treat these as an
   extension of your by-design list".
3. Its area's **still-open findings from the last report** — "re-run each finding's repro; verdict
   each `still-broken` or `fixed` in `prior_findings`".
4. Any **ux-review possible-bug hypotheses** for its area — "confirm or refute each, and say
   which".
5. The brief: *"Exercise and stress-test ONLY this area against the running app: API stress on its
   endpoints, a role-matrix pass on its sensitive reads, a UI click-through of each of its pages with
   `ui_crawl.py`, and a backend-log scan. Return findings in the JSON envelope your system prompt
   defines. Save screenshots under the configured output directory."*

   The agent already knows the URLs, the identity shim, the crawler, the methodology, severity, roles
   and test-data hygiene — **don't re-explain those.**

**If an agent errors or returns nothing usable, re-spawn it once** with a narrower brief — its single
riskiest page. If it fails again, record that area as **NOT COVERED** in the report header. **Never
fabricate coverage.**

## 4. Verify adversarially — refute before you report

An agent that just spent its whole budget trying to CONFIRM bugs is badly placed to catch its own
false positives: a by-design behaviour, test-data residue, or a misread number dressed up as a bug.
Before collating, put the serious findings through an **independent adversarial pass** — a fresh
agent that never saw the hunt's reasoning, whose only job is to refute.

**This is the single biggest quality lever in the whole command.** A false positive wastes the user's
hand-triage time and erodes their trust in the tool, which is how a QA tool ends up unused.

Skip this step entirely for `quick` (no verification budget) and when the hunt returned zero Critical
or High findings.

1. **Select what to verify:** every **Critical** and **High** finding, across all areas. Medium and
   Low are not worth the verification budget — carry them through as reported, marked `unverified`.
2. **Fan out verifiers (parallel):** spawn **`bug-verifier`** agents, all in one message — one per
   area that has Critical/High findings, or batch several findings into one agent if an area has
   many. Hand each verifier:
   - The full finding objects it must check: title, severity, steps, expected/actual, evidence,
     suspected cause.
   - Its area's by-design entries, solutions write-ups and prior ruled-out list — the same material
     the hunter got, so it can refute on those grounds.
   - The brief: *"Try to REFUTE each finding — reproduce it yourself from the current state, rule out
     by-design, residue and arithmetic error, trace the mechanism to a `file:line` (a repro without a
     traced mechanism is `uncertain`, not `confirmed`), and return a verdict with YOUR own fresh
     evidence. Default to skepticism."*
3. **Apply the verdicts:**
   - **confirmed** → keep in Findings, mark `Verified? = confirmed`, use the verifier's corrected
     severity if it changed one, and prefer the verifier's fresh evidence.
   - **refuted** → **remove from Findings** and list under **"Withdrawn on verification"** with a
     one-line reason. **Never silently drop one** — the user should see what was cleared and why,
     both to trust the process and to catch a wrong refutation.
   - **uncertain** → keep, marked low-confidence, with the verifier's "what would settle it" note.
   - **not_verified** → carry through marked `unverified` with the reason.
   - Fold every verifier's `incidental` items into the next hunt's hypothesis pool and note them
     under Coverage.
4. **If a verifier can't run** (app down mid-run, tool failure): don't block. Carry its findings
   through marked `unverified` and say so in the report header. **An unverified real bug beats a
   dropped one.**

## 5. Restore the database (mandatory — always, including `quick`)

Once every hunter and verifier has returned **and you have captured their JSON**, restore the
snapshot so no `QA-` residue or throwaway state is left behind.

Use the **literal snapshot path** you recorded in step 1 — shell variables do not persist between
tool calls — and run the config's `database.restore`.

**Then always verify the restore** using `database.verify`, and compare against
`database.fingerprint` if one is set. **Never trust a restore silently.** Restores fail quietly more
often than you'd expect: a `--clean` restore can report "errors ignored" and exit successfully while
leaving records behind.

**Beware a stale snapshot if a schema migration landed after you snapshotted** — a drop-schema
restore replaces the schema with the dump's, silently reverting a since-added column, after which the
app fails with "column does not exist". If that happens, re-apply the pending migrations directly.
The best prevention is a **fresh snapshot at the start of this run**, never one left from an earlier
session.

**If the restore fails, tell the user clearly** and hand them the literal snapshot path so they can
restore manually. **Never leave them believing the database is clean when it isn't.**

## 6. Collate into one report

Write `<output>/bug-reports/bug-report-<date>[-<scope>].md`, creating the folder if missing.
Structure:

- **Title** plus a line on stack state, areas covered, and any NOT COVERED or re-assigned routes.
- **Triage summary** table: `# · Severity · Area · Title · Verified? · Status`, sorted Critical to
  Low. `Verified?` comes from step 4 (Medium/Low read `unverified`; a `quick` run reads `unverified`
  throughout). `Status` is **NEW / STILL-OPEN / FIXED / REGRESSION**, from the agents'
  `prior_findings` verdicts — **a prior finding nobody re-ran is NOT RE-TESTED, never assumed fixed.**
- **Findings**: one section per bug — endpoint or page, numbered steps, expected versus actual,
  evidence, suspected cause (`file:line`), confidence.
- **Withdrawn on verification**: every refuted finding, one line each with the reason.
- **Possible-bug hypotheses**: each confirmed (→ full finding) or refuted (one line why).
- **Ruled out / by design**: every agent's `ruled_out` list, verbatim, grouped by area. **Suggest the
  user promote the durable ones into `.claude/qa/by-design.md`** — that's how the false-positive
  rate falls over time.
- **Coverage**: per area, what was exercised and every `not_tested` entry with its reason. Not
  covered is **reported**, never silently dropped.
- **Test-data residue**: aggregate of the agents' `residue` lists.

**De-dupe** on shared root cause — `suspected_cause`, or endpoint plus symptom when that's absent.
Keep the highest severity and the shortest reproducible repro, and cite every area that hit it.

## 7. Report back

Print a tight summary: **confirm the database was snapshotted and restored** (or flag the failure and
give the snapshot path), counts by severity, **how many Critical/High were confirmed versus withdrawn
on verification**, NEW versus STILL-OPEN and any REGRESSION, the top 3 confirmed findings one line
each, and the report path. Recommend where to start — but **do not start fixing.**

> Pair with `/qa:layout-sweep` (measured responsive defects) and `/qa:ux-review` (design and
> UX suggestions). To run on a schedule: `/loop 1d /qa:bug-sweep`.
