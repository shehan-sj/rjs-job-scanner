import type { ReactNode } from "react";
import { SliderRow } from "../components/SliderRow";
import type { FormState } from "./settingsForm";
import { MODELS } from "./settingsForm";
import { SETTINGS_FIELDS } from "./settingsFieldMap";

type Patch = (patch: Partial<FormState>) => void;

function Section({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: ReactNode;
}): ReactNode {
  return (
    <div className="settings-section">
      <div className="settings-section-label">{label}</div>
      {hint ? <div className="settings-section-hint">{hint}</div> : null}
      {children}
    </div>
  );
}

function Divider(): ReactNode {
  return <div className="settings-divider" />;
}

/** A group heading — the words that tell the user what the fields below
 * have in common. For `location` this is load-bearing (T53): it is what
 * distinguishes "where I'll take a job" from "where we search". */
function GroupHeading({ label, hint }: { label: string; hint: string }): ReactNode {
  return (
    <div className="settings-group-heading">
      <div className="settings-group-label">{label}</div>
      <div className="settings-section-hint">{hint}</div>
    </div>
  );
}

/** Role intent — one home (T53).
 *
 * Job titles (`sources[].search_terms`) used to live on Basic setup while
 * job functions and seniority (`role_filter.*`) lived here, which is what
 * the owner reported as "duplication between Basic setup and Role". It was
 * not duplication: titles decide *what is searched for* and these two
 * decide *which of those results survive*, so reading either half alone
 * told you nothing about what you would actually get. All three now sit
 * together, under a heading that says why.
 *
 * Labels and hints come from `SETTINGS_FIELDS` rather than being inlined,
 * so tests/test_settings_field_map.py is asserting over the same strings
 * the user reads. */
export function RoleTab({ form, patch }: { form: FormState; patch: Patch }): ReactNode {
  const titles = SETTINGS_FIELDS.jobTitlesText;
  const functions = SETTINGS_FIELDS.jobFunctionsText;
  const seniority = SETTINGS_FIELDS.seniorityLevelsText;

  return (
    <div className="settings-tab">
      <GroupHeading label={titles.groupLabel} hint={titles.groupHint} />

      <Section label={titles.label} hint={titles.hint}>
        <textarea
          className="settings-textarea"
          rows={6}
          value={form.jobTitlesText}
          onChange={(e) => patch({ jobTitlesText: e.target.value })}
        />
      </Section>

      <Divider />

      <Section label={functions.label} hint={functions.hint}>
        <textarea
          className="settings-textarea"
          rows={5}
          /* The examples span unrelated fields on purpose (T51).
             This placeholder used to read "Engineering / Information Technology
             / Quality Assurance", which told every user who was not an engineer
             that the product was not built for them. A placeholder's job here
             is to show the one-value-per-line format, so the values it shows
             should not add up to a presumed profession. */
          placeholder={"Marketing\nLegal\nHealth Care Provider"}
          value={form.jobFunctionsText}
          onChange={(e) => patch({ jobFunctionsText: e.target.value })}
        />
      </Section>
      <Section label={seniority.label} hint={seniority.hint}>
        <textarea
          className="settings-textarea"
          rows={5}
          placeholder={"Mid-Senior level\nDirector"}
          value={form.seniorityLevelsText}
          onChange={(e) => patch({ seniorityLevelsText: e.target.value })}
        />
      </Section>
    </div>
  );
}

/** Location — one home, two clearly-named concepts (T53).
 *
 * This tab exists because "location" meant two different things in two
 * different places, under labels similar enough to be mistaken for each
 * other:
 *
 *   * `location_filter.preferred_regions` / `location_filter.search_country`
 *     / `location_filter.remote_ok_worldwide` — where the user will
 *     *accept* a job. These sort and filter matches that have already been
 *     fetched; narrowing them never changes what gets scraped. The first
 *     two were on Basic setup, the third was over on Filters.
 *   * `sources[].location` — where the *search* is pointed. This is passed
 *     to the job boards, so narrowing it means fewer postings are ever
 *     fetched. It was on Sources.
 *
 * Co-locating them without naming them would have made things worse —
 * `search_country` and the search location would then sit side by side
 * under near-identical names. So each group gets a heading that states
 * what it governs and explicitly contrasts itself with the other; those
 * strings live in `SETTINGS_FIELDS` and are asserted by
 * tests/test_settings_field_map.py.
 *
 * Phase 8 note: when T60 deletes this modal, keep these two groups as two
 * labelled groups. Collapsing them back under one "Location" heading
 * re-creates exactly the defect T53 fixed. */
