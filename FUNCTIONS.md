# Secretary Server – Mapa funkcí, chybějící endpointy a vazby

## Pravidlo systému

> Tento dokument mapuje všechny existující endpointy, chybějící CRUD operace,
> neúplné vazby a oblasti bez implementace.

---

## 1. Kompletní mapa API endpointů

### AUTH

| Metoda | Cesta | Funkce | Stav |
|--------|-------|--------|------|
| POST | `/auth/login` | `auth_login()` | ✅ |
| POST | `/auth/refresh` | `auth_refresh()` | ✅ |
| GET | `/auth/me` | `auth_me()` | ✅ |
| GET | `/auth/permissions` | `auth_list_permissions()` | ✅ |
| GET | `/auth/roles` | `auth_list_roles()` | ✅ |
| GET | `/auth/users` | `auth_list_users()` | ✅ |
| GET | `/auth/first-login-users` | `auth_first_login_users()` | ✅ |
| PUT | `/auth/users/{user_id}` | `auth_update_user()` | ✅ |
| DELETE | `/auth/users/{user_id}` | `auth_delete_user()` | ✅ |
| POST | `/auth/register` | `auth_register()` | ✅ |
| PUT | `/auth/change-password` | `auth_change_password()` | ✅ |
| POST | `/auth/forgot-password` | – | ❌ CHYBÍ |
| POST | `/auth/verify-email` | – | ❌ CHYBÍ |

### CRM – Klienti

| Metoda | Cesta | Funkce | Stav |
|--------|-------|--------|------|
| GET | `/crm/clients` | `get_clients()` | ✅ |
| GET | `/crm/clients/search` | `search_clients()` | ✅ |
| GET | `/crm/clients/{id}` | `get_client_detail()` | ✅ |
| POST | `/crm/clients` | `api_create_client()` | ✅ |
| PUT | `/crm/clients/{id}` | `update_client()` | ✅ |
| DELETE | `/crm/clients/{id}` | `archive_client()` | ✅ soft-delete |
| POST | `/crm/clients/{id}/notes` | `add_client_note()` | ✅ |
| POST | `/crm/clients/sync-contacts` | `sync_contacts()` | ✅ |
| GET | `/crm/clients/{id}/service-rates` | `get_client_service_rates()` | ✅ |
| PUT | `/crm/clients/{id}/service-rates` | `update_client_service_rates()` | ✅ |
| PUT | `/crm/clients/{id}/rate` | `update_client_rate()` | ✅ |

### CRM – Nemovitosti ⚠️ NEÚPLNÉ

| Metoda | Cesta | Funkce | Stav |
|--------|-------|--------|------|
| GET | `/crm/properties` | `get_properties()` | ✅ |
| GET | `/crm/properties/{id}` | – | ❌ CHYBÍ |
| POST | `/crm/properties` | – | ❌ CHYBÍ |
| PUT | `/crm/properties/{id}` | – | ❌ CHYBÍ |
| DELETE | `/crm/properties/{id}` | – | ❌ CHYBÍ |
| GET | `/crm/properties/{id}/zones` | – | ❌ CHYBÍ |

### CRM – Zakázky

| Metoda | Cesta | Funkce | Stav |
|--------|-------|--------|------|
| GET | `/crm/jobs` | `get_jobs()` | ✅ |
| GET | `/crm/jobs/{id}` | `get_job_detail()` | ✅ |
| POST | `/crm/jobs` | `create_job()` | ✅ |
| PUT | `/crm/jobs/{id}` | `update_job()` | ✅ |
| DELETE | `/crm/jobs/{id}` | – | ❌ CHYBÍ |
| POST | `/crm/jobs/{id}/notes` | `add_job_note()` | ✅ |
| GET | `/crm/jobs/{id}/photos` | (via job detail) | ✅ |
| POST | `/crm/jobs/{id}/photos` | `add_job_photos()` | ✅ |
| POST | `/crm/jobs/{id}/audit` | `add_job_audit()` | ✅ |
| GET | `/crm/jobs/{id}/tasks` | – | ❌ CHYBÍ |
| GET | `/crm/jobs/search` | – | ❌ CHYBÍ |

### CRM – Úkoly

| Metoda | Cesta | Funkce | Stav |
|--------|-------|--------|------|
| GET | `/crm/tasks` | `get_tasks()` | ✅ |
| POST | `/crm/tasks` | `api_create_task()` | ✅ |
| PUT | `/crm/tasks/{id}` | `update_task()` | ✅ |
| DELETE | `/crm/tasks/{id}` | `delete_task()` | ✅ |
| GET | `/crm/calendar-feed` | `get_calendar_feed()` | ✅ |
| GET | `/crm/tasks/search` | – | ❌ CHYBÍ |

