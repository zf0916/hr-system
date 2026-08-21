# Build

What is done, what is next, what is parked. Replaced in place, never appended to.

---

## The rule

**A decision gets made now only if not making it blocks the step in front of us.** Everything else goes to Parked as one line, with no design attached.

**And one more, specific to this project: build on the assumed values, demonstrate, and let HR correct what they see.** HR is fully occupied with the manual workload and cannot sit for an interview. Working software on a screen gets answers that a question list does not. Every assumed value is a row in a table, so correcting one is an update.

---

## Milestones

|#|Delivers|To|
|---|---|---|
|**1**|HR stops reading punch cards and stops filling the attendance sheet by hand|**HR**|
|**2**|Accounts stops keying from punch cards|Accounts|
|**3**|The late coming and time-off summaries generate instead of being compiled by hand|HR|
|**4**|Permits and expiry dates tracked instead of remembered|HR|
|**5**|Employees apply for leave themselves|Everyone|

Milestone 4 is independent of the rest and can run any time once the privacy question is answered. Its supervisor relationships are a prerequisite for Milestone 5.

**Paper retires one document at a time.** At every stage Accounts keys fewer fields than before and more than none. There is no point where the system produces a complete payroll entry but is not yet in use.

**Milestone 2 is blocked on Accounts, not on code.** Its inputs are Milestone 1's — the same punches, the same daily rows, the same leave entry — so nothing else has to be built first. What gates it is the Accounts questions in Parked: what the payroll codes mean and which are used, whether `Lateness` is the raw total or the deductible portion, whether `Work Hours` is gross or net, whether SQL Account imports a file or is keyed, and who keys `Basic Rate`. **Overtime is the hardest of them, and differently hard:** the others are questions about a field this system can fill, and overtime derives from nothing this system holds. SPEC §8 says why — the device shows time present, which is not approved overtime. Answering it may mean an input path that does not exist yet.

---

## Milestone 1 — steps

|Step|What it delivers|Done when|
|---|---|---|
|**1**|**Capture** — receiver, raw request storage, punch parsing, **and the device simulator that exercises them**|The simulator runs a full push cycle and exits clean|
|**2**|**Employees** — number, name, section, role, group, active and left dates, device PIN|A real employee list loads|
|**3**|**Schedule and calendar** — per group, effective-dated, plus holidays and rest days|A past period renders with the schedule that was in force then, not today's|
|**4**|**Corrections** — guard entry and HR retroactive entry, both marked and counted|A guard entry cannot be given a time; an HR entry can|
|**5**|**HR entry** — leave and gate pass, from the paper forms HR already receives|Codes appear on the generated sheet — **built**|
|**6**|**Daily attendance** — first in, last out, late minutes, status per employee per day|Period totals are queries over it — **built; the rows exist and a total is a query away**|
|**7**|**The sheet** — a screen and an Excel file in HR's existing layout, plus per-day punch detail|HR reads it instead of the punch card, and files the Excel copy (SPEC §7) — **built; leave codes appear, and the browser draws the same render**|
|**8**|**Device control** — command queue, push users, set and clear fallback passwords, pending re-enrollment list|An employee created in the app appears on the device — **the queue is built and carries REBOOT and CHECK; user push waits on the formats being real**|
|**10**|**The screen** — the HR interface and the guard's one screen, in seven pieces|Each piece has its own gate; **pieces 1 to 5 are built** — the serving shape, the read-only screens, the guard screen, leave entry, gate pass entry|
|**9**|**Ingestion alert** — warns when punches stop arriving|Silence for N hours raises a warning — **built; contact silence and punch silence are separate, both rows**|

**Then: demo to HR, walking the assumed values line by line while the software is on screen.**

The only thing actually blocking the build is **the employee list** — number, name, section, role, group. Everything else is assumed and corrected afterwards. The device is not a prerequisite; the simulator stands in for it.

---

## Where it stands

**Step 1 is built.** The receiver (every route in SPEC.md §12), the raw request layer, the punch parsing layer and the device simulator. Steps 3–9 not started.

The simulator runs a full push cycle and exits clean. Its handshake query, option set, punch line and OPERLOG cursor parameter are copied from the second capture. **Deleting the `Realtime` option row made it fail on two checks and exit 1** — the handshake is now gated on the option set the real device accepted, not just on the lines being `Key=Value`. Each of these was also broken on purpose, and the simulator failed on every one: the catch-all route removed, trailing-slash redirects left on, an auth check added, the raw layer deduplicating, the body decoded at capture, FastAPI's own JSON error page reaching the device, and the parser raising on every line. The parser one is the interesting failure — every response stayed `200 OK: {n}` and only the parsed layer went empty, which is the rule §12 asks for.

**Step 2 is built and is not done.** Its done-when is "a real employee list loads", and the list does not exist yet. What exists: the employee model, effective-dated; the device-PIN mapping in its own dated table; an Excel importer driven by an explicit mapping file; a committed fixture spreadsheet; and a gate of 24 deliberate mistakes, all of which the importer refuses. When HR's file arrives, step 2 finishes by writing a mapping file for it — no code change, unless the file carries a field the model has no room for.

Deliberately not in step 2: no employee screen, no push to the device, no leave, no daily attendance.

**Step 3 is built and is not done either.** Its done-when is "a past period renders with the schedule that was in force then" — the gate proves that against provisional data, and HR has confirmed none of it. What exists: schedules per group, effective-dated, with the rest day as a column on the row and the crossed-midnight fact stated on the row; a calendar of one row per date carrying whether the factory actually closes; a holiday importer using the same explicit mapping as the employee list; a committed blank template for HR to fill in; per-date adjustments that survive a re-upload; and the query that answers what applied for a group on a date, including which attendance day a punch belongs to.

**Every schedule and every holiday now in the database is marked provisional.** The 2026 list is still parked. `fixtures/holidays_provisional_2026.xlsx` carries only holidays whose dates are fixed by the calendar — Hari Raya, Chinese New Year, Deepavali, Wesak and Thaipusam are deliberately absent, because a plausible wrong date is worse than a missing one.

Deliberately not in step 3: no late minutes, no daily attendance, no sheet.

**Two more artifacts read, and four assumptions retired.** The individual time-off record settles its own shape — seven ruled lines of date, reason out, from, to, hours, then a total and the employee's signature, with the reason column holding the gate pass category (`PERSONAL` on the specimen). The late coming record settles two things by printing them: its period, `16/12/2025` to `15/01/2026` (A8), and an employee at exactly `0 hours 30 minutes` on the deduction list (A11). **A28 and A29 go too** — four digits zero-padded, keyed to four, numbers reaching about 1500 — and they are now §2's rules rather than §9's guesses.

**The importer used to halt on `090`, and that was wrong.** HR's own paper prints `090` and `1601` on one page, so a number written short is the same number, not a typo: the shape row is `^[0-9]{1,4}$`, `090` loads with no flag and keys to `0090`, and what still halts is a number that cannot be keyed to four — five digits, or something that is not digits. **The cost is a safety net**: a mapping pointed at the running-number column used to fail on the shape and now loads. The header echo is what shows it, which is why the importer prints the header text it never matches on.

**A month of punches, so the sheet can be read.** `tools/make_month_fixture.py` posts a plausible August at the receiver — **the same `/iclock/cdata` route, the same ten-field ATTLOG line, chunked 20 to a push as A34 reads the device's own limit.** Nothing about it is a second code path: the raw layer stores it, the parser reads it, `hr attendance build` derives the rows, and the sheet renders them. It is deterministic on its seed, it adds to whatever is already captured, and **it writes no leave** — step 5 does not exist and no cell can hold a code.

What it produces across the 54 employees who have a PIN: two punches on most working days, late arrivals scattered with a few people crossing the 30-minute accumulated threshold, a handful of early departures, night-shift employees punching out after midnight, some days with one punch, some with none, and **nothing on a Sunday**.

**Two fixtures, and a rule about PINs.** `fixtures/employees_sample.xlsx` is the shape of HR's list. `fixtures/employees_punch_demo.xlsx` is a demonstration list of 58 employees whose PINs match punches actually in the raw layer — `1` for the real device, `0090`/`0657`/`1627` and `0001`–`0050` for the simulator — so the sheet can be seen with data on it. Four of its employees have no Device ID at all, because an empty row is a real case.

