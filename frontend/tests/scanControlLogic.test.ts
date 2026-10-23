// Tests for T95's scan control mapping — pure logic, no DOM, same pattern
// as boardQuietDayLogic.test.ts. Every one of the eight
// (running, scanCycleScheduled, schedulerAlive) combinations is asserted,
// including the "impossible-looking" ones: a scheduler reported dead
// while `running`/`scan_cycle_scheduled` still read true (the engine died
// mid-scan, or the snapshot is stale by a beat), and `running` while the
// cycle is paused (an immediate scan fired after pausing — T95's brief
// calls this out by name as real, not a bug).
import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { scanControlFor, type ScanControlInput } from "../src/components/scanControlLogic.ts";

function input(overrides: Partial<ScanControlInput> = {}): ScanControlInput {
  return {
    running: false,
    scanCycleScheduled: true,
    schedulerAlive: true,
    ...overrides,
  };
}

describe("scanControlFor", () => {
  it("idle, automatic scanning on: offers Scan now and a pause", () => {
    const view = scanControlFor(input({ running: false, scanCycleScheduled: true, schedulerAlive: true }));
    assert.equal(view.primaryLabel, "Scan now");
    assert.equal(view.primaryAction, "scan");
    assert.equal(view.primaryDisabled, false);
    assert.equal(view.secondaryLabel, "Pause automatic scanning");
    assert.equal(view.secondaryAction, "pause");
  });

  it("idle, automatic scanning paused: Scan now still works, offers resume", () => {
    const view = scanControlFor(input({ running: false, scanCycleScheduled: false, schedulerAlive: true }));
    assert.equal(view.primaryLabel, "Scan now");
    assert.equal(view.primaryAction, "scan");
    assert.equal(view.primaryDisabled, false);
    assert.equal(view.secondaryLabel, "Resume automatic scanning");
    assert.equal(view.secondaryAction, "play");
  });

  it("running, automatic scanning on: says so, offers pause (not stop)", () => {
    const view = scanControlFor(input({ running: true, scanCycleScheduled: true, schedulerAlive: true }));
    assert.equal(view.primaryLabel, "Scanning now…");
    assert.equal(view.primaryAction, "none");
    assert.equal(view.primaryDisabled, true);
    assert.equal(view.secondaryLabel, "Pause automatic scanning");
    assert.equal(view.secondaryAction, "pause");
  });

  it("running, automatic scanning already paused: an immediate scan fired after pausing is real", () => {
    const view = scanControlFor(input({ running: true, scanCycleScheduled: false, schedulerAlive: true }));
    assert.equal(view.primaryLabel, "Scanning now…");
    assert.equal(view.primaryAction, "none");
    assert.equal(view.primaryDisabled, true);
    assert.equal(view.secondaryLabel, "Resume automatic scanning");
    assert.equal(view.secondaryAction, "play");
  });

  it("scheduler dead, idle, scheduled: surfaced distinctly from paused", () => {
    const view = scanControlFor(input({ running: false, scanCycleScheduled: true, schedulerAlive: false }));
    assert.equal(view.primaryLabel, "Scheduler not responding");
    assert.equal(view.primaryAction, "none");
    assert.equal(view.primaryDisabled, true);
    assert.equal(view.secondaryLabel, null);
    assert.equal(view.secondaryAction, "none");
  });

  it("scheduler dead, idle, not scheduled: still 'not responding', not 'paused'", () => {
    const view = scanControlFor(input({ running: false, scanCycleScheduled: false, schedulerAlive: false }));
    assert.equal(view.primaryLabel, "Scheduler not responding");
    assert.equal(view.secondaryLabel, null);
  });

  it("scheduler dead, running reported true, scheduled: dead status still wins", () => {
    const view = scanControlFor(input({ running: true, scanCycleScheduled: true, schedulerAlive: false }));
    assert.equal(view.primaryLabel, "Scheduler not responding");
    assert.equal(view.primaryAction, "none");
    assert.equal(view.secondaryLabel, null);
  });

  it("scheduler dead, running reported true, not scheduled: dead status still wins", () => {
    const view = scanControlFor(input({ running: true, scanCycleScheduled: false, schedulerAlive: false }));
    assert.equal(view.primaryLabel, "Scheduler not responding");
    assert.equal(view.primaryAction, "none");
    assert.equal(view.secondaryLabel, null);
  });

  it("no label ever contains 'stop' while running is true, across every schedulerAlive/scanCycleScheduled pairing", () => {
    for (const schedulerAlive of [true, false]) {
      for (const scanCycleScheduled of [true, false]) {
        const view = scanControlFor(input({ running: true, scanCycleScheduled, schedulerAlive }));
        assert.doesNotMatch(view.primaryLabel.toLowerCase(), /stop/);
        if (view.secondaryLabel) {
          assert.doesNotMatch(view.secondaryLabel.toLowerCase(), /stop/);
        }
      }
    }
  });

  it("no label ever contains 'stop' at all, across every combination", () => {
    for (const running of [true, false]) {
      for (const scanCycleScheduled of [true, false]) {
        for (const schedulerAlive of [true, false]) {
          const view = scanControlFor(input({ running, scanCycleScheduled, schedulerAlive }));
          assert.doesNotMatch(view.primaryLabel.toLowerCase(), /stop/);
          if (view.secondaryLabel) {
            assert.doesNotMatch(view.secondaryLabel.toLowerCase(), /stop/);
          }
        }
      }
    }
  });
});
