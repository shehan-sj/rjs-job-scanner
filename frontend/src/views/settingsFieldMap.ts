// Settings field map (T53) — which tab owns each setting, and the words
// the screen uses for it.
//
// WHY THIS FILE EXISTS
//
// The owner reported "duplication between Basic setup and Role". There was
// no duplication: there was **one concept living in two homes**, which
// reads as duplication from the outside.
//
//   * Role intent was split. `sources[].search_terms` ("job titles to
//     search for") sat in Basic setup, while `role_filter.job_functions`
//     and `role_filter.seniority_levels` — the two filters that decide
//     which of those titles' results survive — sat in Role. You could not
//     see the whole of "what job do I want" on one screen.
//
//   * Location was worse. Basic setup held *two different concepts under
//     similar labels*: `location_filter.preferred_regions` /
//     `location_filter.search_country` (where I am willing to work) next
//     to nothing explaining that `sources[].location` (where to point the
//     search) was a different question living on the Sources tab — while
//     `location_filter.remote_ok_worldwide`, which belongs to the first
//     concept, sat in Filters.
//
// So: role intent has one home (`role`), location has one home
// (`location`), and the two location concepts are tagged `concept` and
// rendered under headings that state the difference in words.
//
// WHAT IS DURABLE HERE, AND WHAT IS NOT
//
// The seven-tab settings modal is scheduled for deletion in Phase 8: T59
// moves the frequently-touched filters inline beside the results and T60
// replaces the modal with a navigation rail (docs/DESIGN.md: "The
// seven-tab modal is gone… Rarely-touched configuration (sources, API
// keys, appearance) stays on its own page, reached from the rail").
//
// **The field-to-group mapping and the labels below are meant to survive
// that.** They are the answer to "which of these belong together, and what
// do we call them", which is a question the navigation rail still has to
// answer. The `tab` key is the perishable part — when the modal goes,
// re-read `tab` as "group", keep the groups intact, and carry
// `groupLabel`/`groupHint` across verbatim. In particular `location`'s two
// groups should stay two labelled groups wherever they land; collapsing
// them back into one heading re-creates the exact defect T53 fixed.
//
// This module is not documentation — the tab components render from it
// (see `LocationTab` / `RoleTab` in SettingsTabs.tsx), and
// tests/test_settings_field_map.py asserts that the mapping declared here
// matches where the .tsx actually binds each field. If the two disagree,
// the test fails rather than this comment quietly becoming a lie.
//
// NOTE ON CONFIG KEYS: nothing here renames a config key. `configPath` is
// recorded, not changed. The YAML namespaces already disambiguate the two
// location concepts (`location_filter.*` vs. `sources[].location`); it was
// only the UI labels that didn't. Moving a field between tabs touches no
// config key, so there is no migration — see
// tests/test_config_roundtrip_pre_t53.py, which holds that line.

/** The settings groups a field can belong to. Today these are the tabs of
 * the settings modal; after Phase 8 they are groups on the configuration
 * page the navigation rail opens. */
export type SettingsTabKey =
  | "basic"
  | "role"
  | "location"
  | "sources"
  | "filters"
  | "ai"
  | "appearance"
  | "advanced";

// T98: "Scan frequency" lived under Appearance — a scanning setting filed
// under how the app looks, which is why the owner could not find "the
// time delay for the scan to restart". Sources is where the rest of the
// scan cycle already lives (results per title, remote sweep, per-source
// freshness — SourcesTab's own docstring already says "per scan cycle"),
// so that is its one home now. Appearance keeps the log console display
// toggles and the theme picker; nothing scanning-related is left there.

/** Which of the two location questions a field answers.
 *
 * `acceptance` — "where would I take a job?" Filters and classifies
 * matches that have already been fetched (`_classify_tier`'s tiers).
 * Narrowing it never changes what gets scraped.
 *
 * `search-scope` — "where should we look?" Passed to the job boards as the
 * geographic argument of the search itself. Narrowing it means fewer
 * postings are ever fetched.
 *
 * Fields outside the location group use `n/a`.
 */
export type LocationConcept = "acceptance" | "search-scope" | "n/a";

export interface SettingsFieldSpec {
  /** Owning group. Exactly one per field — that is the T53 invariant. */
  tab: SettingsTabKey;
  /** Where this lives in config.yaml. Recorded for traceability; T53
   * renames nothing. */
  configPath: string;
  /** The field's own label on screen. */
  label: string;
  /** The field's own hint text. */
  hint: string;
  /** For location fields, which of the two concepts this answers. */
  concept: LocationConcept;
  /** Heading of the group this field is rendered under. Fields sharing a
   * concept share a heading. */
  groupLabel: string;
  /** The heading's explanatory line. For the two location groups this is
   * where the difference between them is stated. */
  groupHint: string;
}

