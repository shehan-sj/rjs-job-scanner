// SSE client for `GET /api/stream/scan` (T20's tagged event protocol,
// consumed here by the log drawer (T27) and the heartbeat/notifications
// strip (T28)). The endpoint has no typed OpenAPI schema beyond
// `text/event-stream` (see `schema.ts`'s `stream_scan_api_stream_scan_get`
// — `content: { "text/event-stream": unknown }`), so the tagged event
// shapes below are narrowed from the real server-side contract
// (`jobfinder/web/stream.py`'s `BroadcastLogHandler.emit` and
// `jobfinder/orchestrator.py`'s `_tk_queue.put(...)` call sites), same
// convention `api/settings.ts` already uses for its untyped
// `GET /api/settings` payload.
//
// `fastapi.sse`'s `ServerSentEvent` JSON-encodes every `data` value (see
// `fastapi/sse.py`'s docstring: "All `data` values ... are
// JSON-serialized"), with no `event:` field set — the browser dispatches
// every one as a generic `message` event, so a plain `EventSource` with
// `onmessage` is sufficient; no `addEventListener(type, ...)` per event
// type is needed.
//
// Reconnect behaviour: the backend's `_scan_event_stream` always replays
// its *current* ring buffer (`EventBroadcaster.subscribe`) from the start
// on every new connection — it does not consult the browser's
// auto-sent `Last-Event-ID` header at all. A native `EventSource`
// reconnect (after a dropped connection) therefore re-delivers whatever
// is still in that bounded 200-event ring buffer, which can overlap
// events this tab already saw. `subscribeScanStream` guards against that
// by dropping any event whose `seq` is not strictly greater than the
// highest `seq` already forwarded on *this* subscription.
export interface ScanStartEvent {
  type: "scan_start";
  seq: number;
}

export interface ScanCompleteEvent {
  type: "scan_complete";
  new_jobs: number;
  matches: number;
  seq: number;
}

export interface MatchesStreamEvent {
  type: "matches";
  items: unknown[];
  seq: number;
}

export interface ShowHistoryStreamEvent {
  type: "show_history";
  items: unknown[];
  seq: number;
}

export interface LogStreamEvent {
  type: "log";
  level: string;
  logger: string;
  message: string;
  /** `record.created` — a `time.time()`-style float, seconds since epoch. */
  time: number;
  seq: number;
}

export type ScanStreamEvent =
  | ScanStartEvent
  | ScanCompleteEvent
  | MatchesStreamEvent
  | ShowHistoryStreamEvent
  | LogStreamEvent;

function isScanStreamEvent(value: unknown): value is ScanStreamEvent {
  if (typeof value !== "object" || value === null) {
    return false;
  }
  const v = value as Record<string, unknown>;
  if (typeof v.seq !== "number") {
    return false;
  }
  return (
    v.type === "scan_start" ||
    v.type === "scan_complete" ||
    v.type === "matches" ||
    v.type === "show_history" ||
    v.type === "log"
  );
}

/**
 * Opens a new subscription to `GET /api/stream/scan` and forwards every
 * well-formed, not-yet-seen (by `seq`, on this connection) tagged event to
 * `onEvent`. Malformed payloads are dropped silently rather than thrown —
 * a stream is long-lived and best-effort, same posture
 * `client.ts`'s `fetchSettings` takes for a single failed request.
 *
 * Returns a cleanup function that closes the underlying `EventSource`;
 * callers (both T27's log drawer and T28's heartbeat strip) open their
 * own independent subscription — the backend's `EventBroadcaster` fans
 * out to every subscriber queue, so this is cheap and keeps each
 * consumer's reconnect/dedup state isolated from the other's.
 */
export function subscribeScanStream(onEvent: (event: ScanStreamEvent) => void): () => void {
  const source = new EventSource("/api/stream/scan");
  let maxSeqSeen = 0;

  source.onmessage = (ev: MessageEvent<string>) => {
    let parsed: unknown;
    try {
      parsed = JSON.parse(ev.data);
    } catch {
      return;
    }
    if (!isScanStreamEvent(parsed)) {
      return;
    }
    if (parsed.seq <= maxSeqSeen) {
      return;
    }
    maxSeqSeen = parsed.seq;
    onEvent(parsed);
  };

  return () => {
    source.close();
  };
}
