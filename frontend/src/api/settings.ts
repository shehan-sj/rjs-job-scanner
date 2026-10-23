// Settings data fetching + mutation (T25) — GET/PUT /api/settings,
// POST /api/resume. Mirrors `jobfinder/ui/settings.py`'s SettingsDialog
// one-to-one for *data*, not widget layout (that's SettingsView.tsx).
//
// `GET /api/settings` has no declared FastAPI `response_model`
// (jobfinder/web/settings.py's `_settings_payload` returns a plain
// dict), so `npm run gen:api` can only produce `{ [key: string]: unknown }`
// for it (same situation client.ts already documents for the
// agentic_filter narrowing it does). `SettingsPayload` below is this
// view's local narrowing of that payload, built from
// `_settings_payload`'s actual shape (the real server-side contract)
// rather than hand-guessed — same convention as `src/types/job.ts`'s
// `toMatchCard`/etc.
//
// The PUT request body, by contrast, *is* typed by the generated schema
// (`components["schemas"]["SettingsUpdate"]` and friends) since
// `SettingsUpdate` is a real Pydantic model with a declared shape.
import type { components, operations } from "./schema";

export type SettingsUpdate = components["schemas"]["SettingsUpdate"];
export type SourceIn = components["schemas"]["SourceIn"];

type PutSettingsResponse =
  operations["put_settings_api_settings_put"]["responses"][200]["content"]["application/json"];

function asRecord(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null ? (value as Record<string, unknown>) : {};
}

