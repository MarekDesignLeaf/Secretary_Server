-- Clean Secretary backend foundation schema draft.
-- This is not a Railway migration and was not executed.
-- System catalogue defaults are immutable source data; tenant pricing only stores
-- company-specific selection/overrides and can be reset without deleting defaults.

CREATE TABLE clean_companies (
    id UUID PRIMARY KEY,
    legal_name TEXT NOT NULL,
    trading_name TEXT,
    legal_type TEXT,
    default_country TEXT NOT NULL DEFAULT 'GB',
    default_currency TEXT NOT NULL DEFAULT 'GBP',
    timezone TEXT NOT NULL DEFAULT 'Europe/London',
    phone TEXT,
    website TEXT,
    industry_group TEXT,
    industry_subtype TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE clean_company_operating_settings (
    company_id UUID PRIMARY KEY REFERENCES clean_companies(id) ON DELETE CASCADE,
    workspace_mode TEXT NOT NULL DEFAULT 'single_company',
    quote_prefix TEXT NOT NULL DEFAULT 'Q',
    invoice_prefix TEXT NOT NULL DEFAULT 'INV',
    default_tax_rate_percent NUMERIC(7, 4) NOT NULL DEFAULT 0,
    require_quote_acceptance_before_invoice BOOLEAN NOT NULL DEFAULT TRUE,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE tenant_operating_profile (
    company_id UUID PRIMARY KEY REFERENCES clean_companies(id) ON DELETE CASCADE,
    workspace_mode TEXT NOT NULL DEFAULT 'single_company',
    industry_group TEXT,
    industry_subtype TEXT,
    internal_language_mode TEXT NOT NULL DEFAULT 'single',
    customer_language_mode TEXT NOT NULL DEFAULT 'multilingual',
    default_internal_language_code TEXT NOT NULL DEFAULT 'en-GB',
    default_customer_language_code TEXT NOT NULL DEFAULT 'en-GB',
    voice_input_strategy TEXT NOT NULL DEFAULT 'detect_from_context',
    voice_output_strategy TEXT NOT NULL DEFAULT 'client_preferred',
    auto_translate_customer_to_internal BOOLEAN NOT NULL DEFAULT TRUE,
    auto_translate_internal_to_customer BOOLEAN NOT NULL DEFAULT TRUE,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (internal_language_mode IN ('single', 'multilingual', 'context')),
    CHECK (customer_language_mode IN ('single', 'multilingual', 'context')),
    CHECK (voice_input_strategy IN ('tenant_default', 'user_preferred', 'client_preferred', 'detect_from_context')),
    CHECK (voice_output_strategy IN ('tenant_default', 'user_preferred', 'client_preferred', 'detect_from_context'))
);

CREATE TABLE tenant_languages (
    company_id UUID NOT NULL REFERENCES clean_companies(id) ON DELETE CASCADE,
    language_code TEXT NOT NULL,
    language_scope TEXT NOT NULL,
    is_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    is_default BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (company_id, language_scope, language_code),
    CHECK (language_scope IN ('internal', 'customer', 'voice_input', 'voice_output'))
);

CREATE TABLE clean_users (
    id UUID PRIMARY KEY,
    company_id UUID NOT NULL REFERENCES clean_companies(id) ON DELETE CASCADE,
    email CITEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    preferred_language_code TEXT,
    first_name TEXT,
    last_name TEXT,
    phone TEXT,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);


CREATE TABLE clean_tenant_configuration (
    company_id UUID PRIMARY KEY REFERENCES clean_companies(id) ON DELETE CASCADE,
    workspace_mode TEXT NOT NULL DEFAULT 'single_company',
    industry_group TEXT,
    industry_subtype TEXT,
    phone TEXT,
    website TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE clean_permissions (
    code TEXT PRIMARY KEY,
    description TEXT NOT NULL
);

CREATE TABLE clean_role_permissions (
    role TEXT NOT NULL,
    permission_code TEXT NOT NULL REFERENCES clean_permissions(code),
    PRIMARY KEY (role, permission_code)
);

CREATE TABLE clean_industries (
    code TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    display_order INTEGER NOT NULL UNIQUE
);

CREATE TABLE clean_work_subtypes (
    code TEXT PRIMARY KEY,
    industry_code TEXT NOT NULL REFERENCES clean_industries(code),
    name TEXT NOT NULL,
    display_order INTEGER NOT NULL,
    UNIQUE (industry_code, display_order)
);

CREATE TABLE clean_pricing_methods (
    code TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    unit TEXT,
    display_order INTEGER NOT NULL UNIQUE
);

CREATE TABLE clean_additional_charges (
    code TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    display_order INTEGER NOT NULL UNIQUE
);

CREATE TABLE clean_work_activities (
    code TEXT PRIMARY KEY,
    industry_code TEXT NOT NULL REFERENCES clean_industries(code),
    subtype_code TEXT NOT NULL REFERENCES clean_work_subtypes(code),
    name TEXT NOT NULL,
    default_pricing_method_code TEXT NOT NULL REFERENCES clean_pricing_methods(code),
    display_order INTEGER NOT NULL,
    UNIQUE (subtype_code, display_order)
);

CREATE TABLE clean_work_activity_pricing_methods (
    activity_code TEXT NOT NULL REFERENCES clean_work_activities(code) ON DELETE CASCADE,
    pricing_method_code TEXT NOT NULL REFERENCES clean_pricing_methods(code),
    is_system_default BOOLEAN NOT NULL DEFAULT FALSE,
    PRIMARY KEY (activity_code, pricing_method_code)
);

CREATE UNIQUE INDEX clean_one_default_pricing_method_per_activity
    ON clean_work_activity_pricing_methods(activity_code)
    WHERE is_system_default;

CREATE TABLE clean_tenant_activity_pricing (
    company_id UUID NOT NULL REFERENCES clean_companies(id) ON DELETE CASCADE,
    activity_code TEXT NOT NULL REFERENCES clean_work_activities(code),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    selected_pricing_method_code TEXT NOT NULL REFERENCES clean_pricing_methods(code),
    rate NUMERIC(12, 2),
    custom_name TEXT,
    enabled_additional_charge_codes TEXT[] NOT NULL DEFAULT '{}',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (company_id, activity_code)
);

CREATE TABLE clean_clients (
    id UUID PRIMARY KEY,
    company_id UUID NOT NULL REFERENCES clean_companies(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    preferred_language_code TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    data JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE clean_jobs (
    id UUID PRIMARY KEY,
    company_id UUID NOT NULL REFERENCES clean_companies(id) ON DELETE CASCADE,
    client_id UUID REFERENCES clean_clients(id),
    name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    data JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE clean_tasks (
    id UUID PRIMARY KEY,
    company_id UUID NOT NULL REFERENCES clean_companies(id) ON DELETE CASCADE,
    job_id UUID REFERENCES clean_jobs(id),
    name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    data JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE clean_quotes (
    id UUID PRIMARY KEY,
    company_id UUID NOT NULL REFERENCES clean_companies(id) ON DELETE CASCADE,
    client_id UUID REFERENCES clean_clients(id),
    job_id UUID REFERENCES clean_jobs(id),
    name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft',
    data JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE clean_invoices (
    id UUID PRIMARY KEY,
    company_id UUID NOT NULL REFERENCES clean_companies(id) ON DELETE CASCADE,
    client_id UUID REFERENCES clean_clients(id),
    quote_id UUID REFERENCES clean_quotes(id),
    name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft',
    data JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE clean_communications (
    id UUID PRIMARY KEY,
    company_id UUID NOT NULL REFERENCES clean_companies(id) ON DELETE CASCADE,
    client_id UUID REFERENCES clean_clients(id),
    name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'logged',
    data JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE clean_work_reports (
    id UUID PRIMARY KEY,
    company_id UUID NOT NULL REFERENCES clean_companies(id) ON DELETE CASCADE,
    job_id UUID REFERENCES clean_jobs(id),
    name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft',
    data JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE clean_voice_command_logs (
    id UUID PRIMARY KEY,
    company_id UUID NOT NULL REFERENCES clean_companies(id) ON DELETE CASCADE,
    user_id UUID REFERENCES clean_users(id),
    utterance TEXT NOT NULL,
    resolved_intent TEXT,
    confidence NUMERIC(5, 4) NOT NULL DEFAULT 0,
    executed BOOLEAN NOT NULL DEFAULT FALSE,
    requires_confirmation BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
