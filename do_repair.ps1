cd C:\Users\hutra\AndroidStudioProjects\secretary\server
$content = [System.IO.File]::ReadAllText("$PWD\main.py")

$repair = @'

@app.post("/debug/repair-schema")
async def repair_schema():
    conn = get_db_conn()
    results = []
    try:
        with conn.cursor() as cur:
            for sql in ["ALTER TABLE clients ADD COLUMN IF NOT EXISTS tenant_id INT NOT NULL DEFAULT 1","ALTER TABLE clients ADD COLUMN IF NOT EXISTS company_registration_no TEXT","ALTER TABLE clients ADD COLUMN IF NOT EXISTS vat_no TEXT","ALTER TABLE leads ADD COLUMN IF NOT EXISTS contact_name TEXT","ALTER TABLE leads ADD COLUMN IF NOT EXISTS contact_email TEXT","ALTER TABLE leads ADD COLUMN IF NOT EXISTS contact_phone TEXT","ALTER TABLE leads ADD COLUMN IF NOT EXISTS description TEXT","ALTER TABLE leads ADD COLUMN IF NOT EXISTS notes TEXT","ALTER TABLE leads ADD COLUMN IF NOT EXISTS job_id BIGINT","ALTER TABLE leads ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT now()","ALTER TABLE leads ADD COLUMN IF NOT EXISTS tenant_id INT NOT NULL DEFAULT 1","ALTER TABLE communications ADD COLUMN IF NOT EXISTS comm_type TEXT DEFAULT 'telefon'","ALTER TABLE communications ADD COLUMN IF NOT EXISTS job_id BIGINT","ALTER TABLE communications ADD COLUMN IF NOT EXISTS notes TEXT","ALTER TABLE communications ADD COLUMN IF NOT EXISTS tenant_id INT NOT NULL DEFAULT 1","ALTER TABLE jobs ALTER COLUMN property_id DROP NOT NULL","ALTER TABLE jobs ADD COLUMN IF NOT EXISTS tenant_id INT NOT NULL DEFAULT 1","ALTER TABLE invoices ADD COLUMN IF NOT EXISTS tenant_id INT NOT NULL DEFAULT 1","ALTER TABLE properties ADD COLUMN IF NOT EXISTS tenant_id INT NOT NULL DEFAULT 1","ALTER TABLE activity_timeline ADD COLUMN IF NOT EXISTS tenant_id INT DEFAULT 1","ALTER TABLE activity_timeline ADD COLUMN IF NOT EXISTS user_id_ref TEXT","ALTER TABLE tasks ADD COLUMN IF NOT EXISTS tenant_id INT NOT NULL DEFAULT 1","ALTER TABLE photos ADD COLUMN IF NOT EXISTS tenant_id INT NOT NULL DEFAULT 1","ALTER TABLE waste_loads ADD COLUMN IF NOT EXISTS tenant_id INT NOT NULL DEFAULT 1","ALTER TABLE client_notes ADD COLUMN IF NOT EXISTS tenant_id INT NOT NULL DEFAULT 1","ALTER TABLE job_notes ADD COLUMN IF NOT EXISTS tenant_id INT NOT NULL DEFAULT 1","ALTER TABLE task_history ADD COLUMN IF NOT EXISTS tenant_id INT NOT NULL DEFAULT 1"]:
                try:
                    cur.execute(sql)
                    results.append({"sql": sql[:60], "ok": True})
                except Exception as e:
                    results.append({"sql": sql[:60], "ok": False, "err": str(e)})
                    conn.rollback()
            for sql in ["CREATE TABLE IF NOT EXISTS roles (id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, role_name TEXT NOT NULL UNIQUE, description TEXT, created_at TIMESTAMPTZ DEFAULT now())","CREATE TABLE IF NOT EXISTS users (id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, tenant_id INT DEFAULT 1, role_id BIGINT, first_name TEXT NOT NULL, last_name TEXT NOT NULL, display_name TEXT NOT NULL, email TEXT NOT NULL UNIQUE, phone TEXT, status TEXT DEFAULT 'active', password_hash TEXT DEFAULT '', created_at TIMESTAMPTZ DEFAULT now(), updated_at TIMESTAMPTZ DEFAULT now(), deleted_at TIMESTAMPTZ)","CREATE TABLE IF NOT EXISTS audit_log (id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, tenant_id INT DEFAULT 1, user_id BIGINT, action TEXT NOT NULL, entity_type TEXT NOT NULL, entity_id TEXT, old_values JSONB, new_values JSONB, created_at TIMESTAMPTZ DEFAULT now())","CREATE TABLE IF NOT EXISTS quotes (id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, tenant_id INT DEFAULT 1, quote_number TEXT UNIQUE, client_id BIGINT, status TEXT DEFAULT 'draft', total NUMERIC(12,2) DEFAULT 0, created_at TIMESTAMPTZ DEFAULT now())","CREATE TABLE IF NOT EXISTS tenants (id INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, name TEXT NOT NULL, slug TEXT NOT NULL UNIQUE, status TEXT DEFAULT 'active', created_at TIMESTAMPTZ DEFAULT now())","CREATE TABLE IF NOT EXISTS migration_log (id INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, filename TEXT NOT NULL UNIQUE, applied_at TIMESTAMPTZ DEFAULT now())"]:
                try:
                    cur.execute(sql)
                    results.append({"sql": "CREATE TABLE OK", "ok": True})
                except Exception as e:
                    results.append({"sql": "CREATE TABLE FAIL", "ok": False, "err": str(e)})
                    conn.rollback()
            try:
                cur.execute("INSERT INTO tenants (name,slug) VALUES ('DesignLeaf','designleaf') ON CONFLICT (slug) DO NOTHING")
                cur.execute("INSERT INTO roles (role_name,description) VALUES ('admin','Full access') ON CONFLICT (role_name) DO NOTHING")
                cur.execute("INSERT INTO migration_log (filename) VALUES ('001_full_repair.sql') ON CONFLICT (filename) DO NOTHING")
                results.append({"sql": "SEED data", "ok": True})
            except Exception as e:
                results.append({"sql": "SEED", "ok": False, "err": str(e)})
                conn.rollback()
            for t in ['work_report_waste','work_report_materials','work_report_entries','work_report_workers','work_reports','voice_sessions','tasks','task_history','activity_timeline','photos','client_notes','job_notes','pricing_rules']:
                try:
                    cur.execute("DROP TABLE IF EXISTS public." + t + " CASCADE")
                    results.append({"sql": "DROP public." + t, "ok": True})
                except Exception as e:
                    results.append({"sql": "DROP public." + t, "ok": False, "err": str(e)})
                    conn.rollback()
            conn.commit()
            return {"status": "REPAIR COMPLETE", "results": results}
    except Exception as e:
        conn.rollback()
        return {"status": "REPAIR FAILED: " + str(e), "results": results}
    finally: release_conn(conn)

'@

$marker = '@app.get("/health")'
$content = $content.Replace($marker, $repair + "`n" + $marker)
$content = $content.Replace('"version":"1.1a"', '"version":"1.2a"')
[System.IO.File]::WriteAllText("$PWD\main.py", $content)
git add main.py
git commit -m "v1.2a full repair endpoint"
git push
Write-Host "HOTOVO. Pockej 2 minuty." -ForegroundColor Green
