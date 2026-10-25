// The developer page's build and dev server. The ports come from config/settings.yaml (and
// settings.local.yaml), so this file keeps no copies of them.
import { fileURLToPath } from 'node:url'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'
import { readServerPorts } from './serverPorts'

const configFolder = fileURLToPath(new URL('../config', import.meta.url))
const fontsFolder = fileURLToPath(new URL('../web_app/fonts', import.meta.url))
const thisFolder = fileURLToPath(new URL('.', import.meta.url))

export default defineConfig(({ command, mode }) => {
  // Only `npm run dev` talks to the server. Tests and builds don't need the ports, so they work
  // even before config/settings.yaml has a dev: section.
  const ports = command === 'serve' && mode !== 'test' ? readServerPorts(configFolder) : null
  return {
    base: '/dev/',                      // the main server shows the built page at /dev/
    plugins: [react()],
    server: ports
      ? {
          port: ports.vitePort,
          strictPort: true,             // another port would be a different origin, and the server would say 403
          proxy: { '/api': `http://127.0.0.1:${ports.webPort}` },
          fs: { allow: [thisFolder, fontsFolder] },   // the font is shared with the app page
        }
      : undefined,
    test: {
      environment: 'jsdom',
      setupFiles: ['src/test-setup.ts'],
    },
  }
})
