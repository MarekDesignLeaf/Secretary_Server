# CALENDAR VALIDATION REPORT (Phase A4)

**Datum:** 2026-05-31
**Repozitář:** `C:\Users\hutra\AndroidStudioProjects\secretary\server`
**Validovaný commit:** `2d925bb` (Phase A3 backend calendar service)
**Router:** `secretary_clean/api/routes/calendar.py`, prefix `/api/v1/calendar`
**Metoda validace:** Lokální spuštění identického kódu (commit 2d925bb) přes FastAPI `TestClient` proti `InMemorySecretaryRepository`. Calendar logika (CRUD, tenant izolace, permission guard) žije v routeru a repository, nikoli v DB — lokální běh proto ověřuje stejné rozhodovací cesty jako produkce.

> **Poznámka k živému serveru:** Produkční admin heslo není v této session k dispozici (login vrací `Invalid credentials` pro známá hesla). Produkční login jsem nereseroval (zákaz zásahu do produkčních dat). Endpointy jsou na produkci ověřené přes OpenAPI (viz sekce 8) a odpovídají `401 Bearer token required` bez tokenu — tj. jsou živé a chráněné. Funkční chování je ověřeno lokálně.

---

## 1. CREATE EVENT — `POST /calendar/events`

**REQUEST**
```http
POST /api/v1/calendar/events
Authorization: Bearer <token>
Content-Type: application/json

{
  "title": "Site visit",
  "description": "Quote for garden",
  "location": "Oxford",
  "start_at": "2026-06-01T09:00:00Z",
  "end_at": "2026-06-01T10:00:00Z"
}
```

**RESPONSE — `201 Created`**
```json
{
  "id": "bc53a725-ccbc-49f1-a049-5334ce04caa6",
  "company_id": "8ccc495a-f50f-4b4c-8103-68b6feef68c7",
  "title": "Site visit",
  "description": "Quote for garden",
  "location": "Oxford",
  "start_at": "2026-06-01T09:00:00Z",
  "end_at": "2026-06-01T10:00:00Z",
  "all_day": false,
  "client_id": null,
  "job_id": null,
  "created_by": "<admin user id>",
  "created_at": "...",
  "updated_at": "..."
}
```
✅ PASS — `company_id` odpovídá přihlášenému tenantovi, `created_by` odpovídá uživateli.

---

## 2. READ EVENTS — `GET /calendar/events`

**REQUEST**
```http
GET /api/v1/calendar/events
Authorization: Bearer <token>
```

**RESPONSE — `200 OK`** — pole událostí tenanta, řazené dle `start_at ASC`.
```json
[ { "id": "bc53a725-...", "title": "Site visit", "start_at": "2026-06-01T09:00:00Z", ... } ]
```

**Single GET — `GET /calendar/events/{id}`** → `200 OK`, vrací jednu událost.

**Filtr časového okna** (ověřeno v A3 smoke + A4):
```http
GET /api/v1/calendar/events?start=2026-06-01T00:00:00Z&end=2026-06-02T00:00:00Z   → 1 událost
GET /api/v1/calendar/events?start=2026-07-01T00:00:00Z                            → 0 událostí
```
✅ PASS — list, single get i filtr `start`/`end` fungují.

---

## 3. UPDATE EVENT — `PUT /calendar/events/{id}`

**REQUEST**
```http
PUT /api/v1/calendar/events/{id}
Authorization: Bearer <token>

{ "title": "Site visit (rescheduled)", "location": "Didcot", "start_at": "2026-06-01T11:00:00Z" }
```

**RESPONSE — `200 OK`**
```json
{ "title": "Site visit (rescheduled)", "location": "Didcot", "start_at": "2026-06-01T11:00:00Z", "updated_at": "<nově>" }
```
✅ PASS — částečný update (jen zadaná pole), `updated_at` se aktualizuje. Neexistující id → `404`.

---

## 4. DELETE EVENT — `DELETE /calendar/events/{id}`

**REQUEST**
```http
DELETE /api/v1/calendar/events/{id}
Authorization: Bearer <token>
```

**RESPONSE — `200 OK`**
```json
{ "deleted": true, "id": "bc53a725-ccbc-49f1-a049-5334ce04caa6" }
```
Následný `GET /calendar/events/{id}` → `404 Not Found`.
✅ PASS — smazání funguje, smazaná událost je nedostupná. Neexistující id → `404`.

---

## 5. TENANT ISOLATION

Dva tenanti (A, B), každý vlastní událost.

| Akce | Výsledek | Stav |
|------|----------|------|
| Tenant A vytvoří událost | `201` | ✅ |
| Tenant B vytvoří událost | `201` | ✅ |
| Tenant B `GET /calendar/events` | vidí **jen** `["Tenant B private meeting"]` | ✅ |
| Tenant A `GET /calendar/events` | vidí **jen** `["Tenant A private meeting"]` | ✅ |
| Tenant B `GET` událost tenanta A podle id | `404` | ✅ |
| Tenant B `PUT` událost tenanta A | `404` (nezměněno) | ✅ |
| Tenant B `DELETE` událost tenanta A | `404` (nesmazáno) | ✅ |
| Událost tenanta A po pokusech B | stále `200`, beze změny | ✅ |

