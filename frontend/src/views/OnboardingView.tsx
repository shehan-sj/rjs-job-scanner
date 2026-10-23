import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { fetchFullSettings, putSettings, uploadResume } from "../api/settings";
import type { SettingsPayload } from "../api/settings";
import {
  acknowledgeDisclosure,
  completeOnboarding,
  fetchPrefill,
  testApiKey,
  PREFILL_JOB_FUNCTIONS,
  PREFILL_SEARCH_COUNTRY,
  PREFILL_SEARCH_TERMS,
  PREFILL_SENIORITY_LEVELS,
  type Prefill,
  type PrefillField,
} from "../api/onboarding";
import { buildSettingsUpdate, initFormState, type FormState } from "./settingsForm";
import { SETTINGS_FIELDS } from "./settingsFieldMap";
import {
  applyCountryEdit,
  breakLink,
  initialLinkState,
  restoreLink,
} from "./onboardingLocationLink";
import "./OnboardingView.css";

/**
 * First-run onboarding (T26, reordered around the resume by T52, redesigned
 * by T61 into **docs/DESIGN.md**'s one page, four sections:
 *
 *   1. Résumé          — upload first, because everything after it is
 * pre-filled from what it says (the product spec:
 *                        upload, not a path picker)
 *   2. How we read it  — which extraction ran (Claude vs. this machine,
 *                        keyless) and what the no-key path means day to day
 *   3. What we read    — the resume-derived fields, each with its
 *                        provenance (T52), plus the country/search-location
 *                        link control (T61 closes the T52/T53 seam)
 *   4. API key         — optional, with a live "Test connection" round trip
 *                        and the one-time data-disclosure acknowledgement
 *
 * T61's own DoD is "a user who abandons midway is not left in a
 * half-configured state". The four-step wizard this replaces could only
 * hold that by hand, one step at a time; a single page with one Start
 * button holds it *structurally* instead — nothing is written until Start
 * is pressed (`handleStart`), same as before, just with no intermediate
 * "Next" writes to lose track of.
 *
 * Every pre-filled value stays editable and shows where it came from, via
 * `GET /api/onboarding/prefill`. The wizard works with no API key — the
 * pre-fill is then read locally and says so — and a resume that yields
 * nothing simply leaves the fields empty, stated as such rather than left
 * looking merely unfilled (`absent` renders as "the résumé did not say").
 *
 * Finishing calls `POST /api/onboarding/complete` after `PUT /api/settings`
 * — see `jobfinder/web/onboarding.py`'s `needs_onboarding` for the defect
 * this closes: before it existed, finishing with no key meant seeing this
 * page again on every reload, forever.
 *
 * Reuses `settingsForm.ts`'s `initFormState`/`buildSettingsUpdate` (T25)
 * rather than re-deriving the `PUT /api/settings` body from scratch: fields
 * this page never touches (match threshold, tag visibility, quality
 * filters, …) round-trip through unchanged instead of silently reverting
 * to `SettingsUpdate`'s defaults.
 */

// `jobfinder/ui/wizard.py`'s `_TitlesPage` pre-filled a brand-new install
// with a generic *software engineering* set, and T26 copied it. T52 drops
// it: the resume now supplies the titles, and showing a nurse "Backend
// Engineer" is the same baked-in assumption about someone's line of work
// that the product spec exists to undo — worse than a blank, because a
// pre-filled wrong answer is one the user may not think to change. A resume
// that yields nothing leaves this empty with a placeholder instead.

// Pinned, not derived from any config — a small, mixed-market starter set
// so the checkboxes are useful on first load regardless of where the user
// ends up typing custom entries.
const SHORTCUT_CITIES = ["Toronto", "Vancouver", "Waterloo", "Montreal", "New York", "San Francisco"];

