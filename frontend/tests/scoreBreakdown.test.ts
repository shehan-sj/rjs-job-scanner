// Tests for the job panel's score breakdown — the replacement for
// rendering `jobfinder/matcher/local_matcher.py`'s debug line at the user.
//
// The fixture string below is that line's real shape, copied from the
// owner's screenshot of the shipped panel. `tests/test_score_breakdown_weights.py`
// pins the Python side of this contract: it builds the string from the real
// matcher constants and fails if the format or the weights drift from what
// the parser here expects.
import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  SCORE_AXES,
  breakdownSentence,
  parseReasoningPoints,
  reasoningProse,
  scoreBreakdown,
} from "../src/components/scoreBreakdown.ts";

// Exactly what the owner's screenshot showed in "WHY THIS SCORED 60".
const REAL =
  "title:30  skills:21 (0 from LinkedIn + 7 from desc)  tools:9 (5)  seniority:0  function:0";

// Claude's matcher replaces `reasoning` with prose (claude_matcher.py).
const PROSE =
  "Strong overlap with your automation background, though the role leans more " +
  "toward performance testing than you have shown. No red flags.";

describe("parseReasoningPoints", () => {
  it("recovers every axis's points from the matcher's line", () => {
    const parsed = parseReasoningPoints(REAL);
    assert.ok(parsed !== null);
    assert.deepEqual(parsed.points, { title: 30, skills: 21, tools: 9, seniority: 0, function: 0 });
  });

  it("recovers the skills split and the tool count", () => {
    const parsed = parseReasoningPoints(REAL);
    assert.ok(parsed !== null);
    assert.equal(parsed.explicitSkillHits, 0);
    assert.equal(parsed.descSkillHits, 7);
    assert.equal(parsed.toolHits, 5);
  });

  it("returns null for prose rather than half-parsing it", () => {
    assert.equal(parseReasoningPoints(PROSE), null);
  });

  it("returns null when only some axes are present", () => {
    // A sentence that happens to contain "skills:" must not become a
    // breakdown with four empty bars.
    assert.equal(parseReasoningPoints("Matched on skills: automation, cypress."), null);
  });

  it("returns null for an empty string", () => {
    assert.equal(parseReasoningPoints(""), null);
  });
});

describe("scoreBreakdown", () => {
  it("renders the five axes with points out of their real maxima", () => {
    const axes = scoreBreakdown({ signals: {}, reasoning: REAL });
    assert.ok(axes !== null);
    assert.deepEqual(
      axes.map((a) => [a.label, a.points, a.max]),
      [
        ["Title", 30, 30],
        ["Skills", 21, 30],
        ["Tools", 9, 15],
        ["Seniority", 0, 15],
        ["Function", 0, 10],
      ],
    );
  });

  it("computes each meter's percentage against that axis's own max", () => {
    const axes = scoreBreakdown({ signals: {}, reasoning: REAL });
    assert.ok(axes !== null);
    // title is full marks; the old UI showed "title:30" and "tools:9"
    // with no way to tell that the first was 100% and the second 60%.
    assert.equal(axes[0].pct, 100);
    assert.equal(axes[2].pct, 60);
  });

  it("prefers populated signals over the reasoning string", () => {
    // signals are 0-100 per axis; 50 of title's 30 points is 15.
    const axes = scoreBreakdown({ signals: { title: 50 }, reasoning: REAL });
    assert.ok(axes !== null);
    assert.equal(axes[0].points, 15);
  });

  it("falls back to the parsed line for axes signals does not carry", () => {
    const axes = scoreBreakdown({ signals: { title: 50 }, reasoning: REAL });
    assert.ok(axes !== null);
    assert.equal(axes[1].points, 21);
  });

  it("is null when there is neither signals nor a parseable line", () => {
    assert.equal(scoreBreakdown({ signals: {}, reasoning: PROSE }), null);
    assert.equal(scoreBreakdown({ signals: {}, reasoning: "" }), null);
  });

  it("names where the skill hits came from, omitting a zero side", () => {
    const axes = scoreBreakdown({ signals: {}, reasoning: REAL });
    assert.ok(axes !== null);
    // "0 from LinkedIn" is dropped: on a source with no structured skills
    // list that zero is not a failure, it is an absent feature.
    assert.equal(axes[1].detail, "7 from the description");
  });

  it("reports both sides when both are non-zero", () => {
    const axes = scoreBreakdown({
      signals: {},
      reasoning: "title:10  skills:25 (4 from LinkedIn + 3 from desc)  tools:5 (1)  seniority:15  function:10",
    });
    assert.ok(axes !== null);
    assert.equal(axes[1].detail, "4 from the listed skills · 3 from the description");
    assert.equal(axes[2].detail, "1 tool matched");
  });

  it("clamps a signal above 100 to the axis maximum", () => {
    const axes = scoreBreakdown({ signals: { title: 150 }, reasoning: REAL });
    assert.ok(axes !== null);
    assert.equal(axes[0].points, 30);
  });

  it("the axis maxima sum to 100, like the score they add up to", () => {
    assert.equal(SCORE_AXES.reduce((sum, a) => sum + a.max, 0), 100);
  });
});

