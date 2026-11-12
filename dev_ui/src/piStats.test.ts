import { describe, expect, it } from 'vitest'
import type { PiSample, PiStatsReply } from './api'
import { formatRate, hottest, keepCount, lineNames, mergeSamples, series, tempLevel, throttleWords } from './piStats'

// Only the fields a test reads; the helpers never look at the rest.
const at = (t: number, over: Partial<PiSample> = {}): PiSample => ({ at: t, ...over }) as PiSample

const reply = (every_sec: number, keep_min: number): PiStatsReply => ({
  collecting: true, every_sec, keep_min, info: {}, samples: [], address: 'pi.local',
})

describe('mergeSamples', () => {
  it('adds the new samples after the old ones, oldest first', () => {
    const merged = mergeSamples([at(1), at(2)], [at(3), at(4)], 10)
    expect(merged.map(s => s.at)).toEqual([1, 2, 3, 4])
  })

  it('takes a reply that starts at or before its newest as the Pi sending everything again (its clock stepped back)', () => {
    expect(mergeSamples([at(1), at(2)], [at(2), at(3)], 10).map(s => s.at)).toEqual([2, 3])
    expect(mergeSamples([at(500), at(600)], [at(100), at(102)], 10).map(s => s.at)).toEqual([100, 102])
  })

  it('keeps only the newest `keep` of a full resend too', () => {
    const fresh = Array.from({ length: 5 }, (_, i) => at(10 + i))
    expect(mergeSamples([at(900)], fresh, 3).map(s => s.at)).toEqual([12, 13, 14])
  })

  it('keeps the old samples when the reply is empty', () => {
    expect(mergeSamples([at(1), at(2)], [], 10).map(s => s.at)).toEqual([1, 2])
  })

  it('keeps only the newest `keep`, so a tab open for hours does not grow forever', () => {
    const old = Array.from({ length: 900 }, (_, i) => at(i))
    const fresh = Array.from({ length: 5 }, (_, i) => at(900 + i))
    const merged = mergeSamples(old, fresh, 900)
    expect(merged).toHaveLength(900)
    expect(merged[0].at).toBe(5)
    expect(merged[merged.length - 1].at).toBe(904)
  })

  it('keeps nothing when `keep` is 0', () => {
    expect(mergeSamples([at(1)], [at(2)], 0)).toEqual([])
  })
})

describe('keepCount', () => {
  it('is keep_min minutes of samples, one every every_sec', () => {
    expect(keepCount(reply(2, 30))).toBe(900)
    expect(keepCount(reply(3, 0.1))).toBe(2)
  })

  it('is 0 when the reply has no sensible every_sec', () => {
    expect(keepCount(reply(0, 30))).toBe(0)
  })
})

describe('series', () => {
  it('gives one value per sample, and null where the sample does not have it', () => {
    const samples = [at(1), at(2, { fans: [{ name: 'pwmfan', rpm: 3000, pwm_pct: 30 }] })]
    const values = series(samples, s => s.fans?.find(f => f.name === 'pwmfan')?.rpm)
    expect(values).toEqual([null, 3000])
  })
})

describe('lineNames', () => {
  it('lists each name once, in the order they first came', () => {
    const samples = [
      at(1, { network: [{ name: 'wlan0' } as never] }),
      at(2, { network: [{ name: 'eth0' } as never, { name: 'wlan0' } as never] }),
    ]
    expect(lineNames(samples, s => s.network)).toEqual(['wlan0', 'eth0'])
  })

  it('is empty when no sample has any (a Mac has no fan)', () => {
    expect(lineNames([at(1, { fans: [] })], s => s.fans)).toEqual([])
  })
})

describe('formatRate', () => {
  it('says bytes a second in words', () => {
    expect(formatRate(0)).toBe('0 B/s')
    expect(formatRate(1536)).toBe('1.5 KB/s')
    expect(formatRate(3 * 1024 * 1024)).toBe('3 MB/s')
  })
})

describe('hottest and tempLevel', () => {
  const limits = { warning_c: 70, serious_c: 80 }

  it('finds the hottest sensor, or null with none', () => {
    expect(hottest(at(1, { temps: [{ name: 'a', c: 50 }, { name: 'b', c: 61.2 }] }))).toEqual({ name: 'b', c: 61.2 })
    expect(hottest(at(1, { temps: [] }))).toBeNull()
  })

  it('is amber from warning_c and red from serious_c', () => {
    expect(tempLevel(69.9, limits)).toBe('ok')
    expect(tempLevel(70, limits)).toBe('warn')
    expect(tempLevel(80, limits)).toBe('bad')
  })
})

describe('throttleWords', () => {
  it("says the Pi firmware's slow-down flags in words: bits 0-3 now, bits 16-19 since the Pi started", () => {
    expect(throttleWords('0x50005')).toEqual({
      now: ['the power supply is too weak', 'the processor is slowed down'],
      earlier: ['the power supply was too weak', 'the processor was slowed down'],
    })
    expect(throttleWords('0xa')).toEqual({ now: ['the processor speed is capped', 'the processor is close to too hot'], earlier: [] })
    expect(throttleWords('0x80000')).toEqual({ now: [], earlier: ['the processor was close to too hot'] })
  })

  it('is empty for 0x0, and when the Pi could not read them', () => {
    for (const flags of ['0x0', '', 'nonsense']) expect(throttleWords(flags)).toEqual({ now: [], earlier: [] })
  })
})
