import { defineConfig } from 'vitest/config'
import vue from '@vitejs/plugin-vue'

// Dev: `npm run dev` proxies API calls to the running service (start it with AKU_DEV_ORIGIN=http://localhost:5173).
// Build: output goes into the Python package, which serves it.
const api = 'http://127.0.0.1:4587'
const paths = ['/devices', '/status', '/toggle', '/sessions', '/settings', '/vocab', '/voiceprints']

export default defineConfig({
  plugins: [vue()],
  build: { outDir: '../backend/aku/static', emptyOutDir: true },
  server: {
    port: 5173,
    proxy: {
      ...Object.fromEntries(paths.map((p) => [p, { target: api, changeOrigin: true }])),
      '/live': { target: api, ws: true, changeOrigin: true },
    },
  },
  test: { environment: 'jsdom' },
})
