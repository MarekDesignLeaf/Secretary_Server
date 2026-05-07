-- Migration 023: Normalize language codes + fix industry group names
-- Safe read-only migrations only (no TRUNCATE, no migration_log edits)
-- Activity re-seed is handled by Python self-heal in main.py

BEGIN;

-- 1. Normalize language codes in tenant_languages
UPDATE crm.tenant_languages SET language_code = 'cs-CZ' WHERE language_code IN ('cs','cs_CZ','cs_cz');
UPDATE crm.tenant_languages SET language_code = 'pl-PL' WHERE language_code IN ('pl','pl_PL','pl_pl');
UPDATE crm.tenant_languages SET language_code = 'de-DE' WHERE language_code IN ('de','de_DE','de_de');
UPDATE crm.tenant_languages SET language_code = 'fr-FR' WHERE language_code IN ('fr','fr_FR','fr_fr');
UPDATE crm.tenant_languages SET language_code = 'es-ES' WHERE language_code IN ('es','es_ES','es_es');
UPDATE crm.tenant_languages SET language_code = 'sk-SK' WHERE language_code IN ('sk','sk_SK','sk_sk');
UPDATE crm.tenant_languages SET language_code = 'hu-HU' WHERE language_code IN ('hu','hu_HU','hu_hu');
UPDATE crm.tenant_languages SET language_code = 'ro-RO' WHERE language_code IN ('ro','ro_RO','ro_ro');
UPDATE crm.tenant_languages SET language_code = 'uk-UA' WHERE language_code IN ('uk','uk_UA','uk_ua');
UPDATE crm.tenant_languages SET language_code = 'ru-RU' WHERE language_code IN ('ru','ru_RU','ru_ru');
UPDATE crm.tenant_languages SET language_code = 'en-GB'
    WHERE language_code IN ('en','en_GB','en_gb') AND language_scope IN ('customer','voice_output','internal','voice_input');

-- 2. Normalize tenant_operating_profile default codes
UPDATE crm.tenant_operating_profile SET
    default_internal_language_code = CASE default_internal_language_code
        WHEN 'cs' THEN 'cs-CZ' WHEN 'en' THEN 'en-GB' WHEN 'pl' THEN 'pl-PL'
        WHEN 'de' THEN 'de-DE' WHEN 'sk' THEN 'sk-SK' ELSE default_internal_language_code
    END,
    default_customer_language_code = CASE default_customer_language_code
        WHEN 'en' THEN 'en-GB' WHEN 'cs' THEN 'cs-CZ' WHEN 'pl' THEN 'pl-PL'
        ELSE default_customer_language_code
    END;

-- 3. Fix stale industry group names left over from old DesignLeaf schema
UPDATE crm.industry_groups SET name='Trades and field services',  sort_order=10  WHERE code='trades';
UPDATE crm.industry_groups SET name='Construction and building',  sort_order=20  WHERE code='construction';
UPDATE crm.industry_groups SET name='Property management',        sort_order=30  WHERE code='property';
UPDATE crm.industry_groups SET name='Real estate and lettings',   sort_order=40  WHERE code='real_estate';
UPDATE crm.industry_groups SET name='Cleaning services',          sort_order=50  WHERE code='cleaning';
UPDATE crm.industry_groups SET name='Automotive services',        sort_order=60  WHERE code='automotive';
UPDATE crm.industry_groups SET name='Logistics and transport',    sort_order=70  WHERE code='logistics';
UPDATE crm.industry_groups SET name='Beauty and personal care',   sort_order=80  WHERE code='beauty';
UPDATE crm.industry_groups SET name='Healthcare and wellbeing',   sort_order=90  WHERE code='healthcare';
UPDATE crm.industry_groups SET name='Fitness and coaching',       sort_order=100 WHERE code='fitness';
UPDATE crm.industry_groups SET name='Hospitality and food service',sort_order=110 WHERE code='hospitality';
UPDATE crm.industry_groups SET name='Events and entertainment',   sort_order=120 WHERE code='events';
UPDATE crm.industry_groups SET name='Education and training',     sort_order=130 WHERE code='education';
UPDATE crm.industry_groups SET name='IT and technology',          sort_order=140 WHERE code='it_tech';
UPDATE crm.industry_groups SET name='Retail and e-commerce',      sort_order=150 WHERE code='retail';
UPDATE crm.industry_groups SET name='Security services',          sort_order=160 WHERE code='security';
UPDATE crm.industry_groups SET name='Agriculture and farming',    sort_order=170 WHERE code='agriculture';
UPDATE crm.industry_groups SET name='Other / General business',   sort_order=999 WHERE code='other';
-- Deactivate legacy standalone 'landscaping' group (it is now a subtype under trades)
UPDATE crm.industry_groups SET is_active=false WHERE code IN ('landscaping','it_services');

-- 4. Ensure landscaping + other missing trade subtypes exist under trades group
INSERT INTO crm.industry_subtypes (industry_group_id, code, name, sort_order, is_active)
SELECT g.id, s.code, s.name, s.sort_order, true
FROM crm.industry_groups g,
(VALUES
    ('landscaping','Landscaping and garden services',10),
    ('grounds_maintenance','Grounds and parks maintenance',20),
    ('tree_surgery','Tree surgery and arboriculture',30),
    ('fencing','Fencing and gate installation',40),
    ('drainage_groundworks','Drainage and groundworks',50),
    ('paving_patios','Paving, patios and driveways',60),
    ('building_maintenance','Building maintenance and repairs',70),
    ('painting_decorating','Painting and decorating',80),
    ('electrical','Electrical installations and repairs',90),
    ('plumbing_heating','Plumbing and heating',100),
    ('carpentry_joinery','Carpentry and joinery',130),
    ('roofing_guttering','Roofing and guttering',140),
    ('flooring_tiling','Flooring and tiling',150),
    ('handyman','Handyman and general maintenance',240)
) AS s(code, name, sort_order)
WHERE g.code = 'trades'
ON CONFLICT (industry_group_id, code) DO NOTHING;

COMMIT;
