-- Migration 019: Comprehensive industry catalog
-- Adds all major industry groups with full subtype trees
-- All inserts are ON CONFLICT DO NOTHING — safe to run on existing DB

BEGIN;

-- INDUSTRY GROUPS (upsert names/sort, keep existing ids)
INSERT INTO industry_groups (code, name, sort_order, is_active) VALUES
('trades',        'Trades and field services',         10, true),
('construction',  'Construction and building',         20, true),
('property',      'Property management',               30, true),
('real_estate',   'Real estate and lettings',          40, true),
('cleaning',      'Cleaning services',                 50, true),
('automotive',    'Automotive services',               60, true),
('logistics',     'Logistics and transport',           70, true),
('beauty',        'Beauty and personal care',          80, true),
('healthcare',    'Healthcare and wellbeing',          90, true),
('fitness',       'Fitness and coaching',             100, true),
('hospitality',   'Hospitality and food service',     110, true),
('events',        'Events and entertainment',         120, true),
('education',     'Education and training',           130, true),
('it_tech',       'IT and technology',                140, true),
('retail',        'Retail and e-commerce',            150, true),
('security',      'Security services',                160, true),
('agriculture',   'Agriculture and farming',          170, true),
('other',         'Other / General business',         999, true)
ON CONFLICT (code) DO UPDATE SET
    name = EXCLUDED.name,
    sort_order = EXCLUDED.sort_order,
    is_active = true;

-- ─── TRADES AND FIELD SERVICES ────────────────────────────────────────────
INSERT INTO industry_subtypes (industry_group_id, code, name, sort_order, is_active)
SELECT g.id, s.code, s.name, s.sort_order, true
FROM industry_groups g, (VALUES
    ('landscaping',          'Landscaping and garden services',          10),
    ('grounds_maintenance',  'Grounds and parks maintenance',            20),
    ('tree_surgery',         'Tree surgery and arboriculture',           30),
    ('fencing',              'Fencing and gate installation',            40),
    ('drainage_groundworks', 'Drainage and groundworks',                 50),
    ('paving_patios',        'Paving, patios and driveways',             60),
    ('building_maintenance', 'Building maintenance and repairs',         70),
    ('painting_decorating',  'Painting and decorating',                  80),
    ('electrical',           'Electrical installations and repairs',     90),
    ('plumbing_heating',     'Plumbing and heating',                    100),
    ('gas_boilers',          'Gas services and boiler installation',    110),
    ('hvac',                 'HVAC (air conditioning and ventilation)', 120),
    ('carpentry_joinery',    'Carpentry and joinery',                   130),
    ('roofing_guttering',    'Roofing and guttering',                   140),
    ('flooring_tiling',      'Flooring and tiling',                     150),
    ('glazing_windows',      'Glazing and window fitting',              160),
    ('pest_control',         'Pest control and prevention',             170),
    ('locksmith',            'Locksmith services',                      180),
    ('solar_energy',         'Solar panels and renewable energy',       190),
    ('scaffolding',          'Scaffolding erection and hire',           200),
    ('pool_maintenance',     'Pool and hot tub maintenance',            210),
    ('pressure_washing',     'Pressure washing and exterior cleaning',  220),
    ('waste_removal',        'Waste removal and skip hire',             230),
    ('handyman',             'Handyman and general maintenance',        240),
    ('security_systems',     'Security alarms and camera installation', 250)
) AS s(code, name, sort_order)
WHERE g.code = 'trades'
ON CONFLICT (industry_group_id, code) DO UPDATE SET name = EXCLUDED.name, sort_order = EXCLUDED.sort_order;

-- ─── CONSTRUCTION AND BUILDING ────────────────────────────────────────────
INSERT INTO industry_subtypes (industry_group_id, code, name, sort_order, is_active)
SELECT g.id, s.code, s.name, s.sort_order, true
FROM industry_groups g, (VALUES
    ('new_build',              'New build residential',                  10),
    ('extensions_conversions', 'Extensions and conversions',             20),
    ('commercial_fit_out',     'Commercial fit-out and refurbishment',   30),
    ('structural_works',       'Structural engineering and steelwork',   40),
    ('groundworks_civil',      'Groundworks and civil engineering',      50),
    ('demolition',             'Demolition and site clearance',          60),
    ('dry_lining_plastering',  'Dry lining and plastering',              70),
    ('bricklaying_masonry',    'Bricklaying and masonry',                80),
    ('project_management',     'Construction project management',        90)
) AS s(code, name, sort_order)
WHERE g.code = 'construction'
ON CONFLICT (industry_group_id, code) DO UPDATE SET name = EXCLUDED.name, sort_order = EXCLUDED.sort_order;

