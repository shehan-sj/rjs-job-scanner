// Card shapes for the Matches / Applied / Removed views (T23/T24).
//
// `GET /api/matches` / `/api/applied` / `/api/removed` have no declared
// FastAPI `response_model` (jobfinder/web/jobs.py returns plain `list[dict]`),
// so `npm run gen:api` can only produce `Record<string, unknown>[]` for
// their responses — a real generated type, just not a narrow one (same
// situation `client.ts` already documents for `GET /api/settings`).
// `JobCard` below is this view's local narrowing of that payload, derived
// from `jobfinder/dto.py`'s `MatchCardDTO`/`HistoryCardDTO` (the actual
// server-side contract) rather than hand-guessed.
//
// `agent_verified` is present on a live-scan `MatchCardDTO` but **absent**
// on a `HistoryCardDTO` (dto.py's module docstring: a history row has no
// such column, so the key is never persisted — tracked as T41, deferred).
// Every history-shaped card (Matches, which reads `get_recent_matches()`
// rows, as well as Applied/Removed) must therefore treat it as optional
// and never assume its presence.
//
// `skills` already arrives truncated to 8 items from the server
// (`dto.py`'s `MatchCardDTO.skills`/`HistoryCardDTO.skills`, both sliced
// `[:8]`) — there is no client-side "+N" indicator because v3's
// `widget.py` never had one either (`_build_skill_chips` renders whatever
// list it's handed, full stop). `matched_skills` is part of the DTO but
// `widget.py` never rendered it, so it is not surfaced here either.

export type MatchTier = "preferred" | "country" | "global";
export type RemovedBy = "user" | "agent";

/** Per-signal match breakdown (`MatchResult.signals`) — keys are a subset
 * of title/skills/tools/seniority/function, values are 0-100. Absent/empty
 * on history rows (`HistoryCardDTO.signals` is always `{}`). */
export type Signals = Record<string, number>;

/** Fields present on every card, regardless of tab — the full
 * `MatchCardDTO`/`HistoryCardDTO` shape minus the per-tab extras
 * (`tier`/`applied_at`/`removed_at`/`removed_by`) layered on below. */
export interface JobCardBase {
  job_id: string;
  title: string;
  company: string;
  location: string;
  score: number;
  source: string;
  url: string;
  /** Employer's direct ATS link, when LinkedIn exposes one. */
  url_direct: string;
  skills: string[];
  reasoning: string;
  signals: Signals;
  geo_warning: string | null;
  work_arrangement: string;
  employment_type: string;
  job_level: string;
  job_function: string;
  listing_type: string;
  company_industry: string;
  company_num_employees: string;
  compensation_min: number | null;
  compensation_max: number | null;
  compensation_currency: string;
  compensation_interval: string;
  date_posted: string;
  /** When this job was found — ISO-8601, naive UTC (`jobfinder/dto.py`'s
   * module docstring pins the timezone contract: no offset means UTC, not
   * local time). Not itself a batch key — batching ("which jobs were
   * found by the same scan") is a server-side `scan_id` decision
   * (`jobfinder/storage/database.py`), not derived by clustering this
   * field client-side (T57). */
  matched_at: string;
  /** Which scan found this job (T58) — the actual batch key `matched_at`
   * above is not. `null` for a pre-T57 row, which must stay a singleton
   * batch rather than merging with anything (see `jobBatching.ts`'s
   * `groupCardsByScan`). */
  scan_id: string | null;
  /** The employer's own site, when the source scraper captured one
   * (`jobfinder/dto.py`'s `company_url`) — the parity matrix's card-actions row
   * calls this "company site"; it was in the DTO contract already but
   * had never been carried into this file's narrowing of it (T58). */
  company_url: string;
  /** Absent on history-shaped rows — see module docstring. */
  agent_verified?: boolean | null;
  /** The job's city, distinct from `location`'s fuller free-text string —
   * `jobfinder/dto.py`'s `city` was already part of both DTOs but had
   * never been narrowed into this file (T83's panel heading line:
   * "company · city · arrangement · listing type · ..."). */
  city: string;
  /** The subset of `skills` actually found against the user's résumé
   * (`jobfinder/dto.py`'s `matched_skills`, `MatchResult.skills_matched`
   * truncated to 12) — distinct from `skills`, which is the job's own
   * listed/required skills (preferring LinkedIn's structured list) and so
   * is not itself evidence of a match. T83's panel is the first consumer:
   * `skills` minus `matched_skills` is "asked for, not in your résumé". */
  matched_skills: string[];
}

