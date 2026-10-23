// Settings form state (T25) — the editable in-memory shape the Settings
// screen's 7 tabs read/write, plus the translation to/from
// `SettingsPayload` (GET) and `SettingsUpdate` (PUT).
//
// Multi-line fields (regions, job titles, job functions, seniority
// levels) are kept as raw newline-delimited text while editing, exactly
// like `jobfinder/ui/settings.py`'s `QPlainTextEdit` fields — split into
// a trimmed, blank-filtered list only at save time
// (`_save`'s `[t.strip() for t in ... if t.strip()]`).
import type { SettingsUpdate, SourceIn, SourceSettings, SettingsPayload } from "../api/settings";
// Extension-ful on purpose (`allowImportingTsExtensions` is on): this is the
// only *value* import in this module, and without it `node --test` cannot
// resolve the specifier, which is what `tests/onboardingPrefill.test.ts`
// needs in order to exercise `buildSources` with no transpile step.
import { sourceCredentialState } from "../api/settings.ts";

export const MODELS = [
  "claude-haiku-4-5",
  "claude-haiku-4-5-20251001",
  "claude-sonnet-4-5",
  "claude-opus-4-6",
];

/** The base four sources v3's Sources tab exposed
 * (`jobfinder/ui/settings.py`'s `_AVAILABLE_SOURCES`) — label/hint text
 * is copied verbatim for parity. */
export const CORE_SOURCES: { name: string; label: string; hint: string }[] = [
  {
    name: "linkedin",
    label: "LinkedIn",
    hint:
      "Scraped via python-jobspy. Best source for Canada — strong title/location " +
      "filtering, detailed descriptions, structured fields (skills, salary, company " +
      "size). Searches ca.linkedin.com/jobs directly.",
  },
  {
    name: "indeed",
    label: "Indeed",
    hint:
      "Scraped via python-jobspy. Large Canadian job board (ca.indeed.com). " +
      "Good volume but posts tend to skew older — the freshness filter may remove " +
      "many results. Best for broad QA/engineering roles.",
  },
  {
    name: "remotive",
    label: "Remotive",
    hint:
      "Free API, no key required. Remote-only jobs worldwide — small board with " +
      "mostly tech/startup roles. Low volume for niche titles, but catches remote " +
      "opportunities other sources miss.",
  },
  {
    name: "arbeitnow",
    label: "Arbeitnow",
    hint:
      "Free API, no key required. Primarily European & remote jobs. Server-side " +
      "search filtering is weak — may return many irrelevant results. Best if you " +
      "are open to EU-based remote work.",
  },
];

/** Sources added after v3 (the product spec delta) that need a credential before
 * they'll return anything — not present in `_AVAILABLE_SOURCES`, so
 * there's no v3 copy to match; hints are written fresh from each
 * source's own module docstring. */
export const CREDENTIAL_SOURCES: {
  name: string;
  label: string;
  hint: string;
  fields: { key: string; label: string; secret: boolean }[];
}[] = [
  {
    name: "jsearch",
    label: "JSearch (RapidAPI)",
    hint:
      "Official RapidAPI aggregator — LinkedIn, Indeed, Glassdoor, ZipRecruiter and " +
      "more via one key. Free tier: 500 requests/month. Sign up at rapidapi.com and " +
      "subscribe to JSearch.",
    fields: [{ key: "api_key", label: "RapidAPI key", secret: true }],
  },
  {
    name: "adzuna",
    label: "Adzuna",
    hint:
      "Official Adzuna jobs API — aggregates listings from across the web, " +
      "including LinkedIn-posted roles. Free tier: 250 requests/day. Sign up at " +
      "developer.adzuna.com.",
    fields: [
      { key: "app_id", label: "Application ID", secret: true },
      { key: "app_key", label: "Application key", secret: true },
    ],
  },
];

export const ALL_SOURCE_NAMES = [
  ...CORE_SOURCES.map((s) => s.name),
  ...CREDENTIAL_SOURCES.map((s) => s.name),
];

