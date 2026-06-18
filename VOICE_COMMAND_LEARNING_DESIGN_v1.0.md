# Voice Command Learning System — Design v1.0

> Backend-owned, self-learning voice-command alias system for the Secretary CRM.
> **Scope of this document:** ONLY voice-command alias learning (phrase → intent
> translation). It does **not** add UI, a Contacts module, photos, Google
> Calendar, Vercel, or any learning engine for prices / business rules /
> client data. Those are explicitly out of scope.

Status: **Phases 1–4 implemented & green (backend suite 133 passed).** Phase 5
(Android) deferred as not required — the learning dialog rides the existing
pending-action round-trip the client already drives, and the alias/intents/events
endpoints await a future admin UI (§11). See §13 for per-phase status.
Author: backend. Source of truth: backend + PostgreSQL.

---

## 0. Why this exists (problem statement)

Today the assistant resolves a spoken command through three layers:

```
parse_intent()  (deterministic rules, voice_intents.py)
   ↓ miss
_LEARNED         (per-tenant in-memory cache in voice.py — lost on restart)
   ↓ miss
ai_intent.classify()  (LLM fallback, non-deterministic, costs a call)
   ↓ miss
"Nerozuměl jsem příkazu."
```

Problems:

1. **No pre-built synonym coverage.** A user who says "založ klienta" instead of
   "vytvoř klienta" falls straight through to the LLM (or fails), even though
   both obviously mean `client.create`. The system should already *know* the
   common synonyms before it ever asks the user to teach it anything.
2. **Learned aliases are not durable or shared.** `_LEARNED` is a process-memory
   dict; a deploy wipes it. Client-side aliases (SettingsManager) are per-device
   and invisible to the backend, so they can't be audited or governed.
3. **No safe learning loop.** When the system truly doesn't understand, there is
   no structured "teach me" dialog that (a) confirms the mapping, (b) supports
   cancel words, (c) records a learning event, and (d) refuses to activate a
   mapping to a non-existent command without admin approval.

This system fixes all three, **without weakening any security guarantee**.

---

## 1. Non-negotiable security invariants

These hold for every code path added by this system. They are restated as
test assertions in §12.

1. **No voice command bypasses the backend, API, permissions, tenant isolation,
   or validation.** Android may only send text; it owns no business logic.
2. **Backend + DB are the single source of truth.** The pre-built synonym
   dictionary and intent registry live in backend code; learned aliases live in
   PostgreSQL, scoped by `company_id`.
3. **An alias is a *translation*, never a *grant*.** Resolving "udělej fakturu"
   → `invoice.create` does **not** grant invoice permission. The required
   permission for the resolved intent is **always** checked at execution time,
   exactly as if the user had spoken the canonical phrase.
4. **Tenant isolation is always enforced.** Every alias / learning / pending row
   is keyed by `company_id`; lookups always filter by the caller's company. A
   `is_global` alias is readable by all tenants but writable only by the system.
5. **Dangerous actions always confirm.** delete / cancel / send / invoice.create
   / invoice.send / payment.record / client.delete / job.delete / quote.approve
   / whatsapp.send / email.send require an explicit confirmation step regardless
   of how confidently the phrase resolved (§8).
6. **Ambiguity never guesses.** If two intents tie within the ambiguity margin,
   the system asks a disambiguating question; it does not pick one silently.

---

## 2. Mandatory resolution pipeline

Every utterance flows through this ordered pipeline. Each stage can short-circuit.

```
voice input (text from Android STT)
  │
  ▼
A. normalize            lowercase, strip diacritics, strip punctuation,
                        collapse spaces, map common STT errors
  │
  ▼
B. builtin synonym      exact phrase match against the pre-built synonym
   match                dictionary (§4) → intent + confidence 0.95
  │ miss
  ▼
C. tenant/user alias    exact normalized-phrase match against
   match                clean_voice_command_aliases (status=ACTIVE),
                        user-scoped first, then company-global, then is_global
                        → intent + alias.confidence (default 1.0)
  │ miss
  ▼
D. deterministic parse  existing voice_intents.parse_intent() + rule scoring
   + scoring            → intent + parsed.confidence
  │ miss / low
  ▼
D'. AI fallback         ai_intent.classify() (unchanged) → intent
  │
  ▼
E. entity extraction    person / date / time / title slots (existing helpers)
  │
  ▼
F. ambiguity check      if top-2 candidate intents within margin → CLARIFY
  │
  ▼
G/H/I. confidence gate
   HIGH  (≥0.85)  → execute (or confirm if dangerous §8)
   MED   (0.55–0.84) → clarify ("Myslíš X, nebo Y?")
   LOW   (<0.55)  → learning dialog (§5): "Tomuto příkazu zatím nerozumím…"
  │
  ▼
permission check (at execution) → action → confirmation → learning event log
```

