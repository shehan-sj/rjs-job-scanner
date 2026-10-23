// T81 — the board: GET /api/board, one payload for all
// five pipeline stages, and POST /api/jobs/{id}/stage, the single write
// path. No `response_model` is declared on either FastAPI route
// (jobfinder/web/jobs.py returns plain dicts, same situation `job.ts`'s
// module docstring already describes for /api/matches et al.), so these
// go through `toBoardPayload` rather than a generated type.
import type { Stage } from "../types/job";
import { toBoardPayload, type BoardPayload } from "../types/job";

export async function fetchBoard(): Promise<BoardPayload> {
  const resp = await fetch("/api/board", { credentials: "same-origin" });
  if (!resp.ok) {
    throw new Error(`/api/board failed: ${resp.status}`);
  }
  return toBoardPayload(await resp.json());
}

export interface SetStageOptions {
  note?: string;
  removedBy?: "user" | "agent";
}

/** The one write path — keyboard `1`-`5`, a future
 * drag (T82) and the future panel's stage control (T83) must all call
 * this same endpoint rather than any of the legacy per-action wrappers,
 * so a move is one reducer action regardless of which input made it. */
export async function setJobStage(jobId: string, stage: Stage, options: SetStageOptions = {}): Promise<void> {
  const body: { stage: Stage; note?: string; removed_by?: string } = { stage };
  if (options.note !== undefined) body.note = options.note;
  if (options.removedBy !== undefined) body.removed_by = options.removedBy;
  const resp = await fetch(`/api/jobs/${encodeURIComponent(jobId)}/stage`, {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!resp.ok) {
    throw new Error(`/api/jobs/${jobId}/stage failed: ${resp.status}`);
  }
}

/** The one star write path (T101) — mirrors
 * `setJobStage` above, but a star is not a stage: it never calls that
 * endpoint and must not move the card. */
export async function setJobStarred(jobId: string, starred: boolean): Promise<void> {
  const resp = await fetch(`/api/jobs/${encodeURIComponent(jobId)}/star`, {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ starred }),
  });
  if (!resp.ok) {
    throw new Error(`/api/jobs/${jobId}/star failed: ${resp.status}`);
  }
}
