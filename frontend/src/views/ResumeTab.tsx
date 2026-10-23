import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import {
  ResumeProfileRequestError,
  fetchDerivedProfile,
  patchDerivedProfile,
  reparseResume,
  type DerivedProfilePayload,
  type Proposal,
  type ReparseIn,
} from "../api/resumeProfile";
import {
  agenticDisabledReason,
  formatFieldForDisplay,
  formatFieldForEditing,
  parseFieldInput,
  rowStates,
  type FieldRowState,
} from "../api/resumeProfileLogic";
import { fetchFullSettings, uploadResume } from "../api/settings";
import "./ResumeTab.css";

/**
 * The Résumé tab (T54) — every resume-derived field, with its provenance,
 * editable where the owner table (`jobfinder/derived_profile.py`'s
 * `FIELD_SPECS`) allows it, plus the two actions that change what is
 * derived: replacing the resume file, and re-parsing it.
 *
 * **Self-contained on purpose.** Unlike every other `*Tab` in
 * `SettingsTabs.tsx`, this component takes no `form`/`patch` props from
 * `SettingsView` — it fetches and owns its own data. The task ledger lifts
 * this tab to a top-level navigation destination once the settings modal
 * is retired (docs/DESIGN.md principle 4); a component that depended on
 * `SettingsView`'s state would have to be rewritten to make that move, and
 * the whole point of calling that out now is that it should not need to
 * be.
 *
 * The data model: `rowStates` (api/resumeProfileLogic.ts) merges the
 * resolved field (`GET /api/resume/derived-profile`) with any pending
 * re-parse proposal for the same field. A field a user edit already
 * decides shows both its current value and the disagreeing proposal,
 * and applies neither until the user explicitly accepts one — the product spec
 * 3.6's "applies neither until accepted", and the reason `rowStates`
 * never substitutes a proposal for a current value (pinned by
 * resumeProfileLogic.test.ts).
 */
