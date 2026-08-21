// Who was on the assignment rows on a date, and what they were then.
//
// **Not "the employees".** An assignment is effective-dated, so this screen
// always answers about a day: somebody who left in June is absent from an
// August roster and present in a May one, with nothing edited (SPEC §2).
import { useEffect, useState } from 'react'

import { ask } from '../api.js'

export default function Employees({ search, go }) {
  const on = new URLSearchParams(search).get('on') || ''
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    setData(null)
    setError(null)
    ask(`/api/employees${on ? `?on=${on}` : ''}`).then(setData).catch((problem) => setError(problem.message))
  }, [on])

  if (error) return <p className="text-red-700">{error}</p>
  if (!data) return <p className="text-slate-500">asking…</p>

  return (
    <div>
      <h1 className="text-xl font-semibold text-slate-900">Employees</h1>
      <p className="mt-1 text-sm text-slate-600">
        As the assignment rows stood on{' '}
        <input
          type="date"
          value={data.on_date}
          onChange={(event) => go(`/?on=${event.target.value}`)}
          className="rounded border border-slate-300 bg-white px-2 py-0.5"
        />
        . {data.headcount} of {data.employees_on_file} on file.
      </p>

      {data.not_enrolled > 0 && (
        <p className="mt-4 rounded border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-900">
          <strong>{data.not_enrolled}</strong> of these have no PIN mapped to
          them. A punch from the device cannot reach an employee with no
          mapping, however well the device is working — their sheet row stays
          blank and nothing says why.
        </p>
      )}

      {/* The header stays put while the list scrolls. Fifty-eight rows is
          already more than a screen, and a column of bare numbers with no
          heading above it is a column somebody has to scroll back up to read. */}
      <table className="mt-6 w-full border-separate border-spacing-0 bg-white text-sm">
        <thead>
          <tr className="text-left text-slate-500">
            {['No.', 'Name', 'Section', 'Role', 'Group', 'Device PIN',
              'Employed'].map((heading) => (
              <th
                key={heading}
                data-column-heading
                className="sticky top-0 z-10 border-b border-slate-300 bg-white px-3 py-2 font-medium"
              >
                {heading}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {data.people.map((person) => (
            <tr
              key={person.employee_id}
              data-employee={person.employee_number}
              className="cursor-pointer hover:bg-slate-100"
              onClick={() => go(`/employee/${encodeURIComponent(person.employee_number)}`)}
            >
              <td className="border-b border-slate-100 px-3 py-1.5 font-mono">{person.employee_number}</td>
              <td className="border-b border-slate-100 px-3 py-1.5">{person.name}</td>
              <td className="border-b border-slate-100 px-3 py-1.5 text-slate-600">{person.section_code}</td>
              <td className="border-b border-slate-100 px-3 py-1.5 text-slate-600">{person.role_code}</td>
              <td className="border-b border-slate-100 px-3 py-1.5 text-slate-600">{person.group_code}</td>
              <td className="border-b border-slate-100 px-3 py-1.5 font-mono">
                {person.enrolled ? (
                  person.pins.join(', ')
                ) : (
                  <span className="font-sans text-amber-700">not enrolled</span>
                )}
              </td>
              <td className="border-b border-slate-100 px-3 py-1.5 text-slate-600">
                {person.active_from}
                {person.left_on ? ` → ${person.left_on}` : ''}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {data.people.length === 0 && (
        <p className="mt-6 text-slate-500">
          No assignment row covers {data.on_date}.
        </p>
      )}
    </div>
  )
}
