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

// The frozen edges. Every sticky cell needs an opaque background of its own —
// what scrolls underneath would otherwise show through — and a border drawn on
// the cell rather than collapsed between cells, because a collapsed border
// belongs to neither and scrolls away with the wrong one.
// The four frozen columns, in pixels, because a sticky offset has to be the
// exact width of what is to its left. A class that is *nearly* the column's
// width pins the column over its neighbour instead of beside it.
//
// **Section is one of them, and it is the file's third column.** The rows are
// ordered by section — that is how HR reads the sheet — and the screen used to
// show the order with nothing on it explaining the order. §7 says the screen
// carries everything the file carries except how it prints, and a column is
// not how it prints.
const NUMBER_W = 72
const NAME_W = 208
const SECTION_W = 128
const ROLE_W = 168
const FROZEN_LEFT = [0, NUMBER_W, NUMBER_W + NAME_W,
                     NUMBER_W + NAME_W + SECTION_W]
const FROZEN_WIDTH = NUMBER_W + NAME_W + SECTION_W + ROLE_W

const EDGE = 'border-b border-slate-200 '
const FROZEN_HEAD =
  'sticky top-0 z-40 h-8 bg-white font-medium border-r border-slate-300 ' + EDGE
const FROZEN_SECOND =
  'sticky top-8 z-40 h-8 bg-white border-r border-slate-300 ' + EDGE
const FROZEN_CELL =
  'sticky z-20 whitespace-nowrap bg-white px-2 py-1 border-r border-slate-300 ' + EDGE
const DAY_HEAD =
  'sticky z-30 h-8 border-r border-slate-200 px-1 text-center font-medium ' + EDGE

