// Every call the website makes to the server, and the shape of each reply. Nothing else in the page fetches,
// so if a route's reply looks different from what is written here, this is the one file to fix. The one call
// that isn't to the server is the knock on each address of the home network, to find Pis (findPis).
//
// Routes used (the Pi's /v1 routes also answer to the person's login cookie):
//   GET  /auth/google/login                     POST /auth/logout        POST /auth/admin/login
//   GET  /api/me                                DELETE /api/me
//   GET  /api/devices                           POST /api/devices/code   PATCH|DELETE /api/devices/{id}
//   DELETE /api/devices/{id}/data
//   GET  /api/pis/where_to_look                 POST /api/pis/link
//   GET  http://<each address>:<port>/api/cloud/hello  (each Pi's "who are you", from the browser)
//   GET  /v1/devices/{id}/state                 GET  /v1/devices/{id}/events?since=&after_id=
//   GET  /api/reports                           GET  /api/reports/{id}
//   GET  /api/admin/overview                    POST /api/admin/reports/{id}/rerun

/** A call that didn't work. `status` is the HTTP status, or 0 when the server couldn't be reached. */
export class ApiError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

/** The words for a call that never got an answer (status 0). */
export const NO_ANSWER = "Can't reach the server."

type Fields = Record<string, unknown>

// The server says what went wrong as {"detail": "..."} (FastAPI).
function errorWords(body: unknown): string | null {
  const { detail } = fields(body)
  return typeof detail === 'string' && detail ? detail : null
}

async function readBody(response: Response): Promise<unknown> {
  const text = await response.text()
  try {
    return text ? JSON.parse(text) : null
  } catch {
    return null
  }
}

async function send(path: string, init?: RequestInit): Promise<unknown> {
  let response: Response
  try {
    response = await fetch(path, init)
  } catch {
    throw new ApiError(0, NO_ANSWER)
  }
  const body = await readBody(response)
  if (!response.ok) {
    throw new ApiError(response.status, errorWords(body) ?? `${response.status} ${response.statusText}`.trim())
  }
  return body
}

const get = (path: string) => send(path)
const post = (path: string, body?: unknown) =>
  send(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body ?? {}) })