/** Which form fields a prefill field is allowed to seed. */
const PREFILL_TARGETS: Record<string, (keyof FormState)[]> = {
  // Country lands in both `location_filter.search_country` and
  // `sources[].location` (the task's mapping), which are `searchCountry`
  // and `searchLocation` in form terms.
  [PREFILL_SEARCH_COUNTRY]: ["searchCountry", "searchLocation"],
  [PREFILL_SEARCH_TERMS]: ["jobTitlesText"],
  [PREFILL_JOB_FUNCTIONS]: ["jobFunctionsText"],
  [PREFILL_SENIORITY_LEVELS]: ["seniorityLevelsText"],
};

function prefillText(field: PrefillField): string {
  return Array.isArray(field.value) ? field.value.join("\n") : (field.value ?? "");
}

/**
 * Seeds the blank form fields from the resume, and records what came from
 * where so each one can say so.
 *
 * **Only blank fields are seeded.** A value already in config is either the
 * user's own earlier edit or their saved setting, and the product spec's first
 * precedence rule — "a user edit is never silently overwritten, not by a
 * re-parse, not by a better extraction" — is the rule the whole feature's
 * trustworthiness rests on. Re-entering onboarding must not revert a
 * setting to what the resume says.
 */
function applyPrefill(
  form: FormState,
  prefill: Prefill,
): { patch: Partial<FormState>; origins: Record<string, PrefillField> } {
  const patch: Partial<FormState> = {};
  const origins: Record<string, PrefillField> = {};

  for (const [key, targets] of Object.entries(PREFILL_TARGETS)) {
    const field = prefill.fields[key];
    if (!field) continue;
    const text = prefillText(field);
    if (!text.trim()) continue;
    for (const target of targets) {
      const current = form[target];
      if (typeof current !== "string" || current.trim()) continue;
      (patch as Record<string, unknown>)[target] = text;
      origins[target] = field;
    }
  }

  return { patch, origins };
}

function linesOf(text: string): string[] {
  return text
    .split("\n")
    .map((l) => l.trim())
    .filter((l) => l.length > 0);
}

function hasCity(text: string, city: string): boolean {
  const lowered = city.toLowerCase();
  return linesOf(text).some((l) => l.toLowerCase() === lowered);
}

function toggleCity(text: string, city: string, checked: boolean): string {
  const lines = linesOf(text);
  if (checked) {
    if (lines.some((l) => l.toLowerCase() === city.toLowerCase())) return text;
    return [...lines, city].join("\n");
  }
  return lines.filter((l) => l.toLowerCase() !== city.toLowerCase()).join("\n");
}

