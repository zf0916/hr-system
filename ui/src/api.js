// Asking the API, and nothing else.
//
// **The browser computes nothing about attendance.** Every figure on every
// screen arrives finished from `app/screens.py`, which is a projection of the
// same render the Excel file is drawn from (SPEC §7). If this file grew a
// function that worked out a total or decided what a cell says, the screen and
// the filed record could start to disagree, which is the one thing the sheet
// is not allowed to do.

export async function ask(path) {
  const response = await fetch(path)
  const body = await response.text()
  if (!response.ok) {
    let detail = body
    try {
      detail = JSON.parse(body).detail ?? body
    } catch {
      // not JSON — show whatever came back
    }
    throw new Error(detail || `the API answered ${response.status}`)
  }
  return JSON.parse(body)
}

// The month a screen opens on. A row decides what period a sheet covers
// (sheet.period_rule), so the browser never expands a month into dates — it
// passes the month through and the server answers with the span it used.
export function monthOf(search, fallback) {
  return new URLSearchParams(search).get('month') || fallback
}
