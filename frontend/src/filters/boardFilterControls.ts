// T89 — the source-of-truth link between `FilterPanel`'s own field list
// (`FilterPanelValues`, filterPanelLogic.ts) and the stable DOM hooks
// `scripts/t89_filters_browser_check.py` and `FilterPanel.tsx` both key off
// of, so "every control FilterPanel offers is reachable from the board" can
// be asserted against the same enum the panel's state is actually shaped by
// — rather than a hand-maintained list that silently drifts out of sync the
// way the DoD calls out ("derive the expected list from the source of
// truth, do not retype it").
//
// `Record<keyof FilterPanelValues, string>` does double duty: `tsc -b`
// already fails if a field is added to or removed from `FilterPanelValues`
// without a matching entry here (a missing/extra key is a type error, not
// just a test failure), and `tests/boardFilterControls.test.ts` additionally
// checks the *runtime* shape `extractFilterPanelValues` actually returns
// against this same map, in case a future field renames itself in a way
// that still satisfies the type (e.g. an intersection/union edge case).
import type { FilterPanelValues } from "./filterPanelLogic";

export const BOARD_FILTER_CONTROL_TEST_IDS: Record<keyof FilterPanelValues, string> = {
  matchThreshold: "filter-control-match-threshold",
  freshnessHours: "filter-control-freshness",
  arrangement: "filter-control-arrangement",
  hideInternship: "filter-control-hide-internship",
  hideContract: "filter-control-hide-contract",
  hideStaffingAgencies: "filter-control-hide-staffing-agencies",
  hidePromoted: "filter-control-hide-promoted",
};
