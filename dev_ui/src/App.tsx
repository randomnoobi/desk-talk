import { useEffect, useState } from 'react'
import { getDevSettings, NO_ANSWER, type DevSettings } from './api'
import Checks from './tabs/Checks'
import Database from './tabs/Database'
import Live from './tabs/Live'
import Storage from './tabs/Storage'
import System from './tabs/System'
import { usePoll } from './usePoll'

// How often to ask again while the first settings haven't arrived. Once they have, the page asks
// every settings.dev.poll_sec instead. This is the one number the page keeps itself, and it has to:
// it's only used while the server isn't answering, so there's no server to ask for it. It isn't read
// from config/settings.yaml when the page is built either, because the built page (dev_ui/dist) would
// then keep an old copy that goes wrong as soon as someone changes the setting without building again.
export const FIRST_TRY_AGAIN_MS = 2000

/** The tabs, in order. Link to one with its id: <a href="#database">. */
export const TABS = [
  { id: 'checks', name: 'Checks', Tab: Checks },
  { id: 'live', name: 'Live', Tab: Live },
  { id: 'system', name: 'System', Tab: System },
  { id: 'database', name: 'Database', Tab: Database },
  { id: 'storage', name: 'Storage', Tab: Storage },
] as const

type TabId = (typeof TABS)[number]['id']

function tabFromHash(): TabId {
  const asked = window.location.hash.slice(1)
  return TABS.find(t => t.id === asked)?.id ?? TABS[0].id
}

/** The open tab, kept in the address (#live), so a reload or a link opens the same one. */
function useOpenTab(): [TabId, (id: TabId) => void] {
  const [tab, setTab] = useState<TabId>(tabFromHash)
  useEffect(() => {
    const follow = () => setTab(tabFromHash())
    window.addEventListener('hashchange', follow)
    return () => window.removeEventListener('hashchange', follow)
  }, [])
  const open = (id: TabId) => {
    setTab(id)
    if (window.location.hash !== `#${id}`) window.location.hash = id
  }
  return [tab, open]
}

export default function App(): JSX.Element {
  // The settings come from the first reply. The later ones only show whether the server is still there.
  const [settings, setSettings] = useState<DevSettings | null>(null)
  const server = usePoll(getDevSettings, settings ? settings.poll_sec * 1000 : FIRST_TRY_AGAIN_MS)
  useEffect(() => {
    if (settings === null && server.data !== null) setSettings(server.data)
  }, [settings, server.data])

  const [tab, openTab] = useOpenTab()
  // A tab is only started once you open it, then kept, so a run's printout or a query survives a tab switch.
  const [opened, setOpened] = useState<ReadonlySet<TabId>>(() => new Set([tab]))
  useEffect(() => {
    setOpened(old => (old.has(tab) ? old : new Set([...old, tab])))
  }, [tab])

  return (
    <div className="dev-app">
      <header className="top">
        <div className="brand">
          <strong>Desk Talk</strong>
          <span className="muted">developer page</span>
        </div>
        {settings && (
          <nav className="tabs" role="tablist" aria-label="Developer page">
            {TABS.map(t => (
              <button key={t.id} id={`tab-${t.id}`} role="tab" type="button" className="tab"
                      aria-selected={t.id === tab} aria-controls={`panel-${t.id}`} onClick={() => openTab(t.id)}>
                {t.name}
              </button>
            ))}
          </nav>
        )}
      </header>

      {server.error && (
        <div className="offline-bar" role="status">
          Can't reach Desk Talk, trying again.
          {server.error !== NO_ANSWER && <span className="detail"> ({server.error})</span>}
        </div>
      )}

      <main className="page">
        {settings
          ? TABS.filter(t => opened.has(t.id)).map(({ id, name, Tab }) => (
              <section key={id} id={`panel-${id}`} role="tabpanel" aria-label={name} hidden={id !== tab}>
                <Tab settings={settings} />
              </section>
            ))
          : !server.error && <p className="muted">Asking Desk Talk for its settings…</p>}
        {!settings && server.error && (
          <div className="card">
            <h2>Waiting for Desk Talk</h2>
            <p>Start the server on this computer (<code>uv run python server.py</code>) and this page connects by itself.</p>
            <p className="muted">This page only answers on this computer, at http://localhost and the port in config/settings.yaml.</p>
          </div>
        )}
      </main>
    </div>
  )
}
