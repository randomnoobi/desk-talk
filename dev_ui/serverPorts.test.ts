import { mkdtempSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import { readServerPorts } from './serverPorts'

function configFolder(files: Record<string, string>): string {
  const dir = mkdtempSync(join(tmpdir(), 'dev-ui-config-'))
  for (const [name, text] of Object.entries(files)) writeFileSync(join(dir, name), text)
  return dir
}

describe('readServerPorts', () => {
  it('reads web.port and dev.vite_port from settings.yaml', () => {
    const dir = configFolder({ 'settings.yaml': 'web:\n  port: 5000\ndev:\n  vite_port: 5173\n' })
    expect(readServerPorts(dir)).toEqual({ webPort: 5000, vitePort: 5173 })
  })

  it('lets settings.local.yaml change them', () => {
    const dir = configFolder({
      'settings.yaml': 'web:\n  port: 5000\n  host: 0.0.0.0\ndev:\n  vite_port: 5173\n',
      'settings.local.yaml': 'web:\n  port: 5050\n',
    })
    expect(readServerPorts(dir)).toEqual({ webPort: 5050, vitePort: 5173 })
  })

  it('says plainly which setting is missing instead of guessing', () => {
    const dir = configFolder({ 'settings.yaml': 'web:\n  port: 5000\n' })
    expect(() => readServerPorts(dir)).toThrow(/dev\.vite_port/)
  })

  it('refuses a port that isn\'t a number', () => {
    const dir = configFolder({ 'settings.yaml': 'web:\n  port: fast\ndev:\n  vite_port: 5173\n' })
    expect(() => readServerPorts(dir)).toThrow(/web\.port/)
  })
})
