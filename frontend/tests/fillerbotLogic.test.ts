// Tests for the pure logic behind the FillerBot view (T31).
//
// Run with `npm run test:logic` (`node --test`, which strips the types
// itself — no transpile step and no test framework added). The React
// component is `[tdd:skip:ui-covered-by-session-tests]` per the T31 Plans
// row and is deliberately *not* tested here; what is tested is everything
// the component delegates rather than decides: the index mapping that
// keeps a card's key in the engine's single index space, the button state
// machine ported from `fillerbot_window.py`'s seven scattered handlers,
// the URL normalisation, and the post-fill summary.
import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  IDLE_CONTROLS,
  RUNNING_CONTROLS,
  buildActionCards,
  buildFillOverrides,
  confidenceTier,
  controlsOnAnalysis,
  controlsOnFillClicked,
  controlsOnFillComplete,
  controlsOnNeedsHuman,
  controlsOnNextClicked,
  controlsOnPaused,
  controlsOnRescanClicked,
  controlsOnResumed,
  fillFailureSummary,
  normalizeUrl,
  type FillActionDto,
} from "../src/api/fillerbotLogic.ts";

function action(overrides: Partial<FillActionDto>): FillActionDto {
  return {
    action_type: "fill",
    selector_strategy: "id",
    selector_value: "",
    value: "",
    field_label: "",
    confidence: 1,
    source_field: "",
    field_index: 0,
    ...overrides,
  };
}

describe("normalizeUrl", () => {
  it("prefixes a bare host with https:// (fillerbot_window.py:294)", () => {
    assert.equal(normalizeUrl("jobs.example.com/apply"), "https://jobs.example.com/apply");
  });

  it("leaves an explicit scheme alone, including http://", () => {
    assert.equal(normalizeUrl("https://a.example/x"), "https://a.example/x");
    assert.equal(normalizeUrl("http://a.example/x"), "http://a.example/x");
  });

  it("trims, and returns null for blank input so Go is a no-op", () => {
    assert.equal(normalizeUrl("  jobs.example.com  "), "https://jobs.example.com");
    assert.equal(normalizeUrl(""), null);
    assert.equal(normalizeUrl("   "), null);
  });
});

describe("buildActionCards — the single index space", () => {
  it("skips clicks for display but keys cards by the unfiltered index", () => {
    // The exact shape that used to break: a click ordered *before* a fill.
    // The fill is card 0 on screen and index 1 to the engine.
    const cards = buildActionCards([
      action({ action_type: "click", field_label: "Expand details" }),
      action({ action_type: "fill", field_label: "Full name", value: "Jane Doe" }),
    ]);

    assert.equal(cards.length, 1);
    assert.equal(cards[0].index, 1);
    assert.equal(cards[0].fieldLabel, "Full name");
  });

  it("keeps indices stable across several interleaved clicks", () => {
    const cards = buildActionCards([
      action({ action_type: "fill", field_label: "A" }),
      action({ action_type: "click", field_label: "ignored" }),
      action({ action_type: "select", field_label: "B" }),
      action({ action_type: "click", field_label: "also ignored" }),
      action({ action_type: "check", field_label: "C" }),
    ]);

    assert.deepEqual(
      cards.map((c) => [c.fieldLabel, c.index]),
      [
        ["A", 0],
        ["B", 2],
        ["C", 4],
      ],
    );
  });

  it("carries the source field and confidence through verbatim", () => {
    const cards = buildActionCards([
      action({ field_label: "Email", value: "j@e.com", source_field: "email", confidence: 0.4 }),
    ]);
    assert.deepEqual(cards, [
      { index: 0, fieldLabel: "Email", value: "j@e.com", sourceField: "email", confidence: 0.4 },
    ]);
  });

  it("returns nothing for an all-clicks analysis", () => {
    assert.deepEqual(buildActionCards([action({ action_type: "click" })]), []);
  });
});

describe("buildFillOverrides", () => {
  it("keys overrides by the engine index, not the card position", () => {
    const cards = buildActionCards([
      action({ action_type: "click" }),
      action({ field_label: "Full name", value: "Jane Doe" }),
    ]);

    assert.deepEqual(buildFillOverrides(cards, { 1: "Jane Q. Doe" }), { "1": "Jane Q. Doe" });
  });

  it("sends unedited values too, so a cleared field stays cleared", () => {
    const cards = buildActionCards([
      action({ field_label: "City", value: "Lisbon" }),
      action({ field_label: "Phone", value: "+351" }),
    ]);

    assert.deepEqual(buildFillOverrides(cards, { 1: "" }), { "0": "Lisbon", "1": "" });
  });

  it("ignores an edit for an index that is not on screen", () => {
    const cards = buildActionCards([action({ field_label: "City", value: "Lisbon" })]);
    assert.deepEqual(buildFillOverrides(cards, { 7: "nope" }), { "0": "Lisbon" });
  });
});

describe("confidenceTier (fillerbot_window.py:56-62)", () => {
  it("maps the four Qt thresholds, boundaries included", () => {
    assert.equal(confidenceTier(1), "strong");
    assert.equal(confidenceTier(0.8), "strong");
    assert.equal(confidenceTier(0.79), "good");
    assert.equal(confidenceTier(0.5), "good");
    assert.equal(confidenceTier(0.49), "fair");
    assert.equal(confidenceTier(0.3), "fair");
    assert.equal(confidenceTier(0.29), "weak");
    assert.equal(confidenceTier(0), "weak");
  });
});

