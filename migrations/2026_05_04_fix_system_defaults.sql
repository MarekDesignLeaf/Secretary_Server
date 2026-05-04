-- Migration: fix_system_defaults
-- Date: 2026-05-04
-- Reason: Column defaults 'Marek' replaced with 'system' — generic, tenant-independent.
-- These defaults fire when no explicit created_by/changed_by/user_name is provided.
-- Affects: client_notes, job_notes, task_history, activity_timeline, photos

SET search_path TO crm, public;

ALTER TABLE client_notes
    ALTER COLUMN created_by SET DEFAULT 'system';

ALTER TABLE job_notes
    ALTER COLUMN created_by SET DEFAULT 'system';

ALTER TABLE task_history
    ALTER COLUMN changed_by SET DEFAULT 'system';

ALTER TABLE activity_timeline
    ALTER COLUMN user_name SET DEFAULT 'system';

ALTER TABLE photos
    ALTER COLUMN created_by SET DEFAULT 'system';

-- Rollback:
-- ALTER TABLE client_notes      ALTER COLUMN created_by SET DEFAULT 'Marek';
-- ALTER TABLE job_notes         ALTER COLUMN created_by SET DEFAULT 'Marek';
-- ALTER TABLE task_history      ALTER COLUMN changed_by SET DEFAULT 'Marek';
-- ALTER TABLE activity_timeline ALTER COLUMN user_name  SET DEFAULT 'Marek';
-- ALTER TABLE photos            ALTER COLUMN created_by SET DEFAULT 'Marek';
