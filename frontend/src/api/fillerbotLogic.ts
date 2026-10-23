// Pure logic behind the FillerBot view (T31) — no DOM, no fetch, no React,
// so the two parts most likely to break silently can be tested directly
// (`frontend/tests/fillerbotLogic.test.ts`, `npm run test:logic`):
//
//   * **index mapping.** The engine has exactly one index space: the
//     `analysis` event's `actions` array, the `execute_fill` overrides
//     dict, and `action_executed.index` all address the same unfiltered
//     list (`jobfinder/fillerbot/engine.py`'s "Execute fill actions"
//     comment). Cards *skip* click actions for display, as the Qt UI did
//     (`fillerbot_window.py:378`), but key themselves by the unfiltered
//     index — so the card position and the card key deliberately diverge,
//     which is exactly the kind of thing a test should pin.
//   * **the button state machine.** Ported from `fillerbot_window.py`'s
//     `_set_buttons_running` / `_set_buttons_idle` and the per-event
//     enabling in `_on_analysis` / `_on_fill_complete` / `_on_needs_human`.
//     Qt expressed it as scattered `setEnabled` calls across seven
//     handlers; here it is a value, so "which buttons are live right now"
//     is one thing to read and one thing to test.
//
// The React component (`views/FillerBotView.tsx`) holds state and renders;
// it does not decide anything that lives here.

/** One entry of the `analysis` event's `actions` array — the JSON form of
 * `jobfinder/fillerbot/models.py`'s `FillAction` (the engine `asdict()`s
 * the dataclass on its way into SSE). `GET /api/fillerbot/events` has no
 * typed OpenAPI body (`content: { "text/event-stream": unknown }`), so
 * this is narrowed from the real server-side contract — the same
 * convention `api/stream.ts` documents for the scan stream. */
export interface FillActionDto {
  action_type: string;
  selector_strategy: string;
  selector_value: string;
  value: string;
  field_label: string;
  confidence: number;
  source_field: string;
  field_index: number;
}

/** The JSON form of `models.py`'s `PageAnalysis`. */
export interface PageAnalysisDto {
  page_state: string;
  page_description: string;
  actions: FillActionDto[];
  next_step: string;
  human_note: string;
}

/** A per-field card. `index` is the engine's index, *not* the card's
 * position in the rendered list — see the module docstring. */
export interface ActionCardModel {
  index: number;
  fieldLabel: string;
  value: string;
  sourceField: string;
  confidence: number;
}

/** Result marks keyed by the same engine index. */
export type ActionMarks = Readonly<Record<number, boolean>>;

export interface ControlFlags {
  go: boolean;
  fill: boolean;
  rescan: boolean;
  next: boolean;
  stop: boolean;
  /** Pause/Resume: the backend has had both since T30 and the Qt UI never
   * exposed either. */
  pause: boolean;
  resume: boolean;
  /** Bring Chrome to front (T31) — live for as long as there is a session,
   * since the whole point is to find a window that has gone behind
   * something else, which can happen at any moment of a session. */
  front: boolean;
}

/** `_set_buttons_idle` (`fillerbot_window.py:494`), plus the controls Qt
 * had no equivalent for. */
export const IDLE_CONTROLS: ControlFlags = {
  go: true,
  fill: false,
  rescan: false,
  next: false,
  stop: false,
  pause: false,
  resume: false,
  front: false,
};

/** `_set_buttons_running` (`fillerbot_window.py:487`). */
export const RUNNING_CONTROLS: ControlFlags = {
  go: false,
  fill: false,
  rescan: false,
  next: false,
  stop: true,
  pause: true,
  resume: false,
  front: true,
};

/**
 * Normalises what the user typed into the URL box, porting
 * `fillerbot_window.py`'s `_on_go` (`:294`): a bare host becomes
 * `https://`, anything already carrying a scheme is left alone.
 *
 * Returns `null` for input that is empty once trimmed — `_on_go` returned
 * early in that case rather than starting a session, and so does the
 * caller here.
 */
export function normalizeUrl(raw: string): string | null {
  const trimmed = raw.trim();
  if (!trimmed) {
    return null;
  }
  if (trimmed.startsWith("http://") || trimmed.startsWith("https://")) {
    return trimmed;
  }
  return `https://${trimmed}`;
}

/**
 * Builds the per-field cards from an `analysis` event's actions.
 *
 * Click actions are skipped for display (Qt's `_on_analysis` did the same
 * — a "click Next" is a control, not a field the user edits), but each
 * surviving card keeps its index in the *unfiltered* array. That index is
 * what goes back as an `execute_fill` override key and what
 * `action_executed.index` reports, so a click ordered before a fill must
 * not shift it.
 */
