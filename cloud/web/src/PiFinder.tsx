// Finds Pis on the home network and links one with a click. The server does the looking and hands the Pi its
// one-time code itself, so the code never comes to this page.
import { useState } from 'react'
import { ApiError, findPis, linkPi, type FoundPi } from './api'
import { browserTimeZone } from './format'
import { Problem } from './ui'

function wordsOf(error: unknown, otherwise: string): string {
  return error instanceof ApiError ? error.message : otherwise
}

/** A button that looks for Pis on the home network, and a Link button for each one not linked yet.
 *  `onLinked` runs after a Pi links, so the page can show it in its list. */
export function PiFinder({ onLinked }: { onLinked: () => void }) {
  const [looking, setLooking] = useState(false)
  const [found, setFound] = useState<FoundPi[] | null>(null)
  const [problem, setProblem] = useState<string | null>(null)
  // The Pi being linked right now: only one at a time, so two clicks can't send two offers.
  const [linking, setLinking] = useState<string | null>(null)
  const [justLinked, setJustLinked] = useState<string[]>([])

  async function look() {
    setLooking(true)
    setProblem(null)
    setFound(null)
    setJustLinked([])
    try {
      setFound(await findPis())
    } catch (error) {
      setProblem(wordsOf(error, "Couldn't look on your home network."))
    } finally {
      setLooking(false)
    }
  }

  async function link(address: string) {
    if (linking !== null) return
    setLinking(address)
    setProblem(null)
    try {
      await linkPi(address, browserTimeZone())
      setJustLinked(before => [...before, address])
      onLinked()
    } catch (error) {
      setProblem(wordsOf(error, "Couldn't link that Pi."))
    } finally {
      setLinking(null)
    }
  }

  return (
    <div className="stack">
      <div>
        <button type="button" className="btn primary" disabled={looking || linking !== null} onClick={() => void look()}>
          Find Pis on my network
        </button>
      </div>
      {looking && <p className="muted">Looking on your home network…</p>}
      {found && found.length === 0 && (
        <p>
          No Pi answered. Is it switched on, and on the same Wi-Fi as this computer? You can still link it with the
          command below.
        </p>
      )}
      {found && found.length > 0 && (
        <ul className="list">
          {found.map(pi => (
            <li key={pi.address}>
              <strong>{pi.name || pi.address}</strong>
              <span className="muted small">{pi.address}</span>
              {pi.linked && <span className="tag">Already linked</span>}
              {!pi.linked && justLinked.includes(pi.address) && <span className="tag">Linked</span>}
              {!pi.linked && !justLinked.includes(pi.address) && (
                <button
                  type="button"
                  className="btn primary"
                  aria-label={`Link ${pi.name || pi.address}`}
                  disabled={linking !== null}
                  onClick={() => void link(pi.address)}
                >
                  {linking === pi.address ? 'Linking…' : 'Link'}
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
      {problem && <Problem error={problem} />}
    </div>
  )
}
