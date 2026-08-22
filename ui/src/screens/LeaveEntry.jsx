// Step 10, piece 4: leave entry. HR types a form that has already been signed
// on paper (SPEC §6), and this screen computes nothing.
//
// **The fields are in the paper's order**, so a person reads down the page and
// down the form together: name, staff no., department, date of application,
// nature of leave, then period from, to, no. of days. The order comes from the
// server — `leave_entry.FORM_ORDER` — and the page is checked against it.
//
// **The day count is typed and is never derived.** Nothing here subtracts the
// two dates. Where the typed count and the range disagree, the server is asked
// for both numbers and the sentence that goes with them, and both are shown:
// the form's number is the one that counts.
//
// **The applied-for type and the sheet code are two fields.** Choosing a type
// offers the code A48 suggests, for the three types that have one and for no
// others — an offer, not a mapping. Change it, clear it, or type a code with
// no type at all; the row records what is in the boxes.
//
// **The SQL Account code is not on this screen.** It stays empty until
// Accounts answers (SPEC §8), and there is nothing here to type it into.
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

// One row of the paper form. `field` is the name §6 gives it, and the order
// these appear in is checked against the server's list.
function Field({ field, label, children, hint, after }) {
  return (
    <div data-form-field={field} className="border-b border-slate-100 py-3 sm:flex sm:gap-4">
      <label className="block w-56 shrink-0 pt-1.5 text-sm font-medium text-slate-700">
        {label}
      </label>
      <div className="mt-1 min-w-0 flex-1 sm:mt-0">
        {children}
        {hint && <p className="mt-1 text-xs text-slate-500">{hint}</p>}
        {/* Below the hint, not above it: the hint explains the box, and what
            follows is a remark about what was typed into it. */}
        {after}
      </div>
    </div>
  )
}

const box = 'w-full rounded border border-slate-300 px-3 py-1.5'

