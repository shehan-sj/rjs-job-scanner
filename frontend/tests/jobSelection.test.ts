// Tests for T64's split master-detail selection logic (docs/DESIGN.md's
// "Superseded 2026-10-22" block): which job is "current" in the 580px
// list, and how ArrowUp/ArrowDown move that selection — pure logic, no
// DOM, same pattern as jobBatching.test.ts / filterPanelLogic.test.ts.
import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  findSelectedCard,
  neighborJobId,
  resolveSelectedJobId,
  type SelectableCard,
} from "../src/components/jobSelection.ts";

function card(job_id: string): SelectableCard {
  return { job_id };
}

describe("resolveSelectedJobId", () => {
  it("keeps the current selection when it is still present in the list", () => {
    const cards = [card("a"), card("b"), card("c")];
    assert.equal(resolveSelectedJobId(cards, "b"), "b");
  });

  it("falls back to the first card when nothing is selected yet", () => {
    const cards = [card("a"), card("b")];
    assert.equal(resolveSelectedJobId(cards, null), "a");
  });

  it("falls back to the first card when the previously selected job scrolled out of the list (e.g. a filter change)", () => {
    const cards = [card("a"), card("b")];
    assert.equal(resolveSelectedJobId(cards, "gone"), "a");
  });

  it("returns null for an empty list", () => {
    assert.equal(resolveSelectedJobId([], "a"), null);
    assert.equal(resolveSelectedJobId([], null), null);
  });
});

describe("neighborJobId", () => {
  const cards = [card("a"), card("b"), card("c")];

  it("moves forward one row", () => {
    assert.equal(neighborJobId(cards, "a", 1), "b");
    assert.equal(neighborJobId(cards, "b", 1), "c");
  });

  it("moves backward one row", () => {
    assert.equal(neighborJobId(cards, "c", -1), "b");
    assert.equal(neighborJobId(cards, "b", -1), "a");
  });

  it("clamps at the last row rather than wrapping", () => {
    assert.equal(neighborJobId(cards, "c", 1), "c");
  });

  it("clamps at the first row rather than wrapping", () => {
    assert.equal(neighborJobId(cards, "a", -1), "a");
  });

  it("falls back to the first row when the current id is not found", () => {
    assert.equal(neighborJobId(cards, "missing", 1), "a");
  });

  it("returns null for an empty list", () => {
    assert.equal(neighborJobId([], "a", 1), null);
  });
});

describe("findSelectedCard", () => {
  it("returns the matching card", () => {
    const cards = [card("a"), card("b")];
    assert.deepEqual(findSelectedCard(cards, "b"), card("b"));
  });

  it("returns null when selectedId is null or not present", () => {
    const cards = [card("a")];
    assert.equal(findSelectedCard(cards, null), null);
    assert.equal(findSelectedCard(cards, "missing"), null);
  });
});
