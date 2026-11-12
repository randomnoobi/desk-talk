// The sums behind the Pi tab: keeping the samples, pulling one line out of them, and numbers in words.
import type { HealthLimits, PiSample, PiStatsReply } from './api'
import { formatBytes } from './format'
import type { Level } from './ui'

/**
 * The old samples and the new ones together, oldest first, one per moment (`at`), and only the newest `keep`.
 * A reply can repeat a sample the page already has (the Pi's clock stepped back), so it's matched by `at`.
 */
export function mergeSamples(old: PiSample[], fresh: PiSample[], keep: number): PiSample[] {
  if (keep <= 0) return []
  const byAt = new Map<number, PiSample>()
  for (const s of [...old, ...fresh]) byAt.set(s.at, s)
  const all = [...byAt.values()].sort((a, b) => a.at - b.at)
  return all.length > keep ? all.slice(all.length - keep) : all
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

/** Amber from settings → dev.health.pi_cpu_warn_c, red from pi_cpu_bad_c (the System tab's lines too). */
export function tempLevel(c: number, limits: Pick<HealthLimits, 'pi_cpu_warn_c' | 'pi_cpu_bad_c'>): Level {
  return c >= limits.pi_cpu_bad_c ? 'bad' : c >= limits.pi_cpu_warn_c ? 'warn' : 'ok'
}