**A device PIN with a leading zero is refused on import** (SPEC §2). The device will not hold one (§10), so a mapping row carrying `0142` looks like a working link and silently is not — the employee's punches go unattributed with nothing on screen saying why. The sample fixture carried exactly that and now carries `142`. `--accept-leading-zero-pins` loads one deliberately, which is what the demonstration list needs, because the simulator sends shapes the hardware cannot. **The import report now names every employee's PIN outcome** — mapped, blank cell, or refused — since a count cannot tell a correctly skipped blank from a rejected value. The import gate proves both directions.

**Step 10, piece 1 is built: the serving shape.** Two ports, one codebase, one container. `python -m app.serve` starts both ASGI applications in one process — the receiver on the container's 8000, published as `RECEIVER_PORT` 8081; the HR interface on 8100, published as `HR_PORT` 8090. The interface is React and Tailwind, built by a Vite stage in the Dockerfile; **the runtime image carries no Node**, only `dist`.

**Why two ports and not one application.** SPEC §14 wants the HR interface reachable through a tunnel and the device routes never. One application on one port cannot do that: whatever the tunnel reaches, it reaches all of. So the device routes are simply **not present** on the HR port — `/iclock/` there is a `404`, not a redirect and not the single-page fallback, because a device reading an HTML page as a protocol answer is what §12 says makes firmware retry forever.

`tools/serving_gate.py` is 31 checks, and it asks both ports the way a device and a browser would. Broken on purpose, two ways: **mounting the receiver into the HR app and removing the refusal** put a live handshake on the tunnel's port — `GET OPTION FROM: GATE` — and six checks failed; **mounting it while leaving the refusal in front of it** answers `404` correctly and is still caught, because the interface must not so much as import the receiver. An app that imports it is one reordering away from serving it.

The seven pieces, in order: **1 serving shape** · **2 read-only screens** · **3 the guard screen** · **4 leave entry** · **5 gate pass entry** · 6 HR corrections · 7 the demo pass.

**Step 10, piece 5 is built: gate pass entry.** One page at `/gatepass`. **HR types a pass that has already been signed on paper** (SPEC §5), out and in times included, through `hr_entry.record_gate_pass` — the same function `hr gatepass add` calls.

**The fields are in §5's order**: name / no. pekerja, emp no. as a field of its own, date, out time, in time, one tick of four, reason, destination. The order is stated by the server and the page is checked against it, not against its own source.

**No hours, at four depths, and the reverse rule from leave.** Nothing on the page to type them into, no field in the payload, no parameter on either service function, and a generated column underneath that refuses an `INSERT` naming it. They appear once the pass is saved, read back from what the database stored — `14:00` to `16:30` comes back as `2.50`. **Leave is the opposite**: there the number of days is typed and nothing may recompute it. Two forms, two opposite rules, and both enforced rather than trusted.

**No department, because the form has none.** The employee's section is looked up and shown beside the name, marked *looked up — not on this form*, and the record has no column for one. That is the difference from §6's leave form, which has a Department line and shows it as field three.

**The two times are HR's, and the screen says whose they are not.** Beside the boxes: *This is not the guard entry screen. That one stands in for a punch the device did not take, is stamped by the server, and has no field for a time at all.* The two acts look alike from a distance and only one of them has time boxes — that is the whole reason the sentence is there rather than in a comment.

**The four signatures** — applicant, supervisor, Head of Dept, HR — are listed under *Not on this screen*, one fewer than the leave form's five, because the Operation Manager does not sign a gate pass.

**Who types it moved out of the leave screen.** `typists` and `typist` now live in `app/hr_entry.py`, the module both forms write through, rather than in `app/leave_entry.py` — a second entry screen reaching into the first one's module to find the two people at the keyboard would make the leave screen a dependency of everything typed afterwards.

**`hr gatepass list` gained an `entered by` column.** `hr leave list` had one and this did not, so the CLI could show a pass without showing who typed it — harder to check than the paper it came from.

**`tools/gate_pass_gate.py` is 87 checks**, and it presses the page: choose the typist, type the number, fill the date and both times, press *Personal*, type a reason and destination, press *Save*, and read `2.50` back. It also reads **every control on the page at three moments** — blank, filled, and after saving — so an hours box called anything at all fails. Broken on purpose, five ways: **swapping emp no. and date** failed the order check; **an hours field added at every depth at once** failed ten checks and was still refused at the bottom by the generated column; **a department field** failed nine; **accepting an in time not after the out time** failed four and turned two refusals into `500`s; **an unknown tick falling back to `OTHERS`** accepted a fifth category three times over.

**A leave record on the working database is not this session's, and is left alone.** `leave_record` **236** — employee `0090`, ANNUAL, sheet code `AL`, 2026-08-22 to 2026-08-26, 5.00 days, typed by Aisyah, `2026-08-21 22:57:55` local, note *typed on the leave entry screen*. Nothing here produces that shape: this session's gates write only to a throwaway database, and the one break that escaped onto this one wrote 157–159 at 22:16 with different dates, a different count and no sheet code. The note means it came through the screen or its service function, not the CLI. **Somebody used the screen**, which is what the screen is for — the interface is on the LAN. It is recorded here rather than deleted, and rather than counted as an artefact.

**Step 10, piece 4 is built: leave entry.** One page at `/leave`, on the HR interface's own chrome. **HR types a form that has already been signed on paper** (SPEC §6) and the screen computes nothing that ends up on the row: it writes through `hr_entry.record_leave`, the same function `hr leave add` calls.

**The fields are in the paper's order** — name, staff no., department, date of application, nature of leave, period from, to, no. of days — so a person reads down the screen and down the form together. The order is a list in `app/leave_entry.py` and the page is checked against what the server states, not against its own source. The sheet legend's code is not among them and is asked for after them, under a heading saying it is not on the form.

**The day count is typed and nothing derives it.** `leave_entry.record` cannot reach `range_check`, which is the only function in the entry path that counts a range, and the gate reads that out of the call graph rather than out of the prose. What `range_check` produces goes to the screen and stops there: beside the box, while the number is being typed, **1.5 days, as the form states — over a 2-day range. The form's number is the one that counts** — the wording piece 2 settled on the day detail screen. **The browser does not count the range either**; it asks, so a date parsed one day out cannot invent a disagreement.

**The tick and the code stay two fields.** Pressing *Annual* puts `AL` in the code box and says it is a suggestion (A48); pressing *Maternity* puts nothing there and says the legend has no letter for it. The suggestion fills an untouched box and never overwrites a choice — overwriting one would be the screen filling one field in from the other, which §6 forbids. **A type with no code and a code with no type both save.**

**The SQL Account code is on the screen only as a blank that is named.** There is no field for it, the payload model has none, and the recorded line prints *SQL Account code empty (SPEC §8)* rather than leaving a reader to notice an absence. Approval, entitlement and balance are listed under *Not on this screen*, with the five signature boxes, because §6 keeps all of them for Milestone 5.

**Who is typing is a row, and HR's two are real people.** `screen_user` gained `hr-aisyah` and `hr-aslida` — Aisyah and Aslida, **not provisional and not warned about**, unlike the guard placeholders A51 stands over. A placeholder notice printed over a real name teaches a reader to skip the notice where it is true.

**Adding those two rows needed `hr seed --add-missing` to grow.** It seeded only *empty tables*, and `screen_user` already held the two guards, so a new row in an existing seeded table had nowhere to come from but hand-written SQL. It now adds the seeded rows the database does not have, **by primary key**, and names every one it adds. **The first version of that doubled `device_option`** — its key is a serial the database assigns, so all ten rows looked new on every run, and a second run inserted them again. A table whose seeded rows carry no key of their own is now left alone and named. It was caught on a throwaway database, before the working one was touched.

**`tools/leave_entry_gate.py` is 105 checks**, and it presses the page rather than reading it: choose the typist, type the number, press *Annual*, press *Maternity*, override the code, fill the dates, type 1.5, press *Save* — then open the August sheet and find `EL` in the two cells, with nothing else run. Broken on purpose, five ways: **the service function deriving the count from the range** failed ten checks, including the stored number and every refusal that stopped refusing; **the screen quietly correcting 1.5 to 2** failed six, and the recorded line said 2 days; **offering the nearest letter for an uncoded type** put `AL` on Maternity and failed by name; **the payload sending the suggestion instead of the override** stored `AL` and put `AL` in the sheet cells; **requiring both vocabularies** refused Maternity-with-no-code and `EL`-with-no-tick.

