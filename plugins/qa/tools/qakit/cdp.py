"""Cross-platform Chrome-DevTools-Protocol support for the qa probes.

Provides:
  * ``find_browser()``     — locate Chrome / Edge / Chromium / Brave on Windows, macOS or Linux
  * ``load_config()``      — read ``.claude/qa.json`` (walking up from cwd), env vars win
  * ``free_port()``        — a guaranteed-free debug port
  * ``launch()``           — start a headless browser on its own throwaway profile
  * ``wait_for_ws()`` / ``connect()`` — the CDP handshake + retry loop
  * ``shutdown()``         — kill EVERY browser process belonging to this run's profile dir
  * ``apply_identity()``   — a generic, config-driven localStorage/cookie auth shim
  * ``resolve_url()`` / ``output_path()`` / ``normalize_path()`` / ``slug()`` — small helpers

Python 3.9+. Standard library plus ``websocket-client``; ``psutil`` is used when
importable and cleanly fallen back on when it is not.
"""

from __future__ import annotations

import copy
import errno
import json
import os
import platform
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

try:  # optional, only used to make process cleanup tidier
    import psutil  # type: ignore
except Exception:  # pragma: no cover - psutil is genuinely optional
    psutil = None

try:
    import websocket  # websocket-client
    _WEBSOCKET_IMPORT_ERROR = None
except ImportError as exc:  # pragma: no cover
    # Deliberately NOT fatal at import time. Only the CDP connection needs this
    # package; loading config, resolving URLs and computing output paths do not.
    # Killing the process on import makes the module unusable for anything that
    # merely wants to read configuration — including tooling that has no browser
    # and never will. The error is raised at the point of actual use instead,
    # where it is both accurate and actionable.
    websocket = None
    _WEBSOCKET_IMPORT_ERROR = exc


def _require_websocket():
    """Raise a clear, actionable error at the moment a CDP connection is needed."""
    if websocket is None:
        raise QAError(
            "the qa probes need the 'websocket-client' package to drive a browser.\n"
            "Install it with:  pip install -r requirements.txt\n"
            "(import error: {})".format(_WEBSOCKET_IMPORT_ERROR)
        )


# --------------------------------------------------------------------------
# errors
# --------------------------------------------------------------------------

class QAError(RuntimeError):
    """Any probe failure that should be reported to the user, not traced."""


class BrowserNotFound(QAError):
    """No Chromium-family browser could be located."""


# --------------------------------------------------------------------------
# config
# --------------------------------------------------------------------------

CONFIG_RELPATH = os.path.join(".claude", "qa.json")

#: env var -> dotted config path it overrides
CLAUDE_QA_ENV_VARS = {
    "CLAUDE_QA_WEB_URL": "web.url",
    "CLAUDE_QA_API_URL": "api.url",
    "CLAUDE_QA_OUTPUT_DIR": "output.dir",
    "CLAUDE_QA_BROWSER": "browser.executable",
    "CLAUDE_QA_BROWSER_ARGS": "browser.args",
}

DEFAULTS = {
    "web": {"url": "http://localhost:3000"},
    "api": {"url": None},
    "output": {"dir": "./checks"},
    "browser": {"executable": None, "args": []},
    # Optional CSS selector that means "the app has rendered". Purely an
    # optimisation: without it we fall back to readyState + non-empty body.
    "readySelector": None,
    "auth": {},
}


