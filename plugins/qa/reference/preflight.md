# Orchestrator preflight spine (shared)

**Single source of truth** for the preflight that `/bug-sweep`, `/layout-sweep` and `/ux-review` each
run before fanning out. Follow it in order. Each command fills its own **slots** — which data to use,
which prior-report glob to read, whether it snapshots the database — inline in the command, not here.

**Failure mode of skipping a step:** a run against a dead app, hardcoded stale ids, a false-clean
privileged page, or re-hunting a bug you already fixed and documented.

0. **Resolve the plugin's absolute path, and hand it to every agent you spawn.** The tools live
   at `<plugin root>/tools/`. Work out that absolute path once, here — from the location of this
   file, or by locating `plugins/qa/tools/page_shot.py` under the installed plugin cache — and
   confirm the directory exists before fanning out.

   **Then include it verbatim in every agent brief**, e.g. *"your probe tools are at
   `C:\...\plugins\qa\tools\`; invoke them with that absolute path."*

   Subagent prompts are not shell contexts, so `${CLAUDE_PLUGIN_ROOT}` does **not** expand inside
   them. An agent that pastes that literal string into a command gets `python /tools/ui_crawl.py`
   and a file-not-found on its very first tool call — which costs the whole area, because the
   retry fails identically and the area is then recorded NOT COVERED. Passing the real path is
   the single cheapest thing that makes a sweep work at all.

1. **Read the config.** `.claude/qa.json` at the project root ([schema](config.md)). It supplies
   URLs, shell commands, the area map, roles and the by-design list. **If it's missing, say so
   plainly and offer `/qa:setup`** — then continue in fallback mode (web app at
   `http://localhost:3000`, output to `./checks`, areas derived by globbing the frontend pages) and
   note in the report header that the run was unconfigured.

2. **App up.** `GET` the configured `urls.health`. If down, run `commands.start`, re-check for up to
   ~90 seconds. **If it won't come up, STOP and tell the user** — never substitute a code read.

3. **Discover ids.** Using `auth.discover`, list the tenants and pick the one this command names.
   Discover a normal-user id and, separately, a **privileged-role id** for any path in
   `auth.privilegedPaths` — without it those pages silently measure the redirect target
   (see [environment.md](environment.md)). Never hardcode an id, ever.

4. **Read the newest prior report for THIS command** (each command names its own glob, under the
   configured output directory). Carry forward its ruled-out / by-design entries and its still-open
   items, so agents don't re-derive them and collation can tag NEW vs STILL-OPEN from real verdicts
   rather than guesswork.

5. **Read the by-design list** — `.claude/qa/by-design.md` if present. Hand each agent the
   entries touching its area. This is the main defence against false positives.

6. **Read the solutions store** if `paths.solutions` is configured — past fix write-ups, handed to
   agents as known/ruled-out input so a fixed bug isn't re-hunted. **A documented fix that reproduces
   again is a REGRESSION** — a real finding, and tag it as such.

7. **One-line status to the user:** app up, what data is in use, and — for a command that snapshots —
   that the database is snapshotted and will be restored, so they shouldn't make real changes
   mid-run.