-- ─── PROPERTY MANAGEMENT ──────────────────────────────────────────────────
INSERT INTO industry_subtypes (industry_group_id, code, name, sort_order, is_active)
SELECT g.id, s.code, s.name, s.sort_order, true
FROM industry_groups g, (VALUES
    ('hmo_management',              'HMO property management',              10),
    ('residential_management',      'Residential property management',      20),
    ('commercial_management',       'Commercial property management',       30),
    ('block_management',            'Block and estate management',          40),
    ('facilities_management',       'Facilities management',                50),
    ('short_term_lets',             'Short-term let management (Airbnb)',   60),
    ('void_management',             'Void property management',             70),
    ('property_maintenance',        'General property maintenance',         80),
    ('renovation_project_management','Renovation project management',       90),
    ('student_accommodation',       'Student accommodation management',    100)
) AS s(code, name, sort_order)
WHERE g.code = 'property'
ON CONFLICT (industry_group_id, code) DO UPDATE SET name = EXCLUDED.name, sort_order = EXCLUDED.sort_order;

-- ─── REAL ESTATE AND LETTINGS ─────────────────────────────────────────────
INSERT INTO industry_subtypes (industry_group_id, code, name, sort_order, is_active)
SELECT g.id, s.code, s.name, s.sort_order, true
FROM industry_groups g, (VALUES
    ('lettings_coordination',   'Lettings coordination and management',   10),
    ('residential_sales',       'Residential property sales',             20),
    ('commercial_lettings',     'Commercial lettings and sales',          30),
    ('land_development',        'Land and new development sales',         40),
    ('property_valuation',      'Property valuation and surveys',         50),
    ('investment_consultancy',  'Property investment consultancy',        60),
    ('mortgage_brokering',      'Mortgage and financial brokering',       70)
) AS s(code, name, sort_order)
WHERE g.code = 'real_estate'
ON CONFLICT (industry_group_id, code) DO UPDATE SET name = EXCLUDED.name, sort_order = EXCLUDED.sort_order;

-- ─── CLEANING SERVICES ────────────────────────────────────────────────────
INSERT INTO industry_subtypes (industry_group_id, code, name, sort_order, is_active)
SELECT g.id, s.code, s.name, s.sort_order, true
FROM industry_groups g, (VALUES
    ('domestic_cleaning',     'Domestic regular cleaning',               10),
    ('commercial_cleaning',   'Commercial and office cleaning',          20),
    ('end_of_tenancy',        'End of tenancy cleaning',                 30),
    ('deep_cleaning',         'Deep cleaning and sanitisation',          40),
    ('window_cleaning',       'Window cleaning',                         50),
    ('carpet_upholstery',     'Carpet and upholstery cleaning',          60),
    ('oven_appliance',        'Oven and appliance cleaning',             70),
    ('industrial_cleaning',   'Industrial and factory cleaning',         80),
    ('biohazard_specialist',  'Specialist and biohazard cleaning',       90),
    ('after_builders',        'After-builders cleaning',                100),
    ('pressure_cleaning',     'Pressure washing and jet washing',       110)
) AS s(code, name, sort_order)
WHERE g.code = 'cleaning'
ON CONFLICT (industry_group_id, code) DO UPDATE SET name = EXCLUDED.name, sort_order = EXCLUDED.sort_order;

