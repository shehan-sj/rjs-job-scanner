import type { ReactNode } from "react";
import { BOARD_COLUMNS, footLineForCard } from "./boardLogic.ts";
import { fitBgVar, fitFgVar, fitStep } from "./fitRamp.ts";
import "./JobPanel.css";
import "./BoardCardSheet.css";
import type { BoardCard, Stage } from "../types/job";

/**
 * T85 — the phone move control (this task's brief): tapping a card at
 * 390px opens this bottom sheet instead of `JobPanel.tsx` (that panel is
 * a fixed 420px side column and simply hides itself under 640px,
 * JobPanel.css). The sheet is deliberately thin — a summary, not the
 * full detail panel's why-scored/skills/activity sections — because its
 * one job is the move control: the **same five stops** `JobPanel`'s
 * `.job-panel-stage-control` offers, reusing those exact classnames
 * (JobPanel.css) rather than a second stage-control implementation, and
 * wired to the identical `onSetStage` prop `BoardView.tsx` passes both
 * components — `dispatchMoveCard`, the one reducer call site (T82's
 * brief) — so a phone move is the same write path as a desktop one, not
 * a fourth one.
 *
 * Both `JobPanel` and this sheet mount together whenever a card is
 * selected; which one is visible is a `max-width: 640px` media query
 * (JobPanel.css / BoardCardSheet.css), the same CSS-driven split
 * `FilterPanel`'s popover/phone-sheet variants already use — not a JS
 * viewport branch, so there is nothing for a resize to get out of sync
 * with.
 */
export interface BoardCardSheetProps {
  card: BoardCard;
  onClose: () => void;
  onSetStage: (jobId: string, stage: Stage) => void;
}

export function BoardCardSheet({ card, onClose, onSetStage }: BoardCardSheetProps): ReactNode {
  const step = fitStep(card.score, card.stage);
  const url = card.url_direct || card.url;
  const metaLine = [card.company, card.location].filter(Boolean).join(" · ");
  const footLine = footLineForCard(card);

  return (
    <div className="board-card-sheet-overlay" role="presentation" onClick={onClose}>
      <aside
        className="board-card-sheet"
        data-job-id={card.job_id}
        role="dialog"
        aria-label={`Move ${card.title}`}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="board-card-sheet-grabber" aria-hidden="true" />
        <div className="board-card-sheet-header">
          <div
            className="board-card-sheet-fit"
            style={{ background: `var(${fitBgVar(step)})`, color: `var(${fitFgVar(step)})` }}
          >
            {card.score}
          </div>
          <div className="board-card-sheet-titles">
            <p className="board-card-sheet-title">{card.title}</p>
            {metaLine ? <p className="board-card-sheet-meta">{metaLine}</p> : null}
            {footLine ? <p className="board-card-sheet-foot">{footLine}</p> : null}
          </div>
          <button type="button" className="board-card-sheet-close" aria-label="Close" onClick={onClose}>
            {"✕"}
          </button>
        </div>

        {url ? (
          <a
            className="job-panel-btn job-panel-btn-primary board-card-sheet-open-btn"
            href={url}
            target="_blank"
            rel="noreferrer"
          >
            {"Open the posting"}
          </a>
        ) : null}

        {/* The move control — same five stops, same reducer call site as
            desktop's `.job-panel-stage-control` (module docstring). */}
        <div className="job-panel-stage-control" role="group" aria-label="Stage">
          {BOARD_COLUMNS.map((meta, i) => (
            <button
              key={meta.stage}
              type="button"
              className="job-panel-stage-btn"
              aria-pressed={card.stage === meta.stage}
              data-active={card.stage === meta.stage ? "true" : "false"}
              onClick={() => onSetStage(card.job_id, meta.stage)}
            >
              {meta.label}
              <span className="job-panel-stage-key">{i + 1}</span>
            </button>
          ))}
        </div>
      </aside>
    </div>
  );
}