describe("reasoningProse", () => {
  it("shows Claude's explanation as prose", () => {
    assert.equal(reasoningProse({ reasoning: PROSE }), PROSE);
  });

  it("never also prints the debug line that became the breakdown", () => {
    assert.equal(reasoningProse({ reasoning: REAL }), null);
  });

  it("is null when there is no reasoning at all", () => {
    assert.equal(reasoningProse({ reasoning: "   " }), null);
  });
});

describe("breakdownSentence", () => {
  it("leads with what carried the score, then names the gaps", () => {
    const axes = scoreBreakdown({ signals: {}, reasoning: REAL });
    assert.ok(axes !== null);
    assert.equal(breakdownSentence(axes), "Full marks on title; no seniority or function signal.");
  });

  it("never states a total, which could contradict the headline score", () => {
    // `score_job` rounds the SUM of five float axes; the reasoning line
    // prints each one int()-truncated. Summing the parsed axes therefore
    // lands under the real score -- the shipped panel read "61 of 100
    // points" under a heading saying "Why this scored 62". No total, no
    // contradiction.
    const axes = scoreBreakdown({ signals: {}, reasoning: REAL });
    assert.ok(axes !== null);
    const sentence = breakdownSentence(axes);
    assert.ok(!/\d+\s*of\s*\d+/.test(sentence), `sentence restates a total: ${sentence}`);
    assert.ok(!/\bpoints\b/.test(sentence), `sentence restates a points total: ${sentence}`);
  });

  it("says 'strongest on' when the best axis is not full marks", () => {
    const axes = scoreBreakdown({
      signals: {},
      reasoning: "title:20  skills:24 (0 from LinkedIn + 8 from desc)  tools:7 (4)  seniority:5  function:3",
    });
    assert.ok(axes !== null);
    // skills 24/30 = 80%, title 20/30 = 67% -> skills leads, nothing is zero.
    assert.equal(breakdownSentence(axes), "Strongest on skills.");
  });

  it("joins three missing axes with commas and a final 'or'", () => {
    const axes = scoreBreakdown({
      signals: {},
      reasoning: "title:30  skills:0 (0 from LinkedIn + 0 from desc)  tools:0 (0)  seniority:0  function:4",
    });
    assert.ok(axes !== null);
    assert.equal(breakdownSentence(axes), "Full marks on title; no skills, tools or seniority signal.");
  });

  it("says so plainly when nothing scored", () => {
    const axes = scoreBreakdown({
      signals: {},
      reasoning: "title:0  skills:0 (0 from LinkedIn + 0 from desc)  tools:0 (0)  seniority:0  function:0",
    });
    assert.ok(axes !== null);
    assert.match(breakdownSentence(axes), /Nothing scored/);
  });
});
