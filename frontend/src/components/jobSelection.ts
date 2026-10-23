// T64 — split master-detail (docs/DESIGN.md's "Superseded 2026-10-22"
// block). Pure logic behind "which job is current" in the 580px list and
// the detail pane it fills: no React import here, same pattern
// jobBatching.ts / filterPanelLogic.ts / railLogic.ts already use so this
// is unit-testable with `node --test` and the component that renders it
// (JobRow.tsx / JobsView.tsx) stays a thin consumer.

export interface SelectableCard {
  job_id: string;
}

/** Keeps the current selection if it is still present in `cards` (e.g.
 * after a reload that doesn't drop the selected job), otherwise falls
 * back to the first card — never a job that has scrolled out of the
 * visible list after a filter change. `null` for an empty list, which is
 * how the caller knows to show the empty state instead of a detail pane. */
export function resolveSelectedJobId<T extends SelectableCard>(
  cards: T[],
  selectedId: string | null,
): string | null {
  if (selectedId !== null && cards.some((c) => c.job_id === selectedId)) {
    return selectedId;
  }
  return cards[0]?.job_id ?? null;
}

/** ArrowDown (`direction=1`) / ArrowUp (`direction=-1`) one row at a time.
 * Clamps at either end rather than wrapping — DESIGN.md's list is a
 * single scannable column, not a carousel. If the current id is not
 * found (stale selection) this starts over at the first row rather than
 * silently doing nothing. */
export function neighborJobId<T extends SelectableCard>(
  cards: T[],
  currentId: string | null,
  direction: 1 | -1,
): string | null {
  if (cards.length === 0) return null;
  const idx = cards.findIndex((c) => c.job_id === currentId);
  if (idx === -1) return cards[0].job_id;
  const nextIdx = Math.min(cards.length - 1, Math.max(0, idx + direction));
  return cards[nextIdx].job_id;
}

/** The full card object for the current selection, or `null` when
 * nothing is selected / the selection no longer exists in `cards` — the
 * detail pane's "nothing selected" state reads this directly rather than
 * re-deriving it. */
export function findSelectedCard<T extends SelectableCard>(cards: T[], selectedId: string | null): T | null {
  if (selectedId === null) return null;
  return cards.find((c) => c.job_id === selectedId) ?? null;
}
