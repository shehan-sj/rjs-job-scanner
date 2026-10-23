// The job panel's score breakdown — the five scoring axes with the points
// each actually earned, as structured data. Pure; `JobPanel.tsx` renders it.
//
// Why this exists. The panel's headline section is "Why this scored N", and
// what it rendered there was `card.reasoning` verbatim:
//
//   title:30  skills:21 (0 from LinkedIn + 7 from desc)  tools:9 (5)  seniority:0  function:0
//
// That is `jobfinder/matcher/local_matcher.py`'s own debug line, printed at
// the user. Every number in it is real and useful — it is the complete
// output of the scoring pipeline, which is the thing this product exists to
// produce — but as a run-on string it reads as a log, and it never says what
// any axis was out of, so `title:30` and `function:0` look like a big number
// and a small one rather than full marks and none.
//
// The structured per-axis data (`MatchResult.signals`) does exist, but it
// does not survive to the board: `HistoryCardDTO` sets `signals={}` because
// there is no signals column on `match_history` (jobfinder/dto.py,
// jobfinder/storage/database.py), so every card the board renders has an
// empty one. Parsing the reasoning line recovers the same numbers with no
// schema migration. `signals` is still preferred whenever it is populated
// (a live `MatchCardDTO`), so this gets strictly better if that column is
// ever added.
//
// `reasoning` is NOT always that debug line: when the Claude matcher runs it
// replaces it with a 1-2 sentence prose explanation
// (`jobfinder/matcher/claude_matcher.py`). `parseReasoningPoints` returns
// `null` for anything it does not recognise, and the panel falls back to
// rendering the prose as prose — never a half-parsed mixture of the two.
import type { Signals } from "../types/job";

/** The five axes and their maximum point values — `_W_TITLE`/`_W_SKILLS`/
 * `_W_TOOLS`/`_W_SENIORITY`/`_W_FUNCTION` in
 * `jobfinder/matcher/local_matcher.py`, which sum to 100.
 *
 * Duplicated across the language boundary, so it is pinned:
 * `tests/test_score_breakdown_weights.py` parses this array and fails if it
 * stops matching the Python constants. Change one and that test names the
 * other. */
export const SCORE_AXES: ReadonlyArray<{ key: string; label: string; max: number }> = [
  { key: "title", label: "Title", max: 30 },
  { key: "skills", label: "Skills", max: 30 },
  { key: "tools", label: "Tools", max: 15 },
  { key: "seniority", label: "Seniority", max: 15 },
  { key: "function", label: "Function", max: 10 },
];

export interface ScoreAxis {
  key: string;
  label: string;
  /** Points earned on this axis. */
  points: number;
  /** Points available on this axis — what `points` is out of. */
  max: number;
  /** `points / max` as 0-100, for the meter's width. */
  pct: number;
  /** The one extra fact the matcher recorded for this axis, already
   * phrased for display (e.g. "7 from the description"), or `null`. */
  detail: string | null;
}

/** Per-axis points recovered from a local-matcher reasoning line. */
export interface ReasoningPoints {
  points: Record<string, number>;
  /** Skills found in LinkedIn's structured list, when the line says. */
  explicitSkillHits: number | null;
  /** Skills found only in the description text, when the line says. */
  descSkillHits: number | null;
  /** How many tools matched, when the line says. */
  toolHits: number | null;
}

function intOrNull(raw: string | undefined): number | null {
  if (raw === undefined) return null;
  const n = Number.parseInt(raw, 10);
  return Number.isNaN(n) ? null : n;
}

/**
 * Pull the per-axis points out of a local-matcher reasoning line, or return
 * `null` if this is not one.
 *
 * Recognition is deliberately strict — *every* axis must be present. A
 * prose sentence that happens to contain "skills:" should fall through to
 * being rendered as prose, not become a breakdown with four empty bars.
 */
export function parseReasoningPoints(reasoning: string): ReasoningPoints | null {
  if (!reasoning) return null;

  const points: Record<string, number> = {};
  for (const axis of SCORE_AXES) {
    const match = new RegExp(`\\b${axis.key}:\\s*(\\d+)`).exec(reasoning);
    if (match === null) {
      return null;
    }
    points[axis.key] = Number.parseInt(match[1], 10);
  }

  const skillSplit = /\(\s*(\d+)\s+from\s+LinkedIn\s*\+\s*(\d+)\s+from\s+desc\s*\)/i.exec(reasoning);
  const tools = /\btools:\s*\d+\s*\(\s*(\d+)\s*\)/i.exec(reasoning);

  return {
    points,
    explicitSkillHits: intOrNull(skillSplit?.[1]),
    descSkillHits: intOrNull(skillSplit?.[2]),
    toolHits: intOrNull(tools?.[1]),
  };
}