### CRM – Leady

| Metoda | Cesta | Funkce | Stav |
|--------|-------|--------|------|
| GET | `/crm/leads` | `get_leads()` | ✅ |
| POST | `/crm/leads` | `create_lead()` | ✅ |
| GET | `/crm/leads/{id}` | `get_lead_detail()` | ✅ |
| PUT | `/crm/leads/{id}` | `update_lead()` | ✅ |
| DELETE | `/crm/leads/{id}` | – | ❌ CHYBÍ |
| POST | `/crm/leads/{id}/convert-to-client` | `convert_lead_to_client()` | ✅ |
| POST | `/crm/leads/{id}/convert-to-job` | `convert_lead_to_job()` | ✅ |
| GET | `/crm/leads/search` | – | ❌ CHYBÍ |

### CRM – Nabídky

| Metoda | Cesta | Funkce | Stav |
|--------|-------|--------|------|
| GET | `/crm/quotes` | `list_quotes()` | ✅ |
| GET | `/crm/quotes/{id}` | `get_quote_detail()` | ✅ |
| POST | `/crm/quotes` | `create_quote()` | ✅ |
| PUT | `/crm/quotes/{id}` | `update_quote()` | ✅ |
| DELETE | `/crm/quotes/{id}` | – | ❌ CHYBÍ |
| POST | `/crm/quotes/{id}/items` | `add_quote_item()` | ✅ |
| PUT | `/crm/quotes/{id}/items/{item_id}` | `update_quote_item()` | ✅ (S12) |
| DELETE | `/crm/quotes/{id}/items/{item_id}` | `delete_quote_item()` | ✅ |
| POST | `/crm/quotes/{id}/approve` | `approve_quote()` | ✅ |

### CRM – Faktury

| Metoda | Cesta | Funkce | Stav |
|--------|-------|--------|------|
| GET | `/crm/invoices` | `get_invoices()` | ✅ |
| POST | `/crm/invoices` | `create_invoice()` | ✅ |
| PUT | `/crm/invoices/{id}` | `update_invoice()` | ✅ |
| DELETE | `/crm/invoices/{id}` | – | ❌ CHYBÍ |
| POST | `/crm/invoices/from-work-report` | `create_invoice_from_work_report()` | ✅ |
| POST | `/crm/invoices/batch-from-work-reports` | batch verze | ✅ |
| GET | `/crm/invoices/{id}/items` | `get_invoice_items()` | ✅ |
| POST | `/crm/invoices/{id}/items` | `add_invoice_item()` | ✅ |
| DELETE | `/crm/invoices/{id}/items/{item_id}` | `delete_invoice_item()` | ✅ |
| GET | `/crm/invoices/{id}/payments` | `get_payments()` | ✅ |
| POST | `/crm/invoices/{id}/payments` | `add_payment()` | ✅ |
| GET | `/crm/invoices/search` | – | ❌ CHYBÍ |

### CRM – Komunikace

| Metoda | Cesta | Funkce | Stav |
|--------|-------|--------|------|
| GET | `/crm/communications` | `get_communications()` | ✅ |
| POST | `/crm/communications` | `create_communication()` | ✅ |
| PUT | `/crm/communications/{id}` | – | ❌ CHYBÍ |
| DELETE | `/crm/communications/{id}` | – | ❌ CHYBÍ |

### CRM – Kontakty

| Metoda | Cesta | Funkce | Stav |
|--------|-------|--------|------|
| GET | `/crm/contact-sections` | `get_contact_sections()` | ✅ |
| POST | `/crm/contact-sections` | `create_contact_section()` | ✅ |
| GET | `/crm/contacts` | `get_shared_contacts()` | ✅ |
| POST | `/crm/contacts` | `create_shared_contact()` | ✅ |
| PUT | `/crm/contacts/{id}` | `update_shared_contact()` | ✅ |
| DELETE | `/crm/contacts/{id}` | `delete_shared_contact()` | ✅ |
| POST | `/crm/contacts/import` | `import_shared_contacts()` | ✅ |

### CRM – Notifikace

