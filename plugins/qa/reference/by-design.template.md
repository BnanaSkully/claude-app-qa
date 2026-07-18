# By-design behaviours — <YOUR APP>

Copy this to **`.claude/qa/by-design.md`** in your project and fill it in.

**This is the single most valuable file in the whole setup.** It is the shared catalogue of "this is
deliberate, not a defect", read by both the bug hunt and the design review so they cannot drift apart
and start disagreeing about what counts as a bug versus a feature.

- **bug-hunter / bug-verifier:** a finding that merely describes something on this list is **not a
  bug** — refute it and cite the entry. The *bug* is the opposite: the guarantee below being broken.
- **ux-reviewer / ux-verifier:** never *suggest away* anything on this list. It is intentional
  product behaviour, already decided.
- Orchestrators also hand agents the previous report's "ruled out" entries and any solutions
  write-ups — those count as **extensions** of this list. If you re-probe one and it now genuinely
  fails, that is a **regression**, and a real finding.

**Grow this file every run.** Each finding you triage as "no, that's intended" belongs here. Ten
minutes spent here after a sweep is what stops the same false positive arriving every single time.

---

## The core guarantee

> One sentence: what must never be wrong in this product, and why.
>
> Example: *"Trust is the product. One wrong headline number permanently loses a customer."*

State how the critical values are represented, since agents will check arithmetic against it.

> Example: *"Money is integer cents, never floats. Schemas return `*_cents` plus a preformatted
> `*_display` string."*

## Intentional behaviours

One heading per cluster. For each: what the behaviour is, why it's deliberate, and — critically —
**what the actual bug would look like**, so an agent knows what it should still be hunting for.

### <Cluster, e.g. Permissions and redaction>

- **<The behaviour>.** <Why it is deliberate.> The *bug* would be <the opposite failure>.
- **<An explicit exception to the rule above>**, because <reason>. Do not flag it.

### <Cluster, e.g. Honesty flags>

- **<Uncertainty shown rather than hidden>** — deliberate: better an honest "we can't be sure" than a
  confident wrong number.

### <Cluster, e.g. Validation>

- A **4xx with a clear validation message** on bad input is *correct*. The bug would be a **500**, or
  a silent accept of bad data.

## Testing-only affordances — deliberate, but must be gated

List any dev-login, user-switcher, impersonation or debug route that exists on purpose in
development. Then state the gate:

> These are deliberate testing tools. Do not suggest removing them and do not call them a leak *in
> development*. It **is** a finding — guardrail, Critical — if any of them is not gated to the
> development environment, i.e. could appear or function in production.

## Test / demo data

Note any seeded data with deliberately-planted anomalies, so an agent cross-checks against the seed
before reporting one as a real defect.

## Shipped design decisions — do not reopen

List settled decisions the design reviewer should suggest *within*, not redo: the visual language,
navigation shape, anything you already considered and declined.

> Example: *"Global search lives in the top bar. A command palette was considered and declined —
> don't propose one."*

## Environment expectations — not bugs

Things that look broken but are known and expected: tests that self-skip without a database, a
command that wipes data by design, and so on.
