import { useEffect, useState, type ReactNode } from "react";
import { BOARD_COLUMNS } from "./boardLogic.ts";
import {
  activityTrailLines,
  askedForNotInResumeChips,
  foundAgoOnSourceLine,
  matchedSkillChips,
  panelRailFacts,
  panelIdentityLine,
  payChipText,
  positionLabel,
  tierSentence,
} from "./panelLogic.ts";
import { breakdownSentence, reasoningProse, scoreBreakdown } from "./scoreBreakdown.ts";
import {
  cachedJobDescription,
  loadJobDescription,
  type JobDescriptionState,
} from "../api/jobDescription.ts";
import { fitBgVar, fitFgVar, fitStep } from "./fitRamp.ts";
import "./Chips.css";
import "./JobPanel.css";
import type { BoardCard, Stage } from "../types/job";

/**
 * The job detail panel (T83), opened in `JobModal.tsx`'s `<dialog>` (T100).
 *
 * ## The wide dossier
 *
 * Two columns at 960px: the **job** on the left, the **decision** on the
 * right. The owner picked this shape from three mocked directions.
 *
 * The problem it solves is not decoration. The panel answered "why did this
 * score 62?" — explanation — in the largest block on screen, above pay,
 * level and setup, while the question a person opens a job to answer is
 * "is this worth my time, and what do I do next?". And the one thing that
 * actually answers it, the posting's own text, could not be shown at all:
 * `description` was scraped, scored and discarded, with no column to store
 * it in. That is fixed underneath this (`seen_jobs.description`, served by
 * `GET /api/jobs/{job_id}/description`), which is what makes a left column
 * worth having.
 *
 * So: identity spans the top, with pay as a chip beside it. Left is the
 * posting then the skills diff. Right is a rail — the actions and the
 * stage control first, because they are what the panel is *for*, then the
 * score card, the remaining facts, and the quiet provenance line. A 560px
 * single column had everything below "Matched skills" behind a scroll on a
 * 2560px display; this fits.
 *
 * The score keeps its five meters, but in a 320px rail rather than at full
 * width, and it leads with a sentence (`breakdownSentence`) instead of a
 * restated total — see that function for the off-by-one that forced it.
 *
 * Not changed, deliberately: the classnames the verification scripts drive
 * (`.job-panel`'s `data-job-id`, `.job-panel-nav-btn`'s aria-labels,
 * `.job-panel-stage-btn`, the "Never show this company" button) — see
 * `scripts/t83_panel_browser_check.py` and `scripts/t100_modal_browser_check.py`
 * — and the stage-control classes `BoardCardSheet.tsx` reuses for the phone
 * sheet.
 *
 * The score chip stays on the **neutral** `--jf-fit-*` ramp, the same one
 * `.board-card-chip` uses. It is greyscale on purpose (tokens.css: the
 * product spec "reserves hue for the pipeline"), and the tinted
 * `≥80/65–79/<65` bands docs/DESIGN.md describes belong to `--jf-score-*`,
 * a different ramp used for status. A tinted chip here would read as a
 * status colour and would stop matching the card it opened from.
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

function DescriptionBody({ state }: { state: JobDescriptionState }): ReactNode {
  if (state.status === "loading") {
    return <p className="job-panel-empty-text">Loading the posting…</p>;
  }
  if (state.status === "error") {
    return <p className="job-panel-empty-text">Could not load the posting. Open it on the source site.</p>;
  }
  if (state.status === "empty") {
    // Two different absences, worded differently: a job found before this
    // was stored is not a job with no description, and saying so stops it
    // reading as a bug.
    return (
      <p className="job-panel-empty-text">
        {state.storedEver
          ? "This posting had no description text."
          : "Found before JobFinder kept posting text — the next scan that sees this job will store it."}
      </p>
    );
  }
  return <div className="job-panel-description-text">{state.text}</div>;
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
  const axes = scoreBreakdown(card);
  const prose = reasoningProse(card);
  const matched = matchedSkillChips(card);
  const gaps = askedForNotInResumeChips(card);
  const facts = panelRailFacts(card);
  const trail = activityTrailLines(card);
  const identity = panelIdentityLine(card);
  const found = foundAgoOnSourceLine(card);
  const pay = payChipText(card);

  // Seeded from the cache so a job already fetched paints immediately
  // instead of flashing "Loading…" when it is reopened. `JobModal` keys
  // this component by job id, so swapping jobs remounts it and this
  // initializer runs again — there is no state to reset from an effect.
  const [description, setDescription] = useState<JobDescriptionState>(
    () => cachedJobDescription(card.job_id) ?? { status: "loading" },
  );

  useEffect(() => {
    // Already answered from the cache by the initializer above.
    if (cachedJobDescription(card.job_id) !== undefined) {
      return;
    }
    // Guards against a response landing after this panel is gone — the
    // user can press Next before a slow fetch returns.
    let alive = true;
    void loadJobDescription(card.job_id, () => alive, setDescription);
    return () => {
      alive = false;
    };
  }, [card.job_id]);

  const matchedTotal = matched.length + gaps.length;

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

      {/* Identity spans both columns: it is the one thing neither column owns. */}
      <header className="job-panel-header">
        <div className="job-panel-headings">
          <h2 className="job-panel-title">{card.title}</h2>
          {identity ? <p className="job-panel-meta">{identity}</p> : null}
          {found ? <p className="job-panel-provenance">{found}</p> : null}
        </div>
        {pay ? <span className="job-panel-pay">{pay}</span> : null}
      </header>

      {card.geo_warning ? (
        <p className="job-panel-geo-warning">{card.geo_warning}</p>
      ) : null}

      <div className="job-panel-columns">
        {/* ── Left: the job ─────────────────────────────────────────── */}
        <div className="job-panel-main">
          <section className="job-panel-section">
            <h3 className="job-panel-section-title">The posting</h3>
            <div className="job-panel-description">
              <DescriptionBody state={description} />
            </div>
          </section>

          <section className="job-panel-section">
            <h3 className="job-panel-section-title">
              Your skills against this posting
              {matchedTotal > 0 ? (
                <span className="job-panel-section-count">
                  {matched.length} of {matchedTotal}
                </span>
              ) : null}
            </h3>
            {matchedTotal > 0 ? (
              <div className="job-panel-skill-bar" aria-hidden="true">
                <span className="job-panel-skill-bar-matched" style={{ flexGrow: matched.length }} />
                {gaps.length > 0 ? (
                  <span className="job-panel-skill-bar-gap" style={{ flexGrow: gaps.length }} />
                ) : null}
              </div>
            ) : null}
            {matched.length > 0 ? (
              <div className="chip-row">
                {matched.map((skill) => (
                  <span
                    key={skill}
                    className="chip"
                    style={{ background: "var(--jf-tag-skill-bg)", color: "var(--jf-tag-skill-fg)" }}
                  >
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
                  {gaps.map((skill) => (
                    <span
                      key={skill}
                      className="chip"
                      style={{ background: "var(--jf-tag-warn-bg)", color: "var(--jf-tag-warn-fg)" }}
                    >
                      {skill}
                    </span>
                  ))}
                </div>
              </>
            ) : null}
          </section>
        </div>

        {/* ── Right: the decision rail ──────────────────────────────── */}
        <div className="job-panel-rail">
          <div className="job-panel-actions">
            {url ? (
              <a
                className="job-panel-btn job-panel-btn-primary job-panel-btn-block"
                href={url}
                target="_blank"
                rel="noreferrer"
              >
                {"Open the posting"}
              </a>
            ) : null}
            <div className="job-panel-actions-row">
              {onOpenFillerBot && url ? (
                <button
                  type="button"
                  className="job-panel-btn job-panel-btn-secondary"
                  onClick={() => onOpenFillerBot(url)}
                >
                  {"FillerBot"}
                </button>
              ) : null}
              {card.company_url ? (
                <a
                  className="job-panel-btn job-panel-btn-secondary"
                  href={card.company_url}
                  target="_blank"
                  rel="noreferrer"
                >
                  {"Company site"}
                </a>
              ) : null}
            </div>
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

          <section className="job-panel-score-card">
            <div className="job-panel-score-head">
              <div
                className="job-panel-fit"
                style={{ background: `var(${fitBgVar(step)})`, color: `var(${fitFgVar(step)})` }}
                title={`Fit score ${card.score} out of 100`}
                aria-label={`Fit score ${card.score} out of 100`}
              >
                <span className="job-panel-fit-score">{card.score}</span>
              </div>
              {axes ? <p className="job-panel-score-sentence">{breakdownSentence(axes)}</p> : null}
            </div>
            {axes ? (
              <ul className="job-panel-axes">
                {axes.map((axis) => (
                  <li
                    className="job-panel-axis"
                    key={axis.key}
                    data-empty={axis.points === 0 ? "true" : "false"}
                  >
                    <span className="job-panel-axis-label">{axis.label}</span>
                    <span className="job-panel-axis-meter" aria-hidden="true">
                      <span className="job-panel-axis-fill" style={{ width: `${axis.pct}%` }} />
                    </span>
                    <span className="job-panel-axis-points">
                      {axis.points}
                      <span className="job-panel-axis-max">/{axis.max}</span>
                    </span>
                  </li>
                ))}
              </ul>
            ) : null}
            {prose ? <p className="job-panel-reasoning">{prose}</p> : null}
            {!axes && !prose ? (
              <p className="job-panel-empty-text">No reasoning recorded for this match.</p>
            ) : null}
          </section>

          {facts.known.length > 0 ? (
            <dl className="job-panel-facts">
              {facts.known.map((fact) => (
                <div className="job-panel-fact" key={fact.label}>
                  <dt className="job-panel-fact-label">{fact.label}</dt>
                  <dd className="job-panel-fact-value">{fact.value}</dd>
                </div>
              ))}
            </dl>
          ) : null}
          {facts.missing.length > 0 ? (
            <p className="job-panel-facts-missing">
              {"Not listed: "}
              {facts.missing.join(", ").toLowerCase()}
            </p>
          ) : null}

          <div className="job-panel-quiet">
            <p className="job-panel-tier-sentence">{tierSentence(card)}</p>
            {trail.map((line, i) => (
              <p className="job-panel-activity-line" key={i}>
                {line}
              </p>
            ))}
          </div>

          {/* The product spec: "never show this company" lives here and
              nowhere else — never on a card, see BoardView.tsx's markup. */}
          <button
            type="button"
            className="job-panel-btn job-panel-btn-danger job-panel-btn-block"
            disabled={!card.company}
            onClick={() => onSkipCompany(card.company)}
          >
            {"Never show this company"}
          </button>
        </div>
      </div>
    </aside>
  );
}
