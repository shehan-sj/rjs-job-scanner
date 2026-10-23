// T81 — the board card's score chip uses the four-step fit ramp
// (`--jf-fit-0..3-{bg,fg}`, tokens.css), not the
// `--jf-score-*` status ramp `ScoreBadge.tsx` already uses elsewhere on
// screen. Each step owns both its background *and* its foreground, so
// the top step's inversion (dark text on a light fill in dark mode,
// tokens.css's own comment on `--jf-fit-3-*`) is safe — nothing here
// inherits an ambient text colour the way a plain `color: var(--jf-text)`
// consumer of the ramp would.
//
// Pure function, no CSS/DOM here — `BoardCard.tsx` is the thin render of
// this, same split jobBatching.ts/railLogic.ts already use.
import type { Stage } from "../types/job";

export type FitStep = 0 | 1 | 2 | 3;

/** 90+ -> 3, 80-89 -> 2, 70-79 -> 1, under 70 -> 0, and -- per the brief --
 * always 0 in Not for me (`stage === "out"`), regardless of the job's
 * actual score: a dismissed job's fit is no longer the headline number. */
export function fitStep(score: number, stage: Stage): FitStep {
  if (stage === "out") return 0;
  if (score >= 90) return 3;
  if (score >= 80) return 2;
  if (score >= 70) return 1;
  return 0;
}

export function fitBgVar(step: FitStep): string {
  return `--jf-fit-${step}-bg`;
}

export function fitFgVar(step: FitStep): string {
  return `--jf-fit-${step}-fg`;
}
