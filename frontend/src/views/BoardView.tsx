import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
  type PointerEvent as ReactPointerEvent,
  type ReactNode,
} from "react";
import { fetchBoard, setJobStage, setJobStarred } from "../api/board";
import { skipCompany, triggerScan } from "../api/jobs";
import { fetchFullSettings, putSettingsPatch } from "../api/settings";
import { fetchScanState } from "../api/state";
import { cardFlags } from "../components/cardFlags";
import {
  BOARD_COLUMNS,
  STAGE_BY_DIGIT,
  TIER_FILTER_OPTIONS,
  cardPassesTierFilter,
  dropSlotLabel,
  footLineForCard,
  moveCardAction,
  moveToAdjacentColumn,
  moveWithinColumn,
  sameCountryTierIsConfigured,
  sortColumn,
  undoToastLabel,
  type TierFilterValue,
} from "../components/boardLogic";
import { closeColumnInfo, openColumnInfo } from "../components/boardColumnInfoLogic";
import {
  buildQuietDayCopy,
  FIRST_RUN_COLUMN_COPY,
  isFirstRunBoard,
  type QuietDayScanInfo,
} from "../components/boardQuietDayLogic";
import { fitBgVar, fitFgVar, fitStep } from "../components/fitRamp";
import { rowMetaLine } from "../components/cardFormat";
import { BoardCardSheet } from "../components/BoardCardSheet";
import { FilterPanel } from "../components/FilterPanel";
import { JobModal } from "../components/JobModal";
import { closeJob, openJob } from "../components/jobModalLogic";
import { ScanControl } from "../components/ScanControl";
import { STAGES, type BoardCard, type BoardPayload, type Stage } from "../types/job";
import "./BoardView.css";

const EMPTY_BOARD: BoardPayload = { new: [], shortlist: [], applied: [], replied: [], out: [] };

export interface BoardCounts {
  board: number;
}

/**
 * T81/T83 — the board: five columns replacing the Matches/
 * Applied/Removed shell, in one `GET /api/board` payload. `boardLogic.ts`
 * holds the pure sort/filter/keyboard logic this component just renders
 * and wires up; `cardFlags.ts`/`fitRamp.ts` likewise. Clicking a card
 * opens `JobPanel.tsx`'s content in a modal (T100, superseding T83's
 * side panel — docs/DESIGN.md's dated block) — the board itself stays
 * mounted behind it, untouched and at full width, and keeps its own
 * scroll position.
 *
 * T82 adds pointer drag/drop between columns and the ten-second undo
 * `dispatchMoveCard` below is the one reducer call
 * site — the keyboard digit handler, `JobPanel`'s stage control
 * (`onSetStage`) and the drag-end handler all call it, and it is the only
 * place that builds a `moveCardAction` (boardLogic.ts) or calls
 * `setJobStage`, so a move is one write path regardless of input.
 *
 * T89 reuses T59's `FilterPanel` (minimum score, freshness, arrangement,
 * the four hide toggles) rather than re-deriving its already-tested
 * debounced-write logic — mounted here as a header popover (`variant=
 * "popover"`, FilterPanel.tsx) rather than a permanent sidebar, so it
 * never eats into the five-column grid's width (T81's committed
 * horizontal-fill assertion, `scripts/t81_board_browser_check.py`).
 * `handleFiltersCommitted` just calls `reload()` — the same re-fetch
 * `onCountsChange`/every stage move already triggers — so a filter
 * change re-filters the visible board the instant the debounced write
 * lands, with no reload.
 *
 * Out of scope here, each its own task (see this task's brief): the phone
 * layout / empty-state design (T85, so the empty-column copy below stays
 * minimal and the panel hides outright under 640px, JobPanel.css).
 */