// Only these names feed jobspy (`jobfinder/sources/jobspy_source.py`) and
// so share the "Results per title" slider on save — same
// `jobspy_sites = {"linkedin", "indeed", "google", "glassdoor",
// "zip_recruiter"}` set `_save` uses. "google"/"glassdoor"/
// "zip_recruiter" aren't offered in this UI (v3 never exposed them
// either) but are kept here so a config file that already lists one
// isn't silently dropped (`buildSources` only touches ALL_SOURCE_NAMES
// entries; anything else in the loaded config is out of this form's
// reach and is left untouched by `_apply_update`'s per-name merge on
// the backend).
const JOBSPY_SITE_NAMES = new Set(["linkedin", "indeed", "google", "glassdoor", "zip_recruiter"]);

export interface SourceFormState {
  enabled: boolean;
  hoursOld: number;
  /** Blank by default — never pre-filled from a redacted GET response.
   * Keyed by the credential field's name (e.g. "api_key", "app_id"). */
  credentialInputs: Record<string, string>;
  /** Whether/last4 each credential currently reports as set server-side
   * (display only, never editable). */
  credentialState: Record<string, { set: boolean; last4: string | null }>;
  /** Non-secret passthrough extras (adzuna's "country"); not shown as a
   * credential, but must round-trip on save. */
  extraPassthrough: Record<string, unknown>;
}

export interface FormState {
  checkIntervalMinutes: number;
  matchThreshold: number;

  apiKeySet: boolean;
  apiKeyLast4: string | null;
  apiKeyInput: string;
  model: string;

  preferredRegionsText: string;
  searchCountry: string;
  remoteOkWorldwide: boolean;

  jobTitlesText: string;
  searchLocation: string;
  resultsPerTitle: number;
  remoteSweepEnabled: boolean;
  sweepResults: number;

  sources: Record<string, SourceFormState>;

  jobFunctionsText: string;
  seniorityLevelsText: string;

  hideFullTime: boolean;
  hidePartTime: boolean;
  hideContract: boolean;
  hideTemporary: boolean;
  hideInternship: boolean;
  hideRemote: boolean;
  hideHybrid: boolean;
  hideOnsite: boolean;
  filterContracts: boolean;
  hideStaffingAgencies: boolean;
  hidePromoted: boolean;
  minSalary: number;
  minCompanySize: number;
  maxPostAgeDays: number;

  agenticEnabled: boolean;
  agenticMinScore: number;
  fillerbotEnabled: boolean;
  fbTimeoutSeconds: number;
  fbAutoFillLow: boolean;

  showLogConsole: boolean;
  consoleShowTimestamp: boolean;
  consoleShowLevel: boolean;
  theme: "dark" | "light";
}

function linesToList(text: string): string[] {
  return text
    .split("\n")
    .map((l) => l.trim())
    .filter((l) => l.length > 0);
}

function sourceByName(sources: SourceSettings[], name: string): SourceSettings | undefined {
  // The *first* match only — a second "linkedin" entry is the remote
  // sweep (settings.py:806-815 on the backend), handled separately.
  return sources.find((s) => s.name === name);
}

function credentialFieldsFor(name: string): { key: string; secret: boolean }[] {
  const found = CREDENTIAL_SOURCES.find((s) => s.name === name);
  return found ? found.fields : [];
}

function buildSourceFormState(sources: SourceSettings[], name: string): SourceFormState {
  const existing = sourceByName(sources, name);
  const credentialInputs: Record<string, string> = {};
  const credentialState: Record<string, { set: boolean; last4: string | null }> = {};
  for (const field of credentialFieldsFor(name)) {
    credentialInputs[field.key] = "";
    credentialState[field.key] = existing
      ? sourceCredentialState(existing.extra, field.key)
      : { set: false, last4: null };
  }
  const extraPassthrough: Record<string, unknown> = {};
  if (existing) {
    for (const [key, value] of Object.entries(existing.extra)) {
      if (key.endsWith("_set") || key.endsWith("_last4")) continue;
      if (credentialInputs[key] !== undefined) continue; // the credential itself, never present post-redaction anyway
      extraPassthrough[key] = value;
    }
  }
  return {
    enabled: existing?.enabled ?? false,
    hoursOld: existing?.hours_old ?? 24,
    credentialInputs,
    credentialState,
    extraPassthrough,
  };
}

/** Builds the initial {@link FormState} from a freshly-fetched
 * {@link SettingsPayload}. Never seeds `apiKeyInput` or any
 * `credentialInputs` from the response — both stay blank, per the
 * write-only contract. */
