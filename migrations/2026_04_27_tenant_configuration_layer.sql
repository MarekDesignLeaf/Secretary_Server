BEGIN;

CREATE SCHEMA IF NOT EXISTS crm;
SET search_path TO crm, public;

/*
Secretary CRM safe additive migration.
Purpose: tenant configuration layer, onboarding support, language model and industry model.
Rule: no destructive rewrite, no table rebuild, no ID conversion, no legacy foreign key hardening.
*/

CREATE TABLE IF NOT EXISTS tenants (
    id integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name text NOT NULL,
    slug text UNIQUE,
    status text DEFAULT 'active',
    created_at timestamptz DEFAULT now()
);

ALTER TABLE IF EXISTS tenants ADD COLUMN IF NOT EXISTS legal_type text;
ALTER TABLE IF EXISTS tenants ADD COLUMN IF NOT EXISTS company_registration_no text;
ALTER TABLE IF EXISTS tenants ADD COLUMN IF NOT EXISTS vat_no text;
ALTER TABLE IF EXISTS tenants ADD COLUMN IF NOT EXISTS phone text;
ALTER TABLE IF EXISTS tenants ADD COLUMN IF NOT EXISTS email text;
ALTER TABLE IF EXISTS tenants ADD COLUMN IF NOT EXISTS website text;
ALTER TABLE IF EXISTS tenants ADD COLUMN IF NOT EXISTS country_code text DEFAULT 'GB';
ALTER TABLE IF EXISTS tenants ADD COLUMN IF NOT EXISTS timezone text DEFAULT 'Europe/London';
ALTER TABLE IF EXISTS tenants ADD COLUMN IF NOT EXISTS currency text DEFAULT 'GBP';
ALTER TABLE IF EXISTS tenants ADD COLUMN IF NOT EXISTS updated_at timestamptz DEFAULT now();

ALTER TABLE IF EXISTS users ADD COLUMN IF NOT EXISTS preferred_language_code text;
ALTER TABLE IF EXISTS users ADD COLUMN IF NOT EXISTS is_owner boolean DEFAULT false;
ALTER TABLE IF EXISTS users ADD COLUMN IF NOT EXISTS is_assistant boolean DEFAULT false;

ALTER TABLE IF EXISTS clients ADD COLUMN IF NOT EXISTS preferred_language_code text;

ALTER TABLE IF EXISTS tasks ADD COLUMN IF NOT EXISTS version bigint NOT NULL DEFAULT 1;
ALTER TABLE IF EXISTS tasks ADD COLUMN IF NOT EXISTS last_modified_by_device_id text;
ALTER TABLE IF EXISTS tasks ADD COLUMN IF NOT EXISTS deleted_at_sync timestamptz;

ALTER TABLE IF EXISTS voice_sessions ADD COLUMN IF NOT EXISTS language_code text;
ALTER TABLE IF EXISTS voice_sessions ADD COLUMN IF NOT EXISTS detected_language text;

CREATE TABLE IF NOT EXISTS tenant_settings (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_id integer NOT NULL,
    date_format text DEFAULT 'DD/MM/YYYY',
    time_format text DEFAULT '24h',
    email_enabled boolean DEFAULT true,
    whatsapp_enabled boolean DEFAULT true,
    voice_enabled boolean DEFAULT true,
    client_portal_enabled boolean DEFAULT false,
    created_at timestamptz DEFAULT now(),
    updated_at timestamptz DEFAULT now()
);

ALTER TABLE IF EXISTS tenant_settings ADD COLUMN IF NOT EXISTS tenant_id integer;
ALTER TABLE IF EXISTS tenant_settings ADD COLUMN IF NOT EXISTS date_format text DEFAULT 'DD/MM/YYYY';
ALTER TABLE IF EXISTS tenant_settings ADD COLUMN IF NOT EXISTS time_format text DEFAULT '24h';
ALTER TABLE IF EXISTS tenant_settings ADD COLUMN IF NOT EXISTS email_enabled boolean DEFAULT true;
ALTER TABLE IF EXISTS tenant_settings ADD COLUMN IF NOT EXISTS whatsapp_enabled boolean DEFAULT true;
ALTER TABLE IF EXISTS tenant_settings ADD COLUMN IF NOT EXISTS voice_enabled boolean DEFAULT true;
ALTER TABLE IF EXISTS tenant_settings ADD COLUMN IF NOT EXISTS client_portal_enabled boolean DEFAULT false;
ALTER TABLE IF EXISTS tenant_settings ADD COLUMN IF NOT EXISTS created_at timestamptz DEFAULT now();
ALTER TABLE IF EXISTS tenant_settings ADD COLUMN IF NOT EXISTS updated_at timestamptz DEFAULT now();

