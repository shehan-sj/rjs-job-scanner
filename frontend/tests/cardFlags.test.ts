// Tests for T81's `cardFlags` pure function (the product spec brief: "derived
// from dates, never typed"). Run with `npm run test:logic` (`node --test`),
// same pattern as jobBatching.test.ts / railLogic.test.ts.
import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { cardFlags } from "../src/components/cardFlags.ts";
import type { BoardCard } from "../src/types/job.ts";

const NOW = new Date("2026-10-22T00:00:00.000Z");

function isoDaysAgo(days: number): string {
  return new Date(NOW.getTime() - days * 24 * 60 * 60 * 1000).toISOString().replace("Z", "");
}

function card(overrides: Partial<BoardCard> = {}): BoardCard {
  return {
    job_id: "job-1",
    title: "QA Engineer",
    company: "Globex",
    location: "Remote",
    score: 80,
    source: "linkedin",
    url: "https://example.com/job-1",
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
    compensation_min: 100000,
    compensation_max: 130000,
    compensation_currency: "USD",
    compensation_interval: "yearly",
    date_posted: "",
    matched_at: "",
    scan_id: null,
    company_url: "",
    city: "",
    matched_skills: [],
    stage: "new",
    stage_entered_at: null,
    removed_by: null,
    reply_note: null,
    tier: "global",
    tier_location_is_unresolved: false,
    same_country_tier_configured: true,
    ...overrides,
  };
}

describe("cardFlags — applied aging (7d / 14d thresholds)", () => {
  it("no flag the day before 7 days applied", () => {
    const flags = cardFlags(card({ stage: "applied", stage_entered_at: isoDaysAgo(6) }), NOW);
    assert.deepEqual(flags.map((f) => f.key), []);
  });

  it("'Worth a nudge' exactly at 7 days applied", () => {
    const flags = cardFlags(card({ stage: "applied", stage_entered_at: isoDaysAgo(7) }), NOW);
    assert.deepEqual(flags.map((f) => f.key), ["worth-a-nudge"]);
    assert.equal(flags[0].tone, "warn");
  });

  it("'Worth a nudge' the day after 7 days applied", () => {
    const flags = cardFlags(card({ stage: "applied", stage_entered_at: isoDaysAgo(8) }), NOW);
    assert.deepEqual(flags.map((f) => f.key), ["worth-a-nudge"]);
  });

  it("still 'Worth a nudge' the day before 14 days applied", () => {
    const flags = cardFlags(card({ stage: "applied", stage_entered_at: isoDaysAgo(13) }), NOW);
    assert.deepEqual(flags.map((f) => f.key), ["worth-a-nudge"]);
  });

  it("'Gone quiet' (not 'Worth a nudge') exactly at 14 days applied", () => {
    const flags = cardFlags(card({ stage: "applied", stage_entered_at: isoDaysAgo(14) }), NOW);
    assert.deepEqual(flags.map((f) => f.key), ["gone-quiet"]);
    assert.equal(flags[0].tone, "neutral");
  });

  it("'Gone quiet' the day after 14 days applied", () => {
    const flags = cardFlags(card({ stage: "applied", stage_entered_at: isoDaysAgo(15) }), NOW);
    assert.deepEqual(flags.map((f) => f.key), ["gone-quiet"]);
  });

  it("no applied-aging flag outside the applied stage", () => {
    const flags = cardFlags(card({ stage: "shortlist", stage_entered_at: isoDaysAgo(30) }), NOW);
    assert.ok(!flags.some((f) => f.key === "worth-a-nudge" || f.key === "gone-quiet"));
  });
});

describe("cardFlags — shortlist aging (5d threshold)", () => {
  it("no flag the day before 5 days shortlisted", () => {
    const flags = cardFlags(card({ stage: "shortlist", stage_entered_at: isoDaysAgo(4) }), NOW);
    assert.deepEqual(flags.map((f) => f.key), []);
  });

  it("'Sitting a while' exactly at 5 days shortlisted", () => {
    const flags = cardFlags(card({ stage: "shortlist", stage_entered_at: isoDaysAgo(5) }), NOW);
    assert.deepEqual(flags.map((f) => f.key), ["sitting-a-while"]);
    assert.equal(flags[0].tone, "warn");
  });

  it("'Sitting a while' the day after 5 days shortlisted", () => {
    const flags = cardFlags(card({ stage: "shortlist", stage_entered_at: isoDaysAgo(6) }), NOW);
    assert.deepEqual(flags.map((f) => f.key), ["sitting-a-while"]);
  });
});

describe("cardFlags — contract employment type", () => {
  it("flags a contract role", () => {
    const flags = cardFlags(card({ employment_type: "Contract" }), NOW);
    assert.ok(flags.some((f) => f.key === "contract" && f.tone === "warn"));
  });

  it("is case-insensitive", () => {
    const flags = cardFlags(card({ employment_type: "contract" }), NOW);
    assert.ok(flags.some((f) => f.key === "contract"));
  });

  it("does not flag a full-time role", () => {
    const flags = cardFlags(card({ employment_type: "Full-time" }), NOW);
    assert.ok(!flags.some((f) => f.key === "contract"));
  });
});

describe("cardFlags — pay not stated", () => {
  it("flags when both compensation fields are absent", () => {
    const flags = cardFlags(card({ compensation_min: null, compensation_max: null }), NOW);
    assert.ok(flags.some((f) => f.key === "pay-not-stated" && f.tone === "neutral"));
  });

  it("does not flag when at least one compensation field is present", () => {
    const flags = cardFlags(card({ compensation_min: 50000, compensation_max: null }), NOW);
    assert.ok(!flags.some((f) => f.key === "pay-not-stated"));
  });

  it("does not flag a zero-but-present compensation differently than absent (0 is falsy, same as the brief's wording)", () => {
    const flags = cardFlags(card({ compensation_min: 0, compensation_max: 0 }), NOW);
    assert.ok(flags.some((f) => f.key === "pay-not-stated"));
  });
});

describe("cardFlags — Not for me provenance", () => {
  it("flags an agent dismissal", () => {
    const flags = cardFlags(card({ stage: "out", removed_by: "agent" }), NOW);
    assert.ok(flags.some((f) => f.key === "ai-filter" && f.tone === "neutral"));
  });

  it("does not flag a user dismissal", () => {
    const flags = cardFlags(card({ stage: "out", removed_by: "user" }), NOW);
    assert.ok(!flags.some((f) => f.key === "ai-filter"));
  });
});

describe("cardFlags — unresolved location", () => {
  it("flags when the location could not be parsed", () => {
    const flags = cardFlags(card({ tier_location_is_unresolved: true, compensation_min: null, compensation_max: null }), NOW);
    assert.ok(flags.some((f) => f.key === "location-unresolved"));
  });
});

describe("cardFlags — at most two, highest priority kept", () => {
  it("keeps only the first two in priority order when more would apply", () => {
    const flags = cardFlags(
      card({
        stage: "applied",
        stage_entered_at: isoDaysAgo(20),
        employment_type: "Contract",
        compensation_min: null,
        compensation_max: null,
        tier_location_is_unresolved: true,
      }),
      NOW,
    );
    assert.equal(flags.length, 2);
    assert.deepEqual(flags.map((f) => f.key), ["gone-quiet", "contract"]);
  });
});
