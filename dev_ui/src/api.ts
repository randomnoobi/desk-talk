// Every call the developer page makes to the Desk Talk server, and the shape of each reply.
// The /api/dev routes are in desktalk/web/routers/dev.py; /api/health and /api/log/status already
// existed for the app page and are used as they are.

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
export const NO_ANSWER = 'no answer from the Desk Talk server'

async function send<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await fetch(path, init)
  } catch {
    throw new ApiError(0, NO_ANSWER)
  }
  const text = await response.text()
  let body: unknown = null
  try {
    body = text ? JSON.parse(text) : null
  } catch {
    body = null
  }
  if (!response.ok) {
    throw new ApiError(response.status, errorWords(body) ?? `${response.status} ${response.statusText}`.trim())
  }
  if (body === null && text) throw new ApiError(response.status, `${path} didn't send JSON`)
  return body as T
}

// The server says what went wrong as {"error": "..."}; FastAPI's own replies use {"detail": "..."}.
function errorWords(body: unknown): string | null {
  if (typeof body !== 'object' || body === null) return null
  const { error, detail } = body as { error?: unknown, detail?: unknown }
  if (typeof error === 'string' && error) return error
  if (typeof detail === 'string' && detail) return detail
  return null
}

export function getJSON<T>(path: string): Promise<T> {
  return send<T>(path)
}

