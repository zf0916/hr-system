// One employee, one period, every day of it — what Accounts reads instead of
// the punch card (SPEC §7).
//
// Everything here arrives finished from `app.detail`, which the `hr sheet
// detail` command also draws. Two things this screen says out loud rather than
// leaving a reader to infer:
//
//   * a leave record's day count is what the form says, and where that differs
//     from the range it covers, both numbers show (SPEC §6);
//   * a lateness figure measured against an unconfirmed schedule is marked,
//     because the punch time is real and the lateness is arithmetic on a guess.
import { useEffect, useState } from 'react'

import { ask, monthOf } from '../api.js'

function Punch({ punch }) {
  return (
    <div className="flex flex-wrap gap-x-3 text-xs text-slate-500">
      <span className="font-mono">{punch.at}</span>
      <span>{punch.source}</span>
      {punch.who && <span>entered by {punch.who}</span>}
      {punch.why && <span>· {punch.why}</span>}
    </div>
  )
}

export default function Detail({ number, search, go }) {
  const month = monthOf(search, new Date().toISOString().slice(0, 7))
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    setData(null)
    setError(null)
    ask(`/api/employees/${encodeURIComponent(number)}/detail?month=${month}`)
      .then(setData)
      .catch((problem) => setError(problem.message))
  }, [number, month])

  if (error) return <p className="text-red-700">{error}</p>
  if (!data) return <p className="text-slate-500">reading…</p>

  return (
    <div>
      <div className="flex flex-wrap items-end gap-4">
        <div>
          <h1 className="text-xl font-semibold text-slate-900">
            <span className="font-mono">{data.employee_number}</span> {data.name}
          </h1>
          <p className="mt-1 text-sm text-slate-600">
            {data.section_code} · {data.role_code} · {data.group_code} ·{' '}
            {data.period_start} to {data.period_end}
          </p>
        </div>
        <input
          type="month"
          value={month}
          onChange={(event) =>
            go(`/employee/${encodeURIComponent(number)}?month=${event.target.value}`)
          }
          className="rounded border border-slate-300 bg-white px-2 py-1 text-sm"
        />
        <a
          href={`/sheet?month=${month}`}
          onClick={(event) => {
            event.preventDefault()
            go(`/sheet?month=${month}`)
          }}
          className="text-sm text-slate-600 underline hover:text-slate-900"
        >
          back to the sheet
        </a>
      </div>

      {data.provisional_days > 0 && (
        <div
          data-provisional-banner
          className="mt-4 border-l-4 border-amber-500 bg-amber-50 px-4 py-3 text-sm text-amber-900"
        >
          <strong>
            {data.provisional_days} lateness figure
            {data.provisional_days === 1 ? '' : 's'} here rest on a schedule HR
            has never confirmed.
          </strong>{' '}
          They are marked <span className="font-mono">p</span>. The punch times
          are real.
        </div>
      )}

      <table className="mt-6 w-full border-collapse bg-white text-sm" data-detail-days>
        <thead>
          <tr className="border-b border-slate-300 text-left text-slate-500">
            <th className="px-3 py-2 font-medium">Date</th>
            <th className="px-3 py-2 font-medium">Day</th>
            <th className="px-3 py-2 font-medium">First in</th>
            <th className="px-3 py-2 font-medium">Last out</th>
            <th className="px-3 py-2 text-right font-medium">Late</th>
            <th className="px-3 py-2 font-medium">Leave</th>
            <th className="px-3 py-2 text-right font-medium">Punches</th>
            <th className="px-3 py-2 font-medium">Notes</th>
          </tr>
        </thead>
        <tbody>
          {data.days.map((day) => (
            <tr
              key={day.date}
              data-day={day.date}
              className={
                'border-b border-slate-100 align-top ' +
                (day.is_rest_day || day.holiday_closes ? 'bg-slate-100' : '')
              }
            >
              <td className="px-3 py-1.5 font-mono">{day.date}</td>
              <td className="px-3 py-1.5 text-slate-500">{day.weekday}</td>
              <td className="px-3 py-1.5 font-mono" data-first-in>
                {day.first_in || ''}
                {day.first_in_manual ? '*' : ''}
              </td>
              <td className="px-3 py-1.5 font-mono" data-last-out>
                {day.last_out || ''}
                {day.last_out_manual ? '*' : ''}
              </td>
              <td className="px-3 py-1.5 text-right font-mono" data-late>
                {day.late_minutes === null ? '' : day.late_minutes}
                {day.late_minutes !== null && day.provisional ? 'p' : ''}
              </td>
              <td className="px-3 py-1.5 font-semibold text-indigo-700" data-leave>
                {day.leave_code ||
                  (day.leave_record_id ? (
                    <span className="font-normal text-slate-500">
                      {day.leave_type_label || 'leave'} — no sheet code
                    </span>
                  ) : (
                    ''
                  ))}
              </td>
              <td className="px-3 py-1.5 text-right">{day.has_row ? day.punch_count : ''}</td>
              <td className="px-3 py-1.5 text-slate-500">
                {day.is_rest_day && <span className="mr-2">rest day</span>}
                {day.holiday_name && (
                  <span className="mr-2">
                    {day.holiday_name}
                    {day.holiday_closes ? '' : ' (worked)'}
                  </span>
                )}
                {day.punches
                  .filter((punch) => punch.counted)
                  .map((punch, index) => (
                    <Punch key={index} punch={punch} />
                  ))}
                {day.duplicate_pushes > 0 && (
                  // The copies are kept and counted, and listing 237 of them
                  // would bury the day they belong to. The number is here and
                  // `hr sheet detail --punches` prints every one.
                  <span className="text-xs text-slate-400">
                    {day.duplicate_pushes} re-pushed cop
                    {day.duplicate_pushes === 1 ? 'y' : 'ies'} dropped, not listed
                  </span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <section className="mt-6 grid gap-6 md:grid-cols-2">
        <div>
          <h2 className="font-semibold text-slate-900">Leave covering this period</h2>
          {data.leave.length === 0 ? (
            <p className="mt-2 text-sm text-slate-500">none recorded</p>
          ) : (
            <ul className="mt-2 space-y-3 text-sm">
              {data.leave.map((line) => (
                <li key={line.record_id} data-leave-record={line.record_id} className="border-b border-slate-100 pb-3">
                  <div>
                    <span className="font-mono">{line.period_from}</span> to{' '}
                    <span className="font-mono">{line.period_to}</span> ·{' '}
                    {line.type_label || line.type_code || 'no type stated'}
                    {line.sheet_code ? ` · code ${line.sheet_code}` : ' · no sheet code'}
                  </div>
                  <div className="mt-1 text-slate-600">
                    <strong data-days-stated>{line.days_stated}</strong> day
                    {line.days_stated === '1' ? '' : 's'}, as the form states
                    {line.counts_differ && (
                      <span data-days-differ>
                        {' '}
                        — over a {line.days_spanned}-day range. The form’s
                        number is the one that counts; nothing here recomputes
                        it (SPEC §6).
                      </span>
                    )}
                  </div>
                  {line.reason && <div className="mt-1 text-slate-500">{line.reason}</div>}
                  <div className="mt-1 text-xs text-slate-400">
                    typed by {line.entered_by}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>
        <div className="text-sm text-slate-600">
          <h2 className="font-semibold text-slate-900">Reading this</h2>
          <p className="mt-2">
            <span className="font-mono">*</span> marks a punch a person entered
            rather than the device (SPEC §3). A blank is no punch, which is a
            fact and never an absence — nothing on this screen says anybody was
            absent. <span className="font-mono">p</span> marks a lateness figure
            measured against a provisional schedule row.
          </p>
          <p className="mt-2">
            {data.manual_days > 0
              ? `${data.manual_days} day(s) here carry a punch somebody entered by hand.`
              : 'Every punch here came from the device.'}
          </p>
        </div>
      </section>
    </div>
  )
}
