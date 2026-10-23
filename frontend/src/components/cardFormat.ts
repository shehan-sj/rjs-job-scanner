// Text-formatting helpers ported from `jobfinder/ui/widget.py` (T24):
// `_time_ago`, `_score_color`/`_score_label`, `_format_salary`,
// `_company_meta_line`, `_why_matched_line`. Pure functions, no DOM/Qt —
// kept separate from JobCardView.tsx so they're easy to reason about and
// pin against the Python originals.
import type { JobCardBase, Signals } from "../types/job";

/** Port of `widget.py`'s `_time_ago`. v3 stores `applied_at`/`removed_at`
 * as naive `datetime.utcnow().isoformat()` strings (no timezone suffix);
 * `Date.parse` treats a date-time string with no zone as *local* time per
 * ECMA-262, which would silently reinterpret a naive-UTC timestamp — so
 * an explicit "Z" is appended when the string carries no zone info,
 * matching Python's naive-UTC-vs-naive-UTC comparison. */
export function timeAgo(isoStr: string | null | undefined): string {
  if (!isoStr) {
    return "";
  }
  const hasZone = /[Zz]|[+-]\d{2}:?\d{2}$/.test(isoStr);
  const t = Date.parse(hasZone ? isoStr : `${isoStr}Z`);
  if (Number.isNaN(t)) {
    return "";
  }
  const deltaMs = Date.now() - t;
  const hours = Math.floor(deltaMs / 3_600_000);
  if (hours < 1) {
    return "just now";
  }
  if (hours < 24) {
    return `${hours}h ago`;
  }
  return `${Math.floor(hours / 24)}d ago`;
}

/** Score-tier CSS custom property name, port of `_score_color`'s
 * thresholds (>=70 strong, >=50 good, >=35 fair, else weak). */
export function scoreColorVar(score: number): string {
  if (score >= 70) return "--jf-score-strong";
  if (score >= 50) return "--jf-score-good";
  if (score >= 35) return "--jf-score-fair";
  return "--jf-score-weak";
}

/** Port of `_score_label`. */
export function scoreLabel(score: number): string {
  if (score >= 70) return "Strong";
  if (score >= 50) return "Good";
  if (score >= 35) return "Fair";
  return "Weak";
}

const SALARY_SYMBOLS: Record<string, string> = { USD: "$", CAD: "$", GBP: "£", EUR: "€" };
const SALARY_SUFFIXES: Record<string, string> = {
  yearly: "/yr",
  hourly: "/hr",
  monthly: "/mo",
  weekly: "/wk",
};

function fmtAmount(v: number, symbol: string): string {
  const rounded = Math.round(v);
  return rounded >= 1000 ? `${symbol}${(rounded / 1000).toFixed(0)}k` : `${symbol}${rounded}`;
}

/** Port of `_format_salary`. Returns `null` when there is no compensation
 * data at all, same as the Python original. */
export function formatSalary(card: JobCardBase): string | null {
  const mn = card.compensation_min;
  const mx = card.compensation_max;
  if (!mn && !mx) {
    return null;
  }
  const currency = (card.compensation_currency || "USD").toUpperCase();
  const interval = (card.compensation_interval || "yearly").toLowerCase();
  const symbol = SALARY_SYMBOLS[currency] ?? "";

  let amount: string;
  if (mn && mx && mn !== mx) {
    amount = `${fmtAmount(mn, symbol)} – ${fmtAmount(mx, symbol)}`;
  } else {
    amount = fmtAmount((mn || mx) as number, symbol);
  }
  if (!symbol) {
    amount = `${amount} ${currency}`;
  }
  const suffix = SALARY_SUFFIXES[interval] ?? "";
  return `\u{1F4B0}  ${amount}${suffix}`;
}

// Display names for known job sources — shared by MetaChips' source chip
// and T58's batch headers (`Today 09:12 · 14 found · LinkedIn, Indeed`),
// so there is exactly one place a source key maps to a human label.
export const SOURCE_LABELS: Record<string, [string, string]> = {
  linkedin: ["LinkedIn", "Scraped from ca.linkedin.com — best for Canadian jobs with rich detail"],
  indeed: ["Indeed", "Scraped from ca.indeed.com — large Canadian job board"],
  remotive: ["Remotive", "Free API — remote-only jobs worldwide, small board"],
  arbeitnow: ["Arbeitnow", "Free API — primarily European & remote jobs"],
};

export function titleCase(s: string): string {
  return s.length ? s[0].toUpperCase() + s.slice(1) : s;
}

/** The short display label for a source key, e.g. `"linkedin"` -> `"LinkedIn"`.
 * Falls back to a title-cased version of the raw key for an unknown source
 * rather than hiding it. */
