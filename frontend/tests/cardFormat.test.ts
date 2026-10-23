// Tests for T64's new pure card-formatting additions to cardFormat.ts:
// the list row's short meta line (company · location · arrangement), the
// detail pane's structured facts grid, and the "gaps" breakdown derived
// from the same per-signal scoring data `whyMatchedLine` already reads.
import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { buildFactsGrid, deriveGaps, rowMetaLine } from "../src/components/cardFormat.ts";
import type { JobCardBase } from "../src/types/job.ts";

function baseCard(overrides: Partial<JobCardBase> = {}): JobCardBase {
  return {
    job_id: "j1",
    title: "QA Engineer",
    company: "Acme",
    location: "Remote",
    score: 80,
    source: "linkedin",
    url: "https://example.com/j1",
    url_direct: "",
    skills: [],
    reasoning: "",
    signals: {},
    geo_warning: null,
    work_arrangement: "Remote",
    employment_type: "Full-time",
    job_level: "",
    job_function: "",
    listing_type: "",
    company_industry: "",
    company_num_employees: "",
    compensation_min: null,
    compensation_max: null,
    compensation_currency: "",
    compensation_interval: "",
    date_posted: "",
    matched_at: "2026-10-22T09:12:00",
    scan_id: null,
    company_url: "",
    city: "",
    matched_skills: [],
    ...overrides,
  };
}

describe("rowMetaLine", () => {
  it("joins company, location and arrangement with the shared separator", () => {
    const line = rowMetaLine(baseCard());
    assert.equal(line, "Acme  ·  Remote  ·  Remote");
  });

  it("omits an Unknown arrangement rather than printing the literal word", () => {
    const line = rowMetaLine(baseCard({ work_arrangement: "Unknown" }));
    assert.equal(line, "Acme  ·  Remote");
  });

  it("returns null when every part is empty", () => {
    const line = rowMetaLine(baseCard({ company: "", location: "", work_arrangement: "Unknown" }));
    assert.equal(line, null);
  });
});

describe("buildFactsGrid", () => {
  it("fills every fact with a stated blank instead of omitting the row", () => {
    const facts = buildFactsGrid(baseCard());
    const byLabel = Object.fromEntries(facts.map((f) => [f.label, f.value]));
    assert.equal(byLabel["Seniority"], "Not listed");
    assert.equal(byLabel["Function"], "Not listed");
    assert.equal(byLabel["Posted"], "Not listed");
    assert.equal(byLabel["Source"], "LinkedIn");
    assert.equal(byLabel["Company size"], "Not listed");
    assert.equal(byLabel["Compensation"], "Not listed");
  });

  it("surfaces real values when present", () => {
    const facts = buildFactsGrid(
      baseCard({
        job_level: "Senior",
        job_function: "Engineering",
        company_num_employees: "500",
        compensation_min: 100000,
        compensation_max: 120000,
        compensation_currency: "USD",
        compensation_interval: "yearly",
      }),
    );
    const byLabel = Object.fromEntries(facts.map((f) => [f.label, f.value]));
    assert.equal(byLabel["Seniority"], "Senior");
    assert.equal(byLabel["Function"], "Engineering");
    assert.equal(byLabel["Company size"], "500 employees");
    assert.match(byLabel["Compensation"], /100k/);
  });
});

describe("deriveGaps", () => {
  it("lists signal labels scoring below the strong-match threshold", () => {
    const gaps = deriveGaps({ title: 90, skills: 50, tools: 20 });
    assert.deepEqual(gaps, ["skills", "tools"]);
  });

  it("returns an empty list when there are no signals or all are strong", () => {
    assert.deepEqual(deriveGaps({}), []);
    assert.deepEqual(deriveGaps({ title: 95 }), []);
  });
});