function str(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

function num(value: unknown, fallback = 0): number {
  return typeof value === "number" ? value : fallback;
}

function bool(value: unknown, fallback = false): boolean {
  return typeof value === "boolean" ? value : fallback;
}

function strList(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((v): v is string => typeof v === "string") : [];
}

function optStr(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

/** Narrowed `anthropic` section of `GET /api/settings`.
 * `api_key` is always `""` — write-only, see module docstring on the
 * backend (jobfinder/web/settings.py) and never populate a form field
 * from this value. */
export interface AnthropicSettings {
  api_key: "";
  api_key_set: boolean;
  api_key_last4: string | null;
  model: string;
}

/** Narrowed per-source `extra` dict as redacted by `redact_extra`
 * (jobfinder/web/redact.py) — any credential-shaped key is replaced by
 * `<key>_set`/`<key>_last4`, everything else (e.g. adzuna's `country`)
 * passes through unredacted. Read a specific credential's redacted
 * shadow fields with `sourceCredentialState`, below. */
export type RedactedExtra = Record<string, unknown>;

export interface SourceSettings {
  name: string;
  enabled: boolean;
  site_names: string[];
  search_terms: string[];
  location: string;
  results_wanted: number;
  hours_old: number;
  linkedin_fetch_description: boolean;
  extra: RedactedExtra;
}

/** Whether/what-last4 a named credential inside a redacted `extra` dict
 * reports as set. Never returns the real value — there isn't one here. */
export function sourceCredentialState(
  extra: RedactedExtra,
  key: string,
): { set: boolean; last4: string | null } {
  return {
    set: bool(extra[`${key}_set`]),
    last4: optStr(extra[`${key}_last4`]),
  };
}

/** `resume` section of `GET /api/settings` (T45) — read-only, display
 * only. `filename` is never a filesystem path (`jobfinder/web/resume.py`'s
 * `sanitize_resume_display_name`, applied again on the way out by
 * `jobfinder/web/settings.py`), and `PUT` cannot change either field. */
export interface ResumeSettings {
  uploaded: boolean;
  filename: string | null;
}

export interface SettingsPayload {
  resume: ResumeSettings;
  check_interval_minutes: number;
  match_threshold: number;
  filter_contracts: boolean;
  anthropic: AnthropicSettings;
  sources: SourceSettings[];
  notifications: { auto_dismiss_seconds: number };
  location_filter: {
    preferred_regions: string[];
    search_country: string;
    remote_ok_worldwide: boolean;
  };
  tag_visibility: {
    hide_full_time: boolean;
    hide_part_time: boolean;
    hide_contract: boolean;
    hide_temporary: boolean;
    hide_internship: boolean;
    hide_remote: boolean;
    hide_hybrid: boolean;
    hide_onsite: boolean;
  };
  agentic_filter: { enabled: boolean; min_local_score: number };
  role_filter: { job_functions: string[]; seniority_levels: string[] };
  quality_filter: {
    hide_staffing_agencies: boolean;
    hide_promoted: boolean;
    min_salary: number;
    min_company_size: number;
    max_post_age_days: number;
  };
  fillerbot: {
    enabled: boolean;
    page_timeout_seconds: number;
    auto_fill_low_confidence: boolean;
  };
  ui: {
    show_log_console: boolean;
    theme: string;
    console_show_timestamp: boolean;
    console_show_level: boolean;
    console_hidden_levels: string[];
  };
}

function toSource(value: unknown): SourceSettings {
  const raw = asRecord(value);
  return {
    name: str(raw.name),
    enabled: bool(raw.enabled, true),
    site_names: strList(raw.site_names),
    search_terms: strList(raw.search_terms),
    location: str(raw.location),
    results_wanted: num(raw.results_wanted, 20),
    hours_old: num(raw.hours_old, 24),
    linkedin_fetch_description: bool(raw.linkedin_fetch_description),
    extra: asRecord(raw.extra),
  };
}

/** Narrows the raw `GET /api/settings` JSON into {@link SettingsPayload}.
 * Every field has a safe default so a partial/unexpected payload (a
 * future field addition, a transient backend regression) degrades to
 * "nothing selected" rather than throwing — same fail-closed convention
 * `client.ts`'s `isAgenticFilterEnabled` already uses. */
export function toSettingsPayload(value: unknown): SettingsPayload {
  const raw = asRecord(value);
  const resume = asRecord(raw.resume);
  const ant = asRecord(raw.anthropic);
  const ntf = asRecord(raw.notifications);
  const lf = asRecord(raw.location_filter);
  const tv = asRecord(raw.tag_visibility);
  const af = asRecord(raw.agentic_filter);
  const rf = asRecord(raw.role_filter);
  const qf = asRecord(raw.quality_filter);
  const fb = asRecord(raw.fillerbot);
  const ui = asRecord(raw.ui);

  return {
    resume: {
      uploaded: bool(resume.uploaded),
      filename: optStr(resume.filename),
    },
    check_interval_minutes: num(raw.check_interval_minutes, 30),
    match_threshold: num(raw.match_threshold, 70),
    filter_contracts: bool(raw.filter_contracts),
    anthropic: {
      api_key: "",
      api_key_set: bool(ant.api_key_set),
      api_key_last4: optStr(ant.api_key_last4),
      model: str(ant.model, "claude-opus-4-6"),
    },
    sources: Array.isArray(raw.sources) ? raw.sources.map(toSource) : [],
    notifications: { auto_dismiss_seconds: num(ntf.auto_dismiss_seconds, 0) },
    location_filter: {
      preferred_regions: strList(lf.preferred_regions),
      search_country: str(lf.search_country),
      remote_ok_worldwide: bool(lf.remote_ok_worldwide, true),
    },
    tag_visibility: {
      hide_full_time: bool(tv.hide_full_time),
      hide_part_time: bool(tv.hide_part_time),
      hide_contract: bool(tv.hide_contract),
      hide_temporary: bool(tv.hide_temporary),
      hide_internship: bool(tv.hide_internship, true),
      hide_remote: bool(tv.hide_remote),
      hide_hybrid: bool(tv.hide_hybrid),
      hide_onsite: bool(tv.hide_onsite),
    },
    agentic_filter: {
      enabled: bool(af.enabled),
      min_local_score: num(af.min_local_score, 60),
    },
    role_filter: {
      job_functions: strList(rf.job_functions),
      seniority_levels: strList(rf.seniority_levels),
    },
    quality_filter: {
      hide_staffing_agencies: bool(qf.hide_staffing_agencies, true),
      hide_promoted: bool(qf.hide_promoted),
      min_salary: num(qf.min_salary, 0),
      min_company_size: num(qf.min_company_size, 0),
      max_post_age_days: num(qf.max_post_age_days, 0),
    },
    fillerbot: {
      enabled: bool(fb.enabled, true),
      page_timeout_seconds: num(fb.page_timeout_seconds, 30),
      auto_fill_low_confidence: bool(fb.auto_fill_low_confidence),
    },
    ui: {
      show_log_console: bool(ui.show_log_console, true),
      theme: str(ui.theme, "dark"),
      console_show_timestamp: bool(ui.console_show_timestamp, true),
      console_show_level: bool(ui.console_show_level, true),
      console_hidden_levels: strList(ui.console_hidden_levels),
    },
  };
}

/** `GET /api/settings`, narrowed. Throws on a network/non-2xx failure —
 * unlike `client.ts`'s `fetchSettings` (best-effort, used only for the
 * theme pre-load), the settings screen needs to distinguish "failed to
 * load" from "loaded, all defaults" so it can show a real error. */
export async function fetchFullSettings(): Promise<SettingsPayload> {
  const resp = await fetch("/api/settings", { credentials: "same-origin" });
  if (!resp.ok) {
    throw new Error(`GET /api/settings failed: ${resp.status}`);
  }
  return toSettingsPayload(await resp.json());
}

/** `PUT /api/settings`. Returns the new (narrowed) payload on success —
 * same shape `GET` returns, per `_put_settings_sync`'s return value. A
 * non-2xx response (422 validation failure — bounds-checked fields like
 * `match_threshold`) throws with the raw body text so the caller can
 * surface the Pydantic validation detail. */
export async function putSettings(update: SettingsUpdate): Promise<SettingsPayload> {
  const resp = await fetch("/api/settings", {
    method: "PUT",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(update),
  });
  if (!resp.ok) {
    const text = await resp.text();
    throw new Error(`PUT /api/settings failed: ${resp.status} ${text}`);
  }
  const body = (await resp.json()) as PutSettingsResponse;
  return toSettingsPayload(body);
}

/** `PUT /api/settings`, but with a genuinely partial body rather than
 * the full {@link SettingsUpdate} shape `putSettings` requires.
 *
 * `SettingsUpdate` (from the generated schema) is not usable for a
 * partial write: it marks `match_threshold`/`check_interval_minutes`/
 * `filter_contracts` as *required* keys even though they have server-
 * side defaults, because openapi-typescript only marks *nested* Pydantic
 * model fields optional. Sending those three every time would defeat
 * `exclude_unset` the same way a full-body PUT does. This instead takes
 * a plain partial object (T59's `FilterPatch`,
 * `frontend/src/filters/filterPanelLogic.ts`) and sends exactly its own
 * keys — the backend's `update.model_dump(exclude_unset=True)`
 * (jobfinder/web/settings.py) does the rest, same endpoint, same merge
 * logic `putSettings` already relies on, no new route. */
export async function putSettingsPatch(patch: Record<string, unknown>): Promise<SettingsPayload> {
  const resp = await fetch("/api/settings", {
    method: "PUT",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
  if (!resp.ok) {
    const text = await resp.text();
    throw new Error(`PUT /api/settings failed: ${resp.status} ${text}`);
  }
  const body = (await resp.json()) as PutSettingsResponse;
  return toSettingsPayload(body);
}

/** `POST /api/reset` (jobfinder/web/scan.py) — wipes every job-data table
 * plus the resume-profile cache. There is no narrower "just the cache"
 * endpoint at this layer (v3's separate "Delete resume-profile cache"
 * button had no server-side counterpart to call over HTTP without
 * clearing job data too), so the Advanced tab only exposes this combined
 * action — see SettingsView.tsx's Advanced tab for the user-facing copy. */
export async function resetAllData(): Promise<void> {
  const resp = await fetch("/api/reset", { method: "POST", credentials: "same-origin" });
  if (!resp.ok) {
    throw new Error(`POST /api/reset failed: ${resp.status}`);
  }
}

export interface ResumeUploadResult {
  status: string;
  text_length: number;
  profile_status: string;
  /** The sanitised display name the server stored (T45) — never a path.
   * Lets the uploading browser update its own UI immediately instead of
   * waiting on a GET /api/settings round trip. */
  filename: string;
}

/** `POST /api/resume` — upload only, see jobfinder/web/resume.py's module
 * docstring. On a 400/413 the server's `detail` message is surfaced
 * (unparseable file, wrong format, too large); the caller is expected to
 * show it next to the file picker. */
export async function uploadResume(file: File): Promise<ResumeUploadResult> {
  const body = new FormData();
  body.append("file", file);
  const resp = await fetch("/api/resume", {
    method: "POST",
    credentials: "same-origin",
    body,
  });
  const json = (await resp.json().catch(() => ({}))) as Record<string, unknown>;
  if (!resp.ok) {
    const detail = typeof json.detail === "string" ? json.detail : `upload failed: ${resp.status}`;
    throw new Error(detail);
  }
  return {
    status: str(json.status),
    text_length: num(json.text_length),
    profile_status: str(json.profile_status),
    filename: str(json.filename),
  };
}
