// The admin page: who has an account, whether each Pi is working, which reports failed and each Pi's latest
// finished report (with a button to run one again). It has no events and no report text: it never asks for them.
import { useState } from 'react'
import { adminLogin, ApiError, getAdminOverview, rerunReport, type AdminOverview } from '../api'
import { formatTime } from '../format'
import { Loading, Problem } from '../ui'
import { useLoad } from '../useLoad'

function AdminLogin({ onDone }: { onDone: () => void }) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [problem, setProblem] = useState<string | null>(null)

  async function logIn() {
    setProblem(null)
    try {
      await adminLogin(username, password)
      onDone()
    } catch (error) {
      setProblem(error instanceof ApiError ? error.message : "Couldn't log in.")
    }
  }

  return (
    <form className="narrow stack" onSubmit={e => { e.preventDefault(); void logIn() }}>
      <h2>Admin login</h2>
      <label>Username <input className="input" value={username} autoComplete="username" onChange={e => setUsername(e.target.value)} /></label>
      <label>Password <input className="input" type="password" value={password} autoComplete="current-password" onChange={e => setPassword(e.target.value)} /></label>
      <div><button type="submit" className="btn primary">Log in</button></div>
      {problem && <Problem error={problem} />}
    </form>
  )
}

function Overview({ overview, reload }: { overview: AdminOverview, reload: () => void }) {
  const [problem, setProblem] = useState<string | null>(null)
  const emailOf = (id: string) => overview.users.find(u => u.id === id)?.email
  const nameOf = (id: string) => overview.devices.find(d => d.id === id)?.name

  async function again(id: string) {
    setProblem(null)
    try {
      await rerunReport(id)
      reload()
    } catch (error) {
      setProblem(error instanceof ApiError ? error.message : "Couldn't run it again.")
    }
  }

  return (
    <div className="stack">
      {problem && <Problem error={problem} />}
      <section aria-labelledby="admin-people" className="stack">
        <h3 id="admin-people">People</h3>
        {overview.users.length === 0 && <p className="muted">Nobody has logged in yet.</p>}
        <ul className="list">
          {overview.users.map(u => <li key={u.id}><strong>{u.email}</strong> <span className="muted small">{u.name}</span></li>)}
        </ul>
      </section>
      <section aria-labelledby="admin-pis" className="stack">
        <h3 id="admin-pis">Pis</h3>
        {overview.devices.length === 0 && <p className="muted">No Pis linked yet.</p>}
        <ul className="list">
          {overview.devices.map(d => (
            <li key={d.id}>
              <strong>{d.name}</strong>
              {d.removed && <span className="tag">Removed</span>}
              <span className="muted small">
                {emailOf(d.owner_id) ?? 'unknown owner'}. {d.last_seen_at === null ? 'Not seen yet' : `Last seen ${formatTime(d.last_seen_at)}`}
              </span>
            </li>
          ))}
        </ul>
      </section>
      <section aria-labelledby="admin-failed" className="stack">
        <h3 id="admin-failed">Failed reports</h3>
        {overview.failed_reports.length === 0 && <p className="muted">None.</p>}
        <ul className="list">
          {overview.failed_reports.map(r => (
            <li key={r.id}>
              <span>{r.week_start}</span> <span className="muted small">{nameOf(r.device_id) ?? ''}</span>
              {r.error && <div className="small">{r.error}</div>}
              <button type="button" className="btn" onClick={() => void again(r.id)}>Run again</button>
            </li>
          ))}
        </ul>
      </section>
      <section aria-labelledby="admin-finished" className="stack">
        <h3 id="admin-finished">Latest finished reports</h3>
        {overview.recent_reports.length === 0 && <p className="muted">None.</p>}
        <ul className="list">
          {overview.recent_reports.map(r => (
            <li key={r.id}>
              <span>{r.week_start}</span> <span className="muted small">{nameOf(r.device_id) ?? ''}</span>
              <div className="small">{r.status === 'no_data' ? 'No data that week.' : 'Written.'}</div>
              <button type="button" className="btn" onClick={() => void again(r.id)}>Run again</button>
            </li>
          ))}
        </ul>
      </section>
    </div>
  )
}

export default function Admin() {
  const { data, error, loading, reload } = useLoad(getAdminOverview, 0)
  if (error instanceof ApiError && error.status === 401) return <AdminLogin onDone={reload} />
  if (error) return <Problem error={error} />
  if (!data) return loading ? <Loading /> : null
  return (
    <section className="stack">
      <h2>Admin</h2>
      <Overview overview={data} reload={reload} />
    </section>
  )
}
