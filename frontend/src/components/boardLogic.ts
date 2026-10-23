// T81 — the board's pure logic: column metadata/copy, the tier filter,
// sort order per column, and keyboard movement. No React/DOM import here
// (same split jobBatching.ts/railLogic.ts already use) so this is
// unit-testable with `node --test`; `BoardView.tsx` is the thin render of
// it.
import { formatSalary, timeAgo } from "./cardFormat.ts";
import { groupCardsByScan } from "./jobBatching.ts";
import type { BoardCard, MatchTier, Stage } from "../types/job";

export const BOARD_COLUMNS: {
  stage: Stage;
  label: string;
  description: string;
  fillsItself: string;
}[] = [
  {
    stage: "new",
    label: "New",
    description: "Everything the last scans found above your bar, newest scan first.",
    fillsItself: "Filled automatically",
  },
  {
    stage: "shortlist",
    label: "Shortlist",
    description: "Worth applying to, when you have time to write something.",
    fillsItself: "You move these",
  },
  {
    stage: "applied",
    label: "Applied",
    description: "Sent. Oldest at the bottom, so the ones going stale are visible.",
    fillsItself: "You move these · FillerBot too",
  },
  {
    stage: "replied",
    label: "Replied",
    description: "Someone came back to you. Add the date and it shows on the card.",
    fillsItself: "You move these",
  },
  {
    stage: "out",
    label: "Not for me",
    description: "Dismissed, with who dismissed it. Drag one back to undo.",
    fillsItself: "You and the AI filter",
  },
];

/** Keyboard `1`-`5` (the product spec's board DoD) maps straight onto
 * `BOARD_COLUMNS`' order, one-indexed. */
export const STAGE_BY_DIGIT: Record<string, Stage> = Object.fromEntries(
  BOARD_COLUMNS.map((c, i) => [String(i + 1), c.stage]),
) as Record<string, Stage>;

/** New groups by scan batch, newest batch first (the product spec principle
 * 2) — `groupCardsByScan` already orders batches by first-appearance, and
 * the board's "new" column rows arrive newest-first from the server
 * (`get_recent_matches`), so this is a direct reuse, not a reimplementation. */
export function sortNewColumn(cards: BoardCard[]): BoardCard[] {
  return groupCardsByScan(cards).flatMap((batch) => batch.cards);
}

/** Applied sorts oldest-last (brief: "so stale applications are
 * visible"), by `stage_entered_at` ascending, i.e. the most recently
 * applied job is first and the one that has been sitting longest is at
 * the bottom. Cards with no timestamp (shouldn't happen once applied, but
 * kept defensive) sort to the top as "unknown, treat as freshest". */
export function sortAppliedColumn(cards: BoardCard[]): BoardCard[] {
  return [...cards].sort((a, b) => {
    const ta = a.stage_entered_at ? Date.parse(a.stage_entered_at) : Infinity;
    const tb = b.stage_entered_at ? Date.parse(b.stage_entered_at) : Infinity;
    return tb - ta;
  });
}

/** Every other column (shortlist/replied/out) sorts newest-stage-entry
 * first — no brief requirement either way, so this picks the one
 * consistent, most-recently-touched-first order for "what changed
 * recently" rather than leaving insertion order (server dict order)
 * undefined from the UI's perspective. */
export function sortByStageEnteredDesc(cards: BoardCard[]): BoardCard[] {
  return [...cards].sort((a, b) => {
    const ta = a.stage_entered_at ? Date.parse(a.stage_entered_at) : 0;
    const tb = b.stage_entered_at ? Date.parse(b.stage_entered_at) : 0;
    return tb - ta;
  });
}

/** T101 — starred cards float to the top of a column, leaving the
 * existing within-column order intact beneath them (New's scan-batch
 * order, Applied's oldest-last, etc.). `Array.prototype.sort` is stable
 * (guaranteed since ES2019), so a comparator that only ever distinguishes
 * starred from unstarred preserves the relative order *within* each of
 * those two groups exactly as `cards` arrived — this must run *after*
 * the stage-specific sort below, not instead of it. */
export function floatStarredToTop(cards: BoardCard[]): BoardCard[] {
  return [...cards].sort((a, b) => Number(b.starred) - Number(a.starred));
}

export function sortColumn(stage: Stage, cards: BoardCard[]): BoardCard[] {
  const sorted =
    stage === "new" ? sortNewColumn(cards) : stage === "applied" ? sortAppliedColumn(cards) : sortByStageEnteredDesc(cards);
  return floatStarredToTop(sorted);
}

// ── Tier filter (the product spec: "location tiers stop being sub-tabs and
// become a filter over the board"). ─────────────────────────────────────

export type TierFilterValue = "all" | MatchTier;

export const TIER_FILTER_OPTIONS: { key: TierFilterValue; label: string }[] = [
  { key: "all", label: "All" },
  { key: "preferred", label: "Preferred" },
  { key: "country", label: "Same country" },
  { key: "global", label: "Global remote" },
];

/** Whether `card` passes the active tier filter. `"all"` always passes. */
export function cardPassesTierFilter(card: BoardCard, filter: TierFilterValue): boolean {
  return filter === "all" || card.tier === filter;
}

/** T84: when the "same country" tier isn't configured at all, say so
 * rather than render it as a filter that matched zero jobs — reads the
 * per-card `same_country_tier_configured` flag every card in a non-empty
 * board carries (jobfinder/web/jobs.py). An empty board (no cards at all)
 * has nothing to read this off of, so it defaults to "configured" (no
 * warning) rather than guessing unconfigured from silence. */
export function sameCountryTierIsConfigured(cards: BoardCard[]): boolean {
  if (cards.length === 0) return true;
  return cards[0].same_country_tier_configured;
}

