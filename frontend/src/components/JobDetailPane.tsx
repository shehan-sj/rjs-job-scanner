import type { ReactNode } from "react";
import type { MatchCard } from "../types/job";
import { ScoreBadge } from "./ScoreBadge";
import { MetaChips } from "./MetaChips";
import { SkillChips } from "./SkillChips";
import { buildFactsGrid, companyMetaLine, deriveGaps, whyMatchedLine } from "./cardFormat";
import "./JobDetailPane.css";

export interface JobDetailActions {
  onApply: (jobId: string) => void;
  onRemove: (jobId: string) => void;
  onSkipCompany: (company: string) => void;
}

/** T64 — the split master-detail's detail pane (docs/DESIGN.md's
 * "Superseded 2026-10-22" block): "a detail pane carrying the match
 * reasoning, matched skills, gaps and the structured facts. Selecting a
 * row fills the pane; nothing has to be expanded to be read." Everything
 * that used to live in `JobRow`'s expanded body (T58) lives here now,
 * always visible for whichever job is selected — including the three
 * The parity matrix card actions that used to require expanding a row first
 * (skip company / open posting / company site), which is why this pane
 * carries its own action row rather than reusing the list row's. */
export function JobDetailPane({
  card,
  actions,
  onBack,
}: {
  card: MatchCard | null;
  actions: JobDetailActions;
  /** T65 — Phone: the detail pane's back affordance (JobsView.css's
   * `@media (max-width: 640px)` block hides this button entirely above
   * that width, where list and detail already sit side by side and there
   * is nothing to "go back" from). `onBack` is always passed in
   * (JobsView.tsx) rather than made conditional — the button simply has
   * no visible home on a wide viewport. */
  onBack?: () => void;
}): ReactNode {
  if (!card) {
    return (
      <div className="job-detail-pane job-detail-pane-empty">
        <p className="job-detail-empty-text">Select a job from the list to see the details.</p>
      </div>
    );
  }

  const metaLine = companyMetaLine(card);
  const why = whyMatchedLine(card.signals);
  const gaps = deriveGaps(card.signals);
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
          {metaLine ? <p className="job-detail-meta">{metaLine}</p> : null}
        </div>
      </header>

      <MetaChips card={card} />

      {/* The parity matrix's five card actions, all reachable here without
          expanding anything: mark applied / dismiss / skip company /
          open posting / company site.
          T65 (b) — "Open job" used to sit behind a `job-card-actions-spacer`
          that pushed it to the far right, detached from the four actions
          it belongs with (the task ledger's traced cause). It now opens the
          row, first and styled as the primary action, rather than
          trailing alone across a gap from its siblings. */}
      <div className="job-detail-actions">
        {url ? (
          <a className="job-card-btn job-card-btn-action" href={url} target="_blank" rel="noreferrer">
            {"Open job  →"}
          </a>
        ) : null}
        <button
          type="button"
          className="job-card-btn job-card-btn-secondary"
          onClick={() => actions.onApply(card.job_id)}
        >
          {"Mark applied"}
        </button>
        <button
          type="button"
          className="job-card-btn job-card-btn-danger"
          onClick={() => actions.onRemove(card.job_id)}
        >
          {"Dismiss"}
        </button>
        <button
          type="button"
          className="job-card-btn job-card-btn-secondary"
          disabled={!card.company}
          onClick={() => actions.onSkipCompany(card.company)}
        >
          {"🚫 Skip company"}
        </button>
        {card.company_url ? (
          <a
            className="job-card-btn job-card-btn-secondary"
            href={card.company_url}
            target="_blank"
            rel="noreferrer"
          >
            {"Company site"}
          </a>
        ) : null}
      </div>

      <section className="job-detail-section">
        <h3 className="job-detail-section-title">Match reasoning</h3>
        {why ? <p className="job-detail-why">{why}</p> : null}
        {card.reasoning ? <p className="job-detail-reasoning">{card.reasoning}</p> : null}
        {!why && !card.reasoning ? <p className="job-detail-empty-text">No reasoning recorded for this match.</p> : null}
      </section>

      {card.skills.length > 0 ? (
        <section className="job-detail-section">
          <h3 className="job-detail-section-title">Matched skills</h3>
          <SkillChips skills={card.skills} />
        </section>
      ) : null}

      {gaps.length > 0 ? (
        <section className="job-detail-section">
          <h3 className="job-detail-section-title">Gaps</h3>
          <div className="chip-row">
            {gaps.map((gap) => (
              <span key={gap} className="chip job-detail-gap-chip">
                {gap}
              </span>
            ))}
          </div>
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
