// Step 10, piece 6: HR corrections. Two acts on one screen, both through the
// same service functions the CLI calls.
//
// **Adding a punch the device did not take.** A typed time, a reason in words,
// and who entered it. This is the HR path (SPEC §3) — the one with a time on
// it. The guard's screen is a different act in a different place, and it has
// no field for a time at all: **nothing here opens it, links to it, or posts to
// it**, and the two do not share a form.
//
// **Cancelling a correction.** The guard's screen tells employees that HR
// fixes mistakes; this is where that happens. A cancellation is a new row that
// voids an earlier one — never an edit and never a delete. The punch keeps its
// time, its reason and its author; the day is rebuilt so the figures stop
// counting it; and the per-day detail shows it as cancelled rather than
// hiding it, because a punch that disappears is indistinguishable from one
// that never happened.
//
// Only a correction can be cancelled. The list is read from `manual_punch` and
// no other table, so a device punch cannot appear on it.
import { useEffect, useRef, useState } from 'react'

import { ask } from '../api.js'

const REMEMBERED = 'hr.leave.typed-by'

async function send(path, body) {
  const response = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  const text = await response.text()
  if (!response.ok) {
    let detail = text
    try {
      const parsed = JSON.parse(text)
      detail = parsed.detail ?? text
      if (Array.isArray(detail)) detail = detail.map((d) => d.msg).join('; ')
    } catch {
      // not JSON
    }
    throw new Error(detail)
  }
  return JSON.parse(text)
}

const box = 'w-full rounded border border-slate-300 px-3 py-1.5'

function Panel({ title, children, note }) {
  return (
    <section className="rounded-lg border border-slate-200 bg-white p-5">
      <h2 className="font-semibold text-slate-900">{title}</h2>
      {note && <p className="mt-1 text-sm text-slate-600">{note}</p>}
      {children}
    </section>
  )
}

