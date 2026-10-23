// T95 — the one `GET /api/state` poller, shared by every consumer.
//
// Before this, `HeartbeatStrip.tsx` (T28) owned the only poll loop, on its
// own `setInterval`. Adding the board header's scan control (T95) as a
// second independent poller of the same endpoint is exactly the failure
// mode this task's brief warns about: two timers racing the same request
// will eventually land their responses out of order and disagree with
// each other mid-render, and the owner will see the flicker before any
// test does. This module is the fix — one interval, one in-flight
// request, fanned out to every subscriber — so `HeartbeatStrip` and
// `ScanControl` (and anything added later) always render the same
// snapshot at the same moment.
import { fetchScanState, type ScanStateSnapshot } from "./state";

const POLL_INTERVAL_MS = 5000;

type Listener = (state: ScanStateSnapshot) => void;

const listeners = new Set<Listener>();
let intervalId: ReturnType<typeof setInterval> | null = null;
let lastSnapshot: ScanStateSnapshot | null = null;
let inFlight: Promise<void> | null = null;

async function pollOnce(): Promise<void> {
  // De-dupes a manual `refreshScanStateNow()` call that races the
  // interval's own tick into a single outstanding request rather than
  // two concurrent ones.
  if (inFlight) {
    return inFlight;
  }
  inFlight = (async () => {
    const snapshot = await fetchScanState();
    if (snapshot !== null) {
      lastSnapshot = snapshot;
      for (const listener of listeners) {
        listener(snapshot);
      }
    }
  })();
  try {
    await inFlight;
  } finally {
    inFlight = null;
  }
}

function ensurePolling(): void {
  if (intervalId !== null) {
    return;
  }
  intervalId = setInterval(() => void pollOnce(), POLL_INTERVAL_MS);
}

function stopPollingIfIdle(): void {
  if (listeners.size === 0 && intervalId !== null) {
    clearInterval(intervalId);
    intervalId = null;
  }
}

/**
 * Subscribes to the shared `/api/state` snapshot. Fires synchronously
 * with the last-known snapshot (if any) so a late subscriber does not
 * render a blank/unknown state while waiting on the network, then again
 * on every poll tick. Starts the shared interval on the first subscriber
 * and stops it once the last one unsubscribes — no polling happens with
 * nothing mounted to show it.
 */
export function subscribeScanState(listener: Listener): () => void {
  listeners.add(listener);
  if (lastSnapshot !== null) {
    listener(lastSnapshot);
  }
  ensurePolling();
  void pollOnce();
  return () => {
    listeners.delete(listener);
    stopPollingIfIdle();
  };
}

/**
 * Forces an immediate `/api/state` refresh outside the regular interval.
 * `ScanControl` calls this on the SSE stream's `scan_start`/`scan_complete`
 * events (T20) and right after its own pause/play/scan-now writes — a scan
 * started by the schedule or another tab can start and finish inside one
 * `POLL_INTERVAL_MS` window, which a poll-only control would simply never
 * see.
 */
export function refreshScanStateNow(): void {
  void pollOnce();
}
