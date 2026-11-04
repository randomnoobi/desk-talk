// The website's build and dev server. In production nginx serves the built files and passes /api, /auth
// and /v1 to the server. For `npm run dev`, set DESKTALK_CLOUD_API to the server's address (for example
// the one `docker compose up` shows) and the dev server passes those same paths on, so the page can
// stay on one address, as it does in production.
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

export default defineConfig(({ command, mode }) => {
  const target = command === 'serve' && mode !== 'test' ? process.env.DESKTALK_CLOUD_API : undefined
  return {
    plugins: [react()],
    server: target
      ? { proxy: { '/api': target, '/auth': target, '/v1': target } }
      : undefined,
    test: {
      environment: 'jsdom',
      setupFiles: ['src/test-setup.ts'],
    },
  }
})