def _deep_merge(base, overlay):
    """Merge ``overlay`` over ``base``, copying nested dicts all the way down.

    The copy is load-bearing, not tidiness. A shallow ``dict(base)`` leaves the
    sub-dicts SHARED with the base, so ``_apply_env_overrides`` writes straight
    through into module-level ``DEFAULTS``: one ``load_config()`` with
    CLAUDE_QA_WEB_URL set permanently rewrites ``DEFAULTS["web"]["url"]`` for the
    life of the process, and every later ``load_config()`` in that process returns
    the poisoned URL even with the env var unset — a probe silently driving the
    wrong app, with nothing on screen to explain it.
    """
    out = copy.deepcopy(base) if isinstance(base, dict) else {}
    for k, v in (overlay or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def _find_config_file(start=None):
    """Walk up from ``start`` (default cwd) looking for ``.claude/qa.json``."""
    here = os.path.abspath(start or os.getcwd())
    last = None
    while here != last:
        candidate = os.path.join(here, CONFIG_RELPATH)
        if os.path.isfile(candidate):
            return candidate
        last, here = here, os.path.dirname(here)
    return None


class Config(dict):
    """A dict with dotted-path ``get()``.

    ``cfg.get("web.url")`` and ``cfg.get("auth.localStorage", {})`` both work;
    a plain ``cfg.get("web")`` still returns the sub-dict.
    """

    #: absolute path of the config file this was loaded from (None if defaults)
    source = None
    #: directory the config file lives in, two levels up from .claude/ (or cwd)
    root = None

    def get(self, path, default=None):  # type: ignore[override]
        if not isinstance(path, str) or "." not in path:
            return dict.get(self, path, default)
        node = self
        for part in path.split("."):
            if isinstance(node, dict) and part in node:
                node = node[part]
            else:
                return default
        return default if node is None else node


def _apply_env_overrides(data):
    for env_name, dotted in CLAUDE_QA_ENV_VARS.items():
        raw = os.environ.get(env_name)
        if raw is None or raw == "":
            continue
        value = raw.split() if dotted == "browser.args" else raw
        node = data
        parts = dotted.split(".")
        for part in parts[:-1]:
            if not isinstance(node.get(part), dict):
                node[part] = {}
            node = node[part]
        node[parts[-1]] = value
    return data


#: config keys the probes actually consume, and the type each one must be.
#: (dotted path, accepted types, human description of the right shape)
_CONFIG_SCHEMA = [
    ("web", dict, "an object like {\"url\": \"http://localhost:3000\"}"),
    ("web.url", str, "a URL string"),
    ("api", dict, "an object like {\"url\": \"http://localhost:8000\"}"),
    ("api.url", str, "a URL string"),
    ("output", dict, "an object like {\"dir\": \"./checks\"}"),
    ("output.dir", str, "a directory path string"),
    ("browser", dict, "an object like {\"executable\": \"/path/to/chrome\"}"),
    ("browser.executable", str, "a path string"),
    ("browser.args", (list, str), "a list of argument strings"),
    ("readySelector", str, "a CSS selector string"),
    ("auth", dict, "an object with 'localStorage' and/or 'cookies'"),
    ("auth.localStorage", dict, "an object mapping key -> value template"),
    ("auth.cookies", dict, "an object mapping cookie name -> value template"),
    ("urls", dict, "an object like {\"web\": \"...\", \"api\": \"...\"}"),
    ("paths", dict, "an object like {\"output\": \"./checks\"}"),
]


def _validate_config(data, source=None):
    """Type-check the config keys the probes consume, before anything reads them.

    Without this, a wrong type surfaces as a raw traceback from deep inside a
    helper (``'str' object has no attribute 'items'``), or — far worse — not at
    all: ``"web": "http://prod"`` makes ``Config.get("web.url")`` return None, the
    probes fall back to localhost:3000, and the whole run silently drives the
    WRONG APP with no message anywhere. Naming the offending key and the file is
    the difference between a five-second fix and an hour of confusion.
    """
    if not isinstance(data, dict):
        raise QAError("Config {} must contain a JSON object, got {}".format(
            source or "(defaults)", type(data).__name__))
    where = " in {}".format(source) if source else ""
    for dotted, types, shape in _CONFIG_SCHEMA:
        node = data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                node = _MISSING
                break
            node = node[part]
        if node is _MISSING or node is None:
            continue                      # absent / explicitly null is always fine
        if not isinstance(node, types):
            raise QAError(
                "Config key {!r}{} must be {}, but it is {} ({!r}).".format(
                    dotted, where, shape, type(node).__name__, node)
            )


class _Missing:
    """Sentinel: 'this key was absent', distinct from 'present and null'."""


_MISSING = _Missing()


def _normalise(raw, source=None):
    """Map the documented config shape onto the internal one.

    ``reference/config.md`` documents ``urls.web`` / ``urls.api`` / ``paths.output``,
    because that groups readably for the person writing the file. Internally the
    accessors want ``web.url`` / ``api.url`` / ``output.dir``. Without this bridge a
    fully correct config parses fine, sets nothing, and every probe silently targets
    the default ``http://localhost:3000`` while the user stares at a file that says
    otherwise — a failure with no error message anywhere. Both shapes are accepted;
    the internal one wins if somebody writes both.
    """
    if not isinstance(raw, dict):
        return raw
    out = dict(raw)

    # Every lookup below assumes the sections are dicts. A config that writes
    # "web": "http://x" (a string where an object belongs) would otherwise raise
    # a bare AttributeError from inside a helper, with no mention of the file or
    # the key — so the shape is checked first and reported properly.
    _validate_config(out, source)

    urls = out.get("urls")
    if isinstance(urls, dict):
        for src, (section, key) in {
            "web": ("web", "url"),
            "api": ("api", "url"),
            "health": ("health", "url"),
            "openapi": ("openapi", "url"),
        }.items():
            if urls.get(src) and not (out.get(section) or {}).get(key):
                out.setdefault(section, {})
                if isinstance(out[section], dict):
                    out[section][key] = urls[src]

    paths = out.get("paths")
    if isinstance(paths, dict):
        if paths.get("output") and not (out.get("output") or {}).get("dir"):
            out.setdefault("output", {})
            if isinstance(out["output"], dict):
                out["output"]["dir"] = paths["output"]

    return out


def load_config(start=None):
    """Load project config, or sensible defaults when there is no config at all.

    Search order for each value: environment variable > ``.claude/qa.json``
    (nearest one at or above ``start``/cwd) > built-in default.
    """
    path = _find_config_file(start)
    file_data = {}
    if path:
        try:
            with open(path, "r", encoding="utf-8") as fh:
                file_data = json.load(fh) or {}
        except json.JSONDecodeError as exc:
            raise QAError("Config file {} is not valid JSON: {}".format(path, exc))
        except OSError as exc:
            raise QAError("Could not read config file {}: {}".format(path, exc))
        if not isinstance(file_data, dict):
            raise QAError("Config file {} must contain a JSON object".format(path))

    merged = _apply_env_overrides(_deep_merge(DEFAULTS, _normalise(file_data, path)))
    # Re-check after the env overrides: an env var can introduce a bad shape of
    # its own (CLAUDE_QA_BROWSER_ARGS is split into a list, the rest stay strings),
    # and a value that only becomes wrong at this stage must still be reported.
    _validate_config(merged, path or "environment overrides")
    cfg = Config(merged)
    cfg.source = path
    # Project root = the folder holding .claude/, else cwd. Relative output
    # dirs resolve against it so probes write to the same place from anywhere.
    cfg.root = os.path.dirname(os.path.dirname(path)) if path else os.path.abspath(os.getcwd())
    return cfg


# --------------------------------------------------------------------------
# browser discovery
# --------------------------------------------------------------------------

_WINDOWS_RELATIVE = [
    r"Google\Chrome\Application\chrome.exe",
    r"Google\Chrome Beta\Application\chrome.exe",
    r"Microsoft\Edge\Application\msedge.exe",
    r"Chromium\Application\chrome.exe",
    r"BraveSoftware\Brave-Browser\Application\brave.exe",
]

_MACOS_PATHS = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
    os.path.expanduser("~/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
    os.path.expanduser("~/Applications/Chromium.app/Contents/MacOS/Chromium"),
]

_LINUX_COMMANDS = [
    "google-chrome",
    "google-chrome-stable",
    "chromium",
    "chromium-browser",
    "microsoft-edge",
    "microsoft-edge-stable",
    "brave-browser",
]

#: process names we are willing to kill during cleanup (basename, lowercased)
BROWSER_PROCESS_NAMES = {
    "chrome.exe", "msedge.exe", "brave.exe", "chromium.exe",
    "chrome", "msedge", "brave", "brave-browser", "chromium",
    "chromium-browser", "google-chrome", "google-chrome-stable",
    "microsoft-edge", "microsoft-edge-stable",
    "Google Chrome", "Chromium", "Microsoft Edge", "Brave Browser",
}


def _windows_candidates():
    bases = [
        os.environ.get("PROGRAMFILES", r"C:\Program Files"),
        os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"),
        os.environ.get("LOCALAPPDATA", os.path.expanduser(r"~\AppData\Local")),
    ]
    out = []
    for base in bases:
        if not base:
            continue
        for rel in _WINDOWS_RELATIVE:
            out.append(os.path.join(base, rel))
    return out


def _candidates():
    system = platform.system()
    if system == "Windows":
        return _windows_candidates(), []
    if system == "Darwin":
        return list(_MACOS_PATHS), list(_LINUX_COMMANDS)
    return [], list(_LINUX_COMMANDS)


def find_browser(config=None):
    """Locate a Chromium-family browser.

    Order: ``CLAUDE_QA_BROWSER`` env var, ``browser.executable`` from config, then
    well-known install locations for this OS. Raises ``BrowserNotFound`` with
    the full list of what was tried.
    """
    tried = []

    explicit = os.environ.get("CLAUDE_QA_BROWSER") or (config.get("browser.executable") if config else None)
    if explicit:
        expanded = os.path.expanduser(os.path.expandvars(explicit))
        if os.path.isfile(expanded) and os.access(expanded, os.X_OK):
            return expanded
        found = shutil.which(expanded)
        if found:
            return found
        source = "CLAUDE_QA_BROWSER" if os.environ.get("CLAUDE_QA_BROWSER") else "config browser.executable"
        raise BrowserNotFound(
            "The browser given by {} does not exist or is not executable:\n"
            "  {}\n"
            "Point it at a Chrome, Edge, Chromium or Brave binary, or unset it to "
            "let the probes search the usual locations.".format(source, expanded)
        )

    paths, commands = _candidates()
    for path in paths:
        tried.append(path)
        if os.path.isfile(path):
            return path
    for command in commands:
        tried.append("{} (on PATH)".format(command))
        found = shutil.which(command)
        if found:
            return found

    raise BrowserNotFound(
        "No Chromium-family browser found on this machine ({}).\n"
        "the qa probes need Chrome, Edge, Chromium or Brave to drive the app.\n\n"
        "Tried:\n{}\n\n"
        "Fix it by either:\n"
        "  * installing one of those browsers, or\n"
        "  * setting the CLAUDE_QA_BROWSER environment variable to the binary, e.g.\n"
        "      CLAUDE_QA_BROWSER=/path/to/chrome\n"
        "  * or adding {{\"browser\": {{\"executable\": \"/path/to/chrome\"}}}} "
        "to .claude/qa.json".format(
            platform.platform(), "\n".join("  - " + t for t in tried) or "  (nothing)"
        )
    )


# --------------------------------------------------------------------------
# launching / shutting down
# --------------------------------------------------------------------------

def free_port():
    """A guaranteed-free debug port.

    (An arithmetic scheme like ``9801 + pid % 150`` collides whenever two PIDs
    differ by a multiple of 150 — the colliding run then attaches to the other
    run's browser, which is a large source of flakiness. Binding port 0 and
    reading back what the OS handed us cannot collide that way.)
    """
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
    finally:
        s.close()


def _profile_dir(tag, port):
    base = os.environ.get("TEMP") or os.environ.get("TMPDIR") or tempfile.gettempdir()
    return os.path.join(base, "qa-cdp-{}-{}-{}".format(tag, os.getpid(), port))


def launch(config=None, headless=True, window_size=(1400, 1600), extra_args=None, tag="probe"):
    """Start a browser with remote debugging on a free port and a throwaway profile.

    Returns ``(proc, profile_dir, port)``. Always pair with ``shutdown(proc, profile_dir)``
    in a ``finally:`` block.
    """
    browser = find_browser(config)
    port = free_port()
    profile = _profile_dir(tag, port)

    width, height = window_size
    args = [
        browser,
        "--disable-gpu",
        "--remote-debugging-port={}".format(port),
        "--remote-allow-origins=*",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-dev-shm-usage",
        "--disable-background-timer-throttling",
        "--disable-renderer-backgrounding",
        "--user-data-dir=" + profile,
        "--window-size={},{}".format(int(width), int(height)),
    ]
    if headless:
        args.insert(1, "--headless=new")
    # Unprivileged sandboxing fails for root in containers/CI; harmless otherwise.
    if platform.system() == "Linux" and hasattr(os, "geteuid") and os.geteuid() == 0:
        args.append("--no-sandbox")

    configured_args = (config.get("browser.args", []) if config else []) or []
    if isinstance(configured_args, str):
        configured_args = configured_args.split()
    args.extend(configured_args)
    args.extend(extra_args or [])
    args.append("about:blank")

    try:
        proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as exc:
        raise QAError("Could not start the browser at {}: {}".format(browser, exc))
    return proc, profile, port


def _is_browser_process_name(name):
    """Does this process name belong to the Chromium family we launched?

    Prefix matching, not exact, because the reparented helpers that actually leak
    are named after their parent with a suffix: macOS runs ``Google Chrome Helper``,
    ``Google Chrome Helper (Renderer)``, ``(GPU)`` and ``(Plugin)``, and Linux adds
    ``chrome_crashpad_handler``. An exact-name test matches NONE of those, so on
    macOS the cleanup killed only the launcher and left behind precisely the
    processes the whole routine exists to reap.
    """
    low = (name or "").lower()
    if not low:
        return False
    if low in {n.lower() for n in BROWSER_PROCESS_NAMES}:
        return True
    return any(low.startswith(n.lower()) for n in BROWSER_PROCESS_NAMES) or \
        low.startswith("chrome_crashpad") or low.startswith("chrome-crashpad")


def _kill_with_psutil(leaf):
    """Kill every process whose command line carries this run's profile leaf.

    The leaf is a unique per-run directory name (tag-pid-port), so the cmdline
    match alone identifies our processes; the name check is a secondary sanity
    filter, and the self-PID guard is what genuinely matters — this interpreter's
    own command line can contain the leaf.
    """
    killed = 0
    me = os.getpid()
    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            if proc.info["pid"] == me:
                continue
            cmdline = proc.info.get("cmdline") or []
            if not any(leaf in part for part in cmdline):
                continue
            name = proc.info.get("name") or ""
            if name and not _is_browser_process_name(name):
                continue
            proc.kill()
            killed += 1
        except Exception:
            # NoSuchProcess / AccessDenied / a race on cmdline — never fatal.
            continue
    return killed


def _kill_windows_powershell(leaf):
    # Name filter is load-bearing: this PowerShell process's OWN command line
    # contains the leaf string, so matching on CommandLine alone would have it
    # kill itself before it kills the browser.
    names = ", ".join("'{}'".format(n) for n in sorted(BROWSER_PROCESS_NAMES) if n.endswith(".exe"))
    ps = (
        "Get-CimInstance Win32_Process | "
        "Where-Object {{ $_.Name -in @({names}) -and $_.CommandLine -like '*{leaf}*' }} | "
        "ForEach-Object {{ Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }}"
    ).format(names=names, leaf=leaf)
    subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=25,
    )


