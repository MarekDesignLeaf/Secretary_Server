# Clean Secretary Backend Foundation

## Direction

This package stops patching the legacy Secretary app. It does not preserve old endpoint names, old Android flows, old settings panels, debug panels, onboarding flow, assistant memory flow, hierarchy integrity flow, or 404 compatibility behavior.

The clean backend uses the current repository only as source material:

- `secretary_work_types_tree_pricing_logic.txt` is the primary catalogue source of truth.
- Authentication keeps the useful idea of JWT access/refresh tokens and PBKDF2 password hashing, but is implemented in a clean module instead of importing legacy globals.
- Catalogue data is backend-owned; frontend clients should only display backend options and submit choices.

## Backend modules created

The `secretary_clean` package defines these backend-first modules:

1. Bootstrap: status, first company creation, first admin creation.
2. Auth: login, refresh, current user, roles.
3. Company: company profile, operating settings, workspace mode.
4. Users: users, roles, permissions and preferred language.
5. Language system: tenant language settings, active tenant languages, client language and context resolution.
6. Catalogue: industries, subtypes, work activities, pricing methods, additional charges.
7. Tenant pricing: selected activities, pricing overrides, reset to system default.
8. CRM core: clients, jobs, tasks, quotes, invoices, communications, work reports.
9. Voice foundation: command resolver and execution guard with no fake action execution, respecting language context and backend permissions.

## Clean API contract foundation

All clean endpoints are versioned under `/api/v1`. This is intentionally not a compatibility wrapper for the old app or Android clients.

Clean language endpoints are part of the core contract:

- `GET /language/settings`
- `PUT /language/settings`
- `GET /language/available`
- `GET /language/tenant`
- `PUT /language/tenant`
- `GET /language/client/{client_id}`
- `PUT /language/client/{client_id}`


## Database model foundation

`secretary_clean/db/schema.sql` is a schema draft only. It was not run as a migration. It separates immutable system catalogue defaults from tenant overrides:

- `clean_work_activities.default_pricing_method_code` stores the system recommendation.
- `clean_work_activity_pricing_methods` stores every available pricing method for every activity and enforces exactly one default with a partial unique index.
- `clean_tenant_activity_pricing` stores company-specific selection, rate, custom name and additional charge choices without deleting system defaults.
- `tenant_operating_profile` stores `internal_language_mode`, `customer_language_mode`, `default_internal_language_code`, `default_customer_language_code`, `voice_input_strategy`, `voice_output_strategy`, `auto_translate_customer_to_internal` and `auto_translate_internal_to_customer`.
- `tenant_languages` stores enabled languages by `language_scope`: `internal`, `customer`, `voice_input` and `voice_output`.
- `clean_users.preferred_language_code` and `clean_clients.preferred_language_code` support user and client language preferences.

## Catalogue invariants

The parser validates that:

- every industry from the source file is loaded,
- every subtype from the source file is loaded,
- every concrete activity from the source file is loaded,
- no subtype is empty,
- every activity exposes all pricing methods,
- every activity has exactly one default pricing method.

## Deliberately ignored legacy logic

- Legacy endpoint structure and compatibility wrappers.
- Old Android request/response assumptions.
- Old settings, debug, onboarding, assistant memory and hierarchy integrity flows.
- Old 404 patching behavior.
- Frontend-owned business logic.
- Fake voice execution.
- Railway migration/deployment workflow.
