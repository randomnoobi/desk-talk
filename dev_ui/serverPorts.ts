// Reads the two ports `npm run dev` needs from config/settings.yaml, with settings.local.yaml laid on
// top the same way the server does it (nested sections are merged, not replaced).
import { existsSync, readFileSync } from 'node:fs'
import { join } from 'node:path'
import { parse } from 'yaml'

export interface ServerPorts {
  webPort: number   // web.port: where the Desk Talk server answers; /api goes there
  vitePort: number  // dev.vite_port: where `npm run dev` serves this page
}

type Section = Record<string, unknown>

function isSection(value: unknown): value is Section {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function merge(base: Section, extra: Section): Section {
  const out: Section = { ...base }
  for (const [key, value] of Object.entries(extra)) {
    const old = out[key]
    out[key] = isSection(value) && isSection(old) ? merge(old, value) : value
  }
  return out
}

function readFile(path: string): Section {
  const data: unknown = parse(readFileSync(path, 'utf8'))
  if (data == null) return {}
  if (!isSection(data)) throw new Error(`${path} should be a list of settings, not ${JSON.stringify(data)}`)
  return data
}

function port(settings: Section, section: string, key: string, files: string): number {
  const part = settings[section]
  const value = isSection(part) ? part[key] : undefined
  if (value === undefined) {
    throw new Error(`The developer page needs ${section}.${key} in ${files}, and it isn't there. ` +
      `Add it under "${section}:" in config/settings.yaml.`)
  }
  if (typeof value !== 'number' || !Number.isInteger(value) || value < 1 || value > 65535) {
    throw new Error(`${section}.${key} in ${files} should be a port number, like 5000 (got ${JSON.stringify(value)}).`)
  }
  return value
}

export function readServerPorts(configFolder: string): ServerPorts {
  let settings = readFile(join(configFolder, 'settings.yaml'))
  let files = 'config/settings.yaml'
  const local = join(configFolder, 'settings.local.yaml')
  if (existsSync(local)) {
    settings = merge(settings, readFile(local))
    files += ' (or settings.local.yaml)'
  }
  return { webPort: port(settings, 'web', 'port', files), vitePort: port(settings, 'dev', 'vite_port', files) }
}