def _kill_posix(leaf):
    """Terminate this run's browser processes on POSIX, then insist.

    A bare ``pkill -f <leaf>`` has NO process-name filter, so it kills anything
    whose command line merely contains the leaf — including the wrapper shell
    that invoked the probe, or an editor with the path open. The Windows branch
    already treats that name filter as load-bearing for exactly this reason;
    ``pkill -x`` cannot express "name is one of a family", so the matching is done
    here instead, against ``ps`` output.

    SIGTERM first so the browser can flush and exit cleanly, SIGKILL only for
    whatever ignores it. Returns the number of processes signalled.
    """
    me = os.getpid()

    def ps_field(spec):
        """pid -> field, for a ps format whose field is LAST on the line.

        Two separate calls rather than one ``pid=,comm=,args=``, because process
        names legitimately contain spaces: macOS runs ``Google Chrome Helper
        (Renderer)``. Splitting a combined line into three fields shredded that
        name into "Google" + leftovers, the name filter then rejected it, and the
        very helper processes this routine exists to reap survived. Keeping the
        variable-width field last means one split(None, 1) is always correct.
        """
        try:
            out = subprocess.run(
                ["ps", "-eo", spec],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=25,
            )
        except FileNotFoundError:
            # No ps: a distroless/slim image. Say so rather than reporting
            # success — a silent no-op here is how leaked browsers go unnoticed.
            raise QAError(
                "Cannot clean up browser processes: neither 'psutil' nor 'ps' is available.\n"
                "Install psutil (pip install psutil) so the probes can reap the browser "
                "processes they start, or the profile directory will be left behind."
            )
        found = {}
        for line in (out.stdout or b"").decode("utf-8", "replace").splitlines():
            parts = line.strip().split(None, 1)
            if len(parts) != 2:
                continue
            try:
                found[int(parts[0])] = parts[1].strip()
            except ValueError:
                continue
        return found

    cmdlines = ps_field("pid=,args=")
    names = ps_field("pid=,comm=")

    targets = []
    for pid, argv in cmdlines.items():
        if pid == me or leaf not in argv:
            continue
        if not _is_browser_process_name(os.path.basename(names.get(pid, ""))):
            continue
        targets.append(pid)

    # Resolved via getattr rather than named directly: signal.SIGKILL does not
    # exist on Windows, and a module-level reference to it would make this
    # function unimportable-in-practice there — including from tests — even
    # though only the POSIX branch ever calls it.
    sigkill = getattr(signal, "SIGKILL", signal.SIGTERM)
    for sig in (signal.SIGTERM, sigkill):
        alive = []
        for pid in targets:
            try:
                os.kill(pid, sig)
                alive.append(pid)
            except OSError:
                pass                      # already gone, or not ours to signal
        if not alive:
            break
        targets = alive
        time.sleep(0.5)
    return len(targets)


