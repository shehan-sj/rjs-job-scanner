// T83 — the job detail panel's pure logic (the task brief):
// the heading meta line, matched-skill/gap split, the facts grid, the
// tier sentence and the activity trail. No React/DOM import here, same
// split `boardLogic.ts`/`cardFlags.ts` already use, so this is
// unit-testable with `node --test`; `JobPanel.tsx` is the thin render of
// it.
import { daysAgoLabel, BOARD_COLUMNS } from "./boardLogic.ts";
import { formatSalary, sourceLabel, timeAgo, whyMatchedLine, type FactItem } from "./cardFormat.ts";
import type { BoardCard } from "../types/job";

/** The panel header's "Job n of m in Column" line — `index`/`total` are
 * the card's position within the *same* filtered/sorted column list the
 * board itself renders (BoardView.tsx passes that list through), not a
 * reimplementation of the board's sort/filter here. */
export function positionLabel(index: number, total: number, columnLabel: string): string {
  return `Job ${index + 1} of ${total} in ${columnLabel}`;
}

/** "found N ago on Source" — reuses `timeAgo` (which already appends
 * "ago") rather than reformatting the relative time a second way. `null`
 * when there is no date to report, so the caller can omit the whole
 * clause instead of printing a dangling "found on Source". */
export function foundAgoOnSourceLine(card: BoardCard): string | null {
  const ageSource = card.date_posted || card.matched_at;
  const age = ageSource ? timeAgo(ageSource) : "";
  if (!age) return null;
  const source = sourceLabel(card.source);
  return source ? `found ${age} on ${source}` : `found ${age}`;
}

/** The panel heading's meta line (the task brief, verbatim order):
 * "company · city · arrangement · listing type · found N ago on Source".
 * Any part with no data is dropped rather than printed as a stated blank
 * — this is a scannable one-liner, not the facts grid (which does state
 * blanks explicitly, see `panelFactsGrid` below). */
export function panelHeadingLine(card: BoardCard): string {
  const parts: string[] = [];
  if (card.company.trim()) parts.push(card.company.trim());
  if (card.city.trim()) parts.push(card.city.trim());
  if (card.work_arrangement && card.work_arrangement !== "Unknown") parts.push(card.work_arrangement);
  if (card.listing_type.trim()) parts.push(card.listing_type.trim());
  const found = foundAgoOnSourceLine(card);
  if (found) parts.push(found);
  return parts.join("  ·  ");
}

/** "Why this scored N" — the per-signal breakdown line plus the server's
 * free-text `reasoning`, same two sources `JobDetailPane.tsx` (dead since
 * T81) already read. No keyword-overlap-alone aside is added here: that
 * would require re-deriving the matcher's per-axis weights (30/30/15/15/10,
 * `jobfinder/matcher/local_matcher.py`) which `signals` does not itself
 * carry, and the brief is explicit that inventing that number is worse
 * than omitting the line — so it is omitted. */
export function whyScoredLine(card: BoardCard): string | null {
  return whyMatchedLine(card.signals);
}

function normalizedSkillSet(skills: string[]): Set<string> {
  return new Set(skills.map((s) => s.trim().toLowerCase()).filter(Boolean));
}

/** The skills actually found against the résumé (`matched_skills`) — see
 * `types/job.ts`'s field docstring for why this, not `skills`, is the
 * "matched" set. */
export function matchedSkillChips(card: BoardCard): string[] {
  return card.matched_skills;
}

/** "Asked for, not in your résumé" — `skills` (the posting's own listed
 * skills) minus whatever `matched_skills` actually found, case/whitespace
 * -insensitive. Empty whenever the data doesn't distinguish the two sets
 * (e.g. a non-LinkedIn source where `skills` falls back to
 * `skills_matched` itself, `jobfinder/dto.py`), which correctly reports
 * no gap rather than a fabricated one. */
export function askedForNotInResumeChips(card: BoardCard): string[] {
  const matchedSet = normalizedSkillSet(card.matched_skills);
  return card.skills.filter((s) => !matchedSet.has(s.trim().toLowerCase()));
}

/** T83's facts grid — pay, seniority, function, listing type, company
 * size, industry, posted, source (the task brief's enumerated eight),
 * every row stated even when blank ("Not listed"), same convention
 * `cardFormat.ts`'s `buildFactsGrid` already established for the
 * now-dead split-view pane. Deliberately a different field set from that
 * function (this adds listing type/industry, drops compensation's old
 * single "Compensation" label in favour of "Pay") rather than reusing it
 * outright. */
export function panelFactsGrid(card: BoardCard): FactItem[] {
  const companySize = card.company_num_employees.trim();
  return [
    { label: "Pay", value: formatSalary(card) ?? "Not listed" },
    { label: "Seniority", value: card.job_level.trim() || "Not listed" },
    { label: "Function", value: card.job_function.trim() || "Not listed" },
    { label: "Listing type", value: card.listing_type.trim() || "Not listed" },
    { label: "Company size", value: companySize ? `${companySize} employees` : "Not listed" },
    { label: "Industry", value: card.company_industry.trim() || "Not listed" },
    { label: "Posted", value: card.date_posted ? timeAgo(card.date_posted) : "Not listed" },
    { label: "Source", value: sourceLabel(card.source) || "Not listed" },
  ];
}

/** "Why it reached you" — the tier, in a sentence (the task brief).
 * `tier_location_is_unresolved` (T84) takes precedence over the plain
 * `global` sentence when both are true, since "global" there means
 * "could not be classified", not "chosen as worldwide-remote". */
export function tierSentence(card: BoardCard): string {
  if (card.tier === "global" && card.tier_location_is_unresolved) {
    return "This reached you because its location could not be matched to a tier, so it was treated as global remote.";
  }
  if (card.tier === "preferred") {
    return "This reached you because its location is one of your preferred regions.";
  }
  if (card.tier === "country") {
    return "This reached you because it's in your country, outside your preferred regions.";
  }
  return "This reached you as a global remote match, outside your preferred regions and country.";
}

/** The activity trail (the task brief): when the scan found it, who
 * moved it and when, and what the next action would do — built only from
 * fields that exist (`matched_at`, `scan_id`, `stage`, `stage_entered_at`,
 * `removed_by`). There is no per-move audit table (the brief is explicit
 * that this task does not add one), so this is at most two sentences
 * about the *current* stage, never an invented history. */
export function activityTrailLines(card: BoardCard, now: Date = new Date()): string[] {
  const lines: string[] = [];

  if (card.matched_at) {
    const age = timeAgo(card.matched_at);
    if (age) lines.push(`A scan found this ${age}.`);
  }

  if (card.stage === "out") {
    if (card.stage_entered_at) {
      const who = card.removed_by === "agent" ? "the AI filter" : "you";
      const ago = daysAgoLabel(card.stage_entered_at, now);
      lines.push(ago ? `Dismissed by ${who} ${ago}.` : `Dismissed by ${who}.`);
    }
  } else if (card.stage !== "new" && card.stage_entered_at) {
    const label = BOARD_COLUMNS.find((c) => c.stage === card.stage)?.label ?? card.stage;
    const ago = daysAgoLabel(card.stage_entered_at, now);
    lines.push(ago ? `Moved to ${label} ${ago}.` : `Moved to ${label}.`);
  }

  // What the next action would do — generic by design, not per-card data
  // (there is nothing in the enumerated fields that predicts a future
  // action), so this is fixed UI guidance rather than a guess.
  lines.push("Use the stage control below to move it to a different stage.");

  return lines;
}
