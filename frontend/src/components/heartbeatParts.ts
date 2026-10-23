// T96(a) — the heartbeat strip's inline form and its rail-foot popover
// used to share one `fullContent` fragment (`HeartbeatStrip.tsx`), built
// for the inline strip: `<span className="heartbeat-sep">·</span>`
// between items, meant to sit beside its neighbours on one line. Reused
// verbatim inside `.heartbeat-compact-popover`, whose `flex-direction:
// column` stacks every child onto its own line — each separator lands
// on a line by itself, an orphaned dot (the owner's screenshot).
//
// The fix is in the data, not either layout: a list of *parts*, each
// with its own opinion on whether the inline form draws a separator
// glyph immediately before it. The popover form never draws that glyph
// at all — every part gets its own line regardless of
// `precededBySeparator`, because a stacked layout does not need a
// divider between lines the way an inline one needs one between runs.
//
// Generic over `T` so this stays pure/testable without pulling in React
// or a DOM — `tests/heartbeatParts.test.ts` exercises it with plain
// strings standing in for `ReactNode`.
export interface HeartbeatPart<T> {
  key: string;
  content: T;
  /** Whether the inline form draws its separator glyph immediately
   * before this part. `false` for the first part, and for any part
   * that should run on from the previous one with no divider (matches
   * the pre-T96 inline layout's own placement exactly — this task does
   * not change what the inline strip looks like). */
  precededBySeparator: boolean;
}

export interface HeartbeatRenderItem<T> {
  key: string;
  content: T;
}

/** Inline form: each part's content, with a separator item inserted
 * immediately before any part whose `precededBySeparator` is `true`. */
export function joinInline<T>(
  parts: ReadonlyArray<HeartbeatPart<T>>,
  separator: T,
): Array<HeartbeatRenderItem<T>> {
  const out: Array<HeartbeatRenderItem<T>> = [];
  for (const part of parts) {
    if (part.precededBySeparator) {
      out.push({ key: `${part.key}-sep`, content: separator });
    }
    out.push({ key: part.key, content: part.content });
  }
  return out;
}

/** Popover/stacked form: every part's content, one per line, and never
 * the separator glyph as an entry of its own — the fix for the owner's
 * stray-bullet bug, expressed as "this function never inserts one". */
export function stackWithoutSeparators<T>(
  parts: ReadonlyArray<HeartbeatPart<T>>,
): Array<HeartbeatRenderItem<T>> {
  return parts.map((part) => ({ key: part.key, content: part.content }));
}
