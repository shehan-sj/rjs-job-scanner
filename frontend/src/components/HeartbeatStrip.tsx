import { Fragment, useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { type ScanStateSnapshot } from "../api/state";
import { refreshScanStateNow, subscribeScanState } from "../api/scanStatePoller";
import { subscribeScanStream, type ScanCompleteEvent } from "../api/stream";
import { scanControlFor } from "./scanControlLogic";
import { joinInline, stackWithoutSeparators, type HeartbeatPart } from "./heartbeatParts";
import "./HeartbeatStrip.css";

// Notifications + heartbeat (T28).
//
// Browser Notifications API, permission requested only on a user
// gesture (the "Enable notifications" button's own click handler —
// never on load, since browsers reject an unsolicited load-time request
// and it would be hostile besides).
//
// The product spec's notification contract is a deliberate, inverted trade
// from v3: these fire **only while a JobFinder tab is open**. There is
// no server-push-while-closed equivalent in v4 (no desktop/tray layer,
// The product spec). The limitation is stated plainly in the UI rather than
// left implicit, per the task's instruction and the spec itself ("The UI
// must therefore make scan state legible on load").
//
// The heartbeat strip reads `GET /api/state` (T19) — scheduler
// alive/dead is not something the SSE stream pushes on its own; a dead
// scheduler produces *no* events at all, which is exactly why polling a
// snapshot is the only way to detect it. "A silently dead scheduler is a
// defect, not a display state": `scheduler_alive === false`
// renders visibly differently (red, "not responding") from
// `scheduler_alive === true && !running` (idle, green) rather than
// collapsing both into one quiet "not scanning" state.
//
// T95: the actual polling now lives in `api/scanStatePoller.ts`, shared
// with the board header's `ScanControl` — this component used to own its
// own `setInterval`, which would have made two independent pollers of the
// same endpoint once `ScanControl` needed the same data.
type PermissionState = "default" | "granted" | "denied" | "unsupported";

function readPermission(): PermissionState {
  if (typeof Notification === "undefined") {
    return "unsupported";
  }
  return Notification.permission;
}

function formatLastScan(iso: string | null): string {
  if (iso === null) {
    return "No scan yet";
  }
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) {
    return "No scan yet";
  }
  return d.toLocaleString();
}

/** T60 — `variant="compact"` is the rail-foot presentation (docs/DESIGN.md
 * principle 4: "the heartbeat sit[s] at the foot of the rail"). All the
 * polling/notification logic below is unchanged and shared; compact mode
 * only changes what's rendered by default (a single status dot, sized
 * for a 68px rail) versus on demand (the exact same strip content as
 * `variant="full"`, shown in a popover anchored to that dot) — nothing
 * in HeartbeatStrip's *capability* is lost by narrowing its footprint,
 * per the product spec. */
