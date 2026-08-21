# HR Attendance — Spec

What the system must do. Written in the language of the work.

The database schema is not in this document. Claude Code decides tables from what is written here. §12 is the one exception — the device protocol is fixed by the firmware and cannot be redesigned.

---

## 1. What this replaces

Today both HR and Accounts read the same punch card, at different times and at different detail.

1. Employees punch a card. The machine prints red when the punch is outside schedule.
2. HR marks leave codes onto the punch card **a day or two after each form arrives**, and then **transcribes the whole month into the Daily Workers Attendance sheet by hand in one sitting** — a tick for present, the actual punch time when outside schedule, leave codes.
3. **At cut-off:** Accounts reads the punch card, and HR's reports where they help. **Accounts prioritises the card over the attendance sheet.**
4. HR separately compiles the late coming and time-off summaries, signs them, and sends them to Accounts for deduction.

**That duplication is the target.** One capture, read by both.

**Payroll is not built here.** Accounts owns payroll in SQL Account. This system captures attendance and leave and hands it over.

**Fully separate from Production Tracking** — separate codebase, separate database, no integration. Passports, ICs and medical certificates never enter that system.

---

## 2. Employees

|Attribute|Notes|
|---|---|
|Employee number|**Four digits, zero-padded** — `0090`, `0657`, `1627`. Stored exactly as the source wrote it; **the padding happens in the matching key, never on write** (below)|
|Name||
|Section|A column on the attendance sheet — PACK ASSY, QC, MAINT, PROJECT DOOR, WAREHOUSE and others|
|Role|Row colour on the sheet — Management/Office, Production Assistant, HOD/Supervisor, QA/QC, Assistant Supervisor, Charge Hand|
|Group|Decides schedule and break length|
|Active and left dates|**Stored as dates, never a boolean**|
|Device PIN|The device's own user identifier|

- **`EMP-1001` is not used and never was.** The number Accounts and HR already print on every document wins.
- **The employee number is stored exactly as given, with no padding applied on write, and the matching key is that number padded on the left to four characters with zeros.** Those are one rule with two halves, and they are the reason a number written short on paper still finds its employee: `090` is stored `090` and keyed `0090`, which is the same key as `0090` stored in full.
- **A wrong column is caught by the header echo, not by the number's shape.** Accepting `090` means accepting a running number `1` too, so a mapping pointed at the wrong column loads instead of halting. The importer reads the header row only to print it back — `employee_number <- 'S/N'` rather than `'Emp No.'` — and says how many numbers came from numeric cells. **That echo is the check now**, and it is why the importer never matches on header text but always shows it.
- **Four digits, zero-padded, is confirmed rather than assumed** — and the numbers in use have reached about 1500. **Real HR paper prints both forms on one page**: the late coming record carries `090` and `1601` together. So a list writing a number short is not a typo and does not stop an import; it is keyed to four and matched like any other. **What stops an import is a number that cannot be keyed to four** — more than four digits, or something that is not digits at all — and only until somebody accepts it deliberately.
- **A device PIN is not an employee number.** It is stored as a string, exactly as the device sends it, with no lookup at capture time.
- **The device refuses a leading zero in a user ID** (§10, observed). So a PIN can never be `0090`, and the padded employee number is not usable as a PIN as it stands. The dated device-user mapping is what joins the two, which is why this costs nothing: it is rows, not a format to agree on.
- **A PIN with a leading zero is refused on import, and loaded only when somebody says so deliberately.** A list that gives an employee `0142` as a Device ID is describing a PIN the device cannot hold, so the mapping row would never match a punch — **it would look like a working link and silently not be one**, and the employee's punches would go unattributed with nothing on screen to say why. The importer refuses it, names the employee and the value, and `--accept-leading-zero-pins` loads it anyway for a list belonging to another device or a firmware where §10's observation does not hold.
- **Every employee's PIN outcome is reported by name.** Mapped, blank cell, or refused — a count of mappings written cannot tell a correctly skipped blank from a rejected value, and those are different facts about HR's list.
- **Employees are created in the application and pushed to the device**, never typed on the device.
- Schedules and employment status are **effective-dated**. Re-rendering a past period uses the schedule and headcount that were in force then, not today's.
- **The leave card's and the leave application form's "Department" is the attendance sheet's Section.** One field, under two names on three pieces of paper — not a second attribute.
- **The employee number goes by three names on the paper.** The leave application form calls it **Staff no.**; the gate pass carries **no. pekerja** on its name line and **Emp no.** as a field of its own (§5). One field, three labels — not three attributes.

---

## 3. Attendance capture

**The device records punches and nothing else.** It cannot record leave or a gate pass. Those have their own entry paths and are never inferred from punch data.

**Three layers, each rebuildable from the one above:**

1. **Raw request** — every HTTP request from the device, stored whole and append-only. Never validated, never rejected, never deleted.
2. **Parsed punches** — one row per punch line. Disposable: rebuilt by replaying the raw layer.
3. **Daily attendance** — one row per employee per day. First in, last out, late minutes, status.

**A re-pushed punch is one punch, and the daily row is where that is settled.** The device re-pushes a batch after a timeout and both layers above keep every copy (§12). The daily row counts the same employee at the same second once, and records how many copies it dropped — a rising number there is the device retrying, not somebody punching. **A manual punch is never deduplicated:** each one is a separate act by a named person.

**A status on the daily row says what the punches amount to and nothing else** — two or more punches, exactly one, or none. **There is no status meaning absent**, because absence needs leave, and a status list that can express it invites the collapse this section forbids.

**Every period total is a query over the daily rows.** Half-month, 16th-to-15th, calendar month — all of them. Unresolved period boundaries therefore cost nothing structural.

**A parser change means replaying the raw layer, never re-collecting from the device.**

**The device's USB export is not a second format and does not shape any of this.** It is ATTLOG — the same punch lines already arriving over HTTP, from the same device, with the same fields (§10, §12). So a recovery path is a loader into the raw layer and a replay, not a storage design. **And the daily row carries what the device has never heard of:** which attendance day a punch belongs to, which employee a PIN was, the corrections sitting beside the punches, and the schedule that was in force on that date. Shaping storage to match a flat punch dump would throw all four away.

### Corrections

A missed, failed or wrong punch is corrected by **adding a row to a separate adjustment layer**. Punch data is never edited. Every derived figure is punches plus adjustments, and every adjustment carries who made it and why.

**Two correction paths, and the difference matters:**

