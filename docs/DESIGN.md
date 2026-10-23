# JobFinder v4 design brief

The direction T58–T61 are built to and graded against. The product spec states the
principles; this file states how they are met, with the numbers.

Mockups: <https://claude.ai/artifact/L3k6ZqvH9HkQdCFohCQnqu> — twelve artboards.
Row 1: three job-list variations. Row 2: the résumé-first onboarding wizard,
variation B at phone width, and the palette with measured contrast. Row 3:
three whole-shell answers to T59+T60 plus the phone shell. Row 4: a one-page
onboarding alternative and the country/search-location seam.

**Decision: Variation B — compact rows that expand in place.**

Not A (dense table), despite it showing the most rows. The match reasoning and
skill overlap are the output of the whole scoring pipeline, and a table cannot
hold them — they would move behind a second click into a surface that has to be
designed, built and navigated. B shows them where the user already is.

Not C (split master–detail) yet, though it is the strongest for reading one job
deeply: it needs width, so the phone layout has to become two screens. Worth
revisiting as a density option once B is in use.

### Superseded 2026-10-22 — the decision above was made on the wrong assumptions

**Decision: split master–detail (the deferred "C"), at full width.** The line
above predicted this would be worth revisiting once B was in use. It was, for
exactly the stated reason.

Two assumptions behind the original choice turned out to be wrong in practice:

- **Width.** C was deferred for needing width that 1440 did not have. The real
  display is **2560**. At that width B leaves most of the viewport empty while
  C has room for a list *and* a readable job.
- **Result count.** B's density argument was measured against **74 seeded
  jobs**. Real scans return ~36 of which a handful clear the threshold. At 1–5
  results compact rows buy no density at all — they only add a click between
  the user and the reasoning the whole scoring pipeline exists to produce.

What ships: a 580px scannable list beside a detail pane carrying the match
reasoning, matched skills, gaps and the structured facts. Selecting a row fills
the pane; nothing has to be expanded to be read. On a phone the two panes
become two screens — the cost this file named, now accepted deliberately
rather than avoided.

Also corrected, because the built UI diverged from the approved mockups without
being caught:

- **The job list must be a list.** The build kept the ported
  `repeat(auto-fill, minmax(380px, 1fr))` grid with no max-width, so at 2560 it
  rendered six columns and stranded a single result in the first one. Variation
  B was never a grid.
- **The rail is 88px, not 68px.** 68px was specced for icons above short
  labels; the build shipped text-only labels, so "Sources & keys" wrapped.
- **Type scales with the viewport.** Hardcoded 12/13px is legible at 1440 and
  too small at 2560. Body 15, row title 17, page title 30 at this width.
- **Empty and single-result states are designed, not incidental.** "Nothing
  above the threshold" states that the scan worked, names the highest score it
  did find, and offers the control that changes the outcome.

### Superseded 2026-10-22 (T87) — the board replaces split master-detail

**Decision: one five-stage board (New · Shortlist · Applied · Replied · Not
for me) plus a side detail panel, per the product spec.** Phase 9 (T81-T86, T88,
T89) shipped this instead of the two-pane shell described above. The split
master-detail decision above is kept as the historical record of a decision
that was made and then superseded again — not deleted, same convention this
file already used for Variation B further up.

What changed, concretely: the 580px scannable list and its three rail
destinations (Matches/Applied/Removed) collapsed into one `board` rail
destination with five columns (`frontend/src/views/BoardView.tsx`); the
detail pane survives as `frontend/src/components/JobPanel.tsx` — opened the
same way (select a card, the pane appears beside the list) but keyed to a
card's resolved *stage* rather than a tab. Everything the four corrected
bullets above fixed (the list-not-grid shape, the 88px rail, viewport-scaled
type, designed empty states) carries forward unchanged into the board; none
of it was re-litigated by Phase 9.

### Superseded 2026-10-22 (T100) — the job detail opens in a modal, not a side panel

