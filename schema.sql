BEGIN;

CREATE SCHEMA IF NOT EXISTS crm;
SET search_path TO crm, public;

-- 1. AUDITNÍ LOGIKA (Musí být vytvořena jako první)
CREATE TABLE IF NOT EXISTS audit_log (
    id                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    entity_type         text NOT NULL,
    entity_id           bigint NOT NULL,
    action              text NOT NULL,
    old_values_json     jsonb,
    new_values_json     jsonb,
    changed_by_user_id  bigint,
    changed_at          timestamptz NOT NULL DEFAULT now(),
    ip_address          inet,
    user_agent          text,
    CONSTRAINT chk_audit_log_action CHECK (
        action IN ('insert','update','delete','approve','send','convert','archive','restore','login')
    )
);

CREATE OR REPLACE FUNCTION crm.proc_audit_log() RETURNS TRIGGER AS $$
DECLARE
    old_data jsonb := NULL;
    new_data jsonb := NULL;
BEGIN
    IF (TG_OP = 'UPDATE') THEN
        old_data := to_jsonb(OLD);
        new_data := to_jsonb(NEW);
    ELSIF (TG_OP = 'INSERT') THEN
        new_data := to_jsonb(NEW);
    ELSIF (TG_OP = 'DELETE') THEN
        old_data := to_jsonb(OLD);
    END IF;

    INSERT INTO crm.audit_log (entity_type, entity_id, action, old_values_json, new_values_json, changed_at)
    VALUES (TG_TABLE_NAME, COALESCE(NEW.id, OLD.id), lower(TG_OP), old_data, new_data, now());

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- 2. ZÁKLADNÍ STRUKTURA (Role, Uživatelé)
CREATE TABLE roles (
    id                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    role_code           text NOT NULL,
    name                text NOT NULL,
    description         text,
    is_system_role      boolean NOT NULL DEFAULT false,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_roles_role_code UNIQUE (role_code),
    CONSTRAINT chk_roles_role_code CHECK (role_code <> '')
);

CREATE TABLE permissions (
    id                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    permission_code     text NOT NULL,
    module_name         text NOT NULL,
    name                text NOT NULL,
    description         text,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_permissions_code UNIQUE (permission_code),
    CONSTRAINT chk_permissions_code CHECK (permission_code <> ''),
    CONSTRAINT chk_permissions_module_name CHECK (module_name <> '')
);

