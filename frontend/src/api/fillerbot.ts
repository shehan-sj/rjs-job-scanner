// FillerBot HTTP + SSE client (T31) for `jobfinder/web/fillerbot.py`.
//
// Request bodies come from the generated schema wherever the backend has a
// real Pydantic model (`StartSessionIn`, `FillIn`, `UploadReplyIn`,
// `ExtrasIn`) so a rename on the backend fails to compile here instead of
// 422-ing at runtime — same posture `client.ts` takes for
// `TokenExchangeRequest`. The two endpoints that return plain dicts
// (`GET /session`, `GET /profile`) can only be generated as
// `{ [key: string]: unknown }`, so they are narrowed at runtime below, the
// convention `settings.ts` already documents.
//
// The SSE half follows `stream.ts` exactly, including the `seq`-based
// dedup: the backend's `_event_stream` replays its *current* ring buffer
// on every new connection and never consults `Last-Event-ID`, so a native
// `EventSource` reconnect re-delivers events this tab already saw. That
// replay is also the feature a refreshed tab depends on (T30's "Refresh"
// lifecycle note) — it is why reattaching to a live session recovers the
// cards and status rather than showing an empty panel.
import type { components } from "./schema";
import type { PageAnalysisDto } from "./fillerbotLogic";

type StartSessionIn = components["schemas"]["StartSessionIn"];
type FillIn = components["schemas"]["FillIn"];
type UploadReplyIn = components["schemas"]["UploadReplyIn"];
type ExtrasIn = components["schemas"]["ExtrasIn"];

// ── Tagged events (jobfinder/fillerbot/engine.py's module docstring) ─────

export interface FillerStatusEvent {
  type: "status";
  message: string;
  seq: number;
}

export interface FillerAnalysisEvent {
  type: "analysis";
  analysis: PageAnalysisDto;
  seq: number;
}

/** `index` is the action's index in the `analysis` event's `actions` array
 * — the engine's single index space, shared with the `execute_fill`
 * overrides map. */
export interface FillerActionExecutedEvent {
  type: "action_executed";
  index: number;
  ok: boolean;
  seq: number;
}

export interface FillerFillCompleteEvent {
  type: "fill_complete";
  seq: number;
}

export interface FillerNeedsHumanEvent {
  type: "needs_human";
  message: string;
  seq: number;
}

/** Carries a correlated `request_id` that `accept`/`skip` must echo back
 * (T30) — the server answers 409 for a stale or duplicate reply. */
export interface FillerResumeUploadPromptEvent {
  type: "resume_upload_prompt";
  request_id: string;
  seq: number;
}

export interface FillerErrorEvent {
  type: "error";
  message: string;
  seq: number;
}

export interface FillerFinishedSessionEvent {
  type: "finished_session";
  seq: number;
}

export type FillerBotEvent =
  | FillerStatusEvent
  | FillerAnalysisEvent
  | FillerActionExecutedEvent
  | FillerFillCompleteEvent
  | FillerNeedsHumanEvent
  | FillerResumeUploadPromptEvent
  | FillerErrorEvent
  | FillerFinishedSessionEvent;

const EVENT_TYPES = new Set([
  "status",
  "analysis",
  "action_executed",
  "fill_complete",
  "needs_human",
  "resume_upload_prompt",
  "error",
  "finished_session",
]);

function isFillerBotEvent(value: unknown): value is FillerBotEvent {
  if (typeof value !== "object" || value === null) {
    return false;
  }
  const v = value as Record<string, unknown>;
  if (typeof v.seq !== "number" || typeof v.type !== "string") {
    return false;
  }
  return EVENT_TYPES.has(v.type);
}

