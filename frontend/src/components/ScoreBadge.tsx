import { useMemo, type ReactNode } from "react";
import { bestFgFor } from "../theme/theme";
import { scoreColorVar, scoreLabel } from "./cardFormat";
import "./ScoreBadge.css";

/** Port of `widget.py`'s `_score_badge` — a circular badge whose
 * background is the score-tier colour (`--jf-score-*`, one of four) and
 * whose text colour is picked by WCAG luminance contrast against that
 * *resolved* colour, not a static token, because the tier colours differ
 * between the light and dark theme (tokens.css) and a single hardcoded
 * foreground would fail contrast on one of them. */
export function ScoreBadge({ score }: { score: number }): ReactNode {
  const bgVar = scoreColorVar(score);
  const fg = useMemo(() => {
    if (typeof document === "undefined") {
      return "#ffffff";
    }
    const hex = getComputedStyle(document.documentElement).getPropertyValue(bgVar).trim();
    return hex ? bestFgFor(hex) : "#ffffff";
  }, [bgVar]);

  return (
    <span
      className="job-score-badge"
      style={{ background: `var(${bgVar})`, color: fg }}
      title={`${scoreLabel(score)} match (${score}%)`}
    >
      {score}
    </span>
  );
}
