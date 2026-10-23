import type { ReactNode } from "react";
import "./TabBar.css";

export interface TabItem<K extends string> {
  key: K;
  label: string;
  count: number;
}

// Ports `jobfinder/ui/widget.py`'s `_build_tabs`/`_build_subtabs` label
// format (`"Matches  ·  N"` when N > 0, else just `"Matches"`) and active
// styling (T23). Used for both the main Matches/Applied/Removed bar and
// the Preferred/Same-country/Global-remote and Removed-by-you/-AI
// sub-tab bars.
export function TabBar<K extends string>({
  items,
  active,
  onSelect,
  variant = "main",
}: {
  items: TabItem<K>[];
  active: K;
  onSelect: (key: K) => void;
  variant?: "main" | "sub";
}): ReactNode {
  return (
    <div className={`tab-bar tab-bar-${variant}`} role="tablist">
      {items.map((item) => (
        <button
          key={item.key}
          type="button"
          role="tab"
          aria-selected={item.key === active}
          className={`tab-btn tab-btn-${variant} ${item.key === active ? "active" : ""}`}
          onClick={() => onSelect(item.key)}
        >
          {item.count > 0 ? `${item.label}  ·  ${item.count}` : item.label}
        </button>
      ))}
    </div>
  );
}