CREATE TABLE users (
    id                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    employee_code       text,
    role_id             bigint NOT NULL,
    first_name          text NOT NULL,
    last_name           text NOT NULL,
    display_name        text NOT NULL,
    email               text NOT NULL,
    phone               text,
    status              text NOT NULL DEFAULT 'active',
    password_hash       text NOT NULL,
    last_login_at       timestamptz,
    must_change_password boolean NOT NULL DEFAULT false,
    two_factor_enabled  boolean NOT NULL DEFAULT false,
    timezone            text NOT NULL DEFAULT 'Europe/London',
    assistant_output_language_code text,
    assistant_output_language_name text,
    assistant_language_locked boolean DEFAULT true,
    assistant_tone      text DEFAULT 'professional',
    assistant_style     text DEFAULT 'concise',
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    deleted_at          timestamptz,
    CONSTRAINT fk_users_role
        FOREIGN KEY (role_id) REFERENCES roles(id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT uq_users_email UNIQUE (email),
    CONSTRAINT uq_users_employee_code UNIQUE (employee_code),
    CONSTRAINT chk_users_status CHECK (status IN ('active','inactive','suspended'))
);

CREATE TABLE role_permissions (
    id                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    role_id             bigint NOT NULL,
    permission_id       bigint NOT NULL,
    allowed             boolean NOT NULL DEFAULT true,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT fk_role_permissions_role
        FOREIGN KEY (role_id) REFERENCES roles(id)
        ON DELETE CASCADE ON UPDATE RESTRICT,
    CONSTRAINT fk_role_permissions_permission
        FOREIGN KEY (permission_id) REFERENCES permissions(id)
        ON DELETE CASCADE ON UPDATE RESTRICT,
    CONSTRAINT uq_role_permissions UNIQUE (role_id, permission_id)
);

-- 3. CRM MODUL (Klienti, Nemovitosti)
CREATE TABLE clients (
    id                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    client_code         text NOT NULL,
    client_type         text NOT NULL,
    title               text,
    first_name          text,
    last_name           text,
    display_name        text NOT NULL,
    company_name        text,
    company_registration_no text,
    vat_no              text,
    phone_primary       text,
    phone_secondary     text,
    email_primary       text,
    email_secondary     text,
    website             text,
    preferred_contact_method text NOT NULL DEFAULT 'email',
    billing_address_line1 text,
    billing_city        text,
    billing_postcode    text,
    billing_country     text NOT NULL DEFAULT 'GB',
    status              text NOT NULL DEFAULT 'active',
    is_commercial       boolean NOT NULL DEFAULT false,
    preferred_language_code text,
    preferred_language_name text,
    language_source     text,
    language_confidence numeric(3,2),
    language_updated_at timestamptz,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    deleted_at          timestamptz,
    created_by_user_id  bigint,
    CONSTRAINT uq_clients_client_code UNIQUE (client_code),
    CONSTRAINT chk_clients_status CHECK (status IN ('active','inactive','archived'))
);

CREATE TABLE properties (
    id                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    client_id           bigint NOT NULL,
    property_code       text NOT NULL,
    property_name       text NOT NULL,
    property_type       text NOT NULL,
    address_line1       text NOT NULL,
    city                text NOT NULL,
    postcode            text NOT NULL,
    country             text NOT NULL DEFAULT 'GB',
    status              text NOT NULL DEFAULT 'active',
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    deleted_at          timestamptz,
    CONSTRAINT fk_properties_client
        FOREIGN KEY (client_id) REFERENCES clients(id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT uq_properties_client_code UNIQUE (client_id, property_code)
);

CREATE TABLE property_zones (
    id                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    property_id         bigint NOT NULL,
    zone_code           text NOT NULL,
    zone_name           text NOT NULL,
    zone_type           text NOT NULL,
    area_m2             numeric(12,2),
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT fk_property_zones_property
        FOREIGN KEY (property_id) REFERENCES properties(id)
        ON DELETE RESTRICT ON UPDATE RESTRICT
);

-- 4. OBCHODNÍ MODUL (Poptávky, Nabídky, Zakázky)
CREATE TABLE leads (
    id                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    lead_code           text NOT NULL,
    client_id           bigint,
    lead_source         text NOT NULL,
    status              text NOT NULL DEFAULT 'new',
    received_at         timestamptz NOT NULL DEFAULT now(),
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_leads_code UNIQUE (lead_code)
);

CREATE TABLE quotes (
    id                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    quote_number        text NOT NULL,
    client_id           bigint NOT NULL,
    property_id         bigint NOT NULL,
    quote_title         text NOT NULL,
    status              text NOT NULL DEFAULT 'draft',
    grand_total         numeric(12,2) NOT NULL DEFAULT 0,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_quotes_number UNIQUE (quote_number)
);

CREATE TABLE jobs (
    id                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    job_number          text NOT NULL,
    client_id           bigint NOT NULL,
    property_id         bigint NOT NULL,
    quote_id            bigint,
    job_title           text NOT NULL,
    job_status          text NOT NULL DEFAULT 'draft',
    start_date_planned  date,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    deleted_at          timestamptz,
    CONSTRAINT uq_jobs_number UNIQUE (job_number)
);

CREATE TABLE job_tasks (
    id                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    job_id              bigint NOT NULL,
    task_code           text NOT NULL,
    title               text NOT NULL,
    status              text NOT NULL DEFAULT 'pending',
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT fk_job_tasks_job
        FOREIGN KEY (job_id) REFERENCES jobs(id)
        ON DELETE RESTRICT ON UPDATE RESTRICT
);

-- 5. ODPADY A MATERIÁLY
CREATE TABLE waste_types (
    id                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    waste_code          text NOT NULL,
    name                text NOT NULL,
    default_unit        text NOT NULL
);

CREATE TABLE waste_loads (
    id                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    job_id              bigint NOT NULL,
    waste_type_id       bigint NOT NULL,
    quantity            numeric(12,2) NOT NULL,
    unit                text NOT NULL,
    load_date           date NOT NULL,
    created_at          timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT fk_waste_loads_job
        FOREIGN KEY (job_id) REFERENCES jobs(id)
);

-- 6. FINANCE
CREATE TABLE invoices (
    id                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    invoice_number      text NOT NULL,
    client_id           bigint NOT NULL,
    grand_total         numeric(12,2) NOT NULL DEFAULT 0,
    status              text NOT NULL DEFAULT 'draft',
    due_date            date,
    created_at          timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_invoices_number UNIQUE (invoice_number)
);

-- 7. KOMUNIKACE A AUDIT
CREATE TABLE communications (
    id                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    client_id           bigint,
    subject             text,
    message_summary     text NOT NULL,
    direction           text NOT NULL,
    sent_at             timestamptz,
    created_at          timestamptz NOT NULL DEFAULT now()
);

-- APLIKACE AUDITNÍCH TRIGGERŮ
CREATE TRIGGER trg_audit_clients AFTER INSERT OR UPDATE OR DELETE ON clients FOR EACH ROW EXECUTE FUNCTION crm.proc_audit_log();
CREATE TRIGGER trg_audit_properties AFTER INSERT OR UPDATE OR DELETE ON properties FOR EACH ROW EXECUTE FUNCTION crm.proc_audit_log();
CREATE TRIGGER trg_audit_jobs AFTER INSERT OR UPDATE OR DELETE ON jobs FOR EACH ROW EXECUTE FUNCTION crm.proc_audit_log();
CREATE TRIGGER trg_audit_quotes AFTER INSERT OR UPDATE OR DELETE ON quotes FOR EACH ROW EXECUTE FUNCTION crm.proc_audit_log();
CREATE TRIGGER trg_audit_tasks AFTER INSERT OR UPDATE OR DELETE ON job_tasks FOR EACH ROW EXECUTE FUNCTION crm.proc_audit_log();
CREATE TRIGGER trg_audit_users AFTER INSERT OR UPDATE OR DELETE ON users FOR EACH ROW EXECUTE FUNCTION crm.proc_audit_log();

-- INDEXY PRO RYCHLÉ VYHLEDÁVÁNÍ
CREATE INDEX idx_clients_display_name ON clients(display_name);
CREATE INDEX idx_jobs_status ON jobs(job_status);
CREATE INDEX idx_audit_log_changed_at ON audit_log(changed_at);

COMMIT;