export function postJSON<T>(path: string, body: unknown): Promise<T> {
  return send<T>(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
}

export function deleteJSON<T>(path: string): Promise<T> {
  return send<T>(path, { method: 'DELETE' })
}

// ── the replies ──

/** settings → dev.health: when the System tab turns a number amber or red. */
export interface HealthLimits {
  pi_cpu_warn_c: number
  pi_cpu_bad_c: number
  pi_disk_warn_mb: number
  detect_warn_ms: number
}

/** settings → object_states: the numbers the smoothing chart draws its lines at. appear_sec and gone_sec are the
 *  waits in use now: the sensitivity slider's step stretches them (a new thing waits a little longer to appear). */
export interface SmoothingSettings {
  on_confidence: number
  off_confidence: number
  appear_sec: number
  gone_sec: number
  window_pictures: number
  vote_share: number
}

/** GET /api/dev/settings: every number the page uses. The page keeps no copies of its own. */
export interface DevSettings {
  poll_sec: number
  checks: string[]                 // the names of the "Run everything" steps, in order
  health: HealthLimits
  trace_pictures: number
  sql_max_rows: number
  data_folder: string
  disconnect_after_sec: number     // settings → camera.disconnect_after_sec: no picture for this long = camera is off
  web_port: number                 // settings → web.port: the Desk Talk server's port (this page is at /dev/ on it)
  flicker_flips_per_min: number    // settings → dev.flicker_flips_per_min: more flips than this in a minute = "flickering"
  output_keep_kb: number           // settings → dev.output_keep_kb: how much of a run's printout the Checks tab keeps
  flips_window_min: number         // settings → dev.flips_window_min: how far back the ready-made "flips" query looks
  table_counts_every_sec: number   // settings → dev.table_counts_every_sec: the Database and Storage tabs count again this often
  trace_events_kept: number        // settings → object_story.events_kept: an object's trace keeps this many changes and chat joins
  trace_photos_kept: number        // settings → object_story.timed_photos_kept: this many timed pictures...
  trace_step_photos_kept: number   // settings → object_story.step_photos_kept: ...and this many step pictures
  object_states: SmoothingSettings
  scenarios: DemoLimits
}

/** settings → dev.scenarios: the Demo tab's countdowns. */
export interface DemoLimits {
  countdown_default_sec: number    // the countdown the Demo tab starts with
  countdown_max_sec: number        // the longest one the server takes
  waiting_max: number              // how many can count down at once
}

/** A pretend warning the Demo tab can fire (settings → dev.scenarios.items). */
export interface Scenario {
  name: string
  label: string
  fires: string                    // edge_warning, danger_zone, drink_reminder, camera_covered, sip, lost_object
}

/** A pretend warning counting down. */
export interface WaitingScenario {
  id: string
  name: string
  label: string
  fires_at: number                 // seconds since 1970
  seconds_left: number             // when the server answered
}

/** GET /api/dev/scenarios */
export interface Scenarios {
  scenarios: Scenario[]
  waiting: WaitingScenario[]       // the soonest first
}

export type Phase = 'PRESENT' | 'ABSENT' | 'UNKNOWN'
export type Change = 'appeared' | 'removed' | 'moved'

export interface ObjectNow {
  state: Phase
  since: number | null             // seconds since 1970; null = not known
}

export interface StateChange {
  ts: number
  label: string
  change: Change
}

/** GET /api/dev/states?since= */
export interface States {
  objects: Record<string, ObjectNow>
  watched_since: number | null     // when the camera's current run of pictures began
  changes: StateChange[]           // oldest first
}

/** One picture of one object's smoothing (GET /api/dev/smoothing/trace). */
export interface SmoothingStep {
  ts: number
  conf: number | null              // the picked box's confidence; null = no box
  box_seen: boolean                // the on/off confidence answer
  votes_yes: number
  votes_total: number
  seen: boolean                    // the vote's answer
  center: [number, number] | null
  phase: Phase
  heading: Phase | null            // where it's going while it waits
  waiting_sec: number | null
}

/** One thing that happened to an object (GET /api/dev/objects/{label}): a state change ('state'), a chat join,
 *  leave or refusal ('chat'), or a picture that goes with no other step ('photo'), with why and the picture taken
 *  for it. A value the server doesn't know is null. */
export interface TraceRow {
  ts: number
  source: 'state' | 'chat' | 'photo'
  kind: string                     // state: appeared, removed, moved; chat: joined, left, refused; photo: its why
  confidence: number | null        // how sure the detector was (for a state change: the average while seen)
  seen_for_sec: number | null      // how long it had been seen
  on_desk: boolean | null          // inside the marked desk (a chat row's null: no desk corners marked)
  step: string | null              // the sensitivity step in use (chat rows)
  before: Phase | null             // the state before and after (state rows)
  after: Phase | null
  photo: TracePhoto | null         // the picture taken for this step (null: none was)
  what: string                     // the server's plain words for it (config/prompts.yaml → object_story)
}

/** A picture kept of an object, with its box in the picture's own pixels. 'removed' is the picture where it was
 *  gone, with its last box: the empty spot. */
export interface TracePhoto {
  id: string
  ts: number
  why: 'appeared' | 'joined' | 'moved' | 'removed' | 'every'
  confidence: number
  box: [number, number, number, number]   // left, top, right, bottom
  size: [number, number]                  // the picture's width, height
}

/** GET /api/dev/objects/{label}: everything that happened to one object, oldest first, and its pictures. */
export interface ObjectTraceReply {
  label: string
  timeline: TraceRow[]
  photos: TracePhoto[]
}

/** One test pytest found. `id` is pytest's node id, e.g. tests/unit/test_x.py::test_y. */
export interface TestItem {
  id: string
  file: string
  markers: string[]                // e.g. ['slow']
}

export type Outcome = 'passed' | 'failed' | 'skipped' | 'error'
export type RunState = 'running' | 'passed' | 'failed' | 'stopped' | 'timed out'

export interface TestOutcome {
  id: string
  outcome: Outcome
  duration_sec: number
  message: string                  // the short failure text, or ''
}

/** One step of "Run everything" (settings → dev.checks). */
export interface StepStatus {
  name: string
  state: string
  exit_code: number | null
}

/** GET /api/dev/runs/{run_id}?from= */
export interface RunStatus {
  run_id: string
  what: string
  state: RunState
  started: number
  finished: number | null
  steps: StepStatus[]
  outcomes: TestOutcome[]
  output: string                   // the printout after the offset asked for
  output_end: number               // ask from here next time
}

/** One table in a database (GET /api/dev/db/{name}/tables). */
export interface TableInfo {
  name: string
  columns: string[]
  rows: number
  sql: string                      // its CREATE statement
}

/** POST /api/dev/db/{name}/query */
export interface QueryResult {
  columns: string[]
  rows: unknown[][]
  truncated: boolean               // there were more than settings.sql_max_rows rows
  took_ms: number
}

/** One file or folder in the data folder (GET /api/dev/files). */
export interface DataFile {
  path: string                     // relative to the data folder
  bytes: number
  modified: number                 // seconds since 1970
  is_folder: boolean
}

// GET /api/health (desktalk/web/routers/health.py)
export interface JobStatus {
  name: string
  state: 'running' | 'waiting after error' | 'stopped'
  failures_in_row: number
  total_failures: number
  last_error: string | null
  last_ok_at: number | null
}

export interface TimedJob {
  name: string
  every_sec: number
  runs: number
  last_run_at: number | null
  last_error: string | null
  next_run_at: number
  next_in_sec: number
}

export interface ConsumerStats {
  name: string
  handled: number
  waiting: number
  dropped: number
  errors: number
}

/** What the Pi has said about itself. Each value is only there once the Pi has sent it. */
export interface PiInfo {
  last_frame_id: string
  camera?: string                  // picamera2, usb, folder, video
  photos_stored?: number
  disk_free_mb?: number
  last_cleanup_ts?: number
  cpu_temp_c?: number
  throttled?: string               // the Pi's slow-down flags: '0x0' is fine; '' = the Pi could not read them
}

export interface PiStatus extends PiInfo {
  upload_format: number | null
  detector: 'ok' | 'busy' | 'off' | null
  detector_reason: string
  detect_ms: number | null
  model: string
  clock_off_sec: number | null     // the Pi's clock minus this computer's; negative = behind
}

export interface AiStatus {
  ready: boolean
  label: string
  problem: string | null
}

export interface Health {
  uptime_sec: number
  jobs: JobStatus[]
  scheduler: TimedJob[]
  events: ConsumerStats[]
  camera: { source: string, connected: boolean, fps: number, pictures_received: number | null }
  pi: PiStatus | null              // null when the camera isn't the Pi
  detection: { pictures_analysed: number, detect_ms: number | null, problem: string | null, model: string }
  ai: AiStatus
}

// GET /api/log/status (desktalk/web/routers/log.py)
export interface LogStatus {
  folder: string
  today: { pictures_waiting: number, events: Record<string, number> }
  last_compaction: ({ ts: number } & Record<string, unknown>) | null
  scheduler: Pick<TimedJob, 'name' | 'every_sec' | 'runs' | 'last_error' | 'next_in_sec'>[]
  stream: ConsumerStats[]
  camera: {
    source: string
    connected: boolean
    fps: number
    frames_received: number | null
    yolo_ms: number
    model: string
    pi?: PiInfo                    // only when the camera is the Pi
  }
}

// GET /api/dev/pi_stats (desktalk/web/routers/dev.py): the Pi's GET /api/system/stats, passed on by this
// computer, plus the Pi's address. A number the Pi can't read (no fan, no Wi-Fi, a Mac) is left out of its list.

export interface PiCpu {
  busy_pct: number
  user_pct: number
  system_pct: number
  iowait_pct: number
  other_pct: number
  cores_pct: number[]
  freq_mhz: number[]               // each core's speed now
  max_freq_mhz: number             // the fastest a core can go
  load: number[]                   // the 1, 5 and 15 minute load
  running: number
  processes: number
}

export interface PiMemory {
  total_bytes: number
  used_bytes: number
  available_bytes: number
  cached_bytes: number
  buffers_bytes: number
  free_bytes: number
  swap_total_bytes: number
  swap_used_bytes: number
}

export interface PiDiskSpace {
  label: string                    // settings → system_stats.disks, e.g. "data folder"
  path: string
  total_bytes: number
  used_bytes: number
  free_bytes: number
}

export interface PiDiskIo {
  name: string                     // e.g. mmcblk0
  read_bps: number                 // bytes a second
  write_bps: number
  reads_per_sec: number
  writes_per_sec: number
  busy_pct: number
}

export interface PiNetwork {
  name: string                     // e.g. wlan0
  rx_bps: number                   // received, bytes a second
  tx_bps: number                   // sent
  rx_errors: number                // these four are counted since the Pi started
  tx_errors: number
  rx_drops: number
  tx_drops: number
}

export interface PiProcess {
  pid: number
  name: string
  command: string
  cpu_pct: number                  // of one core, like top (so up to 100 × cores)
  memory_bytes: number
}

/** One reading of the Pi, every system_stats.every_sec on the Pi. */
export interface PiSample {
  at: number                       // seconds since 1970, by the Pi's clock
  uptime_sec: number
  cpu: PiCpu
  memory: PiMemory
  temps: { name: string, c: number }[]
  fans: { name: string, rpm: number, pwm_pct: number }[]
  cooling: { name: string, state: number, max_state: number }[]
  voltages: { name: string, v: number }[]
  under_voltage: boolean | null    // the power supply is too weak right now; null = the Pi can't tell
  throttled: string                // the Pi's slow-down flags in hex; '' = it couldn't read them
  disks: PiDiskSpace[]
  disk_io: PiDiskIo[]
  network: PiNetwork[]
  wifi: { name: string, quality: number, signal_dbm: number }[]
  top_processes: PiProcess[]
}

export interface PiStatsReply {
  collecting: boolean              // the Pi's system_stats.enabled
  every_sec: number                // how often the Pi takes a sample
  keep_min: number                 // how many minutes of samples it keeps
  info: { model?: string, kernel?: string, cores?: number }   // empty when it isn't collecting
  samples: PiSample[]              // oldest first
  address: string                  // settings → dev.pi_address
}

// ── one function per route ──

const q = encodeURIComponent

export const getDevSettings = () => getJSON<DevSettings>('/api/dev/settings')

export const getStates = (since: number) => getJSON<States>(`/api/dev/states?since=${q(String(since))}`)

export const getTracedLabels = async () => (await getJSON<{ labels: string[] }>('/api/dev/smoothing')).labels

/** The label goes in the query, so one with a slash (pen/pencil) works. Oldest picture first. */
export const getTrace = async (label: string) =>
  (await getJSON<{ label: string, steps: SmoothingStep[] }>(`/api/dev/smoothing/trace?label=${q(label)}`)).steps

/** One object's whole trace. The label is in the path; a slash in it (pen/pencil) is sent as %2F and still works. */
export const getObjectTrace = (label: string) => getJSON<ObjectTraceReply>(`/api/dev/objects/${q(label)}`)

/** Where one of an object's pictures is (a JPEG). */
export const tracePhotoUrl = (id: string) => `/api/dev/objects/photo/${q(id)}`

export const getTests = async () => (await getJSON<{ tests: TestItem[] }>('/api/dev/tests')).tests

/** Starts "Run everything". Gives the run id. A 409 ApiError means a run is already going. */
export const startCheck = async () => (await postJSON<{ run_id: string }>('/api/dev/runs', { check: true })).run_id

/** Starts the tests (or files) with these ids. Gives the run id. 409 while a run is going, 400 for an unknown id. */
export const startTests = async (ids: string[]) =>
  (await postJSON<{ run_id: string }>('/api/dev/runs', { tests: ids })).run_id

/** `from` is the last reply's output_end (0 the first time), so each reply only has the new printout. */
export const getRun = (id: string, from: number) =>
  getJSON<RunStatus>(`/api/dev/runs/${q(id)}?from=${q(String(from))}`)

export const stopRun = async (id: string): Promise<void> => {
  await postJSON<{ ok: boolean }>(`/api/dev/runs/${q(id)}/stop`, {})
}

export const getDatabases = async () => (await getJSON<{ databases: string[] }>('/api/dev/db')).databases

export const getTables = async (db: string) =>
  (await getJSON<{ tables: TableInfo[] }>(`/api/dev/db/${q(db)}/tables`)).tables

/** A query that isn't one read-only statement, or runs too long, is a 400 ApiError with the reason. */
export const runQuery = (db: string, sql: string) => postJSON<QueryResult>(`/api/dev/db/${q(db)}/query`, { sql })

export const getFiles = async () => (await getJSON<{ files: DataFile[] }>('/api/dev/files')).files

/** The start of a file in the data folder. 400 for a path outside it, 404 when nothing is there. */
export const getPreview = async (path: string) =>
  (await getJSON<{ path: string, text: string }>(`/api/dev/files/preview?path=${q(path)}`)).text

export const getScenarios = () => getJSON<Scenarios>('/api/dev/scenarios')

/** Starts a pretend warning counting down. 400 for a name not in the list, seconds out of range, or too many
 *  counting down already. */
export const startScenario = (name: string, afterSec: number) =>
  postJSON<WaitingScenario>('/api/dev/scenarios', { name, after_sec: afterSec })

/** Stops one counting down. 404 once it has gone off. */
export const cancelScenario = async (id: string): Promise<void> => {
  await deleteJSON<{ ok: boolean }>(`/api/dev/scenarios/${q(id)}`)
}

export const getHealth = () => getJSON<Health>('/api/health')

export const getLogStatus = () => getJSON<LogStatus>('/api/log/status')

/** The Pi's system numbers. `since` (the newest sample's `at` you have) asks only for newer samples. A 502 ApiError
 *  says in plain words why the Pi couldn't be reached. */
export const getPiStats = (since?: number) =>
  getJSON<PiStatsReply>(since === undefined ? '/api/dev/pi_stats' : `/api/dev/pi_stats?since=${q(String(since))}`)