const patch = (path: string, body: unknown) =>
  send(path, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
const del = (path: string) => send(path, { method: 'DELETE' })

function fields(value: unknown): Fields {
  return typeof value === 'object' && value !== null && !Array.isArray(value) ? value as Fields : {}
}

function listOf(list: unknown): Fields[] {
  return Array.isArray(list) ? list.map(fields) : []
}

const text = (value: unknown, otherwise = '') => typeof value === 'string' ? value : otherwise
const num = (value: unknown): number | null => typeof value === 'number' && Number.isFinite(value) ? value : null

// ---------------------------------------------------------------- logging in

/** Asks the server whether Google login is ready. Null means yes (it answered with a redirect to Google);
 *  otherwise the words to show: the server's own setup message, or that it couldn't be reached. */
export async function googleLoginProblem(path: string): Promise<string | null> {
  let response: Response
  try {
    response = await fetch(path, { redirect: 'manual' })
  } catch {
    return NO_ANSWER
  }
  if (response.type === 'opaqueredirect' || response.ok || (response.status >= 300 && response.status < 400)) return null
  return errorWords(await readBody(response)) ?? `${response.status} ${response.statusText}`.trim()
}

export interface Me { email: string, name: string }

export async function getMe(): Promise<Me> {
  const body = fields(await get('/api/me'))
  return { email: text(body.email), name: text(body.name) }
}

export async function logout(): Promise<void> {
  await post('/auth/logout')
}

export async function deleteMe(): Promise<void> {
  await del('/api/me')
}

// ---------------------------------------------------------------- Pis

export interface Device {
  id: string
  name: string
  time_zone: string
  last_seen_at: number | null
  removed: boolean
}

function toDevice(raw: Fields): Device {
  return {
    id: text(raw.id),
    name: text(raw.name),
    time_zone: text(raw.time_zone),
    last_seen_at: num(raw.last_seen_at),
    removed: raw.removed === true,
  }
}

export async function listDevices(): Promise<Device[]> {
  return listOf(await get('/api/devices')).map(toDevice)
}

export interface CodeReply {
  code: string
  expires_at: number | null
  server_url: string
  command: string
}

/** Makes a one-time code. With `deviceId` it is a code to link that same (kept) Pi again. */
export async function makeCode(timeZone: string, deviceId?: string): Promise<CodeReply> {
  const body = fields(await post('/api/devices/code', deviceId ? { time_zone: timeZone, device_id: deviceId } : { time_zone: timeZone }))
  return { code: text(body.code), expires_at: num(body.expires_at), server_url: text(body.server_url), command: text(body.command) }
}

// ---------------------------------------------------------------- finding Pis on the home network
// The browser does the looking, not the server: knocking on a whole home network from the server's container
// stalls every other connection it makes (on a Mac, Docker's relay keeps each empty knock open for a minute).

/** A Pi that answered on the home network: its address there, the name it gives itself, and whether it is
 *  already linked to a cloud server. Only this page knows them; the server is told just the address to link. */
export interface FoundPi { address: string, name: string, linked: boolean }

/** How to knock, from GET /api/pis/where_to_look, so the page keeps no copy of these settings. */
interface WhereToLook { addresses: string[], port: number, waitMs: number, atOnce: number, nameMaxChars: number, replyMaxKb: number }

const FOUR_NUMBERS = /^\d{1,3}(\.\d{1,3}){3}$/
// What a one-line name may not hold: control and format characters, half letters, line breaks, and any space
// but a plain one.
const NOT_PLAIN = /[\p{C}\p{Zl}\p{Zp}]|(?! )\p{Zs}/u

const whole = (value: unknown, otherwise: number) =>
  typeof value === 'number' && Number.isInteger(value) && value > 0 ? value : otherwise

async function whereToLook(): Promise<WhereToLook> {
  const body = fields(await get('/api/pis/where_to_look'))
  const addresses = Array.isArray(body.addresses) ? body.addresses : []
  return {
    addresses: addresses.filter((a): a is string => typeof a === 'string' && FOUR_NUMBERS.test(a)),
    port: whole(body.port, 0),
    waitMs: whole(body.wait_ms, 0),
    atOnce: whole(body.at_once, 1),
    nameMaxChars: whole(body.name_max_chars, 0),
    replyMaxKb: whole(body.reply_max_kb, 0),
  }
}

const addressNumber = (address: string) => address.split('.').reduce((sum, part) => sum * 256 + Number(part), 0)

/** The whole reply as text, or null when it passes `limit` bytes or isn't UTF-8. */
async function readUpTo(response: Response, limit: number): Promise<string | null> {
  const reader = response.body?.getReader()
  if (!reader) return ''
  const pieces: Uint8Array[] = []
  let size = 0
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    size += value.byteLength
    if (size > limit) {
      void reader.cancel().catch(() => {})
      return null
    }
    pieces.push(value)
  }
  const all = new Uint8Array(size)
  let at = 0
  for (const piece of pieces) {
    all.set(piece, at)
    at += piece.byteLength
  }
  try {
    return new TextDecoder('utf-8', { fatal: true }).decode(all)
  } catch {
    return null
  }
}

function isPlainLine(value: unknown): value is string {
  return typeof value === 'string' && value.trim() !== '' && !NOT_PLAIN.test(value)
}

/** The Pi in a "who are you" reply, or null for anything that isn't one. */
function asFoundPi(address: string, reply: unknown, nameMaxChars: number): FoundPi | null {
  if (typeof reply !== 'object' || reply === null || Array.isArray(reply)) return null
  const { desk_talk: kind, name, linked } = reply as Fields
  if (kind !== 'pi' || !isPlainLine(name) || typeof linked !== 'boolean') return null
  return { address, name: Array.from(name.trim()).slice(0, nameMaxChars).join('').trim(), linked }
}

