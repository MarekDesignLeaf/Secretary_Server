# Voice Command Roadmap — planned commands per module

Status: PLANNING document. Lists intended voice commands so they can be wired
quickly once each module's backend action is built and stable.

RULE (from blueprint Section 15 / Phase 18): a voice command may only be enabled
after the module has a working, validated backend action AND a UI workflow. Voice
mirrors existing workflows — it never introduces new logic or bypasses validation.

Legend: [LIVE] backend executes it now | [PLANNED] needs backend action first.

## Calendar
- [LIVE] calendar.create  — "vytvoř schůzku", "přidej schůzku", "domluv schůzku", "naplánuj"
- [LIVE] calendar.list    — "co mám zítra", "co je v kalendáři", "co je dnes"
- [LIVE] calendar.delete  — "zruš schůzku", "smaž termín", "odvolej schůzku"
- [PLANNED] calendar.update — "přesuň schůzku na 14:00", "změň termín"

## Clients
- [LIVE] client.create — "vytvoř klienta", "nový klient", "zaeviduj klienta", "přidej zákazníka"
- [PLANNED] client.find    — "najdi klienta Novák", "zobraz klienta"
- [PLANNED] client.note    — "přidej poznámku ke klientovi"
- [PLANNED] client.archive — "archivuj klienta"

## Tasks
- [LIVE] task.create — "vytvoř úkol", "přidej úkol", "zadej úkol", "udělej úkol"
- [PLANNED] task.list     — "moje úkoly", "co mám za úkoly"
- [PLANNED] task.complete — "dokonči úkol", "označ úkol jako hotový"
- [PLANNED] task.assign   — "přiřaď úkol Danielovi"

## Jobs (zakázky)
- [PLANNED] job.create        — "vytvoř zakázku"
- [PLANNED] job.change_status — "změň stav zakázky na dokončeno"
- [PLANNED] job.list          — "zobraz zakázky", "zakázky na zítřek"

## Leads
- [PLANNED] lead.create        — "nový lead od Smithe z Checkatrade"
- [PLANNED] lead.convert       — "převeď lead na klienta"

## Quotes (nabídky)
- [PLANNED] quote.create  — "vytvoř nabídku"
- [PLANNED] quote.send    — "odešli nabídku klientovi"
- [PLANNED] quote.approve — "schval nabídku"

## Invoices (fakturace)
- [PLANNED] invoice.create — "vytvoř fakturu ze zakázky"
- [PLANNED] invoice.status — "označ fakturu jako uhrazenou"
- [PLANNED] invoice.list   — "neuhrazené faktury"

## Communication
- [PLANNED] comm.log  — "zaloguj hovor s klientem"
- [PLANNED] comm.list — "historie komunikace klienta"

## Materials / stock
- [PLANNED] material.order — "objednej materiál"
- [PLANNED] material.check — "kolik máme na skladě"

## Reports / settings
- [PLANNED] report.jobs    — "report zakázek"
- [PLANNED] settings.open  — "otevři nastavení"

## Command aliases (Phase A6 — LIVE)
User-defined aliases map a custom phrase to any of the LIVE commands above:
- "vytvoř alias domluv návštěvu na příkaz přidej schůzku"
- then "domluv návštěvu" runs calendar.create.

## Wiring checklist for each PLANNED command (when its backend is ready)
1. Backend action exists in /voice/execute and is validated + permission-checked.
2. UI workflow for the same action exists.
3. Add phrase list to voice_intents.py (CS + EN, with/without diacritics).
4. Add slot definitions to voice_slots.py if the action needs follow-up info.
5. Add execution branch + Czech confirmation message (with diacritics) in voice.py.
6. Verify on real phone before marking done.
