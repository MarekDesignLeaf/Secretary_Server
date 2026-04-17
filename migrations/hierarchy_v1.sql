-- ============================================================
-- Secretary – Povinná hierarchie předávání práce
-- Migration v1  (fáze 1 – nullable, bez NOT NULL constraints)
-- Spustit v pořadí: 1-schema → 2-fk → 3-triggers → 4-backfill
-- ============================================================

-- ============================================================
-- 1. SCHEMA CHANGES
-- ============================================================

-- 1a. clients – přidat owner a next_action pointer
ALTER TABLE clients
    ADD COLUMN IF NOT EXISTS owner_user_id       BIGINT,
    ADD COLUMN IF NOT EXISTS next_action_task_id TEXT,
    ADD COLUMN IF NOT EXISTS hierarchy_status    TEXT NOT NULL DEFAULT 'unchecked';

COMMENT ON COLUMN clients.owner_user_id       IS 'Hlavní odpovědná osoba (aktivní uživatel)';
COMMENT ON COLUMN clients.next_action_task_id IS 'Aktuální otevřený navazující krok (task.id)';
COMMENT ON COLUMN clients.hierarchy_status    IS 'valid | orphan | unchecked';

-- 1b. jobs – doplnit next_action pointer, hierarchy_status
ALTER TABLE jobs
    ADD COLUMN IF NOT EXISTS next_action_task_id TEXT,
    ADD COLUMN IF NOT EXISTS hierarchy_status    TEXT NOT NULL DEFAULT 'unchecked';

COMMENT ON COLUMN jobs.next_action_task_id IS 'Aktuální otevřený navazující krok (task.id)';
COMMENT ON COLUMN jobs.hierarchy_status    IS 'valid | orphan | unchecked';

-- 1c. tasks – ensure assigned_user_id + add source column
ALTER TABLE tasks
    ADD COLUMN IF NOT EXISTS task_source TEXT DEFAULT 'manual';

COMMENT ON COLUMN tasks.task_source IS 'manual | voice | system_migration | ai';

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_clients_owner_user_id       ON clients(owner_user_id);
CREATE INDEX IF NOT EXISTS idx_clients_next_action         ON clients(next_action_task_id);
CREATE INDEX IF NOT EXISTS idx_clients_hierarchy_status    ON clients(hierarchy_status);
CREATE INDEX IF NOT EXISTS idx_jobs_next_action            ON jobs(next_action_task_id);
CREATE INDEX IF NOT EXISTS idx_jobs_hierarchy_status       ON jobs(hierarchy_status);


-- ============================================================
-- 2. FOREIGN KEYS (přidat po backfill fáze 2)
-- ============================================================
-- POZOR: Spustit až po backfill skriptu!

-- ALTER TABLE clients
--     ADD CONSTRAINT fk_clients_owner_user
--         FOREIGN KEY (owner_user_id) REFERENCES users(id) ON DELETE RESTRICT;

-- ALTER TABLE jobs
--     ADD CONSTRAINT fk_jobs_assigned_user
--         FOREIGN KEY (assigned_user_id) REFERENCES users(id) ON DELETE RESTRICT;


-- ============================================================
-- 3. TRIGGER – validate_task_hierarchy
--    Blokuje save tasku bez assignee nebo bez času
-- ============================================================
CREATE OR REPLACE FUNCTION trg_validate_task_hierarchy()
RETURNS TRIGGER AS $$
BEGIN
    -- Assignee musí být vyplněn
    IF NEW.assigned_user_id IS NULL THEN
        RAISE EXCEPTION 'Task musí mít přiřazeného uživatele (assigned_user_id)'
            USING ERRCODE = 'P0001';
    END IF;

    -- Assignee musí být aktivní (jen pokud tabulka users existuje a má status)
    IF NOT EXISTS (
        SELECT 1 FROM users
        WHERE id = NEW.assigned_user_id
          AND status = 'active'
          AND deleted_at IS NULL
    ) THEN
        RAISE EXCEPTION 'Task nelze přiřadit neaktivnímu nebo neexistujícímu uživateli (id=%)', NEW.assigned_user_id
            USING ERRCODE = 'P0002';
    END IF;

    -- Musí být aspoň plánovaný čas nebo deadline
    IF NEW.planned_start_at IS NULL AND (NEW.deadline IS NULL OR NEW.deadline = '') THEN
        RAISE EXCEPTION 'Task musí mít vyplněný planned_start_at nebo deadline'
            USING ERRCODE = 'P0003';
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Trigger aktivovat až ve Fázi 5 (po backfill + čistém auditu)
-- CREATE TRIGGER trig_task_hierarchy
--     BEFORE INSERT OR UPDATE ON tasks
--     FOR EACH ROW EXECUTE FUNCTION trg_validate_task_hierarchy();


