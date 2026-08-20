# HR Attendance — Spec

What the system must do. Written in the language of the work.

The database schema is not in this document. Claude Code decides tables from what is written here. §12 is the one exception — the device protocol is fixed by the firmware and cannot be redesigned.

---

## 1. What this replaces

Today both HR and Accounts read the same punch card, at different times and at different detail.

1. Employees punch a card. The machine prints red when the punch is outside schedule.
2. **Daily:** HR reads the card and the leave forms, and fills the Daily Workers Attendance sheet by hand — a tick for present, the actual punch time when outside schedule, leave codes. Leave codes also get written onto the card.
3. **At cut-off:** Accounts reads the punch card, and HR's reports where they help. **Accounts prioritises the card over the attendance sheet.**
4. HR separately compiles the late coming and time-off summaries, signs them, and sends them to Accounts for deduction.

**That duplication is the target.** One capture, read by both.

**Payroll is not built here.** Accounts owns payroll in SQL Account. This system captures attendance and leave and hands it over.

**Fully separate from Production Tracking** — separate codebase, separate database, no integration. Passports, ICs and medical certificates never enter that system.

---

## 2. Employees

|Attribute|Notes|
|---|---|
|Employee number|**4 digits, zero-padded** — `0090`, `0657`, `1627`. Forms writing `090` are padded on entry. The padded form is authoritative|
|Name||
|Section|A column on the attendance sheet — PACK ASSY, QC, MAINT, PROJECT DOOR, WAREHOUSE and others|
|Role|Row colour on the sheet — Management/Office, Production Assistant, HOD/Supervisor, QA/QC, Assistant Supervisor, Charge Hand|
|Group|Decides schedule and break length|
|Active and left dates|**Stored as dates, never a boolean**|
|Device PIN|The device's own user identifier|

- **`EMP-1001` is not used and never was.** The number Accounts and HR already print on every document wins.
- **The employee number is stored exactly as given, with no padding applied on write.** A separate matching key handles the padding, so a wrong assumption about the format is corrected by remapping rows rather than by a schema change.
- **A device PIN is not an employee number.** It is stored as a string, exactly as the device sends it, with no lookup at capture time.
- **Employees are created in the application and pushed to the device**, never typed on the device.
- Schedules and employment status are **effective-dated**. Re-rendering a past period uses the schedule and headcount that were in force then, not today's.
- **The leave card's and the leave application form's "Department" is the attendance sheet's Section.** One field, under two names on three pieces of paper — not a second attribute.
- **"Staff number" on the leave application form and the gate pass is the employee number.** Same field, another name.

---

## 3. Attendance capture

**The device records punches and nothing else.** It cannot record leave, gate pass or treatment slips. Those have their own entry paths and are never inferred from punch data.

**Three layers, each rebuildable from the one above:**

1. **Raw request** — every HTTP request from the device, stored whole and append-only. Never validated, never rejected, never deleted.
2. **Parsed punches** — one row per punch line. Disposable: rebuilt by replaying the raw layer.
3. **Daily attendance** — one row per employee per day. First in, last out, late minutes, status.

**Every period total is a query over the daily rows.** Half-month, 16th-to-15th, calendar month — all of them. Unresolved period boundaries therefore cost nothing structural.

**A parser change means replaying the raw layer, never re-collecting from the device.**

### Corrections

A missed, failed or wrong punch is corrected by **adding a row to a separate adjustment layer**. Punch data is never edited. Every derived figure is punches plus adjustments, and every adjustment carries who made it and why.

**Two correction paths, and the difference matters:**

|Path|Who|Time|Reasons available|
|---|---|---|---|
|**Guard entry** — biometric failed at the door|Security guard, in the application|**Server-stamped at the moment of entry. The guard cannot type a time and cannot backdate**|Biometric failed, not enrolled|
|**Retroactive** — device down, forgotten punch|HR only|Entered|Any|

