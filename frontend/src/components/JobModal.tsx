import { useEffect, useRef, type ReactNode } from "react";
import { JobPanel, type JobPanelProps } from "./JobPanel";
import "./JobModal.css";

/**
 * T100 — the job detail modal's container (the product spec supersession,
 * docs/DESIGN.md's dated block). `JobPanel.tsx`'s content and stage
 * control are unchanged (T83) — this file only changes *where* that
 * content renders: beside the board as a sixth flex column, to a
 * native `<dialog>` floated over it.
 *
 * One `<dialog>`, mounted once for the whole board — never one per
 * card. `BoardView.tsx` only ever tracks at most one open job id (see
 * `jobModalLogic.ts`), so opening a second job while the dialog is
 * already open never calls `showModal()` a second time (that throws —
 * a dialog already open cannot be re-opened); it only swaps which
 * card's content `JobPanel` renders inside the *same* dialog instance.
 * That is what makes one-at-a-time structural rather than incidental:
 * there is only ever one `<dialog>` element to begin with, so there is
 * nothing a second call could stack on top of.
 *
 * `showModal()` gives focus trapping, Escape-to-cancel and inertness
 * for the rest of the page for free in evergreen Chromium/Firefox —
 * hand-rolling a focus trap, an Escape listener and an `inert` toggle
 * on the board is exactly the kind of thing that ends up subtly wrong
 * (three separate behaviours to keep in sync across resizes/renders),
 * so this uses the platform rather than re-implementing it.
 *
 * Focus-return is *not* left to the browser's own "restore focus to
 * whatever had it when `showModal()` was called" behaviour: since
 * `showModal()` is only called once per open/close cycle, that native
 * behaviour would keep returning focus to job A's card even after job
 * B replaced it in the same, still-open dialog. This file instead
 * takes an explicit `openerRef` — the element `BoardView.tsx` last
 * focused immediately before opening a job — and focuses it itself on
 * the dialog's `close` event, so closing always returns focus to
 * whichever card most recently opened the dialog, not the first one.
 *
 * Only shown at/above the phone breakpoint: `BoardCardSheet.tsx` (T85)
 * stays the phone form unchanged, and a `<dialog>` cannot be shown
 * modally while its computed `display` is `none` (the browser throws),
 * so opening is gated on a `matchMedia` check rather than CSS alone.
 */
export interface JobModalProps extends Omit<JobPanelProps, "card"> {
  card: JobPanelProps["card"] | null;
  isOpen: boolean;
  /** The element to refocus when the dialog closes — the card that
   * most recently opened it, captured by the caller at open time. */
  openerRef: { current: HTMLElement | null };
}

const DESKTOP_MEDIA_QUERY = "(min-width: 641px)";

export function JobModal({ card, isOpen, openerRef, ...panelProps }: JobModalProps): ReactNode {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const { onClose } = panelProps;

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;

    const sync = () => {
      const isDesktopWidth = window.matchMedia(DESKTOP_MEDIA_QUERY).matches;
      if (isOpen && isDesktopWidth) {
        if (!dialog.open) dialog.showModal();
      } else if (dialog.open) {
        dialog.close();
      }
    };

    sync();
    window.addEventListener("resize", sync);
    return () => window.removeEventListener("resize", sync);
  }, [isOpen]);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    // Fires for every way the dialog can close — Escape (the native
    // `cancel` -> `close` sequence), the explicit close control inside
    // `JobPanel` (which calls `onClose`, triggering the effect above to
    // call `dialog.close()`), or a resize past the phone breakpoint.
    // One handler, so focus always returns the same way regardless of
    // which of those triggered it.
    const onDialogClose = () => {
      onClose();
      // `preventScroll` — same lesson `BoardView.tsx`'s own
      // keyboard-follows-focus effect already paid for: a plain
      // `.focus()` on a card scrolls its column to bring that card
      // into view, which would silently violate the "board's scroll
      // position is unchanged by opening and closing" requirement.
      openerRef.current?.focus({ preventScroll: true });
    };
    dialog.addEventListener("close", onDialogClose);
    return () => dialog.removeEventListener("close", onDialogClose);
  }, [onClose, openerRef]);

  return (
    <dialog
      ref={dialogRef}
      className="job-modal"
      aria-label="Job detail"
      // Clicking the backdrop closes it. With `showModal()` the rest of
      // the page is inert, so this is the *only* way to dismiss by
      // pointer other than the close control — and it is what the owner
      // reaches for when they say "clicking another should close the
      // previous one". The click cannot reach the card underneath (that
      // is what inert means), so it takes two: one to dismiss, one to
      // open the next.
      //
      // `e.target === dialogRef.current` is the test for "the backdrop,
      // not the content": a `<dialog>`'s backdrop is painted by the
      // element itself, so a click on it reports the dialog as its
      // target while a click inside reports a descendant.
      onClick={(e) => {
        if (e.target === dialogRef.current) {
          dialogRef.current?.close();
        }
      }}
    >
      {/* Keyed by job id so swapping jobs inside this one dialog remounts
          the panel rather than reusing it. The panel holds per-job state
          (the lazily fetched description), and a `key` is React's own way
          to reset that — the alternative is resetting it from an effect,
          which fires a second render pass on every swap and is what
          `react(set-state-in-effect)` warns about. Still exactly one
          `.job-panel` in the DOM, which
          scripts/t100_modal_browser_check.py asserts. */}
      {card ? <JobPanel key={card.job_id} card={card} {...panelProps} /> : null}
    </dialog>
  );
}
