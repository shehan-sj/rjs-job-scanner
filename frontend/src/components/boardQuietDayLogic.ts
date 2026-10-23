// T85 — the quiet day (the task ledger / the mockup's "First run, and the
// ordinary quiet day"): New is empty because nothing cleared the bar,
// while the other columns hold content. docs/DESIGN.md/the product spec's sparse-
// state principle already shipped once for the Matches tab (T65,
// `sparseStateLogic.ts`'s `buildSparseMatchesCopy`) — this is the board's
// New column telling the same kind of truth ("the scan worked, here is
// exactly what it found") rather than the void the brief calls out.
//
// Every number here traces to a real, already-persisted field
// (`jobfinder/web/scan.py`'s `ScanState`, `GET /api/state`,
// `api/state.ts`'s `ScanStateSnapshot`) or a real setting (`GET
// /api/settings`'s `check_interval_minutes`/`match_threshold`,
// `api/settings.ts`). Nothing below is invented: a scan's *total fetched*
// count (including already-seen duplicates, `len(all_jobs)` in
// `jobfinder/orchestrator.py`) is never persisted anywhere the server
// hands to the client — only logged — so this does not claim that number.
// `postingsRead` is `last_new_jobs`: the count of genuinely new postings
// the last scan actually tried to score, the same number T65's
// `jobsScanned` already uses for the identical "how many did the scan
// check" sentence on the Matches tab.
import type { Stage } from "../types/job";

export interface QuietDayScanInfo {
  /** `last_scan_completed_at !== null` (api/state.ts). */
  completed: boolean;
  /** `last_scan_completed_at`, ISO-8601 — used only to name the clock
   * time the sentence quotes ("The 15:26 scan..."), never to derive a
   * count. */
  lastScanCompletedAt: string | null;
  /** `last_new_jobs` — see module docstring: the real "how many postings
   * did the last scan actually read and try to score" number. */
  postingsRead: number;
  /** `last_best_score` — `null` means nothing was scored at all. */
  bestScore: number | null;
  /** The board's current minimum-score bar (`match_threshold`,
   * `SettingsPayload`), or `null` if settings could not be read — the
   * "None scored N% or better" clause is omitted rather than guessed
   * when this is unavailable. */
  matchThreshold: number | null;
  /** `check_interval_minutes` (`SettingsPayload`), or `null` if settings
   * could not be read. */
  checkIntervalMinutes: number | null;
  /** `scan_cycle_scheduled` (api/state.ts) — periodic scanning is
   * actually on. When false, no next-scan time is computed; scanning is
   * paused is the true statement instead. */
  scanScheduled: boolean;
}

export interface QuietDayAction {
  label: string;
  /** The `match_threshold` value a click on this action should write
   * (`PUT /api/settings`), same convention as `sparseStateLogic.ts`'s
   * `SparseMatchesAction.thresholdTarget`. */
  matchThreshold: number;
}

export interface QuietDayCopy {
  title: string;
  /** The scan sentence ("The 15:26 scan read 36 postings. ..."), or
   * `null` when no scan has ever completed. Built only from the fields
   * that actually exist on `scan` — see `buildQuietDayCopy` below for
   * which clauses degrade when a field is unavailable. */
  scanSummary: string | null;
  /** When the next scan runs, or why there isn't one — never `null`,
   * always says something true. */
  nextScanText: string;
  /** Zero, one, or two actions — empty when there is no score to act on
   * (nothing was ever scored this cycle). */
  actions: QuietDayAction[];
}

/** Naive-UTC parse, same convention `boardLogic.ts`'s `parseNaiveUtcMs`
 * already established for this wire format: no offset means UTC, not
 * local time (`jobfinder/dto.py`'s module docstring). */
function parseNaiveUtcMs(iso: string): number | null {
  const hasZone = /[Zz]|[+-]\d{2}:?\d{2}$/.test(iso);
  const t = Date.parse(hasZone ? iso : `${iso}Z`);
  return Number.isNaN(t) ? null : t;
}

function pad2(n: number): string {
  return n < 10 ? `0${n}` : `${n}`;
}

/** "15:26", UTC — rendered in the clock the server's timestamps are
 * actually in, not whatever the browser's local offset happens to be, so
 * this stays testable independent of the machine's timezone (same reason
 * `boardLogic.ts`'s `dateLabel` renders in UTC). */
function formatClockTime(ms: number): string {
  const d = new Date(ms);
  return `${pad2(d.getUTCHours())}:${pad2(d.getUTCMinutes())}`;
}

function pluralPostings(n: number): string {
  return `${n} posting${n === 1 ? "" : "s"}`;
}

/** The scan sentence, or `null` if no scan has ever completed. Degrades
 * clause by clause as fields are unavailable (module docstring): the
 * clock time is dropped if the timestamp doesn't parse, the "None scored
 * N% or better" clause is dropped if `matchThreshold` is unknown, and the
 * "best came in at" clause is dropped if nothing was scored at all. */
