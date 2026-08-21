"""
cdp-headless-browser — Hermes plugin

Gives Hermes browser tools (browser_exec / browser_navigate / browser_use)
a persistent headless Chromium-family browser over CDP, so they never fall
back to curl because "the browser wasn't open".

What it does:
  * On gateway startup, launches a headless Chrome/Chromium/Edge/Brave with
    --remote-debugging-port (127.0.0.1:<cdp_port>, default 9222) using a
    dedicated user-data-dir (required by Chrome 136+). Idempotent: if the
    port already answers, nothing is launched.
  * Registers the /cdp-browser slash command (status / launch / reap / stop).
  * Bundles the cdp-browser skill (usage + conventions).
  * A separate cron job (registered automatically on gateway startup when
    auto_reap is enabled) runs the tab reaper to keep memory bounded (idle
    tabs closed after reap_after_min minutes).

Cross-platform: binary auto-detection covers Windows, macOS and Linux.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parent


# --------------------------------------------------------------------------- #
# Configuration helpers
# --------------------------------------------------------------------------- #
def _cfg(key: str, default):
    # Feature toggles live under plugins.entries.<plugin_id>.settings.<key>
    # (the path config_schema / ctx.set_config use — NOT plugins.config.<name>).
    try:
        from hermes_cli.config import read_raw_config
        plugins = read_raw_config().get("plugins", {})
        settings = plugins.get("entries", {}).get("cdp-headless-browser", {}).get("settings", {})
        val = settings.get(key)
        if val is not None:
            return val
    except Exception:
        pass
    return default


def cdp_port() -> int:
    return int(_cfg("cdp_port", 9222))


def reap_after_ms() -> int:
    return int(_cfg("reap_after_min", 10)) * 60 * 1000


def max_tabs() -> int:
    return int(_cfg("max_tabs", 5))


def browser_user_data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_DATA_HOME") or str(Path.home())
    return Path(base) / "hermes" / "chrome-cdp-headless"


# --------------------------------------------------------------------------- #
# Browser binary detection (cross-platform)
# --------------------------------------------------------------------------- #
def _detect_browser() -> str | None:
    explicit = _cfg("browser_bin", "")
    if explicit:
        return explicit if Path(explicit).exists() else None

    candidates = []
    if sys.platform.startswith("win"):
        prog = os.environ.get("ProgramFiles", "C:\\Program Files")
        prog86 = os.environ.get("ProgramFiles(x86)", "C:\\Program Files (x86)")
        candidates = [
            f"{prog}\\Google\\Chrome\\Application\\chrome.exe",
            f"{prog86}\\Google\\Chrome\\Application\\chrome.exe",
            f"{prog}\\Microsoft\\Edge\\Application\\msedge.exe",
            f"{prog86}\\Microsoft\\Edge\\Application\\msedge.exe",
            f"{prog}\\BraveSoftware\\Brave-Browser\\Application\\brave.exe",
            f"{prog86}\\BraveSoftware\\Brave-Browser\\Application\\brave.exe",
        ]
    elif sys.platform == "darwin":
        candidates = [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
            "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
        ]
    else:  # linux / wsl
        for name in ("google-chrome", "chromium", "chromium-browser", "msedge", "brave", "brave-browser"):
            found = shutil.which(name)
            if found:
                return found
        candidates = [
            "/usr/bin/google-chrome",
            "/usr/bin/chromium",
            "/usr/bin/chromium-browser",
            "/snap/bin/chrome",
            "/opt/google/chrome/chrome",
            "/opt/brave.com/brave/brave",
        ]

    for c in candidates:
        if Path(c).exists():
            return c
    return None


# --------------------------------------------------------------------------- #
# Launch / probe
# --------------------------------------------------------------------------- #
def _port_open(port: int, timeout: float = 1.0) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        try:
            s.connect(("127.0.0.1", port))
            return True
        except OSError:
            return False


def ensure_browser_launched() -> dict:
    """Launch the headless browser if the CDP port is not already serving
    OUR browser.

    Returns a status dict (always JSON-serialisable). Safe to call repeatedly.
    """
    port = cdp_port()
    if _port_open(port):
        # Port answers — but is it OUR browser (not a stray Chrome someone else
        # started on 9222)? Verify the Browser field before skipping, otherwise
        # we'd silently attach the browser tools to the wrong instance.
        try:
            import json
            import urllib.request
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=3) as r:
                v = json.loads(r.read())
            ua = (v.get("Browser") or "")
            if "Chrome" in ua or "Chromium" in ua or "Edge" in ua or "Brave" in ua:
                return {"ok": True, "launched": False, "port": port,
                        "note": "already listening (our browser)"}
        except Exception:
            pass  # fall through to (re)launch attempt
        # Port is occupied by something we can't identify as ours — try to
        # launch anyway; Chrome's --user-data-dir lock will surface a clear
        # error rather than attaching to a foreign process.
    else:
        # Port free — make sure a leftover lock from a previous run isn't
        # lying around (Chrome crashes leave stale SingletonLock sometimes).
        import shutil as _sh
        lock = browser_user_data_dir() / "SingletonLock"
        if lock.exists():
            try:
                _sh.rmtree(lock)
            except OSError:
                pass

    bin_path = _detect_browser()
    if not bin_path:
        return {"ok": False, "launched": False, "port": port,
                "error": "no Chromium-family browser found on this machine"}

    udd = browser_user_data_dir()
    udd.mkdir(parents=True, exist_ok=True)
    args = [
        bin_path,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={udd}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-background-networking",
        "--disable-component-update",
        "--disable-sync",
        "--no-sandbox",
        "--remote-allow-origins=*",
    ]
    if _cfg("headless", True):
        args.append("--headless=new")  # position-independent; appended before url
    args.append("about:blank")
    try:
        flags = 0
        if sys.platform.startswith("win"):
            flags = (
                subprocess.DETACHED_PROCESS
                | subprocess.CREATE_NEW_PROCESS_GROUP
                | getattr(subprocess, "CREATE_NO_WINDOW", 0)
            )
        subprocess.Popen(
            args,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=flags,
            close_fds=True,
        )
    except Exception as e:  # pragma: no cover - defensive
        return {"ok": False, "launched": False, "port": port, "error": str(e)}

    return {"ok": True, "launched": True, "port": port, "binary": bin_path}


def stop_browser() -> dict:
    """Best-effort: close every page tab via CDP. Leaves the browser process
    running. If auto_launch is enabled it will be relaunched on the next
    gateway start; if auto_launch is disabled, it stays stopped until you run
    /cdp-browser launch (or re-enable auto_launch)."""
    import json
    import urllib.request

    port = cdp_port()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=3) as r:
            tabs = json.loads(r.read())
    except Exception as e:
        return {"ok": False, "error": f"cannot reach CDP: {e}"}
    closed = 0
    for t in tabs:
        if t.get("type") == "page":
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/json/close/{t['id']}", timeout=3)
                closed += 1
            except Exception:
                pass
    return {"ok": True, "closed_tabs": closed}


def status() -> dict:
    port = cdp_port()
    if not _port_open(port):
        return {"listening": False, "port": port}
    import json
    import urllib.request
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=3) as r:
            v = json.loads(r.read())
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=3) as r:
            tabs = json.loads(r.read())
        return {
            "listening": True,
            "port": port,
            "browser": v.get("Browser"),
            "page_tabs": sum(1 for t in tabs if t.get("type") == "page"),
        }
    except Exception as e:
        return {"listening": True, "port": port, "error": str(e)}


# --------------------------------------------------------------------------- #
# Slash command: /cdp-browser
# --------------------------------------------------------------------------- #
def _cmd_cdp_browser(raw_args: str) -> str:
    sub = (raw_args or "").strip().lower().split()
    action = sub[0] if sub else "status"
    if action == "launch":
        s = ensure_browser_launched() if _cfg("auto_launch", True) else \
            {"ok": False, "error": "auto_launch is disabled in plugin config"}
    elif action == "stop":
        s = stop_browser()
    elif action == "reap":
        if not _cfg("auto_reap", True):
            s = {"ok": False, "error": "auto_reap is disabled in plugin config"}
        else:
            reaper = PLUGIN_DIR / "reap_tabs.py"
            if reaper.exists():
                try:
                    out = subprocess.run(
                        [sys.executable, str(reaper),
                         "--after", str(reap_after_ms()),
                         "--max", str(max_tabs())],
                        capture_output=True, text=True, timeout=30,
                    )
                    s = {"ok": True, "output": out.stdout.strip() or "(no idle tabs)"}
                except Exception as e:
                    s = {"ok": False, "error": str(e)}
            else:
                s = {"ok": False, "error": "reaper script not found"}
    elif action == "config":
        s = {
            "auto_launch": _cfg("auto_launch", True),
            "headless": _cfg("headless", True),
            "auto_set_cdp_url": _cfg("auto_set_cdp_url", True),
            "auto_reap": _cfg("auto_reap", True),
            "cdp_port": cdp_port(),
            "reap_after_min": _cfg("reap_after_min", 10),
            "max_tabs": max_tabs(),
        }
    else:  # status (default)
        s = status()
        s["config"] = {
            "auto_launch": _cfg("auto_launch", True),
            "headless": _cfg("headless", True),
            "auto_set_cdp_url": _cfg("auto_set_cdp_url", True),
            "auto_reap": _cfg("auto_reap", True),
        }
    import json
    return "```json\n" + json.dumps(s, ensure_ascii=False, indent=2) + "\n```"


# --------------------------------------------------------------------------- #
# Hook: gateway startup -> ensure browser
# --------------------------------------------------------------------------- #
def _hermes_bin() -> str:
    """Resolve the hermes CLI. Prefer the on-PATH `hermes` (hermes.exe on
    Windows); fall back to `python -m hermes` only if that's missing. Using the
    real launcher matters because `python -m hermes` can resolve a different
    venv/cwd than the installed `hermes` command, which silently breaks
    `cron create` / `config set` when invoked from a subprocess."""
    import shutil
    found = shutil.which("hermes")
    if found:
        return found
    return sys.executable  # last resort; caller appends ["-m", "hermes", ...]


def _hermes_base_cmd() -> list:
    """Return the prefix for a hermes CLI invocation."""
    b = _hermes_bin()
    if b == sys.executable:
        return [b, "-m", "hermes"]
    return [b]


def _ensure_cdp_url_configured() -> None:
    """Best-effort: point Hermes browser tools at our headless browser.

    Only sets browser.cdp_url when it is NOT already configured. If the user
    has pointed browser tools at a different debugger (e.g. a remote host or
    their own Chrome), we must not clobber that choice on every gateway start.

    Cross-platform (hermes is a Python wrapper with a .exe on Windows; no bash
    / git-bash needed). Idempotent.
    """
    try:
        from hermes_cli.config import read_raw_config
        existing = (read_raw_config().get("browser", {}) or {}).get("cdp_url")
        if existing:
            # Already set (by the user or by a previous run) — respect it.
            return
    except Exception:
        pass  # if we can't read config, fall through and try to set it
    try:
        url = f"http://127.0.0.1:{cdp_port()}"
        subprocess.run(
            _hermes_base_cmd() + ["config", "set", "browser.cdp_url", url],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30,
        )
    except Exception as e:  # pragma: no cover - defensive
        sys.stderr.write(f"[cdp-headless-browser] config set failed: {e!r}\n")


def _ensure_reaper_cron() -> None:
    """Best-effort, idempotent: register (or remove) the idle-tab reaper cron so
    memory stays bounded. Respects the `auto_reap` setting:
      * auto_reap=true  -> ensure the cdp-tab-reaper cron exists
      * auto_reap=false -> remove it if present (user opts out)
    Runs inside the plugin (no manual `python install.py` step needed).
    Cross-platform via the `hermes` CLI (hermes.exe on Windows)."""
    try:
        home = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
        reaper_dst = home / "scripts" / "cdp-tab-reaper.py"
        base = _hermes_base_cmd()
        enabled = _cfg("auto_reap", True)

        if not enabled:
            subprocess.run(
                base + ["cron", "remove", "cdp-tab-reaper"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30,
            )
            return

        # enabled: ensure the reaper script + cron job exist.
        if not reaper_dst.exists():
            reaper_dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(PLUGIN_DIR / "reap_tabs.py", reaper_dst)

        out = subprocess.run(
            base + ["cron", "list"],
            capture_output=True, text=True, timeout=30,
        )
        if "cdp-tab-reaper" in out.stdout:
            return

        r = subprocess.run(
            base + ["cron", "create",
                    "every 2m", "CDP headless browser idle tab reaper",
                    "--name", "cdp-tab-reaper", "--no-agent",
                    "--script", "cdp-tab-reaper.py"],
            capture_output=True, text=True, timeout=30,
        )
        if r.returncode != 0:
            # Surface the failure instead of silently leaving cron unregistered.
            sys.stderr.write(
                f"[cdp-headless-browser] cron create failed ({r.returncode}): "
                f"{r.stderr.strip() or r.stdout.strip()}\n")
    except Exception as e:  # pragma: no cover - defensive
        sys.stderr.write(f"[cdp-headless-browser] reaper cron setup failed: {e!r}\n")


async def _on_gateway_startup(event_type: str, context: dict) -> None:
    # Run the (possibly slow) launch off the event loop so startup isn't blocked.
    try:
        if _cfg("auto_set_cdp_url", True):
            _ensure_cdp_url_configured()
        if _cfg("auto_reap", True):
            _ensure_reaper_cron()
        if _cfg("auto_launch", True):
            ensure_browser_launched()
    except Exception as e:  # never crash the gateway on startup
        sys.stderr.write(f"[cdp-headless-browser] startup failed: {e!r}\n")


# --------------------------------------------------------------------------- #
# Plugin entry point
# --------------------------------------------------------------------------- #
def register(ctx) -> None:
    ctx.register_hook("gateway:startup", _on_gateway_startup)
    ctx.register_command(
        name="cdp-browser",
        handler=_cmd_cdp_browser,
        description="Manage the persistent headless CDP browser (status/launch/stop/reap).",
        args_hint="[status|launch|stop|reap]",
    )
    # Bundle the skill so the agent knows the window.name tagging convention.
    skill_md = PLUGIN_DIR / "skills" / "cdp-headless-browser" / "SKILL.md"
    if skill_md.exists():
        ctx.register_skill("cdp-headless-browser", skill_md)
