// A fake server for the page tests: put it in place of `fetch`, say what each route answers, and look at
// the calls afterwards. A route nobody set up answers 404, so a page that calls something unexpected shows up.
import { vi } from 'vitest'

export interface Call { method: string, path: string, body: unknown }
export interface Reply { status?: number, body?: unknown }
type Handler = Reply | ((call: Call) => Reply)

/** `routes` is keyed like 'GET /api/me': the method, a space, and the path (a query string is left out). */
export function fakeServer(routes: Record<string, Handler>) {
  const calls: Call[] = []
  vi.stubGlobal('fetch', vi.fn(async (input: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET'
    const path = String(input).split('?')[0]
    const call: Call = { method, path: String(input), body: init?.body ? JSON.parse(String(init.body)) : undefined }
    calls.push(call)
    const handler = routes[`${method} ${input}`] ?? routes[`${method} ${path}`]
    if (!handler) return new Response(JSON.stringify({ detail: 'not in this test' }), { status: 404 })
    const reply = typeof handler === 'function' ? handler(call) : handler
    const status = reply.status ?? 200
    return new Response(status === 204 ? null : JSON.stringify(reply.body ?? {}), { status })
  }))
  return {
    calls,
    /** The calls made with this method to this path (query strings left out). */
    made: (method: string, path: string) => calls.filter(c => c.method === method && c.path.split('?')[0] === path),
  }
}