/**
 * Subscribes to `GET /api/fillerbot/events`, forwarding every well-formed,
 * not-yet-seen (by `seq`) event. Returns a cleanup function that closes the
 * `EventSource`.
 *
 * The endpoint 404s when no session is active, which `EventSource` reports
 * as a generic `error` — hence `onStreamError`, so a reattach attempt
 * against a session that ended between `GET /session` and here can say so
 * instead of hanging on an empty panel. Malformed *payloads*, by contrast,
 * are dropped silently (a long-lived stream is best-effort), same as
 * `stream.ts`.
 */
export function subscribeFillerBotStream(
  onEvent: (event: FillerBotEvent) => void,
  onStreamError?: () => void,
): () => void {
  const source = new EventSource("/api/fillerbot/events");
  let maxSeqSeen = 0;

  source.onmessage = (ev: MessageEvent<string>) => {
    let parsed: unknown;
    try {
      parsed = JSON.parse(ev.data);
    } catch {
      return;
    }
    if (!isFillerBotEvent(parsed) || parsed.seq <= maxSeqSeen) {
      return;
    }
    maxSeqSeen = parsed.seq;
    onEvent(parsed);
  };

  if (onStreamError) {
    source.onerror = () => {
      onStreamError();
    };
  }

  return () => {
    source.close();
  };
}

// ── HTTP ──────────────────────────────────────────────────────────────────

/** A failed FillerBot request, carrying the status so callers can tell the
 * cases apart rather than collapsing them into "something went wrong":
 * 409 means "a session is already active" (or a stale upload reply), 400
 * means one of the three start-up gates (`fillerbot.enabled`, API key,
 * Chrome) said no, 404 means the session ended underneath us. */
export class FillerBotRequestError extends Error {
  readonly status: number;