**Confidence bands** (constants in `voice_intent_registry.py`):

| Band   | Range        | Action                                  |
|--------|--------------|-----------------------------------------|
| HIGH   | `>= 0.85`    | execute (confirm first if dangerous)    |
| MEDIUM | `0.55–0.84`  | clarify / confirm the single best guess |
| LOW    | `< 0.55`     | enter learning dialog                   |

Builtin-synonym and active-alias matches are intentionally scored at/above the
HIGH threshold (0.95 / alias confidence) so a known phrase executes directly.

### Relationship to the existing pipeline

This system is a **thin resolver in front of the existing executor**, not a
rewrite. Stages B and C are *new* and run *before* the current
`parse_intent → _LEARNED → ai_intent` chain (stages D/D'). The execution
branches, slot-filling, localization, and pending-action machinery in
`voice.py` are reused unchanged. `_LEARNED` (in-memory) is superseded by the
durable alias table but kept as a harmless fallback during migration.

---

## 3. Central intent registry — `secretary_clean/core/voice_intent_registry.py`

Code-first (not a regex in Android). Each intent is one `VoiceIntent` record:

```python
@dataclass(frozen=True)
class VoiceIntent:
    intent_code: str            # "client.create"
    module: str                 # "crm"
    description: str
    required_permission: str    # Permission enum name, checked at execution
    is_active: bool
    is_implemented: bool        # True if voice.py has an execution branch
    requires_confirmation: bool # dangerous → always confirm (§8)
    supported_languages: list[str]   # ["cs", "en", "pl"]
    canonical_phrases: list[str]
    synonyms: list[str]              # extra whole-phrase synonyms
    required_entities: list[str]
    optional_entities: list[str]
    example_commands: list[str]
    fallback_message: str            # shown when entities can't be filled
```

- The registry is the **authoritative list of intents** the system understands.
- `is_implemented` is derived from / kept in sync with `voice.py` execution
  branches (today this lives in `alias_learning.SUPPORTED_INTENTS`; the registry
  becomes the new home and `SUPPORTED_INTENTS` is computed from it).
- Exposed read-only for audit via `GET /api/v1/voice/intents` (§6). No write API.

The registry seeds every implemented intent listed in §9.

---

## 4. Synonym dictionary — `secretary_clean/core/voice_synonyms.py`

Two composable layers:

1. **Action synonyms** (verb → canonical action):
   `create` ← vytvoř, založ, přidej, nový/nová/nové, udělej, zapiš, naplánuj,
   create, add, make, new, set up, schedule …
   `update` ← uprav, změň, přepiš, posuň, update, change, edit, move, reschedule …
   `delete` ← smaž, zruš, odstraň, vymaž, delete, remove, cancel …
   `list`   ← ukaž, vypiš, zobraz, co mám, list, show, what's …
2. **Object synonyms** (noun → canonical object):
   `client` ← klient/klienta/klientku, zákazník, kontakt, client, customer …
   `task`   ← úkol, úkoly, task, todo …
   `job`    ← zakázka/zakázku, job, order, gig …
   `calendar`/`event` ← schůzka, událost, kalendář, meeting, appointment …
   `invoice`← faktura/fakturu, účet, invoice …
   `quote`  ← nabídka, cenová nabídka, quote, estimate …
   `work_report` ← výkaz, pracovní výkaz, work report, timesheet …

**Composition:** `action_synonym + object_synonym → intent_code`
(e.g. `create` + `client` → `client.create`). This generates a large synonym
surface from a small hand-maintained table, on top of the explicit whole-phrase
`canonical_phrases`/`synonyms` in the registry (§3, §9).

**Normalization** (`normalize(text)`) handles, in order:
case-folding · diacritics stripping (NFKD) · punctuation removal · whitespace
collapse · common STT-error substitutions · Czech word-form tolerance (matching
on stems for the action/object tables) · English variants. Reuses the existing
`alias_learning.normalize` / `voice.py::_strip_diacritics` semantics so behaviour
is identical to the rest of the codebase.

---

## 5. Database tables (additive migration — `_EXTRA_DDL` / `_ALTER_DDL`)

All four tables are created with `CREATE TABLE IF NOT EXISTS`; **no existing
table or column is dropped or altered destructively**. They are also modelled in
the in-memory repository for tests.

### 5.1 `clean_voice_command_aliases`
```
id              TEXT PK
company_id      TEXT NOT NULL          -- tenant isolation
user_id         TEXT                   -- NULL = company-wide alias
raw_phrase      TEXT NOT NULL
normalized_phrase TEXT NOT NULL
target_intent   TEXT NOT NULL
language_code   TEXT
status          TEXT NOT NULL DEFAULT 'ACTIVE'   -- ACTIVE/PENDING/DISABLED/REJECTED
confidence      DOUBLE PRECISION NOT NULL DEFAULT 1.0
source          TEXT NOT NULL DEFAULT 'user_learning'
created_by      TEXT
created_at      TIMESTAMPTZ NOT NULL
updated_at      TIMESTAMPTZ NOT NULL
last_used_at    TIMESTAMPTZ
use_count       INTEGER NOT NULL DEFAULT 0
is_global       BOOLEAN NOT NULL DEFAULT FALSE
UNIQUE(company_id, normalized_phrase, user_id)
```

### 5.2 `clean_voice_learning_events`
```
id               TEXT PK
company_id       TEXT NOT NULL
user_id          TEXT
raw_input        TEXT NOT NULL
normalized_input TEXT
resolved_intent  TEXT
resolution_type  TEXT NOT NULL   -- BUILTIN_SYNONYM / USER_ALIAS / PENDING_ALIAS /
                                 -- UNKNOWN / CANCELLED / AMBIGUOUS /
                                 -- FAILED_PERMISSION / FAILED_VALIDATION
confidence       DOUBLE PRECISION
was_executed     BOOLEAN NOT NULL DEFAULT FALSE
was_confirmed    BOOLEAN NOT NULL DEFAULT FALSE
created_alias_id TEXT
created_at       TIMESTAMPTZ NOT NULL
metadata         JSONB
```

### 5.3 `clean_voice_pending_learnings`
```
id                      TEXT PK
company_id              TEXT NOT NULL
user_id                 TEXT
unknown_phrase          TEXT NOT NULL
normalized_unknown_phrase TEXT
state                   TEXT NOT NULL   -- WAITING_FOR_TARGET / WAITING_FOR_CONFIRMATION /
                                        -- CANCELLED / COMPLETED / EXPIRED
attempt_count           INTEGER NOT NULL DEFAULT 0
created_at              TIMESTAMPTZ NOT NULL
expires_at              TIMESTAMPTZ
metadata                JSONB
```

### 5.4 `clean_voice_intent_registry` (optional / code-first)
The registry is authoritative in code (§3). This table is an **optional cache**
for audit/export; the canonical export is the `GET /voice/intents` endpoint.
Created `IF NOT EXISTS` but never required for runtime resolution.

---

## 6. API endpoints (all under `/api/v1/voice`, all permission-gated)

| Method | Path                         | Permission        | Purpose |
|--------|------------------------------|-------------------|---------|
| GET    | `/voice/intents`             | voice_execute     | Export intent registry (audit) |
| GET    | `/voice/aliases`             | voice_execute     | List tenant aliases (active/pending/disabled) |
| POST   | `/voice/aliases`             | crm_manage        | Create/teach an alias (maps phrase→intent) |
| PUT    | `/voice/aliases/{id}`        | crm_manage        | Remap / re-enable an alias |
| DELETE | `/voice/aliases/{id}`        | crm_manage        | **Soft** disable (status→DISABLED) |
| POST   | `/voice/learning/resolve`    | voice_execute     | Run pipeline §2, return resolution (read-only preview) |
| GET    | `/voice/learning/events`     | crm_manage        | List learning events (audit) |

`POST /voice/execute` (existing) is **extended** to run stages B (builtin
synonym) and C (alias DB) *before* the current parse/learned/AI chain, and to
write a learning event on every resolution.

---

## 7. Security at execution (restated)

- An alias may point at any intent, but resolving it does **not** imply the
  caller may run it. After resolution, `voice/execute` calls the same
  `require_permission` path the canonical phrase would. If the user lacks the
  intent's `required_permission`, the action is refused and a
  `FAILED_PERMISSION` learning event is written; nothing executes.
- Teaching an alias (`POST /voice/aliases`) requires `crm_manage`. The taught
  target is validated against the registry; an alias to an unknown intent is
  **not** auto-activated (§4 of spec → admin approval queue, status governs).

---

## 8. Dangerous actions (always confirm)

`requires_confirmation = True` in the registry for:
`*.delete`, `*.cancel`, `*.send`, `invoice.create`, `invoice.send`,
`payment.record`, `client.delete`, `job.delete`, `quote.approve`,
`whatsapp.send`, `email.send`.

For these, even a HIGH-confidence resolution returns a confirmation step
(`requires_confirmation=True`) before executing. Confirmation reuses the
existing pending-action / `needs_confirm` machinery in `voice.py`.

---

## 9. Pre-built synonym coverage (seed set)

Every **implemented** intent ships with **≥10 Czech + ≥10 English** phrases
(canonical + synonyms), plus STT variants, short commands, and natural
sentences. Implemented intents (have a `voice.py` execution branch today):

```
calendar.list  calendar.create  calendar.update  calendar.delete
client.create  client.find      client.set_address
task.create    task.list        task.complete
job.create     job.list         job.change_status
work_report.start
invoice.from_work_report
quote.create
whatsapp.send  whatsapp.read
comm.log  comm.list  weather.get  contacts.import
```

Plus control intents: `unknown.cancel` (omyl / zruš / neplatný příkaz / to nic /
zapomeň …), `alias.create`, `alias.delete`.

Planned-but-not-implemented intents (registry `is_implemented=False`) keep their
synonyms too, so a taught alias to them parks as PENDING and auto-activates when
the module ships (§10).

---

## 10. Activation of pending aliases (server start / deploy)

On app startup, a `activate_pending_aliases()` pass scans
`clean_voice_command_aliases WHERE status='PENDING'`. For each, if its
`target_intent` is now `is_implemented` in the registry, flip status→ACTIVE and
write a `learning_event(resolution_type=PENDING_ALIAS, was_executed=False)`
noting the activation. Idempotent; safe to run every boot.

---

## 11. Admin surface (backend-only for now)

No UI in this version. The endpoints in §6 are shaped so a future Settings
screen can: list active/pending/disabled aliases, list learning events, soft
delete, and remap — all already covered by the alias + events endpoints.

---

## 12. Test matrix (per phase, in `server/tests/`)

1. builtin synonym resolves ("založ klienta" → client.create, no AI call)
2. active user alias resolves
3. pending alias does **not** resolve to execution (parks)
4. unknown phrase → learning dialog prompt
5. "omyl" cancels the learning dialog (no alias saved)
6. "neplatný příkaz" cancels
7. ambiguous utterance → clarification question, no execution
8. permission denied → no execution, FAILED_PERMISSION event
9. dangerous intent → requires confirmation before executing
10. alias does **not** bypass permission (alias→dangerous still confirms + checks perm)
11. pending alias activates after target becomes implemented
12. learning event written for each resolution_type

---

## 13. Implementation phases (commit + report after each)

- **Phase 1 — Resolver core (code-only, no DB). ✅ DONE (commit a473feb).**
  `voice_intent_registry.py`, `voice_synonyms.py`, `voice_resolver.py`
  (normalize → builtin synonym → parse → score → confidence band → ambiguity).
  Tests: synonym match, ambiguity, confidence bands. No schema change.
- **Phase 2 — Durable aliases + learning events. ✅ DONE (commit ab52621).**
  4 tables (additive migration) + in-memory repo models; alias lookup wired into
  the resolver (stage C); `voice/execute` writes a learning event each call.
  Endpoints: GET `/voice/intents`, GET/POST/PUT/DELETE `/voice/aliases`,
  GET `/voice/learning/events`, POST `/voice/learning/resolve`. Tests: alias
  match, soft delete, permission-at-execution, event written.
- **Phase 3 — Learning dialog + pending learnings. ✅ DONE (commit fc17868).**
  Unknown → "Tomuto příkazu zatím nerozumím…" → map to known command → ACTIVE /
  PENDING; cancel words; max-two re-asks; `pending_learnings` state machine.
  Tests: unknown→learn, omyl/neplatný cancel, two-ask limit.
- **Phase 4 — Pending activation + admin endpoints polish. ✅ DONE (commit d354708).**
  `_activate_pending_aliases()` on startup + POST /voice/learning/activate-pending.
  Tests: pending→active after implement, idempotent, tenant-scoped endpoint.
- **Phase 5 — Android wiring. ⏸ DEFERRED (not required).**
  The learning dialog already works through the existing pending-action
  round-trip the client drives for slot-filling, so no client change is needed
  for the core capability. Moving the client's local alias store to the backend
  endpoints, and a Settings admin screen for aliases/events, are future
  enhancements — to be done only when an admin UI is built. Android stays a thin
  client (text in, spoken result out); no business logic moves to it.

After each phase: run the full backend test suite, commit on
`clean-first-install-api`, list changed files, what changed / didn't, the
rollback procedure (revert the phase commit; tables are additive so no data
migration to undo), and update `SECRETARY_MASTER_STATE.md`.

---

## 14. Rollback

Every phase is a single commit on `clean-first-install-api`. To roll back a
phase: `git revert <commit>`. Tables are additive (`CREATE TABLE IF NOT EXISTS`)
and unreferenced by older code paths, so reverting code leaves harmless empty
tables — no destructive down-migration is needed. The resolver degrades to the
pre-existing `parse_intent → _LEARNED → ai_intent` chain if the registry /
synonym modules are absent.
```
