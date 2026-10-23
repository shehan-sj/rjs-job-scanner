# JobFinder

A single-user tool that watches job boards for new postings, scores each one
against your resume, and keeps track of what you applied to or dismissed. It
scans on a schedule and keeps state between runs.

**Version 4.0 — JobFinder is a locally-hosted web app.** It runs a server on
your own machine, bound to `127.0.0.1` only, and you use it in your browser.
There is no desktop app, no tray icon, no installer, and no account.

## Quick start

One script does the whole thing — it checks for Python and Node, offers to
install whatever is missing, creates the virtualenv, installs dependencies and
builds the web UI.

**Windows** (PowerShell):

```powershell
git clone https://github.com/shehan-sj/rjs-job-scanner.git
cd rjs-job-scanner
.\scripts\setup.ps1
```

**macOS / Linux**:

```bash
git clone https://github.com/shehan-sj/rjs-job-scanner.git
cd rjs-job-scanner
./scripts/setup.sh
```

Then start it:

```powershell
.\jobfinder.ps1                        # macOS/Linux: ./jobfinder.sh
```

Use those launchers rather than calling Python directly. `python -m jobfinder`
has two requirements that are easy to miss and that both fail with the same
confusing message, `No module named jobfinder`:

- **It must be the virtualenv's interpreter.** On Windows a bare `python.exe`
  typed *inside* `.venv\Scripts` does **not** run the one sitting there —
  PowerShell never searches the current directory, so it silently picks
  whatever Python is on PATH.
- **The working directory must be the repository root.** The package is not
  pip-installed into the venv; it is found because the repo root is the current
  directory.

The launchers resolve their own location, so neither depends on where you are
standing. Every argument is passed straight through, so `.\jobfinder.ps1
--no-browser` works. The equivalent by hand is:

```bash
cd /path/to/JobFinder
.venv/bin/python -m jobfinder          # Windows: .venv\Scripts\python.exe -m jobfinder
```

The setup scripts are **re-runnable** — every step detects what is already done
and skips it, so run them again after a `git pull`. They never install anything
system-wide without asking; pass `--yes` (`-Yes` on PowerShell) to accept those
prompts in advance.