-- ============================================================
-- 4. TRIGGER – validate_client_hierarchy
--    Blokuje save klienta bez ownera nebo bez validního next action
-- ============================================================
CREATE OR REPLACE FUNCTION trg_validate_client_hierarchy()
RETURNS TRIGGER AS $$
DECLARE
    v_task RECORD;
BEGIN
    -- Owner musí být vyplněn
    IF NEW.owner_user_id IS NULL THEN
        RAISE EXCEPTION 'Klient musí mít owner_user_id'
            USING ERRCODE = 'P0010';
    END IF;

    -- Owner musí být aktivní
    IF NOT EXISTS (
        SELECT 1 FROM users
        WHERE id = NEW.owner_user_id
          AND status = 'active'
          AND deleted_at IS NULL
    ) THEN
        RAISE EXCEPTION 'Klient nelze uložit s neaktivním ownerem (id=%)', NEW.owner_user_id
            USING ERRCODE = 'P0011';
    END IF;

    -- next_action musí existovat a být validní
    IF NEW.next_action_task_id IS NOT NULL THEN
        SELECT * INTO v_task FROM tasks WHERE id::TEXT = NEW.next_action_task_id LIMIT 1;

        IF v_task IS NULL THEN
            RAISE EXCEPTION 'next_action_task_id % neexistuje', NEW.next_action_task_id
                USING ERRCODE = 'P0012';
        END IF;

        IF v_task.is_completed = TRUE OR v_task.status IN ('hotovo','zruseno') THEN
            RAISE EXCEPTION 'next_action_task_id % je dokončen nebo zrušen – nelze použít jako další krok', NEW.next_action_task_id
                USING ERRCODE = 'P0013';
        END IF;

        IF v_task.assigned_user_id IS NULL THEN
            RAISE EXCEPTION 'next_action task (id=%) nemá přiřazeného uživatele', NEW.next_action_task_id
                USING ERRCODE = 'P0014';
        END IF;
    END IF;

    -- Aktualizovat hierarchy_status
    IF NEW.owner_user_id IS NOT NULL AND NEW.next_action_task_id IS NOT NULL THEN
        NEW.hierarchy_status := 'valid';
    ELSE
        NEW.hierarchy_status := 'orphan';
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Trigger aktivovat až ve Fázi 5
-- CREATE TRIGGER trig_client_hierarchy
--     BEFORE INSERT OR UPDATE ON clients
--     FOR EACH ROW EXECUTE FUNCTION trg_validate_client_hierarchy();


-- ============================================================
-- 5. TRIGGER – validate_job_hierarchy
--    Blokuje save zakázky bez ownera nebo bez validního next action
-- ============================================================
CREATE OR REPLACE FUNCTION trg_validate_job_hierarchy()
RETURNS TRIGGER AS $$
DECLARE
    v_task RECORD;