export function OnboardingView({ onDone }: { onDone: () => void }): ReactNode {
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [settings, setSettings] = useState<SettingsPayload | null>(null);
  const [form, setForm] = useState<FormState | null>(null);

  const [resumeUploaded, setResumeUploaded] = useState(false);
  const [resumeFilename, setResumeFilename] = useState<string | null>(null);
  const [resumeBusy, setResumeBusy] = useState(false);
  const [resumeMessage, setResumeMessage] = useState<string | null>(null);
  const [resumeMessageIsError, setResumeMessageIsError] = useState(false);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<{ ok: boolean; error: string | null } | null>(null);

  const [prefill, setPrefill] = useState<Prefill | null>(null);
  // Keyed by form field, so a value's origin is shown next to the control
  // the user edits rather than on a summary screen.
  const [origins, setOrigins] = useState<Record<string, PrefillField>>({});

  // The country/search-location link (T61, docs/DESIGN.md "the
  // country/search-location seam closes by asking both, with a breakable
  // link"). Starts in whatever state the loaded form already implies —
  // see `initialLinkState`'s own docs for why that is not always "linked".
  const [locationLinked, setLocationLinked] = useState(true);

  const [disclosureAck, setDisclosureAck] = useState(false);

  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const payload = await fetchFullSettings();
      setSettings(payload);
      const initial = initFormState(payload);
      // The resume is read before anything downstream is shown, so a user
      // who already uploaded one sees the pre-fill on arrival and not only
      // after a re-upload. `fetchPrefill` resolves to null on failure
      // rather than throwing: an assist that cannot be fetched must still
      // leave the page usable.
      const derived = await fetchPrefill();
      if (derived) {
        setPrefill(derived);
        const { patch: seeded, origins: seededOrigins } = applyPrefill(initial, derived);
        Object.assign(initial, seeded);
        setOrigins(seededOrigins);
      }
      setForm(initial);
      setLocationLinked(initialLinkState(initial.searchCountry, initial.searchLocation));
      // T45: reflects the server's real resume state — previously this
      // page always started at "no resume" even when one had already been
      // uploaded (e.g. a user who left and came back).
      setResumeUploaded(payload.resume.uploaded);
      setResumeFilename(payload.resume.filename);
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : "failed to load settings");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const patch = useCallback((p: Partial<FormState>) => {
    setForm((prev) => (prev ? { ...prev, ...p } : prev));
  }, []);

  /** Re-reads the resume-derived pre-fill and seeds whatever is still
   * blank. Called after an upload, which is the one moment the derived
   * answers can change. */
  const refreshPrefill = useCallback(async () => {
    const derived = await fetchPrefill();
    if (!derived) return;
    setPrefill(derived);
    setForm((prev) => {
      if (!prev) return prev;
      const { patch: seeded, origins: seededOrigins } = applyPrefill(prev, derived);
      setOrigins((old) => ({ ...old, ...seededOrigins }));
      const next = { ...prev, ...seeded };
      setLocationLinked((wasLinked) => wasLinked && initialLinkState(next.searchCountry, next.searchLocation));
      return next;
    });
  }, []);

  const handleResumePick = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0];
      e.target.value = "";
      if (!file) return;
      setResumeBusy(true);
      setResumeMessage(null);
      setResumeMessageIsError(false);
      uploadResume(file)
        .then((result) => {
          setResumeUploaded(true);
          // Server-reported (sanitised) display name, not the raw
          // `file.name` the client sent (T45).
          setResumeFilename(result.filename);
          setResumeMessage(
            result.profile_status === "ok"
              ? "Looks good — resume uploaded and profile built."
              : result.profile_status === "skipped-no-api-key"
                ? "Resume uploaded. Add an API key below to have Claude build a profile, " +
                  "or keep going without one."
                : "Resume uploaded, but profile extraction failed — it'll retry on the next scan.",
          );
          // Everything below follows from the resume, so read it back now
          // Independent of `profile_status`:
          // "skipped-no-api-key" still yields the local, keyless read.
          void refreshPrefill();
        })
        .catch((err) => {
          // Server-side messages worth surfacing verbatim here:
          // "Word documents aren't supported — please export your resume
          // as PDF" (.docx), "could not read this file as a resume…"
          // (corrupt/password-protected), "no extractable text found…"
          // (image-only/scanned PDF) — jobfinder/web/resume.py.
          setResumeMessageIsError(true);
          setResumeMessage(err instanceof Error ? err.message : "upload failed");
        })
        .finally(() => setResumeBusy(false));
    },
    [refreshPrefill],
  );

  const handleTestKey = useCallback(() => {
    if (!form) return;
    const key = form.apiKeyInput.trim();
    if (!key) {
      setTestResult({ ok: false, error: "paste your key first" });
      return;
    }
    setTesting(true);
    setTestResult(null);
    void testApiKey(key, form.model).then((result) => {
      setTestResult(result);
      setTesting(false);
    });
  }, [form]);

  const handleStart = useCallback(async () => {
    if (!form || !settings) return;
    setStarting(true);
    setStartError(null);
    try {
      const update = buildSettingsUpdate(form, settings.sources);
      await putSettings(update);
      // Marks the page itself as done — see `jobfinder/web/onboarding.py`'s
      // `needs_onboarding` for why this is the piece that was missing:
      // without it, finishing with no key meant this page came back on
      // every reload regardless of what was just saved.
      await completeOnboarding();
      // Best-effort — `acknowledgeDisclosure()` never throws; Start must
      // not be blocked by a failure to record *when* the disclosure
      // (already shown and checked client-side) was accepted.
      await acknowledgeDisclosure();
      onDone();
    } catch (err) {
      setStartError(err instanceof Error ? err.message : "could not save your settings");
    } finally {
      setStarting(false);
    }
  }, [form, settings, onDone]);

  // Nothing is written until Start is pressed — no per-field PUT, no
  // partial save. That is what makes "a user who abandons midway is not
  // left in a half-configured state" true structurally rather than by
  // policy: there is no half-saved state to leave anyone in.
  const canStart = form !== null && resumeUploaded && disclosureAck;

  return (
    <div className="onboarding-overlay" role="dialog" aria-modal="true" aria-label="Welcome to JobFinder">
      <div className="onboarding-panel">
        <div className="onboarding-header">
          <h2 className="onboarding-title">Welcome to JobFinder</h2>
          <button type="button" className="onboarding-skip" onClick={onDone}>
            Skip for now
          </button>
        </div>

        <div className="onboarding-body">
          {loading ? <div className="onboarding-loading">Loading…</div> : null}
          {loadError ? (
            <div className="onboarding-error">
              Couldn't load settings: {loadError}{" "}
              <button type="button" className="onboarding-btn onboarding-btn-secondary" onClick={() => void load()}>
                Retry
              </button>
            </div>
          ) : null}

          {!loading && !loadError && form ? (
            <>
              <ResumeSection
                resumeUploaded={resumeUploaded}
                resumeFilename={resumeFilename}
                resumeBusy={resumeBusy}
                resumeMessage={resumeMessage}
                resumeMessageIsError={resumeMessageIsError}
                fileInputRef={fileInputRef}
                onResumePick={handleResumePick}
              />

              <HowWeReadItSection prefill={prefill} />

              <WhatWeReadSection
                form={form}
                patch={patch}
                origins={origins}
                prefill={prefill}
                locationLinked={locationLinked}
                onCountryChange={(value) => patch(applyCountryEdit(locationLinked, value))}
                onLocationChange={(value) => patch({ searchLocation: value })}
                onBreakLink={() => setLocationLinked(breakLink().linked)}
                onRestoreLink={() => {
                  const result = restoreLink(form.searchCountry);
                  setLocationLinked(result.linked);
                  patch({ searchLocation: result.searchLocation });
                }}
              />

              <ApiKeySection
                form={form}
                patch={patch}
                testing={testing}
                testResult={testResult}
                onTest={handleTestKey}
                onKeyChanged={() => setTestResult(null)}
                disclosureAck={disclosureAck}
                onDisclosureAckChange={setDisclosureAck}
              />
            </>
          ) : null}
        </div>

        <div className="onboarding-footer">
          {startError ? <span className="onboarding-finish-error">{startError}</span> : null}
          <button
            type="button"
            className="onboarding-btn onboarding-btn-primary"
            disabled={!canStart || starting}
            onClick={() => void handleStart()}
          >
            {starting ? "Saving…" : "Start — find my first jobs"}
          </button>
        </div>
      </div>
    </div>
  );
}

