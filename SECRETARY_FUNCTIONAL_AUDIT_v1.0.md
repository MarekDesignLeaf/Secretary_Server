# SECRETARY — FUNKČNÍ AUDIT v1.0

**Datum:** 2026-05-31
**Repozitář:** `C:\Users\hutra\AndroidStudioProjects\secretary\server`
**Remote:** `MarekDesignLeaf/Secretary_Server`
**HEAD:** `c642432`
**Backend:** `secretary_clean/` (clean architektura, FastAPI, prefix `/api/v1`)
**Deploy:** Railway — `storage_type: postgresql`, `postgres_error: null`, `is_ready: true`
**Server version (live):** `1.0.0` / `backend: secretary_clean`

> Audit zjišťuje skutečný stav z kódu (routery + repository) a z živého serveru.
> Žádné opravy, žádné změny kódu. Pouze zjištění stavu.
> Ověřené commity přítomné v repu: `0fa2cfc`, `71743ef`, `4ee94d6`, `548dd1b`.

---

## 0. POZNÁMKA K NEZACOMMITOVANÉ ZMĚNĚ

V pracovní kopii je nezacommitovaná modifikace `secretary_clean/api/routes/crm.py`
(5 insertů / 17 deletů). Je čistě **kosmetická** — náhrada českých znaků (á, é, —)
za ASCII a smazání komentářů. **Žádná změna business logiky.** Nebyla zahrnuta do
auditu funkčnosti, protože nemění chování. Doporučení: zahodit (`git checkout`)
nebo ponechat — bez dopadu.

---

## 1. ONBOARDING FIRMY

| Schopnost | Stav | Důkaz |
|---|---|---|
| Vytvoření první firmy | ✅ funkční | `POST /bootstrap/first-company`, `repository.create_first_company()` |
| Vytvoření prvního administrátora | ✅ funkční | `POST /bootstrap/first-admin` + `POST /bootstrap/first-install` (firma+admin atomicky), `repository.create_first_admin()` |
| Bootstrap status detekce | ✅ funkční | `GET /bootstrap/status` → živě vrací `is_ready: true` |
| Seed z ENV při startu | ✅ funkční | `app.py::_seed_from_env()` — SEED_ADMIN_EMAIL/PASSWORD/COMPANY |
| **Více oborů (industries)** | ⚠️ částečné | Onboarding ukládá **jeden** `industry_group` + `industry_subtype` (`update_company_industry`). Katalog oborů existuje (`GET /catalogue/industries`), ale tenant má profil **jediného** oboru, ne více současně. |
| **Více jazyků (multilang)** | ✅ funkční | `default_internal_language_code` + `default_customer_language_code` při instalaci; `replace_tenant_languages()` umožní víc jazyků per tenant; `_seed_default_languages()` |

**Závěr:** Onboarding vytvoří firmu + prvního admina + nastaví jazyky (interní i zákaznický, podporuje více jazyků). **Více oborů současně NEPODPORUJE** — pouze jeden primární obor na tenant.

---

## 2. ČINNOSTI A SAZBY

| Schopnost | Stav | Důkaz |
|---|---|---|
| Katalog typů práce (1814) | ✅ funkční | `catalogue/source_parser.py::load_catalogue()`, `GET /catalogue/*` |
| Vytvořit typ práce (tenant override) | ⚠️ jen override | `PUT /tenant-pricing/activities/{activity_code}` — neukládá nový typ, ale **override existujícího** z katalogu (`save_tenant_pricing`) |
| Upravit cenu | ✅ funkční | `PUT /tenant-pricing/activities/{activity_code}` s `TenantActivityOverrideRequest` |
| Uložit změny | ✅ funkční | `repository.save_tenant_pricing()` → PostgresRepo `save_tenant_pricing` |
| Reset/smazat override | ✅ funkční | `DELETE /tenant-pricing/activities/{activity_code}/override` |
| Číst tenant sazby | ✅ funkční | `GET /tenant-pricing/activities` → `list_tenant_pricing()` |

