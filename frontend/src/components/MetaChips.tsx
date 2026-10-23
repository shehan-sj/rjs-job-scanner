import type { ReactNode } from "react";
import type { JobCardBase } from "../types/job";
import { formatSalary, SOURCE_LABELS, titleCase } from "./cardFormat";
import "./Chips.css";

interface ChipSpec {
  text: string;
  bgVar: string;
  fgVar: string;
  tooltip?: string;
}

/** Port of `widget.py`'s `_build_meta_chips` — source, work arrangement,
 * employment type, seniority, function, salary, "Promoted", "✓ Verified"
 * (`agent_verified`), and a geo-warning chip, in that order. Flows across
 * rows via CSS flex-wrap instead of widget.py's manual row-width
 * accounting (Qt had no flex-wrap equivalent; the web idiom replaces it
 * without changing which chips appear — the product spec). */
export function MetaChips({ card }: { card: JobCardBase }): ReactNode {
  const chips: ChipSpec[] = [];

  const source = card.source.trim();
  if (source) {
    const info = SOURCE_LABELS[source];
    chips.push({
      text: info ? info[0] : titleCase(source),
      bgVar: "--jf-tag-hybrid-bg",
      fgVar: "--jf-tag-hybrid-fg",
      tooltip: info ? info[1] : undefined,
    });
  }

  const arr = card.work_arrangement;
  if (arr && arr !== "Unknown") {
    if (arr === "Remote") {
      chips.push({ text: arr, bgVar: "--jf-tag-remote-bg", fgVar: "--jf-tag-remote-fg" });
    } else if (arr === "Hybrid") {
      chips.push({ text: arr, bgVar: "--jf-tag-hybrid-bg", fgVar: "--jf-tag-hybrid-fg" });
    } else {
      chips.push({ text: arr, bgVar: "--jf-tag-onsite-bg", fgVar: "--jf-tag-onsite-fg" });
    }
  }

  const emp = card.employment_type;
  if (emp && emp !== "Unknown") {
    chips.push({ text: emp, bgVar: "--jf-tag-skill-bg", fgVar: "--jf-tag-skill-fg" });
  }

  const seniority = card.job_level.trim();
  if (seniority) {
    chips.push({ text: seniority, bgVar: "--jf-tag-onsite-bg", fgVar: "--jf-tag-onsite-fg" });
  }

  const func = card.job_function.trim();
  if (func) {
    chips.push({ text: func, bgVar: "--jf-tag-skill-bg", fgVar: "--jf-tag-skill-fg" });
  }

  const salary = formatSalary(card);
  if (salary) {
    chips.push({ text: salary, bgVar: "--jf-tag-remote-bg", fgVar: "--jf-tag-remote-fg" });
  }

  if (card.listing_type.trim().toLowerCase() === "promoted") {
    chips.push({ text: "Promoted", bgVar: "--jf-tag-hybrid-bg", fgVar: "--jf-tag-hybrid-fg" });
  }

  if (card.agent_verified) {
    chips.push({ text: "✓ Verified", bgVar: "--jf-tag-remote-bg", fgVar: "--jf-tag-remote-fg" });
  }

  if (card.geo_warning) {
    chips.push({
      text: `⚠ ${card.geo_warning}`,
      bgVar: "--jf-tag-warn-bg",
      fgVar: "--jf-tag-warn-fg",
    });
  }

  if (chips.length === 0) {
    return null;
  }

  return (
    <div className="chip-row">
      {chips.map((chip, idx) => (
        <span
          key={`${chip.text}-${idx}`}
          className="chip"
          title={chip.tooltip}
          style={{ background: `var(${chip.bgVar})`, color: `var(${chip.fgVar})` }}
        >
          {chip.text}
        </span>
      ))}
    </div>
  );
}
