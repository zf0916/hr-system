// Step 10, piece 5: gate pass entry. HR types a pass that has already been
// signed on paper (SPEC §5), out and in times included, and this screen
// computes nothing.
//
// **The fields are in the paper's order**: name / no. pekerja, emp no., date,
// out time, in time, one tick of four, reason, destination. The order comes
// from the server — `gate_pass_entry.FORM_ORDER` — and the page is checked
// against it.
//
// **There is no hours box anywhere on this page** and none in what it sends.
// The hours are not written on the gate pass: the two times are, and the
// database generates the hours from the pair. They appear once the pass is
// saved, read back from the stored column. **This is the reverse of leave**,
// where the number of days is typed and never recomputed.
//
// **There is no department box either.** §5's form has no line for one, so the
// section is looked up and shown beside the name, and nothing writes it.
//
// **The two times are HR's, off the paper.** That is not the guard entry
// screen, which stands in for a punch the device did not take and has no time
// field at all — the page says so beside the boxes, because the two acts look
// alike and only this one has somewhere to type a time.
import { useEffect, useState } from 'react'

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

// One line of the paper form. The order these appear in is checked against the
// server's list, not against this file.
function Field({ field, label, children, hint, after }) {
  return (
    <div data-form-field={field} className="border-b border-slate-100 py-3 sm:flex sm:gap-4">
      <label className="block w-56 shrink-0 pt-1.5 text-sm font-medium text-slate-700">
        {label}
      </label>
      <div className="mt-1 min-w-0 flex-1 sm:mt-0">
        {children}
        {hint && <p className="mt-1 text-xs text-slate-500">{hint}</p>}
        {after}
      </div>
    </div>
  )
}

const box = 'w-full rounded border border-slate-300 px-3 py-1.5'