export function ResumeTab(): ReactNode {
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [profile, setProfile] = useState<DerivedProfilePayload | null>(null);
  const [apiKeySet, setApiKeySet] = useState(false);

  const [proposals, setProposals] = useState<Proposal[]>([]);
  const [reparseMethod, setReparseMethod] = useState<ReparseIn["method"]>("heuristic");
  const [acknowledged, setAcknowledged] = useState(false);
  const [reparsing, setReparsing] = useState(false);
  const [reparseError, setReparseError] = useState<string | null>(null);

  const [editingField, setEditingField] = useState<string | null>(null);
  const [editValue, setEditValue] = useState("");
  const [savingField, setSavingField] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);

  const [resumeBusy, setResumeBusy] = useState(false);
  const [resumeMessage, setResumeMessage] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const [dp, settings] = await Promise.all([fetchDerivedProfile(), fetchFullSettings()]);
      setProfile(dp);
      setApiKeySet(settings.anthropic.api_key_set === true);
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : "failed to load your resume profile");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const disabledReason = agenticDisabledReason(apiKeySet);

  const startEdit = useCallback((row: FieldRowState) => {
    setEditingField(row.name);
    setEditValue(formatFieldForEditing(row.value as string | number | string[]));
    setSaveError(null);
  }, []);

  const cancelEdit = useCallback(() => {
    setEditingField(null);
    setEditValue("");
  }, []);

  const saveEdit = useCallback(
    async (row: FieldRowState) => {
      const parsed = parseFieldInput(row.kind, editValue);
      if (parsed === null) {
        setSaveError(`${row.label}: that value doesn't fit this field.`);
        return;
      }
      setSavingField(row.name);
      setSaveError(null);
      try {
        const updated = await patchDerivedProfile({ [row.name]: parsed });
        setProfile(updated);
        setEditingField(null);
        // Accepting/editing this field makes any pending proposal for it
        // stale — it would otherwise keep showing a "proposed" value
        // already superseded by the edit just made.
        setProposals((prev) => prev.filter((p) => p.name !== row.name));
      } catch (err) {
        setSaveError(err instanceof Error ? err.message : "save failed");
      } finally {
        setSavingField(null);
      }
    },
    [editValue],
  );

  const acceptProposal = useCallback(async (proposal: Proposal) => {
    // Entry-list fields (work_experience/education) are shown for
    // information only — they aren't in FIELD_SHAPES, so a PATCH of one
    // would just come back 422. Nothing should ever call this with one;
    // this guard is defence in depth, not the real gate (the real gate is
    // `userEditable`, checked before an Accept button is even rendered).
    if (Array.isArray(proposal.proposedValue) && proposal.proposedValue.some((v) => typeof v === "object")) {
      return;
    }
    setSavingField(proposal.name);
    setSaveError(null);
    try {
      const updated = await patchDerivedProfile({
        [proposal.name]: proposal.proposedValue as string | number | string[],
      });
      setProfile(updated);
      setProposals((prev) => prev.filter((p) => p.name !== proposal.name));
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : "accept failed");
    } finally {
      setSavingField(null);
    }
  }, []);

  const dismissProposal = useCallback((name: string) => {
    setProposals((prev) => prev.filter((p) => p.name !== name));
  }, []);

  const runReparse = useCallback(async () => {
    setReparsing(true);
    setReparseError(null);
    try {
      const body: ReparseIn =
        reparseMethod === "agentic" ? { method: "agentic", acknowledged: true } : { method: "heuristic", acknowledged: false };
      const result = await reparseResume(body);
      setProposals(result);
    } catch (err) {
      setReparseError(
        err instanceof ResumeProfileRequestError ? err.message : err instanceof Error ? err.message : "re-parse failed",
      );
    } finally {
      setReparsing(false);
    }
  }, [reparseMethod]);

  const handleResumePick = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0];
      e.target.value = "";
      if (!file) return;
      setResumeBusy(true);
      setResumeMessage(null);
      uploadResume(file)
        .then((result) => {
          setResumeMessage(
            result.profile_status === "ok"
              ? "Resume replaced — profile rebuilt."
              : result.profile_status === "skipped-no-api-key"
                ? "Resume replaced. Add an API key on the Basic setup tab to build an AI profile."
                : "Resume replaced, but AI profile extraction failed — it'll retry on the next scan.",
          );
          // A new resume file makes every pending proposal (computed
          // against the old file) meaningless.
          setProposals([]);
          void load();
        })
        .catch((err) => {
          setResumeMessage(err instanceof Error ? err.message : "upload failed");
        })
        .finally(() => setResumeBusy(false));
    },
    [load],
  );

  if (loading) {
    return <div className="resume-tab-loading">Loading…</div>;
  }
  if (loadError || !profile) {
    return (
      <div className="resume-tab-error">
        Couldn't load your resume profile: {loadError}{" "}
        <button type="button" className="resume-tab-btn resume-tab-btn-secondary" onClick={() => void load()}>
          Retry
        </button>
      </div>
    );
  }

  const rows = rowStates(profile.fields, proposals);

  return (
    <div className="resume-tab">
      <section className="resume-tab-section">
        <div className="resume-tab-section-label">Your resume</div>
        <div className="resume-tab-section-hint">
          {profile.resumeOnFile ? "A resume is on file." : "No resume on file yet."}{" "}
          {apiKeySet
            ? "Replacing it sends its full text to Anthropic to rebuild your AI profile."
            : "No Anthropic API key is configured, so replacing it only runs the local, keyless extraction — nothing leaves this machine."}
        </div>
        <button
          type="button"
          className="resume-tab-btn resume-tab-btn-secondary"
          disabled={resumeBusy}
          onClick={() => fileInputRef.current?.click()}
        >
          {resumeBusy ? "Uploading…" : profile.resumeOnFile ? "Replace…" : "Upload…"}
        </button>
        <input
          ref={fileInputRef}
          type="file"
          accept=".pdf,.txt,application/pdf,text/plain"
          className="resume-tab-file-input"
          onChange={handleResumePick}
        />
        {resumeMessage ? <div className="resume-tab-message">{resumeMessage}</div> : null}
      </section>

      <div className="resume-tab-divider" />

      <section className="resume-tab-section">
        <div className="resume-tab-section-label">Re-parse</div>
        <div className="resume-tab-section-hint">
          Re-run extraction and review what changed, field by field. Nothing is applied until you accept it.
        </div>
        <label className="resume-tab-radio">
          <input
            type="radio"
            name="reparse-method"
            checked={reparseMethod === "heuristic"}
            onChange={() => setReparseMethod("heuristic")}
          />
          Heuristic — local, no API key, sends nothing. Approximate by design.
        </label>
        <label className="resume-tab-radio">
          <input
            type="radio"
            name="reparse-method"
            checked={reparseMethod === "agentic"}
            disabled={!apiKeySet}
            onChange={() => setReparseMethod("agentic")}
          />
          Agentic — sends your resume text to Anthropic for a better extraction.
          {disabledReason ? <span className="resume-tab-disabled-reason"> {disabledReason}</span> : null}
        </label>
        {reparseMethod === "agentic" ? (
          <label className="resume-tab-checkbox">
            <input type="checkbox" checked={acknowledged} onChange={(e) => setAcknowledged(e.target.checked)} />
            I understand this sends my resume's full text to Anthropic.
          </label>
        ) : null}
        <button
          type="button"
          className="resume-tab-btn resume-tab-btn-primary"
          disabled={reparsing || !profile.resumeOnFile || (reparseMethod === "agentic" && (!apiKeySet || !acknowledged))}
          onClick={() => void runReparse()}
        >
          {reparsing ? "Re-parsing…" : "Re-parse"}
        </button>
        {reparseError ? <div className="resume-tab-error-text">{reparseError}</div> : null}
      </section>

      <div className="resume-tab-divider" />

      <section className="resume-tab-section">
        <div className="resume-tab-section-label">Fields</div>
        {saveError ? <div className="resume-tab-error-text">{saveError}</div> : null}
        <div className="resume-tab-rows">
          {rows.map((row) => (
            <ResumeFieldRow
              key={row.name}
              row={row}
              editing={editingField === row.name}
              editValue={editValue}
              saving={savingField === row.name}
              onEditValueChange={setEditValue}
              onStartEdit={() => startEdit(row)}
              onCancelEdit={cancelEdit}
              onSaveEdit={() => void saveEdit(row)}
              onAcceptProposal={(proposal) => void acceptProposal(proposal)}
              onDismissProposal={() => dismissProposal(row.name)}
            />
          ))}
        </div>
      </section>
    </div>
  );
}

