#!/usr/bin/env python3
"""UI click-through crawler for bug-hunting.

Drives a headless Chromium-family browser over CDP, visits one page, then clicks
EVERY visible control (buttons, links, tabs, menu items, summaries), opening and
safely CANCELLING any confirm dialog, while capturing JS console errors, uncaught
exceptions, failed network requests (>=400) and on-screen error notices.

SAFE by default: it does NOT click controls whose label looks destructive
(delete / remove / unmap / unlink / wipe / reset / discard / log out). It still
clicks everything else and cancels any confirm dialog those open, so data is
preserved. Pass --all to click those too — never do that against production.

It mutates whatever data the app is pointed at, by design (it approves, edits,
submits). Run it against a disposable environment and reseed afterwards.

Prints one JSON object to stdout; human chatter goes to stderr.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from qakit import cdp as A  # noqa: E402


NOISE = re.compile(r"favicon|hot-update|__nextjs|\.map(\?|$)|/_next/static|/_next/webpack|/static/chunks")
DESTRUCTIVE = re.compile(
    r"log\s?out|sign\s?out|\bdelete\b|\bremove\b|unmap|unlink|\bwipe\b|\breset\b|\bdiscard\b|"
    r"\bdeactivate\b|\bcancel subscription\b|\bpurge\b",
    re.I,
)

ENUM_JS = r"""
(() => {
  const sel = 'button, a[href], [role="button"], [role="tab"], [role="menuitem"], summary';
  const out = []; let i = 0;
  for (const el of document.querySelectorAll(sel)) {
    const r = el.getBoundingClientRect();
    if (el.offsetParent === null || r.width < 1 || r.height < 1) continue;
    if (el.disabled || el.getAttribute('aria-disabled') === 'true') continue;
    el.setAttribute('data-qaprobe', i);
    const text = (el.innerText || el.textContent || el.getAttribute('aria-label') || '')
                 .trim().replace(/\s+/g, ' ').slice(0, 60);
    out.push({idx: i, tag: el.tagName.toLowerCase(), text, href: el.getAttribute('href') || ''});
    i++;
  }
  return out;
})()
"""

DIALOG_OPEN_JS = r"""
(() => { const d = document.querySelector('[role="dialog"], [role="alertdialog"], dialog[open], [class*="ialog"], [class*="modal"], [class*="Modal"]');
         return !!(d && d.offsetParent !== null); })()
