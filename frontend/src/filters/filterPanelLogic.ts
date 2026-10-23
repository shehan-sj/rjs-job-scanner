// Pure logic behind T59's inline filter panel (docs/DESIGN.md principle
// 3 / the task ledger) -- reading the real `GET /api/settings` sections the
// panel's four controls correspond to, and building the minimal
// `PUT /api/settings` partial bodies for each one. No React, no fetch --
// kept pure and separate from FilterPanel.tsx the same way T58 kept
// batch grouping in jobBatching.ts.
//
// Field sourcing (grep'd against jobfinder/config.py, not guessed):
//   - "Minimum score"  -> `match_threshold`, a TOP-LEVEL `Config` field,
//     not nested under any section.
//   - "Freshness"      -> `SourceConfig.hours_old`, the same field the
//     Settings modal's per-source "Job freshness" slider already writes
//     (SettingsView.tsx). The panel has one slider, not one per source,
//     so it writes the same hours value onto every configured source.
//   - "Arrangement"    -> `tag_visibility.hide_remote` / `hide_hybrid` /
//     `hide_onsite`. The segmented control only offers Any/Remote/Hybrid
//     (per docs/DESIGN.md principle 3's own wording) -- "on-site only"
//     stays reachable through the modal's three independent checkboxes.
//   - "Hide" checkboxes -> `hide_internship` / `hide_contract` live on
//     `tag_visibility`; `hide_staffing_agencies` / `hide_promoted` live
//     on the *different* `quality_filter` section (config.py:134-142
//     and :135-142) -- getting this split wrong is exactly T46's lesson
//     ("a partial body sent against the wrong section").
import type { SettingsPayload, SourceSettings, SourceIn } from "../api/settings";

export type Arrangement = "any" | "remote" | "hybrid";

export interface FilterPanelValues {
  matchThreshold: number;
  freshnessHours: number;
  arrangement: Arrangement;
  hideInternship: boolean;
  hideContract: boolean;
  hideStaffingAgencies: boolean;
  hidePromoted: boolean;
}

/** The three `tag_visibility` fields the Arrangement control touches. */
export interface ArrangementTagVisibility {
  hide_remote: boolean;
  hide_hybrid: boolean;
  hide_onsite: boolean;
}

/** Derives the segmented control's position from the three raw toggles.
 * Only the two combinations the control itself can produce
 * (`tagVisibilityForArrangement`) map to "remote"/"hybrid" -- anything
 * else (including "all three hidden", reachable only via the modal)
 * reports "any" rather than guessing, since "any" is also what a fresh
 * install (nothing hidden) correctly resolves to. */
export function arrangementFromTagVisibility(
  hideRemote: boolean,
  hideHybrid: boolean,
  hideOnsite: boolean,
): Arrangement {
  if (!hideRemote && hideHybrid && hideOnsite) return "remote";
  if (hideRemote && !hideHybrid && hideOnsite) return "hybrid";
  return "any";
}

export function tagVisibilityForArrangement(arrangement: Arrangement): ArrangementTagVisibility {
  if (arrangement === "remote") return { hide_remote: false, hide_hybrid: true, hide_onsite: true };
  if (arrangement === "hybrid") return { hide_remote: true, hide_hybrid: false, hide_onsite: true };
  return { hide_remote: false, hide_hybrid: false, hide_onsite: false };
}

/** Reads the panel's four controls' current values off a full
 * `GET /api/settings` payload. `sources[0]`'s `hours_old` stands in for
 * "freshness" when at least one source exists; a config with none yet
 * (never scanned) falls back to `SourceConfig`'s own default of 24. */
export function extractFilterPanelValues(settings: SettingsPayload): FilterPanelValues {
  return {
    matchThreshold: settings.match_threshold,
    freshnessHours: settings.sources[0]?.hours_old ?? 24,
    arrangement: arrangementFromTagVisibility(
      settings.tag_visibility.hide_remote,
      settings.tag_visibility.hide_hybrid,
      settings.tag_visibility.hide_onsite,
    ),
    hideInternship: settings.tag_visibility.hide_internship,
    hideContract: settings.tag_visibility.hide_contract,
    hideStaffingAgencies: settings.quality_filter.hide_staffing_agencies,
    hidePromoted: settings.quality_filter.hide_promoted,
  };
}