export default function LeaveEntry({ go }) {
  const [screen, setScreen] = useState(null)
  const [error, setError] = useState(null)
  const [typedBy, setTypedBy] = useState(
    () => window.localStorage.getItem(REMEMBERED) || '')

  const [number, setNumber] = useState('')
  const [person, setPerson] = useState(null)
  const [applied, setApplied] = useState('')
  const [type, setType] = useState('')
  const [reason, setReason] = useState('')
  const [code, setCode] = useState('')
  // **Whether HR has touched the code box.** The suggestion fills an untouched
  // box and never overwrites a choice: a code somebody picked, replaced by a
  // suggestion because they went back and changed the tick, is the screen
  // filling one field in from the other — which §6 forbids.
  const codeTouched = useRef(false)
  const [from, setFrom] = useState('')
  const [to, setTo] = useState('')
  const [days, setDays] = useState('')
  const [count, setCount] = useState(null)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(null)

  useEffect(() => {
    ask('/api/leave/screen').then(setScreen).catch((p) => setError(p.message))
  }, [])

  // The name, staff number and department behind the number, read against the
  // paper. Read on the day the leave starts: an assignment is effective-dated.
  useEffect(() => {
    if (!number.trim()) {
      setPerson(null)
      return
    }
    let dropped = false
    ask(`/api/leave/employee/${encodeURIComponent(number.trim())}`
        + (from ? `?on=${from}` : ''))
      .then((found) => !dropped && setPerson(found))
      .catch(() => !dropped && setPerson(null))
    return () => {
      dropped = true
    }
  }, [number, from])

  // **The server counts the range, not this page.** What comes back is two
  // numbers and the sentence that goes with them; nothing here works out
  // either, and neither ever reaches the day count that gets saved.
  useEffect(() => {
    if (!from || !to || !days.trim()) {
      setCount(null)
      return
    }
    let dropped = false
    ask(`/api/leave/range-check?from=${from}&to=${to}&days=${encodeURIComponent(days.trim())}`)
      .then((answer) => !dropped && setCount(answer))
      .catch(() => !dropped && setCount(null))
    return () => {
      dropped = true
    }
  }, [from, to, days])

  const chosen = screen?.types.find((one) => one.code === type)
  const suggestion = chosen?.suggested_sheet_code || null
  const typist = screen?.typists.find((one) => one.code === typedBy)

  const pickType = (one) => {
    setType(one.code)
    if (!one.reason_required) setReason('')
    // A48: offer the code, and only into a box nobody has touched.
    if (!codeTouched.current) setCode(one.suggested_sheet_code || '')
  }

  const save = async () => {
    setSaving(true)
    setError(null)
    try {
      const result = await send('/api/leave/entry', {
        entered_by: typedBy,
        employee_number: number.trim(),
        period_from: from,
        period_to: to,
        // Exactly what is in the box. There is no other source for it.
        days: days.trim(),
        date_of_application: applied || null,
        leave_type_code: type || null,
        sheet_code: code || null,
        reason: reason.trim() || null,
      })
      setSaved(result)
      setNumber('')
      setPerson(null)
      setApplied('')
      setType('')
      setReason('')
      setCode('')
      codeTouched.current = false
      setFrom('')
      setTo('')
      setDays('')
      setCount(null)
    } catch (problem) {
      setError(problem.message)
    } finally {
      setSaving(false)
    }
  }

  if (error && !screen) return <p className="text-red-700">{error}</p>
  if (!screen) return <p className="text-slate-500">asking…</p>

  if (!typist) {
    return (
      <div className="max-w-md">
        <h1 className="text-xl font-semibold text-slate-900">Leave entry</h1>
        <p className="mt-1 text-sm text-slate-600">
          Who is typing? Every leave record says who entered it (SPEC §6). This
          browser remembers the choice.
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
    <div className="max-w-3xl">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <h1 className="text-xl font-semibold text-slate-900">Leave entry</h1>
        <button
          data-change-typist
          onClick={() => {
            window.localStorage.setItem(REMEMBERED, '')
            setTypedBy('')
          }}
          className="text-sm text-slate-500 underline"
        >
          typed by {typist.name} — change
        </button>
      </div>
      <p className="mt-1 text-sm text-slate-600">
        The form, in the order it is printed. It has already been signed on
        paper; this records what it says and nothing else.
      </p>

      {error && (
        <p data-error className="mt-4 rounded bg-red-50 px-4 py-3 text-red-800">
          {error}
        </p>
      )}

      {saved && (
        <div data-saved className="mt-4 rounded-lg border border-emerald-300 bg-emerald-50 p-4">
          <p className="font-semibold text-emerald-900">
            Recorded — {saved.name} ({saved.employee_number}),{' '}
            {saved.period_from} to {saved.period_to}, {saved.days} day(s)
          </p>
          <p className="mt-1 text-sm text-emerald-900">
            applied for {saved.leave_type_code || '— nothing ticked'} · sheet
            code {saved.sheet_code || '— none'} · SQL Account code{' '}
            {saved.sql_account_code || 'empty (SPEC §8)'} · typed by{' '}
            {saved.entered_by}
          </p>
          <p className="mt-2 text-sm text-emerald-900">{saved.stored}</p>
          <button
            data-see-on-sheet
            onClick={() => go(`/sheet?month=${saved.month}`)}
            className="mt-2 text-sm text-emerald-900 underline"
          >
            See it on the {saved.month} sheet
          </button>
        </div>
      )}

      <div className="mt-6 rounded-lg border border-slate-200 bg-white px-5 py-2">
        <Field field="name" label="Name of applicant">
          <p data-employee-name className="pt-1.5 text-lg text-slate-900">
            {person ? person.name || '(no name on the assignment rows)' : '—'}
          </p>
        </Field>

        <Field
          field="staff_no"
          label="Staff no."
          hint="Type the number on the form. The name above is read back from it."
        >
          <input
            data-staff-no
            type="text"
            inputMode="numeric"
            autoComplete="off"
            value={number}
            onChange={(event) => setNumber(event.target.value)}
            placeholder="four digits"
            className={box + ' font-mono'}
          />
        </Field>

        <Field
          field="department"
          label="Department"
          hint={person ? person.department_note : undefined}
        >
          <p data-department className="pt-1.5 text-slate-900">
            {person ? person.department || '—' : '—'}
            {person && (
              <span className="ml-2 text-xs text-slate-500">
                as on {person.as_of}
              </span>
            )}
          </p>
        </Field>

        <Field
          field="date_of_application"
          label="Date"
          hint="When the form was made out. A different fact from the dates of the leave (SPEC §6)."
        >
          <input
            data-applied
            type="date"
            value={applied}
            onChange={(event) => setApplied(event.target.value)}
            className={box}
          />
        </Field>

        <Field field="nature_of_leave" label="Nature of leave">
          <div className="flex flex-wrap gap-2">
            {screen.types.map((one) => (
              <button
                key={one.code}
                data-type={one.code}
                onClick={() => pickType(type === one.code ? { code: '', reason_required: false, suggested_sheet_code: null } : one)}
                className={
                  'rounded border px-3 py-1.5 text-sm ' +
                  (type === one.code
                    ? 'border-slate-900 bg-slate-900 text-white'
                    : 'border-slate-300 hover:bg-slate-100')
                }
              >
                {one.label}
              </button>
            ))}
          </div>
          {chosen?.reason_required && (
            // On the paper the Reason is not a field of its own: it hangs off
            // the Unpaid Leave tick, so it is shown here rather than as a
            // ninth line.
            <div className="mt-3">
              <label className="block text-sm text-slate-600" htmlFor="reason">
                Reason — the form carries one for this tick
              </label>
              <input
                id="reason"
                data-reason
                type="text"
                value={reason}
                onChange={(event) => setReason(event.target.value)}
                className={box + ' mt-1'}
              />
            </div>
          )}
        </Field>

        <Field field="period_from" label="Period from">
          <input
            data-from
            type="date"
            value={from}
            onChange={(event) => setFrom(event.target.value)}
            className={box}
          />
        </Field>

        <Field field="period_to" label="to">
          <input
            data-to
            type="date"
            value={to}
            onChange={(event) => setTo(event.target.value)}
            className={box}
          />
        </Field>

        <Field
          field="days"
          label="No. of days"
          hint="As the form states it. A half day is a fraction — 0.5 (SPEC §9 A15)."
          after={
            count && (
              <p
                data-count-note
                className={
                  'mt-2 rounded px-3 py-2 text-sm ' +
                  (count.counts_differ
                    ? 'bg-amber-50 text-amber-900'
                    : 'text-slate-500')
                }
              >
                {count.note}
              </p>
            )
          }
        >
          <input
            data-days
            type="text"
            inputMode="decimal"
            autoComplete="off"
            value={days}
            onChange={(event) => setDays(event.target.value)}
            placeholder="1"
            className={box + ' w-32'}
          />
        </Field>
      </div>

      {/* **Not on the form.** The legend code is what HR writes on the sheet,
          and §6 keeps the two vocabularies apart: either field may be empty,
          and filling one in from the other would invent a mapping the paper
          does not contain. */}
      <div className="mt-6 rounded-lg border border-slate-200 bg-white px-5 py-4">
        <h2 className="font-semibold text-slate-900">
          Written on the sheet — not on the form
        </h2>
        <p className="mt-1 text-sm text-slate-600">
          The code that goes in the cell. A separate field: a type with no code
          and a code with no type both save.
        </p>
        <select
          data-sheet-code
          value={code}
          onChange={(event) => {
            codeTouched.current = true
            setCode(event.target.value)
          }}
          className={box + ' mt-3 max-w-md'}
        >
          <option value="">— no sheet code</option>
          {screen.codes.map((one) => (
            <option key={one.code} value={one.code}>
              {one.code} — {one.label}
            </option>
          ))}
        </select>
        {chosen && suggestion && (
          <p data-suggested={suggestion} className="mt-2 text-sm text-slate-600">
            <strong>{suggestion}</strong> is suggested for {chosen.label} (SPEC
            §9 A48). It is an offer, not a mapping — change it or clear it, and
            what is in the box is what is recorded.
          </p>
        )}
        {chosen && !suggestion && (
          <p data-no-suggestion={chosen.code} className="mt-2 text-sm text-slate-600">
            The sheet legend has no code for {chosen.label}, so none is
            suggested. Four of the seven ticks have none (SPEC §6).
          </p>
        )}
      </div>

      <button
        data-save
        disabled={saving || !number.trim() || !from || !to || !days.trim()}
        onClick={save}
        className="mt-6 rounded-lg bg-slate-900 px-6 py-3 font-semibold text-white disabled:bg-slate-300"
      >
        {saving ? 'Saving…' : 'Save this form'}
      </button>

      <div data-not-here className="mt-6 text-sm text-slate-600">
        <h2 className="font-semibold text-slate-900">Not on this screen</h2>
        <ul className="mt-2 list-disc space-y-1 pl-5">
          {screen.not_on_this_screen.map((line) => (
            <li key={line}>{line}</li>
          ))}
        </ul>
      </div>
    </div>
  )
}