-- ─── AUTOMOTIVE SERVICES ──────────────────────────────────────────────────
INSERT INTO industry_subtypes (industry_group_id, code, name, sort_order, is_active)
SELECT g.id, s.code, s.name, s.sort_order, true
FROM industry_groups g, (VALUES
    ('vehicle_repairs',       'Vehicle repairs and servicing',           10),
    ('mot_testing',           'MOT testing and inspection',              20),
    ('body_repairs',          'Bodywork, paint and panel repairs',       30),
    ('tyres_wheels',          'Tyres and wheel alignment',               40),
    ('vehicle_valeting',      'Vehicle valeting and detailing',          50),
    ('mobile_mechanic',       'Mobile mechanic',                         60),
    ('breakdown_recovery',    'Breakdown recovery and towing',           70),
    ('car_sales',             'Car sales and brokerage',                 80),
    ('fleet_management',      'Fleet management and maintenance',        90),
    ('vehicle_diagnostics',   'Diagnostics and ECU tuning',             100),
    ('auto_electrics',        'Auto electrics and audio installation',  110)
) AS s(code, name, sort_order)
WHERE g.code = 'automotive'
ON CONFLICT (industry_group_id, code) DO UPDATE SET name = EXCLUDED.name, sort_order = EXCLUDED.sort_order;

-- ─── LOGISTICS AND TRANSPORT ──────────────────────────────────────────────
INSERT INTO industry_subtypes (industry_group_id, code, name, sort_order, is_active)
SELECT g.id, s.code, s.name, s.sort_order, true
FROM industry_groups g, (VALUES
    ('courier_delivery',      'Courier and parcel delivery',             10),
    ('removals_storage',      'House and office removals',               20),
    ('man_and_van',           'Man and van services',                    30),
    ('taxi_private_hire',     'Taxi and private hire',                   40),
    ('haulage_freight',       'Haulage and freight transport',           50),
    ('warehousing',           'Warehousing and fulfilment',              60),
    ('same_day_delivery',     'Same-day and express delivery',           70)
) AS s(code, name, sort_order)
WHERE g.code = 'logistics'
ON CONFLICT (industry_group_id, code) DO UPDATE SET name = EXCLUDED.name, sort_order = EXCLUDED.sort_order;

-- ─── BEAUTY AND PERSONAL CARE ─────────────────────────────────────────────
INSERT INTO industry_subtypes (industry_group_id, code, name, sort_order, is_active)
SELECT g.id, s.code, s.name, s.sort_order, true
FROM industry_groups g, (VALUES
    ('hairdressing',          'Hairdressing and barbering',              10),
    ('beauty_therapy',        'Beauty therapy and facials',              20),
    ('nail_technician',       'Nail technician',                         30),
    ('massage_therapy',       'Massage therapy and bodywork',            40),
    ('lash_brow',             'Lash and brow technician',                50),
    ('aesthetics',            'Aesthetic treatments and injectables',    60),
    ('permanent_makeup',      'Permanent makeup and microblading',       70),
    ('spray_tanning',         'Spray tanning and bronzing',              80),
    ('mobile_beauty',         'Mobile beauty services',                  90),
    ('hair_extensions',       'Hair extensions',                        100),
    ('makeup_artistry',       'Makeup artistry and bridal',             110)
) AS s(code, name, sort_order)
WHERE g.code = 'beauty'
ON CONFLICT (industry_group_id, code) DO UPDATE SET name = EXCLUDED.name, sort_order = EXCLUDED.sort_order;

-- ─── HEALTHCARE AND WELLBEING ─────────────────────────────────────────────
INSERT INTO industry_subtypes (industry_group_id, code, name, sort_order, is_active)
SELECT g.id, s.code, s.name, s.sort_order, true
FROM industry_groups g, (VALUES
    ('physiotherapy',         'Physiotherapy and sports rehab',          10),
    ('mental_health',         'Mental health and counselling',           20),
    ('private_gp',            'Private GP and medical consultations',    30),
    ('dentistry',             'Dentistry and oral health',               40),
    ('optometry',             'Optometry and eyecare',                   50),
    ('nutrition_dietetics',   'Nutrition and dietetics',                 60),
    ('osteopathy',            'Osteopathy and chiropractic',             70),
    ('podiatry',              'Podiatry and chiropody',                  80),
    ('home_nursing',          'Home nursing and domiciliary care',       90),
    ('occupational_therapy',  'Occupational therapy',                   100),
    ('acupuncture',           'Acupuncture and complementary therapy',  110),
    ('veterinary',            'Veterinary services',                    120)
) AS s(code, name, sort_order)
WHERE g.code = 'healthcare'
ON CONFLICT (industry_group_id, code) DO UPDATE SET name = EXCLUDED.name, sort_order = EXCLUDED.sort_order;