|Path|Who|Time|Reasons available|
|---|---|---|---|
|**Guard entry** — biometric failed at the door|Security guard, in the application|**Server-stamped at the moment of entry. The guard cannot type a time and cannot backdate**|Biometric failed, not enrolled|
|**Retroactive** — device down, forgotten punch|HR only|Entered|Any|

**The guard records that the employee is standing in front of him. He does not record what the employee says happened earlier.** Without that rule, guard entry is buddy punching with a log — an employee names a favourable time and the guard types it. The log alone does not prevent it; removing the time field does.

**Accepted residual risk:** a colluding guard can still enter a punch for an absent employee. Software cannot prevent that. But it is attributable to one named person, counted per employee, and cannot be backdated — against a punch card, where the same act is free, invisible and unattributable.

**The guard's screen shows the employee's name back before anything is submitted, and that is the verification.** Nothing else stands between a mistyped number and a punch on somebody else's day: the guard is looking at the person, so the name is the one fact he can check, and it is the largest thing on the screen. It is shown *before* the entry exists, because afterwards is too late — **a confirmed entry cannot be undone**, the screen says so in those words, and HR corrects mistakes.

**Who the guard is comes from a row, not a text box.** §3 requires every manual punch to say who made it; a typed name is attribution that cannot be checked against anything and spells differently every time. The guard picks himself from the list once and the browser remembers it. **This is not a login** — nothing is checked and nothing is secret, and access control stays network position until Milestone 5 (§14).

**A correction lands on an attendance day the same way a punch does** — through the schedule in force, so a night-shift correction after midnight belongs to the shift's day, not the clock's. It is derived, and rebuilt when a schedule is corrected.

**A correction cannot be deleted, and that binds the employee list too.** Reloading the employee list wholesale is refused while any manual punch exists, the same way it is refused while a leave record or gate pass does (§5, §6) — and for a stronger reason: those are forms that could in principle be typed again, and a correction is an act somebody performed.

**Every manual punch is marked on the generated sheet and counted per employee per period.** An unmarked manual punch is indistinguishable from a biometric one and recreates the hole the device exists to close. **A rising count for one employee means a bad enrollment or a process being worked around** — both need acting on.

**No punch and an absence are not the same thing** and are never collapsed into one status. No punch is a fact; absence is an HR judgement.

### The ingestion alert

**Capture stopping is silent by design** — the device retries quietly and keeps its records (§12), so a receiver that has been unreachable since Tuesday looks exactly like a factory where nobody punched. The alert is what turns that silence into a warning.

**Two silences, and they are different failures:**

|Silence|What it means|When it is checked|
|---|---|---|
|**Contact** — nothing at all from the device|The device is off, the network is down, or the Cloud Server setting moved (§10)|Always. The device polls every few seconds whether or not anybody punches, so this is checkable at 3am on a Sunday|
|**Punches** — the device is talking, but no punch has arrived|Something between the reader and the record is broken|Only while a shift is running on a day the calendar says the factory is open|

**A single "time since the last punch" would be both useless and dangerous**: it alarms every weekend and every public holiday, and it stays quiet when the receiver is unplugged on one. Both thresholds are rows, and so is whether punches are checked at all on a closed day (§9 A43–A45).

**The alert reads the database and never asks the device anything.** That is what lets it answer during the outage it exists to catch. It does not re-request a batch and does not recover anything — the device does that by itself.

**The alert watches the serials on the allowlist, and the allowlist says which of them are expected to be talking.** A device is not simply on or off the list: it is **live**, **knowingly down** — out for repair, not yet mounted — or **retired**, like a test serial that has served its purpose. Only a live serial is alerted on, and which states are alerted on is a row rather than a branch.

**A serial that stops being watched keeps its row**, its label, its state, when the state changed and why. **Deleting it is not the way to silence it** (§13): the raw layer holds its requests forever, and a list that has forgotten a device cannot say why it went quiet. Standing a device down also clears whatever alert it had raised, because **a permanent known alert is what teaches people to ignore the alert** — which would cost more than the noise it saves.

A device that is capturing but was never added to the list is a device nobody is watching, so the check also reports serials that have pushed and are not on it.

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
- **Threshold: 30 minutes or more accumulated is deducted from salary. Confirmed, and inclusive:** an employee at exactly `0 hours 30 minutes` appears on the deduction list. Below 30 is exempt.
- In force since July 2012 salary.
- Produced as two documents: a per-half working summary, then a combined record signed by HR/Admin and Acct/Payroll.
- Enters SQL Account as the `Lateness` hours field.

**Showing a punch time and computing a deduction are separate things.** Displaying first in and last out needs nothing but punches. Computing late minutes needs only the scheduled start. **Applying a deduction needs the grace period, the threshold, and a management decision** — a figure on screen is not a deduction.

### Time off

The gate pass carries, as the form has it:

|Field|Notes|
|---|---|
|Name / no. pekerja|One line on the form|
|Emp no.|**A separate field of its own**, beside the name line|
|Date||
|Out time, in time|Written on the paper form; the hours follow from them|
|Category|One tick of four — **Official · Personal · Medical Treatment · Others**|
|Reason|Why, in words|
|Destination|Where the employee is going|
|Signatures|**Four** — applicant, verified by the immediate supervisor, approved by the Head of Dept, reviewed by HR Dept|

**There is no department on the gate pass.** The leave application form and the leave card have one (§2, §6); this form does not, and the employee's section is looked up rather than transcribed.

- **Hours are not written on the gate pass.** Out time and in time are, and the hours are computed from the pair. This is the reverse of leave, where the number of days is written on the form and is stored as given, never recomputed (§6).
- **The out and in times are the guard's on paper and HR's in the system.** The guard fills them in at the gate on the paper form; HR types both when the form is entered. **This is not the guard entry path in §3** — that path corrects a failed biometric punch, is server-stamped, and has no field for a typed time. Two different acts: a gate pass time is HR transcribing an authorised absence off paper, a guard entry is a punch standing in for one the device did not take.
- **The summary totals gate pass hours per employee**, and nothing else feeds it.
- Same 30-minute threshold, deducted from the next due salary.

### The individual time-off record

One per employee per period, from the real form. **Seven ruled lines**, and a line carries:

|Field|Notes|
|---|---|
|Date||
|Reason out|**The gate pass category** — the specimen reads `PERSONAL` (§5, the four ticks)|
|From, to|The out and in times off the gate pass|
|Hours|Computed from the pair, as on the gate pass|

Under the lines: **a total of hours taken, and the employee's signature.**