function Cell({ cell, shaded }) {
  if (!cell) return <td className="border-b border-r border-slate-200" />

  // **One background, chosen in the file's order.** A shaded column shades the
  // whole column, cells included, the way `to_excel` fills it — a header-only
  // stripe would say the day was closed at the top and say nothing at the row
  // a reader is actually looking along.
  const background = shaded
    ? 'bg-slate-300'
    : cell.manual
      ? 'bg-amber-100'
      : 'bg-white'

  const classes = ['border-b', 'border-r', 'border-slate-200', 'text-center',
                   'whitespace-nowrap', 'px-1', background]
  if (cell.kind === 'leave') classes.push('font-semibold text-indigo-700')
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
      data-shaded={shaded ? 'yes' : 'no'}
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

  // Each column as wide as the widest thing in it, which is the rule `to_text`
  // follows for a terminal. One width for all of them would either clip the
  // days that carry both an arrival and a departure — and clipping a time is
  // losing it — or make a month of ticks four screens wide.
  const columnWidth = (column) =>
    Math.max(
      30,
      10 + 7 * Math.max(2, ...data.rows.map(
        (row) => (data.cells[`${row.employee_id}:${column.date}`]?.text ?? '').length)),
    )
  const dayWidths = data.columns.map(columnWidth)
  const gridWidth = FROZEN_WIDTH + dayWidths.reduce((total, w) => total + w, 0)

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

      {/* The grid scrolls inside itself, in both directions. Reaching the
          horizontal scrollbar must not mean scrolling past every employee
          first — and the four identifying columns and the two date rows stay
          put while the rest moves, which is exactly what the file does with
          freeze panes and repeating print titles (SPEC §7). */}
      <div
        data-grid-scroll
        className="mt-6 max-h-[72vh] overflow-auto border border-slate-300 bg-white"
      >
        <table
          className="table-fixed border-separate border-spacing-0 text-xs"
          style={{ minWidth: gridWidth }}
          data-sheet-grid
        >
          <colgroup>
            <col style={{ width: NUMBER_W }} />
            <col style={{ width: NAME_W }} />
            <col style={{ width: SECTION_W }} />
            <col style={{ width: ROLE_W }} />
            {data.columns.map((column, index) => (
              <col key={column.date} style={{ width: dayWidths[index] }} />
            ))}
          </colgroup>
          <thead>
            <tr>
              {['No.', 'Name', 'Section', 'Role'].map((heading, index) => (
                <th
                  key={heading}
                  data-frozen={index}
                  data-identifying={index}
                  className={FROZEN_HEAD + ' px-2 text-left'}
                  style={{ left: FROZEN_LEFT[index] }}
                >
                  {heading}
                </th>
              ))}
              {data.columns.map((column) => (
                <th
                  key={column.date}
                  title={column.shade_reason || column.date}
                  data-day-number={column.date}
                  className={
                    DAY_HEAD + ' top-0 ' +
                    (column.shaded ? 'bg-slate-300' : 'bg-white')
                  }
                >
                  {column.day}
                </th>
              ))}
            </tr>
            <tr>
              {FROZEN_LEFT.map((left, index) => (
                <th key={left} data-frozen={index} className={FROZEN_SECOND}
                    style={{ left }} />
              ))}
              {data.columns.map((column) => (
                <th
                  key={column.date}
                  data-weekday={column.date}
                  className={
                    DAY_HEAD + ' top-8 font-normal text-slate-500 ' +
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
              <tr key={row.employee_id}>
                <th
                  data-frozen="0"
                  className={FROZEN_CELL + ' text-left font-mono font-normal'}
                  style={{ left: FROZEN_LEFT[0] }}
                >
                  <a
                    href={`/employee/${encodeURIComponent(row.employee_number)}?month=${month}`}
                    className="text-slate-900 hover:underline"
                  >
                    {row.employee_number}
                  </a>
                </th>
                <td data-frozen="1" className={FROZEN_CELL + ' truncate'}
                    title={row.name} style={{ left: FROZEN_LEFT[1] }}>
                  {row.name}
                </td>
                <td data-frozen="2" data-row-section={row.employee_number}
                    className={FROZEN_CELL + ' truncate text-slate-600'}
                    title={row.section_code} style={{ left: FROZEN_LEFT[2] }}>
                  {row.section_code}
                </td>
                <td data-frozen="3" className={FROZEN_CELL + ' truncate text-slate-500'}
                    title={row.role_code} style={{ left: FROZEN_LEFT[3] }}>
                  {row.role_code}
                </td>
                {data.columns.map((column) => (
                  <Cell
                    key={column.date}
                    cell={cellFor(row, column)}
                    shaded={column.shaded}
                  />
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* **What every mark above was measured against.** A tick means
          "inside these times"; a lateness figure is measured from this start
          plus this grace; a punch belongs to this day because it fell inside
          this window. Those are §9's A1, A2, A4, A30 and A31 — rows, all of
          them provisional, and until now visible only in their consequences.
          It is under the grid rather than on a screen of its own because it is
          read against the grid, and it is on the filed file too (SPEC §7). */}
      <section data-schedule-block className="mt-6">
        <h2 className="font-semibold text-slate-900">
          The schedule every mark above was measured against
        </h2>
        {data.schedules.length === 0 ? (
          <p className="mt-2 text-sm text-slate-500">
            No group on this sheet has a schedule row in force in this period.
          </p>
        ) : (
          <div className="mt-2 overflow-x-auto border border-slate-200 bg-white">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-slate-300 text-left text-slate-500">
                  {['Group', 'Shift', 'Break', 'Grace', 'Rest day',
                    'A punch belongs to this day within', 'Row'].map((h) => (
                    <th key={h} className="px-3 py-2 font-medium">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {data.schedules.map((line) => (
                  <tr
                    key={`${line.group_code}:${line.schedule_id}`}
                    data-schedule={`${line.group_code}:${line.schedule_id}`}
                    className="border-b border-slate-100 align-top"
                  >
                    <td className="px-3 py-1.5 font-mono">{line.group_code}</td>
                    <td className="px-3 py-1.5 font-mono" data-shift>{line.shift}</td>
                    <td className="px-3 py-1.5 font-mono" data-break>{line.break}</td>
                    <td className="px-3 py-1.5 font-mono" data-grace>
                      {line.grace_minutes} min
                    </td>
                    <td className="px-3 py-1.5">{line.rest_days}</td>
                    <td className="px-3 py-1.5" data-window>
                      {line.attendance_day_window}
                    </td>
                    <td className="px-3 py-1.5 text-slate-500">
                      #{line.schedule_id}, from {line.effective_from}
                      {line.effective_to ? ` to ${line.effective_to}` : ''} ·{' '}
                      {line.days_here} day(s) here
                      {line.provisional && (
                        <span
                          data-schedule-provisional={line.schedule_id}
                          className="ml-2 rounded bg-amber-100 px-1.5 py-0.5 text-xs font-semibold text-amber-900"
                        >
                          PROVISIONAL — never confirmed by HR
                        </span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <p className="mt-2 text-sm text-slate-600">
          Every value here is a row (SPEC §9 A1, A2, A4, A30, A31). Correcting
          one is an update and a rebuild, not a code change — and the same
          block is on the downloaded file.
        </p>
      </section>

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
