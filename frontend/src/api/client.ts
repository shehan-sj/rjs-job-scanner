// Auth bootstrap (T21) — precondition for every later view.
//
// The launcher (built in a later task, see jobfinder/web/security.py's
// `LaunchToken` docstring) opens
// `http://127.0.0.1:<port>/?token=<launch-token>`. Every route except
// `/api/health` and `/api/auth/exchange` requires a session cookie
// (jobfinder/web/security.py's `SessionAuthMiddleware`), so this must run
// and succeed before anything else in the SPA touches the API.
//
// `components["schemas"]["TokenExchangeRequest"]` is generated from the
// backend's OpenAPI schema (`npm run gen:api`) — if the backend ever
// renames `token` on `TokenExchangeRequest`, this call site fails to
// compile instead of silently POSTing `{}` and getting a 422 at runtime.
import type { components, operations } from "./schema";

type TokenExchangeRequest = components["schemas"]["TokenExchangeRequest"];

// GET /api/settings has no declared `response_model` on the backend today
// (jobfinder/web/settings.py returns a plain dict), so openapi-typescript
// can only generate `{ [key: string]: unknown }` for it — a real
// generated type, just not a narrow one. Narrowing that is a backend
// change (adding a response_model), out of scope for T21/T22.
export type SettingsPayload =
  operations["get_settings_api_settings_get"]["responses"][200]["content"]["application/json"];

/**
 * Narrows `settings.agentic_filter.enabled` out of the untyped
 * `SettingsPayload` at runtime. Drives whether the Removed tab's
 * "by you" / "by AI" sub-tabs are shown (`jobfinder/ui/widget.py`'s
 * `_refresh_subtab_visibility`, T23) — existing behaviour, not a nicety.
 * Returns `false` (hide the sub-tabs) for any shape that doesn't match,
 * same fail-closed default `widget.py` uses via `bool(self._config and ...)`.
 */
export function isAgenticFilterEnabled(settings: SettingsPayload | null): boolean {
  if (typeof settings !== "object" || settings === null) {
    return false;
  }
  const af = (settings as Record<string, unknown>).agentic_filter;
  if (typeof af !== "object" || af === null) {
    return false;
  }
  return (af as Record<string, unknown>).enabled === true;
}

/**
 * Fetches `GET /api/settings` and returns the raw payload, or `null` on
 * any failure (network error, non-401 non-2xx status). Called after
 * `bootstrapAuth()` so the session cookie is already set; a 401 here
 * most likely means the exchange above failed, in which case the
 * settings-driven theme override is skipped and the OS preference
 * (tokens.css's `@media` block) decides instead.
 */
export async function fetchSettings(): Promise<SettingsPayload | null> {
  try {
    const resp = await fetch("/api/settings", { credentials: "same-origin" });
    if (!resp.ok) {
      return null;
    }
    return (await resp.json()) as SettingsPayload;
  } catch {
    return null;
  }
}

/**
 * Reads the `token` query param (if present), exchanges it for an
 * HttpOnly/SameSite=Strict session cookie via `POST /api/auth/exchange`,
 * then strips the token from the URL with `history.replaceState` so it
 * never lands in browser history or gets leaked via a `Referer` header on
 * a later cross-origin navigation.
 *
 * Safe to call unconditionally on every load: if there is no `token`
 * query param (e.g. a reload after the first exchange already ran), this
 * is a no-op. A failed exchange (invalid/consumed token) is reported via
 * the return value rather than thrown, so a stale/reloaded tab can show a
 * "this link has expired, relaunch from the app" message instead of a
 * crash.
 */
export async function bootstrapAuth(
  location: Pick<Location, "href" | "search"> = window.location,
): Promise<{ exchanged: boolean; ok: boolean }> {
  const params = new URLSearchParams(location.search);
  const token = params.get("token");

  if (!token) {
    return { exchanged: false, ok: true };
  }

  const body: TokenExchangeRequest = { token };

  let ok = false;
  try {
    const resp = await fetch("/api/auth/exchange", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify(body),
    });
    ok = resp.ok;
  } catch {
    ok = false;
  }

  // Strip the token from the URL regardless of outcome — it must never
  // stick around in history/referrer even if the exchange failed.
  params.delete("token");
  const url = new URL(location.href);
  url.search = params.toString();
  window.history.replaceState(null, "", url.toString());

  return { exchanged: true, ok };
}
