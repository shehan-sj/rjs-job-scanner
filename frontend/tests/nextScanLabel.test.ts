// Tests for the heartbeat strip's "next scan" line — the display half of
// the auto-scan fix. The owner's report was that the scan policy "doesn't
// seem to be firing ... more than one time"; part of why it looked that way
// is that the UI could not say whether a periodic cycle existed, only
// "Scheduler idle", which is what a dead schedule shows too.
import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { nextScanLabel } from "../src/components/nextScanLabel.ts";

const NOW = new Date("2026-10-23T12:00:00Z");

describe("nextScanLabel", () => {
  it("names the time when the cycle is armed", () => {
    const due = new Date("2026-10-23T12:20:00Z");
    const label = nextScanLabel(
      { schedulerAlive: true, scanCycleScheduled: true, nextScanAt: due.toISOString() },
      NOW,
    );
    assert.ok(label !== null);
    assert.ok(label.startsWith("Next scan: "));
    // Rendered in the viewer's locale/zone, so assert it names *that*
    // instant rather than a hard-coded wall-clock string.
    assert.equal(label, `Next scan: ${due.toLocaleTimeString()}`);
  });

  it("says so when automatic scanning is paused", () => {
    assert.equal(
      nextScanLabel({ schedulerAlive: true, scanCycleScheduled: false, nextScanAt: null }, NOW),
      "Automatic scanning is paused",
    );
  });

  it("stays silent when the scheduler is dead, rather than promising a scan", () => {
    // The status part already renders "Scheduler not responding"; a next-scan
    // promise beside it would contradict it.
    assert.equal(
      nextScanLabel({ schedulerAlive: false, scanCycleScheduled: true, nextScanAt: NOW.toISOString() }, NOW),
      null,
    );
  });

  it("still reports a cycle exists when the server gave no time", () => {
    assert.equal(
      nextScanLabel({ schedulerAlive: true, scanCycleScheduled: true, nextScanAt: null }, NOW),
      "Next scan: scheduled",
    );
  });

  it("does not render an unparseable timestamp", () => {
    assert.equal(
      nextScanLabel({ schedulerAlive: true, scanCycleScheduled: true, nextScanAt: "not-a-date" }, NOW),
      "Next scan: scheduled",
    );
  });

  it("reads an overdue fire time as due now, not as a stuck clock", () => {
    // What a wake-from-sleep looks like: the scheduler's infinite misfire
    // grace (jobfinder/scheduler.py) runs that scan late rather than
    // dropping it, so a past timestamp means imminent, not broken.
    assert.equal(
      nextScanLabel(
        { schedulerAlive: true, scanCycleScheduled: true, nextScanAt: "2026-10-23T11:13:00Z" },
        NOW,
      ),
      "Next scan: due now",
    );
  });
});