**Two of the deliberate breaks wrote real leave records, and that is the lesson repeating.** The gate's refusal checks posted to the working interface, and a break that turns a refusal into an acceptance turns those posts into writes: **`leave_record` 157, 158 and 159 — employee `0090`, ANNUAL, 2026-08-07 to 2026-08-08, 2.00 days each, attributed to Aisyah, timestamped 14:16 on 2026-08-21.** They are inventions and they are **not deleted**: nothing in a working session deletes from `leave_record`, including tidying up after a deliberate mistake, and the August sheet reads correctly only if this note is read with it. The gate was then rebuilt so that **every `POST` it makes goes to a throwaway database** — `tools/throwaway.py` creates one beside the working one, runs the same image against it on the compose network, and drops it at the end. Reads still go to the interface that is actually serving. The three rows are the price of finding that out, and re-running that break afterwards cost nothing.

**Step 10, piece 3 is built: the guard screen.** One page at `/guard`, on a phone, on the factory Wi-Fi by LAN address. It carries none of the HR interface's chrome and reaches none of its screens. Four steps: who is on duty, which employee, why, confirm — then the server stamps the time.

**The name showing back is the whole safeguard**, so it is the largest thing on the screen — and the last step names the person once more. The page's button says **Submit** and opens a dialog: *Record a punch for Lim Wei Sheng, 0090?* A big green button low on a phone screen is easy to press while scrolling, and a dialog that only asked "are you sure?" would add a tap without adding a check. **Cancel holds the focus, explicitly** — leaving it to the dialog's own rule would make the safe default an accident of markup order that a later edit reverses silently.

The confirm step lives at `/guard?employee=0090`, which means the phone's Back button undoes a mistyped number — the correction that has to be easy, because the one after confirming does not exist. The page says in those words that a confirmed entry cannot be undone and that HR fixes mistakes. **No void path was built**; that question is still parked for piece 6.

**No time, at four depths.** There is no time control on the page; the request model declares three fields and `forbid`s a fourth, so a crafted payload is a `422` rather than a value quietly dropped; `guard.record` and `record_guard_entry` have no parameter for one; and `manual_punch_guard_cannot_state_a_time` refuses the row underneath all of it.

**Who the guard is comes from a row.** New table `screen_user` — attribution, not a login. The two seeded guards are **placeholders marked provisional** and the screen says so, because nobody has read the roster (A51). Adding that table needed `hr seed --add-missing`: it creates tables the model has and the database does not, and seeds only tables that are empty. **It never drops, never updates, never deletes** — which matters now that the database holds leave records, gate passes and corrections that nothing can rebuild.

**`tools/guard_gate.py` is 80 checks**, and it reads all three steps of the page out of a browser at phone width — and now presses them. `tools/browser.py` drives Chromium over the DevTools protocol: a dumped DOM settles what is *on* a page, but not what the layout does at a width, and not what happens when a button is pressed. It is standard library only; the WebSocket client is sixty lines, which is why there is no driver package. Chromium binds its debugging port to the container's own loopback and ignores the flag that asks otherwise, so fifteen lines of the image's own Node forward the published port to it.

**The horizontal overflow could not be reproduced headlessly.** At 320, 360 and 390px, with the longest name on the roster and with a guard called *Mohd Faizal bin Abdul Rahman*, the page measures clean. What the header did carry was the shape that produces it — two flex children that could neither wrap nor shrink — and on a phone whose text is a size larger, that is exactly when the right edge goes. The header wraps and its children may shrink now, long names break rather than push, and the dialog's width no longer uses `100vw`, which counts a scrollbar the content cannot use.

**The first version of that check could not have failed.** It compared `scrollWidth` against `window.innerWidth` — and when a page overflows, the layout viewport grows to fit it and `innerWidth` grows with it, so the two move together and the comparison holds for a page that is sixteen pixels too wide as readily as for one that is not. Measured against `clientWidth`, a deliberately unshrinkable header reports `right=406` in a 390px viewport. **The rule that a gate is not passed until a deliberate mistake makes it fail is what caught it**, and nothing else would have. Broken on purpose, four ways: **a `type="time"` box added to the confirm step** was caught in the DOM; **a payload that accepted `asserted_time` and passed it all the way down** failed nine checks and was refused at the bottom by the constraint the CLI hits; **removing the reason check** let an HR-path reason through and turned an unknown reason into a `500` instead of a refusal; **making an unknown employee number fall back to the first employee on file** wrote three punches against the wrong person, which is exactly the failure the check exists to prevent.

**Two of those breaks wrote real rows, and that is the lesson.** A deliberate mistake on a *write* path costs rows on the working database. Worse, the gate itself had been writing one on every run: `guard.record` committed, so the `session.rollback()` under it did nothing. **The commit moved to the caller**, and the gate now ends by checking that `manual_punch` is exactly the size it was when it started. A break on a write path is run against a throwaway database from now on, because the alternative is deleting from a layer §13 says is never edited.

**Two of the rows deleted in that clean-up were not artefacts.** They were guard entries made from a phone on the LAN, at 15:46 and 15:53 local, by somebody standing at the screen — invisible in this session's own logs, which is not the same as unexplained. **A row nobody can account for is left alone and reported**, because deleting it destroys the only evidence that would have explained it; the rule is in CLAUDE.md. They were test punches and are deliberately not re-created — inventing a replacement for a record is the same error twice.

**A real correction also exposed a hole in the importer.** `--replace` counted leave records and gate passes but not manual punches, so with a guard entry on file it did not refuse — it reached the `DELETE` and died on a foreign key. It refuses by name now, and the import gate proves it.

**Step 10, piece 2 is built: the read-only screens.** The employee list on a date, the sheet, one employee's period in detail, and the Excel download. **Nothing in this piece writes.** Three screens and one file, and every one of them is a face on a function `hr` already calls: `app/screens.py` is the only part of the application the HTTP layer imports, and it hands back finished answers.

**The sheet screen is §7's screen, and it is the same render as the file.** `app.sheet.render` builds the sheet once; `to_text` draws it for a terminal, `to_json` for the browser and `to_excel` for the file. **The browser is given the answer, never the ingredients** — a cell arrives as text with its kind, whether a person entered it, and whether it rests on a provisional schedule already decided.

**The Excel file became reproducible, and had to.** Two exports of one period used to differ, because openpyxl stamps the archive and the document with the clock. They now carry the period's own date instead, so **the same period always exports to the same bytes** — which is what makes "the download is the filed record" a thing that can be checked rather than asserted. The download is those exact bytes.

**Two presentation questions, answered by looking at a screen.** *Does the leave screen say the day count is the form's?* Yes, on its face: the detail screen prints "1.50 days, as the form states", and where that differs from the range it adds "over a 2-day range. The form's number is the one that counts." Both numbers, neither hidden — a reader shown only one of them eventually corrects the wrong one. *How prominent is the provisional warning?* A banner above the grid saying how many cells rest on unconfirmed schedules — 1,349 of them in August — **and every one of those cells is marked where it stands.** A banner alone lets somebody carry one number away and leave the warning behind; a tick is the claim "this was on time", and that claim is the provisional part.

**What the screen cannot carry, said rather than dropped:** only how the file prints — page breaks, the repeating header rows, landscape fitted to one page wide, the legend's own page. The screen scrolls and says so under the grid. Every mark a reader interprets is on both.

**The grid freezes its edges the way the file does.** The three identifying columns stay put while the days scroll, the day-number and weekday rows stay put while the employees scroll, and the grid scrolls inside a bounded box so the horizontal scrollbar is reachable without passing 58 rows first — the screen's answer to the file's freeze panes and repeating print titles. Each day column is as wide as the widest thing in it, which is the rule `to_text` already followed. **Shading was a header-only stripe and is now the whole column**, cells included, as `to_excel` fills it.

**`tools/screens_gate.py` is 109 checks**, and it reads the page out of a real browser — Chromium's own `--dump-dom` inside the Playwright image, no driver package and nothing installed at run time. Broken on purpose, four ways: **the browser truncating a two-time cell** to its first half changed ten cells and was caught only by the DOM check, which is the whole reason that check exists; **dropping the deterministic write** made two exports differ and the download stop matching; **a loop and an import added to a route handler** failed the import check and the AST check together; **widening the fixture-serial pattern** to `^(GATE|TEST|CHECK|NOT|PYA)` made the real device look like a fixture and failed on its serial by name.