**The guard records that the employee is standing in front of him. He does not record what the employee says happened earlier.** Without that rule, guard entry is buddy punching with a log — an employee names a favourable time and the guard types it. The log alone does not prevent it; removing the time field does.

**Accepted residual risk:** a colluding guard can still enter a punch for an absent employee. Software cannot prevent that. But it is attributable to one named person, counted per employee, and cannot be backdated — against a punch card, where the same act is free, invisible and unattributable.

**A correction lands on an attendance day the same way a punch does** — through the schedule in force, so a night-shift correction after midnight belongs to the shift's day, not the clock's. It is derived, and rebuilt when a schedule is corrected.

**Every manual punch is marked on the generated sheet and counted per employee per period.** An unmarked manual punch is indistinguishable from a biometric one and recreates the hole the device exists to close. **A rising count for one employee means a bad enrollment or a process being worked around** — both need acting on.

**No punch and an absence are not the same thing** and are never collapsed into one status. No punch is a fact; absence is an HR judgement.

---

## 4. Schedule, breaks and calendar

Stored per group, effective-dated: start, end, break.

|Value|Current setting|Confirmed?|
|---|---|---|
|Day shift|08:00–17:30|**Assumed** — start/end per group|
|Night shift|19:30–04:30|From the sheet note|
|Production break|12:30–13:15|From the sheet note|
|Office break|12:30–13:30|From the sheet note|
|Grace period|0 minutes|**Assumed** — grace period|
|Rest day|Sunday|From the sheet legend|
|Public holidays|Malaysia federal plus Melaka state|2026 list needed|

**Break length differs by group, so shift assignment is per group, not global.**

**A shift that ends after midnight says so on its row.** Night shift 19:30–04:30 ends the following day, and the schedule row carries that fact. **The attendance day is the shift's, not the clock's** — a night-shift punch at 04:35 on Tuesday belongs to Monday, because Monday's shift window contains it. Nothing downstream infers a crossed midnight by comparing two times.

**Rest day is a column on the schedule row.** A group that rests on another day is a row, not a code change.

Public holidays and rest days shade whole columns on the sheet, driven by the calendar, never entered per employee.

**The calendar is one row per date**, and it carries two separate facts: what the day is, and **whether the factory actually closes for it**. A gazetted public holiday that is worked is a real case, and only the second fact shades the column.

**Two ways to change the calendar, and they do not fight.** A year is re-uploaded whole. A single date is changed by a row in a separate adjustment layer, which survives a re-upload and is applied on top of whatever the new upload says. **A re-upload reports every adjustment that still stands.** Discarding an adjustment silently would lose a decision somebody made deliberately.

---

## 5. Late coming and time off

### Late coming

- Late minutes accumulate across the period.
- **Threshold: 30 minutes or more accumulated is deducted from salary.** Below 30 is exempt. Verified — an employee at exactly 30 minutes was deducted.
- In force since July 2012 salary.
- Produced as two documents: a per-half working summary, then a combined record signed by HR/Admin and Acct/Payroll.
- Enters SQL Account as the `Lateness` hours field.

**Showing a punch time and computing a deduction are separate things.** Displaying first in and last out needs nothing but punches. Computing late minutes needs only the scheduled start. **Applying a deduction needs the grace period, the threshold, and a management decision** — a figure on screen is not a deduction.

### Time off

The gate pass carries: employee name, staff number, department, date, a category tick, destination, reason, out time, in time, and four signatures.

|Field|Notes|
|---|---|
|Category|One tick of four — **Official · Personal · Medical Treatment · Others**|
|Destination|Where the employee is going|
|Out time, in time|Written on the paper form; the hours follow from them|
|Signatures|**Four, not one** — applicant, immediate supervisor, Head of Dept, HR. The same chain as leave, and it does not differ by category (§6)|

