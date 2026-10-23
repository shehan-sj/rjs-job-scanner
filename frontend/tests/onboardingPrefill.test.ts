// Tests for the pure logic behind resume-driven onboarding (T52).
//
// Run with `npm run test:logic` (`node --test`, which strips the types
// itself — no transpile step, no test framework added), same as
// control.test.ts.
//
// The React wiring is covered end to end on the Python side
// (tests/test_onboarding_prefill.py drives the real API). What is worth
// testing here is the two pieces of pure logic that can silently lose a
// user's data:
//
//   1. `buildSources` preferring the *stored* search terms over the form's.
//      That discarded every edit the moment config had anything in it: a
//      second pass through the wizard saved the old terms straight back over
//      the pre-filled ones the user had just corrected. It is the exact
//      failure the DoD's "persists the edit, not the derived value" clause
//      is about, and it is invisible — the wizard says "Saved".
//
// 2. The rule that a pre-fill only ever seeds a *blank* field. The product spec's
//      first precedence rule is that a user edit is never silently
//      overwritten, "not by a re-parse, not by a better extraction"; a
//      pre-fill that overwrote a saved setting on re-entry would break it
//      from the other side.
import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { buildSources, type FormState } from "../src/views/settingsForm.ts";
import type { SourceSettings } from "../src/api/settings.ts";

function form(overrides: Partial<FormState>): FormState {
  // Only the fields `buildSources` reads matter; the rest of FormState is
  // irrelevant here and is left off.
  const sources: FormState["sources"] = {};
  for (const name of ["linkedin", "indeed", "remotive", "arbeitnow", "jsearch", "adzuna"]) {
    sources[name] = {
      enabled: name === "linkedin",
      hoursOld: 24,
      credentialInputs: {},
      credentialState: {},
      extraPassthrough: {},
    };
  }
  const base = {
    jobTitlesText: "",
    searchLocation: "",
    resultsPerTitle: 50,
    remoteSweepEnabled: false,
    sweepResults: 30,
    sources,
  };
  return Object.assign(base, overrides) as unknown as FormState;
}

function storedLinkedin(overrides: Partial<SourceSettings> = {}): SourceSettings {
  return {
    name: "linkedin",
    enabled: true,
    site_names: ["linkedin"],
    search_terms: [],
    location: "",
    results_wanted: 50,
    hours_old: 24,
    linkedin_fetch_description: true,
    extra: {},
    ...overrides,
  } as SourceSettings;
}

describe("buildSources: the edit wins over what is already stored", () => {
  it("writes the form's titles even when config already has some", () => {
    const existing = [storedLinkedin({ search_terms: ["Staff Nurse"], location: "Ireland" })];
    const built = buildSources(form({ jobTitlesText: "Theatre Nurse", searchLocation: "Spain" }), existing);

    const primary = built.find((s) => s.name === "linkedin");
    assert.deepEqual(primary?.search_terms, ["Theatre Nurse"]);
    assert.equal(primary?.location, "Spain");
  });

  it("clears the primary location when the user clears the field", () => {
    const existing = [storedLinkedin({ location: "Ireland" })];
    const built = buildSources(form({ jobTitlesText: "Theatre Nurse", searchLocation: "" }), existing);

    assert.equal(built.find((s) => s.name === "linkedin")?.location, "");
  });

  it("leaves a non-primary source's own search terms alone", () => {
    // `indeed`'s terms are not represented anywhere in this form, so there
    // is no edit to honour and overwriting them would be destructive.
    const existing = [
      storedLinkedin(),
      { ...storedLinkedin(), name: "indeed", search_terms: ["Clinical Nurse"], site_names: ["indeed"] },
    ];
    const built = buildSources(form({ jobTitlesText: "Theatre Nurse" }), existing);

    assert.deepEqual(built.find((s) => s.name === "indeed")?.search_terms, ["Clinical Nurse"]);
  });

  it("seeds a source that has no stored terms from the form", () => {
    const built = buildSources(form({ jobTitlesText: "Theatre Nurse" }), [storedLinkedin()]);

    assert.deepEqual(built.find((s) => s.name === "indeed")?.search_terms, ["Theatre Nurse"]);
  });
});
