import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { fetchFullSettings, putSettingsPatch } from "../api/settings";
import { subscribeScanStream, type LogStreamEvent } from "../api/stream";
import { ALL_LOG_LEVELS, DEFAULT_HIDDEN_LEVELS, filterLogLines, toggleHiddenLevel, type LogLevel } from "./logLevelFilter";
import "./LogDrawer.css";

// In-page log drawer (T27) — replaces the desktop app's always-on-top
// floating console window (`jobfinder/ui/log_console.py`). Ports its
// real behaviour rather than redesigning it:
//
//   * 600-line cap — `LogConsoleWindow.MAX_LINES` /
//     `QPlainTextEdit.setMaximumBlockCount(600)`, which keeps the most
//     recently appended 600 lines and silently drops the oldest as new
//     ones arrive. `MAX_LINES` below is the same number, enforced the
//     same way (drop-from-the-front on push), see `pushLine`.
//   * Level colouring — `_append`'s `colors` map (DEBUG/INFO/WARNING/
//     ERROR/CRITICAL), reproduced as CSS classes against the same
//     `--jf-score-*`/`--jf-text-*` tokens `theme.py`'s palette maps onto
//     (`tokens.css`).
//   * Clear button — `_clear`.
//   * Timestamp/level toggles — `_append`'s `show_ts`/`show_lvl`, driven
//     by `config.ui.console_show_timestamp`/`console_show_level`. Those
//     two checkboxes already exist on the Settings screen's System tab
//     (T25, `SettingsTabs.tsx`); this drawer only *reads* that config
//     (via `GET /api/settings`) to decide what to render — it does not
//     duplicate the controls.
//
//   * Per-level hide filter (T97, owner: "console needs a hide warnings
//     feature") — unlike the two toggles above, this control lives *in*
//     the drawer itself (there was nothing on the desktop app or the
//     Settings screen to port), because it's something the user reaches
//     for while actually looking at the console, not while configuring
//     it ahead of time. Persisted the same way — `config.ui`, via
//     `GET`/`PUT /api/settings`, as `console_hidden_levels` — but written
//     through `putSettingsPatch`'s genuinely-partial body (same helper
//     T59's inline filter panel uses) rather than the Settings screen's
//     full-form `putSettings`, since a drawer toggle has no form session
//     to round-trip. `logLevelFilter.ts` is the pure
//     "lines + hidden levels -> visible lines + hidden count" mapping;
//     this component only wires it to state and to the toggle UI. The
//     count of hidden lines is always shown rather than the list
//     silently shrinking (see that module's docstring), and the default
//     hides nothing — a fresh user must never miss an ERROR/CRITICAL
//     line they never chose to hide.
//
// Collecting/accumulating log lines happens unconditionally, independent
// of collapsed/expanded — `LogConsoleWindow._poll` keeps draining its
// queue into the text view on a timer even while the window is hidden
// (`.hide()` doesn't stop the `QTimer`). The SSE subscription here is
// opened once at mount for the same reason: a collapsed drawer should
// not lose scan activity that happened while it was closed.
//
// Collapsed/expanded persists across reloads via `localStorage` — a
// drawer-local UI preference, not a server-side setting. This is
// deliberately *not* wired to `config.ui.show_log_console` (also exposed
// on the System tab): that field drives the full round-trip
// `PUT /api/settings` body (`settingsForm.ts`'s `buildSettingsUpdate`,
// which reconstructs every settings section including per-source
// credentials) and this drawer's own collapse toggle should not risk a
// partial/stale write to that payload just to flip one unrelated
// boolean. `config.ui.show_log_console` is still honoured as the
// *initial* default the first time this drawer ever renders in a
// browser with no stored preference yet.
const STORAGE_KEY = "jf.logDrawer.collapsed";
const MAX_LINES = 600;

const LEVEL_CLASS: Record<string, string> = {
  DEBUG: "log-level-debug",
  INFO: "log-level-info",
  WARNING: "log-level-warning",
  ERROR: "log-level-error",
  CRITICAL: "log-level-critical",
};

function readStoredCollapsed(): boolean | null {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (raw === "true") return true;
    if (raw === "false") return false;
    return null;
  } catch {
    return null;
  }
}

function storeCollapsed(collapsed: boolean): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, collapsed ? "true" : "false");
  } catch {
    // Storage disabled/full — collapse state just won't survive reload.
  }
}

/** Shortened logger name, port of `_append`'s `name` derivation
 * (`log_console.py:198`). */
function shortLoggerName(logger: string): string {
  return logger.replace("jobfinder.", "").replace("apscheduler.", "sched.");
}

function formatTime(epochSeconds: number): string {
  // `datetime.fromtimestamp(record.created).strftime("%H:%M:%S")`.
  const d = new Date(epochSeconds * 1000);
  const hh = String(d.getHours()).padStart(2, "0");
  const mm = String(d.getMinutes()).padStart(2, "0");
  const ss = String(d.getSeconds()).padStart(2, "0");
  return `${hh}:${mm}:${ss}`;
}

/** T60 — exposes `toggle()` so the rail foot's "Activity log" button
 * (Rail.tsx) can drive the same collapse state this drawer already
 * owned before the rail existed, rather than duplicating it. The drawer
 * keeps its own in-header toggle too (useful once it's open), so there
 * are now two buttons that call the same one piece of state — not two
 * copies of the state itself. */
export interface LogDrawerHandle {
  toggle: () => void;
}