- **Hours are not written on the gate pass.** Out time and in time are, and the hours are computed from the pair. This is the reverse of leave, where the number of days is written on the form and is stored as given, never recomputed (§6).
- **The out and in times are the guard's on paper and HR's in the system.** The guard fills them in at the gate on the paper form; HR types both when the form is entered. **This is not the guard entry path in §3** — that path corrects a failed biometric punch, is server-stamped, and has no field for a typed time. Two different acts: a gate pass time is HR transcribing an authorised absence off paper, a guard entry is a punch standing in for one the device did not take.
- **Medical Treatment on the gate pass is the exit authorisation, not the treatment record.** It does not replace the medical treatment slip, and the two are not one entry.
- **The summary combines gate pass hours and medical treatment slip hours into one total per employee.**
- Same 30-minute threshold, deducted from the next due salary.
- Up to 5 treatment slips per employee per month appear on the summary.

### Periods currently in use, which do not align

|Item|Period|
|---|---|
|Late coming|16th → 15th|
|Gate pass / time off|1st → month end|
|Payroll entry|Half-month, 1–15 and 16–end|

Cut-offs stated on the sheet: time off and late coming close on the 10th, salary and OT on the 15th, annual leave forms on the 20th. **Whether these are data periods or submission deadlines is unconfirmed.**

---

## 6. Leave

**Leave is entered, never derived.** Today HR writes leave codes onto the punch card at the same moment as attendance. That one combined step becomes two separate ones.

**There are two leave vocabularies and they are not the same list.** What an employee applies for is on the leave application form. What HR writes on the sheet and the card is the legend code. The form is the request; the legend is the record. **Both are stored, as two separate fields on the leave record, and neither is derived from the other.**

### Applied for — the leave application form

|Type on the form|Notes|
|---|---|
|Annual Leave||
|Sick Leave|Sick certificate attached|
|Compassionate Leave||
|Hospitalization||
|Ind. Accident Leave (SOCSO)||
|Maternity Leave||
|Unpaid Leave|Reason required|

The form also carries: staff number, department, date of application, period from, period to, number of days, and the applicant's signature.

### Written on the sheet — codes from the sheet legend

|Code|Meaning|
|---|---|
|AL|Annual leave|
|MC|Medical leave|
|EL|Emergency leave|
|UL|Unpaid leave|
|PH|Public holiday|
|AB|Absent — cut 3 times _(the calculation itself is unconfirmed)_|
|SS|Suspended|
|T / C|Temporary / Contract|

**The two lists do not line up, and that is a fact about the paper, not an open question:**

- **Compassionate Leave, Hospitalization, Ind. Accident Leave (SOCSO) and Maternity Leave have no legend code.** They are applied for and the legend has no letter for them.
- **EL — emergency leave — has no box on the form.** HR writes it on the sheet; nobody applies for it under that name.

Either field on a leave record can therefore be empty: a form type with no code, or a code HR wrote with no form behind it. **Filling one in from the other would be inventing a mapping that the paper does not contain.**

### Approval

**Applicant signs, immediate supervisor verifies, Head of Dept approves, HR reviews.** One chain, and **it does not differ by leave type** — the leave application form and the gate pass (§5) carry the same four signatures whatever the type or category. **Milestone 1 is HR typing a form that has already been signed on paper.** Recording who signed, and routing an approval to a person, are both Milestone 5.

### Other rules

- **Half-day leave exists** and is stored as a fraction.
- **Leave naming is not a free choice** — every type must map onto SQL Account's Pay Days and No Pay Days codes.
- A leave record carries its SQL Account code from the start, left empty until the mapping is answered.
- **The date of application is recorded, separately from the leave dates.** Both the leave card and the application form carry it: when leave was asked for and when it was taken are different facts, and the paper keeps both.
- **The number of days is recorded as given, not computed from the range.** The card states it per line and the form has a field for it. A half day, and a non-working day inside a range, both mean the count and the span are not the same number — deriving one from the other would overwrite what HR wrote.
- **Entitlement rules and balances are not designed, and neither is the approval workflow.** The chain is known (above); nothing routes it yet. Milestone 1 is entry only: employee, applied-for type, sheet code, date or range, number of days, date of application. The leave card's balance, entitlement, comments and remarks are marked for office use and stay out with them.

