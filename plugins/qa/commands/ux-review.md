---
description: EXHAUSTIVE every-click / every-adjust / every-role sweep of the running app — screenshots the UI, drives every control as every role, reads front and back code, writes a dated improvement-suggestions report. Snapshots the DB first and restores it after. Suggests, never fixes; not a bug hunt.
argument-hint: "[full | quick | <area name> ...]"
---

# /ux-review — exhaustive visual, interaction and code improvement sweep

Scope requested: **$ARGUMENTS** (empty = `full`).

`full` is an **exhaustive** pass: every page is driven through **every click, every adjust, every
state, as every role that can reach it**, plus a code read. This finds IMPROVEMENTS and writes a
report. It does **not** fix anything, and it is **not** a bug hunt — use `/bug-sweep` for
reproducible bugs. The user triages.

Because an exhaustive pass **mutates** the database, this command **snapshots before it starts and
restores after** — so existing data is left exactly as it was.

## 1. Preflight

Follow the shared preflight spine — `${CLAUDE_PLUGIN_ROOT}/reference/preflight.md`. This command's
own slots:

- **Data — use only what already exists. Never seed or create.** Discover what's there via the
  configured endpoints. If the app is nearly empty, most pages will show first-run states and the
  review will be thin — say so up front rather than producing a report full of empty-state
  observations dressed up as findings.
- **Snapshot the database** (skip only for `quick`, which is screenshot-only and never mutates).
  Use `database.snapshot` and `database.fetch`, writing to
  `<output>/snapshots/pre-review-<timestamp>.dump`. Confirm it exists and is a plausible size, and
  **record the literal path** — shell variables do not survive between tool calls.

  **If the config has no `database` block, do not run the exhaustive pass.** Offer the `quick`
  screenshot-only mode instead, and explain that exhaustive needs a restore path.

  Tell the user: *"I've snapshotted the database and will restore it when I'm done — please don't
  make real changes in the app during the run, or they'll be rolled back too."*

- **Roles:** discover a real user id per role from the configured users endpoint. Per-role coverage
  is the single thing this review does that no other tool does, so get the ids right rather than
  reviewing everything as an admin.
- **Prior report:** the newest `<output>/ux-reviews/ux-review-*.md`. Extract, per area, the previous
  suggestions with their IDs and the **"Looked good"** entries. Each agent gets its area's lists
  with: *"do not re-derive these — if a prior suggestion is still unaddressed and still worth it, tag
  it STILL-OPEN with its prior ID instead of re-writing it."*
- **Solutions store:** if configured, hand each agent the entries touching its area, so a suggestion
  for something already solved isn't re-raised. A documented fix that has visibly regressed is a
  `possible_bug` for `/bug-sweep`, not a suggestion.

## 2. Areas and page map

**Sanity-check the map:** glob the configured `paths.frontendPages` and `paths.backendRoutes`. Any
route not in the config's `areas` gets assigned to the nearest area, noted in the report header.
Pure redirects don't need their own review.

## 3. Resolve `$ARGUMENTS`

- **`full` → exhaustive.** Fan out **one agent per page-cluster**, which is finer-grained than one
  per area, so each agent has the budget to actually exhaust every control against every role. Build
  the cluster list from the config's areas: split any area with more than about three substantial
  pages into two clusters. Aim for roughly 6 to 12 clusters.
- **`quick` → one** agent, screenshot-only, no crawl and no adjust, and therefore **no snapshot or
  restore**: one shot per page as an ordinary user (privileged identity for privileged paths), at
  most 3 issues per page, about 15 to 20 minutes, report suffix `-quick`.
- **Named areas** → just those areas' clusters, exhaustive, with snapshot and restore.

## 4. Central responsive sweep (before fan-out)

Run the width pass **once, centrally**, before spawning agents:

```
python ${CLAUDE_PLUGIN_ROOT}/tools/responsive_audit.py --widths 800,1280 <every path in scope>
```

It writes `<output>/audit/<slug>_<width>.png`. In each agent's brief, point it at its paths' shots
and tell it to **actually study the 800px ones** — narrow-width overflow is consistently the most
under-reported defect class, and past runs have found it at 1280 too.

## 5. Fan out (parallel)

Spawn the selected `ux-reviewer` agents (`subagent_type: ux-reviewer`), **all in one message**. If
the machine struggles under that many concurrent headless browsers, split into **two waves**. Give
each agent:

1. Its cluster's pages and front/back code locations, and the ids to use.
2. The **roles that can reach its pages** — brief it to *drive every applicable role*.
3. Its area's **prior suggestions and "Looked good" entries**, with the STILL-OPEN instruction.
4. The path to its pre-made width shots.
5. The brief: *"Review ONLY this cluster, EXHAUSTIVELY: enumerate every interactive element from the
   code, then click everything (`ui_crawl` per role), adjust every input (valid, invalid, boundary,
   destructive), and screenshot every state (default, each sub-view, modal, empty, loading, error,
   success) as every role that can reach it — including restricted and tier-locked variants. Read the
   front and back code and verify guardrails at the source. Return the JSON envelope your system
   prompt defines, with a provable `coverage` block."*

The agent already knows the tools, the lens, the dimensions, and the output format — **don't
re-explain them.**

