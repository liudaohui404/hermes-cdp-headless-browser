# cdp-headless-browser

A **Hermes Agent plugin** that keeps a *persistent, always-on headless
Chromium-family browser* (Chrome / Chromium / Edge / Brave) available over the
Chrome DevTools Protocol, so Hermes browser tools (`browser_exec`,
`browser_navigate`, `browser_use`) never fall back to curl because "the browser
wasn't open".

- **Zero-setup install** — after `hermes plugins enable`, the `gateway:startup`
  hook does everything automatically and idempotently: it points browser tools at
  the headless browser (`browser.cdp_url`), launches a headless Chrome/Chromium
  on `127.0.0.1:9222` (dedicated profile, no window), and registers the idle-tab
  reaper cron job. **No `cd` into a directory, no shell script, no manual step.**
- **Bounded memory** — a bundled cron job (`reap_tabs.py`, every 2 min) closes
  page tabs idle longer than `reap_after_min` (default 10 min), with a hard
  `max_tabs` cap (default 5). No more tabs leaking memory.
- **Cross-platform** — binary auto-detection for Windows, macOS and Linux. No
  `git-bash`, no bash — everything is `hermes` CLI calls (hermes.exe on Windows).

## Install

```bash
hermes plugins install liudaohui404/hermes-cdp-headless-browser
hermes plugins enable cdp-headless-browser
```

That is the entire setup. Reopen Hermes (or restart its gateway) and the
headless browser is up, the tools are wired to it, and the reaper cron is
registered — all on `gateway:startup`. Optionally run `python install.py` only
to re-validate / repair the setup (it is not required for normal use).

Or one-click from a website / README:

```html
<a href="hermes://plugin/install?repo=liudaohui404/hermes-cdp-headless-browser&enable=1">Install in Hermes</a>
```

## In-session commands

No slash command, no LLM — just a plain CLI (`cdp-browser.py`, pure stdlib).
Run it from anywhere, or have an agent call it directly:

```bash
python cdp-browser.py status          # listening? browser? page tab count?
python cdp-browser.py launch          # force (re)launch now
python cdp-browser.py reap            # run the idle-tab reaper once
python cdp-browser.py stop            # close all page tabs (browser keeps running)
python cdp-browser.py config          # print current plugin settings
```

`status` queries the CDP endpoint directly (sub-second, no model involved). The
script is also copied to `~/.hermes/scripts/cdp-browser.py` by the startup hook,
so `hermes cron` or any shell can call it.

## Important: tag the tabs you use

The reaper closes tabs whose `window.name` stamp is older than `reap_after_min`.
**Every `browser_exec` call must refresh the marker** so the tab you are
actively using survives:

```python
js("window.name = 'h:' + Date.now()")
```

This convention is also documented in the bundled skill
`cdp-headless-browser` (auto-loaded by the plugin).

## Config (`config.yaml` → `plugins.entries.cdp-headless-browser.settings`)

All features are **on by default** — zero-config out of the box. Set any to
`false` to disable. Edit `config.yaml` (or `hermes config set
plugins.entries.cdp-headless-browser.settings.<key> <val>`).

| Key | Type | Default | Meaning |
|---|---|---|---|
| `auto_launch` | bool | `true` | Launch the browser on gateway startup if the CDP port isn't already serving |
| `headless` | bool | `true` | Run `--headless=new` (no window). Set `false` for a visible windowed browser |
| `auto_set_cdp_url` | bool | `true` | Point `browser.cdp_url` at this plugin's CDP endpoint on startup |
| `auto_reap` | bool | `true` | Register + keep the idle-tab reaper cron job running |
| `cdp_port` | int | `9222` | Local CDP port |
| `reap_after_min` | int | `10` | Idle minutes before a tab is reaped |
| `max_tabs` | int | `5` | Hard cap on page tabs |
| `browser_bin` | str | `""` (auto) | Explicit browser binary path |

Example — disable auto-launch and reaping, keep it manual:

```yaml
plugins:
  entries:
    cdp-headless-browser:
      settings:
        auto_launch: false
        auto_reap: false
```

## How it works

```
gateway:startup (if auto_set_cdp_url)  ──► browser.cdp_url = http://127.0.0.1:9222
gateway:startup (if auto_launch)       ──► ensure_browser_launched()  ──► Chrome(:9222)
gateway:startup (if auto_reap)         ──► register cdp-tab-reaper cron (every 2m)

cron every 2m ──► reap_tabs.py ──► close tabs idle > reap_after_min OR over max_tabs
```

No external services, no API keys, no network egress. Pure local.

## License

MIT