export function LocationTab({ form, patch }: { form: FormState; patch: Patch }): ReactNode {
  const regions = SETTINGS_FIELDS.preferredRegionsText;
  const country = SETTINGS_FIELDS.searchCountry;
  const remote = SETTINGS_FIELDS.remoteOkWorldwide;
  const searchLocation = SETTINGS_FIELDS.searchLocation;

  return (
    <div className="settings-tab">
      <GroupHeading label={regions.groupLabel} hint={regions.groupHint} />

      <Section label={regions.label} hint={regions.hint}>
        <textarea
          className="settings-textarea"
          rows={5}
          placeholder={"Toronto\nVancouver\nWaterloo\nMontreal"}
          value={form.preferredRegionsText}
          onChange={(e) => patch({ preferredRegionsText: e.target.value })}
        />
      </Section>

      <Section label={country.label} hint={country.hint}>
        <input
          type="text"
          className="settings-input"
          value={form.searchCountry}
          onChange={(e) => patch({ searchCountry: e.target.value })}
        />
      </Section>

      <Section label={remote.label} hint={remote.hint}>
        <label className="settings-checkbox">
          <input
            type="checkbox"
            checked={form.remoteOkWorldwide}
            onChange={(e) => patch({ remoteOkWorldwide: e.target.checked })}
          />
          {remote.label}
        </label>
      </Section>

      <Divider />

      <GroupHeading label={searchLocation.groupLabel} hint={searchLocation.groupHint} />

      <Section label={searchLocation.label} hint={searchLocation.hint}>
        <input
          type="text"
          className="settings-input"
          value={form.searchLocation}
          onChange={(e) => patch({ searchLocation: e.target.value })}
        />
      </Section>
    </div>
  );
}

/** Port of `_tab_filters` (settings.py). Employment / work-arrangement
 * visibility + quality filters.
 *
 * T53 moved `remote_ok_worldwide` out of here and onto the Location tab.
 * It was the odd one out: every other toggle on this tab hides jobs from
 * a list, while that one widens which locations count as acceptable — it
 * is part of the `location_filter` concept, and splitting it off is why
 * "location" read as unrelated settings in three different places. */
