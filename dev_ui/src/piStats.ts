// The sums behind the Pi tab: keeping the samples, pulling one line out of them, and numbers in words.
import type { PiHotLimits, PiSample, PiStatsReply } from './api'
import { formatBytes } from './format'
import type { Level } from './ui'

/**
 * The old samples and the new ones together, oldest first, one per moment (`at`), and only the newest `keep`.
 * The page asks for samples newer than its newest, so a normal reply only has newer ones. When the Pi's clock
 * steps back (it has no clock battery), its newest sample is older than the page's, and it sends all its samples
 * again: a reply that starts at or before the page's newest is that full resend, so it replaces the old samples.
 */
export function mergeSamples(old: PiSample[], fresh: PiSample[], keep: number): PiSample[] {
  if (keep <= 0) return []
  const newestOld = old.length ? old[old.length - 1].at : -Infinity
  const resent = fresh.length > 0 && fresh[0].at <= newestOld
  const byAt = new Map<number, PiSample>()
  for (const s of resent ? fresh : [...old, ...fresh]) byAt.set(s.at, s)
  const all = [...byAt.values()].sort((a, b) => a.at - b.at)
  return all.length > keep ? all.slice(all.length - keep) : all
}

// The Pi firmware's slow-down flags (vcgencmd get_throttled): bits 0-3 say what's happening now, bits 16-19 the
// same things since the Pi started. The same list as THROTTLE_BITS in raspberry_pi/camera/health.py.
const THROTTLE_BITS: [number, number, string, string][] = [
  [0, 16, 'the power supply is too weak', 'the power supply was too weak'],
  [1, 17, 'the processor speed is capped', 'the processor speed was capped'],
  [2, 18, 'the processor is slowed down', 'the processor was slowed down'],
  [3, 19, 'the processor is close to too hot', 'the processor was close to too hot'],
]

/** The slow-down flags (e.g. '0x50005') in plain words: what's happening now, and what happened earlier.
 *  Both empty for '0x0', and for '' (the Pi couldn't read them). */
export function throttleWords(flags: string): { now: string[], earlier: string[] } {
  const value = parseInt(flags, 16)
  if (Number.isNaN(value)) return { now: [], earlier: [] }
  const on = (bit: number) => Math.floor(value / 2 ** bit) % 2 === 1
  return {
    now: THROTTLE_BITS.filter(([bit]) => on(bit)).map(([, , words]) => words),
    earlier: THROTTLE_BITS.filter(([, bit]) => on(bit)).map(([, , , words]) => words),
  }
}

/** How many samples the Pi itself keeps: keep_min minutes, one every every_sec. The page keeps no more. */
export function keepCount(reply: Pick<PiStatsReply, 'every_sec' | 'keep_min'>): number {
  if (!(reply.every_sec > 0)) return 0
  return Math.max(0, Math.round(reply.keep_min * 60 / reply.every_sec))
}

/** One value per sample for a chart line; null where the sample doesn't have it (a fan that came later). */
export function series(samples: PiSample[], pick: (s: PiSample) => number | null | undefined): (number | null)[] {
  return samples.map(s => {
    const v = pick(s)
    return v === undefined || v === null || !Number.isFinite(v) ? null : v
  })
}

/** Every name in one of the samples' lists (temps, fans, network ...), once each, in the order they first came,
 *  so a line keeps its place, and its colour, while the samples move on. */
export function lineNames(samples: PiSample[], list: (s: PiSample) => { name: string }[] | undefined): string[] {
  const names: string[] = []
  for (const s of samples) {
    for (const item of list(s) ?? []) if (!names.includes(item.name)) names.push(item.name)
  }
  return names
}

/** Bytes a second in words: 1536 → "1.5 KB/s". */
export function formatRate(bytesPerSec: number): string {
  return `${formatBytes(Math.max(0, bytesPerSec))}/s`
}

/** The hottest sensor in a sample, or null when it has none. */
export function hottest(sample: PiSample): { name: string, c: number } | null {
  let top: { name: string, c: number } | null = null
  for (const t of sample.temps ?? []) if (top === null || t.c > top.c) top = t
  return top
}

/** Amber from settings → pi.hot_warning_c, red from pi.hot_serious_c (the lines the health checks use too). */
export function tempLevel(c: number, limits: PiHotLimits): Level {
  return c >= limits.serious_c ? 'bad' : c >= limits.warning_c ? 'warn' : 'ok'
}
