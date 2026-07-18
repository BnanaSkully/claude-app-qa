# Scenario: <NAME>

Copy this to **`.claude/qa/scenarios/<name>.md`** and fill it in. `/qa:drive-app` reads it and
lives through it as your app's real user personas.

**What a scenario is:** the recurring cycle your product exists to serve, written down as periods of
work with named people doing it. Not a test plan — a description of a few weeks in the life of a
team using your app. Write it the way you'd describe the job to a new employee.

**Why it's worth the hour it takes:** this is the only input that lets an agent find workflow
problems — a number that drifts across periods, a handoff between two roles that nobody tested, a
daily task that's fine once and exhausting on the fifteenth repetition. No amount of per-page testing
surfaces those.

---

## The cycle

What is the loop this app exists to serve, and how often does it turn? Daily, weekly, monthly?

> Example: *"A team receives supplier deliveries most days, records what arrived, uploads sales at
> week's end, counts stock monthly, and closes the books at month end."*

**Periods in this scenario:** <e.g. 10 days plus a close> — enough turns of the cycle that drift and
repetition-fatigue would show up. Fewer than about five rarely reveals anything a page-level review
wouldn't.

## The personas

One row per person. Use the roles that actually exist in your app.

| Persona | Role | What they do in the cycle |
|---|---|---|
| <name> | <role> | <the routine work they own> |
| <name> | <role> | <what they approve or review> |
| <name> | <role> | <what they read, and when> |

**Say explicitly whether the run should stop or continue if a persona is missing** from the discovered
users.

## The periods

One block per period, or one block per *type* of period plus a schedule. Be concrete about what
happens, in what order, and by whom.

### Period 1

- **<persona>** does <the routine entry work>, using <the input data — see below>.
- **<persona>** reviews and approves it.
- Expected afterwards: <a figure, a state, a count — whatever the scenario can state>.

### Period N (a reporting or closing period)

- **<persona>** reads <the report>. Expected: <the figures>.
- **<persona>** performs the close or lock.
- **<persona>** pulls <whatever export or pack the app produces>. Expected: <figures>.

## The input data

Where does the data the personas enter come from?

- **A generator or injection command**, if you have one: give the exact command. Note it must exist
  before a run — `/qa:drive-app` stops rather than improvising.
- **Fixture files** checked into the repo: give the paths.
- **Written inline below**, for a small scenario: just list what gets entered each period.

## Planted complications

The most valuable part of a scenario. List anything deliberately awkward in the data that the app
**should** handle, and what handling it correctly looks like.

| Complication | Where | Correct app response |
|---|---|---|
| <a duplicate submission> | period N | <the app flags it as a duplicate> |
| <a record that doesn't add up> | period N | <flagged for review, not silently accepted> |
| <a genuine anomaly in the data> | across the run | <surfaced in the report as an exception> |

**Critical framing:** if the app *catches* one of these, that is a **passing check** worth recording,
**not a bug**. The bug is the opposite — the app **failing** to catch it. Spell that out for each row,
because an agent reacting like a user will otherwise report the app's correct warning as a defect.

## Expected outcomes

The figures a verification pass compares the live app against, after the run. Be as specific as you
can — an exact number where you know it, a direction or bound where you don't.

| What | Expected |
|---|---|
| <the headline figure at close> | <value, or "should match the sum of periods 1-10"> |
| <a per-record state> | <value> |
| <an export's totals> | <value> |

**A scenario with no expected outcomes still finds friction and crashes — but it cannot catch a
quietly wrong number, which is usually the most expensive bug in the product.** Even rough
expectations ("this should be within a few percent of that") are worth stating.

## Notes

Anything else a driver should know: things that look wrong but aren't (though prefer
`.claude/qa/by-design.md` for those), known-slow steps, or an order that matters more than it looks.