def shutdown(proc, profile_dir):
    """Kill EVERY browser process belonging to this run's profile dir, then drop the dir.

    ``proc.terminate()`` only kills the launcher, and even ``taskkill /T`` on it
    misses the GPU / utility / network / renderer processes a Chromium browser
    reparents (roughly eight per run) — they pile up holding debug ports and
    profile directories, which is what made repeat runs flaky. Every one of
    those subprocesses carries the unique ``--user-data-dir`` path on its
    command line, so matching on the profile leaf name catches the whole set
    regardless of parentage.
    """
    if not profile_dir:
        return
    leaf = os.path.basename(profile_dir)
    done = False

    if psutil is not None:
        try:
            # "Did not raise" is NOT success. If psutil is installed but every
            # proc.kill() hits AccessDenied, nothing died — and treating that as
            # done skipped BOTH the platform fallback and proc.terminate(), so a
            # full set of browser processes leaked while cleanup reported fine.
            done = _kill_with_psutil(leaf) > 0
        except Exception:
            done = False

    if not done:
        try:
            if platform.system() == "Windows":
                _kill_windows_powershell(leaf)
            else:
                _kill_posix(leaf)
            done = True
        except QAError as exc:
            # No usable way to reap processes on this box (no psutil, no ps).
            # Say it out loud: swallowing it means leaked browsers accumulate
            # invisibly, which is the failure this whole routine exists to stop.
            sys.stderr.write("{}\n".format(exc))
            done = False
        except Exception:
            done = False

    if not done and proc is not None:
        try:
            proc.terminate()
        except Exception:
            pass

    if proc is not None:
        try:
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    _remove_profile(profile_dir)
    _sweep_stale_profiles()


