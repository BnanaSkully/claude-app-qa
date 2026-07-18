#!/usr/bin/env python3
"""UI click-through crawler for bug-hunting.

Drives a headless Chromium-family browser over CDP, visits one page, then clicks
EVERY visible control (buttons, links, tabs, menu items, summaries), opening and
safely CANCELLING any confirm dialog, while capturing JS console errors, uncaught
exceptions, failed network requests (>=400) and on-screen error notices.

SAFE by default, in two tiers:

  * DESTRUCTIVE labels (delete / remove / archive / clear / void / revoke /
    approve / merge / restore / lock period / ...) are skipped. Pass --all to
    click them too — never do that against production.
  * IRREVERSIBLE-EXTERNAL labels (send / email / SMS / notify / invite /
    webhook / charge) are skipped ALWAYS, and --all does NOT override them.
    Reseeding undoes a deleted row; nothing un-sends an invoice to a real
    customer.

Any confirm dialog that does open is closed with Escape first, and only falls
back to clicking an exact-word "Cancel"/"Close" button that is itself checked
against both lists — so a dialog whose destructive action reads "Cancel plan"
is never mistaken for the way out.

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


#: exit status meaning "the run did not produce trustworthy evidence" — distinct
#: from 0 (clean) and from the usage/reachability codes die() uses (2 and 3).
MEASUREMENT_FAILED_EXIT = 4

NOISE = re.compile(r"favicon|hot-update|__nextjs|\.map(\?|$)|/_next/static|/_next/webpack|/static/chunks")

# Two separate categories, because they differ in whether --all may override them.
#
# DESTRUCTIVE = it changes or loses data in THIS system. Skipped by default;
# --all clicks them, on the understanding that a disposable environment can be
# reseeded afterwards.
#
# The list is deliberately broad. Every entry below was a label the crawler used
# to click happily: archiving a period, clearing a table, voiding a document,
# revoking a key, merging two records, restoring a backup over live data,
# finalising or locking an accounting period, posting to a ledger. "Reversible in
# principle" is not the test — the test is whether an unattended click can cost
# somebody an afternoon.
_DESTRUCTIVE_SRC = (
    r"log\s?out|sign\s?out|\bdelete\b|\bremove\b|unmap|unlink|\bwipe\b|\breset\b|\bdiscard\b|"
    r"\bdeactivate\b|\bpurge\b|\barchive\b|\bclear\b|\bempty\b|\berase\b|\btrash\b|\bvoid\b|"
    r"\brevoke\b|\bterminate\b|\bsuspend\b|\bdisable\b|\bban\b|\bunpublish\b|\bwithdraw\b|"
    r"\breject\b|\bdecline\b|\bmerge\b|\boverwrite\b|\brestore\b|\broll\s?back\b|"
    r"\bfinali[sz]e\b|\bfinalise[sd]?\b|\block\b|\bclose\s+(period|account|book)|"
    r"\bpost\s+to\b|\bpay\b|\brefund\b|\bapprove\b|\bpublish\b|\bdetach\b|\bdrop\b|"
    r"\bend\s+session\b|\bunsubscribe\b|"
    # "Cancel" followed by a noun is the DESTRUCTIVE action ("Cancel plan",
    # "Cancel my subscription", "Cancel order") — the opposite of a bare
    # "Cancel" button, which is the safe way out of a dialog. That distinction
    # is the whole of finding S1.
    r"\bcancell?(?:ing)?\s+(?:my\s+|the\s+|this\s+|your\s+)?\w"
)
DESTRUCTIVE = re.compile(_DESTRUCTIVE_SRC, re.I)

# IRREVERSIBLE_EXTERNAL = it reaches OUTSIDE this system — email, SMS, webhooks,
# a payment processor, a third-party publish. NEVER clicked, --all included.
#
# The distinction matters because --all's promise ("point it at a disposable
# environment and reseed afterwards") is simply false here. No snapshot, reseed
# or database restore un-sends an invoice to a real customer, and a test run that
# emails somebody's client list is not a QA finding, it is an incident. A staging
# environment pointed at a live SMTP or Stripe key is the normal case, not the
# exotic one.
_EXTERNAL_SRC = (
    r"\bsend\b|\bre-?send\b|\bemail\b|\be-mail\b|\bmail\s+(to|out)\b|\bsms\b|\btext\s+(customer|client)|"
    r"\bnotify\b|\bnotification\s+test\b|\binvite\b|\bbroadcast\b|\bblast\b|\bremind(er)?\b|"
    r"\bwebhook\b|\bshare\b|\bpublish\s+to\b|\bcharge\b|\bcapture\s+payment\b|\bpayout\b"
)
IRREVERSIBLE_EXTERNAL = re.compile(_EXTERNAL_SRC, re.I)

# A real visibility test, shared by every piece of injected JS below.
#
# The old test was `el.offsetParent !== null`, which is null BY SPEC for any
# position:fixed element. Every fixed element was therefore invisible to the
# crawler: a fixed "Delete all data" FAB was never enumerated, a fixed error
# toast (how most toast libraries render) was never reported, and — worst —
# DIALOG_OPEN_JS returned false for a visible fixed modal while its child buttons
# WERE enumerated, so the crawler never knew a modal was open, skipped dismissal
# entirely, and clicked straight into the modal's buttons.
_VISIBLE_FN = r"""
  const _st = el => { try { return getComputedStyle(el); } catch (e) { return null; } };
  const visible = el => {
    if (!el) return false;
    const r = el.getBoundingClientRect();
    if (r.width < 1 || r.height < 1) return false;
    const s = _st(el);
    return !!s && s.display !== 'none' && s.visibility !== 'hidden'
           && parseFloat(s.opacity || '1') > 0.01;
  };
