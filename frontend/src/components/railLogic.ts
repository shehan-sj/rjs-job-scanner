// T60 — the one navigation model (docs/DESIGN.md principle 4 /
// The product spec). Pure data/logic behind `Rail.tsx`: which
// destinations exist, in what order, with what live counts, and how the
// phone breakpoint folds the rail destinations onto the bottom tab bar
// DESIGN.md's "Phone" section describes.
//
// T81 — Matches/Applied/Removed collapse into one "Board" destination
// (the product spec: three views were really three stages of one pipeline).
// This is the rail model's second shape change (the first was T60's
// original five); the mobile tab bar shrinks from four items to two
// (Board, More) for the same reason.
//
// Deliberately kept separate from Rail.tsx (no React import here) so the
// shape of the nav model is unit-testable with `node --test`, the same
// pattern jobBatching.ts/filterPanelLogic.ts already use — Rail.tsx is a
// thin render of this data, exercised by the browser check instead.

/** The three destinations left after T81's board collapse: one job
 * pipeline destination plus Résumé and Sources & keys. Kept to exactly
 * one level — none of these may grow rail children, which is the whole
 * reason location tiers are a filter *over* the board (BoardView.tsx)
 * instead of rail sub-items. */
export type RailDestination = "board" | "resume" | "sources";

export const RAIL_DESTINATIONS: { key: RailDestination; label: string }[] = [
  { key: "board", label: "Board" },
  { key: "resume", label: "Résumé" },
  { key: "sources", label: "Sources & keys" },
];

export interface RailCounts {
  board: number;
}

export interface RailItem {
  key: RailDestination;
  label: string;
  count: number | null;
}

/** Only the board destination carries a live count — the New column's
 * size (BoardView.tsx reports it up), same "how many" badge convention
 * T60 established, now for the one destination that still has one.
 * Résumé and Sources & keys have never carried a count. */
export function buildRailItems(counts: RailCounts): RailItem[] {
  return RAIL_DESTINATIONS.map((d) => ({
    key: d.key,
    label: d.label,
    count: d.key === "board" ? counts.board : null,
  }));
}

/** Below this width the rail becomes a bottom tab bar
 * (DESIGN.md "Phone", shown in the mockups at 390×844). A plain,
 * testable number rather than a magic value only living in CSS — the
 * browser check asserts the same breakpoint the stylesheet uses. */
export const MOBILE_BREAKPOINT_PX = 640;

export function isMobileViewport(width: number): boolean {
  return width <= MOBILE_BREAKPOINT_PX;
}

export type MobileTabKey = "board" | "more";

export const MOBILE_TAB_ITEMS: { key: MobileTabKey; label: string }[] = [
  { key: "board", label: "Board" },
  { key: "more", label: "More" },
];

/** Board maps straight through to the same rail destination on phone;
 * "More" has no single destination of its own — it opens the sheet
 * holding Résumé, Sources & keys and the foot controls instead of
 * navigating anywhere by itself. */
export function mobileDestinationForTab(key: MobileTabKey): RailDestination | null {
  if (key === "board") {
    return key;
  }
  return null;
}

/** Résumé and Sources & keys are reachable on phone only through the
 * "More" sheet — the board stays a direct bottom-bar tab. */
export function isDestinationInMoreSheet(destination: RailDestination): boolean {
  return destination === "resume" || destination === "sources";
}
