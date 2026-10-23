// T89 — the DoD's central assertion: enumerate every control
// `FilterPanel` offers **against `FilterPanel`'s own field list**
// (`FilterPanelValues`, filterPanelLogic.ts), so a control dropped from
// the board's popover mount (`BoardView.tsx`) fails this test rather than
// going unnoticed. `BOARD_FILTER_CONTROL_TEST_IDS` (boardFilterControls.ts)
// is the shared manifest `FilterPanel.tsx` stamps onto each control's DOM
// node and `scripts/t89_filters_browser_check.py` queries for — this test
// checks that manifest's *keys* line up with the real runtime shape
// `extractFilterPanelValues` reads off a `GET /api/settings` payload, not
// a hand-retyped copy of the field list.
//
// Run with `npm run test:logic` (`node --test`), same convention as
// filterPanelLogic.test.ts / jobBatching.test.ts.
import assert from "node:assert/strict";
import { describe, it } from "node:test";

import type { SettingsPayload, SourceSettings } from "../src/api/settings.ts";
import { BOARD_FILTER_CONTROL_TEST_IDS } from "../src/filters/boardFilterControls.ts";
import { extractFilterPanelValues } from "../src/filters/filterPanelLogic.ts";

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

describe("BOARD_FILTER_CONTROL_TEST_IDS", () => {
  it("covers exactly the fields extractFilterPanelValues reads off a real settings payload", () => {
    const values = extractFilterPanelValues(settings());
    const expectedFields = Object.keys(values).sort();
    const coveredFields = Object.keys(BOARD_FILTER_CONTROL_TEST_IDS).sort();
    assert.deepEqual(
      coveredFields,
      expectedFields,
      "a field was added to or dropped from FilterPanelValues without updating BOARD_FILTER_CONTROL_TEST_IDS " +
        "-- every control FilterPanel offers must stay reachable from the board",
    );
  });

  it("gives every control its own test id -- no two controls share a DOM hook", () => {
    const ids = Object.values(BOARD_FILTER_CONTROL_TEST_IDS);
    assert.equal(new Set(ids).size, ids.length, "two controls share the same test id");
  });
});