**There is no medical treatment slip.** HR confirms the only attendance forms are the leave application and the gate pass, and this record — seven ruled rows — says nothing about slips. **`Medical Treatment` on the gate pass is one of four category ticks and nothing else**: it is why somebody left the premises, not a second document with its own count.

### Periods currently in use, which do not align

|Item|Period|
|---|---|
|Late coming|16th → 15th — **confirmed: the record states its own period, 16/12/2025 to 15/01/2026**|
|Gate pass / time off|1st → month end|
|Payroll entry|Half-month, 1–15 and 16–end|

**The late coming period is settled and the other two are not.** The time-off record's period is blank on the specimen, and the payroll halves come from the entry screen rather than from a period anybody has stated.

Cut-offs stated on the sheet: time off and late coming close on the 10th, salary and OT on the 15th, annual leave forms on the 20th. **Whether these are data periods or submission deadlines is unconfirmed** — and the late coming record's own header shows the 16th → 15th period running alongside a 10th cut-off, so at least there the cut-off is a deadline and not a boundary.

---

## 6. Leave

**Leave is entered, never derived.** Today HR writes leave codes onto the punch card at the same moment as attendance. That one combined step becomes two separate ones.

**There are two leave vocabularies and they are not the same list.** What an employee applies for is on the leave application form. What HR writes on the sheet and the card is the legend code. The form is the request; the legend is the record. **Both are stored, as two separate fields on the leave record, and neither is derived from the other.**

### Applied for — the leave application form

**Nature of leave is one tick**, in the form's own order:

|Type on the form|Notes|
|---|---|
|Annual||
|Compassionate||
|Hospitalization||
|Ind. Accident (SOCSO)||
|Sick|Sick certificate attached|
|Maternity||
|Unpaid Leave|**Carries a Reason of its own** on the form, unlike the six ticks above|

The rest of the form, as it is laid out:

|Field|Notes|
|---|---|
|Name of applicant||
|Staff no.||
|Department|The attendance sheet's Section (§2)|
|Date|When the form is made out — the date of application|
|Period from, to||
|No. of days|Stated on the form, and stored as stated (below)|
|Signature of applicant||
|Verified by|Immediate Supervisor|
|Approved by|Head of Dept|
|**For office use only**|**Human Resource Dept, with a date of its own** — and **Operation Manager**|

**Policy stated on the form itself:**

- **Leave is applied for 7 days in advance and approved before it is taken.** Compassionate leave is the exception to the seven days.
- **Sick leave attaches a sick certificate.**
- **Unpaid leave attaches supporting documents**, as well as the reason.

These are the factory's rules, printed where the employee signs. Nothing in the system enforces them — HR types forms that were already accepted on paper, whatever the dates on them say. Enforcing them is Milestone 5.

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

- **Compassionate, Hospitalization, Ind. Accident (SOCSO) and Maternity have no legend code.** They are ticks on the form and the legend has no letter for them.
- **EL — emergency leave — has no box on the form.** HR writes it on the sheet; nobody applies for it under that name.

Either field on a leave record can therefore be empty: a form type with no code, or a code HR wrote with no form behind it. **Filling one in from the other would be inventing a mapping that the paper does not contain.**

### Approval

**Five signature boxes on the leave form:** the applicant signs, the immediate supervisor verifies, the Head of Dept approves, and then the office-use block carries **Human Resource Dept with a date of its own** and **Operation Manager** beside it.

**The two forms do not carry the same chain.** The gate pass has **four** — applicant, supervisor, Head of Dept, HR (§5). The leave form has **five**, because the Operation Manager signs it and does not sign a gate pass. **Leaving the factory for an afternoon and taking leave are not authorised by the same people**, and a system that stored one chain for both would have to invent a signature or drop one.

**Within each form the chain does not differ**: no leave type routes differently, and no gate pass category routes differently.

**Milestone 1 is HR typing a form that has already been signed on paper.** Recording who signed, and routing an approval to a person, are both Milestone 5.

### Other rules

- **Half-day leave exists** and is stored as a fraction.
- **Leave naming is not a free choice** — every type must map onto SQL Account's Pay Days and No Pay Days codes.
- A leave record carries its SQL Account code from the start, left empty until the mapping is answered.
- **The date of application is recorded, separately from the leave dates.** Both the leave card and the application form carry it: when leave was asked for and when it was taken are different facts, and the paper keeps both.
- **The number of days is recorded as given, not computed from the range.** The card states it per line and the form has a field for it. A half day, and a non-working day inside a range, both mean the count and the span are not the same number — deriving one from the other would overwrite what HR wrote.
- **A screen showing a leave record says the count is the form's, and where it differs from the range, shows both numbers.** Not a discrepancy to reconcile: 1½ days over a two-day range is a half day, and a reader who sees only one of the two numbers will eventually correct the "wrong" one. Saying which number came from paper is what stops that.
- **Entitlement rules and balances are not designed, and neither is the approval workflow.** The chain is known (above); nothing routes it yet. Milestone 1 is entry only: employee, applied-for type, sheet code, date or range, number of days, date of application. The leave card's balance, entitlement, comments and remarks are marked for office use and stay out with them.

**A leave record holds:** employee, applied-for type, sheet code, period from and to, the number of days as the form states it, the date of application, a reason where the form asks for one, the SQL Account code left empty, and who typed it. **It holds no balance, no entitlement and no signature** — the paper was signed before it reached HR, and recording who signed is Milestone 5.

**Reloading the employee list never deletes what HR typed.** Daily attendance is cleared with the list because it is rebuilt from punches; **a leave record and a gate pass are forms somebody typed off paper and cannot be rebuilt from anything**, so a wholesale reload is refused while any exist and the list is corrected in place instead.

**A gate pass record holds:** employee, date, one of the four categories, reason, destination, out time, in time, and who typed it. **The hours are generated from the two times by the database**, so there is no field to type them into — the same shape as the guard entry that has no field for a time (§3). **There is no department field**, because the form has none.

---

## 7. The attendance sheet

The Daily Workers Attendance sheet, in HR's existing layout.

**HR files it as the official record, and that is what it is for.** It is not working paper and it is not a convenience view of something else — the filed sheet is the source of truth HR keeps, and Accounts reads it.

**Two outputs, and neither replaces the other:**

- **A screen, which is the system.** Regenerated on demand, always current with the stored data.
- **An Excel file in HR's existing layout, which is the record.** That is the artefact that gets filed.

**One render stands behind both, and behind the terminal too.** The sheet is built once and every output draws that same object; a cell's text, whether it is a tick, a time or a code, whether a person entered it, and whether its column shades are all decided in that one place. **An output chooses fonts, colours and widths and nothing else.** The screen and the filed file therefore cannot come to differ about what a day says — not because they are kept in step, but because there is nothing to keep in step.