| Metoda | Cesta | Funkce | Stav |
|--------|-------|--------|------|
| GET | `/crm/notifications` | `get_notifications()` | ✅ |
| POST | `/crm/notifications` | `create_notification()` | ✅ |
| PUT | `/crm/notifications/{id}/read` | `mark_notification_read()` | ✅ |
| DELETE | `/crm/notifications/{id}` | – | ❌ CHYBÍ |
| PUT | `/crm/notifications/{id}` | – | ❌ CHYBÍ (full update) |

### Work Reports

| Metoda | Cesta | Funkce | Stav |
|--------|-------|--------|------|
| GET | `/work-reports` | `get_work_reports()` | ✅ |
| GET | `/work-reports/{id}` | `get_work_report()` | ✅ |
| POST | `/work-reports` | `create_work_report()` | ✅ |
| PUT | `/work-reports/{id}` | – | ❌ CHYBÍ |
| DELETE | `/work-reports/{id}` | – | ❌ CHYBÍ |

### Plants / Nature

| Metoda | Cesta | Funkce | Stav |
|--------|-------|--------|------|
| POST | `/plants/identify` | `identify_plant()` | ✅ |
| POST | `/plants/health-assessment` | `assess_plant_health()` | ✅ |
| POST | `/mushrooms/identify` | `identify_mushroom()` | ✅ |
| GET | `/nature/history` | `get_nature_history()` | ✅ |
| GET | `/nature/services/status` | `get_nature_services_status()` | ✅ |

### Voice

| Metoda | Cesta | Funkce | Stav |
|--------|-------|--------|------|
| POST | `/voice/session/start` | `voice_session_start()` | ✅ |
| POST | `/voice/session/input` | `voice_session_input()` | ✅ |
| POST | `/voice/session/resume` | `voice_session_resume()` | ✅ |
| DELETE | `/voice/session/{id}` | – | ❌ CHYBÍ (cleanup) |

### Admin

| Metoda | Cesta | Funkce | Stav |
|--------|-------|--------|------|
| GET | `/admin/activity-log` | `get_admin_activity_log()` | ✅ |
| GET | `/admin/hierarchy-integrity` | – | ❌ CHYBÍ (dle HIERARCHY.md) |
| GET | `/admin/users/{id}/activity` | – | ❌ CHYBÍ |

### Tenant / Onboarding

| Metoda | Cesta | Funkce | Stav |
|--------|-------|--------|------|
| GET | `/tenant/config/{id}` | `get_tenant_config_endpoint()` | ✅ |
| PUT | `/tenant/config/{id}/languages` | `update_tenant_languages_endpoint()` | ✅ |
| GET | `/tenant/default-rates/{id}` | `get_tenant_default_rates()` | ✅ |
| PUT | `/tenant/default-rates/{id}` | `update_tenant_default_rates()` | ✅ |
| GET | `/onboarding/presets` | `get_onboarding_presets()` | ✅ |
| GET | `/onboarding/industry-groups` | `get_industry_groups()` | ✅ |
| GET | `/onboarding/industry-subtypes/{id}` | `get_industry_subtypes()` | ✅ |
| GET | `/onboarding/status/{id}` | `get_onboarding_status()` | ✅ |
| POST | `/onboarding/company-setup` | `company_setup()` | ✅ |

### Pricing Rules

| Metoda | Cesta | Funkce | Stav |
|--------|-------|--------|------|
| GET | `/pricing-rules` | `list_pricing_rules()` | ✅ |
| POST | `/pricing-rules` | `create_pricing_rule()` | ✅ |
| PUT | `/pricing-rules/{id}` | – | ❌ CHYBÍ |
| DELETE | `/pricing-rules/{id}` | – | ❌ CHYBÍ |

### WhatsApp

| Metoda | Cesta | Funkce | Stav |
|--------|-------|--------|------|
| GET | `/whatsapp/webhook` | `wa_verify()` | ✅ |
| POST | `/whatsapp/webhook` | `wa_incoming()` | ✅ |
| POST | `/whatsapp/send` | `wa_send()` | ✅ |
| GET | `/whatsapp/status` | `wa_status()` | ✅ |

### System / Debug

| Metoda | Cesta | Funkce | Stav |
|--------|-------|--------|------|
| GET | `/health` | `health()` | ✅ |
| GET | `/` | `root()` | ✅ |
| GET | `/system/settings` | `get_system_settings()` | ✅ |
| GET | `/debug/db-schema` | `schema_audit()` | ⚠️ NEBEZPEČNÉ v prod |
| POST | `/debug/repair-schema` | – | ⚠️ NEBEZPEČNÉ v prod |
| GET | `/debug/test-ai` | `test_ai()` | ⚠️ NEBEZPEČNÉ v prod |
| GET | `/debug/test-voice` | – | ⚠️ NEBEZPEČNÉ v prod |
| GET | `/debug/schema-audit` | – | ⚠️ NEBEZPEČNÉ v prod |

