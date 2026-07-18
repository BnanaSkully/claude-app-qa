#!/usr/bin/env python3
"""Responsive audit: one browser session captures each path at several viewport widths.

For eyeballing a set of pages across breakpoints. If you want MEASURED defects
rather than pictures, use viewport_probe.py instead.

Examples:
  python responsive_audit.py --widths 390,768,1280 /dashboard /settings
  python responsive_audit.py --widths 800,1280 --theme dark /reports

Writes <output.dir>/audit/<slug>_<width>.png and prints one JSON object.

Note: this uses a FREE debug port per run rather than a fixed one, so several
audits can run in parallel without attaching to each other's browser.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from qakit import cdp as A  # noqa: E402


def build_parser():
    p = argparse.ArgumentParser(
        prog="responsive_audit.py",
        description="Screenshot one or more pages at several viewport widths in a single browser session.",
        epilog=(
            "Output: <output.dir>/audit/<slug>_<width>.png\n"
            "--theme writes a 'theme' key to localStorage before navigating; apps that read a "
            "different key can set auth-style overrides in config instead.\n"
            "Config: .claude/qa.json. Env: CLAUDE_QA_WEB_URL, CLAUDE_QA_OUTPUT_DIR, CLAUDE_QA_BROWSER."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("paths", nargs="+", help="one or more page paths to capture")
    p.add_argument("--widths", default="390,768,1280",
                   help="comma-separated CSS widths (default: 390,768,1280)")
    p.add_argument("--height", type=int, default=1400, help="viewport height (default: 1400)")
    p.add_argument("--theme", default=None,
                   help="optional value written to localStorage['theme'] before capture")
    p.add_argument("--as-user", dest="as_user", default=None,
                   help="identity value substituted into {userId} in the config auth shim")
    p.add_argument("--as-tenant", dest="as_tenant", default=None,
                   help="identity value substituted into {tenantId} in the config auth shim")
    p.add_argument("--mobile-below", type=int, default=760,
                   help="widths below this emulate a touch device (default: 760)")
    p.add_argument("--settle", type=float, default=1.6,
                   help="seconds to wait after load before capturing (default: 1.6)")
    p.add_argument("--headed", action="store_true", help="run with a visible browser window")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        config = A.load_config()
    except A.QAError as exc:
        A.die(exc)

    try:
        widths = [int(x) for x in args.widths.split(",") if x.strip()]
    except ValueError:
        A.die("--widths must be a comma-separated list of integers, e.g. 390,768,1280")
    if not widths:
        A.die("--widths produced no widths")

    paths = [A.normalize_path(p) for p in args.paths]
    base_url = A.resolve_url(config, "/")

    ok, why = A.check_reachable(base_url)
    if not ok:
        A.die(
            "Cannot reach the app at {}\n  {}\n"
            "Start the app, or point the probes elsewhere with CLAUDE_QA_WEB_URL=<url> "
            "or a 'web.url' in .claude/qa.json.".format(base_url, why),
            code=3,
        )

    try:
        audit_dir = A.output_path(config, "audit")
    except A.QAError as exc:
        A.die(exc)

    identity = {"userId": args.as_user, "tenantId": args.as_tenant}
    payload = {
        "widths": widths, "paths": paths, "theme": args.theme,
        "output_dir": audit_dir, "shots": [], "errors": [],
    }

    proc = profile = None
    try:
        proc, profile, port = A.launch(
            config, headless=not args.headed, window_size=(1500, 1500), tag="audit")
    except A.QAError as exc:
        A.die(exc)

    try:
        cdp = A.connect(A.wait_for_ws(port))
        cdp.send("Page.enable")
        cdp.send("Runtime.enable")

        cdp.send("Page.navigate", {"url": base_url})
        A.wait_ready(cdp, config, timeout=24)
        payload["auth_shim"] = A.apply_identity(cdp, config, identity)
        if args.theme:
            script = "(() => { try { localStorage.setItem('theme', %s); } catch (e) {} })();" % json.dumps(args.theme)
            cdp.send("Page.addScriptToEvaluateOnNewDocument", {"source": script})
            cdp.js(script)

        for path in paths:
            url = A.resolve_url(config, path)
            for w in widths:
                cdp.send("Emulation.setDeviceMetricsOverride", {
                    "width": w, "height": args.height, "deviceScaleFactor": 1,
                    "mobile": w < args.mobile_below,
                })
                cdp.send("Page.navigate", {"url": url})
                A.wait_ready(cdp, config, timeout=18)
                time.sleep(args.settle)

                out = os.path.join(audit_dir, "{}_{}.png".format(A.slug(path), w))
                shot = cdp.send("Page.captureScreenshot",
                                {"format": "png", "captureBeyondViewport": False}, timeout=60)
                if "data" not in shot:
                    payload["errors"].append({"path": path, "width": w, "error": "no image data"})
                    sys.stderr.write("FAILED {} @ {}\n".format(path, w))
                    continue
                with open(out, "wb") as fh:
                    fh.write(base64.b64decode(shot["data"]))
                payload["shots"].append({"path": path, "width": w, "file": out,
                                         "bytes": os.path.getsize(out)})
                sys.stderr.write("saved {}\n".format(out))
    except A.QAError as exc:
        A.shutdown(proc, profile)
        A.die(exc)
    finally:
        A.shutdown(proc, profile)

    print(json.dumps(payload, indent=2))
    return 0 if not payload["errors"] else 1


if __name__ == "__main__":
    sys.exit(main())
