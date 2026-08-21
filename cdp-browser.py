#!/usr/bin/env python3
"""cdp-browser.py — CLI for the cdp-headless-browser plugin.

No LLM, no plugin import, no thinking — just fixed operations over CDP / hermes CLI.
Pure stdlib (urllib, subprocess, argparse). Cross-platform.

Usage:
    python cdp-browser.py status          # print listening/browser/page_tabs
    python cdp-browser.py launch          # launch headless browser if not up
    python cdp-browser.py stop            # close all page tabs (browser keeps running)
    python cdp-browser.py reap            # run idle-tab reaper now
    python cdp-browser.py config          # print current plugin settings

Exit code 0 = ok, 1 = error. Output is plain text, one fact per line.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

PORT = int(__import__("os").environ.get("CDP_PORT", "9222"))
PLUGIN_DIR = Path(__file__).resolve().parent


def _get_json(url: str, timeout: int = 3):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read())


def _hermes_bin() -> str:
    found = shutil.which("hermes")
    return found or (sys.executable + " -m hermes")


def _read_cfg(key: str, default):
    """Read a plugin setting via hermes config get (best-effort)."""
    try:
        out = subprocess.run(
            _hermes_bin().split() + [
                "config", "get",
                f"plugins.entries.cdp-headless-browser.settings.{key}"],
            capture_output=True, text=True, timeout=20,
        )
        v = out.stdout.strip().splitlines()[-1] if out.stdout.strip() else ""
        if v.lower() in ("true", "false"):
            return v.lower() == "true"
        if v.isdigit():
            return int(v)
        return v or default
    except Exception:
        return default


def cmd_status() -> int:
    try:
        v = _get_json(f"http://127.0.0.1:{PORT}/json/version")
        tabs = _get_json(f"http://127.0.0.1:{PORT}/json")
        pages = sum(1 for t in tabs if t.get("type") == "page")
        print("listening: true")
        print(f"browser: {v.get('Browser')}")
        print(f"port: {PORT}")
        print(f"page_tabs: {pages}")
        return 0
    except Exception as e:
        print("listening: false")
        print(f"error: {e}")
        return 1


def cmd_launch() -> int:
    if not _read_cfg("auto_launch", True):
        print("auto_launch disabled in plugin config — not launching")
        return 1
    # Reuse the plugin's launch logic by calling its ensure_browser_launched.
    # Import is lazy so this CLI stays cheap for status/reap/config.
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "cdpb_init", str(PLUGIN_DIR / "__init__.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        s = mod.ensure_browser_launched()
        print(json.dumps(s, ensure_ascii=False))
        return 0 if s.get("ok") else 1
    except Exception as e:
        print(f"error: {e}")
        return 1


def cmd_stop() -> int:
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "cdpb_init", str(PLUGIN_DIR / "__init__.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        s = mod.stop_browser()
        print(json.dumps(s, ensure_ascii=False))
        return 0 if s.get("ok") else 1
    except Exception as e:
        print(f"error: {e}")
        return 1


def cmd_reap() -> int:
    if not _read_cfg("auto_reap", True):
        print("auto_reap disabled in plugin config — not reaping")
        return 1
    reaper = PLUGIN_DIR / "reap_tabs.py"
    if not reaper.exists():
        print("error: reaper script not found")
        return 1
    after = int(_read_cfg("reap_after_min", 10)) * 60 * 1000
    mx = int(_read_cfg("max_tabs", 5))
    r = subprocess.run(
        [sys.executable, str(reaper), "--after", str(after), "--max", str(mx)],
        capture_output=True, text=True, timeout=30,
    )
    print(r.stdout.strip() or "(no idle tabs)")
    if r.returncode != 0:
        print(r.stderr.strip(), file=sys.stderr)
    return r.returncode


def cmd_config() -> int:
    defaults = {
        "auto_launch": True, "headless": True, "auto_set_cdp_url": True,
        "auto_reap": True, "cdp_port": 9222, "reap_after_min": 10, "max_tabs": 5,
    }
    for k in defaults:
        print(f"{k}: {_read_cfg(k, defaults[k])}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="cdp-headless-browser control CLI")
    p.add_argument("action", nargs="?", default="status",
                   choices=["status", "launch", "stop", "reap", "config"])
    args = p.parse_args()
    return {
        "status": cmd_status,
        "launch": cmd_launch,
        "stop": cmd_stop,
        "reap": cmd_reap,
        "config": cmd_config,
    }[args.action]()


if __name__ == "__main__":
    sys.exit(main())
