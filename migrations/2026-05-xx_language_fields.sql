-- Language fields migration (customers + users)
-- Apply with psql/railway. Backup before run.
SET search_path TO crm, public;

-- Customers (clients)
ALTER TABLE crm.clients ADD COLUMN IF NOT EXISTS preferred_language_code TEXT;
ALTER TABLE crm.clients ADD COLUMN IF NOT EXISTS preferred_language_name TEXT;
ALTER TABLE crm.clients ADD COLUMN IF NOT EXISTS language_source TEXT;
ALTER TABLE crm.clients ADD COLUMN IF NOT EXISTS language_confidence NUMERIC(3,2);
ALTER TABLE crm.clients ADD COLUMN IF NOT EXISTS language_updated_at TIMESTAMPTZ;

-- Users (assistant settings)
ALTER TABLE crm.users ADD COLUMN IF NOT EXISTS assistant_output_language_code TEXT;
ALTER TABLE crm.users ADD COLUMN IF NOT EXISTS assistant_output_language_name TEXT;
ALTER TABLE crm.users ADD COLUMN IF NOT EXISTS assistant_language_locked BOOLEAN DEFAULT true;
ALTER TABLE crm.users ADD COLUMN IF NOT EXISTS assistant_tone TEXT DEFAULT 'professional';
ALTER TABLE crm.users ADD COLUMN IF NOT EXISTS assistant_style TEXT DEFAULT 'concise';

-- Seed defaults (non-destructive): only fill NULLs, keep existing data
UPDATE crm.users
SET assistant_output_language_code = COALESCE(assistant_output_language_code, 'en-GB'),
    assistant_output_language_name = COALESCE(assistant_output_language_name, 'English UK'),
    assistant_language_locked = COALESCE(assistant_language_locked, true),
    assistant_tone = COALESCE(assistant_tone, 'professional'),
    assistant_style = COALESCE(assistant_style, 'concise');

-- Rollback note: see 2026-05-xx_language_fields_rollback.sql
