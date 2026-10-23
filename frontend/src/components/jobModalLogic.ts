// T100 — the job detail modal's pure open-state logic (the product spec
// supersession, this task's brief: "opening B while A is open must
// close A — one modal instance, not a stack"). No React/DOM import
// here, same split boardLogic.ts/panelLogic.ts already use, so this is
// unit-testable with `node --test`; `JobModal.tsx`/`BoardView.tsx` are
// the thin renders of it.
//
// The open state is modelled as a list of ids rather than a bare
// `string | null` deliberately: that is the shape a "stack" bug would
// actually take (pushing a newly-opened id onto whatever was already
// open instead of replacing it), so a test against this list form is
// the one that would actually fail against a stacking implementation —
// a test written against a scalar could not tell the two apart.

/** Opening a job always *replaces* whatever was open — it is never
 * appended. `BoardView.tsx` only ever reads `[0]` of the result (or
 * `null` when empty), but the invariant this function exists to
 * enforce — "opening a second job while one is open leaves exactly one
 * open id, and it is the new one" — holds regardless of how the caller
 * reads the result, which is the point. */
export function openJob(_currentlyOpenJobIds: readonly string[], jobId: string): string[] {
  return [jobId];
}

/** Closing always empties the list — there is no "pop the last one and
 * reveal the one under it" behaviour, because there is never more than
 * one underneath. */
export function closeJob(_currentlyOpenJobIds: readonly string[]): string[] {
  return [];
}
