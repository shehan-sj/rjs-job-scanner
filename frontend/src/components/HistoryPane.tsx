import { useEffect, useRef, useState, type ReactNode } from "react";
import type { JobCardBase } from "../types/job";
import { CardGrid } from "./CardGrid";
import { EmptyState } from "./EmptyState";
import { MetaChips } from "./MetaChips";
import { ScoreBadge } from "./ScoreBadge";
import { SkillChips } from "./SkillChips";
import { buildFactsGrid, rowMetaLine, whyMatchedLine } from "./cardFormat";
import { findSelectedCard, neighborJobId, resolveSelectedJobId } from "./jobSelection";
import "./JobRow.css";
import "./JobDetailPane.css";

/** T65 — "Applied and Removed still render the pre-T58 `JobCardView`, so
 * those tabs read as a different app" (the task ledger's Consistency
 * bullet). This brings both into the same split list/detail shell
 * `MatchesPane` (JobsView.tsx) already uses for Matches — same row
 * (`.job-row`), same detail pane (`.job-detail-pane`), same selection
 * model (`jobSelection.ts`) — rather than the tiling `CardGrid` of
 * `JobCardView` cards (T24) that made these two tabs look like a
 * different layout entirely.
 *
 * Deliberately a *sibling* of `JobRow`/`JobDetailPane`, not a prop
 * change to them: those two carry Matches' specific
 * apply/dismiss/skip-company contract (`JobRowActions`/
 * `JobDetailActions`), which T64's committed browser check
 * (`scripts/t64_split_view_browser_check.py`) asserts against directly.
 * Applied's "Undo" and Removed's "Restore" are a single action each, not
 * that three/four-action set, so this is a new, smaller component built
 * from the same shared pieces (`ScoreBadge`, `MetaChips`, `SkillChips`,
 * `cardFormat.ts`, `jobSelection.ts`) and the same CSS classes, rather
 * than overloading `JobRowActions` with an optional fourth shape nothing
 * else needs.
 *
 * No batch headers (`groupCardsByScan`) here — that's Matches' own
 * "grouped by when the scan found them" framing (docs/DESIGN.md
 * principle 2); applied/removed history reads newest-action-first
 * already, and grouping it by the scan that originally found the job
 * would not mean the same thing here. */
export function HistoryPane<T extends JobCardBase>({
  cards,
  emptyTitle,
  emptySubtitle,
  actionLabel,
  actionIcon,
  onAction,
  renderWhen,
}: {
  cards: T[];
  emptyTitle: string;
  emptySubtitle: string;
  actionLabel: string;
  actionIcon: string;
  onAction: (jobId: string) => void;
  renderWhen?: (card: T) => ReactNode;
}): ReactNode {
  const [selectedJobId, setSelectedJobId] = useState<string | null>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const effectiveSelectedId = resolveSelectedJobId(cards, selectedJobId);
  const selectedCard = findSelectedCard(cards, effectiveSelectedId);

  // T65 — Phone's two-screen list/detail (same mechanism as MatchesPane's
  // own copy in JobsView.tsx; see JobsView.css's `@media (max-width:
  // 640px)` block, which this component's reused `.jobs-view-matches-*`
  // class names already fall under).
  const [mobileDetailOpen, setMobileDetailOpen] = useState(false);
  const handleSelectRow = (jobId: string) => {
    setSelectedJobId(jobId);
    setMobileDetailOpen(true);
  };

  // T65 — same guard as MatchesPane's copy of this effect (JobsView.tsx):
  // only follow focus into the list if it was already there (keyboard
  // navigation or a row click), never steal it from elsewhere (e.g. a
  // reload resolving a fresh first-visible-card selection).
  useEffect(() => {
    if (!effectiveSelectedId || !listRef.current) return;
    const activeEl = document.activeElement;
    const focusWasInsideList = activeEl instanceof Node && listRef.current.contains(activeEl);
    if (!focusWasInsideList) return;
    const row = listRef.current.querySelector<HTMLElement>(
      `[data-job-id="${CSS.escape(effectiveSelectedId)}"] .job-row-head`,
    );
    if (row && document.activeElement !== row) {
      row.focus({ preventScroll: true });
    }
  }, [effectiveSelectedId]);

  if (cards.length === 0) {
    return (
      <CardGrid>
        <EmptyState title={emptyTitle} subtitle={emptySubtitle} />
      </CardGrid>
    );
  }

  const handleMove = (jobId: string, direction: 1 | -1) => {
    setSelectedJobId(neighborJobId(cards, jobId, direction));
  };

  return (
    <div className="jobs-view-history-wrap">
      <div
        className={`jobs-view-matches-split ${mobileDetailOpen ? "jobs-view-matches-split-show-detail" : ""}`}
      >
        <div className="jobs-view-matches-list" ref={listRef}>
          {cards.map((card) => (
            <HistoryRow
              key={card.job_id}
              card={card}
              selected={card.job_id === effectiveSelectedId}
              actionLabel={actionLabel}
              actionIcon={actionIcon}
              onAction={onAction}
              onSelect={handleSelectRow}
              onMove={handleMove}
            />
          ))}
        </div>
        <HistoryDetailPane
          card={selectedCard}
          actionLabel={actionLabel}
          onAction={onAction}
          renderWhen={renderWhen}
          onBack={() => setMobileDetailOpen(false)}
        />
      </div>
    </div>
  );
}