export function FiltersTab({ form, patch }: { form: FormState; patch: Patch }): ReactNode {
  return (
    <div className="settings-tab">
      <Section label="Employment type" hint="Hide jobs with these employment tags from the Matches list.">
        <label className="settings-checkbox">
          <input
            type="checkbox"
            checked={form.hideFullTime}
            onChange={(e) => patch({ hideFullTime: e.target.checked })}
          />
          Hide full-time
        </label>
        <label className="settings-checkbox">
          <input
            type="checkbox"
            checked={form.hidePartTime}
            onChange={(e) => patch({ hidePartTime: e.target.checked })}
          />
          Hide part-time
        </label>
        <label className="settings-checkbox">
          <input
            type="checkbox"
            checked={form.hideContract}
            onChange={(e) => patch({ hideContract: e.target.checked })}
          />
          Hide contract / C2H
        </label>
        <label className="settings-checkbox">
          <input
            type="checkbox"
            checked={form.hideTemporary}
            onChange={(e) => patch({ hideTemporary: e.target.checked })}
          />
          Hide temporary
        </label>
        <label className="settings-checkbox">
          <input
            type="checkbox"
            checked={form.hideInternship}
            onChange={(e) => patch({ hideInternship: e.target.checked })}
          />
          Hide internships
        </label>
      </Section>

      <Section label="Work arrangement" hint="Hide jobs by how they're structured.">
        <label className="settings-checkbox">
          <input
            type="checkbox"
            checked={form.hideRemote}
            onChange={(e) => patch({ hideRemote: e.target.checked })}
          />
          Hide remote
        </label>
        <label className="settings-checkbox">
          <input
            type="checkbox"
            checked={form.hideHybrid}
            onChange={(e) => patch({ hideHybrid: e.target.checked })}
          />
          Hide hybrid
        </label>
        <label className="settings-checkbox">
          <input
            type="checkbox"
            checked={form.hideOnsite}
            onChange={(e) => patch({ hideOnsite: e.target.checked })}
          />
          Hide on-site
        </label>
      </Section>

      <Divider />

      <Section
        label="Quality filters"
        hint="Cut common sources of noise. Staffing-agency filtering is on by default since many LinkedIn listings are agency reposts rather than direct hires."
      >
        <label className="settings-checkbox">
          <input
            type="checkbox"
            checked={form.filterContracts}
            onChange={(e) => patch({ filterContracts: e.target.checked })}
          />
          Hide contract / temp / C2H / 1099
        </label>
        <label className="settings-checkbox">
          <input
            type="checkbox"
            checked={form.hideStaffingAgencies}
            onChange={(e) => patch({ hideStaffingAgencies: e.target.checked })}
          />
          Hide staffing / recruiting agencies
        </label>
        <label className="settings-checkbox">
          <input
            type="checkbox"
            checked={form.hidePromoted}
            onChange={(e) => patch({ hidePromoted: e.target.checked })}
          />
          Hide promoted (paid) listings
        </label>
      </Section>

      <Section
        label="Minimum salary (annual)"
        hint="Jobs listing a max salary below this get hidden. Postings with no salary data are never hidden by this rule. 0 disables."
      >
        <SliderRow
          label="Minimum salary"
          min={0}
          max={300000}
          value={form.minSalary}
          onChange={(v) => patch({ minSalary: v })}
        />
      </Section>

      <Section label="Minimum company size" hint="Jobs at smaller employers get hidden. 0 disables.">
        <SliderRow
          label="Minimum company size"
          min={0}
          max={10000}
          value={form.minCompanySize}
          onChange={(v) => patch({ minCompanySize: v })}
        />
      </Section>

      <Section
        label="Only jobs posted in the last N days"
        hint="0 disables — show everything regardless of age."
      >
        <SliderRow
          label="Max post age"
          min={0}
          max={60}
          suffix=" days"
          value={form.maxPostAgeDays}
          onChange={(v) => patch({ maxPostAgeDays: v })}
        />
      </Section>
    </div>
  );
}

/** Port of `_tab_ai` (settings.py). AI double-check + FillerBot. */
export function AiVerificationTab({ form, patch }: { form: FormState; patch: Patch }): ReactNode {
  return (
    <div className="settings-tab">
      <Section
        label="Claude model"
        hint="Used to parse your resume and (if enabled below) double-check matches. Haiku is cheapest and usually enough."
      >
        <select
          className="settings-select"
          value={form.model}
          onChange={(e) => patch({ model: e.target.value })}
        >
          {MODELS.map((m) => (
            <option key={m} value={m}>
              {m}
            </option>
          ))}
        </select>
      </Section>

      <Divider />

      <Section
        label="AI double-check"
        hint="Optional second pass: for each match, Claude re-reads the full job posting, verifies location and role type, and re-scores it. Jobs that clearly don't fit move to Removed → 'Removed by AI'. Each check is one API call — costs add up on large scans."
      >
        <label className="settings-checkbox">
          <input
            type="checkbox"
            checked={form.agenticEnabled}
            onChange={(e) => patch({ agenticEnabled: e.target.checked })}
          />
          Let AI double-check matches
        </label>
      </Section>

      <Section
        label="Minimum score to verify"
        hint="Only ask the AI about matches scoring at least this value — higher = fewer API calls."
      >
        <SliderRow
          label="Minimum score to verify"
          min={0}
          max={100}
          suffix="%"
          value={form.agenticMinScore}
          onChange={(v) => patch({ agenticMinScore: v })}
        />
      </Section>
      <p className="settings-section-hint">
        AI verification requires an API key (set it on the Basic setup tab).
      </p>

      <Divider />

      <Section
        label="FillerBot"
        hint="AI-powered application form filler. Opens job application pages in a browser and fills fields using your resume data. Uses the Claude model selected above."
      >
        <label className="settings-checkbox">
          <input
            type="checkbox"
            checked={form.fillerbotEnabled}
            onChange={(e) => patch({ fillerbotEnabled: e.target.checked })}
          />
          Enable FillerBot
        </label>
      </Section>

      <Section label="Page timeout" hint="How long to wait for pages to load before giving up.">
        <SliderRow
          label="Page timeout"
          min={10}
          max={120}
          suffix=" sec"
          value={form.fbTimeoutSeconds}
          onChange={(v) => patch({ fbTimeoutSeconds: v })}
        />
      </Section>

      <label className="settings-checkbox">
        <input
          type="checkbox"
          checked={form.fbAutoFillLow}
          onChange={(e) => patch({ fbAutoFillLow: e.target.checked })}
        />
        Auto-fill low-confidence fields (&lt; 50%)
      </label>
    </div>
  );
}

