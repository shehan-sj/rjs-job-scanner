import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { fetchFullSettings, putSettings, resetAllData, uploadResume } from "../api/settings";
import type { SettingsPayload } from "../api/settings";
import { SliderRow } from "../components/SliderRow";
import { TabBar, type TabItem } from "../components/TabBar";
import { SETTINGS_FIELDS } from "./settingsFieldMap";
import {
  CORE_SOURCES,
  CREDENTIAL_SOURCES,
  buildSettingsUpdate,
  initFormState,
  type FormState,
} from "./settingsForm";
import {
  AdvancedTab,
  AiVerificationTab,
  AppearanceTab,
  FiltersTab,
  LocationTab,
  RoleTab,
} from "./SettingsTabs";
import "./SettingsView.css";

type SettingsTab = "basic" | "role" | "location" | "sources" | "filters" | "ai" | "appearance" | "advanced";

// T53 added "location" as a home of its own and moved job titles into
// "role", so the tab order now follows the questions in the order a user
// answers them: who am I (basic) -> what work do I want (role) -> where
// (location) -> where to look for it (sources) -> what to hide (filters).
//
// T60 — this is the "Sources & keys" rail destination now
// (docs/DESIGN.md principle 4): rarely-touched configuration, reached
// from the rail rather than a modal. Two tabs left this set rather than
// being dropped: Résumé is its own rail destination (ResumeTab.tsx was
// already built self-contained for exactly this move — see its own
// docstring); the frequently-touched filters (match threshold, freshness,
// arrangement, hide agencies/internships/contract/promoted) live in the
// inline `FilterPanel` beside Matches (T59) instead. What's left here —
// Basic setup/Role/Location/Sources/Filters/AI verification/
// Appearance/Advanced — is the rest: configuration a user sets once and
// rarely revisits. See settingsFieldMap.ts for which group owns what,
// and why; none of the parity matrix settings were dropped by this move
// (T62 re-verifies the mechanical count).
const TAB_ITEMS: TabItem<SettingsTab>[] = [
  { key: "basic", label: "Basic setup", count: 0 },
  { key: "role", label: "Role", count: 0 },
  { key: "location", label: "Location", count: 0 },
  { key: "sources", label: "Sources", count: 0 },
  { key: "filters", label: "Filters", count: 0 },
  { key: "ai", label: "AI verification", count: 0 },
  { key: "appearance", label: "Appearance", count: 0 },
  { key: "advanced", label: "Advanced", count: 0 },
];

/**
 * Sources & keys page (T25, repointed by T60) — ported from
 * `jobfinder/ui/settings.py`'s `SettingsDialog`. Was a full-screen modal
 * overlay through T59; T60 makes it a normal rail destination instead
 * (docs/DESIGN.md principle 4: "Settings stop being a modal"), which is
 * why there's no `onClose`/Cancel here any more — there's nothing to
 * dismiss back to, the rail just navigates away.
 *
 * Was v3's seven tabs one-for-one; T53 added an eighth ("Location") so
 * that location had a single home instead of being spread across Basic
 * setup, Sources and Filters. The tab *set* is therefore no longer a
 * straight port — the settings themselves still are. See
 * settingsFieldMap.ts for which group owns what, and why.
 */
