// Step 10, piece 3: the guard screen. One screen, on a phone, on the factory
// Wi-Fi by LAN address — never the tunnel and never mobile data (SPEC §12, §14).
//
// It does what SPEC §3 says a guard entry is, and nothing else: who is on duty,
// which employee, one of two reasons, confirm. **The server stamps the time.**
//
// **There is no time control on this page and no time in what it sends.** Not
// disabled, not hidden — absent. The payload is three names, the service layer
// takes no time, and the database refuses a guard row that carries one. A guard
// who can type a time is a guard who can be asked to type a different one.
//
// **Nothing here undoes an entry.** Once confirmed it is on the record and HR
// corrects it; what should replace a wrong one is parked and belongs to piece 6.
import { useEffect, useState } from 'react'

import { ask } from '../api.js'

const REMEMBERED = 'hr.guard.on-duty'

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

function Panel({ children }) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
      {children}
    </div>
  )
}

function Cannot() {
  return (
    <p data-cannot-undo className="mt-4 text-sm text-slate-600">
      Once you confirm, this entry is on the record and{' '}
      <strong>cannot be undone here.</strong> If it is wrong, tell HR — they
      correct it.
    </p>
  )
}

export default function Guard({ search, go }) {
  const parameters = new URLSearchParams(search)
  const asked = parameters.get('employee') || ''
  const [screen, setScreen] = useState(null)
  const [guardCode, setGuardCode] = useState(
    () => parameters.get('guard') || window.localStorage.getItem(REMEMBERED) || '')
  const [typed, setTyped] = useState('')
  const [people, setPeople] = useState([])
  const [chosen, setChosen] = useState(null)
  const [reason, setReason] = useState('')
  const [done, setDone] = useState(null)
  const [error, setError] = useState(null)
  const [sending, setSending] = useState(false)

  useEffect(() => {
    ask('/api/guard/screen').then(setScreen).catch((p) => setError(p.message))
    ask('/api/employees')
      .then((roster) => setPeople(roster.people))
      .catch((p) => setError(p.message))
  }, [])

  // The employee in the address bar is the confirm step. Keeping it there means
  // the phone's Back button undoes a mistyped number, which is the correction
  // that has to be easy — the one after confirming is not available at all.
  useEffect(() => {
    if (!asked) {
      setChosen(null)
      return
    }
    setError(null)
    ask(`/api/guard/employee/${encodeURIComponent(asked)}`)
      .then(setChosen)
      .catch((problem) => {
        setChosen(null)
        setError(problem.message)
      })
  }, [asked])

  const rememberGuard = (code) => {
    window.localStorage.setItem(REMEMBERED, code)
    setGuardCode(code)
  }

  const guard = screen?.guards.find((one) => one.code === guardCode)
  const matches = typed
    ? people.filter(
        (person) =>
          person.employee_number.includes(typed) ||
          person.name.toLowerCase().includes(typed.toLowerCase()),
      )
    : []

  const confirm = async () => {
    setSending(true)
    setError(null)
    try {
      // Three names. There is nothing else to send.
      const result = await send('/api/guard/entry', {
        guard_code: guardCode,
        employee_number: chosen.employee_number,
        reason_code: reason,
      })
      setDone(result)
      setTyped('')
      setReason('')
      go('/guard')
    } catch (problem) {
      setError(problem.message)
    } finally {
      setSending(false)
    }
  }

  return (
    <main className="mx-auto min-h-screen max-w-md bg-slate-50 px-4 py-5">
      <header className="mb-4 flex items-baseline justify-between">
        <h1 className="text-lg font-semibold text-slate-900">Guard entry</h1>
        {guard && (
          <button
            onClick={() => rememberGuard('')}
            className="text-sm text-slate-500 underline"
          >
            {guard.name} — change
          </button>
        )}
      </header>

      {error && (
        <p data-error className="mb-4 rounded-lg bg-red-50 px-4 py-3 text-red-800">
          {error}
        </p>
      )}

      {done && (
        <div
          data-recorded
          className="mb-4 rounded-xl border border-emerald-300 bg-emerald-50 p-4"
        >
          <p className="text-lg font-semibold text-emerald-900">
            Recorded — {done.name}
          </p>
          <p className="mt-1 text-sm text-emerald-900">
            {done.employee_number} · {done.reason_label} · entered by{' '}
            {done.made_by}
          </p>
          <p className="mt-2 text-sm text-emerald-900">
            The server stamped <strong>{done.recorded_at}</strong>, counted on{' '}
            {done.attendance_day}. {done.final}
          </p>
        </div>
      )}

      {!screen && !error && <p className="text-slate-500">loading…</p>}

      {screen && !guard && (
        <Panel>
          <h2 className="font-semibold text-slate-900">Who is on duty?</h2>
          <p className="mt-1 text-sm text-slate-600">
            This phone remembers your choice. Every entry records who made it.
          </p>
          <div className="mt-3 space-y-2">
            {screen.guards.map((one) => (
              <button
                key={one.code}
                data-guard-choice={one.code}
                onClick={() => rememberGuard(one.code)}
                className="w-full rounded-lg border border-slate-300 px-4 py-3 text-left text-lg hover:bg-slate-100"
              >
                {one.name}
                {one.label && (
                  <span className="block text-sm text-slate-500">{one.label}</span>
                )}
              </button>
            ))}
          </div>
          {screen.guards_provisional && (
            <p data-provisional-guards className="mt-3 text-xs text-amber-700">
              These names are placeholders — the guard roster has not been read
              yet. Tell HR your name so the list can be corrected.
            </p>
          )}
        </Panel>
      )}

      {screen && guard && !chosen && (
        <Panel>
          <h2 className="font-semibold text-slate-900">Which employee?</h2>
          <label className="mt-3 block text-sm text-slate-600" htmlFor="employee">
            Type the employee number, or part of the name
          </label>
          <input
            id="employee"
            name="employee"
            type="text"
            inputMode="numeric"
            autoComplete="off"
            value={typed}
            onChange={(event) => setTyped(event.target.value)}
            className="mt-1 w-full rounded-lg border border-slate-300 px-4 py-3 text-2xl"
            placeholder="0090"
          />
          <div className="mt-3 max-h-80 space-y-1 overflow-y-auto">
            {matches.slice(0, 20).map((person) => (
              <button
                key={person.employee_id}
                data-pick={person.employee_number}
                onClick={() => go(`/guard?employee=${encodeURIComponent(person.employee_number)}`)}
                className="w-full rounded-lg border border-slate-200 px-4 py-3 text-left hover:bg-slate-100"
              >
                <span className="font-mono text-slate-500">
                  {person.employee_number}
                </span>{' '}
                <span className="text-slate-900">{person.name}</span>
              </button>
            ))}
            {typed && matches.length === 0 && (
              <p className="px-1 py-2 text-slate-500">
                Nobody on the list matches “{typed}”.
              </p>
            )}
          </div>
        </Panel>
      )}

      {screen && guard && chosen && (
        <Panel>
          <p className="text-sm text-slate-600">Is this the person in front of you?</p>
          {/* **The safeguard.** The name is the only thing between a mistyped
              number and a punch on somebody else's day, so it is the largest
              thing on the screen. */}
          <p data-name-back className="mt-2 text-3xl font-semibold leading-tight text-slate-900">
            {chosen.name || '(no name on the assignment rows)'}
          </p>
          <p className="mt-1 font-mono text-lg text-slate-500">
            {chosen.employee_number}
          </p>
          <button
            onClick={() => go('/guard')}
            className="mt-2 text-sm text-slate-500 underline"
          >
            No — go back
          </button>

          <h2 className="mt-5 font-semibold text-slate-900">Why?</h2>
          <div className="mt-2 space-y-2">
            {screen.reasons.map((one) => (
              <button
                key={one.code}
                data-reason={one.code}
                onClick={() => setReason(one.code)}
                className={
                  'w-full rounded-lg border px-4 py-3 text-left text-lg ' +
                  (reason === one.code
                    ? 'border-slate-900 bg-slate-900 text-white'
                    : 'border-slate-300 hover:bg-slate-100')
                }
              >
                {one.label}
              </button>
            ))}
          </div>

          <Cannot />

          <button
            data-confirm
            disabled={!reason || sending}
            onClick={confirm}
            className="mt-3 w-full rounded-lg bg-emerald-700 px-4 py-4 text-lg font-semibold text-white disabled:bg-slate-300"
          >
            {sending ? 'Recording…' : 'Confirm — record it now'}
          </button>
          <p className="mt-2 text-center text-xs text-slate-500">
            The server records the time. There is nothing here to type it into.
          </p>
        </Panel>
      )}
    </main>
  )
}
