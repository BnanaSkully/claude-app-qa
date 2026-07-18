# Role & capability matrix — <YOUR APP>

Copy this to **`.claude/qa/role-matrix.md`** in your project and fill it in. Skip the whole file
if your app has no role model.

**Single source of truth** for the roles and who can do what. Both `bug-hunter` and `ux-reviewer`
read it, so their role coverage stays identical.

**The code is authoritative.** Name the module that actually enforces permissions below. If this file
and that code disagree, **the code wins — and the disagreement is itself a finding.**

- Backend enforcement: `<e.g. backend/permissions.py>`
- Frontend mirror: `<e.g. frontend/lib/permissions.ts>`

## Ranks

List roles high to low, with any off-ladder ones called out.

> Example: **admin (100) · owner (80) · manager (40) · member (20) · auditor (off-ladder).**

## Capability matrix

| capability | admin | owner | manager | member | auditor |
|---|---|---|---|---|---|
| <read the main records> | ✓ | ✓ | ✓ | ✓ | ✓ |
| <approve / mutate them> | ✓ | ✓ | ✓ | — | ✓ |
| <manage users> | ✓ | ✓ | — | — | — |
| <admin-only actions> | ✓ | — | — | — | — |

## How to act as each role

- The probe tools take `--as-user <id>` and `--as-tenant <id>` and apply whatever identity shim
  `.claude/qa.json` declares. Discover a real user id per role from the configured users
  endpoint each run — **never hardcode ids.**
- If your app has a real login path that behaves differently from the header/localStorage shim
  (a gate that checks the session cookie rather than a header), document that path here, and note
  which gates require it.
- **Privileged pages need a privileged identity.** Document any route that redirects an
  under-privileged user away, and which role is needed to actually render it. Probing it as the
  wrong role produces a silent false clean.

## Server-side is the real gate

A hidden button is **not** a permission. When a role shouldn't be able to do something, verify the
**server** refuses the write — not merely that the UI hides the control. A control that is hidden in
the UI but that the API still honours for a lower role is a **Critical** guardrail bug.

Cross-tenant data access is likewise Critical: act as a user of tenant A, request tenant B's data,
and confirm it is refused rather than returned. If your model nests tenants (an organisation above a
workspace, say), probe **each** level — a leak one level up is just as severe.