| Flag | What it adds |
|---|---|
| `--yes` / `-Yes` | Answer yes to install prompts. For CI or an unattended machine. |
| `--with-chromium` / `-WithChromium` | Downloads the Chromium build FillerBot drives (~150 MB). Nothing else needs it. |
| `--bundle` / `-Bundle` | Also builds the standalone `dist/JobFinder/` folder (see [Deployment](#deployment)). |
| `--skip-frontend` / `-SkipFrontend` | Skips `npm ci` and the Vite build. |

### Doing it by hand

If you would rather not run a script, or one of its steps fails and you want to
take over partway:

Prerequisites:

- **Python 3.12+** (verified on 3.14.7)
- **Node.js + npm**, to build the web UI (verified on Node 26.7.0 / npm 11.19.0)

```bash
# 1. Python dependencies
python -m venv .venv
.venv/bin/python -m pip install -r requirements.txt       # Windows: .venv\Scripts\python.exe

# 2. Build the web UI (the server serves it; without this you get the API and a blank page)
cd frontend
npm ci
npm run build
cd ..

# 3. Run it
.venv/bin/python -m jobfinder
```

`openapi-typescript` 7.x declares a peer on TypeScript 5 while this project is
on TypeScript 6. That conflict is already resolved by an `overrides` entry in
`frontend/package.json`, so plain `npm ci` works — if you ever regenerate
`package-lock.json`, keep that entry or the install starts failing with
`ERESOLVE`.

The last command binds a free loopback port, waits until the server is actually
up, then opens your browser on an authenticated URL. Use `--no-browser` to print
the URL instead of opening a tab (handy over SSH); the same URL is written to
`logs/jobfinder.log` in the data directory.

## First run

The browser opens a single onboarding page with four sections: your resume, how
you want it read (locally by pattern, or by Claude), what was read from it, and
your Anthropic API key. The one-time disclosure of what reaches Anthropic sits
in the API-key section, because that is what it is about.

**Nothing is written until you press Start**, so abandoning the page halfway
leaves no half-configured install. Finishing it writes `config.yaml` into the
data directory.

**The API key is optional.** Without one JobFinder still scans every source and
still applies your filters — it scores matches by keyword overlap with your
resume instead of asking Claude to judge them. You can add a key later from
**Sources & keys**.

Every pre-filled value shows where it came from, and a field your resume did not
state is left blank and says so rather than being guessed at.

Everything it asks for can be changed later; changes apply without a restart.

## Everyday use

- **Scanning starts by itself.** The scheduler comes up with the server and runs
  on the interval in **Sources & keys**. **Scan now**, in the board's toolbar,
  forces a one-off scan without waiting for the next tick. The heartbeat at the
  foot of the rail shows whether the scheduler is alive and when the last scan
  ran.
- **One board, five stages.** Jobs move left to right as you act on them — New,
  Shortlist, Applied, Replied, Not for me — instead of separate tabs. Scans
  only ever add to New; everything to the right waits for you to move it
  there, by drag, by the keyboard (1-5), or from a job's own detail panel.
- **Filters narrow what is new.** Minimum score, freshness, arrangement and the
  hide toggles live in the board's Filters popover and apply only to the New
  column — a job you have already moved stays where you put it, whatever its
  score. Changing a filter re-filters the board immediately and persists
  without a round trip through a dialog.
- **Closing the tab does not stop scanning.** The server keeps running and keeps
  scanning. What you lose is the alert: browser notifications only fire while a
  JobFinder tab is open.
- **To actually stop it**, use the stop control in the UI (it asks "Stop scanning
  and quit?"). That shuts the server down cleanly, including the scheduler and any
  open FillerBot browser.
- **Launching JobFinder twice does not start a second server.** The second launch
  finds the running one and opens it in your browser.
- `GET /api/health` returns `{"status":"ok"}` without needing a session, if you
  want to check from a script whether the process is up.

Your config, database, logs, uploaded resume and cached profile all live in one
platform data directory (`%LOCALAPPDATA%\JobFinder\JobFinder`,
`~/Library/Application Support/JobFinder`, `~/.local/share/JobFinder`), and
`JOBFINDER_DATA_DIR` overrides it. [docs/RUNNING.md](docs/RUNNING.md) has the
details.

## What JobFinder sends to Anthropic

JobFinder calls the Anthropic API (Claude) with your own API key. This is the one
part of the app that sends your data off your machine, so here is exactly what
goes and when. (the product spec is the contract; the app also states this once
during onboarding.)

| What | When | Why |
|---|---|---|
| **The full text of your resume** | Once per resume file, the first time a scan needs it — then cached locally and not re-sent until you change the resume | To extract a profile (skills, tools, titles, seniority) used for scoring |
| **The profile extracted from your resume** | On every AI verification call, as a cached system prompt | So Claude can judge a posting against you |
| **Full job descriptions** (title, company, location, source, description text) | Only when AI verification is enabled, and only for postings that already scored at or above your minimum local score | Re-read the posting, confirm location/arrangement/type, re-score, flag clear mismatches |
| **A second, more detailed extraction of your resume** — name, address, phone, email, work and education history | Only if you use FillerBot, once per resume, then cached locally | To fill application forms |
| **Screenshots of the application page** | Only during a FillerBot session, for each step it plans | Claude Vision reads the page layout to decide what to fill |

What is **not** sent:

- Nothing is sent at all until you have configured an API key.
- The resume **file** never leaves your machine; only text extracted from it does.
  (FillerBot does upload the file itself to the job site you are applying on, after
  asking you.)
- Values you type into password fields during a FillerBot session are never
  included in what goes to the model.
- Screenshots and video frames are not written to disk by default.
- There is no telemetry, analytics, or crash reporting of any kind. Apart from the
  Anthropic API, JobFinder only talks to the job boards it scrapes.

Job scoring itself is a local keyword matcher; AI verification is the part that
sends job descriptions. **A fresh install seeds it on**, with a minimum local
score of 65, so by default postings scoring 65 or above are sent for
verification. Turn it off in Settings → AI verification if you would rather
nothing but the resume extraction leaves your machine. When it is on, the resume
profile travels as a cached prompt, so a batch of jobs reuses a single cache
entry instead of re-sending it per job.

## Resume handling

- **PDF and plain text only.** A Word document is rejected with a message saying
  so — `.docx` has never actually been parsed by this app (despite what an earlier
  version of this README implied), and accepting one would mean scoring you on
  mojibake while reporting success. Export to PDF.
- Uploads are also rejected if the file cannot be parsed, or parses to no text at
  all (an image-only or scanned PDF — OCR is not supported). A rejected upload
  leaves nothing behind.
- Maximum size is 10 MB.
- **The resume is uploaded, not referenced.** The server copies the file into its
  data directory and owns the stored name; it never accepts a filesystem path from
  the browser. Editing the original file on disk therefore no longer affects
  scoring — re-upload to update it.

## Deliberately given up in v4

v3 was a PySide6 desktop app. These capabilities were dropped on purpose when it
became a web app, and are listed in the product spec so they do not get rediscovered
as bugs:

| Lost capability | Status |
|---|---|
| Always-on-top floating widget | **Dropped.** No web equivalent. Ambient glanceability is not replaced. |
| System tray icon and tray menu | **Dropped.** No tray presence in v4. |
| Notifications with no UI open | **Dropped.** Browser notifications fire only while a JobFinder tab is open. |
| Remembered window position | **Dropped.** The browser owns window geometry. |
| Separate always-on-top log console window | **Replaced** by an in-page collapsible log panel. |
| Auto-launch on login | **Not provided** by v4. You start the app. |

Scanning itself still runs server-side with no tab open; only the alert is lost.

## Security model

The server is loopback-only and single-user, and holds your API key, so a few
things are load-bearing rather than optional:

- Binds `127.0.0.1` on an OS-assigned port. Never `0.0.0.0`.
- Each launch mints a random token, delivered in the URL that is opened, exchanged
  once by the UI for an `HttpOnly`, `SameSite=Strict`, host-only cookie and then
  stripped from the URL. Every API and stream route requires it.
- `Host` and `Origin` are checked against exact allowlists — that is the
  DNS-rebinding defence, and why state-changing requests from another page fail.
- API keys are write-only over HTTP: reads report only whether a key is set, and
  logs and error responses are redacted.

## FillerBot (assisted form filling)

FillerBot drives a real Chromium with Claude Vision to fill in an application
form. You review and edit every field, and it never clicks Submit — that rule is
enforced in server code, not just in the prompt.

It needs a Chromium installed once, in a specific place — the setup scripts do
it for you with `--with-chromium` (`-WithChromium`). See
[FillerBot needs a Chromium](#fillerbot-needs-a-chromium) under Deployment for
why the location matters, and **[docs/RUNNING.md](docs/RUNNING.md)** for the
manual and packaged-app forms of the command.

## Deployment

JobFinder is **not a hosted service and cannot be deployed as one**. The server
binds `127.0.0.1` only, holds a single user's config and API key, and has no
accounts, no multi-tenancy and no TLS. Putting it on a shared or public host
would expose your Anthropic key and your resume to anyone who could reach the
port. "Deploying" it means getting it onto a machine *you* sit at.

There are two ways to do that.

### Option A — run from source

Best when the target machine has (or can get) Python and Node, and when you
want to pull updates with `git pull`.

```powershell
git clone https://github.com/shehan-sj/rjs-job-scanner.git
cd rjs-job-scanner
.\scripts\setup.ps1              # macOS/Linux: ./scripts/setup.sh
.\jobfinder.ps1                  # macOS/Linux: ./jobfinder.sh
```

To update later:

```bash
git pull
./scripts/setup.sh               # Windows: .\scripts\setup.ps1
```

Re-running setup is the update step. It reinstalls dependencies and rebuilds the
UI; it does not touch your data directory, so your config, database and resume
survive.

### Option B — a standalone bundle

Best when the target machine has no Python and you do not want to install one.

```powershell
.\scripts\setup.ps1 -Bundle      # macOS/Linux: ./scripts/setup.sh --bundle
```

That installs PyInstaller, builds the web UI, and produces a one-dir bundle at
`dist/JobFinder/`. Copy that whole folder to the target machine and run
`JobFinder.exe` (`JobFinder` on macOS/Linux) inside it.

**Build on the platform you are deploying to.** PyInstaller bundles a real
interpreter and native extensions, so a Windows build does not run on macOS or
Linux and vice versa. There is no cross-compilation.

The bundle is roughly 190 MB and is **one-dir, not one-file**, on purpose: a
one-file build re-extracts itself into a fresh temp directory on every launch,
which would both re-unpack ~100 MB each time and make the resource path change
between runs. `scripts/build_app.py`'s docstring has the full reasoning.

There is deliberately **no installer and no code signing**. An installer around
an unsigned binary just adds a second scary prompt, and signing needs
certificates this repo should not hold. On first launch Windows SmartScreen will
warn about an unrecognised publisher — that is expected for an unsigned binary.

### FillerBot needs a Chromium

FillerBot is the only feature that drives a browser, and the Chromium it uses is
**not** part of either the source install or the bundle.

```powershell
.\scripts\setup.ps1 -WithChromium    # macOS/Linux: ./scripts/setup.sh --with-chromium
```

It has to land in the right place: `jobfinder/paths.py` points
`PLAYWRIGHT_BROWSERS_PATH` at `<data dir>/browsers` at startup, and
`playwright install` does not read `JOBFINDER_DATA_DIR`, so a plain
`playwright install chromium` puts it somewhere JobFinder will not look. The
setup scripts resolve that path for you. [docs/RUNNING.md](docs/RUNNING.md) has
the manual form and the copy-to-another-machine case.

### Your data lives outside the install

Config, database, logs, uploaded resume and cached profile all live in one
platform data directory, **not** in the repo or the bundle:

| Platform | Location |
|---|---|
| Windows | `%LOCALAPPDATA%\JobFinder\JobFinder` |
| macOS | `~/Library/Application Support/JobFinder` |
| Linux | `~/.local/share/JobFinder` |

`JOBFINDER_DATA_DIR` overrides it, which is how you keep a throwaway instance
away from your real one:

```bash
JOBFINDER_DATA_DIR=/tmp/jf-test .venv/bin/python -m jobfinder
```

That directory is what to back up, and the only thing worth copying between
machines. **`config.yaml` holds your Anthropic API key in plain text** — treat a
copy of it the way you would treat the key itself, and do not commit it
anywhere.

### Stopping it

Closing the browser tab does **not** stop scanning; the server keeps running.
Use **Quit** at the foot of the rail, which shuts down the scheduler, any open
FillerBot browser, and the server itself. [docs/RUNNING.md](docs/RUNNING.md)
covers the case where the UI is unreachable and you need to kill the port.

## Known gaps

- **`google` and `zip_recruiter` return nothing.** Both return zero rows with no
  error against a live scrape; `zip_recruiter` does so even for a US location,
  so it is not a coverage artefact. Neither is enabled by default — LinkedIn is
  the only source on out of the box, and LinkedIn and Indeed both verified live
  on `python-jobspy` 1.4.0's `curl_cffi` backend.
- The browser tab is still titled "frontend".
- **The "maximum post age" quality filter does nothing.** The code that applies
  it reads a field nothing in the app ever sets, and the posting date is not
  persisted at all, so it cannot work even with that read fixed. Every other
  quality filter works. Tracked in the product spec.

## License

MIT
