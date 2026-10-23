import type { ReactNode } from "react";
import type { MatchCard } from "../types/job";
import { ScoreBadge } from "./ScoreBadge";
import { rowMetaLine } from "./cardFormat";
import "./JobRow.css";

export interface JobRowActions {
  onApply: (jobId: string) => void;
  onRemove: (jobId: string) => void;
}

/** T64 — the split master-detail's 580px list row (docs/DESIGN.md's
 * "Superseded 2026-10-22" block). One line: score chip, title, and
 * `company · location · arrangement` — rows do not expand any more
 * (that was T58's Variation B, now superseded); clicking or pressing
 * Enter/Space selects the row instead, and the detail pane
 * (`JobDetailPane.tsx`) fills with everything that used to live in the
 * expanded body (skill chips, why-matched, meta chips, the rest of the
 * actions).
 *
 * Mark applied / dismiss stay on the row itself (the parity matrix: these two
 * must be reachable without expanding anything, same as T58), using the
 * same `.job-row-icon-apply`/`.job-row-icon-dismiss` class names the T60
 * browser check already asserts against. Skip company / open posting /
 * company site move into the always-visible detail pane now that there
 * is no expanded body to hold them. */
export function JobRow({
  card,
  selected,
  actions,
  onSelect,
  onMove,
}: {
  card: MatchCard;
  selected: boolean;
  actions: JobRowActions;
  onSelect: (jobId: string) => void;
  /** ArrowUp/ArrowDown keyboard selection (this task's "the list needs
   * keyboard selection" DoD line) — `direction` is 1 for ArrowDown/-1 for
   * ArrowUp. The row itself doesn't know what "next" means (that's
   * `jobSelection.ts`'s `neighborJobId`, over the full visible list); it
   * just reports the key press up. */
  onMove: (jobId: string, direction: 1 | -1) => void;
}): ReactNode {
  const metaLine = rowMetaLine(card);
  const stop = (e: React.SyntheticEvent) => e.stopPropagation();

  return (
    <article
      className={`job-row ${selected ? "job-row-selected" : ""}`}
      data-job-id={card.job_id}
    >
      <div
        className="job-row-head"
        role="button"
        tabIndex={0}
        aria-pressed={selected}
        aria-label={`${card.title}${metaLine ? `, ${metaLine}` : ""}`}
        onClick={() => onSelect(card.job_id)}
        onKeyDown={(e) => {
          // T60 found a real keyboard trap here: keydown bubbles up from
          // the quick-action buttons nested inside this row head (Mark
          // applied/Dismiss). Without the `e.target === e.currentTarget`
          // guard, pressing Enter while one of *those* buttons is
          // focused would call `preventDefault()` here first and select
          // the row instead of letting the button's own click fire.
          // T64 keeps the same guard for the same reason, now for
          // selection instead of expand/collapse.
          if (e.target !== e.currentTarget) return;
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            onSelect(card.job_id);
          } else if (e.key === "ArrowDown") {
            e.preventDefault();
            onMove(card.job_id, 1);
          } else if (e.key === "ArrowUp") {
            e.preventDefault();
            onMove(card.job_id, -1);
          }
        }}
      >
        <ScoreBadge score={card.score} />
        <div className="job-row-title-block">
          <p className="job-row-title">{card.title}</p>
          {metaLine ? <p className="job-row-subtitle">{metaLine}</p> : null}
        </div>
        <div className="job-row-quick-actions" onClick={stop}>
          <button
            type="button"
            className="job-row-icon-btn job-row-icon-apply"
            title="Mark applied"
            aria-label="Mark applied"
            onClick={() => actions.onApply(card.job_id)}
          >
            {"✓"}
          </button>
          <button
            type="button"
            className="job-row-icon-btn job-row-icon-dismiss"
            title="Dismiss"
            aria-label="Dismiss"
            onClick={() => actions.onRemove(card.job_id)}
          >
            {"✕"}
          </button>
        </div>
      </div>
    </article>
  );
}
