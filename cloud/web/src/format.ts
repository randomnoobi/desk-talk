// Small helpers for showing times and values the server sends.

/** A time the server sent (seconds since 1970) in the person's own time zone and language. */
export function formatTime(ts: number | null | undefined): string {
  if (typeof ts !== 'number' || !Number.isFinite(ts)) return 'Not seen yet'
  return new Date(ts * 1000).toLocaleString()
}

/** "water_ml" -> "water ml" */
export function plainName(key: string): string {
  return key.replace(/_/g, ' ')
}

/** A value from an event or a state, written the way a person would read it. */
export function plainValue(value: unknown): string {
  if (typeof value === 'string') return value
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  return JSON.stringify(value)
}

/** This browser's time zone, which becomes a new Pi's. */
export function browserTimeZone(): string {
  return Intl.DateTimeFormat().resolvedOptions().timeZone
}
