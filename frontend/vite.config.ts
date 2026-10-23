import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// Backend port — must match jobfinder/web/app.py's DEFAULT_PORT so the
// Host/Origin allowlists installed by install_security() (T15) line up
// with what the dev proxy actually forwards to.
const BACKEND_PORT = 8756

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      // Dev proxy, not CORS (per the product spec): same-origin in dev means
      // the session cookie set by POST /api/auth/exchange is sent on
      // every subsequent proxied request with no CORS config needed on
      // either side.
      '/api': {
        target: `http://127.0.0.1:${BACKEND_PORT}`,
        changeOrigin: true,
        // SSE (/api/stream/scan) must stream, not buffer. Node's
        // http-proxy (what Vite's dev server proxy wraps) pipes the
        // upstream response through as chunks arrive by default — no
        // buffering option to disable here. What *does* break SSE if
        // left on is compression: an upstream/gzip layer would buffer
        // the stream waiting for enough bytes to compress, which is why
        // the FastAPI side (jobfinder/web/stream.py) must not wrap the
        // EventSourceResponse in a compression middleware. Verified by
        // manually curling /api/stream/scan through the running Vite
        // dev server and observing `data: ` lines arrive incrementally
        // rather than all at once at connection close — see T21 report.
        ws: false,
      },
    },
  },
})