**Závěr:** Sazby lze upravovat a ukládat (override nad katalogem 1814 typů). **Nelze vytvořit zcela nový vlastní typ práce mimo katalog** — pouze override existujícího.

---

## 3. HLASOVÉ PŘÍKAZY

Dva oddělené systémy:

### 3a. `/voice` (foundation) — STUB
| Co dělá | Stav |
|---|---|
| `POST /voice/resolve` | ⚠️ stub — match proti **9 hardcoded frázím** (`create client`, `new job`, …), vrací intent string |
| `POST /voice/execute` | ⚠️ **NEZAPISUJE do DB** — vrátí jen `"accepted for execution by the application service layer"`. Žádná skutečná mutace. |

`_INTENTS` = 9 frází: create/new client, create/new job, create/new task, create quote, create invoice, work report.
Žádné embedding, žádné AI, žádné synonyma. Pouze substring match. **Reálně neprovede žádnou akci.**

### 3b. `/voice/session` (work-report dialog) — FUNKČNÍ, ale in-process
| Co dělá | Stav |
|---|---|
| `POST /voice/session/start` | ✅ otevře dialog session |
| `POST /voice/session/input` | ✅ posouvá dialog (client→entries→materials→waste→confirm), Czech number parsing |
| `POST /voice/session/resume` | ✅ obnoví pozastavenou session |
| Zápis do DB | ✅ na confirm volá `repository.create_work_report()` → **reálně persistuje** |
| ⚠️ Riziko | Session store je **module-level dict** (`_SESSIONS`) — **ztratí se při restartu serveru**. Nepřežije redeploy. |

**Závěr:**
- `/voice/execute` pouze potvrzuje intent, **NIC nezapisuje** (stub).
- `/voice/session/input` **reálně vytvoří work report v DB** při confirm — to je jediná hlasová cesta která píše do databáze.
- Sessions nejsou perzistentní (in-memory) — riziko ztráty při restartu.

---

## 4. KALENDÁŘ

| Schopnost | Stav | Důkaz |
|---|---|---|
| Čtení Google Calendar | ❌ není v backendu | Backend nemá žádný Google Calendar endpoint ani integraci |
| "Co je zítra" | ❌ není v backendu | Žádný calendar-feed endpoint |
| Vytvořit/změnit/zrušit událost | ❌ není v backendu | Žádný `/crm/calendar-feed`, `/crm/appointments` |

**Závěr:** Kalendář **v clean backendu zcela chybí**. Recovery plán ho uvádí jako `❌ chybí` (byl v `440aa04`).
Pozn.: Google Calendar čtení je implementováno na **Android straně** (CalendarManager čte z telefonu), ne v backendu. Backend o kalendáři neví.

---

## 5. CRM MODULY

Generický list+create existuje pro **7 modulů** (smyčka přes `CRM_MODULES`):
`clients, jobs, tasks, quotes, invoices, communications, work_reports`

| Modul | List | Create | Detail (GET/{id}) | Update (PUT) | Delete | Notes |
|---|---|---|---|---|---|---|
| **clients** | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ `/notes` |
| **jobs** | ✅ | ✅ | ✅ | ✅ | ❌ | ✅ `/notes` |
| **tasks** | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ |
| **quotes** | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ |
| **invoices** | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ |
| **communications** | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ |
| **work_reports** | ✅ | ✅ | ✅ (přes `/work-reports/{id}`) | ❌ | ❌ | ❌ |

### Kontakty
- ❌ **Žádný `/crm/clients/sync-contacts`** v clean backendu (byl v `440aa04`).
- Tabulka kontaktů / contact sorting / merge — **není v clean backendu** (byla řešena ve starém `main.py`).

### Work Reports + Invoice
| Endpoint | Stav |
|---|---|
| `POST /work-reports` | ✅ funkční |
| `GET /work-reports` | ✅ funkční |
| `GET /work-reports/{id}` | ✅ funkční |
| `POST /crm/invoices/from-work-report` | ✅ funkční, **rates binding implementován** |

