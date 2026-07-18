---
description: Spawn layout-hunter agents to probe the running UI across a full device x display-scaling matrix and write a dated report of objective responsive defects (overflow, clipping, off-screen controls, tiny tap targets), each with a suggested CSS fix.
argument-hint: "[full | quick | <area name> ...]"
---

# /layout-sweep — responsive / visual defect sweep

Scope requested: **$ARGUMENTS** (empty = `full`).

This finds **objective layout defects across resolutions and display scaling** and writes a report.
It does **not** fix anything. It is the third sibling: `/bug-sweep` finds functional bugs,
`/ux-review` makes subjective design suggestions, and **`/layout-sweep` measures layout correctness.**
The user triages.

## 1. Preflight

Follow the shared preflight spine — `<plugin>/reference/preflight.md` (read config →
app up → discover ids → read prior report → read the by-design list → one-line status). This
command's own slots:

- **Data:** probe against whatever data exists, so pages render real content rather than empty
  states. An empty app produces a misleadingly clean sweep — say so if that's what you find.
- **No database snapshot.** The probe navigates only and mutates nothing, so this command is
  read-only and needs no snapshot or restore. This makes it the **cheapest and safest of the three
  sweeps** — a good one to run often.
- **Privileged identity:** hand the agent covering `auth.privilegedPaths` an identity with
  `auth.privilegedRole`, and the ordinary identity for everything else. If none exists, record those
  paths under `not_probed`. **Never report a privileged page clean off an under-privileged probe** —
  the app redirects, and the probe silently measures the page it landed on instead. See
  `reference/environment.md`.
- **Prior reports:** the newest `<output>/ux-reviews/ux-review-*.md` (pull its overflow, responsive
  and narrow-width suggestions and hand each to the matching agent to confirm or refute **with a
  measurement**) and the newest `<output>/layout-sweeps/layout-sweep-*.md` (to tag findings NEW
  versus STILL-OPEN).
- **Solutions store:** if configured, hand each agent the responsive and layout write-ups touching
  its area as known input. **A documented layout fix that measures broken again is a regression** —
  a real finding.

## 2. Pages to probe

Probe **every distinct layout**, including sub-views reached by a query parameter and at least one
instance of each dynamic route.

**Sanity-check first:** glob the configured `paths.frontendPages`. Any route not in the config's
`areas` gets assigned to the nearest area, and you say so in the report header.

For each area, list its concrete paths. For a dynamic route, discover a real id from the API rather
than guessing one. For a route that only redirects, just confirm it lands and move on — it has no
layout of its own.

Resolve `$ARGUMENTS`:

- **`full`** or empty → every area.
- **`quick`** → **one** agent probing the five or six highest-traffic pages across the full matrix,
  top findings only, about 15 minutes.
- **Named areas** → just those.

## 3. Fan out (parallel)

Spawn one **layout-hunter** per selected area — Agent tool, `subagent_type: layout-hunter`, **all
calls in one message**. Give each:

0. **The absolute path to the plugin's `tools/` directory** (preflight step 0), and the **API base
   URL** from `urls.api`. Without the first, every tool call fails; without the second, an agent
   probing endpoints is guessing hostnames.
1. Its page list, with the discovered identity ids and any dynamic ids resolved.
2. Its area's responsive hypotheses from the prior UX review (step 1).
3. The brief: *"Probe each of your pages with `viewport_probe.py` across the full matrix, adjudicate
   every flagged candidate against the screenshot and the code, and return findings with a suggested
   CSS fix in the JSON envelope your system prompt defines."*

The agent already knows the probe, the matrix, the three signals, the by-design exclusions, severity
and the output format — **don't re-explain them.**

**If an agent errors or returns nothing usable, re-spawn it once** on its single most important page.
A second failure means that area is **NOT COVERED** in the report header. **Never fabricate
coverage.**

## 4. Collate into one report

Write `<output>/layout-sweeps/layout-sweep-<date>[-<scope>].md`, creating the folder. Structure:

- **Title** plus stack state, areas covered, any NOT COVERED.
- **Breakage matrix** — a compact table of **page x viewport-band** (phone / scaled-laptop / standard
  desktop / wide) marking where each page breaks. This is the most useful thing in the report,
  because it makes the *pattern* visible: one column breaking everywhere usually means a single
  shared container is at fault, not twelve separate bugs.
- **Triage summary** table: `# · Severity · Defect · Page · Viewports · Confidence · Title`, sorted
  Critical to Low. Flag any low-confidence item for a manual eyeball.
- **Findings**: one section per defect — page, viewports, **the measurement**, the culprit selector,
  evidence (screenshot path plus `file:line`), and **the suggested CSS fix**. De-dupe one root cause
  across pages and viewports: keep the highest severity and list all affected.
- **UX-review hypotheses**: each confirmed (becoming a finding) or refuted, one line with the
  measurement that settled it.
- **By design / cleared** — intended scroll regions, off-canvas drawers — so the next run doesn't
  re-litigate them. **Suggest the user promote the durable ones into `.claude/qa/by-design.md`.**
- **Coverage**: pages probed, and any `not_probed` with the reason.

## 5. Report back

Tight summary: counts by severity, the worst-breaking pages and widths, the top 3 fixes one line
each, and the report path.

Call out the **scaled-laptop band** specifically if it breaks. It is the most commonly missed band in
responsive testing, because a developer on a 100%-scaled monitor never sees it, while a large share
of real users on 125% or 150% Windows scaling live there permanently.

Recommend where to start; **do not implement**. Note any overlap with `/ux-review` findings.

> Continuous: `/loop 1w /qa:layout-sweep`. Pair with `/qa:bug-sweep` (functional) and `/qa:ux-review`
> (design). This one is measured layout correctness across resolutions.
