// Tests for T83's job detail panel pure logic (heading line, matched
// skill/gap split, facts grid, tier sentence, activity trail). Run with
// `npm run test:logic` (`node --test`), same pattern as
// boardLogic.test.ts/cardFlags.test.ts.
import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  activityTrailLines,
  askedForNotInResumeChips,
  foundAgoOnSourceLine,
  matchedSkillChips,
  panelFacts,
  panelRailFacts,
  panelFactsGrid,
  panelHeadingLine,
  panelIdentityLine,
  payChipText,
  positionLabel,
  tierSentence,
  whyScoredLine,
} from "../src/components/panelLogic.ts";
import type { BoardCard } from "../src/types/job.ts";

// Anchored to the real clock, deliberately.
//
// `timeAgo` (cardFormat.ts) — which `foundAgoOnSourceLine` and
// `panelHeadingLine` go through — reads `Date.now()` directly and takes
// no injectable clock. A fixture pinned to a literal date therefore
// agrees with it on exactly one day: these three tests passed when T83
// landed and went red the next day, which is worse than failing outright
// because a suite that is green by luck is not evidence of anything.
//
// `activityTrailLines` does take `now`, so it is handed this same anchor
// rather than a second one — one clock per test file.
const NOW = new Date();

function isoDaysAgo(days: number): string {
  return new Date(NOW.getTime() - days * 24 * 60 * 60 * 1000).toISOString().replace("Z", "");
}

