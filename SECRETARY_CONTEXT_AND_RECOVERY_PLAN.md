# SECRETARY — CONTEXT AND RECOVERY PLAN

**Verze:** 1.0  
**Datum:** 2026-05-29  
**Status:** Aktivní — řídí vývoj od tohoto commitu dál

---

## 1. CO JE SECRETARY

Secretary **není** čisté CRM.  
Secretary **není** jen Android aplikace.  
Secretary **není** jen backend.  
Secretary **není** jen hlasový asistent.

**Secretary je hlasově ovládaný operační systém pro řízení firmy.**

Cílový stav: plně funkční hlasově ovládané CRM pro Android jako první platformu,
se serverovou databází na Railway, rolemi, právy, historií změn, notifikacemi,
klientským portálem, týmovým řízením práce a připraveností pro iPhone a PC
**bez změny backendové logiky**.

---

## 2. ZÁKLADNÍ PRAVIDLA PROJEKTU

Tato pravidla jsou neměnná. Žádný vývojový krok je nesmí porušit.

1. **Backend je jediný zdroj business logiky.**
2. **Databáze je jediný trvalý zdroj pravdy.**
3. **Android je pouze prezentační vrstva.**
4. **Voice nesmí obcházet API ani oprávnění.**
5. **Žádná akce bez audit logu.**
6. **Žádný přístup k datům bez tenant izolace.**
7. **Nejdřív logika, potom DB, potom backend, potom Android, potom voice.**
8. **Hlas nesmí být přidáván před stabilním workflow.**
9. **Nic se nemaže jen proto, že to není právě napojené v UI.**
10. **Rozpracovaná business logika není legacy odpad.**

---

## 3. CO SECRETARY MUSÍ OBSAHOVAT

Toto je finální seznam modulů. Žádný z nich nesmí být smazán bez explicitního schválení vlastníka projektu.

| Modul | Stav v clean backendu | Stav v git historii |
|---|---|---|
| Klienti (CRUD) | ⚠️ stub (list+create) | ✅ plný CRUD v `440aa04` |
| Zakázky (jobs) | ⚠️ stub | ✅ plný CRUD v `440aa04` |
| Úkoly (tasks) | ⚠️ stub | ✅ plný CRUD v `440aa04` |
| Nabídky (quotes) | ⚠️ stub | ✅ plný modul v `440aa04` |
| Fakturace (invoices) | ⚠️ stub | ✅ plný modul v `440aa04` |
| Work reports | ⚠️ stub | ✅ plný modul v `440aa04` |
| Komunikace | ⚠️ stub | ✅ plný modul v `440aa04` |
| Poptávky (leads) | ❌ chybí | ✅ plný modul v `440aa04` |
| Kalendář / calendar-feed | ❌ chybí | ✅ v `440aa04` |
| Notifikace | ❌ chybí | ✅ v `440aa04` |
| Timeline / activity log | ❌ chybí | ✅ v `440aa04` |
| Fotodokumentace | ❌ chybí | ✅ v `440aa04` |
| Vlastnosti (properties) | ❌ chybí | ✅ v `440aa04` |
| Sazby (rates, tenant/user/client) | ⚠️ základ | ✅ plný v `440aa04` |
| Voice → Work Report → Invoice | ❌ chybí | ✅ 299 řádků v `440aa04` |
| Voice session (start/input/resume) | ❌ chybí | ✅ plný v `440aa04` |
| Voice resolve + execute | ✅ stub clean | ✅ plný v `440aa04` |
| AI processing (/process) | ❌ chybí | ✅ v `440aa04` |
| Translate (/translate) | ❌ chybí | ✅ v `440aa04` |
| Session summarize | ❌ chybí | ✅ v `440aa04` |
| Assistant memory | ❌ chybí | ✅ v `440aa04` |
| WhatsApp integrace | ❌ chybí | ✅ plný v `440aa04` |
| Email (SMTP/SendGrid) | ❌ chybí | ✅ v `tool_packages` v `3966a60^` |
| Admin activity-log | ❌ chybí | ✅ v `440aa04` |
| Admin hierarchy-integrity | ❌ chybí | ✅ v `440aa04` |
| Zaměstnanci / role / práva | ✅ základ | ✅ rozvinutý v `440aa04` |
| Auth / JWT | ✅ funkční | ✅ |
| Company profile | ✅ funkční | ✅ |
| Jazyky / multilang | ✅ funkční | ✅ |
| Activity catalogue (1814 typů) | ✅ funkční | ✅ |
| Activity pricing | ✅ funkční | ✅ |
| Backup / restore | ✅ funkční | ✅ |
| Bootstrap / first-install | ✅ funkční | ✅ |
| Tenant izolace | ✅ funkční | ✅ |
| Industry templates | ✅ v katalogu | ✅ |
| Import kontaktů (sync-contacts) | ❌ chybí | ✅ v `440aa04` |
| Checkatrade workflow | ❌ chybí | nutno navrhnout |
| Klientský portál | ❌ chybí | nutno navrhnout |
| Opakované úkoly | ❌ chybí | nutno navrhnout |
| Sklad a materiál | ❌ chybí | základ v work-report |
| Smlouvy (contracts) | ❌ chybí | nutno navrhnout |

