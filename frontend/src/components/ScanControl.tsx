import { useCallback, useEffect, useState, type ReactNode } from "react";
import { pauseScan, playScan, type ScanStateSnapshot } from "../api/state";
import { refreshScanStateNow, subscribeScanState } from "../api/scanStatePoller";
import { subscribeScanStream } from "../api/stream";
import { triggerScan } from "../api/jobs";
import { scanControlFor } from "./scanControlLogic";

/**
 * T95 — the board header's one scan control (the product spec's play/pause
 * bullet, unreachable from the UI until this task — see
 * `scanControlLogic.ts`'s module docstring for the full "no Stop" reasoning).
 *
 * Driven entirely by `GET /api/state` via the shared poller
 * (`api/scanStatePoller.ts`) rather than any local "I clicked this"
 * boolean, so a scan started by the schedule or by another tab is
 * reflected here exactly like one this tab's own button started.
 *
 * Also subscribes to the scan SSE stream (`api/stream.ts`, already opened
 * independently by `HeartbeatStrip` — `EventBroadcaster` fans out to every
 * subscriber, so a second `EventSource` here is cheap, unlike a second
 * `/api/state` poller would be) purely to force an immediate state
 * refresh on `scan_start`/`scan_complete` — a scan that starts and
 * finishes inside one 5s poll window would otherwise never show up here.
 */
export function ScanControl({ className }: { className?: string }): ReactNode {
  const [state, setState] = useState<ScanStateSnapshot | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => subscribeScanState(setState), []);

  useEffect(
    () =>
      subscribeScanStream((event) => {
        if (event.type === "scan_start" || event.type === "scan_complete") {
          refreshScanStateNow();
        }
      }),
    [],
  );

  const view = scanControlFor({
    running: state?.running ?? false,
    scanCycleScheduled: state?.scan_cycle_scheduled ?? false,
    schedulerAlive: state?.scheduler_alive ?? false,
  });

  const handlePrimary = useCallback(() => {
    if (view.primaryAction !== "scan") {
      return;
    }
    setBusy(true);
    void triggerScan()
      .then(refreshScanStateNow)
      .finally(() => setBusy(false));
  }, [view.primaryAction]);

  const handleSecondary = useCallback(() => {
    if (view.secondaryAction !== "pause" && view.secondaryAction !== "play") {
      return;
    }
    setBusy(true);
    const action = view.secondaryAction === "pause" ? pauseScan : playScan;
    void action()
      .then(refreshScanStateNow)
      .finally(() => setBusy(false));
  }, [view.secondaryAction]);

  return (
    <div className={`scan-control ${className ?? ""}`.trim()}>
      <button
        type="button"
        className="board-view-scan-now-btn"
        onClick={handlePrimary}
        disabled={view.primaryDisabled || busy}
      >
        {view.primaryLabel}
      </button>
      {view.secondaryLabel ? (
        <button
          type="button"
          className="scan-control-secondary-btn"
          onClick={handleSecondary}
          disabled={busy}
        >
          {view.secondaryLabel}
        </button>
      ) : null}
    </div>
  );
}
