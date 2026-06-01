# Google Calendar Integration — Architecture Plan (G1)

**Project:** Secretary DesignLeaf CRM
**Status:** Architecture / phasing document. NOT yet implemented.
**Principle:** Backend is the source of truth. Google Calendar is an EXTERNAL sync
service owned by the Secretary backend. Android is only a client that views and
controls calendar data THROUGH the backend. Android must never be the primary
sync layer.

## Data flow (final architecture)

    Secretary backend  <-- OAuth -->  Google Calendar API
           ^
           | REST (/api/v1/...)
           v
    Android app  (display + control only; no direct Google access)

- The backend holds the Google OAuth refresh token (per company / tenant).
- The backend performs all reads/writes to Google Calendar.
- The backend reconciles Google events with clean_calendar_events (existing A3 table).
- Android calls existing backend calendar endpoints; it never talks to Google.
- Sync works even when the phone is off, because it runs server-side.

## Three-way reconciliation (extends A5 sync logic)

Sources: (1) Secretary backend DB = TRUTH, (2) Google Calendar = external mirror,
(3) Android = thin client (already syncs via backend only).

Rules (consistent with A5 clean_calendar_sync_log conflict logic):
- Event in backend only            -> push (create) to Google.
- Event in Google only             -> import to backend, source=google_import.
- Event in both, backend newer     -> update Google.
- Event in both, Google newer      -> update backend (conflict_google_wins).
- Deletion in backend              -> delete in Google.
- Deletion in Google               -> soft-handle in backend (mark, do not silently lose).
Mapping by stable Google event id <-> backend event id (see G2 table).

## Phases

- **G1** Architecture plan (THIS DOCUMENT).
- **G2** DB tables: google_calendar_accounts (token), google_calendar_mappings
  (backend event <-> google event id), google_calendar_sync_log.
- **G3** Backend endpoints: connect (OAuth callback), status, disconnect, sync (manual trigger).
- **G4** Android Settings section: connect/disconnect button, status display,
  choose calendar, auto/manual toggle, "Sync now" button. CONTROL ONLY — no Google SDK on device.
- **G5** Manual sync: POST /calendar/google/sync runs full reconciliation on demand.
- **G6** Auto sync: server-side scheduled reconciliation (interval per tenant).

## OAuth responsibility split

- YOU (Marek) do in Google Cloud Console: create project, enable Google Calendar API,
  configure OAuth consent screen, create OAuth Client ID (Web application), set the
  authorized redirect URI to the backend callback, and grant the consent. Tokens and
  the "Allow access" click are done by you — never automated.
- I (assistant) prepare: DB tables, backend endpoints, token storage/refresh code,
  reconciliation logic, Android control UI, and exact step-by-step Console instructions.

## Required Google scope

- https://www.googleapis.com/auth/calendar  (read/write events)
  (or calendar.events if narrower scope is preferred)

## NOT in scope

- No GPT / chat. No AI. No new voice features. Google Calendar is purely a backend
  synchronization service for Secretary.
