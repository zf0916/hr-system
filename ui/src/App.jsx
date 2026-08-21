// Piece 1: the serving shape, and nothing else.
//
// This page exists to prove the plumbing: the HR interface is served on its own
// port, it can reach its own API, and **the device routes are not here** — a
// request for /iclock/ on this port is a 404, which is what lets a Tailscale
// tunnel point at this port and never at the receiver (SPEC §14).
import { useEffect, useState } from 'react'

function Row({ label, children }) {
  return (
    <div className="flex gap-3 py-1.5 border-b border-slate-200 last:border-0">
      <div className="w-44 shrink-0 text-slate-500">{label}</div>
      <div className="font-medium text-slate-900">{children}</div>
    </div>
  )
}

export default function App() {
  const [health, setHealth] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    fetch('/api/health')
      .then((response) => {
        if (!response.ok) throw new Error(`the API answered ${response.status}`)
        return response.json()
      })
      .then(setHealth)
      .catch((problem) => setError(problem.message))
  }, [])

  return (
    <main className="min-h-screen bg-slate-50 text-slate-800">
      <div className="mx-auto max-w-2xl px-6 py-12">
        <h1 className="text-2xl font-semibold text-slate-900">HR Attendance</h1>
        <p className="mt-1 text-slate-600">
          The HR interface. Nothing is built on it yet — this is the serving
          shape, so that what comes next has somewhere to stand.
        </p>

        <section className="mt-8 rounded-lg border border-slate-200 bg-white p-5">
          <h2 className="mb-3 font-semibold text-slate-900">This port</h2>
          {error && (
            <p className="text-red-700">
              The API did not answer: {error}
            </p>
          )}
          {health && (
            <div className="text-sm">
              <Row label="service">{health.service}</Row>
              <Row label="serves">{health.serves}</Row>
              <Row label="device routes">{health.device_routes}</Row>
              <Row label="database">{health.database}</Row>
            </div>
          )}
          {!health && !error && <p className="text-slate-500">asking…</p>}
        </section>

        <section className="mt-6 rounded-lg border border-slate-200 bg-white p-5 text-sm text-slate-600">
          <h2 className="mb-2 font-semibold text-slate-900">Why two ports</h2>
          <p>
            The device pushes punches at the receiver on its own port and must
            never be reachable through a tunnel. This port carries the HR
            interface and nothing else, so Tailscale can reach it without the
            device routes coming with it. There is no login here before
            Milestone 5: access control is network position, exactly as it is
            for the device routes.
          </p>
        </section>
      </div>
    </main>
  )
}