---

## 7. The attendance sheet

The Daily Workers Attendance sheet, in HR's existing layout.

- **Generated output. Regenerated on demand. Never annotated by hand or edited in place.** A sheet HR writes on cannot be regenerated without losing what they wrote, and leaves that data invisible to everything downstream.
- Everything on it comes from stored data: punches, corrections, leave, schedule, calendar.
- A cell holds **a tick when the punch is on schedule, the actual punch time when it is outside schedule**, or a leave code.
- Rest days and public holidays shade as whole columns.
- Manual punches are marked.

**Per-day punch detail is available for any employee and day.** This is what replaces reading the punch card — for HR and for Accounts both.

---

## 8. Accounts export

One record per employee per half-month, matching the SQL Account payroll entry screen field for field.

**Pay Days:** `DW` `PH` `AL` `MC` `MT` `MR` `CL` `HL` `EX` `PT` `AD` **No Pay Days:** `LS` `NPL` `AB` **Overtime (hours):** 1.0 · 1.5 · 2.0 · 3.0 — **(days):** R/D & P/H, Public Holiday **Hours:** Work Hours · Lateness · Early Departure · No Pay Hour **Other:** OOB (days) · Working Days · Basic Rate

- The Hours block and Overtime come from punches. Pay Days and No Pay Days come from leave records.
- **Whether SQL Account imports a file or is keyed by hand is unknown.** Until answered, the deliverable is a screen-and-print summary in the entry screen's field order.
- **Overtime has no known source.** The device shows time present, which is not approved overtime. An input path may be needed and is not yet planned.

---

## 9. Assumed values

Built on, demonstrated, and corrected from what HR says when they see it working. Each one has a matching question in BUILD.md's parked list. **Every one is a row in a table. Correcting one is an update, not a code change.**

|#|Assumed|
|---|---|
|A1–A2|Day shift 08:00–17:30|
|A4|Grace period 0 minutes|
|A7|Work hours = time present minus break|
|A8|Late coming period runs 16th → 15th|
|A9|Time off period runs 1st → month end|
|A10|Payroll halves are 1–15 and 16–end|
|A11|Late deduction threshold ≥30 min, inclusive|
|A12|Threshold applies to the combined 16→15 total|
|A13|Time off threshold ≥30 min, gate pass and slips combined|
|A14|Leave codes AL, MC, EL, UL, PH, AB, SS — the sheet legend, now known to be an incomplete list: four form types have no code at all (§6)|
|A15|Half day stored as 0.5|
|A19|Device PIN equals the employee number|
|A21|The device pushes the PIN with leading zeros intact|
|A25|The guard can reach the application where failures happen|
|A26|The handshake answers these options: Stamp, OpStamp, ErrorDelay, Delay, TransTimes, TransInterval, TransFlag, TimeZone, Realtime, Encrypt|
|A27|An ATTLOG body decodes as UTF-8, else GBK, else Latin-1|
|A28|An employee number's matching key is the number padded on the left to 4 characters with zeros|
|A29|An employee number is exactly 4 digits; anything else stops an import until it is accepted deliberately|
|A30|A punch belongs to an attendance day if it falls within 240 minutes before that day's shift start or 240 minutes after its end|
|A31|Which group runs which shift — DAY-PROD day, NIGHT-PROD night, OFFICE day with the office break|
|A32|The site's timezone is Asia/Kuala_Lumpur, and a guard entry's server stamp is read as a local punch time through it|
|A33|A device punch belongs to the employee its PIN mapped to on the punch's own date, and to the group that employee was in on that date|

**Assumptions about presentation and rules are free to make. Assumptions about identity and schema are not.** A19 and A21 are both isolated in the device-user mapping, so a wrong PIN format is corrected by remapping rows.

A32 and A33 are what turn a correction and a device punch into rows about the same person on the same day. A32 is a row; A33 is the date the mapping is read on, and it matters only when a PIN is reassigned or an employee changes group mid-shift.