function ResumeSection({
  resumeUploaded,
  resumeFilename,
  resumeBusy,
  resumeMessage,
  resumeMessageIsError,
  fileInputRef,
  onResumePick,
}: {
  resumeUploaded: boolean;
  resumeFilename: string | null;
  resumeBusy: boolean;
  resumeMessage: string | null;
  resumeMessageIsError: boolean;
  fileInputRef: React.RefObject<HTMLInputElement | null>;
  onResumePick: (e: React.ChangeEvent<HTMLInputElement>) => void;
}): ReactNode {
  return (
    <section className="onboarding-section">
      <div className="onboarding-section-title">1. Where's your resume?</div>
      <div className="onboarding-step-hint">
        JobFinder uses your resume to learn what you're qualified for, and to pre-fill the rest of
        this page. PDF or plain text — Word documents aren't supported, export to PDF first.
      </div>
      <div className="onboarding-resume-row">
        <span className="onboarding-resume-filename">
          {resumeUploaded && resumeFilename ? resumeFilename : "No resume uploaded yet"}
        </span>
        <button
          type="button"
          className="onboarding-btn onboarding-btn-secondary"
          disabled={resumeBusy}
          onClick={() => fileInputRef.current?.click()}
        >
          {resumeBusy ? "Uploading…" : resumeUploaded ? "Replace…" : "Upload…"}
        </button>
        <input
          ref={fileInputRef}
          type="file"
          accept=".pdf,.txt,application/pdf,text/plain"
          className="onboarding-file-input"
          onChange={onResumePick}
        />
      </div>
      {resumeMessage ? (
        <div className={resumeMessageIsError ? "onboarding-message-error" : "onboarding-message-ok"}>
          {resumeMessage}
        </div>
      ) : null}
    </section>
  );
}