---

## 4. HLAVNÍ PRODUKTOVÁ FUNKCE — SRDCE SECRETARY

```
HLAS → WORK REPORT → INVOICE → SEND
```

1. Uživatel hlasem řekne, co se udělalo
2. Systém vytvoří pracovní report
3. Systém spočítá položky (hodiny × sazba + materiál + odpad)
4. Systém vytvoří fakturu
5. Systém ji odešle klientovi (WhatsApp / email)

**Toto je prioritní workflow č. 1. Vše ostatní je sekundární.**

---

## 5. NEJDŮLEŽITĚJŠÍ BUSINESS WORKFLOW

```
1.  Nový klient          → POST /crm/clients
2.  Nová poptávka        → POST /crm/leads
3.  Nabídka              → POST /crm/quotes
4.  Schválení            → POST /crm/quotes/{id}/approve
5.  Vytvoření zakázky    → POST /crm/jobs
6.  Delegace úkolů       → POST /crm/tasks
7.  Práce v terénu       → voice session start/input
8.  Nahrání fotek        → POST /crm/photos
9.  Změna termínu        → PUT /crm/jobs/{id}
10. Dokončení            → PUT /crm/jobs/{id} (status: completed)
11. Work report          → POST /work-reports (z voice session)
12. Faktura              → POST /crm/invoices/from-work-report
13. Odeslání klientovi   → POST /whatsapp/send nebo email
14. Následná péče        → notifikace + calendar-feed
```

---

## 6. AKTUÁLNÍ ARCHITEKTURA — CLEAN BACKEND

Umístění: `server/secretary_clean/`  
Deploy: Railway via `server/` git submodule  
Branch: `main`

### Struktura:
```
secretary_clean/
├── app.py                      ← FastAPI factory, router registration
├── api/
│   ├── deps.py                 ← current_user, get_repository, require_permission
│   └── routes/
│       ├── auth.py             ← POST /login, /refresh, GET /me, /users CRUD
│       ├── bootstrap.py        ← GET /bootstrap/status, POST /bootstrap/first-admin, /version
│       ├── company.py          ← GET/PUT /company/profile, industry
│       ├── users.py            ← GET/PUT/DELETE /users + /users/reset-password, /wipe
│       ├── language.py         ← GET /languages, /languages/tenant, POST /languages/apply
│       ├── catalogue.py        ← GET /catalogue (1814 work types)
│       ├── tenant_pricing.py   ← GET/POST/PUT/DELETE /tenant-pricing
│       ├── activities_compat.py← Legacy Android ID bridge (CRC32)
│       ├── crm.py              ← ⚠️ STUB: GET+POST pro 7 modulů, chybí detail/update/delete
│       ├── voice.py            ← ⚠️ STUB: /resolve + /execute, hardcoded intents
│       ├── backup.py           ← POST /backup, POST /restore
│       └── __init__.py
├── core/
│   ├── models.py               ← Pydantic modely
│   ├── repository.py           ← InMemorySecretaryRepository interface
│   └── security.py             ← JWT, password hash
├── db/
│   ├── migration.py            ← Auto-migration při startu
│   └── postgres_repository.py  ← PostgreSQL implementace
└── catalogue/
    ├── source_parser.py        ← Parser secretary_work_types_tree_pricing_logic.txt
    └── models.py
```

