#!/usr/bin/env python3
"""
install.py — OPTIONAL setup validator/repair for the cdp-headless-browser plugin.

NOTE: This script is NOT required for normal use. The plugin's `gateway:startup`
hook already performs all setup idempotently (sets browser.cdp_url, launches the
headless browser, and registers the reaper cron). Run this only to re-validate
or repair the setup, or if you prefer an explicit CLI step over the automatic
hook. Cross-platform: plain `python3 install.py` works on Windows (CMD/PowerShell),
macOS and Linux — no git-bash.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

PORT = os.environ.get("CDP_PORT", "9222")
PLUGIN_DIR = Path(__file__).resolve().parent
REAPER_SRC = PLUGIN_DIR / "reap_tabs.py"


def _hermes_bin() -> str:
    if os.environ.get("HERMES_BIN"):
        return os.environ["HERMES_BIN"]
    found = shutil.which("hermes")
    if found:
        return found
    return "hermes"


def _home() -> Path:
    # Hermes resolves HERMES_HOME; fall back to ~/.hermes
    return Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))


def run(args):
    print(f"[cdp-headless-browser] {' '.join(args)}")
    try:
        subprocess.run(args, check=True)
        return True
    except subprocess.CalledProcessError as e:
        print(f"  ! command failed (exit {e.returncode}): {e}", file=sys.stderr)
        return False


def _auto_reap_enabled() -> bool:
    """Mirror the plugin's auto_reap toggle. install.py is a companion script;
    it must agree with the hook so re-running it cannot re-create a cron the
    user explicitly turned off via plugin config. Reads the same config path."""
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "cdpb_init", str(PLUGIN_DIR / "__init__.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return bool(mod._cfg("auto_reap", True))
    except Exception:
        return True  # default-on when we can't read config


def main() -> int:
    hermes = _hermes_bin()
    ok = True

    # 1. point browser tools at the persistent headless browser
    ok &= run([hermes, "config", "set", "browser.cdp_url", f"http://127.0.0.1:{PORT}"])

    # 2. register (or remove) the idle-tab reaper cron, honouring auto_reap.
    #    This matches the gateway:startup hook behaviour so the two never fight:
    #    if auto_reap is OFF, we remove the cron rather than re-create it.
    scripts_dir = _home() / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    reaper_dst = scripts_dir / "cdp-tab-reaper.py"
    if REAPER_SRC.exists():
        shutil.copyfile(REAPER_SRC, reaper_dst)
        print(f"[cdp-headless-browser] reaper copied -> {reaper_dst}")
    else:
        print(f"  ! {REAPER_SRC} not found; skipping cron registration", file=sys.stderr)
        ok = False

    if REAPER_SRC.exists():
        if _auto_reap_enabled():
            run([hermes, "cron", "remove", "cdp-tab-reaper"])  # best-effort clean
            ok &= run([
                hermes, "cron", "create",
                "every 2m",
                "CDP headless browser idle tab reaper",
                "--name", "cdp-tab-reaper",
                "--no-agent",
                "--script", "cdp-tab-reaper.py",
            ])
        else:
            run([hermes, "cron", "remove", "cdp-tab-reaper"])  # respect opt-out
            print("[cdp-headless-browser] auto_reap disabled in plugin config — "
                  "cron removed (not registered).")

    if ok:
        print("[cdp-headless-browser] done. Verify: hermes cron list")
        print("[cdp-headless-browser] in-session status: /cdp-browser status")
    else:
        print("[cdp-headless-browser] some steps failed — see above.", file=sys.stderr)
        print("  You can re-run `python install.py` any time.", file=sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