export function BoardView({
  onCountsChange,
  onOpenFillerBot,
}: {
  onCountsChange?: (counts: BoardCounts) => void;
  /** T83's "Fill it with FillerBot" panel action — distinct from the
   * rail's generic `onOpenFillerBot` (Rail.tsx), which carries no job
   * context. `App.tsx` wires this one to seed the FillerBot overlay's URL
   * field with the posting's own URL. */
  onOpenFillerBot?: (url: string) => void;
}): ReactNode {
  const [board, setBoard] = useState<BoardPayload>(EMPTY_BOARD);
  const [loaded, setLoaded] = useState(false);
  const [tierFilter, setTierFilter] = useState<TierFilterValue>("all");
  const [scanRequested, setScanRequested] = useState(false);
  // T89 — the header's "Filters" popover (FilterPanel.tsx, variant=
  // "popover"). Closed by default: unlike the sidebar's T59 default of
  // expanded (new UI nobody has found yet), this control sits next to
  // the tier chips a returning user already knows, so there is nothing
  // to surface unprompted on first paint.
  const [filtersOpen, setFiltersOpen] = useState(false);
  const filtersPopoverRef = useRef<HTMLDivElement>(null);
  const filtersButtonRef = useRef<HTMLButtonElement>(null);
  // T104 — each column header's info disclosure (description +
  // fills-itself line, relocated off the header so it no longer stands
  // on screen permanently). Tracked the same shape as T100's
  // `openJobIds`/`jobModalLogic.ts` — a list of at most one open
  // *stage* — so "opening one column's info closes any other's" is an
  // invariant the data shape enforces, not an incidental consequence of
  // how this component happens to call `setState` (boardColumnInfoLogic.ts's
  // module docstring on why this beats five independent booleans).
  const [openInfoStages, setOpenInfoStages] = useState<Stage[]>([]);
  const infoOpenStage = openInfoStages[0] ?? null;
  // One cursor: which column is active, and which card is the "current"
  // one within each column (kept per-column so switching columns with
  // ArrowLeft/Right and back doesn't forget where you were).
  const [activeColumnIndex, setActiveColumnIndex] = useState(0);
  const [selectedByStage, setSelectedByStage] = useState<Record<Stage, string | null>>({
    new: null,
    shortlist: null,
    applied: null,
    replied: null,
    out: null,
  });

  // T85 — the quiet day's numbers (this task's brief). Every field here
  // traces to a real, already-persisted value: `GET /api/state`'s
  // `ScanStateSnapshot` (api/state.ts, `jobfinder/web/scan.py`'s
  // `ScanState`) for the scan's own counts, `GET /api/settings`'s
  // `match_threshold`/`check_interval_minutes` for the bar and the
  // schedule. Neither fetch is allowed to take the board down with it —
  // `fetchScanState` already returns `null` on failure (api/state.ts),
  // and `fetchFullSettings` is caught here the same way, so a dropped
  // settings request degrades `boardQuietDayLogic.ts`'s copy to the
  // fields that did load rather than failing `reload()` itself.
  const [quietDayInfo, setQuietDayInfo] = useState<QuietDayScanInfo | null>(null);

  const reload = useCallback(async () => {
    const payload = await fetchBoard();
    setBoard(payload);
    setLoaded(true);
    const [scanState, settings] = await Promise.all([
      fetchScanState(),
      fetchFullSettings().catch(() => null),
    ]);
    setQuietDayInfo({
      completed: scanState?.last_scan_completed_at != null,
      lastScanCompletedAt: scanState?.last_scan_completed_at ?? null,
      postingsRead: scanState?.last_new_jobs ?? 0,
      bestScore: scanState?.last_best_score ?? null,
      matchThreshold: settings?.match_threshold ?? null,
      checkIntervalMinutes: settings?.check_interval_minutes ?? null,
      scanScheduled: scanState?.scan_cycle_scheduled ?? false,
    });
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  // The quiet day's two controls (this task's brief: "show the ones
  // under the bar, and lower the bar") — both are the one real write
  // `FilterPanel`'s minimum-score slider already makes
  // (`PUT /api/settings` `match_threshold`), called directly here rather
  // than through `FilterPanel`'s debounced queue (`debouncedPatchQueue.ts`,
  // a hard boundary this task does not touch) since this is a single,
  // immediate, user-initiated write, not a slider drag to coalesce.
  const handleAdjustMatchThreshold = useCallback(
    (value: number) => {
      void putSettingsPatch({ match_threshold: value }).then(reload);
    },
    [reload],
  );

  // T89 — a filter write re-filters the visible board without a reload
  // `FilterPanel` calls this once its
  // debounced `PUT /api/settings` write actually lands, and `reload()`
  // re-fetches `GET /api/board`, which now enforces `match_threshold`/
  // `tag_visibility`/`quality_filter` against the New column
  // server-side (jobfinder/web/jobs.py's `_list_board_sync`, T89).
  const handleFiltersCommitted = useCallback(() => {
    void reload();
  }, [reload]);

  const closeFilters = useCallback(() => setFiltersOpen(false), []);

  // Close on outside click/Escape — the popover owns its own open state
  // rather than FilterPanel's localStorage-backed collapse (that's the
  // sidebar variant's job, not this one's).
  useEffect(() => {
    if (!filtersOpen) return;
    const onPointerDown = (e: globalThis.PointerEvent) => {
      const target = e.target as Node | null;
      if (
        target &&
        !filtersPopoverRef.current?.contains(target) &&
        !filtersButtonRef.current?.contains(target)
      ) {
        closeFilters();
      }
    };
    const onKeyDown = (e: globalThis.KeyboardEvent) => {
      if (e.key === "Escape") closeFilters();
    };
    window.addEventListener("pointerdown", onPointerDown);
    window.addEventListener("keydown", onKeyDown);
    return () => {
      window.removeEventListener("pointerdown", onPointerDown);
      window.removeEventListener("keydown", onKeyDown);
    };
  }, [filtersOpen, closeFilters]);

  // T104 — toggling a column's info button opens it (closing any other
  // column's, per `openColumnInfo`) or closes it if it is already the
  // one open; clicking the same header's button a second time is how a
  // keyboard/mouse user dismisses without reaching for Escape.
  const closeColumnInfoPopover = useCallback(() => setOpenInfoStages((prev) => closeColumnInfo(prev)), []);
  const handleToggleColumnInfo = useCallback((stage: Stage) => {
    setOpenInfoStages((prev) => (prev[0] === stage ? closeColumnInfo(prev) : openColumnInfo(prev, stage)));
  }, []);

  // Escape dismisses whichever column's info is open — same convention
  // as the filters popover above.
  useEffect(() => {
    if (openInfoStages.length === 0) return;
    const onKeyDown = (e: globalThis.KeyboardEvent) => {
      if (e.key === "Escape") closeColumnInfoPopover();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [openInfoStages, closeColumnInfoPopover]);

  const onCountsChangeRef = useRef(onCountsChange);
  useEffect(() => {
    onCountsChangeRef.current = onCountsChange;
  }, [onCountsChange]);
  useEffect(() => {
    // The rail's one badge for the board destination: the New column's
    // count, i.e. "how much is waiting on you" — the only column that
    // fills itself, so it is the one number that changes without the
    // user having done anything yet.
    onCountsChangeRef.current?.({ board: board.new.length });
  }, [board.new.length]);

  const filteredSortedColumns = useMemo(() => {
    const result: Record<Stage, BoardCard[]> = { new: [], shortlist: [], applied: [], replied: [], out: [] };
    for (const column of BOARD_COLUMNS) {
      const filtered = board[column.stage].filter((c) => cardPassesTierFilter(c, tierFilter));
      result[column.stage] = sortColumn(column.stage, filtered);
    }
    return result;
  }, [board, tierFilter]);

  const sameCountryConfigured = useMemo(() => {
    const allCards = BOARD_COLUMNS.flatMap((c) => board[c.stage]);
    return sameCountryTierIsConfigured(allCards);
  }, [board]);

  // T83/T100 — the modal. Only the *open job ids* are tracked, as a list
  // of at most one (`jobModalLogic.ts`'s `openJob`/`closeJob`) rather
  // than a bare `string | null`, so "opening a second job while one is
  // open leaves exactly one open id" is an invariant the data shape
  // itself enforces, not just an incidental consequence of how this
  // component happens to call `setState`. `panelJobId` below is the one
  // id (or null) `JobModal`/the rest of this component actually reads —
  // the card itself, its column list, its position and its stage are all
  // *derived* below from the live `filteredSortedColumns`, never cached —
  // so moving the card via the stage control (or the keyboard, which
  // writes through the same path) is reflected the instant `reload()`
  // refreshes `board`, without the modal having to listen for that move
  // specially.
  const [openJobIds, setOpenJobIds] = useState<string[]>([]);
  const panelJobId = openJobIds[0] ?? null;
  // T100 — the element to return focus to when the modal closes: the
  // card that most recently *opened* it (captured at the point of
  // opening, in the click/keyboard handlers below), not whatever
  // `showModal()`'s own native focus-restore would pick, which only ever
  // remembers the first opener in an A->B swap (JobModal.tsx's
  // docstring).
  const modalOpenerRef = useRef<HTMLElement | null>(null);

  const panelCard = useMemo(() => {
    if (!panelJobId) return null;
    for (const stage of STAGES) {
      const found = filteredSortedColumns[stage].find((c) => c.job_id === panelJobId);
      if (found) return found;
    }
    return null;
  }, [panelJobId, filteredSortedColumns]);

  // A job can leave the board behind the panel's back (the tier filter
  // changes to hide it). No effect clears `panelJobId` for that case —
  // `panelCard` above just resolves to `null` and the panel renders
  // nothing (see the JSX below) until the id resolves again, which is
  // harmless (the product spec: a posting is kept, never deleted, and
  // `/api/board` never drops a skipped company's existing cards either —
  // `jobfinder/web/jobs.py`'s `_list_board_sync` has no skip-list check,
  // unlike `_list_matches_sync`) and keeps this component's state to one
  // thing worth owning: which job the user asked to see.
  const findCardAnyStage = useCallback(
    (jobId: string): BoardCard | null => {
      for (const stage of STAGES) {
        const found = board[stage].find((c) => c.job_id === jobId);
        if (found) return found;
      }
      return null;
    },
    [board],
  );

  const panelColumnCards = panelCard ? filteredSortedColumns[panelCard.stage] : [];
  const panelIndex = panelCard ? panelColumnCards.findIndex((c) => c.job_id === panelCard.job_id) : -1;

  const handleClosePanel = useCallback(() => setOpenJobIds((prev) => closeJob(prev)), []);

  const handlePanelPrev = useCallback(() => {
    if (!panelCard) return;
    const cards = filteredSortedColumns[panelCard.stage];
    const idx = cards.findIndex((c) => c.job_id === panelCard.job_id);
    if (idx > 0) setOpenJobIds((prev) => openJob(prev, cards[idx - 1].job_id));
  }, [panelCard, filteredSortedColumns]);

  const handlePanelNext = useCallback(() => {
    if (!panelCard) return;
    const cards = filteredSortedColumns[panelCard.stage];
    const idx = cards.findIndex((c) => c.job_id === panelCard.job_id);
    if (idx >= 0 && idx < cards.length - 1) setOpenJobIds((prev) => openJob(prev, cards[idx + 1].job_id));
  }, [panelCard, filteredSortedColumns]);

  const handleSkipCompany = useCallback(
    (company: string) => {
      if (!company) return;
      const confirmed = window.confirm(
        `Never show postings from "${company}" again?\n\nThis hides every future posting from this employer — a different, ` +
          "permanent decision from dismissing just this one.",
      );
      if (!confirmed) return;
      void skipCompany(company).then(reload);
    },
    [reload],
  );

  const resolvedSelection = useCallback(
    (stage: Stage): string | null => {
      const cards = filteredSortedColumns[stage];
      const current = selectedByStage[stage];
      if (current && cards.some((c) => c.job_id === current)) return current;
      return cards[0]?.job_id ?? null;
    },
    [filteredSortedColumns, selectedByStage],
  );

  const handleRunScanNow = useCallback(() => {
    setScanRequested(true);
    void triggerScan().finally(() => {
      setScanRequested(false);
      void reload();
    });
  }, [reload]);

  const handleFocusCard = useCallback((stage: Stage, columnIndex: number, jobId: string) => {
    setActiveColumnIndex(columnIndex);
    setSelectedByStage((prev) => ({ ...prev, [stage]: jobId }));
  }, []);

  // T82 — the ten-second undo toast. Tracked
  // outside the reducer call itself: `dispatchMoveCard` below only knows
  // the move it was asked to make, not whether that move should be
  // offered back, so the "offer undo" decision lives here, once.
  const [undoState, setUndoState] = useState<{ jobId: string; title: string; fromStage: Stage; toStage: Stage } | null>(
    null,
  );
  const undoTimerRef = useRef<number | null>(null);
  const clearUndoTimer = useCallback(() => {
    if (undoTimerRef.current !== null) {
      window.clearTimeout(undoTimerRef.current);
      undoTimerRef.current = null;
    }
  }, []);

  // The one reducer call site (this task's brief: "three faces of one
  // write, not three code paths"). Every stage move — drag, keyboard
  // `1`-`5`, the panel's stage control — calls this same function, which
  // builds the identical `moveCardAction` (boardLogic.ts) for the same
  // (jobId, stage) regardless of which of the three called it, then
  // makes the one write (`setJobStage`) and offers the undo toast.
  // `suppressUndo` is only used by `handleUndo` itself, so reversing a
  // move does not immediately offer to undo the undo.
  const dispatchMoveCard = useCallback(
    (jobId: string, toStage: Stage, opts: { suppressUndo?: boolean } = {}) => {
      const action = moveCardAction(jobId, toStage);
      const card = findCardAnyStage(action.jobId);
      const fromStage = card?.stage ?? null;
      void setJobStage(action.jobId, action.toStage).then(() => {
        void reload();
        if (!opts.suppressUndo && card && fromStage && fromStage !== action.toStage) {
          clearUndoTimer();
          setUndoState({ jobId: action.jobId, title: card.title, fromStage, toStage: action.toStage });
          undoTimerRef.current = window.setTimeout(() => {
            setUndoState(null);
            undoTimerRef.current = null;
          }, 10000);
        }
      });
    },
    [findCardAnyStage, reload, clearUndoTimer],
  );

  // T101 — the star control. Deliberately separate from `dispatchMoveCard`:
  // a star is not a stage move, so it must never
  // build a `moveCardAction` or call `setJobStage`, and it offers no undo
  // toast (nothing about the card's stage or position-by-move changed).
  const handleToggleStar = useCallback(
    (jobId: string, currentlyStarred: boolean) => {
      void setJobStarred(jobId, !currentlyStarred).then(() => void reload());
    },
    [reload],
  );

  const handleUndo = useCallback(() => {
    if (!undoState) return;
    clearUndoTimer();
    const { jobId, fromStage } = undoState;
    setUndoState(null);
    dispatchMoveCard(jobId, fromStage, { suppressUndo: true });
  }, [undoState, clearUndoTimer, dispatchMoveCard]);

  // ⌘Z/Ctrl+Z triggers undo while the toast is live.
  useEffect(() => {
    if (!undoState) return;
    const onKeyDown = (e: globalThis.KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && (e.key === "z" || e.key === "Z")) {
        e.preventDefault();
        handleUndo();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [undoState, handleUndo]);

  // ── T82: pointer drag between columns. Pointer events, not HTML5
  // drag-and-drop (no usable touch story, unstyleable drag image) and no
  // new dependency — `dragRef` tracks the in-progress drag without
  // re-rendering on every pointermove; `dragVisual` is the only piece
  // that needs to be state, since it drives the lifted-card class and
  // the drop slot's render.
  const dragRef = useRef<{
    jobId: string;
    fromStage: Stage;
    pointerId: number;
    startX: number;
    startY: number;
    moved: boolean;
  } | null>(null);
  const suppressNextClickRef = useRef(false);
  const [dragVisual, setDragVisual] = useState<{ jobId: string; overStage: Stage | null } | null>(null);

  const handleCardPointerDown = useCallback((e: ReactPointerEvent<HTMLDivElement>, card: BoardCard, stage: Stage) => {
    if (e.button !== 0) return;
    dragRef.current = {
      jobId: card.job_id,
      fromStage: stage,
      pointerId: e.pointerId,
      startX: e.clientX,
      startY: e.clientY,
      moved: false,
    };
  }, []);

  useEffect(() => {
    const DRAG_THRESHOLD_PX = 6;

    function stageAtPoint(x: number, y: number): Stage | null {
      const el = document.elementFromPoint(x, y);
      const col = el instanceof Element ? el.closest<HTMLElement>(".board-column") : null;
      const stage = col?.dataset.stage;
      return (stage as Stage | undefined) ?? null;
    }

    function handleMove(e: globalThis.PointerEvent) {
      const d = dragRef.current;
      if (!d || e.pointerId !== d.pointerId) return;
      const dx = e.clientX - d.startX;
      const dy = e.clientY - d.startY;
      if (!d.moved && Math.hypot(dx, dy) < DRAG_THRESHOLD_PX) return;
      d.moved = true;
      const overStage = stageAtPoint(e.clientX, e.clientY);
      setDragVisual({ jobId: d.jobId, overStage });
    }

    function endDrag(e: globalThis.PointerEvent) {
      const d = dragRef.current;
      if (!d || e.pointerId !== d.pointerId) return;
      if (d.moved) {
        suppressNextClickRef.current = true;
        const overStage = stageAtPoint(e.clientX, e.clientY);
        if (overStage && overStage !== d.fromStage) {
          dispatchMoveCard(d.jobId, overStage);
        }
      }
      dragRef.current = null;
      setDragVisual(null);
    }

    window.addEventListener("pointermove", handleMove);
    window.addEventListener("pointerup", endDrag);
    window.addEventListener("pointercancel", endDrag);
    return () => {
      window.removeEventListener("pointermove", handleMove);
      window.removeEventListener("pointerup", endDrag);
      window.removeEventListener("pointercancel", endDrag);
    };
  }, [dispatchMoveCard]);

  const handleOpenPosting = useCallback((card: BoardCard) => {
    const url = card.url_direct || card.url;
    if (url) window.open(url, "_blank", "noopener");
  }, []);

  // ── Keyboard (the product spec DoD): ArrowUp/Down within a column,
  // ArrowLeft/Right across columns, 1-5 sends the focused card to that
  // stage (through the same `setJobStage` the pointer/panel will reuse in
  // T82/T83), 'O' opens the posting.
  const handleCardKeyDown = useCallback(
    (e: KeyboardEvent<HTMLDivElement>, card: BoardCard, stage: Stage, columnIndex: number) => {
      const cards = filteredSortedColumns[stage];
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        const next = moveWithinColumn(cards, card.job_id, e.key === "ArrowDown" ? 1 : -1);
        if (next) handleFocusCard(stage, columnIndex, next);
        return;
      }
      if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
        e.preventDefault();
        const nextIndex = moveToAdjacentColumn(columnIndex, e.key === "ArrowRight" ? 1 : -1);
        if (nextIndex !== columnIndex) {
          setActiveColumnIndex(nextIndex);
        }
        return;
      }
      if (e.key in STAGE_BY_DIGIT) {
        e.preventDefault();
        dispatchMoveCard(card.job_id, STAGE_BY_DIGIT[e.key]);
        return;
      }
      if (e.key === "o" || e.key === "O") {
        e.preventDefault();
        handleOpenPosting(card);
        return;
      }
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        modalOpenerRef.current = e.currentTarget;
        setOpenJobIds((prev) => openJob(prev, card.job_id));
      }
    },
    [filteredSortedColumns, handleFocusCard, dispatchMoveCard, handleOpenPosting],
  );

  // Keep DOM focus following the active column/selection on keyboard
  // navigation — same guarded pattern `JobsView.tsx`'s `MatchesPane`
  // established: only steal focus when it was already inside the board,
  // so a reload triggered by something unrelated never yanks focus.
  const boardRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const activeStage = BOARD_COLUMNS[activeColumnIndex]?.stage;
    if (!activeStage || !boardRef.current) return;
    const jobId = resolvedSelection(activeStage);
    if (!jobId) return;
    const activeEl = document.activeElement;
    const focusWasInsideBoard = activeEl instanceof Node && boardRef.current.contains(activeEl);
    if (!focusWasInsideBoard) return;
    const el = boardRef.current.querySelector<HTMLElement>(`[data-job-id="${CSS.escape(jobId)}"]`);
    if (el && document.activeElement !== el) {
      el.focus({ preventScroll: true });
    }
  }, [activeColumnIndex, resolvedSelection]);

  // T85 — the two states that are the common case (this task's brief),
  // both keyed off what the owner actually sees on screen (post tier-
  // filter, `filteredSortedColumns`) rather than the raw server payload:
  // "first run" is every column empty; "quiet day" is New empty while at
  // least one other column holds something.
  const columnCounts = useMemo(() => {
    const out = {} as Record<Stage, number>;
    for (const stage of STAGES) out[stage] = filteredSortedColumns[stage].length;
    return out;
  }, [filteredSortedColumns]);
  const isFirstRun = isFirstRunBoard(columnCounts);
  const isQuietDay = !isFirstRun && columnCounts.new === 0;
  const quietDayCopy = useMemo(
    () => (quietDayInfo ? buildQuietDayCopy(quietDayInfo) : null),
    [quietDayInfo],
  );

  if (!loaded) {
    return <div className="board-view-loading">Loading…</div>;
  }

  return (
    <div className="board-view">
      <div className="board-view-header">
        <h1 className="board-view-title">Board</h1>
        <div className="board-view-tier-filter" role="group" aria-label="Location tier filter">
          {TIER_FILTER_OPTIONS.map((opt) => {
            const isUnconfigured = opt.key === "country" && !sameCountryConfigured;
            return (
              <button
                key={opt.key}
                type="button"
                className={`board-tier-chip ${tierFilter === opt.key ? "active" : ""}`}
                aria-pressed={tierFilter === opt.key}
                onClick={() => setTierFilter(opt.key)}
                title={isUnconfigured ? "Same country isn't configured — set your country in Settings." : undefined}
              >
                {opt.label}
                {isUnconfigured ? <span className="board-tier-chip-warn"> (unconfigured)</span> : null}
              </button>
            );
          })}
        </div>
        {/* T89 — minimum score, freshness, arrangement, and the four hide
            toggles, reachable from the board without a settings round
            trip. A popover, not a permanent
            264px column: the tier chips above are the only control T81
            put in the header row, and a panel that always occupies
            column width would break T81's committed horizontal-fill
            assertion. */}
        <div className="board-view-filters-anchor">
          <button
            ref={filtersButtonRef}
            type="button"
            className="board-view-filters-btn"
            aria-haspopup="dialog"
            aria-expanded={filtersOpen}
            onClick={() => setFiltersOpen((v) => !v)}
          >
            Filters
          </button>
          {filtersOpen ? (
            <div
              ref={filtersPopoverRef}
              className="board-filters-popover"
              role="dialog"
              aria-label="Board filters"
            >
              <FilterPanel variant="popover" onCommitted={handleFiltersCommitted} />
            </div>
          ) : null}
        </div>
        {/* T95 — one control, driven by `GET /api/state`, not by the
            `scanRequested` boolean below (kept only for the first-run
            empty-column CTA further down, a separate affordance in a
            separate context). See `scanControlLogic.ts`'s module
            docstring for why there is no Stop here. */}
        <ScanControl />
      </div>

      {/* T85 — the phone "one stage at a time" strip (this task's brief):
          five columns can't be five columns at 390px, so a horizontally
          scrollable row of pills picks which single column
          `.board-columns` shows below (CSS, `@media (max-width: 640px)`,
          BoardView.css) — hidden entirely above that width, where the
          five-column grid already shows every stage at once. Reuses
          `activeColumnIndex`/`setActiveColumnIndex`, the same cursor the
          desktop keyboard's ArrowLeft/ArrowRight already drives, so
          "which column is current" is one piece of state regardless of
          input. The strip scrolls its own overflow (`overflow-x: auto`
          on `.board-stage-strip` itself) — never the page, which is the
          DoD's "no horizontal page scroll" assertion. */}
      <div className="board-stage-strip" role="tablist" aria-label="Pipeline stage">
        {BOARD_COLUMNS.map((meta, i) => (
          <button
            key={meta.stage}
            type="button"
            role="tab"
            aria-selected={activeColumnIndex === i}
            className={`board-stage-pill ${activeColumnIndex === i ? "active" : ""}`}
            onClick={() => setActiveColumnIndex(i)}
          >
            {meta.label}
            <span className="board-stage-pill-count">{filteredSortedColumns[meta.stage].length}</span>
          </button>
        ))}
      </div>

      {/* T100: the modal (`JobModal`, below) floats over the board in a
          native `<dialog>`, in the top layer — `.board-columns` here is
          never resized or unmounted by it, so the board keeps its own
          columns' scroll position regardless of whether a job is open. */}
      <div className="board-view-body">
        <div className="board-columns" ref={boardRef}>
          {BOARD_COLUMNS.map((meta, columnIndex) => {
            const cards = filteredSortedColumns[meta.stage];
            const selected = resolvedSelection(meta.stage);
            const firstRunCopy = FIRST_RUN_COLUMN_COPY[meta.stage];
            return (
              <div
                key={meta.stage}
                className="board-column"
                data-stage={meta.stage}
                data-active={columnIndex === activeColumnIndex ? "true" : "false"}
              >
                <div className="board-column-header">
                  <h2 className="board-column-label">{meta.label}</h2>
                  <span className="board-column-count">{cards.length}</span>
                  {/* T104 — the owner's capture (the task ledger's `bug 4.png`)
                      showed all five headers' description + fills-itself
                      lines on screen at once, permanently, above columns
                      that are often nearly empty. Both lines are
                      learn-once copy (T81's, unchanged — see
                      boardLogic.ts's `BOARD_COLUMNS`), so they move
                      behind this one info control per header; the header
                      itself keeps only swatch/band, name, count and this
                      button. */}
                  <button
                    type="button"
                    className="board-column-info-btn"
                    aria-label={`${meta.label} column info`}
                    aria-haspopup="dialog"
                    aria-expanded={infoOpenStage === meta.stage}
                    onClick={() => handleToggleColumnInfo(meta.stage)}
                  >
                    i
                  </button>
                </div>
                {infoOpenStage === meta.stage ? (
                  <div className="board-column-info-popover" role="dialog" aria-label={`${meta.label} column info`}>
                    <p className="board-column-desc">{meta.description}</p>
                    <span className="board-column-fills">{meta.fillsItself}</span>
                  </div>
                ) : null}
                <div className="board-column-body">
                  {dragVisual && dragVisual.overStage === meta.stage
                    ? (() => {
                        const draggedCard = findCardAnyStage(dragVisual.jobId);
                        return draggedCard ? (
                          <div className="board-drop-slot">{dropSlotLabel(meta.stage, draggedCard)}</div>
                        ) : null;
                      })()
                    : null}
                  {cards.length === 0 && isFirstRun ? (
                    // T85 — first run (this task's brief): every column
                    // says what belongs in it, and only New carries the
                    // scan CTA.
                    <div className="board-column-empty board-column-empty-first-run">
                      <p className="board-column-empty-title">{firstRunCopy.title}</p>
                      <p className="board-column-empty-subtitle">{firstRunCopy.subtitle}</p>
                      {firstRunCopy.scanCta ? (
                        <button
                          type="button"
                          className="board-view-scan-now-btn board-column-empty-scan-btn"
                          onClick={handleRunScanNow}
                          disabled={scanRequested}
                        >
                          {scanRequested ? "Scanning…" : "Scan now"}
                        </button>
                      ) : null}
                    </div>
                  ) : cards.length === 0 && meta.stage === "new" && isQuietDay && quietDayCopy ? (
                    // T85 — the quiet day (this task's brief): New is
                    // empty because nothing cleared the bar, not because
                    // nothing happened. `boardQuietDayLogic.ts`'s
                    // `buildQuietDayCopy` only names numbers that are
                    // actually persisted (module docstring there).
                    <div className="board-column-empty board-column-empty-quiet-day">
                      <p className="board-column-empty-title">{quietDayCopy.title}</p>
                      {quietDayCopy.scanSummary ? (
                        <p className="board-column-empty-subtitle">{quietDayCopy.scanSummary}</p>
                      ) : null}
                      {quietDayCopy.nextScanText ? (
                        <p className="board-column-empty-next-scan">{quietDayCopy.nextScanText}</p>
                      ) : null}
                      {quietDayCopy.actions.length > 0 ? (
                        <div className="board-column-empty-actions">
                          {quietDayCopy.actions.map((action) => (
                            <button
                              key={action.label}
                              type="button"
                              className="board-column-empty-action-btn"
                              onClick={() => handleAdjustMatchThreshold(action.matchThreshold)}
                            >
                              {action.label}
                            </button>
                          ))}
                        </div>
                      ) : null}
                    </div>
                  ) : cards.length === 0 ? (
                    <p className="board-column-empty">
                      {meta.stage === "replied"
                        ? "Nothing here yet. JobFinder can't read your email, so this only fills in when you mark a reply yourself."
                        : "Nothing here yet."}
                    </p>
                  ) : (
                    cards.map((card) => {
                      const step = fitStep(card.score, card.stage);
                      const flags = cardFlags(card);
                      const isFocused = card.job_id === selected && columnIndex === activeColumnIndex;
                      return (
                        <div
                          key={card.job_id}
                          data-job-id={card.job_id}
                          role="button"
                          tabIndex={isFocused ? 0 : -1}
                          className={`board-card ${dragVisual?.jobId === card.job_id ? "board-card-dragging" : ""}`}
                          onFocus={() => handleFocusCard(meta.stage, columnIndex, card.job_id)}
                          onPointerDown={(e) => handleCardPointerDown(e, card, meta.stage)}
                          onClick={(e) => {
                            if (suppressNextClickRef.current) {
                              suppressNextClickRef.current = false;
                              return;
                            }
                            // T100 — `preventScroll`: a plain `.focus()`
                            // here scrolls this card's column to bring it
                            // into view, which would violate "the board's
                            // scroll position is unchanged by opening and
                            // closing" the instant a card below the fold
                            // opens the modal (same lesson the keyboard-
                            // follows-focus effect below already paid
                            // for).
                            (e.currentTarget as HTMLDivElement).focus({ preventScroll: true });
                            modalOpenerRef.current = e.currentTarget;
                            setOpenJobIds((prev) => openJob(prev, card.job_id));
                          }}
                          onKeyDown={(e) => handleCardKeyDown(e, card, meta.stage, columnIndex)}
                        >
                          <div
                            className="board-card-chip"
                            style={{ background: `var(${fitBgVar(step)})`, color: `var(${fitFgVar(step)})` }}
                          >
                            {card.score}
                          </div>
                          <button
                            type="button"
                            className={`board-card-star ${card.starred ? "board-card-star-active" : ""}`}
                            aria-label={card.starred ? `Unstar ${card.title}` : `Star ${card.title}`}
                            aria-pressed={card.starred}
                            onClick={(e) => {
                              e.stopPropagation();
                              handleToggleStar(card.job_id, card.starred);
                            }}
                            onPointerDown={(e) => e.stopPropagation()}
                          >
                            {card.starred ? "★" : "☆"}
                          </button>
                          <div className="board-card-body">
                            <div className="board-card-role">{card.title}</div>
                            <div className="board-card-meta">{rowMetaLine(card)}</div>
                            <div className="board-card-foot">
                              <span className="board-card-foot-text">{footLineForCard(card)}</span>
                              {flags.map((f) => (
                                <span key={f.key} className={`board-card-flag board-card-flag-${f.tone}`}>
                                  {f.label}
                                </span>
                              ))}
                            </div>
                          </div>
                        </div>
                      );
                    })
                  )}
                </div>
              </div>
            );
          })}
        </div>

        {/* T85 — the phone move control (BoardCardSheet.tsx's module
            docstring), mounted here whenever a card is selected. T100
            keeps this component and this gate unchanged — the phone form
            stays the bottom sheet, never the modal below. */}
        {panelCard ? (
          <BoardCardSheet card={panelCard} onClose={handleClosePanel} onSetStage={dispatchMoveCard} />
        ) : null}
      </div>

      {/* T100 — the job detail modal (JobModal.tsx's docstring): one
          `<dialog>`, mounted once here regardless of which job (or none)
          is open, outside `.board-view-body` so it never participates in
          that row's flex layout — it floats over the board in the native
          top layer instead. Always rendered (not conditionally mounted on
          `panelCard`) so there is exactly one dialog element for the
          lifetime of this view, never one created per card. */}
      <JobModal
        card={panelCard}
        isOpen={panelCard !== null}
        openerRef={modalOpenerRef}
        index={panelIndex}
        total={panelColumnCards.length}
        onClose={handleClosePanel}
        onPrev={handlePanelPrev}
        onNext={handlePanelNext}
        canPrev={panelIndex > 0}
        canNext={panelIndex >= 0 && panelIndex < panelColumnCards.length - 1}
        onSetStage={dispatchMoveCard}
        onOpenFillerBot={onOpenFillerBot}
        onSkipCompany={handleSkipCompany}
      />

      {/* T82 — the ten-second undo toast. Both the
          text and the reverse move go through `undoToastLabel`/
          `dispatchMoveCard`, the same wording/reducer every other move
          uses. */}
      {undoState ? (
        <div className="board-undo-toast" role="status">
          <span className="board-undo-toast-text">{undoToastLabel(undoState.title, undoState.toStage)}</span>
          <button type="button" className="board-undo-toast-btn" onClick={handleUndo}>
            {"Undo"}
          </button>
        </div>
      ) : null}
    </div>
  );
}