**The screen carries everything the file carries except how it prints.** Page breaks, landscape, fit-to-width and the legend's own page are facts about paper; the screen scrolls instead. Every mark a reader interprets — cells, shading, the manual asterisk, the legend, the notes — is on both.

**The file's freeze panes and repeating print titles are not one of those exceptions.** They exist because a row of ticks with no name beside it and no day number above it cannot be read, and that is as true on a screen as on paper: the identifying columns stay put while the days scroll, the day-number and weekday rows stay put while the employees scroll, and the grid scrolls within its own bounds so reaching the days at the end of the month does not mean scrolling past everybody first.

**The same period always exports to the same bytes.** Nothing in the file varies with the moment it was made: the document's own timestamps describe the period it covers rather than the export. A filed record that differs from a fresh render only in the clock cannot be compared with anything, and comparing it is how anybody checks that a filed sheet still matches what the system holds.

**The file is printed, so it carries a page setup, not just cells.** Landscape, fit to one page wide, and **the employee header and the day-number row repeat on every page** — a page of ticks with no day numbers above them cannot be read at all. A page break every `rows_per_page` rows (§9 A39, a row), and the legend and notes start their own page at the end rather than sitting under whichever employees happen to be above them. **A file whose pagination does not match that row is a defect in the record even when every cell in it is right.**

**This is what makes "never annotated by hand" enforceable rather than a preference.** A filed record somebody writes on is precisely the thing this system replaces: the writing is invisible to everything downstream, and the sheet cannot be regenerated without losing it. Because the screen is always regenerable and the file is only ever printed from it, a correction has one place to go — a row — and the filed copy is reprinted rather than amended.

**HR enters everything through the screen. The Excel file is export only and is never re-uploaded or read back in.** The file travels one way. **An edited sheet coming back would put a correction in a cell instead of a row**, which is the whole thing the never-annotated rule exists to prevent — and it would arrive with no author, no reason and no way to rebuild it.

**Because the sheet is generated it is always current, and the monthly fill-in disappears.** Today HR marks leave onto the punch card a day or two after each form arrives, and then transcribes a month of cards and forms into the sheet in one sitting (§1). **That sitting is transcription cost, not a requirement.** Entry timing does not change — the forms still arrive when they arrive and are still entered when they are entered (§6) — but nothing has to be copied anywhere afterwards, and the sheet is readable on any day of the month rather than after it.

- **Generated output. Regenerated on demand. Never annotated by hand or edited in place.** A sheet HR writes on cannot be regenerated without losing what they wrote, and leaves that data invisible to everything downstream.
- Everything on it comes from stored data: punches, corrections, leave, schedule, calendar.
- A cell holds **a tick when the punch is on schedule, the actual punch time when it is outside schedule**, or a leave code. **The legend prints the codes from their rows**, so a code HR adds to the legend appears on the sheet without anything being edited.
- Rest days and public holidays shade as whole columns.
- Manual punches are marked.

**Per-day punch detail is available for any employee and day. This is what replaces reading the punch card, and it is Accounts who needs it most.** One employee, one period, every day of it, in one view: punch times, leave codes, and manual punches marked. **Re-pushed copies of a punch are counted and stated rather than listed one by one** — a day that arrived two hundred times would otherwise bury the month around it — and the full list stays available on demand. **Accounts prioritises the card over the attendance sheet today** (§1), because the card is the primary record and shows the detail; this view is what that preference transfers to.

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
|A9|Time off period runs 1st → month end|
|A10|Payroll halves are 1–15 and 16–end|
|A12|Threshold applies to the combined 16→15 total|
|A13|Time off threshold ≥30 min, over gate pass hours — there is nothing else to combine them with (§5)|
|A14|Leave codes AL, MC, EL, UL, PH, AB, SS — the sheet legend, now known to be an incomplete list: four form types have no code at all (§6)|
|A15|Half day stored as 0.5|
|A19|Device PIN equals the employee number — **it cannot be the padded form: the device refuses a leading zero (§10), so a padded number and its PIN differ by that zero**|
|A25|The guard can reach the application where failures happen|
|A27|An ATTLOG body decodes as UTF-8, else GBK, else Latin-1. Still open after two captures: every byte in both was ASCII, and `Name` was empty in both|
|A30|A punch belongs to an attendance day if it falls within 240 minutes before that day's shift start or 240 minutes after its end|
|A31|Which group runs which shift — DAY-PROD day, NIGHT-PROD night, OFFICE day with the office break|
|A32|The site's timezone is Asia/Kuala_Lumpur, and a guard entry's server stamp is read as a local punch time through it. **The device's offset is observed at exactly +8** (§12), which is what that zone produces and what `TimeZone=8` told it|
|A33|A device punch belongs to the employee its PIN mapped to on the punch's own date, and to the group that employee was in on that date|
|A34|`~MaxAttLogCount=20`, `~MaxUserCount=80` and `~MaxFingerCount=80` in the device's option push are **per-push batch limits, not storage limits**|
|A35|A day's first in and last out are its earliest and latest punch. **With one punch there is a first in and no last out** — the device does not label direction, so a single punch cannot say which it was|
|A36|Late minutes are the whole minutes, floored, from scheduled start plus grace to the day's first punch, and are **empty rather than zero** where there is nothing to measure: no punch, a rest day, a closed holiday, or no schedule|
|A37|**Two device punches for the same employee at the same second are one punch.** The extra pushes are counted as copies and not as punches|
|A38|A cell is a tick when every punch that day is inside the schedule, and otherwise **the punch times that fall outside it** — the first in when it is later than the scheduled start plus grace, the last out when it is earlier than the scheduled end, both when both are. On a day with no scheduled start, any punch shows as a time|
|A39|**30 rows to a page**, which is also where the printed page breaks fall. Headcount and the real sheet's page count are unread|
|A40|**One sheet covers one calendar month.** The 10th, 15th and 20th on the paper sheet may be deadlines rather than boundaries (§5)|
|A41|**The note in the sheet's top-left has never been read.** The cell renders empty and is marked unread; nothing is guessed into it|
|A42|A column shades **only when the day is closed for every group on the sheet.** Groups resting on different days would break whole-column shading|
|A43|**15 minutes of total silence from a device is a fault.** The device polls every 10 seconds, so that is about 90 missed polls|
|A44|**A punch is due once a shift has been running 60 minutes**, and no punch for 180 minutes while one is running on an open day is a fault. **Closed days are not checked for punch silence at all**|
|A45|**A serial on the allowlist that has never been heard from is not an outage.** It starts being watched at its first request|
|A46|**A queued command is handed to the device as `C:{id}:{CMD}`, one per poll**, and the device acts on it. From the protocol document; no command has ever been sent to this device|
|A47|**The device reports the result as `ID={id}&Return={code}&CMD={command}`**, with `Return=0` meaning success, posted to `/iclock/devicecmd`. Same document, same lack of evidence|
|A48|**Leave entry offers a sheet code beside the applied-for type** — Annual Leave `AL`, Sick Leave `MC`, Unpaid Leave `UL` — as a convenience on the screen. The other four form types are offered no code, because the legend has none|
|A49|**A day with leave shows its sheet code in the cell.** Where the record has no code, the cell shows what the punches say instead of a letter, and the day's fraction is not shown on the sheet at all|
|A51|**The guard roster is unread.** `screen_user` holds two placeholder guards, marked provisional, and the screen says on its face that the names are placeholders|
|A50|**A serial beginning `GATE-` was invented by a gate or a fixture, not by a device.** Four bare names — `GATE`, `TEST`, `CHECK`, `NOTALLOWLISTED9` — are older than that convention and are permanent in the append-only raw layer, so the row names them too|

