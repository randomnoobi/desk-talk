// The page a person sees when nobody is logged in.
import { useState, type MouseEvent } from 'react'
import { googleLoginProblem } from '../api'
import { Problem } from '../ui'

const GOOGLE_LOGIN = '/auth/google/login'

/** `go` is where the browser is sent (the tests replace it). */
export default function Login({ go = (address: string) => window.location.assign(address) }: { go?: (address: string) => void }) {
  const [problem, setProblem] = useState<string | null>(null)

  // Ask first, so that when Google login isn't set up the server's own message shows here, instead of
  // the person landing on a bare error page.
  async function logIn(event: MouseEvent) {
    event.preventDefault()
    setProblem(null)
    const found = await googleLoginProblem(GOOGLE_LOGIN)
    if (found === null) go(GOOGLE_LOGIN)
    else setProblem(found)
  }

  return (
    <main className="narrow stack">
      <h1>Desk Talk</h1>
      <p>Log in to link a Pi, see what it sees, and read its weekly report.</p>
      <p><a className="btn primary big" href={GOOGLE_LOGIN} onClick={e => void logIn(e)}>Log in with Google</a></p>
      {problem && <Problem error={problem} />}
      <p className="small muted"><a href="#/admin">Admin login</a></p>
    </main>
  )
}
