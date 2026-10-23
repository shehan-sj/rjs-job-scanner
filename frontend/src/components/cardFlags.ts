// T81 — the board card's foot-line flags (the product spec brief: "the
// card's flags are derived from dates, never typed"). Pure function, no
// React/DOM — exercised directly by `tests/cardFlags.test.ts`, same split
// jobBatching.ts/railLogic.ts already use so this is unit-testable with
// `node --test`.
//
// Only flags with real data behind them are implemented here. The
// mockup also shows "Closes in 2 days" and "Posting gone" — there is no
// `closes_at` column and nothing checks whether a posting still resolves,
// so neither is faked from `date_posted`; that call is left to the owner.
import type { BoardCard } from "../types/job";

export type FlagTone = "warn" | "neutral";

export interface CardFlag {
  key: string;
  label: string;
  tone: FlagTone;
}

const DAY_MS = 24 * 60 * 60 * 1000;

/** Naive-UTC day-delta, same zone convention `cardFormat.ts`'s `timeAgo`
 * and `jobBatching.ts`'s `parseNaiveUtc` already use: a timestamp with no
 * zone suffix is treated as UTC, not local time, because that's how the
 * server wrote it (`datetime.utcnow().isoformat()`). `null` when `iso` is
 * missing or unparseable. */
function daysAgo(iso: string | null, now: Date): number | null {
  if (!iso) return null;
  const hasZone = /[Zz]|[+-]\d{2}:?\d{2}$/.test(iso);
  const t = Date.parse(hasZone ? iso : `${iso}Z`);
  if (Number.isNaN(t)) return null;
  return (now.getTime() - t) / DAY_MS;
}

/** Priority order, verbatim from the brief — at most two flags, the
 * two highest-priority in this order when more would otherwise apply.
 * "Worth a nudge" (>=7d applied) and "Gone quiet" (>=14d applied) are
 * both about the same applied-aging axis; a card that clears 14 days
 * necessarily also clears 7, so only the more specific (`elif`) one of
 * the pair is ever produced for a given card, not both. */
export function cardFlags(card: BoardCard, now: Date = new Date()): CardFlag[] {
  const flags: CardFlag[] = [];
  const stageAge = daysAgo(card.stage_entered_at, now);

  if (card.stage === "applied" && stageAge !== null) {
    if (stageAge >= 14) {
      flags.push({ key: "gone-quiet", label: "Gone quiet", tone: "neutral" });
    } else if (stageAge >= 7) {
      flags.push({ key: "worth-a-nudge", label: "Worth a nudge", tone: "warn" });
    }
  }

  if (card.stage === "shortlist" && stageAge !== null && stageAge >= 5) {
    flags.push({ key: "sitting-a-while", label: "Sitting a while", tone: "warn" });
  }

  if (card.employment_type.trim().toLowerCase() === "contract") {
    flags.push({ key: "contract", label: "Contract", tone: "warn" });
  }

  if (!card.compensation_min && !card.compensation_max) {
    flags.push({ key: "pay-not-stated", label: "Pay not stated", tone: "neutral" });
  }

  if (card.stage === "out" && card.removed_by === "agent") {
    flags.push({ key: "ai-filter", label: "Dismissed by the AI filter", tone: "neutral" });
  }

  if (card.tier_location_is_unresolved) {
    flags.push({ key: "location-unresolved", label: "Location could not be parsed", tone: "neutral" });
  }

  return flags.slice(0, 2);
}
