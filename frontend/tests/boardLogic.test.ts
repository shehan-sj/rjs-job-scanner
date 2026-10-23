// Tests for T81's board pure logic (column sort order, tier filter,
// keyboard movement). Run with `npm run test:logic`.
import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  BOARD_COLUMNS,
  cardPassesTierFilter,
  daysAgoLabel,
  dropSlotLabel,
  floatStarredToTop,
  footLineForCard,
  moveCardAction,
  moveToAdjacentColumn,
  moveWithinColumn,
  sameCountryTierIsConfigured,
  sortAppliedColumn,
  sortByStageEnteredDesc,
  sortColumn,
  STAGE_BY_DIGIT,
  undoToastLabel,
} from "../src/components/boardLogic.ts";
import type { BoardCard } from "../src/types/job.ts";

function card(id: string, overrides: Partial<BoardCard> = {}): BoardCard {
  return {
    job_id: id,
    title: "QA Engineer",
    company: "Globex",
    location: "Remote",
    score: 80,
    source: "linkedin",
    url: "",
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
    stage: "applied",
    stage_entered_at: null,
    removed_by: null,
    reply_note: null,
    tier: "global",
    tier_location_is_unresolved: false,
    same_country_tier_configured: true,
    starred: false,
    ...overrides,
  };
}

describe("BOARD_COLUMNS / STAGE_BY_DIGIT", () => {
  it("has the five stages in the product spec order", () => {
    assert.deepEqual(
      BOARD_COLUMNS.map((c) => c.stage),
      ["new", "shortlist", "applied", "replied", "out"],
    );
  });

  it("maps keyboard 1-5 onto the five stages in order", () => {
    assert.deepEqual(STAGE_BY_DIGIT, {
      "1": "new",
      "2": "shortlist",
      "3": "applied",
      "4": "replied",
      "5": "out",
    });
  });
});

describe("moveCardAction — one reducer, three callers (T82)", () => {
  it("a drag to Applied, pressing '3', and the panel's Applied stop all produce the identical action", () => {
    const viaDrag = moveCardAction("job-x", "applied");
    const viaKeyboard = moveCardAction("job-x", STAGE_BY_DIGIT["3"]);
    const viaPanel = moveCardAction("job-x", BOARD_COLUMNS[2].stage);
    assert.deepEqual(viaDrag, { type: "board/moveCard", jobId: "job-x", toStage: "applied" });
    assert.deepEqual(viaDrag, viaKeyboard);
    assert.deepEqual(viaDrag, viaPanel);
  });

  it("different jobs or stages are not conflated into the same action", () => {
    assert.notDeepEqual(moveCardAction("job-x", "applied"), moveCardAction("job-y", "applied"));
    assert.notDeepEqual(moveCardAction("job-x", "applied"), moveCardAction("job-x", "shortlist"));
  });
});

describe("dropSlotLabel — the mid-drag drop slot (T82)", () => {
  it("names the target stage, the job's company and its score", () => {
    const text = dropSlotLabel("shortlist", card("a", { company: "Coursera", score: 92 }));
    assert.equal(text, "Drop here to shortlist · Coursera · fit 92");
  });
});

describe("undoToastLabel — the ten-second undo toast (T82)", () => {
  it("names what moved and where", () => {
    assert.equal(undoToastLabel("Test Automation Lead", "shortlist"), "Moved Test Automation Lead to Shortlist");
  });
});

describe("sortAppliedColumn — oldest at the bottom", () => {
  it("sorts most-recently-applied first, longest-waiting last", () => {
    const a = card("a", { stage_entered_at: "2026-10-20T00:00:00" });
    const b = card("b", { stage_entered_at: "2026-10-22T00:00:00" });
    const c = card("c", { stage_entered_at: "2026-10-15T00:00:00" });
    const sorted = sortAppliedColumn([a, b, c]);
    assert.deepEqual(sorted.map((x) => x.job_id), ["b", "a", "c"]);
  });
});