  constructor(status: number, detail: string) {
    super(detail);
    this.name = "FillerBotRequestError";
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(path, { credentials: "same-origin", ...init });
  if (!resp.ok) {
    throw new FillerBotRequestError(resp.status, await readDetail(resp));
  }
  return (await resp.json()) as T;
}

/** FastAPI puts its message in `detail`; a 422 from the extras validator
 * puts a list of per-field errors there instead. Both are flattened into
 * one line the view can show verbatim. */
async function readDetail(resp: Response): Promise<string> {
  let body: unknown;
  try {
    body = await resp.json();
  } catch {
    return `Request failed (${resp.status})`;
  }
  const detail = (body as Record<string, unknown> | null)?.detail;
  if (typeof detail === "string") {
    return detail;
  }
  if (Array.isArray(detail)) {
    const parts = detail
      .map((item) => {
        const msg = (item as Record<string, unknown>)?.msg;
        return typeof msg === "string" ? msg : null;
      })
      .filter((msg): msg is string => msg !== null);
    if (parts.length) {
      return parts.join("; ");
    }
  }
  return `Request failed (${resp.status})`;
}

function jsonBody(body: unknown): RequestInit {
  return {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  };
}

export interface FillerSessionStatus {
  active: boolean;
  sessionId: string | null;
}

/** `GET /api/fillerbot/session`. Called on mount: a refreshed tab, or a
 * second tab, discovers the live session here and attaches to its stream
 * instead of being offered a Go button that would only 409 (T30). */
export async function fetchFillerSession(): Promise<FillerSessionStatus> {
  const body = await request<Record<string, unknown>>("/api/fillerbot/session");
  return {
    active: body.active === true,
    sessionId: typeof body.session_id === "string" ? body.session_id : null,
  };
}

/** `POST /api/fillerbot/session`. 409 if one is already active, 400 if
 * FillerBot is disabled in settings / has no API key / finds no Chrome. */
export async function startFillerSession(url: string): Promise<string | null> {
  const body: StartSessionIn = { url };
  const resp = await request<Record<string, unknown>>("/api/fillerbot/session", jsonBody(body));
  return typeof resp.session_id === "string" ? resp.session_id : null;
}

export interface PromptableField {
  key: string;
  label: string;
}

export interface FillerProfilePreflight {
  missing: PromptableField[];
  promptable: PromptableField[];
  extras: Record<string, string>;
  fullName: string;
}

function toPromptableFields(value: unknown): PromptableField[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value.flatMap((item) => {
    const row = item as Record<string, unknown> | null;
    if (typeof row?.key !== "string" || typeof row.label !== "string") {
      return [];
    }
    return [{ key: row.key, label: row.label }];
  });
}

function toStringMap(value: unknown): Record<string, string> {
  const out: Record<string, string> = {};
  if (typeof value !== "object" || value === null) {
    return out;
  }
  for (const [key, raw] of Object.entries(value as Record<string, unknown>)) {
    if (typeof raw === "string") {
      out[key] = raw;
    }
  }
  return out;
}

function toPreflight(body: Record<string, unknown>): FillerProfilePreflight {
  return {
    missing: toPromptableFields(body.missing),
    promptable: toPromptableFields(body.promptable),
    extras: toStringMap(body.extras),
    fullName: typeof body.full_name === "string" ? body.full_name : "",
  };
}

/** `GET /api/fillerbot/profile` — which promptable fields the resume left
 * empty, *before* a session starts. Gated identically to
 * `POST /session`, so a 400 here is the same refusal a Go would hit. */
export async function fetchFillerProfile(): Promise<FillerProfilePreflight> {
  return toPreflight(await request<Record<string, unknown>>("/api/fillerbot/profile"));
}

/** `PATCH /api/fillerbot/profile/extras` — persists supplemental values
 * (`save_extras`) and returns the recomputed missing list. Merged, not
 * mass-assigned: only the keys sent are touched. */
export async function saveFillerExtras(
  values: Record<string, string>,
): Promise<FillerProfilePreflight> {
  const body: ExtrasIn = { values };
  const resp = await request<Record<string, unknown>>("/api/fillerbot/profile/extras", {
    ...jsonBody(body),
    method: "PATCH",
  });
  return toPreflight(resp);
}

async function control(path: string, body?: unknown): Promise<void> {
  await request<unknown>(path, body === undefined ? { method: "POST" } : jsonBody(body));
}

export function pauseFiller(): Promise<void> {
  return control("/api/fillerbot/pause");
}

export function resumeFiller(): Promise<void> {
  return control("/api/fillerbot/resume");
}

export function rescanFiller(): Promise<void> {
  return control("/api/fillerbot/rescan");
}

export function nextPageFiller(): Promise<void> {
  return control("/api/fillerbot/next");
}

export function stopFiller(): Promise<void> {
  return control("/api/fillerbot/stop");
}

/** `POST /api/fillerbot/front` — CDP `Page.bringToFront`. 409 while the
 * window is still launching. */
export function bringFillerToFront(): Promise<void> {
  return control("/api/fillerbot/front");
}

/** `POST /api/fillerbot/fill` with the user's edited values, keyed by the
 * engine index (see `fillerbotLogic.buildFillOverrides`). */
export function executeFillerFill(overrides: Record<string, string>): Promise<void> {
  // `dict[int, str]` generates as `{ [key: string]: string }` — JSON object
  // keys are strings and FastAPI coerces them back to ints on arrival.
  const body: FillIn = { overrides };
  return control("/api/fillerbot/fill", body);
}

/** `POST /api/fillerbot/upload/accept`. The `request_id` from the
 * `resume_upload_prompt` event must be echoed back; a mismatch is a 409
 * the caller is expected to surface, not swallow. */
export function acceptFillerUpload(requestId: string): Promise<void> {
  const body: UploadReplyIn = { request_id: requestId };
  return control("/api/fillerbot/upload/accept", body);
}

/** `POST /api/fillerbot/upload/skip` — "Skip — Fill Manually". Same
 * correlated-id contract as accept. */
export function skipFillerUpload(requestId: string): Promise<void> {
  const body: UploadReplyIn = { request_id: requestId };
  return control("/api/fillerbot/upload/skip", body);
}
