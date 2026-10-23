// Tests for the country/search-location link control (T61).
//
// Run with `npm run test:logic` (`node --test`), same as
// onboardingPrefill.test.ts. What is worth testing here is purely the state
// transitions: whether the link holds, mirrors, breaks and restores
// correctly, and — the one failure mode that matters most — that breaking
// the link never touches either field's *value*, only whether future edits
// mirror.
import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  applyCountryEdit,
  applyLocationEdit,
  breakLink,
  initialLinkState,
  restoreLink,
} from "../src/views/onboardingLocationLink.ts";

describe("initialLinkState", () => {
  it("starts linked when both are blank", () => {
    assert.equal(initialLinkState("", ""), true);
  });

  it("starts linked when a prior session mirrored them (T52's own behavior)", () => {
    assert.equal(initialLinkState("Ireland", "Ireland"), true);
  });

  it("starts unlinked when they already disagree", () => {
    assert.equal(initialLinkState("Canada", "Toronto, Ontario, Canada"), false);
  });
});

describe("applyCountryEdit", () => {
  it("mirrors into the location field while linked", () => {
    assert.deepEqual(applyCountryEdit(true, "Spain"), { searchCountry: "Spain", searchLocation: "Spain" });
  });

  it("leaves the location field alone once unlinked", () => {
    assert.deepEqual(applyCountryEdit(false, "Spain"), { searchCountry: "Spain" });
  });
});

describe("applyLocationEdit", () => {
  it("is a plain field update with no side effect on the link state", () => {
    assert.deepEqual(applyLocationEdit("Toronto, Ontario, Canada"), {
      searchLocation: "Toronto, Ontario, Canada",
    });
  });
});

describe("breakLink / restoreLink", () => {
  it("breaking changes only the link flag, not either field's value", () => {
    assert.deepEqual(breakLink(), { linked: false });
  });

  it("restoring re-syncs the location field to the country field's current value", () => {
    assert.deepEqual(restoreLink("Ireland"), { linked: true, searchLocation: "Ireland" });
  });
});
