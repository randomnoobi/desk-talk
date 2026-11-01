// Which reports this browser has opened, so a report nobody has read yet can wear a "new" mark. It is kept in
// the browser's own storage and nowhere else. The browser may refuse storage (a private window, blocked site
// data), so every use is wrapped, and what was opened during this visit is also kept in memory: the page then
// works, and only forgets between visits.

const KEY = 'desktalk_cloud_seen_reports'
let thisVisit = new Set<string>()

function stored(): string[] {
  try {
    const found: unknown = JSON.parse(window.localStorage.getItem(KEY) ?? '[]')
    return Array.isArray(found) ? found.filter((id): id is string => typeof id === 'string') : []
  } catch {
    return []
  }
}

export function isSeen(reportId: string): boolean {
  return thisVisit.has(reportId) || stored().includes(reportId)
}

export function markSeen(reportId: string): void {
  thisVisit.add(reportId)
  try {
    window.localStorage.setItem(KEY, JSON.stringify([...new Set([...stored(), reportId])]))
  } catch {
    // storage is blocked: the memory copy above is all we have
  }
}

/** Forget what was opened during this visit (the tests use this between cases). */
export function clearSeen(): void {
  thisVisit = new Set()
}
