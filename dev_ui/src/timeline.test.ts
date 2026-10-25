import { describe, expect, it } from 'vitest'
import type { SmoothingSettings, SmoothingStep, StateChange } from './api'
import { countdown, flipsPerMinute, isFlickering, keepRecent, mergeChanges, newestFirst, withPhases } from './timeline'

const cup = (ts: number, change: StateChange['change']): StateChange => ({ ts, label: 'cup', change })

const step = (over: Partial<SmoothingStep>): SmoothingStep => ({
  ts: 100, conf: 0.9, box_seen: true, votes_yes: 3, votes_total: 5, seen: true, center: null,
  phase: 'PRESENT', heading: null, waiting_sec: null, ...over,
})

const smoothing: SmoothingSettings = {
  on_confidence: 0.5, off_confidence: 0.4, appear_sec: 3, gone_sec: 5, window_pictures: 5, vote_share: 0.5,
}

describe('flipsPerMinute', () => {
  it('counts five changes for cup in the last 60 s as 5', () => {
    const changes = [cup(941, 'appeared'), cup(950, 'removed'), cup(962, 'appeared'), cup(975, 'removed'),
                     cup(999, 'appeared'), { ts: 990, label: 'pen', change: 'removed' as const }]
    expect(flipsPerMinute(changes, 'cup', 1000)).toBe(5)
    expect(flipsPerMinute(changes, 'pen', 1000)).toBe(1)
  })

  it('leaves out changes older than a minute, and moves', () => {
    const changes = [cup(900, 'appeared'), cup(939, 'removed'), cup(950, 'appeared'), cup(960, 'moved')]
    expect(flipsPerMinute(changes, 'cup', 1000)).toBe(1)
  })

  it('calls more than the limit of flips in a minute flickering', () => {
    const four = [cup(950, 'appeared'), cup(955, 'removed'), cup(960, 'appeared'), cup(965, 'removed')]
    expect(isFlickering(four, 'cup', 1000, 4)).toBe(false)
    expect(isFlickering([...four, cup(970, 'appeared')], 'cup', 1000, 4)).toBe(true)
  })

  it('takes the limit it is given (settings → dev.flicker_flips_per_min), not a fixed one', () => {
    const three = [cup(950, 'appeared'), cup(955, 'removed'), cup(960, 'appeared')]
    expect(isFlickering(three, 'cup', 1000, 2)).toBe(true)
    expect(isFlickering(three, 'cup', 1000, 3)).toBe(false)
  })
})

describe('mergeChanges', () => {
  it('keeps each change once when the lists overlap, oldest first', () => {
    const old = [cup(1, 'appeared'), cup(2, 'moved'), cup(3, 'removed')]
    const fresh = [cup(3, 'removed'), cup(4, 'appeared'), { ts: 3, label: 'pen', change: 'removed' as const }]
    expect(mergeChanges(old, fresh)).toEqual([
      cup(1, 'appeared'), cup(2, 'moved'), cup(3, 'removed'), { ts: 3, label: 'pen', change: 'removed' }, cup(4, 'appeared'),
    ])
  })

  it('keeps the old list as it is when nothing is new', () => {
    const old = [cup(1, 'appeared')]
    expect(mergeChanges(old, [])).toBe(old)
    expect(mergeChanges(old, [cup(1, 'appeared')])).toBe(old)
  })
})

describe('withPhases and newestFirst', () => {
  it('works out before and after from the change and the ones before it', () => {
    const rows = withPhases([cup(1, 'appeared'), cup(2, 'moved'), cup(3, 'removed'), cup(4, 'appeared')])
    expect(rows.map(r => `${r.before} → ${r.after}`)).toEqual([
      'UNKNOWN or ABSENT → PRESENT', 'PRESENT → PRESENT', 'PRESENT → ABSENT', 'ABSENT → PRESENT',
    ])
    expect(newestFirst(rows).map(r => r.ts)).toEqual([4, 3, 2, 1])
  })
})

describe('countdown', () => {
  it('says how long it has waited out of appear_sec or gone_sec', () => {
    expect(countdown(step({ heading: 'PRESENT', waiting_sec: 1.2, phase: 'ABSENT' }), smoothing))
      .toEqual({ heading: 'PRESENT', waited: 1.2, of: 3 })
    expect(countdown(step({ heading: 'ABSENT', waiting_sec: 2 }), smoothing))
      .toEqual({ heading: 'ABSENT', waited: 2, of: 5 })
  })

  it('is null when it is not waiting', () => {
    expect(countdown(step({}), smoothing)).toBeNull()
    expect(countdown(undefined, smoothing)).toBeNull()
  })
})

describe('keepRecent', () => {
  it('gives back the same list when nothing has to go', () => {
    const changes = [cup(10, 'appeared'), cup(20, 'removed')]
    expect(keepRecent(changes, 1000, 5)).toBe(changes)
  })

  it('keeps only the newest ones the list shows', () => {
    const changes = [cup(10, 'appeared'), cup(20, 'removed'), cup(30, 'appeared')]
    expect(keepRecent(changes, 1000, 2)).toEqual([cup(20, 'removed'), cup(30, 'appeared')])
  })

  it('keeps every change from the last minute too, so the flickering badge still counts them all', () => {
    const changes = [cup(10, 'appeared'), cup(950, 'removed'), cup(960, 'appeared'), cup(970, 'removed')]
    expect(keepRecent(changes, 1000, 1)).toEqual([cup(950, 'removed'), cup(960, 'appeared'), cup(970, 'removed')])
  })
})
