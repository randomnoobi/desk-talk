// The list of weekly reports, newest week first. A report nobody has opened yet wears a "new" mark.
import { listAllReports, type Report } from '../api'
import { isSeen } from '../seen'
import { Loading, Problem } from '../ui'
import { useLoad } from '../useLoad'

/** The short note under a report in the list (and at the top of a report that has no text). */
export function statusWords(report: Report): string | null {
  if (report.status === 'pending') return 'Being written.'
  if (report.status === 'no_data') return 'No data from this Pi that week, so there is no report.'
  if (report.status === 'failed') return "This report couldn't be written."
  return null
}

/** True for a finished report this browser hasn't opened. */
export function isNew(report: Report): boolean {
  return report.status === 'done' && !isSeen(report.id)
}

export default function Reports() {
  const { data, error, loading } = useLoad(listAllReports, 0)
  return (
    <section className="stack">
      <h2>Reports</h2>
      {error && <Problem error={error} />}
      {loading && !data && <Loading />}
      {data && data.length === 0 && <p>No reports yet. A report is written each week for each Pi.</p>}
      {data && data.length > 0 && (
        <ul className="list">
          {data.map(({ report, deviceName }) => (
            <li key={report.id}>
              <a href={`#/reports/${encodeURIComponent(report.id)}`}>Week of {report.week_start}</a>
              {isNew(report) && <span className="mark">new</span>}
              <span className="muted small">{deviceName}</span>
              {statusWords(report) && <div className="small">{statusWords(report)}{report.error && <> <span>{report.error}</span></>}</div>}
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