**One of those breaks passed the first time and should not have.** The rebuilt image had not reached the container, so the gate read the old page and agreed with it. The bundle is now checked for the change before the failure is believed — a gate proven against a stale artefact proves nothing.

**A fifth break, and it is the bug that was actually shipped:** the frozen columns were pinned at offsets guessed from utility classes rather than taken from the column widths, so each one sat *over* its neighbour and the first two days of the month disappeared underneath. The gate now reads the `<col>` widths and the sticky offsets out of the DOM and compares them — `found {'80'}, columns are [72, 208, 168]`.

**A gate serial is recognisable now.** Anything a tool invents begins with `GATE-` (SPEC §9 A50), the pattern is a row, and those serials are kept off `hr alert check`'s unwatched list — **counted on it instead**, with `hr alert fixtures` listing them. The list existed to catch one real device nobody was watching, and it had filled with five names from the test tools.

**Step 5 is built.** `hr leave add` and `hr gatepass add` type what is on the two forms, and nothing else — no approval routed, no entitlement, no balance. The vocabularies are rows: seven ticks on the leave form, nine legend codes, four gate pass categories. **Three of the seven types carry a suggested sheet code and four carry none**, because the legend has no letter for them, and the screen offers nothing rather than the nearest guess (A48).

**Two rules from the forms are structural, not advisory.** The number of days is required and **nothing in the code subtracts the two dates** — the gate reads the source to say so. The gate pass hours are a **generated column**: `INSERT ... hours` is refused by Postgres with `cannot insert a non-DEFAULT value into column "hours"`, which is the same shape as the guard entry that has no field for a time.

**The sheet shows leave codes now**, and its legend prints them from `leave_code` rows. A leave day with no sheet code falls through to whatever the punches say rather than blanking a day somebody actually worked (A49), and the sheet counts both kinds in its notes.

**Reloading the employee list stopped being free.** `--replace` deletes employees, and a leave record or gate pass hangs off one — so it is refused while any HR entry exists, and the list is corrected in place instead. Daily attendance is still cleared with the list, because that is rebuilt from punches; typed forms are not rebuildable from anything.

**The same rule binds the gates, and it took a real loss to notice.** The import gate cleared `leave_record` and committed, so that its own case could run. It destroyed every leave record on the working database — the sheet's August notes fell from 8 coded days to 5 — and it had been doing it on every run since step 5. **A gate makes its own rows inside the transaction it rolls back**, because a form somebody signed is not rebuildable from anything and a test fixture is a poor reason to lose one. The rule is now in CLAUDE.md.

**The other gates were audited by counting, not by reading.** Every table's row count taken before and after each run: **the eight in-container gates and the screens gate change nothing at all.** Two tools do write, and both are right to. `serving_gate` adds one `raw_request` because it asks the receiver a real device question over HTTP, and the receiver stores what arrives. The simulator adds thirty requests, the punches parsed from them, and one `device_command` row the route itself writes for a result nobody asked for. **What it used to leave behind as well were the two commands it queued by hand** — the one write in the tools that cannot be rolled back, since the receiver is another process and cannot see an uncommitted row. It now takes those rows back when the run ends. What arrives through the receiver's own routes stays: that is capture, and capture is append-only.

**The demo leave and gate pass for 9001 were restored from BUILD.md's own run block**, which is the only reason they were recoverable. A real form would have been gone.

`tools/hr_entry_gate.py` is 38 checks, and the import gate is 43. Broken on purpose: a record with no day count, a day count recomputed from the range, zero days, a record that says neither type nor code, typed hours, an in time before the out time, a category that is not one of the four, and a code sitting in a cell with no record behind it.

**Step 8 is built, as far as it can be without the device.** `hr cmd send <serial> REBOOT|CHECK` queues one command; the device collects it on its next poll and reports the result back. `getrequest` hands out **one command per poll, oldest first, for that serial only**, and marks the hand-out in the same transaction as the request that took it — a command cannot leave without the request that collected it being on the record. **A device with nothing queued gets exactly the reply it got before this step**, byte for byte. `devicecmd` records the result against its command, and **a result for a command nobody issued is stored, flagged and still answered `OK`** — the same reflex as an unknown serial.

**Two commands exist: REBOOT and CHECK.** They are rows, and there is deliberately no row that clears, deletes or resets anything. The device is believed to buffer punches across an outage and **that is still unproven on this hardware** — an unbuffered clear would take punches with it, so adding such a command is a decision made in front of evidence, not an edit. SPEC §13 now says so as a never.

**Both wire formats are documented, not observed** — `C:{id}:{CMD}` going out and `ID=&Return=&CMD=` coming back (SPEC §9 A46, A47). No command has ever been sent to this device. The simulator exercises what the document says, which proves the receiver consistent with the document and nothing about the firmware.

The simulator now queues a command of each kind, collects them, acts, and reports; it also posts a result for a command nobody issued. **Every poll it makes answers a command if one comes back**, because the first version only listened in one place, took a command there and never answered it — the queue looked answered from the outside while a command hung. The check that catches that: every command a run issues must end with a result recorded against it, under the id it was sent with. Broken on purpose: not marking the hand-out made one command go out four times and none come back; raising on an unsolicited result made the flagged row disappear — and **the device still saw `OK` in both cases**, which is §12's rule holding.

**Step 9 is built.** `hr alert check` watches every serial on the allowlist and reports **two silences, never one**: contact silence, which means the device is off, the network is down or the Cloud Server setting moved, and punch silence, which only counts while a shift is running on a day the calendar says the factory is open. A single "time since the last punch" would alarm every weekend and stay quiet when the receiver is unplugged on a public holiday.

**Where the warning goes:** the check is silent when all is well and **exits 2 when it is not**, so the transport is a cron entry or a systemd timer on the host — no daemon, no dependency, and nobody has to be looking at a screen. `ingestion_alert` records the transitions, raised and cleared, so an outage has a start, an end and a length. `--status-file` writes the current state for a monitoring agent if the site ever grows one. **The notification channel is not this** — Telegram and WhatsApp stay parked for the supervisors and Milestone 5.

**The alert reads the database and never asks the device anything**, which is what lets it answer during the outage it exists to catch. `tools/alert_gate.py` is 44 checks, including that `app/alert.py` imports no HTTP client, no mail client and no subprocess — checked on the imports, not on the prose.

**The allowlist carries a state.** `live`, `down` — out for repair, not yet mounted — or `retired`, like a test serial. Only a live serial is alerted on, and the `alerted` column on `device_state` is what decides, so a new kind of not-talking is a row. `hr devices state <serial> <state> --reason "..."` records who changed it and why; `hr devices list` shows the state, when it changed and the reason. **Deleting a serial is not the way to silence it** — the raw layer keeps its requests forever and the list has to say why it went quiet (SPEC §3, §13). Standing a device down also clears whatever it had raised, because a permanent known alert is what teaches people to ignore the alert. The simulator is `retired`: it only talks when a gate is run, so its silence is never news.

**Two things the real run found, and neither was staged.**

**The device has been unreachable since 04:38 UTC.** The alert caught it the moment the device was added to the allowlist: 229 minutes of contact silence, plus punch silence because a shift was nominally running. The receiver is up and answers locally; **the Windows port proxy that carries `192.168.60.50:8081` into WSL2 is gone** — `netsh interface portproxy show all` is empty. This repo already warned about exactly that ("Going live", Prepare). It is the failure mode SPEC §10 describes, and it is the first time anything has caught it.

**The real device was never on the allowlist.** Only the simulator was, so nothing watched the device that matters — it captured perfectly for days and its four-hour outage raised nothing. `hr devices add` puts a serial on the list and `hr devices list` says when each was last heard from; the check now also reports serials that have pushed and are not on the list, because that hole is quiet by construction.

**Step 7 is built.** One render, two outputs. `app/sheet.py` builds the sheet once from the daily rows and both emitters draw that same object — `to_text` for the screen, which is the system, and `to_excel` for the file, which is the record HR files (SPEC §7). Every mark a reader can see is decided in the render and carried on the cell; the emitters pick fonts and column widths and nothing else. **The file and the screen cannot disagree about a day**, and the gate proves it by writing the file from one render and comparing it against a second render deliberately made to differ.

