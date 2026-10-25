// The smoothing of one object, one picture after another, drawn with uPlot:
//   top     the box's confidence per picture, with the on and off lines (gaps = no box)
//   on/off  the hysteresis answer: blue while the on/off lines say "seen"
//   vote    how many of the last pictures voted yes, filled from the bottom, with a mark at vote_share
//           (green once the vote says "seen")
//   state   PRESENT green, ABSENT grey, UNKNOWN an outline; a mustard line underneath while it waits
import { useEffect, useRef, useState } from 'react'
import type uPlot from 'uplot'
import 'uplot/dist/uPlot.min.css'
import type { SmoothingSettings, SmoothingStep } from './api'

// Where things go on the chart's up-down scale: confidence uses 0 to 1, the three strips sit below it.
// These only place the drawing; they aren't settings.
const STRIPS = {
  hysteresis: { bottom: -0.22, top: -0.08, name: 'on/off' },
  vote: { bottom: -0.46, top: -0.28, name: 'vote' },
  state: { bottom: -0.70, top: -0.52, name: 'state' },
} as const
const Y_RANGE: [number, number] = [-0.74, 1.05]
const CONFIDENCE_TICKS = [0, 0.25, 0.5, 0.75, 1]
const HEIGHT_PX = 340

const middle = (s: { bottom: number, top: number }) => (s.bottom + s.top) / 2
const STRIP_NAMES = new Map(Object.values(STRIPS).map(s => [middle(s), s.name]))

/** The page's own colours, from style.css, so the chart matches the rest of the page. */
function colours() {
  const css = getComputedStyle(document.documentElement)
  const get = (name: string, fallback: string) => css.getPropertyValue(name).trim() || fallback
  return {
    ink: get('--ink', '#2B2440'), ink3: get('--ink-3', '#8D88A0'), line: get('--line', '#E4E5EE'),
    mist: get('--mist', '#F3F4F9'), cobalt: get('--cobalt', '#2F5FD0'), teal: get('--teal', '#1F9A83'),
    mustard: get('--mustard', '#9A6C00'), danger: get('--danger', '#C8324A'),
  }
}

function chartData(steps: SmoothingStep[], rules: SmoothingSettings): uPlot.AlignedData {
  return [
    steps.map(s => s.ts),
    steps.map(s => s.conf),
    steps.map(() => rules.on_confidence),
    steps.map(() => rules.off_confidence),
  ]
}

/** Draws the three strips under the confidence line, one block per picture. */
function drawStrips(u: uPlot, px: number, steps: SmoothingStep[], rules: SmoothingSettings): void {
  if (steps.length === 0) return
  const c = colours()
  const ctx = u.ctx
  const left = u.bbox.left
  const right = u.bbox.left + u.bbox.width
  const y = (v: number) => u.valToPos(v, 'y', true)
  const xs = steps.map(s => u.valToPos(s.ts, 'x', true))

  ctx.save()
  steps.forEach((s, i) => {
    // Each picture holds until the next one; the last one is as wide as the gap before it.
    const x0 = Math.max(left, xs[i])
    const gap = i + 1 < xs.length ? xs[i + 1] - xs[i] : i > 0 ? xs[i] - xs[i - 1] : right - x0
    const x1 = Math.min(right, xs[i] + gap)
    const w = Math.max(1, x1 - x0)

    const box = STRIPS.hysteresis
    ctx.fillStyle = s.box_seen ? c.cobalt : c.mist
    ctx.fillRect(x0, y(box.top), w, y(box.bottom) - y(box.top))

    const vote = STRIPS.vote
    ctx.fillStyle = c.mist
    ctx.fillRect(x0, y(vote.top), w, y(vote.bottom) - y(vote.top))
    const share = s.votes_total > 0 ? s.votes_yes / s.votes_total : 0
    const filledTo = vote.bottom + share * (vote.top - vote.bottom)
    ctx.fillStyle = s.seen ? c.teal : c.ink3
    ctx.fillRect(x0, y(filledTo), w, y(vote.bottom) - y(filledTo))

    const state = STRIPS.state
    const [top, bottom] = [y(state.top), y(state.bottom)]
    if (s.phase === 'UNKNOWN') {
      ctx.strokeStyle = c.ink3
      ctx.lineWidth = px
      ctx.strokeRect(x0, top, w, bottom - top)
    } else {
      ctx.fillStyle = s.phase === 'PRESENT' ? c.teal : c.line
      ctx.fillRect(x0, top, w, bottom - top)
    }
    if (s.heading !== null) {
      ctx.fillStyle = c.mustard
      ctx.fillRect(x0, bottom, w, 3 * px)
    }
  })

  // The vote_share mark across the vote strip.
  const vote = STRIPS.vote
  const mark = y(vote.bottom + rules.vote_share * (vote.top - vote.bottom))
  ctx.strokeStyle = c.ink
  ctx.lineWidth = px
  ctx.setLineDash([4 * px, 3 * px])
  ctx.beginPath()
  ctx.moveTo(left, mark)
  ctx.lineTo(right, mark)
  ctx.stroke()
  ctx.restore()
}

