// Tests for the navigation-rail logic (docs/DESIGN.md principle 4 /
// The product spec: "one navigation model").
//
// T81 — Matches/Applied/Removed collapsed into one "Board" destination
// this file's expectations moved with it. Run with
// `npm run test:logic` (`node --test`, same pattern as
// jobBatching.test.ts / filterPanelLogic.test.ts — no transpile step).
//
// Pure logic only: which destinations exist, in what order, with what
// counts, and how the phone breakpoint maps the rail destinations onto
// the bottom tab bar DESIGN.md's "Phone" section describes. The rail
// markup itself (components/Rail.tsx) is a thin, untested-here render of
// this data.
import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  MOBILE_BREAKPOINT_PX,
  MOBILE_TAB_ITEMS,
  RAIL_DESTINATIONS,
  buildRailItems,
  isDestinationInMoreSheet,
  isMobileViewport,
  mobileDestinationForTab,
  type RailCounts,
} from "../src/components/railLogic.ts";

describe("RAIL_DESTINATIONS", () => {
  it("has exactly the three destinations left after T81's board collapse, in order", () => {
    assert.deepEqual(
      RAIL_DESTINATIONS.map((d) => d.key),
      ["board", "resume", "sources"],
    );
    assert.deepEqual(
      RAIL_DESTINATIONS.map((d) => d.label),
      ["Board", "Résumé", "Sources & keys"],
    );
  });

  it("stays one level deep — no destination declares children", () => {
    for (const d of RAIL_DESTINATIONS) {
      assert.ok(!("children" in d), `${d.key} must not have rail children`);
    }
  });
});

describe("buildRailItems", () => {
  const counts: RailCounts = { board: 14 };

  it("attaches the live count to the board destination only", () => {
    const items = buildRailItems(counts);
    const byKey = Object.fromEntries(items.map((i) => [i.key, i.count]));
    assert.equal(byKey.board, 14);
    assert.equal(byKey.resume, null);
    assert.equal(byKey.sources, null);
  });

  it("preserves RAIL_DESTINATIONS order", () => {
    const items = buildRailItems(counts);
    assert.deepEqual(items.map((i) => i.key), RAIL_DESTINATIONS.map((d) => d.key));
  });
});

describe("isMobileViewport", () => {
  it("treats widths at or below the breakpoint as mobile", () => {
    assert.equal(isMobileViewport(MOBILE_BREAKPOINT_PX), true);
    assert.equal(isMobileViewport(390), true);
  });

  it("treats widths above the breakpoint as desktop", () => {
    assert.equal(isMobileViewport(MOBILE_BREAKPOINT_PX + 1), false);
    assert.equal(isMobileViewport(1920), false);
  });
});

describe("MOBILE_TAB_ITEMS", () => {
  it("is the two-item bottom bar after T81's board collapse", () => {
    assert.deepEqual(
      MOBILE_TAB_ITEMS.map((t) => t.key),
      ["board", "more"],
    );
    assert.deepEqual(
      MOBILE_TAB_ITEMS.map((t) => t.label),
      ["Board", "More"],
    );
  });
});

describe("mobileDestinationForTab", () => {
  it("maps board straight through", () => {
    assert.equal(mobileDestinationForTab("board"), "board");
  });

  it("'more' has no single destination — it opens the sheet instead", () => {
    assert.equal(mobileDestinationForTab("more"), null);
  });
});

describe("isDestinationInMoreSheet", () => {
  it("résumé and sources live behind More on phone, board does not", () => {
    assert.equal(isDestinationInMoreSheet("resume"), true);
    assert.equal(isDestinationInMoreSheet("sources"), true);
    assert.equal(isDestinationInMoreSheet("board"), false);
  });
});
