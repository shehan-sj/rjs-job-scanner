import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { fetchFullSettings, putSettingsPatch, type SettingsPayload } from "../api/settings";
import { BOARD_FILTER_CONTROL_TEST_IDS } from "../filters/boardFilterControls";
import { createDebouncedPatchQueue, type DebouncedPatchQueue } from "../filters/debouncedPatchQueue";
import {
  buildQualityFilterPatch,
  buildTagVisibilityPatch,
  extractFilterPanelValues,
  isEmptyFilterPatch,
  mergeFilterPatch,
  sourcesWithUniformHours,
  type Arrangement,
  type FilterPanelValues,
  type FilterPatch,
} from "../filters/filterPanelLogic";
import { SliderRow } from "./SliderRow";
import "./FilterPanel.css";

const ARRANGEMENTS: { key: Arrangement; label: string }[] = [
  { key: "any", label: "Any" },
  { key: "remote", label: "Remote" },
  { key: "hybrid", label: "Hybrid" },
];

// Debounce delay for the queued PUT /api/settings write (the task ledger:
// "Debounce writes"). Long enough that dragging a slider across its
// whole range doesn't fire a write per tick, short enough that letting
// go feels immediate.
const WRITE_DEBOUNCE_MS = 500;

const COLLAPSE_STORAGE_KEY = "jobfinder.filterPanel.collapsed";

/** Whether the panel starts collapsed or expanded on first run — the
 * question docs/DESIGN.md's "Open, deliberately" section left to T59.
 *
 * Decision: **expanded**. This panel is new UI replacing a settings
 * modal users already knew how to find; starting it collapsed would hide
 * the one thing this task exists to make visible on the very first
 * screen a user who upgrades sees. Collapsing is one click away and the
 * choice persists locally (`localStorage`, not a server round trip — this
 * is view state, not a setting `PUT /api/settings` should know about) so
 * a user who prefers the extra width only has to make that call once. */
function loadCollapsedPreference(): boolean {
  if (typeof window === "undefined") return false;
  try {
    return window.localStorage.getItem(COLLAPSE_STORAGE_KEY) === "1";
  } catch {
    return false;
  }
}

function saveCollapsedPreference(collapsed: boolean): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(COLLAPSE_STORAGE_KEY, collapsed ? "1" : "0");
  } catch {
    // localStorage can throw in a locked-down browser profile — the
    // panel still works, it just forgets the preference across reloads.
  }
}

/** T59 — the 264px inline filter panel (docs/DESIGN.md principle 3):
 * minimum score, freshness, arrangement and the four "hide" toggles,
 * written to `PUT /api/settings` debounced and coalesced
 * (`debouncedPatchQueue.ts`) rather than through the Settings modal.
 *
 * Every control is optimistic — the slider/checkbox shows the new value
 * the instant the user moves it, independent of the debounced write —
 * and `onCommitted` is called once a write actually lands, so the caller
 * can re-fetch the filtered list (`GET /api/matches` already applies
 * `tag_visibility`/`quality_filter` server-side, jobfinder/web/jobs.py;
 * this panel does not reimplement that filtering client-side, it just
 * triggers a re-fetch of the single source of truth). A failed write
 * reverts the affected control(s) to the last known-good server value
 * and shows an inline error — never leaves a control showing a value the
 * server rejected.
 *
 * T89 adds `variant`: `"sidebar"` (default) is the original T59 layout —
 * a 264px column with its own collapse/expand chevron and localStorage
 * memory, built for `JobsView`, which T81 deleted. `"popover"` is the
 * board's mount (`BoardView.tsx`): the *caller* owns open/closed (a
 * header button toggling a positioned popover), so this variant always
 * renders expanded content and skips the chevron/localStorage state
 * entirely rather than nesting one collapse control inside another. Every
 * handler below — the debounced-queue wiring, the optimistic
 * value/revert logic — is identical between variants; only which chrome
 * renders around them changes. */
