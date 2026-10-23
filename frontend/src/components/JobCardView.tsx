import type { ReactNode } from "react";
import type { AppliedCard, MatchCard, RemovedCard } from "../types/job";
import { ScoreBadge } from "./ScoreBadge";
import { MetaChips } from "./MetaChips";
import { SkillChips } from "./SkillChips";
import { companyMetaLine, timeAgo, whyMatchedLine } from "./cardFormat";
import "./JobCardView.css";

type JobCardViewProps =
  | {
      variant: "match";
      card: MatchCard;
      onApply: (jobId: string) => void;
      onRemove: (jobId: string) => void;
      onSkipCompany: (company: string) => void;
    }
  | { variant: "applied"; card: AppliedCard; onUndo: (jobId: string) => void }
  | { variant: "removed"; card: RemovedCard; onRestore: (jobId: string) => void };

/** Ports `jobfinder/ui/widget.py`'s `_build_match_card` /
 * `_build_applied_card` / `_build_removed_card` (T24). Title, score
 * badge, meta line, meta chips, skill chips and the why-matched
 * breakdown are shared across all three variants (widget.py builds the
 * same rows on every card type); the applied-date/Undo and
 * removed-date/reasoning/Restore pieces are variant-specific, matching
 * which fields each endpoint's response actually carries. */
export function JobCardView(props: JobCardViewProps): ReactNode {
  const { card } = props;
  const metaLine = companyMetaLine(card);
  const why = whyMatchedLine(card.signals);

  return (
    <article className="job-card" data-job-id={card.job_id}>
      <header className="job-card-header">
        <div className="job-card-title-block">
          <h3 className="job-card-title">{card.title}</h3>
          {metaLine ? <p className="job-card-subtitle">{metaLine}</p> : null}
          {props.variant === "applied" ? <AppliedWhen card={props.card} /> : null}
          {props.variant === "removed" ? <RemovedWhen card={props.card} /> : null}
        </div>
        <ScoreBadge score={card.score} />
      </header>

      <MetaChips card={card} />

      {card.skills.length > 0 ? (
        <>
          <hr className="job-card-sep" />
          <SkillChips skills={card.skills} />
        </>
      ) : null}

      {why ? <p className="job-card-why">{why}</p> : null}

      <footer className="job-card-actions">
        {props.variant === "match" ? <MatchActions {...props} /> : null}
        {props.variant === "applied" ? <AppliedActions {...props} /> : null}
        {props.variant === "removed" ? <RemovedActions {...props} /> : null}
      </footer>
    </article>
  );
}

function AppliedWhen({ card }: { card: AppliedCard }): ReactNode {
  const ago = timeAgo(card.applied_at);
  return ago ? <p className="job-card-when job-card-when-applied">{`✓  Applied ${ago}`}</p> : null;
}

function RemovedWhen({ card }: { card: RemovedCard }): ReactNode {
  const ago = timeAgo(card.removed_at);
  const bits: string[] = [];
  if (ago) bits.push(`Removed ${ago}`);
  if (card.removed_by === "agent") bits.push("by Agent");
  return (
    <>
      {bits.length ? <p className="job-card-when job-card-when-removed">{bits.join("  ·  ")}</p> : null}
      {card.removed_by === "agent" && card.reasoning ? (
        <p className="job-card-reasoning">{card.reasoning}</p>
      ) : null}
    </>
  );
}

function MatchActions({
  card,
  onApply,
  onRemove,
  onSkipCompany,
}: Extract<JobCardViewProps, { variant: "match" }>): ReactNode {
  const url = card.url_direct || card.url;
  return (
    <>
      <button type="button" className="job-card-btn job-card-btn-secondary" onClick={() => onApply(card.job_id)}>
        {"✓ Applied"}
      </button>
      <button type="button" className="job-card-btn job-card-btn-danger" onClick={() => onRemove(card.job_id)}>
        Dismiss
      </button>
      <button
        type="button"
        className="job-card-btn job-card-btn-secondary"
        disabled={!card.company}
        onClick={() => onSkipCompany(card.company)}
      >
        {"🚫 Skip company"}
      </button>
      <span className="job-card-actions-spacer" />
      {url ? (
        <a className="job-card-btn job-card-btn-action" href={url} target="_blank" rel="noreferrer">
          {"Open job  →"}
        </a>
      ) : null}
    </>
  );
}

function AppliedActions({ card, onUndo }: Extract<JobCardViewProps, { variant: "applied" }>): ReactNode {
  return (
    <>
      <button type="button" className="job-card-btn job-card-btn-secondary" onClick={() => onUndo(card.job_id)}>
        Undo
      </button>
      <span className="job-card-actions-spacer" />
      {card.url ? (
        <a className="job-card-btn job-card-btn-action" href={card.url} target="_blank" rel="noreferrer">
          {"Open job  →"}
        </a>
      ) : null}
    </>
  );
}

function RemovedActions({ card, onRestore }: Extract<JobCardViewProps, { variant: "removed" }>): ReactNode {
  return (
    <>
      <button type="button" className="job-card-btn job-card-btn-secondary" onClick={() => onRestore(card.job_id)}>
        Restore
      </button>
      <span className="job-card-actions-spacer" />
      {card.url ? (
        <a className="job-card-btn job-card-btn-action" href={card.url} target="_blank" rel="noreferrer">
          {"Open job  →"}
        </a>
      ) : null}
    </>
  );
}
