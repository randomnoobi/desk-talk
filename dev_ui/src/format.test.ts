import { describe, expect, it } from 'vitest'
import { formatAgo, formatBytes, formatSeconds, keepLast } from './format'

describe('formatBytes', () => {
  it.each([
    [0, '0 B'], [512, '512 B'], [1536, '1.5 KB'], [64 * 1024, '64 KB'], [5 * 1024 * 1024, '5 MB'],
    [3.25 * 1024 ** 3, '3.3 GB'],
  ])('%d bytes is %s', (n, words) => {
    expect(formatBytes(n)).toBe(words)
  })
})

describe('formatSeconds', () => {
  it.each([
    [0.4, '0.4 s'], [42, '42 s'], [125, '2 min 5 s'], [3700, '1 h 1 min'], [90000, '1 d 1 h'],
  ])('%d seconds is %s', (sec, words) => {
    expect(formatSeconds(sec)).toBe(words)
  })
})

describe('formatAgo', () => {
  it('says how long ago, or how long until', () => {
    expect(formatAgo(100, 112)).toBe('12 s ago')
    expect(formatAgo(115, 112)).toBe('in 3 s')
  })
})

describe('keepLast', () => {
  it('keeps text that fits as it is', () => {
    expect(keepLast('hello', 5)).toBe('hello')
  })

  it('lets the oldest text go when there is too much', () => {
    expect(keepLast('old lines, new lines', 9)).toBe('new lines')
  })
})