export function SettingsView(): ReactNode {
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [settings, setSettings] = useState<SettingsPayload | null>(null);
  const [form, setForm] = useState<FormState | null>(null);
  const [activeTab, setActiveTab] = useState<SettingsTab>("basic");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  // T45: sourced from GET /api/settings' `resume` section (updated by
  // `load()` below), not localStorage — this is now a real server fact
  // (what did the server actually keep?), not a client-only memory that
  // showed "No resume on file yet" on every browser/profile but the one
  // that happened to upload it.
  const [resumeFilename, setResumeFilename] = useState<string | null>(null);
  const [resumeBusy, setResumeBusy] = useState(false);
  const [resumeMessage, setResumeMessage] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const payload = await fetchFullSettings();
      setSettings(payload);
      setForm(initFormState(payload));
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

  const patchSource = useCallback(
    (name: string, p: Partial<FormState["sources"][string]>) => {
      setForm((prev) => {
        if (!prev) return prev;
        return {
          ...prev,
          sources: { ...prev.sources, [name]: { ...prev.sources[name], ...p } },
        };
      });
    },
    [],
  );

  // T60 — this used to call `onClose()` to dismiss the modal on success;
  // there's no modal to dismiss any more, so a saved page just shows
  // "Saved." and stays put, the same pattern any ordinary settings page
  // uses.
  const [savedMessage, setSavedMessage] = useState<string | null>(null);

  const handleSave = useCallback(async () => {
    if (!form || !settings) return;
    setSaving(true);
    setSaveError(null);
    setSavedMessage(null);
    try {
      const update = buildSettingsUpdate(form, settings.sources);
      const result = await putSettings(update);
      setSettings(result);
      setForm(initFormState(result));
      setSavedMessage("Saved.");
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : "save failed");
    } finally {
      setSaving(false);
    }
  }, [form, settings]);

  const handleResumePick = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    setResumeBusy(true);
    setResumeMessage(null);
    uploadResume(file)
      .then((result) => {
        // Server-reported (sanitised) display name, not the raw
        // `file.name` the client sent — the server is the source of
        // truth now, not a local echo of what was just picked.
        setResumeFilename(result.filename);
        setResumeMessage(
          result.profile_status === "ok"
            ? "Resume uploaded — profile refreshed."
            : result.profile_status === "skipped-no-api-key"
              ? "Resume uploaded. Add an API key above to build a profile."
              : "Resume uploaded, but profile extraction failed — it'll retry on the next scan.",
        );
      })
      .catch((err) => {
        setResumeMessage(err instanceof Error ? err.message : "upload failed");
      })
      .finally(() => setResumeBusy(false));
  }, []);

  const handleResetAllData = useCallback(() => {
    const confirmed = window.confirm(
      "This erases every job record JobFinder has seen, plus the cached resume profile.\n\n" +
        "Your settings are kept.\n\nContinue?",
    );
    if (!confirmed) return;
    void resetAllData().then(
      () => window.alert("Cleared. Your next scan will rebuild from scratch."),
      (err: unknown) => window.alert(err instanceof Error ? err.message : "reset failed"),
    );
  }, []);

  return (
    <div className="settings-page">
      <div className="settings-header">
        <h2 className="settings-title">Sources &amp; keys</h2>
      </div>

      <TabBar items={TAB_ITEMS} active={activeTab} onSelect={setActiveTab} variant="main" />

      <div className="settings-body">
        {loading ? <div className="settings-loading">Loading…</div> : null}
        {loadError ? (
          <div className="settings-error">
            Couldn't load settings: {loadError}{" "}
            <button type="button" className="settings-btn settings-btn-secondary" onClick={() => void load()}>
              Retry
            </button>
          </div>
        ) : null}

        {!loading && !loadError && form && settings ? (
          <>
            {activeTab === "basic" ? (
              <BasicTab
                form={form}
                patch={patch}
                resumeFilename={resumeFilename}
                resumeBusy={resumeBusy}
                resumeMessage={resumeMessage}
                fileInputRef={fileInputRef}
                onResumePick={handleResumePick}
              />
            ) : null}
            {activeTab === "sources" ? (
              <SourcesTab form={form} patch={patch} patchSource={patchSource} />
            ) : null}
            {activeTab === "role" ? <RoleTab form={form} patch={patch} /> : null}
            {activeTab === "location" ? <LocationTab form={form} patch={patch} /> : null}
            {activeTab === "filters" ? <FiltersTab form={form} patch={patch} /> : null}
            {activeTab === "ai" ? <AiVerificationTab form={form} patch={patch} /> : null}
            {activeTab === "appearance" ? <AppearanceTab form={form} patch={patch} /> : null}
            {activeTab === "advanced" ? <AdvancedTab onResetAllData={handleResetAllData} /> : null}
          </>
        ) : null}
      </div>

      <div className="settings-footer">
        {saveError ? <span className="settings-save-error">{saveError}</span> : null}
        {!saveError && savedMessage ? <span className="settings-saved-message">{savedMessage}</span> : null}
        <button
          type="button"
          className="settings-btn settings-btn-primary"
          disabled={!form || saving}
          onClick={() => void handleSave()}
        >
          {saving ? "Saving…" : "Save"}
        </button>
      </div>
    </div>
  );
}