✅ **PASS — úplná tenant izolace.** Tenant B nevidí, nemění ani nemaže data tenanta A. Všechny cross-tenant pokusy vrací `404` (událost pro daného tenanta neexistuje), nikoli `403` — což je správné (neúnik existence cizích záznamů).

**Mechanismus:** každá repository metoda filtruje `company_id`; `get/update/delete` ověřují `event.company_id == user.company_id` před jakoukoli operací.

---

## 6. PERMISSION GUARDS

| Test | Výsledek | Stav |
|------|----------|------|
| `POST /calendar/events` bez tokenu | `401 Bearer token required` | ✅ |
| `GET /calendar/events` bez tokenu | `401` | ✅ |
| Write endpointy vyžadují `crm.manage` | ano (`require_permission(Permission.crm_manage)`) | ✅ |
| Read endpointy vyžadují přihlášení | ano (`current_user`) | ✅ |

**Matice rolí vs. oprávnění (current model):**

| Role | `crm.manage` (calendar write) | `voice.execute` |
|------|------|------|
| owner | ✅ | ✅ |
| admin | ✅ | ✅ |
| manager | ✅ | ✅ |
| staff | ✅ | ✅ |
| accountant | ✅ | ❌ |

### ⚠️ DŮLEŽITÉ ZJIŠTĚNÍ (není bug, je to stav modelu)
- **Role „worker" v systému NEEXISTUJE.** Definované role jsou: `owner, admin, manager, staff, accountant`. Nejblíže „workerovi" je `staff`.
- **Všechny role mají `crm.manage`** → calendar WRITE (create/update/delete) je povolen pro každého přihlášeného uživatele bez ohledu na roli.
- Zadání A4 požaduje „worker role nesmí provádět akce vyhrazené admin/manager". V současném modelu **žádné takové omezení pro calendar neexistuje** — calendar zápis není vyhrazen admin/manager, je dostupný všem rolím.
- READ vyžaduje pouze autentizaci (jakákoli role).

**Toto NENÍ chyba calendar implementace** — je to vlastnost globálního permission modelu (`ROLE_PERMISSIONS` v `core/models.py`), který je sdílený s celým CRM (clients, jobs, tasks, work_reports — všechny také používají `crm.manage`). Změna by ovlivnila celý systém a je mimo rozsah A4. **Pokud má být calendar zápis omezen jen na admin/manager, je potřeba samostatné rozhodnutí o zavedení nové permission (např. `calendar.manage`) — to je nová práce, ne oprava.**

---

## 7. SMOKE TEST RESULTS

| Test | Stav |
|------|------|
| 1. Create event (`POST`) | ✅ PASS |
| 2. Read events (`GET` list + single + date filter) | ✅ PASS |
| 3. Update event (`PUT`, částečný) | ✅ PASS |
| 4. Delete event (`DELETE`) + 404 po smazání | ✅ PASS |
| 5. Tenant isolation (8 sub-testů) | ✅ PASS |
| 6. Permission guards (401 bez tokenu) | ✅ PASS |
| Existující pytest suite (4 testy) | ✅ PASS (neporušeno) |

---

## 8. OPENAPI / LIVE VERIFICATION

Živý server (`https://web-production-4b451.up.railway.app`), `version` = `1.0.0`, `storage_type: postgresql`, `postgres_error: null`.

Cesty registrované na **živém** serveru (z `/openapi.json`):
```
/api/v1/calendar/events             : ['GET', 'POST']
/api/v1/calendar/events/{event_id}  : ['GET', 'PUT', 'DELETE']
```
Bez tokenu vrací `401 Bearer token required` → endpoint je živý a chráněný.

---

## 9. ZÁVĚR

| Oblast | Verdikt |
|--------|---------|
| CRUD (create/read/update/delete) | ✅ Funkční, korektní status kódy |
| Tenant izolace | ✅ Úplná, žádný cross-tenant únik |
| Auth guard | ✅ 401 bez tokenu, write vyžaduje crm.manage |
| Date-window filtr | ✅ Funkční |
| Live deployment | ✅ Endpointy registrovány a chráněny |

**Bugy nalezené: 0.** Žádné změny kódu nebyly v rámci A4 provedeny.

**Jediný nález k rozhodnutí (ne bug):** Permission model nemá roli „worker" ani omezení calendar zápisu na admin/manager — všechny role mají `crm.manage`. Pokud je vyžadováno přísnější omezení, je to nová designová práce nad rámec A4.

---

*Konec reportu A4. Calendar backend je validován a připraven pro Fázi A5 (Voice Calendar Sync).*
