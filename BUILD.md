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

---

## Milestone 1 — steps

|Step|What it delivers|Done when|
|---|---|---|
|**1**|**Capture** — receiver, raw request storage, punch parsing, **and the device simulator that exercises them**|The simulator runs a full push cycle and exits clean|
|**2**|**Employees** — number, name, section, role, group, active and left dates, device PIN|A real employee list loads|
|**3**|**Schedule and calendar** — per group, effective-dated, plus holidays and rest days|A past period renders with the schedule that was in force then, not today's|
|**4**|**Corrections** — guard entry and HR retroactive entry, both marked and counted|A guard entry cannot be given a time; an HR entry can|
|**5**|**HR entry** — leave, gate pass, treatment slip, from the paper forms HR already receives|Codes appear on the generated sheet|
|**6**|**Daily attendance** — first in, last out, late minutes, status per employee per day|Period totals are queries over it|
|**7**|**The sheet** — generated in HR's existing layout, plus per-day punch detail|HR reads it instead of the punch card|
|**8**|**Device control** — command queue, push users, set and clear fallback passwords, pending re-enrollment list|An employee created in the app appears on the device|
|**9**|**Ingestion alert** — warns when punches stop arriving|Silence for N hours raises a warning|

**Then: demo to HR, walking the assumed values line by line while the software is on screen.**

The only thing actually blocking the build is **the employee list** — number, name, section, role, group. Everything else is assumed and corrected afterwards. The device is not a prerequisite; the simulator stands in for it.

---

## Where it stands

**Step 1 is built.** The receiver (every route in SPEC.md §12), the raw request layer, the punch parsing layer and the device simulator. Steps 3–9 not started.

The simulator runs a full push cycle and exits clean. Each of these was then broken on purpose and the simulator failed on every one: the catch-all route removed, trailing-slash redirects left on, an auth check added, the raw layer deduplicating, the body decoded at capture, FastAPI's own JSON error page reaching the device, and the parser raising on every line. The parser one is the interesting failure — every response stayed `200 OK: {n}` and only the parsed layer went empty, which is the rule §12 asks for.

**Step 2 is built and is not done.** Its done-when is "a real employee list loads", and the list does not exist yet. What exists: the employee model, effective-dated; the device-PIN mapping in its own dated table; an Excel importer driven by an explicit mapping file; a committed fixture spreadsheet; and a gate of 24 deliberate mistakes, all of which the importer refuses. When HR's file arrives, step 2 finishes by writing a mapping file for it — no code change, unless the file carries a field the model has no room for.

Deliberately not in step 2: no employee screen, no push to the device, no schedule, no leave, no daily attendance.

**openpyxl** was added for it: it reads .xlsx without a spreadsheet application and writes the fixture, so one dependency covers both directions. It cannot read the legacy .xls format — an .xls from HR gets re-saved as .xlsx before import.

Run it:

    docker compose up -d --build
    docker compose exec api hr seed            # drops, recreates and seeds — one command
    uv run python tools/adms_sim.py --port 8080  # must exit 0
    docker compose exec api hr replay          # rebuild parsed punches from the raw layer

    docker compose exec api hr employees import /srv/fixtures/employees_sample.xlsx \
        --mapping /srv/fixtures/employees_sample.mapping.toml --allow-new group
    uv run python tools/employee_import_gate.py  # must exit 0

HR's real list goes in `import/`, which is not committed. The importer is told which sheet, which rows and which column letters hold what; it never matches on header text, it echoes the headers back so a person can see where the mapping is pointing, and one bad row writes nothing at all.

`hr seed` refuses to drop once requests have been captured, and needs `--force` to go ahead. That guard becomes the real thing on the first day of the parallel run, when dropping stops being recoverable.

Verified on the compose stack: a full cycle clean, a second cycle clean against the database the first one filled, capture surviving `docker compose down` and back up, and the replay running in the container.

**The receiver's host port is `RECEIVER_PORT`, default 8080.** Not 8000 — Production Tracking already holds that on this host, and the two stay separate. This port is half of the device's Cloud Server Setting, so changing it means changing the setting on the device.

**The device is on site.** Power adapter pending.

Artifacts received and analysed: daily attendance sheet, late coming summary and deduction record, individual time-off record, time-off salary summary, SQL Account payroll entry screen.

**Nothing in the protocol contract has been verified against the device** — see SPEC.md §12. The simulator therefore tests that the receiver behaves as the contract says, not that the contract is right. Only real traffic settles that.

---

## When the adapter arrives — a gate on §12 and the parser only

**Nothing else waits for it.** Steps 2 onward are built without the device; the simulator stands in for it.

Ten minutes of real traffic converts every unverified line in SPEC.md §12 into fact, and the parser then gets written against real bytes.

1. Run the receiver on a laptop on the same LAN.
2. Point the device at it — COMM → Cloud Server Setting. Server IP, port, plain HTTP.
3. Punch a few times and capture.

**Enroll one employee with a leading-zero number first** and confirm whether the device pushes `0090` or `90`. That answers A21 before anyone enrolls everyone.

While in the menus: register the super administrator, set verify mode to face and fingerprint only, check whether USB user import exists.

---

## Data and migrations

**No real data exists until employees are punching for real** — the start of the parallel run. Until then:

- The database is dropped and recreated whenever the shape changes. No migration files, no ordering, no schema history.
- Test captures and simulator output are not worth preserving.

**From the first day of the parallel run: migrations only.** Raw device capture is append-only from that moment and cannot be recreated — the device does not keep it.

---

## Going live

Code being ready is not the gate. These are.

**Decide first**

