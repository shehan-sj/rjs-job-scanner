// Job-view data fetching and card actions (T23/T24) — GET /api/matches,
// /api/applied, /api/removed, POST /api/scan for the "Run a scan now"
// empty-state CTA, and the card action endpoints wired in T24: mark
// applied, dismiss (remove), skip company, restore, undo (unapply), and
// clear-all per tab.
import type { components, operations } from "./schema";
import {
  toAppliedCard,
  toMatchCard,
  toRemovedCard,
  type AppliedCard,
  type MatchCard,
  type RemovedCard,
} from "../types/job";

// Every route below requires the session cookie set by bootstrapAuth()
// (src/api/client.ts) — same-origin fetch with `credentials: "same-origin"`
// sends it automatically once set.

type MatchesPayload =
  operations["list_matches_api_matches_get"]["responses"][200]["content"]["application/json"];
type AppliedPayload =
  operations["list_applied_api_applied_get"]["responses"][200]["content"]["application/json"];
type RemovedPayload =
  operations["list_removed_api_removed_get"]["responses"][200]["content"]["application/json"];
type SkipCompanyRequest = components["schemas"]["SkipCompanyRequest"];

async function getJson<T>(path: string): Promise<T> {
  const resp = await fetch(path, { credentials: "same-origin" });
  if (!resp.ok) {
    throw new Error(`${path} failed: ${resp.status}`);
  }
  return (await resp.json()) as T;
}

async function post(path: string, body?: unknown): Promise<void> {
  const resp = await fetch(path, {
    method: "POST",
    credentials: "same-origin",
    headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!resp.ok) {
    throw new Error(`${path} failed: ${resp.status}`);
  }
}

export async function fetchMatches(): Promise<MatchCard[]> {
  const payload = await getJson<MatchesPayload>("/api/matches");
  return payload.map(toMatchCard);
}

export async function fetchApplied(): Promise<AppliedCard[]> {
  const payload = await getJson<AppliedPayload>("/api/applied");
  return payload.map(toAppliedCard);
}

export async function fetchRemoved(): Promise<RemovedCard[]> {
  const payload = await getJson<RemovedPayload>("/api/removed");
  return payload.map(toRemovedCard);
}

/** Fire-and-forget scan trigger for the Matches empty-state "Run a scan
 * now" CTA. Mirrors `jobfinder/ui/widget.py`'s `_toggle_auto_scan` intent
 * (kick off a scan) without the play/pause scheduler state machine, which
 * is out of this task's scope. */
export async function triggerScan(): Promise<void> {
  await fetch("/api/scan", { method: "POST", credentials: "same-origin" });
}

// ── Card actions (T24) — mirrors widget.py's _apply_job/_remove_job/
//    _skip_company/_restore_job/_unapply_job/_clear_* one-to-one. ─────────

/** "Mark applied" — moves a card from Matches to Applied. */
export async function applyJob(jobId: string): Promise<void> {
  await post(`/api/matches/${encodeURIComponent(jobId)}/apply`);
}

/** "Dismiss" — moves a card from Matches to Removed (removed_by: "user"). */
export async function removeJob(jobId: string): Promise<void> {
  await post(`/api/matches/${encodeURIComponent(jobId)}/remove`);
}

/** "Restore" on a Removed card — moves it back to Matches. */
export async function restoreJob(jobId: string): Promise<void> {
  await post(`/api/matches/${encodeURIComponent(jobId)}/restore`);
}

/** "Undo" on an Applied card — moves it back to Matches. */
export async function unapplyJob(jobId: string): Promise<void> {
  await post(`/api/applied/${encodeURIComponent(jobId)}/unapply`);
}

/** "Skip company" — adds the company to the skip list; the caller is
 * responsible for the confirm dialog (widget.py's `_skip_company`) since
 * it needs the affected-match count, which only the view has. */
export async function skipCompany(name: string): Promise<void> {
  const body: SkipCompanyRequest = { name };
  await post("/api/skip-company", body);
}

/** Clear-all for the Matches tab. Jobs stay "seen" (won't reappear on the
 * next scan) — only the match-history display is cleared. */
export async function clearMatches(): Promise<void> {
  await post("/api/matches/clear");
}

/** Clear-all for the Applied tab. The jobs themselves aren't deleted. */
export async function clearApplied(): Promise<void> {
  await post("/api/applied/clear");
}

/** Clear-all for the Removed tab. Cleared jobs can reappear in Matches on
 * the next scan if they still match. */
export async function clearRemoved(): Promise<void> {
  await post("/api/removed/clear");
}