/** Asks one address "who are you". Most addresses have nobody there, or something that isn't a Pi: null. */
async function knock(address: string, where: WhereToLook): Promise<FoundPi | null> {
  const stop = new AbortController()
  const timer = setTimeout(() => stop.abort(), where.waitMs)
  try {
    // No cookies go, a redirect counts as no answer, and only a reply with the Pi's CORS header can be read.
    const response = await fetch(`http://${address}:${where.port}/api/cloud/hello`,
      { mode: 'cors', credentials: 'omit', redirect: 'error', cache: 'no-store', signal: stop.signal })
    if (response.status !== 200) return null
    const text = await readUpTo(response, where.replyMaxKb * 1024)
    return text === null ? null : asFoundPi(address, JSON.parse(text), where.nameMaxChars)
  } catch {
    return null
  } finally {
    clearTimeout(timer)
  }
}

/** Looks for Pis on the home network around the server's own address: the server says where, and this browser
 *  knocks on each address, `at_once` at a time. Takes a few seconds. */
export async function findPis(): Promise<FoundPi[]> {
  const where = await whereToLook()
  const answers: (FoundPi | null)[] = []
  let next = 0
  async function knockOnTheNext() {
    while (next < where.addresses.length) {
      const index = next++
      answers[index] = await knock(where.addresses[index], where)
    }
  }
  await Promise.all(Array.from({ length: Math.min(where.atOnce, where.addresses.length) }, knockOnTheNext))
  return answers.filter((pi): pi is FoundPi => pi !== null)
    .sort((a, b) => addressNumber(a.address) - addressNumber(b.address))
}

/** Links the Pi at `address`: the server makes a one-time code and hands it to the Pi itself, so the code
 *  never comes to the page. */
export async function linkPi(address: string, timeZone: string): Promise<void> {
  await post('/api/pis/link', { address, time_zone: timeZone })
}

export async function renameDevice(id: string, name: string): Promise<void> {
  await patch(`/api/devices/${encodeURIComponent(id)}`, { name })
}

/** Removes the Pi: its token stops working, its history stays. */
export async function removeDevice(id: string): Promise<void> {
  await del(`/api/devices/${encodeURIComponent(id)}`)
}

/** Deletes the Pi and everything stored for it. */
export async function deleteDeviceWithData(id: string): Promise<void> {
  await del(`/api/devices/${encodeURIComponent(id)}/data`)
}

// ---------------------------------------------------------------- one Pi

export interface ObjectState { label: string, state: string, since: number | null }

export interface DeviceState {
  recent_hours: number | null
  device: { id: string, name: string, time_zone: string, removed: boolean }
  last_seen_at: number | null
  goals: Fields | null
  last_camera: Fields | null
  objects: ObjectState[]
}

export async function getState(deviceId: string): Promise<DeviceState> {
  const body = fields(await get(`/v1/devices/${encodeURIComponent(deviceId)}/state`))
  const device = fields(body.device)
  return {
    recent_hours: num(body.recent_hours),
    device: { id: text(device.id, deviceId), name: text(device.name), time_zone: text(device.time_zone), removed: device.removed === true },
    last_seen_at: num(body.last_seen_at),
    goals: body.goals ? fields(body.goals) : null,
    last_camera: body.last_camera ? fields(body.last_camera) : null,
    objects: listOf(body.objects).map(o => ({ label: text(o.label), state: text(o.state), since: num(o.since) })),
  }
}

export interface CloudEvent { id: string, ts: number, type: string, details: Fields }

interface EventsPage { events: CloudEvent[], next_since: number, next_after_id: string | null }