export function initFormState(settings: SettingsPayload): FormState {
  const primary = sourceByName(settings.sources, "linkedin");
  const linkedinEntries = settings.sources.filter((s) => s.name === "linkedin");
  const sweep = linkedinEntries.length > 1 ? linkedinEntries[1] : undefined;

  const sources: Record<string, SourceFormState> = {};
  for (const name of ALL_SOURCE_NAMES) {
    sources[name] = buildSourceFormState(settings.sources, name);
  }

  return {
    checkIntervalMinutes: settings.check_interval_minutes,
    matchThreshold: settings.match_threshold,

    apiKeySet: settings.anthropic.api_key_set,
    apiKeyLast4: settings.anthropic.api_key_last4,
    apiKeyInput: "",
    model: settings.anthropic.model,

    preferredRegionsText: settings.location_filter.preferred_regions.join("\n"),
    searchCountry: settings.location_filter.search_country,
    remoteOkWorldwide: settings.location_filter.remote_ok_worldwide,

    jobTitlesText: (primary?.search_terms ?? []).join("\n"),
    searchLocation: primary?.location ?? "",
    resultsPerTitle: primary?.results_wanted ?? 50,
    remoteSweepEnabled: Boolean(sweep && sweep.enabled),
    sweepResults: sweep?.results_wanted ?? 30,

    sources,

    jobFunctionsText: settings.role_filter.job_functions.join("\n"),
    seniorityLevelsText: settings.role_filter.seniority_levels.join("\n"),

    hideFullTime: settings.tag_visibility.hide_full_time,
    hidePartTime: settings.tag_visibility.hide_part_time,
    hideContract: settings.tag_visibility.hide_contract,
    hideTemporary: settings.tag_visibility.hide_temporary,
    hideInternship: settings.tag_visibility.hide_internship,
    hideRemote: settings.tag_visibility.hide_remote,
    hideHybrid: settings.tag_visibility.hide_hybrid,
    hideOnsite: settings.tag_visibility.hide_onsite,
    filterContracts: settings.filter_contracts,
    hideStaffingAgencies: settings.quality_filter.hide_staffing_agencies,
    hidePromoted: settings.quality_filter.hide_promoted,
    minSalary: settings.quality_filter.min_salary,
    minCompanySize: settings.quality_filter.min_company_size,
    maxPostAgeDays: settings.quality_filter.max_post_age_days,

    agenticEnabled: settings.agentic_filter.enabled,
    agenticMinScore: settings.agentic_filter.min_local_score,
    fillerbotEnabled: settings.fillerbot.enabled,
    fbTimeoutSeconds: settings.fillerbot.page_timeout_seconds,
    fbAutoFillLow: settings.fillerbot.auto_fill_low_confidence,

    showLogConsole: settings.ui.show_log_console,
    consoleShowTimestamp: settings.ui.console_show_timestamp,
    consoleShowLevel: settings.ui.console_show_level,
    theme: settings.ui.theme === "light" ? "light" : "dark",
  };
}

/** Builds every `SourceIn` entry for the PUT body — port of
 * `jobfinder/ui/settings.py`'s `_save` source-building loop
 * (lines 773-816), including the remote-sweep derived write
 * (settings.py:806-815). `slow_mo` is deliberately **not** reproduced:
 * it has no field anywhere in `SourceConfig`/`SourceIn` (confirmed by
 * grep — only the Qt dialog and wizard ever wrote it to YAML; nothing
 * in `jobfinder/sources/*.py` or `config.py` ever reads it back), so it
 * was always dead weight, not a setting to port.
 */
