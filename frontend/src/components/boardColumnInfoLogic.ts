// T104 — the board column header's info disclosure open-state logic.
// Pure, no React/DOM import, same split boardLogic.ts/jobModalLogic.ts
// already use, so this is unit-testable with `node --test`;
// `BoardView.tsx` is the thin render of it.
//
// Modelled the same way T100's `jobModalLogic.ts` models "which job is
// open" — a list of at most one stage, never a `boolean` per column —
// deliberately: "at most one column's info open at a time" is the DoD's
// own wording, and a boolean-per-column implementation (five
// independent `useState<boolean>`s, one per column) could not be made
// to fail the shape of the test below the way this list form can. A
// test against five independent booleans can only ever assert "A's
// flag is true" and "B's flag is true" as two separate facts; it has no
// single value to assert "and nothing else is open" against. This
// list's length *is* that assertion.
import type { Stage } from "../types/job";

/** Opening a column's info always *replaces* whatever was open — it is
 * never appended, so opening a second column's info closes the first. */
export function openColumnInfo(_currentlyOpen: readonly Stage[], stage: Stage): Stage[] {
  return [stage];
}

/** Closing always empties the list — there is no "stack" to pop back
 * into, because there is never more than one entry in it. */
export function closeColumnInfo(_currentlyOpen: readonly Stage[]): Stage[] {
  return [];
}