/** Builds every `SourceIn` entry with `hours_old` overridden to `hours`,
 * preserving every other field -- including the redacted `extra` shadow
 * keys (`<cred>_set`/`<cred>_last4`), which `_merge_extra`
 * (jobfinder/web/settings.py) already ignores on the way in, and the raw
 * credential, which is simply absent from a redacted GET response and so
 * is never present to overwrite with blank. This is required because
 * `_apply_update` replaces the *entire* `sources` array whenever the key
 * is present at all (jobfinder/web/settings.py) -- sending only the
 * source(s) the user happens to be looking at would silently drop every
 * other one, the array-shaped version of T46's defect. */
export function sourcesWithUniformHours(sources: SourceSettings[], hours: number): SourceIn[] {
  return sources.map((src) => ({
    name: src.name,
    enabled: src.enabled,
    site_names: src.site_names,
    search_terms: src.search_terms,
    location: src.location,
    results_wanted: src.results_wanted,
    hours_old: hours,
    linkedin_fetch_description: src.linkedin_fetch_description,
    extra: src.extra,
  }));
}

export function buildTagVisibilityPatch(opts: {
  arrangement?: Arrangement;
  hideInternship?: boolean;
  hideContract?: boolean;
}): Partial<ArrangementTagVisibility & { hide_internship: boolean; hide_contract: boolean }> {
  const patch: Record<string, boolean> = {};
  if (opts.arrangement !== undefined) {
    Object.assign(patch, tagVisibilityForArrangement(opts.arrangement));
  }
  if (opts.hideInternship !== undefined) patch.hide_internship = opts.hideInternship;
  if (opts.hideContract !== undefined) patch.hide_contract = opts.hideContract;
  return patch;
}

export function buildQualityFilterPatch(opts: {
  hideStaffingAgencies?: boolean;
  hidePromoted?: boolean;
}): Partial<{ hide_staffing_agencies: boolean; hide_promoted: boolean }> {
  const patch: Record<string, boolean> = {};
  if (opts.hideStaffingAgencies !== undefined) patch.hide_staffing_agencies = opts.hideStaffingAgencies;
  if (opts.hidePromoted !== undefined) patch.hide_promoted = opts.hidePromoted;
  return patch;
}

/** The shape of a partial `PUT /api/settings` body this panel ever
 * sends -- a strict subset of the generated `SettingsUpdate` schema
 * (frontend/src/api/schema.ts), kept separate from it because that
 * generated type marks `match_threshold`/`check_interval_minutes`/
 * `filter_contracts` as *required* top-level keys (an openapi-typescript
 * quirk: Pydantic defaults only make *nested* model fields optional in
 * the generated schema) -- exactly what a genuinely partial body must
 * not send. */
export type FilterPatch = {
  match_threshold?: number;
  sources?: SourceIn[];
  tag_visibility?: Partial<ArrangementTagVisibility & { hide_internship: boolean; hide_contract: boolean }>;
  quality_filter?: Partial<{ hide_staffing_agencies: boolean; hide_promoted: boolean }>;
};

/** Coalesces an incoming partial patch onto an already-pending one.
 *
 * This is the client-side guard against T46's exact failure shape
 * a later patch touching one section must
 * never erase an earlier, still-unsent patch to a *different* section,
 * and a later change to a field already pending must win over the
 * earlier one (the "last input" the DoD requires) without discarding a
 * sibling field in the same section. `sources` is merged as a whole
 * array, matching `_apply_update`'s own all-or-nothing replacement of
 * that key -- there is no finer-grained merge to do here client-side.
 */
export function mergeFilterPatch(base: FilterPatch, incoming: FilterPatch): FilterPatch {
  const merged: FilterPatch = { ...base };
  if (incoming.match_threshold !== undefined) {
    merged.match_threshold = incoming.match_threshold;
  }
  if (incoming.sources !== undefined) {
    merged.sources = incoming.sources;
  }
  if (incoming.tag_visibility !== undefined) {
    merged.tag_visibility = { ...base.tag_visibility, ...incoming.tag_visibility };
  }
  if (incoming.quality_filter !== undefined) {
    merged.quality_filter = { ...base.quality_filter, ...incoming.quality_filter };
  }
  return merged;
}

export function isEmptyFilterPatch(patch: FilterPatch): boolean {
  return Object.keys(patch).length === 0;
}