### Registrované routery (prefix `/api/v1`):
| Router | Prefix | Status |
|---|---|---|
| bootstrap | /api/v1 | ✅ funkční |
| auth | /api/v1/auth | ✅ funkční |
| company | /api/v1/company | ✅ funkční |
| users | /api/v1/users | ✅ funkční |
| catalogue | /api/v1/catalogue | ✅ funkční |
| language | /api/v1/languages | ✅ funkční |
| tenant_pricing | /api/v1/tenant-pricing | ✅ funkční |
| crm | /api/v1/crm | ⚠️ stub |
| voice | /api/v1/voice | ⚠️ stub |
| backup | /api/v1/backup | ✅ funkční |
| activities_compat | /api/v1/activities | ✅ funkční |
| version_router | /api/v1/version | ✅ funkční |

---

## 7. CO BYLO CHYBNĚ SMAZÁNO

### Commit `3966a60` — "cleanup: remove all legacy server files"

Tento commit odstranil nejen skutečná residua, ale i **připravenou business logiku** a **budoucí moduly**.

### Skupina 1 — Business logika (musí být obnovena v clean architektuře)

#### `main.py` — commit `440aa04` (10 383 řádků)
Obsahoval plnou implementaci těchto endpointů, které v clean backendu chybí:

```
# Voice session — GPT work-report konverzace (299 řádků logiky)
POST /voice/session/start
POST /voice/session/input    ← Czech number parsing, entries, materials, waste, confirm
POST /voice/session/resume

# AI
POST /process                ← GPT chat endpoint
POST /translate              ← překlad zpráv
POST /session/summarize      ← dialog session s pamětí

# Paměť asistenta
GET    /assistant/memory
POST   /assistant/memory
DELETE /assistant/memory/{memory_id}

# CRM — detail operace (chybí v clean backendu)
GET    /crm/clients/{id}
PUT    /crm/clients/{id}
DELETE /crm/clients/{id}
POST   /crm/clients/sync-contacts
POST   /crm/clients/{id}/notes
GET    /crm/jobs/{id}
PUT    /crm/jobs/{id}
POST   /crm/jobs/{id}/notes
PUT    /crm/tasks/{id}
DELETE /crm/tasks/{id}

# Chybějící CRM moduly
GET/POST         /crm/leads
GET/PUT          /crm/leads/{id}
POST             /crm/leads/{id}/convert-to-client
POST             /crm/leads/{id}/convert-to-job
GET/POST         /crm/quotes  +  /{id}, items, approve
PUT              /crm/invoices/{id}
GET/POST/DELETE  /crm/invoices/{id}/items
GET/POST         /crm/invoices/{id}/payments
POST             /crm/invoices/from-work-report     ← KRITICKÁ (Voice→Invoice)
POST             /crm/invoices/batch-from-work-reports
GET/POST         /crm/communications
GET/POST         /crm/photos
GET              /crm/properties
GET              /crm/waste
GET              /crm/timeline
GET              /crm/calendar-feed
GET/POST         /crm/notifications  +  mark-read
POST/GET         /crm/import
GET              /crm/export/csv

# Work reports
POST /work-reports
GET  /work-reports
GET  /work-reports/{id}

# Sazby
GET/PUT /crm/users/{id}/rates
PUT     /crm/clients/{id}/rate
GET/PUT /tenant/default-rates/{tenant_id}

# WhatsApp
GET  /whatsapp/webhook
POST /whatsapp/webhook
POST /whatsapp/send
GET  /whatsapp/status

# Admin
GET  /admin/activity-log
GET  /admin/hierarchy-integrity
POST /admin/hierarchy-integrity/backfill

# Pricing rules
GET/POST /pricing-rules
```