**Assumptions about presentation and rules are free to make. Assumptions about identity and schema are not.** A19 is isolated in the device-user mapping, so a wrong PIN format is corrected by remapping rows.

**A21 — "the device pushes the PIN with leading zeros intact" — is answered and gone.** The question never arises: the device refuses to accept a leading zero in a user ID at all (§10). The mapping absorbed it with no code change, which is what §13's rule against resolving a PIN at capture was for.

A51 is the same reflex as the sheet's unread top-left note (A41): the screen needs somebody to attribute an entry to, nobody has read the guard roster, and inventing two plausible names would put invented people on real records. The placeholders work, say what they are, and are replaced by an UPDATE.

A50 exists because a list meant to catch one thing had filled with five of something else. **The unwatched list is how a real device nobody added gets noticed** — it went four hours unwatched once — and five gate serials sitting on it teach a reader to skip it, and then to skip the sixth line too. The gates now name themselves, the pattern is a row, and the serials it suppresses are **counted on the same report** rather than dropped silently, because a filter nobody can see is a filter nobody can check. The pattern must never be wide enough to swallow a device: the one in service is `PYA8262300072`, and ZKTeco serials carry no hyphen.

A49 is what a reader sees when leave and punches land on the same day. **A coded leave day is a leave cell**: nobody writes a punch time under an `AL`. **An uncoded leave day falls through to the punches**, because four form types have no letter and blanking the cell would hide a punch that really happened — the leave is still on the record and still in the per-day detail, and §6 forbids borrowing a letter from the type applied for. **The fraction is not on the sheet**: a half day shows its code like a full one, and what HR writes for a half day worked is not known.

A48 is the only assumption on the leave entry screen, and it is a suggestion rather than a rule. **§6 settles that the applied-for type and the sheet code are two fields and that neither is derived from the other**, and four form types have no legend code at all — so this is what the screen fills in for HR before they touch it, not a mapping the system believes. **HR overrides it by typing something else, and the row records what they typed.** Three of the seven types happen to have an obvious code; the others are left empty and stay empty until somebody who writes the sheet says otherwise.

A46 and A47 are the command queue's, and they are the only two assumptions in this system that **nothing has ever tested against the hardware** — not even once, the way A26 was tested by a handshake and A27 was not. The simulator exercises what the document says, which proves the receiver consistent with the document and says nothing about the firmware. **The factory test is one `REBOOT`**, and what it settles is listed in BUILD.md.

A43 to A45 are the alert's, and the risk in them is the same in both directions: too tight and HR learns to ignore it, too loose and a day of capture is lost before anybody looks. **A43 is the cheap one to get right** — the device polls every ten seconds, so any threshold above a few minutes is safe and 15 was chosen to survive a reboot without crying wolf. **A44 is the one carrying a real judgement**: 60 minutes before a punch is due, because a shift can legitimately start slowly, and 180 minutes of nothing while a shift runs, because that is long enough to be a fault and short enough to fix the same day. **A45 exists because the alternative alarms forever**: a serial added before the device is installed would raise an outage every check until it is mounted.

A38 to A42 are the sheet's, and they are all rows so that the layout has no constants in it. **A38 is the one that decides what a reader sees**: it makes "outside schedule" mean late in or early out, which §5 defines for arrival and §8 only names for departure — so the early half is the unconfirmed half, and a day that is both shows both times separated by a slash. **A41 is deliberately empty rather than filled in**: the note exists on HR's paper, has never been read, and a plausible guess in a filed record is worse than a blank marked unread. **A42 is the one that could quietly stop being true** — §4 makes the rest day a column on the schedule row, so a group resting on another day is legal, and on that day the sheet stops shading and says why rather than shading a column that is not whole.

A35, A36 and A37 are the three the daily row rests on, and all three are about what a figure means rather than what it is worth.

A35 is the one to watch on paper. A day with one punch is a real case — a failed punch at the door, a shift left early — and what HR writes on the sheet for it is not known. Storing a last out equal to the first in would say the employee left the moment they arrived, so the row says nothing instead, and the database refuses a last out on a single punch.

A36 decides whether a period total can add its days up. Empty is not zero: a rest day and an on-time arrival are different facts, and a total that treats them alike would count rest days as punctuality. Floored minutes mean 08:00:59 against an 08:00:00 start is not yet a minute late.

A37 is the dedup rule §12 defers downstream, and it is the one most likely to be wrong in a specific way: **two employees cannot share a second, but one employee arriving at a queue could conceivably be read twice in the same second by the device.** The capture cannot settle it — the device suppresses a repeat verification from the same user within an interval nobody has read yet (§10), which may make it impossible by construction.

A32 and A33 are what turn a correction and a device punch into rows about the same person on the same day. A32 is a row; A33 is the date the mapping is read on, and it matters only when a PIN is reassigned or an employee changes group mid-shift.

A30 is the width of the attendance-day window, and it is per schedule row. It is a guess until real punches show how early people arrive and how late they leave. A31 is provisional in the strongest sense: the group codes came from a sample list, not from HR, and every seeded schedule is marked provisional in the database.

