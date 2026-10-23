// Onboarding wizard support calls (T26) — GET /api/onboarding/status,
// POST /api/onboarding/test-key, POST /api/onboarding/acknowledge-disclosure
// (jobfinder/web/onboarding.py). None of these three have a declared
// `response_model` on the backend (same situation client.ts/settings.ts
// already document for GET /api/settings), so these are hand-narrowed
// from the real server-side shape rather than generated.
import type { components } from "./schema";

export type TestKeyRequest = components["schemas"]["TestKeyRequest"];

function asRecord(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null ? (value as Record<string, unknown>) : {};
}

/**
 * `GET /api/onboarding/status`. Mirrors `main.py`'s `_needs_onboarding`
 * (the Qt app's own first-run gate): true when the live config has no
 * Anthropic key, or no resume file that still exists on disk. Fails
 * *open* (returns `false`, i.e. "don't show the wizard") on a network
 * error — a broken status check must never trap an already-configured
 * user behind a wizard they can't get past; the Settings screen remains
 * reachable either way to fix anything that's actually missing.
 */
export async function fetchNeedsOnboarding(): Promise<boolean> {
  try {
    const resp = await fetch("/api/onboarding/status", { credentials: "same-origin" });
    if (!resp.ok) {
      return false;
    }
    const body = asRecord(await resp.json());
    return body.needs_onboarding === true;
  } catch {
    return false;
  }
}

export interface TestKeyResult {
  ok: boolean;
  error: string | null;
}

/**
 * `POST /api/onboarding/test-key` — one real round trip to the Anthropic
 * API with the given key. The key is never persisted by this call; it
 * only reaches disk when the wizard finishes via `PUT /api/settings`.
 * Resolves (never throws) with `{ ok: false, error }` on any failure,
 * including a network error reaching our own backend, so the caller can
 * render a single status line either way.
 */
export async function testApiKey(apiKey: string, model = "claude-haiku-4-5"): Promise<TestKeyResult> {
  const body: TestKeyRequest = { api_key: apiKey, model };
  try {
    const resp = await fetch("/api/onboarding/test-key", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const json = asRecord(await resp.json().catch(() => ({})));
    if (!resp.ok) {
      return { ok: false, error: typeof json.detail === "string" ? json.detail : `request failed: ${resp.status}` };
    }
    return {
      ok: json.ok === true,
      error: typeof json.error === "string" ? json.error : null,
    };
  } catch {
    return { ok: false, error: "could not reach the server" };
  }
}

/** One pre-fillable configuration field as `GET /api/onboarding/prefill`
 * reports it (T52). `provenance` is the product spec's vocabulary; `origin` is
 * the same thing in a sentence, which is what actually gets shown — a value
 * whose origin the user can't tell is a value they can't judge. */
export interface PrefillField {
  /** `string` for the country, `string[]` for the three list fields. */
  value: string | string[];
  provenance: "user" | "agentic" | "heuristic" | "absent";
  confidence: number | null;
  method: string;
  origin: string;
  label: string;
  source_field: string;
  /** The configuration field(s) this lands in, e.g.
   * `["location_filter.search_country", "sources[].location"]`. */
  targets: string[];
  /** Non-empty when there is something to say beyond the value — chiefly why
   * a field came back empty. */
  note: string;
}

export interface Prefill {
  resume_on_file: boolean;
  api_key_configured: boolean;
  /** Which extraction actually read the resume. `none` means nothing did. */
  extraction: "agentic" | "heuristic" | "none";
  extraction_note: string;
  fields: Record<string, PrefillField>;
}

export const PREFILL_SEARCH_COUNTRY = "search_country";
export const PREFILL_SEARCH_TERMS = "search_terms";
export const PREFILL_JOB_FUNCTIONS = "job_functions";
export const PREFILL_SENIORITY_LEVELS = "seniority_levels";

/**
 * `GET /api/onboarding/prefill` — what the resume says the search should be,
 * with each value's provenance (T52). Read-only: it writes
 * nothing, and the values only reach disk if the user finishes the wizard,
 * via `PUT /api/settings`.
 *
 * Returns `null` on any failure rather than throwing. A pre-fill is an
 * assist; failing to fetch one must leave the wizard fully usable with
 * whatever the user types, which is the same state a resume that yields
 * nothing leaves it in.
 */
export async function fetchPrefill(): Promise<Prefill | null> {
  try {
    const resp = await fetch("/api/onboarding/prefill", { credentials: "same-origin" });
    if (!resp.ok) return null;
    const body = asRecord(await resp.json());
    if (typeof body.extraction !== "string" || typeof body.fields !== "object") return null;
    return body as unknown as Prefill;
  } catch {
    return null;
  }
}

/**
 * `POST /api/onboarding/complete` (T61) — the single page's Start/Finish
 * button, separate from `PUT /api/settings`. Marks the wizard itself as
 * done so a reload never shows it again, with or without an Anthropic
 * key — the defect T61 fixes: before this endpoint existed,
 * `needs_onboarding` only ever recomputed "no key or no resume" on every
 * load, so finishing without a key meant seeing the wizard forever.
 * Resolves `false` (never throws) on failure; the caller still calls
 * `onDone()` so the user is not stuck behind a failed network call after
 * their settings already saved.
 */
export async function completeOnboarding(): Promise<boolean> {
  try {
    const resp = await fetch("/api/onboarding/complete", {
      method: "POST",
      credentials: "same-origin",
    });
    return resp.ok;
  } catch {
    return false;
  }
}

/**
 * `POST /api/onboarding/acknowledge-disclosure` — records that the
 * The product spec data-disclosure step was shown and accepted. Best-effort:
 * a failure here does not block Finish (the disclosure was still shown
 * and accepted client-side; losing the server-side record of *when* is
 * not worth blocking the user's first scan over).
 */
export async function acknowledgeDisclosure(): Promise<boolean> {
  try {
    const resp = await fetch("/api/onboarding/acknowledge-disclosure", {
      method: "POST",
      credentials: "same-origin",
    });
    return resp.ok;
  } catch {
    return false;
  }
}
