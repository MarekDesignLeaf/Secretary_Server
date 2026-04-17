# Secretary – Povinná hierarchie předávání práce

## Pravidlo systému

> Žádný klient, žádná zakázka a žádný úkol nesmí existovat bez odpovědné aktivní osoby a bez naplánované navazující akce přiřazené aktivnímu uživateli.

---

## Backend checklist

### Sprint 1 – DB migrace

- [ ] Spustit `migrations/hierarchy_v1.sql` (fáze 1 – nullable sloupce)
- [ ] Ověřit, že indexy jsou vytvořeny
- [ ] Spustit audit query (sekce 7 v migraci) a exportovat výsledky
- [ ] Napsat backfill skript (sekce 8) a otestovat v transakci s ROLLBACK
- [ ] Spustit backfill v produkci
- [ ] Spustit audit znovu – potvrdit nula orphanů
- [ ] Aktivovat triggers (odkomentovat CREATE TRIGGER řádky)
- [ ] Aktivovat NOT NULL constraints pro `clients.owner_user_id`, `jobs.assigned_user_id`, `tasks.assigned_user_id`

### Sprint 2 – Backend API

#### Nové helper funkce

- [ ] `validate_active_user(conn, tenant_id, user_id)` – ověří `status='active'` a `deleted_at IS NULL`
- [ ] `validate_task_planning(task_payload)` – ověří assignee + čas
- [ ] `validate_client_hierarchy(conn, client_id)` – ověří ownera + next_action
- [ ] `validate_job_hierarchy(conn, job_id)` – ověří ownera + next_action
- [ ] `set_client_next_action(conn, client_id, task_id)` – nastaví a validuje next_action
- [ ] `set_job_next_action(conn, job_id, task_id)` – nastaví a validuje next_action
- [ ] `complete_task_with_replacement(conn, task_id, replacement_payload)` – atomická transakce
- [ ] `get_hierarchy_integrity_report(conn, tenant_id)` – vrátí orphan lists

#### Úpravy endpointů

**POST /crm/clients**
- [ ] Vyžadovat `owner_user_id` v body
- [ ] Vyžadovat `first_action` objekt (`title`, `assigned_user_id`, `planned_start_at` nebo `deadline`)
- [ ] Transakce: vytvoř klienta → vytvoř task → ulož `next_action_task_id` → audit
- [ ] Při selhání: ROLLBACK celé transakce

**PUT /crm/clients/{client_id}**
- [ ] Blokovat smazání `owner_user_id`
- [ ] Blokovat nastavení neaktivního ownera
- [ ] Blokovat `next_action_task_id = null` bez náhrady

**POST /crm/jobs**
- [ ] Vyžadovat `assigned_user_id`
- [ ] Vyžadovat `first_action` objekt
- [ ] Transakce: vytvoř zakázku → vytvoř task → ulož `next_action_task_id` → audit

**PUT /crm/jobs/{job_id}**
- [ ] Blokovat odpojení `assigned_user_id`
- [ ] Blokovat neaktivní assignee
- [ ] Blokovat `next_action_task_id = null` bez náhrady

**POST /crm/tasks**
- [ ] Vyžadovat `assigned_user_id`
- [ ] Vyžadovat `planned_start_at` nebo `deadline`
- [ ] Volitelně: pokud `set_as_client_next_action=true`, aktualizovat `clients.next_action_task_id`
- [ ] Volitelně: pokud `set_as_job_next_action=true`, aktualizovat `jobs.next_action_task_id`

**PUT /crm/tasks/{task_id}** (complete)
- [ ] Pokud task je `next_action_task_id` klienta nebo zakázky:
  - Vyžadovat `replacement_task_id` NEBO `replacement_task_payload`
  - Vrátit HTTP 422 bez toho
- [ ] `complete_task_with_replacement()` v jedné transakci

