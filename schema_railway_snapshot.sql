-- ============================================================
-- Railway DB Schema Snapshot (crm schema)
-- Generated: 2026-04-07 from crm_schema_snapshot.txt
-- SOURCE OF TRUTH — do not edit manually
-- ============================================================

BEGIN;
SET search_path TO crm, public;

CREATE TABLE IF NOT EXISTS activity_timeline (
    id BIGINT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    action TEXT NOT NULL,
    description TEXT NOT NULL,
    user_name TEXT DEFAULT 'Marek',
    created_at TIMESTAMPTZ DEFAULT now(),
    tenant_id INT DEFAULT 1,
    user_id_ref TEXT
);

CREATE TABLE IF NOT EXISTS audit_log (
    id BIGINT NOT NULL,
    tenant_id INT DEFAULT 1,
    user_id BIGINT,
    action TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT,
    old_values JSONB,
    new_values JSONB,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS client_notes (
    id BIGINT NOT NULL,
    client_id BIGINT NOT NULL,
    note TEXT NOT NULL,
    created_by TEXT DEFAULT 'Marek',
    created_at TIMESTAMPTZ DEFAULT now(),
    tenant_id INT DEFAULT 1 NOT NULL
);

CREATE TABLE IF NOT EXISTS clients (
    id BIGINT NOT NULL,
    client_code TEXT,
    client_type TEXT DEFAULT 'domestic' NOT NULL,
    title TEXT,
    first_name TEXT,
    last_name TEXT,
    display_name TEXT NOT NULL,
    company_name TEXT,
    phone_primary TEXT,
    phone_secondary TEXT,
    email_primary TEXT,
    email_secondary TEXT,
    website TEXT,
    preferred_contact_method TEXT DEFAULT 'email' NOT NULL,
    billing_address_line1 TEXT,
    billing_city TEXT,
    billing_postcode TEXT,
    billing_country TEXT DEFAULT 'GB' NOT NULL,
    status TEXT DEFAULT 'active' NOT NULL,
    is_commercial BOOLEAN DEFAULT false NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now() NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT now() NOT NULL,
    deleted_at TIMESTAMPTZ,
    created_by_user_id BIGINT,
    tenant_id INT DEFAULT 1 NOT NULL,
    company_registration_no TEXT,
    vat_no TEXT,
    preferred_language_code TEXT,
    default_hourly_rate DECIMAL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS communications (
    id BIGINT NOT NULL,
    client_id BIGINT,
    subject TEXT,
    message_summary TEXT DEFAULT '' NOT NULL,
    direction TEXT DEFAULT 'inbound' NOT NULL,
    sent_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT now() NOT NULL,
    comm_type TEXT DEFAULT 'telefon',
    job_id BIGINT,
    notes TEXT,
    tenant_id INT DEFAULT 1 NOT NULL
);

CREATE TABLE IF NOT EXISTS industry_groups (
    id BIGINT GENERATED ALWAYS AS IDENTITY,
    code TEXT NOT NULL,
    name TEXT NOT NULL,
    sort_order INT DEFAULT 0
);

CREATE TABLE IF NOT EXISTS industry_subtypes (
    id BIGINT GENERATED ALWAYS AS IDENTITY,
    industry_group_id BIGINT NOT NULL,
    code TEXT NOT NULL,
    name TEXT NOT NULL,
    sort_order INT DEFAULT 0
);

CREATE TABLE IF NOT EXISTS invoice_items (
    id BIGINT NOT NULL,
    tenant_id INT DEFAULT 1 NOT NULL,
    invoice_id BIGINT NOT NULL,
    description TEXT NOT NULL,
    quantity DECIMAL DEFAULT 1 NOT NULL,
    unit_price DECIMAL DEFAULT 0 NOT NULL,
    total DECIMAL DEFAULT 0 NOT NULL,
    sort_order INT DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS invoices (
    id BIGINT NOT NULL,
    invoice_number TEXT,
    client_id BIGINT DEFAULT 1 NOT NULL,
    grand_total DECIMAL DEFAULT 0 NOT NULL,
    status TEXT DEFAULT 'draft' NOT NULL,
    due_date DATE,
    created_at TIMESTAMPTZ DEFAULT now() NOT NULL,
    tenant_id INT DEFAULT 1 NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT now(),
    notes TEXT,
    job_id BIGINT,
    created_by BIGINT,
    work_report_id BIGINT
);

CREATE TABLE IF NOT EXISTS job_notes (
    id BIGINT NOT NULL,
    job_id BIGINT NOT NULL,
    note TEXT NOT NULL,
    created_by TEXT DEFAULT 'Marek',
    created_at TIMESTAMPTZ DEFAULT now(),
    tenant_id INT DEFAULT 1 NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
    id BIGINT NOT NULL,
    job_number TEXT,
    client_id BIGINT DEFAULT 1 NOT NULL,
    property_id BIGINT DEFAULT 1,
    quote_id BIGINT,
    job_title TEXT NOT NULL,
    job_status TEXT DEFAULT 'draft' NOT NULL,
    start_date_planned DATE,
    created_at TIMESTAMPTZ DEFAULT now() NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT now() NOT NULL,
    deleted_at TIMESTAMPTZ,
    tenant_id INT DEFAULT 1 NOT NULL
);

CREATE TABLE IF NOT EXISTS leads (
    id BIGINT NOT NULL,
    lead_code TEXT,
    client_id BIGINT,
    lead_source TEXT DEFAULT 'web' NOT NULL,
    status TEXT DEFAULT 'new' NOT NULL,
    received_at TIMESTAMPTZ DEFAULT now() NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now() NOT NULL,
    contact_name TEXT,
    contact_email TEXT,
    contact_phone TEXT,
    description TEXT,
    notes TEXT,
    job_id BIGINT,
    updated_at TIMESTAMPTZ DEFAULT now(),
    tenant_id INT DEFAULT 1 NOT NULL
);

CREATE TABLE IF NOT EXISTS migration_log (
    id INT NOT NULL,
    filename TEXT NOT NULL,
    applied_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS notifications (
    id BIGINT NOT NULL,
    tenant_id INT DEFAULT 1 NOT NULL,
    user_id BIGINT,
    title TEXT NOT NULL,
    body TEXT,
    notification_type TEXT DEFAULT 'info',
    entity_type TEXT,
    entity_id TEXT,
    is_read BOOLEAN DEFAULT false,
    created_at TIMESTAMPTZ DEFAULT now(),
    read_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS payments (
    id BIGINT NOT NULL,
    tenant_id INT DEFAULT 1 NOT NULL,
    invoice_id BIGINT NOT NULL,
    amount DECIMAL NOT NULL,
    payment_date DATE DEFAULT CURRENT_DATE NOT NULL,
    payment_method TEXT DEFAULT 'bank_transfer',
    reference TEXT,
    notes TEXT,
    created_by BIGINT,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS photos (
    id BIGINT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    filename TEXT NOT NULL,
    description TEXT,
    file_path TEXT,
    thumbnail_base64 TEXT,
    created_by TEXT DEFAULT 'Marek',
    created_at TIMESTAMPTZ DEFAULT now(),
    tenant_id INT DEFAULT 1 NOT NULL
);

CREATE TABLE IF NOT EXISTS pricing_rules (
    id BIGINT NOT NULL,
    tenant_id INT DEFAULT 1,
    scope TEXT DEFAULT 'system',
    scope_id BIGINT,
    rule_type TEXT NOT NULL,
    rule_key TEXT,
    rate DECIMAL NOT NULL,
    currency TEXT DEFAULT 'GBP',
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS properties (
    id BIGINT NOT NULL,
    client_id BIGINT NOT NULL,
    property_code TEXT,
    property_name TEXT NOT NULL,
    property_type TEXT DEFAULT 'residential' NOT NULL,
    address_line1 TEXT DEFAULT '' NOT NULL,
    city TEXT DEFAULT '' NOT NULL,
    postcode TEXT DEFAULT '' NOT NULL,
    country TEXT DEFAULT 'GB' NOT NULL,
    status TEXT DEFAULT 'active' NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now() NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT now() NOT NULL,
    deleted_at TIMESTAMPTZ,
    tenant_id INT DEFAULT 1 NOT NULL
);

CREATE TABLE IF NOT EXISTS quote_items (
    id BIGINT NOT NULL,
    quote_id BIGINT NOT NULL,
    description TEXT NOT NULL,
    quantity DECIMAL DEFAULT 1 NOT NULL,
    unit_price DECIMAL DEFAULT 0 NOT NULL,
    total DECIMAL DEFAULT 0 NOT NULL,
    sort_order INT DEFAULT 0 NOT NULL
);

CREATE TABLE IF NOT EXISTS quotes (
    id BIGINT NOT NULL,
    tenant_id INT DEFAULT 1,
    quote_number TEXT,
    client_id BIGINT,
    status TEXT DEFAULT 'draft',
    total DECIMAL DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT now(),
    quote_title TEXT,
    valid_until DATE,
    notes TEXT,
    grand_total DECIMAL DEFAULT 0,
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS roles (
    id BIGINT NOT NULL,
    role_name TEXT NOT NULL,
    description TEXT,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS subscription_limits (
    id BIGINT GENERATED ALWAYS AS IDENTITY,
    tenant_id INT NOT NULL,
    max_users INT,
    max_clients INT,
    max_jobs_per_month INT,
    max_voice_minutes INT,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS system_templates (
    id BIGINT GENERATED ALWAYS AS IDENTITY,
    template_type TEXT NOT NULL,
    industry_group_id BIGINT,
    industry_subtype_id BIGINT,
    code TEXT NOT NULL,
    name TEXT NOT NULL,
    version TEXT,
    config_json JSONB NOT NULL,
    is_system BOOLEAN DEFAULT true,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS task_history (
    id BIGINT NOT NULL,
    task_id TEXT NOT NULL,
    field_name TEXT NOT NULL,
    old_value TEXT,
    new_value TEXT,
    changed_by TEXT DEFAULT 'Marek',
    changed_at TIMESTAMPTZ DEFAULT now(),
    tenant_id INT DEFAULT 1 NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
    id TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT,
    task_type TEXT DEFAULT 'interni_poznamka',
    status TEXT DEFAULT 'novy',
    priority TEXT DEFAULT 'bezna',
    created_at TIMESTAMPTZ DEFAULT now(),
    deadline TEXT,
    planned_date TEXT,
    time_window_start TEXT,
    time_window_end TEXT,
    estimated_minutes INT,
    actual_minutes INT,
    created_by TEXT,
    assigned_to TEXT,
    delegated_by TEXT,
    client_id BIGINT,
    client_name TEXT,
    job_id BIGINT,
    property_id BIGINT,
    property_address TEXT,
    is_recurring BOOLEAN DEFAULT false,
    recurrence_rule TEXT,
    result TEXT,
    notes JSONB DEFAULT [],
    communication_method TEXT,
    source TEXT DEFAULT 'manualne',
    is_billable BOOLEAN DEFAULT false,
    has_cost BOOLEAN DEFAULT false,
    waiting_for_payment BOOLEAN DEFAULT false,
    checklist JSONB DEFAULT [],
    is_completed BOOLEAN DEFAULT false,
    updated_at TIMESTAMPTZ DEFAULT now(),
    tenant_id INT DEFAULT 1 NOT NULL,
    version BIGINT DEFAULT 1 NOT NULL,
    last_modified_by_device_id TEXT,
    deleted_at_sync TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS tenant_default_rates (
    id BIGINT GENERATED ALWAYS AS IDENTITY,
    tenant_id INT NOT NULL,
    rate_type TEXT NOT NULL,
    rate DECIMAL DEFAULT 0 NOT NULL,
    currency TEXT DEFAULT 'GBP',
    description TEXT,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS tenant_industry_profile (
    id BIGINT GENERATED ALWAYS AS IDENTITY,
    tenant_id INT NOT NULL,
    industry_group_id BIGINT NOT NULL,
    industry_subtype_id BIGINT,
    is_primary BOOLEAN DEFAULT true,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS tenant_languages (
    id BIGINT GENERATED ALWAYS AS IDENTITY,
    tenant_id INT NOT NULL,
    language_code TEXT NOT NULL,
    language_scope TEXT NOT NULL,
    is_default BOOLEAN DEFAULT false,
    is_active BOOLEAN DEFAULT true,
    sort_order INT DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS tenant_operating_profile (
    id BIGINT GENERATED ALWAYS AS IDENTITY,
    tenant_id INT NOT NULL,
    internal_language_mode TEXT DEFAULT 'single' NOT NULL,
    customer_language_mode TEXT DEFAULT 'single' NOT NULL,
    default_internal_language_code TEXT DEFAULT 'en' NOT NULL,
    default_customer_language_code TEXT DEFAULT 'en' NOT NULL,
    auto_translate_internal_to_customer BOOLEAN DEFAULT true,
    auto_translate_customer_to_internal BOOLEAN DEFAULT true,
    voice_input_strategy TEXT DEFAULT 'auto_detect',
    voice_output_strategy TEXT DEFAULT 'customer_default',
    workspace_mode TEXT DEFAULT 'solo' NOT NULL,
    max_active_users INT DEFAULT 1 NOT NULL,
    industry_group_id BIGINT,
    industry_subtype_id BIGINT,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS tenant_settings (
    id BIGINT GENERATED ALWAYS AS IDENTITY,
    tenant_id INT NOT NULL,
    date_format TEXT DEFAULT 'DD/MM/YYYY',
    time_format TEXT DEFAULT '24h',
    email_enabled BOOLEAN DEFAULT true,
    whatsapp_enabled BOOLEAN DEFAULT true,
    voice_enabled BOOLEAN DEFAULT true,
    client_portal_enabled BOOLEAN DEFAULT false,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS tenant_template_overrides (
    id BIGINT GENERATED ALWAYS AS IDENTITY,
    tenant_id INT NOT NULL,
    system_template_id BIGINT NOT NULL,
    override_json JSONB,
    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS tenants (
    id INT NOT NULL,
    name TEXT NOT NULL,
    slug TEXT NOT NULL,
    status TEXT DEFAULT 'active',
    created_at TIMESTAMPTZ DEFAULT now(),
    legal_type TEXT,
    company_registration_no TEXT,
    vat_no TEXT,
    phone TEXT,
    email TEXT,
    website TEXT,
    country_code TEXT DEFAULT 'GB',
    timezone TEXT DEFAULT 'Europe/London',
    currency TEXT DEFAULT 'GBP',
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS users (
    id BIGINT NOT NULL,
    tenant_id INT DEFAULT 1,
    role_id BIGINT,
    first_name TEXT NOT NULL,
    last_name TEXT NOT NULL,
    display_name TEXT NOT NULL,
    email TEXT NOT NULL,
    phone TEXT,
    status TEXT DEFAULT 'active',
    password_hash TEXT DEFAULT '',
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now(),
    deleted_at TIMESTAMPTZ,
    preferred_language_code TEXT,
    is_owner BOOLEAN DEFAULT false,
    is_assistant BOOLEAN DEFAULT false,
    hourly_rate DECIMAL DEFAULT 0,
    hourly_cost DECIMAL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS voice_sessions (
    id TEXT NOT NULL,
    tenant_id INT DEFAULT 1,
    user_id BIGINT,
    session_type TEXT DEFAULT 'work_report',
    state TEXT DEFAULT 'init',
    dialog_step TEXT DEFAULT 'client',
    context JSONB DEFAULT {},
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now(),
    expires_at TIMESTAMPTZ DEFAULT (now() + '01:00:00'),
    language_code TEXT,
    detected_language TEXT
);

CREATE TABLE IF NOT EXISTS waste_loads (
    id BIGINT NOT NULL,
    job_id BIGINT DEFAULT 1 NOT NULL,
    waste_type_id BIGINT DEFAULT 1 NOT NULL,
    quantity DECIMAL DEFAULT 0 NOT NULL,
    unit TEXT DEFAULT 'kg' NOT NULL,
    load_date DATE DEFAULT CURRENT_DATE NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now() NOT NULL,
    tenant_id INT DEFAULT 1 NOT NULL
);

CREATE TABLE IF NOT EXISTS waste_types (
    id BIGINT NOT NULL,
    waste_code TEXT NOT NULL,
    name TEXT NOT NULL,
    default_unit TEXT DEFAULT 'kg' NOT NULL
);

CREATE TABLE IF NOT EXISTS work_report_entries (
    id BIGINT NOT NULL,
    work_report_id BIGINT NOT NULL,
    type TEXT NOT NULL,
    description TEXT,
    hours DECIMAL DEFAULT 0,
    unit_rate DECIMAL DEFAULT 0,
    total_price DECIMAL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS work_report_materials (
    id BIGINT NOT NULL,
    work_report_id BIGINT NOT NULL,
    material_name TEXT NOT NULL,
    quantity DECIMAL DEFAULT 0,
    unit TEXT DEFAULT 'ks',
    unit_price DECIMAL DEFAULT 0,
    total_price DECIMAL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS work_report_waste (
    id BIGINT NOT NULL,
    work_report_id BIGINT NOT NULL,
    quantity DECIMAL DEFAULT 0,
    unit TEXT DEFAULT 'bulkbag',
    unit_price DECIMAL DEFAULT 0,
    total_price DECIMAL DEFAULT 0,
    bulkbags DECIMAL DEFAULT 0,
    cost_per_bag DECIMAL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS work_report_workers (
    id BIGINT NOT NULL,
    work_report_id BIGINT NOT NULL,
    user_id BIGINT,
    worker_name TEXT NOT NULL,
    hours DECIMAL NOT NULL,
    hourly_rate DECIMAL DEFAULT 0,
    total_price DECIMAL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS work_reports (
    id BIGINT NOT NULL,
    tenant_id INT DEFAULT 1,
    client_id BIGINT NOT NULL,
    property_id BIGINT,
    job_id BIGINT,
    work_date DATE NOT NULL,
    total_hours DECIMAL NOT NULL,
    total_price DECIMAL DEFAULT 0,
    currency TEXT DEFAULT 'GBP',
    notes TEXT,
    created_by BIGINT,
    input_type TEXT DEFAULT 'voice',
    status TEXT DEFAULT 'draft',
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

COMMIT;