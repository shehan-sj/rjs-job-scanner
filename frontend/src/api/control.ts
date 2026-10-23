// Stop the server (T34) — the UI half of the product spec's "The UI provides
// an explicit way to stop the server".
//
// Closing the tab deliberately does *not* stop scanning (the
// heartbeat strip says so), so this is the only way out of the app short
// of killing the process. That makes the outcome reporting load-bearing:
// a failed stop that reads as success leaves a scheduler running that the
// user believes they shut down.
//
// `POST`, not `GET`, and `credentials: "same-origin"` so the
// HttpOnly/SameSite=Strict session cookie rides along. The backend route
// (`jobfinder/web/control.py`) explains why only POST exists at all: a
// GET shutdown would be firable from any page on the internet with an
// `<img src>`.

export type StopOutcome = "stopping" | "unsupported" | "unauthorized" | "error";

export function stopOutcomeForStatus(status: number): StopOutcome {
  if (status >= 200 && status < 300) {
    return "stopping";
  }
  if (status === 501) {
    // Not started by the launcher, so no shutdown handler is registered.
    return "unsupported";
  }
  if (status === 401 || status === 403) {
    return "unauthorized";
  }
  return "error";
}

export function stopMessage(outcome: StopOutcome): string {
  switch (outcome) {
    case "stopping":
      return "JobFinder has stopped. You can close this tab.";
    case "unsupported":
      return "This server is still running: it was not started by the JobFinder launcher, so it cannot stop itself. Close the terminal that started it.";
    case "unauthorized":
      return "This tab is no longer signed in, so JobFinder is still running. Relaunch JobFinder and try again.";
    case "error":
      return "JobFinder could not be stopped and may still be running. Check the log drawer for details.";
  }
}

/**
 * Asks the server to shut itself down.
 *
 * A thrown `fetch` is deliberately reported as `"stopping"`: the server
 * answers 202 and *then* tears down, so a connection reset at exactly
 * this moment is the success path racing us, not a failure. Reporting
 * "error" there would scare the user about the one case that worked.
 */
export async function requestStop(): Promise<StopOutcome> {
  try {
    const resp = await fetch("/api/stop", {
      method: "POST",
      credentials: "same-origin",
    });
    return stopOutcomeForStatus(resp.status);
  } catch {
    return "stopping";
  }
}
