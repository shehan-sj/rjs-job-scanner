// Theme runtime helpers (T22) — ported from jobfinder/ui/theme.py.
//
// The CSS custom properties themselves live in ./tokens.css; this module
// is for the two pieces of theme.py that are logic, not static values:
//
//   1. `best_fg_for()` — WCAG-luminance-based contrast picker, used by
//      the score badge (ported ahead of need, for T24).
//   2. `apply()`/`get()` — theme.py's module-level "currently active
//      theme" switch. The web equivalent is the `data-theme` attribute
//      on `<html>`, which tokens.css's `:root[data-theme="..."]` rules
//      key off. `applyTheme` below is the DOM-side of that switch.

export type ThemeName = "dark" | "light";

/** Relative luminance per the WCAG formula. Input is a `#rrggbb` string. */
function luminance(hex: string): number {
  const h = hex.replace("#", "");
  const r = Number.parseInt(h.slice(0, 2), 16);
  const g = Number.parseInt(h.slice(2, 4), 16);
  const b = Number.parseInt(h.slice(4, 6), 16);

  const lin = (c: number): number => {
    const v = c / 255;
    return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
  };

  return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
}

/** WCAG contrast ratio between two `#rrggbb` colors. Always >= 1. */
export function contrastRatio(a: string, b: string): number {
  const la = luminance(a);
  const lb = luminance(b);
  return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05);
}

/**
 * Port of `jobfinder/ui/theme.py`'s `best_fg_for`: returns whichever of
 * near-black / near-white has higher WCAG contrast against `bgHex`.
 * Used for the score badge (T24), whose background is a dynamic
 * score-tier color rather than one of the fixed theme tokens, so a
 * single static foreground token can't always keep it legible.
 */
export function bestFgFor(bgHex: string): string {
  const blackRatio = contrastRatio(bgHex, "#0a0a0a");
  const whiteRatio = contrastRatio(bgHex, "#ffffff");
  return blackRatio >= whiteRatio ? "#0a0a0a" : "#ffffff";
}

const THEME_ATTR = "data-theme";

/**
 * Sets (or clears) the explicit `data-theme` override on `<html>`.
 * Passing `null` removes the override, falling back to the OS
 * `prefers-color-scheme` (tokens.css's `@media` block).
 */
export function setThemeOverride(theme: ThemeName | null): void {
  const root = document.documentElement;
  if (theme === null) {
    root.removeAttribute(THEME_ATTR);
  } else {
    root.setAttribute(THEME_ATTR, theme);
  }
}

/**
 * Applies `config.ui.theme` (as returned by `GET /api/settings`) as the
 * explicit override, taking precedence over the OS preference — per
 * T22's contract, the server-side setting wins once it's known. Call
 * this once the settings payload has loaded; before that, the page
 * follows the OS preference via tokens.css alone.
 */
export function applyServerTheme(theme: string | null | undefined): void {
  if (theme === "dark" || theme === "light") {
    setThemeOverride(theme);
  }
  // Any other value (unset/unrecognized) leaves the OS preference in
  // control. In practice `config.ui.theme` defaults to "dark"
  // (jobfinder/config.py), so a real settings payload almost always
  // supplies an explicit value and the OS-preference fallback only
  // matters before that payload has loaded or for a config predating
  // the default.
}
