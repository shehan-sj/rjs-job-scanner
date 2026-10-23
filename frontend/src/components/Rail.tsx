import { type ReactNode } from "react";
import { HeartbeatStrip } from "./HeartbeatStrip";
import { StopServerButton } from "./StopServerButton";
import {
  MOBILE_TAB_ITEMS,
  RAIL_DESTINATIONS,
  buildRailItems,
  isDestinationInMoreSheet,
  mobileDestinationForTab,
  type RailCounts,
  type RailDestination,
} from "./railLogic";
import "./Rail.css";

/** T64 — "88px with icons" (docs/DESIGN.md's correction: "the build
 * shipped text-only labels, so 'Sources & keys' wrapped"). Inline stroke
 * SVG, one 20x20 `currentColor` path per destination, so the icon picks
 * up the same hover/active colour the label text already does (no extra
 * CSS var) and never needs a separate asset request. Deliberately plain
 * (a handful of straight lines/shapes, not a full icon-font import) — the
 * point is a recognizable glyph above a short label, not iconography as
 * its own design system. */
const RAIL_ICONS: Record<RailDestination, ReactNode> = {
  // T81 — one icon for the collapsed Matches/Applied/Removed destination
  // five small lanes, standing in for the board's five
  // columns, rather than reusing one of the three retired per-tab icons.
  board: (
    <svg viewBox="0 0 20 20" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true">
      <rect x="2.5" y="3.5" width="2.6" height="13" rx="1" />
      <rect x="6.7" y="3.5" width="2.6" height="13" rx="1" />
      <rect x="10.9" y="3.5" width="2.6" height="13" rx="1" />
      <rect x="15.1" y="3.5" width="2.6" height="13" rx="1" />
    </svg>
  ),
  resume: (
    <svg viewBox="0 0 20 20" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true">
      <path d="M6 2.5h5.5L15 6v11.5H6z" strokeLinejoin="round" />
      <path d="M11.5 2.5V6H15" strokeLinejoin="round" />
      <path d="M8 10.5h4M8 13h4" strokeLinecap="round" />
    </svg>
  ),
  sources: (
    <svg viewBox="0 0 20 20" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true">
      <circle cx="7" cy="13" r="3.25" />
      <path d="M9.3 10.7 15.5 4.5" strokeLinecap="round" />
      <path d="M13 7l2 2M15 5l2 2" strokeLinecap="round" />
    </svg>
  ),
};

/** T60 — the single navigation model (docs/DESIGN.md principle 4 /
 * The product spec). Replaces three stacked systems that used to
 * live at the App.tsx level: the `app-shell-triggers` floating strip
 * (FillerBot/Settings/Stop), the nine-tab `SettingsView` modal, and
 * `LogDrawer`'s separate drawer. Every control here is a real
 * `<button>`/`<a>` so Tab order and `aria-label`s make the shell
 * operable without a mouse (this task's keyboard-focus DoD line).
 *
 * `RAIL_DESTINATIONS`/`buildRailItems` (railLogic.ts) hold the data this
 * renders — this component is deliberately thin so the nav *model* is
 * unit-testable (tests/railLogic.test.ts) independent of React/DOM.
 *
 * Two regions, not one — same shape docs/DESIGN.md decided on: this is
 * the rail ("where am I"); `FilterPanel` (JobsView.tsx, T59) is the
 * separate, collapsible "what am I filtering" panel. Matches' three
 * location tiers are intentionally *not* rendered here — they are chips
 * above the list (JobsView.tsx) so the rail stays one level deep.
 *
 * On a phone (`@media (max-width: 640px)`, Rail.css) this same markup is
 * restyled into a four-item bottom tab bar via CSS alone — no separate
 * mobile component, so there is exactly one source of truth for "what
 * are the destinations" in both layouts. The desktop top rail list is
 * hidden and `.rail-mobile-bar`/`.rail-more-sheet` take over; see Rail.css.
 */
export function Rail({
  active,
  onNavigate,
  counts,
  moreOpen,
  onToggleMore,
  onToggleLog,
  onOpenFillerBot,
}: {
  active: RailDestination;
  onNavigate: (destination: RailDestination) => void;
  counts: RailCounts;
  moreOpen: boolean;
  onToggleMore: () => void;
  onToggleLog: () => void;
  onOpenFillerBot: () => void;
}): ReactNode {
  const items = buildRailItems(counts);
  const moreDestinations = RAIL_DESTINATIONS.filter((d) => isDestinationInMoreSheet(d.key));
  const moreIsActive = isDestinationInMoreSheet(active);

  const footControls = (
    <div className="rail-foot">
      <HeartbeatStrip variant="compact" />
      <button type="button" className="rail-foot-btn" aria-label="Toggle activity log" onClick={onToggleLog}>
        Log
      </button>
      <button type="button" className="rail-foot-btn" aria-label="Open FillerBot" onClick={onOpenFillerBot}>
        FillerBot
      </button>
      {/* The product spec's explicit stop — lives at the rail foot next to
          the heartbeat that tells the user closing the tab does not
          stop scanning, because this is the control that does. */}
      <StopServerButton />
    </div>
  );

  return (
    <>
      <nav className="rail rail-desktop" aria-label="Main">
        <ul className="rail-list">
          {items.map((item) => (
            <li key={item.key}>
              <button
                type="button"
                className={`rail-btn ${item.key === active ? "active" : ""}`}
                aria-label={item.count !== null ? `${item.label}, ${item.count}` : item.label}
                aria-current={item.key === active ? "page" : undefined}
                onClick={() => onNavigate(item.key)}
              >
                <span className="rail-btn-icon">{RAIL_ICONS[item.key]}</span>
                <span className="rail-btn-label">{item.label}</span>
                {item.count !== null && item.count > 0 ? (
                  <span className="rail-btn-count">{item.count}</span>
                ) : null}
              </button>
            </li>
          ))}
        </ul>
        {footControls}
      </nav>

      <nav className="rail rail-mobile-bar" aria-label="Main">
        {MOBILE_TAB_ITEMS.map((tab) => {
          const destination = mobileDestinationForTab(tab.key);
          const isActive = tab.key === "more" ? moreIsActive || moreOpen : destination === active;
          return (
            <button
              key={tab.key}
              type="button"
              className={`rail-mobile-btn ${isActive ? "active" : ""}`}
              aria-label={tab.label}
              aria-current={isActive && tab.key !== "more" ? "page" : undefined}
              aria-expanded={tab.key === "more" ? moreOpen : undefined}
              onClick={() => (destination ? onNavigate(destination) : onToggleMore())}
            >
              {tab.label}
            </button>
          );
        })}
      </nav>

      {moreOpen ? (
        <div className="rail-more-sheet" role="dialog" aria-label="More">
          <ul className="rail-list">
            {moreDestinations.map((d) => (
              <li key={d.key}>
                <button
                  type="button"
                  className={`rail-btn ${d.key === active ? "active" : ""}`}
                  aria-current={d.key === active ? "page" : undefined}
                  onClick={() => onNavigate(d.key)}
                >
                  {d.label}
                </button>
              </li>
            ))}
          </ul>
          {footControls}
        </div>
      ) : null}
    </>
  );
}
