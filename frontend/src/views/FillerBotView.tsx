import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import {
  FillerBotRequestError,
  acceptFillerUpload,
  bringFillerToFront,
  executeFillerFill,
  fetchFillerProfile,
  fetchFillerSession,
  nextPageFiller,
  pauseFiller,
  rescanFiller,
  resumeFiller,
  saveFillerExtras,
  skipFillerUpload,
  startFillerSession,
  stopFiller,
  subscribeFillerBotStream,
  type FillerBotEvent,
  type FillerProfilePreflight,
} from "../api/fillerbot";
import {
  IDLE_CONTROLS,
  RUNNING_CONTROLS,
  buildActionCards,
  buildFillOverrides,
  confidenceTier,
  controlsOnAnalysis,
  controlsOnFillComplete,
  controlsOnFillClicked,
  controlsOnNeedsHuman,
  controlsOnNextClicked,
  controlsOnPaused,
  controlsOnRescanClicked,
  controlsOnResumed,
  fillFailureSummary,
  normalizeUrl,
  type ActionCardModel,
  type ControlFlags,
} from "../api/fillerbotLogic";
import "./FillerBotView.css";

/**
 * FillerBot control panel (T31) — the web port of
 * `jobfinder/ui/fillerbot_window.py`'s `FillerBotWindow`, sitting alongside
 * the live Chrome window the engine drives.
 *
 * Rendered as a full-screen overlay, the same substitute for Qt modality
 * `SettingsView` documents. Every decision worth testing (index mapping,
 * the button state machine, URL normalisation, the failure summary) lives
 * in `api/fillerbotLogic.ts`; this component holds state and renders.
 *
 * Three lifecycle facts shape the mount path:
 *
 *  * **One session maximum.** `POST /session` answers 409 while one is
 *    active, so this never offers Go without first asking
 *    `GET /session` — a second tab attaches to the running session rather
 *    than appearing to start its own (T30).
 *  * **A refresh recovers.** The SSE endpoint replays its ring buffer on
 *    connect, so reattaching rebuilds the cards and status from the events
 *    already emitted rather than starting blank.
 *  * **Nothing auto-submits.** T32 enforces that server-side; there is
 *    deliberately no control here that reads as "submit the application",
 *    and the status copy says so.
 */
