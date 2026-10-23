import type { ReactNode } from "react";
import "./CardGrid.css";

// Replaces `ResponsiveCardGrid` (jobfinder/ui/widget.py:253-332, ~80 lines
// of manual Qt reflow computing 1/2/3 columns from the widget's current
// width) with plain CSS grid (T23). `minmax(690px, 1fr)` is the floor a
// card's action row needs (T24's Mark applied / Dismiss / Skip company /
// Open posting buttons) — not an arbitrary aesthetic choice, see
// CardGrid.css.
export function CardGrid({
  children,
  compact = false,
}: {
  children: ReactNode;
  /** T58 — Variation B's Rows density tiles the same grid at a much
   * shorter row height (JobRow.css), so the 16px gap built for a
   * 221px-tall card eats disproportionately into the "≥48 above the
   * fold" DoD target. `compact` switches to a tighter gap for that case
   * without touching the default (Applied/Removed/empty-state) usage. */
  compact?: boolean;
}): ReactNode {
  return <div className={`card-grid ${compact ? "card-grid-compact" : ""}`}>{children}</div>;
}