async function getEventsPage(deviceId: string, since: number, afterId: string | null): Promise<EventsPage> {
  const query = new URLSearchParams({ since: String(since) })
  if (afterId !== null) query.set('after_id', afterId)
  const body = fields(await get(`/v1/devices/${encodeURIComponent(deviceId)}/events?${query}`))
  const events = listOf(body.events).map(raw => {
    const { id, ts, type, ...details } = raw
    return { id: text(id), ts: num(ts) ?? 0, type: text(type), details }
  })
  return { events, next_since: num(body.next_since) ?? since, next_after_id: typeof body.next_after_id === 'string' ? body.next_after_id : null }
}

/** Every event after `since` (seconds since 1970), newest first. The server gives them oldest first, a page
 *  at a time, with the place to carry on from; this keeps asking until a page comes back empty or no
 *  further on than the last one. */
export async function recentEvents(deviceId: string, since: number): Promise<CloudEvent[]> {
  const found = new Map<string, CloudEvent>()
  let from = since
  let after: string | null = null
  for (;;) {
    const page = await getEventsPage(deviceId, from, after)
    for (const event of page.events) found.set(event.id, event)
    const samePlace = page.next_since === from && page.next_after_id === after
    if (page.events.length === 0 || samePlace) break
    from = page.next_since
    after = page.next_after_id
  }
  return [...found.values()].sort((a, b) => b.ts - a.ts)
}

// ---------------------------------------------------------------- reports

export interface Report {
  id: string
  device_id: string
  week_start: string
  status: string
  markdown: string | null
  error: string | null
}

function toReport(raw: Fields): Report {
  return {
    id: text(raw.id),
    device_id: text(raw.device_id),
    week_start: text(raw.week_start),
    status: text(raw.status),
    markdown: typeof raw.markdown === 'string' ? raw.markdown : null,
    error: typeof raw.error === 'string' && raw.error ? raw.error : null,
  }
}

export async function getReport(id: string): Promise<Report> {
  return toReport(fields(await get(`/api/reports/${encodeURIComponent(id)}`)))
}

export interface ReportRow { report: Report, deviceName: string }

/** The reports of every Pi the person has, newest week first (the server's order). */
export async function listAllReports(): Promise<ReportRow[]> {
  const body = await get('/api/reports')
  return listOf(body).map(raw => ({ report: toReport(raw), deviceName: text(raw.device_name) }))
}

// ---------------------------------------------------------------- admin

export interface AdminUser { id: string, email: string, name: string }
export interface AdminDevice { id: string, name: string, owner_id: string, last_seen_at: number | null, removed: boolean }
export interface AdminFailure { id: string, device_id: string, week_start: string, error: string | null }
export interface AdminFinished { id: string, device_id: string, week_start: string, status: string }
export interface AdminOverview { users: AdminUser[], devices: AdminDevice[], failed_reports: AdminFailure[], recent_reports: AdminFinished[] }

export async function getAdminOverview(): Promise<AdminOverview> {
  const body = fields(await get('/api/admin/overview'))
  return {
    users: listOf(body.users).map(u => ({ id: text(u.id), email: text(u.email), name: text(u.name) })),
    devices: listOf(body.devices).map(d => ({ ...toDevice(d), owner_id: text(d.user_id) })),
    failed_reports: listOf(body.failed_reports).map(r => ({
      id: text(r.id), device_id: text(r.device_id), week_start: text(r.week_start), error: typeof r.error === 'string' && r.error ? r.error : null,
    })),
    recent_reports: listOf(body.recent_reports).map(r => ({
      id: text(r.id), device_id: text(r.device_id), week_start: text(r.week_start), status: text(r.status),
    })),
  }
}

export async function adminLogin(username: string, password: string): Promise<void> {
  await post('/auth/admin/login', { username, password })
}

export async function rerunReport(id: string): Promise<void> {
  await post(`/api/admin/reports/${encodeURIComponent(id)}/rerun`)
}