function pluralHits(n: number, noun: string): string {
  return `${n} ${noun}${n === 1 ? "" : "s"}`;
}

/** The skills axis's detail line, from however the matcher found them.
 * Omits a zero side rather than printing "0 from LinkedIn", which reads as
 * a failure when it usually just means the source has no structured list. */
function skillsDetail(parsed: ReasoningPoints | null): string | null {
  if (parsed === null) return null;
  const { explicitSkillHits: explicit, descSkillHits: desc } = parsed;
  if (explicit === null || desc === null) return null;
  const parts: string[] = [];
  if (explicit > 0) parts.push(`${explicit} from the listed skills`);
  if (desc > 0) parts.push(`${desc} from the description`);
  if (parts.length === 0) return "none found";
  return parts.join(" · ");
}

function hasAnyAxis(signals: Signals): boolean {
  if (!signals) return false;
  return SCORE_AXES.some((axis) => typeof signals[axis.key] === "number");
}

function clampPct(value: number): number {
  if (!Number.isFinite(value)) return 0;
  return Math.max(0, Math.min(100, value));
}

/**
 * The five axes with real numbers, or `null` when neither source has any.
 *
 * `signals` wins when populated — it is the matcher's own structured output
 * (0-100 per axis), so points are derived back from it rather than parsed
 * from a string. Otherwise the reasoning line is parsed. The two agree by
 * construction: `signals[k]` is `100 * points / max` in local_matcher.py.
 */
export function scoreBreakdown(card: { signals: Signals; reasoning: string }): ScoreAxis[] | null {
  const parsed = parseReasoningPoints(card.reasoning);
  const useSignals = hasAnyAxis(card.signals);
  if (!useSignals && parsed === null) {
    return null;
  }

  return SCORE_AXES.map((axis) => {
    let points: number;
    if (useSignals && typeof card.signals[axis.key] === "number") {
      points = Math.round((clampPct(card.signals[axis.key]) * axis.max) / 100);
    } else {
      points = parsed?.points[axis.key] ?? 0;
    }
    points = Math.max(0, Math.min(axis.max, points));

    let detail: string | null = null;
    if (axis.key === "skills") {
      detail = skillsDetail(parsed);
    } else if (axis.key === "tools" && parsed?.toolHits !== null && parsed?.toolHits !== undefined) {
      detail = parsed.toolHits > 0 ? pluralHits(parsed.toolHits, "tool") + " matched" : "none found";
    }

    return {
      key: axis.key,
      label: axis.label,
      points,
      max: axis.max,
      pct: axis.max === 0 ? 0 : clampPct((100 * points) / axis.max),
      detail,
    };
  });
}

/** The reasoning text to show as prose, or `null`.
 *
 * `null` precisely when `scoreBreakdown` has already turned this same
 * string into the breakdown — showing both would print the debug line under
 * its own rendered form. Claude's prose explanation has no axis numbers to
 * parse, so it always survives this and is always shown. */
export function reasoningProse(card: { reasoning: string }): string | null {
  const text = card.reasoning.trim();
  if (!text) return null;
  return parseReasoningPoints(text) === null ? text : null;
}

function joinWithOr(labels: string[]): string {
  if (labels.length === 1) return labels[0];
  if (labels.length === 2) return `${labels[0]} or ${labels[1]}`;
  return `${labels.slice(0, -1).join(", ")} or ${labels[labels.length - 1]}`;
}

/**
 * The sentence beside the score: what carried it, and what it found nothing
 * for. The shape a person would use — strongest first, then the gaps.
 *
 * It deliberately states **no total**. The previous version opened "61 of
 * 100 points" beside a heading reading "Why this scored 62", because
 * `score_job` rounds the *sum* of the five float axis values while the
 * reasoning line prints each one `int()`-truncated — so summing the parsed
 * axes lands up to a couple of points under the real score. Reconciling
 * the two would mean re-deriving the matcher's own rounding from strings it
 * already threw precision away in; not restating the total removes the
 * contradiction at the source instead, and the headline score stays the one
 * number on screen.
 */
export function breakdownSentence(axes: ScoreAxis[]): string {
  const strongest = axes.reduce((best, a) => (a.pct > best.pct ? a : best), axes[0]);
  if (strongest.points === 0) {
    return "Nothing scored on any axis.";
  }

  const lead =
    strongest.pct >= 100
      ? `Full marks on ${strongest.label.toLowerCase()}`
      : `Strongest on ${strongest.label.toLowerCase()}`;

  const missing = axes.filter((a) => a.points === 0).map((a) => a.label.toLowerCase());
  if (missing.length === 0) {
    return `${lead}.`;
  }
  return `${lead}; no ${joinWithOr(missing)} signal.`;
}
