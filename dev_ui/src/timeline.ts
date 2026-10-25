// The Live tab's sums: flips per minute, joining the lists of changes, before → after, and the
// countdown while an object waits. Times are seconds since 1970, like the server's.
import type { Phase, SmoothingSettings, SmoothingStep, StateChange } from './api'

const MINUTE_SEC = 60

/** How many times `label` appeared or was removed in the minute before `now`. Moves don't count. */
export function flipsPerMinute(changes: StateChange[], label: string, now: number): number {
  return changes.filter(c => c.label === label && c.change !== 'moved' && c.ts > now - MINUTE_SEC && c.ts <= now).length
}

/**
 * Whether `label` gets the "flickering" badge: more than `limit` flips (appeared or removed) in the
 * minute before `now`. The limit is settings → dev.flicker_flips_per_min.
 */
export function isFlickering(changes: StateChange[], label: string, now: number, limit: number): boolean {
  return flipsPerMinute(changes, label, now) > limit
}

const keyOf = (c: StateChange) => `${c.ts}|${c.label}|${c.change}`

/**
 * The changes we had plus the new ones, each once (the same ts, label and change is the same change),
 * oldest first. Gives back `old` itself when nothing is new, so React sees no change.
 */
export function mergeChanges(old: StateChange[], fresh: StateChange[]): StateChange[] {
  const known = new Set(old.map(keyOf))
  const added: StateChange[] = []
  for (const c of fresh) {
    const key = keyOf(c)
    if (!known.has(key)) {
      known.add(key)
      added.push(c)
    }
  }
  if (added.length === 0) return old
  return [...old, ...added].sort((a, b) => a.ts - b.ts)   // a stable sort keeps same-time changes in order
}

export interface ChangeRow extends StateChange {
  before: string
  after: Phase
}

/**
 * Adds what the object was before and after each change (oldest first in, oldest first out).
 * `removed` is always PRESENT → ABSENT and `moved` PRESENT → PRESENT. Before an `appeared` it was ABSENT
 * if we saw it go earlier, and otherwise UNKNOWN or ABSENT (UNKNOWN to ABSENT happens without a change).
 */
export function withPhases(changes: StateChange[]): ChangeRow[] {
  const seenBefore = new Set<string>()
  return changes.map(c => {
    const known = seenBefore.has(c.label)
    seenBefore.add(c.label)
    if (c.change === 'removed') return { ...c, before: 'PRESENT', after: 'ABSENT' }
    if (c.change === 'moved') return { ...c, before: 'PRESENT', after: 'PRESENT' }
    return { ...c, before: known ? 'ABSENT' : 'UNKNOWN or ABSENT', after: 'PRESENT' }
  })
}

export function newestFirst<T extends { ts: number }>(rows: T[]): T[] {
  return [...rows].reverse()
}

export interface Countdown {
  heading: Phase
  waited: number      // seconds it has waited so far
  of: number          // appear_sec or gone_sec: it switches once it has waited longer than this
}

/** While the newest picture says the object is waiting to switch: how long out of how long. */
export function countdown(step: SmoothingStep | undefined, rules: SmoothingSettings): Countdown | null {
  if (!step || step.heading === null || step.waiting_sec === null) return null
  return { heading: step.heading, waited: step.waiting_sec, of: step.heading === 'PRESENT' ? rules.appear_sec : rules.gone_sec }
}
