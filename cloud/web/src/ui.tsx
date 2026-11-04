// Small pieces every page uses.
import { useState, type ReactNode } from 'react'
import { ApiError, makeCode, type CodeReply } from './api'
import { browserTimeZone, formatTime } from './format'

export function Problem({ error }: { error: Error | string }) {
  return <p className="error" role="alert">{typeof error === 'string' ? error : error.message}</p>
}

export function Loading() {
  return <p className="muted">Loading…</p>
}

/** Asks before doing something that can't be taken back. Cancel is the first thing under the question. */
export function Confirm(props: { question: ReactNode, doIt: string, danger?: boolean, onYes: () => void, onNo: () => void }) {
  return (
    <div className="confirm" role="alertdialog" aria-label="Are you sure?">
      <p>{props.question}</p>
      <div className="row">
        <button type="button" className="btn" onClick={props.onNo}>Cancel</button>
        <button type="button" className={props.danger ? 'btn danger' : 'btn primary'} onClick={props.onYes}>{props.doIt}</button>
      </div>
    </div>
  )
}

// Addresses that only mean "this computer": a Pi can't use them.
function isThisComputer(address: string): boolean {
  try {
    const host = new URL(address).hostname.replace(/^\[|\]$/g, '')
    return host === 'localhost' || host.endsWith('.localhost') || host === '127.0.0.1' || host === '::1'
  } catch {
    return false
  }
}

/** The one-time code and the command to run. The code is only shown here, never put in an address. */
export function CodeBox({ reply }: { reply: CodeReply }) {
  return (
    <div className="card">
      <p>Your one-time code:</p>
      <p className="bigcode"><code>{reply.code}</code></p>
      {reply.expires_at !== null && <p className="muted small">It works once, until {formatTime(reply.expires_at)}.</p>}
      <p>Run this on the Pi. It installs Desk Talk if needed, and links it:</p>
      <pre className="printout">{reply.command}</pre>
      {isThisComputer(reply.server_url) && (
        <p className="warning" role="alert">
          The server's address is {reply.server_url}. That works for Desk Talk running on this computer, but a Pi can't
          reach it. To link a Pi, set PUBLIC_URL in cloud/.env to this computer's address on your Wi-Fi, restart
          the server, and make a new code.
        </p>
      )}
    </div>
  )
}

/** A button that makes a one-time code and shows it. With `deviceId` the code links that same Pi again. */
export function CodeMaker({ label, deviceId }: { label: string, deviceId?: string }) {
  const [reply, setReply] = useState<CodeReply | null>(null)
  const [problem, setProblem] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function make() {
    setBusy(true)
    setProblem(null)
    try {
      setReply(await makeCode(browserTimeZone(), deviceId))
    } catch (error) {
      setReply(null)
      setProblem(error instanceof ApiError ? error.message : "Couldn't make a code.")
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="stack">
      <div><button type="button" className="btn primary" disabled={busy} onClick={() => void make()}>{label}</button></div>
      {problem && <Problem error={problem} />}
      {reply && <CodeBox reply={reply} />}
    </div>
  )
}
