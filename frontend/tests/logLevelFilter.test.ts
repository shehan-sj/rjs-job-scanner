// Tests for the log drawer's per-level hide filter (T97).
//
// Run with `npm run test:logic` (`node --test`), same as
// control.test.ts/scanControlLogic.test.ts.
import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  ALL_LOG_LEVELS,
  DEFAULT_HIDDEN_LEVELS,
  filterLogLines,
  toggleHiddenLevel,
  type LevelTagged,
} from "../src/components/logLevelFilter.ts";

/** A seeded log of known composition — 2 DEBUG, 3 INFO, 2 WARNING,
 * 2 ERROR, 1 CRITICAL, in a deliberately interleaved order so "hide one
 * level" can't pass by accident (e.g. by dropping a contiguous slice). */
function seededLines(): LevelTagged[] {
  return [
    { level: "DEBUG" },
    { level: "INFO" },
    { level: "WARNING" },
    { level: "ERROR" },
    { level: "INFO" },
    { level: "CRITICAL" },
    { level: "DEBUG" },
    { level: "WARNING" },
    { level: "ERROR" },
    { level: "INFO" },
  ];
}

describe("filterLogLines", () => {
  it("hides nothing when hiddenLevels is empty", () => {
    const lines = seededLines();
    const { visible, hiddenCount } = filterLogLines(lines, []);
    assert.equal(hiddenCount, 0);
    assert.equal(visible.length, lines.length);
    assert.deepEqual(visible, lines);
  });

  for (const level of ALL_LOG_LEVELS) {
    it(`hiding ${level} removes exactly those lines and no others`, () => {
      const lines = seededLines();
      const expectedHiddenCount = lines.filter((l) => l.level === level).length;
      const { visible, hiddenCount } = filterLogLines(lines, [level]);

      assert.equal(hiddenCount, expectedHiddenCount);
      assert.equal(visible.length, lines.length - expectedHiddenCount);
      assert.ok(visible.every((l) => l.level !== level), `no visible line should have level ${level}`);
      // Every other level's lines must all still be present, untouched.
      for (const other of ALL_LOG_LEVELS) {
        if (other === level) continue;
        const expectedCount = lines.filter((l) => l.level === other).length;
        const actualCount = visible.filter((l) => l.level === other).length;
        assert.equal(actualCount, expectedCount, `${other} lines should be unaffected by hiding ${level}`);
      }
    });
  }

  it("hiding multiple levels removes the union of those levels", () => {
    const lines = seededLines();
    const { visible, hiddenCount } = filterLogLines(lines, ["WARNING", "DEBUG"]);
    const expected = lines.filter((l) => l.level !== "WARNING" && l.level !== "DEBUG");
    assert.equal(hiddenCount, lines.length - expected.length);
    assert.deepEqual(visible, expected);
  });

  it("hiding all five levels hides every line", () => {
    const lines = seededLines();
    const { visible, hiddenCount } = filterLogLines(lines, ALL_LOG_LEVELS);
    assert.equal(visible.length, 0);
    assert.equal(hiddenCount, lines.length);
  });

  it("fails open on a level outside the known taxonomy — stays visible", () => {
    const lines: LevelTagged[] = [{ level: "DEBUG" }, { level: "TRACE" }, { level: "ERROR" }];
    const { visible, hiddenCount } = filterLogLines(lines, ["TRACE", "DEBUG"]);
    assert.equal(hiddenCount, 1); // only DEBUG, a known level
    assert.equal(visible.length, 2);
    assert.ok(visible.some((l) => l.level === "TRACE"));
    assert.ok(visible.some((l) => l.level === "ERROR"));
  });

  it("accepts a Set the same way it accepts an array", () => {
    const lines = seededLines();
    const fromArray = filterLogLines(lines, ["ERROR"]);
    const fromSet = filterLogLines(lines, new Set(["ERROR"]));
    assert.deepEqual(fromArray, fromSet);
  });
});

// Written so a future change to the default silently re-introducing
// ERROR or CRITICAL into the hidden set fails this test, not just a
// human reviewer's attention: spec's one non-negotiable rule is that a
// fresh user must never miss a failure they didn't choose to hide.
describe("DEFAULT_HIDDEN_LEVELS", () => {
  it("excludes ERROR", () => {
    assert.ok(!DEFAULT_HIDDEN_LEVELS.includes("ERROR"));
  });

  it("excludes CRITICAL", () => {
    assert.ok(!DEFAULT_HIDDEN_LEVELS.includes("CRITICAL"));
  });

  it("hides nothing at all out of the box", () => {
    // The strongest form of the rule this task can ship: a fresh
    // install shows every level, full stop.
    assert.equal(DEFAULT_HIDDEN_LEVELS.length, 0);
  });
});

describe("toggleHiddenLevel", () => {
  it("adds a level not yet hidden", () => {
    assert.deepEqual(toggleHiddenLevel([], "WARNING"), ["WARNING"]);
  });

  it("removes a level already hidden", () => {
    assert.deepEqual(toggleHiddenLevel(["WARNING", "DEBUG"], "WARNING"), ["DEBUG"]);
  });

  it("does not mutate the array it was given", () => {
    const current = ["DEBUG"];
    toggleHiddenLevel(current, "INFO");
    assert.deepEqual(current, ["DEBUG"]);
  });

  it("can toggle ERROR/CRITICAL on deliberately — the default only forbids starting hidden", () => {
    assert.deepEqual(toggleHiddenLevel([], "ERROR"), ["ERROR"]);
    assert.deepEqual(toggleHiddenLevel([], "CRITICAL"), ["CRITICAL"]);
  });
});