def _remove_profile(profile_dir, attempts=6):
    """Delete a run's profile directory, retrying while the OS releases its locks.

    Windows in particular releases a browser profile's file handles LAZILY after the
    process exits, so a single ``rmtree(..., ignore_errors=True)`` routinely loses that
    race and returns silently, leaving the entire profile (hundreds of MB) behind. Doing
    that once per run is how thousands of stale profiles accumulate in the temp
    directory. Retry with backoff so the normal race is simply won.
    """
    if not profile_dir:
        return
    for attempt in range(attempts):
        time.sleep(0.3 * (attempt + 1))
        try:
            shutil.rmtree(profile_dir)
            return
        except FileNotFoundError:
            return
        except Exception:
            continue
    shutil.rmtree(profile_dir, ignore_errors=True)


#: exactly the shape ``_profile_dir()`` creates: qa-cdp-{tag}-{pid}-{port}.
#: Anchored, because the sweep DELETES RECURSIVELY and must never match a
#: directory this launcher did not create. A ``qa-cdp-*`` glob happily matched
#: (and destroyed) a user's own ``qa-cdp-my-important-notes``, and even a bare
#: ``qa-cdp-``. Nothing outside this pattern is ours to remove.
_PROFILE_DIR_RE = re.compile(r"qa-cdp-[A-Za-z0-9_]+-(\d+)-\d+$")