**Decision: `JobPanel.tsx`'s content opens in a native `<dialog>` modal, floated
over the board, instead of beside it as a sixth flex column.** The owner, having
now used the side panel in practice, asked for this directly: "currently the
jobs open as another column when clicked; it would be ideal for a modal to pop
up for each job, which can be cancelled — only one at a time, clicking another
should close the previous and open the new one." The decision immediately
above — a side detail panel, per the product spec — is kept as the historical
record, same convention this file already used twice before.

What changed, concretely: `JobPanel.tsx`'s content and its five-stop stage
control are untouched (same component, same `dispatchMoveCard` call site);
only the container changed. `frontend/src/components/JobModal.tsx` wraps that
content in one `<dialog>`, mounted once for the whole board regardless of
which job (or none) is open — `BoardView.tsx` tracks at most one open job id
(`jobModalLogic.ts`'s `openJob`/`closeJob`), so opening a second job while one
is already open swaps the dialog's content rather than opening a second
dialog; there is structurally never more than one. The board
(`.board-columns`) is no longer a flex sibling of the panel and so is never
resized or unmounted by it, keeping each column's own scroll position exactly
as before. The phone form is unchanged: `BoardCardSheet.tsx` (T85) stays the
390px bottom sheet, gated by the same `max-width: 640px` breakpoint it already
used; the modal only opens at/above that width (a `matchMedia` check, since a
`<dialog>` cannot be shown modally while its computed `display` is `none`).

The native `<dialog>`'s `showModal()` was used rather than a hand-rolled
overlay: it gives focus trapping, Escape-to-cancel and page inertness for free
in evergreen Chromium/Firefox, and those three behaviours are exactly the kind
that end up subtly wrong when re-implemented by hand. The one piece not left
to the browser is focus-return on close — `showModal()`'s own "restore focus to
whoever had it when `showModal()` was called" only remembers the *first*
opener across an A→B swap in the same dialog instance, so `JobModal.tsx` takes
an explicit `openerRef` from `BoardView.tsx` (the card that most recently
opened it) and focuses that element itself on the dialog's `close` event.

The "five principles" section immediately below predates the board and
describes the split-view shell specifically (rail destinations, the filter
panel's permanent 264px sidebar, row-level actions). It is left as-is below,
as the split-view's own record, per the same "don't quietly reword" rule —
the parity matrix's 14-row re-verification against the board (T87) is the current
source of truth for which principles still hold and how they are met today.

## The five principles, and how this meets them

**1 · Density is a first-class requirement.** v3's card was built for a 690px
floating panel. B's row is one line of title plus one line of
company · location · compensation · age, with the score as a 38px mono chip.
Three densities ship — `Table`, `Rows`, `Split` — because "dense enough" differs
between triaging 40 new matches and reading one. `Rows` is the default. The earlier
380px card floor does not apply; it was a porting fix and the product spec
supersedes it.

**Withdrawn 2026-10-22 (T64): there is no density switch any more.** The split
layout answers the sentence above structurally rather than with a control —
the 580px list *is* the dense triage view and the detail pane *is* the reading
view, both on screen at once, so there is nothing to switch between.
`DensityToggle` is deleted. Recorded here rather than left as a silent
deletion, because this file committed to shipping `Table` and withdrawing a
stated commitment is exactly what the product spec exists to make visible. No
the parity matrix row cites a density, so no parity capability is affected.

**2 · Jobs are grouped by when they were found.** Every variation groups by scan
batch with the newest first: `Today 09:12 · 14 found · LinkedIn, Indeed`. Ranking
still orders within a batch. This needs `matched_at` on the card payload — it is
already stored and already `ORDER BY`'d, just dropped before the client (T57).

**3 · Settings take effect where the user is looking.** The settings modal is
gone. The filters that get touched — minimum score, freshness, location tier,
arrangement, hide agencies/internships/contract/promoted — live in a 264px
panel beside the results, and changing one re-filters the visible list. The
panel collapses, handing its width back to the list. No new endpoint: T46 made
`PUT /api/settings` merge partial bodies. Rarely-touched configuration (sources,
API keys, appearance) stays on its own page, reached from the rail.

**4 · One navigation model.** **Decision: Shell 2 — a 68px icon rail plus a
separate collapsible filter panel.** One rail replaces the trigger strip, the
nine-tab modal and the separate drawer. Destinations: Matches · Applied ·
Removed · Résumé · Sources & keys, each with its live count; activity log,
FillerBot and the heartbeat sit at the foot. Matches' three location tiers
become chips above the list, not rail children, so the rail stays one level
deep. Scan now moves to the list header where the count it changes is visible.

This is **two regions, not the single rail an earlier draft of this principle
described**, and the deviation is deliberate: a rail carrying five
destinations, three sub-tabs, four filters and six foot controls overflows at
980px window height, which puts the filters under a scroll exactly when the
window is short. Separating "where am I" from "what am I filtering" also lets
the filter panel collapse and return 264px to the list. One navigation model is
still the principle — there is one way to reach any destination — and the
filter panel is not a navigation system, which is why it does not count as a
third.

On a phone the rail becomes a four-item bottom tab bar (Matches · Applied ·
Removed · More) and the filters become one sheet behind a single control.

**5 · No capability is lost.** Every per-card action from the parity matrix survives:
mark applied, dismiss, skip company, open posting, company site. In B they are
on the row (applied, dismiss) or in the expanded body (skip company, open
posting, company site). **Correction (T62):** FillerBot is not, and has never
been, one of those per-card actions — the parity matrix's card-actions row has
never listed it, there is no per-job entry point anywhere in the codebase,
and the backend allows exactly one FillerBot session at a time. It survives
as the single global control principle 4 already describes (a rail foot
button, same as before T60's shell replaced the floating trigger strip).
This file's own earlier phrasing — "(or in the expanded body) … FillerBot" —
wrongly implied a per-card entry point; corrected here so the two documents
stop disagreeing. T62 re-verifies all 13 the product spec bullets (the parity matrix's
table carries one further capability beyond those 13 — "History cards carry
the same content as match cards" — for 14 rows total) against the built UI
with fresh evidence.

## Onboarding

**Decision: one page, four sections** — résumé, how we read it, what we read,
API key — rather than the four-step wizard the first mockup drew. The reason is
T61's own DoD: "a user who abandons midway is not left in a half-configured
state". A single page with one commit button satisfies that *structurally* —
nothing is written until the user presses Start — where a stepper has to
prevent it by hand, step by step. T52's provenance display carries over
unchanged: every pre-filled value wears its origin, and `absent` shows as a
stated blank ("the résumé did not say"), never as a guess. The no-API-key path
stays first-class: without a key JobFinder still scans and still filters, it
only scores by keyword overlap, and the page says so where the key is asked
for.

**Decision: the country/search-location seam closes by asking both, with a
breakable link.** T52 asked one question and mirrored `searchCountry` into
`searchLocation` silently; T53's Settings then showed two independently
editable fields, so one question became two at the hand-off. Both fields now
appear together on both screens, joined by an explicit link control. While the
link holds they move together; breaking it is an ordinary **user edit**, which
already outranks everything in the precedence model. That is why this option
beat showing the mirror as a badge: the badge would have needed a fifth
provenance value, and `user edit > agentic > heuristic`, plus `absent`, is a
closed set the tests enforce.

## Palette

Every pair below was computed before anything was drawn, with the sRGB relative
luminance formula. Today's build ships **six** failing pairs, one at 2.89:1,
inherited from the Qt stylesheet and never chosen. The product spec puts the
ratchet at zero, so these are the replacements.

### Dark (default)

| Token | Hex | On | Ratio | AA small text |
|---|---|---|---|---|
| text | `#E8ECF2` | ground `#0E1116` | 15.95 | pass |
| text | `#E8ECF2` | surface `#171B22` | 14.56 | pass |
| dim | `#9AA5B4` | surface `#171B22` | 6.92 | pass |
| dim | `#9AA5B4` | raised `#1F242D` | 6.24 | pass |
| accent | `#5AA9FF` | surface `#171B22` | 7.03 | pass |
| good | `#4FC79A` | surface `#171B22` | 8.20 | pass |
| warn | `#E8B059` | surface `#171B22` | 8.87 | pass |
| bad | `#FF8A8A` | surface `#171B22` | 7.61 | pass |

Ground `#0E1116` · surface `#171B22` · raised `#1F242D` · border `#2A313C`.
Worst pair: **6.24**.

### Light

| Token | Hex | On | Ratio | AA small text |
|---|---|---|---|---|
| text | `#101620` | ground `#FFFFFF` | 18.14 | pass |
| text | `#101620` | surface `#F5F7FA` | 16.90 | pass |
| dim | `#56616F` | surface `#F5F7FA` | 5.87 | pass |
| accent | `#0A5FCC` | surface `#F5F7FA` | 5.56 | pass |
| good | `#117A54` | surface `#F5F7FA` | 4.97 | pass |
| warn | `#8A5A00` | surface `#F5F7FA` | 5.52 | pass |
| bad | `#C0392F` | surface `#F5F7FA` | 5.06 | pass |

Border `#D5DCE5`. Worst pair: **4.97**, against AA's 4.5 floor.

Score bands carry a tinted background as well as a colour, so they are not
distinguished by hue alone: ≥80 good, 65–79 accent, <65 warn.

## Type and spacing

| Role | Size / weight |
|---|---|
| Page title | 26 / 600, `-0.015em` |
| Section | 20 / 600, `-0.01em` |
| Job title | 14 / 600 |
| Body, table cells | 13 / 400 |
| Secondary meta | **12** / 400 |
| Numerals (scores, salary) | 13 / 650 mono, tabular |

`system-ui` for text; `ui-monospace, SFMono-Regular, Menlo` for numerals so
score columns align. **Secondary text stops at 12px** — today's worst contrast
failure is an 11px grey label, so the type scale is part of that fix, not just
the hex values. Spacing is a 4px rhythm; radii 6 / 8 / 10 / 999; touch targets
≥44px.

## Phone

Shown in the mockups at 390×844. The rail becomes a four-item bottom tab bar;
filters collapse behind one control in the header; rows become cards that expand
in place with full-width actions. Batch headers survive. Variation C is the one
that cannot do this without becoming two screens, which is part of why B wins.

## Settled by measurement, not argument

T58 answered both of the questions this file previously left open.

- **`Split` did not ship.** `Table` and `Rows` are the two densities, `Rows`
  default. Split needs width the phone layout cannot give without becoming two
  screens; it stays available as a later density option.
- **Row counts are measured.** 74 jobs in one scan batch, real headless
  Chromium at 1920×1080, counted as `y + height <= 1080`: **64 rows fully above
  the fold** against the ported build's **16**, a row height of 52px against
  221px, and no horizontal scroll at 1920 or 390. That is 4.0× against a 3×
  target.
- **Batch headers cost density, and the margin is thinnest when it matters
  most.** Measured across three seedings: 64 above the fold for one batch of
  74, 60 for four batches of 20, and **48 for eight batches of 10** — exactly
  the target floor. A user who scans often sees the most headers and the fewest
  jobs. Any future change that adds vertical chrome to a row or a header should
  be re-measured against the eight-batch case, not the one-batch case.

## Open, deliberately

- Whether the filter panel starts collapsed or expanded on first run. Expanded
  teaches that the filters exist; collapsed gives the list its full width. T59
  should pick one and say why.
