#!/usr/bin/env python3
"""Responsive / visual defect PROBE — the measurement engine behind a visual hunt.

Drives one headless browser session across a full device x display-scaling matrix
and, at each viewport, injects JS that OBJECTIVELY measures layout defects (not
taste): page-level horizontal overflow, elements pushed off-screen or clipped by
an edge, and (at touch widths) tap targets below the WCAG 2.2 AA minimum.

Prints one JSON object to stdout and saves a screenshot ONLY for viewports that
actually have a defect, so you get evidence without a PNG per viewport.

Two exclusions stop it false-flagging deliberate design:
  * an element inside an `overflow-x: auto|scroll` ancestor that is genuinely
    scrolling is INTENDED internal scroll, not a page bug;
  * an element parked FULLY off-screen is an intended off-canvas drawer or a
    closed mobile menu — only clipped CONTENT (partly in view, cut by an edge)
    counts.

Display scaling is modelled the way it actually behaves: a 1920px monitor at
150% OS scaling lays out as 1280 CSS px, so each scaled row carries the
effective CSS width AND the scaling factor as the CDP deviceScaleFactor.

Measurements are taken against the LAYOUT viewport (documentElement.clientWidth),
not window.innerWidth — see the comment in MEASURE_JS for why that distinction
decides whether mobile clipping is detected at all.
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

# (label, css_width, css_height, device_scale_factor, mobile).
# Scaled desktop/laptop rows carry the EFFECTIVE CSS width that device produces
# at that OS display-scaling percentage.
MATRIX = [
    ("phone-small 360 (Android)",          360,  740, 3.0,  True),
    ("phone 390 (iPhone)",                 390,  844, 3.0,  True),
    ("phone-large 430 (iPhone Max)",       430,  932, 3.0,  True),
    ("tablet-portrait 768 (iPad)",         768, 1024, 2.0,  True),
    ("laptop 1366 @150% -> 911px",         911,  512, 1.5,  False),
    ("tablet-landscape 1024 (iPad)",      1024,  768, 2.0,  True),
    ("laptop 1366 @125% -> 1093px",       1093,  614, 1.25, False),
    ("desktop 1280 @150% / FHD@150%",     1280,  720, 1.5,  False),
    ("laptop 1366 @100%",                 1366,  768, 1.0,  False),
    ("FHD 1920 @125% -> 1536px",          1536,  864, 1.25, False),
    ("desktop 1280 @100%",                1280,  800, 1.0,  False),
    ("FHD 1920 @100%",                    1920, 1080, 1.0,  False),
    ("ultrawide 2560 @100%",              2560, 1080, 1.0,  False),
]


def select_matrix(name):
    if name == "phone":
        return [row for row in MATRIX if row[4]]
    if name == "desktop":
        return [row for row in MATRIX if not row[4]]
    return list(MATRIX)


# The measurement, run in the page. Objective layout defects for the CURRENT viewport.
MEASURE_JS = r"""
(() => {
  // Measure against the LAYOUT viewport, not window.innerWidth. Under mobile
  // emulation Chrome shrinks-to-fit a too-wide page, and innerWidth then reports
  // the VISUAL viewport after that zoom-out (e.g. 1440 for an emulated 360px
  // phone, or 980 on a page with no viewport meta). Measuring overflow against
  // that inflated number silently misses every element that sticks out past the
  // real 360px viewport but lands inside 1440 — exactly the mobile clipping bugs
  // this probe exists to find. documentElement.clientWidth is the true CSS
  // layout viewport in both mobile and desktop emulation.
  const de = document.documentElement;
  const vw = de.clientWidth || window.innerWidth;
  const vh = de.clientHeight || window.innerHeight;
  const tol = 2;
  const docW = de.scrollWidth;
  const sel = el => {
    if (!el || el === document.body) return 'body';
    let s = el.tagName.toLowerCase();
    if (el.id) return s + '#' + el.id;
    if (typeof el.className === 'string' && el.className.trim())
      s += '.' + el.className.trim().split(/\s+/).slice(0, 2).join('.');
    return s;
  };
  const styleOf = el => { try { return getComputedStyle(el); } catch (e) { return null; } };
  const visible = el => {
    const r = el.getBoundingClientRect();
    if (r.width < 1 || r.height < 1) return false;
    const st = styleOf(el);
    return st && st.visibility !== 'hidden' && st.display !== 'none' && parseFloat(st.opacity || '1') > 0.01;
  };
  const inScrollContainer = el => {          // intended internal scroll — not a page bug
    let p = el.parentElement;
    while (p && p !== document.documentElement) {
      const st = styleOf(p);
      if (st && (st.overflowX === 'auto' || st.overflowX === 'scroll') && p.scrollWidth > p.clientWidth + tol)
        return true;
      p = p.parentElement;
    }
    return false;
  };
  const off = [];
  for (const el of document.body.getElementsByTagName('*')) {
    if (!visible(el)) continue;
    const r = el.getBoundingClientRect();
    const over = Math.max(r.right - vw, -r.left);       // sticks out right, or off the left edge
    // Skip elements PARKED fully off-screen: an intended off-canvas drawer or a
    // closed mobile menu (transform/left off-screen). Only clipped CONTENT —
    // partially in view, cut by an edge — is a bug.
    const fullyOff = r.right <= tol || r.left >= vw - tol;
    if (over <= tol || fullyOff || inScrollContainer(el)) continue;
    const p = el.parentElement;
    const pr = p ? p.getBoundingClientRect() : { right: 0, left: vw };
    if (pr.right <= vw + tol && -pr.left <= tol)        // parent fits -> el is the boundary culprit
      off.push({ sel: sel(el), overPx: Math.round(over), w: Math.round(r.width),
                 text: (el.innerText || '').trim().replace(/\s+/g, ' ').slice(0, 50) });
  }
  off.sort((a, b) => b.overPx - a.overPx);
  const seen = new Set(), offscreen = [];
  for (const o of off) { if (seen.has(o.sel)) continue; seen.add(o.sel); offscreen.push(o); if (offscreen.length >= 12) break; }
  const tiny = [];
  for (const el of document.querySelectorAll('button,a,input,select,textarea,[role=button]')) {
    if (!visible(el)) continue;
    const r = el.getBoundingClientRect();
    // WCAG 2.2 AA minimum target is 24x24 CSS px — flag genuine violations, not merely-small (<44).
    if (r.width > 0 && Math.min(r.width, r.height) < 24)
      tiny.push({ sel: sel(el), w: Math.round(r.width), h: Math.round(r.height),
                  text: (el.innerText || el.value || '').trim().slice(0, 30) });
  }
  return { vw, vh, docW, overflowPx: docW - vw > tol ? docW - vw : 0,
           offscreen, tinyTargets: tiny.slice(0, 20),
           visualViewportPx: window.innerWidth,   // differs from vw when mobile shrink-to-fit kicks in
           hasViewportMeta: !!document.querySelector('meta[name=viewport]') };
})()
"""


def build_parser():
    p = argparse.ArgumentParser(
        prog="viewport_probe.py",
        description="Measure objective responsive defects for one page across a device x display-scaling matrix.",
        epilog=(
            "Signals per viewport: overflowPx (page scrolls sideways), offscreen[] "
            "(elements clipped by an edge), tinyTargets[] (touch targets under 24x24 CSS px, "
            "mobile rows only).\n"
            "Screenshots for defective viewports land in <output.dir>/visual-hunts/shots/.\n"
            "Config: .claude/qa.json. Env: CLAUDE_QA_WEB_URL, CLAUDE_QA_OUTPUT_DIR, CLAUDE_QA_BROWSER."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("path", nargs="?", default="/",
                   help="page path to probe, with or without a leading slash (default: /)")
    p.add_argument("--as-user", dest="as_user", default=None,
                   help="identity value substituted into {userId} in the config auth shim")
    p.add_argument("--as-tenant", dest="as_tenant", default=None,
                   help="identity value substituted into {tenantId} in the config auth shim")
    p.add_argument("--matrix", choices=["full", "phone", "desktop"], default="full",
                   help="which viewports to run (default: full — all 13)")
    p.add_argument("--settle", type=float, default=1.2,
                   help="seconds to wait after load for async data/layout (default: 1.2)")
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

    ok, why = A.check_reachable(base_url)
    if not ok:
        A.die(
            "Cannot reach the app at {}\n  {}\n"
            "Start the app, or point the probes elsewhere with CLAUDE_QA_WEB_URL=<url> "
            "or a 'web.url' in .claude/qa.json.".format(base_url, why),
            code=3,
        )

    try:
        shots_dir = A.output_path(config, "visual-hunts", "shots", is_dir=True)
    except A.QAError as exc:
        A.die(exc)

    matrix = select_matrix(args.matrix)
    identity = {"userId": args.as_user, "tenantId": args.as_tenant}
    result = {
        "path": path,
        "url": url,
        "acting_as": {k: v for k, v in identity.items() if v},
        "matrix": args.matrix,
        "viewports": [],
    }

    proc = profile = None
    try:
        proc, profile, port = A.launch(
            config, headless=not args.headed, window_size=(1500, 1500), tag="vprobe")
    except A.QAError as exc:
        A.die(exc)

    try:
        cdp = A.connect(A.wait_for_ws(port, proc=proc))
        cdp.send("Page.enable")
        cdp.send("Runtime.enable")

        # Prime the origin, install the identity, then probe each viewport.
        cdp.send("Emulation.setDeviceMetricsOverride",
                 {"width": 1280, "height": 900, "deviceScaleFactor": 1, "mobile": False})
        cdp.send("Page.navigate", {"url": base_url})
        A.wait_ready(cdp, config, timeout=24)
        result["auth_shim"] = A.apply_identity(cdp, config, identity)

        for label, w, h, dsf, mobile in matrix:
            cdp.send("Emulation.setDeviceMetricsOverride",
                     {"width": w, "height": h, "deviceScaleFactor": dsf, "mobile": mobile})
            cdp.send("Page.navigate", {"url": url})
            ready = bool(A.wait_ready(cdp, config, timeout=18))
            time.sleep(args.settle)              # settle async data + layout

            m = cdp.js(MEASURE_JS)
            entry = {"label": label, "width": w, "height": h, "scale": dsf,
                     "mobile": mobile, "ready": ready}
            # None means the measurement itself failed — MEASURE_JS walks every
            # element calling getComputedStyle, then again per ancestor, so it is
            # genuinely slow on a large DOM and does time out. `or {}` turned that
            # into an entry with no vw and no docW, has_defect False, and a
            # viewport recorded as CLEAN. A timing-out probe and a defect-free
            # page must never be indistinguishable.
            if m is None:
                entry["measurement_failed"] = True
                entry["reason"] = "MEASURE_JS returned nothing (evaluation timed out or threw)"
                result["viewports"].append(entry)
                continue
            if not mobile:
                m["tinyTargets"] = []            # small targets are only a defect at touch widths
            has_defect = bool(m.get("overflowPx") or m.get("offscreen") or m.get("tinyTargets"))
            entry.update(m)
            if has_defect:
                name = "{}__{}w{}x.png".format(A.slug(path), w, str(dsf).replace(".", "_"))
                out = os.path.join(shots_dir, name)
                shot = cdp.send("Page.captureScreenshot",
                                {"format": "png", "captureBeyondViewport": False})
                if "data" in shot:
                    with open(out, "wb") as fh:
                        fh.write(base64.b64decode(shot["data"]))
                    entry["screenshot"] = out
            result["viewports"].append(entry)

        defective = [v for v in result["viewports"] if v.get("overflowPx") or v.get("offscreen") or v.get("tinyTargets")]
        result["viewports_probed"] = len(result["viewports"])
        result["viewports_with_defects"] = len(defective)
        result["viewports_unmeasured"] = len(
            [v for v in result["viewports"] if v.get("measurement_failed") or v.get("ready") is False])
    except A.QAError as exc:
        # No shutdown() here: die() raises SystemExit, so the finally: below runs
        # and does the full kill-plus-sweep. Doing it in both places ran the
        # whole teardown twice on every error path.
        A.die(exc)
    finally:
        A.shutdown(proc, profile)

    print(json.dumps(result, indent=2))
    # "No defects found" is only meaningful if the measurements actually ran.
    if result.get("viewports_unmeasured"):
        sys.stderr.write(
            "MEASUREMENT UNRELIABLE: {} of {} viewports were not measured "
            "(JS failure or page never ready) — do not read this run as clean.\n".format(
                result["viewports_unmeasured"], result["viewports_probed"]))
        return MEASUREMENT_FAILED_EXIT
    return 0


if __name__ == "__main__":
    sys.exit(main())
