// A small, generic debounced write queue -- T59's mechanism for
// "Debounce writes; never leave the visible list disagreeing with
// persisted state" (docs/DESIGN.md principle 3).
//
// Deliberately generic over the patch shape (rather than hardcoding
// `FilterPatch`) and over the timer (`Scheduler`, injectable) so the
// coalescing behaviour -- the part the T46-regression DoD item is
// actually about -- can be exercised with a deterministic fake scheduler
// in tests instead of real timers.
export interface Scheduler {
  schedule(fn: () => void, delayMs: number): unknown;
  cancel(handle: unknown): void;
}

export const realScheduler: Scheduler = {
  schedule: (fn, delayMs) => setTimeout(fn, delayMs),
  cancel: (handle) => clearTimeout(handle as ReturnType<typeof setTimeout>),
};

export interface DebouncedPatchQueueOptions<P> {
  /** Coalesces an incoming patch onto the still-pending one. */
  merge: (base: P, incoming: P) => P;
  isEmpty: (patch: P) => boolean;
  /** The "nothing pending" value -- e.g. `{}`. */
  initial: P;
  delayMs: number;
  /** Actually performs the write (the real caller: `putSettingsPatch`). */
  send: (patch: P) => Promise<void>;
  /** Called when `send` rejects. The queue does not retry on its own --
   * see `enqueue`'s docstring for why automatic retry is deliberately
   * out of scope here. */
  onError?: (error: unknown, patch: P) => void;
  scheduler?: Scheduler;
}

export interface DebouncedPatchQueue<P> {
  /** Merge `patch` onto whatever's pending and (re)start the debounce
   * timer. Calling this again before the timer fires cancels the
   * previous timer and starts a new one from zero -- that's the
   * "debounce", not a batch interval -- so a user who keeps dragging a
   * slider never fires a write until they stop. */
  enqueue(patch: P): void;
  /** Fires the pending write immediately (if any) without waiting for
   * the timer -- used on unmount/tab switch so a change made right
   * before navigating away isn't lost to a timer that never gets to
   * fire. Returns the same promise `send` produces. */
  flushNow(): Promise<void>;
  dispose(): void;
}

export function createDebouncedPatchQueue<P>(options: DebouncedPatchQueueOptions<P>): DebouncedPatchQueue<P> {
  const scheduler = options.scheduler ?? realScheduler;
  let pending: P = options.initial;
  let handle: unknown = null;

  function cancelTimer(): void {
    if (handle !== null) {
      scheduler.cancel(handle);
      handle = null;
    }
  }

  function fire(): Promise<void> {
    cancelTimer();
    if (options.isEmpty(pending)) {
      return Promise.resolve();
    }
    const patch = pending;
    pending = options.initial;
    return options.send(patch).catch((error: unknown) => {
      // Deliberately does NOT re-merge `patch` back onto `pending`: a
      // rejected write (e.g. a 422 bounds failure) would otherwise retry
      // forever on every subsequent unrelated change. The caller's
      // `onError` is where "surface the failure, don't leave the UI
      // claiming a value the server rejected" gets
      // implemented -- it receives the exact patch that failed so it can
      // revert just those fields.
      options.onError?.(error, patch);
    });
  }

  return {
    enqueue(patch: P) {
      pending = options.merge(pending, patch);
      cancelTimer();
      handle = scheduler.schedule(() => {
        void fire();
      }, options.delayMs);
    },
    flushNow() {
      return fire();
    },
    dispose() {
      cancelTimer();
    },
  };
}
