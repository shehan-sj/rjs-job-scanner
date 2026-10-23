// Tests for T85's board quiet-day/first-run copy. Pure logic, no DOM —
// same pattern as sparseStateLogic.test.ts, which this module's
// `buildQuietDayCopy` extends to the board's New column.
import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  buildQuietDayCopy,
  FIRST_RUN_COLUMN_COPY,
  isFirstRunBoard,
  type QuietDayScanInfo,
} from "../src/components/boardQuietDayLogic.ts";
import { STAGES, type Stage } from "../src/types/job.ts";

function scan(overrides: Partial<QuietDayScanInfo> = {}): QuietDayScanInfo {
  return {
    completed: true,
    lastScanCompletedAt: "2026-10-22T15:26:00",
    postingsRead: 36,
    bestScore: 57,
    matchThreshold: 70,
    checkIntervalMinutes: 180,
    scanScheduled: true,
    ...overrides,
  };
}

describe("buildQuietDayCopy", () => {
  it("always titles the state 'You are caught up'", () => {
    assert.equal(buildQuietDayCopy(scan()).title, "You are caught up");
  });

  it("names the clock time, postings read, the bar and the best score when every field exists", () => {
    const copy = buildQuietDayCopy(scan());
    assert.match(copy.scanSummary ?? "", /15:26/);
    assert.match(copy.scanSummary ?? "", /36 postings/);
    assert.match(copy.scanSummary ?? "", /70% or better/);
    assert.match(copy.scanSummary ?? "", /57%/);
  });

  it("has no scan summary when no scan has ever completed", () => {
    const copy = buildQuietDayCopy(scan({ completed: false }));
    assert.equal(copy.scanSummary, null);
  });

  it("says nothing new was read when the last scan found zero postings, without naming a score", () => {
    const copy = buildQuietDayCopy(scan({ postingsRead: 0, bestScore: null }));
    assert.match(copy.scanSummary ?? "", /nothing new/i);
    assert.doesNotMatch(copy.scanSummary ?? "", /%/);
  });

  it("drops the bar clause when the current threshold is unknown, but keeps the best score", () => {
    const copy = buildQuietDayCopy(scan({ matchThreshold: null }));
    assert.doesNotMatch(copy.scanSummary ?? "", /or better/);
    assert.match(copy.scanSummary ?? "", /57%/);
  });

  it("says nothing could be scored, and offers no action, when nothing was scored at all", () => {
    const copy = buildQuietDayCopy(scan({ bestScore: null }));
    assert.match(copy.scanSummary ?? "", /none.*could be scored/i);
    assert.deepEqual(copy.actions, []);
  });

  it("offers to lower the bar to the best score, and to drop it to zero", () => {
    const copy = buildQuietDayCopy(scan());
    assert.equal(copy.actions.length, 2);
    assert.equal(copy.actions[0].matchThreshold, 57);
    assert.match(copy.actions[0].label, /57%/);
    assert.equal(copy.actions[1].matchThreshold, 0);
    // Both buttons write the threshold; neither is a preview, because no
    // server-side preview exists. So the label has to name the write —
    // "show the ones under the bar" read as a view and was not one.
    assert.match(copy.actions[1].label, /\b0\b/);
    assert.doesNotMatch(copy.actions[1].label, /^show /i);
  });

  it("says when the next scan runs from the last completion time and the interval", () => {
    const copy = buildQuietDayCopy(scan());
    assert.match(copy.nextScanText, /18:26/);
  });

  it("says scanning is paused instead of a next-run time when the scan cycle isn't scheduled", () => {
    const copy = buildQuietDayCopy(scan({ scanScheduled: false }));
    assert.match(copy.nextScanText, /paused/i);
  });

  it("gives no next-scan time when the interval is unknown", () => {
    const copy = buildQuietDayCopy(scan({ checkIntervalMinutes: null }));
    assert.equal(copy.nextScanText, "");
  });
});

describe("FIRST_RUN_COLUMN_COPY", () => {
  it("carries the verbatim copy for every one of the five stages", () => {
    for (const stage of STAGES) {
      assert.ok(FIRST_RUN_COLUMN_COPY[stage].title.length > 0);
      assert.ok(FIRST_RUN_COLUMN_COPY[stage].subtitle.length > 0);
    }
  });

  it("only New carries the scan CTA", () => {
    for (const stage of STAGES) {
      assert.equal(FIRST_RUN_COLUMN_COPY[stage].scanCta, stage === "new");
    }
  });

  it("matches the brief's literal wording for New", () => {
    assert.equal(FIRST_RUN_COLUMN_COPY.new.title, "Nothing scanned yet");
    assert.match(FIRST_RUN_COLUMN_COPY.new.subtitle, /every three hours/);
  });
});

describe("isFirstRunBoard", () => {
  function counts(overrides: Partial<Record<Stage, number>> = {}): Record<Stage, number> {
    const base: Record<Stage, number> = { new: 0, shortlist: 0, applied: 0, replied: 0, out: 0 };
    return { ...base, ...overrides };
  }

  it("is true when every column is empty", () => {
    assert.equal(isFirstRunBoard(counts()), true);
  });

  it("is false when even one column holds a card", () => {
    assert.equal(isFirstRunBoard(counts({ applied: 1 })), false);
  });
});
