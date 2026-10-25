import type { DevSettings } from '../api'

// Placeholder: a later task fills in this tab.
export default function Storage(props: { settings: DevSettings }): JSX.Element {
  return (
    <div className="card">
      <h2>Storage</h2>
      <p className="muted">What this browser and the data folder keep. Not built yet.</p>
      <p className="muted small">The page asks for new numbers every {props.settings.poll_sec} s.</p>
    </div>
  )
}
