-- Migration 023: Normalize language codes + force activity_templates re-seed
-- Fixes: short codes (cs/en/pl) -> full BCP-47 (cs-CZ/en-GB/pl-PL)
-- Fixes: activity_templates only has 19 entries (electrical), missing all other subtypes
-- Safe to run multiple times (ON CONFLICT clauses)

BEGIN;

-- ─── 1. NORMALIZE LANGUAGE CODES ──────────────────────────────────────────
-- tenant_languages: upgrade 2-letter codes to full BCP-47 locale codes
UPDATE crm.tenant_languages SET language_code = 'cs-CZ' WHERE language_code IN ('cs', 'cs_CZ');
UPDATE crm.tenant_languages SET language_code = 'en-GB' WHERE language_code IN ('en', 'en_GB') AND language_scope IN ('customer', 'voice_output');
UPDATE crm.tenant_languages SET language_code = 'en-US' WHERE language_code IN ('en_US');
UPDATE crm.tenant_languages SET language_code = 'pl-PL' WHERE language_code IN ('pl', 'pl_PL');
UPDATE crm.tenant_languages SET language_code = 'de-DE' WHERE language_code IN ('de', 'de_DE');
UPDATE crm.tenant_languages SET language_code = 'fr-FR' WHERE language_code IN ('fr', 'fr_FR');
UPDATE crm.tenant_languages SET language_code = 'es-ES' WHERE language_code IN ('es', 'es_ES');
UPDATE crm.tenant_languages SET language_code = 'sk-SK' WHERE language_code IN ('sk', 'sk_SK');
UPDATE crm.tenant_languages SET language_code = 'hu-HU' WHERE language_code IN ('hu', 'hu_HU');
UPDATE crm.tenant_languages SET language_code = 'ro-RO' WHERE language_code IN ('ro', 'ro_RO');
UPDATE crm.tenant_languages SET language_code = 'uk-UA' WHERE language_code IN ('uk', 'uk_UA');
UPDATE crm.tenant_languages SET language_code = 'ru-RU' WHERE language_code IN ('ru', 'ru_RU');
-- Also for 'en' used as internal language (should be en-GB)
UPDATE crm.tenant_languages SET language_code = 'en-GB' WHERE language_code = 'en' AND language_scope IN ('internal', 'voice_input');

-- tenant_operating_profile: normalize default language codes
UPDATE crm.tenant_operating_profile SET
    default_internal_language_code = CASE
        WHEN default_internal_language_code = 'cs' THEN 'cs-CZ'
        WHEN default_internal_language_code = 'en' THEN 'en-GB'
        WHEN default_internal_language_code = 'pl' THEN 'pl-PL'
        WHEN default_internal_language_code = 'de' THEN 'de-DE'
        WHEN default_internal_language_code = 'sk' THEN 'sk-SK'
        ELSE default_internal_language_code
    END,
    default_customer_language_code = CASE
        WHEN default_customer_language_code = 'en' THEN 'en-GB'
        WHEN default_customer_language_code = 'cs' THEN 'cs-CZ'
        WHEN default_customer_language_code = 'pl' THEN 'pl-PL'
        ELSE default_customer_language_code
    END;

-- ─── 2. FIX INDUSTRY GROUPS (correct old DesignLeaf names) ─────────────────
UPDATE crm.industry_groups SET name = 'Trades and field services', sort_order = 10  WHERE code = 'trades';
UPDATE crm.industry_groups SET name = 'Construction and building', sort_order = 20  WHERE code = 'construction';
UPDATE crm.industry_groups SET name = 'Property management',       sort_order = 30  WHERE code = 'property';
UPDATE crm.industry_groups SET name = 'Real estate and lettings',  sort_order = 40  WHERE code = 'real_estate';
UPDATE crm.industry_groups SET name = 'Cleaning services',         sort_order = 50  WHERE code = 'cleaning';
UPDATE crm.industry_groups SET name = 'Automotive services',       sort_order = 60  WHERE code = 'automotive';
UPDATE crm.industry_groups SET name = 'Logistics and transport',   sort_order = 70  WHERE code = 'logistics';
UPDATE crm.industry_groups SET name = 'Beauty and personal care',  sort_order = 80  WHERE code = 'beauty';
UPDATE crm.industry_groups SET name = 'Healthcare and wellbeing',  sort_order = 90  WHERE code = 'healthcare';
UPDATE crm.industry_groups SET name = 'Fitness and coaching',      sort_order = 100 WHERE code = 'fitness';
UPDATE crm.industry_groups SET name = 'Hospitality and food service', sort_order = 110 WHERE code = 'hospitality';
UPDATE crm.industry_groups SET name = 'Events and entertainment',  sort_order = 120 WHERE code = 'events';
UPDATE crm.industry_groups SET name = 'Education and training',    sort_order = 130 WHERE code = 'education';
UPDATE crm.industry_groups SET name = 'IT and technology',         sort_order = 140 WHERE code = 'it_tech';
UPDATE crm.industry_groups SET name = 'Retail and e-commerce',     sort_order = 150 WHERE code = 'retail';
UPDATE crm.industry_groups SET name = 'Security services',         sort_order = 160 WHERE code = 'security';
UPDATE crm.industry_groups SET name = 'Agriculture and farming',   sort_order = 170 WHERE code = 'agriculture';
UPDATE crm.industry_groups SET name = 'Other / General business',  sort_order = 999 WHERE code = 'other';

-- Deactivate the legacy top-level 'landscaping' group (it became a subtype of trades)
UPDATE crm.industry_groups SET is_active = false WHERE code = 'landscaping';
-- Same for other old legacy groups that were replaced
UPDATE crm.industry_groups SET is_active = false WHERE code IN (
    'it_services', 'gardening', 'garden', 'landscape'
);

-- ─── 3. ENSURE 'landscaping' EXISTS AS SUBTYPE OF 'trades' ──────────────────
INSERT INTO crm.industry_subtypes (industry_group_id, code, name, sort_order, is_active)
SELECT g.id, 'landscaping', 'Landscaping and garden services', 10, true
FROM crm.industry_groups g WHERE g.code = 'trades'
ON CONFLICT (industry_group_id, code) DO NOTHING;

-- ─── 4. FORCE ACTIVITY TEMPLATES RE-SEED (delete 020 from migration_log) ────
-- This causes seed_activity_templates() to re-run migration 020 SQL on next startup
-- which will now find all subtypes correctly
DELETE FROM crm.migration_log WHERE filename = '2026_05_06_activity_templates_020.sql';
-- Also delete any partial activity_templates data to allow clean re-insert
-- (ON CONFLICT DO NOTHING in migration 020 would just skip, so we clear first)
TRUNCATE crm.activity_templates RESTART IDENTITY CASCADE;

COMMIT;
