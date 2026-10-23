// T95 — one scan control, driven by `GET /api/state`'s three independent
// booleans (`running`, `scan_cycle_scheduled`, `scheduler_alive`), not by
// local click state. `BoardView.tsx` used to toggle its label off
// `scanRequested`, a boolean that only recorded this tab's own click — a
// scan started by the schedule, or by another tab, left the button
// reading "Scan now" throughout. This module is the pure mapping from
// state to label/action; `ScanControl.tsx` just renders it.
//
// There is deliberately no "stop" anywhere below. `jobfinder/scheduler.py`'s
// `JobScheduler.pause()` docstring: a scan already running is on a worker
// thread doing blocking HTTP and can't be cleanly aborted — `pause()` only
// prevents the *next* scheduled cycle and drops a queued-but-not-yet-started
// immediate scan. A control that looks like it kills a running scan and
// doesn't would be worse than no control at all, so while `running` is
// true the only affordance offered is pausing/resuming the *automatic*
// schedule, which does not touch the scan in flight.
export interface ScanControlInput {
  running: boolean;
  scanCycleScheduled: boolean;
  schedulerAlive: boolean;
}

export type ScanControlAction = "scan" | "pause" | "play" | "none";

export interface ScanControlView {
  /** The primary button's text. Never "Scan now" while a scan is
   * running — that case renders a disabled status instead. */
  primaryLabel: string;
  primaryAction: ScanControlAction;
  primaryDisabled: boolean;
  /** `null` when there is nothing secondary to offer (the scheduler is
   * dead — pausing/resuming a dead engine is not a real action). */
  secondaryLabel: string | null;
  secondaryAction: ScanControlAction;
}

export function scanControlFor(input: ScanControlInput): ScanControlView {
  const { running, scanCycleScheduled, schedulerAlive } = input;

  if (!schedulerAlive) {
    // The product spec: "a silently dead scheduler is a defect, not a display
    // state" — distinct from (and worse than) automatic scanning merely
    // being paused, so it gets its own label rather than collapsing into
    // "Resume automatic scanning".
    return {
      primaryLabel: "Scheduler not responding",
      primaryAction: "none",
      primaryDisabled: true,
      secondaryLabel: null,
      secondaryAction: "none",
    };
  }

  const secondaryLabel = scanCycleScheduled ? "Pause automatic scanning" : "Resume automatic scanning";
  const secondaryAction: ScanControlAction = scanCycleScheduled ? "pause" : "play";

  if (running) {
    // A scan is underway, started by anyone — this tab's own click, the
    // schedule, or another tab. Say so; do not offer anything that could
    // be read as stopping it.
    return {
      primaryLabel: "Scanning now…",
      primaryAction: "none",
      primaryDisabled: true,
      secondaryLabel,
      secondaryAction,
    };
  }

  // Idle. `trigger_scan` (jobfinder/web/scan.py) calls
  // `scheduler.trigger_now()` unconditionally — it never checks
  // `is_running` — so "Scan now" genuinely works whether or not the
  // automatic cycle is currently scheduled.
  return {
    primaryLabel: "Scan now",
    primaryAction: "scan",
    primaryDisabled: false,
    secondaryLabel,
    secondaryAction,
  };
}
