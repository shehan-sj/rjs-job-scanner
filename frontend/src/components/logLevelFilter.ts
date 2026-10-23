// T97 — the log drawer's per-level hide filter. Pure mapping from "all
// collected lines" + "which levels the user has hidden" to "what's
// visible" + "how many got hidden", so `LogDrawer.tsx` never has to
// silently shrink the list without saying so (owner: "console needs a
// hide warnings feature" — a console that quietly drops lines is worse
// than one that shows too many, because the user can't tell "nothing
// happened" from "I hid it").
//
// `ALL_LOG_LEVELS` is the same five values `LogDrawer.tsx`'s
// `LEVEL_CLASS` already classifies every line into
// (DEBUG/INFO/WARNING/ERROR/CRITICAL) — this module doesn't invent a
// new taxonomy, it just filters the existing one.
//
// `DEFAULT_HIDDEN_LEVELS` is empty — nothing hidden until the user asks.
// This is not a style choice: the task's one non-negotiable rule is that
// a fresh user must never miss a failure they didn't choose to hide, so
// ERROR and CRITICAL (and everything else) start visible. See
// `defaultHiddenLevelsExcludesErrorAndCritical` in the test file, which
// exists specifically so a future change to this default fails loudly.
export type LogLevel = "DEBUG" | "INFO" | "WARNING" | "ERROR" | "CRITICAL";

export const ALL_LOG_LEVELS: readonly LogLevel[] = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"];

export const DEFAULT_HIDDEN_LEVELS: readonly LogLevel[] = [];

/** The minimal shape this module needs from a log line — just its level,
 * so it can be used against `LogStreamEvent` (api/stream.ts) without
 * importing that type and without the test fixtures having to fake a
 * whole SSE event. */
export interface LevelTagged {
  level: string;
}

export interface LogLevelFilterResult<T extends LevelTagged> {
  visible: T[];
  hiddenCount: number;
}

/** Splits `lines` into what survives `hiddenLevels` and how many did not.
 * A line whose `level` isn't one of the five known values (defensive —
 * the SSE payload is server-controlled but not a closed type at this
 * boundary) is never hidden by this function: an unrecognised level
 * should fail open (stay visible), the same reasoning `LEVEL_CLASS`'s
 * `?? "log-level-info"` fallback already applies to styling it. */
export function filterLogLines<T extends LevelTagged>(
  lines: readonly T[],
  hiddenLevels: ReadonlySet<string> | readonly string[],
): LogLevelFilterResult<T> {
  const hidden = hiddenLevels instanceof Set ? hiddenLevels : new Set(hiddenLevels);
  const visible: T[] = [];
  let hiddenCount = 0;
  for (const line of lines) {
    if (ALL_LOG_LEVELS.includes(line.level as LogLevel) && hidden.has(line.level)) {
      hiddenCount += 1;
    } else {
      visible.push(line);
    }
  }
  return { visible, hiddenCount };
}

/** Toggles one level in/out of a hidden-levels list, returning a new
 * array (never mutating `current`) — the shape `putSettings`'s partial
 * `ui` body and `useState` setters both want. */
export function toggleHiddenLevel(current: readonly string[], level: LogLevel): string[] {
  return current.includes(level) ? current.filter((l) => l !== level) : [...current, level];
}
