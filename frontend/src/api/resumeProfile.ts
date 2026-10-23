// The one derived-profile read model, over HTTP (T54; backend:
// jobfinder/derived_profile.py, jobfinder/web/resume.py). 30 fields, each
// with provenance ("user" / "agentic" / "heuristic" / "absent" — see
// The product spec), surfaced by three endpoints:
//
//   GET   /api/resume/derived-profile  — every field, as it resolves now.
//   PATCH /api/resume/derived-profile  — write a user edit (or accept a
//                                         proposal — there is no separate
//                                         "accept" endpoint, see
//                                         ResumeTab.tsx).
//   POST  /api/resume/reparse          — propose what a fresh extraction
//                                         would change. Writes nothing.
//
// All three return/accept plain dicts on the backend (no declared
// `response_model` for the two GET/PATCH payloads — same situation
// client.ts documents for `GET /api/settings`), so the response shapes are
// narrowed at runtime here, following fillerbot.ts's `toPreflight`
// convention. The two request bodies (`ProfilePatchIn`, `ReparseIn`) *do*
// have real Pydantic models and are typed from the generated schema, same
// as fillerbot.ts's `ExtrasIn`.
import type { components } from "./schema";

export type ProfilePatchIn = components["schemas"]["ProfilePatchIn"];
export type ReparseIn = components["schemas"]["ReparseIn"];

export class ResumeProfileRequestError extends Error {
  readonly status: number;

  constructor(status: number, detail: string) {
    super(detail);
    this.name = "ResumeProfileRequestError";
    this.status = status;
  }
}

async function readDetail(resp: Response): Promise<string> {
  let body: unknown;
  try {
    body = await resp.json();
  } catch {
    return `Request failed (${resp.status})`;
  }
  const detail = (body as Record<string, unknown> | null)?.detail;
  if (typeof detail === "string") {
    return detail;
  }
  // A pydantic 422 reports a list of per-field errors under `detail`
  // instead of a string — flatten it to one line, same as fillerbot.ts.
  if (Array.isArray(detail)) {
    const messages = detail
      .map((item) => (item as Record<string, unknown> | null)?.msg)
      .filter((msg): msg is string => typeof msg === "string");
    if (messages.length > 0) {
      return messages.join("; ");
    }
  }
  return `Request failed (${resp.status})`;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(path, { credentials: "same-origin", ...init });
  if (!resp.ok) {
    throw new ResumeProfileRequestError(resp.status, await readDetail(resp));
  }
  return (await resp.json()) as T;
}

function jsonBody(method: string, body: unknown): RequestInit {
  return {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  };
}

// ── Provenance vocabulary ─────────────────────────────────

export type Provenance = "user" | "agentic" | "heuristic" | "absent";
export type FieldKind = "str" | "int" | "str_list" | "entry_list";

/** `work_experience`/`education`'s nested shape — read-only (not in
 * `FIELD_SHAPES`, so `PATCH` rejects them as unknown keys), kept to a
 * flat allowlist of string sub-fields for the same reason `DerivedField`
 * is never a nested object elsewhere: nothing path-shaped should be able
 * to ride through here even by accident. */
export interface DerivedEntry {
  [key: string]: string;
}

export type FieldValue = string | number | string[] | DerivedEntry[];

/** One derived field, as `DerivedProfile.to_payload()`
 * (jobfinder/derived_profile.py) reports it. `value` is `string`,
 * `number` or `string[]` for every user-editable field; only the two
 * read-only `entry_list` fields (`work_experience`/`education`) use
 * `DerivedEntry[]`. */
export interface DerivedField {
  name: string;
  value: FieldValue;
  provenance: Provenance;
  store: string;
  confidence: number | null;
  method: string;
  label: string;
  kind: FieldKind;
  userEditable: boolean;
}

export interface DerivedProfilePayload {
  fields: Record<string, DerivedField>;
  resumeOnFile: boolean;
}

const PROVENANCE_VALUES: readonly Provenance[] = ["user", "agentic", "heuristic", "absent"];
const FIELD_KINDS: readonly FieldKind[] = ["str", "int", "str_list", "entry_list"];

function toProvenance(value: unknown): Provenance {
  return typeof value === "string" && (PROVENANCE_VALUES as readonly string[]).includes(value)
    ? (value as Provenance)
    : "absent";
}

function toFieldKind(value: unknown): FieldKind {
  return typeof value === "string" && (FIELD_KINDS as readonly string[]).includes(value)
    ? (value as FieldKind)
    : "str";
}