**Rates binding (invoice line items):** priorita v `create_invoice_from_work_report`:
1. `worker.hourly_rate` (pokud > 0)
2. tenant default labour rate (kódy `labour/labor/...` nebo metoda `hourly`)
3. `0.0` + `pricing_warnings`

⚠️ **Krok "user-specific rate" (bod 2 ze zadání) CHYBÍ** — současná logika má jen worker.hourly_rate → tenant rate → 0.0. Nepřeskakuje přes user-specific sazbu.

### Chybějící CRM moduly (vs recovery plán)
❌ leads, ❌ photos, ❌ properties, ❌ timeline, ❌ calendar-feed, ❌ notifications, ❌ import/export, ❌ quote items/approve, ❌ invoice items/payments, ❌ invoice update.

---

## 6. ROLE A OPRÁVNĚNÍ

| Role | Stav | Důkaz |
|---|---|---|
| admin | ✅ | `GET /auth/roles`, `list_roles()` |
| manager | ✅ | role v systému |
| worker | ✅ | role v systému |
| assistant | ✅ | role v systému |

| Mechanismus | Stav | Důkaz |
|---|---|---|
| JWT autentizace | ✅ funkční | `core/security.py`, `POST /auth/login`, `/refresh` |
| Permission guard | ✅ funkční | `require_permission(Permission.X)` na endpointech (crm_manage, voice_execute, …) |
| Tenant izolace | ✅ funkční | Všechny repo metody filtrují `company_id` |
| Ownership guard (worker vidí jen své) | ⚠️ neověřeno | Permission guard existuje, ale jemnozrnná ownership logika (worker = jen vlastní úkoly) nebyla v auditu potvrzena |

**Závěr:** 4 role existují, JWT + permission guard + tenant izolace fungují. Ownership-level filtrování (worker jen své záznamy) vyžaduje hlubší ověření.

---

## 7. API ENDPOINTY — KOMPLETNÍ MAPA

### Registrované routery (prefix `/api/v1`):
bootstrap, version, auth, company, users, catalogue, language, tenant-pricing, crm, work_reports, voice, voice/session, backup, activities.

### ✅ EXISTUJE A FUNGUJE
```
GET  /api/v1/version
GET  /api/v1/bootstrap/status
POST /api/v1/bootstrap/first-company | first-admin | first-install | wipe
POST /api/v1/auth/login | refresh | register | change-password
GET  /api/v1/auth/me | roles | first-login-users
GET/PUT /api/v1/company/profile | legal-identity | operating-settings | operating-profile
PUT  /api/v1/company/industry
GET  /api/v1/users  | POST /users | PUT/DELETE /users/{id} | reset-password
GET  /api/v1/catalogue/industries | pricing-methods | additional-charges | validation-summary
GET/PUT /api/v1/language/settings | available | tenant | client/{id} | context
GET  /api/v1/tenant-pricing/activities | PUT/DELETE override
GET/POST /api/v1/crm/{clients|jobs|tasks|quotes|invoices|communications|work_reports}   (list+create)
GET/PUT/DELETE /api/v1/crm/clients/{id} + /notes
GET/PUT /api/v1/crm/jobs/{id} + /notes
GET/PUT/DELETE /api/v1/crm/tasks/{id}
POST/GET /api/v1/work-reports | GET /work-reports/{id}
POST /api/v1/crm/invoices/from-work-report
POST /api/v1/voice/session/start | input | resume   (input zapisuje work report)
POST /api/v1/backup/create | GET manifests | restore/{token} | biometric
GET  /api/v1/activities/groups | subtypes | templates | tenant
```

### ⚠️ EXISTUJE JAKO STUB (neprovede reálnou akci)
```
POST /api/v1/voice/resolve     — jen match 9 frází
POST /api/v1/voice/execute     — NEZAPISUJE do DB, jen potvrdí intent
```