function buildScanSummary(scan: QuietDayScanInfo): string | null {
  if (!scan.completed) return null;

  if (scan.postingsRead === 0) {
    return "The last scan found nothing new to read.";
  }

  const completedMs = scan.lastScanCompletedAt ? parseNaiveUtcMs(scan.lastScanCompletedAt) : null;
  const time = completedMs !== null ? formatClockTime(completedMs) : "";
  const scanLabel = time ? `The ${time} scan` : "The last scan";
  const readClause = `${scanLabel} read ${pluralPostings(scan.postingsRead)}.`;

  if (scan.bestScore === null) {
    return `${readClause} None of them could be scored.`;
  }

  if (scan.matchThreshold !== null) {
    return `${readClause} None scored ${scan.matchThreshold}% or better, and the best came in at ${scan.bestScore}%.`;
  }
  return `${readClause} The best came in at ${scan.bestScore}%.`;
}

function buildNextScanText(scan: QuietDayScanInfo): string {
  if (!scan.scanScheduled) {
    return "Automatic scanning is paused.";
  }
  if (scan.checkIntervalMinutes === null || !scan.lastScanCompletedAt) {
    return "";
  }
  const base = parseNaiveUtcMs(scan.lastScanCompletedAt);
  if (base === null) return "";
  const time = formatClockTime(base + scan.checkIntervalMinutes * 60_000);
  return `Next scan around ${time}.`;
}

function buildActions(scan: QuietDayScanInfo): QuietDayAction[] {
  if (scan.bestScore === null) return [];
  const actions: QuietDayAction[] = [
    { label: `Lower the bar to ${scan.bestScore}%`, matchThreshold: scan.bestScore },
  ];
  if (scan.matchThreshold === null || scan.matchThreshold > 0) {
    // The label says what the button does, not what it is for.
    //
    // There is no server-side way to view below-threshold New cards
    // without changing the persisted threshold — T89 made the filters
    // server-side and no preview parameter exists. So this button sets
    // the bar to 0 and leaves it there. "Show the ones under the bar"
    // promised a view and delivered a settings write, which is the
    // class of quiet dishonesty this whole phase has been removing; a
    // user who clicks it and later wonders why every scan floods New
    // deserved to be told here.
    actions.push({ label: "Set my bar to 0 and show everything", matchThreshold: 0 });
  }
  return actions;
}

/** "You are caught up" — the board's New-column quiet-day state (this
 * task's brief). Always returns a title; `scanSummary`/`nextScanText`/
 * `actions` each degrade to a true, lesser statement rather than
 * fabricating a number that was never persisted (module docstring). */
export function buildQuietDayCopy(scan: QuietDayScanInfo): QuietDayCopy {
  return {
    title: "You are caught up",
    scanSummary: buildScanSummary(scan),
    nextScanText: buildNextScanText(scan),
    actions: buildActions(scan),
  };
}

// ── First run (this task's brief: "first run, and the ordinary quiet
// day" are the two states that are the common case). Verbatim copy, one
// entry per stage — static, so this is data rather than a function, but
// lives alongside `buildQuietDayCopy` since both exist to answer "what
// does an empty column say" for the board's two sparse states. ─────────

export interface FirstRunColumnCopy {
  title: string;
  subtitle: string;
  /** Only `"new"` carries the scan CTA. */
  scanCta: boolean;
}

export const FIRST_RUN_COLUMN_COPY: Record<Stage, FirstRunColumnCopy> = {
  new: {
    title: "Nothing scanned yet",
    subtitle:
      "Once scanning is on, every job above your bar lands here by itself, every three hours.",
    scanCta: true,
  },
  shortlist: {
    title: "Worth coming back to",
    subtitle:
      "Drag a job here when you want it but have not written the application yet. Nothing here expires.",
    scanCta: false,
  },
  applied: {
    title: "Sent",
    subtitle: "The date is stamped when a card arrives, so you can see which applications are going stale.",
    scanCta: false,
  },
  replied: {
    title: "When someone answers",
    subtitle: "JobFinder cannot read your email, so you move the card here yourself and add the date.",
    scanCta: false,
  },
  out: {
    title: "Dismissed",
    subtitle: "Yours and the AI filter's, each saying which. Nothing is deleted — drag one back any time.",
    scanCta: false,
  },
};

/** Whether the board is in the "first run" state at all — the product spec/this
 * task's brief: every column empty, nothing scanned yet. A board with
 * even one card anywhere is not first-run, regardless of which column
 * holds it. */
export function isFirstRunBoard(countsByStage: Record<Stage, number>): boolean {
  return Object.values(countsByStage).every((n) => n === 0);
}