export function buildSources(form: FormState, existing: SourceSettings[]): SourceIn[] {
  const terms = linesToList(form.jobTitlesText);
  const searchLoc = form.searchLocation.trim();
  const sources: SourceIn[] = [];

  for (const name of ALL_SOURCE_NAMES) {
    const existingSrc = sourceByName(existing, name);
    const formSrc = form.sources[name];
    const isJobspy = JOBSPY_SITE_NAMES.has(name);

    const siteNames =
      existingSrc && existingSrc.site_names.length > 0
        ? existingSrc.site_names
        : isJobspy
          ? [name]
          : [];
    // The form's titles and location *are* the primary source's, both on the
    // Settings screen (`initFormState` reads them from it) and in onboarding
    // (T52 pre-fills them from the resume). Preferring the stored value there
    // discarded every edit the moment config had anything in it — a
    // second pass through the wizard silently saved the old terms back. The
    // other jobspy sources keep that preference: their per-source terms are
    // not represented in this form, so the form has no edit to honour.
    const isPrimary = name === "linkedin";
    const searchTerms =
      !isPrimary && existingSrc && existingSrc.search_terms.length > 0
        ? existingSrc.search_terms
        : terms;
    const location = isPrimary ? searchLoc : existingSrc?.location || searchLoc;
    const resultsWanted = isJobspy ? form.resultsPerTitle : existingSrc?.results_wanted ?? 20;

    const extra: Record<string, unknown> = { ...formSrc.extraPassthrough };
    for (const field of credentialFieldsFor(name)) {
      // Blank means "leave the stored credential unchanged" —
      // `_merge_extra`'s write-only contract (jobfinder/web/settings.py).
      extra[field.key] = formSrc.credentialInputs[field.key] ?? "";
    }

    sources.push({
      name,
      enabled: formSrc.enabled,
      site_names: siteNames,
      search_terms: searchTerms,
      location,
      results_wanted: resultsWanted,
      hours_old: formSrc.hoursOld,
      // Hardcoded True for every LinkedIn entry, same as `_save` — never
      // a user-facing toggle in v3 either.
      linkedin_fetch_description: name === "linkedin" ? true : existingSrc?.linkedin_fetch_description ?? false,
      extra,
    });
  }

  if (form.remoteSweepEnabled && terms.length > 0) {
    const sweepTerms = terms.slice(0, 4).map((t) => `${t} remote`);
    sources.push({
      name: "linkedin",
      enabled: true,
      site_names: ["linkedin"],
      search_terms: sweepTerms,
      location: searchLoc,
      results_wanted: form.sweepResults,
      hours_old: form.sources.linkedin.hoursOld,
      linkedin_fetch_description: true,
      extra: {},
    });
  }

  return sources;
}

/** Builds the full `PUT /api/settings` body from the form state. */
export function buildSettingsUpdate(form: FormState, existingSources: SourceSettings[]): SettingsUpdate {
  return {
    check_interval_minutes: form.checkIntervalMinutes,
    match_threshold: form.matchThreshold,
    filter_contracts: form.filterContracts,
    anthropic: {
      api_key: form.apiKeyInput.trim(),
      model: form.model,
    },
    sources: buildSources(form, existingSources),
    // `auto_dismiss_seconds` has no UI control (the task ledger — it's
    // configurable in two places and read by none, `ui/tray.py:139`
    // hardcodes 5000) — carried forward unchanged rather than reset to
    // the model default on every save, in case it's ever revived.
    notifications: { auto_dismiss_seconds: 0 },
    location_filter: {
      preferred_regions: linesToList(form.preferredRegionsText),
      search_country: form.searchCountry.trim(),
      remote_ok_worldwide: form.remoteOkWorldwide,
    },
    tag_visibility: {
      hide_full_time: form.hideFullTime,
      hide_part_time: form.hidePartTime,
      hide_contract: form.hideContract,
      hide_temporary: form.hideTemporary,
      hide_internship: form.hideInternship,
      hide_remote: form.hideRemote,
      hide_hybrid: form.hideHybrid,
      hide_onsite: form.hideOnsite,
    },
    agentic_filter: {
      enabled: form.agenticEnabled,
      min_local_score: form.agenticMinScore,
    },
    role_filter: {
      job_functions: linesToList(form.jobFunctionsText),
      seniority_levels: linesToList(form.seniorityLevelsText),
    },
    quality_filter: {
      hide_staffing_agencies: form.hideStaffingAgencies,
      hide_promoted: form.hidePromoted,
      min_salary: form.minSalary,
      min_company_size: form.minCompanySize,
      max_post_age_days: form.maxPostAgeDays,
    },
    fillerbot: {
      enabled: form.fillerbotEnabled,
      page_timeout_seconds: form.fbTimeoutSeconds,
      auto_fill_low_confidence: form.fbAutoFillLow,
    },
    ui: {
      show_log_console: form.showLogConsole,
      theme: form.theme,
      console_show_timestamp: form.consoleShowTimestamp,
      console_show_level: form.consoleShowLevel,
    },
  };
}
