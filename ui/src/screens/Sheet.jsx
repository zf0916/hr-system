// The Daily Workers Attendance sheet — §7's "screen, which is the system".
//
// **Every cell here is a string the server already decided.** The tick, the
// out-of-schedule time, the leave code, the asterisk on a manual punch, and
// whether a column is shaded all arrive on the cell from `app.sheet.render` —
// the same object the Excel file is drawn from. This file chooses colours and
// borders and nothing else, which is why the screen and the filed record
// cannot disagree about what a day says (SPEC §7).
import { useEffect, useState } from 'react'

import { ask, monthOf } from '../api.js'

function Cell({ cell }) {
  if (!cell) return <td className="border border-slate-200" />

  const classes = ['border', 'border-slate-200', 'text-center', 'whitespace-nowrap', 'px-1']
  if (cell.kind === 'leave') classes.push('font-semibold text-indigo-700')
  if (cell.manual) classes.push('bg-amber-100')
  // A tick or a time is a claim about a schedule. When that schedule is one HR
  // has never confirmed, the claim is marked where it is made — a banner alone
  // lets a reader take one number off the screen and forget the banner.
  if (cell.provisional && (cell.kind === 'tick' || cell.kind === 'time')) {
    classes.push('underline decoration-dotted decoration-amber-500 underline-offset-4')
  }

  return (
    <td
      className={classes.join(' ')}
      data-cell={`${cell.key}`}
      data-kind={cell.kind}
      title={cell.detail}
    >
      {cell.text}
    </td>
  )
}

export default function Sheet({ search, go }) {
  const month = monthOf(search, new Date().toISOString().slice(0, 7))
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    setData(null)
    setError(null)
    ask(`/api/sheet?month=${month}`).then(setData).catch((problem) => setError(problem.message))
  }, [month])

  if (error) return <p className="text-red-700">{error}</p>
  if (!data) return <p className="text-slate-500">rendering…</p>

  const cellFor = (row, column) => {
    const key = `${row.employee_id}:${column.date}`
    const cell = data.cells[key]
    return cell ? { ...cell, key } : null
  }

  return (
    <div>
      <div className="flex flex-wrap items-end gap-4">
        <div>
          <h1 className="text-xl font-semibold text-slate-900">{data.title}</h1>
          <p className="mt-1 text-sm text-slate-600">
            {data.period_start} to {data.period_end} · headcount {data.headcount}
          </p>
        </div>
        <input
          type="month"
          value={month}
          onChange={(event) => go(`/sheet?month=${event.target.value}`)}
          className="rounded border border-slate-300 bg-white px-2 py-1 text-sm"
        />
        <a
          href={`/api/sheet.xlsx?month=${month}`}
          data-download
          className="rounded bg-slate-900 px-3 py-1.5 text-sm text-white hover:bg-slate-700"
        >
          Download the Excel sheet
        </a>
      </div>

      <p className="mt-3 text-sm">
        {data.note_is_unread ? (
          <span className="rounded bg-rose-50 px-2 py-1 text-rose-800" data-note-unread>
            The note in the sheet’s top-left corner has never been read, so
            nothing is written there (SPEC §9 A41).
          </span>
        ) : (
          <span className="text-slate-700">Note: {data.note_top_left}</span>
        )}
      </p>

      {data.provisional_cells > 0 && (
        <div
          data-provisional-banner
          className="mt-4 border-l-4 border-amber-500 bg-amber-50 px-4 py-3 text-sm text-amber-900"
        >
          <strong>
            {data.provisional_cells} cell{data.provisional_cells === 1 ? '' : 's'} on this
            sheet rest on a schedule HR has never confirmed.
          </strong>{' '}
          Every tick means “on schedule” and every time means “outside it”, both
          measured against provisional rows. The punch times are real; whether
          they are late is arithmetic on a guess. Those cells are underlined.
        </div>
      )}

      <div className="mt-6 overflow-x-auto border border-slate-300 bg-white">
        <table className="border-collapse text-xs" data-sheet-grid>
          <thead>
            <tr>
              <th className="sticky left-0 z-10 bg-white px-2 py-1 text-left">No.</th>
              <th className="bg-white px-2 py-1 text-left">Name</th>
              <th className="bg-white px-2 py-1 text-left">Role</th>
              {data.columns.map((column) => (
                <th
                  key={column.date}
                  title={column.shade_reason || column.date}
                  className={
                    'border border-slate-200 px-1 text-center font-medium ' +
                    (column.shaded ? 'bg-slate-300' : 'bg-white')
                  }
                >
                  {column.day}
                </th>
              ))}
            </tr>
            <tr>
              <th className="sticky left-0 z-10 bg-white" />
              <th className="bg-white" />
              <th className="bg-white" />
              {data.columns.map((column) => (
                <th
                  key={column.date}
                  data-weekday={column.date}
                  className={
                    'border border-slate-200 px-1 text-center font-normal text-slate-500 ' +
                    (column.shaded ? 'bg-slate-300' : 'bg-white')
                  }
                >
                  {column.weekday.slice(0, 2)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {data.rows.map((row) => (
              <tr key={row.employee_id} className="hover:bg-slate-50">
                <th className="sticky left-0 z-10 bg-white px-2 py-1 text-left font-mono font-normal">
                  <a
                    href={`/employee/${encodeURIComponent(row.employee_number)}?month=${month}`}
                    className="text-slate-900 hover:underline"
                  >
                    {row.employee_number}
                  </a>
                </th>
                <td className="whitespace-nowrap bg-white px-2 py-1">{row.name}</td>
                <td className="whitespace-nowrap bg-white px-2 py-1 text-slate-500">
                  {row.role_code}
                </td>
                {data.columns.map((column) => (
                  <Cell key={column.date} cell={cellFor(row, column)} />
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <section className="mt-6 grid gap-6 md:grid-cols-2">
        <div>
          <h2 className="font-semibold text-slate-900">Legend</h2>
          <dl className="mt-2 text-sm">
            {data.legend.map((entry) => (
              <div key={entry.code + entry.label} className="flex gap-3 border-b border-slate-100 py-1">
                <dt className="w-20 shrink-0 font-mono">{entry.code}</dt>
                <dd className="text-slate-600">{entry.label}</dd>
              </div>
            ))}
          </dl>
        </div>
        <div>
          <h2 className="font-semibold text-slate-900">Notes</h2>
          <ul className="mt-2 space-y-2 text-sm text-slate-600">
            {data.notes.map((note) => (
              <li key={note} data-note>
                {note}
              </li>
            ))}
            {data.notes.length === 0 && <li className="text-slate-400">none</li>}
          </ul>

          <h2 className="mt-6 font-semibold text-slate-900">
            What the downloaded file carries and this screen does not
          </h2>
          <p className="mt-2 text-sm text-slate-600">
            Only how it prints. The file breaks every {data.rows_per_page} rows
            into {data.page_count} page{data.page_count === 1 ? '' : 's'},
            repeats the day-number and weekday rows on each of them, prints
            landscape fitted to one page wide, and starts the legend on its own
            page. This screen scrolls instead. <strong>Every mark is the
            same</strong> — the cells, the shading, the asterisks, the legend
            and these notes all come off one render (SPEC §7).
          </p>
        </div>
      </section>
    </div>
  )
}