A cell is a tick when the day's punches are inside the schedule, the punch times when they are outside it, blank when there was no punch, and marked with an asterisk when a person entered it (A38). Rest days and public holidays shade whole columns from the calendar; a gazetted holiday the factory works does not shade, because only the `closes` flag shades. **Leave codes do not exist** — entry is step 5 — so the leave path is in place, empty, and the sheet says so on its own face rather than leaving it to be noticed.

`hr sheet detail` is the per-day punch detail §7 requires: one employee, one period, every day of it, with the punches behind each day, manual entries marked and the leave column present and empty. **It is what Accounts reads instead of the punch card**, and it is not a second sheet — no cells, no shading, no pages.

**The Excel is set up to print**: landscape, fit to one page wide, the header and day-number rows repeating on every page, a break every `rows_per_page` rows, and the legend on its own page at the end. It had none of that until it was looked at — every cell was right and the artefact was not. `tools/sheet_gate.py` now checks the page setup against the render and is 59 checks. `tools/sheet_readback.py` reads an exported file and compares it to the render that produced it — **it lives in `tools/` because it is a check on the writer, not an ingest path**: nothing in `app/` reads a sheet file, and §13 says why.

**Deliberately not in step 7:** no period totals, no late coming summary, no Accounts export, no leave entry. A period total is a query over the daily rows and belongs to Milestone 3 (SPEC §3).

**Over the real capture**, August 2026 renders 7 employees on one page — the eighth left on 30 June and is correctly absent from an August sheet. Two cells carry anything: `0090` on the 20th shows `11:26/11:29`, both real device times, late in and early out against a **provisional** schedule; `1627` on the 17th shows `08:03`, a simulator push on a night-shift group. The Excel was exported and read back, and the file agrees with the render about every day for every employee. **Everything on that sheet that is a time is real. Everything that says a time is *late* rests on a schedule row HR has never seen**, and the sheet carries a note saying so.

**Step 6 is built.** One row per employee per day, over parsed punches, corrections and the schedule in force on that day. First in and last out are the day's earliest and latest punch — and with one punch there is a first in and no last out, which the database enforces rather than trusts (A35). Late minutes are computed from that day's schedule row plus its grace, and the row carries the start it was measured against, the schedule row's id, and **whether that schedule is still provisional**, so a figure that rests on a guess reads as one. Manual punches count toward the figures and every figure says whether a person entered it. A re-pushed punch counts once, and the row says how many copies it dropped (A37).

`tools/attendance_gate.py` is 46 checks: a night-shift punch at 04:35 landing on the previous day and not its own, late minutes against June's schedule rather than July's, a manual punch that cannot be silent, one punch that cannot grow a last out, and two rebuilds producing an identical row. Four raw inserts are refused by the constraint each was aimed at — a first in with no source, a last out on one punch, two rows for one employee on one day, and a status of `absent`, which is not in the vocabulary and never will be.

**Deliberately not in step 6:** no sheet, no period totals, no screen, no export, no work hours, no overtime, no absence. Every period total is a query over these rows (SPEC §3), and no period boundary is confirmed.

**What the real capture produces.** With the provisional schedules and the sample employee list loaded, and device user 1 mapped to a fixture employee, 2026-08-20 comes out as three punches, first in 11:26:01, last out 11:29:42, 206 late minutes — **and the 206 is against a schedule row HR has never seen.** The punch times are real; the lateness is arithmetic on a guess. The simulator's own pushes produce the other instructive row: 42 copies of one punch collapse to one, and a night-shift group's 08:03 punch lands on the previous attendance day with 753 late minutes, which is what a 240-minute window (A30) does with a punch that does not belong to that shift.

**Step 4 is built.** Both correction paths, both marked, both counted. A guard entry has no field for a time — the check constraint refuses a guard row that carries one, and the function has no parameter for it. An HR retroactive entry must carry a time and a reason in words. Both land on an attendance day through the schedule in force, so a night-shift correction after midnight belongs to the shift's day. `hr punches --employee N --day D` reads device punches and corrections together, and every line says where it came from and, if a person made it, who and why. `hr corrections count` is per employee per period, split by path — the signal SPEC §3 asks for.

**One gap, deliberately not filled: a mistaken correction cannot be undone.** A guard entry made for the wrong employee stays on the record. SPEC §3 does not say what should replace it — an offsetting row, a void row, or nothing — so it is parked rather than invented.

Deliberately not in step 4: no daily attendance, no late minutes, no sheet, no screen.

**openpyxl** was added for it: it reads .xlsx without a spreadsheet application and writes the fixture, so one dependency covers both directions. It cannot read the legacy .xls format — an .xls from HR gets re-saved as .xlsx before import.

Run it:

    docker compose up -d --build
    docker compose exec api hr seed            # drops, recreates and seeds — one command
    uv run python tools/adms_sim.py --port 8081  # must exit 0
    docker compose exec api hr replay          # rebuild parsed punches from the raw layer

    docker compose exec api hr employees import /srv/fixtures/employees_sample.xlsx \
        --mapping /srv/fixtures/employees_sample.mapping.toml --allow-new group

    # a plausible August, pushed at the receiver the way the device would
    uv run python tools/make_month_fixture.py --port 8081 --dry-run
    uv run python tools/make_month_fixture.py --port 8081

    # the list whose PINs match the captured punches, so the sheet has data on it
    docker compose exec api hr employees import /srv/fixtures/employees_punch_demo.xlsx \
        --mapping /srv/fixtures/employees_punch_demo.mapping.toml \
        --replace --allow-new group --accept-leading-zero-pins
    uv run python tools/employee_import_gate.py  # must exit 0

    docker compose exec api hr schedule seed-provisional
    docker compose exec api hr calendar import /srv/fixtures/holidays_provisional_2026.xlsx \
        --mapping /srv/fixtures/holidays.mapping.toml --year 2026
    uv run python tools/schedule_gate.py         # must exit 0

    hr schedule show --group NIGHT-PROD --date 2026-08-17
    hr schedule attendance-day --group NIGHT-PROD --at "2026-08-18 04:35:00"
    hr calendar adjust --date 2026-05-01 --closes no --reason "..." --by "..."

    uv run python tools/corrections_gate.py   # must exit 0
    uv run python tools/attendance_gate.py    # must exit 0
    uv run python tools/sheet_gate.py         # must exit 0
    uv run python tools/alert_gate.py         # must exit 0
    uv run python tools/serving_gate.py       # must exit 0 — both ports, by asking

    # the interface: http://<server>:8090/   ·  the receiver stays on 8081
    docker compose up -d --build

    hr devices add --serial PYA8262300072 --label "SenseFace 4A, main door"
    hr devices list
    hr alert check --verbose        # silent and 0 when well, loud and 2 when not
    hr alert history

    hr devices states
    hr devices state SIM0000000001 retired --reason "..." --by "..."
    hr devices list

    hr cmd types
    hr cmd send SIM0000000001 CHECK
    hr cmd list SIM0000000001
    */5 * * * * docker compose exec -T api hr alert check   # the transport

    hr leave types
    hr leave add --employee 9001 --type ANNUAL --from 2026-08-24 --to 2026-08-26 \
        --days 3 --applied 2026-08-14 --by "HR: ..."
    hr leave list --from 2026-08-01 --to 2026-08-31
    hr gatepass add --employee 9001 --date 2026-08-19 --category PERSONAL \
        --out 14:00 --in 16:30 --destination "..." --by "HR: ..."
    hr gatepass list --from 2026-08-01 --to 2026-08-31

    # the screens, on the LAN or through Tailscale
    http://<server>:8090/            # employees, on a date
    http://<server>:8090/sheet?month=2026-08
    http://<server>:8090/employee/0090?month=2026-08
    uv run python tools/screens_gate.py        # 109 checks; reads the real DOM
    uv run python tools/screens_gate.py --no-dom   # without Docker

    http://<server>:8090/leave              # leave entry, off the paper form
    uv run python tools/leave_entry_gate.py    # 105 checks; presses the page,
                                               #   and saves on a throwaway
    uv run python tools/leave_entry_gate.py --no-dom   # without the browser
    uv run python tools/throwaway.py        # the same interface, on a database
                                            #   that is dropped when it exits

    http://<server>:8090/gatepass           # gate pass entry, off the paper form
    uv run python tools/gate_pass_gate.py      # 87 checks; presses the page,
                                               #   and saves on a throwaway
    uv run python tools/gate_pass_gate.py --no-dom     # without the browser

    hr alert fixtures                          # what the unwatched list omits

    hr sheet render --month 2026-08
    hr sheet export --month 2026-08 --out /tmp/attendance_2026-08.xlsx
    hr sheet detail --employee 0090 --from 2026-08-17 --to 2026-08-22 --punches
    uv run python tools/sheet_readback.py --month 2026-08 --file /tmp/attendance_2026-08.xlsx

    hr attendance build --from 2026-08-16 --to 2026-08-21
    hr attendance show --employee 0090 --from 2026-08-20 --detail
    hr employees map-pin --pin 1 --employee 0090 --from 2026-08-01 --source "..."

    docker compose exec api python tools/parser_gate.py   # must exit 0
    SKIP_DB=1 uv run python tools/parser_gate.py          #   shape half only

    hr raw --limit 20                         # what arrived, when, from which
    hr raw --serial PYA8262300072 --body      #   serial, which table, and the
    hr raw --id 26                            #   bytes. It parses nothing

    http://<server>:8090/guard              # the guard's phone, on the LAN
    uv run python tools/guard_gate.py       # 80 checks; drives the page at phone width
    uv run python tools/browser.py <url> <js>   # measure or press anything, by hand

    hr seed --add-missing                   # a new table or seeded row, without
                                            #   dropping anything. Names what it adds

    hr corrections guard --employee 0090 --reason biometric_failed --by "Guard: ..."
    hr corrections retroactive --employee 0090 --at "2026-08-17 08:05:00" \
        --reason "device down" --by "HR: ..."
    hr corrections count --from 2026-08-01 --to 2026-08-31
    hr punches --employee 0090 --day 2026-08-17