export default function Corrections({ go }) {
  const [screen, setScreen] = useState(null)
  const [error, setError] = useState(null)
  const [typedBy, setTypedBy] = useState(
    () => window.localStorage.getItem(REMEMBERED) || '')

  // Adding one.
  const [number, setNumber] = useState('')
  const [at, setAt] = useState('')
  const [why, setWhy] = useState('')
  const [adding, setAdding] = useState(false)
  const [added, setAdded] = useState(null)

  // Finding one to cancel.
  const [lookNumber, setLookNumber] = useState('')
  const [from, setFrom] = useState('')
  const [to, setTo] = useState('')
  const [found, setFound] = useState(null)
  const [looking, setLooking] = useState(false)

  // Cancelling one.
  const [target, setTarget] = useState(null)
  const [cancelWhy, setCancelWhy] = useState('')
  const [cancelling, setCancelling] = useState(false)
  const [cancelled, setCancelled] = useState(null)
  const dialog = useRef(null)
  const keepIt = useRef(null)

  useEffect(() => {
    ask('/api/corrections/screen').then(setScreen).catch((p) => setError(p.message))
  }, [])

  const typist = screen?.typists.find((one) => one.code === typedBy)

  const look = async () => {
    setLooking(true)
    setError(null)
    try {
      setFound(await ask(
        `/api/corrections/list?employee=${encodeURIComponent(lookNumber.trim())}`
        + `&from=${from}&to=${to}`))
    } catch (problem) {
      setFound(null)
      setError(problem.message)
    } finally {
      setLooking(false)
    }
  }

  const add = async () => {
    setAdding(true)
    setError(null)
    try {
      setAdded(await send('/api/corrections/retroactive', {
        entered_by: typedBy,
        employee_number: number.trim(),
        at,
        reason: why.trim(),
      }))
      setNumber('')
      setAt('')
      setWhy('')
    } catch (problem) {
      setError(problem.message)
    } finally {
      setAdding(false)
    }
  }

  const askAgain = (line) => {
    setTarget(line)
    setCancelWhy('')
    // The dialog is opened after the state lands, so it names the right punch.
    window.setTimeout(() => {
      dialog.current?.showModal()
      // **Keep it holds the focus, explicitly.** The default answer to a
      // question nobody meant to ask is no — the same rule the guard's dialog
      // follows, and for the same reason: leaving it to markup order makes the
      // safe default an accident a later edit reverses silently.
      keepIt.current?.focus()
    }, 0)
  }

  const doCancel = async () => {
    setCancelling(true)
    setError(null)
    try {
      const result = await send('/api/corrections/cancel', {
        cancelled_by: typedBy,
        punch_id: target.punch_id,
        reason: cancelWhy.trim(),
      })
      dialog.current?.close()
      setCancelled(result)
      setTarget(null)
      await look()
    } catch (problem) {
      setError(problem.message)
    } finally {
      setCancelling(false)
    }
  }

  if (error && !screen) return <p className="text-red-700">{error}</p>
  if (!screen) return <p className="text-slate-500">asking…</p>

  if (!typist) {
    return (
      <div className="max-w-md">
        <h1 className="text-xl font-semibold text-slate-900">Corrections</h1>
        <p className="mt-1 text-sm text-slate-600">
          Who is making the correction? Every correction records who made it
          (SPEC §3). This browser remembers the choice.
        </p>
        <div className="mt-4 space-y-2">
          {screen.typists.map((one) => (
            <button
              key={one.code}
              data-typist={one.code}
              onClick={() => {
                window.localStorage.setItem(REMEMBERED, one.code)
                setTypedBy(one.code)
              }}
              className="w-full rounded-lg border border-slate-300 bg-white px-4 py-3 text-left text-lg hover:bg-slate-100"
            >
              {one.name}
              {one.label && <span className="block text-sm text-slate-500">{one.label}</span>}
            </button>
          ))}
        </div>
      </div>
    )
  }

  return (
    <div className="max-w-4xl">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <h1 className="text-xl font-semibold text-slate-900">Corrections</h1>
        <button
          data-change-typist
          onClick={() => {
            window.localStorage.setItem(REMEMBERED, '')
            setTypedBy('')
          }}
          className="text-sm text-slate-500 underline"
        >
          {typist.name} — change
        </button>
      </div>

      {/* **Which path this is, said rather than assumed.** The two acts look
          alike from a distance; only one of them types a time. */}
      <p data-not-guard className="mt-3 rounded bg-slate-100 px-4 py-3 text-sm text-slate-700">
        {screen.not_the_guard_path}
      </p>

      {error && (
        <p data-error className="mt-4 rounded bg-red-50 px-4 py-3 text-red-800">
          {error}
        </p>
      )}

      <div className="mt-6 space-y-6">
        <Panel
          title="Add a punch the device did not take"
          note="A day the device was down, a punch somebody forgot. The time is typed, and the reason is required."
        >
          {added && (
            <div data-added className="mt-3 rounded-lg border border-emerald-300 bg-emerald-50 p-4 text-sm text-emerald-900">
              <p className="font-semibold">
                Recorded — {added.employee_number} at {added.at}, counted on{' '}
                {added.attendance_day}
              </p>
              <p className="mt-1">
                {added.reason} · entered by {added.made_by} · server-stamped{' '}
                {added.recorded_at}
              </p>
              <p className="mt-1">{added.marked}</p>
            </div>
          )}
          <div className="mt-3 grid gap-3 sm:grid-cols-3">
            <div>
              <label className="block text-sm text-slate-600" htmlFor="employee">
                Employee no.
              </label>
              <input
                id="employee"
                data-add-employee
                type="text"
                inputMode="numeric"
                autoComplete="off"
                value={number}
                onChange={(event) => setNumber(event.target.value)}
                placeholder="four digits"
                className={box + ' mt-1 font-mono'}
              />
            </div>
            <div>
              <label className="block text-sm text-slate-600" htmlFor="at">
                Time of the punch
              </label>
              <input
                id="at"
                data-add-at
                type="datetime-local"
                value={at}
                onChange={(event) => setAt(event.target.value)}
                className={box + ' mt-1'}
              />
            </div>
            <div>
              <label className="block text-sm text-slate-600" htmlFor="why">
                Why
              </label>
              <input
                id="why"
                data-add-why
                type="text"
                value={why}
                onChange={(event) => setWhy(event.target.value)}
                placeholder="device down"
                className={box + ' mt-1'}
              />
            </div>
          </div>
          <button
            data-add
            disabled={adding || !number.trim() || !at || !why.trim()}
            onClick={add}
            className="mt-4 rounded-lg bg-slate-900 px-5 py-2.5 font-semibold text-white disabled:bg-slate-300"
          >
            {adding ? 'Recording…' : 'Record this punch'}
          </button>
        </Panel>

        <Panel
          title="Cancel a correction"
          note="Find the correction first. Only corrections are listed — a device punch is a fact from the hardware and nothing here touches it."
        >
          {cancelled && (
            <div data-cancel-done className="mt-3 rounded-lg border border-amber-300 bg-amber-50 p-4 text-sm text-amber-900">
              <p className="font-semibold">
                Correction {cancelled.punch_id} cancelled by{' '}
                {cancelled.cancelled_by}
              </p>
              <p className="mt-1">
                {cancelled.reason} · {cancelled.cancelled_at} · the{' '}
                {cancelled.attendance_day} row was rebuilt, so the figures stop
                counting it
              </p>
              <p data-punch-unchanged className="mt-1">{cancelled.punch_unchanged}</p>
              <p className="mt-1">{cancelled.final}</p>
            </div>
          )}

          <div className="mt-3 grid gap-3 sm:grid-cols-4">
            <div>
              <label className="block text-sm text-slate-600" htmlFor="look">
                Employee no.
              </label>
              <input
                id="look"
                data-look-employee
                type="text"
                inputMode="numeric"
                autoComplete="off"
                value={lookNumber}
                onChange={(event) => setLookNumber(event.target.value)}
                placeholder="four digits"
                className={box + ' mt-1 font-mono'}
              />
            </div>
            <div>
              <label className="block text-sm text-slate-600" htmlFor="from">
                From
              </label>
              <input id="from" data-look-from type="date" value={from}
                     onChange={(event) => setFrom(event.target.value)}
                     className={box + ' mt-1'} />
            </div>
            <div>
              <label className="block text-sm text-slate-600" htmlFor="to">
                To
              </label>
              <input id="to" data-look-to type="date" value={to}
                     onChange={(event) => setTo(event.target.value)}
                     className={box + ' mt-1'} />
            </div>
            <div className="flex items-end">
              <button
                data-look
                disabled={looking || !lookNumber.trim() || !from || !to}
                onClick={look}
                className="w-full rounded-lg border border-slate-400 px-4 py-2 font-medium disabled:border-slate-200 disabled:text-slate-300"
              >
                {looking ? 'Looking…' : 'Find them'}
              </button>
            </div>
          </div>

          {found && (
            <div className="mt-4">
              <p data-only-manual className="text-xs text-slate-500">
                {found.only_manual}
              </p>
              {found.corrections.length === 0 ? (
                <p data-none className="mt-2 text-sm text-slate-500">
                  No corrections for {found.employee_number} between{' '}
                  {found.start} and {found.end}.
                </p>
              ) : (
                <table className="mt-2 w-full text-sm">
                  <thead>
                    <tr className="text-left text-slate-500">
                      {['Day', 'Time', 'Path', 'Why', 'Entered by', ''].map((h) => (
                        <th key={h} className="border-b border-slate-300 px-2 py-1.5 font-medium">
                          {h}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {found.corrections.map((line) => (
                      <tr
                        key={line.punch_id}
                        data-correction={line.punch_id}
                        data-cancelled={line.cancelled ? 'yes' : 'no'}
                        className={line.cancelled ? 'text-slate-400' : ''}
                      >
                        <td className="border-b border-slate-100 px-2 py-1.5 font-mono">
                          {line.attendance_day}
                        </td>
                        <td className="border-b border-slate-100 px-2 py-1.5 font-mono">
                          {line.at || '—'}
                        </td>
                        <td className="border-b border-slate-100 px-2 py-1.5">
                          {line.source}
                        </td>
                        <td className="border-b border-slate-100 px-2 py-1.5">
                          {line.why}
                          {line.cancelled && (
                            <span className="block text-xs text-amber-700">
                              cancelled by {line.cancelled_by} —{' '}
                              {line.cancelled_why}
                            </span>
                          )}
                        </td>
                        <td className="border-b border-slate-100 px-2 py-1.5">
                          {line.who}
                        </td>
                        <td className="border-b border-slate-100 px-2 py-1.5 text-right">
                          {line.cancelled ? (
                            <span data-already className="text-xs">cancelled</span>
                          ) : (
                            <button
                              data-cancel={line.punch_id}
                              onClick={() => askAgain(line)}
                              className="rounded border border-slate-300 px-2 py-1 text-xs hover:bg-slate-100"
                            >
                              Cancel this
                            </button>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          )}

          <p data-cannot-undo className="mt-4 text-sm text-slate-600">
            {screen.cannot_undo}
          </p>
        </Panel>
      </div>

      {/* The dialog names the punch it is about to void, and asks why. **A
          dialog that only asked "are you sure?" would add a tap without adding
          a check** — the same reasoning as the guard's. */}
      <dialog
        ref={dialog}
        data-dialog
        className="m-auto w-[30rem] max-w-[calc(100%-2rem)] rounded-xl p-5 backdrop:bg-slate-900/40"
      >
        {target && (
          <>
            <p data-dialog-question className="text-lg text-slate-900">
              Cancel the correction of{' '}
              <strong className="font-mono">{target.at || target.attendance_day}</strong>
              {' '}for <strong>{found?.employee_number}</strong>?
            </p>
            <p className="mt-2 text-sm text-slate-600">
              {target.source} · {target.why} · entered by {target.who}. The punch
              row is not changed and not removed; a row is added beside it, and
              the day is rebuilt so the figures stop counting it.
            </p>
            <label className="mt-4 block text-sm text-slate-600" htmlFor="cancel-why">
              Why is it being cancelled? Required.
            </label>
            <input
              id="cancel-why"
              data-cancel-why
              type="text"
              value={cancelWhy}
              onChange={(event) => setCancelWhy(event.target.value)}
              className={box + ' mt-1'}
            />
            <div className="mt-5 flex flex-wrap gap-2">
              <button
                data-dialog-keep
                ref={keepIt}
                onClick={() => {
                  dialog.current?.close()
                  setTarget(null)
                }}
                className="min-w-0 flex-1 rounded-lg border border-slate-300 px-4 py-2.5 text-lg"
              >
                Keep it
              </button>
              <button
                data-dialog-cancel-it
                disabled={cancelling || !cancelWhy.trim()}
                onClick={doCancel}
                className="min-w-0 flex-1 rounded-lg bg-amber-700 px-4 py-2.5 text-lg font-semibold text-white disabled:bg-slate-300"
              >
                {cancelling ? 'Cancelling…' : 'Cancel the correction'}
              </button>
            </div>
          </>
        )}
      </dialog>
    </div>
  )
}