export function FilterPanel({
  onCommitted,
  variant = "sidebar",
}: {
  onCommitted: () => void;
  variant?: "sidebar" | "popover";
}): ReactNode {
  const [collapsed, setCollapsed] = useState<boolean>(variant === "popover" ? false : loadCollapsedPreference);
  const [loaded, setLoaded] = useState(false);
  const [lastGoodSettings, setLastGoodSettings] = useState<SettingsPayload | null>(null);
  const [values, setValues] = useState<FilterPanelValues | null>(null);
  const [error, setError] = useState<string | null>(null);

  const queueRef = useRef<DebouncedPatchQueue<FilterPatch> | null>(null);

  useEffect(() => {
    let cancelled = false;
    void fetchFullSettings().then((settings) => {
      if (cancelled) return;
      setLastGoodSettings(settings);
      setValues(extractFilterPanelValues(settings));
      setLoaded(true);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    const queue = createDebouncedPatchQueue<FilterPatch>({
      merge: mergeFilterPatch,
      isEmpty: isEmptyFilterPatch,
      initial: {},
      delayMs: WRITE_DEBOUNCE_MS,
      send: async (patch) => {
        const updated = await putSettingsPatch(patch);
        setLastGoodSettings(updated);
        setError(null);
        onCommitted();
      },
      onError: (err) => {
        // Revert to the last value the server actually accepted — the
        // DoD's "must not leave the UI claiming a value the server
        // rejected".
        setLastGoodSettings((current) => {
          if (current) setValues(extractFilterPanelValues(current));
          return current;
        });
        setError(err instanceof Error ? err.message : "Failed to save filter change.");
      },
    });
    queueRef.current = queue;
    return () => {
      queue.dispose();
      queueRef.current = null;
    };
    // `onCommitted` is expected to be a stable callback from the caller
    // (JobsView wraps it in `useCallback`) — including it here means the
    // queue is rebuilt if that ever stops being true, rather than
    // silently capturing a stale closure.
  }, [onCommitted]);

  const toggleCollapsed = useCallback(() => {
    setCollapsed((prev) => {
      const next = !prev;
      saveCollapsedPreference(next);
      return next;
    });
  }, []);

  const enqueue = useCallback((patch: FilterPatch) => {
    queueRef.current?.enqueue(patch);
  }, []);

  const handleMatchThreshold = useCallback(
    (v: number) => {
      setValues((prev) => (prev ? { ...prev, matchThreshold: v } : prev));
      enqueue({ match_threshold: v });
    },
    [enqueue],
  );

  const handleFreshness = useCallback(
    (v: number) => {
      setValues((prev) => (prev ? { ...prev, freshnessHours: v } : prev));
      if (lastGoodSettings) {
        enqueue({ sources: sourcesWithUniformHours(lastGoodSettings.sources, v) });
      }
    },
    [enqueue, lastGoodSettings],
  );

  const handleArrangement = useCallback(
    (arrangement: Arrangement) => {
      setValues((prev) => (prev ? { ...prev, arrangement } : prev));
      enqueue({ tag_visibility: buildTagVisibilityPatch({ arrangement }) });
    },
    [enqueue],
  );

  const handleHideInternship = useCallback(
    (checked: boolean) => {
      setValues((prev) => (prev ? { ...prev, hideInternship: checked } : prev));
      enqueue({ tag_visibility: buildTagVisibilityPatch({ hideInternship: checked }) });
    },
    [enqueue],
  );

  const handleHideContract = useCallback(
    (checked: boolean) => {
      setValues((prev) => (prev ? { ...prev, hideContract: checked } : prev));
      enqueue({ tag_visibility: buildTagVisibilityPatch({ hideContract: checked }) });
    },
    [enqueue],
  );

  const handleHideStaffingAgencies = useCallback(
    (checked: boolean) => {
      setValues((prev) => (prev ? { ...prev, hideStaffingAgencies: checked } : prev));
      enqueue({ quality_filter: buildQualityFilterPatch({ hideStaffingAgencies: checked }) });
    },
    [enqueue],
  );

  const handleHidePromoted = useCallback(
    (checked: boolean) => {
      setValues((prev) => (prev ? { ...prev, hidePromoted: checked } : prev));
      enqueue({ quality_filter: buildQualityFilterPatch({ hidePromoted: checked }) });
    },
    [enqueue],
  );

  const errorBanner = useMemo(() => {
    if (!error) return null;
    return (
      <div className="filter-panel-error" role="alert">
        {error}
      </div>
    );
  }, [error]);

  if (collapsed) {
    return (
      <div className="filter-panel filter-panel-collapsed">
        <button
          type="button"
          className="filter-panel-collapse-btn"
          onClick={toggleCollapsed}
          aria-label="Expand filters"
          title="Expand filters"
        >
          ‹
        </button>
      </div>
    );
  }

  const panelClassName = variant === "popover" ? "filter-panel filter-panel-popover" : "filter-panel";

  if (!loaded || !values) {
    return (
      <div className={panelClassName}>
        <div className="filter-panel-loading">Loading filters…</div>
      </div>
    );
  }

  return (
    <div className={panelClassName}>
      <div className="filter-panel-header">
        <span className="filter-panel-title">Filters</span>
        {variant === "sidebar" ? (
          <button
            type="button"
            className="filter-panel-collapse-btn"
            onClick={toggleCollapsed}
            aria-label="Collapse filters"
            title="Collapse filters"
          >
            ›
          </button>
        ) : null}
      </div>

      {errorBanner}

      <div className="filter-panel-section" data-testid={BOARD_FILTER_CONTROL_TEST_IDS.matchThreshold}>
        <div className="filter-panel-label">Minimum score</div>
        <SliderRow
          label="Minimum score"
          min={0}
          max={100}
          value={values.matchThreshold}
          onChange={handleMatchThreshold}
        />
      </div>

      <div className="filter-panel-section" data-testid={BOARD_FILTER_CONTROL_TEST_IDS.freshnessHours}>
        <div className="filter-panel-label">Freshness</div>
        <SliderRow
          label="Freshness"
          min={1}
          max={168}
          suffix=" hrs"
          value={values.freshnessHours}
          onChange={handleFreshness}
        />
      </div>

      <div className="filter-panel-section" data-testid={BOARD_FILTER_CONTROL_TEST_IDS.arrangement}>
        <div className="filter-panel-label">Arrangement</div>
        <div className="filter-panel-arrangement" role="radiogroup" aria-label="Arrangement">
          {ARRANGEMENTS.map((a) => (
            <button
              key={a.key}
              type="button"
              role="radio"
              aria-checked={a.key === values.arrangement}
              className={`filter-panel-arrangement-btn ${a.key === values.arrangement ? "active" : ""}`}
              onClick={() => handleArrangement(a.key)}
            >
              {a.label}
            </button>
          ))}
        </div>
      </div>

      <div className="filter-panel-section">
        <div className="filter-panel-label">Hide</div>
        <label className="filter-panel-checkbox" data-testid={BOARD_FILTER_CONTROL_TEST_IDS.hideInternship}>
          <input
            type="checkbox"
            checked={values.hideInternship}
            onChange={(e) => handleHideInternship(e.target.checked)}
          />
          Internships
        </label>
        <label className="filter-panel-checkbox" data-testid={BOARD_FILTER_CONTROL_TEST_IDS.hideContract}>
          <input
            type="checkbox"
            checked={values.hideContract}
            onChange={(e) => handleHideContract(e.target.checked)}
          />
          Contract
        </label>
        <label className="filter-panel-checkbox" data-testid={BOARD_FILTER_CONTROL_TEST_IDS.hideStaffingAgencies}>
          <input
            type="checkbox"
            checked={values.hideStaffingAgencies}
            onChange={(e) => handleHideStaffingAgencies(e.target.checked)}
          />
          Staffing agencies
        </label>
        <label className="filter-panel-checkbox" data-testid={BOARD_FILTER_CONTROL_TEST_IDS.hidePromoted}>
          <input
            type="checkbox"
            checked={values.hidePromoted}
            onChange={(e) => handleHidePromoted(e.target.checked)}
          />
          Promoted listings
        </label>
      </div>
    </div>
  );
}
