// T58 — grouping the Matches view by scan batch, newest first
// (DESIGN.md Variation B).
//
// `groupCardsByScan` mirrors `jobfinder/storage/database.py`'s
// `group_matches_by_scan` (T57) byte-for-byte in its grouping rule: it is
// order-preserving (a batch first appears at the position of its first
// row), and a `null` scan_id is never merged with anything else — each
// such row is its own singleton batch, matching "no batch information"
// from before `scan_id` existed. The server is the one place "which jobs
// were found together" is decided; this function only
// re-expresses that same rule client-side over already-fetched cards, it
// does not invent a different clustering (e.g. by `matched_at` gaps).
import { sourceLabel } from "./cardFormat.ts";

export interface BatchableCard {
  scan_id: string | null;
  matched_at: string;
  source: string;
}

export interface Batch<T extends BatchableCard> {
  scanId: string | null;
  cards: T[];
}

export function groupCardsByScan<T extends BatchableCard>(cards: T[]): Batch<T>[] {
  const batches: Batch<T>[] = [];
  const indexByScan = new Map<string, number>();
  for (const card of cards) {
    const scanId = card.scan_id;
    if (scanId === null || scanId === undefined) {
      batches.push({ scanId: null, cards: [card] });
      continue;
    }
    const existing = indexByScan.get(scanId);
    if (existing !== undefined) {
      batches[existing].cards.push(card);
    } else {
      indexByScan.set(scanId, batches.length);
      batches.push({ scanId, cards: [card] });
    }
  }
  return batches;
}

/** `widget.py` never had an equivalent — this is new UI, so there is no
 * Python original to port. Naive-UTC parsing follows `cardFormat.ts`'s
 * `timeAgo`: a `matched_at` with no zone suffix is treated as UTC, not
 * local time, because that's how it was written
 * (`datetime.utcnow().isoformat()`, jobfinder/dto.py's module docstring). */
function parseNaiveUtc(isoStr: string): Date | null {
  const hasZone = /[Zz]|[+-]\d{2}:?\d{2}$/.test(isoStr);
  const t = Date.parse(hasZone ? isoStr : `${isoStr}Z`);
  return Number.isNaN(t) ? null : new Date(t);
}

function isSameUtcCalendarDay(a: Date, b: Date): boolean {
  return (
    a.getUTCFullYear() === b.getUTCFullYear() &&
    a.getUTCMonth() === b.getUTCMonth() &&
    a.getUTCDate() === b.getUTCDate()
  );
}

function pad2(n: number): string {
  return String(n).padStart(2, "0");
}

const MONTH_ABBR = [
  "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
];

/** Formats the batch's representative time as `Today HH:MM`,
 * `Yesterday HH:MM`, or `Mon D, HH:MM` for anything older. Deliberately
 * UTC throughout rather than the viewer's local zone: `matched_at` is
 * naive UTC on the wire (jobfinder/dto.py's module docstring) and this
 * keeps "Today"/"Yesterday" a pure, machine-independent function of two
 * timestamps instead of depending on the browser's timezone. Returns
 * `""` when the timestamp cannot be parsed. */
export function formatBatchTime(isoStr: string, now: Date = new Date()): string {
  const t = parseNaiveUtc(isoStr);
  if (!t) return "";
  const time = `${pad2(t.getUTCHours())}:${pad2(t.getUTCMinutes())}`;
  if (isSameUtcCalendarDay(t, now)) return `Today ${time}`;
  const yesterday = new Date(now.getTime() - 24 * 60 * 60 * 1000);
  if (isSameUtcCalendarDay(t, yesterday)) return `Yesterday ${time}`;
  return `${MONTH_ABBR[t.getUTCMonth()]} ${t.getUTCDate()}, ${time}`;
}

/** The unique sources in a batch, in first-seen order, as display labels
 * (e.g. `["linkedin", "linkedin", "indeed"]` -> `["LinkedIn", "Indeed"]`). */
export function batchSourceLabels<T extends BatchableCard>(batch: Batch<T>): string[] {
  const seen = new Set<string>();
  const labels: string[] = [];
  for (const card of batch.cards) {
    const key = card.source.trim().toLowerCase();
    if (!key || seen.has(key)) continue;
    seen.add(key);
    labels.push(sourceLabel(card.source));
  }
  return labels;
}

/** Builds the literal header line DESIGN.md specifies:
 * `Today 09:12 · 14 found · LinkedIn, Indeed`. The representative
 * timestamp is the batch's first card, which — because the server
 * already returns `get_recent_matches()` rows newest-first — is also the
 * newest (i.e. latest) timestamp in the batch. */
export function batchHeaderText<T extends BatchableCard>(batch: Batch<T>, now: Date = new Date()): string {
  const [first] = batch.cards;
  const parts: string[] = [];
  const time = first ? formatBatchTime(first.matched_at, now) : "";
  if (time) parts.push(time);
  parts.push(`${batch.cards.length} found`);
  const sources = batchSourceLabels(batch);
  if (sources.length) parts.push(sources.join(", "));
  return parts.join("  ·  ");
}