/** `GET /api/matches` — carries the server-computed tier (jobfinder/web/jobs.py). */
export interface MatchCard extends JobCardBase {
  tier: MatchTier;
}

/** `GET /api/applied`. */
export interface AppliedCard extends JobCardBase {
  applied_at: string;
}

/** `GET /api/removed`. */
export interface RemovedCard extends JobCardBase {
  removed_at: string;
  removed_by: RemovedBy;
}

function asRecord(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null
    ? (value as Record<string, unknown>)
    : {};
}

function str(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

function num(value: unknown, fallback = 0): number {
  return typeof value === "number" ? value : fallback;
}

function optNum(value: unknown): number | null {
  return typeof value === "number" ? value : null;
}

function optBool(value: unknown): boolean | null {
  return typeof value === "boolean" ? value : null;
}

function strList(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((v): v is string => typeof v === "string") : [];
}

function signals(value: unknown): Signals {
  if (typeof value !== "object" || value === null) {
    return {};
  }
  const out: Signals = {};
  for (const [k, v] of Object.entries(value as Record<string, unknown>)) {
    if (typeof v === "number") {
      out[k] = v;
    }
  }
  return out;
}

function base(raw: Record<string, unknown>): JobCardBase {
  return {
    job_id: str(raw.job_id),
    title: str(raw.title),
    company: str(raw.company),
    location: str(raw.location),
    score: num(raw.score),
    source: str(raw.source),
    url: str(raw.url),
    url_direct: str(raw.url_direct),
    skills: strList(raw.skills),
    reasoning: str(raw.reasoning),
    signals: signals(raw.signals),
    geo_warning: typeof raw.geo_warning === "string" ? raw.geo_warning : null,
    work_arrangement: str(raw.work_arrangement, "Unknown"),
    employment_type: str(raw.employment_type, "Unknown"),
    job_level: str(raw.job_level),
    job_function: str(raw.job_function),
    listing_type: str(raw.listing_type),
    company_industry: str(raw.company_industry),
    company_num_employees: str(raw.company_num_employees),
    compensation_min: optNum(raw.compensation_min),
    compensation_max: optNum(raw.compensation_max),
    compensation_currency: str(raw.compensation_currency),
    compensation_interval: str(raw.compensation_interval),
    date_posted: str(raw.date_posted),
    matched_at: str(raw.matched_at),
    scan_id: typeof raw.scan_id === "string" ? raw.scan_id : null,
    company_url: str(raw.company_url),
    agent_verified: optBool(raw.agent_verified),
    city: str(raw.city),
    matched_skills: strList(raw.matched_skills),
  };
}

/** Narrows one untyped `/api/matches` row, defaulting an unrecognized
 * `tier` value to `"global"` rather than throwing — a forward-compatible
 * tier the client doesn't know about should not break the whole view. */
export function toMatchCard(value: unknown): MatchCard {
  const raw = asRecord(value);
  const tierRaw = raw.tier;
  const tier: MatchTier =
    tierRaw === "preferred" || tierRaw === "country" || tierRaw === "global"
      ? tierRaw
      : "global";
  return { ...base(raw), tier };
}

export function toAppliedCard(value: unknown): AppliedCard {
  const raw = asRecord(value);
  return { ...base(raw), applied_at: str(raw.applied_at) };
}

export function toRemovedCard(value: unknown): RemovedCard {
  const raw = asRecord(value);
  const removedBy: RemovedBy = raw.removed_by === "agent" ? "agent" : "user";
  return { ...base(raw), removed_at: str(raw.removed_at), removed_by: removedBy };
}

// ── T81: the board (GET /api/board) — five pipeline stages, one payload
// Replaces the three separate Matches/Applied/Removed
// shapes above as the one live view's card type; `MatchCard`/`AppliedCard`/
// `RemovedCard` and their fetchers stay in `api/jobs.ts` only because the
// four legacy endpoints they read are still thin wrappers T80 kept for
// backward compatibility, not because anything in the UI still renders
// them (BoardView.tsx is the only live consumer of jobs now).

/** The five pipeline stages, the product spec, in column order. */
export const STAGES = ["new", "shortlist", "applied", "replied", "out"] as const;
export type Stage = (typeof STAGES)[number];

/** `GET /api/board` — every `JobCardBase` field plus the board-specific
 * additions `jobfinder/web/jobs.py`'s `_board_card` adds (T80/T81). */
export interface BoardCard extends JobCardBase {
  stage: Stage;
  /** ISO-8601 naive-UTC timestamp for when this card entered its current
   * stage, or `null` for `stage === "new"` (never wrote to a stage table). */
  stage_entered_at: string | null;
  /** Who dismissed the card ("user" | "agent"), only set for `stage === "out"`. */
  removed_by: RemovedBy | null;
  /** The free-text note attached to a reply, only set for `stage === "replied"`. */
  reply_note: string | null;
  tier: MatchTier;
  /** T84/T81: whether this card's location had content but no country
   * could be recognized from it — see `jobfinder/filters.py`'s
   * `tier_location_is_unresolved`. A companion signal to `tier === "global"`,
   * not a fourth tier. */
  tier_location_is_unresolved: boolean;
  /** T84/T81: whether the "same country" tier is reachable at all for the
   * current config (`jobfinder/filters.py`'s `country_tier_is_configured`).
   * Carried per-card (same value on every card in a given response) rather
   * than as a new top-level `/api/board` key, so the tier filter can tell
   * "zero same-country matches" apart from "same country isn't set up". */
  same_country_tier_configured: boolean;
  /** T101: whether this job is starred, independent of `stage` — a star
   * is not a stage, so it can be `true` in any column and a stage move
   * never changes it (`jobfinder/web/jobs.py`'s `_board_card`). */
  starred: boolean;
}

/** `GET /api/board`'s response shape: one array per stage. */
export type BoardPayload = Record<Stage, BoardCard[]>;

function asStage(value: unknown): Stage {
  return (STAGES as readonly string[]).includes(value as string) ? (value as Stage) : "new";
}

export function toBoardCard(value: unknown): BoardCard {
  const raw = asRecord(value);
  const tierRaw = raw.tier;
  const tier: MatchTier =
    tierRaw === "preferred" || tierRaw === "country" || tierRaw === "global" ? tierRaw : "global";
  return {
    ...base(raw),
    stage: asStage(raw.stage),
    stage_entered_at: typeof raw.stage_entered_at === "string" ? raw.stage_entered_at : null,
    removed_by: raw.removed_by === "agent" ? "agent" : raw.removed_by === "user" ? "user" : null,
    reply_note: typeof raw.reply_note === "string" ? raw.reply_note : null,
    tier,
    tier_location_is_unresolved: raw.tier_location_is_unresolved === true,
    same_country_tier_configured: raw.same_country_tier_configured === true,
    starred: raw.starred === true,
  };
}

export function toBoardPayload(value: unknown): BoardPayload {
  const raw = asRecord(value);
  const out: Partial<BoardPayload> = {};
  for (const stage of STAGES) {
    const arr = raw[stage];
    out[stage] = Array.isArray(arr) ? arr.map(toBoardCard) : [];
  }
  return out as BoardPayload;
}