export function sourceLabel(source: string): string {
  const trimmed = source.trim();
  if (!trimmed) return "";
  return SOURCE_LABELS[trimmed]?.[0] ?? titleCase(trimmed);
}

/** Port of `_company_meta_line` — company / location / size / industry /
 * posted-date, joined the same way `TabBar.tsx` already joins label and
 * count (`"  ·  "`, T23's established separator convention). */
export function companyMetaLine(card: JobCardBase): string | null {
  const parts: string[] = [];
  if (card.company.trim()) parts.push(card.company.trim());
  if (card.location.trim()) parts.push(card.location.trim());
  if (card.company_num_employees.trim()) parts.push(`${card.company_num_employees.trim()} employees`);
  if (card.company_industry.trim()) parts.push(card.company_industry.trim());
  if (card.date_posted) {
    const ago = timeAgo(card.date_posted);
    if (ago) parts.push(`Posted ${ago}`);
  }
  return parts.length ? parts.join("  ·  ") : null;
}

const WHY_MATCHED_LABELS: Record<string, string> = {
  title: "title",
  skills: "skills",
  tools: "tools",
  seniority: "seniority",
  function: "function",
};

/** Port of `_why_matched_line` — compact "✓ skills  ·  ~ seniority  ·  ✗
 * tools" breakdown. Iterates in the same key order as the Python dict
 * literal (`title, skills, tools, seniority, function`). */
export function whyMatchedLine(signals: Signals): string | null {
  if (!signals || Object.keys(signals).length === 0) {
    return null;
  }
  const bits: string[] = [];
  for (const [key, label] of Object.entries(WHY_MATCHED_LABELS)) {
    const v = signals[key];
    if (v === undefined || v === null) continue;
    if (v >= 70) bits.push(`✓ ${label}`);
    else if (v >= 40) bits.push(`~ ${label}`);
    else bits.push(`✗ ${label}`);
  }
  return bits.length ? bits.join("  ·  ") : null;
}

/** T64 — the 580px list row's one meta line (docs/DESIGN.md's split
 * master-detail list: "title, company · location · arrangement"),
 * deliberately shorter than `companyMetaLine` above (which also carries
 * company size/industry/posted-date — that fuller line is what the
 * detail pane's facts grid is for, not the scannable list). An "Unknown"
 * arrangement is the DTO's own placeholder for "the scraper didn't say",
 * same convention `MetaChips.tsx` already applies — printed nowhere, not
 * even here. */
export function rowMetaLine(card: JobCardBase): string | null {
  const parts: string[] = [];
  if (card.company.trim()) parts.push(card.company.trim());
  if (card.location.trim()) parts.push(card.location.trim());
  if (card.work_arrangement && card.work_arrangement !== "Unknown") {
    parts.push(card.work_arrangement);
  }
  return parts.length ? parts.join("  ·  ") : null;
}

export interface FactItem {
  label: string;
  value: string;
}

/** T64 — the detail pane's structured facts grid (docs/DESIGN.md: "a
 * facts grid — seniority, function, posted, source, company size,
 * compensation"). Every row always renders, with a stated blank
 * ("Not listed") rather than being omitted when the field is empty — the
 * same "absent shows as a stated blank, never a guess" convention
 * The product spec's onboarding provenance work established, applied here to scan
 * data instead of resume data. */
export function buildFactsGrid(card: JobCardBase): FactItem[] {
  const posted = card.date_posted ? timeAgo(card.date_posted) : "";
  const source = sourceLabel(card.source);
  const companySize = card.company_num_employees.trim();
  return [
    { label: "Seniority", value: card.job_level.trim() || "Not listed" },
    { label: "Function", value: card.job_function.trim() || "Not listed" },
    { label: "Posted", value: posted || "Not listed" },
    { label: "Source", value: source || "Not listed" },
    { label: "Company size", value: companySize ? `${companySize} employees` : "Not listed" },
    { label: "Compensation", value: formatSalary(card) ?? "Not listed" },
  ];
}

/** T64 — the detail pane's "gaps" list: the same per-signal scoring
 * breakdown `whyMatchedLine` already reads (title/skills/tools/
 * seniority/function, each 0-100), but split so the ✓ 70+ signals stay
 * implicit (the matched-skills chips already show what matched) and only
 * the weaker ones surface here as what the match reasoning found
 * lacking. Reuses `whyMatchedLine`'s >=70 threshold for "not a gap" so
 * the two views of the same scoring data never disagree with each other
 * about where that line sits. */
export function deriveGaps(signals: Signals): string[] {
  if (!signals) return [];
  const gaps: string[] = [];
  for (const [key, label] of Object.entries(WHY_MATCHED_LABELS)) {
    const v = signals[key];
    if (v === undefined || v === null) continue;
    if (v < 70) gaps.push(label);
  }
  return gaps;
}