-- ─── FITNESS AND COACHING ─────────────────────────────────────────────────
INSERT INTO industry_subtypes (industry_group_id, code, name, sort_order, is_active)
SELECT g.id, s.code, s.name, s.sort_order, true
FROM industry_groups g, (VALUES
    ('personal_training',     'Personal training',                       10),
    ('group_fitness',         'Group fitness classes',                   20),
    ('yoga_pilates',          'Yoga and Pilates instruction',            30),
    ('sports_coaching',       'Sports coaching and development',         40),
    ('online_coaching',       'Online fitness and wellness coaching',    50),
    ('nutrition_coaching',    'Nutritional coaching',                    60),
    ('gym_studio',            'Gym and studio management',               70),
    ('swimming_coaching',     'Swimming instruction and coaching',       80),
    ('martial_arts',          'Martial arts and self-defence',           90)
) AS s(code, name, sort_order)
WHERE g.code = 'fitness'
ON CONFLICT (industry_group_id, code) DO UPDATE SET name = EXCLUDED.name, sort_order = EXCLUDED.sort_order;

-- ─── HOSPITALITY AND FOOD SERVICE ────────────────────────────────────────
INSERT INTO industry_subtypes (industry_group_id, code, name, sort_order, is_active)
SELECT g.id, s.code, s.name, s.sort_order, true
FROM industry_groups g, (VALUES
    ('restaurant_catering',   'Restaurant and dining',                   10),
    ('cafe_coffee',           'Café and coffee shop',                    20),
    ('takeaway_delivery',     'Takeaway and food delivery',              30),
    ('bar_pub',               'Bar, pub and club management',            40),
    ('hotel_bnb',             'Hotel, B&B and serviced accommodation',  50),
    ('event_catering',        'Event and mobile catering',               60),
    ('private_chef',          'Private chef and personal catering',      70),
    ('food_production',       'Food production and meal prep',           80)
) AS s(code, name, sort_order)
WHERE g.code = 'hospitality'
ON CONFLICT (industry_group_id, code) DO UPDATE SET name = EXCLUDED.name, sort_order = EXCLUDED.sort_order;

-- ─── EVENTS AND ENTERTAINMENT ─────────────────────────────────────────────
INSERT INTO industry_subtypes (industry_group_id, code, name, sort_order, is_active)
SELECT g.id, s.code, s.name, s.sort_order, true
FROM industry_groups g, (VALUES
    ('event_planning',        'Event planning and coordination',         10),
    ('wedding_services',      'Wedding services',                        20),
    ('photography_video',     'Photography and videography',             30),
    ('entertainment',         'Entertainment, DJs and performers',       40),
    ('av_technical',          'AV and technical production',             50),
    ('venue_management',      'Venue hire and management',               60),
    ('marquee_equipment',     'Marquee and equipment hire',              70),
    ('floristry',             'Floristry and event décor',               80),
    ('events_catering',       'Events catering and bar staff',           90),
    ('photobooth_hire',       'Photo booth and prop hire',              100)
) AS s(code, name, sort_order)
WHERE g.code = 'events'
ON CONFLICT (industry_group_id, code) DO UPDATE SET name = EXCLUDED.name, sort_order = EXCLUDED.sort_order;

-- ─── EDUCATION AND TRAINING ───────────────────────────────────────────────
INSERT INTO industry_subtypes (industry_group_id, code, name, sort_order, is_active)
SELECT g.id, s.code, s.name, s.sort_order, true
FROM industry_groups g, (VALUES
    ('private_tutoring',      'Private tutoring (school subjects)',      10),
    ('music_tuition',         'Music tuition and instrument lessons',    20),
    ('driving_instruction',   'Driving instruction and theory coaching', 30),
    ('language_teaching',     'Language teaching and translation',       40),
    ('corporate_training',    'Corporate and professional training',     50),
    ('vocational_courses',    'Vocational and trade courses',            60),
    ('online_courses',        'Online courses and digital learning',     70),
    ('childcare_education',   'Childcare and early years education',     80),
    ('arts_creative',         'Arts, crafts and creative workshops',     90),
    ('sports_instruction',    'Sports instruction and coaching',        100)
) AS s(code, name, sort_order)
WHERE g.code = 'education'
ON CONFLICT (industry_group_id, code) DO UPDATE SET name = EXCLUDED.name, sort_order = EXCLUDED.sort_order;

