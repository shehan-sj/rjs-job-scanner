// Tests for the debounced-write queue T59's filter panel uses to satisfy
// "Debounce writes; never leave the visible list disagreeing with
// persisted state" and the T46-shaped DoD requirement: rapid consecutive
// changes land as *one* persisted state matching the last input, with no
// lost section.
//
// Uses an injected fake scheduler instead of real timers / node:test's
// mock.timers, so firing the debounce is a single deterministic call
// rather than a real-time wait -- same "no transpile step, no framework"
// convention as the rest of tests/.
import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { createDebouncedPatchQueue, type Scheduler } from "../src/filters/debouncedPatchQueue.ts";
import { mergeFilterPatch, type FilterPatch } from "../src/filters/filterPanelLogic.ts";

function fakeScheduler(): { scheduler: Scheduler; fire: () => void; scheduledCount: () => number } {
  let pending: (() => void) | null = null;
  let scheduledCount = 0;
  const scheduler: Scheduler = {
    schedule(fn, _delayMs) {
      scheduledCount += 1;
      pending = fn;
      return {};
    },
    cancel(_handle) {
      pending = null;
    },
  };
  return {
    scheduler,
    fire: () => {
      const fn = pending;
      pending = null;
      if (fn) fn();
    },
    scheduledCount: () => scheduledCount,
  };
}

describe("createDebouncedPatchQueue", () => {
  it("coalesces several rapid enqueues into exactly one send, matching the last input", async () => {
    const { scheduler, fire } = fakeScheduler();
    const sent: FilterPatch[] = [];
    const queue = createDebouncedPatchQueue<FilterPatch>({
      merge: mergeFilterPatch,
      isEmpty: (p) => Object.keys(p).length === 0,
      initial: {},
      delayMs: 400,
      scheduler,
      send: async (patch) => {
        sent.push(patch);
      },
    });

    queue.enqueue({ match_threshold: 60 });
    queue.enqueue({ match_threshold: 65 });
    queue.enqueue({ match_threshold: 70 }); // the "last input"
    queue.enqueue({ tag_visibility: { hide_internship: false } }); // a different section

    fire();
    await Promise.resolve();
    await Promise.resolve();

    assert.deepEqual(sent, [{ match_threshold: 70, tag_visibility: { hide_internship: false } }]);
  });

  it("resets the timer on every enqueue rather than firing once per call", () => {
    const { scheduler, scheduledCount, fire } = fakeScheduler();
    const sent: FilterPatch[] = [];
    const queue = createDebouncedPatchQueue<FilterPatch>({
      merge: mergeFilterPatch,
      isEmpty: (p) => Object.keys(p).length === 0,
      initial: {},
      delayMs: 400,
      scheduler,
      send: async (patch) => {
        sent.push(patch);
      },
    });

    queue.enqueue({ match_threshold: 60 });
    queue.enqueue({ match_threshold: 61 });
    queue.enqueue({ match_threshold: 62 });
    // Each enqueue reschedules; only the last-scheduled callback is still
    // pending (the fake scheduler models "cancel+reschedule" as simply
    // overwriting `pending`), so firing sends exactly once.
    fire();
    assert.equal(sent.length, 1);
    assert.equal(sent[0].match_threshold, 62);
    assert.ok(scheduledCount() >= 3);
  });

  it("on a failed write, surfaces the error and does not silently keep claiming the rejected patch as pending", async () => {
    const { scheduler, fire } = fakeScheduler();
    const errors: unknown[] = [];
    let attempts = 0;
    const queue = createDebouncedPatchQueue<FilterPatch>({
      merge: mergeFilterPatch,
      isEmpty: (p) => Object.keys(p).length === 0,
      initial: {},
      delayMs: 400,
      scheduler,
      send: async () => {
        attempts += 1;
        throw new Error("PUT /api/settings failed: 500");
      },
      onError: (err, _patch) => {
        errors.push(err);
      },
    });

    queue.enqueue({ match_threshold: 70 });
    fire();
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();

    assert.equal(attempts, 1);
    assert.equal(errors.length, 1);
    assert.match(String(errors[0]), /500/);
  });

  it("a later enqueue after a failed write starts a fresh patch rather than silently resending the failed one forever", async () => {
    const { scheduler, fire } = fakeScheduler();
    const sent: FilterPatch[] = [];
    let shouldFail = true;
    const queue = createDebouncedPatchQueue<FilterPatch>({
      merge: mergeFilterPatch,
      isEmpty: (p) => Object.keys(p).length === 0,
      initial: {},
      delayMs: 400,
      scheduler,
      send: async (patch) => {
        if (shouldFail) throw new Error("boom");
        sent.push(patch);
      },
      onError: () => {},
    });

    queue.enqueue({ match_threshold: 70 });
    fire();
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();

    shouldFail = false;
    queue.enqueue({ match_threshold: 80 });
    fire();
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();

    assert.deepEqual(sent, [{ match_threshold: 80 }]);
  });
});