describe("floatStarredToTop / sortColumn — T101 starring", () => {
  it("floats starred cards above unstarred ones, leaving each group's relative order unchanged", () => {
    const a = card("a");
    const b = card("b", { starred: true });
    const c = card("c");
    const d = card("d", { starred: true });
    const sorted = floatStarredToTop([a, b, c, d]);
    // Starred (b, d) first in their original relative order, then
    // unstarred (a, c) in their original relative order -- asserted as
    // the whole list, not just that the first element is starred.
    assert.deepEqual(sorted.map((x) => x.job_id), ["b", "d", "a", "c"]);
  });

  it("an all-unstarred column is left in its original order", () => {
    const cards = [card("a"), card("b"), card("c")];
    assert.deepEqual(floatStarredToTop(cards).map((x) => x.job_id), ["a", "b", "c"]);
  });

  it("an all-starred column is left in its original order", () => {
    const cards = [card("a", { starred: true }), card("b", { starred: true })];
    assert.deepEqual(floatStarredToTop(cards).map((x) => x.job_id), ["a", "b"]);
  });

  it("sortColumn stars-first, leaving the unstarred tail in the same order the stage sort already produced (Applied: oldest-last)", () => {
    const fresh = card("fresh", { stage: "applied", stage_entered_at: "2026-10-22T00:00:00" });
    const starredOld = card("starred-old", { stage: "applied", stage_entered_at: "2026-10-10T00:00:00", starred: true });
    const middling = card("middling", { stage: "applied", stage_entered_at: "2026-10-18T00:00:00" });
    const oldest = card("oldest", { stage: "applied", stage_entered_at: "2026-10-05T00:00:00" });

    // sortAppliedColumn alone (no starring) would order these
    // fresh, middling, oldest (most-recent-first, oldest-last).
    const unstarredOnly = sortAppliedColumn([fresh, middling, oldest]);
    assert.deepEqual(unstarredOnly.map((x) => x.job_id), ["fresh", "middling", "oldest"]);

    const sorted = sortColumn("applied", [fresh, starredOld, middling, oldest]);
    // Starred card first despite being the oldest by stage_entered_at;
    // the unstarred tail keeps exactly the order it already had.
    assert.deepEqual(sorted.map((x) => x.job_id), ["starred-old", "fresh", "middling", "oldest"]);
  });

  it("sortColumn stars-first for a non-applied, non-new column too (sortByStageEnteredDesc)", () => {
    const newest = card("newest", { stage: "shortlist", stage_entered_at: "2026-10-22T00:00:00" });
    const middle = card("middle", { stage: "shortlist", stage_entered_at: "2026-10-18T00:00:00" });
    const starredOldest = card("starred-oldest", {
      stage: "shortlist",
      stage_entered_at: "2026-10-05T00:00:00",
      starred: true,
    });

    const unstarredOnly = sortByStageEnteredDesc([newest, middle]);
    assert.deepEqual(unstarredOnly.map((x) => x.job_id), ["newest", "middle"]);

    const sorted = sortColumn("shortlist", [newest, middle, starredOldest]);
    assert.deepEqual(sorted.map((x) => x.job_id), ["starred-oldest", "newest", "middle"]);
  });
});

describe("cardPassesTierFilter", () => {
  it("'all' always passes", () => {
    assert.equal(cardPassesTierFilter(card("a", { tier: "preferred" }), "all"), true);
  });

  it("filters to the matching tier only", () => {
    assert.equal(cardPassesTierFilter(card("a", { tier: "preferred" }), "preferred"), true);
    assert.equal(cardPassesTierFilter(card("a", { tier: "preferred" }), "country"), false);
  });
});

describe("sameCountryTierIsConfigured", () => {
  it("defaults to configured on an empty board", () => {
    assert.equal(sameCountryTierIsConfigured([]), true);
  });

  it("reads the flag off the first card", () => {
    assert.equal(sameCountryTierIsConfigured([card("a", { same_country_tier_configured: false })]), false);
  });
});

describe("moveWithinColumn", () => {
  const cards = [card("a"), card("b"), card("c")];

  it("moves down one row", () => {
    assert.equal(moveWithinColumn(cards, "a", 1), "b");
  });

  it("clamps at the bottom", () => {
    assert.equal(moveWithinColumn(cards, "c", 1), "c");
  });

  it("clamps at the top", () => {
    assert.equal(moveWithinColumn(cards, "a", -1), "a");
  });
});

describe("moveToAdjacentColumn", () => {
  it("clamps at either end rather than wrapping", () => {
    assert.equal(moveToAdjacentColumn(0, -1), 0);
    assert.equal(moveToAdjacentColumn(BOARD_COLUMNS.length - 1, 1), BOARD_COLUMNS.length - 1);
    assert.equal(moveToAdjacentColumn(1, 1), 2);
  });
});

const NOW = new Date("2026-10-22T00:00:00.000Z");

describe("daysAgoLabel", () => {
  it("'today' for same-day", () => {
    assert.equal(daysAgoLabel("2026-10-22T00:00:00", NOW), "today");
  });
  it("'1 day ago' at the boundary", () => {
    assert.equal(daysAgoLabel("2026-10-21T00:00:00", NOW), "1 day ago");
  });
  it("'N days ago' for the literal brief wording", () => {
    assert.equal(daysAgoLabel("2026-10-13T00:00:00", NOW), "9 days ago");
    assert.equal(daysAgoLabel("2026-10-20T00:00:00", NOW), "2 days ago");
  });
});

describe("footLineForCard — stage-dependent text", () => {
  it("Shortlist: 'kept 2 days ago'", () => {
    const text = footLineForCard(card("a", { stage: "shortlist", stage_entered_at: "2026-10-20T00:00:00" }), NOW);
    assert.equal(text, "Kept 2 days ago");
  });

  it("Applied: 'applied 9 days ago'", () => {
    const text = footLineForCard(card("a", { stage: "applied", stage_entered_at: "2026-10-13T00:00:00" }), NOW);
    assert.equal(text, "Applied 9 days ago");
  });

  it("Replied: the reply date", () => {
    const text = footLineForCard(card("a", { stage: "replied", stage_entered_at: "2026-10-15T00:00:00" }), NOW);
    assert.equal(text, "Replied Oct 15");
  });

  it("Not for me: who dismissed it", () => {
    assert.equal(footLineForCard(card("a", { stage: "out", removed_by: "agent" }), NOW), "Dismissed by the AI filter");
    assert.equal(footLineForCard(card("a", { stage: "out", removed_by: "user" }), NOW), "Dismissed by you");
  });

  it("New: age and pay", () => {
    const text = footLineForCard(
      card("a", {
        stage: "new",
        matched_at: "2026-10-21T22:00:00",
        compensation_min: 100000,
        compensation_max: 130000,
        compensation_currency: "USD",
        compensation_interval: "yearly",
      }),
      NOW,
    );
    assert.ok(text.includes("ago"), text);
    assert.ok(text.includes("$100k"), text);
  });
});