export default function GatePassEntry({ go }) {
  const [screen, setScreen] = useState(null)
  const [error, setError] = useState(null)
  const [typedBy, setTypedBy] = useState(
    () => window.localStorage.getItem(REMEMBERED) || '')

  const [number, setNumber] = useState('')
  const [person, setPerson] = useState(null)
  const [date, setDate] = useState('')
  const [out, setOut] = useState('')
  const [back, setBack] = useState('')
  const [category, setCategory] = useState('')
  const [reason, setReason] = useState('')
  const [destination, setDestination] = useState('')
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(null)

  useEffect(() => {
    ask('/api/gatepass/screen').then(setScreen).catch((p) => setError(p.message))
  }, [])

  // The name behind the number, and the section — looked up, never typed.
  useEffect(() => {
    if (!number.trim()) {
      setPerson(null)
      return
    }
    let dropped = false
    ask(`/api/gatepass/employee/${encodeURIComponent(number.trim())}`
        + (date ? `?on=${date}` : ''))
      .then((found) => !dropped && setPerson(found))
      .catch(() => !dropped && setPerson(null))
    return () => {
      dropped = true
    }
  }, [number, date])

  const typist = screen?.typists.find((one) => one.code === typedBy)

  const save = async () => {
    setSaving(true)
    setError(null)
    try {
      // Eight fields, and not one of them is an hours figure or a department.
      const result = await send('/api/gatepass/entry', {
        entered_by: typedBy,
        employee_number: number.trim(),
        pass_date: date,
        out_time: out,
        in_time: back,
        category_code: category,
        reason: reason.trim() || null,
        destination: destination.trim() || null,
      })
      setSaved(result)
      setNumber('')
      setPerson(null)
      setDate('')
      setOut('')
      setBack('')
      setCategory('')
      setReason('')
      setDestination('')
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
        <h1 className="text-xl font-semibold text-slate-900">Gate pass entry</h1>
        <p className="mt-1 text-sm text-slate-600">
          Who is typing? Every gate pass says who entered it (SPEC §5). This
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
        <h1 className="text-xl font-semibold text-slate-900">Gate pass entry</h1>
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
            Recorded — {saved.name} ({saved.employee_number}) on{' '}
            {saved.pass_date}, {saved.out_time} to {saved.in_time} ={' '}
            <span data-saved-hours>{saved.hours}</span> hours
          </p>
          <p className="mt-1 text-sm text-emerald-900">
            {saved.category_label}
            {saved.destination ? ` · ${saved.destination}` : ''} · section{' '}
            {saved.section || '—'}, looked up · typed by {saved.entered_by}
          </p>
          <p className="mt-2 text-sm text-emerald-900">{saved.derived}</p>
          <button
            data-see-summary
            onClick={() => go(`/employee/${encodeURIComponent(saved.employee_number)}?month=${saved.month}`)}
            className="mt-2 text-sm text-emerald-900 underline"
          >
            See {saved.employee_number}’s {saved.month} in detail
          </button>
        </div>
      )}

      <div className="mt-6 rounded-lg border border-slate-200 bg-white px-5 py-2">
        <Field
          field="name"
          label="Name / no. pekerja"
          hint={person ? person.section_note : undefined}
        >
          <p data-employee-name className="pt-1.5 text-lg text-slate-900">
            {person ? person.name || '(no name on the assignment rows)' : '—'}
          </p>
          {person && (
            // **Not a field.** The section is shown so the person typing can
            // see who they have; §5's form has no department line and the
            // record has no column for one.
            <p data-section className="text-sm text-slate-500">
              section {person.section || '—'}, looked up — not on this form
            </p>
          )}
        </Field>

        <Field
          field="emp_no"
          label="Emp no."
          hint="A field of its own on the paper, beside the name line (SPEC §5)."
        >
          <input
            data-emp-no
            type="text"
            inputMode="numeric"
            autoComplete="off"
            value={number}
            onChange={(event) => setNumber(event.target.value)}
            placeholder="four digits"
            className={box + ' font-mono'}
          />
        </Field>

        <Field field="date" label="Date">
          <input
            data-date
            type="date"
            value={date}
            onChange={(event) => setDate(event.target.value)}
            className={box}
          />
        </Field>

        <Field field="out_time" label="Out time">
          <input
            data-out
            type="time"
            value={out}
            onChange={(event) => setOut(event.target.value)}
            className={box + ' max-w-40'}
          />
        </Field>

        <Field
          field="in_time"
          label="In time"
          after={
            <p data-not-guard className="mt-2 rounded bg-slate-100 px-3 py-2 text-sm text-slate-700">
              These two times are typed by HR off the paper the guard filled in
              at the gate. <strong>This is not the guard entry screen.</strong>{' '}
              That one stands in for a punch the device did not take, is stamped
              by the server, and has no field for a time at all. The two acts
              look alike and only this one has time boxes (SPEC §3, §5).
            </p>
          }
        >
          <input
            data-in
            type="time"
            value={back}
            onChange={(event) => setBack(event.target.value)}
            className={box + ' max-w-40'}
          />
        </Field>

        <Field field="category" label="Category">
          <div className="flex flex-wrap gap-2">
            {screen.categories.map((one) => (
              <button
                key={one.code}
                data-category={one.code}
                onClick={() => setCategory(category === one.code ? '' : one.code)}
                className={
                  'rounded border px-3 py-1.5 text-sm ' +
                  (category === one.code
                    ? 'border-slate-900 bg-slate-900 text-white'
                    : 'border-slate-300 hover:bg-slate-100')
                }
              >
                {one.label}
              </button>
            ))}
          </div>
        </Field>

        <Field field="reason" label="Reason" hint="Why, in words.">
          <input
            data-reason
            type="text"
            value={reason}
            onChange={(event) => setReason(event.target.value)}
            className={box}
          />
        </Field>

        <Field field="destination" label="Destination" hint="Where the employee is going.">
          <input
            data-destination
            type="text"
            value={destination}
            onChange={(event) => setDestination(event.target.value)}
            className={box}
          />
        </Field>
      </div>

      <button
        data-save
        disabled={saving || !number.trim() || !date || !out || !back || !category}
        onClick={save}
        className="mt-6 rounded-lg bg-slate-900 px-6 py-3 font-semibold text-white disabled:bg-slate-300"
      >
        {saving ? 'Saving…' : 'Save this pass'}
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