def _pid_alive(pid):
    """Is this PID currently running? Used to protect a LIVE run's profile."""
    if pid <= 0:
        return False
    try:
        if psutil is not None:
            return psutil.pid_exists(pid)
        if platform.system() == "Windows":
            out = subprocess.run(
                ["tasklist", "/FI", "PID eq {}".format(pid), "/NH"],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=15,
            )
            return str(pid).encode() in (out.stdout or b"")
        os.kill(pid, 0)          # signal 0 = existence check, sends nothing
        return True
    except OSError as exc:
        # EPERM means it exists but belongs to another user — still alive.
        return exc.errno == errno.EPERM
    except Exception:
        # Unknown => assume alive. Being wrong here only leaves a stale directory;
        # being wrong the other way deletes a running session's profile.
        return True


def _sweep_stale_profiles(max_age_hours=6):
    """Collect profiles orphaned by runs that died before their cleanup could run.

    A ``finally:`` block does not execute if the process is hard-killed (Ctrl-C, a
    crashed agent, a harness timeout), so those profiles would otherwise live forever.

    Two guards, in order of authority:
      * the directory name must match ``_PROFILE_DIR_RE`` exactly — we only ever
        delete directories this launcher itself created;
      * the PID embedded in that name must be dead. The PID is a FACT about
        whether the owning run still exists; mtime is only a heuristic, and it is
        wrong in exactly the cases that hurt — an idle ``--headed`` session left
        open, or a laptop suspended mid-run, both look "old" while very much alive.

    The age check stays on as a backstop for the one case the PID cannot cover:
    a recycled PID now belonging to some unrelated process.
    """
    try:
        base = os.environ.get("TEMP") or os.environ.get("TMPDIR") or tempfile.gettempdir()
        cutoff = time.time() - max_age_hours * 3600
        for name in os.listdir(base):
            match = _PROFILE_DIR_RE.fullmatch(name)
            if not match:
                continue
            path = os.path.join(base, name)
            try:
                if not os.path.isdir(path):
                    continue
                if _pid_alive(int(match.group(1))):
                    continue
                if os.path.getmtime(path) >= cutoff:
                    continue
                shutil.rmtree(path, ignore_errors=True)
            except Exception:
                pass
    except Exception:
        pass


# --------------------------------------------------------------------------
# CDP handshake + client
# --------------------------------------------------------------------------

def wait_for_ws(port, tries=60, delay=0.2, proc=None):
    """Poll ``/json`` until the browser exposes a page target; return its ws URL.

    Pass ``proc`` so a browser that died during startup is reported AS a dead
    browser. Its stdout/stderr go to DEVNULL, so the reason is already lost;
    without this poll the user then waits out the full timeout and is told the
    "CDP endpoint was never exposed", which points at the protocol rather than at
    the process that never came up (a bad --user-data-dir, a missing shared
    library, a sandbox refusal). The exit code is the one clue left — surface it.
    """
    for _ in range(tries):
        if proc is not None:
            code = proc.poll()
            if code is not None:
                raise QAError(
                    "The browser exited with code {} before exposing its debugging port.\n"
                    "Common causes: a profile directory that is not writable, a missing "
                    "system library, or sandboxing refused in a container (try running the "
                    "probe as a non-root user, or set CLAUDE_QA_BROWSER to a different "
                    "Chromium-family binary).".format(code)
                )
        try:
            with urllib.request.urlopen("http://127.0.0.1:{}/json".format(port), timeout=2) as resp:
                tabs = json.load(resp)
            pages = [t for t in tabs if t.get("type") == "page" and t.get("webSocketDebuggerUrl")]
            if pages:
                return pages[0]["webSocketDebuggerUrl"]
        except Exception:
            pass
        time.sleep(delay)
    return None