### ❌ VRACÍ 404 / NEEXISTUJE V BACKENDU (ale bylo v `440aa04` nebo je v Androidu)
```
# CRM detail/moduly
GET/POST /crm/leads + convert-to-client/job
GET/POST /crm/quotes/{id} | items | approve
PUT /crm/invoices/{id} | items | payments
DELETE /crm/jobs/{id}
GET/POST /crm/communications/{id}
POST /crm/clients/sync-contacts
GET/POST /crm/photos
GET /crm/properties | waste | timeline | calendar-feed
GET/POST /crm/notifications + mark-read
POST/GET /crm/import | export/csv

# Kontakty (řešeno ve starém main.py + Androidu)
GET  /crm/contacts | sort-session | duplicates | audit
POST /crm/contacts/assign-section | merge | migrate-from-clients

# AI / komunikace
POST /process | translate | session/summarize
GET/POST/DELETE /assistant/memory
POST /whatsapp/send | webhook | status

# Sazby (rozšířené)
GET/PUT /crm/users/{id}/rates
PUT /crm/clients/{id}/rate
GET/PUT /tenant/default-rates/{tenant_id}

# Admin
GET /admin/activity-log | hierarchy-integrity
POST /admin/hierarchy-integrity/backfill

# Pricing rules
GET/POST /pricing-rules

# Kalendář (celý modul)
veškeré calendar/appointment endpointy
```

### ⚠️ EXISTUJE JEN V ANDROIDU, NE V BACKENDU
```
Contact sorting / merge / duplicates  — Android UI volá /crm/contacts/* které v clean backendu NEEXISTUJÍ
WhatsApp send                          — Android má SEND_WHATSAPP akci, backend /whatsapp/send chybí
/process (GPT)                         — Android voice flow volá /process, backend ho nemá
/translate                             — Android překládá přes /translate, backend ho nemá
Google Calendar                        — Android CalendarManager čte z telefonu, backend kalendář nemá
```

---

## 8. SOUHRN STAVU

### ✅ Skutečně hotové a funkční
- Onboarding (firma + admin + jazyky)
- Auth (JWT, role, permission guard, tenant izolace)
- Company profile, legal identity, operating settings
- Users CRUD
- Katalog 1814 typů práce + tenant pricing override
- Jazyky (multilang)
- CRM clients (plný CRUD + notes)
- CRM jobs (CRUD bez delete + notes)
- CRM tasks (CRUD)
- CRM list+create pro quotes/invoices/communications
- Work reports (create/list/get)
- **Invoice from work report + rates binding** (worker rate → tenant rate → 0.0+warning)
- Voice session dialog → work report (zapisuje do DB)
- Backup/restore + biometric

### ⚠️ Vypadá hotové, ale není
- `/voice/execute` — stub, nezapisuje
- Voice session store — in-memory, nepřežije restart
- Tenant pricing — jen override, ne nové typy
- Onboarding — jen jeden obor, ne více
- Rates binding — **chybí user-specific rate krok** (bod 2 ze zadání)

### ❌ Chybí úplně (vs recovery plán)
- Leads, quotes detail, invoice items/payments
- Kontakty (sync, sort, merge, contacts tabulka)
- Photos, properties, timeline, notifications
- Kalendář (celý)
- AI: /process, /translate, /session/summarize
- Assistant memory
- WhatsApp send (backend)
- Admin activity-log, hierarchy-integrity
- Import/export

---

## 9. NEJBLIŽŠÍ KRITICKÝ NÁLEZ

**Rates binding** (otevřený úkol) je implementován **z 3/4**:
- ✅ 1) worker.hourly_rate
- ❌ 2) user-specific rate — **CHYBÍ**
- ✅ 3) tenant default labour rate
- ✅ 4) 0.0 + pricing_warnings

Pro splnění zadání (4-stupňová priorita) chybí vložit **user-specific rate** mezi worker.hourly_rate a tenant rate.

---

*Konec auditu. Žádné změny kódu provedeny. Pouze zjištění stavu z routerů, repository a živého serveru.*
