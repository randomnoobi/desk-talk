import type { DevSettings } from '../api'

// Placeholder: a later task fills in this tab.
export default function Checks(props: { settings: DevSettings }): JSX.Element {
  return (
    <div className="card">
      <h2>Checks</h2>
      <p className="muted">Run everything, or one test at a time. Not built yet.</p>
      <p className="muted small">The page asks for new numbers every {props.settings.poll_sec} s.</p>
    </div>
  )
}