A30 is the width of the attendance-day window, and it is per schedule row. It is a guess until real punches show how early people arrive and how late they leave. A31 is provisional in the strongest sense: the group codes came from a sample list, not from HR, and every seeded schedule is marked provisional in the database.

A28 and A29 are the employee number's shape and its key. §2 settles that the stored number is never padded or stripped and that a separate key does the matching; it does not settle what a number may look like, and BUILD.md parks that question. Both are rows, so correcting them is an UPDATE and a rekey — no stored number is touched.

A26 and A27 are the two the receiver itself runs on. §12 fixes that the handshake answers `Key=Value` lines and that names are GBK on many builds; it does not fix which options or which encoding. Both are rows, so the first real handshake and the first real body correct them with an UPDATE.

---

## 10. Device configuration

- **Face and fingerprint only.** PIN-alone and card verification disabled. Punch cards are currently shared between employees; a PIN or a card reproduces that, a biometric does not. The PIN still exists as the device's user identifier — what is disabled is the PIN as a credential.
- **A super administrator is registered before deployment.** Until one exists the device menu is open to anyone who walks up to it, and the verify mode or the server address can be changed by hand.
- **The device has one Cloud Server setting and this system holds it.** ZKTeco's own software competes for the same setting — whichever holds it receives the pushes and the other receives nothing. Their software may be used once for bulk enrollment, then the address is repointed here. It is not installed, licensed or maintained as part of the running system.
- **If anyone repoints that setting, capture stops silently while the device keeps recording locally.** The ingestion alert is the only thing that catches it. Record the correct value somewhere findable.
- Device timezone +8. Clock drift corrupts lateness directly — compare the device's time against the server's arrival time to detect it.

### Fallback when biometrics fail

**The application path is primary** — guard entry, attributable, reason-coded, un-backdatable.

**A shared device password is not allowed as the primary mechanism.** A static secret typed at a wall-mounted terminal, in front of a queue, repeatedly, over months, will be observed. Once one employee knows it, any employee can punch for any other by keying an ID and that password — no card to borrow, no accomplice. Cheaper than the buddy punching this system exists to stop, and the device records only that the method was a password, not who authorised it.

**Permitted only as an interim bridge, and only if the guard has no screen where failures happen.** Conditions, all of them:

- Enrolled only for employees whose biometrics actually fail. Never all staff.
- A password-punch count per employee per period runs from day one.
- Rotated whenever password punches spread to employees with no enrollment problem.
- Retired once the guard has application access.

**A fallback password is temporary.** A failed biometric puts the employee on a pending re-enrollment list; the password is cleared remotely once re-enrollment succeeds. **A password never cleared is the permanent shared secret this rules out.**

---

## 11. Device commands

Employee records are pushed to the device from the application. The device asks for pending commands and reports each result back.

Used for: creating and updating user records, setting and clearing a fallback password, deleting users. Biometric templates are still captured physically at the device.

**A user update replaces the whole record.** Payloads are built from the full record captured from the device. Sending a partial record wipes name, privilege, group and card.

**Password payloads are plaintext over an unauthenticated endpoint.** Purged on completion, never logged. A further reason the receiver stays on the LAN.

Command strings are unverified and get pinned during the first real device capture. The queue itself does not depend on them.

---

## 12. The device protocol — fixed, not designed

**This section is not a design. It is what the firmware does.** It is here because getting it wrong makes the device retry forever or drop a batch of punches silently.

The device is the HTTP client. It pushes; we never poll. All routes under `/iclock/`.

|Route|Response body|
|---|---|
|`GET /iclock/cdata?SN=&options=all&pushver=&language=`|`GET OPTION FROM: {SN}` then `Key=Value` lines|
|`POST /iclock/cdata?SN=&table=ATTLOG&Stamp=`|`OK: {n}`|
|`POST /iclock/cdata?SN=&table=OPERLOG&Stamp=`|`OK`|
|`GET /iclock/getrequest?SN=`|`OK`, or `C:{id}:{CMD}`|
|`POST /iclock/devicecmd?SN=`|`OK`|
|`POST /iclock/cdata?SN=&table=ATTPHOTO`, `POST /iclock/fdata`|`OK`|
|catch-all `/iclock/{rest:path}`|`OK`|

