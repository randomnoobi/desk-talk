// One Pi: what it sees now, its goals, its recent events, and the buttons to rename, remove or delete it.
import { useState } from 'react'
import {
  ApiError, deleteDeviceWithData, getState, recentEvents, removeDevice, renameDevice,
  type CloudEvent, type DeviceState,
} from '../api'
import { formatTime, plainName, plainValue } from '../format'
import { CodeMaker, Confirm, Loading, Problem } from '../ui'
import { useLoad } from '../useLoad'

function Facts({ title, facts }: { title: string, facts: Record<string, unknown> | null }) {
  if (!facts) return <section><h3>{title}</h3><p className="muted">Nothing yet.</p></section>
  const { ts, ...rest } = facts
  const shown = Object.entries(rest).filter(([key]) => key !== 'id' && key !== 'type')
  return (
    <section>
      <h3>{title}</h3>
      {typeof ts === 'number' && <p className="muted small">As of {formatTime(ts)}</p>}
      <dl className="facts">
        {shown.map(([key, value]) => (
          <div key={key}><dt>{plainName(key)}</dt><dd>{plainValue(value)}</dd></div>
        ))}
      </dl>
    </section>
  )
}

function Events({ deviceId, hours }: { deviceId: string, hours: number }) {
  const { data, error, loading } = useLoad<CloudEvent[]>(
    () => recentEvents(deviceId, Math.floor(Date.now() / 1000) - hours * 3600), deviceId)
  return (
    <section>
      <h3>Recent events</h3>
      {error && <Problem error={error} />}
      {loading && !data && <Loading />}
      {data && data.length === 0 && <p className="muted">No events in the last {hours} hours.</p>}
      {data && data.length > 0 && (
        <ul className="list events">
          {data.map(event => (
            <li key={event.id} aria-label="Event">
              <strong>{event.type}</strong> <span className="muted small">{formatTime(event.ts)}</span>
              <div className="small">{Object.entries(event.details).map(([k, v]) => `${plainName(k)}: ${plainValue(v)}`).join(', ')}</div>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

function Rename({ state, onSaved }: { state: DeviceState, onSaved: () => void }) {
  const [name, setName] = useState(state.device.name)
  const [problem, setProblem] = useState<string | null>(null)

  async function save() {
    setProblem(null)
    try {
      await renameDevice(state.device.id, name)
      onSaved()
    } catch (error) {
      setProblem(error instanceof ApiError ? error.message : "Couldn't save the name.")
    }
  }

  return (
    <form className="row" onSubmit={e => { e.preventDefault(); void save() }}>
      <label className="grow">Name <input className="input" value={name} onChange={e => setName(e.target.value)} /></label>
      <button type="submit" className="btn">Save name</button>
      {problem && <Problem error={problem} />}
    </form>
  )
}

export default function OnePi({ id, afterDelete = () => { window.location.hash = '#/' } }: { id: string, afterDelete?: () => void }) {
  const { data, error, loading, reload } = useLoad(() => getState(id), id)
  const [asking, setAsking] = useState<'remove' | 'delete' | null>(null)
  const [problem, setProblem] = useState<string | null>(null)

  async function carryOut(action: 'remove' | 'delete') {
    setAsking(null)
    setProblem(null)
    try {
      if (action === 'remove') {
        await removeDevice(id)
        reload()
      } else {
        await deleteDeviceWithData(id)
        afterDelete()
      }
    } catch (failure) {
      setProblem(failure instanceof ApiError ? failure.message : "That didn't work.")
    }
  }

  if (error) return <section><Problem error={error} /><p><a href="#/">Back to your Pis</a></p></section>
  if (!data) return loading ? <Loading /> : null
  const removed = data.device.removed

  return (
    <section className="stack">
      <p><a href="#/">All Pis</a></p>
      <h2>{data.device.name}</h2>
      {removed && <p className="tag">Removed. Its history is kept, and it can't send new events until it is linked again.</p>}
      <p className="muted small">
        {data.last_seen_at === null ? 'Not seen yet' : `Last seen ${formatTime(data.last_seen_at)}`}. Time zone {data.device.time_zone}.
      </p>

      <section>
        <h3>Objects</h3>
        {data.objects.length === 0 && <p className="muted">None seen yet.</p>}
        <ul className="list">
          {data.objects.map(o => (
            <li key={o.label}><strong>{o.label}</strong> {o.state} <span className="muted small">since {formatTime(o.since)}</span></li>
          ))}
        </ul>
      </section>
      <Facts title="Goals" facts={data.goals} />
      <Facts title="Camera" facts={data.last_camera} />
      {data.recent_hours !== null && <Events deviceId={id} hours={data.recent_hours} />}

      <section className="stack">
        <h3>This Pi</h3>
        <Rename state={data} onSaved={reload} />
        {problem && <Problem error={problem} />}
        {removed && <CodeMaker label="Link again" deviceId={id} />}
        <div className="row">
          {!removed && <button type="button" className="btn" onClick={() => setAsking('remove')}>Remove this Pi</button>}
          <button type="button" className="btn danger" onClick={() => setAsking('delete')}>Delete this Pi and its data</button>
        </div>
        {asking === 'remove' && (
          <Confirm
            question="Remove this Pi? It stops sending at once, and the server keeps its history. You can link it again later."
            doIt="Remove" onNo={() => setAsking(null)} onYes={() => void carryOut('remove')} />
        )}
        {asking === 'delete' && (
          <Confirm
            question="Delete this Pi and everything stored for it: its events, its state and its reports? This can't be undone."
            doIt="Delete" danger onNo={() => setAsking(null)} onYes={() => void carryOut('delete')} />
        )}
      </section>
    </section>
  )
}
