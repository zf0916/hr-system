// The HR interface's screens, and the guard's.
//
// Piece 2's three read-only screens and the download, piece 3's guard screen,
// piece 4's leave entry. Every one of them is a face on a function that
// already exists. **The read-only screens still write nothing**; entry arrives
// one screen at a time, each with the record it writes — gate passes and HR
// corrections are pieces 5 and 6.
//
// Routing is done here rather than by a library: a handful of paths do not
// need a dependency, and the server already falls back to this page for any path that
// is not an API or a device route (SPEC §14).
import { useEffect, useState } from 'react'

import Employees from './screens/Employees.jsx'
import Sheet from './screens/Sheet.jsx'
import Detail from './screens/Detail.jsx'
import Guard from './screens/Guard.jsx'
import LeaveEntry from './screens/LeaveEntry.jsx'

function useRoute() {
  const [route, setRoute] = useState(() => window.location.pathname + window.location.search)

  useEffect(() => {
    const onPop = () => setRoute(window.location.pathname + window.location.search)
    window.addEventListener('popstate', onPop)
    return () => window.removeEventListener('popstate', onPop)
  }, [])

  const go = (to) => {
    window.history.pushState({}, '', to)
    setRoute(to)
  }
  return [route, go]
}

function NavLink({ to, current, go, children }) {
  const active = current.split('?')[0] === to.split('?')[0]
  return (
    <a
      href={to}
      onClick={(event) => {
        event.preventDefault()
        go(to)
      }}
      className={
        'rounded px-3 py-1.5 text-sm ' +
        (active
          ? 'bg-slate-900 text-white'
          : 'text-slate-600 hover:bg-slate-200 hover:text-slate-900')
      }
    >
      {children}
    </a>
  )
}

export default function App() {
  const [route, go] = useRoute()
  const [path, search] = [route.split('?')[0], route.includes('?') ? `?${route.split('?')[1]}` : '']

  // The guard's screen is not part of the HR interface and does not wear its
  // chrome: a different person, a different place, a phone rather than a desk.
  // It shares the port because it shares the LAN (SPEC §14).
  if (path === '/guard') {
    return <Guard search={search} go={go} />
  }

  let screen
  if (path === '/leave') {
    screen = <LeaveEntry go={go} />
  } else if (path === '/sheet') {
    screen = <Sheet search={search} go={go} />
  } else if (path.startsWith('/employee/')) {
    screen = <Detail number={decodeURIComponent(path.slice('/employee/'.length))} search={search} go={go} />
  } else {
    screen = <Employees search={search} go={go} />
  }

  return (
    <div className="min-h-screen bg-slate-50 text-slate-800">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-[110rem] flex-wrap items-center gap-4 px-6 py-3">
          <span className="font-semibold text-slate-900">HR Attendance</span>
          <nav className="flex gap-1">
            <NavLink to="/" current={route} go={go}>
              Employees
            </NavLink>
            <NavLink to="/sheet" current={route} go={go}>
              The sheet
            </NavLink>
            <NavLink to="/leave" current={route} go={go}>
              Leave entry
            </NavLink>
          </nav>
          <span className="ml-auto text-xs text-slate-400">
            {path === '/leave'
              ? 'leave entry writes what HR types off a signed form'
              : 'read only — nothing on these screens writes anything'}
          </span>
        </div>
      </header>
      <main className="mx-auto max-w-[110rem] px-6 py-8">{screen}</main>
    </div>
  )
}
