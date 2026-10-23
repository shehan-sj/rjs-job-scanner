import type { ReactNode } from "react";
import "./Chips.css";

/** Port of `widget.py`'s `_build_skill_chips`. `card.skills` arrives
 * already truncated to 8 items by the server (`jobfinder/dto.py`) and v3
 * never added a "+N" indicator on top of that truncation, so this
 * renders the list as-is. */
export function SkillChips({ skills }: { skills: string[] }): ReactNode {
  if (skills.length === 0) {
    return null;
  }
  return (
    <div className="chip-row">
      {skills.map((skill, idx) => (
        <span
          key={`${skill}-${idx}`}
          className="chip"
          style={{ background: "var(--jf-tag-skill-bg)", color: "var(--jf-tag-skill-fg)" }}
        >
          {skill}
        </span>
      ))}
    </div>
  );
}