#### `ai_control_bridge.py` — 1 145 řádků
Sémantický bridge: hlas → intent → action.  
Architektura: resolve_order = context → exact synonym → entity alias → embedding → AI → clarification.  
AI NENÍ rozhodovatel. AI je sémantický bridge.  
Obsahoval: `resolve_voice_command()`, `update_voice_context()`, `get_screen_controls()`, `generate_synonyms_for_control()`, `approve_synonym()`, `reject_synonym()`.  
**Status: BUSINESS LOGIKA — musí být přenesena do clean backendu.**

#### `action_executor.py` — 366 řádků
Unified action handler: každá UI akce, hlasový příkaz i API call šly přes `execute_action()`.  
Zajišťoval: permission check, risk-level confirmation, audit log, single source of truth.  
**Status: ARCHITEKTURA — vzor musí být zachován v clean backendu.**

#### `data_importer.py` — 1 206 řádků
Import systém: kontakty, komunikační historii, kalendář, fotky, pracovní záznamy.  
**Status: BUDOUCÍ MODUL — archivovat, nemazat.**

### Skupina 2 — Připravené pluginy a nástroje (archivovat, nemazat)

#### `tool_packages/nature_recognition/` + `tool_packages/sendgrid_email/`
Plugin architektura: manifest.json, install.py, uninstall.py, commands.json, config_schema.json.  
**Status: BUDOUCÍ ROZŠÍŘENÍ — vhodný vzor pro plugin systém.**

#### `tool_exporter.py`, `tool_installer.py`, `tool_manifest_validator.py`, `tool_connection_test.py`, `tool_secret_store.py`
Plugin management system (celkem ~2 200 řádků).  
**Status: BUDOUCÍ MODUL — archivovat.**

### Skupina 3 — Skutečná residua (správně smazána)

- `mcp-gateway/` — experimentální MCP gateway, nesouvisí s hlavním deploymentem
- `schema.sql`, `schema_railway_snapshot.sql` — nahrazeny migration systémem v clean backendu
- `setup_db.py` — nahrazen `db/migration.py`
- `tests/test_language.py` — testy pro starou architekturu (přepsat pro clean)
- `migrations/*.sql` — historické SQL migrace pro starý schema, nahrazeny clean migration

---

## 8. RECOVERY ARCHIVE

Všechna smazaná business logika je dostupná v git historii:

```bash
# Nejbohatší verze old main.py (10 383 řádků):
git show 440aa04:main.py

# ai_control_bridge.py:
git show 440aa04:ai_control_bridge.py

# action_executor.py:
git show 440aa04:action_executor.py

# data_importer.py:
git show 440aa04:data_importer.py

# tool_packages/:
git show 3966a60^:tool_packages/nature_recognition/manifest.json
```

**Commit `440aa04` je referenční bod pro recovery.**  
**Commit `3966a60` je HEAD — clean architektura.**

---

## 9. VÝVOJOVÝ POSTUP OD TOHOTO BODU

### Fáze 1 — Recovery Archive *(hotovo — viz tento dokument)*
- ✅ git log analyzován
- ✅ smazané soubory identifikovány
- ✅ chybějící endpointy katalogizovány
- ✅ klasifikace: business logika / budoucí modul / skutečné residuum

### Fáze 2 — CRM Detail Endpointy *(priorita 1)*
Rozšířit `secretary_clean/api/routes/crm.py`:

```
GET    /crm/clients/{id}
PUT    /crm/clients/{id}
DELETE /crm/clients/{id}
GET    /crm/jobs/{id}
PUT    /crm/jobs/{id}
PUT    /crm/tasks/{id}
DELETE /crm/tasks/{id}
```

Vzor: čistý router, plná tenant izolace, PostgresRepo metody, audit log.

### Fáze 3 — Work Reports + Invoice from Work Report *(priorita 2)*
Kritická pro srdce produktu (Voice → Work Report → Invoice → Send).

