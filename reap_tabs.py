#!/usr/bin/env python3
"""cdp-headless-browser: idle tab reaper.

Closes page tabs that have been inactive for longer than --after ms, using the
window.name='h:<epoch_ms>' tagging convention. Falls back to a hard MAX_TABS
cap (oldest first). No third-party deps beyond the optional `websockets` lib
(which comes with Hermes); if it's missing, we still close by age when tags
are present, and only skip the tag-reading path with a clear warning.

Usage:
    python reap_tabs.py [--port 9222] [--after 600000] [--max 5]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
import urllib.error

try:
    import websockets  # type: ignore
    _HAVE_WS = True
except Exception:  # pragma: no cover
    _HAVE_WS = False


def _get_json(url: str, timeout: float = 5.0):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read())


def _eval_on_tab(ws_url: str, expression: str):
    """Return the JSON value of `expression` evaluated in the tab. Needs WS."""
    if not _HAVE_WS:
        return None

    async def _run():
        async with websockets.connect(ws_url, max_size=None) as ws:
            await ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate",
                                      "params": {"expression": expression, "returnByValue": True}}))
            while True:
                msg = json.loads(await ws.recv())
                if msg.get("id") == 1:
                    return msg.get("result", {}).get("result", {}).get("value")

    try:
        # Bound each tab's WS round-trip so a single slow/hung tab can't stall
        # the whole reaper run (matters when many tabs are open).
        return asyncio.run(asyncio.wait_for(_run(), timeout=3.0))
    except Exception:
        return None


def reap(port: int, after_ms: int, max_tabs: int) -> dict:
    try:
        tabs = _get_json(f"http://127.0.0.1:{port}/json", timeout=3)
    except (urllib.error.URLError, OSError, ValueError) as e:
        # Browser not running (e.g. between gateway restarts, or user paused it).
        # This is a normal state — the plugin's startup hook will relaunch it.
        # Exit 0 so the cron job stays silent instead of logging an error.
        return {"ok": True, "closed": 0, "pages_left": 0,
                "note": f"CDP not listening on {port} (browser not running)"}

    pages = [t for t in tabs if t.get("type") == "page"]
    if not pages:
        return {"ok": True, "closed": 0, "pages_left": 0}

    now = time.time() * 1000
    seen = []  # (id, ts, tab)
    for t in pages:
        ts = 0
        name = _eval_on_tab(t.get("webSocketDebuggerUrl", ""), "window.name") if _HAVE_WS else None
        if isinstance(name, str):
            import re
            m = re.match(r"^h:(\d+)$", name.strip())
            if m:
                ts = int(m.group(1))
        if ts == 0:
            # Unmarked tab: stamp it (grace period) so it isn't immediately killed.
            if _HAVE_WS:
                _eval_on_tab(t.get("webSocketDebuggerUrl", ""), f"window.name='h:{int(now)}'")
            ts = int(now)
        seen.append((t["id"], ts, t))

    dead = set()
    for tid, ts, _ in seen:
        if now - ts > after_ms:
            dead.add(tid)

    # Hard cap: after idle reaping, if we still have more than max_tabs page
    # tabs, close the oldest extras (oldest first). Compute the cap against
    # the survivors of idle reaping so we never exceed max_tabs total.
    survivors = [x for x in seen if x[0] not in dead]
    if len(survivors) > max_tabs:
        by_age = sorted(survivors, key=lambda x: x[1])
        for tid, _, _ in by_age[: len(survivors) - max_tabs]:
            dead.add(tid)

    closed = []
    for tid in dead:
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/json/close/{tid}", timeout=3)
            closed.append(tid[:8])
        except Exception:
            pass

    return {"ok": True, "closed": len(closed), "closed_ids": closed,
            "pages_left": len(seen) - len(closed)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9222)
    ap.add_argument("--after", type=int, default=600000, help="idle ms before reap")
    ap.add_argument("--max", type=int, default=5, help="hard cap on page tabs")
    args = ap.parse_args()

    res = reap(args.port, args.after, args.max)
    print(json.dumps(res, ensure_ascii=False))
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