function card(overrides: Partial<BoardCard> = {}): BoardCard {
  return {
    job_id: "job-1",
    title: "QA Engineer",
    company: "Globex",
    location: "Remote, Canada",
    score: 82,
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
    compensation_min: null,
    compensation_max: null,
    compensation_currency: "",
    compensation_interval: "",
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

describe("positionLabel", () => {
  it("is 1-indexed for the user-facing count", () => {
    assert.equal(positionLabel(0, 4, "Shortlist"), "Job 1 of 4 in Shortlist");
    assert.equal(positionLabel(3, 4, "Shortlist"), "Job 4 of 4 in Shortlist");
  });
});

describe("foundAgoOnSourceLine", () => {
  it("joins the relative age and the source label", () => {
    const line = foundAgoOnSourceLine(card({ matched_at: isoDaysAgo(3), source: "linkedin" }));
    assert.equal(line, "found 3d ago on LinkedIn");
  });

  it("is null with no date to report", () => {
    assert.equal(foundAgoOnSourceLine(card({ matched_at: "", date_posted: "" })), null);
  });
});

describe("panelHeadingLine", () => {
  it("joins company, city, arrangement, listing type and the found clause", () => {
    const line = panelHeadingLine(
      card({
        company: "Globex",
        city: "Toronto",
        work_arrangement: "Remote",
        listing_type: "Promoted",
        matched_at: isoDaysAgo(1),
        source: "indeed",
      }),
    );
    assert.equal(line, "Globex  ·  Toronto  ·  Remote  ·  Promoted  ·  found 1d ago on Indeed");
  });

  it("drops any part with no data rather than printing a stated blank", () => {
    const line = panelHeadingLine(card({ company: "Globex", city: "", work_arrangement: "Unknown", listing_type: "" }));
    assert.equal(line, "Globex");
  });
});

describe("whyScoredLine", () => {
  it("reuses the shared per-signal breakdown", () => {
    const line = whyScoredLine(card({ signals: { title: 80, skills: 20 } }));
    assert.equal(line, "✓ title  ·  ✗ skills");
  });

  it("is null with no signals", () => {
    assert.equal(whyScoredLine(card({ signals: {} })), null);
  });
});

describe("matched skills vs asked-for-not-in-résumé", () => {
  it("matched skills is the matched_skills field, not skills", () => {
    const c = card({ skills: ["Python", "SQL", "Kubernetes"], matched_skills: ["python", "sql"] });
    assert.deepEqual(matchedSkillChips(c), ["python", "sql"]);
  });

  it("the gap is skills minus matched_skills, case/whitespace-insensitive", () => {
    const c = card({ skills: ["Python", "SQL", "Kubernetes"], matched_skills: ["python", "  sql  "] });
    assert.deepEqual(askedForNotInResumeChips(c), ["Kubernetes"]);
  });

  it("is empty when the data doesn't distinguish the two sets", () => {
    const c = card({ skills: ["Python", "SQL"], matched_skills: ["Python", "SQL"] });
    assert.deepEqual(askedForNotInResumeChips(c), []);
  });

  it("never renders the same matched skill twice", () => {
    // The owner's screenshot showed "appium" as two chips. The server builds
    // `matched_skills` as `matched_skills + matched_tools` (local_matcher.py),
    // two independently-matched sets that can name the same term.
    const c = card({ matched_skills: ["appium", "cypress", "Appium", " appium "] });
    assert.deepEqual(matchedSkillChips(c), ["appium", "cypress"]);
  });

  it("drops blank entries from the matched chips", () => {
    const c = card({ matched_skills: ["cypress", "", "   "] });
    assert.deepEqual(matchedSkillChips(c), ["cypress"]);
  });

  it("never renders the same gap chip twice", () => {
    const c = card({ skills: ["Kafka", "kafka", "Terraform"], matched_skills: [] });
    assert.deepEqual(askedForNotInResumeChips(c), ["Kafka", "Terraform"]);
  });
});

describe("panelIdentityLine", () => {
  it("is the heading line without the provenance clause", () => {
    const c = card({
      company: "Globex",
      city: "Toronto",
      work_arrangement: "Remote",
      listing_type: "Promoted",
      matched_at: isoDaysAgo(1),
      source: "indeed",
    });
    // `panelHeadingLine` still carries "found 1d ago on Indeed"; this does not.
    assert.equal(panelIdentityLine(c), "Globex  ·  Toronto  ·  Remote  ·  Promoted");
    assert.match(panelHeadingLine(c), /found 1d ago on Indeed$/);
  });

  it("is null when the card carries no identity parts at all", () => {
    assert.equal(panelIdentityLine(card({ company: "", city: "", work_arrangement: "Unknown", listing_type: "" })), null);
  });
});

describe("payChipText", () => {
  it("drops the ported Qt-era money emoji", () => {
    const text = payChipText(
      card({
        compensation_min: 120000,
        compensation_max: 150000,
        compensation_currency: "USD",
        compensation_interval: "yearly",
      }),
    );
    assert.equal(text, "$120k – $150k/yr");
  });

  it("is null when the posting carries no compensation", () => {
    assert.equal(payChipText(card({ compensation_min: null, compensation_max: null })), null);
  });

  it("keeps a currency code when there is no symbol for it", () => {
    const text = payChipText(
      card({ compensation_min: 90000, compensation_max: null, compensation_currency: "SEK", compensation_interval: "yearly" }),
    );
    assert.equal(text, "90k SEK/yr");
  });
});

describe("panelFacts", () => {
  it("splits what the posting carried from what it did not", () => {
    const { known, missing } = panelFacts(
      card({ job_level: "Senior", company_industry: "Software", source: "linkedin" }),
    );
    assert.deepEqual(known.map((f) => f.label), ["Seniority", "Industry", "Source"]);
    assert.deepEqual(missing, ["Pay", "Function", "Listing type", "Company size", "Posted"]);
  });

  it("names every fact exactly once across the two lists", () => {
    const all = panelFactsGrid(card({}));
    const { known, missing } = panelFacts(card({}));
    assert.equal(known.length + missing.length, all.length);
  });

  it("has nothing missing when the posting carried everything", () => {
    const { missing } = panelFacts(
      card({
        compensation_min: 100000,
        compensation_max: 130000,
        compensation_currency: "USD",
        compensation_interval: "yearly",
        job_level: "Senior",
        job_function: "Engineering",
        listing_type: "Promoted",
        company_num_employees: "501-1000",
        company_industry: "Software",
        date_posted: isoDaysAgo(2),
        source: "linkedin",
      }),
    );
    assert.deepEqual(missing, []);
  });
});

describe("panelFactsGrid", () => {
  it("carries exactly the eight enumerated facts, every row stated even when blank", () => {
    const facts = panelFactsGrid(card({}));
    assert.deepEqual(facts.map((f) => f.label), [
      "Pay",
      "Seniority",
      "Function",
      "Listing type",
      "Company size",
      "Industry",
      "Posted",
      "Source",
    ]);
    assert.ok(facts.every((f) => f.value !== ""));
  });

  it("fills in real values when present", () => {
    const facts = panelFactsGrid(
      card({
        compensation_min: 100000,
        compensation_max: 130000,
        compensation_currency: "USD",
        compensation_interval: "yearly",
        job_level: "Senior",
        job_function: "Engineering",
        listing_type: "Promoted",
        company_num_employees: "501-1000",
        company_industry: "Software",
        date_posted: isoDaysAgo(2),
        source: "linkedin",
      }),
    );
    const byLabel = Object.fromEntries(facts.map((f) => [f.label, f.value]));
    assert.equal(byLabel.Seniority, "Senior");
    assert.equal(byLabel["Listing type"], "Promoted");
    assert.equal(byLabel["Company size"], "501-1000 employees");
    assert.equal(byLabel.Industry, "Software");
    assert.equal(byLabel.Source, "LinkedIn");
  });
});

describe("panelRailFacts", () => {
  const paid = {
    compensation_min: 145000,
    compensation_max: 180000,
    compensation_currency: "USD",
    compensation_interval: "yearly",
  };

  it("omits Pay from the rail when the header chip already shows it", () => {
    // The salary was printed twice on every card that has one: once as the
    // chip beside the title, once as a rail fact.
    const { known } = panelRailFacts(card({ ...paid, job_level: "Senior" }));
    assert.deepEqual(known.map((f) => f.label), ["Seniority", "Source"]);
  });

  it("still lists pay as missing when the posting has none", () => {
    // No chip is rendered in that case, so dropping it from both lists
    // would make the panel silently stop mentioning pay at all.
    const { known, missing } = panelRailFacts(card({ job_level: "Senior" }));
    assert.deepEqual(known.map((f) => f.label), ["Seniority", "Source"]);
    assert.ok(missing.includes("Pay"));
  });

  it("leaves every other fact alone", () => {
    const { known } = panelRailFacts(card({ ...paid, job_level: "Senior", company_industry: "Software" }));
    assert.deepEqual(known.map((f) => f.label), ["Seniority", "Industry", "Source"]);
  });
});

describe("the Pay fact's own value", () => {
  it("carries no money pictogram", () => {
    // `formatSalary` prefixes a 💰 ported from the v3 Qt widget; a row
    // labelled "Pay" does not need one.
    const facts = panelFactsGrid(
      card({
        compensation_min: 145000,
        compensation_max: 180000,
        compensation_currency: "USD",
        compensation_interval: "yearly",
      }),
    );
    const pay = facts.find((f) => f.label === "Pay");
    assert.ok(pay);
    assert.equal(pay.value, "$145k – $180k/yr");
  });
});

describe("tierSentence", () => {
  it("states preferred", () => {
    assert.match(tierSentence(card({ tier: "preferred" })), /preferred regions/);
  });

  it("states same-country", () => {
    assert.match(tierSentence(card({ tier: "country" })), /your country/);
  });

  it("states global remote", () => {
    assert.match(tierSentence(card({ tier: "global", tier_location_is_unresolved: false })), /global remote/);
  });

  it("states unresolved location ahead of the plain global sentence", () => {
    assert.match(tierSentence(card({ tier: "global", tier_location_is_unresolved: true })), /could not be matched/);
  });
});

describe("activityTrailLines", () => {
  it("states when a scan found it, for a New card", () => {
    const lines = activityTrailLines(card({ stage: "new", matched_at: isoDaysAgo(2) }), NOW);
    assert.equal(lines[0], "A scan found this 2d ago.");
  });

  it("states who moved it and when, for a non-New stage", () => {
    const lines = activityTrailLines(
      card({ stage: "shortlist", matched_at: isoDaysAgo(5), stage_entered_at: isoDaysAgo(1) }),
      NOW,
    );
    assert.ok(lines.includes("Moved to Shortlist 1 day ago."));
  });

  it("names the AI filter, not 'you', for an agent dismissal", () => {
    const lines = activityTrailLines(
      card({ stage: "out", removed_by: "agent", stage_entered_at: isoDaysAgo(3) }),
      NOW,
    );
    assert.ok(lines.includes("Dismissed by the AI filter 3 days ago."));
  });

  it("names 'you' for a user dismissal", () => {
    const lines = activityTrailLines(
      card({ stage: "out", removed_by: "user", stage_entered_at: isoDaysAgo(3) }),
      NOW,
    );
    assert.ok(lines.includes("Dismissed by you 3 days ago."));
  });

  it("never fabricates a per-move history beyond the current stage", () => {
    const lines = activityTrailLines(card({ stage: "new", matched_at: isoDaysAgo(2) }), NOW);
    assert.equal(lines.length, 2);
  });
});