const PROVENANCE_LABEL: Record<FieldRowState["provenance"], string> = {
  user: "You edited this",
  agentic: "Extracted by Claude",
  heuristic: "Approximate (no AI key)",
  absent: "Not found",
};

function ResumeFieldRow({
  row,
  editing,
  editValue,
  saving,
  onEditValueChange,
  onStartEdit,
  onCancelEdit,
  onSaveEdit,
  onAcceptProposal,
  onDismissProposal,
}: {
  row: FieldRowState;
  editing: boolean;
  editValue: string;
  saving: boolean;
  onEditValueChange: (value: string) => void;
  onStartEdit: () => void;
  onCancelEdit: () => void;
  onSaveEdit: () => void;
  onAcceptProposal: (proposal: Proposal) => void;
  onDismissProposal: () => void;
}): ReactNode {
  const { proposal } = row;
  // `absent` is a real, distinct provenance — rendered as a
  // dash plus its own badge, not left to look like an empty/missing value
  // the way an unset string field otherwise would.
  const displayValue = row.provenance === "absent" ? "—" : formatFieldForDisplay(row.value);

  return (
    <div className="resume-tab-row">
      <div className="resume-tab-row-label">{row.label}</div>
      <div className={`resume-tab-provenance resume-tab-provenance-${row.provenance}`}>
        {PROVENANCE_LABEL[row.provenance]}
      </div>

      {editing ? (
        <div className="resume-tab-edit">
          {row.kind === "str_list" ? (
            <textarea
              className="resume-tab-textarea"
              rows={3}
              value={editValue}
              onChange={(e) => onEditValueChange(e.target.value)}
            />
          ) : (
            <input
              className="resume-tab-input"
              type={row.kind === "int" ? "number" : "text"}
              value={editValue}
              onChange={(e) => onEditValueChange(e.target.value)}
            />
          )}
          <button type="button" className="resume-tab-btn resume-tab-btn-primary" disabled={saving} onClick={onSaveEdit}>
            {saving ? "Saving…" : "Save"}
          </button>
          <button type="button" className="resume-tab-btn resume-tab-btn-secondary" disabled={saving} onClick={onCancelEdit}>
            Cancel
          </button>
        </div>
      ) : (
        <div className="resume-tab-value">
          <span>{displayValue}</span>
          {row.userEditable ? (
            <button type="button" className="resume-tab-btn resume-tab-btn-secondary" onClick={onStartEdit}>
              Edit
            </button>
          ) : null}
        </div>
      )}

      {proposal ? (
        <div className="resume-tab-proposal">
          <div className="resume-tab-proposal-text">
            Proposed: <strong>{formatFieldForDisplay(proposal.proposedValue)}</strong>
            {proposal.blockedByUserEdit ? (
              <span className="resume-tab-disabled-reason">
                {" "}
                — disagrees with your edit; your value is kept until you accept this instead.
              </span>
            ) : null}
          </div>
          {row.userEditable ? (
            <div className="resume-tab-proposal-actions">
              <button
                type="button"
                className="resume-tab-btn resume-tab-btn-primary"
                disabled={saving}
                onClick={() => onAcceptProposal(proposal)}
              >
                Accept
              </button>
              <button type="button" className="resume-tab-btn resume-tab-btn-secondary" onClick={onDismissProposal}>
                Dismiss
              </button>
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