function toStringList(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

function toDerivedEntry(raw: unknown): DerivedEntry {
  const row = (raw ?? {}) as Record<string, unknown>;
  const out: DerivedEntry = {};
  for (const [key, value] of Object.entries(row)) {
    if (typeof value === "string") {
      out[key] = value;
    }
  }
  return out;
}

function toFieldValue(kind: FieldKind, value: unknown): FieldValue {
  if (kind === "entry_list") {
    return Array.isArray(value) ? value.map(toDerivedEntry) : [];
  }
  if (typeof value === "string" || typeof value === "number") {
    return value;
  }
  if (Array.isArray(value)) {
    return toStringList(value);
  }
  return "";
}

function toDerivedField(name: string, raw: unknown): DerivedField {
  const row = (raw ?? {}) as Record<string, unknown>;
  const kind = toFieldKind(row.kind);
  return {
    name,
    value: toFieldValue(kind, row.value),
    provenance: toProvenance(row.provenance),
    store: typeof row.store === "string" ? row.store : "",
    confidence: typeof row.confidence === "number" ? row.confidence : null,
    method: typeof row.method === "string" ? row.method : "",
    label: typeof row.label === "string" ? row.label : name,
    kind,
    userEditable: row.user_editable === true,
  };
}

function toDerivedProfilePayload(body: Record<string, unknown>): DerivedProfilePayload {
  const rawFields = (body.fields ?? {}) as Record<string, unknown>;
  const fields: Record<string, DerivedField> = {};
  for (const [name, raw] of Object.entries(rawFields)) {
    fields[name] = toDerivedField(name, raw);
  }
  return { fields, resumeOnFile: body.resume_on_file === true };
}

/** `GET /api/resume/derived-profile` — every resume-derived field with
 * its provenance. Never gated on an API key: a user with none still has a
 * derived profile, it is just heuristic. */
export async function fetchDerivedProfile(): Promise<DerivedProfilePayload> {
  return toDerivedProfilePayload(await request<Record<string, unknown>>("/api/resume/derived-profile"));
}

/** `PATCH /api/resume/derived-profile` — write one or more user edits
 * (or accept a proposal: same call, see ResumeTab.tsx). `values` must be
 * shaped per field's `kind`: a `str` field wants a `string`, `int` wants a
 * `number`, `str_list` wants a `string[]` — the backend's `ProfilePatchIn`
 * rejects the wrong shape with a 422 before it reaches storage. */
export async function patchDerivedProfile(
  values: Record<string, string | number | string[]>,
): Promise<DerivedProfilePayload> {
  const body: ProfilePatchIn = { values };
  return toDerivedProfilePayload(
    await request<Record<string, unknown>>("/api/resume/derived-profile", jsonBody("PATCH", body)),
  );
}

/** One field a re-parse would change, and whether it's blocked from
 * applying by an existing user edit (the product spec: shown, applied to
 * neither side, until the user accepts). Mirrors
 * `derived_profile.Proposal` field-for-field. */
export interface Proposal {
  name: string;
  label: string;
  currentValue: FieldValue;
  currentProvenance: Provenance;
  proposedValue: FieldValue;
  proposedProvenance: Provenance;
  proposedStore: string;
  blockedByUserEdit: boolean;
}

/** The backend's ``Proposal`` carries no ``kind`` of its own (it is a diff
 * between two already-resolved values, not a field spec), so this infers
 * the right conversion from the value's own shape: an array of objects is
 * an entry-list field's value, an array of anything else is a str_list's. */
function toProposalValue(value: unknown): FieldValue {
  if (Array.isArray(value) && value.some((item) => typeof item === "object" && item !== null)) {
    return value.map(toDerivedEntry);
  }
  return toFieldValue("str_list", value);
}

function toProposal(raw: unknown): Proposal | null {
  const row = raw as Record<string, unknown> | null;
  if (typeof row?.name !== "string") {
    return null;
  }
  return {
    name: row.name,
    label: typeof row.label === "string" ? row.label : row.name,
    currentValue: toProposalValue(row.current_value),
    currentProvenance: toProvenance(row.current_provenance),
    proposedValue: toProposalValue(row.proposed_value),
    proposedProvenance: toProvenance(row.proposed_provenance),
    proposedStore: typeof row.proposed_store === "string" ? row.proposed_store : "",
    blockedByUserEdit: row.blocked_by_user_edit === true,
  };
}

/** `POST /api/resume/reparse` — proposes changes, writes nothing
 * `method: "agentic"` requires `acknowledged: true`
 * (the product spec's disclosure, enforced server-side: a 400 comes back
 * without it). Throws `ResumeProfileRequestError` on any non-2xx,
 * including "no resume on file" (400) and "no API key" (400) for the
 * agentic method. */
export async function reparseResume(body: ReparseIn): Promise<Proposal[]> {
  const resp = await request<Record<string, unknown>>("/api/resume/reparse", jsonBody("POST", body));
  const raw = Array.isArray(resp.proposals) ? resp.proposals : [];
  return raw.flatMap((item) => {
    const proposal = toProposal(item);
    return proposal ? [proposal] : [];
  });
}