export default function SmoothingChart(props: { steps: SmoothingStep[], rules: SmoothingSettings }): JSX.Element {
  const box = useRef<HTMLDivElement>(null)
  const plot = useRef<uPlot | null>(null)
  const [problem, setProblem] = useState<string | null>(null)
  // The draw hook reads the newest steps and rules from here.
  const latest = useRef(props)
  latest.current = props

  useEffect(() => {
    const el = box.current
    if (!el) return
    let gone = false
    let resize: ResizeObserver | null = null
    // uPlot is loaded the first time a chart is drawn: it reads the screen as soon as it loads,
    // which the page's tests (with no real screen) can't do.
    import('uplot').then(({ default: UPlot }) => {
      if (gone) return
      const c = colours()
      const opts: uPlot.Options = {
        width: Math.max(el.clientWidth, 1),
        height: HEIGHT_PX,
        scales: { x: { time: true }, y: { range: Y_RANGE } },
        series: [
          {},
          { label: 'box confidence', stroke: c.ink, width: 2, spanGaps: false, points: { show: true, size: 5 },
            value: (_u, v) => (v === null || v === undefined ? 'no box' : v.toFixed(2)) },
          { label: 'on line', stroke: c.teal, dash: [6, 4], points: { show: false } },
          { label: 'off line', stroke: c.danger, dash: [6, 4], points: { show: false } },
        ],
        axes: [
          {},
          {
            size: 60,
            splits: () => [...STRIP_NAMES.keys(), ...CONFIDENCE_TICKS],
            values: (_u, splits) => splits.map(v => STRIP_NAMES.get(v) ?? String(v)),
          },
        ],
        hooks: { draw: [u => drawStrips(u, UPlot.pxRatio, latest.current.steps, latest.current.rules)] },
      }
      const u = new UPlot(opts, chartData(latest.current.steps, latest.current.rules), el)
      plot.current = u
      resize = new ResizeObserver(() => u.setSize({ width: Math.max(el.clientWidth, 1), height: HEIGHT_PX }))
      resize.observe(el)
    }).catch((e: unknown) => {
      if (!gone) setProblem(`Couldn't draw the chart: ${e instanceof Error ? e.message : String(e)}`)
    })
    return () => {
      gone = true
      resize?.disconnect()
      plot.current?.destroy()
      plot.current = null
    }
  }, [])

  useEffect(() => {
    plot.current?.setData(chartData(props.steps, props.rules))
  }, [props.steps, props.rules])

  return (
    <>
      {problem && <p className="error" role="alert">{problem}</p>}
      <div ref={box} className="smoothing-chart" />
    </>
  )
}