"""

_DIALOG_SEL = ('[role="dialog"], [role="alertdialog"], dialog[open], '
               '[class*="ialog"], [class*="modal"], [class*="Modal"]')

ENUM_JS = r"""
(() => {
  %(visible)s
  const sel = 'button, a[href], [role="button"], [role="tab"], [role="menuitem"], summary';
  const out = []; let i = 0;
  for (const el of document.querySelectorAll(sel)) {
    if (!visible(el)) continue;
    if (el.disabled || el.getAttribute('aria-disabled') === 'true') continue;
    el.setAttribute('data-qaprobe', i);
    const text = (el.innerText || el.textContent || el.getAttribute('aria-label') || '')
                 .trim().replace(/\s+/g, ' ').slice(0, 60);
    out.push({idx: i, tag: el.tagName.toLowerCase(), text, href: el.getAttribute('href') || ''});
    i++;
  }
  return out;
})()
""" % {"visible": _VISIBLE_FN}

DIALOG_OPEN_JS = r"""
(() => {
  %(visible)s
  for (const d of document.querySelectorAll(%(sel)s)) { if (visible(d)) return true; }
  return false;
})()
""" % {"visible": _VISIBLE_FN, "sel": json.dumps(_DIALOG_SEL)}

# Chooses the button that gets us OUT of a dialog, and never one that acts.
#
# The old rule took the first button whose label merely CONTAINED
# cancel|close|back|keep|... . A dialog whose destructive action reads "Cancel
# plan", "Cancel subscription" or "Close account" matched that substring first,
# so the safety mechanism performed the destructive action itself — proved with a
# fixture whose "Cancel plan" button fired a request that duly arrived.
#
# Three rules replace it: an explicit close affordance, then a literal
# value="cancel", then an EXACT-WORD label match. No substring matching at any
# tier, and every candidate is run through both danger lists before it is
# clicked. When nothing qualifies, the dialog is left open and reported — a stuck
# dialog is a far better outcome than a wrong click.
DISMISS_JS = r"""
(() => {
  %(visible)s
  const DESTRUCTIVE = /%(destructive)s/i;
  const EXTERNAL = /%(external)s/i;
  const EXACT = /^(cancel|close|dismiss|go back|back|not now|no thanks|no|keep.*|×|✕|✗|x)$/i;
  let d = null;
  for (const cand of document.querySelectorAll(%(sel)s)) { if (visible(cand)) { d = cand; break; } }
  if (!d) return 'none';
  const label = b => (b.innerText || b.textContent || b.getAttribute('aria-label') || '')
                     .trim().replace(/\s+/g, ' ');
  const unsafe = b => { const t = label(b) + ' ' + (b.getAttribute('aria-label') || '');
                        return DESTRUCTIVE.test(t) || EXTERNAL.test(t); };
  const btns = [...d.querySelectorAll('button, [role="button"]')].filter(visible);
  let c = btns.find(b => /close/i.test(b.getAttribute('aria-label') || '') && !unsafe(b));
  if (!c) c = btns.find(b => (b.getAttribute('value') || '').toLowerCase() === 'cancel' && !unsafe(b));
  if (!c) c = btns.find(b => EXACT.test(label(b)) && !unsafe(b));
  if (c) { c.click(); return 'cancelled'; }
  return 'noSafeCancelBtn';
})()
""" % {"visible": _VISIBLE_FN, "sel": json.dumps(_DIALOG_SEL),
       "destructive": _DESTRUCTIVE_SRC, "external": _EXTERNAL_SRC}

NOTICE_JS = r"""
(() => {
  %(visible)s
  const els = [...document.querySelectorAll('[role="alert"], [role="status"], .notice, .alert, .error, [class*="error"], [class*="Error"]')]
              .filter(e => visible(e) && (e.innerText || '').trim());
  const scary = /failed to fetch|something went wrong|unexpected error|traceback|internal server error|cannot read prop|undefined is not|is not a function/i;
  const seen = new Set(), notices = [];
  for (const e of els) {
    const t = (e.innerText || '').trim().slice(0, 140);
    if (t && !seen.has(t)) { seen.add(t); notices.push(t); }
    if (notices.length >= 6) break;
  }
  return { notices, scary: scary.test(document.body.innerText) };
})()
""" % {"visible": _VISIBLE_FN}


def dismiss_dialog(cdp):
    """Close an open dialog the safest way available, and say how it went.

    Escape FIRST. It is the one dismissal that cannot trigger an action: no
    button is chosen, so no button can be the wrong one. Only if the dialog
    survives Escape do we fall back to picking something to click, and that
    choice is heavily constrained (see DISMISS_JS).

    The old order was the reverse — click first, Escape afterwards — so the risky
    path ran every time, even for the great majority of dialogs that Escape
    would have closed on its own.
    """
    for kind in ("keyDown", "keyUp"):
        cdp.send("Input.dispatchKeyEvent", {
            "type": kind, "key": "Escape", "code": "Escape", "windowsVirtualKeyCode": 27,
        })
    cdp.drain(0.3)
    if not cdp.js(DIALOG_OPEN_JS):
        return "escape"
    return cdp.js(DISMISS_JS) or "dismissFailed"


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
                   help="ALSO click destructive-looking controls (disposable environments only). "
                        "Does NOT unlock send/email/SMS/webhook/charge controls, which are never "
                        "clicked because no reseed can undo them.")
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
            "or a 'urls.web' in .claude/qa.json.".format(base_url, why),
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
        ws_url = A.wait_for_ws(port, proc=proc)
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

        # A page that never finished rendering makes every finding below
        # meaningless, so the answer is recorded rather than discarded.
        ready = {"value": True}

        def goto(target_url):
            cdp.send("Page.navigate", {"url": target_url})
            ready["value"] = bool(A.wait_ready(cdp, config, timeout=20)) and ready["value"]
            cdp.drain(1.2)

        goto(url)
        # Captured on FIRST navigation, before any clicking can move us. If an app
        # bounces this identity off the requested route, everything crawled below
        # belongs to a different page than the one being reported on.
        _landed = A.landed_url(cdp)

        load_problems = {
            "console": list(console), "exceptions": list(exceptions),
            "network": list(net), "log": list(log),
        }
        del console[:], exceptions[:], net[:], log[:]

        results, seen_sigs = [], set()
        notices_observed, requests_seen = [], []
        measurement_failed = None
        start = time.time()
        while len(results) < args.max_clicks and time.time() - start < args.wall_seconds:
            controls = cdp.js(ENUM_JS)
            # None means the EVALUATION failed (timeout, or the page threw);
            # [] means it ran and the page genuinely has no clickable controls.
            # Collapsing the two with `or []` ended the crawl at clicked_count 0
            # and exit 0 — a broken probe and a clean page reported identically,
            # which is the exact false-clean this tool exists to prevent.
            if controls is None:
                measurement_failed = "control enumeration failed (JS evaluation timed out or threw)"
                break
            target = None
            for c in controls:
                sig = "{}|{}|{}".format(c["tag"], c["text"], c["href"])
                if sig in seen_sigs:
                    continue
                if not c["text"] and not c["href"]:
                    seen_sigs.add(sig)
                    continue
                # --all deliberately does NOT reach the external category: a
                # reseed can undo a deleted row, but nothing recalls an email.
                if IRREVERSIBLE_EXTERNAL.search(c["text"]):
                    seen_sigs.add(sig)
                    results.append({"text": c["text"], "tag": c["tag"],
                                    "skipped": "irreversible-external"})
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
                res["dismissed_by"] = dismiss_dialog(cdp)
                cdp.drain(0.3)
                if cdp.js(DIALOG_OPEN_JS):      # stuck dialog — hard reset
                    goto(url)
            if path_after != path_before:       # a link took us elsewhere — return and keep crawling
                goto(url)

        problems = [r for r in results if r.get("problem")]
        report = {
            "path": path,
            "url": url,
            "landed_url": _landed,
            "redirected": bool(_landed and _landed.rstrip("/") != url.rstrip("/")),
            "acting_as": {k: v for k, v in identity.items() if v},
            "auth_shim": auth_summary,
            "ready": ready["value"],
            "measurement_failed": measurement_failed,
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
        # No shutdown() here: die() raises SystemExit, so the finally: below runs
        # and does the full kill-plus-sweep. Doing it in both places ran the
        # whole teardown twice on every error path.
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
    # Exit non-zero when the evidence cannot be trusted, so a caller that only
    # checks the status code is never told "clean" by a probe that failed.
    if report.get("measurement_failed") or report.get("ready") is False:
        sys.stderr.write("MEASUREMENT UNRELIABLE: {}\n".format(
            report.get("measurement_failed") or "page never reached a ready state"))
        return MEASUREMENT_FAILED_EXIT
    return 0


if __name__ == "__main__":
    sys.exit(main())
