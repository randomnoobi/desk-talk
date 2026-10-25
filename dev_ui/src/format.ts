// Numbers in words the tabs share. Times from the server are seconds since 1970.

const SIZES = ['B', 'KB', 'MB', 'GB', 'TB']

/** 1536 → "1.5 KB" (1 KB = 1024 bytes). */
export function formatBytes(bytes: number): string {
  let n = bytes
  let i = 0
  while (n >= 1024 && i < SIZES.length - 1) {
    n /= 1024
    i += 1
  }
  return `${i === 0 ? n : Number(n.toFixed(1))} ${SIZES[i]}`
}

/** 125 → "2 min 5 s", 3700 → "1 h 1 min". Under 10 s keeps one decimal: 0.4 → "0.4 s". */
export function formatSeconds(sec: number): string {
  const s = Math.abs(sec)
  if (s < 10) return `${Number(s.toFixed(1))} s`
  if (s < 60) return `${Math.round(s)} s`
  const whole = Math.round(s)
  const [d, h, m, rest] = [Math.floor(whole / 86400), Math.floor(whole / 3600) % 24, Math.floor(whole / 60) % 60, whole % 60]
  if (d) return h ? `${d} d ${h} h` : `${d} d`
  if (h) return m ? `${h} h ${m} min` : `${h} h`
  return rest ? `${m} min ${rest} s` : `${m} min`
}

/** How long ago `ts` was, as seen at `now` (both seconds since 1970): "12 s ago", or "in 3 s". */
export function formatAgo(ts: number, now: number): string {
  return ts > now ? `in ${formatSeconds(ts - now)}` : `${formatSeconds(now - ts)} ago`
}

/** The time of day on this computer, e.g. "14:03:27". */
export function formatClock(ts: number): string {
  return new Date(ts * 1000).toLocaleTimeString([], { hour12: false })
}

/** The date and time on this computer, e.g. "2026-10-25 14:03". */
export function formatDateTime(ts: number): string {
  const d = new Date(ts * 1000)
  const two = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${two(d.getMonth() + 1)}-${two(d.getDate())} ${two(d.getHours())}:${two(d.getMinutes())}`
}

/** The last `chars` characters of `text`: the oldest text goes first, like the server's own printout. */
export function keepLast(text: string, chars: number): string {
  return text.length > chars ? text.slice(-chars) : text
}