BEGIN
    -- Owner/assignee musí být vyplněn
    IF NEW.assigned_user_id IS NULL THEN
        RAISE EXCEPTION 'Zakázka musí mít assigned_user_id'
            USING ERRCODE = 'P0020';
    END IF;

    -- Assignee musí být aktivní
    IF NOT EXISTS (
        SELECT 1 FROM users
        WHERE id = NEW.assigned_user_id
          AND status = 'active'
          AND deleted_at IS NULL
    ) THEN
        RAISE EXCEPTION 'Zakázka nelze uložit s neaktivním ownerem (id=%)', NEW.assigned_user_id
            USING ERRCODE = 'P0021';
    END IF;

    -- next_action musí být validní
    IF NEW.next_action_task_id IS NOT NULL THEN
        SELECT * INTO v_task FROM tasks WHERE id::TEXT = NEW.next_action_task_id LIMIT 1;

        IF v_task IS NULL THEN
            RAISE EXCEPTION 'next_action_task_id % neexistuje', NEW.next_action_task_id
                USING ERRCODE = 'P0022';
        END IF;

        IF v_task.is_completed = TRUE OR v_task.status IN ('hotovo','zruseno') THEN
            RAISE EXCEPTION 'next_action task (id=%) je dokončen nebo zrušen', NEW.next_action_task_id
                USING ERRCODE = 'P0023';
        END IF;
    END IF;

    -- Aktualizovat hierarchy_status
    IF NEW.assigned_user_id IS NOT NULL AND NEW.next_action_task_id IS NOT NULL THEN
        NEW.hierarchy_status := 'valid';
    ELSE
        NEW.hierarchy_status := 'orphan';
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- CREATE TRIGGER trig_job_hierarchy
--     BEFORE INSERT OR UPDATE ON jobs
--     FOR EACH ROW EXECUTE FUNCTION trg_validate_job_hierarchy();


-- ============================================================
-- 6. TRIGGER – prevent_complete_next_action_without_replacement
--    Blokuje dokončení tasku vedeného jako next_action
-- ============================================================
CREATE OR REPLACE FUNCTION trg_prevent_orphan_on_task_complete()
RETURNS TRIGGER AS $$
BEGIN
    -- Jen pokud task přechází do dokončeného stavu
    IF (NEW.is_completed = TRUE OR NEW.status IN ('hotovo', 'zruseno'))
       AND (OLD.is_completed IS DISTINCT FROM TRUE)
    THEN
        -- Zkontrolovat, zda je tento task veden jako next_action klienta
        IF EXISTS (
            SELECT 1 FROM clients
            WHERE next_action_task_id = OLD.id::TEXT
              AND deleted_at IS NULL
        ) THEN
            RAISE EXCEPTION
                'Task % je aktuální next_action klienta. Nelze dokončit bez nastavení náhradního next_action_task_id.',
                OLD.id
                USING ERRCODE = 'P0030';
        END IF;

        -- Zkontrolovat, zda je tento task veden jako next_action zakázky
        IF EXISTS (
            SELECT 1 FROM jobs
            WHERE next_action_task_id = OLD.id::TEXT
        ) THEN
            RAISE EXCEPTION
                'Task % je aktuální next_action zakázky. Nelze dokončit bez nastavení náhradního next_action_task_id.',
                OLD.id
                USING ERRCODE = 'P0031';
        END IF;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- CREATE TRIGGER trig_prevent_orphan_on_complete
--     BEFORE UPDATE ON tasks
--     FOR EACH ROW EXECUTE FUNCTION trg_prevent_orphan_on_task_complete();


-- ============================================================
-- 7. AUDIT QUERY – orphan report (použít pro Fázi 2 audit)
-- ============================================================

-- Klienti bez ownera
-- SELECT id, display_name, 'missing_owner' AS issue
-- FROM clients WHERE owner_user_id IS NULL AND deleted_at IS NULL;

-- Klienti bez next action
-- SELECT id, display_name, 'missing_next_action' AS issue
-- FROM clients WHERE next_action_task_id IS NULL AND deleted_at IS NULL;

-- Zakázky bez ownera
-- SELECT id, title, 'missing_owner' AS issue
-- FROM jobs WHERE assigned_user_id IS NULL;

-- Zakázky bez next action
-- SELECT id, title, 'missing_next_action' AS issue
-- FROM jobs WHERE next_action_task_id IS NULL AND status NOT IN ('dokonceno','vyfakturovano','uzavreno','zruseno');

-- Tasky bez assignee
-- SELECT id, title, 'missing_assignee' AS issue
-- FROM tasks WHERE assigned_user_id IS NULL AND is_completed = FALSE;

-- Tasky bez plánování
-- SELECT id, title, 'missing_schedule' AS issue
-- FROM tasks WHERE assigned_user_id IS NOT NULL
--   AND planned_start_at IS NULL AND (deadline IS NULL OR deadline = '')
--   AND is_completed = FALSE;