HR's real list goes in `import/`, which is not committed. The importer is told which sheet, which rows and which column letters hold what; it never matches on header text, it echoes the headers back so a person can see where the mapping is pointing, and one bad row writes nothing at all.

`hr seed` refuses to drop once requests have been captured, and needs `--force` to go ahead. That guard becomes the real thing on the first day of the parallel run, when dropping stops being recoverable.

Verified on the compose stack: a full cycle clean, a second cycle clean against the database the first one filled, capture surviving `docker compose down` and back up, and the replay running in the container.

**The receiver's host port is `RECEIVER_PORT`, now 8081** — the device is already pointed there. Not 8000, which Production Tracking holds on this host, and not 8080, which belongs to another project; all three stay separate. This port is half of the device's Cloud Server Setting, so changing it again means changing the setting on the device.

**The device is on site, powered, on the LAN, and has pushed real traffic.** SenseFace 4A, serial `PYA8262300072`, protocol switched from BEST to PUSH (ADMS), HTTPS off, cloud server pointed at port 8081. The full identity, firmware and capacities are in SPEC §10.

**The first capture is not in the raw layer, and cannot be.** It was taken with a throwaway FastAPI script outside this repo, which answered every push `OK` — so the device cleared those records from its own memory and nothing was stored. What the traffic settled is written into SPEC §12, line by line, marked observed once against a non-conforming server. **The re-capture that matters runs the receiver itself:** it answers the handshake as §12 specifies and stores every request whole, so the next capture is keepable. `hr raw` reads it.

What the first capture settled: OPERLOG names its cursor `OpStamp`, the handshake query carries `DeviceType` and `PushOptionsFlag`, the first `getrequest` after a handshake carries `INFO`, two undocumented tables (`options` and `BIODATA`) arrive and are absorbed harmlessly, verify is 15 for face and 1 for fingerprint, and status is 255 on every punch — the device does not label in versus out, and first-in/last-out from times is settled rather than assumed.

**A21 is answered and deleted** (SPEC §9): the device refuses a leading zero in a user ID, so the question of whether it pushes `0090` or `90` never arises. No code changed — §13 and the dated device-user mapping already covered it.

**The second capture is through the receiver and is kept: `raw_request` 96–115.** A handshake and the ten option lines the receiver answered it with, the device's own option push, `INFO` on the next poll, two OPERLOG pushes, two real punches, and the polls between them. The device polls every few seconds — `iClock Proxy/1.09`, `Host: 192.168.60.50:8081` — and everything it sends is in the raw layer, readable with `hr raw` and replayable. SPEC §12 is rewritten against those bytes.

**The parser now accepts the real punch line. Parser version 2, replayed.** Ten fields stored positionally and verbatim, four of them named — pin, device time, status, verify — and the other six left unnamed on purpose. The trailing empty piece is a separator, not a field. Any other shape is a failed row with the line kept whole: nothing is padded, nothing is truncated.

Every real punch in the capture now parses clean. `raw_request` 96, 109 and 129 each yield one row with `parse_ok` true, pin `1`, the device's own time string, status `255`, verify `15` — checked against the stored bytes by `tools/parser_gate.py`, not against a copy of them.

**The replay is also what re-marked the history.** Rows from older simulator runs were seven-field lines that no device ever sent, and under parser 2 they are failures. That is the correct answer for them, and it cost a replay rather than a re-collection.

Artifacts received and analysed: daily attendance sheet, late coming summary and deduction record, individual time-off record, time-off salary summary, SQL Account payroll entry screen, **leave card** — HR's per-employee leave ledger, kept by hand and not in the repo, since it carries a real employee's name and join date. What it settled is in SPEC §6 and §2.

**Leave application form** — the two leave vocabularies, the form's own field order, and **five signature boxes** including the Operation Manager, SPEC §6. **Gate pass** — category, destination, out and in times, **four** signatures and no department at all, SPEC §5. **The two forms do not carry the same chain.** Both were photographed blank, with no employee data on them, and **the photographs are not in the repo**: they are not source, and what they settled is in SPEC.

**Most of the protocol contract is now observed, and the second capture is the one that is kept** — see SPEC.md §12, where each line says what was seen and what was not. The simulator still only tests that the receiver behaves as the contract says, not that the contract is right; what changed is that its shapes are now copied from real bytes rather than from documentation.

---

## What the second capture settled

Through the receiver, kept as `raw_request` 96–115. Every line here can be checked against the bytes.

**Settled**

- **A26 is answered and deleted.** The receiver sent all ten option lines and the device accepted them and carried on pushing. SPEC §12 records the set as observed, and `Realtime=1` is why punches arrive within seconds rather than on the `TransTimes` schedule.
- **The punch line is ten tab-separated fields and a trailing tab**, not the seven §12 claimed. Fields five to ten were `0` in every line, so they are recorded as unknown rather than named.
- **The device's clock offset is exactly +8** against the server's arrival stamp, matching the `TimeZone=8` it was sent.
- **`Stamp=9999` was never the device's choice — it is ours**, echoed back from the handshake reply. The earlier note in SPEC §12 said otherwise and has been corrected.
- **OPERLOG's cursor parameter is `OpStamp`**, confirmed a second time.
- **The application cannot see the device's address.** Everything arrives from the Docker bridge gateway; the device's own address is a field inside the options body. Address filtering has to be at the firewall.
- **The device suppresses a repeat verification from the same user within a fixed interval**, per user and not per method — a second attempt never reaches the receiver at all (SPEC §10).

**Not settled**

- **A27.** Every byte of both captures was ASCII and `Name` was empty in both. It settles the first time a non-Latin name is enrolled, and not before.
- **A moving stamp cursor.** Ours is a row fixed at `9999`.
- **The suppression interval's value**, which has not been read off the device menu.
- **A34**, the small `~Max...` numbers in the option push.

**Still owed at the device menus, next time it is in reach:**

- **Whether exporting also clears records from the device.** This is the only real risk in the USB question — exporting casually during the parallel run would destroy the re-push safety net.
- The export file shape: tab-separated ATTLOG, or a `.dat`/`.csv` variant.
- Whether the export includes biometric templates or user records only.
- Whether USB import overwrites or merges existing enrollments. **Bulk-loading employee records makes enrollment materially faster** — the person at the desk then captures only the biometric — and it is step 8's fallback if the command queue turns out awkward on this firmware.
- The repeat-verification suppression interval (parked above).
- Register the super administrator. Set verify mode to face and fingerprint only.

---

## The outage happened by itself, and buffering is proven

**It is no longer an assumption.** The receiver was unreachable from `2026-08-20 04:38:45Z` to `2026-08-21 00:50:32Z` — twenty hours, because the Windows port proxy carrying `192.168.60.50:8081` into WSL2 had gone. Five punches were made at the device during that gap. **All five arrived in a single push one second after contact came back** (`raw_request` 3335), the oldest having been held **953 minutes — nearly sixteen hours**. Nothing was lost, nothing was re-collected, and nobody asked the device for anything.

