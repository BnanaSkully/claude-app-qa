#!/usr/bin/env python3
"""Structural self-check for the qa plugin.

Catches the drift that a human review misses and that only shows up on a stranger's
first run: an agent referenced by a command but not shipped, a tool flag that no longer
exists, a config key used in a prompt but never documented, a leftover reference to the
project this was generalised from.

Run locally with ``python scripts/validate.py``; CI runs the same thing on every push.
Exits 0 when clean, 1 when any error is found. Warnings never fail the build.

Deliberately dependency-free (no PyYAML) so it runs anywhere Python does.
"""
from __future__ import annotations

import ast
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGIN = os.path.join(ROOT, "plugins", "qa")

errors: list[str] = []
warnings: list[str] = []


def err(msg: str) -> None:
    errors.append(msg)


def warn(msg: str) -> None:
    warnings.append(msg)


def rel(path: str) -> str:
    return os.path.relpath(path, ROOT).replace("\\", "/")


def read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def frontmatter(text: str) -> dict:
    """Parse the leading --- block as flat key: value pairs. Good enough for our schema."""
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    out = {}
    for line in text[3:end].splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, _, value = line.partition(":")
        out[key.strip()] = value.strip().strip('"').strip("'")
    return out


def md_files(subdir: str) -> list[str]:
    d = os.path.join(PLUGIN, subdir)
    if not os.path.isdir(d):
        err("missing directory: plugins/qa/%s" % subdir)
        return []
    return sorted(os.path.join(d, f) for f in os.listdir(d) if f.endswith(".md"))


# ---------------------------------------------------------------- manifests
def check_manifests() -> None:
    mk_path = os.path.join(ROOT, ".claude-plugin", "marketplace.json")
    pl_path = os.path.join(PLUGIN, ".claude-plugin", "plugin.json")

    for path in (mk_path, pl_path):
        if not os.path.exists(path):
            err("missing manifest: %s" % rel(path))
            return
        try:
            json.loads(read(path))
        except json.JSONDecodeError as exc:
            err("%s is not valid JSON: %s" % (rel(path), exc))
            return

    mk = json.loads(read(mk_path))
    pl = json.loads(read(pl_path))

    for field in ("name", "owner", "plugins"):
        if field not in mk:
            err("marketplace.json missing required field: %s" % field)
    for field in ("name", "description", "version"):
        if field not in pl:
            err("plugin.json missing required field: %s" % field)

    for entry in mk.get("plugins", []):
        if entry.get("name") != pl.get("name"):
            err("name mismatch: marketplace lists %r, plugin.json declares %r"
                % (entry.get("name"), pl.get("name")))
        source = (entry.get("source") or "").lstrip("./")
        target = os.path.join(ROOT, "plugins", os.path.basename(source))
        if not os.path.isdir(target):
            err("marketplace source path does not exist: %s" % entry.get("source"))
        if ".." in (entry.get("source") or ""):
            err("marketplace source uses '..', which breaks after install: %s" % entry.get("source"))

    name = pl.get("name", "")
    if not re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", name):
        err("plugin name must be kebab-case, got %r" % name)


# ---------------------------------------------------------------- frontmatter
def check_frontmatter() -> tuple[set, set]:
    agents, commands = set(), set()

    for path in md_files("agents"):
        fm = frontmatter(read(path))
        stem = os.path.splitext(os.path.basename(path))[0]
        if not fm:
            err("%s has no YAML frontmatter" % rel(path))
            continue
        for field in ("name", "description"):
            if not fm.get(field):
                err("%s frontmatter missing required field: %s" % (rel(path), field))
        if fm.get("name") and fm["name"] != stem:
            err("%s declares name %r but the filename says %r; agents are resolved by name"
                % (rel(path), fm["name"], stem))
        agents.add(fm.get("name", stem))
        model = fm.get("model")
        if model and model not in ("opus", "sonnet", "haiku", "inherit"):
            warn("%s pins model %r — a dated or unknown alias will break when it is retired"
                 % (rel(path), model))

    for path in md_files("commands"):
        fm = frontmatter(read(path))
        stem = os.path.splitext(os.path.basename(path))[0]
        if not fm:
            err("%s has no YAML frontmatter" % rel(path))
            continue
        if not fm.get("description"):
            err("%s frontmatter missing required field: description" % rel(path))
        commands.add(stem)

    return agents, commands


