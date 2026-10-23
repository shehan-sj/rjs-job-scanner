import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import './theme/tokens.css'
import App from './App.tsx'
import { bootstrapAuth, fetchSettings } from './api/client'
import { applyServerTheme } from './theme/theme'

// Must complete (or at least run) before the app renders anything that
// touches the API — every route but /api/health and /api/auth/exchange
// requires the session cookie this sets.
await bootstrapAuth()

// config.ui.theme (server-side, GET /api/settings) is the explicit
// theme override, taking precedence over the OS's prefers-color-scheme
// (T22). Best-effort: fetchSettings() already swallows failures and
// applyServerTheme() ignores an unset/unrecognized value, so a settings
// fetch failure just leaves the OS preference in control.
const settings = await fetchSettings()
const ui = settings?.ui
const theme =
  typeof ui === 'object' && ui !== null && 'theme' in ui && typeof ui.theme === 'string'
    ? ui.theme
    : undefined
applyServerTheme(theme)

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
