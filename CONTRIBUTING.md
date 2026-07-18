# Contributing

Thanks for looking. This started as a private toolkit for one app and was generalised for
public use, so the most valuable contributions right now are **reports of where it fails on a
project unlike the one it grew up in.**

## The most useful thing you can report

Run `/qa:setup` on your project and tell us what it got wrong. It has to infer your stack, your
routes, your roles, your identity shim and your database commands from a cold read of the
codebase. When it guesses badly, that is a bug worth filing — please include the config it
generated and what the right answer was.

Stacks that differ most from the original (FastAPI + Next.js + Postgres, docker compose) are the
most valuable: Rails, Django templates, Laravel, Go, SPA-with-no-backend, serverless, anything
without Docker, anything without a REST API.

## Running the checks

```bash
python scripts/validate.py                       # structure and cross-references
python -m compileall -q plugins/qa/tools         # tools byte-compile
pip install -r plugins/qa/tools/requirements.txt # websocket-client (psutil optional)
```

`validate.py` is the important one. It catches what review misses: a command spawning an agent
that does not ship, a prompt passing a tool flag that does not exist, a config key used in a
prompt but never documented, and any leftover absolute path or reference to the original
project. CI runs it on every push, along with the tools on Python 3.9 / 3.11 / 3.13 and on
Linux, macOS and Windows.

## Testing the plugin locally

Point Claude Code at your working copy rather than the published marketplace:

```bash
claude --plugin-dir ./plugins/qa
```

Then `/qa:setup` in a real project. There is no substitute for a real project — the commands
drive a running app, so nothing meaningful is proven by a fixture.

## Working on the prompts

The commands and agents in `plugins/qa/commands/` and `plugins/qa/agents/` are the actual
product; the Python is just instrumentation. A few conventions worth keeping:

- **Say why, not just what.** Nearly every instruction carries the failure it prevents. That is
  deliberate — an agent that knows *why* a rule exists applies it correctly in situations the
  rule never anticipated, and drops it when genuinely irrelevant.
- **Never let a gap pass silently.** Anything an agent could not test must be reported as
  not-tested with a reason. "Found nothing" and "did not look" must never be indistinguishable.
- **Findings need observed evidence** — a response, a log line, a measurement, a screenshot the
  agent actually opened. Code-reading alone yields a suspicion, not a finding.
- **Keep the verification pass adversarial.** The verifier agents exist to *refute*, and they
  must stay independent of the agent whose work they check. Do not let them share reasoning, and
  do not soften their bias toward refuting.
- **Stay project-neutral.** No hardcoded paths, ports, roles, or domain vocabulary. Say "tenant"
  and "identity", never a specific product's nouns. `validate.py` enforces the obvious cases.

## Adding a tool

Tools live in `plugins/qa/tools/`, take `--as-user` / `--as-tenant` where identity matters, print
exactly one JSON object to stdout with human chatter on stderr, and must:

- pick a **free debug port** (never a fixed one) so parallel agents do not collide,
- clean up their browser processes and profile directory on **every** exit path,
- degrade with a clear message and a non-zero exit when the app or browser is missing.

If a prompt references a new flag, `validate.py` will check it exists.

## Pull requests

Keep them focused, explain what breaks without the change, and say what you actually ran. If you
changed a prompt, saying "I ran this against my project and here is the diff in output" is worth
more than any amount of reasoning about why it should be better.

## Reporting security issues

Please do not open a public issue for anything exploitable. These tools execute commands from a
config file and drive a browser against a local app; if you find a way that turns into something
worse, raise it privately with the maintainer first.