-- Tasky přiřazené neaktivnímu uživateli
-- SELECT t.id, t.title, t.assigned_user_id, u.status
-- FROM tasks t
-- JOIN users u ON u.id = t.assigned_user_id
-- WHERE (u.status != 'active' OR u.deleted_at IS NOT NULL)
--   AND t.is_completed = FALSE;


-- ============================================================
-- 8. BACKFILL SCRIPT (fáze 3 – spustit po auditu)
-- ============================================================
-- Tento skript DOPORUČEN ke spuštění v transakci s ROLLBACK testem.

BEGIN;

-- Najít default managera pro backfill (první admin nebo manager v systému)
-- DO $$
-- DECLARE
--     v_default_user_id BIGINT;
--     v_placeholder_task_id TEXT;
--     v_tenant_id BIGINT;
-- BEGIN
--     SELECT id INTO v_default_user_id FROM users
--     WHERE status = 'active' AND deleted_at IS NULL
--       AND role IN ('admin','manager')
--     ORDER BY id LIMIT 1;
--
--     IF v_default_user_id IS NULL THEN
--         RAISE EXCEPTION 'Nenalezen žádný aktivní admin/manager pro backfill';
--     END IF;
--
--     -- Backfill clients bez ownera
--     UPDATE clients SET owner_user_id = v_default_user_id
--     WHERE owner_user_id IS NULL AND deleted_at IS NULL;
--
--     -- Backfill tasks bez assignee
--     UPDATE tasks SET assigned_user_id = v_default_user_id
--     WHERE assigned_user_id IS NULL AND is_completed = FALSE;
--
--     -- Backfill tasks bez deadlinu (nastavit 3 pracovní dny)
--     UPDATE tasks SET deadline = (CURRENT_DATE + INTERVAL '3 days')::TEXT
--     WHERE assigned_user_id IS NOT NULL
--       AND planned_start_at IS NULL
--       AND (deadline IS NULL OR deadline = '')
--       AND is_completed = FALSE;
--
--     -- Vytvořit placeholder tasky pro klienty bez next_action
--     FOR client_rec IN
--         SELECT c.id, c.tenant_id, c.owner_user_id
--         FROM clients c
--         WHERE c.next_action_task_id IS NULL
--           AND c.deleted_at IS NULL
--     LOOP
--         INSERT INTO tasks (
--             tenant_id, title, task_type, status, priority,
--             assigned_user_id, client_id, deadline,
--             planning_notes, task_source
--         ) VALUES (
--             client_rec.tenant_id,
--             'Doplnit další krok',
--             'obecny', 'novy', 'vysoka',
--             client_rec.owner_user_id, client_rec.id,
--             (CURRENT_DATE + INTERVAL '1 day')::TEXT,
--             'Systémově vytvořený task při migraci hierarchie. Nutná ruční kontrola.',
--             'system_migration'
--         ) RETURNING id::TEXT INTO v_placeholder_task_id;
--
--         UPDATE clients SET next_action_task_id = v_placeholder_task_id
--         WHERE id = client_rec.id;
--     END LOOP;
--
--     -- Vytvořit placeholder tasky pro zakázky bez next_action (aktivní)
--     FOR job_rec IN
--         SELECT j.id, j.tenant_id, j.assigned_user_id, j.client_id
--         FROM jobs j
--         WHERE j.next_action_task_id IS NULL
--           AND j.status NOT IN ('dokonceno','vyfakturovano','uzavreno','zruseno')
--     LOOP
--         INSERT INTO tasks (
--             tenant_id, title, task_type, status, priority,
--             assigned_user_id, job_id, client_id, deadline,
--             planning_notes, task_source
--         ) VALUES (
--             job_rec.tenant_id,
--             'Doplnit další krok',
--             'obecny', 'novy', 'vysoka',
--             job_rec.assigned_user_id, job_rec.id, job_rec.client_id,
--             (CURRENT_DATE + INTERVAL '1 day')::TEXT,
--             'Systémově vytvořený task při migraci hierarchie. Nutná ruční kontrola.',
--             'system_migration'
--         ) RETURNING id::TEXT INTO v_placeholder_task_id;
--
--         UPDATE jobs SET next_action_task_id = v_placeholder_task_id
--         WHERE id = job_rec.id;
--     END LOOP;
-- END $$;

ROLLBACK; -- Změnit na COMMIT po ověření
