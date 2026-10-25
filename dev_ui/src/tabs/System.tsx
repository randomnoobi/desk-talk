import type { DevSettings } from '../api'

// Placeholder: a later task fills in this tab.
export default function System(props: { settings: DevSettings }): JSX.Element {
  return (
    <div className="card">
      <h2>System</h2>
      <p className="muted">The Pi and every part of the app. Not built yet.</p>
      <p className="muted small">The page asks for new numbers every {props.settings.poll_sec} s.</p>
    </div>
  )
}
