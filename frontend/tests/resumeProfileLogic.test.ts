// Tests for the pure logic behind the Résumé tab (T54).
//
// Run with `npm run test:logic` (`node --test`), same as
// onboardingPrefill.test.ts/fillerbotLogic.test.ts. What is worth testing
// here is the same class of defect those two guard against: logic that
// can silently lose or misapply a user's data.
//
//   1. `rowState`/`rowStates` never substitute a re-parse proposal's
// value for the field's current value — the product spec's "applies
//      neither until accepted", from the client side this time (the
//      backend side is pinned by
//      tests/test_resume_reparse_patch.py::
//      test_reparse_proposal_marks_a_field_blocked_by_a_user_edit).
//   2. `parseFieldInput` fails closed on a shape it cannot honestly
//      represent, rather than coercing something lossy that would then
//      reach `PATCH /api/resume/derived-profile`.
import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  agenticDisabledReason,
  formatFieldForDisplay,
  formatFieldForEditing,
  indexProposalsByField,
  parseFieldInput,
  rowState,
  rowStates,
} from "../src/api/resumeProfileLogic.ts";
import type { DerivedField, Proposal } from "../src/api/resumeProfile.ts";

function field(overrides: Partial<DerivedField> = {}): DerivedField {
  return {
    name: "email",
    value: "amara@example.com",
    provenance: "heuristic",
    store: "heuristic",
    confidence: 0.8,
    method: "found in the contact block",
    label: "Email address",
    kind: "str",
    userEditable: true,
    ...overrides,
  };
}

function proposal(overrides: Partial<Proposal> = {}): Proposal {
  return {
    name: "email",
    label: "Email address",
    currentValue: "amara@example.com",
    currentProvenance: "heuristic",
    proposedValue: "amara.w@example.com",
    proposedProvenance: "agentic",
    proposedStore: "filler_profile",
    blockedByUserEdit: false,
    ...overrides,
  };
}

describe("parseFieldInput", () => {
  it("trims a str field", () => {
    assert.equal(parseFieldInput("str", "  hello  "), "hello");
  });

  it("parses a well-formed int", () => {
    assert.equal(parseFieldInput("int", "12"), 12);
  });

  it("treats a blank int input as 0 (PATCHing '' clears a field)", () => {
    assert.equal(parseFieldInput("int", "   "), 0);
  });

  it("fails closed on a non-numeric int input rather than coercing it", () => {
    assert.equal(parseFieldInput("int", "five years"), null);
  });

  it("fails closed on a float-shaped int input", () => {
    assert.equal(parseFieldInput("int", "5.5"), null);
  });

  it("splits a str_list field one entry per line, trimmed, blanks dropped", () => {
    assert.deepEqual(parseFieldInput("str_list", "Python\n  Go  \n\nRust\n"), ["Python", "Go", "Rust"]);
  });

  it("never offers a value for an entry_list field — it isn't user-editable", () => {
    assert.equal(parseFieldInput("entry_list", "anything"), null);
  });
});

describe("formatFieldForEditing / formatFieldForDisplay", () => {
  it("round-trips a list through editing format and back via parseFieldInput", () => {
    const original = ["Python", "Go", "Rust"];
    const edited = formatFieldForEditing(original);
    assert.deepEqual(parseFieldInput("str_list", edited), original);
  });

  it("joins a list with ', ' for compact display, not one per line", () => {
    assert.equal(formatFieldForDisplay(["Python", "Go"]), "Python, Go");
  });

  it("formats a number for display without quoting", () => {
    assert.equal(formatFieldForDisplay(7), "7");
  });
});

describe("indexProposalsByField", () => {
  it("indexes by field name", () => {
    const p1 = proposal({ name: "email" });
    const p2 = proposal({ name: "skills", currentValue: [], proposedValue: ["Python"] });
    const indexed = indexProposalsByField([p1, p2]);
    assert.equal(indexed.email, p1);
    assert.equal(indexed.skills, p2);
  });

  it("returns an empty index for no proposals", () => {
    assert.deepEqual(indexProposalsByField([]), {});
  });
});

describe("rowState", () => {
  it("surfaces the field's own current value, not a matching proposal's", () => {
    const f = field({ value: "amara@example.com" });
    const p = proposal({ proposedValue: "amara.w@example.com" });
    const row = rowState(f, { email: p });
    assert.equal(row.value, "amara@example.com");
    assert.equal(row.proposal?.proposedValue, "amara.w@example.com");
  });

  it("still surfaces the current value when the proposal is blocked by a user edit", () => {
    // This is the exact trapdoor the product spec forbids, from the client
    // side: a blocked proposal must not quietly become the displayed
    // value just because it exists.
    const f = field({ value: "user-chosen@example.com", provenance: "user" });
    const p = proposal({ proposedValue: "agentic-disagrees@example.com", blockedByUserEdit: true });
    const row = rowState(f, { email: p });
    assert.equal(row.value, "user-chosen@example.com");
    assert.equal(row.provenance, "user");
    assert.equal(row.proposal?.blockedByUserEdit, true);
  });

  it("has no proposal when none was returned for this field", () => {
    const row = rowState(field({ name: "phone" }), {});
    assert.equal(row.proposal, null);
  });
});

describe("rowStates", () => {
  it("builds one row per field and attaches matching proposals only", () => {
    const fields = {
      email: field({ name: "email" }),
      phone: field({ name: "phone", value: "", kind: "str" }),
    };
    const proposals = [proposal({ name: "email" })];
    const rows = rowStates(fields, proposals);
    const byName = Object.fromEntries(rows.map((r) => [r.name, r]));
    assert.ok(byName.email.proposal);
    assert.equal(byName.phone.proposal, null);
  });
});

describe("agenticDisabledReason", () => {
  it("is disabled with a reason when no API key is configured", () => {
    const reason = agenticDisabledReason(false);
    assert.ok(reason && reason.length > 0);
  });

  it("is enabled (no reason) once a key is configured", () => {
    assert.equal(agenticDisabledReason(true), null);
  });
});