|Decision|Whose|
|---|---|
|**Grace period.** The device is stricter than a person reading a card. Identical rules will produce more recorded lateness, and lateness is deducted from pay. Reproducing today's effective strictness needs a grace period; tightening needs a decision and a communication to staff|**Management, not HR**|
|Who may correct a missed punch, and on what evidence|Management|
|**Biometric notice to employees**, local and foreign. Required before enrollment, not after|Management|
|How long raw device data is retained. Face templates and photos land there from the first test push, whatever the wider privacy decision|Management|
|Whether the guard has a screen where failures happen|Zi Fong, on inspection|

**Prepare**

- Device mounted, powered, on the LAN at a fixed address.
- **Cloud Server address points here and stays here.** Record the correct value somewhere findable. The port is `RECEIVER_PORT`, 8080 unless changed.
- **If the receiver is run under WSL2 for a test, the device cannot reach it without a port proxy on the Windows host.** On the on-premises server this does not arise. Worth knowing before the first power-on wastes an afternoon.
- Timezone +8.
- Ingestion alert live.
- Throughput check at shift start — a whole shift punching within a few minutes, one door.

**Enroll**

- **Super administrator registered first.** Until one exists the device menu is open to anyone.
- Verify mode set to face and fingerprint only.
- Every active employee, face and fingerprint, PIN set to employee number.
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

---

## Parked

**Needs a person, not a decision**

|Question|Who|Blocks|
|---|---|---|
|**Scheduled start and end per group. Read it off the punch card machine before it is decommissioned** — it prints red for out-of-schedule punches, so the schedule is already configured on it|HR / the machine|Late coming|
|Is there a grace period before a minute counts as late|HR, then management|Late coming|
|Is lateness measured against a fixed start, or the shift the employee was on that day? Do employees move between shifts?|HR|Late coming|
|**Which period does each item actually run on** — are the 10th/15th/20th cut-offs deadlines or period boundaries?|HR|All aggregation|
|Is the 30-minute threshold applied per month or per payroll half?|HR|Milestone 3|
|What exactly does `AB — absent cut 3 times` cut, and against what?|HR|Milestone 3|
|Is the employee number always 4 digits, and is it the device PIN?|HR|Enrollment|
|**When a number in the list is not four digits, is it a typo, an older format, or a different scheme?** (SPEC §9 A28, A29). Until answered the importer refuses it and has to be told to accept it|HR|The employee list|
|Are numbers reused after someone leaves?|HR|Employees|
|What employee groups exist, and does the group decide shift and break?|HR|Schedule|
|Half-day marks — which leave types can be half days?|HR|Leave|
|What is the note in the top-left of the attendance sheet? A close-up photo may answer the schedule question|HR|—|
|How many pages is the sheet, and what is headcount?|HR|—|
|2026 public holidays including Melaka state|HR|Calendar|
|Does the sheet need to stay Excel, or is a screen acceptable?|HR|Milestone 1 output|
|**Can SQL Account import a file, or is it keyed by hand?** Sample export if yes|Accounts|Milestone 2 deliverable|
|What do `DW` `MT` `MR` `CL` `HL` `EX` `PT` `AD` `LS` `OOB` mean, and which are actually used?|Accounts|Milestone 2|
|Is `Lateness` the raw total or only the deductible portion?|Accounts|Milestone 2|
|Is `Work Hours` gross or net of break — and which break?|Accounts|Milestone 2|
|**Where does overtime come from?** A form, an approval, who calculates it|Accounts|Milestone 2|
|**What is at the guard post** — a PC, a shared phone, or nothing? And how far from the device?|Zi Fong|The fallback design|
|Where is the device mounted, and is there network and power there?|Zi Fong|Install|
|How many enroll, and when can they be scheduled including night shift?|HR|Enrollment|
|Which employees have worn fingerprints?|HR|Enrollment|
|Which notification channel do supervisors actually use?|Supervisors|Milestone 5|
|Who approves leave, and does the chain differ by type?|HR|Milestone 5|
|Can floor workers use a web form, in which languages, and is a kiosk needed?|HR|Milestone 5|
|**Privacy handling for passport, IC and medical data** — encryption, retention, access logging|Management|Milestone 4|
|**ADMS protocol spec**|ZKTeco supplier|Confirms §12|
|Which `Key=Value` options does the device accept in the handshake, and what does it do with each? Read them off the first real handshake (SPEC §9 A26)|The device, then ZKTeco supplier|Nothing structural — they are rows|
|Which encoding does this firmware send ATTLOG and OPERLOG bodies in (SPEC §9 A27)?|The first real capture|Nothing structural — it is a row|

**Artifacts still wanted**

Leave application form · gate pass form · medical treatment slip · **one closed month of attendance sheet with the matching SQL Account entries** — that last one lets the whole chain be reconciled end to end before anything is built.

The punch card itself is not needed. Its only unique content is the leave codes HR writes on it, and those already appear on the sheet.

**Requesting artifacts instead of interview time worked, and is the default from here.**

**Features, deliberately deferred**

|Item|Note|
|---|---|
|Leave entitlements and balances|Milestone 5|
|Approval workflow|**Approval links must be single-use, expiring and bound to one supervisor** — a messaging link can be forwarded, and the endpoint is public. **HR-entered applications never re-trigger approval**; the paper form was already signed, and asking a supervisor twice makes them stop responding|
|Notification channel|Telegram's bot API is free and quick. WhatsApp Business API costs per message and needs verification through Meta. WhatsApp is the common channel locally. **Settle with the supervisors before building**|
|Overtime input path|Source unknown. May be needed for Milestone 2|
|Government-application field set|Milestone 4|
|Reports beyond the three known summaries|Milestone 3|