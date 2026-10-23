// Tests for T96(a)'s fix: the heartbeat strip's inline form and its
// popover form used to share one fragment built for the inline layout —
// `heartbeatParts.ts` module docstring. This pins the owner's bug as a
// test: given the same parts, the inline form joins them with a
// separator and the popover form never produces a line that is just the
// separator glyph.
import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  joinInline,
  stackWithoutSeparators,
  type HeartbeatPart,
} from "../src/components/heartbeatParts.ts";

const SEP = "·";

// Stand-in for the real HeartbeatStrip content — plain strings instead
// of ReactNode, since this module is generic over its content type and
// the behaviour under test (where separators land) does not depend on
// what the content actually is.
const parts: HeartbeatPart<string>[] = [
  { key: "status", content: "Scheduler idle", precededBySeparator: false },
  { key: "last-scan", content: "Last scan: No scan yet", precededBySeparator: true },
  { key: "note", content: "Scanning continues when you close this tab.", precededBySeparator: true },
  { key: "permission", content: "Notifications on", precededBySeparator: true },
  {
    key: "notification-note",
    content:
      "Notifications only fire while a JobFinder tab is open — closing every tab means no alert, even though scanning keeps running.",
    precededBySeparator: false,
  },
];

describe("joinInline (the inline strip)", () => {
  it("inserts the separator between parts that ask for one, and only those", () => {
    const joined = joinInline(parts, SEP).map((item) => item.content);
    assert.deepEqual(joined, [
      "Scheduler idle",
      SEP,
      "Last scan: No scan yet",
      SEP,
      "Scanning continues when you close this tab.",
      SEP,
      "Notifications on",
      "Notifications only fire while a JobFinder tab is open — closing every tab means no alert, even though scanning keeps running.",
    ]);
  });

  it("never draws a separator before the first part", () => {
    const joined = joinInline(parts, SEP).map((item) => item.content);
    assert.notEqual(joined[0], SEP);
  });

  it("produces one entry per part plus one per separator requested", () => {
    const joined = joinInline(parts, SEP);
    const separatorCount = parts.filter((p) => p.precededBySeparator).length;
    assert.equal(joined.length, parts.length + separatorCount);
  });
});

describe("stackWithoutSeparators (the popover)", () => {
  it("returns exactly the parts' own content, unchanged, in order", () => {
    const stacked = stackWithoutSeparators(parts).map((item) => item.content);
    assert.deepEqual(stacked, parts.map((p) => p.content));
  });

  it("produces exactly one entry per part, never more", () => {
    const stacked = stackWithoutSeparators(parts);
    assert.equal(stacked.length, parts.length);
  });

  it("the owner's bug, as a test: no line of the popover's content is just the separator glyph", () => {
    const stacked = stackWithoutSeparators(parts).map((item) => item.content);
    assert.ok(
      !stacked.some((line) => line === SEP),
      `popover produced a line that is just the separator: ${JSON.stringify(stacked)}`,
    );
  });

  it("holds even when every part asks to be preceded by a separator", () => {
    const allSeparated: HeartbeatPart<string>[] = parts.map((p) => ({ ...p, precededBySeparator: true }));
    const stacked = stackWithoutSeparators(allSeparated).map((item) => item.content);
    assert.ok(!stacked.some((line) => line === SEP));
    assert.equal(stacked.length, allSeparated.length);
  });
});
