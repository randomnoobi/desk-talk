// The Checks tab: one button that runs every check in settings → dev.checks, and every test pytest
// finds, by folder and file, each with its own Run button. One run at a time; the page asks the
// server how it's going every settings.poll_sec and adds the new printout on.
import { useEffect, useMemo, useRef, useState, type CSSProperties } from 'react'
import {
  ApiError, getRun, getTests, startCheck, startTests, stopRun,
  type DevSettings, type RunStatus, type StepStatus, type TestItem, type TestOutcome,
} from '../api'
import { formatClock, formatSeconds } from '../format'
import { buildTree, filterTree, type TreeNode } from '../testTree'
import { ErrorLine, Pill, type Level } from '../ui'

const wordsOf = (e: unknown) => (e instanceof Error ? e.message : String(e))

/** A run this page started: the check, or some tests. */
interface Started {
  id: string
  kind: 'check' | 'tests'
}

const isBad = (o: TestOutcome) => o.outcome === 'failed' || o.outcome === 'error'

function outcomeLevel(o: TestOutcome | undefined): Level | undefined {
  if (!o) return undefined
  if (isBad(o)) return 'bad'
  return o.outcome === 'passed' ? 'ok' : 'off'
}

function stateLevel(state: string): Level | undefined {
  if (state === 'passed') return 'ok'
  if (state === 'failed' || state === 'timed out') return 'bad'
  if (state === 'running') return 'warn'
  if (state === 'stopped') return 'off'
  return undefined
}

/** A folder or file is red if any test in it failed, green once every test in it passed. */
function nodeLevel(node: TreeNode, results: ReadonlyMap<string, TestOutcome>): Level | undefined {
  if (node.children.length === 0) return outcomeLevel(results.get(node.path))
  let allPassed = true
  for (const id of node.testIds) {
    const o = results.get(id)
    if (o && isBad(o)) return 'bad'
    if (!o || o.outcome !== 'passed') allPassed = false
  }
  return allPassed ? 'ok' : undefined
}

// The tab's own bits of layout (the shared look is in style.css).
const S: Record<string, CSSProperties> = {
  plainList: { listStyle: 'none' },
  group: { listStyle: 'none', marginLeft: 22 },
  line: { display: 'flex', alignItems: 'center', gap: 8, padding: '3px 0', minHeight: 32 },
  toggle: { display: 'flex', alignItems: 'center', gap: 6, fontWeight: 600, textAlign: 'left' },
  arrow: { display: 'inline-block', width: 12, color: 'var(--ink-3)' },
  smallBtn: { padding: '3px 10px', fontSize: 13 },
  grow: { flex: 1 },
  failed: { display: 'flex', flexDirection: 'column', gap: 4, padding: '8px 0', borderTop: '1px solid var(--line)' },
}

function Branch(props: {
  node: TreeNode
  results: ReadonlyMap<string, TestOutcome>
  isOpen: (node: TreeNode) => boolean
  toggle: (node: TreeNode) => void
  busy: boolean
  run: (ids: string[]) => void
}): JSX.Element {
  const { node, results, isOpen, toggle, busy, run } = props
  const leaf = node.children.length === 0
  const open = !leaf && isOpen(node)
  const level = nodeLevel(node, results)
  const result = leaf ? results.get(node.path) : undefined
  const runButton = (
    <button type="button" className="btn" style={S.smallBtn} disabled={busy} aria-label={`Run ${node.name}`}
            onClick={() => run(node.testIds)}>
      Run
    </button>
  )
  return (
    <li role="treeitem" aria-label={node.name} aria-expanded={leaf ? undefined : open} className={level}>
      <div style={S.line}>
        <span className="dot" aria-hidden="true" style={level ? undefined : { visibility: 'hidden' }} />
        {leaf
          ? <span className="mono" style={level === 'bad' ? { color: 'var(--danger)' } : undefined}>{node.name}</span>
          : (
            <button type="button" style={S.toggle} onClick={() => toggle(node)}>
              <span aria-hidden="true" style={S.arrow}>{open ? '▾' : '▸'}</span>
              <span>{node.name}</span>
            </button>
          )}
        {node.slow && <span className="tag">slow</span>}
        {!leaf && <span className="muted small">{node.testIds.length} {node.testIds.length === 1 ? 'test' : 'tests'}</span>}
        {result && <span className="muted small">{formatSeconds(result.duration_sec)}</span>}
        <span style={S.grow} />
        {runButton}
      </div>
      {open && (
        <ul role="group" style={S.group}>
          {node.children.map(child => <Branch key={child.path} {...props} node={child} />)}
        </ul>
      )}
    </li>
  )
}