**If an agent errors or returns nothing usable, re-spawn it once** with a narrower brief. A second
failure means that cluster is **NOT COVERED** in the report header. **Never fabricate coverage.**

## 6. Restore the database (mandatory, after all step-5 agents finish)

Once every agent has returned **and you have captured their JSON**, restore the snapshot. Use the
**literal path** from step 1 and the config's `database.restore`.

**Then always verify the restore** with `database.verify`, comparing against
`database.fingerprint` if set. **Never trust a restore silently** — a `--clean` style restore can
report "errors ignored", exit successfully, and still leave records behind.

**Beware a stale snapshot if a migration landed after you snapshotted** — a drop-schema restore
reverts the schema to the dump's, after which the app fails with "column does not exist". Re-apply
pending migrations directly if so. Prevention: snapshot at the very start of *this* run.

**If the restore fails, say so clearly** and hand over the literal snapshot path. **Never leave the
user believing the database is clean when it isn't.** (Skip this step for `quick`, which never
snapshotted.)

## 7. Verify the high-impact suggestions (skeptic pass — skip for `quick`)

The reviewers just spent their budget *generating* suggestions, so they are poorly placed to judge
which are truly worth acting on. Before collating, put **only the High-impact suggestions** through
an independent skeptic that never saw their reasoning.

Keep it cheap — you verify a handful of High items, not the whole list. This runs **after** the
restore, against clean data, so "is this already implemented?" is judged against real state.

1. **Gather** every `impact: High` suggestion across all agents, deduping obvious repeats first.
2. **Fan out `ux-verifier`** — one agent, or a few if there are many. Hand it the High suggestions
   plus each area's prior "Looked good" and STILL-OPEN lists, so it can spot already-raised or
   already-done items. Brief: *"Try to knock each High-impact suggestion down — already implemented,
   by-design, misgrounded, duplicate, or not actually worth the time. Return a verdict with your own
   `file:line` or screenshot evidence. Default to skepticism. You run AFTER the database restore, so
   **never mutate app state** — GET-only calls and render-only screenshots, no crawl clicks and no
   writes. If a suggestion can only be verified by a write, return `uncertain` and say why."*
3. **Apply verdicts:** `confirmed` → keep, using `corrected_impact` if it lowered one; `refuted` →
   move to **"Withdrawn on verification"** with a one-line reason, **never silently dropped**;
   `uncertain` → keep, flagged low-confidence. Fold duplicates together.
4. **If the verifier can't run,** carry the High suggestions through unverified and say so in the
   header.

## 8. Collate into one report

Write `<output>/ux-reviews/ux-review-<date>[-<scope>].md`, creating the folder if missing.

**Suggestion IDs are permanent:** an area prefix plus a number, using the `prefix` from the config's
areas. Numbering continues from the **highest ID ever issued per area across all prior reports in the
folder** — addressed items drop out of reports, but their IDs stay used, so a reference in an old
note never silently points at a different suggestion. A re-raised item keeps its original ID with
status STILL-OPEN.

Structure:

- **Title** plus stack state, snapshot and restore confirmed, data used, roles driven, clusters
  covered, any NOT COVERED or re-assigned routes.
- **Quick wins** first: every High or Medium impact item that is also S-effort, one line each. This
  section is what actually gets acted on — put it at the top.
- **Triage summary** table: `ID · Impact · Effort · Dimension · Area · Roles · Title · Status`,
  sorted impact High to Low then effort ascending, so a High/S beats a High/L.
- **Suggestions**: one section per item — page or `file:line`, what's there *now*, the *suggestion*,
  evidence, effort, roles affected.
- **Withdrawn on verification**: every High-impact suggestion the verifier refuted, one line each
  with the reason.
- **Role gaps** — merged from every agent: per role, what it can't reach and the proposed feature.
- **Guardrail gaps** — merged: missing confirms, silent validation, gates not enforced server-side,
  and any testing-only affordance that isn't environment-gated.
- **Quality-of-life wins** — merged.
- **Cross-page consistency**, **Looked good** (merged verbatim, de-duping exact repeats only), and
  **Possible bugs → /bug-sweep** (carried verbatim — they feed the next bug sweep and must survive
  collation).
- **Coverage**: per cluster — roles driven, controls exercised, states shot, pages shot, files read,
  and every `not_reviewed` entry with its reason. A failed screenshot or a skipped control is
  reported, **never silently dropped**.

**De-dupe** across agents on shared root cause: keep the higher impact and lower effort, cite both
areas.

## 9. Report back

Tight summary: **confirm the database was snapshotted and restored** (or flag the failure and give
the path), then lead with the quick wins, counts by impact, **how many High-impact suggestions were
confirmed versus withdrawn on verification**, the role-gap and guardrail-gap headlines, NEW versus
STILL-OPEN, and the report path. Recommend where to start, but **do not implement.**

> `full` is heavy — many agents, every control against every role, plus snapshot and restore. For a
> fast skim use `/qa:ux-review quick`. Pair with `/qa:bug-sweep` (bugs) and `/qa:layout-sweep`
> (measured responsive defects); this one is polish, UX, quality-of-life, guardrails and role
> completeness.
