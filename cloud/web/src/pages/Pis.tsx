// The person's Pis: a list, and a button to add one.
import { listDevices } from '../api'
import { formatTime } from '../format'
import { CodeMaker, Loading, Problem } from '../ui'
import { useLoad } from '../useLoad'

export default function Pis() {
  const { data, error, loading } = useLoad(listDevices, 0)
  return (
    <section className="stack">
      <h2>Your Pis</h2>
      {error && <Problem error={error} />}
      {loading && !data && <Loading />}
      {data && data.length === 0 && <p>No Pis linked yet.</p>}
      {data && data.length > 0 && (
        <ul className="list">
          {data.map(device => (
            <li key={device.id}>
              <a href={`#/pis/${encodeURIComponent(device.id)}`}>{device.name}</a>
              {device.removed && <span className="tag">Removed (history kept)</span>}
              <span className="muted small">{device.last_seen_at === null ? 'Not seen yet' : `Last seen ${formatTime(device.last_seen_at)}`}</span>
            </li>
          ))}
        </ul>
      )}
      <CodeMaker label="Add a Pi" />
    </section>
  )
}
