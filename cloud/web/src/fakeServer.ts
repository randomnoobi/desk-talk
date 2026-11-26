// A fake server for the page tests: put it in place of `fetch`, say what each route answers, and look at
// the calls afterwards. A route nobody set up answers 404, so a page that calls something unexpected shows up.
// It also stands in for the Pis on the home network: key a route by its whole address, like
// 'GET http://192.168.86.225:5000/api/cloud/hello' (homeNetwork() makes those).
import { vi } from 'vitest'

export interface Call { method: string, path: string, body: unknown, init?: RequestInit }
// `text` is the body as it is, for an answer that isn't JSON; otherwise `body` is sent as JSON.
export interface Reply { status?: number, body?: unknown, text?: string }
// A function may answer later (a Promise), so a test can look at the page while a call waits. One that throws
// is a call that never got an answer.
type Handler = Reply | ((call: Call) => Reply | Promise<Reply>)

// Like a real fetch, a call that is stopped (its AbortSignal) while it waits fails at once.
function untilStopped<T>(answer: Promise<T>, signal?: AbortSignal | null): Promise<T> {
  if (!signal) return answer
  return new Promise((resolve, reject) => {
    const stop = () => reject(new DOMException('The call was stopped.', 'AbortError'))
    if (signal.aborted) return stop()
    signal.addEventListener('abort', stop, { once: true })
    answer.then(resolve, reject)
  })
}

/** `routes` is keyed like 'GET /api/me': the method, a space, and the path (a query string is left out). */
export function fakeServer(routes: Record<string, Handler>) {
  const calls: Call[] = []
  vi.stubGlobal('fetch', vi.fn(async (input: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET'
    const path = String(input).split('?')[0]
    const call: Call = { method, path: String(input), body: init?.body ? JSON.parse(String(init.body)) : undefined, init }
    calls.push(call)
    const handler = routes[`${method} ${input}`] ?? routes[`${method} ${path}`]
    if (!handler) return new Response(JSON.stringify({ detail: 'not in this test' }), { status: 404 })
    const reply = await untilStopped(Promise.resolve().then(() => typeof handler === 'function' ? handler(call) : handler), init?.signal)
    const status = reply.status ?? 200
    return new Response(status === 204 ? null : reply.text ?? JSON.stringify(reply.body ?? {}), { status })
  }))
  return {
    calls,
    /** The calls made with this method to this path (query strings left out). */
    made: (method: string, path: string) => calls.filter(c => c.method === method && c.path.split('?')[0] === path),
    /** The calls this page made to its own server (not to an address on the home network). */
    toServer: () => calls.filter(c => c.path.startsWith('/')),
  }
}

export interface HomePi { address: string, name: string, linked: boolean }

/** What the server says about where to look, as GET /api/pis/where_to_look answers. */
export const WHERE_TO_LOOK = {
  addresses: ['192.168.86.1', '192.168.86.9', '192.168.86.225', '192.168.86.240'],
  port: 5000, wait_ms: 1500, at_once: 64, name_max_chars: 60, reply_max_kb: 4,
}

/** The address the page knocks on to ask "who are you". */
export const helloAt = (address: string, port = WHERE_TO_LOOK.port) => `http://${address}:${port}/api/cloud/hello`

/** Routes for a home network where these Pis answer "who are you", and the server's answer of where to look. */
export function homeNetwork(pis: HomePi[], where: Partial<typeof WHERE_TO_LOOK> = {}): Record<string, Handler> {
  const look = { ...WHERE_TO_LOOK, ...where }
  const routes: Record<string, Handler> = { 'GET /api/pis/where_to_look': { body: look } }
  for (const pi of pis) {
    routes[`GET ${helloAt(pi.address, look.port)}`] = { body: { desk_talk: 'pi', name: pi.name, linked: pi.linked } }
  }
  return routes
}