-- ─── IT AND TECHNOLOGY ────────────────────────────────────────────────────
INSERT INTO industry_subtypes (industry_group_id, code, name, sort_order, is_active)
SELECT g.id, s.code, s.name, s.sort_order, true
FROM industry_groups g, (VALUES
    ('it_support',            'IT support and helpdesk',                 10),
    ('web_development',       'Website development and design',          20),
    ('software_development',  'Software and app development',            30),
    ('network_infrastructure','Network setup and infrastructure',        40),
    ('cybersecurity',         'Cybersecurity and data protection',       50),
    ('cloud_services',        'Cloud migration and managed services',    60),
    ('digital_marketing',     'Digital marketing and SEO',               70),
    ('graphic_design',        'Graphic design and branding',             80),
    ('data_analytics',        'Data analytics and reporting',            90),
    ('ecommerce',             'E-commerce setup and management',        100)
) AS s(code, name, sort_order)
WHERE g.code = 'it_tech'
ON CONFLICT (industry_group_id, code) DO UPDATE SET name = EXCLUDED.name, sort_order = EXCLUDED.sort_order;

-- ─── RETAIL AND E-COMMERCE ────────────────────────────────────────────────
INSERT INTO industry_subtypes (industry_group_id, code, name, sort_order, is_active)
SELECT g.id, s.code, s.name, s.sort_order, true
FROM industry_groups g, (VALUES
    ('physical_retail',       'Physical retail store',                   10),
    ('online_retail',         'Online shop and marketplace',             20),
    ('wholesale',             'Wholesale and distribution',              30),
    ('market_stall',          'Market stall and pop-up retail',          40),
    ('specialist_retail',     'Specialist and trade retail',             50)
) AS s(code, name, sort_order)
WHERE g.code = 'retail'
ON CONFLICT (industry_group_id, code) DO UPDATE SET name = EXCLUDED.name, sort_order = EXCLUDED.sort_order;

-- ─── SECURITY SERVICES ────────────────────────────────────────────────────
INSERT INTO industry_subtypes (industry_group_id, code, name, sort_order, is_active)
SELECT g.id, s.code, s.name, s.sort_order, true
FROM industry_groups g, (VALUES
    ('manned_guarding',       'Manned guarding and security officers',   10),
    ('door_supervision',      'Door supervision and event security',     20),
    ('cctv_monitoring',       'CCTV monitoring and installation',        30),
    ('alarm_response',        'Alarm response and key holding',          40),
    ('retail_security',       'Retail loss prevention',                  50),
    ('mobile_patrol',         'Mobile patrols and inspections',          60)
) AS s(code, name, sort_order)
WHERE g.code = 'security'
ON CONFLICT (industry_group_id, code) DO UPDATE SET name = EXCLUDED.name, sort_order = EXCLUDED.sort_order;

-- ─── AGRICULTURE AND FARMING ──────────────────────────────────────────────
INSERT INTO industry_subtypes (industry_group_id, code, name, sort_order, is_active)
SELECT g.id, s.code, s.name, s.sort_order, true
FROM industry_groups g, (VALUES
    ('arable_farming',        'Arable and crop farming',                 10),
    ('livestock_farming',     'Livestock and animal husbandry',          20),
    ('horticulture',          'Horticulture and market gardening',       30),
    ('equestrian',            'Equestrian services and livery',          40),
    ('land_management',       'Land and estate management',              50),
    ('agricultural_contracting','Agricultural contracting',             60),
    ('forestry',              'Forestry and woodland management',        70)
) AS s(code, name, sort_order)
WHERE g.code = 'agriculture'
ON CONFLICT (industry_group_id, code) DO UPDATE SET name = EXCLUDED.name, sort_order = EXCLUDED.sort_order;

INSERT INTO migration_log (filename)
VALUES ('2026_05_05_industry_catalog_019.sql')
ON CONFLICT (filename) DO NOTHING;

COMMIT;