// Group headings for the two location concepts. Pulled out as constants
// because every field in a group must render the *same* heading, and
// because these two strings are the heart of the fix: each one names the
// other concept so a user reading either field alone can tell which
// question they are answering.
const ACCEPTANCE_GROUP_LABEL = "Where you'll accept a job";
const ACCEPTANCE_GROUP_HINT =
  "Sorts and filters the matches JobFinder has already found. This does not change " +
  "where JobFinder searches — for that, see 'Where JobFinder searches' below.";

const SEARCH_SCOPE_GROUP_LABEL = "Where JobFinder searches";
const SEARCH_SCOPE_GROUP_HINT =
  "The place name sent to each job board when it runs a search. Widening this fetches " +
  "more postings; it does not decide which postings you'd accept, which is set above.";

const ROLE_GROUP_LABEL = "What role do you want?";
const ROLE_GROUP_HINT =
  "Your job titles drive the searches. Job functions and seniority then narrow what " +
  "those searches bring back, so all three belong together.";

/** The settings whose placement T53 fixed, keyed by their form-state field
 * name (what the .tsx binds). Not every setting is listed — only the ones
 * whose home was ambiguous or split. Adding the rest is not needed to hold
 * the invariant and would be churn ahead of Phase 8. */
export const SETTINGS_FIELDS: Record<string, SettingsFieldSpec> = {
  jobTitlesText: {
    tab: "role",
    configPath: "sources[].search_terms",
    label: "Job titles to search for",
    hint: "One per line. Each is searched separately. Keep the list focused — 5-10 is usually plenty.",
    concept: "n/a",
    groupLabel: ROLE_GROUP_LABEL,
    groupHint: ROLE_GROUP_HINT,
  },
  jobFunctionsText: {
    tab: "role",
    configPath: "role_filter.job_functions",
    label: "Job functions",
    hint: "Restrict matches to specific LinkedIn job functions. Leave blank to accept all. One per line, written as LinkedIn writes them — e.g. Marketing, Legal, Health Care Provider.",
    concept: "n/a",
    groupLabel: ROLE_GROUP_LABEL,
    groupHint: ROLE_GROUP_HINT,
  },
  seniorityLevelsText: {
    tab: "role",
    configPath: "role_filter.seniority_levels",
    label: "Seniority levels",
    hint: "Restrict matches to specific LinkedIn seniority levels. Leave blank to accept all. Common values: Entry level, Associate, Mid-Senior level, Director, Executive.",
    concept: "n/a",
    groupLabel: ROLE_GROUP_LABEL,
    groupHint: ROLE_GROUP_HINT,
  },
  preferredRegionsText: {
    tab: "location",
    configPath: "location_filter.preferred_regions",
    label: "Cities or regions you prefer",
    hint: "Jobs in these places appear on the 'Preferred' tab. One per line.",
    concept: "acceptance",
    groupLabel: ACCEPTANCE_GROUP_LABEL,
    groupHint: ACCEPTANCE_GROUP_HINT,
  },
  searchCountry: {
    tab: "location",
    configPath: "location_filter.search_country",
    label: "Country you'll accept work in (optional)",
    hint: "Matches in this country but outside your preferred cities appear on the 'Same country' tab. Example: Canada, United States, United Kingdom.",
    concept: "acceptance",
    groupLabel: ACCEPTANCE_GROUP_LABEL,
    groupHint: ACCEPTANCE_GROUP_HINT,
  },
  remoteOkWorldwide: {
    tab: "location",
    configPath: "location_filter.remote_ok_worldwide",
    label: "Allow remote from any location",
    hint: "When on, remote postings skip the location check above regardless of where they're listed.",
    concept: "acceptance",
    groupLabel: ACCEPTANCE_GROUP_LABEL,
    groupHint: ACCEPTANCE_GROUP_HINT,
  },
  searchLocation: {
    tab: "location",
    configPath: "sources[].location",
    label: "Place name to search",
    hint: "Use a country ('Canada') or a specific city ('Toronto, Ontario, Canada'). Sent to every enabled job source.",
    concept: "search-scope",
    groupLabel: SEARCH_SCOPE_GROUP_LABEL,
    groupHint: SEARCH_SCOPE_GROUP_HINT,
  },
  checkIntervalMinutes: {
    tab: "sources",
    configPath: "check_interval_minutes",
    label: "Scan frequency",
    hint: "The time delay before JobFinder restarts the scan and checks for new postings again.",
    concept: "n/a",
    groupLabel: "",
    groupHint: "",
  },
};