That settles what SPEC §12 had been asserting from the protocol's shape and what this file called unproven: **the device buffers while the receiver is unreachable and re-pushes on reconnect.** It also settles what an outage actually costs, because this one cost exactly that: capture stopped for twenty hours and **nothing said so** until the device was put on the allowlist and the alert had something to watch.

**The rule about destructive commands stands, with a better reason.** The device's buffer is the only copy of a punch it has not been acknowledged for — now observed holding one for sixteen hours — and that is precisely what a clear command would delete (SPEC §11, §13).

---

## The outage test — five minutes, and it is step 9's gate

The device buffers when the receiver is unreachable and re-pushes on reconnect, deleting a record only once the server answers `OK` (SPEC §12). **That is settled by the protocol's shape rather than by evidence, and five minutes settles it with evidence:**

    docker compose stop api
    # punch several times, wait
    docker compose start api
    hr raw --serial PYA8262300072 --since-id <last>

Punches arriving after the restart confirms it. 200,000 transaction records is years of buffer (SPEC §10), so an outage of any plausible length is not data loss.

**The real gap is not data loss — it is not knowing.** The device retries quietly, so a receiver that has been down since Tuesday looks exactly like a factory where nobody punched. Nobody finds out until a sheet has holes in it. **That is step 9**, and this test is the gate it has to pass: silence for N hours raises a warning.

---

## Data and migrations

**No real data exists until employees are punching for real** — the start of the parallel run. Until then:

- The database is dropped and recreated whenever the shape changes. No migration files, no ordering, no schema history.
- Simulator output is not worth preserving.
- **The device's own capture is, and already had to be preserved once.** Parser version 2 changed `parsed_punch`, and the drop would have taken `raw_request` with it. What to do, in this order: **stop the receiver first** — the device is pushing, and anything that arrives after the dump is gone — then `pg_dump --data-only --table=raw_request`, `hr seed --force`, restore the dump, `setval` the id sequence, `hr replay`, start the receiver. The ids stay as they were, because SPEC §12 cites them.

**That dump-and-restore is not a migration and does not become one.** It rebuilds one append-only table from its own rows; nothing about the schema's history is recorded, and layer 2 is rebuilt by replay rather than carried across. It is, though, the rehearsal for what comes next.

**From the first day of the parallel run: migrations only.** Raw device capture is append-only from that moment and cannot be recreated — the device does not keep it.

---

## Going live

Code being ready is not the gate. These are.

**Decide first**

|Decision|Whose|
|---|---|
|**Grace period.** The device is stricter than a person reading a card. Identical rules will produce more recorded lateness, and lateness is deducted from pay. Reproducing today's effective strictness needs a grace period; tightening needs a decision and a communication to staff|**Management, not HR**|
|**Who may correct a missed punch, and on what evidence.** Today the evidence is a supervisor's signature in the punch card slot, and **after cutover there is no card to sign.** The recommendation is a signed slip for Milestone 1, superseded by supervisor confirmation in Milestone 5, which Milestone 4's supervisor relationships make possible. **It does not block the build** — HR retroactive entry from step 4 works without it — but it needs answering before the parallel run ends, because that is when corrections start being real|Management|
|**Biometric notice to employees**, local and foreign. Required before enrollment, not after|Management|
|How long raw device data is retained. Face templates and photos land there from the first test push, whatever the wider privacy decision|Management|
|Whether the guard has a screen where failures happen|Zi Fong, on inspection|

**Prepare**

- Device mounted, powered, on the LAN at a fixed address.
- **Cloud Server address points here and stays here.** Record the correct value somewhere findable. The port is `RECEIVER_PORT`, 8081 unless changed, and the device is pointed at it.
- **If the receiver is run under WSL2 for a test, the device cannot reach it without a port proxy on the Windows host.** On the on-premises server this does not arise. Worth knowing before the first power-on wastes an afternoon.
- Timezone +8.
- Ingestion alert live.
- Throughput check at shift start — a whole shift punching within a few minutes, one door.

**Enroll**

- **Super administrator registered first.** Until one exists the device menu is open to anyone.
- Verify mode set to face and fingerprint only.
- Every active employee, face and fingerprint. **The PIN cannot be the padded employee number — the device refuses a leading zero** (SPEC §10), so what a `0090` employee's PIN is has to be decided before enrollment and recorded in the device-user mapping.
- Scheduled around shifts, night shift included.
- **Manual workers with worn fingerprints enroll on face.** Verify each enrollment works before the employee leaves the desk — one visibly failing employee damages confidence in the whole system.
- Expect real failures in the first weeks: wet hands, gloves, lighting. The manual-entry count per employee is how bad enrollments get found and redone.

**Parallel run**

Cards and device run together for **at least one full 16th → 15th cycle**, two if the first shows discrepancies.

**Acceptance: the system's computed late coming totals match HR's hand-computed totals for the same period.** This is what makes HR willing to stop using cards. It is not optional.

**Discrepancies are investigated, never adjusted away.** Each one is a device problem, an enrollment problem, or a rule the system has wrong.

**Cutover**

- On a period boundary. The 16th is the natural one.
- Cards stop only after a clean parallel cycle. Retained afterwards for the statutory period.
- **HR continues the paper attendance sheet until leave entry exists**, because leave codes are written on the card today and have nowhere else to go.
- The summaries keep being signed by HR/Admin and Acct/Payroll as today. The system generates them; it does not change who approves them.
- **After cutover Accounts reads the generated sheet by hand until Milestone 2 exists** — the cards are gone either way, and the sheet is the filed record they read instead (SPEC §7). That is "fewer fields than before and more than none" working as intended, not a gap waiting to be closed.

---

## Parked

**Needs a person, not a decision**