**Four are gone, answered by HR's own paper.** A8 — the late coming period — is printed on the record itself, `16/12/2025` to `15/01/2026`. A11 — the 30-minute threshold, inclusive — is on the deduction list, where an employee at exactly `0 hours 30 minutes` appears. **A28 and A29, the employee number's key and its shape, are now §2's rules rather than guesses**: four digits zero-padded, keyed to four, numbers reaching about 1500, and both `090` and `1601` printed on one page of real paper. They are still rows — `employee_number_rule` still holds the shape and the key width, so a later correction is still an UPDATE and a rekey — but nothing is being assumed by holding them.

**A9 and A10 stay.** The time-off record's period is blank on the specimen, and the payroll halves come from the entry screen rather than from a period anybody has stated.

**A26 is answered and gone.** It was what *we* send the device in the handshake reply. The receiver sent all ten option lines, the device accepted them without complaint and carried on pushing, and §12 now records the set as observed. The rows did not change; what changed is that they are no longer a guess.

A27 is the one the receiver still runs on blind. Two captures, and every byte in both was ASCII with an empty `Name` field, so nothing has exercised the fallback chain. **It settles the first time a name that is not Latin is enrolled on the device**, and not before. It is a row, so that day is an UPDATE.

A34 comes from the option push, and it contradicts the datasheet by orders of magnitude — 20 attendance records against 200,000, 80 users against 8,000. Reading the small numbers as how much the device moves in one exchange, rather than how much it can hold, is the assumption. **It matters at step 8, where users are pushed to the device**: a batch limit means chunking the queue, a storage limit would mean the device cannot hold the workforce. The datasheet says it can. **The same push weakens the simple reading**: `~MaxFaceCount=6000` and `~MaxUserPhotoCount=8000` in the same line *do* match the datasheet, so the three small numbers are not one convention applied throughout. It stays an assumption with a question against it.

---

## 10. Device configuration

### The device, as it now stands

|What|Value|
|---|---|
|Model|SenseFace 4A|
|Serial|`PYA8262300072`|
|Platform|`ZAM70_TFT`|
|Firmware|`ZAM70-NF43VA-Ver3.3.12`|
|Push version|`Ver 3.1.2S-20250616`|
|Protocol|**Switched from BEST to PUSH (ADMS)**|
|HTTPS|Disabled|
|Cloud server|The receiver, plain HTTP, **port 8081**|

Capacity, from the manufacturer's datasheet: 8,000 users · 8,000 fingerprint templates · 6,000 face templates · 200,000 transaction records · user ID up to 14 digits. **Headcount is not a constraint**, and **200,000 transaction records is years of buffering rather than days** — which is what makes an outage survivable (§12). The device's own option push reports far smaller numbers, which is §9 A34.

**The device has USB export and USB upload.** Export is a manual recovery path if the receiver is permanently lost, and upload may make bulk enrollment materially faster — the person at the desk then captures only the biometric. Neither is a second ingest path, and what the menus actually offer is unread; BUILD.md parks it.

**The device refuses a leading zero in a user ID.** Observed at enrollment. `0090` cannot be a PIN; §2 and the dated device-user mapping are where that is handled.

**The device suppresses a repeat verification from the same user within a fixed interval.** Observed: a second attempt inside the window is refused at the reader and **never sent** — nothing arrives, so nothing is missing from the raw layer either. Face, then fingerprint five seconds later, was refused the same way, so **the window is per user, not per method.** The interval's value has not been read off the device menu and is parked.

**That interval is the floor on how close two genuine punches can be.** It matters where two are legitimately close together — an employee leaving on a gate pass and coming back inside the window has no second punch, and the return is a paper time HR types (§5), not a punch that went missing.

### Rules

- **Face and fingerprint only.** PIN-alone and card verification disabled. Punch cards are currently shared between employees; a PIN or a card reproduces that, a biometric does not. The PIN still exists as the device's user identifier — what is disabled is the PIN as a credential.
- **A super administrator is registered before deployment.** Until one exists the device menu is open to anyone who walks up to it, and the verify mode or the server address can be changed by hand.
- **The device has one Cloud Server setting and this system holds it.** ZKTeco's own software competes for the same setting — whichever holds it receives the pushes and the other receives nothing. Their software may be used once for bulk enrollment, then the address is repointed here. It is not installed, licensed or maintained as part of the running system.
- **If anyone repoints that setting, capture stops silently while the device keeps recording locally.** The ingestion alert is the only thing that catches it, and it catches it as contact silence (§3) — usually within minutes, because the polls stop with everything else. Record the correct value somewhere findable.
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

**The queue is built, and it carries two commands: `REBOOT` and `CHECK`.** Neither touches a record on the device. **There is deliberately nothing that clears, deletes or resets anything.** The device's own buffer is the only copy of any punch it has not yet been acknowledged for — that is now observed rather than assumed (§12), and it is precisely what a clear command would delete. Adding such a command is a row somebody adds in front of evidence, not a line of code (§13).

**One command per poll, oldest first, for that serial only.** A device with nothing queued gets exactly the reply it got before the queue existed. The hand-out is marked in the same transaction as the request that collected it, so a command cannot leave without the request that took it being on the record.

**A result for a command this system never issued is stored, flagged and answered `OK`** — the same reflex as an unknown serial. The device is reporting something that happened, and refusing to write it down would not make it un-happen.

Command strings are unverified and get pinned during the first real device capture. The queue itself does not depend on them.

---

## 12. The device protocol — fixed, not designed

**This section is not a design. It is what the firmware does.** It is here because getting it wrong makes the device retry forever or drop a batch of punches silently.

The device is the HTTP client. It pushes; we never poll. All routes under `/iclock/`.

|Route|Response body|Seen|
|---|---|---|
|`GET /iclock/cdata?SN=&options=all&pushver=&language=`|`GET OPTION FROM: {SN}` then `Key=Value` lines|Observed. The query also carries `DeviceType=att` and `PushOptionsFlag=1`|
|`POST /iclock/cdata?SN=&table=ATTLOG&Stamp=`|`OK: {n}`|Observed. `Stamp=`, as written — **and its value is the one we handed the device in the handshake reply**|
|`POST /iclock/cdata?SN=&table=OPERLOG&OpStamp=`|`OK`|Observed. **`OpStamp=`, not `Stamp=`** — the two tables name their cursor differently|
|`GET /iclock/getrequest?SN=`|`OK`, or `C:{id}:{CMD}`|The `OK` is observed, and **the command line is not** (§9 A46). The first poll after a handshake also carries `INFO=` — firmware version, the device's IP, and counts, comma-separated|
|`POST /iclock/cdata?SN=&table=options`|`OK`|Observed, and **not in any material in hand**: the device POSTs its whole option set to us|
|`POST /iclock/cdata?SN=&table=BIODATA`|`OK`|Observed, and also undocumented here: biometric templates, base64|
|`POST /iclock/devicecmd?SN=`|`OK`|**Not seen.** The result format is documented only (§9 A47)|
|`POST /iclock/cdata?SN=&table=ATTPHOTO`, `POST /iclock/fdata`|`OK`|Not seen|
|catch-all `/iclock/{rest:path}`|`OK`|**This is what absorbed `options` and `BIODATA`** — see below|