Punch line, tab-separated: `pin, YYYY-MM-DD HH:MM:SS, status, verify, workcode, reserved, reserved`

**Status and verify code meanings are unverified. Do not build logic on them.** The verify field does show which method was used, which is what the password-punch count reads.

**Nothing in this section has been verified against the device.** The vendor spec is not in hand, and no capture has ever been taken. The table is assembled from ZKTeco documentation and community reports — treat every line as unconfirmed.

**Raw capture is what makes building on it acceptable.** Every request is stored whole before anything parses it, so a wrong assumption costs a replay rather than lost punches. **This is the reason the raw layer exists, and why it is never validated or filtered.**

Verify against real traffic at first power-on, then against the vendor spec when it arrives, and update this table in the same task.

**Absolute rules on `/iclock/` routes**

- **Return only `200` and plain text.** A redirect, a `401`, or an HTML error page makes the firmware retry forever or drop the batch.
- Trailing-slash redirects are on by default in the framework and must be turned off — **and the catch-all route kept. Neither alone is enough.**
- No exception handler that returns JSON. No request-body validation — bodies are tab-separated text or raw binary, and a validation failure produces an error status.
- **No auth middleware.** The protocol has no credential mechanism. Access control is network position: device on an isolated segment, firewall permitting only its address, serial-number allowlist that logs unknown serials and still returns `200 OK`. Plain HTTP.
- **Never routed through the tunnel, never exposed beyond the LAN.**
- **Never decode the body at capture.** Store bytes — name fields are GBK on many firmware builds. Decode in the parser.
- **A parse failure never affects the response.** Store, respond `OK`, log it.
- **Never deduplicate, normalise or drop anything at the raw layer.** Devices re-push after a timeout. Deduplicate downstream.

---

## 13. Not allowed

|Never|Because|
|---|---|
|Editing punch data to fix a bad punch|Corrections are separate rows|
|Resolving a device PIN to an employee at capture|Store the string; map downstream|
|Hand-editing the generated attendance sheet|It cannot then be regenerated|
|Collapsing "no punch" and "absent"|One is a fact, the other a judgement|
|A guard-typed punch time|Server-stamped only|
|An unmarked manual punch|It must be visibly countable|
|A fallback password left in place after re-enrollment|That is the permanent shared secret|
|Logging or retaining a password payload|Purge on completion|
|Hard-coding a schedule, grace period, threshold, period boundary, leave code or holiday|These are rows|
|Inferring that a shift crossed midnight by comparing two times|The schedule row states it; the attendance day follows from the shift|
|Discarding a calendar adjustment when the year is re-uploaded|It is a deliberate decision, kept as its own row and reported|
|Padding or stripping the employee number on write|Stored verbatim; a separate key does the matching|
|A partial user update to the device|It wipes the whole record|
|Building overtime|Its source is unknown|
|Designing leave entitlements, the export format, the government-application field set, or reports|Blocked on HR. Stop and say so|
|Storing passport, IC or medical certificate data|Privacy handling undecided|
|Assuming ZKTeco software can run alongside this|One server setting, and this system holds it|

---

## 14. Environment

- Python/FastAPI managed with **uv** · React + Tailwind · PostgreSQL · Docker Compose
- **On-premises, and this is a requirement rather than a preference** — the device pushes over the LAN and cannot reach a cloud host.
- Remote access by tunnel, **for the HR interface only**. Never the device routes.
- Server-observed times are stored with timezone. Device-reported times are stored as the device sent them, alongside the original string, **never converted on the way in**.
- **No migrations until real punches arrive** (see BUILD.md). Until then the database is dropped and recreated.