"""

DISMISS_JS = r"""
(() => {
  const d = document.querySelector('[role="dialog"], [role="alertdialog"], dialog[open], [class*="ialog"], [class*="modal"], [class*="Modal"]');
  if (!d || d.offsetParent === null) return 'none';
  const btns = [...d.querySelectorAll('button, [role="button"]')];
  const c = btns.find(b => /cancel|close|back|not now|keep|dismiss|no thanks|×|✕|✗/i
              .test((b.innerText || b.textContent || b.getAttribute('aria-label') || '')));
  if (c) { c.click(); return 'cancelled'; }
  return 'noCancelBtn';
})()
"""

NOTICE_JS = r"""
(() => {
  const els = [...document.querySelectorAll('[role="alert"], [role="status"], .notice, .alert, .error, [class*="error"], [class*="Error"]')]
              .filter(e => e.offsetParent !== null && (e.innerText || '').trim());
  const scary = /failed to fetch|something went wrong|unexpected error|traceback|internal server error|cannot read prop|undefined is not|is not a function/i;
  const seen = new Set(), notices = [];
  for (const e of els) {
    const t = (e.innerText || '').trim().slice(0, 140);
    if (t && !seen.has(t)) { seen.add(t); notices.push(t); }
    if (notices.length >= 6) break;
  }
  return { notices, scary: scary.test(document.body.innerText) };
})()
"""


def build_parser():
    p = argparse.ArgumentParser(
        prog="ui_crawl.py",
        description="Click every visible control on one page and report the faults that fall out.",
        epilog=(
            "Config: .claude/qa.json (searched upwards from cwd). "
            "Env overrides: CLAUDE_QA_WEB_URL, CLAUDE_QA_OUTPUT_DIR, CLAUDE_QA_BROWSER.\n"
            "Example: python ui_crawl.py /settings --as-user 42 --as-tenant acme"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("path", nargs="?", default="/",
                   help="page path to crawl, with or without a leading slash (default: /)")
    p.add_argument("--as-user", dest="as_user", default=None,
                   help="identity value substituted into {userId} in the config auth shim")
    p.add_argument("--as-tenant", dest="as_tenant", default=None,
                   help="identity value substituted into {tenantId} in the config auth shim")
    p.add_argument("--all", action="store_true",
                   help="ALSO click destructive-looking controls (disposable environments only)")
    p.add_argument("--max-clicks", type=int, default=120,
                   help="stop after this many controls (default: 120)")
    p.add_argument("--wall-seconds", type=int, default=240,
                   help="stop after this many seconds (default: 240)")
    p.add_argument("--json-out", default=None,
                   help="also write the JSON report to this file")
    p.add_argument("--headed", action="store_true",
                   help="run with a visible browser window (debugging)")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        config = A.load_config()
    except A.QAError as exc:
        A.die(exc)

    path = A.normalize_path(args.path)
    url = A.resolve_url(config, path)
    base_url = A.resolve_url(config, "/")

    ok, why = A.check_reachable(base_url)
    if not ok:
        A.die(
            "Cannot reach the app at {}\n  {}\n"
            "Start the app, or point the probes elsewhere with CLAUDE_QA_WEB_URL=<url> "
            "or a 'web.url' in .claude/qa.json.".format(base_url, why),
            code=3,
        )

    console, exceptions, net, log = [], [], [], []

    def record_event(msg):
        m = msg.get("method")
        p = msg.get("params", {}) or {}
        if m == "Runtime.consoleAPICalled":
            if p.get("type") in ("error", "warning", "assert"):
                txt = " ".join(str(a.get("value", a.get("description", ""))) for a in p.get("args", []))
                console.append("[{}] {}".format(p.get("type"), txt)[:300])
        elif m == "Runtime.exceptionThrown":
            d = p.get("exceptionDetails", {}) or {}
            txt = (d.get("text", "") + " " + ((d.get("exception") or {}).get("description", ""))).strip()
            if txt:
                exceptions.append(txt[:300])
        elif m == "Network.responseReceived":
            r = p.get("response", {}) or {}
            st = r.get("status", 0)
            u = r.get("url", "")
            if st >= 400 and not NOISE.search(u):
                net.append("{} {}".format(st, u[:200]))
        elif m == "Network.loadingFailed":
            et = p.get("errorText", "") or ""
            if et and "ERR_ABORTED" not in et and not p.get("canceled"):
                net.append("failed {}".format(et))
        elif m == "Log.entryAdded":
            e = p.get("entry", {}) or {}
            if e.get("level") == "error" and not NOISE.search(e.get("url", "") or ""):
                log.append((e.get("text", "") or "")[:240])

    proc = profile = None
    try:
        proc, profile, port = A.launch(
            config, headless=not args.headed, window_size=(1400, 1600), tag="uicrawl")
    except A.QAError as exc:
        A.die(exc)

    report = {}
    try:
        ws_url = A.wait_for_ws(port)
        cdp = A.connect(ws_url, event_handler=record_event)

        cdp.send("Page.enable")
        cdp.send("Runtime.enable")
        cdp.send("Network.enable")
        cdp.send("Log.enable")
        cdp.send("Emulation.setDeviceMetricsOverride",
                 {"width": 1400, "height": 1600, "deviceScaleFactor": 1, "mobile": False})

        # Land on the origin first: the auth shim must be installed against the
        # app's own origin before the target page boots.
        cdp.send("Page.navigate", {"url": base_url})
        A.wait_ready(cdp, config, timeout=20)
        identity = {"userId": args.as_user, "tenantId": args.as_tenant}
        auth_summary = A.apply_identity(cdp, config, identity)

        def goto(target_url):
            cdp.send("Page.navigate", {"url": target_url})
            A.wait_ready(cdp, config, timeout=20)
            cdp.drain(1.2)

        goto(url)

        load_problems = {
            "console": list(console), "exceptions": list(exceptions),
            "network": list(net), "log": list(log),
        }
        del console[:], exceptions[:], net[:], log[:]

        results, seen_sigs = [], set()
        notices_observed, requests_seen = [], []
        start = time.time()
        while len(results) < args.max_clicks and time.time() - start < args.wall_seconds:
            controls = cdp.js(ENUM_JS) or []
            target = None
            for c in controls:
                sig = "{}|{}|{}".format(c["tag"], c["text"], c["href"])
                if sig in seen_sigs:
                    continue
                if not c["text"] and not c["href"]:
                    seen_sigs.add(sig)
                    continue
                if DESTRUCTIVE.search(c["text"]) and not args.all:
                    seen_sigs.add(sig)
                    results.append({"text": c["text"], "tag": c["tag"], "skipped": "destructive"})
                    continue
                target = c
                target["sig"] = sig
                break
            if target is None:
                break
            seen_sigs.add(target["sig"])

            b_con, b_exc, b_net, b_log = len(console), len(exceptions), len(net), len(log)
            path_before = cdp.js("location.pathname")
            clicked_ok = cdp.js(
                "(() => {{ const el = document.querySelector('[data-qaprobe=\"{}\"]');"
                " if (!el) return false; el.click(); return true; }})()".format(target["idx"])
            )
            cdp.drain(0.7)

            notice = cdp.js(NOTICE_JS) or {"notices": [], "scary": False}
            dialog = bool(cdp.js(DIALOG_OPEN_JS))
            path_after = cdp.js("location.pathname")
            new_net = net[b_net:]
            res = {
                "text": target["text"], "tag": target["tag"], "href": target["href"],
                "clicked": bool(clicked_ok),
                "console_errors": console[b_con:], "exceptions": exceptions[b_exc:],
                "network_errors": new_net, "log_errors": log[b_log:],
                "error_notices": notice.get("notices", []), "scary_text": notice.get("scary", False),
                "opened_dialog": dialog,
                "navigated_to": path_after if path_after != path_before else None,
            }
            # HARD problem = a genuine fault. An on-screen notice or a 4xx is NOT
            # auto-flagged (apps show duplicate warnings, validation messages and
            # permission refusals by design) — those are surfaced separately for
            # a human or agent to judge.
            has_5xx = any(
                n.split(" ", 1)[0].isdigit() and int(n.split(" ", 1)[0]) >= 500 for n in new_net
            )
            res["problem"] = bool(
                res["exceptions"] or res["log_errors"] or res["scary_text"] or has_5xx
                or any("[error]" in c.lower() for c in res["console_errors"])
            )
            for n in res["error_notices"]:
                if n not in notices_observed:
                    notices_observed.append(n)
            for n in new_net:
                if n not in requests_seen:
                    requests_seen.append(n)
            results.append(res)

            if dialog:
                cdp.js(DISMISS_JS)
                for kind in ("keyDown", "keyUp"):
                    cdp.send("Input.dispatchKeyEvent", {
                        "type": kind, "key": "Escape", "code": "Escape",
                        "windowsVirtualKeyCode": 27,
                    })
                cdp.drain(0.3)
                if cdp.js(DIALOG_OPEN_JS):      # stuck dialog — hard reset
                    goto(url)
            if path_after != path_before:       # a link took us elsewhere — return and keep crawling
                goto(url)

        problems = [r for r in results if r.get("problem")]
        report = {
            "path": path,
            "url": url,
            "acting_as": {k: v for k, v in identity.items() if v},
            "auth_shim": auth_summary,
            "clicked_count": len(results),
            "controls_with_problems": len(problems),
            "on_load_problems": load_problems,
            "problems": problems,
            "notices_observed": notices_observed,   # on-screen notices — judge, not auto-bugs
            "requests_ge_400": requests_seen,       # all >=400 responses seen — 4xx may be expected
            "clicked": [{"text": r.get("text"), "tag": r.get("tag"),
                         "navigated_to": r.get("navigated_to"),
                         "opened_dialog": r.get("opened_dialog"),
                         "skipped": r.get("skipped")} for r in results],
        }
    except A.QAError as exc:
        A.shutdown(proc, profile)
        A.die(exc)
    finally:
        A.shutdown(proc, profile)

    text = json.dumps(report, indent=2)
    print(text)
    if args.json_out:
        try:
            out = os.path.abspath(args.json_out)
            os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
            with open(out, "w", encoding="utf-8") as fh:
                fh.write(text)
            sys.stderr.write("wrote {}\n".format(out))
        except OSError as exc:
            sys.stderr.write("could not write --json-out: {}\n".format(exc))
    return 0


if __name__ == "__main__":
    sys.exit(main())