export function HeartbeatStrip({ variant = "full" }: { variant?: "full" | "compact" }): ReactNode {
  const [state, setState] = useState<ScanStateSnapshot | null>(null);
  const [permission, setPermission] = useState<PermissionState>(() => readPermission());
  const [expanded, setExpanded] = useState(false);
  const matchesRef = useRef(0);

  useEffect(() => subscribeScanState(setState), []);

  // Fire a notification on scan_complete when there are new matches and
  // permission has been granted. This only ever runs while this tab is
  // open and this component is mounted — the stated, accepted
  // limitation.
  useEffect(() => {
    const unsubscribe = subscribeScanStream((event) => {
      if (event.type !== "scan_complete") {
        return;
      }
      const complete = event as ScanCompleteEvent;
      matchesRef.current = complete.matches;
      if (complete.matches > 0 && readPermission() === "granted") {
        try {
          new Notification("JobFinder found new matches", {
            body: `${complete.matches} new match${complete.matches === 1 ? "" : "es"} this scan.`,
          });
        } catch {
          // Notification constructor can throw in some embedded/headless
          // contexts even when permission reports "granted" — never let
          // that take down the stream subscription.
        }
      }
      // A scan just completed — refresh the heartbeat immediately
      // instead of waiting for the next poll tick.
      refreshScanStateNow();
    });
    return unsubscribe;
  }, []);

  const requestPermission = useCallback(() => {
    if (typeof Notification === "undefined") {
      return;
    }
    // Called synchronously from this button's own click handler — the
    // user gesture the browser requires. Never call this on mount/load.
    void Notification.requestPermission().then((result) => {
      setPermission(result);
    });
  }, []);

  // T96(b) — whether a scan is running is derived through `scanControlFor`
  // (T95, scanControlLogic.ts), the same pure mapping `ScanControl` uses,
  // rather than a second set of `running`/`scheduler_alive` conditionals
  // here. Re-deriving it independently is exactly the bug T95 fixed for
  // the scan-now button: two components disagreeing about whether a scan
  // is running. `scanControlFor` only distinguishes "dead" from "scanning"
  // from "idle" through its returned view (it has no notion of "haven't
  // polled yet"), so that tri-state is read off the view rather than
  // recomputed: dead and scanning both disable the primary action, and
  // only dead also has no secondary (pause/resume) action — idle alone
  // leaves the primary enabled.
  const view = state
    ? scanControlFor({
        running: state.running,
        scanCycleScheduled: state.scan_cycle_scheduled,
        schedulerAlive: state.scheduler_alive,
      })
    : null;

  let statusClass = "heartbeat-status-unknown";
  let statusText = "Checking scheduler…";
  if (view) {
    if (view.primaryDisabled && view.secondaryLabel === null) {
      statusClass = "heartbeat-status-dead";
      statusText = "Scheduler not responding";
    } else if (view.primaryDisabled) {
      statusClass = "heartbeat-status-scanning";
      statusText = "Scanning now";
    } else {
      statusClass = "heartbeat-status-idle";
      statusText = "Scheduler idle";
    }
  }

  // T96(a) — parts, not one fragment shared by two layouts. Each part
  // owns its own content and whether the *inline* strip draws a
  // separator glyph immediately before it (heartbeatParts.ts); the
  // popover never draws that glyph at all, regardless of this flag. The
  // `precededBySeparator` values below reproduce the pre-T96 inline
  // layout's own separator placement exactly — this task fixes the
  // popover, not the inline strip's appearance.
  const parts: Array<HeartbeatPart<ReactNode>> = [
    {
      key: "status",
      precededBySeparator: false,
      content: (
        <>
          <span className={`heartbeat-dot ${statusClass}`} aria-hidden="true" />
          <span className="heartbeat-text">{statusText}</span>
        </>
      ),
    },
    {
      key: "last-scan",
      precededBySeparator: true,
      content: (
        <span className="heartbeat-text">
          Last scan: {formatLastScan(state?.last_scan_completed_at ?? null)}
        </span>
      ),
    },
    {
      key: "note",
      precededBySeparator: true,
      content: <span className="heartbeat-text heartbeat-note">Scanning continues when you close this tab.</span>,
    },
    {
      key: "permission",
      precededBySeparator: true,
      content:
        permission === "default" ? (
          <button type="button" className="heartbeat-notify-btn" onClick={requestPermission}>
            Enable notifications
          </button>
        ) : permission === "granted" ? (
          <span className="heartbeat-text">Notifications on</span>
        ) : permission === "denied" ? (
          <span className="heartbeat-text">Notifications blocked</span>
        ) : null,
    },
    {
      key: "notification-note",
      precededBySeparator: false,
      content: (
        <span className="heartbeat-text heartbeat-note">
          Notifications only fire while a JobFinder tab is open — closing every tab means no alert, even
          though scanning keeps running.
        </span>
      ),
    },
  ];

  const separator = <span className="heartbeat-sep">·</span>;

  if (variant === "full") {
    return (
      <div className="heartbeat-strip">
        {joinInline(parts, separator).map((item) => (
          <Fragment key={item.key}>{item.content}</Fragment>
        ))}
      </div>
    );
  }

  return (
    <div className="heartbeat-compact">
      <button
        type="button"
        className="heartbeat-compact-btn"
        aria-label={`Scanner status: ${statusText}. ${expanded ? "Hide" : "Show"} details.`}
        aria-expanded={expanded}
        onClick={() => setExpanded((prev) => !prev)}
        title={statusText}
      >
        <span className={`heartbeat-dot ${statusClass}`} aria-hidden="true" />
      </button>
      {expanded ? (
        <div className="heartbeat-compact-popover" role="status">
          {stackWithoutSeparators(parts).map((item) => (
            <div className="heartbeat-compact-popover-row" key={item.key}>
              {item.content}
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}