# ---------------------------------------------------------------- cross-refs
def check_agent_refs(agents: set) -> None:
    """Every subagent_type a command spawns must actually ship."""
    for path in md_files("commands"):
        text = read(path)
        for match in re.finditer(r"subagent_type:\s*`?([a-z][a-z0-9-]*)`?", text):
            ref = match.group(1)
            if ref not in agents:
                line = text[: match.start()].count("\n") + 1
                err("%s:%d spawns subagent_type %r, which is not in plugins/qa/agents/"
                    % (rel(path), line, ref))


def _argparse_flags(script_path: str) -> set:
    """Every --flag the script's argparse actually defines, read from the AST."""
    flags = set()
    try:
        tree = ast.parse(read(script_path))
    except SyntaxError:
        return flags
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if isinstance(fn, ast.Attribute) and fn.attr == "add_argument":
            for arg in node.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    if arg.value.startswith("-"):
                        flags.add(arg.value)
    return flags


def check_tool_refs() -> None:
    """Prompts must only invoke tools that exist, with flags that exist."""
    tools_dir = os.path.join(PLUGIN, "tools")
    if not os.path.isdir(tools_dir):
        err("missing plugins/qa/tools/")
        return

    available = {f for f in os.listdir(tools_dir) if f.endswith(".py")}
    flag_cache = {}

    for sub in ("commands", "agents"):
        for path in md_files(sub):
            text = read(path)
            for match in re.finditer(r"tools/([a-z_]+\.py)((?:[^\n`]*))", text):
                script = match.group(1)
                line = text[: match.start()].count("\n") + 1
                if script not in available:
                    err("%s:%d references tools/%s, which does not exist"
                        % (rel(path), line, script))
                    continue
                if script not in flag_cache:
                    flag_cache[script] = _argparse_flags(os.path.join(tools_dir, script))
                known = flag_cache[script]
                if not known:
                    continue
                for flag in re.findall(r"(--[a-z][a-z0-9-]*)", match.group(2)):
                    if flag not in known:
                        err("%s:%d passes %s to %s, which does not define it (has: %s)"
                            % (rel(path), line, flag, script, ", ".join(sorted(known)) or "none"))


def check_config_keys() -> None:
    """Every config key a prompt relies on should be documented in reference/config.md."""
    doc_path = os.path.join(PLUGIN, "reference", "config.md")
    if not os.path.exists(doc_path):
        err("missing plugins/qa/reference/config.md")
        return
    doc = read(doc_path)

    documented = set(re.findall(r'"([a-zA-Z][a-zA-Z0-9_]*)"\s*:', doc))
    for sub in ("commands", "agents"):
        for path in md_files(sub):
            text = read(path)
            for match in re.finditer(r"`config\.([a-zA-Z][a-zA-Z0-9_.]*)`|`?([a-z]+\.[a-zA-Z]+)`\s+from the config", text):
                key = match.group(1) or match.group(2) or ""
                root = key.split(".")[0]
                leaf = key.split(".")[-1]
                if root and root not in documented and leaf not in documented:
                    line = text[: match.start()].count("\n") + 1
                    warn("%s:%d references config key %r not found in reference/config.md"
                         % (rel(path), line, key))


# ---------------------------------------------------------------- leftovers
LEAKS = [
    # (regex, why it matters)
    (r"C:\\Users\\[A-Za-z]", "an absolute path from the author's machine"),
    (r"\bMyze\b", "the name of the project this was generalised from"),
    (r"\bBrass Tap\b", "a fixture venue from the original project"),
    (r"\bmargin_control\b", "the original project's database name"),
    (r"\bGapp\b", "a seeded tenant from the original project"),
    (r"\.uitest\b", "the original project's private script directory"),
]


