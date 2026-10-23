// Pure logic behind the Résumé tab (T54) — kept apart from ResumeTab.tsx
// the same way fillerbotLogic.ts/settingsForm.ts split logic from
// rendering, so it can be covered by `npm run test:logic` without a DOM.
//
// Two things here are worth testing because getting them wrong loses data
// silently, same bar onboardingPrefill.test.ts's docstring sets:
//
//   1. A field a user edit already decides (`blockedByUserEdit`) must
//      keep showing its *current* value regardless of what a re-parse
//      proposed — `rowState` never substitutes the proposed value for the
//      current one; a caller has to call `acceptProposal`'s PATCH
// explicitly (ResumeTab.tsx) for that to change. See the product spec:
//      "applies neither until accepted".
//   2. `parseFieldInput` must fail *closed* — an unparseable int, or a
//      list typed with a non-string item, returns `null` rather than a
//      best-effort coercion a caller could accidentally PATCH with.
import type { DerivedField, FieldKind, FieldValue, Proposal, Provenance } from "./resumeProfile";

/** One line of textarea input per list entry — same split/trim/dedupe-less
 * convention `settingsForm.ts`'s `linesToList` already uses for every
 * other list-of-strings field in this app. */
function linesToList(text: string): string[] {
  return text
    .split("\n")
    .map((l) => l.trim())
    .filter((l) => l.length > 0);
}

/** Turns a text input's raw string into the shape `patchDerivedProfile`
 * wants for `kind`, or `null` if the input cannot be made into that shape
 * at all — never a lossy best guess. `entry_list` fields are never
 * editable (`derived_profile.FIELD_SHAPES` excludes them), so this always
 * returns `null` for them; callers should not be offering an edit control
 * for one in the first place. */
export function parseFieldInput(kind: FieldKind, raw: string): string | number | string[] | null {
  if (kind === "str") {
    return raw.trim();
  }
  if (kind === "int") {
    const trimmed = raw.trim();
    if (trimmed === "") {
      return 0;
    }
    if (!/^-?\d+$/.test(trimmed)) {
      return null;
    }
    return Number.parseInt(trimmed, 10);
  }
  if (kind === "str_list") {
    return linesToList(raw);
  }
  return null;
}

/** The inverse of the textarea-splitting half of `parseFieldInput`, for
 * pre-filling an edit control from a field's current value. Only ever
 * called for a `str`/`int`/`str_list` field — `entry_list` fields have no
 * edit control (see `parseFieldInput`'s docstring) — so a `DerivedEntry[]`
 * is not a shape this needs to round-trip. */
export function formatFieldForEditing(value: string | number | string[]): string {
  if (Array.isArray(value)) {
    return value.join("\n");
  }
  return String(value);
}

/** A short, human join for read-only display — `", "` rather than one
 * entry per line, since the read-only rendering is denser than an edit
 * control needs to be. An `entry_list` value summarises to a count: the
 * Résumé tab lists `work_experience`/`education` entries separately
 * rather than trying to compress a multi-field record onto one line. */
export function formatFieldForDisplay(value: FieldValue): string {
  if (Array.isArray(value) && value.length > 0 && typeof value[0] === "object") {
    const count = value.length;
    return count === 1 ? "1 entry" : `${count} entries`;
  }
  if (Array.isArray(value)) {
    return (value as string[]).join(", ");
  }
  return String(value);
}

export function indexProposalsByField(proposals: Proposal[]): Record<string, Proposal> {
  const out: Record<string, Proposal> = {};
  for (const proposal of proposals) {
    out[proposal.name] = proposal;
  }
  return out;
}

/** Why the agentic re-parse (and the "replace resume file" control, which
 * triggers the same kind of Anthropic call on the backend — see
 * jobfinder/web/resume.py's `POST /api/resume`) is disabled, or `null`
 * when it isn't. The product spec: "the heuristic path must never be
 * presented as equivalent" — the reason string here is what makes that
 * difference visible rather than just a disabled button with no
 * explanation. */
export function agenticDisabledReason(apiKeySet: boolean): string | null {
  if (apiKeySet) {
    return null;
  }
  return "Add an Anthropic API key on the Basic setup tab to use this.";
}

/** What one row of the Résumé tab should show for a field, folding in a
 * pending re-parse proposal if there is one. The load-bearing invariant:
 * `value`/`provenance` always come from `field`, never from a proposal —
 * a proposal is additional information shown *alongside* the current
 * value, not a substitute for it, whether or not it is blocked by a user
 * edit. Applying one is a separate, explicit action
 * (`patchDerivedProfile`, called from ResumeTab.tsx's accept handler). */
export interface FieldRowState {
  name: string;
  label: string;
  kind: FieldKind;
  value: FieldValue;
  provenance: Provenance;
  userEditable: boolean;
  proposal: Proposal | null;
}

export function rowState(field: DerivedField, proposalsByField: Record<string, Proposal>): FieldRowState {
  return {
    name: field.name,
    label: field.label,
    kind: field.kind,
    value: field.value,
    provenance: field.provenance,
    userEditable: field.userEditable,
    proposal: proposalsByField[field.name] ?? null,
  };
}

export function rowStates(
  fields: Record<string, DerivedField>,
  proposals: Proposal[],
): FieldRowState[] {
  const byField = indexProposalsByField(proposals);
  return Object.values(fields).map((field) => rowState(field, byField));
}