export function FillerBotView({
  onClose,
  initialUrl,
}: {
  onClose: () => void;
  /** T83: the job panel's "Fill it with FillerBot" action seeds this
   * overlay's URL field with the posting's own URL (`App.tsx`) rather
   * than opening it blank — read once at mount, same as any other
   * `useState` initializer, since this component unmounts/remounts each
   * time the overlay closes and reopens (App.tsx renders it
   * conditionally). */
  initialUrl?: string;
}): ReactNode {
  const [url, setUrl] = useState(initialUrl ?? "");
  const [status, setStatus] = useState("Ready");
  const [error, setError] = useState<string | null>(null);
  const [controls, setControls] = useState<ControlFlags>(IDLE_CONTROLS);
  const [cards, setCards] = useState<ActionCardModel[]>([]);
  const [edits, setEdits] = useState<Record<number, string>>({});
  const [marks, setMarks] = useState<Record<number, boolean>>({});
  const [log, setLog] = useState<string[]>([]);
  const [needsHuman, setNeedsHuman] = useState<string | null>(null);
  const [uploadRequestId, setUploadRequestId] = useState<string | null>(null);
  const [attaching, setAttaching] = useState(true);
  const [sessionActive, setSessionActive] = useState(false);

  // The missing-fields dialog (gap 2) — shown *before* a session starts,
  // like the Qt `MissingFieldsDialog` the engine was never constructed
  // without.
  const [preflight, setPreflight] = useState<FillerProfilePreflight | null>(null);
  const [extraInputs, setExtraInputs] = useState<Record<string, string>>({});
  const [preflightBusy, setPreflightBusy] = useState(false);
  const [pendingUrl, setPendingUrl] = useState<string | null>(null);

  // Cards live in a ref as well as state because `action_executed` events
  // and the fill summary are handled inside a long-lived SSE callback that
  // must not be torn down and resubscribed on every card change (doing so
  // would re-trigger the ring-buffer replay).
  const cardsRef = useRef<ActionCardModel[]>([]);
  const marksRef = useRef<Record<number, boolean>>({});
  const unsubscribeRef = useRef<(() => void) | null>(null);

  const appendLog = useCallback((line: string) => {
    setLog((prev) => [...prev.slice(-199), line]);
  }, []);

  const resetCards = useCallback(() => {
    cardsRef.current = [];
    marksRef.current = {};
    setCards([]);
    setMarks({});
    setEdits({});
  }, []);

  const handleEvent = useCallback(
    (event: FillerBotEvent) => {
      switch (event.type) {
        case "status":
          setStatus(event.message);
          appendLog(event.message);
          break;
        case "analysis": {
          const next = buildActionCards(event.analysis.actions);
          cardsRef.current = next;
          marksRef.current = {};
          setCards(next);
          setMarks({});
          setEdits({});
          setNeedsHuman(null);
          setControls((prev) =>
            controlsOnAnalysis(prev, {
              cardCount: next.length,
              nextStep: event.analysis.next_step,
            }),
          );
          if (event.analysis.human_note) {
            setStatus(event.analysis.human_note);
          }
          appendLog(`Analysed page (${next.length} field(s), next: ${event.analysis.next_step})`);
          break;
        }
        case "action_executed": {
          marksRef.current = { ...marksRef.current, [event.index]: event.ok };
          setMarks(marksRef.current);
          break;
        }
        case "fill_complete": {
          setControls(controlsOnFillComplete);
          const summary = fillFailureSummary(cardsRef.current, marksRef.current);
          if (summary) {
            setStatus(summary);
            appendLog(summary);
          }
          break;
        }
        case "needs_human":
          setNeedsHuman(event.message);
          setStatus(`Action needed: ${event.message}`);
          appendLog(`Action needed: ${event.message}`);
          setControls(controlsOnNeedsHuman);
          break;
        case "resume_upload_prompt":
          setUploadRequestId(event.request_id);
          resetCards();
          setStatus(
            "Resume upload field detected. Upload your resume so the site can " +
              "autofill, then we'll verify and fix any gaps.",
          );
          break;
        case "error":
          setError(event.message);
          appendLog(`Error: ${event.message}`);
          setControls(IDLE_CONTROLS);
          break;
        case "finished_session":
          setControls(IDLE_CONTROLS);
          setSessionActive(false);
          setUploadRequestId(null);
          appendLog("Session finished.");
          break;
      }
    },
    [appendLog, resetCards],
  );

  const attach = useCallback(() => {
    unsubscribeRef.current?.();
    unsubscribeRef.current = subscribeFillerBotStream(handleEvent, () => {
      // The stream dropped or 404'd. `EventSource` retries on its own and
      // the backend replays its ring buffer when it reconnects, so this is
      // informational rather than fatal — but a session that ended
      // underneath us would otherwise leave a stale "running" button row.
      void fetchFillerSession()
        .then((s) => {
          if (!s.active) {
            setControls(IDLE_CONTROLS);
            setSessionActive(false);
          }
        })
        .catch(() => undefined);
    });
  }, [handleEvent]);

  // Mount: attach to an existing session, or offer Go.
  useEffect(() => {
    let cancelled = false;
    void fetchFillerSession()
      .then((session) => {
        if (cancelled) {
          return;
        }
        if (session.active) {
          setSessionActive(true);
          setControls(RUNNING_CONTROLS);
          setStatus("Reattached to the session already running in another tab.");
          attach();
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setError(describeError(err));
        }
      })
      .finally(() => {
        if (!cancelled) {
          setAttaching(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [attach]);

  // Closing the panel must not stop the session: the engine owns the Chrome
  // window and the backend reaps an abandoned session on its own timeout
  // (T30). Only the stream subscription is released here.
  useEffect(() => {
    return () => {
      unsubscribeRef.current?.();
      unsubscribeRef.current = null;
    };
  }, []);

  const runControl = useCallback(
    async (action: () => Promise<void>, onOk?: () => void) => {
      setError(null);
      try {
        await action();
        onOk?.();
      } catch (err) {
        setError(describeError(err));
      }
    },
    [],
  );

  const beginSession = useCallback(
    async (targetUrl: string) => {
      setError(null);
      resetCards();
      setLog([]);
      setNeedsHuman(null);
      setStatus("Starting session…");
      try {
        await startFillerSession(targetUrl);
        setSessionActive(true);
        setControls(RUNNING_CONTROLS);
        attach();
      } catch (err) {
        setControls(IDLE_CONTROLS);
        setError(describeError(err));
        setStatus("Ready");
      }
    },
    [attach, resetCards],
  );

  const onGo = useCallback(() => {
    // A bare host becomes https:// (`_on_go`, fillerbot_window.py:294), and
    // the normalised value is written back so the user sees the URL the
    // session is actually started with.
    const target = normalizeUrl(url);
    if (target === null) {
      return;
    }
    setUrl(target);
    setError(null);
    setStatus("Checking your profile…");
    void fetchFillerProfile()
      .then((result) => {
        if (result.missing.length === 0) {
          return beginSession(target);
        }
        // Prefill with whatever was saved before, exactly as
        // `MissingFieldsDialog` seeded its line edits from `extras`.
        const seeded: Record<string, string> = {};
        for (const field of result.missing) {
          seeded[field.key] = result.extras[field.key] ?? "";
        }
        setPreflight(result);
        setExtraInputs(seeded);
        setPendingUrl(target);
        setStatus("Some fields weren't found in your resume.");
        return undefined;
      })
      .catch((err: unknown) => {
        setError(describeError(err));
        setStatus("Ready");
      });
  }, [beginSession, url]);

  const dismissPreflight = useCallback(() => {
    setPreflight(null);
    setPendingUrl(null);
    setExtraInputs({});
  }, []);

  const savePreflight = useCallback(() => {
    const target = pendingUrl;
    const filled = Object.fromEntries(
      Object.entries(extraInputs).filter(([, value]) => value.trim() !== ""),
    );
    setPreflightBusy(true);
    setError(null);
    const save = Object.keys(filled).length ? saveFillerExtras(filled) : Promise.resolve(null);
    void save
      .then(() => {
        dismissPreflight();
        if (target) {
          return beginSession(target);
        }
        return undefined;
      })
      .catch((err: unknown) => {
        setError(describeError(err));
      })
      .finally(() => {
        setPreflightBusy(false);
      });
  }, [beginSession, dismissPreflight, extraInputs, pendingUrl]);

  const skipPreflight = useCallback(() => {
    const target = pendingUrl;
    dismissPreflight();
    if (target) {
      void beginSession(target);
    }
  }, [beginSession, dismissPreflight, pendingUrl]);

  const onFill = useCallback(() => {
    setControls(controlsOnFillClicked);
    void runControl(() => executeFillerFill(buildFillOverrides(cardsRef.current, edits)));
  }, [edits, runControl]);

  const onRescan = useCallback(() => {
    setControls(controlsOnRescanClicked);
    resetCards();
    void runControl(rescanFiller);
  }, [resetCards, runControl]);

  const onNext = useCallback(() => {
    setControls(controlsOnNextClicked);
    void runControl(nextPageFiller);
  }, [runControl]);

  const onStop = useCallback(() => {
    void runControl(stopFiller, () => {
      setControls(IDLE_CONTROLS);
      setSessionActive(false);
      setStatus("Stopped.");
    });
  }, [runControl]);

  const onPause = useCallback(() => {
    void runControl(pauseFiller, () => setControls(controlsOnPaused));
  }, [runControl]);

  const onResume = useCallback(() => {
    void runControl(resumeFiller, () => setControls(controlsOnResumed));
  }, [runControl]);

  const onFront = useCallback(() => {
    void runControl(bringFillerToFront);
  }, [runControl]);

  const onAcceptUpload = useCallback(() => {
    const requestId = uploadRequestId;
    if (!requestId) {
      return;
    }
    setUploadRequestId(null);
    void runControl(() => acceptFillerUpload(requestId));
  }, [runControl, uploadRequestId]);

  const onSkipUpload = useCallback(() => {
    const requestId = uploadRequestId;
    if (!requestId) {
      return;
    }
    setUploadRequestId(null);
    void runControl(() => skipFillerUpload(requestId));
  }, [runControl, uploadRequestId]);

  return (
    <div className="fb-overlay" role="dialog" aria-modal="true" aria-label="FillerBot">
      <div className="fb-panel">
        <div className="fb-header">
          <h2 className="fb-title">FillerBot</h2>
          <button type="button" className="fb-close" aria-label="Close FillerBot" onClick={onClose}>
            ×
          </button>
        </div>

        <p className="fb-sub">
          A Chrome window will open — look for the one titled{" "}
          <code className="fb-code">[JobFinder FillerBot]</code>. The AI analyses the page and
          suggests values; edit them here, then click Fill Fields. FillerBot never submits an
          application — the final click is always yours.
        </p>

        <form
          className="fb-url-row"
          onSubmit={(e) => {
            e.preventDefault();
            onGo();
          }}
        >
          <input
            type="text"
            className="fb-input fb-url-input"
            placeholder="https://jobs.example.com/apply/12345"
            aria-label="Application form URL"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            onBlur={(e) => {
              const normalized = normalizeUrl(e.target.value);
              if (normalized !== null && normalized !== e.target.value) {
                setUrl(normalized);
              }
            }}
            disabled={!controls.go || attaching}
          />
          <button type="submit" className="fb-btn fb-btn-primary" disabled={!controls.go || attaching}>
            Go
          </button>
        </form>

        <div className="fb-status" role="status">
          {status}
        </div>
        {error ? <div className="fb-error">{error}</div> : null}
        {sessionActive && !controls.go ? (
          <div className="fb-session-note">
            A session is running. Opening FillerBot in another tab attaches to this same session.
          </div>
        ) : null}

        <div className="fb-body">
          {uploadRequestId ? (
            <div className="fb-prompt">
              <div className="fb-prompt-text">
                Upload your resume so the site can autofill, or skip and fill the fields manually.
              </div>
              <div className="fb-prompt-actions">
                <button type="button" className="fb-btn fb-btn-primary" onClick={onAcceptUpload}>
                  Upload Resume
                </button>
                <button type="button" className="fb-btn fb-btn-secondary" onClick={onSkipUpload}>
                  Skip — Fill Manually
                </button>
              </div>
            </div>
          ) : null}

          {needsHuman ? <div className="fb-needs-human">{needsHuman}</div> : null}

          {cards.length === 0 && !uploadRequestId ? (
            <div className="fb-empty">
              No fields yet. Enter the application URL and click Go.
            </div>
          ) : null}

          {cards.map((card) => (
            <ActionCard
              key={card.index}
              card={card}
              value={edits[card.index] ?? card.value}
              mark={marks[card.index]}
              onChange={(value) => setEdits((prev) => ({ ...prev, [card.index]: value }))}
            />
          ))}
        </div>

        <div className="fb-controls">
          <button
            type="button"
            className="fb-btn fb-btn-primary"
            onClick={onFill}
            disabled={!controls.fill}
          >
            Fill Fields
          </button>
          <button
            type="button"
            className="fb-btn fb-btn-secondary"
            onClick={onRescan}
            disabled={!controls.rescan}
          >
            Re-scan
          </button>
          <button
            type="button"
            className="fb-btn fb-btn-secondary"
            onClick={onNext}
            disabled={!controls.next}
          >
            Next Page
          </button>
          {controls.resume ? (
            <button
              type="button"
              className="fb-btn fb-btn-secondary"
              onClick={onResume}
              disabled={!controls.resume}
            >
              Resume
            </button>
          ) : (
            <button
              type="button"
              className="fb-btn fb-btn-secondary"
              onClick={onPause}
              disabled={!controls.pause}
            >
              Pause
            </button>
          )}
          <button
            type="button"
            className="fb-btn fb-btn-secondary"
            onClick={onFront}
            disabled={!controls.front}
            title="Raise the automated Chrome window"
          >
            Bring Chrome to Front
          </button>
          <div className="fb-controls-spacer" />
          <button
            type="button"
            className="fb-btn fb-btn-danger"
            onClick={onStop}
            disabled={!controls.stop}
          >
            Stop
          </button>
        </div>

        <details className="fb-log">
          <summary className="fb-log-summary">Event log ({log.length})</summary>
          <ol className="fb-log-list">
            {log.map((line, i) => (
              <li className="fb-log-line" key={`${String(i)}-${line}`}>
                {line}
              </li>
            ))}
          </ol>
        </details>
      </div>

      {preflight ? (
        <MissingFieldsDialog
          preflight={preflight}
          values={extraInputs}
          busy={preflightBusy}
          onChange={(key, value) => setExtraInputs((prev) => ({ ...prev, [key]: value }))}
          onSave={savePreflight}
          onSkip={skipPreflight}
        />
      ) : null}
    </div>
  );
}

/** Per-field card — `ActionCard` from `fillerbot_window.py:29`: confidence
 * dot, field label, source field (or "manual"), an editable value, and the
 * success/fail mark driven by `action_executed`. */
function ActionCard({
  card,
  value,
  mark,
  onChange,
}: {
  card: ActionCardModel;
  value: string;
  mark: boolean | undefined;
  onChange: (value: string) => void;
}): ReactNode {
  const failed = mark === false;
  return (
    <div className={`fb-card${failed ? " fb-card-failed" : ""}`}>
      <div className="fb-card-top">
        <span
          className={`fb-dot fb-dot-${confidenceTier(card.confidence)}`}
          aria-label={`Confidence ${String(Math.round(card.confidence * 100))}%`}
          title={`Confidence ${String(Math.round(card.confidence * 100))}%`}
        />
        <span className="fb-card-label">{card.fieldLabel}</span>
        <span className="fb-card-source">{card.sourceField || "manual"}</span>
        <span className={`fb-card-mark${failed ? " fb-card-mark-failed" : ""}`}>
          {mark === undefined ? "" : mark ? "✓" : "✗"}
        </span>
      </div>
      <input
        type="text"
        className="fb-input"
        aria-label={`${card.fieldLabel} value`}
        title="Edit this value before clicking Fill Fields"
        value={value}
        // Never read-only: every field is editable before filling (T31
        // DoD), and the Qt card left its line edit live after a fill too —
        // a failed field is fixed by editing it and filling again.
        onChange={(e) => onChange(e.target.value)}
      />
    </div>
  );
}

/** `MissingFieldsDialog` (`fillerbot_window.py:116`) — prompts for the
 * promptable fields the resume didn't supply, before the session starts.
 * Save persists them (`save_extras`) so the next session doesn't ask again;
 * Skip starts the session anyway, as the Qt dialog's Reject role did. */
function MissingFieldsDialog({
  preflight,
  values,
  busy,
  onChange,
  onSave,
  onSkip,
}: {
  preflight: FillerProfilePreflight;
  values: Record<string, string>;
  busy: boolean;
  onChange: (key: string, value: string) => void;
  onSave: () => void;
  onSkip: () => void;
}): ReactNode {
  return (
    <div className="fb-dialog-backdrop">
      <div className="fb-dialog" role="dialog" aria-modal="true" aria-label="Complete your profile">
        <h3 className="fb-dialog-title">Complete your profile</h3>
        <p className="fb-dialog-hint">
          Some fields weren't found in your resume. Fill them in below — they'll be saved for next
          time.
        </p>
        <form
          className="fb-dialog-form"
          onSubmit={(e) => {
            e.preventDefault();
            onSave();
          }}
        >
          {preflight.missing.map((field) => (
            <label className="fb-dialog-row" key={field.key}>
              <span className="fb-dialog-label">{field.label}</span>
              <input
                type="text"
                className="fb-input"
                placeholder={field.label}
                value={values[field.key] ?? ""}
                maxLength={500}
                onChange={(e) => onChange(field.key, e.target.value)}
              />
            </label>
          ))}
          <div className="fb-dialog-actions">
            <button type="submit" className="fb-btn fb-btn-primary" disabled={busy}>
              {busy ? "Saving…" : "Save"}
            </button>
            <button type="button" className="fb-btn fb-btn-secondary" onClick={onSkip} disabled={busy}>
              Skip
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

function describeError(err: unknown): string {
  if (err instanceof FillerBotRequestError) {
    if (err.status === 409) {
      return `${err.message} (409)`;
    }
    return err.message;
  }
  if (err instanceof Error) {
    return err.message;
  }
  return "Request failed.";
}
