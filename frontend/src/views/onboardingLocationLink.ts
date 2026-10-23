// The country/search-location link control (T61, docs/DESIGN.md "Onboarding").
//
// T52 asked one question (country) and silently mirrored it into
// `sources[].location` (`searchLocation` in form terms); T53's Settings then
// showed the two as independently-editable fields with contrasting labels —
// so one question became two at the hand-off between the two screens.
//
// The fix shows both fields together, joined by an explicit link control the
// user can break. While the link holds, editing the country field mirrors
// into the location field. Breaking it is an ordinary **user edit** — which
// already outranks every other provenance in the product spec's precedence model
// (`user edit > agentic > heuristic`, plus `absent`) — so breaking the link
// produces no new provenance value; it is handled entirely by this module's
// pure state transitions, with no change to the closed provenance set the
// tests enforce.
//
// Kept pure and framework-free (same reason `buildSources` lives in
// `settingsForm.ts` rather than inside a component): `node --test` can
// exercise it directly, and the one failure mode worth pinning here — the
// link silently re-engaging and clobbering a location the user just typed —
// is invisible in a snapshot test but obvious in a table of transitions.

/**
 * Whether the two fields should start linked, given whatever a freshly
 * loaded form (and its resume pre-fill) already holds. They start linked
 * when they already agree — including the common blank/blank case and the
 * pre-filled case, where T52's own mirroring already made them equal — and
 * start *unlinked* when a previous session (or a hand-edited config.yaml)
 * left them genuinely different, which is itself evidence of a prior
 * deliberate break that a reload must not silently re-link.
 */
export function initialLinkState(country: string, location: string): boolean {
  return country.trim() === location.trim();
}

/**
 * Editing the country field. While linked, the location field mirrors it —
 * same behavior T52 shipped. Once broken, the country field edits itself
 * only; the location field is left exactly as the user last set it.
 */
export function applyCountryEdit(
  linked: boolean,
  value: string,
): { searchCountry: string; searchLocation?: string } {
  return linked ? { searchCountry: value, searchLocation: value } : { searchCountry: value };
}

/**
 * Editing the location field directly. This is only reachable once the link
 * is already broken (the field is otherwise mirrored, not independently
 * editable — see `OnboardingView.tsx`'s `LocationLinkStep`), so it never
 * needs to touch `linked` itself; it is here for completeness and for any
 * caller that wants the field's own update in the same pure-function shape
 * as the other two.
 */
export function applyLocationEdit(value: string): { searchLocation: string } {
  return { searchLocation: value };
}

/** Breaking the link via the explicit control. Deliberately touches neither
 * field's value — breaking is a mode change, not an edit, and the DoD's
 * "which provenance value does a broken link produce" is answered by *this
 * function changing nothing about the values*: whatever is already in the
 * location field becomes a plain, un-mirrored user edit from this point on. */
export function breakLink(): { linked: false } {
  return { linked: false };
}

/** Restoring the link via the explicit control. Re-syncs the location field
 * to the country field's current value — the same "while the link holds
 * they move together" rule a freshly-linked pair has always followed —
 * rather than leaving the two to silently disagree until the next keystroke. */
export function restoreLink(country: string): { linked: true; searchLocation: string } {
  return { linked: true, searchLocation: country };
}
