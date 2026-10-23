import { useCallback, useState, type ReactNode } from "react";
import { requestStop, stopMessage, type StopOutcome } from "../api/control";
import "./StopServerButton.css";

// The explicit stop control (T34) — the product spec.
//
// Two-stage rather than one click, and not `window.confirm`. Quitting
// stops scanning entirely, which is the app's whole job, and the
// heartbeat strip right next to this button tells the user that closing
// the tab does *not* stop scanning — so this button is the one thing on
// screen that does, sitting next to Settings and FillerBot. A single
// mis-click should not silently end the day's scanning. `window.confirm`
// would do the job but blocks the event loop, cannot be styled with the
// theme tokens, and reads as a browser-level warning rather than part of
// the app.
//
// After a successful stop the UI deliberately does not try to keep
// working: every later request would fail against a dead server. It shows
// the terminal message and stops offering the button.
export function StopServerButton(): ReactNode {
  const [confirming, setConfirming] = useState(false);
  const [outcome, setOutcome] = useState<StopOutcome | null>(null);
  const [pending, setPending] = useState(false);

  const stop = useCallback(() => {
    setPending(true);
    void requestStop().then((result) => {
      setPending(false);
      setConfirming(false);
      setOutcome(result);
    });
  }, []);

  if (outcome !== null) {
    return (
      <div className="stop-server-result" role="status">
        {stopMessage(outcome)}
      </div>
    );
  }

  if (confirming) {
    return (
      <div className="stop-server-confirm">
        <span className="stop-server-prompt">Stop scanning and quit?</span>
        <button type="button" className="app-shell-trigger stop-server-danger" onClick={stop} disabled={pending}>
          {pending ? "Stopping…" : "Quit"}
        </button>
        <button
          type="button"
          className="app-shell-trigger"
          onClick={() => setConfirming(false)}
          disabled={pending}
        >
          Cancel
        </button>
      </div>
    );
  }

  return (
    <button
      type="button"
      className="app-shell-trigger"
      aria-label="Stop JobFinder"
      onClick={() => setConfirming(true)}
    >
      Quit
    </button>
  );
}