export const LogDrawer = forwardRef<LogDrawerHandle>(function LogDrawer(_props, ref): ReactNode {
  const [collapsed, setCollapsed] = useState<boolean>(() => readStoredCollapsed() ?? true);
  const [lines, setLines] = useState<LogStreamEvent[]>([]);
  const [showTimestamp, setShowTimestamp] = useState(true);
  const [showLevel, setShowLevel] = useState(true);
  const [hiddenLevels, setHiddenLevels] = useState<string[]>(() => [...DEFAULT_HIDDEN_LEVELS]);
  const scrollRef = useRef<HTMLDivElement | null>(null);

  const refreshDisplaySettings = useCallback(async () => {
    const settings = await fetchFullSettings().catch(() => null);
    if (settings === null) {
      return;
    }
    setShowTimestamp(settings.ui.console_show_timestamp);
    setShowLevel(settings.ui.console_show_level);
    setHiddenLevels(settings.ui.console_hidden_levels);
    // Only seed the initial collapse state from config the first time
    // this browser has never had its own stored preference — once the
    // user has toggled the drawer, that local choice wins on every later
    // load (see module docstring).
    if (readStoredCollapsed() === null) {
      setCollapsed(!settings.ui.show_log_console);
    }
  }, []);

  // Load display-toggle defaults once at mount.
  useEffect(() => {
    void refreshDisplaySettings();
  }, [refreshDisplaySettings]);

  // Subscribe to the SSE log events once, independent of collapsed state
  // — see module docstring.
  useEffect(() => {
    const unsubscribe = subscribeScanStream((event) => {
      if (event.type !== "log") {
        return;
      }
      setLines((prev) => {
        const next = prev.length >= MAX_LINES ? prev.slice(prev.length - MAX_LINES + 1) : prev;
        return [...next, event];
      });
    });
    return unsubscribe;
  }, []);

  // Auto-scroll to the newest line, porting `_append`'s
  // `self._view.ensureCursorVisible()`.
  useEffect(() => {
    const el = scrollRef.current;
    if (el !== null) {
      el.scrollTop = el.scrollHeight;
    }
  }, [lines]);

  const toggleCollapsed = useCallback(() => {
    setCollapsed((prev) => {
      const next = !prev;
      storeCollapsed(next);
      if (!next) {
        // Expanding — pick up any Settings-tab toggle changes made while
        // this drawer sat collapsed.
        void refreshDisplaySettings();
      }
      return next;
    });
  }, [refreshDisplaySettings]);

  const handleClear = useCallback(() => {
    setLines([]);
  }, []);

  // Toggling a level writes through immediately (no debounce queue, no
  // Save button) — there's exactly one field in flight at a time, unlike
  // T59's filter panel which can have several form fields pending at
  // once. Optimistic local update first so the checkbox/chip responds
  // the instant it's clicked; `putSettingsPatch` persists it the same
  // way T59's panel and BoardView's threshold slider already do partial
  // `ui`/`match_threshold` writes.
  const handleToggleLevel = useCallback((level: LogLevel) => {
    setHiddenLevels((prev) => {
      const next = toggleHiddenLevel(prev, level);
      void putSettingsPatch({ ui: { console_hidden_levels: next } }).catch(() => {
        // Best-effort — same failure posture as this drawer's other
        // settings reads/writes (refreshDisplaySettings swallows a
        // failed GET too). A dropped write just means the choice
        // reverts to its last-saved value on the next reload; it does
        // not corrupt what's currently rendered.
      });
      return next;
    });
  }, []);

  const { visible: visibleLines, hiddenCount } = filterLogLines(lines, hiddenLevels);

  useImperativeHandle(ref, () => ({ toggle: toggleCollapsed }), [toggleCollapsed]);

  return (
    <div className={`log-drawer ${collapsed ? "log-drawer-collapsed" : "log-drawer-expanded"}`}>
      <div className="log-drawer-header">
        <button
          type="button"
          className="log-drawer-toggle"
          onClick={toggleCollapsed}
          aria-expanded={!collapsed}
        >
          {collapsed ? "▲" : "▼"} Log{lines.length > 0 ? ` (${lines.length})` : ""}
        </button>
        {!collapsed ? (
          <button type="button" className="log-drawer-clear" onClick={handleClear}>
            Clear
          </button>
        ) : null}
      </div>
      {!collapsed ? (
        <div className="log-drawer-filters">
          {ALL_LOG_LEVELS.map((level) => {
            const isHidden = hiddenLevels.includes(level);
            return (
              <label
                key={level}
                className={`log-drawer-filter-chip ${LEVEL_CLASS[level]} ${isHidden ? "log-drawer-filter-chip-hidden" : ""}`}
              >
                <input
                  type="checkbox"
                  checked={!isHidden}
                  onChange={() => handleToggleLevel(level)}
                  aria-label={`Show ${level} lines`}
                />
                {level}
              </label>
            );
          })}
          {hiddenCount > 0 ? (
            <span className="log-drawer-hidden-count">
              {hiddenCount} line{hiddenCount === 1 ? "" : "s"} hidden
            </span>
          ) : null}
        </div>
      ) : null}
      {!collapsed ? (
        <div className="log-drawer-body" ref={scrollRef}>
          {lines.length === 0 ? (
            <div className="log-drawer-empty">No log activity yet.</div>
          ) : visibleLines.length === 0 ? (
            <div className="log-drawer-empty">
              All {lines.length} line{lines.length === 1 ? "" : "s"} hidden by the filter above.
            </div>
          ) : (
            visibleLines.map((line) => (
              <div key={line.seq} className={`log-drawer-line ${LEVEL_CLASS[line.level] ?? "log-level-info"}`}>
                {showTimestamp ? <span className="log-drawer-ts">{formatTime(line.time)}</span> : null}
                {showLevel ? <span className="log-drawer-lvl">{line.level}</span> : null}
                <span className="log-drawer-logger">{shortLoggerName(line.logger)} —</span>
                <span className="log-drawer-msg">{line.message}</span>
              </div>
            ))
          )}
        </div>
      ) : null}
    </div>
  );
});
