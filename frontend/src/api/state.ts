// `GET /api/state` (T19) — the full snapshot the T28 heartbeat strip
// polls: whether a scan is running, when the last one started/completed,
// its counts, and whether the scheduler engine itself is alive. Like
// `GET /api/settings`, this route has no declared `response_model`
// (`jobfinder/web/scan.py`'s `state` returns a plain dict built from
// `ScanState.snapshot()` plus two extra keys), so `schema.ts` can only
// generate `{ [key: string]: unknown }` for it — narrowed here from the
// real server-side contract, same convention `api/settings.ts` uses.
export interface ScanStateSnapshot {
  running: boolean;
  last_scan_started_at: string | null;
  last_scan_completed_at: string | null;
  last_new_jobs: number;
  last_matches: number;
  /** T65 — the highest score among every job the last scan actually
   * scored, even when none of them cleared `match_threshold`
   * (`jobfinder/web/scan.py`'s `ScanState.last_best_score`). `null` means
   * either no scan has completed yet or the last one scored nothing (no
   * jobs fetched, or everything had already been seen). Lets the Matches
   * empty state say the scan worked and name what it found instead of
   * just "nothing matched". */
  last_best_score: number | null;
  last_error: string | null;
  /** `JobScheduler.is_alive` — false means the background scheduler
   * thread/engine itself has died, not merely that no scan is running
   * right now. The product spec: "a silently dead scheduler is a defect, not
   * a display state." */
  scheduler_alive: boolean;
  /** `JobScheduler.is_running` — whether periodic scanning is scheduled
   * at all (false after `POST /api/scan/pause`). */
  scan_cycle_scheduled: boolean;
}

function asRecord(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null ? (value as Record<string, unknown>) : {};
}

function bool(value: unknown, fallback = false): boolean {
  return typeof value === "boolean" ? value : fallback;
}

function num(value: unknown, fallback = 0): number {
  return typeof value === "number" ? value : fallback;
}

function optStr(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

function optNum(value: unknown): number | null {
  return typeof value === "number" ? value : null;
}

export function toScanStateSnapshot(value: unknown): ScanStateSnapshot {
  const raw = asRecord(value);
  return {
    running: bool(raw.running),
    last_scan_started_at: optStr(raw.last_scan_started_at),
    last_scan_completed_at: optStr(raw.last_scan_completed_at),
    last_new_jobs: num(raw.last_new_jobs),
    last_matches: num(raw.last_matches),
    last_best_score: optNum(raw.last_best_score),
    last_error: optStr(raw.last_error),
    // Fail-closed: if the field is somehow missing, treat the scheduler
    // as dead rather than alive — a false "everything is fine" heartbeat
    // is worse than a false alarm.
    scheduler_alive: bool(raw.scheduler_alive, false),
    scan_cycle_scheduled: bool(raw.scan_cycle_scheduled, false),
  };
}

/** `GET /api/state`, narrowed. Returns `null` on any failure (network
 * error, non-2xx) rather than throwing — the heartbeat strip polls this
 * on an interval and a single dropped poll should not crash the UI;
 * the caller renders whatever stale snapshot it already has. */
export async function fetchScanState(): Promise<ScanStateSnapshot | null> {
  try {
    // `cache: "no-store"` — this response has no `Cache-Control` header
    // (jobfinder/web/scan.py's `state` route sets none), and a scan can
    // flip `running` within milliseconds of a previous poll; without this,
    // some browsers' default HTTP cache heuristics can serve a stale
    // snapshot back for an identical request instead of hitting the
    // network, which this task's own browser check caught in practice.
    const resp = await fetch("/api/state", { credentials: "same-origin", cache: "no-store" });
    if (!resp.ok) {
      return null;
    }
    return toScanStateSnapshot(await resp.json());
  } catch {
    return null;
  }
}

async function post(path: string): Promise<void> {
  const resp = await fetch(path, { method: "POST", credentials: "same-origin" });
  if (!resp.ok) {
    throw new Error(`${path} failed: ${resp.status}`);
  }
}

/** T95 — `POST /api/scan/pause` (`jobfinder/web/scan.py`). Stops periodic
 * scanning and drops a queued-but-not-yet-started immediate scan. Does
 * **not** touch a scan already in flight — `JobScheduler.pause()`'s own
 * docstring says a running scan is on a worker thread doing blocking HTTP
 * and can't be cleanly aborted — so this is "pause the schedule", never
 * "stop this scan". */
export async function pauseScan(): Promise<void> {
  await post("/api/scan/pause");
}

/** T95 — `POST /api/scan/play` (`jobfinder/web/scan.py`). Restarts
 * periodic scanning. `JobScheduler.play()` also calls `trigger_now()`
 * internally, so an immediate scan can start right behind this request —
 * callers should re-read `/api/state` rather than assume `running` stays
 * false after calling this. */
export async function playScan(): Promise<void> {
  await post("/api/scan/play");
}