describe("button state machine", () => {
  it("idle offers only Go (_set_buttons_idle)", () => {
    assert.deepEqual(IDLE_CONTROLS, {
      go: true,
      fill: false,
      rescan: false,
      next: false,
      stop: false,
      pause: false,
      resume: false,
      front: false,
    });
  });

  it("running takes Go away and offers Stop/Pause/front (_set_buttons_running)", () => {
    assert.equal(RUNNING_CONTROLS.go, false);
    assert.equal(RUNNING_CONTROLS.stop, true);
    assert.equal(RUNNING_CONTROLS.pause, true);
    assert.equal(RUNNING_CONTROLS.resume, false);
    assert.equal(RUNNING_CONTROLS.front, true);
    assert.equal(RUNNING_CONTROLS.fill, false);
    assert.equal(RUNNING_CONTROLS.rescan, false);
    assert.equal(RUNNING_CONTROLS.next, false);
  });

  it("an analysis with fields enables Fill and Re-scan", () => {
    const next = controlsOnAnalysis(RUNNING_CONTROLS, { cardCount: 2, nextStep: "fill" });
    assert.equal(next.fill, true);
    assert.equal(next.rescan, true);
    assert.equal(next.next, false, "Next Page is only for next_step === 'next_page'");
  });

  it("an analysis with no fields leaves Fill disabled but Re-scan live", () => {
    const next = controlsOnAnalysis(RUNNING_CONTROLS, { cardCount: 0, nextStep: "fill" });
    assert.equal(next.fill, false);
    assert.equal(next.rescan, true);
  });

  it("Next Page is enabled only when the analysis says next_page", () => {
    assert.equal(
      controlsOnAnalysis(RUNNING_CONTROLS, { cardCount: 1, nextStep: "next_page" }).next,
      true,
    );
    for (const nextStep of ["fill", "review", "submit", "done", "needs_human"]) {
      assert.equal(
        controlsOnAnalysis(RUNNING_CONTROLS, { cardCount: 1, nextStep }).next,
        false,
        nextStep,
      );
    }
  });

  it("an analysis never resurrects Go or Stop", () => {
    const next = controlsOnAnalysis(RUNNING_CONTROLS, { cardCount: 1, nextStep: "fill" });
    assert.equal(next.go, false);
    assert.equal(next.stop, true);
  });

  it("each click disables its own button so it cannot double-fire", () => {
    const live = controlsOnAnalysis(RUNNING_CONTROLS, { cardCount: 1, nextStep: "next_page" });
    assert.equal(controlsOnFillClicked(live).fill, false);
    assert.equal(controlsOnNextClicked(live).next, false);
    const rescanned = controlsOnRescanClicked(live);
    assert.equal(rescanned.rescan, false);
    assert.equal(rescanned.fill, false);
  });

  it("fill_complete disables Fill and re-enables Re-scan and Next (:411)", () => {
    // Qt re-enabled Next Page here unconditionally, unlike _on_analysis —
    // kept deliberately; see controlsOnFillComplete's docstring.
    const after = controlsOnFillComplete(
      controlsOnFillClicked(controlsOnAnalysis(RUNNING_CONTROLS, { cardCount: 1, nextStep: "fill" })),
    );
    assert.equal(after.fill, false);
    assert.equal(after.rescan, true);
    assert.equal(after.next, true);
    assert.equal(after.stop, true);
  });

  it("needs_human only re-enables Re-scan (:427)", () => {
    const after = controlsOnNeedsHuman(RUNNING_CONTROLS);
    assert.equal(after.rescan, true);
    assert.equal(after.fill, false);
    assert.equal(after.go, false);
  });

  it("pause/resume swap, and nothing else moves", () => {
    const paused = controlsOnPaused(RUNNING_CONTROLS);
    assert.equal(paused.pause, false);
    assert.equal(paused.resume, true);
    assert.equal(paused.stop, true);
    assert.equal(paused.front, true);

    const resumed = controlsOnResumed(paused);
    assert.equal(resumed.pause, true);
    assert.equal(resumed.resume, false);
    assert.deepEqual(resumed, RUNNING_CONTROLS);
  });
});

describe("fillFailureSummary (fillerbot_window.py:415-422)", () => {
  const cards = buildActionCards([
    action({ field_label: "A" }),
    action({ field_label: "B" }),
    action({ field_label: "C" }),
  ]);

  it("says nothing when every field landed", () => {
    assert.equal(fillFailureSummary(cards, { 0: true, 1: true, 2: true }), null);
  });

  it("counts only the explicit failures, keyed by engine index", () => {
    const summary = fillFailureSummary(cards, { 0: true, 1: false, 2: false });
    assert.equal(
      summary,
      "Done — 2 of 3 fields failed (highlighted in red). Fix manually or Re-scan.",
    );
  });

  it("treats an index with no mark yet as not-failed, not as a failure", () => {
    assert.equal(fillFailureSummary(cards, {}), null);
  });

  it("uses the singular for a one-field page", () => {
    const single = buildActionCards([action({ field_label: "Only" })]);
    assert.equal(
      fillFailureSummary(single, { 0: false }),
      "Done — 1 of 1 field failed (highlighted in red). Fix manually or Re-scan.",
    );
  });
});
