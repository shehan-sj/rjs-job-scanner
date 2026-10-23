import type { ReactNode } from "react";
import { BOARD_COLUMNS } from "./boardLogic.ts";
import {
  activityTrailLines,
  askedForNotInResumeChips,
  matchedSkillChips,
  panelFactsGrid,
  panelHeadingLine,
  positionLabel,
  tierSentence,
  whyScoredLine,
} from "./panelLogic.ts";
import { fitBgVar, fitFgVar, fitStep } from "./fitRamp.ts";
import "./Chips.css";
import "./JobPanel.css";
import type { BoardCard, Stage } from "../types/job";

/**
 * T83 — the job detail panel: opens beside the board, not
 * over it. Written fresh rather than reviving `JobDetailPane.tsx` (dead
 * since T81 replaced the split master-detail list with the board, see
 * that file's own docstring) — that pane's markup and CSS are tied to
 * the old split-view shell (`job-detail-*` classes, the `--jf-btn-*`
 * alias family) and its action set (mark applied / dismiss / skip
 * company / open / company site) doesn't match this task's (the stage
 * control replaces mark-applied/dismiss, and skip-company moves here
 * permanently per rule 7 rather than living on both surfaces). The
 * reusable pure pieces it *did* leave behind (`cardFormat.ts`'s
 * `whyMatchedLine`/`formatSalary`/`sourceLabel`, `SkillChips`'s
 * `.chip`/`.chip-row` classes) are reused via `panelLogic.ts`, just not
 * the pane component itself.
 */
export interface JobPanelProps {
  card: BoardCard;
  index: number;
  total: number;
  onClose: () => void;
  onPrev: () => void;
  onNext: () => void;
  canPrev: boolean;
  canNext: boolean;
  onSetStage: (jobId: string, stage: Stage) => void;
  onOpenFillerBot?: (url: string) => void;
  onSkipCompany: (company: string) => void;
}

export function JobPanel({
  card,
  index,
  total,
  onClose,
  onPrev,
  onNext,
  canPrev,
  canNext,
  onSetStage,
  onOpenFillerBot,
  onSkipCompany,
}: JobPanelProps): ReactNode {
  const columnLabel = BOARD_COLUMNS.find((c) => c.stage === card.stage)?.label ?? card.stage;
  const url = card.url_direct || card.url;
  const step = fitStep(card.score, card.stage);
  const why = whyScoredLine(card);
  const matched = matchedSkillChips(card);
  const gaps = askedForNotInResumeChips(card);
  const facts = panelFactsGrid(card);
  const trail = activityTrailLines(card);

  return (
    <aside className="job-panel" data-job-id={card.job_id} aria-label={`Job detail: ${card.title}`}>
      <div className="job-panel-nav">
        <button type="button" className="job-panel-close" aria-label="Close panel" onClick={onClose}>
          {"✕"}
        </button>
        <span className="job-panel-position">{positionLabel(index, total, columnLabel)}</span>
        <div className="job-panel-nav-btns">
          <button
            type="button"
            className="job-panel-nav-btn"
            aria-label="Previous job in this column"
            onClick={onPrev}
            disabled={!canPrev}
          >
            {"‹"}
          </button>
          <button
            type="button"
            className="job-panel-nav-btn"
            aria-label="Next job in this column"
            onClick={onNext}
            disabled={!canNext}
          >
            {"›"}
          </button>
        </div>
      </div>

      <div
        className="job-panel-fit"
        style={{ background: `var(${fitBgVar(step)})`, color: `var(${fitFgVar(step)})` }}
      >
        <span className="job-panel-fit-score">{card.score}</span>
        <span className="job-panel-fit-label">fit</span>
      </div>

      <header className="job-panel-header">
        <h2 className="job-panel-title">{card.title}</h2>
        <p className="job-panel-meta">{panelHeadingLine(card)}</p>
      </header>

      <div className="job-panel-actions">
        {url ? (
          <a
            className="job-panel-btn job-panel-btn-primary"
            href={url}
            target="_blank"
            rel="noreferrer"
          >
            {"Open the posting"}
          </a>
        ) : null}
        {onOpenFillerBot && url ? (
          <button
            type="button"
            className="job-panel-btn job-panel-btn-secondary"
            onClick={() => onOpenFillerBot(url)}
          >
            {"Fill it with FillerBot"}
          </button>
        ) : null}
        {card.company_url ? (
          <a className="job-panel-btn job-panel-btn-secondary" href={card.company_url} target="_blank" rel="noreferrer">
            {"Company site"}
          </a>
        ) : null}
      </div>

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

      <section className="job-panel-section">
        <h3 className="job-panel-section-title">Why this scored {card.score}</h3>
        {why ? <p className="job-panel-why">{why}</p> : null}
        {card.reasoning ? <p className="job-panel-reasoning">{card.reasoning}</p> : null}
        {!why && !card.reasoning ? <p className="job-panel-empty-text">No reasoning recorded for this match.</p> : null}
      </section>

      {matched.length > 0 || gaps.length > 0 ? (
        <section className="job-panel-section">
          <h3 className="job-panel-section-title">Matched skills</h3>
          {matched.length > 0 ? (
            <div className="chip-row">
              {matched.map((skill, i) => (
                <span key={`${skill}-${i}`} className="chip" style={{ background: "var(--jf-tag-skill-bg)", color: "var(--jf-tag-skill-fg)" }}>
                  {skill}
                </span>
              ))}
            </div>
          ) : (
            <p className="job-panel-empty-text">No matched skills recorded.</p>
          )}
          {gaps.length > 0 ? (
            <>
              <h4 className="job-panel-subsection-title">Asked for, not in your résumé</h4>
              <div className="chip-row">
                {gaps.map((skill, i) => (
                  <span key={`${skill}-${i}`} className="chip" style={{ background: "var(--jf-tag-warn-bg)", color: "var(--jf-tag-warn-fg)" }}>
                    {skill}
                  </span>
                ))}
              </div>
            </>
          ) : null}
        </section>
      ) : null}

      <section className="job-panel-section">
        <h3 className="job-panel-section-title">Details</h3>
        <dl className="job-panel-facts-grid">
          {facts.map((fact) => (
            <div className="job-panel-fact" key={fact.label}>
              <dt className="job-panel-fact-label">{fact.label}</dt>
              <dd className="job-panel-fact-value">{fact.value}</dd>
            </div>
          ))}
        </dl>
      </section>

      <section className="job-panel-section">
        <h3 className="job-panel-section-title">Why it reached you</h3>
        <p className="job-panel-tier-sentence">{tierSentence(card)}</p>
      </section>

      <section className="job-panel-section">
        <h3 className="job-panel-section-title">Activity</h3>
        {trail.map((line, i) => (
          <p className="job-panel-activity-line" key={i}>
            {line}
          </p>
        ))}
      </section>

      {/* The product spec: "never show this company" lives here and
          nowhere else — never on a card, see BoardView.tsx's card markup. */}
      <div className="job-panel-footer">
        <button
          type="button"
          className="job-panel-btn job-panel-btn-danger"
          disabled={!card.company}
          onClick={() => onSkipCompany(card.company)}
        >
          {"Never show this company"}
        </button>
      </div>
    </aside>
  );
}