**The handshake reply, observed and accepted.** The device asked with `SN`, `options=all`, `language=69`, `pushver=2.4.1`, `DeviceType=att`, `PushOptionsFlag=1`. The receiver answered `GET OPTION FROM: {SN}` and these ten option lines, and **the device accepted them without complaint and carried on pushing normally**:

|Option|Value sent|
|---|---|
|`Stamp`, `OpStamp`|`9999` — see the note on cursors below|
|`ErrorDelay`|`30`|
|`Delay`|`10`|
|`TransTimes`|`00:00;14:05`|
|`TransInterval`|`1`|
|`TransFlag`|`1111000000`|
|`TimeZone`|`8`|
|`Realtime`|`1`|
|`Encrypt`|`0`|

**`Realtime=1` is why punches arrive within seconds** rather than on the `TransTimes` schedule. Every one of these is a row, so changing what the device is told is an UPDATE (§9 had this as A26; the capture answered it).

**`Stamp` and `OpStamp` are meant to be cursors, and ours are fixed rows at `9999`.** The device echoes the value straight back on its next push — `Stamp=9999` on ATTLOG, `OpStamp=9999` on OPERLOG — so what is in the raw layer is our own number returning, not something the device chose. It costs nothing here, because the device deletes each record once it is acknowledged and never asks for it again. What a moving cursor would change is parked in BUILD.md, not designed here.

**Two tables arrived that this section did not name, and nothing had to be built for them.** `options` and `BIODATA` were answered `OK` by a fall-through and stored whole. In the receiver that fall-through is `POST /iclock/cdata`'s unrecognised-table branch rather than the catch-all route itself, but it is the same rule doing the work: **an undocumented table or route still gets `200 OK` and still lands in the raw layer.** That is exactly what the catch-all exists for, and it earned its place the first time real traffic arrived.

**Punch line, observed: ten tab-separated fields and a trailing tab.**

    1 <tab> 2026-08-20 11:27:27 <tab> 255 <tab> 15 <tab> 0 <tab> 0 <tab> 0 <tab> 0 <tab> 0 <tab> 0 <tab>

`pin`, `YYYY-MM-DD HH:MM:SS`, `status`, `verify`, then **six more fields, every one of them `0` in every line captured, and a trailing tab after the last.** Their meaning is unknown and they are deliberately not named here — a name guessed from documentation is what put a wrong seven-field line in this section in the first place. **The trailing tab means a split on tabs yields eleven pieces, the last one empty.**

**The parsed layer keeps all ten, positionally and verbatim, and names only those four.** The trailing empty piece is a separator, not an eleventh field, and is not stored as one. **A line of any other shape is a failed row with the line kept whole** — never padded to fit, never truncated to fit. That is what makes a shape this parser refuses cost a parser version bump and a replay, rather than a punch.

**The verify field shows the method. Confirmed physically: `15` face, `1` fingerprint** — both tested at the device. This is the field the password-punch count reads (§10).

**Device time carries no offset, and the offset is `+8`, observed.** The device sent `2026-08-20 11:27:27` with no marker of any kind; the server stamped the arrival at `03:27:27+00:00`. Exactly eight hours, consistent with the `TimeZone=8` the receiver sent it. **This is the case that "stored as sent, never converted on the way in" (§14) exists for**: the string is kept, the arrival instant is kept beside it, and the difference is a fact anybody can re-derive rather than a conversion nobody can undo. It is also how clock drift will show (§10). See §9 A32.

**The command formats are documented, not observed.** No command has ever been sent to this device, so both halves of the exchange below are assumptions (§9 A46, A47) and are marked as such in the route table:

    reply to a poll     C:{id}:{CMD}          e.g. C:12:REBOOT
    result posted back  ID=12&Return=0&CMD=REBOOT

**The id in the reply is ours; the id in the result is stored as the device sent it**, beside ours, and matching is done on the returned text. If this firmware renumbers, truncates or omits it, that shows up as a row with a mismatched `reported_id` rather than as a result nobody can place. **The first real `REBOOT` corrects both formats the way the first real punch corrected the punch line.**

**Status was `255` on every punch, and that is settled rather than unverified.** The device is not labelling in versus out. Punch state options are off by default on this model and are staying off, so **first in and last out come from the times alone** — which is what §3 already does. Nothing downstream reads status.

### What has been captured, and how far it can be trusted

**Every line above marked _Observed_ comes from real traffic from the device in §10, serial `PYA8262300072`.** There have been two captures.

**The first capture stored nothing.** It was taken with a throwaway FastAPI script outside this repo, which answered the handshake with a bare `OK` instead of option lines, and answered `OK` to every push — so the device cleared those records from its own memory and none of it reached the raw layer. Those punches, operations and templates are gone. It is the argument for the raw layer: a server that stores nothing let the device throw its own records away.

**The second capture is through the receiver and is kept.** It is `raw_request` 96–115: a handshake and its reply, the option push, `INFO` on the following poll, two OPERLOG pushes, two ATTLOG pushes, and the polls between them. **It is replayable, so every line drawn from it can be checked against the bytes rather than against a note.**

**Corrected, and it was worth correcting:** an earlier version of this note said the device sent `Stamp=9999` and `OpStamp=9999` as placeholders because no stamp had been issued. That was wrong. **`9999` is ours** — the receiver's own seeded option rows sent it in the handshake reply, and the device echoed it back on the next push. Nothing about the stamp was ever the device's choice.

What is still not settled:

- **Only one device, and only two sessions.** The vendor spec is still not in hand.
- **A moving cursor.** `Stamp` and `OpStamp` have only ever been `9999`, because that is what we send. Parked in BUILD.md.
- **Anything non-ASCII** (§9 A27). Both captures were ASCII end to end, and the `Name` field was empty in both.
- **Fields five to ten of the punch line**, which have only ever been `0`.

**Raw capture is what makes building on it acceptable.** Every request is stored whole before anything parses it, so a wrong assumption costs a replay rather than lost punches. **This is the reason the raw layer exists, and why it is never validated or filtered.**

