// Tests for T59's inline filter panel pure logic — reading the real
// config sections (docs/DESIGN.md principle 3), deriving
// the Arrangement segmented control from `tag_visibility`'s three
// arrangement toggles, and building the PUT /api/settings patch bodies.
//
// Run with `npm run test:logic` (`node --test`), same convention as
// jobBatching.test.ts / control.test.ts.
import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  arrangementFromTagVisibility,
  buildQualityFilterPatch,
  buildTagVisibilityPatch,
  extractFilterPanelValues,
  mergeFilterPatch,
  sourcesWithUniformHours,
  tagVisibilityForArrangement,
  type FilterPatch,
} from "../src/filters/filterPanelLogic.ts";
import type { SettingsPayload, SourceSettings } from "../src/api/settings.ts";

function source(overrides: Partial<SourceSettings> & { name: string }): SourceSettings {
  return {
    enabled: true,
    site_names: [],
    search_terms: [],
    location: "",
    results_wanted: 20,
    hours_old: 24,
    linkedin_fetch_description: false,
    extra: {},
    ...overrides,
  };
}

function settings(overrides: Partial<SettingsPayload> = {}): SettingsPayload {
  return {
    resume: { uploaded: false, filename: null },
    check_interval_minutes: 20,
    match_threshold: 65,
    filter_contracts: true,
    anthropic: { api_key: "", api_key_set: false, api_key_last4: null, model: "claude-opus-4-6" },
    sources: [source({ name: "linkedin", hours_old: 24 })],
    notifications: { auto_dismiss_seconds: 0 },
    location_filter: { preferred_regions: [], search_country: "", remote_ok_worldwide: true },
    tag_visibility: {
      hide_full_time: false,
      hide_part_time: false,
      hide_contract: false,
      hide_temporary: false,
      hide_internship: true,
      hide_remote: false,
      hide_hybrid: false,
      hide_onsite: false,
    },
    agentic_filter: { enabled: false, min_local_score: 60 },
    role_filter: { job_functions: [], seniority_levels: [] },
    quality_filter: {
      hide_staffing_agencies: true,
      hide_promoted: false,
      min_salary: 0,
      min_company_size: 0,
      max_post_age_days: 0,
    },
    fillerbot: { enabled: true, page_timeout_seconds: 30, auto_fill_low_confidence: false },
    ui: { show_log_console: true, theme: "dark", console_show_timestamp: true, console_show_level: true },
    ...overrides,
  };
}

describe("arrangementFromTagVisibility", () => {
  it("is 'any' when nothing is hidden", () => {
    assert.equal(arrangementFromTagVisibility(false, false, false), "any");
  });

  it("is 'remote' when hybrid+onsite are hidden and remote is not", () => {
    assert.equal(arrangementFromTagVisibility(false, true, true), "remote");
  });

  it("is 'hybrid' when remote+onsite are hidden and hybrid is not", () => {
    assert.equal(arrangementFromTagVisibility(true, false, true), "hybrid");
  });

  it("falls back to 'any' for a combination the segmented control can't represent", () => {
    // All three hidden isn't reachable from this control -- only the
    // modal's three independent checkboxes can produce it -- so it must
    // not be misreported as one of the two real selections.
    assert.equal(arrangementFromTagVisibility(true, true, true), "any");
    assert.equal(arrangementFromTagVisibility(true, false, false), "any");
  });
});

describe("tagVisibilityForArrangement / round trip", () => {
  it("round-trips through arrangementFromTagVisibility for all three options", () => {
    for (const arr of ["any", "remote", "hybrid"] as const) {
      const tv = tagVisibilityForArrangement(arr);
      assert.equal(arrangementFromTagVisibility(tv.hide_remote, tv.hide_hybrid, tv.hide_onsite), arr);
    }
  });
});

describe("extractFilterPanelValues", () => {
  it("reads match_threshold from the top level, not a nested section", () => {
    const s = settings({ match_threshold: 71 });
    assert.equal(extractFilterPanelValues(s).matchThreshold, 71);
  });

  it("reads freshness from sources[0].hours_old, the same field the modal writes", () => {
    const s = settings({ sources: [source({ name: "linkedin", hours_old: 48 })] });
    assert.equal(extractFilterPanelValues(s).freshnessHours, 48);
  });

  it("reads hideInternship/hideContract from tag_visibility and hideStaffingAgencies/hidePromoted from quality_filter -- different sections", () => {
    const s = settings({
      tag_visibility: {
        hide_full_time: false,
        hide_part_time: false,
        hide_contract: true,
        hide_temporary: false,
        hide_internship: true,
        hide_remote: false,
        hide_hybrid: false,
        hide_onsite: false,
      },
      quality_filter: {
        hide_staffing_agencies: false,
        hide_promoted: true,
        min_salary: 0,
        min_company_size: 0,
        max_post_age_days: 0,
      },
    });
    const values = extractFilterPanelValues(s);
    assert.equal(values.hideInternship, true);
    assert.equal(values.hideContract, true);
    assert.equal(values.hideStaffingAgencies, false);
    assert.equal(values.hidePromoted, true);
  });
});

