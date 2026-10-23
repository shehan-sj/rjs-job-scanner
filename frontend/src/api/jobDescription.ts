// `GET /api/jobs/{job_id}/description` — the posting's own text, fetched
// when the detail panel opens rather than carried on every board card.
//
// Why a fetch and not a card field: `GET /api/board` returns every card in
// all five stages at once, and a description runs to several KB. Putting it
// there would multiply the board payload to show text that is only ever
// read one job at a time (jobfinder/web/jobs.py's route docstring).
//
// The panel swaps between jobs inside one `<dialog>` (JobModal.tsx), and
// the prev/next buttons make that swap fast, so two things matter here:
// results are cached per job id, and a response that arrives after the
// user has moved on is dropped rather than painted over the new job.

export type JobDescriptionState =
  | { status: "loading" }
  | { status: "ready"; text: string }
  /** The job exists but carries no text. `storedEver: false` means it was
   * found by a scan that ran before descriptions were kept — nothing is
   * wrong and a rescan fills it in, which the panel says differently from
   * a posting that genuinely had no body. */
  | { status: "empty"; storedEver: boolean }
  | { status: "error" };

const cache = new Map<string, JobDescriptionState>();

/** Exposed for tests and for `POST /api/reset`, which invalidates
 * everything the client holds about jobs. */
export function clearJobDescriptionCache(): void {
  cache.clear();
}

export function cachedJobDescription(jobId: string): JobDescriptionState | undefined {
  return cache.get(jobId);
}

function parse(value: unknown): JobDescriptionState {
  const raw = typeof value === "object" && value !== null ? (value as Record<string, unknown>) : {};
  const text = typeof raw.description === "string" ? raw.description : null;
  const stored = typeof raw.stored === "boolean" ? raw.stored : text !== null;
  if (text !== null && text.trim() !== "") {
    return { status: "ready", text };
  }
  return { status: "empty", storedEver: stored };
}

/**
 * Fetch one job's description, caching the result.
 *
 * `onSettled` is only called if `isCurrent()` still answers true, so a slow
 * response for job A cannot overwrite the panel after the user has pressed
 * Next to job B. The caller owns that check because only it knows which job
 * is on screen now.
 */
export async function loadJobDescription(
  jobId: string,
  isCurrent: () => boolean,
  onSettled: (state: JobDescriptionState) => void,
): Promise<void> {
  const hit = cache.get(jobId);
  if (hit !== undefined && hit.status !== "loading") {
    onSettled(hit);
    return;
  }

  let next: JobDescriptionState;
  try {
    const resp = await fetch(`/api/jobs/${encodeURIComponent(jobId)}/description`, {
      credentials: "same-origin",
    });
    next = resp.ok ? parse(await resp.json()) : { status: "error" };
  } catch {
    // Same convention as `api/state.ts`'s poller: a dropped request is a
    // state to render, never an exception for the component to handle.
    next = { status: "error" };
  }

  // An error is not cached: the next open should retry rather than show a
  // permanent failure from one dropped request.
  if (next.status !== "error") {
    cache.set(jobId, next);
  }
  if (isCurrent()) {
    onSettled(next);
  }
}
