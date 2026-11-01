// The website: a login page, or the person's pages under a small menu. Pages are chosen by the address's
// part after '#', so the server only ever has to hand out this one file.
import { useEffect, useState } from 'react'
import { ApiError, deleteMe, getMe, listAllReports, logout } from './api'
import Admin from './pages/Admin'
import Login from './pages/Login'
import OnePi from './pages/OnePi'
import OneReport from './pages/OneReport'
import Pis from './pages/Pis'
import Reports, { isNew } from './pages/Reports'
import { Confirm, Loading, Problem } from './ui'
import { useLoad } from './useLoad'

type Route =
  | { page: 'pis' }
  | { page: 'pi', id: string }
  | { page: 'reports' }
  | { page: 'report', id: string }
  | { page: 'admin' }

function routeOf(hash: string): Route {
  const [, first, second] = hash.replace(/^#/, '').split('/')
  const id = second ? decodeURIComponent(second) : ''
  if (first === 'pis' && id) return { page: 'pi', id }
  if (first === 'reports') return id ? { page: 'report', id } : { page: 'reports' }
  if (first === 'admin') return { page: 'admin' }
  return { page: 'pis' }
}

function useHash(): string {
  const [hash, setHash] = useState(window.location.hash)
  useEffect(() => {
    const onChange = () => setHash(window.location.hash)
    window.addEventListener('hashchange', onChange)
    return () => window.removeEventListener('hashchange', onChange)
  }, [])
  return hash
}

function PersonPage({ route }: { route: Route }) {
  switch (route.page) {
    case 'pi': return <OnePi id={route.id} />
    case 'reports': return <Reports />
    case 'report': return <OneReport id={route.id} />
    default: return <Pis />
  }
}

/** True while a finished report waits that this browser hasn't opened. Asked again each time the page changes. */
function useNewReport(hash: string): boolean {
  const [waiting, setWaiting] = useState(false)
  useEffect(() => {
    let current = true
    listAllReports().then(
      rows => { if (current) setWaiting(rows.some(row => isNew(row.report))) },
      () => { if (current) setWaiting(false) },
    )
    return () => { current = false }
  }, [hash])
  return waiting
}

function Menu({ name, hash, onGone }: { name: string, hash: string, onGone: () => void }) {
  const waiting = useNewReport(hash)
  const [asking, setAsking] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)

  async function run(action: () => Promise<void>) {
    setProblem(null)
    setAsking(false)
    try {
      await action()
      onGone()
    } catch (error) {
      setProblem(error instanceof ApiError ? error.message : "That didn't work.")
    }
  }

  return (
    <>
      <nav className="menu" aria-label="Main">
        <a href="#/">Pis</a>
        <a href="#/reports">Reports{waiting && <span className="mark">new</span>}</a>
        <span className="grow" />
        <span className="muted small">{name}</span>
        <button type="button" className="btn" onClick={() => void run(logout)}>Log out</button>
      </nav>
      {problem && <Problem error={problem} />}
      {asking && (
        <Confirm
          question="Delete your account and everything stored for it: your Pis, their events and your reports? This can't be undone."
          doIt="Delete" danger onNo={() => setAsking(false)} onYes={() => void run(deleteMe)} />
      )}
      <footer className="small muted">
        <button type="button" className="link" onClick={() => setAsking(true)}>Delete my account</button>
        {' '}<a href="#/admin">Admin</a>
      </footer>
    </>
  )
}

function Person({ hash }: { hash: string }) {
  const { data, error, loading } = useLoad(getMe, 0)
  const [gone, setGone] = useState(false)

  if (gone || (error instanceof ApiError && error.status === 401)) return <Login />
  if (error) return <main className="wide"><Problem error={error} /></main>
  if (!data) return loading ? <main className="wide"><Loading /></main> : null

  return (
    <main className="wide stack">
      <h1>Desk Talk</h1>
      <PersonPage route={routeOf(hash)} />
      <Menu name={data.name || data.email} hash={hash} onGone={() => setGone(true)} />
    </main>
  )
}

export default function App() {
  const hash = useHash()
  if (routeOf(hash).page === 'admin') {
    return (
      <main className="wide stack">
        <h1>Desk Talk</h1>
        <p><a href="#/">Back to the site</a></p>
        <Admin />
      </main>
    )
  }
  return <Person hash={hash} />
}