```
POST /work-reports
GET  /work-reports
GET  /work-reports/{id}
POST /crm/invoices/from-work-report
```

Nový router: `secretary_clean/api/routes/work_reports.py`

### Fáze 4 — Voice Session Flow *(priorita 3)*
Obnovit GPT konverzační flow z commitu `440aa04`:

```
POST /voice/session/start
POST /voice/session/input
POST /voice/session/resume
```

Nový router: `secretary_clean/api/routes/voice_session.py`  
Vzor z `git show 440aa04:main.py` (sekce řádky 3178–3524).  
Adaptovat na clean architekturu: PostgresRepo, tenant izolace, permission guard.

### Fáze 5 — Leads, Quotes, Notifications *(priorita 4)*
Dokončení CRM pipeline:
- `POST /crm/leads`, detail, convert-to-client/job
- `GET/POST /crm/quotes` + items + approve
- `GET /crm/notifications` + mark-read

### Fáze 6 — AI, Translate, Session Summarize *(priorita 5)*
```
POST /process           ← OpenAI GPT chat
POST /translate         ← překlad
POST /session/summarize ← dialog memory
```

### Fáze 7 — Assistant Memory, Admin Audit, Hierarchy *(priorita 6)*
```
GET/POST/DELETE /assistant/memory
GET /admin/activity-log
GET /admin/hierarchy-integrity
```

### Fáze 8 — WhatsApp, Email, Notifications Push *(priorita 7)*
```
POST /whatsapp/send
GET/POST /whatsapp/webhook
```

### Fáze 9 — Plugin System, Tool Packages *(budoucnost)*
Obnovit z git historii až bude stabilní fáze 1–8.

---

## 10. CO SE NESMÍ MAZAT NIKDY BEZ SCHVÁLENÍ

Toto je seznam chráněných položek. Kdokoli navrhne smazat cokoliv z tohoto seznamu,
musí dostat explicitní schválení vlastníka projektu.

### Soubory v repozitáři:
- `secretary_clean/` — celý clean backend foundation
- `secretary_work_types_tree_pricing_logic.txt` — zdrojový katalog 1814 work types
- `secretary_clean/api/routes/activities_compat.py` — Android legacy ID bridge
- `secretary_clean/db/migration.py` — auto-migration systém
- `secretary_clean/db/postgres_repository.py` — PostgreSQL implementace

### Git reference:
- Commit `440aa04` — referenční bod business logiky
- Branch `main` v server submodulu

### Žádné soubory se nesmí mazat z důvodu:
- "není napojené v UI" → to není důvod ke smazání
- "není momentálně použité" → to není důvod ke smazání
- "připadá jako legacy" → to není důvod ke smazání bez analýzy
- "je to stub" → stub je záměrný placeholder, ne odpad

---

## 11. CO SE NESMÍ DĚLAT

- ❌ Vracet celý starý `main.py` jako hlavní backend (obsahuje legacy architekturu i business logiku — musí se separovat)
- ❌ Přepisovat projekt od nuly
- ❌ Mazat future moduly bez schválení
- ❌ Dělat UI opravy před backend workflow
- ❌ Řešit design před funkčním business flow
- ❌ Přidávat hlas před stabilním API workflow
- ❌ Obejít tenant izolaci z důvodu "rychlosti"
- ❌ Obejít permission check z důvodu "testování"
- ❌ Commitovat bez audit záznamu ve voice_command_logs
- ❌ Čistit kód bez předchozí analýzy každého souboru

---

## 12. SPRÁVNÝ POŘADÍ VÝVOJE

```
1. Logika (datový model, business rules)
2. Databáze (schema, migrace)
3. Backend (router, repository metoda, permission guard)
4. Android (ViewModel, API call, UI)
5. Voice (integrace až po stabilním API workflow)
```

Nikdy opačně.

---

*Tento dokument platí od commitu `3966a60` a nahrazuje všechny předchozí ad-hoc rozhodnutí.*  
*Jakákoli změna pravidel v sekci 2 nebo seznamu v sekci 10 vyžaduje explicitní schválení vlastníka.*
