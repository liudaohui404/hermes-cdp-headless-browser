---
name: cdp-headless-browser
description: "Use browser_exec via the persistent headless CDP browser from the cdp-headless-browser plugin."
version: 1.0.0
author: liudaohui404
platforms: [linux, macos, windows]
license: MIT
---

# CDP Headless Browser (plugin)

This plugin (installed under `~/.hermes/plugins/cdp-headless-browser/`) keeps a
persistent headless Chromium-family browser on `127.0.0.1:9222`, and sets
`browser.cdp_url` so Hermes browser tools attach to it instead of spinning up
(and forgetting to clean up) their own headless instance.

## 强制约定：tag every tab you touch

The bundled cron job (`reap_tabs.py`, every 2 min) closes page tabs idle longer
than `reap_after_min` (default 10) minutes. Idle = its `window.name` timestamp
has not been refreshed. **Always stamp the current tab at the top of every
`browser_exec` call**, so the tab you are actively using stays alive:

```python
js("window.name = 'h:' + Date.now()")   # refresh activity marker (survives navigation)
```

Do this right after `new_tab(...)` too. Tabs you never touch get reaped — that
is the desired memory-bounding behavior.

## Commands (in-session slash)

- `/cdp-browser status` — is it listening, how many page tabs
- `/cdp-browser launch` — force (re)launch now
- `/cdp-browser reap` — run the reaper once
- `/cdp-browser stop` — close all page tabs (browser process stays, relaunches on next gateway start)

## Config (config.yaml → `plugins.config.cdp-headless-browser`)

- `cdp_port` (int, default 9222)
- `reap_after_min` (int, default 10)
- `max_tabs` (int, default 5)
- `browser_bin` (str, default "" → auto-detect Chrome/Chromium/Edge/Brave)

## Pitfalls

- `/json/new` needs PUT, not GET.
- `/json/close/{id}` returns plain text `Target is closing`, not JSON — ignore the body.
- Chrome 136+ requires a dedicated `--user-data-dir` or the debug port silently
  never opens. The plugin always passes one.
- `window.name` is per-tab and survives navigation; `localStorage` is
  per-origin and is NOT a substitute for the activity marker.
- Headless has no GPU process, so memory is lower than a windowed browser;
  the reaper keeps it bounded as tabs come and go.