|Question|Who|Blocks|
|---|---|---|
|**Scheduled start and end per group. Read it off the punch card machine before it is decommissioned** — it prints red for out-of-schedule punches, so the schedule is already configured on it|HR / the machine|Late coming|
|Is there a grace period before a minute counts as late|HR, then management|Late coming|
|Is lateness measured against a fixed start, or the shift the employee was on that day? Do employees move between shifts?|HR|Late coming|
|**Which period does the time-off record and the payroll half actually run on** (SPEC §9 A9, A10)? **Late coming is answered** — the record states 16/12/2025 to 15/01/2026 — but the time-off record's period is blank on the specimen. Are the 10th/15th/20th cut-offs deadlines or boundaries? **One sheet covers one calendar month is the assumption** (A40)|HR|Time-off aggregation, and the sheet's period|
|Is the 30-minute threshold applied per month or per payroll half?|HR|Milestone 3|
|What exactly does `AB — absent cut 3 times` cut, and against what?|HR|Milestone 3|
|**What is the PIN for an employee whose number starts with a zero?** The device refuses to store one (SPEC §10), so `0090` is enrolled as some other string and the mapping joins them. Which string HR actually keys is not on any paper yet|HR|Enrollment|
|Are numbers reused after someone leaves?|HR|Employees|
|What employee groups exist, and does the group decide shift and break?|HR|Schedule|
|Half-day marks — which leave types can be half days?|HR|Leave|
|**Is `AL` for Annual, `MC` for Sick and `UL` for Unpaid what HR actually writes on the sheet** (SPEC §9 A48)? **The leave entry screen shows it happening**: pressing *Annual* puts `AL` in the code box and says it is a suggestion, pressing *Maternity* puts nothing there and says the legend has no letter for it. A convenience on the screen, not a mapping — HR overrides it in front of us and the row records what they typed|HR|Nothing — the row records what HR typed|
|Is there one leave card per leave type per employee, or one card covering all types? The card has no type column|HR|Nothing structural|
|**What is the note in the top-left of the attendance sheet** (SPEC §9 A41)? A close-up photo may answer the schedule question. The sheet renders the cell empty and marked unread until it is read|HR|The sheet's top-left cell only|
|**How many pages is the sheet, and what is headcount** (SPEC §9 A39)? 30 rows to a page is the assumption the renderer uses|HR|Page breaks on the printed sheet|
|2026 public holidays including Melaka state|HR|Calendar|
|**How early before a shift, and how late after it, does a punch still belong to that day?** (SPEC §9 A30). The seeded 240 minutes is a guess; real punches settle it|The first real capture|Daily attendance|
|**Which group runs which shift** (SPEC §9 A31)? The seeded schedules are provisional and marked so in the database|HR|Schedule|
|**The group codes themselves are invented.** DAY-PROD, NIGHT-PROD and OFFICE came from the sample spreadsheet, not from HR — they are replaced by whatever the real employee list carries, not corrected|HR|Schedule|
|**How is a mistaken correction undone?** A guard entry made for the wrong employee cannot be edited or deleted, and SPEC §3 does not say what should replace it|HR, then management|Corrections|
|**What happens when the guard confirms the wrong employee?** The screen shows the name back before he confirms — built, and the largest thing on it — and the row is still un-removable once made (SPEC §3). **No void path exists.** Piece 6 is where it has to be answered|Management, then HR|Step 10, piece 6|
|**Who is on the guard roster?** `screen_user` holds two placeholders marked provisional, and the guard screen says so on its face (SPEC §9 A51). Replaced by an UPDATE, not corrected|HR|Nothing — they are rows|
|**How often does a missed punch actually happen?** It decides whether a signed slip is a formality or whether the real question is enrollment quality|HR|The correction evidence decision|
|**When is the Excel sheet printed and filed?** Monthly on a period boundary is the assumption. Does not block the build|HR|Nothing structural|
|**How long may the device be silent before somebody should be told** (SPEC §9 A43)? 15 minutes is the assumption, against a device that polls every 10 seconds|Zi Fong, then HR|Nothing — it is a row|
|**How long into a running shift is no punch at all a fault** (SPEC §9 A44)? 60 minutes before a punch is due and 180 minutes of silence is the assumption. A slow start and a genuine fault look the same until HR says where the line is|HR|Nothing — it is a row|
|**Who receives the alert, and how, once the site has a real monitoring path?** Today it is a scheduled job that exits non-zero and writes a status file. This is HR-facing infrastructure and is deliberately not the supervisors' notification channel|Zi Fong, then management|Nothing — the transport is one line of cron|
|**Should a serial that pushes but is not on the allowlist raise an alarm, or stay a notice?** It is a notice today, because a stray probe should not page anybody — but it is also how a real device went unwatched for four hours|Zi Fong|Nothing structural|
|**Does this firmware accept `C:{id}:{CMD}`, and does it act on it** (SPEC §9 A46)? One `REBOOT` settles it. Also unknown: whether more than one command may be sent per poll, and what the device does with an id it does not recognise|The device, then ZKTeco supplier|Step 8's format, not its shape|
|**Does the device report a result as `ID=&Return=&CMD=`, and is `Return=0` success** (SPEC §9 A47)? The same `REBOOT` settles it. Also unknown: whether the id comes back as sent, what a failure code looks like, and whether a result arrives at all for a command that reboots the device before it can answer|The device, then ZKTeco supplier|Step 8's format, not its shape|
|**What does a cell show when both the arrival and the departure are outside the schedule** (SPEC §9 A38)? The sheet writes both times, slash-separated. Early departure is named in §8 and defined nowhere|HR|The sheet's cells|
|**Does any group rest on a day other than Sunday** (SPEC §9 A42)? If one does, a column stops shading wholly, and the sheet reports it rather than shading part of one|HR|Whole-column shading|
|Confirm the site timezone (SPEC §9 A32), and that a PIN is never reassigned to another employee while old punches still matter (A33)|Zi Fong / HR|Corrections, daily attendance|
|**Can SQL Account import a file, or is it keyed by hand?** Sample export if yes|Accounts|Milestone 2 deliverable|
|What do `DW` `MT` `MR` `CL` `HL` `EX` `PT` `AD` `LS` `OOB` mean, and which are actually used? **`CL`, `HL` and `MT` have candidates by content from the leave application form — Compassionate, Hospitalization, Maternity (SPEC §6). Candidates for Accounts to confirm, not decided**|Accounts|Milestone 2|
|Is `Lateness` the raw total or only the deductible portion?|Accounts|Milestone 2|
|Is `Work Hours` gross or net of break — and which break?|Accounts|Milestone 2|
|**Where does overtime come from?** A form, an approval, who calculates it|Accounts|Milestone 2|
|**Does Accounts key `Basic Rate` themselves?** It is on the payroll entry screen and this system never holds pay, so the export would otherwise carry a field with no source|Accounts|Milestone 2|
|**What is at the guard post** — a PC, a shared phone, or nothing? And how far from the device?|Zi Fong|The fallback design|
|Where is the device mounted, and is there network and power there?|Zi Fong|Install|
|How many enroll, and when can they be scheduled including night shift?|HR|Enrollment|
|Which employees have worn fingerprints?|HR|Enrollment|
|Which notification channel do supervisors actually use?|Supervisors|Milestone 5|
|Can floor workers use a web form, in which languages, and is a kiosk needed?|HR|Milestone 5|
|**Privacy handling for passport, IC and medical data** — encryption, retention, access logging|Management|Milestone 4|
|**ADMS protocol spec**|ZKTeco supplier|Confirms §12|
|Which encoding does this firmware send ATTLOG and OPERLOG bodies in (SPEC §9 A27)? **Both captures were ASCII end to end with an empty Name field. It settles the first time a non-Latin name is enrolled on the device, not before**|The first non-Latin enrollment|Nothing structural — it is a row|
|**What would a real `Stamp` / `OpStamp` cursor change?** Ours is a row fixed at `9999` and the device echoes it back; it costs nothing today because the device deletes each record once acknowledged (SPEC §12)|ZKTeco supplier|Nothing yet|
|**Read the repeat-verification interval off the device menu** (SPEC §10). It is the floor on how close two genuine punches can be, which matters for a gate pass return|Zi Fong, at the device|Daily attendance, time off|
|**USB export from the device as a manual recovery path if the receiver is permanently lost.** Not a second ingest path — the device buffers and re-pushes across outages. The export is the same ATTLOG records the device pushes, so a fallback is a loader into the raw layer plus a replay, not a format to design around. Unread: the exact file shape, whether biometric templates are included, whether import overwrites or merges, and **whether exporting also clears records from the device**|Zi Fong, at the device|Nothing — recovery only|
|**Are `~MaxAttLogCount=20`, `~MaxUserCount=80` and `~MaxFingerCount=80` per-push batch limits or storage limits** (SPEC §9 A34)? They contradict the datasheet by orders of magnitude|ZKTeco supplier, or the first push of users|Step 8, pushing users to the device|
|**What does the sheet show for a day with one punch** (SPEC §9 A35)? The row records a first in and no last out, because a single punch cannot say which it was|HR|The sheet, step 7|
|**Are late minutes empty or zero on a rest day, a closed holiday and a day with no punch** (SPEC §9 A36)? The row leaves them empty, so a period total cannot mistake a rest day for punctuality|HR, then Accounts|Milestone 3 totals|
|**Can two genuine punches for one employee land in the same second** (SPEC §9 A37)? The daily row counts them as one push repeated. The device's own repeat-verification suppression may rule it out — its interval is unread|The device, then HR|Daily attendance|

**Artifacts still wanted**

**One closed month of attendance sheet with the matching SQL Account entries** — it lets the whole chain be reconciled end to end before anything is built.

**The medical treatment slip does not exist and is off this list.** HR confirms the only attendance forms are the leave application and the gate pass, and the individual time-off record — seven ruled rows — says nothing about slips. `Medical Treatment` on the gate pass is one of four category ticks and nothing more (SPEC §5). **Step 5 is no longer waiting on a form.**

The punch card itself is not needed. Its only unique content is the leave codes HR writes on it, and those already appear on the sheet.

**Requesting artifacts instead of interview time worked, and is the default from here.**

**Features, deliberately deferred**

|Item|Note|
|---|---|
|Leave entitlements and balances|Milestone 5|
|Enforcing the leave rules stated on the form (SPEC §6) — notice period, approval before the leave, attachments|Milestone 5|
|Approval workflow|**Approval links must be single-use, expiring and bound to one supervisor** — a messaging link can be forwarded, and the endpoint is public. **HR-entered applications never re-trigger approval**; the paper form was already signed, and asking a supervisor twice makes them stop responding|
|Notification channel|Telegram's bot API is free and quick. WhatsApp Business API costs per message and needs verification through Meta. WhatsApp is the common channel locally. **Settle with the supervisors before building**|
|Overtime input path|Source unknown. May be needed for Milestone 2|
|Government-application field set|Milestone 4|
|Reports beyond the three known summaries|Milestone 3|