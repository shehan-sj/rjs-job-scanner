// The heartbeat strip's "next scan" line, as a pure mapping — same split
// as `scanControlLogic.ts`: the component renders, this module decides, and
// `tests/nextScanLabel.test.ts` can exercise it with no DOM.
//
// Why it exists. The strip only ever had `scan_cycle_scheduled`, a boolean,
// and the periodic cycle's first run lands a full interval out (20 minutes
// by default). So an armed, perfectly healthy scanner and one with no
// schedule at all both rendered "Scheduler idle" plus an unchanging "Last
// scan" for twenty minutes — which is precisely the reading behind the
// owner's report that the scan policy "doesn't seem to be firing ... more
// than one time". Naming the next fire time is what separates the two.
//
// Takes its own input shape rather than importing `ScanStateSnapshot`, so
// the field names this depends on are stated here explicitly.
export interface NextScanInput {
  schedulerAlive: boolean;
  scanCycleScheduled: boolean;
  /** ISO-8601 from `GET /api/state`'s `next_scan_at`, or `null`. */
  nextScanAt: string | null;
}

/** The line to show, or `null` when there is nothing truthful to say — the
 * caller drops the part entirely rather than rendering a placeholder.
 *
 * `null` for a dead scheduler on purpose: the status part already says
 * "Scheduler not responding", and a "next scan" promise beside it would
 * contradict it. "A silently dead scheduler is a defect, not a display
 * state" cuts both ways.
 */
export function nextScanLabel(input: NextScanInput, now: Date = new Date()): string | null {
  if (!input.schedulerAlive) {
    return null;
  }
  if (!input.scanCycleScheduled) {
    return "Automatic scanning is paused";
  }
  if (input.nextScanAt === null) {
    // Armed, but the server did not report a time. Still better than
    // silence: it tells the user a cycle exists.
    return "Next scan: scheduled";
  }
  const due = new Date(input.nextScanAt);
  if (Number.isNaN(due.getTime())) {
    return "Next scan: scheduled";
  }
  // A fire time in the past means it is due now and the worker has not
  // picked it up yet (or the machine just woke, which with the scheduler's
  // infinite misfire grace runs the scan late rather than skipping it —
  // jobfinder/scheduler.py). "Next scan: 09:12" for a moment already gone
  // reads as a stuck clock, so say what is actually true.
  if (due.getTime() <= now.getTime()) {
    return "Next scan: due now";
  }
  return `Next scan: ${due.toLocaleTimeString()}`;
}
