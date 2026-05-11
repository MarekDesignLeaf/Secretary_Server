-- Rollback for language fields migration
SET search_path TO crm, public;

ALTER TABLE crm.clients DROP COLUMN IF EXISTS preferred_language_code;
ALTER TABLE crm.clients DROP COLUMN IF EXISTS preferred_language_name;
ALTER TABLE crm.clients DROP COLUMN IF EXISTS language_source;
ALTER TABLE crm.clients DROP COLUMN IF EXISTS language_confidence;
ALTER TABLE crm.clients DROP COLUMN IF EXISTS language_updated_at;

ALTER TABLE crm.users DROP COLUMN IF EXISTS assistant_output_language_code;
ALTER TABLE crm.users DROP COLUMN IF EXISTS assistant_output_language_name;
ALTER TABLE crm.users DROP COLUMN IF EXISTS assistant_language_locked;
ALTER TABLE crm.users DROP COLUMN IF EXISTS assistant_tone;
ALTER TABLE crm.users DROP COLUMN IF EXISTS assistant_style;