/**
 * Section 2 of 4 — "how we read it". States plainly which extraction ran
 * (Claude, this machine without one, or nothing yet) and what the no-key
 * path means in practice: JobFinder still scans and still filters without a
 * key, it only scores by keyword overlap instead of asking Claude. Shown
 * once, ahead of the pre-filled fields it explains, rather than repeated
 * per-field as the four-step wizard did.
 */
function HowWeReadItSection({ prefill }: { prefill: Prefill | null }): ReactNode {
  const body = !prefill || !prefill.resume_on_file ? (
    <div className="onboarding-step-hint">
      No resume on file yet, so there's nothing to read — upload one above and this section fills
      in.
    </div>
  ) : (
    <div className="onboarding-provenance" data-extraction={prefill.extraction}>
      {prefill.extraction === "agentic"
        ? "Read by Claude. "
        : prefill.extraction === "heuristic"
          ? "Read on this machine, without an API key. "
          : ""}
      {prefill.extraction_note}
    </div>
  );
  return (
    <section className="onboarding-section">
      <div className="onboarding-section-title">2. How we read it</div>
      {body}
      <div className="onboarding-step-hint">
        No API key, no problem: JobFinder still scans every source and still filters the results —
        it just scores matches by keyword overlap with your resume instead of asking Claude to
        judge them. You can add a key below any time, now or later.
      </div>
    </section>
  );
}

/**
 * Where a pre-filled value came from, shown next to the control that edits
 * it. The product spec: every derived field "is shown with its provenance — a
 * value the user cannot tell the origin of is a value they cannot judge".
 * `absent` renders as a stated blank via `field.note`, never as a bare
 * empty-looking control.
 */
function Provenance({ field }: { field: PrefillField | undefined }): ReactNode {
  if (!field) return null;
  const isAbsent = field.provenance === "absent";
  return (
    <div className="onboarding-provenance" data-provenance={field.provenance}>
      <span className="onboarding-provenance-origin">
        {isAbsent ? "The résumé did not say." : `From your resume: ${field.origin}`}
      </span>
      {field.note ? <span className="onboarding-provenance-note"> — {field.note}</span> : null}
    </div>
  );
}

/**
 * Section 3 of 4 — "what we read": the resume-derived fields, each with its
 * own provenance, plus the country/search-location link control (T61).
 *
 * `role_filter`'s two fields (functions, seniority) are always safe to
 * leave empty — empty means "every function", "every level" — so nothing
 * here gates Start. The job functions field is the one place the no-key
 * path genuinely has nothing to offer: it's LinkedIn's closed vocabulary,
 * read only by Claude, and deriving it from a job title would need the
 * profession keyword table T49's heuristic layer refuses to contain
 * 
 */
