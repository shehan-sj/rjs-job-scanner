// Tests for T104's board column info open-state logic — pure, no DOM,
// same pattern as jobModalLogic.test.ts. Run with `npm run test:logic`
// (`node --test`).
import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { closeColumnInfo, openColumnInfo } from "../src/components/boardColumnInfoLogic.ts";
import type { Stage } from "../src/types/job.ts";

describe("openColumnInfo", () => {
  it("opening a column's info from nothing-open yields exactly that column open", () => {
    const open = openColumnInfo([], "new");
    assert.deepEqual(open, ["new"]);
  });

  it("opening B's info while A's is open leaves exactly one open, and it is B", () => {
    // Written so a boolean-per-column implementation (five independent
    // `useState<boolean>`s) fails this: that shape has no single value
    // to assert "and only B" against — it would need five separate
    // assertions, and nothing in its shape prevents both A's and B's
    // booleans being true at once. This list's length is the assertion
    // that shape cannot make.
    let open: readonly Stage[] = [];
    open = openColumnInfo(open, "new");
    open = openColumnInfo(open, "shortlist");
    assert.equal(open.length, 1, "opening a second column's info must not grow the open set");
    assert.deepEqual(open, ["shortlist"], "the second column's info replaces the first, not stacks on top of it");
  });

  it("opening the same column's info again while it is already open stays at one", () => {
    let open: readonly Stage[] = [];
    open = openColumnInfo(open, "applied");
    open = openColumnInfo(open, "applied");
    assert.deepEqual(open, ["applied"]);
  });
});

describe("closeColumnInfo", () => {
  it("closing leaves nothing open", () => {
    assert.deepEqual(closeColumnInfo(["replied"]), []);
  });

  it("closing when nothing was open stays empty", () => {
    assert.deepEqual(closeColumnInfo([]), []);
  });
});
