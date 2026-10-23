// T65 — "Sparse states are designed, not incidental" (the task ledger /
// docs/DESIGN.md's "Superseded 2026-10-22" block): "nothing above the
// threshold" must say the scan worked, name the highest score it
// actually found, and offer the control that changes the outcome,
// rather than the single "No matches..." line every tier's empty state
// (`emptyMatchCopy`, JobsView.tsx) already shows regardless of whether a
// scan ever ran. Pure logic, no DOM/React — same pattern as
// jobSelection.ts/filterPanelLogic.ts — exercised by
// JobsView.tsx/MatchesPane and backed by `GET /api/state`'s
// `last_new_jobs`/`last_best_score` (api/state.ts, jobfinder/web/scan.py).

/** The slice of `ScanStateSnapshot` (api/state.ts) this needs. Kept as
 * its own small shape, not the full snapshot, so this module stays
 * independent of the fetch layer and easy to unit test. */
export interface LastScanInfo {
  /** `last_scan_completed_at !== null` — whether any scan has ever
   * finished in this session's lifetime of the running server. */
  completed: boolean;
  /** `last_new_jobs` — how many genuinely new jobs the last scan fetched
   * and tried to score (not how many cleared the threshold). */
  jobsScanned: number;
  /** `last_best_score` — the highest score among every job the last
   * scan actually scored, `null` if nothing was scored at all. */
  bestScore: number | null;
}

export interface SparseMatchesAction {
  label: string;
  /** The `match_threshold` value a click on this action should write
   * (`PUT /api/settings`) — exactly `bestScore`, so the job that scored
   * highest now clears the (lowered) bar. */
  thresholdTarget: number;
}

export interface SparseMatchesCopy {
  title: string;
  subtitle: string;
  /** `null` when there's nothing a control could change — no scan has
   * run yet, or the last one found/scored nothing at all. */
  action: SparseMatchesAction | null;
}

function pluralJobs(n: number): string {
  return `${n} job${n === 1 ? "" : "s"}`;
}

/** The copy for "nothing above the threshold" (Matches, all tiers
 * combined) once a scan has actually run — the state the owner's build
 * showed as "36 jobs scanned and one grey line in a void": the scan
 * worked, it just found nothing worth surfacing at the current minimum
 * score, and that number is exactly as actionable as it sounds. */
export function buildSparseMatchesCopy(scan: LastScanInfo): SparseMatchesCopy {
  if (!scan.completed) {
    return {
      title: "No matches yet.",
      subtitle: "Run a scan to see how your résumé matches real postings.",
      action: null,
    };
  }

  if (scan.jobsScanned === 0) {
    return {
      title: "The last scan found no new postings.",
      subtitle: "Every source came back empty, or everything had already been seen. Try again later.",
      action: null,
    };
  }

  if (scan.bestScore === null) {
    return {
      title: `The last scan checked ${pluralJobs(scan.jobsScanned)}, but none of them could be scored.`,
      subtitle: "Check the activity log for matching errors.",
      action: null,
    };
  }

  return {
    title: `The scan worked: it checked ${pluralJobs(scan.jobsScanned)}, but none cleared your minimum score.`,
    subtitle: `The highest it found was ${scan.bestScore}%.`,
    action: {
      label: `Lower minimum score to ${scan.bestScore}%`,
      thresholdTarget: scan.bestScore,
    },
  };
}
