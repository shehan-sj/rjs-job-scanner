# Running JobFinder

Two ways to run it: from a source checkout, or from the one-dir bundle
`scripts/build_app.py` produces. [README.md](../README.md) covers installing from
source; this file covers the things that differ once it is built, plus the
Chromium step FillerBot needs in both cases.

## From source

```powershell
.\jobfinder.ps1                        # macOS/Linux: ./jobfinder.sh
```

Add `--no-browser` to print the URL instead of opening a tab. That is the only
flag the launcher has; everything else is passed straight through.

Use the launcher rather than calling Python directly. `python -m jobfinder`
needs **both** the virtualenv's interpreter and the repository root as the
working directory, and getting either wrong produces the same misleading error,
`No module named jobfinder`. On Windows this bites hardest inside
`.venv\Scripts`: a bare `python.exe` there does not run the interpreter sitting
beside it, because PowerShell never searches the current directory. The
launchers resolve their own location, so neither trap applies. By hand:

```bash
cd /path/to/JobFinder
.venv/bin/python -m jobfinder          # Windows: .venv\Scripts\python.exe -m jobfinder
```

## From the bundle

`scripts/build_app.py` writes a one-dir bundle to `dist/JobFinder/`:

| Platform | Run |
|---|---|
| Windows | `dist\JobFinder\JobFinder.exe` |
| macOS / Linux | `dist/JobFinder/JobFinder` |

Copy the whole `dist/JobFinder` folder — the executable needs the `_internal`
directory next to it. There is no installer and the binary is unsigned, so
expect a SmartScreen or Gatekeeper warning the first time; that is a deliberate
trade-off, explained in `scripts/build_app.py`'s docstring.

The bundle behaves exactly like the source run: ephemeral loopback port, browser
opened on an authenticated URL, same data directory, same stop control in the UI.
It is windowed on Windows, so it has no console to print to — if something fails
before the browser opens, the reason is in `logs/jobfinder.log` under the data
directory.

## The data directory

Config, the database, logs, uploaded resumes and the Chromium download all live
under one root:

| Platform | Location |
|---|---|
| Windows | `%LOCALAPPDATA%\JobFinder\JobFinder` |
| macOS | `~/Library/Application Support/JobFinder` |
| Linux | `~/.local/share/JobFinder` |

To print yours:

```bash
python -c "import platformdirs; print(platformdirs.user_data_dir('JobFinder'))"
```

Set `JOBFINDER_DATA_DIR` to use a different one. It is honoured verbatim, by both
the source run and the bundle, which makes it the clean way to try a throwaway
instance:

```bash
JOBFINDER_DATA_DIR=/tmp/jf-test .venv/bin/python -m jobfinder
```

## Chromium for FillerBot

FillerBot needs Playwright's Chromium, and JobFinder sets
`PLAYWRIGHT_BROWSERS_PATH` to `<data dir>/browsers` at startup — so Chromium has
to be installed *there*, not in Playwright's default location. From a source
checkout:

```bash
# macOS / Linux
PLAYWRIGHT_BROWSERS_PATH="$(.venv/bin/python -c "import jobfinder.paths as p; print(p.get_data_dir() / 'browsers')")" \
  .venv/bin/python -m playwright install chromium
```

```powershell
# Windows PowerShell
$env:PLAYWRIGHT_BROWSERS_PATH = (.venv\Scripts\python.exe -c "import jobfinder.paths as p; print(p.get_data_dir() / 'browsers')")
.venv\Scripts\python.exe -m playwright install chromium
```

If you set `JOBFINDER_DATA_DIR`, set it for that command too — the helper reads
it, so the browser lands in the same data directory the app will use.

For a machine that only has the bundle and no Python, there is no `playwright`
command available: install Chromium from a source checkout (or any venv with
`playwright` installed) with `PLAYWRIGHT_BROWSERS_PATH` pointed at the target
machine's `<data dir>/browsers`, and copy that folder across. Everything else in
JobFinder works without it; only FillerBot needs a browser.

## Stopping it

Use the stop control in the UI. It asks "Stop scanning and quit?" and then shuts
the server down cleanly, including the scheduler and any FillerBot browser still
open. Closing the browser tab does **not** stop anything — scanning carries on.

`Ctrl-C` in the terminal that started it works too, and takes the same shutdown
path.

## When something looks wrong

| Symptom | Cause |
|---|---|
| Blank page, but `GET /api/health` returns `{"status":"ok"}` | The SPA was never built. Run `npm ci && npm run build` in `frontend/`. The server logs this on startup too. |
| Launching seems to do nothing but open a tab, and the process exits 0 | JobFinder was already running. The second launch reopens the existing instance rather than starting a second server, by design. |
| "Scheduler not responding" in the heartbeat strip | The scheduler thread really is dead — that is a bug, not a display state. The log file is the place to look. |
| Scans return nothing | Possibly the unverified `python-jobspy` 1.4.0 HTTP backend — see README, Known gaps. |

Logs are at `<data dir>/logs/jobfinder.log` (rotating), and the in-page log panel
shows the same stream live.