describe("sourcesWithUniformHours", () => {
  it("overrides hours_old on every source while preserving every other field", () => {
    const existing = [
      source({ name: "linkedin", hours_old: 24, search_terms: ["QA"], extra: { api_key_set: true } }),
      source({ name: "jsearch", hours_old: 12, enabled: false, extra: { api_key_set: true, api_key_last4: "abcd" } }),
    ];
    const result = sourcesWithUniformHours(existing, 72);
    assert.equal(result.length, 2);
    assert.equal(result[0].hours_old, 72);
    assert.equal(result[1].hours_old, 72);
    // Nothing else changed -- this is what stops the freshness slider
    // from wiping search terms / enabled state / credential shadow keys
    // when it has to send the whole `sources` array.
    assert.deepEqual(result[0].search_terms, ["QA"]);
    assert.equal(result[1].enabled, false);
    assert.deepEqual(result[1].extra, { api_key_set: true, api_key_last4: "abcd" });
  });
});

describe("buildTagVisibilityPatch / buildQualityFilterPatch", () => {
  it("builds a minimal tag_visibility patch for the arrangement control", () => {
    const patch = buildTagVisibilityPatch({ arrangement: "remote" });
    assert.deepEqual(patch, { hide_remote: false, hide_hybrid: true, hide_onsite: true });
  });

  it("builds a minimal quality_filter patch for the hide checkboxes", () => {
    const patch = buildQualityFilterPatch({ hidePromoted: true });
    assert.deepEqual(patch, { hide_promoted: true });
  });
});

// ── The T46 regression, at the client's coalescing layer ─────────────────
//
// T46's failure mode: a partial PUT body caused *omitted*
// sections to be reset to their Pydantic defaults. The backend already
// guards against that (tests/test_web_settings.py's
// `test_single_field_put_leaves_every_other_section_byte_identical` /
// `test_nested_partial_put_changes_only_the_named_field`) -- but only if
// the *client* builds its partial body correctly in the first place.
// `mergeFilterPatch` is what the debounced writer (debouncedPatchQueue.ts)
// uses to coalesce several rapid UI changes into one outgoing body; if it
// ever let a later patch silently drop an earlier section, the panel
// would reproduce T46's defect from the browser instead of the server.
describe("mergeFilterPatch -- rapid consecutive changes coalesce without losing a section", () => {
  it("accumulates changes to two different sections without either overwriting the other", () => {
    let pending: FilterPatch = {};
    pending = mergeFilterPatch(pending, { match_threshold: 70 });
    pending = mergeFilterPatch(pending, { tag_visibility: { hide_internship: false } });
    pending = mergeFilterPatch(pending, { quality_filter: { hide_promoted: true } });
    assert.deepEqual(pending, {
      match_threshold: 70,
      tag_visibility: { hide_internship: false },
      quality_filter: { hide_promoted: true },
    });
  });

  it("keeps only the last value when the same field changes twice, but doesn't drop a sibling field in the same section", () => {
    let pending: FilterPatch = {};
    pending = mergeFilterPatch(pending, { tag_visibility: { hide_contract: true } });
    pending = mergeFilterPatch(pending, { tag_visibility: { hide_internship: false } });
    pending = mergeFilterPatch(pending, { tag_visibility: { hide_contract: false } }); // changed mind
    assert.deepEqual(pending, { tag_visibility: { hide_contract: false, hide_internship: false } });
  });

  it("a later whole-array sources patch doesn't blank a match_threshold or tag_visibility change queued earlier", () => {
    let pending: FilterPatch = {};
    pending = mergeFilterPatch(pending, { match_threshold: 80 });
    pending = mergeFilterPatch(pending, { sources: sourcesWithUniformHours(settings().sources, 96) });
    assert.equal(pending.match_threshold, 80);
    assert.ok(pending.sources);
    assert.equal(pending.sources?.[0]?.hours_old, 96);
  });
});
