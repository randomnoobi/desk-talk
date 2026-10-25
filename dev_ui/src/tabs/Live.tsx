import type { DevSettings } from '../api'

// Placeholder: a later task fills in this tab.
export default function Live(props: { settings: DevSettings }): JSX.Element {
  return (
    <div className="card">
      <h2>Live</h2>
      <p className="muted">The object states, their changes and the smoothing. Not built yet.</p>
      <p className="muted small">The page asks for new numbers every {props.settings.poll_sec} s.</p>
    </div>
  )
}