class CDP:
    """A minimal Chrome-DevTools-Protocol client over one page target."""

    def __init__(self, ws, event_handler=None):
        self.ws = ws
        self.ws.settimeout(1.0)
        self._id = 0
        self.event_handler = event_handler

    # -- plumbing ---------------------------------------------------------
    def send(self, method, params=None, timeout=20):
        """Issue a CDP command. Returns the result dict, or a dict carrying
        ``__error__`` / ``__timeout__`` so callers never have to try/except."""
        self._id += 1
        msg_id = self._id
        try:
            self.ws.send(json.dumps({"id": msg_id, "method": method, "params": params or {}}))
        except Exception as exc:
            return {"__error__": {"message": str(exc)}}
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                msg = json.loads(self.ws.recv())
            except websocket.WebSocketTimeoutException:
                continue
            except Exception as exc:
                return {"__error__": {"message": str(exc)}}
            if msg.get("id") == msg_id:
                if "error" in msg:
                    return {"__error__": msg["error"]}
                return msg.get("result", {})
            self._dispatch(msg)
        return {"__timeout__": True}

    def _dispatch(self, msg):
        if self.event_handler is not None:
            try:
                self.event_handler(msg)
            except Exception:
                pass

    def js(self, expression, timeout=20):
        """Evaluate JS in the page and return its value (None on any failure)."""
        r = self.send("Runtime.evaluate", {
            "expression": expression, "returnByValue": True, "awaitPromise": True,
        }, timeout=timeout)
        if "__error__" in r or "__timeout__" in r:
            return None
        if r.get("exceptionDetails"):
            return None
        return r.get("result", {}).get("value")

    def drain(self, seconds):
        """Pump events for a while without issuing a command."""
        deadline = time.time() + seconds
        while time.time() < deadline:
            try:
                msg = json.loads(self.ws.recv())
            except websocket.WebSocketTimeoutException:
                continue
            except Exception:
                break
            self._dispatch(msg)

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass


def connect(ws_url, event_handler=None, timeout=30):
    """Open the CDP websocket and return a ``CDP`` client."""
    _require_websocket()
    if not ws_url:
        raise QAError(
            "Could not reach the browser's CDP endpoint.\n"
            "The browser started but never exposed a debuggable page. Try again, "
            "or run with CLAUDE_QA_BROWSER pointed at a different Chromium-family browser."
        )
    try:
        ws = websocket.create_connection(ws_url, timeout=timeout)
    except Exception as exc:
        raise QAError("Could not open the CDP websocket ({}): {}".format(ws_url, exc))
    return CDP(ws, event_handler=event_handler)


# --------------------------------------------------------------------------
# urls, paths, output
# --------------------------------------------------------------------------

def normalize_path(raw):
    """Accept a page path with or without a leading slash.

    A fully-qualified URL is passed through UNTOUCHED. Every script normalises its
    page argument before calling ``resolve_url``, so without this the full-URL
    branch in ``resolve_url`` could never be reached from the command line:
    ``https://example.com/a`` became ``/https://example.com/a`` and then
    ``http://localhost:3000/https://example.com/a``, quietly probing a 404 on the
    wrong host instead of the site the user named.

    Also recovers a single-segment leading-slash argument that Git Bash's MSYS
    path conversion rewrote into ``C:/Program Files/Git/<page>`` — that mangling
    otherwise produces a bogus ``http://host:portC:/...`` URL and the browser
    answers "Cannot navigate to invalid URL".
    """
    if not raw or raw in ("home", "index", "root", "/"):
        return "/"
    if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://", str(raw).strip()):
        return str(raw).strip()
    p = str(raw).replace("\\", "/")
    if ":" in p and re.search(r"(^|/)Git(/|$)", p):
        p = p.rstrip("/")
        # A bare '/' becomes the Git install root itself ('C:/Program Files/Git'),
        # which carries no page segment — that means the caller meant the site root.
        if re.search(r"(^|/)Git$", p):
            return "/"
        p = p.split("/Git/", 1)[1] if "/Git/" in p else p.split("/")[-1]
    return "/" + p.lstrip("/")


def resolve_url(config, path):
    """Join the configured web base URL with a page path.

    A page argument that is already a full URL wins outright — the same scheme
    test ``normalize_path`` uses, so the two agree and a URL survives the round
    trip both scripts make (normalize, then resolve).
    """
    if path and re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://", str(path).strip()):
        return str(path).strip()
    base = (config.get("web.url") if config else None) or DEFAULTS["web"]["url"]
    return base.rstrip("/") + normalize_path(path)


def slug(path):
    """A filename-safe slug for a page path ('/a/b?x=1' -> 'a_b')."""
    s = str(path).strip("/").split("?")[0].replace("/", "_")
    s = re.sub(r"[^A-Za-z0-9._-]+", "-", s)
    return s or "home"


def output_path(config, *parts, is_dir=False):
    """Resolve a path under the configured output dir, creating parent dirs.

    ``is_dir=True`` means the resolved path is itself a directory to create;
    otherwise it is treated as a FILE and only its parent is created.

    The caller must say which. Inferring it from "does this end in an extension?"
    silently did the wrong thing for any file without one: ``page_shot.py / out``
    created a DIRECTORY named ``out`` and then died with a raw PermissionError
    trying to open it for writing, leaving the junk directory behind.
    """
    is_dir = bool(is_dir)
    out_dir = (config.get("output.dir") if config else None) or DEFAULTS["output"]["dir"]
    if not isinstance(out_dir, str):
        raise QAError("Config key 'output.dir' must be a directory path string, "
                      "but it is {} ({!r}).".format(type(out_dir).__name__, out_dir))
    out_dir = os.path.expanduser(os.path.expandvars(out_dir))
    if not os.path.isabs(out_dir):
        root = getattr(config, "root", None) or os.getcwd()
        out_dir = os.path.join(root, out_dir)
    full = os.path.normpath(os.path.join(out_dir, *[str(p) for p in parts]))
    target = full if is_dir else (os.path.dirname(full) or ".")
    try:
        os.makedirs(target, exist_ok=True)
    except OSError as exc:
        raise QAError("Could not create output directory {}: {}".format(target, exc))
    return full