function WhatWeReadSection({
  form,
  patch,
  origins,
  prefill,
  locationLinked,
  onCountryChange,
  onLocationChange,
  onBreakLink,
  onRestoreLink,
}: {
  form: FormState;
  patch: (p: Partial<FormState>) => void;
  origins: Record<string, PrefillField>;
  prefill: Prefill | null;
  locationLinked: boolean;
  onCountryChange: (value: string) => void;
  onLocationChange: (value: string) => void;
  onBreakLink: () => void;
  onRestoreLink: () => void;
}): ReactNode {
  const functions = prefill?.fields[PREFILL_JOB_FUNCTIONS];
  const seniority = prefill?.fields[PREFILL_SENIORITY_LEVELS];
  const countryLabel = SETTINGS_FIELDS.searchCountry.label;
  const locationLabel = SETTINGS_FIELDS.searchLocation.label;

  return (
    <section className="onboarding-section">
      <div className="onboarding-section-title">3. What we read</div>

      <div className="onboarding-field-group">
        <label className="onboarding-field-label" htmlFor="onboarding-country">
          {countryLabel}
        </label>
        <input
          id="onboarding-country"
          type="text"
          className="onboarding-input"
          placeholder="Country — e.g. Ireland"
          value={form.searchCountry}
          onChange={(e) => onCountryChange(e.target.value)}
        />
        <Provenance field={origins.searchCountry} />

        <div className="onboarding-link-row">
          <span className="onboarding-link-line" aria-hidden="true" />
          <button
            type="button"
            className="onboarding-link-btn"
            aria-pressed={locationLinked}
            onClick={locationLinked ? onBreakLink : onRestoreLink}
          >
            {locationLinked ? "🔗 Linked — search the same place" : "⛓️‍💥 Unlinked — search somewhere else"}
          </button>
        </div>

        <label className="onboarding-field-label" htmlFor="onboarding-location">
          {locationLabel}
        </label>
        <input
          id="onboarding-location"
          type="text"
          className="onboarding-input"
          placeholder="Place name sent to each job board"
          value={form.searchLocation}
          disabled={locationLinked}
          onChange={(e) => onLocationChange(e.target.value)}
        />
        {locationLinked ? (
          <div className="onboarding-step-hint">
            While linked, this follows the country above. Unlink it to search a different place —
            breaking the link is an ordinary edit, the same as typing in any other field.
          </div>
        ) : (
          <Provenance field={origins.searchLocation} />
        )}
      </div>

      <div className="onboarding-step-hint">
        Pick a shortcut, add your own below, or toggle remote-only. These land on the Preferred
        tab — refine any time from Settings.
      </div>
      <div className="onboarding-city-grid">
        {SHORTCUT_CITIES.map((city) => (
          <label className="onboarding-checkbox" key={city}>
            <input
              type="checkbox"
              checked={hasCity(form.preferredRegionsText, city)}
              onChange={(e) =>
                patch({ preferredRegionsText: toggleCity(form.preferredRegionsText, city, e.target.checked) })
              }
            />
            {city}
          </label>
        ))}
      </div>
      <textarea
        className="onboarding-textarea"
        rows={3}
        placeholder={"Custom cities or regions — one per line"}
        value={form.preferredRegionsText}
        onChange={(e) => patch({ preferredRegionsText: e.target.value })}
      />
      <label className="onboarding-checkbox">
        <input
          type="checkbox"
          checked={form.remoteOkWorldwide}
          onChange={(e) => patch({ remoteOkWorldwide: e.target.checked })}
        />
        I'll also take remote roles from anywhere
      </label>

      <div className="onboarding-field-group">
        <label className="onboarding-field-label" htmlFor="onboarding-titles">
          What jobs are you looking for?
        </label>
        <textarea
          id="onboarding-titles"
          className="onboarding-textarea"
          rows={4}
          placeholder={"Job titles to search for — one per line"}
          value={form.jobTitlesText}
          onChange={(e) => patch({ jobTitlesText: e.target.value })}
        />
        <Provenance field={origins.jobTitlesText} />
        {!form.jobTitlesText.trim() && prefill?.fields.search_terms?.note ? (
          <div className="onboarding-step-hint">{prefill.fields.search_terms.note}</div>
        ) : null}
      </div>

      <div className="onboarding-step-hint">Narrow it down — optional, leave either empty to search everything.</div>

      <div className="onboarding-field-group">
        <label className="onboarding-field-label" htmlFor="onboarding-functions">
          {functions?.label ?? "Job functions"}
        </label>
        <textarea
          id="onboarding-functions"
          className="onboarding-textarea"
          rows={2}
          placeholder={"LinkedIn job functions — e.g. Health Care Provider"}
          value={form.jobFunctionsText}
          onChange={(e) => patch({ jobFunctionsText: e.target.value })}
        />
        <Provenance field={origins.jobFunctionsText} />
        {!form.jobFunctionsText.trim() && functions?.note ? (
          <div className="onboarding-step-hint">{functions.note}</div>
        ) : null}
      </div>

      <div className="onboarding-field-group">
        <label className="onboarding-field-label" htmlFor="onboarding-seniority">
          {seniority?.label ?? "Seniority levels"}
        </label>
        <textarea
          id="onboarding-seniority"
          className="onboarding-textarea"
          rows={2}
          placeholder={"LinkedIn seniority levels — e.g. Mid-Senior level"}
          value={form.seniorityLevelsText}
          onChange={(e) => patch({ seniorityLevelsText: e.target.value })}
        />
        <Provenance field={origins.seniorityLevelsText} />
        {!form.seniorityLevelsText.trim() && seniority?.note ? (
          <div className="onboarding-step-hint">{seniority.note}</div>
        ) : null}
      </div>
    </section>
  );
}