export function buildActionCards(actions: readonly FillActionDto[]): ActionCardModel[] {
  const cards: ActionCardModel[] = [];
  actions.forEach((action, index) => {
    if (action.action_type === "click") {
      return;
    }
    cards.push({
      index,
      fieldLabel: action.field_label,
      value: action.value,
      sourceField: action.source_field,
      confidence: action.confidence,
    });
  });
  return cards;
}

/**
 * The confidence dot's colour band, ported from `ActionCard.__init__`
 * (`fillerbot_window.py:56-62`) — the same four thresholds mapped onto the
 * `--jf-score-*` tokens rather than `theme.py`'s `score_*` fields.
 */
export function confidenceTier(confidence: number): "strong" | "good" | "fair" | "weak" {
  if (confidence >= 0.8) {
    return "strong";
  }
  if (confidence >= 0.5) {
    return "good";
  }
  if (confidence >= 0.3) {
    return "fair";
  }
  return "weak";
}

/**
 * The override map `POST /api/fillerbot/fill` expects, built from the
 * user's (possibly edited) card values. Keyed by the engine index, and
 * sent for *every* card, not just the edited ones — same as Qt's `_on_fill`
 * (`:348`), which collected `overrides[card._index] = card.edited_value`
 * unconditionally. Sending an unchanged value is a no-op server-side
 * (`engine.py` assigns it back over the identical string), and sending the
 * full set means a value the user cleared on purpose is honoured rather
 * than falling back to the model's suggestion.
 *
 * JSON object keys are strings; FastAPI coerces them back to the `int` keys
 * of `FillIn.overrides` (`dict[int, str]`).
 */
export function buildFillOverrides(
  cards: readonly ActionCardModel[],
  edits: Readonly<Record<number, string>>,
): Record<string, string> {
  const overrides: Record<string, string> = {};
  for (const card of cards) {
    overrides[String(card.index)] = edits[card.index] ?? card.value;
  }
  return overrides;
}

/** `_on_analysis`'s enabling (`fillerbot_window.py:378`): Fill only with
 * cards to fill, Re-scan always, Next Page *only* when the analysis said
 * `next_page`. */
export function controlsOnAnalysis(
  prev: ControlFlags,
  analysis: { cardCount: number; nextStep: string },
): ControlFlags {
  return {
    ...prev,
    fill: analysis.cardCount > 0,
    rescan: true,
    next: analysis.nextStep === "next_page",
  };
}

/**
 * `_on_fill_complete` (`fillerbot_window.py:411`).
 *
 * Qt re-enabled Next Page here unconditionally — not gated on the analysis
 * having said `next_page`, unlike `_on_analysis`. Kept faithfully: after a
 * fill the engine is parked in `_wait_unpause`, and `click_next` with no
 * click action in hand only unpauses it (`engine.py`'s
 * `if self._do_next_page and click_actions`), i.e. it behaves as a second
 * Re-scan rather than doing anything unexpected. Changing it would be a
 * behaviour change dressed up as a port.
 */
export function controlsOnFillComplete(prev: ControlFlags): ControlFlags {
  return { ...prev, fill: false, rescan: true, next: true };
}

/** `_on_needs_human` (`fillerbot_window.py:427`) — the user has to do
 * something in the browser, and Re-scan is how they hand control back. */
export function controlsOnNeedsHuman(prev: ControlFlags): ControlFlags {
  return { ...prev, rescan: true };
}

/** Qt's `_on_fill` / `_on_rescan` / `_on_next` each disabled their own
 * button on click so a second press couldn't double-fire while the engine
 * was busy. */
export function controlsOnFillClicked(prev: ControlFlags): ControlFlags {
  return { ...prev, fill: false };
}

export function controlsOnRescanClicked(prev: ControlFlags): ControlFlags {
  return { ...prev, fill: false, rescan: false };
}

export function controlsOnNextClicked(prev: ControlFlags): ControlFlags {
  return { ...prev, next: false };
}

export function controlsOnPaused(prev: ControlFlags): ControlFlags {
  return { ...prev, pause: false, resume: true };
}

export function controlsOnResumed(prev: ControlFlags): ControlFlags {
  return { ...prev, pause: true, resume: false };
}

/**
 * The post-fill summary line, ported from `_on_fill_complete`
 * (`fillerbot_window.py:415-422`): silent when everything landed, explicit
 * about the count when it didn't. Returns `null` when there is nothing to
 * say, so the caller leaves the engine's own status message in place.
 */
export function fillFailureSummary(
  cards: readonly ActionCardModel[],
  marks: ActionMarks,
): string | null {
  const total = cards.length;
  const failed = cards.filter((card) => marks[card.index] === false).length;
  if (!failed) {
    return null;
  }
  const plural = total === 1 ? "" : "s";
  return `Done — ${failed} of ${total} field${plural} failed (highlighted in red). Fix manually or Re-scan.`;
}