def check_reachable(url, timeout=5):
    """Return ``(ok, message)`` for a quick HTTP liveness check of the app."""
    req = urllib.request.Request(url, headers={"User-Agent": "qa-probe/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return True, "HTTP {}".format(resp.status)
    except urllib.error.HTTPError as exc:
        # A 404/500 still proves something is listening; the probe can proceed.
        return True, "HTTP {}".format(exc.code)
    except Exception as exc:
        return False, str(exc)


# --------------------------------------------------------------------------
# generic identity / auth shim
# --------------------------------------------------------------------------

_PLACEHOLDER = re.compile(r"\{([A-Za-z0-9_]+)\}")


def _render(template, identity):
    """Substitute ``{key}`` placeholders from ``identity``.

    Returns None when the template needs a key the caller did not supply, so
    the entry is skipped rather than written as a literal '{userId}'.
    """
    if not isinstance(template, str):
        return json.dumps(template)
    missing = []

    def sub(m):
        key = m.group(1)
        value = identity.get(key)
        if value is None or value == "":
            missing.append(key)
            return ""
        return str(value)

    rendered = _PLACEHOLDER.sub(sub, template)
    return None if missing else rendered


def apply_identity(cdp, config, identity):
    """Install a config-declared identity into the page BEFORE navigation.

    ``config`` may declare an ``auth`` block::

        "auth": {
          "localStorage": {"actingUserId": "{userId}", "activeTenantId": "{tenantId}"},
          "cookies":      {"session_hint": "{userId}"}
        }

    ``identity`` is a plain dict such as ``{"userId": "...", "tenantId": "..."}``.
    Values are substituted into the templates and the localStorage writes are
    registered with ``Page.addScriptToEvaluateOnNewDocument`` so they land before
    the app's first paint (setting them after load means the app has already
    booted anonymously and would need a reload).

    Returns a summary dict. If the config declares no auth shim, or the caller
    passed no identity, this is a no-op and the probes run anonymously.
    """
    summary = {"applied": False, "localStorage": [], "cookies": [], "skipped": []}
    identity = {k: v for k, v in (identity or {}).items() if v not in (None, "")}
    auth = (config.get("auth", {}) if config else {}) or {}
    if not auth or not identity:
        return summary

    ls_map = auth.get("localStorage") or {}
    statements = []
    for key, template in ls_map.items():
        value = _render(template, identity)
        if value is None:
            summary["skipped"].append("localStorage.{}".format(key))
            continue
        statements.append("localStorage.setItem({}, {});".format(json.dumps(key), json.dumps(value)))
        summary["localStorage"].append(key)

    if statements:
        script = "(() => { try { %s } catch (e) {} })();" % " ".join(statements)
        # Runs on every new document in this session, so it survives navigation.
        cdp.send("Page.addScriptToEvaluateOnNewDocument", {"source": script})
        # Also set it right now, for the document already open.
        cdp.js(script)
        summary["applied"] = True

    cookie_map = auth.get("cookies") or {}
    if cookie_map:
        base = (config.get("web.url") if config else None) or DEFAULTS["web"]["url"]
        for name, template in cookie_map.items():
            value = _render(template, identity)
            if value is None:
                summary["skipped"].append("cookie.{}".format(name))
                continue
            r = cdp.send("Network.setCookie", {"name": name, "value": value, "url": base})
            if "__error__" not in r:
                summary["cookies"].append(name)
                summary["applied"] = True
            else:
                summary["skipped"].append("cookie.{} (rejected)".format(name))

    return summary


def wait_ready(cdp, config, timeout=20.0, poll=0.25):
    """Block until the page looks rendered. Never fails hard — returns a bool.

    Uses ``readySelector`` from config when present; otherwise falls back to
    document readiness plus any rendered content, which works on any app.
    """
    selector = config.get("readySelector") if config else None
    if selector:
        check = (
            "(() => { const r = document.readyState;"
            " if (r !== 'complete' && r !== 'interactive') return false;"
            " if (document.querySelector(%s)) return true;"
            " return !!document.body && (document.body.innerText.trim().length > 0"
            " || document.body.childElementCount > 0); })()" % json.dumps(selector)
        )
    else:
        check = (
            "(() => { const r = document.readyState;"
            " if (r !== 'complete' && r !== 'interactive') return false;"
            " return !!document.body && (document.body.innerText.trim().length > 0"
            " || document.body.childElementCount > 0); })()"
        )
    deadline = time.time() + timeout
    while time.time() < deadline:
        if cdp.js(check, timeout=5):
            return True
        time.sleep(poll)
    return False


def die(message, code=2):
    """Print a human message on stderr and exit non-zero."""
    sys.stderr.write(str(message).rstrip() + "\n")
    raise SystemExit(code)
