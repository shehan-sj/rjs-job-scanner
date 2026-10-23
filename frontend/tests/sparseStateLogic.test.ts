// Tests for T65's sparse-states logic: docs/DESIGN.md's "nothing above
// the threshold" must say the scan worked, name the highest score it
// actually found, and offer the control that changes the outcome — not
// just a single generic "no matches" line regardless of what the scan
// actually did. Pure logic, no DOM, same pattern as jobSelection.test.ts.
import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { buildSparseMatchesCopy, type LastScanInfo } from "../src/components/sparseStateLogic.ts";

function scan(overrides: Partial<LastScanInfo> = {}): LastScanInfo {
  return { completed: true, jobsScanned: 0, bestScore: null, ...overrides };
}

describe("buildSparseMatchesCopy", () => {
  it("has no action when no scan has ever completed", () => {
    const copy = buildSparseMatchesCopy(scan({ completed: false }));
    assert.equal(copy.action, null);
  });

  it("says nothing was fetched when the scan found zero jobs", () => {
    const copy = buildSparseMatchesCopy(scan({ completed: true, jobsScanned: 0, bestScore: null }));
    assert.match(copy.title, /no new/i);
    assert.equal(copy.action, null);
  });

  it("names the job count and the highest score, and offers to lower the threshold to it", () => {
    const copy = buildSparseMatchesCopy(scan({ completed: true, jobsScanned: 36, bestScore: 62 }));
    assert.match(copy.title, /36/);
    assert.match(copy.title, /work/i);
    assert.match(copy.subtitle, /62/);
    assert.ok(copy.action);
    assert.equal(copy.action?.thresholdTarget, 62);
    assert.match(copy.action!.label, /62/);
  });

  it("singularizes the job count", () => {
    const copy = buildSparseMatchesCopy(scan({ completed: true, jobsScanned: 1, bestScore: 40 }));
    assert.match(copy.title, /1 job\b/);
    assert.doesNotMatch(copy.title, /1 jobs/);
  });

  it("has no threshold-lowering action when the scan scored nothing (not even a below-threshold candidate)", () => {
    const copy = buildSparseMatchesCopy(scan({ completed: true, jobsScanned: 5, bestScore: null }));
    assert.equal(copy.action, null);
    assert.match(copy.title, /5/);
  });
});
