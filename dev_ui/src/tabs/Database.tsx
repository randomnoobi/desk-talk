import type { DevSettings } from '../api'

// Placeholder: a later task fills in this tab.
export default function Database(props: { settings: DevSettings }): JSX.Element {
  return (
    <div className="card">
      <h2>Database</h2>
      <p className="muted">The tables, and simple read-only queries. Not built yet.</p>
      <p className="muted small">The page asks for new numbers every {props.settings.poll_sec} s.</p>
    </div>
  )
}