CREATE UNIQUE INDEX IF NOT EXISTS uq_tenant_settings_tenant_id ON tenant_settings (tenant_id);

CREATE TABLE IF NOT EXISTS tenant_operating_profile (
    tenant_id integer PRIMARY KEY,
    internal_language_mode text DEFAULT 'single',
    customer_language_mode text DEFAULT 'single',
    default_internal_language_code text DEFAULT 'cs-CZ',
    default_customer_language_code text DEFAULT 'en-GB',
    auto_translate_internal_to_customer boolean DEFAULT true,
    auto_translate_customer_to_internal boolean DEFAULT true,
    voice_input_strategy text DEFAULT 'auto_detect',
    voice_output_strategy text DEFAULT 'customer_default',
    workspace_mode text DEFAULT 'team',
    max_active_users integer DEFAULT 10,
    industry_group_id bigint,
    industry_subtype_id bigint,
    created_at timestamptz DEFAULT now(),
    updated_at timestamptz DEFAULT now()
);

ALTER TABLE IF EXISTS tenant_operating_profile ADD COLUMN IF NOT EXISTS tenant_id integer;
ALTER TABLE IF EXISTS tenant_operating_profile ADD COLUMN IF NOT EXISTS internal_language_mode text DEFAULT 'single';
ALTER TABLE IF EXISTS tenant_operating_profile ADD COLUMN IF NOT EXISTS customer_language_mode text DEFAULT 'single';
ALTER TABLE IF EXISTS tenant_operating_profile ADD COLUMN IF NOT EXISTS default_internal_language_code text DEFAULT 'cs-CZ';
ALTER TABLE IF EXISTS tenant_operating_profile ADD COLUMN IF NOT EXISTS default_customer_language_code text DEFAULT 'en-GB';
ALTER TABLE IF EXISTS tenant_operating_profile ADD COLUMN IF NOT EXISTS auto_translate_internal_to_customer boolean DEFAULT true;
ALTER TABLE IF EXISTS tenant_operating_profile ADD COLUMN IF NOT EXISTS auto_translate_customer_to_internal boolean DEFAULT true;
ALTER TABLE IF EXISTS tenant_operating_profile ADD COLUMN IF NOT EXISTS voice_input_strategy text DEFAULT 'auto_detect';
ALTER TABLE IF EXISTS tenant_operating_profile ADD COLUMN IF NOT EXISTS voice_output_strategy text DEFAULT 'customer_default';
ALTER TABLE IF EXISTS tenant_operating_profile ADD COLUMN IF NOT EXISTS workspace_mode text DEFAULT 'team';
ALTER TABLE IF EXISTS tenant_operating_profile ADD COLUMN IF NOT EXISTS max_active_users integer DEFAULT 10;
ALTER TABLE IF EXISTS tenant_operating_profile ADD COLUMN IF NOT EXISTS industry_group_id bigint;
ALTER TABLE IF EXISTS tenant_operating_profile ADD COLUMN IF NOT EXISTS industry_subtype_id bigint;
ALTER TABLE IF EXISTS tenant_operating_profile ADD COLUMN IF NOT EXISTS created_at timestamptz DEFAULT now();
ALTER TABLE IF EXISTS tenant_operating_profile ADD COLUMN IF NOT EXISTS updated_at timestamptz DEFAULT now();

CREATE UNIQUE INDEX IF NOT EXISTS uq_tenant_operating_profile_tenant_id ON tenant_operating_profile (tenant_id);