---

## 2. Chybějící server funkce (helper)

```
validate_active_user(conn, tenant_id, user_id)      ❌ CHYBÍ (dle HIERARCHY.md)
validate_task_planning(task_payload)                ❌ CHYBÍ
validate_client_hierarchy(conn, client_id)          ❌ CHYBÍ
validate_job_hierarchy(conn, job_id)                ❌ CHYBÍ
set_client_next_action(conn, client_id, task_id)    ❌ CHYBÍ
set_job_next_action(conn, job_id, task_id)          ❌ CHYBÍ
complete_task_with_replacement(conn, ...)           ❌ CHYBÍ
get_hierarchy_integrity_report(conn, tenant_id)     ❌ CHYBÍ
send_push_notification(user_id, title, body)        ❌ CHYBÍ
rate_limit_check(ip, endpoint)                      ❌ CHYBÍ
```

---

## 3. Bezpečnostní problémy (přehled)

| Problém | Řádek | Závažnost |
|---------|-------|-----------|
| SHA256 bez salt pro hesla | 5101 | 🔴 KRITICKÁ |
| Hardcoded `DEFAULT_TEMP_PASSWORD = "12345"` | 34 | 🔴 KRITICKÁ |
| SQL injection přes `rate_type` f-string | 4054 | 🔴 KRITICKÁ |
| Debug endpointy v produkci | 4260, 4270, 5856 | 🔴 KRITICKÁ |
| Chybí CORS konfigurace | – | 🔴 KRITICKÁ |
| Hardcoded `tenant_id=1` na 15+ místech | 2717... | 🟠 VYSOKÁ |
| PUT endpoints bez tenant validace | 3038, 3321 | 🟠 VYSOKÁ |
| `log_activity` default `tenant_id=1` | 1604 | 🟠 VYSOKÁ |
| Chybí rate limiting na `/auth/login` | – | 🟠 VYSOKÁ |
| Chybí pagination na jobs/leads/invoices | – | 🟠 VYSOKÁ |
| Synchronní `urllib.request` v async | 2549, 2564 | 🟡 STŘEDNÍ |
| Broad `except: pass` | 235, 1386... | 🟡 STŘEDNÍ |
| Voice sessions bez TTL/cleanup | – | 🟡 STŘEDNÍ |
| HTTP 200 místo 201 pro create | všude | 🟡 STŘEDNÍ |

---

## 4. Chybějící vazby mezi entitami

```
Job → Tasks
  GET /crm/jobs/{id}/tasks    ❌ endpoint chybí
  Tasky se filtrují lokálně na klientovi

Client → Properties (detail)
  GET /crm/properties/{id}    ❌ endpoint chybí
  Nelze zobrazit detail nemovitosti

Invoice ↔ Payment
  Endpointy existují ✅
  Chybí notifikace při přijetí platby

Quote → Job (po schválení)
  POST /crm/quotes/{id}/approve vrací job_id ✅
  Chybí navigace na nový job v response flow

Voice Session → Work Report
  Flow funguje ✅
  Chybí možnost editovat summary před finálním uložením

Notification → Push
  DB notifikace existují ✅
  Chybí FCM/APNs push delivery systém

Work Report → Invoice
  Endpoint existuje ✅
  Chybí zpětná vazba invoice ID do UI
```

---

## 5. Prioritní pořadí implementace

### Sprint A – Bezpečnost (kritické)
1. Hesla → bcrypt/argon2
2. Přidat `AND tenant_id=%s` do všech UPDATE/DELETE
3. Odstranit/zabezpečit debug endpointy
4. Rate limiting na auth/login
5. CORS konfigurace

### Sprint B – Chybějící CRUD
1. Properties full CRUD (POST/PUT/DELETE/GET detail)
2. Work Reports PUT/DELETE
3. DELETE pro jobs, leads, invoices, quotes
4. Vyhledávání pro jobs, leads, invoices, tasks
5. GET /crm/jobs/{id}/tasks

### Sprint C – Admin a systém
1. GET /admin/hierarchy-integrity
2. Auth forgot-password flow
3. Pricing rules PUT/DELETE
4. Voice session cleanup/DELETE
5. Push notifications (FCM)