/** Port of `_tab_appearance` (settings.py). `auto_dismiss_seconds`
 * (desktop tray notifications) has no control here — the task ledger: it's
 * configurable in two places and read by none (`ui/tray.py:139`
 * hardcodes 5000) — and the browser Notifications API this app uses
 * instead has no auto-dismiss setting of its own.
 *
 * T98 moved "Scan frequency" (`checkIntervalMinutes`) out of here to the
 * Sources tab — it is a scanning setting, not an appearance one, and
 * filing it under "how the app looks" is why the owner couldn't find
 * "the time delay for the scan to restart". `ui.console_hidden_levels`
 * and the other log console display toggles stay here; only the scan
 * interval moved. */
export function AppearanceTab({ form, patch }: { form: FormState; patch: Patch }): ReactNode {
  return (
    <div className="settings-tab">
      <Section label="Log console" hint="Shows scan activity and errors in a collapsible panel.">
        <label className="settings-checkbox">
          <input
            type="checkbox"
            checked={form.showLogConsole}
            onChange={(e) => patch({ showLogConsole: e.target.checked })}
          />
          Show log console
        </label>
        <label className="settings-checkbox">
          <input
            type="checkbox"
            checked={form.consoleShowTimestamp}
            onChange={(e) => patch({ consoleShowTimestamp: e.target.checked })}
          />
          Show timestamps in log
        </label>
        <label className="settings-checkbox">
          <input
            type="checkbox"
            checked={form.consoleShowLevel}
            onChange={(e) => patch({ consoleShowLevel: e.target.checked })}
          />
          Show log level (INFO, WARN, ERROR)
        </label>
      </Section>

      <Divider />

      <Section label="Appearance" hint="Pick a colour theme. Changes apply immediately.">
        <select
          className="settings-select"
          value={form.theme}
          onChange={(e) => patch({ theme: e.target.value === "light" ? "light" : "dark" })}
        >
          <option value="dark">Dark</option>
          <option value="light">Light</option>
        </select>
      </Section>
    </div>
  );
}

/** Port of `_tab_advanced` (settings.py). v3 had two separate actions —
 * "Clear all job data" and "Delete resume-profile cache" — against two
 * separate code paths (`on_clear_db` vs. a direct file unlink). The web
 * API only exposes the combined `POST /api/reset` (clears job data *and*
 * the profile cache together, `jobfinder/web/scan.py`'s `_reset_sync`);
 * there is no narrower endpoint to call for cache-only clearing without
 * adding one, which is out of this task's scope. So this tab exposes
 * only the one action the backend actually has. */
export function AdvancedTab({ onResetAllData }: { onResetAllData: () => void }): ReactNode {
  return (
    <div className="settings-tab">
      <Section
        label="Reset your data"
        hint="Erase everything JobFinder remembers — seen jobs, match history, applied / removed lists, and your cached resume profile. Your settings are kept."
      >
        <button type="button" className="settings-btn settings-btn-danger" onClick={onResetAllData}>
          Clear all job data
        </button>
      </Section>
    </div>
  );
}