CREATE TABLE IF NOT EXISTS tenant_languages (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_id integer NOT NULL,
    language_code text NOT NULL,
    language_scope text NOT NULL,
    is_default boolean DEFAULT false,
    is_active boolean DEFAULT true,
    sort_order integer DEFAULT 0,
    created_at timestamptz DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_tenant_languages_tenant_code_scope ON tenant_languages (tenant_id, language_code, language_scope);
CREATE INDEX IF NOT EXISTS idx_tenant_languages_tenant_scope ON tenant_languages (tenant_id, language_scope);

CREATE TABLE IF NOT EXISTS subscription_limits (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_id integer NOT NULL,
    max_users integer,
    max_clients integer,
    max_jobs_per_month integer,
    max_voice_minutes integer,
    created_at timestamptz DEFAULT now(),
    updated_at timestamptz DEFAULT now()
);

ALTER TABLE IF EXISTS subscription_limits ADD COLUMN IF NOT EXISTS tenant_id integer;
ALTER TABLE IF EXISTS subscription_limits ADD COLUMN IF NOT EXISTS max_users integer;
ALTER TABLE IF EXISTS subscription_limits ADD COLUMN IF NOT EXISTS max_clients integer;
ALTER TABLE IF EXISTS subscription_limits ADD COLUMN IF NOT EXISTS max_jobs_per_month integer;
ALTER TABLE IF EXISTS subscription_limits ADD COLUMN IF NOT EXISTS max_voice_minutes integer;
ALTER TABLE IF EXISTS subscription_limits ADD COLUMN IF NOT EXISTS created_at timestamptz DEFAULT now();
ALTER TABLE IF EXISTS subscription_limits ADD COLUMN IF NOT EXISTS updated_at timestamptz DEFAULT now();

CREATE UNIQUE INDEX IF NOT EXISTS uq_subscription_limits_tenant_id ON subscription_limits (tenant_id);

CREATE TABLE IF NOT EXISTS industry_groups (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    code text NOT NULL,
    name text NOT NULL,
    sort_order integer DEFAULT 0,
    is_active boolean DEFAULT true
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_industry_groups_code ON industry_groups (code);

CREATE TABLE IF NOT EXISTS industry_subtypes (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    industry_group_id bigint NOT NULL,
    code text NOT NULL,
    name text NOT NULL,
    sort_order integer DEFAULT 0,
    is_active boolean DEFAULT true
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_industry_subtypes_group_code ON industry_subtypes (industry_group_id, code);
CREATE INDEX IF NOT EXISTS idx_industry_subtypes_group_id ON industry_subtypes (industry_group_id);

CREATE TABLE IF NOT EXISTS tenant_industry_profile (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_id integer NOT NULL,
    industry_group_id bigint NOT NULL,
    industry_subtype_id bigint,
    is_primary boolean DEFAULT true,
    created_at timestamptz DEFAULT now(),
    updated_at timestamptz DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_tenant_industry_profile_primary ON tenant_industry_profile (tenant_id, is_primary) WHERE is_primary = true;
CREATE INDEX IF NOT EXISTS idx_tenant_industry_profile_tenant_id ON tenant_industry_profile (tenant_id);

INSERT INTO industry_groups (code, name, sort_order, is_active) VALUES
('trades', 'Trades and field services', 10, true),
('property', 'Property management and maintenance', 20, true),
('real_estate', 'Real estate and lettings', 30, true),
('cleaning', 'Cleaning services', 40, true),
('automotive', 'Automotive services', 50, true),
('beauty', 'Beauty and personal care', 60, true),
('healthcare', 'Healthcare and wellbeing', 70, true),
('hospitality', 'Hospitality', 80, true),
('fitness', 'Fitness and coaching', 90, true),
('events', 'Events and venue services', 100, true)
ON CONFLICT (code) DO NOTHING;

INSERT INTO industry_subtypes (industry_group_id, code, name, sort_order, is_active)
SELECT id, 'landscaping', 'Landscaping and garden services', 10, true FROM industry_groups WHERE code = 'trades'
ON CONFLICT DO NOTHING;

INSERT INTO industry_subtypes (industry_group_id, code, name, sort_order, is_active)
SELECT id, 'building_maintenance', 'Building maintenance and repairs', 20, true FROM industry_groups WHERE code = 'trades'
ON CONFLICT DO NOTHING;

INSERT INTO industry_subtypes (industry_group_id, code, name, sort_order, is_active)
SELECT id, 'hmo_management', 'HMO property management', 10, true FROM industry_groups WHERE code = 'property'
ON CONFLICT DO NOTHING;

INSERT INTO industry_subtypes (industry_group_id, code, name, sort_order, is_active)
SELECT id, 'renovation_project_management', 'Renovation project management', 20, true FROM industry_groups WHERE code = 'property'
ON CONFLICT DO NOTHING;

INSERT INTO industry_subtypes (industry_group_id, code, name, sort_order, is_active)
SELECT id, 'lettings_coordination', 'Lettings coordination', 10, true FROM industry_groups WHERE code = 'real_estate'
ON CONFLICT DO NOTHING;

INSERT INTO tenants (name, slug, status)
VALUES ('DesignLeaf', 'designleaf', 'active')
ON CONFLICT (slug) DO NOTHING;

INSERT INTO tenant_settings (tenant_id, date_format, time_format, email_enabled, whatsapp_enabled, voice_enabled, client_portal_enabled)
SELECT id, 'DD/MM/YYYY', '24h', true, true, true, false FROM tenants WHERE slug = 'designleaf'
ON CONFLICT (tenant_id) DO NOTHING;

INSERT INTO tenant_operating_profile (
    tenant_id,
    internal_language_mode,
    customer_language_mode,
    default_internal_language_code,
    default_customer_language_code,
    voice_input_strategy,
    voice_output_strategy,
    workspace_mode,
    max_active_users,
    industry_group_id,
    industry_subtype_id
)
SELECT
    t.id,
    'single',
    'multi',
    'cs-CZ',
    'en-GB',
    'auto_detect',
    'customer_default',
    'team',
    10,
    ig.id,
    ist.id
FROM tenants t
LEFT JOIN industry_groups ig ON ig.code = 'trades'
LEFT JOIN industry_subtypes ist ON ist.industry_group_id = ig.id AND ist.code = 'landscaping'
WHERE t.slug = 'designleaf'
ON CONFLICT (tenant_id) DO NOTHING;

INSERT INTO tenant_languages (tenant_id, language_code, language_scope, is_default, is_active, sort_order)
SELECT id, 'cs-CZ', 'internal', true, true, 10 FROM tenants WHERE slug = 'designleaf'
ON CONFLICT (tenant_id, language_code, language_scope) DO NOTHING;

INSERT INTO tenant_languages (tenant_id, language_code, language_scope, is_default, is_active, sort_order)
SELECT id, 'en-GB', 'customer', true, true, 10 FROM tenants WHERE slug = 'designleaf'
ON CONFLICT (tenant_id, language_code, language_scope) DO NOTHING;

INSERT INTO tenant_languages (tenant_id, language_code, language_scope, is_default, is_active, sort_order)
SELECT id, 'cs-CZ', 'voice_input', true, true, 10 FROM tenants WHERE slug = 'designleaf'
ON CONFLICT (tenant_id, language_code, language_scope) DO NOTHING;

INSERT INTO tenant_languages (tenant_id, language_code, language_scope, is_default, is_active, sort_order)
SELECT id, 'en-GB', 'voice_output', true, true, 10 FROM tenants WHERE slug = 'designleaf'
ON CONFLICT (tenant_id, language_code, language_scope) DO NOTHING;

INSERT INTO subscription_limits (tenant_id, max_users, max_clients, max_jobs_per_month, max_voice_minutes)
SELECT id, 10, 1000, 500, 10000 FROM tenants WHERE slug = 'designleaf'
ON CONFLICT (tenant_id) DO NOTHING;

INSERT INTO tenant_industry_profile (tenant_id, industry_group_id, industry_subtype_id, is_primary)
SELECT t.id, ig.id, ist.id, true
FROM tenants t
JOIN industry_groups ig ON ig.code = 'trades'
LEFT JOIN industry_subtypes ist ON ist.industry_group_id = ig.id AND ist.code = 'landscaping'
WHERE t.slug = 'designleaf'
ON CONFLICT DO NOTHING;

CREATE TABLE IF NOT EXISTS migration_log (
    id integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    filename text NOT NULL UNIQUE,
    applied_at timestamptz DEFAULT now()
);

INSERT INTO migration_log (filename)
VALUES ('2026_04_27_tenant_configuration_layer.sql')
ON CONFLICT (filename) DO NOTHING;

COMMIT;
