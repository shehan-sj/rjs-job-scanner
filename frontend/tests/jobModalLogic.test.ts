// Tests for T100's job detail modal open-state logic — pure, no DOM,
// same pattern as jobSelection.test.ts/panelLogic.test.ts. Run with
// `npm run test:logic` (`node --test`).
import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { closeJob, openJob } from "../src/components/jobModalLogic.ts";

describe("openJob", () => {
  it("opening a job from nothing-open yields exactly one open id", () => {
    const open = openJob([], "job-a");
    assert.deepEqual(open, ["job-a"]);
  });

  it("opening B while A is open yields exactly one open job id, and it is B", () => {
    // Written so a stack-based implementation (one that *pushes* the
    // newly-opened id onto whatever list was already open, rather than
    // replacing it) fails this: pushing would leave ["job-a", "job-b"]
    // — two open ids — where this asserts there is exactly one, and it
    // is the second job, never the first.
    let open: readonly string[] = [];
    open = openJob(open, "job-a");
    open = openJob(open, "job-b");
    assert.equal(open.length, 1, "opening a second job must not grow the open set");
    assert.deepEqual(open, ["job-b"], "the second job replaces the first, not stacks on top of it");
  });

  it("opening the same job again while it is already open stays at one", () => {
    let open: readonly string[] = [];
    open = openJob(open, "job-a");
    open = openJob(open, "job-a");
    assert.deepEqual(open, ["job-a"]);
  });
});

describe("closeJob", () => {
  it("closing leaves nothing open", () => {
    assert.deepEqual(closeJob(["job-a"]), []);
  });

  it("closing when nothing was open stays empty", () => {
    assert.deepEqual(closeJob([]), []);
  });
});