export default function Checks(props: { settings: DevSettings }): JSX.Element {
  const { settings } = props
  const everyMs = settings.poll_sec * 1000

  // ── the list of tests ──
  const [tests, setTests] = useState<TestItem[] | null>(null)
  const [listError, setListError] = useState<string | null>(null)
  const [listAsked, setListAsked] = useState(0)
  useEffect(() => {
    let gone = false
    setListError(null)
    getTests().then(
      found => { if (!gone) setTests(found) },
      e => { if (!gone) setListError(wordsOf(e)) },
    )
    return () => { gone = true }
  }, [listAsked])

  const tree = useMemo(() => buildTree(tests ?? []), [tests])
  const [words, setWords] = useState('')
  const shown = useMemo(() => filterTree(tree, words), [tree, words])
  // Folders start open and files shut; a file with a failed test opens by itself. A click wins over both.
  const [folded, setFolded] = useState<ReadonlyMap<string, boolean>>(new Map())

  // ── the run ──
  const [started, setStarted] = useState<Started | null>(null)
  const [status, setStatus] = useState<RunStatus | null>(null)
  const [going, setGoing] = useState(false)
  const [starting, setStarting] = useState(false)
  const [output, setOutput] = useState('')
  const [checkSteps, setCheckSteps] = useState<StepStatus[]>([])
  const [runError, setRunError] = useState<string | null>(null)
  const busy = starting || going

  // Ask how the run is going until it's over. Each answer only has the printout after `from`.
  useEffect(() => {
    if (!started) return
    let gone = false
    let timer: ReturnType<typeof setTimeout> | undefined
    let from = 0
    const ask = async () => {
      try {
        const now = await getRun(started.id, from)
        if (gone) return
        from = Math.max(from, now.output_end)
        setStatus(now)
        if (now.output) setOutput(old => old + now.output)
        if (started.kind === 'check') setCheckSteps(now.steps)
        setRunError(null)
        if (now.state === 'running') timer = setTimeout(ask, everyMs)
        else setGoing(false)
      } catch (e) {
        if (gone) return
        setRunError(wordsOf(e))
        // 404: the server doesn't know this run any more (it was restarted), so stop asking.
        if (e instanceof ApiError && e.status === 404) setGoing(false)
        else timer = setTimeout(ask, everyMs)
      }
    }
    void ask()
    return () => {
      gone = true
      clearTimeout(timer)
    }
  }, [started, everyMs])

  const start = async (kind: Started['kind'], ids: string[]) => {
    if (busy) return
    setStarting(true)
    setRunError(null)
    try {
      const id = kind === 'check' ? await startCheck() : await startTests(ids)
      setStatus(null)
      setOutput('')
      if (kind === 'check') setCheckSteps([])
      setGoing(true)
      setStarted({ id, kind })
    } catch (e) {
      setRunError(wordsOf(e))       // a 409 says "A run is already going."
    } finally {
      setStarting(false)
    }
  }

  const stop = async () => {
    if (!started) return
    try {
      await stopRun(started.id)
    } catch (e) {
      setRunError(wordsOf(e))
    }
  }

  // ── what the run found ──
  const results = useMemo(() => new Map((status?.outcomes ?? []).map(o => [o.id, o])), [status])
  const count = useMemo(() => {
    const c = { passed: 0, failed: 0, skipped: 0, error: 0 }
    for (const o of results.values()) c[o.outcome] += 1
    return c
  }, [results])
  const failed = [...results.values()].filter(isBad)

  const isOpen = (node: TreeNode) => {
    if (words.trim()) return true
    const clicked = folded.get(node.path)
    if (clicked !== undefined) return clicked
    const isFolder = tree.some(f => f.path === node.path)
    return isFolder || nodeLevel(node, results) === 'bad'
  }
  const toggle = (node: TreeNode) => {
    const now = isOpen(node)
    setFolded(old => new Map(old).set(node.path, !now))
  }

  // Keep the printout scrolled to the end, unless you've scrolled up to read something.
  const printout = useRef<HTMLPreElement>(null)
  const atEnd = useRef(true)
  useEffect(() => {
    const box = printout.current
    if (box && atEnd.current) box.scrollTop = box.scrollHeight
  }, [output])

  const stepState = (name: string) => checkSteps.find(s => s.name === name)?.state ?? 'waiting'

  return (
    <div className="stack">
      <div className="card">
        <div className="row">
          <button type="button" className="btn primary big" disabled={busy} onClick={() => void start('check', [])}>
            Run everything
          </button>
          <ul className="row" style={S.plainList} aria-label="Steps">
            {settings.checks.map(name => {
              const state = stepState(name)
              return <li key={name} className={`chip ${stateLevel(state) ?? ''}`.trim()}>{name}: {state}</li>
            })}
          </ul>
        </div>
        <ErrorLine error={runError} />
      </div>

      {started && (
        <div className="card">
          <div className="row">
            <h2 style={{ marginBottom: 0 }}>{status?.what ?? (started.kind === 'check' ? 'Everything' : 'Tests')}</h2>
            <Pill level={stateLevel(status?.state ?? 'running')}>{status?.state ?? 'starting'}</Pill>
            {status && (
              <span className="muted small">
                started {formatClock(status.started)}
                {' · '}
                {status.finished !== null
                  ? `took ${formatSeconds(status.finished - status.started)}`
                  : `${formatSeconds(Math.max(0, Date.now() / 1000 - status.started))} so far`}
              </span>
            )}
            <span style={S.grow} />
            {going && <button type="button" className="btn danger" onClick={() => void stop()}>Stop</button>}
          </div>

          <p className="row" aria-label="Count" style={{ margin: '12px 0' }}>
            <Pill level="ok">{count.passed} passed</Pill>
            <Pill level={count.failed ? 'bad' : undefined}>{count.failed} failed</Pill>
            {count.error > 0 && <Pill level="bad">{count.error} errors</Pill>}
            <Pill level="off">{count.skipped} skipped</Pill>
          </p>

          {!going && failed.length > 0 && (
            <section aria-label="Failed tests" style={{ marginBottom: 12 }}>
              <div className="row" style={{ marginBottom: 6 }}>
                <h3 style={{ marginBottom: 0 }}>Failed</h3>
                <span style={S.grow} />
                <button type="button" className="btn primary" disabled={busy}
                        onClick={() => void start('tests', failed.map(o => o.id))}>
                  Run again
                </button>
              </div>
              <ul style={S.plainList}>
                {failed.map(o => (
                  <li key={o.id} className="bad" style={S.failed}>
                    <span className="mono" style={{ color: 'var(--danger)', fontWeight: 600 }}>{o.id}</span>
                    {o.message && <span className="mono muted">{o.message}</span>}
                  </li>
                ))}
              </ul>
            </section>
          )}

          <pre ref={printout} className="printout" aria-label="Printout"
               onScroll={e => {
                 const box = e.currentTarget
                 atEnd.current = box.scrollHeight - box.scrollTop - box.clientHeight < box.clientHeight / 4
               }}>
            {output || (going ? 'Waiting for the first lines…' : '')}
          </pre>
        </div>
      )}

      <div className="card">
        <div className="row" style={{ marginBottom: 10 }}>
          <h2 style={{ marginBottom: 0 }}>Each test</h2>
          {tests && <span className="muted small">{tests.length} found</span>}
          <span style={S.grow} />
          <input type="search" className="input" aria-label="Find a test" placeholder="Find a test"
                 value={words} onChange={e => setWords(e.target.value)} />
          <button type="button" className="btn" onClick={() => setListAsked(n => n + 1)}>Find them again</button>
        </div>
        <ErrorLine error={listError} />
        {tests === null && !listError && <p className="muted">Asking pytest for the tests…</p>}
        {tests !== null && shown.length === 0 && (
          <p className="muted">{words.trim() ? 'No test matches that.' : 'pytest found no tests.'}</p>
        )}
        {shown.length > 0 && (
          <ul role="tree" aria-label="Tests" style={S.plainList}>
            {shown.map(node => (
              <Branch key={node.path} node={node} results={results} isOpen={isOpen} toggle={toggle} busy={busy}
                      run={ids => void start('tests', ids)} />
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}