**PUT /auth/users/{user_id}** (deactivate)
- [ ] Před deaktivací zkontrolovat:
  - Je owner klienta? → vrátit seznam
  - Je owner zakázky? → vrátit seznam
  - Je assignee otevřeného tasku? → vrátit seznam
  - Je assignee next_action_task_id? → vrátit seznam
- [ ] Vrátit HTTP 409 s listem blokujících entit

**GET /admin/hierarchy-integrity** (nový endpoint)
- [ ] Auth: `manage_users` nebo role `manager`/`admin`
- [ ] Vrátit:
  ```json
  {
    "orphan_clients": [],
    "orphan_jobs": [],
    "orphan_tasks": [],
    "blocked_user_deactivations": [],
    "next_action_mismatches": []
  }
  ```

### Sprint 3 – Audit log

Každá z těchto akcí musí jít do `activity_timeline`:

- [ ] Změna `owner_user_id` klienta
- [ ] Změna `assigned_user_id` zakázky
- [ ] Změna `assigned_user_id` tasku
- [ ] Změna `next_action_task_id` klienta
- [ ] Změna `next_action_task_id` zakázky
- [ ] Dokončení next_action tasku
- [ ] Blokovaný pokus o zneplatnění hierarchie
- [ ] Blokovaná deaktivace uživatele

Formát záznamu:
```
Aktor: {user.display_name} ({user.id})
Entita: {entity_type} #{entity_id}
Změna: {field} z '{old}' na '{new}'
Čas: {timestamp}
```

---

## Datový model (cílový stav)

```
clients
  + owner_user_id       BIGINT NOT NULL → users.id
  + next_action_task_id TEXT   NOT NULL → tasks.id
  + hierarchy_status    TEXT   DEFAULT 'valid'

jobs
  ~ assigned_user_id   BIGINT NOT NULL (zpřísnění)
  + next_action_task_id TEXT   NOT NULL → tasks.id
  + hierarchy_status    TEXT   DEFAULT 'valid'

tasks
  ~ assigned_user_id   BIGINT NOT NULL (zpřísnění)
  ~ planned_start_at OR deadline (logická podmínka)
  + task_source        TEXT   DEFAULT 'manual'
```

## Pravidla rolí

| Akce | admin | manager | worker | assistant |
|------|-------|---------|--------|-----------|
| Změnit owner klienta | ✓ | ✓ | ✗ | ✗ |
| Změnit owner zakázky | ✓ | ✓ | ✗ | ✗ |
| Přeposlat task | ✓ | ✓ | jen vlastní | ✗ |
| Dokončit next_action bez náhrady | ✗ | ✗ | ✗ | ✗ |
| Deaktivovat uživatele s vazbami | ✗ | ✗ | ✗ | ✗ |

## Stavové přechody integrity

```
hierarchy_status:
  unchecked → valid    (po backfill + validaci)
  unchecked → orphan   (audit zjistil problém)
  valid → orphan       (odebrán owner nebo next_action)
  orphan → valid       (opraveno ownerem/next_action)
```

## Akceptační kritéria

### Klient
- [ ] Nelze vytvořit klienta bez ownera
- [ ] Nelze vytvořit klienta bez první akce
- [ ] Nelze uložit klienta s neaktivním ownerem
- [ ] Nelze ponechat klienta bez validního `next_action_task_id`

### Zakázka
- [ ] Nelze vytvořit zakázku bez ownera
- [ ] Nelze vytvořit zakázku bez první akce
- [ ] Nelze zavřít zakázku bez vyřešené návaznosti
- [ ] Nelze ponechat zakázku bez validního `next_action_task_id`

### Task
- [ ] Nelze vytvořit task bez assignee
- [ ] Nelze vytvořit task bez času
- [ ] Nelze přiřadit task neaktivnímu uživateli
- [ ] Nelze dokončit current next_action bez náhrady

### Uživatel
- [ ] Nelze deaktivovat uživatele pokud drží živou odpovědnost

### Dashboard
- [ ] Po aktivaci tvrdých pravidel musí být orphan count = 0