def check_leftovers() -> None:
    skip_dirs = {".git", "__pycache__", "node_modules", ".github"}
    for base, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in skip_dirs]
        for name in files:
            if not name.endswith((".md", ".py", ".json", ".txt", ".yml")):
                continue
            path = os.path.join(base, name)
            if os.path.samefile(path, os.path.abspath(__file__)):
                continue
            try:
                text = read(path)
            except (UnicodeDecodeError, OSError):
                continue
            for pattern, why in LEAKS:
                for match in re.finditer(pattern, text):
                    line = text[: match.start()].count("\n") + 1
                    err("%s:%d contains %r — %s" % (rel(path), line, match.group(0), why))


def check_documented_config_resolves() -> None:
    """The documented config must actually take effect when the tools load it.

    This guards the seam nobody checks: reference/config.md is written by hand and the
    loader is written separately, so the two drift. When they do, a perfectly correct
    config parses without error, sets nothing, and every probe silently targets the
    default URL — with no error message anywhere to explain it. That shipped once.
    Here we take the documented example verbatim, load it, and assert the values come
    back out.
    """
    doc_path = os.path.join(PLUGIN, "reference", "config.md")
    tools_dir = os.path.join(PLUGIN, "tools")
    if not os.path.exists(doc_path):
        return

    blocks = re.findall(r"```json\s*\n(.*?)```", read(doc_path), re.S)
    if not blocks:
        warn("reference/config.md has no plain ```json example to verify against")
        return

    try:
        example = json.loads(blocks[0])
    except json.JSONDecodeError as exc:
        err("the ```json example in reference/config.md is not valid JSON: %s" % exc)
        return

    import tempfile

    sys.path.insert(0, tools_dir)
    try:
        import qakit  # noqa: E402
    except Exception as exc:  # pragma: no cover - import problems are reported elsewhere
        warn("could not import qakit to verify the documented config: %s" % exc)
        return
    finally:
        sys.path.pop(0)

    with tempfile.TemporaryDirectory() as tmp:
        os.makedirs(os.path.join(tmp, ".claude"))
        with open(os.path.join(tmp, ".claude", "qa.json"), "w", encoding="utf-8") as fh:
            json.dump(example, fh)

        # Env vars outrank the file; clear them so we test the file itself.
        saved = {k: os.environ.pop(k) for k in list(os.environ)
                 if k.startswith("CLAUDE_QA_")}
        try:
            cfg = qakit.load_config(start=tmp)
            checks = []
            want_web = (example.get("urls") or {}).get("web") or (example.get("web") or {}).get("url")
            if want_web:
                got = qakit.resolve_url(cfg, "/").rstrip("/")
                checks.append(("web url", want_web.rstrip("/"), got))
            want_out = (example.get("paths") or {}).get("output") or (example.get("output") or {}).get("dir")
            if want_out:
                checks.append(("output dir", want_out, cfg.get("output.dir")))
            want_api = (example.get("urls") or {}).get("api") or (example.get("api") or {}).get("url")
            if want_api:
                checks.append(("api url", want_api, cfg.get("api.url")))

            for label, want, got in checks:
                if got != want:
                    err("config drift: reference/config.md documents %s %r, but the loader "
                        "resolves %r. A user's config would be silently ignored." % (label, want, got))
        except Exception as exc:
            err("loading the documented config example raised: %s" % exc)
        finally:
            os.environ.update(saved)


def check_python_compiles() -> None:
    tools_dir = os.path.join(PLUGIN, "tools")
    for base, dirs, files in os.walk(tools_dir):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for name in files:
            if not name.endswith(".py"):
                continue
            path = os.path.join(base, name)
            try:
                ast.parse(read(path))
            except SyntaxError as exc:
                err("%s does not parse: %s" % (rel(path), exc))


def main() -> int:
    check_manifests()
    agents, _commands = check_frontmatter()
    check_agent_refs(agents)
    check_tool_refs()
    check_config_keys()
    check_leftovers()
    check_documented_config_resolves()
    check_python_compiles()

    for w in warnings:
        print("WARN  %s" % w)
    for e in errors:
        print("ERROR %s" % e)

    print()
    print("%d error(s), %d warning(s)" % (len(errors), len(warnings)))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
