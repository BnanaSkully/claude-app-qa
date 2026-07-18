#!/usr/bin/env python3
"""Screenshot one page of the app, optionally acting as a given identity.

Anonymous by default. Pass --as-user / --as-tenant to have the config's auth
shim (`auth.localStorage` / `auth.cookies` in .claude/qa.json) installed
before the page loads, so the app boots as that identity on first paint.

Examples:
  python page_shot.py /dashboard out.png
  python page_shot.py /reports out.png --as-user 42 --as-tenant acme --full-page
  python page_shot.py / phone.png --width 390 --height 844

Prints one JSON object to stdout describing what was written.
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


#: exit status meaning "the run did not produce trustworthy evidence" — distinct
#: from 0 (clean) and from the usage/reachability codes die() uses (2 and 3).
MEASUREMENT_FAILED_EXIT = 4


def build_parser():
    p = argparse.ArgumentParser(
        prog="page_shot.py",
        description="Screenshot one page of the running app, anonymously or as an identity.",
        epilog=(
            "By default captures the viewport at --width x --height. With --full-page it "
            "captures the whole document height instead.\n"
            "Config: .claude/qa.json. Env: CLAUDE_QA_WEB_URL, CLAUDE_QA_OUTPUT_DIR, CLAUDE_QA_BROWSER."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("path", help="page path, with or without a leading slash (e.g. /dashboard)")
    p.add_argument("out", help="output PNG file (relative paths resolve against the output dir)")
    p.add_argument("--as-user", dest="as_user", default=None,
                   help="identity value substituted into {userId} in the config auth shim")
    p.add_argument("--as-tenant", dest="as_tenant", default=None,
                   help="identity value substituted into {tenantId} in the config auth shim")
    p.add_argument("--width", type=int, default=1280, help="viewport width in CSS px (default: 1280)")
    p.add_argument("--height", type=int, default=1400, help="viewport height in CSS px (default: 1400)")
    p.add_argument("--scale", type=float, default=1.0,
                   help="deviceScaleFactor, i.e. modelled display scaling (default: 1.0)")
    p.add_argument("--mobile", action="store_true", help="emulate a touch/mobile device")
    p.add_argument("--full-page", action="store_true",
                   help="capture the entire document height, not just the viewport")
    p.add_argument("--settle", type=float, default=2.0,
                   help="seconds to wait after load before capturing (default: 2.0)")
    p.add_argument("--headed", action="store_true", help="run with a visible browser window")
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

    out = args.out
    # Validate the extension BEFORE anything is created on disk. Without this,
    # `page_shot.py / noextension` had output_path() infer "no extension, so it
    # must be a directory", create a DIRECTORY named noextension, and then die
    # with a raw PermissionError opening it for writing — leaving the junk
    # directory behind on a run that produced nothing.
    if not out.lower().endswith(".png"):
        A.die("The output file must end in .png — got {!r}.\n"
              "Example: python page_shot.py /dashboard dashboard.png".format(out))
    if os.path.isabs(out):
        parent = os.path.dirname(out)
        if parent:
            try:
                os.makedirs(parent, exist_ok=True)
            except OSError as exc:
                A.die("Could not create output directory {}: {}".format(parent, exc))
    else:
        try:
            out = A.output_path(config, out)
        except A.QAError as exc:
            A.die(exc)

    ok, why = A.check_reachable(base_url)
    if not ok:
        A.die(
            "Cannot reach the app at {}\n  {}\n"
            "Start the app, or point the probes elsewhere with CLAUDE_QA_WEB_URL=<url> "
            "or a 'web.url' in .claude/qa.json.".format(base_url, why),
            code=3,
        )

    identity = {"userId": args.as_user, "tenantId": args.as_tenant}
    proc = profile = None
    try:
        proc, profile, port = A.launch(
            config, headless=not args.headed,
            window_size=(max(args.width, 800), max(args.height, 600)), tag="pageshot")
    except A.QAError as exc:
        A.die(exc)

    payload = {}
    try:
        cdp = A.connect(A.wait_for_ws(port, proc=proc))
        cdp.send("Page.enable")
        cdp.send("Runtime.enable")
        cdp.send("Emulation.setDeviceMetricsOverride", {
            "width": args.width, "height": args.height,
            "deviceScaleFactor": args.scale, "mobile": bool(args.mobile),
        })

        auth_summary = {"applied": False}
        if any(identity.values()):
            # The identity must be installed on the app's own origin, before the
            # target page boots — otherwise the app has already rendered anonymously.
            cdp.send("Page.navigate", {"url": base_url})
            A.wait_ready(cdp, config, timeout=24)
            auth_summary = A.apply_identity(cdp, config, identity)

        cdp.send("Page.navigate", {"url": url})
        rendered = A.wait_ready(cdp, config, timeout=24)
        time.sleep(args.settle)

        shot = cdp.send("Page.captureScreenshot", {
            "format": "png", "captureBeyondViewport": bool(args.full_page),
        }, timeout=60)
        if "data" not in shot:
            raise A.QAError("Screenshot failed: {}".format(
                shot.get("__error__") or "browser returned no image data"))
        with open(out, "wb") as fh:
            fh.write(base64.b64decode(shot["data"]))

        payload = {
            "saved": out,
            "bytes": os.path.getsize(out),
            "path": path,
            "url": url,
            "viewport": {"width": args.width, "height": args.height,
                         "scale": args.scale, "mobile": bool(args.mobile)},
            "full_page": bool(args.full_page),
            "acting_as": {k: v for k, v in identity.items() if v},
            "auth_shim": auth_summary,
            "rendered": rendered,
        }
    except A.QAError as exc:
        # No shutdown() here: die() raises SystemExit, so the finally: below runs
        # and does the full kill-plus-sweep. Doing it in both places ran the
        # whole teardown twice on every error path.
        A.die(exc)
    finally:
        A.shutdown(proc, profile)

    print(json.dumps(payload, indent=2))
    # A screenshot of a page that never finished rendering is not evidence.
    if payload.get("rendered") is False:
        sys.stderr.write(
            "MEASUREMENT UNRELIABLE: the page never reached a ready state; "
            "{} may show a partly-loaded page.\n".format(payload.get("saved")))
        return MEASUREMENT_FAILED_EXIT
    return 0


if __name__ == "__main__":
    sys.exit(main())