/**
 * Section 4 of 4 — API key, optional. Carries the no-API-key path's
 * first-class status (the product spec: the case pre-filling matters most) and
 * ends with the one-time data-disclosure acknowledgement, which must be
 * checked before Start — the same gate the old wizard's last step held.
 */
function ApiKeySection({
  form,
  patch,
  testing,
  testResult,
  onTest,
  onKeyChanged,
  disclosureAck,
  onDisclosureAckChange,
}: {
  form: FormState;
  patch: (p: Partial<FormState>) => void;
  testing: boolean;
  testResult: { ok: boolean; error: string | null } | null;
  onTest: () => void;
  onKeyChanged: () => void;
  disclosureAck: boolean;
  onDisclosureAckChange: (checked: boolean) => void;
}): ReactNode {
  return (
    <section className="onboarding-section">
      <div className="onboarding-section-title">4. Add your Anthropic API key (optional)</div>
      <div className="onboarding-step-hint">
        JobFinder uses Claude to read your resume once and to double-check matches. Get a key at
        console.anthropic.com — the free tier is plenty to start. Without one, JobFinder still
        scans and still filters, just by keyword overlap instead (see "How we read it" above) —
        you can add a key here now or from Settings later.
      </div>
      <input
        type="password"
        className="onboarding-input"
        placeholder={form.apiKeySet ? `Key set — ends in ${form.apiKeyLast4 ?? "????"}` : "sk-ant-…"}
        value={form.apiKeyInput}
        onChange={(e) => {
          patch({ apiKeyInput: e.target.value });
          onKeyChanged();
        }}
      />
      <div className="onboarding-step-row">
        <button
          type="button"
          className="onboarding-btn onboarding-btn-secondary"
          disabled={testing}
          onClick={onTest}
        >
          {testing ? "Testing…" : "Test connection"}
        </button>
      </div>
      {testResult ? (
        <div className={testResult.ok ? "onboarding-message-ok" : "onboarding-message-error"}>
          {testResult.ok ? "✓ Key works — you're set." : `Key didn't work: ${testResult.error ?? "unknown error"}`}
        </div>
      ) : null}
      <div className="onboarding-step-hint">
        You can continue without testing — the key is only saved when you press Start.
      </div>

      <div className="onboarding-disclosure">
        <div className="onboarding-step-title">Before you start — what JobFinder sends to Anthropic</div>
        <div className="onboarding-step-hint">
          JobFinder uses the Anthropic API (Claude) to help find and verify matches, whenever a key
          is configured. Per the product spec, this is what that involves:
        </div>
        <ul className="onboarding-disclosure-list">
          <li>Job descriptions, so Claude can judge how well they match your profile.</li>
          <li>The profile Claude builds from your resume (skills, titles, seniority, etc.).</li>
          <li>
            If you use FillerBot to auto-fill applications: screenshots of the application form,
            which may contain whatever you've typed into it. These aren't retained on disk by
            default.
          </li>
        </ul>
        <div className="onboarding-step-hint">
          This is shown once. You can read the full policy any time in the README.
        </div>
        <label className="onboarding-checkbox">
          <input
            type="checkbox"
            checked={disclosureAck}
            onChange={(e) => onDisclosureAckChange(e.target.checked)}
          />
          I understand what's sent to Anthropic
        </label>
      </div>
    </section>
  );
}