function HistoryRow<T extends JobCardBase>({
  card,
  selected,
  actionLabel,
  actionIcon,
  onAction,
  onSelect,
  onMove,
}: {
  card: T;
  selected: boolean;
  actionLabel: string;
  actionIcon: string;
  onAction: (jobId: string) => void;
  onSelect: (jobId: string) => void;
  onMove: (jobId: string, direction: 1 | -1) => void;
}): ReactNode {
  const metaLine = rowMetaLine(card);
  const stop = (e: React.SyntheticEvent) => e.stopPropagation();

  return (
    <article className={`job-row ${selected ? "job-row-selected" : ""}`} data-job-id={card.job_id}>
      <div
        className="job-row-head"
        role="button"
        tabIndex={0}
        aria-pressed={selected}
        aria-label={`${card.title}${metaLine ? `, ${metaLine}` : ""}`}
        onClick={() => onSelect(card.job_id)}
        onKeyDown={(e) => {
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
            className="job-row-icon-btn"
            title={actionLabel}
            aria-label={actionLabel}
            onClick={() => onAction(card.job_id)}
          >
            {actionIcon}
          </button>
        </div>
      </div>
    </article>
  );
}

function HistoryDetailPane<T extends JobCardBase>({
  card,
  actionLabel,
  onAction,
  renderWhen,
  onBack,
}: {
  card: T | null;
  actionLabel: string;
  onAction: (jobId: string) => void;
  renderWhen?: (card: T) => ReactNode;
  /** T65 — Phone: the detail pane's back affordance, mirroring
   * `JobDetailPane`'s. Applied and Removed share the Matches shell, so
   * they need the same way out of the detail screen; `JobsView.css`'s
   * `@media (max-width: 640px)` block is what hides the button above
   * phone width, where list and detail already sit side by side. */
  onBack?: () => void;
}): ReactNode {
  if (!card) {
    return (
      <div className="job-detail-pane job-detail-pane-empty">
        <p className="job-detail-empty-text">Select a job from the list to see the details.</p>
      </div>
    );
  }

  const why = whyMatchedLine(card.signals);
  const facts = buildFactsGrid(card);
  const url = card.url_direct || card.url;

  return (
    <div className="job-detail-pane" data-job-id={card.job_id}>
      {onBack ? (
        <button type="button" className="job-detail-back-btn" onClick={onBack}>
          {"← Back to list"}
        </button>
      ) : null}
      <header className="job-detail-header">
        <ScoreBadge score={card.score} />
        <div className="job-detail-heading">
          <h2 className="job-detail-title">{card.title}</h2>
          {renderWhen ? renderWhen(card) : null}
        </div>
      </header>

      <MetaChips card={card} />

      <div className="job-detail-actions">
        {url ? (
          <a className="job-card-btn job-card-btn-action" href={url} target="_blank" rel="noreferrer">
            {"Open job  →"}
          </a>
        ) : null}
        <button type="button" className="job-card-btn job-card-btn-secondary" onClick={() => onAction(card.job_id)}>
          {actionLabel}
        </button>
      </div>

      {why || card.reasoning ? (
        <section className="job-detail-section">
          <h3 className="job-detail-section-title">Match reasoning</h3>
          {why ? <p className="job-detail-why">{why}</p> : null}
          {card.reasoning ? <p className="job-detail-reasoning">{card.reasoning}</p> : null}
        </section>
      ) : null}

      {card.skills.length > 0 ? (
        <section className="job-detail-section">
          <h3 className="job-detail-section-title">Matched skills</h3>
          <SkillChips skills={card.skills} />
        </section>
      ) : null}

      <section className="job-detail-section">
        <h3 className="job-detail-section-title">Details</h3>
        <dl className="job-detail-facts-grid">
          {facts.map((fact) => (
            <div className="job-detail-fact" key={fact.label}>
              <dt className="job-detail-fact-label">{fact.label}</dt>
              <dd className="job-detail-fact-value">{fact.value}</dd>
            </div>
          ))}
        </dl>
      </section>
    </div>
  );
}