/** Port of `_tab_basic` (settings.py) — resume, API key, match threshold.
 *
 * T53 moved the search area (`preferred_regions` / `search_country`) to
 * the Location tab and job titles (`sources[].search_terms`) to Role, so
 * what remains is what the tab's name actually promises: getting set up.
 * Those fields were not duplicated elsewhere — they were the *other half*
 * of concepts whose first half lived on another tab. */
function BasicTab({
  form,
  patch,
  resumeFilename,
  resumeBusy,
  resumeMessage,
  fileInputRef,
  onResumePick,
}: {
  form: FormState;
  patch: (p: Partial<FormState>) => void;
  resumeFilename: string | null;
  resumeBusy: boolean;
  resumeMessage: string | null;
  fileInputRef: React.RefObject<HTMLInputElement | null>;
  onResumePick: (e: React.ChangeEvent<HTMLInputElement>) => void;
}): ReactNode {
  return (
    <div className="settings-tab">
      <div className="settings-section">
        <div className="settings-section-label">Your resume</div>
        <div className="settings-section-hint">
          PDF or plain text file. JobFinder reads this once to build your profile.
        </div>
        <div className="settings-resume-row">
          <span className="settings-resume-filename">
            {resumeFilename ? resumeFilename : "No resume on file yet"}
          </span>
          <button
            type="button"
            className="settings-btn settings-btn-secondary"
            disabled={resumeBusy}
            onClick={() => fileInputRef.current?.click()}
          >
            {resumeBusy ? "Uploading…" : resumeFilename ? "Replace…" : "Upload…"}
          </button>
          <input
            ref={fileInputRef}
            type="file"
            accept=".pdf,.txt,application/pdf,text/plain"
            className="settings-file-input"
            onChange={onResumePick}
          />
        </div>
        <div className="settings-section-hint">Accepted formats: PDF and plain text only.</div>
        {resumeMessage ? <div className="settings-resume-message">{resumeMessage}</div> : null}
      </div>

      <div className="settings-divider" />

      <div className="settings-section">
        <div className="settings-section-label">Anthropic API key</div>
        <div className="settings-section-hint">
          Used to parse your resume and (optionally) double-check matches. Get one at
          console.anthropic.com.
        </div>
        <input
          type="password"
          className="settings-input"
          placeholder={form.apiKeySet ? `Key set — ends in ${form.apiKeyLast4 ?? "????"}` : "sk-ant-…"}
          value={form.apiKeyInput}
          onChange={(e) => patch({ apiKeyInput: e.target.value })}
        />
        <div className="settings-section-hint">
          Leave blank to keep the current key. The stored key is never shown here.
        </div>
      </div>

      <div className="settings-divider" />

      <div className="settings-section">
        <div className="settings-section-label">Match threshold</div>
        <div className="settings-section-hint">
          Only show jobs scoring at or above this value. 50% is a good start.
        </div>
        <SliderRow
          label="Match threshold"
          min={0}
          max={100}
          suffix="%"
          value={form.matchThreshold}
          onChange={(v) => patch({ matchThreshold: v })}
        />
      </div>
    </div>
  );
}

/** Port of `_tab_sources` (settings.py) — shared search settings, remote
 * sweep, and per-source enable/freshness, plus the v4-added jsearch/
 * adzuna credential sources.
 *
 * T53 moved "Search location" (`sources[].location`) to the Location tab,
 * next to the settings it kept being confused with. It is still the same
 * key written to every source on save (`buildSources`); only where it is
 * *asked for* changed.
 *
 * T98 moved "Scan frequency" (`check_interval_minutes`) here from
 * Appearance — this tab already owns the rest of the scan cycle (results
 * per title, remote sweep, per-source freshness all say "per scan cycle"
 * in their own hints), so a scanning setting under "how the app looks"
 * was the thing that could not be found. Label/hint come from
 * `SETTINGS_FIELDS` so tests/test_settings_field_map.py can assert this
 * tab, not Appearance, is where it renders. */