**The device deletes a record only once the server answers `OK`, and it buffers and re-pushes while the server is unreachable.** That is what makes the order in this section load-bearing: **store first, answer second.** An `OK` is a receipt, and a server that answers before storing has told the device to forget something nobody kept — which is exactly what the first capture did (above).

**Observed, on this device, after a 20-hour outage nobody planned.** Contact stopped at `2026-08-20 04:38:45Z` and resumed at `2026-08-21 00:50:32Z`. Five punches were made at the reader during that gap — device-local `16:57:20`, `17:03:33`, `17:08:51`, `17:17:08` on the 20th and `08:42:50` on the 21st — and **all five arrived in one push one second after contact resumed**, `raw_request` 3335. The oldest had been held **953 minutes, nearly sixteen hours**. Nothing was lost, nothing was re-collected, and nobody asked the device for anything.

So **an outage is not data loss**: the device holds the records and re-pushes them on reconnect, up to a transaction capacity of 200,000 (§10), which is years of punching rather than days. **What an outage costs is knowing about it** — the device retries quietly, so silence is the only symptom, and the ingestion alert is the thing that turns silence into a warning. That is exactly how this outage went: capture stopped for twenty hours and nothing said so until a device was put on the allowlist and the alert had something to watch.

**Absolute rules on `/iclock/` routes**

- **Return only `200` and plain text.** A redirect, a `401`, or an HTML error page makes the firmware retry forever or drop the batch.
- Trailing-slash redirects are on by default in the framework and must be turned off — **and the catch-all route kept. Neither alone is enough.**
- No exception handler that returns JSON. No request-body validation — bodies are tab-separated text or raw binary, and a validation failure produces an error status.
- **No auth middleware.** The protocol has no credential mechanism. Access control is network position: device on an isolated segment, firewall permitting only its address, serial-number allowlist that logs unknown serials and still returns `200 OK`. Plain HTTP.
- **The application cannot see the device's address, so address filtering has to be at the firewall.** Observed: every request arrives from `172.21.0.1`, the Docker bridge gateway, never from the device's own `192.168.60.165`. The device's real address appears only as a field *inside* the options body and the `INFO` string — data, not request metadata. This does not change the rule above; it makes it the only option. **An address check written in the application would either pass everything or fail everything.**
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
|Reading the exported Excel sheet back in|The file goes one way; a returned sheet is a correction in a cell instead of a row|
|Deleting a serial from the allowlist to stop it alerting|The raw layer keeps its requests forever; the list must say why it stopped being watched. Stand it down instead|
|Collapsing "no punch" and "absent"|One is a fact, the other a judgement|
|A guard-typed punch time|Server-stamped only|
|An unmarked manual punch|It must be visibly countable|
|A fallback password left in place after re-enrollment|That is the permanent shared secret|
|Logging or retaining a password payload|Purge on completion|
|Hard-coding a schedule, grace period, threshold, period boundary, leave code or holiday|These are rows|
|Naming a punch field whose meaning has not been observed|A name invites logic; six of the ten are stored positionally and unnamed|
|Padding or truncating a punch line to make it fit the expected shape|A wrong shape is a failed row with the line kept, and a replay away from being right|
|Inferring that a shift crossed midnight by comparing two times|The schedule row states it; the attendance day follows from the shift|
|Discarding a calendar adjustment when the year is re-uploaded|It is a deliberate decision, kept as its own row and reported|
|Padding or stripping the employee number on write|Stored verbatim; a separate key does the matching|
|A partial user update to the device|It wipes the whole record|
|Queueing a device command that clears, deletes or resets records|The device's buffer is the only copy of an unacknowledged punch — observed holding one for sixteen hours (§12)|
|Building overtime|Its source is unknown|
|Designing leave entitlements, the export format, the government-application field set, or reports|Blocked on HR. Stop and say so|
|Storing passport, IC or medical certificate data|Privacy handling undecided|
|Assuming ZKTeco software can run alongside this|One server setting, and this system holds it|
|A shared passphrase standing in for accounts on the screens|It is the device's shared password one layer up (§10). Access control is network position until Milestone 5|
|Serving the device routes and the HR interface on one port|A tunnel to the interface would carry the device routes with it (§14)|
|A time field on the guard's screen, in its payload, or in the function behind it|A guard who can type a time is a guard who can be asked to type a different one (§3). The server stamps it, and the database refuses a guard row that states one|
|A way to undo a guard entry|A correction is an act somebody performed. What should replace a wrong one is undecided, and a delete button would settle it by accident (§3, BUILD.md Parked)|
|A screen that works out a figure of its own|It becomes a second place the answer lives, and the screen and the filed record can then disagree (§7). Screens ask; they do not calculate|
|A serial pattern wide enough to suppress a real device|The unwatched list is the only thing that notices a device nobody added (§9 A50)|

---

## 14. Environment

- Python/FastAPI managed with **uv** · React + Tailwind, built by a Vite stage in the Dockerfile so the runtime image carries no Node · PostgreSQL · Docker Compose
- **On-premises, and this is a requirement rather than a preference** — the device pushes over the LAN and cannot reach a cloud host.
- **Two ports, one codebase, one container.** The receiver answers the device on its port; the HR interface answers people on another. They are two ASGI applications in one process, sharing a database and nothing else, and **the HR application has no `/iclock/` route** — a request for one is a `404` on that port.
- **That separation is what makes remote access possible at all.** Tailscale reaches the HR port and only the HR port; the device routes are not there to be reached. A single application on a single port could not honour "tunnel the HR interface, never the device routes" — the tunnel would carry both.
- **Tailscale is a private network, not a public URL**, which matters because **there is no login on these screens before Milestone 5**. Access control is network position, exactly as it is for the device routes.
- **The guard does not use the tunnel.** He is on factory Wi-Fi, reaching the server by its LAN address — never mobile data, because §12 keeps the receiver off any public path.
- **The guard's screen is on the same port and is not part of the HR interface.** Same application, same LAN, different person and different place — it carries none of HR's chrome and reaches none of HR's screens. It shares the port because it shares the network, not because it shares an audience.
- **The interface asks and draws; it does not calculate.** Every screen is a face on a function the command line already calls, and the HTTP layer holds no query, no date arithmetic and no totals of its own. That is what keeps §7's one render one render.
- Server-observed times are stored with timezone. Device-reported times are stored as the device sent them, alongside the original string, **never converted on the way in**.
- **No migrations until real punches arrive** (see BUILD.md). Until then the database is dropped and recreated.