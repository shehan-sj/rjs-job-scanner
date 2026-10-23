// Tests for the pure logic behind T58's batch grouping (DESIGN.md
// Variation B / the product spec: "Jobs are grouped by when they
// were found", newest batch first, ranking preserved within a batch).
//
// Run with `npm run test:logic` (`node --test`, which strips the types
// itself — no transpile step, no test framework added), same pattern as
// control.test.ts / fillerbotLogic.test.ts.
//
// `groupCardsByScan` is the client-side mirror of
// `jobfinder/storage/database.py`'s `group_matches_by_scan` (T57) — it
// must stay a mirror, not a reimplementation with different semantics,
// since the server is the one place "which jobs were found together"
// logic is allowed to live. `batchHeaderText` is new: it
// turns a batch into the literal header string DESIGN.md specifies,
// `Today 09:12 · 14 found · LinkedIn, Indeed`.
import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { batchHeaderText, groupCardsByScan, type BatchableCard } from "../src/components/jobBatching.ts";

function card(overrides: Partial<BatchableCard> & { id: string }): BatchableCard & { id: string } {
  return {
    scan_id: null,
    matched_at: "2026-10-22T09:12:00",
    source: "linkedin",
    ...overrides,
  };
}

describe("groupCardsByScan", () => {
  it("merges consecutive rows sharing a scan_id into one batch", () => {
    const rows = [card({ id: "a", scan_id: "s1" }), card({ id: "b", scan_id: "s1" })];
    const batches = groupCardsByScan(rows);
    assert.equal(batches.length, 1);
    assert.equal(batches[0].scanId, "s1");
    assert.deepEqual(batches[0].cards.map((c) => c.id), ["a", "b"]);
  });

  it("keeps batch order as first-appearance order, same as the Python original", () => {
    const rows = [
      card({ id: "a", scan_id: "s1" }),
      card({ id: "b", scan_id: "s2" }),
      card({ id: "c", scan_id: "s1" }),
    ];
    const batches = groupCardsByScan(rows);
    // "s1" appeared first, so its batch comes first even though "c"
    // (also s1) arrives after "b" (s2) in the input order.
    assert.deepEqual(batches.map((b) => b.scanId), ["s1", "s2"]);
    assert.deepEqual(batches[0].cards.map((c) => c.id), ["a", "c"]);
    assert.deepEqual(batches[1].cards.map((c) => c.id), ["b"]);
  });

  it("never merges null scan_id rows -- each is its own singleton batch", () => {
    const rows = [card({ id: "a", scan_id: null }), card({ id: "b", scan_id: null })];
    const batches = groupCardsByScan(rows);
    assert.equal(batches.length, 2);
    assert.deepEqual(batches[0].cards.map((c) => c.id), ["a"]);
    assert.deepEqual(batches[1].cards.map((c) => c.id), ["b"]);
  });

  it("returns no batches for an empty list", () => {
    assert.deepEqual(groupCardsByScan([]), []);
  });
});

describe("batchHeaderText", () => {
  const now = new Date("2026-10-22T20:00:00Z");

  it("renders the literal DESIGN.md example shape", () => {
    const rows = [
      card({ id: "a", scan_id: "s1", matched_at: "2026-10-22T09:12:00", source: "linkedin" }),
      card({ id: "b", scan_id: "s1", matched_at: "2026-10-22T09:12:03", source: "indeed" }),
    ];
    const [batch] = groupCardsByScan(rows);
    assert.equal(batchHeaderText(batch, now), "Today 09:12  ·  2 found  ·  LinkedIn, Indeed");
  });

  it("de-duplicates repeated sources without losing first-seen order", () => {
    const rows = [
      card({ id: "a", scan_id: "s1", source: "linkedin" }),
      card({ id: "b", scan_id: "s1", source: "linkedin" }),
      card({ id: "c", scan_id: "s1", source: "indeed" }),
    ];
    const [batch] = groupCardsByScan(rows);
    assert.match(batchHeaderText(batch, now), /LinkedIn, Indeed$/);
  });

  it("says Yesterday for a batch found on the previous calendar day", () => {
    const rows = [card({ id: "a", scan_id: "s1", matched_at: "2026-10-21T09:12:00" })];
    const [batch] = groupCardsByScan(rows);
    assert.match(batchHeaderText(batch, now), /^Yesterday 09:12/);
  });

  it("falls back to a month/day label for anything older than yesterday", () => {
    const rows = [card({ id: "a", scan_id: "s1", matched_at: "2026-10-18T09:12:00" })];
    const [batch] = groupCardsByScan(rows);
    assert.match(batchHeaderText(batch, now), /^Oct 18, 09:12/);
  });
});