function SourcesTab({
  form,
  patch,
  patchSource,
}: {
  form: FormState;
  patch: (p: Partial<FormState>) => void;
  patchSource: (name: string, p: Partial<FormState["sources"][string]>) => void;
}): ReactNode {
  const scanFrequency = SETTINGS_FIELDS.checkIntervalMinutes;

  return (
    <div className="settings-tab">
      <div className="settings-section">
        <div className="settings-section-label">{scanFrequency.label}</div>
        <div className="settings-section-hint">{scanFrequency.hint}</div>
        <SliderRow
          label={scanFrequency.label}
          min={1}
          max={120}
          suffix=" min"
          value={form.checkIntervalMinutes}
          onChange={(v) => patch({ checkIntervalMinutes: v })}
        />
      </div>

      <div className="settings-divider" />

      <div className="settings-section">
        <div className="settings-section-label">Results per title</div>
        <div className="settings-section-hint">
          How many jobs to pull per search title, per scan cycle.
        </div>
        <SliderRow
          label="Results per title"
          min={5}
          max={200}
          suffix=" jobs"
          value={form.resultsPerTitle}
          onChange={(v) => patch({ resultsPerTitle: v })}
        />
      </div>

      <div className="settings-divider" />

      <div className="settings-section">
        <div className="settings-section-label">Remote sweep (LinkedIn only)</div>
        <div className="settings-section-hint">
          Runs a second LinkedIn pass with 'remote' appended to your top 4 titles — catches
          remote-only postings the main search misses.
        </div>
        <label className="settings-checkbox">
          <input
            type="checkbox"
            checked={form.remoteSweepEnabled}
            onChange={(e) => patch({ remoteSweepEnabled: e.target.checked })}
          />
          Enable remote sweep
        </label>
        <SliderRow
          label="Remote sweep results"
          min={5}
          max={100}
          suffix=" jobs"
          value={form.sweepResults}
          onChange={(v) => patch({ sweepResults: v })}
        />
      </div>

      <div className="settings-divider" />

      <div className="settings-section">
        <div className="settings-section-label">Job sources</div>
        <div className="settings-section-hint">
          Enable sources and set freshness per source. A higher freshness window means more
          results but older posts.
        </div>
      </div>

      {CORE_SOURCES.map(({ name, label, hint }) => (
        <SourceRow
          key={name}
          name={name}
          label={label}
          hint={hint}
          enabled={form.sources[name].enabled}
          hoursOld={form.sources[name].hoursOld}
          onToggle={(enabled) => patchSource(name, { enabled })}
          onHoursChange={(hoursOld) => patchSource(name, { hoursOld })}
        />
      ))}

      <div className="settings-divider" />
      <div className="settings-section">
        <div className="settings-section-label">Additional sources (require a key)</div>
      </div>

      {CREDENTIAL_SOURCES.map(({ name, label, hint, fields }) => (
        <div key={name}>
          <SourceRow
            name={name}
            label={label}
            hint={hint}
            enabled={form.sources[name].enabled}
            hoursOld={form.sources[name].hoursOld}
            onToggle={(enabled) => patchSource(name, { enabled })}
            onHoursChange={(hoursOld) => patchSource(name, { hoursOld })}
          />
          {fields.map((field) => {
            const state = form.sources[name].credentialState[field.key];
            return (
              <div className="settings-credential-row" key={field.key}>
                <label className="settings-credential-label">{field.label}</label>
                <input
                  type="password"
                  className="settings-input"
                  placeholder={
                    state?.set ? `Key set — ends in ${state.last4 ?? "????"}` : "Not set"
                  }
                  value={form.sources[name].credentialInputs[field.key] ?? ""}
                  onChange={(e) =>
                    patchSource(name, {
                      credentialInputs: {
                        ...form.sources[name].credentialInputs,
                        [field.key]: e.target.value,
                      },
                    })
                  }
                />
              </div>
            );
          })}
        </div>
      ))}
    </div>
  );
}

function SourceRow({
  label,
  hint,
  enabled,
  hoursOld,
  onToggle,
  onHoursChange,
}: {
  name: string;
  label: string;
  hint: string;
  enabled: boolean;
  hoursOld: number;
  onToggle: (enabled: boolean) => void;
  onHoursChange: (hoursOld: number) => void;
}): ReactNode {
  return (
    <div className="settings-source-row">
      <div className="settings-divider" />
      <label className="settings-checkbox settings-source-checkbox">
        <input type="checkbox" checked={enabled} onChange={(e) => onToggle(e.target.checked)} />
        {label}
      </label>
      <div className="settings-section-hint">{hint}</div>
      <div className="settings-source-hours-label">Job freshness</div>
      <SliderRow label={`${label} freshness`} min={1} max={168} suffix=" hrs" value={hoursOld} onChange={onHoursChange} />
    </div>
  );
}