// ── T82: one reducer action for every stage move (the product spec /
// this task's brief: "keyboard 1-5, the panel's stage control and a drag
// must go through the same reducer as a drag — three faces of one write,
// not three code paths"). `BoardView.tsx` has exactly one call site —
// `dispatchMoveCard` — that builds this action and is itself the thing
// the keyboard handler, `JobPanel`'s `onSetStage` prop and the drag-end
// handler all call; this function is what makes the three produce the
// identical action object for the same move, asserted in
// `boardLogic.test.ts`. ──────────────────────────────────────────────────

export interface MoveCardAction {
  type: "board/moveCard";
  jobId: string;
  toStage: Stage;
}

/** The one reducer action every stage move produces, regardless of which
 * input (drag, keyboard digit, panel stage control) triggered it. */
export function moveCardAction(jobId: string, toStage: Stage): MoveCardAction {
  return { type: "board/moveCard", jobId, toStage };
}

/** "Drop here to shortlist · Coursera · fit 92" — the mockup's literal
 * drop-slot wording, named after the target stage and the dragged job so
 * a mis-drop is visible before the pointer is released. */
export function dropSlotLabel(stage: Stage, card: BoardCard): string {
  const meta = BOARD_COLUMNS.find((c) => c.stage === stage);
  const label = meta?.label ?? stage;
  return `Drop here to ${label.toLowerCase()} · ${card.company} · fit ${card.score}`;
}

/** "Moved Test Automation Lead to Shortlist" — the undo toast's text
 * pure so the toast and any test of it share one
 * source of wording. */
export function undoToastLabel(cardTitle: string, toStage: Stage): string {
  const meta = BOARD_COLUMNS.find((c) => c.stage === toStage);
  const label = meta?.label ?? toStage;
  return `Moved ${cardTitle} to ${label}`;
}

// ── Keyboard movement (the product spec DoD). ───────────────────────────────

/** `ArrowUp`/`ArrowDown` within a column — clamps at either end, same
 * convention `jobSelection.ts`'s `neighborJobId` already established for
 * the old split list. */
export function moveWithinColumn(cards: BoardCard[], currentJobId: string | null, direction: 1 | -1): string | null {
  if (cards.length === 0) return null;
  const idx = cards.findIndex((c) => c.job_id === currentJobId);
  if (idx === -1) return cards[0].job_id;
  const nextIdx = Math.min(cards.length - 1, Math.max(0, idx + direction));
  return cards[nextIdx].job_id;
}

/** `ArrowLeft`/`ArrowRight` across columns — clamps at either end of
 * `BOARD_COLUMNS`, never wraps (same clamping convention as within a
 * column: this is a grid, not a carousel). */
export function moveToAdjacentColumn(currentIndex: number, direction: 1 | -1): number {
  return Math.min(BOARD_COLUMNS.length - 1, Math.max(0, currentIndex + direction));
}

// ── The card's foot line — stage-dependent text (the product spec's
// brief: "age and pay in New", "'kept 2 days ago' in Shortlist",
// "'applied 9 days ago' in Applied", "the reply date in Replied", "who
// dismissed it in Not for me"). ─────────────────────────────────────────

const DAY_MS = 24 * 60 * 60 * 1000;

function parseNaiveUtcMs(iso: string): number | null {
  const hasZone = /[Zz]|[+-]\d{2}:?\d{2}$/.test(iso);
  const t = Date.parse(hasZone ? iso : `${iso}Z`);
  return Number.isNaN(t) ? null : t;
}

/** `"today"` / `"1 day ago"` / `"N days ago"` — the literal wording the
 * brief quotes for Shortlist/Applied ("kept 2 days ago", "applied 9 days
 * ago"), as a standalone helper so the two stages share one
 * implementation rather than each rolling their own. */
export function daysAgoLabel(iso: string | null, now: Date = new Date()): string {
  if (!iso) return "";
  const t = parseNaiveUtcMs(iso);
  if (t === null) return "";
  const days = Math.floor((now.getTime() - t) / DAY_MS);
  if (days <= 0) return "today";
  if (days === 1) return "1 day ago";
  return `${days} days ago`;
}

const MONTH_ABBR = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** `"Oct 15"` — Replied's "the reply date", UTC throughout (same
 * convention `jobBatching.ts`'s `formatBatchTime` already uses for the
 * naive-UTC wire format). */
export function dateLabel(iso: string | null): string {
  if (!iso) return "";
  const t = parseNaiveUtcMs(iso);
  if (t === null) return "";
  const d = new Date(t);
  return `${MONTH_ABBR[d.getUTCMonth()]} ${d.getUTCDate()}`;
}

export function footLineForCard(card: BoardCard, now: Date = new Date()): string {
  if (card.stage === "new") {
    const ageSource = card.date_posted || card.matched_at;
    const age = ageSource ? timeAgo(ageSource) : "";
    const pay = formatSalary(card);
    return [age, pay].filter((s): s is string => Boolean(s)).join("  ·  ");
  }
  if (card.stage === "shortlist") {
    const ago = daysAgoLabel(card.stage_entered_at, now);
    return ago ? `Kept ${ago}` : "";
  }
  if (card.stage === "applied") {
    const ago = daysAgoLabel(card.stage_entered_at, now);
    return ago ? `Applied ${ago}` : "";
  }
  if (card.stage === "replied") {
    const date = dateLabel(card.stage_entered_at);
    return date ? `Replied ${date}` : "They replied";
  }
  // "out" — the product spec/the brief: who dismissed it.
  return card.removed_by === "agent" ? "Dismissed by the AI filter" : "Dismissed by you";
}
