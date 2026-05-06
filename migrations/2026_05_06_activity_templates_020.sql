-- Migration 020: Activity templates seed (auto-generated)
-- Generated from secretary_work_types_tree_pricing_logic.txt

BEGIN;

CREATE TABLE IF NOT EXISTS crm.activity_templates (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    industry_group_id BIGINT REFERENCES crm.industry_groups(id) ON DELETE SET NULL,
    industry_subtype_id BIGINT REFERENCES crm.industry_subtypes(id) ON DELETE SET NULL,
    code TEXT NOT NULL,
    name TEXT NOT NULL,
    default_pricing_method TEXT NOT NULL DEFAULT 'hourly',
    allowed_pricing_methods TEXT[] NOT NULL DEFAULT ARRAY['hourly','daily','per_visit','per_item','per_m2','per_m','per_m3','per_kg','per_tonne','per_bulk_bag','fixed','package','callout','travel','material','percentage','milestone','subscription'],
    default_unit TEXT,
    sort_order INT DEFAULT 0,
    is_active BOOLEAN DEFAULT true,
    is_builtin BOOLEAN DEFAULT true,
    created_at TIMESTAMPTZ DEFAULT now(),
    CONSTRAINT uq_activity_subtype_code UNIQUE (industry_subtype_id, code),
    CONSTRAINT uq_activity_group_only_code UNIQUE (industry_group_id, code)
);

CREATE TABLE IF NOT EXISTS crm.tenant_activity_pricing (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_id INT NOT NULL DEFAULT 1,
    activity_template_id BIGINT NOT NULL REFERENCES crm.activity_templates(id) ON DELETE CASCADE,
    is_active BOOLEAN DEFAULT true,
    pricing_method TEXT,
    rate NUMERIC(12,4),
    rate_secondary NUMERIC(12,4),
    custom_name TEXT,
    supplementary JSONB DEFAULT '{}',
    voice_aliases TEXT[] DEFAULT '{}',
    notes TEXT,
    updated_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE (tenant_id, activity_template_id)
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_activity_templates_subtype ON crm.activity_templates(industry_subtype_id);
CREATE INDEX IF NOT EXISTS idx_activity_templates_group ON crm.activity_templates(industry_group_id);
CREATE INDEX IF NOT EXISTS idx_tap_tenant ON crm.tenant_activity_pricing(tenant_id);
CREATE INDEX IF NOT EXISTS idx_tap_template ON crm.tenant_activity_pricing(activity_template_id);

-- Seed activity templates
-- 1.1 (landscaping)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='landscaping'
, (VALUES
    ('garden_maintenance', 'Garden maintenance', 'hourly', NULL, 10),
    ('weeding', 'Weeding', 'hourly', NULL, 20),
    ('border_maintenance', 'Border maintenance', 'hourly', NULL, 30),
    ('planting', 'Planting', 'hourly', NULL, 40),
    ('plant_supply', 'Plant supply', 'material', NULL, 50),
    ('plant_sourcing', 'Plant sourcing', 'hourly', NULL, 60),
    ('garden_clearance', 'Garden clearance', 'hourly', NULL, 70),
    ('seasonal_garden_cleanup', 'Seasonal garden cleanup', 'hourly', NULL, 80),
    ('spring_preparation', 'Spring preparation', 'hourly', NULL, 90),
    ('autumn_cleanup', 'Autumn cleanup', 'hourly', NULL, 100),
    ('leaf_clearance', 'Leaf clearance', 'hourly', NULL, 110),
    ('shrub_pruning', 'Shrub pruning', 'hourly', NULL, 120),
    ('flower_bed_maintenance', 'Flower bed maintenance', 'hourly', NULL, 130),
    ('flower_bed_redesign', 'Flower bed redesign', 'hourly', NULL, 140),
    ('garden_design_consultation', 'Garden design consultation', 'per_visit', NULL, 150),
    ('planting_design', 'Planting design', 'hourly', NULL, 160),
    ('soft_landscaping', 'Soft landscaping', 'hourly', NULL, 170),
    ('hard_landscaping_support', 'Hard landscaping support', 'hourly', NULL, 180),
    ('lawn_mowing', 'Lawn mowing', 'hourly', NULL, 190),
    ('strimming', 'Strimming', 'hourly', NULL, 200),
    ('lawn_edging', 'Lawn edging', 'per_m', 'm', 210),
    ('lawn_repair', 'Lawn repair', 'hourly', NULL, 220),
    ('lawn_renovation', 'Lawn renovation', 'hourly', NULL, 230),
    ('lawn_levelling', 'Lawn levelling', 'hourly', NULL, 240),
    ('turf_laying', 'Turf laying', 'per_m2', 'm2', 250),
    ('artificial_grass_installation', 'Artificial grass installation', 'per_m2', 'm2', 260),
    ('lawn_aeration', 'Lawn aeration', 'hourly', NULL, 270),
    ('lawn_scarifying', 'Lawn scarifying', 'hourly', NULL, 280),
    ('lawn_fertilising', 'Lawn fertilising', 'hourly', NULL, 290),
    ('top_dressing', 'Top dressing', 'hourly', NULL, 300),
    ('soil_improvement', 'Soil improvement', 'hourly', NULL, 310),
    ('compost_application', 'Compost application', 'hourly', NULL, 320),
    ('mulching', 'Mulching', 'per_m2', 'm2', 330),
    ('bark_installation', 'Bark installation', 'per_m2', 'm2', 340),
    ('decorative_gravel_installation', 'Decorative gravel installation', 'per_m2', 'm2', 350),
    ('raised_bed_installation', 'Raised bed installation', 'hourly', NULL, 360),
    ('sleeper_installation', 'Sleeper installation', 'hourly', NULL, 370),
    ('garden_edging', 'Garden edging', 'per_m', 'm', 380),
    ('steel_edging_installation', 'Steel edging installation', 'per_m', 'm', 390),
    ('timber_edging_installation', 'Timber edging installation', 'per_m', 'm', 400),
    ('stone_edging_installation', 'Stone edging installation', 'per_m', 'm', 410),
    ('path_preparation', 'Path preparation', 'hourly', NULL, 420),
    ('garden_pathway_installation', 'Garden pathway installation', 'hourly', NULL, 430),
    ('garden_steps', 'Garden steps', 'hourly', NULL, 440),
    ('garden_lighting_preparation', 'Garden lighting preparation', 'hourly', NULL, 450),
    ('low_voltage_garden_lighting_support', 'Low voltage garden lighting support', 'hourly', NULL, 460),
    ('irrigation_installation', 'Irrigation installation', 'hourly', NULL, 470),
    ('irrigation_repair', 'Irrigation repair', 'hourly', NULL, 480),
    ('sprinkler_installation', 'Sprinkler installation', 'hourly', NULL, 490),
    ('sprinkler_testing', 'Sprinkler testing', 'hourly', NULL, 500),
    ('watering_system_setup', 'Watering system setup', 'hourly', NULL, 510),
    ('drainage_improvement', 'Drainage improvement', 'hourly', NULL, 520),
    ('french_drain_installation', 'French drain installation', 'hourly', NULL, 530),
    ('soakaway_installation', 'Soakaway installation', 'hourly', NULL, 540),
    ('pond_installation', 'Pond installation', 'hourly', NULL, 550),
    ('pond_maintenance', 'Pond maintenance', 'hourly', NULL, 560),
    ('garden_structure_preparation', 'Garden structure preparation', 'hourly', NULL, 570),
    ('pergola_preparation', 'Pergola preparation', 'hourly', NULL, 580),
    ('decking_preparation', 'Decking preparation', 'hourly', NULL, 590),
    ('machine_work', 'Machine work', 'hourly', NULL, 600),
    ('mini_digger_work', 'Mini digger work', 'daily', NULL, 610),
    ('dumper_work', 'Dumper work', 'daily', NULL, 620),
    ('waste_loading', 'Waste loading', 'hourly', NULL, 630),
    ('garden_waste_removal', 'Garden waste removal', 'hourly', NULL, 640),
    ('site_visit', 'Site visit', 'per_visit', NULL, 650),
    ('garden_consultation', 'Garden consultation', 'per_visit', NULL, 660),
    ('project_management', 'Project management', 'fixed', NULL, 670),
    ('aftercare_visit', 'Aftercare visit', 'per_visit', NULL, 680),
    ('regular_maintenance_contract', 'Regular maintenance contract', 'subscription', NULL, 690)
) AS v(code, name, pm, unit, sort)
WHERE g.code='trades'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 1.2 (grounds_maintenance)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='grounds_maintenance'
, (VALUES
    ('grass_cutting', 'Grass cutting', 'hourly', NULL, 10),
    ('strimming', 'Strimming', 'hourly', NULL, 20),
    ('commercial_mowing', 'Commercial mowing', 'hourly', NULL, 30),
    ('hedge_trimming', 'Hedge trimming', 'per_m', 'm', 40),
    ('shrub_pruning', 'Shrub pruning', 'hourly', NULL, 50),
    ('tree_base_clearance', 'Tree base clearance', 'hourly', NULL, 60),
    ('leaf_clearance', 'Leaf clearance', 'hourly', NULL, 70),
    ('litter_picking', 'Litter picking', 'hourly', NULL, 80),
    ('weed_control', 'Weed control', 'hourly', NULL, 90),
    ('path_clearing', 'Path clearing', 'hourly', NULL, 100),
    ('car_park_vegetation_control', 'Car park vegetation control', 'hourly', NULL, 110),
    ('estate_grounds_inspection', 'Estate grounds inspection', 'per_visit', NULL, 120),
    ('school_grounds_maintenance', 'School grounds maintenance', 'hourly', NULL, 130),
    ('commercial_grounds_care', 'Commercial grounds care', 'hourly', NULL, 140),
    ('sports_field_maintenance', 'Sports field maintenance', 'hourly', NULL, 150),
    ('public_area_maintenance', 'Public area maintenance', 'hourly', NULL, 160),
    ('winter_grounds_maintenance', 'Winter grounds maintenance', 'hourly', NULL, 170),
    ('seasonal_maintenance', 'Seasonal maintenance', 'hourly', NULL, 180),
    ('grounds_condition_report', 'Grounds condition report', 'hourly', NULL, 190),
    ('invasive_weed_control', 'Invasive weed control', 'hourly', NULL, 200),
    ('brambles_removal', 'Brambles removal', 'hourly', NULL, 210),
    ('vegetation_reduction', 'Vegetation reduction', 'hourly', NULL, 220),
    ('boundary_clearance', 'Boundary clearance', 'hourly', NULL, 230),
    ('drainage_channel_clearance', 'Drainage channel clearance', 'hourly', NULL, 240),
    ('site_safety_inspection', 'Site safety inspection', 'per_visit', NULL, 250),
    ('regular_contract_visit', 'Regular contract visit', 'subscription', NULL, 260),
    ('emergency_clearance', 'Emergency clearance', 'hourly', NULL, 270),
    ('waste_collection', 'Waste collection', 'hourly', NULL, 280),
    ('green_waste_removal', 'Green waste removal', 'hourly', NULL, 290)
) AS v(code, name, pm, unit, sort)
WHERE g.code='trades'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 1.3 (tree_surgery)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='tree_surgery'
, (VALUES
    ('tree_inspection', 'Tree inspection', 'per_visit', NULL, 10),
    ('tree_health_assessment', 'Tree health assessment', 'per_visit', NULL, 20),
    ('tree_risk_assessment', 'Tree risk assessment', 'per_visit', NULL, 30),
    ('fruit_tree_pruning', 'Fruit tree pruning', 'hourly', NULL, 40),
    ('small_tree_pruning', 'Small tree pruning', 'hourly', NULL, 50),
    ('crown_reduction', 'Crown reduction', 'per_item', NULL, 60),
    ('crown_thinning', 'Crown thinning', 'per_item', NULL, 70),
    ('crown_lifting', 'Crown lifting', 'per_item', NULL, 80),
    ('dead_wood_removal', 'Dead wood removal', 'hourly', NULL, 90),
    ('pollarding', 'Pollarding', 'per_item', NULL, 100),
    ('formative_pruning', 'Formative pruning', 'hourly', NULL, 110),
    ('tree_removal', 'Tree removal', 'per_item', NULL, 120),
    ('sectional_dismantling', 'Sectional dismantling', 'hourly', NULL, 130),
    ('emergency_tree_work', 'Emergency tree work', 'callout', NULL, 140),
    ('storm_damage_clearance', 'Storm damage clearance', 'hourly', NULL, 150),
    ('wind_damaged_branch_removal', 'Wind damaged branch removal', 'hourly', NULL, 160),
    ('stump_grinding', 'Stump grinding', 'per_item', NULL, 170),
    ('stump_removal', 'Stump removal', 'per_item', NULL, 180),
    ('hedge_reduction', 'Hedge reduction', 'hourly', NULL, 190),
    ('large_hedge_cutting', 'Large hedge cutting', 'hourly', NULL, 200),
    ('conifer_reduction', 'Conifer reduction', 'hourly', NULL, 210),
    ('tree_planting', 'Tree planting', 'hourly', NULL, 220),
    ('tree_staking', 'Tree staking', 'hourly', NULL, 230),
    ('tree_aftercare', 'Tree aftercare', 'per_visit', NULL, 240),
    ('tree_preservation_advice', 'Tree preservation advice', 'hourly', NULL, 250),
    ('tpo_check_support', 'TPO check support', 'per_visit', NULL, 260),
    ('conservation_area_advice', 'Conservation area advice', 'hourly', NULL, 270),
    ('waste_chipping', 'Waste chipping', 'hourly', NULL, 280),
    ('log_cutting', 'Log cutting', 'hourly', NULL, 290),
    ('log_stacking', 'Log stacking', 'hourly', NULL, 300),
    ('climbing_work', 'Climbing work', 'hourly', NULL, 310),
    ('rigging_work', 'Rigging work', 'hourly', NULL, 320),
    ('mewp_work', 'MEWP work', 'daily', NULL, 330),
    ('site_safety_setup', 'Site safety setup', 'hourly', NULL, 340),
    ('traffic_or_pedestrian_safety_setup', 'Traffic or pedestrian safety setup', 'hourly', NULL, 350)
) AS v(code, name, pm, unit, sort)
WHERE g.code='trades'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 1.4 (fencing)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='fencing'
, (VALUES
    ('fence_inspection', 'Fence inspection', 'per_visit', NULL, 10),
    ('fence_repair', 'Fence repair', 'hourly', NULL, 20),
    ('fence_removal', 'Fence removal', 'hourly', NULL, 30),
    ('closeboard_fence_installation', 'Closeboard fence installation', 'per_m', 'm', 40),
    ('feather_edge_fence_installation', 'Feather edge fence installation', 'per_m', 'm', 50),
    ('panel_fence_installation', 'Panel fence installation', 'per_m', 'm', 60),
    ('post_and_rail_fence_installation', 'Post and rail fence installation', 'per_m', 'm', 70),
    ('picket_fence_installation', 'Picket fence installation', 'per_m', 'm', 80),
    ('security_fencing', 'Security fencing', 'per_m', 'm', 90),
    ('garden_screening', 'Garden screening', 'hourly', NULL, 100),
    ('trellis_installation', 'Trellis installation', 'hourly', NULL, 110),
    ('fence_post_replacement', 'Fence post replacement', 'hourly', NULL, 120),
    ('concrete_post_installation', 'Concrete post installation', 'per_item', NULL, 130),
    ('timber_post_installation', 'Timber post installation', 'per_item', NULL, 140),
    ('gravel_board_installation', 'Gravel board installation', 'hourly', NULL, 150),
    ('gate_installation', 'Gate installation', 'per_item', NULL, 160),
    ('gate_repair', 'Gate repair', 'hourly', NULL, 170),
    ('gate_post_installation', 'Gate post installation', 'per_item', NULL, 180),
    ('fence_painting', 'Fence painting', 'per_m2', 'm2', 190),
    ('fence_staining', 'Fence staining', 'hourly', NULL, 200),
    ('fence_strengthening', 'Fence strengthening', 'hourly', NULL, 210),
    ('storm_damage_fence_repair', 'Storm damage fence repair', 'hourly', NULL, 220),
    ('boundary_line_preparation', 'Boundary line preparation', 'hourly', NULL, 230),
    ('waste_removal', 'Waste removal', 'hourly', NULL, 240),
    ('material_collection', 'Material collection', 'material', NULL, 250)
) AS v(code, name, pm, unit, sort)
WHERE g.code='trades'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 1.5 (drainage_groundworks)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='drainage_groundworks'
, (VALUES
    ('site_excavation', 'Site excavation', 'per_m3', 'm3', 10),
    ('trench_digging', 'Trench digging', 'hourly', NULL, 20),
    ('drainage_trench_excavation', 'Drainage trench excavation', 'per_m3', 'm3', 30),
    ('french_drain_installation', 'French drain installation', 'hourly', NULL, 40),
    ('land_drain_installation', 'Land drain installation', 'hourly', NULL, 50),
    ('surface_water_drainage', 'Surface water drainage', 'hourly', NULL, 60),
    ('soakaway_installation', 'Soakaway installation', 'hourly', NULL, 70),
    ('pipe_installation', 'Pipe installation', 'per_m', 'm', 80),
    ('perforated_pipe_installation', 'Perforated pipe installation', 'per_m', 'm', 90),
    ('inspection_chamber_installation', 'Inspection chamber installation', 'per_visit', NULL, 100),
    ('manhole_repair', 'Manhole repair', 'hourly', NULL, 110),
    ('channel_drain_installation', 'Channel drain installation', 'per_m', 'm', 120),
    ('gully_installation', 'Gully installation', 'hourly', NULL, 130),
    ('ground_levelling', 'Ground levelling', 'hourly', NULL, 140),
    ('sub_base_preparation', 'Sub base preparation', 'hourly', NULL, 150),
    ('mot_type_1_installation', 'MOT Type 1 installation', 'hourly', NULL, 160),
    ('hardcore_installation', 'Hardcore installation', 'per_m3', 'm3', 170),
    ('concrete_base_preparation', 'Concrete base preparation', 'per_m3', 'm3', 180),
    ('concrete_slab_installation', 'Concrete slab installation', 'hourly', NULL, 190),
    ('foundation_excavation', 'Foundation excavation', 'per_m3', 'm3', 200),
    ('footings_excavation', 'Footings excavation', 'per_m3', 'm3', 210),
    ('retaining_wall_preparation', 'Retaining wall preparation', 'hourly', NULL, 220),
    ('clay_soil_improvement', 'Clay soil improvement', 'hourly', NULL, 230),
    ('soil_removal', 'Soil removal', 'per_m3', 'm3', 240),
    ('spoil_removal', 'Spoil removal', 'per_m3', 'm3', 250),
    ('grab_lorry_loading', 'Grab lorry loading', 'per_bulk_bag', NULL, 260),
    ('grab_lorry_coordination', 'Grab lorry coordination', 'per_bulk_bag', NULL, 270),
    ('dumper_work', 'Dumper work', 'daily', NULL, 280),
    ('mini_digger_work', 'Mini digger work', 'daily', NULL, 290),
    ('compaction', 'Compaction', 'hourly', NULL, 300),
    ('geotextile_installation', 'Geotextile installation', 'hourly', NULL, 310),
    ('drainage_gravel_installation', 'Drainage gravel installation', 'per_m2', 'm2', 320),
    ('backfilling', 'Backfilling', 'per_m3', 'm3', 330),
    ('site_clearance', 'Site clearance', 'hourly', NULL, 340)
) AS v(code, name, pm, unit, sort)
WHERE g.code='trades'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 1.6 (paving_patios)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='paving_patios'
, (VALUES
    ('patio_design_support', 'Patio design support', 'hourly', NULL, 10),
    ('patio_removal', 'Patio removal', 'hourly', NULL, 20),
    ('patio_installation', 'Patio installation', 'per_m2', 'm2', 30),
    ('patio_repair', 'Patio repair', 'hourly', NULL, 40),
    ('porcelain_paving', 'Porcelain paving', 'per_m2', 'm2', 50),
    ('sandstone_paving', 'Sandstone paving', 'per_m2', 'm2', 60),
    ('concrete_slab_paving', 'Concrete slab paving', 'per_m2', 'm2', 70),
    ('block_paving', 'Block paving', 'per_m2', 'm2', 80),
    ('path_installation', 'Path installation', 'hourly', NULL, 90),
    ('path_repair', 'Path repair', 'hourly', NULL, 100),
    ('driveway_preparation', 'Driveway preparation', 'hourly', NULL, 110),
    ('driveway_repair', 'Driveway repair', 'hourly', NULL, 120),
    ('kerb_installation', 'Kerb installation', 'per_m', 'm', 130),
    ('edging_installation', 'Edging installation', 'per_m', 'm', 140),
    ('sub_base_preparation', 'Sub base preparation', 'hourly', NULL, 150),
    ('screeding', 'Screeding', 'per_m2', 'm2', 160),
    ('slab_laying', 'Slab laying', 'hourly', NULL, 170),
    ('cutting_slabs', 'Cutting slabs', 'hourly', NULL, 180),
    ('pointing', 'Pointing', 'hourly', NULL, 190),
    ('grouting', 'Grouting', 'hourly', NULL, 200),
    ('jointing_compound_application', 'Jointing compound application', 'hourly', NULL, 210),
    ('drainage_channel_installation', 'Drainage channel installation', 'per_m', 'm', 220),
    ('step_installation', 'Step installation', 'hourly', NULL, 230),
    ('retaining_edge_preparation', 'Retaining edge preparation', 'hourly', NULL, 240),
    ('patio_cleaning', 'Patio cleaning', 'hourly', NULL, 250),
    ('patio_sealing', 'Patio sealing', 'hourly', NULL, 260),
    ('waste_disposal', 'Waste disposal', 'hourly', NULL, 270),
    ('material_collection', 'Material collection', 'material', NULL, 280)
) AS v(code, name, pm, unit, sort)
WHERE g.code='trades'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 1.7 (building_maintenance)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='building_maintenance'
, (VALUES
    ('general_repair', 'General repair', 'hourly', NULL, 10),
    ('small_building_works', 'Small building works', 'hourly', NULL, 20),
    ('wall_repair', 'Wall repair', 'hourly', NULL, 30),
    ('ceiling_repair', 'Ceiling repair', 'hourly', NULL, 40),
    ('door_repair', 'Door repair', 'hourly', NULL, 50),
    ('window_repair', 'Window repair', 'per_item', NULL, 60),
    ('floor_repair', 'Floor repair', 'hourly', NULL, 70),
    ('plaster_patch_repair', 'Plaster patch repair', 'hourly', NULL, 80),
    ('leak_damage_repair', 'Leak damage repair', 'hourly', NULL, 90),
    ('rot_repair', 'Rot repair', 'hourly', NULL, 100),
    ('shed_repair', 'Shed repair', 'hourly', NULL, 110),
    ('garage_repair', 'Garage repair', 'hourly', NULL, 120),
    ('outbuilding_repair', 'Outbuilding repair', 'hourly', NULL, 130),
    ('fence_related_repair', 'Fence related repair', 'hourly', NULL, 140),
    ('small_masonry_repair', 'Small masonry repair', 'hourly', NULL, 150),
    ('small_carpentry_repair', 'Small carpentry repair', 'hourly', NULL, 160),
    ('gutter_minor_repair', 'Gutter minor repair', 'hourly', NULL, 170),
    ('external_maintenance', 'External maintenance', 'hourly', NULL, 180),
    ('internal_maintenance', 'Internal maintenance', 'hourly', NULL, 190),
    ('snagging', 'Snagging', 'hourly', NULL, 200),
    ('property_inspection', 'Property inspection', 'per_visit', NULL, 210),
    ('maintenance_visit', 'Maintenance visit', 'per_visit', NULL, 220),
    ('emergency_repair', 'Emergency repair', 'hourly', NULL, 230),
    ('client_condition_report', 'Client condition report', 'hourly', NULL, 240),
    ('photo_report', 'Photo report', 'hourly', NULL, 250),
    ('material_replacement', 'Material replacement', 'hourly', NULL, 260)
) AS v(code, name, pm, unit, sort)
WHERE g.code='trades'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 1.8 (painting_decorating)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='painting_decorating'
, (VALUES
    ('interior_painting', 'Interior painting', 'per_m2', 'm2', 10),
    ('exterior_painting', 'Exterior painting', 'per_m2', 'm2', 20),
    ('wall_preparation', 'Wall preparation', 'hourly', NULL, 30),
    ('ceiling_painting', 'Ceiling painting', 'per_m2', 'm2', 40),
    ('woodwork_painting', 'Woodwork painting', 'per_m2', 'm2', 50),
    ('door_painting', 'Door painting', 'per_m2', 'm2', 60),
    ('window_frame_painting', 'Window frame painting', 'per_m2', 'm2', 70),
    ('fence_painting', 'Fence painting', 'per_m2', 'm2', 80),
    ('decking_staining', 'Decking staining', 'hourly', NULL, 90),
    ('shed_painting', 'Shed painting', 'per_m2', 'm2', 100),
    ('wallpaper_removal', 'Wallpaper removal', 'hourly', NULL, 110),
    ('wallpaper_hanging', 'Wallpaper hanging', 'hourly', NULL, 120),
    ('filling', 'Filling', 'hourly', NULL, 130),
    ('sanding', 'Sanding', 'hourly', NULL, 140),
    ('primer_application', 'Primer application', 'hourly', NULL, 150),
    ('undercoat_application', 'Undercoat application', 'hourly', NULL, 160),
    ('topcoat_application', 'Topcoat application', 'hourly', NULL, 170),
    ('stain_blocking', 'Stain blocking', 'hourly', NULL, 180),
    ('damp_stain_treatment', 'Damp stain treatment', 'per_visit', NULL, 190),
    ('decorative_finish', 'Decorative finish', 'hourly', NULL, 200),
    ('touch_up_work', 'Touch up work', 'hourly', NULL, 210),
    ('commercial_repaint', 'Commercial repaint', 'hourly', NULL, 220),
    ('end_of_tenancy_repaint', 'End of tenancy repaint', 'per_visit', NULL, 230),
    ('after_repair_painting', 'After repair painting', 'per_m2', 'm2', 240),
    ('paint_supply', 'Paint supply', 'material', NULL, 250)
) AS v(code, name, pm, unit, sort)
WHERE g.code='trades'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 1.9 (electrical)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='electrical'
, (VALUES
    ('electrical_inspection', 'Electrical inspection', 'per_visit', NULL, 10),
    ('fault_finding', 'Fault finding', 'hourly', NULL, 20),
    ('light_fitting_replacement', 'Light fitting replacement', 'per_item', NULL, 30),
    ('socket_replacement', 'Socket replacement', 'per_item', NULL, 40),
    ('switch_replacement', 'Switch replacement', 'hourly', NULL, 50),
    ('outdoor_lighting_installation', 'Outdoor lighting installation', 'hourly', NULL, 60),
    ('garden_lighting_installation', 'Garden lighting installation', 'hourly', NULL, 70),
    ('security_light_installation', 'Security light installation', 'hourly', NULL, 80),
    ('low_voltage_lighting', 'Low voltage lighting', 'hourly', NULL, 90),
    ('cable_routing', 'Cable routing', 'hourly', NULL, 100),
    ('consumer_unit_work', 'Consumer unit work', 'hourly', NULL, 110),
    ('electrical_repair', 'Electrical repair', 'hourly', NULL, 120),
    ('electrical_testing', 'Electrical testing', 'hourly', NULL, 130),
    ('certification_work', 'Certification work', 'hourly', NULL, 140),
    ('emergency_callout', 'Emergency callout', 'callout', NULL, 150),
    ('smoke_alarm_installation', 'Smoke alarm installation', 'per_item', NULL, 160),
    ('extractor_fan_installation', 'Extractor fan installation', 'hourly', NULL, 170),
    ('doorbell_wiring', 'Doorbell wiring', 'hourly', NULL, 180),
    ('cctv_power_support', 'CCTV power support', 'hourly', NULL, 190)
) AS v(code, name, pm, unit, sort)
WHERE g.code='trades'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 1.10 (plumbing_heating)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='plumbing_heating'
, (VALUES
    ('leak_repair', 'Leak repair', 'hourly', NULL, 10),
    ('tap_replacement', 'Tap replacement', 'hourly', NULL, 20),
    ('valve_replacement', 'Valve replacement', 'hourly', NULL, 30),
    ('toilet_repair', 'Toilet repair', 'hourly', NULL, 40),
    ('cistern_repair', 'Cistern repair', 'hourly', NULL, 50),
    ('sink_repair', 'Sink repair', 'hourly', NULL, 60),
    ('waste_pipe_repair', 'Waste pipe repair', 'hourly', NULL, 70),
    ('outdoor_tap_installation', 'Outdoor tap installation', 'hourly', NULL, 80),
    ('irrigation_water_connection', 'Irrigation water connection', 'hourly', NULL, 90),
    ('radiator_repair', 'Radiator repair', 'hourly', NULL, 100),
    ('radiator_replacement', 'Radiator replacement', 'per_item', NULL, 110),
    ('pipework_repair', 'Pipework repair', 'hourly', NULL, 120),
    ('drain_unblocking', 'Drain unblocking', 'hourly', NULL, 130),
    ('pump_installation', 'Pump installation', 'hourly', NULL, 140),
    ('water_pressure_issue', 'Water pressure issue', 'hourly', NULL, 150),
    ('bathroom_repair', 'Bathroom repair', 'hourly', NULL, 160),
    ('kitchen_plumbing', 'Kitchen plumbing', 'hourly', NULL, 170),
    ('shower_repair', 'Shower repair', 'hourly', NULL, 180),
    ('appliance_connection', 'Appliance connection', 'hourly', NULL, 190),
    ('emergency_plumbing', 'Emergency plumbing', 'callout', NULL, 200),
    ('stopcock_repair', 'Stopcock repair', 'hourly', NULL, 210),
    ('water_system_inspection', 'Water system inspection', 'per_visit', NULL, 220)
) AS v(code, name, pm, unit, sort)
WHERE g.code='trades'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 1.11 (gas_boilers)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='gas_boilers'
, (VALUES
    ('boiler_service', 'Boiler service', 'per_visit', NULL, 10),
    ('boiler_repair', 'Boiler repair', 'hourly', NULL, 20),
    ('boiler_installation', 'Boiler installation', 'per_item', NULL, 30),
    ('gas_safety_check', 'Gas safety check', 'per_visit', NULL, 40),
    ('landlord_gas_certificate', 'Landlord gas certificate', 'hourly', NULL, 50),
    ('heating_fault_diagnosis', 'Heating fault diagnosis', 'hourly', NULL, 60),
    ('gas_leak_callout', 'Gas leak callout', 'callout', NULL, 70),
    ('radiator_balancing', 'Radiator balancing', 'hourly', NULL, 80),
    ('thermostat_installation', 'Thermostat installation', 'hourly', NULL, 90),
    ('cylinder_service', 'Cylinder service', 'hourly', NULL, 100),
    ('heating_system_inspection', 'Heating system inspection', 'per_visit', NULL, 110),
    ('flue_inspection', 'Flue inspection', 'per_visit', NULL, 120),
    ('emergency_heating_callout', 'Emergency heating callout', 'callout', NULL, 130)
) AS v(code, name, pm, unit, sort)
WHERE g.code='trades'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 1.12 (hvac)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='hvac'
, (VALUES
    ('air_conditioning_service', 'Air conditioning service', 'hourly', NULL, 10),
    ('air_conditioning_installation', 'Air conditioning installation', 'hourly', NULL, 20),
    ('air_conditioning_repair', 'Air conditioning repair', 'hourly', NULL, 30),
    ('ventilation_repair', 'Ventilation repair', 'hourly', NULL, 40),
    ('ventilation_installation', 'Ventilation installation', 'hourly', NULL, 50),
    ('ducting_installation', 'Ducting installation', 'hourly', NULL, 60),
    ('heat_pump_service', 'Heat pump service', 'hourly', NULL, 70),
    ('heat_pump_installation', 'Heat pump installation', 'hourly', NULL, 80),
    ('filter_replacement', 'Filter replacement', 'hourly', NULL, 90),
    ('fault_diagnosis', 'Fault diagnosis', 'hourly', NULL, 100),
    ('commercial_hvac_inspection', 'Commercial HVAC inspection', 'per_visit', NULL, 110),
    ('hvac_maintenance_visit', 'HVAC maintenance visit', 'per_visit', NULL, 120),
    ('thermostat_setup', 'Thermostat setup', 'hourly', NULL, 130),
    ('airflow_balancing', 'Airflow balancing', 'hourly', NULL, 140)
) AS v(code, name, pm, unit, sort)
WHERE g.code='trades'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 1.13 (carpentry_joinery)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='carpentry_joinery'
, (VALUES
    ('door_fitting', 'Door fitting', 'per_item', NULL, 10),
    ('door_repair', 'Door repair', 'hourly', NULL, 20),
    ('door_adjustment', 'Door adjustment', 'hourly', NULL, 30),
    ('skirting_board_installation', 'Skirting board installation', 'hourly', NULL, 40),
    ('architrave_installation', 'Architrave installation', 'hourly', NULL, 50),
    ('shelving', 'Shelving', 'hourly', NULL, 60),
    ('cabinet_repair', 'Cabinet repair', 'hourly', NULL, 70),
    ('custom_joinery', 'Custom joinery', 'hourly', NULL, 80),
    ('furniture_assembly', 'Furniture assembly', 'hourly', NULL, 90),
    ('decking_installation', 'Decking installation', 'per_m2', 'm2', 100),
    ('decking_repair', 'Decking repair', 'hourly', NULL, 110),
    ('pergola_installation', 'Pergola installation', 'hourly', NULL, 120),
    ('garden_structure_installation', 'Garden structure installation', 'hourly', NULL, 130),
    ('shed_repair', 'Shed repair', 'hourly', NULL, 140),
    ('timber_frame_repair', 'Timber frame repair', 'hourly', NULL, 150),
    ('stair_repair', 'Stair repair', 'hourly', NULL, 160),
    ('floorboard_repair', 'Floorboard repair', 'hourly', NULL, 170),
    ('worktop_fitting', 'Worktop fitting', 'hourly', NULL, 180),
    ('boxing_in_pipes', 'Boxing in pipes', 'hourly', NULL, 190),
    ('timber_cladding', 'Timber cladding', 'hourly', NULL, 200),
    ('gate_construction', 'Gate construction', 'hourly', NULL, 210),
    ('raised_bed_construction', 'Raised bed construction', 'hourly', NULL, 220)
) AS v(code, name, pm, unit, sort)
WHERE g.code='trades'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 1.14 (roofing_guttering)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='roofing_guttering'
, (VALUES
    ('roof_inspection', 'Roof inspection', 'per_visit', NULL, 10),
    ('leak_diagnosis', 'Leak diagnosis', 'hourly', NULL, 20),
    ('tile_replacement', 'Tile replacement', 'hourly', NULL, 30),
    ('slate_repair', 'Slate repair', 'hourly', NULL, 40),
    ('flat_roof_repair', 'Flat roof repair', 'hourly', NULL, 50),
    ('epdm_roof_installation', 'EPDM roof installation', 'hourly', NULL, 60),
    ('felt_roof_repair', 'Felt roof repair', 'hourly', NULL, 70),
    ('lead_flashing_repair', 'Lead flashing repair', 'hourly', NULL, 80),
    ('chimney_flashing_repair', 'Chimney flashing repair', 'hourly', NULL, 90),
    ('ridge_repair', 'Ridge repair', 'hourly', NULL, 100),
    ('gutter_cleaning', 'Gutter cleaning', 'hourly', NULL, 110),
    ('gutter_repair', 'Gutter repair', 'hourly', NULL, 120),
    ('gutter_replacement', 'Gutter replacement', 'hourly', NULL, 130),
    ('downpipe_repair', 'Downpipe repair', 'hourly', NULL, 140),
    ('downpipe_replacement', 'Downpipe replacement', 'hourly', NULL, 150),
    ('fascia_replacement', 'Fascia replacement', 'hourly', NULL, 160),
    ('soffit_replacement', 'Soffit replacement', 'hourly', NULL, 170),
    ('storm_damage_repair', 'Storm damage repair', 'hourly', NULL, 180),
    ('garage_roof_repair', 'Garage roof repair', 'hourly', NULL, 190),
    ('shed_roof_repair', 'Shed roof repair', 'hourly', NULL, 200),
    ('roof_maintenance_visit', 'Roof maintenance visit', 'per_visit', NULL, 210),
    ('roof_photo_report', 'Roof photo report', 'hourly', NULL, 220)
) AS v(code, name, pm, unit, sort)
WHERE g.code='trades'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 1.15 (flooring_tiling)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='flooring_tiling'
, (VALUES
    ('floor_preparation', 'Floor preparation', 'hourly', NULL, 10),
    ('floor_levelling', 'Floor levelling', 'hourly', NULL, 20),
    ('laminate_flooring', 'Laminate flooring', 'per_m2', 'm2', 30),
    ('engineered_wood_flooring', 'Engineered wood flooring', 'per_m2', 'm2', 40),
    ('vinyl_flooring', 'Vinyl flooring', 'per_m2', 'm2', 50),
    ('carpet_fitting', 'Carpet fitting', 'per_m2', 'm2', 60),
    ('floor_tile_installation', 'Floor tile installation', 'per_m2', 'm2', 70),
    ('wall_tiling', 'Wall tiling', 'per_m2', 'm2', 80),
    ('bathroom_tiling', 'Bathroom tiling', 'per_m2', 'm2', 90),
    ('kitchen_tiling', 'Kitchen tiling', 'per_m2', 'm2', 100),
    ('tile_repair', 'Tile repair', 'hourly', NULL, 110),
    ('grouting', 'Grouting', 'hourly', NULL, 120),
    ('silicone_sealing', 'Silicone sealing', 'hourly', NULL, 130),
    ('old_flooring_removal', 'Old flooring removal', 'per_m2', 'm2', 140),
    ('skirting_refit', 'Skirting refit', 'hourly', NULL, 150),
    ('threshold_fitting', 'Threshold fitting', 'hourly', NULL, 160),
    ('subfloor_repair', 'Subfloor repair', 'hourly', NULL, 170)
) AS v(code, name, pm, unit, sort)
WHERE g.code='trades'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 1.16 (glazing_windows)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='glazing_windows'
, (VALUES
    ('window_repair', 'Window repair', 'per_item', NULL, 10),
    ('glass_replacement', 'Glass replacement', 'per_item', NULL, 20),
    ('double_glazing_repair', 'Double glazing repair', 'hourly', NULL, 30),
    ('window_lock_repair', 'Window lock repair', 'hourly', NULL, 40),
    ('window_handle_replacement', 'Window handle replacement', 'hourly', NULL, 50),
    ('door_glass_replacement', 'Door glass replacement', 'per_item', NULL, 60),
    ('seal_replacement', 'Seal replacement', 'hourly', NULL, 70),
    ('conservatory_repair', 'Conservatory repair', 'hourly', NULL, 80),
    ('cat_flap_installation', 'Cat flap installation', 'hourly', NULL, 90),
    ('window_inspection', 'Window inspection', 'per_visit', NULL, 100),
    ('draught_repair', 'Draught repair', 'hourly', NULL, 110),
    ('hinge_replacement', 'Hinge replacement', 'hourly', NULL, 120),
    ('upvc_adjustment', 'UPVC adjustment', 'hourly', NULL, 130),
    ('emergency_board_up', 'Emergency board up', 'callout', NULL, 140)
) AS v(code, name, pm, unit, sort)
WHERE g.code='trades'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 1.17 (pest_control)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='pest_control'
, (VALUES
    ('rodent_control', 'Rodent control', 'hourly', NULL, 10),
    ('wasp_nest_removal', 'Wasp nest removal', 'hourly', NULL, 20),
    ('ant_treatment', 'Ant treatment', 'per_visit', NULL, 30),
    ('flea_treatment', 'Flea treatment', 'per_visit', NULL, 40),
    ('bed_bug_treatment', 'Bed bug treatment', 'per_visit', NULL, 50),
    ('bird_control', 'Bird control', 'hourly', NULL, 60),
    ('mole_control', 'Mole control', 'hourly', NULL, 70),
    ('pest_inspection', 'Pest inspection', 'per_visit', NULL, 80),
    ('pest_proofing', 'Pest proofing', 'hourly', NULL, 90),
    ('follow_up_visit', 'Follow up visit', 'per_visit', NULL, 100),
    ('commercial_pest_contract', 'Commercial pest contract', 'hourly', NULL, 110),
    ('bait_station_installation', 'Bait station installation', 'hourly', NULL, 120),
    ('entry_point_sealing', 'Entry point sealing', 'hourly', NULL, 130),
    ('pest_report', 'Pest report', 'hourly', NULL, 140)
) AS v(code, name, pm, unit, sort)
WHERE g.code='trades'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 1.18 (locksmith)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='locksmith'
, (VALUES
    ('lock_replacement', 'Lock replacement', 'per_item', NULL, 10),
    ('lock_repair', 'Lock repair', 'hourly', NULL, 20),
    ('emergency_lockout', 'Emergency lockout', 'callout', NULL, 30),
    ('door_opening', 'Door opening', 'hourly', NULL, 40),
    ('upvc_lock_repair', 'UPVC lock repair', 'hourly', NULL, 50),
    ('door_mechanism_repair', 'Door mechanism repair', 'hourly', NULL, 60),
    ('key_cutting_coordination', 'Key cutting coordination', 'hourly', NULL, 70),
    ('security_upgrade', 'Security upgrade', 'hourly', NULL, 80),
    ('window_lock_repair', 'Window lock repair', 'hourly', NULL, 90),
    ('safe_opening', 'Safe opening', 'hourly', NULL, 100),
    ('lock_inspection', 'Lock inspection', 'per_visit', NULL, 110),
    ('tenant_lock_change', 'Tenant lock change', 'hourly', NULL, 120),
    ('lost_key_response', 'Lost key response', 'hourly', NULL, 130)
) AS v(code, name, pm, unit, sort)
WHERE g.code='trades'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 1.19 (solar_energy)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='solar_energy'
, (VALUES
    ('solar_panel_installation', 'Solar panel installation', 'per_item', NULL, 10),
    ('solar_panel_inspection', 'Solar panel inspection', 'per_visit', NULL, 20),
    ('solar_maintenance', 'Solar maintenance', 'hourly', NULL, 30),
    ('inverter_replacement', 'Inverter replacement', 'hourly', NULL, 40),
    ('battery_installation', 'Battery installation', 'hourly', NULL, 50),
    ('fault_diagnosis', 'Fault diagnosis', 'hourly', NULL, 60),
    ('panel_cleaning', 'Panel cleaning', 'hourly', NULL, 70),
    ('performance_report', 'Performance report', 'hourly', NULL, 80),
    ('cable_inspection', 'Cable inspection', 'per_visit', NULL, 90),
    ('mounting_repair', 'Mounting repair', 'hourly', NULL, 100),
    ('system_monitoring_setup', 'System monitoring setup', 'hourly', NULL, 110)
) AS v(code, name, pm, unit, sort)
WHERE g.code='trades'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 1.20 (scaffolding)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='scaffolding'
, (VALUES
    ('scaffold_erection', 'Scaffold erection', 'per_item', NULL, 10),
    ('scaffold_dismantling', 'Scaffold dismantling', 'per_item', NULL, 20),
    ('tower_scaffold', 'Tower scaffold', 'hourly', NULL, 30),
    ('temporary_access', 'Temporary access', 'hourly', NULL, 40),
    ('roof_access_scaffold', 'Roof access scaffold', 'hourly', NULL, 50),
    ('inspection', 'Inspection', 'per_visit', NULL, 60),
    ('hire_period', 'Hire period', 'daily', NULL, 70),
    ('emergency_scaffold', 'Emergency scaffold', 'hourly', NULL, 80),
    ('edge_protection', 'Edge protection', 'hourly', NULL, 90),
    ('access_platform', 'Access platform', 'hourly', NULL, 100),
    ('site_safety_handover', 'Site safety handover', 'hourly', NULL, 110)
) AS v(code, name, pm, unit, sort)
WHERE g.code='trades'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 1.21 (pool_maintenance)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='pool_maintenance'
, (VALUES
    ('pool_cleaning', 'Pool cleaning', 'per_visit', NULL, 10),
    ('chemical_treatment', 'Chemical treatment', 'per_visit', NULL, 20),
    ('filter_cleaning', 'Filter cleaning', 'hourly', NULL, 30),
    ('pump_repair', 'Pump repair', 'hourly', NULL, 40),
    ('pool_opening', 'Pool opening', 'hourly', NULL, 50),
    ('pool_closing', 'Pool closing', 'hourly', NULL, 60),
    ('leak_inspection', 'Leak inspection', 'per_visit', NULL, 70),
    ('regular_maintenance', 'Regular maintenance', 'subscription', NULL, 80),
    ('hot_tub_service', 'Hot tub service', 'per_visit', NULL, 90),
    ('water_testing', 'Water testing', 'hourly', NULL, 100),
    ('pool_cover_fitting', 'Pool cover fitting', 'hourly', NULL, 110),
    ('equipment_inspection', 'Equipment inspection', 'per_visit', NULL, 120)
) AS v(code, name, pm, unit, sort)
WHERE g.code='trades'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 1.22 (pressure_washing)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='pressure_washing'
, (VALUES
    ('driveway_cleaning', 'Driveway cleaning', 'hourly', NULL, 10),
    ('patio_cleaning', 'Patio cleaning', 'hourly', NULL, 20),
    ('decking_cleaning', 'Decking cleaning', 'hourly', NULL, 30),
    ('wall_cleaning', 'Wall cleaning', 'hourly', NULL, 40),
    ('roof_cleaning', 'Roof cleaning', 'hourly', NULL, 50),
    ('commercial_pressure_washing', 'Commercial pressure washing', 'per_m2', 'm2', 60),
    ('graffiti_removal', 'Graffiti removal', 'hourly', NULL, 70),
    ('surface_treatment', 'Surface treatment', 'per_visit', NULL, 80),
    ('sealing', 'Sealing', 'hourly', NULL, 90),
    ('moss_removal', 'Moss removal', 'hourly', NULL, 100),
    ('algae_removal', 'Algae removal', 'hourly', NULL, 110),
    ('external_cleaning', 'External cleaning', 'hourly', NULL, 120)
) AS v(code, name, pm, unit, sort)
WHERE g.code='trades'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 1.23 (waste_removal)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='waste_removal'
, (VALUES
    ('garden_waste_removal', 'Garden waste removal', 'hourly', NULL, 10),
    ('green_waste_removal', 'Green waste removal', 'hourly', NULL, 20),
    ('construction_waste_removal', 'Construction waste removal', 'hourly', NULL, 30),
    ('house_clearance', 'House clearance', 'hourly', NULL, 40),
    ('garage_clearance', 'Garage clearance', 'hourly', NULL, 50),
    ('shed_clearance', 'Shed clearance', 'hourly', NULL, 60),
    ('soil_removal', 'Soil removal', 'per_m3', 'm3', 70),
    ('hardcore_removal', 'Hardcore removal', 'hourly', NULL, 80),
    ('bulk_bag_removal', 'Bulk bag removal', 'per_bulk_bag', NULL, 90),
    ('skip_coordination', 'Skip coordination', 'per_bulk_bag', NULL, 100),
    ('grab_lorry_coordination', 'Grab lorry coordination', 'per_bulk_bag', NULL, 110),
    ('licensed_disposal', 'Licensed disposal', 'hourly', NULL, 120),
    ('waste_sorting', 'Waste sorting', 'hourly', NULL, 130),
    ('waste_loading', 'Waste loading', 'hourly', NULL, 140)
) AS v(code, name, pm, unit, sort)
WHERE g.code='trades'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 1.24 (handyman)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='handyman'
, (VALUES
    ('general_repairs', 'General repairs', 'hourly', NULL, 10),
    ('furniture_assembly', 'Furniture assembly', 'hourly', NULL, 20),
    ('shelf_installation', 'Shelf installation', 'hourly', NULL, 30),
    ('curtain_rail_fitting', 'Curtain rail fitting', 'hourly', NULL, 40),
    ('tv_mounting', 'TV mounting', 'hourly', NULL, 50),
    ('minor_plumbing', 'Minor plumbing', 'hourly', NULL, 60),
    ('minor_carpentry', 'Minor carpentry', 'hourly', NULL, 70),
    ('door_adjustment', 'Door adjustment', 'hourly', NULL, 80),
    ('picture_hanging', 'Picture hanging', 'hourly', NULL, 90),
    ('flat_pack_assembly', 'Flat pack assembly', 'hourly', NULL, 100),
    ('small_maintenance_visit', 'Small maintenance visit', 'per_visit', NULL, 110),
    ('sealant_replacement', 'Sealant replacement', 'hourly', NULL, 120),
    ('blind_fitting', 'Blind fitting', 'hourly', NULL, 130),
    ('small_wall_repair', 'Small wall repair', 'hourly', NULL, 140),
    ('minor_exterior_repair', 'Minor exterior repair', 'hourly', NULL, 150)
) AS v(code, name, pm, unit, sort)
WHERE g.code='trades'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 1.25 (security_systems)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='security_systems'
, (VALUES
    ('cctv_installation', 'CCTV installation', 'per_item', NULL, 10),
    ('cctv_maintenance', 'CCTV maintenance', 'hourly', NULL, 20),
    ('alarm_installation', 'Alarm installation', 'per_item', NULL, 30),
    ('alarm_maintenance', 'Alarm maintenance', 'hourly', NULL, 40),
    ('camera_setup', 'Camera setup', 'hourly', NULL, 50),
    ('doorbell_camera_installation', 'Doorbell camera installation', 'per_item', NULL, 60),
    ('security_light_installation', 'Security light installation', 'hourly', NULL, 70),
    ('access_control', 'Access control', 'hourly', NULL, 80),
    ('fault_finding', 'Fault finding', 'hourly', NULL, 90),
    ('system_upgrade', 'System upgrade', 'hourly', NULL, 100),
    ('camera_positioning', 'Camera positioning', 'hourly', NULL, 110),
    ('network_video_recorder_setup', 'Network video recorder setup', 'hourly', NULL, 120),
    ('alarm_response_setup', 'Alarm response setup', 'per_visit', NULL, 130)
) AS v(code, name, pm, unit, sort)
WHERE g.code='trades'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 2.1 (new_build)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='new_build'
, (VALUES
    ('site_setup', 'Site setup', 'hourly', NULL, 10),
    ('ground_clearance', 'Ground clearance', 'hourly', NULL, 20),
    ('setting_out', 'Setting out', 'hourly', NULL, 30),
    ('foundation_excavation', 'Foundation excavation', 'per_m3', 'm3', 40),
    ('foundation_concrete', 'Foundation concrete', 'hourly', NULL, 50),
    ('drainage_installation', 'Drainage installation', 'hourly', NULL, 60),
    ('substructure_works', 'Substructure works', 'hourly', NULL, 70),
    ('superstructure_works', 'Superstructure works', 'hourly', NULL, 80),
    ('brickwork', 'Brickwork', 'hourly', NULL, 90),
    ('blockwork', 'Blockwork', 'hourly', NULL, 100),
    ('roof_structure', 'Roof structure', 'hourly', NULL, 110),
    ('roof_covering', 'Roof covering', 'hourly', NULL, 120),
    ('first_fix_carpentry', 'First fix carpentry', 'hourly', NULL, 130),
    ('first_fix_plumbing', 'First fix plumbing', 'hourly', NULL, 140),
    ('first_fix_electrical', 'First fix electrical', 'hourly', NULL, 150),
    ('insulation', 'Insulation', 'hourly', NULL, 160),
    ('plasterboarding', 'Plasterboarding', 'hourly', NULL, 170),
    ('plastering', 'Plastering', 'hourly', NULL, 180),
    ('second_fix_carpentry', 'Second fix carpentry', 'hourly', NULL, 190),
    ('second_fix_plumbing', 'Second fix plumbing', 'hourly', NULL, 200),
    ('second_fix_electrical', 'Second fix electrical', 'hourly', NULL, 210),
    ('kitchen_installation', 'Kitchen installation', 'hourly', NULL, 220),
    ('bathroom_installation', 'Bathroom installation', 'hourly', NULL, 230),
    ('flooring', 'Flooring', 'per_m2', 'm2', 240),
    ('decoration', 'Decoration', 'hourly', NULL, 250),
    ('external_works', 'External works', 'hourly', NULL, 260),
    ('snagging', 'Snagging', 'hourly', NULL, 270),
    ('handover', 'Handover', 'hourly', NULL, 280),
    ('project_management', 'Project management', 'fixed', NULL, 290)
) AS v(code, name, pm, unit, sort)
WHERE g.code='construction'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 2.2 (extensions_conversions)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='extensions_conversions'
, (VALUES
    ('site_survey', 'Site survey', 'hourly', NULL, 10),
    ('scope_preparation', 'Scope preparation', 'hourly', NULL, 20),
    ('planning_support', 'Planning support', 'hourly', NULL, 30),
    ('building_control_support', 'Building control support', 'fixed', NULL, 40),
    ('demolition', 'Demolition', 'hourly', NULL, 50),
    ('strip_out', 'Strip out', 'hourly', NULL, 60),
    ('foundation_excavation', 'Foundation excavation', 'per_m3', 'm3', 70),
    ('drainage_alteration', 'Drainage alteration', 'hourly', NULL, 80),
    ('structural_opening', 'Structural opening', 'hourly', NULL, 90),
    ('steel_installation', 'Steel installation', 'hourly', NULL, 100),
    ('brickwork', 'Brickwork', 'hourly', NULL, 110),
    ('blockwork', 'Blockwork', 'hourly', NULL, 120),
    ('roof_structure', 'Roof structure', 'hourly', NULL, 130),
    ('roof_covering', 'Roof covering', 'hourly', NULL, 140),
    ('insulation', 'Insulation', 'hourly', NULL, 150),
    ('first_fix', 'First fix', 'hourly', NULL, 160),
    ('second_fix', 'Second fix', 'hourly', NULL, 170),
    ('plastering', 'Plastering', 'hourly', NULL, 180),
    ('flooring', 'Flooring', 'per_m2', 'm2', 190),
    ('decoration', 'Decoration', 'hourly', NULL, 200),
    ('kitchen_adaptation', 'Kitchen adaptation', 'hourly', NULL, 210),
    ('bathroom_adaptation', 'Bathroom adaptation', 'hourly', NULL, 220),
    ('garage_conversion', 'Garage conversion', 'hourly', NULL, 230),
    ('loft_conversion', 'Loft conversion', 'hourly', NULL, 240),
    ('hmo_conversion', 'HMO conversion', 'hourly', NULL, 250),
    ('final_snagging', 'Final snagging', 'hourly', NULL, 260),
    ('handover', 'Handover', 'hourly', NULL, 270)
) AS v(code, name, pm, unit, sort)
WHERE g.code='construction'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 2.3 (commercial_fit_out)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='commercial_fit_out'
, (VALUES
    ('site_survey', 'Site survey', 'hourly', NULL, 10),
    ('space_planning', 'Space planning', 'hourly', NULL, 20),
    ('partitioning', 'Partitioning', 'hourly', NULL, 30),
    ('dry_lining', 'Dry lining', 'hourly', NULL, 40),
    ('suspended_ceiling', 'Suspended ceiling', 'hourly', NULL, 50),
    ('flooring', 'Flooring', 'per_m2', 'm2', 60),
    ('electrical_coordination', 'Electrical coordination', 'hourly', NULL, 70),
    ('plumbing_coordination', 'Plumbing coordination', 'hourly', NULL, 80),
    ('hvac_coordination', 'HVAC coordination', 'hourly', NULL, 90),
    ('fire_safety_coordination', 'Fire safety coordination', 'hourly', NULL, 100),
    ('decoration', 'Decoration', 'hourly', NULL, 110),
    ('furniture_installation', 'Furniture installation', 'hourly', NULL, 120),
    ('signage', 'Signage', 'hourly', NULL, 130),
    ('data_cabling', 'Data cabling', 'hourly', NULL, 140),
    ('shopfront_preparation', 'Shopfront preparation', 'hourly', NULL, 150),
    ('access_control_preparation', 'Access control preparation', 'hourly', NULL, 160),
    ('snagging', 'Snagging', 'hourly', NULL, 170),
    ('handover', 'Handover', 'hourly', NULL, 180)
) AS v(code, name, pm, unit, sort)
WHERE g.code='construction'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 2.4 (structural_works)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='structural_works'
, (VALUES
    ('structural_inspection', 'Structural inspection', 'per_visit', NULL, 10),
    ('temporary_support', 'Temporary support', 'hourly', NULL, 20),
    ('acrow_props_setup', 'Acrow props setup', 'hourly', NULL, 30),
    ('wall_opening', 'Wall opening', 'hourly', NULL, 40),
    ('load_bearing_wall_work', 'Load bearing wall work', 'hourly', NULL, 50),
    ('steel_beam_installation', 'Steel beam installation', 'hourly', NULL, 60),
    ('padstone_installation', 'Padstone installation', 'hourly', NULL, 70),
    ('lintel_replacement', 'Lintel replacement', 'hourly', NULL, 80),
    ('foundation_repair', 'Foundation repair', 'hourly', NULL, 90),
    ('crack_repair', 'Crack repair', 'hourly', NULL, 100),
    ('helibar_installation', 'Helibar installation', 'hourly', NULL, 110),
    ('masonry_stitching', 'Masonry stitching', 'hourly', NULL, 120),
    ('concrete_repair', 'Concrete repair', 'hourly', NULL, 130),
    ('subsidence_investigation', 'Subsidence investigation', 'hourly', NULL, 140),
    ('structural_report', 'Structural report', 'hourly', NULL, 150),
    ('building_control_coordination', 'Building control coordination', 'fixed', NULL, 160)
) AS v(code, name, pm, unit, sort)
WHERE g.code='construction'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 2.5 (groundworks_civil)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='groundworks_civil'
, (VALUES
    ('site_clearance', 'Site clearance', 'hourly', NULL, 10),
    ('excavation', 'Excavation', 'per_m3', 'm3', 20),
    ('earthworks', 'Earthworks', 'per_m3', 'm3', 30),
    ('foundations', 'Foundations', 'hourly', NULL, 40),
    ('drainage', 'Drainage', 'hourly', NULL, 50),
    ('service_trench', 'Service trench', 'hourly', NULL, 60),
    ('concrete_slab', 'Concrete slab', 'hourly', NULL, 70),
    ('road_base', 'Road base', 'hourly', NULL, 80),
    ('kerbing', 'Kerbing', 'per_m', 'm', 90),
    ('paving_base', 'Paving base', 'per_m2', 'm2', 100),
    ('manhole_installation', 'Manhole installation', 'hourly', NULL, 110),
    ('retaining_structures', 'Retaining structures', 'hourly', NULL, 120),
    ('soil_stabilisation', 'Soil stabilisation', 'hourly', NULL, 130),
    ('compaction', 'Compaction', 'hourly', NULL, 140),
    ('ducting', 'Ducting', 'hourly', NULL, 150),
    ('utility_preparation', 'Utility preparation', 'hourly', NULL, 160),
    ('access_road_preparation', 'Access road preparation', 'hourly', NULL, 170)
) AS v(code, name, pm, unit, sort)
WHERE g.code='construction'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 2.6 (demolition)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='demolition'
, (VALUES
    ('soft_strip', 'Soft strip', 'hourly', NULL, 10),
    ('internal_demolition', 'Internal demolition', 'hourly', NULL, 20),
    ('wall_removal', 'Wall removal', 'hourly', NULL, 30),
    ('garage_demolition', 'Garage demolition', 'hourly', NULL, 40),
    ('outbuilding_demolition', 'Outbuilding demolition', 'hourly', NULL, 50),
    ('concrete_breaking', 'Concrete breaking', 'hourly', NULL, 60),
    ('waste_sorting', 'Waste sorting', 'hourly', NULL, 70),
    ('skip_loading', 'Skip loading', 'per_bulk_bag', NULL, 80),
    ('site_clearance', 'Site clearance', 'hourly', NULL, 90),
    ('hazard_identification', 'Hazard identification', 'hourly', NULL, 100),
    ('dust_control', 'Dust control', 'hourly', NULL, 110),
    ('temporary_protection', 'Temporary protection', 'hourly', NULL, 120),
    ('salvage_removal', 'Salvage removal', 'hourly', NULL, 130),
    ('final_clearance', 'Final clearance', 'hourly', NULL, 140)
) AS v(code, name, pm, unit, sort)
WHERE g.code='construction'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 2.7 (dry_lining_plastering)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='dry_lining_plastering'
, (VALUES
    ('stud_partition', 'Stud partition', 'hourly', NULL, 10),
    ('metal_stud_installation', 'Metal stud installation', 'hourly', NULL, 20),
    ('timber_stud_installation', 'Timber stud installation', 'hourly', NULL, 30),
    ('plasterboard_installation', 'Plasterboard installation', 'hourly', NULL, 40),
    ('insulated_plasterboard', 'Insulated plasterboard', 'hourly', NULL, 50),
    ('dot_and_dab', 'Dot and dab', 'hourly', NULL, 60),
    ('tape_and_jointing', 'Tape and jointing', 'hourly', NULL, 70),
    ('skimming', 'Skimming', 'hourly', NULL, 80),
    ('ceiling_repair', 'Ceiling repair', 'hourly', NULL, 90),
    ('wall_repair', 'Wall repair', 'hourly', NULL, 100),
    ('patch_plastering', 'Patch plastering', 'hourly', NULL, 110),
    ('fire_board_installation', 'Fire board installation', 'hourly', NULL, 120),
    ('moisture_board_installation', 'Moisture board installation', 'hourly', NULL, 130),
    ('sound_insulation_board', 'Sound insulation board', 'hourly', NULL, 140)
) AS v(code, name, pm, unit, sort)
WHERE g.code='construction'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 2.8 (bricklaying_masonry)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='bricklaying_masonry'
, (VALUES
    ('brick_wall', 'Brick wall', 'hourly', NULL, 10),
    ('block_wall', 'Block wall', 'hourly', NULL, 20),
    ('garden_wall', 'Garden wall', 'hourly', NULL, 30),
    ('retaining_wall', 'Retaining wall', 'hourly', NULL, 40),
    ('extension_masonry', 'Extension masonry', 'hourly', NULL, 50),
    ('repointing', 'Repointing', 'hourly', NULL, 60),
    ('chimney_repair', 'Chimney repair', 'hourly', NULL, 70),
    ('lintel_replacement', 'Lintel replacement', 'hourly', NULL, 80),
    ('crack_stitching', 'Crack stitching', 'hourly', NULL, 90),
    ('stonework', 'Stonework', 'hourly', NULL, 100),
    ('masonry_repair', 'Masonry repair', 'hourly', NULL, 110),
    ('pier_construction', 'Pier construction', 'hourly', NULL, 120),
    ('step_masonry', 'Step masonry', 'hourly', NULL, 130),
    ('boundary_wall_repair', 'Boundary wall repair', 'hourly', NULL, 140)
) AS v(code, name, pm, unit, sort)
WHERE g.code='construction'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 2.9 (project_management)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='project_management'
, (VALUES
    ('site_coordination', 'Site coordination', 'fixed', NULL, 10),
    ('schedule_preparation', 'Schedule preparation', 'hourly', NULL, 20),
    ('subcontractor_management', 'Subcontractor management', 'fixed', NULL, 30),
    ('material_ordering', 'Material ordering', 'hourly', NULL, 40),
    ('client_reporting', 'Client reporting', 'fixed', NULL, 50),
    ('budget_tracking', 'Budget tracking', 'hourly', NULL, 60),
    ('variation_management', 'Variation management', 'fixed', NULL, 70),
    ('risk_assessment', 'Risk assessment', 'per_visit', NULL, 80),
    ('progress_report', 'Progress report', 'per_visit', NULL, 90),
    ('building_control_coordination', 'Building control coordination', 'fixed', NULL, 100),
    ('health_and_safety_coordination', 'Health and safety coordination', 'fixed', NULL, 110),
    ('quality_inspection', 'Quality inspection', 'per_visit', NULL, 120),
    ('handover_coordination', 'Handover coordination', 'fixed', NULL, 130)
) AS v(code, name, pm, unit, sort)
WHERE g.code='construction'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 3.1 (hmo_management)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='hmo_management'
, (VALUES
    ('room_maintenance', 'Room maintenance', 'hourly', NULL, 10),
    ('tenant_repair_request', 'Tenant repair request', 'hourly', NULL, 20),
    ('common_area_maintenance', 'Common area maintenance', 'hourly', NULL, 30),
    ('fire_door_inspection', 'Fire door inspection', 'per_visit', NULL, 40),
    ('smoke_alarm_check', 'Smoke alarm check', 'per_item', NULL, 50),
    ('emergency_light_check', 'Emergency light check', 'per_visit', NULL, 60),
    ('hmo_compliance_check', 'HMO compliance check', 'per_visit', NULL, 70),
    ('void_room_preparation', 'Void room preparation', 'hourly', NULL, 80),
    ('inventory_check', 'Inventory check', 'per_visit', NULL, 90),
    ('cleaning_coordination', 'Cleaning coordination', 'hourly', NULL, 100),
    ('garden_maintenance_coordination', 'Garden maintenance coordination', 'hourly', NULL, 110),
    ('waste_area_check', 'Waste area check', 'per_visit', NULL, 120),
    ('tenant_communication', 'Tenant communication', 'hourly', NULL, 130),
    ('landlord_report', 'Landlord report', 'hourly', NULL, 140),
    ('room_lock_repair', 'Room lock repair', 'hourly', NULL, 150),
    ('furniture_replacement', 'Furniture replacement', 'hourly', NULL, 160),
    ('safety_inspection', 'Safety inspection', 'per_visit', NULL, 170)
) AS v(code, name, pm, unit, sort)
WHERE g.code='property'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 3.2 (residential_management)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='residential_management'
, (VALUES
    ('property_inspection', 'Property inspection', 'per_visit', NULL, 10),
    ('tenant_issue', 'Tenant issue', 'hourly', NULL, 20),
    ('landlord_maintenance', 'Landlord maintenance', 'hourly', NULL, 30),
    ('move_in_check', 'Move in check', 'per_visit', NULL, 40),
    ('move_out_check', 'Move out check', 'per_visit', NULL, 50),
    ('repair_coordination', 'Repair coordination', 'hourly', NULL, 60),
    ('garden_maintenance_coordination', 'Garden maintenance coordination', 'hourly', NULL, 70),
    ('emergency_repair', 'Emergency repair', 'hourly', NULL, 80),
    ('contractor_booking', 'Contractor booking', 'hourly', NULL, 90),
    ('monthly_report', 'Monthly report', 'subscription', NULL, 100),
    ('rent_related_reminder', 'Rent related reminder', 'hourly', NULL, 110),
    ('photo_inspection', 'Photo inspection', 'per_visit', NULL, 120),
    ('appliance_issue', 'Appliance issue', 'hourly', NULL, 130),
    ('key_management', 'Key management', 'hourly', NULL, 140),
    ('general_maintenance_visit', 'General maintenance visit', 'per_visit', NULL, 150)
) AS v(code, name, pm, unit, sort)
WHERE g.code='property'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 3.3 (commercial_management)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='commercial_management'
, (VALUES
    ('site_inspection', 'Site inspection', 'per_visit', NULL, 10),
    ('maintenance_coordination', 'Maintenance coordination', 'hourly', NULL, 20),
    ('tenant_communication', 'Tenant communication', 'hourly', NULL, 30),
    ('compliance_visit', 'Compliance visit', 'per_visit', NULL, 40),
    ('cleaning_coordination', 'Cleaning coordination', 'hourly', NULL, 50),
    ('security_coordination', 'Security coordination', 'hourly', NULL, 60),
    ('service_charge_support', 'Service charge support', 'hourly', NULL, 70),
    ('contractor_management', 'Contractor management', 'hourly', NULL, 80),
    ('incident_report', 'Incident report', 'hourly', NULL, 90),
    ('common_area_check', 'Common area check', 'per_visit', NULL, 100),
    ('lighting_check', 'Lighting check', 'per_visit', NULL, 110),
    ('access_issue', 'Access issue', 'hourly', NULL, 120),
    ('emergency_repair', 'Emergency repair', 'hourly', NULL, 130)
) AS v(code, name, pm, unit, sort)
WHERE g.code='property'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 3.4 (block_management)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='block_management'
, (VALUES
    ('common_area_inspection', 'Common area inspection', 'per_visit', NULL, 10),
    ('grounds_maintenance', 'Grounds maintenance', 'hourly', NULL, 20),
    ('lighting_check', 'Lighting check', 'per_visit', NULL, 30),
    ('waste_area_inspection', 'Waste area inspection', 'per_visit', NULL, 40),
    ('cleaning_inspection', 'Cleaning inspection', 'per_visit', NULL, 50),
    ('contractor_visit', 'Contractor visit', 'per_visit', NULL, 60),
    ('resident_communication', 'Resident communication', 'hourly', NULL, 70),
    ('estate_report', 'Estate report', 'hourly', NULL, 80),
    ('planned_maintenance', 'Planned maintenance', 'hourly', NULL, 90),
    ('car_park_inspection', 'Car park inspection', 'per_visit', NULL, 100),
    ('entrance_inspection', 'Entrance inspection', 'per_visit', NULL, 110),
    ('bin_store_check', 'Bin store check', 'per_visit', NULL, 120),
    ('drainage_check', 'Drainage check', 'per_visit', NULL, 130),
    ('boundary_inspection', 'Boundary inspection', 'per_visit', NULL, 140)
) AS v(code, name, pm, unit, sort)
WHERE g.code='property'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 3.5 (facilities_management)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='facilities_management'
, (VALUES
    ('planned_maintenance', 'Planned maintenance', 'hourly', NULL, 10),
    ('reactive_maintenance', 'Reactive maintenance', 'hourly', NULL, 20),
    ('site_inspection', 'Site inspection', 'per_visit', NULL, 30),
    ('asset_inspection', 'Asset inspection', 'per_visit', NULL, 40),
    ('contractor_coordination', 'Contractor coordination', 'hourly', NULL, 50),
    ('compliance_check', 'Compliance check', 'per_visit', NULL, 60),
    ('cleaning_coordination', 'Cleaning coordination', 'hourly', NULL, 70),
    ('security_coordination', 'Security coordination', 'hourly', NULL, 80),
    ('helpdesk_ticket', 'Helpdesk ticket', 'hourly', NULL, 90),
    ('monthly_report', 'Monthly report', 'subscription', NULL, 100),
    ('sla_monitoring', 'SLA monitoring', 'subscription', NULL, 110),
    ('maintenance_schedule', 'Maintenance schedule', 'hourly', NULL, 120),
    ('incident_handling', 'Incident handling', 'hourly', NULL, 130),
    ('access_coordination', 'Access coordination', 'hourly', NULL, 140)
) AS v(code, name, pm, unit, sort)
WHERE g.code='property'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 3.6 (short_term_lets)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='short_term_lets'
, (VALUES
    ('guest_turnover', 'Guest turnover', 'hourly', NULL, 10),
    ('cleaning_coordination', 'Cleaning coordination', 'hourly', NULL, 20),
    ('linen_coordination', 'Linen coordination', 'hourly', NULL, 30),
    ('maintenance_issue', 'Maintenance issue', 'hourly', NULL, 40),
    ('guest_message', 'Guest message', 'hourly', NULL, 50),
    ('check_in_support', 'Check in support', 'per_visit', NULL, 60),
    ('check_out_support', 'Check out support', 'per_visit', NULL, 70),
    ('damage_report', 'Damage report', 'hourly', NULL, 80),
    ('emergency_repair', 'Emergency repair', 'hourly', NULL, 90),
    ('listing_readiness', 'Listing readiness', 'hourly', NULL, 100),
    ('inventory_check', 'Inventory check', 'per_visit', NULL, 110),
    ('key_safe_issue', 'Key safe issue', 'hourly', NULL, 120),
    ('photo_update', 'Photo update', 'hourly', NULL, 130),
    ('review_follow_up', 'Review follow up', 'per_visit', NULL, 140)
) AS v(code, name, pm, unit, sort)
WHERE g.code='property'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 3.7 (void_management)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='void_management'
, (VALUES
    ('void_inspection', 'Void inspection', 'per_visit', NULL, 10),
    ('clearance', 'Clearance', 'hourly', NULL, 20),
    ('cleaning', 'Cleaning', 'hourly', NULL, 30),
    ('minor_repair', 'Minor repair', 'hourly', NULL, 40),
    ('decoration', 'Decoration', 'hourly', NULL, 50),
    ('garden_clearance', 'Garden clearance', 'hourly', NULL, 60),
    ('utility_check', 'Utility check', 'per_visit', NULL, 70),
    ('lock_change', 'Lock change', 'hourly', NULL, 80),
    ('photo_report', 'Photo report', 'hourly', NULL, 90),
    ('ready_to_let_report', 'Ready to let report', 'hourly', NULL, 100),
    ('waste_removal', 'Waste removal', 'hourly', NULL, 110),
    ('safety_check', 'Safety check', 'per_visit', NULL, 120),
    ('meter_reading', 'Meter reading', 'hourly', NULL, 130),
    ('final_inspection', 'Final inspection', 'per_visit', NULL, 140)
) AS v(code, name, pm, unit, sort)
WHERE g.code='property'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 3.8 (property_maintenance)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='property_maintenance'
, (VALUES
    ('repair_request', 'Repair request', 'hourly', NULL, 10),
    ('routine_maintenance', 'Routine maintenance', 'hourly', NULL, 20),
    ('emergency_callout', 'Emergency callout', 'callout', NULL, 30),
    ('inspection', 'Inspection', 'per_visit', NULL, 40),
    ('contractor_allocation', 'Contractor allocation', 'hourly', NULL, 50),
    ('material_order', 'Material order', 'hourly', NULL, 60),
    ('completion_report', 'Completion report', 'hourly', NULL, 70),
    ('client_update', 'Client update', 'hourly', NULL, 80),
    ('small_repair', 'Small repair', 'hourly', NULL, 90),
    ('preventive_maintenance', 'Preventive maintenance', 'hourly', NULL, 100),
    ('photo_evidence', 'Photo evidence', 'hourly', NULL, 110),
    ('access_arrangement', 'Access arrangement', 'hourly', NULL, 120)
) AS v(code, name, pm, unit, sort)
WHERE g.code='property'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 3.9 (renovation_project_management)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='renovation_project_management'
, (VALUES
    ('scope_planning', 'Scope planning', 'fixed', NULL, 10),
    ('budget_planning', 'Budget planning', 'fixed', NULL, 20),
    ('schedule_planning', 'Schedule planning', 'fixed', NULL, 30),
    ('contractor_coordination', 'Contractor coordination', 'hourly', NULL, 40),
    ('progress_inspection', 'Progress inspection', 'per_visit', NULL, 50),
    ('variation_handling', 'Variation handling', 'hourly', NULL, 60),
    ('photo_report', 'Photo report', 'hourly', NULL, 70),
    ('client_update', 'Client update', 'hourly', NULL, 80),
    ('material_coordination', 'Material coordination', 'hourly', NULL, 90),
    ('quality_check', 'Quality check', 'per_visit', NULL, 100),
    ('snagging', 'Snagging', 'hourly', NULL, 110),
    ('final_handover', 'Final handover', 'hourly', NULL, 120)
) AS v(code, name, pm, unit, sort)
WHERE g.code='property'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 3.10 (student_accommodation)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='student_accommodation'
, (VALUES
    ('room_inspection', 'Room inspection', 'per_visit', NULL, 10),
    ('common_area_issue', 'Common area issue', 'hourly', NULL, 20),
    ('maintenance_ticket', 'Maintenance ticket', 'hourly', NULL, 30),
    ('move_in_check', 'Move in check', 'per_visit', NULL, 40),
    ('move_out_check', 'Move out check', 'per_visit', NULL, 50),
    ('cleaning_coordination', 'Cleaning coordination', 'hourly', NULL, 60),
    ('damage_report', 'Damage report', 'hourly', NULL, 70),
    ('emergency_repair', 'Emergency repair', 'hourly', NULL, 80),
    ('furniture_issue', 'Furniture issue', 'hourly', NULL, 90),
    ('lock_issue', 'Lock issue', 'hourly', NULL, 100),
    ('waste_issue', 'Waste issue', 'hourly', NULL, 110),
    ('student_communication', 'Student communication', 'hourly', NULL, 120),
    ('landlord_update', 'Landlord update', 'hourly', NULL, 130)
) AS v(code, name, pm, unit, sort)
WHERE g.code='property'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 4.1 (lettings_coordination)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='lettings_coordination'
, (VALUES
    ('viewing_booking', 'Viewing booking', 'per_visit', NULL, 10),
    ('applicant_follow_up', 'Applicant follow up', 'per_visit', NULL, 20),
    ('landlord_update', 'Landlord update', 'hourly', NULL, 30),
    ('tenant_referencing', 'Tenant referencing', 'hourly', NULL, 40),
    ('document_collection', 'Document collection', 'hourly', NULL, 50),
    ('move_in_coordination', 'Move in coordination', 'hourly', NULL, 60),
    ('inventory_coordination', 'Inventory coordination', 'hourly', NULL, 70),
    ('deposit_coordination', 'Deposit coordination', 'hourly', NULL, 80),
    ('renewal_reminder', 'Renewal reminder', 'hourly', NULL, 90),
    ('tenancy_agreement_support', 'Tenancy agreement support', 'hourly', NULL, 100),
    ('key_handover', 'Key handover', 'hourly', NULL, 110),
    ('property_listing_update', 'Property listing update', 'hourly', NULL, 120),
    ('compliance_document_check', 'Compliance document check', 'per_visit', NULL, 130),
    ('tenant_onboarding', 'Tenant onboarding', 'hourly', NULL, 140)
) AS v(code, name, pm, unit, sort)
WHERE g.code='real_estate'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 4.2 (residential_sales)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='residential_sales'
, (VALUES
    ('valuation_appointment', 'Valuation appointment', 'per_visit', NULL, 10),
    ('viewing', 'Viewing', 'per_visit', NULL, 20),
    ('vendor_update', 'Vendor update', 'hourly', NULL, 30),
    ('buyer_follow_up', 'Buyer follow up', 'per_visit', NULL, 40),
    ('offer_negotiation', 'Offer negotiation', 'hourly', NULL, 50),
    ('sales_progression', 'Sales progression', 'per_visit', NULL, 60),
    ('survey_coordination', 'Survey coordination', 'hourly', NULL, 70),
    ('solicitor_update', 'Solicitor update', 'hourly', NULL, 80),
    ('completion_coordination', 'Completion coordination', 'hourly', NULL, 90),
    ('property_photography_coordination', 'Property photography coordination', 'hourly', NULL, 100),
    ('listing_preparation', 'Listing preparation', 'hourly', NULL, 110),
    ('feedback_collection', 'Feedback collection', 'hourly', NULL, 120),
    ('chain_follow_up', 'Chain follow up', 'per_visit', NULL, 130)
) AS v(code, name, pm, unit, sort)
WHERE g.code='real_estate'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 4.3 (commercial_lettings)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='commercial_lettings'
, (VALUES
    ('viewing', 'Viewing', 'per_visit', NULL, 10),
    ('tenant_enquiry', 'Tenant enquiry', 'hourly', NULL, 20),
    ('lease_document_coordination', 'Lease document coordination', 'hourly', NULL, 30),
    ('landlord_update', 'Landlord update', 'hourly', NULL, 40),
    ('fit_out_coordination', 'Fit out coordination', 'hourly', NULL, 50),
    ('compliance_document_collection', 'Compliance document collection', 'hourly', NULL, 60),
    ('heads_of_terms_preparation', 'Heads of terms preparation', 'hourly', NULL, 70),
    ('commercial_valuation_support', 'Commercial valuation support', 'hourly', NULL, 80),
    ('access_arrangement', 'Access arrangement', 'hourly', NULL, 90),
    ('tenant_follow_up', 'Tenant follow up', 'per_visit', NULL, 100)
) AS v(code, name, pm, unit, sort)
WHERE g.code='real_estate'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 4.4 (land_development)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='land_development'
, (VALUES
    ('site_enquiry', 'Site enquiry', 'hourly', NULL, 10),
    ('landowner_contact', 'Landowner contact', 'hourly', NULL, 20),
    ('developer_contact', 'Developer contact', 'hourly', NULL, 30),
    ('viewing', 'Viewing', 'per_visit', NULL, 40),
    ('planning_document_collection', 'Planning document collection', 'hourly', NULL, 50),
    ('offer_tracking', 'Offer tracking', 'hourly', NULL, 60),
    ('due_diligence_support', 'Due diligence support', 'hourly', NULL, 70),
    ('site_report', 'Site report', 'hourly', NULL, 80),
    ('development_appraisal_support', 'Development appraisal support', 'hourly', NULL, 90),
    ('access_coordination', 'Access coordination', 'hourly', NULL, 100)
) AS v(code, name, pm, unit, sort)
WHERE g.code='real_estate'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 4.5 (property_valuation)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='property_valuation'
, (VALUES
    ('valuation_visit', 'Valuation visit', 'per_visit', NULL, 10),
    ('survey_visit', 'Survey visit', 'per_visit', NULL, 20),
    ('condition_report', 'Condition report', 'hourly', NULL, 30),
    ('photo_documentation', 'Photo documentation', 'hourly', NULL, 40),
    ('comparable_research', 'Comparable research', 'hourly', NULL, 50),
    ('report_preparation', 'Report preparation', 'fixed', NULL, 60),
    ('client_follow_up', 'Client follow up', 'per_visit', NULL, 70),
    ('property_measurement', 'Property measurement', 'hourly', NULL, 80),
    ('risk_note', 'Risk note', 'hourly', NULL, 90),
    ('defect_note', 'Defect note', 'hourly', NULL, 100)
) AS v(code, name, pm, unit, sort)
WHERE g.code='real_estate'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 4.6 (investment_consultancy)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='investment_consultancy'
, (VALUES
    ('portfolio_review', 'Portfolio review', 'per_visit', NULL, 10),
    ('yield_calculation', 'Yield calculation', 'hourly', NULL, 20),
    ('client_meeting', 'Client meeting', 'hourly', NULL, 30),
    ('property_sourcing', 'Property sourcing', 'hourly', NULL, 40),
    ('report_preparation', 'Report preparation', 'fixed', NULL, 50),
    ('investor_follow_up', 'Investor follow up', 'per_visit', NULL, 60),
    ('market_comparison', 'Market comparison', 'hourly', NULL, 70),
    ('deal_analysis', 'Deal analysis', 'hourly', NULL, 80),
    ('rent_estimate', 'Rent estimate', 'hourly', NULL, 90),
    ('strategy_review', 'Strategy review', 'per_visit', NULL, 100)
) AS v(code, name, pm, unit, sort)
WHERE g.code='real_estate'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 4.7 (mortgage_brokering)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='mortgage_brokering'
, (VALUES
    ('client_fact_find', 'Client fact find', 'hourly', NULL, 10),
    ('document_request', 'Document request', 'hourly', NULL, 20),
    ('lender_comparison', 'Lender comparison', 'hourly', NULL, 30),
    ('application_preparation', 'Application preparation', 'hourly', NULL, 40),
    ('client_update', 'Client update', 'hourly', NULL, 50),
    ('completion_tracking', 'Completion tracking', 'hourly', NULL, 60),
    ('affordability_check', 'Affordability check', 'per_visit', NULL, 70),
    ('agreement_in_principle_support', 'Agreement in principle support', 'hourly', NULL, 80),
    ('mortgage_product_review', 'Mortgage product review', 'per_visit', NULL, 90),
    ('compliance_note', 'Compliance note', 'hourly', NULL, 100)
) AS v(code, name, pm, unit, sort)
WHERE g.code='real_estate'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 5.1 (domestic_cleaning)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='domestic_cleaning'
, (VALUES
    ('regular_domestic_cleaning', 'Regular domestic cleaning', 'per_visit', NULL, 10),
    ('one_off_cleaning', 'One off cleaning', 'per_visit', NULL, 20),
    ('kitchen_cleaning', 'Kitchen cleaning', 'hourly', NULL, 30),
    ('bathroom_cleaning', 'Bathroom cleaning', 'hourly', NULL, 40),
    ('dusting', 'Dusting', 'hourly', NULL, 50),
    ('vacuuming', 'Vacuuming', 'hourly', NULL, 60),
    ('mopping', 'Mopping', 'hourly', NULL, 70),
    ('inside_window_cleaning', 'Inside window cleaning', 'hourly', NULL, 80),
    ('deep_cleaning', 'Deep cleaning', 'per_visit', NULL, 90),
    ('move_out_cleaning', 'Move out cleaning', 'hourly', NULL, 100),
    ('cleaning_supplies_charge', 'Cleaning supplies charge', 'hourly', NULL, 110)
) AS v(code, name, pm, unit, sort)
WHERE g.code='cleaning'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 5.2 (commercial_cleaning)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='commercial_cleaning'
, (VALUES
    ('office_cleaning', 'Office cleaning', 'per_visit', NULL, 10),
    ('desk_area_cleaning', 'Desk area cleaning', 'hourly', NULL, 20),
    ('kitchen_area_cleaning', 'Kitchen area cleaning', 'hourly', NULL, 30),
    ('washroom_cleaning', 'Washroom cleaning', 'hourly', NULL, 40),
    ('floor_cleaning', 'Floor cleaning', 'hourly', NULL, 50),
    ('bin_emptying', 'Bin emptying', 'hourly', NULL, 60),
    ('meeting_room_cleaning', 'Meeting room cleaning', 'hourly', NULL, 70),
    ('reception_cleaning', 'Reception cleaning', 'hourly', NULL, 80),
    ('regular_contract_cleaning', 'Regular contract cleaning', 'subscription', NULL, 90),
    ('cleaning_inspection', 'Cleaning inspection', 'per_visit', NULL, 100)
) AS v(code, name, pm, unit, sort)
WHERE g.code='cleaning'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 5.3 (end_of_tenancy)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='end_of_tenancy'
, (VALUES
    ('full_property_clean', 'Full property clean', 'hourly', NULL, 10),
    ('kitchen_deep_clean', 'Kitchen deep clean', 'per_visit', NULL, 20),
    ('bathroom_deep_clean', 'Bathroom deep clean', 'per_visit', NULL, 30),
    ('carpet_cleaning', 'Carpet cleaning', 'hourly', NULL, 40),
    ('oven_cleaning', 'Oven cleaning', 'hourly', NULL, 50),
    ('window_cleaning', 'Window cleaning', 'hourly', NULL, 60),
    ('appliance_cleaning', 'Appliance cleaning', 'hourly', NULL, 70),
    ('limescale_removal', 'Limescale removal', 'hourly', NULL, 80),
    ('inventory_standard_clean', 'Inventory standard clean', 'hourly', NULL, 90),
    ('final_inspection', 'Final inspection', 'per_visit', NULL, 100)
) AS v(code, name, pm, unit, sort)
WHERE g.code='cleaning'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 5.4 (deep_cleaning)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='deep_cleaning'
, (VALUES
    ('full_deep_clean', 'Full deep clean', 'per_visit', NULL, 10),
    ('grease_removal', 'Grease removal', 'hourly', NULL, 20),
    ('limescale_removal', 'Limescale removal', 'hourly', NULL, 30),
    ('high_dusting', 'High dusting', 'hourly', NULL, 40),
    ('behind_appliances', 'Behind appliances', 'hourly', NULL, 50),
    ('sanitising', 'Sanitising', 'hourly', NULL, 60),
    ('floor_deep_clean', 'Floor deep clean', 'per_visit', NULL, 70),
    ('bathroom_deep_clean', 'Bathroom deep clean', 'per_visit', NULL, 80),
    ('kitchen_deep_clean', 'Kitchen deep clean', 'per_visit', NULL, 90),
    ('post_illness_clean', 'Post illness clean', 'hourly', NULL, 100)
) AS v(code, name, pm, unit, sort)
WHERE g.code='cleaning'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 5.5 (window_cleaning)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='window_cleaning'
, (VALUES
    ('external_window_cleaning', 'External window cleaning', 'hourly', NULL, 10),
    ('internal_window_cleaning', 'Internal window cleaning', 'hourly', NULL, 20),
    ('conservatory_cleaning', 'Conservatory cleaning', 'hourly', NULL, 30),
    ('shopfront_window_cleaning', 'Shopfront window cleaning', 'hourly', NULL, 40),
    ('high_reach_window_cleaning', 'High reach window cleaning', 'hourly', NULL, 50),
    ('frame_cleaning', 'Frame cleaning', 'hourly', NULL, 60),
    ('sill_cleaning', 'Sill cleaning', 'hourly', NULL, 70),
    ('regular_route_clean', 'Regular route clean', 'subscription', NULL, 80)
) AS v(code, name, pm, unit, sort)
WHERE g.code='cleaning'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 5.6 (carpet_upholstery)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='carpet_upholstery'
, (VALUES
    ('carpet_cleaning', 'Carpet cleaning', 'hourly', NULL, 10),
    ('upholstery_cleaning', 'Upholstery cleaning', 'hourly', NULL, 20),
    ('stain_treatment', 'Stain treatment', 'per_visit', NULL, 30),
    ('rug_cleaning', 'Rug cleaning', 'hourly', NULL, 40),
    ('mattress_cleaning', 'Mattress cleaning', 'hourly', NULL, 50),
    ('odour_treatment', 'Odour treatment', 'per_visit', NULL, 60),
    ('commercial_carpet_cleaning', 'Commercial carpet cleaning', 'hourly', NULL, 70),
    ('drying_support', 'Drying support', 'hourly', NULL, 80)
) AS v(code, name, pm, unit, sort)
WHERE g.code='cleaning'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 5.7 (oven_appliance)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='oven_appliance'
, (VALUES
    ('oven_cleaning', 'Oven cleaning', 'hourly', NULL, 10),
    ('hob_cleaning', 'Hob cleaning', 'hourly', NULL, 20),
    ('extractor_cleaning', 'Extractor cleaning', 'hourly', NULL, 30),
    ('microwave_cleaning', 'Microwave cleaning', 'hourly', NULL, 40),
    ('fridge_cleaning', 'Fridge cleaning', 'hourly', NULL, 50),
    ('freezer_cleaning', 'Freezer cleaning', 'hourly', NULL, 60),
    ('dishwasher_cleaning', 'Dishwasher cleaning', 'hourly', NULL, 70),
    ('washing_machine_clean', 'Washing machine clean', 'hourly', NULL, 80),
    ('appliance_descaling', 'Appliance descaling', 'hourly', NULL, 90)
) AS v(code, name, pm, unit, sort)
WHERE g.code='cleaning'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 5.8 (industrial_cleaning)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='industrial_cleaning'
, (VALUES
    ('factory_cleaning', 'Factory cleaning', 'hourly', NULL, 10),
    ('warehouse_cleaning', 'Warehouse cleaning', 'hourly', NULL, 20),
    ('machine_area_cleaning', 'Machine area cleaning', 'hourly', NULL, 30),
    ('floor_scrubbing', 'Floor scrubbing', 'hourly', NULL, 40),
    ('degreasing', 'Degreasing', 'hourly', NULL, 50),
    ('high_level_cleaning', 'High level cleaning', 'hourly', NULL, 60),
    ('industrial_washroom_cleaning', 'Industrial washroom cleaning', 'hourly', NULL, 70),
    ('safety_cleaning', 'Safety cleaning', 'hourly', NULL, 80),
    ('industrial_waste_support', 'Industrial waste support', 'hourly', NULL, 90)
) AS v(code, name, pm, unit, sort)
WHERE g.code='cleaning'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 5.9 (biohazard_specialist)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='biohazard_specialist'
, (VALUES
    ('biohazard_cleaning', 'Biohazard cleaning', 'hourly', NULL, 10),
    ('trauma_cleaning', 'Trauma cleaning', 'hourly', NULL, 20),
    ('sharps_removal_coordination', 'Sharps removal coordination', 'hourly', NULL, 30),
    ('bodily_fluid_cleaning', 'Bodily fluid cleaning', 'hourly', NULL, 40),
    ('contamination_cleaning', 'Contamination cleaning', 'hourly', NULL, 50),
    ('ppe_setup', 'PPE setup', 'hourly', NULL, 60),
    ('specialist_disposal_coordination', 'Specialist disposal coordination', 'hourly', NULL, 70),
    ('disinfection', 'Disinfection', 'hourly', NULL, 80),
    ('incident_report', 'Incident report', 'hourly', NULL, 90)
) AS v(code, name, pm, unit, sort)
WHERE g.code='cleaning'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 5.10 (after_builders)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='after_builders'
, (VALUES
    ('dust_removal', 'Dust removal', 'hourly', NULL, 10),
    ('post_construction_clean', 'Post construction clean', 'hourly', NULL, 20),
    ('window_cleaning', 'Window cleaning', 'hourly', NULL, 30),
    ('floor_cleaning', 'Floor cleaning', 'hourly', NULL, 40),
    ('paint_splatter_removal', 'Paint splatter removal', 'hourly', NULL, 50),
    ('bathroom_clean', 'Bathroom clean', 'hourly', NULL, 60),
    ('kitchen_clean', 'Kitchen clean', 'hourly', NULL, 70),
    ('final_sparkle_clean', 'Final sparkle clean', 'hourly', NULL, 80),
    ('waste_support', 'Waste support', 'hourly', NULL, 90)
) AS v(code, name, pm, unit, sort)
WHERE g.code='cleaning'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 5.11 (pressure_cleaning)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='pressure_cleaning'
, (VALUES
    ('driveway_cleaning', 'Driveway cleaning', 'hourly', NULL, 10),
    ('patio_cleaning', 'Patio cleaning', 'hourly', NULL, 20),
    ('decking_cleaning', 'Decking cleaning', 'hourly', NULL, 30),
    ('wall_cleaning', 'Wall cleaning', 'hourly', NULL, 40),
    ('commercial_pressure_washing', 'Commercial pressure washing', 'per_m2', 'm2', 50),
    ('graffiti_removal', 'Graffiti removal', 'hourly', NULL, 60),
    ('bin_store_cleaning', 'Bin store cleaning', 'hourly', NULL, 70),
    ('surface_sealing', 'Surface sealing', 'hourly', NULL, 80),
    ('algae_removal', 'Algae removal', 'hourly', NULL, 90)
) AS v(code, name, pm, unit, sort)
WHERE g.code='cleaning'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 6.1 (vehicle_repairs)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='vehicle_repairs'
, (VALUES
    ('general_repair', 'General repair', 'hourly', NULL, 10),
    ('engine_repair', 'Engine repair', 'hourly', NULL, 20),
    ('brake_repair', 'Brake repair', 'hourly', NULL, 30),
    ('suspension_repair', 'Suspension repair', 'hourly', NULL, 40),
    ('exhaust_repair', 'Exhaust repair', 'hourly', NULL, 50),
    ('cooling_system_repair', 'Cooling system repair', 'hourly', NULL, 60),
    ('battery_replacement', 'Battery replacement', 'hourly', NULL, 70),
    ('clutch_repair', 'Clutch repair', 'hourly', NULL, 80),
    ('diagnostic_repair', 'Diagnostic repair', 'hourly', NULL, 90),
    ('parts_sourcing', 'Parts sourcing', 'hourly', NULL, 100)
) AS v(code, name, pm, unit, sort)
WHERE g.code='automotive'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 6.2 (mot_testing)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='mot_testing'
, (VALUES
    ('mot_test', 'MOT test', 'per_visit', NULL, 10),
    ('mot_preparation', 'MOT preparation', 'hourly', NULL, 20),
    ('mot_failure_repair', 'MOT failure repair', 'hourly', NULL, 30),
    ('retest', 'Retest', 'hourly', NULL, 40),
    ('lighting_check', 'Lighting check', 'per_visit', NULL, 50),
    ('brake_check', 'Brake check', 'per_visit', NULL, 60),
    ('tyre_check', 'Tyre check', 'per_visit', NULL, 70),
    ('emissions_issue', 'Emissions issue', 'hourly', NULL, 80),
    ('advisory_repair', 'Advisory repair', 'hourly', NULL, 90)
) AS v(code, name, pm, unit, sort)
WHERE g.code='automotive'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 6.3 (body_repairs)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='body_repairs'
, (VALUES
    ('dent_repair', 'Dent repair', 'hourly', NULL, 10),
    ('scratch_repair', 'Scratch repair', 'hourly', NULL, 20),
    ('panel_repair', 'Panel repair', 'hourly', NULL, 30),
    ('paint_repair', 'Paint repair', 'hourly', NULL, 40),
    ('bumper_repair', 'Bumper repair', 'hourly', NULL, 50),
    ('rust_repair', 'Rust repair', 'hourly', NULL, 60),
    ('respray', 'Respray', 'hourly', NULL, 70),
    ('paint_correction', 'Paint correction', 'hourly', NULL, 80),
    ('accident_repair', 'Accident repair', 'hourly', NULL, 90)
) AS v(code, name, pm, unit, sort)
WHERE g.code='automotive'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 6.4 (tyres_wheels)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='tyres_wheels'
, (VALUES
    ('tyre_replacement', 'Tyre replacement', 'per_item', NULL, 10),
    ('puncture_repair', 'Puncture repair', 'hourly', NULL, 20),
    ('wheel_balancing', 'Wheel balancing', 'hourly', NULL, 30),
    ('wheel_alignment', 'Wheel alignment', 'hourly', NULL, 40),
    ('tracking_adjustment', 'Tracking adjustment', 'hourly', NULL, 50),
    ('tyre_rotation', 'Tyre rotation', 'hourly', NULL, 60),
    ('valve_replacement', 'Valve replacement', 'hourly', NULL, 70),
    ('winter_tyre_swap', 'Winter tyre swap', 'hourly', NULL, 80)
) AS v(code, name, pm, unit, sort)
WHERE g.code='automotive'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 6.5 (vehicle_valeting)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='vehicle_valeting'
, (VALUES
    ('exterior_wash', 'Exterior wash', 'hourly', NULL, 10),
    ('interior_clean', 'Interior clean', 'hourly', NULL, 20),
    ('full_valet', 'Full valet', 'hourly', NULL, 30),
    ('mini_valet', 'Mini valet', 'hourly', NULL, 40),
    ('machine_polish', 'Machine polish', 'hourly', NULL, 50),
    ('paint_correction', 'Paint correction', 'hourly', NULL, 60),
    ('ceramic_coating', 'Ceramic coating', 'hourly', NULL, 70),
    ('upholstery_clean', 'Upholstery clean', 'hourly', NULL, 80),
    ('engine_bay_clean', 'Engine bay clean', 'hourly', NULL, 90)
) AS v(code, name, pm, unit, sort)
WHERE g.code='automotive'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 6.6 (mobile_mechanic)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='mobile_mechanic'
, (VALUES
    ('mobile_diagnostic', 'Mobile diagnostic', 'hourly', NULL, 10),
    ('mobile_repair', 'Mobile repair', 'hourly', NULL, 20),
    ('battery_callout', 'Battery callout', 'callout', NULL, 30),
    ('brake_repair_on_site', 'Brake repair on site', 'hourly', NULL, 40),
    ('service_on_site', 'Service on site', 'hourly', NULL, 50),
    ('emergency_callout', 'Emergency callout', 'callout', NULL, 60),
    ('vehicle_inspection', 'Vehicle inspection', 'per_visit', NULL, 70),
    ('parts_fitting', 'Parts fitting', 'hourly', NULL, 80)
) AS v(code, name, pm, unit, sort)
WHERE g.code='automotive'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 6.7 (breakdown_recovery)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='breakdown_recovery'
, (VALUES
    ('vehicle_recovery', 'Vehicle recovery', 'hourly', NULL, 10),
    ('roadside_assistance', 'Roadside assistance', 'hourly', NULL, 20),
    ('jump_start', 'Jump start', 'hourly', NULL, 30),
    ('wheel_change', 'Wheel change', 'hourly', NULL, 40),
    ('accident_recovery', 'Accident recovery', 'hourly', NULL, 50),
    ('vehicle_transport', 'Vehicle transport', 'hourly', NULL, 60),
    ('storage_coordination', 'Storage coordination', 'hourly', NULL, 70),
    ('emergency_callout', 'Emergency callout', 'callout', NULL, 80)
) AS v(code, name, pm, unit, sort)
WHERE g.code='automotive'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 6.8 (car_sales)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='car_sales'
, (VALUES
    ('vehicle_preparation', 'Vehicle preparation', 'hourly', NULL, 10),
    ('vehicle_listing', 'Vehicle listing', 'hourly', NULL, 20),
    ('viewing_appointment', 'Viewing appointment', 'per_visit', NULL, 30),
    ('test_drive', 'Test drive', 'hourly', NULL, 40),
    ('sales_paperwork', 'Sales paperwork', 'hourly', NULL, 50),
    ('warranty_coordination', 'Warranty coordination', 'hourly', NULL, 60),
    ('part_exchange_inspection', 'Part exchange inspection', 'per_visit', NULL, 70),
    ('customer_handover', 'Customer handover', 'hourly', NULL, 80)
) AS v(code, name, pm, unit, sort)
WHERE g.code='automotive'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 6.9 (fleet_management)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='fleet_management'
, (VALUES
    ('fleet_inspection', 'Fleet inspection', 'per_visit', NULL, 10),
    ('fleet_maintenance', 'Fleet maintenance', 'hourly', NULL, 20),
    ('service_scheduling', 'Service scheduling', 'hourly', NULL, 30),
    ('mot_scheduling', 'MOT scheduling', 'hourly', NULL, 40),
    ('driver_report', 'Driver report', 'hourly', NULL, 50),
    ('vehicle_defect_log', 'Vehicle defect log', 'hourly', NULL, 60),
    ('fleet_repair_coordination', 'Fleet repair coordination', 'hourly', NULL, 70),
    ('mileage_tracking', 'Mileage tracking', 'travel', NULL, 80)
) AS v(code, name, pm, unit, sort)
WHERE g.code='automotive'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 6.10 (vehicle_diagnostics)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='vehicle_diagnostics'
, (VALUES
    ('fault_code_scan', 'Fault code scan', 'hourly', NULL, 10),
    ('ecu_diagnostics', 'ECU diagnostics', 'hourly', NULL, 20),
    ('sensor_diagnosis', 'Sensor diagnosis', 'hourly', NULL, 30),
    ('electrical_diagnosis', 'Electrical diagnosis', 'hourly', NULL, 40),
    ('software_update', 'Software update', 'hourly', NULL, 50),
    ('coding', 'Coding', 'hourly', NULL, 60),
    ('performance_report', 'Performance report', 'hourly', NULL, 70),
    ('fault_finding', 'Fault finding', 'hourly', NULL, 80)
) AS v(code, name, pm, unit, sort)
WHERE g.code='automotive'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 6.11 (auto_electrics)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='auto_electrics'
, (VALUES
    ('battery_issue', 'Battery issue', 'hourly', NULL, 10),
    ('alternator_issue', 'Alternator issue', 'hourly', NULL, 20),
    ('starter_motor_issue', 'Starter motor issue', 'hourly', NULL, 30),
    ('wiring_repair', 'Wiring repair', 'hourly', NULL, 40),
    ('lighting_fault', 'Lighting fault', 'hourly', NULL, 50),
    ('sensor_wiring', 'Sensor wiring', 'hourly', NULL, 60),
    ('accessory_installation', 'Accessory installation', 'hourly', NULL, 70),
    ('dashcam_installation', 'Dashcam installation', 'hourly', NULL, 80)
) AS v(code, name, pm, unit, sort)
WHERE g.code='automotive'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 7.1 (courier_delivery)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='courier_delivery'
, (VALUES
    ('parcel_collection', 'Parcel collection', 'hourly', NULL, 10),
    ('parcel_delivery', 'Parcel delivery', 'hourly', NULL, 20),
    ('same_day_courier', 'Same day courier', 'hourly', NULL, 30),
    ('document_courier', 'Document courier', 'hourly', NULL, 40),
    ('multi_drop_route', 'Multi drop route', 'hourly', NULL, 50),
    ('proof_of_delivery', 'Proof of delivery', 'hourly', NULL, 60),
    ('failed_delivery_handling', 'Failed delivery handling', 'hourly', NULL, 70),
    ('return_delivery', 'Return delivery', 'hourly', NULL, 80)
) AS v(code, name, pm, unit, sort)
WHERE g.code='logistics'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 7.2 (removals_storage)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='removals_storage'
, (VALUES
    ('house_move', 'House move', 'hourly', NULL, 10),
    ('office_move', 'Office move', 'hourly', NULL, 20),
    ('packing', 'Packing', 'hourly', NULL, 30),
    ('loading', 'Loading', 'hourly', NULL, 40),
    ('unloading', 'Unloading', 'hourly', NULL, 50),
    ('furniture_dismantling', 'Furniture dismantling', 'hourly', NULL, 60),
    ('furniture_reassembly', 'Furniture reassembly', 'hourly', NULL, 70),
    ('storage_move', 'Storage move', 'hourly', NULL, 80),
    ('waste_removal_support', 'Waste removal support', 'hourly', NULL, 90)
) AS v(code, name, pm, unit, sort)
WHERE g.code='logistics'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 7.3 (man_and_van)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='man_and_van'
, (VALUES
    ('small_move', 'Small move', 'hourly', NULL, 10),
    ('furniture_delivery', 'Furniture delivery', 'hourly', NULL, 20),
    ('marketplace_collection', 'Marketplace collection', 'hourly', NULL, 30),
    ('student_move', 'Student move', 'hourly', NULL, 40),
    ('single_item_transport', 'Single item transport', 'hourly', NULL, 50),
    ('loading_assistance', 'Loading assistance', 'hourly', NULL, 60),
    ('unloading_assistance', 'Unloading assistance', 'hourly', NULL, 70),
    ('local_delivery', 'Local delivery', 'hourly', NULL, 80)
) AS v(code, name, pm, unit, sort)
WHERE g.code='logistics'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 7.4 (taxi_private_hire)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='taxi_private_hire'
, (VALUES
    ('taxi_trip', 'Taxi trip', 'hourly', NULL, 10),
    ('private_hire_trip', 'Private hire trip', 'hourly', NULL, 20),
    ('airport_transfer', 'Airport transfer', 'hourly', NULL, 30),
    ('waiting_time', 'Waiting time', 'hourly', NULL, 40),
    ('meet_and_greet', 'Meet and greet', 'hourly', NULL, 50),
    ('return_journey', 'Return journey', 'hourly', NULL, 60),
    ('school_run', 'School run', 'hourly', NULL, 70),
    ('executive_transfer', 'Executive transfer', 'hourly', NULL, 80)
) AS v(code, name, pm, unit, sort)
WHERE g.code='logistics'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 7.5 (haulage_freight)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='haulage_freight'
, (VALUES
    ('pallet_delivery', 'Pallet delivery', 'hourly', NULL, 10),
    ('freight_transport', 'Freight transport', 'hourly', NULL, 20),
    ('bulk_transport', 'Bulk transport', 'hourly', NULL, 30),
    ('vehicle_loading', 'Vehicle loading', 'hourly', NULL, 40),
    ('vehicle_unloading', 'Vehicle unloading', 'hourly', NULL, 50),
    ('route_planning', 'Route planning', 'hourly', NULL, 60),
    ('delivery_paperwork', 'Delivery paperwork', 'hourly', NULL, 70),
    ('freight_tracking', 'Freight tracking', 'hourly', NULL, 80)
) AS v(code, name, pm, unit, sort)
WHERE g.code='logistics'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 7.6 (warehousing)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='warehousing'
, (VALUES
    ('goods_in', 'Goods in', 'hourly', NULL, 10),
    ('goods_out', 'Goods out', 'hourly', NULL, 20),
    ('stock_handling', 'Stock handling', 'hourly', NULL, 30),
    ('pick_and_pack', 'Pick and pack', 'hourly', NULL, 40),
    ('inventory_count', 'Inventory count', 'hourly', NULL, 50),
    ('labelling', 'Labelling', 'hourly', NULL, 60),
    ('palletising', 'Palletising', 'hourly', NULL, 70),
    ('storage_management', 'Storage management', 'hourly', NULL, 80)
) AS v(code, name, pm, unit, sort)
WHERE g.code='logistics'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 7.7 (same_day_delivery)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='same_day_delivery'
, (VALUES
    ('urgent_delivery', 'Urgent delivery', 'hourly', NULL, 10),
    ('direct_delivery', 'Direct delivery', 'hourly', NULL, 20),
    ('multi_stop_delivery', 'Multi stop delivery', 'hourly', NULL, 30),
    ('timed_delivery', 'Timed delivery', 'hourly', NULL, 40),
    ('collection_coordination', 'Collection coordination', 'hourly', NULL, 50),
    ('delivery_confirmation', 'Delivery confirmation', 'hourly', NULL, 60),
    ('failed_delivery_resolution', 'Failed delivery resolution', 'hourly', NULL, 70)
) AS v(code, name, pm, unit, sort)
WHERE g.code='logistics'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 8.1 (hairdressing)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='hairdressing'
, (VALUES
    ('haircut', 'Haircut', 'per_visit', NULL, 10),
    ('wash_and_cut', 'Wash and cut', 'hourly', NULL, 20),
    ('blow_dry', 'Blow dry', 'per_visit', NULL, 30),
    ('hair_colouring', 'Hair colouring', 'per_visit', NULL, 40),
    ('highlights', 'Highlights', 'hourly', NULL, 50),
    ('balayage', 'Balayage', 'hourly', NULL, 60),
    ('hair_styling', 'Hair styling', 'hourly', NULL, 70),
    ('mens_haircut', 'Men’s haircut', 'per_visit', NULL, 80),
    ('beard_trim', 'Beard trim', 'hourly', NULL, 90),
    ('childrens_haircut', 'Children’s haircut', 'per_visit', NULL, 100),
    ('consultation', 'Consultation', 'per_visit', NULL, 110),
    ('patch_test', 'Patch test', 'per_visit', NULL, 120)
) AS v(code, name, pm, unit, sort)
WHERE g.code='beauty'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 8.2 (beauty_therapy)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='beauty_therapy'
, (VALUES
    ('facial_treatment', 'Facial treatment', 'per_visit', NULL, 10),
    ('skin_consultation', 'Skin consultation', 'per_visit', NULL, 20),
    ('waxing', 'Waxing', 'per_visit', NULL, 30),
    ('body_treatment', 'Body treatment', 'per_visit', NULL, 40),
    ('exfoliation', 'Exfoliation', 'hourly', NULL, 50),
    ('tinting', 'Tinting', 'per_visit', NULL, 60),
    ('relaxation_treatment', 'Relaxation treatment', 'per_visit', NULL, 70),
    ('aftercare_advice', 'Aftercare advice', 'per_visit', NULL, 80)
) AS v(code, name, pm, unit, sort)
WHERE g.code='beauty'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 8.3 (nail_technician)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='nail_technician'
, (VALUES
    ('manicure', 'Manicure', 'per_visit', NULL, 10),
    ('pedicure', 'Pedicure', 'per_visit', NULL, 20),
    ('gel_nails', 'Gel nails', 'hourly', NULL, 30),
    ('acrylic_nails', 'Acrylic nails', 'hourly', NULL, 40),
    ('nail_repair', 'Nail repair', 'hourly', NULL, 50),
    ('nail_art', 'Nail art', 'hourly', NULL, 60),
    ('soak_off', 'Soak off', 'hourly', NULL, 70),
    ('cuticle_treatment', 'Cuticle treatment', 'per_visit', NULL, 80)
) AS v(code, name, pm, unit, sort)
WHERE g.code='beauty'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 8.4 (massage_therapy)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='massage_therapy'
, (VALUES
    ('relaxation_massage', 'Relaxation massage', 'per_visit', NULL, 10),
    ('deep_tissue_massage', 'Deep tissue massage', 'per_visit', NULL, 20),
    ('sports_massage', 'Sports massage', 'per_visit', NULL, 30),
    ('back_massage', 'Back massage', 'per_visit', NULL, 40),
    ('full_body_massage', 'Full body massage', 'per_visit', NULL, 50),
    ('aromatherapy_massage', 'Aromatherapy massage', 'per_visit', NULL, 60),
    ('consultation', 'Consultation', 'per_visit', NULL, 70),
    ('aftercare', 'Aftercare', 'per_visit', NULL, 80)
) AS v(code, name, pm, unit, sort)
WHERE g.code='beauty'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 8.5 (lash_brow)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='lash_brow'
, (VALUES
    ('lash_lift', 'Lash lift', 'hourly', NULL, 10),
    ('lash_extensions', 'Lash extensions', 'hourly', NULL, 20),
    ('lash_tint', 'Lash tint', 'per_visit', NULL, 30),
    ('brow_shaping', 'Brow shaping', 'hourly', NULL, 40),
    ('brow_tint', 'Brow tint', 'per_visit', NULL, 50),
    ('brow_lamination', 'Brow lamination', 'hourly', NULL, 60),
    ('patch_test', 'Patch test', 'per_visit', NULL, 70),
    ('infills', 'Infills', 'hourly', NULL, 80)
) AS v(code, name, pm, unit, sort)
WHERE g.code='beauty'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 8.6 (aesthetics)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='aesthetics'
, (VALUES
    ('aesthetics_consultation', 'Aesthetics consultation', 'per_visit', NULL, 10),
    ('injectables_treatment', 'Injectables treatment', 'per_visit', NULL, 20),
    ('skin_booster', 'Skin booster', 'hourly', NULL, 30),
    ('anti_wrinkle_treatment', 'Anti wrinkle treatment', 'per_visit', NULL, 40),
    ('dermal_filler', 'Dermal filler', 'hourly', NULL, 50),
    ('aftercare_follow_up', 'Aftercare follow up', 'per_visit', NULL, 60),
    ('consent_form', 'Consent form', 'hourly', NULL, 70),
    ('patch_test', 'Patch test', 'per_visit', NULL, 80)
) AS v(code, name, pm, unit, sort)
WHERE g.code='beauty'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 8.7 (permanent_makeup)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='permanent_makeup'
, (VALUES
    ('consultation', 'Consultation', 'per_visit', NULL, 10),
    ('brow_permanent_makeup', 'Brow permanent makeup', 'hourly', NULL, 20),
    ('lip_blush', 'Lip blush', 'hourly', NULL, 30),
    ('eyeliner_permanent_makeup', 'Eyeliner permanent makeup', 'hourly', NULL, 40),
    ('top_up_appointment', 'Top up appointment', 'per_visit', NULL, 50),
    ('correction_appointment', 'Correction appointment', 'per_visit', NULL, 60),
    ('aftercare_follow_up', 'Aftercare follow up', 'per_visit', NULL, 70)
) AS v(code, name, pm, unit, sort)
WHERE g.code='beauty'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 8.8 (spray_tanning)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='spray_tanning'
, (VALUES
    ('spray_tan', 'Spray tan', 'per_visit', NULL, 10),
    ('mobile_spray_tan', 'Mobile spray tan', 'per_visit', NULL, 20),
    ('patch_test', 'Patch test', 'per_visit', NULL, 30),
    ('pre_tan_consultation', 'Pre tan consultation', 'per_visit', NULL, 40),
    ('aftercare_advice', 'Aftercare advice', 'per_visit', NULL, 50),
    ('group_booking', 'Group booking', 'hourly', NULL, 60)
) AS v(code, name, pm, unit, sort)
WHERE g.code='beauty'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 8.9 (mobile_beauty)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='mobile_beauty'
, (VALUES
    ('mobile_treatment', 'Mobile treatment', 'per_visit', NULL, 10),
    ('travel_charge', 'Travel charge', 'travel', NULL, 20),
    ('event_beauty', 'Event beauty', 'hourly', NULL, 30),
    ('wedding_beauty', 'Wedding beauty', 'hourly', NULL, 40),
    ('home_visit', 'Home visit', 'per_visit', NULL, 50),
    ('setup_time', 'Setup time', 'hourly', NULL, 60),
    ('pack_down', 'Pack down', 'hourly', NULL, 70)
) AS v(code, name, pm, unit, sort)
WHERE g.code='beauty'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 8.10 (hair_extensions)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='hair_extensions'
, (VALUES
    ('consultation', 'Consultation', 'per_visit', NULL, 10),
    ('extension_fitting', 'Extension fitting', 'hourly', NULL, 20),
    ('extension_removal', 'Extension removal', 'hourly', NULL, 30),
    ('maintenance_appointment', 'Maintenance appointment', 'per_visit', NULL, 40),
    ('colour_match', 'Colour match', 'hourly', NULL, 50),
    ('aftercare_advice', 'Aftercare advice', 'per_visit', NULL, 60)
) AS v(code, name, pm, unit, sort)
WHERE g.code='beauty'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 8.11 (makeup_artistry)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='makeup_artistry'
, (VALUES
    ('makeup_application', 'Makeup application', 'hourly', NULL, 10),
    ('wedding_makeup', 'Wedding makeup', 'hourly', NULL, 20),
    ('trial_makeup', 'Trial makeup', 'hourly', NULL, 30),
    ('event_makeup', 'Event makeup', 'hourly', NULL, 40),
    ('photoshoot_makeup', 'Photoshoot makeup', 'hourly', NULL, 50),
    ('touch_up_service', 'Touch up service', 'hourly', NULL, 60),
    ('travel_charge', 'Travel charge', 'travel', NULL, 70)
) AS v(code, name, pm, unit, sort)
WHERE g.code='beauty'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 9.1 (physiotherapy)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='physiotherapy'
, (VALUES
    ('initial_assessment', 'Initial assessment', 'per_visit', NULL, 10),
    ('follow_up_session', 'Follow up session', 'per_visit', NULL, 20),
    ('treatment_session', 'Treatment session', 'per_visit', NULL, 30),
    ('exercise_plan', 'Exercise plan', 'hourly', NULL, 40),
    ('progress_review', 'Progress review', 'per_visit', NULL, 50),
    ('rehabilitation_session', 'Rehabilitation session', 'per_visit', NULL, 60),
    ('home_visit', 'Home visit', 'per_visit', NULL, 70),
    ('report_preparation', 'Report preparation', 'fixed', NULL, 80)
) AS v(code, name, pm, unit, sort)
WHERE g.code='healthcare'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 9.2 (mental_health)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='mental_health'
, (VALUES
    ('initial_consultation', 'Initial consultation', 'per_visit', NULL, 10),
    ('counselling_session', 'Counselling session', 'per_visit', NULL, 20),
    ('therapy_session', 'Therapy session', 'per_visit', NULL, 30),
    ('assessment', 'Assessment', 'per_visit', NULL, 40),
    ('care_note', 'Care note', 'hourly', NULL, 50),
    ('follow_up_session', 'Follow up session', 'per_visit', NULL, 60),
    ('referral_letter', 'Referral letter', 'hourly', NULL, 70),
    ('risk_note', 'Risk note', 'hourly', NULL, 80)
) AS v(code, name, pm, unit, sort)
WHERE g.code='healthcare'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 9.3 (private_gp)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='private_gp'
, (VALUES
    ('gp_appointment', 'GP appointment', 'per_visit', NULL, 10),
    ('health_assessment', 'Health assessment', 'per_visit', NULL, 20),
    ('prescription_review', 'Prescription review', 'per_visit', NULL, 30),
    ('blood_test_coordination', 'Blood test coordination', 'hourly', NULL, 40),
    ('referral_letter', 'Referral letter', 'hourly', NULL, 50),
    ('follow_up_appointment', 'Follow up appointment', 'per_visit', NULL, 60),
    ('medical_note', 'Medical note', 'hourly', NULL, 70)
) AS v(code, name, pm, unit, sort)
WHERE g.code='healthcare'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 9.4 (dentistry)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='dentistry'
, (VALUES
    ('dental_check', 'Dental check', 'per_visit', NULL, 10),
    ('hygiene_appointment', 'Hygiene appointment', 'per_visit', NULL, 20),
    ('filling', 'Filling', 'hourly', NULL, 30),
    ('extraction', 'Extraction', 'hourly', NULL, 40),
    ('emergency_dental_appointment', 'Emergency dental appointment', 'per_visit', NULL, 50),
    ('treatment_plan', 'Treatment plan', 'per_visit', NULL, 60),
    ('follow_up', 'Follow up', 'per_visit', NULL, 70)
) AS v(code, name, pm, unit, sort)
WHERE g.code='healthcare'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 9.5 (optometry)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='optometry'
, (VALUES
    ('eye_test', 'Eye test', 'per_visit', NULL, 10),
    ('contact_lens_check', 'Contact lens check', 'per_visit', NULL, 20),
    ('frame_fitting', 'Frame fitting', 'hourly', NULL, 30),
    ('prescription_review', 'Prescription review', 'per_visit', NULL, 40),
    ('follow_up', 'Follow up', 'per_visit', NULL, 50),
    ('referral_letter', 'Referral letter', 'hourly', NULL, 60)
) AS v(code, name, pm, unit, sort)
WHERE g.code='healthcare'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 9.6 (nutrition_dietetics)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='nutrition_dietetics'
, (VALUES
    ('initial_consultation', 'Initial consultation', 'per_visit', NULL, 10),
    ('diet_plan', 'Diet plan', 'hourly', NULL, 20),
    ('follow_up', 'Follow up', 'per_visit', NULL, 30),
    ('progress_review', 'Progress review', 'per_visit', NULL, 40),
    ('meal_plan', 'Meal plan', 'hourly', NULL, 50),
    ('report_preparation', 'Report preparation', 'fixed', NULL, 60)
) AS v(code, name, pm, unit, sort)
WHERE g.code='healthcare'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 9.7 (osteopathy)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='osteopathy'
, (VALUES
    ('initial_assessment', 'Initial assessment', 'per_visit', NULL, 10),
    ('treatment_session', 'Treatment session', 'per_visit', NULL, 20),
    ('follow_up', 'Follow up', 'per_visit', NULL, 30),
    ('posture_review', 'Posture review', 'per_visit', NULL, 40),
    ('exercise_advice', 'Exercise advice', 'hourly', NULL, 50),
    ('progress_note', 'Progress note', 'per_visit', NULL, 60)
) AS v(code, name, pm, unit, sort)
WHERE g.code='healthcare'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 9.8 (podiatry)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='podiatry'
, (VALUES
    ('foot_assessment', 'Foot assessment', 'per_visit', NULL, 10),
    ('nail_care', 'Nail care', 'hourly', NULL, 20),
    ('hard_skin_removal', 'Hard skin removal', 'hourly', NULL, 30),
    ('diabetic_foot_check', 'Diabetic foot check', 'per_visit', NULL, 40),
    ('follow_up', 'Follow up', 'per_visit', NULL, 50),
    ('home_visit', 'Home visit', 'per_visit', NULL, 60)
) AS v(code, name, pm, unit, sort)
WHERE g.code='healthcare'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 9.9 (home_nursing)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='home_nursing'
, (VALUES
    ('home_visit', 'Home visit', 'per_visit', NULL, 10),
    ('care_visit', 'Care visit', 'per_visit', NULL, 20),
    ('medication_support', 'Medication support', 'hourly', NULL, 30),
    ('wound_care', 'Wound care', 'hourly', NULL, 40),
    ('observation_note', 'Observation note', 'hourly', NULL, 50),
    ('patient_report', 'Patient report', 'hourly', NULL, 60),
    ('family_update', 'Family update', 'hourly', NULL, 70)
) AS v(code, name, pm, unit, sort)
WHERE g.code='healthcare'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 9.10 (occupational_therapy)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='occupational_therapy'
, (VALUES
    ('assessment', 'Assessment', 'per_visit', NULL, 10),
    ('home_assessment', 'Home assessment', 'per_visit', NULL, 20),
    ('equipment_recommendation', 'Equipment recommendation', 'hourly', NULL, 30),
    ('care_plan', 'Care plan', 'hourly', NULL, 40),
    ('progress_review', 'Progress review', 'per_visit', NULL, 50),
    ('report_preparation', 'Report preparation', 'fixed', NULL, 60)
) AS v(code, name, pm, unit, sort)
WHERE g.code='healthcare'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 9.11 (acupuncture)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='acupuncture'
, (VALUES
    ('initial_consultation', 'Initial consultation', 'per_visit', NULL, 10),
    ('treatment_session', 'Treatment session', 'per_visit', NULL, 20),
    ('follow_up', 'Follow up', 'per_visit', NULL, 30),
    ('aftercare_advice', 'Aftercare advice', 'per_visit', NULL, 40),
    ('progress_note', 'Progress note', 'per_visit', NULL, 50)
) AS v(code, name, pm, unit, sort)
WHERE g.code='healthcare'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 9.12 (veterinary)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='veterinary'
, (VALUES
    ('consultation', 'Consultation', 'per_visit', NULL, 10),
    ('vaccination', 'Vaccination', 'hourly', NULL, 20),
    ('emergency_appointment', 'Emergency appointment', 'per_visit', NULL, 30),
    ('follow_up', 'Follow up', 'per_visit', NULL, 40),
    ('treatment_plan', 'Treatment plan', 'per_visit', NULL, 50),
    ('surgery_coordination', 'Surgery coordination', 'hourly', NULL, 60),
    ('animal_care_note', 'Animal care note', 'hourly', NULL, 70)
) AS v(code, name, pm, unit, sort)
WHERE g.code='healthcare'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 10.1 (personal_training)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='personal_training'
, (VALUES
    ('pt_session', 'PT session', 'per_visit', NULL, 10),
    ('initial_assessment', 'Initial assessment', 'per_visit', NULL, 20),
    ('training_plan', 'Training plan', 'hourly', NULL, 30),
    ('progress_review', 'Progress review', 'per_visit', NULL, 40),
    ('fitness_test', 'Fitness test', 'hourly', NULL, 50),
    ('nutrition_check', 'Nutrition check', 'per_visit', NULL, 60),
    ('package_session', 'Package session', 'per_visit', NULL, 70),
    ('home_workout_plan', 'Home workout plan', 'hourly', NULL, 80)
) AS v(code, name, pm, unit, sort)
WHERE g.code='fitness'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 10.2 (group_fitness)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='group_fitness'
, (VALUES
    ('group_class', 'Group class', 'per_visit', NULL, 10),
    ('class_booking', 'Class booking', 'per_visit', NULL, 20),
    ('attendance_tracking', 'Attendance tracking', 'hourly', NULL, 30),
    ('class_plan', 'Class plan', 'per_visit', NULL, 40),
    ('membership_check_in', 'Membership check in', 'subscription', NULL, 50),
    ('progress_note', 'Progress note', 'per_visit', NULL, 60)
) AS v(code, name, pm, unit, sort)
WHERE g.code='fitness'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 10.3 (yoga_pilates)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='yoga_pilates'
, (VALUES
    ('yoga_class', 'Yoga class', 'per_visit', NULL, 10),
    ('pilates_class', 'Pilates class', 'per_visit', NULL, 20),
    ('private_session', 'Private session', 'per_visit', NULL, 30),
    ('group_session', 'Group session', 'per_visit', NULL, 40),
    ('online_session', 'Online session', 'per_visit', NULL, 50),
    ('programme_plan', 'Programme plan', 'hourly', NULL, 60),
    ('progress_review', 'Progress review', 'per_visit', NULL, 70)
) AS v(code, name, pm, unit, sort)
WHERE g.code='fitness'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 10.4 (sports_coaching)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='sports_coaching'
, (VALUES
    ('coaching_session', 'Coaching session', 'per_visit', NULL, 10),
    ('skill_assessment', 'Skill assessment', 'per_visit', NULL, 20),
    ('training_plan', 'Training plan', 'hourly', NULL, 30),
    ('match_preparation', 'Match preparation', 'hourly', NULL, 40),
    ('performance_review', 'Performance review', 'per_visit', NULL, 50),
    ('group_coaching', 'Group coaching', 'hourly', NULL, 60)
) AS v(code, name, pm, unit, sort)
WHERE g.code='fitness'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 10.5 (online_coaching)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='online_coaching'
, (VALUES
    ('online_consultation', 'Online consultation', 'per_visit', NULL, 10),
    ('programme_setup', 'Programme setup', 'hourly', NULL, 20),
    ('progress_check', 'Progress check', 'per_visit', NULL, 30),
    ('video_review', 'Video review', 'per_visit', NULL, 40),
    ('message_support', 'Message support', 'hourly', NULL, 50),
    ('plan_update', 'Plan update', 'hourly', NULL, 60)
) AS v(code, name, pm, unit, sort)
WHERE g.code='fitness'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 10.6 (nutrition_coaching)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='nutrition_coaching'
, (VALUES
    ('nutrition_consultation', 'Nutrition consultation', 'per_visit', NULL, 10),
    ('meal_plan', 'Meal plan', 'hourly', NULL, 20),
    ('progress_review', 'Progress review', 'per_visit', NULL, 30),
    ('check_in', 'Check in', 'per_visit', NULL, 40),
    ('supplement_note', 'Supplement note', 'hourly', NULL, 50),
    ('goal_setting', 'Goal setting', 'hourly', NULL, 60)
) AS v(code, name, pm, unit, sort)
WHERE g.code='fitness'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 10.7 (gym_studio)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='gym_studio'
, (VALUES
    ('gym_induction', 'Gym induction', 'hourly', NULL, 10),
    ('studio_booking', 'Studio booking', 'hourly', NULL, 20),
    ('membership_onboarding', 'Membership onboarding', 'subscription', NULL, 30),
    ('class_scheduling', 'Class scheduling', 'per_visit', NULL, 40),
    ('equipment_check', 'Equipment check', 'per_visit', NULL, 50),
    ('member_follow_up', 'Member follow up', 'per_visit', NULL, 60)
) AS v(code, name, pm, unit, sort)
WHERE g.code='fitness'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 10.8 (swimming_coaching)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='swimming_coaching'
, (VALUES
    ('swimming_lesson', 'Swimming lesson', 'per_visit', NULL, 10),
    ('assessment', 'Assessment', 'per_visit', NULL, 20),
    ('group_lesson', 'Group lesson', 'per_visit', NULL, 30),
    ('private_lesson', 'Private lesson', 'per_visit', NULL, 40),
    ('progress_review', 'Progress review', 'per_visit', NULL, 50),
    ('safety_note', 'Safety note', 'hourly', NULL, 60)
) AS v(code, name, pm, unit, sort)
WHERE g.code='fitness'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 10.9 (martial_arts)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='martial_arts'
, (VALUES
    ('class_session', 'Class session', 'per_visit', NULL, 10),
    ('grading_preparation', 'Grading preparation', 'hourly', NULL, 20),
    ('technique_coaching', 'Technique coaching', 'hourly', NULL, 30),
    ('attendance_tracking', 'Attendance tracking', 'hourly', NULL, 40),
    ('progress_review', 'Progress review', 'per_visit', NULL, 50),
    ('competition_preparation', 'Competition preparation', 'hourly', NULL, 60)
) AS v(code, name, pm, unit, sort)
WHERE g.code='fitness'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 11.1 (restaurant_catering)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='restaurant_catering'
, (VALUES
    ('table_booking', 'Table booking', 'hourly', NULL, 10),
    ('table_service', 'Table service', 'hourly', NULL, 20),
    ('food_order', 'Food order', 'hourly', NULL, 30),
    ('drink_order', 'Drink order', 'hourly', NULL, 40),
    ('customer_complaint', 'Customer complaint', 'hourly', NULL, 50),
    ('private_booking', 'Private booking', 'hourly', NULL, 60),
    ('staff_rota', 'Staff rota', 'hourly', NULL, 70),
    ('supplier_order', 'Supplier order', 'hourly', NULL, 80),
    ('stock_check', 'Stock check', 'per_visit', NULL, 90)
) AS v(code, name, pm, unit, sort)
WHERE g.code='hospitality'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 11.2 (cafe_coffee)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='cafe_coffee'
, (VALUES
    ('counter_service', 'Counter service', 'hourly', NULL, 10),
    ('table_service', 'Table service', 'hourly', NULL, 20),
    ('coffee_preparation', 'Coffee preparation', 'hourly', NULL, 30),
    ('food_preparation', 'Food preparation', 'hourly', NULL, 40),
    ('stock_check', 'Stock check', 'per_visit', NULL, 50),
    ('supplier_order', 'Supplier order', 'hourly', NULL, 60),
    ('cleaning_task', 'Cleaning task', 'hourly', NULL, 70),
    ('customer_order', 'Customer order', 'hourly', NULL, 80)
) AS v(code, name, pm, unit, sort)
WHERE g.code='hospitality'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 11.3 (takeaway_delivery)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='takeaway_delivery'
, (VALUES
    ('takeaway_order', 'Takeaway order', 'hourly', NULL, 10),
    ('delivery_order', 'Delivery order', 'hourly', NULL, 20),
    ('order_preparation', 'Order preparation', 'hourly', NULL, 30),
    ('driver_assignment', 'Driver assignment', 'hourly', NULL, 40),
    ('customer_update', 'Customer update', 'hourly', NULL, 50),
    ('refund_handling', 'Refund handling', 'hourly', NULL, 60),
    ('failed_delivery', 'Failed delivery', 'hourly', NULL, 70)
) AS v(code, name, pm, unit, sort)
WHERE g.code='hospitality'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 11.4 (bar_pub)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='bar_pub'
, (VALUES
    ('bar_service', 'Bar service', 'hourly', NULL, 10),
    ('table_booking', 'Table booking', 'hourly', NULL, 20),
    ('event_booking', 'Event booking', 'hourly', NULL, 30),
    ('cellar_task', 'Cellar task', 'hourly', NULL, 40),
    ('stock_check', 'Stock check', 'per_visit', NULL, 50),
    ('staff_rota', 'Staff rota', 'hourly', NULL, 60),
    ('cleaning_task', 'Cleaning task', 'hourly', NULL, 70),
    ('customer_incident', 'Customer incident', 'hourly', NULL, 80)
) AS v(code, name, pm, unit, sort)
WHERE g.code='hospitality'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 11.5 (hotel_bnb)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='hotel_bnb'
, (VALUES
    ('room_booking', 'Room booking', 'hourly', NULL, 10),
    ('check_in', 'Check in', 'per_visit', NULL, 20),
    ('check_out', 'Check out', 'per_visit', NULL, 30),
    ('room_cleaning', 'Room cleaning', 'hourly', NULL, 40),
    ('guest_message', 'Guest message', 'hourly', NULL, 50),
    ('maintenance_request', 'Maintenance request', 'hourly', NULL, 60),
    ('breakfast_service', 'Breakfast service', 'hourly', NULL, 70),
    ('damage_report', 'Damage report', 'hourly', NULL, 80),
    ('review_follow_up', 'Review follow up', 'per_visit', NULL, 90)
) AS v(code, name, pm, unit, sort)
WHERE g.code='hospitality'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 11.6 (event_catering)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='event_catering'
, (VALUES
    ('menu_planning', 'Menu planning', 'hourly', NULL, 10),
    ('catering_setup', 'Catering setup', 'hourly', NULL, 20),
    ('food_preparation', 'Food preparation', 'hourly', NULL, 30),
    ('service_staff', 'Service staff', 'hourly', NULL, 40),
    ('equipment_hire', 'Equipment hire', 'hourly', NULL, 50),
    ('delivery', 'Delivery', 'hourly', NULL, 60),
    ('setup', 'Setup', 'hourly', NULL, 70),
    ('breakdown', 'Breakdown', 'hourly', NULL, 80)
) AS v(code, name, pm, unit, sort)
WHERE g.code='hospitality'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 11.7 (private_chef)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='private_chef'
, (VALUES
    ('menu_consultation', 'Menu consultation', 'per_visit', NULL, 10),
    ('ingredient_sourcing', 'Ingredient sourcing', 'hourly', NULL, 20),
    ('meal_preparation', 'Meal preparation', 'hourly', NULL, 30),
    ('private_dining', 'Private dining', 'hourly', NULL, 40),
    ('kitchen_cleanup', 'Kitchen cleanup', 'hourly', NULL, 50),
    ('travel_charge', 'Travel charge', 'travel', NULL, 60),
    ('event_service', 'Event service', 'hourly', NULL, 70)
) AS v(code, name, pm, unit, sort)
WHERE g.code='hospitality'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 11.8 (food_production)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='food_production'
, (VALUES
    ('batch_production', 'Batch production', 'hourly', NULL, 10),
    ('food_preparation', 'Food preparation', 'hourly', NULL, 20),
    ('packaging', 'Packaging', 'hourly', NULL, 30),
    ('labelling', 'Labelling', 'hourly', NULL, 40),
    ('stock_check', 'Stock check', 'per_visit', NULL, 50),
    ('quality_control', 'Quality control', 'hourly', NULL, 60),
    ('delivery_preparation', 'Delivery preparation', 'hourly', NULL, 70),
    ('cleaning_task', 'Cleaning task', 'hourly', NULL, 80)
) AS v(code, name, pm, unit, sort)
WHERE g.code='hospitality'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 12.1 (event_planning)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='event_planning'
, (VALUES
    ('initial_consultation', 'Initial consultation', 'per_visit', NULL, 10),
    ('event_brief', 'Event brief', 'hourly', NULL, 20),
    ('budget_planning', 'Budget planning', 'fixed', NULL, 30),
    ('supplier_coordination', 'Supplier coordination', 'hourly', NULL, 40),
    ('schedule_planning', 'Schedule planning', 'fixed', NULL, 50),
    ('guest_coordination', 'Guest coordination', 'hourly', NULL, 60),
    ('risk_assessment', 'Risk assessment', 'per_visit', NULL, 70),
    ('setup_coordination', 'Setup coordination', 'hourly', NULL, 80),
    ('breakdown_coordination', 'Breakdown coordination', 'hourly', NULL, 90),
    ('event_report', 'Event report', 'hourly', NULL, 100)
) AS v(code, name, pm, unit, sort)
WHERE g.code='events'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 12.2 (wedding_services)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='wedding_services'
, (VALUES
    ('wedding_consultation', 'Wedding consultation', 'per_visit', NULL, 10),
    ('supplier_coordination', 'Supplier coordination', 'hourly', NULL, 20),
    ('timeline_planning', 'Timeline planning', 'hourly', NULL, 30),
    ('venue_coordination', 'Venue coordination', 'hourly', NULL, 40),
    ('guest_list_support', 'Guest list support', 'hourly', NULL, 50),
    ('setup', 'Setup', 'hourly', NULL, 60),
    ('on_day_coordination', 'On day coordination', 'hourly', NULL, 70),
    ('breakdown', 'Breakdown', 'hourly', NULL, 80)
) AS v(code, name, pm, unit, sort)
WHERE g.code='events'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 12.3 (photography_video)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='photography_video'
, (VALUES
    ('photo_session', 'Photo session', 'per_visit', NULL, 10),
    ('video_shoot', 'Video shoot', 'hourly', NULL, 20),
    ('event_photography', 'Event photography', 'hourly', NULL, 30),
    ('editing', 'Editing', 'hourly', NULL, 40),
    ('retouching', 'Retouching', 'hourly', NULL, 50),
    ('gallery_delivery', 'Gallery delivery', 'hourly', NULL, 60),
    ('travel_charge', 'Travel charge', 'travel', NULL, 70),
    ('equipment_setup', 'Equipment setup', 'hourly', NULL, 80)
) AS v(code, name, pm, unit, sort)
WHERE g.code='events'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 12.4 (entertainment)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='entertainment'
, (VALUES
    ('dj_booking', 'DJ booking', 'hourly', NULL, 10),
    ('performance', 'Performance', 'hourly', NULL, 20),
    ('playlist_preparation', 'Playlist preparation', 'hourly', NULL, 30),
    ('equipment_setup', 'Equipment setup', 'hourly', NULL, 40),
    ('sound_check', 'Sound check', 'per_visit', NULL, 50),
    ('travel_charge', 'Travel charge', 'travel', NULL, 60),
    ('pack_down', 'Pack down', 'hourly', NULL, 70)
) AS v(code, name, pm, unit, sort)
WHERE g.code='events'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 12.5 (av_technical)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='av_technical'
, (VALUES
    ('av_setup', 'AV setup', 'hourly', NULL, 10),
    ('sound_setup', 'Sound setup', 'hourly', NULL, 20),
    ('lighting_setup', 'Lighting setup', 'hourly', NULL, 30),
    ('technical_rehearsal', 'Technical rehearsal', 'hourly', NULL, 40),
    ('live_operation', 'Live operation', 'hourly', NULL, 50),
    ('equipment_hire', 'Equipment hire', 'hourly', NULL, 60),
    ('pack_down', 'Pack down', 'hourly', NULL, 70),
    ('technical_support', 'Technical support', 'hourly', NULL, 80)
) AS v(code, name, pm, unit, sort)
WHERE g.code='events'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 12.6 (venue_management)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='venue_management'
, (VALUES
    ('venue_booking', 'Venue booking', 'hourly', NULL, 10),
    ('site_visit', 'Site visit', 'per_visit', NULL, 20),
    ('room_setup', 'Room setup', 'hourly', NULL, 30),
    ('cleaning_coordination', 'Cleaning coordination', 'hourly', NULL, 40),
    ('supplier_access', 'Supplier access', 'hourly', NULL, 50),
    ('event_supervision', 'Event supervision', 'hourly', NULL, 60),
    ('incident_report', 'Incident report', 'hourly', NULL, 70)
) AS v(code, name, pm, unit, sort)
WHERE g.code='events'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 12.7 (marquee_equipment)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='marquee_equipment'
, (VALUES
    ('marquee_hire', 'Marquee hire', 'hourly', NULL, 10),
    ('marquee_setup', 'Marquee setup', 'hourly', NULL, 20),
    ('marquee_dismantling', 'Marquee dismantling', 'hourly', NULL, 30),
    ('furniture_hire', 'Furniture hire', 'hourly', NULL, 40),
    ('equipment_delivery', 'Equipment delivery', 'hourly', NULL, 50),
    ('equipment_collection', 'Equipment collection', 'hourly', NULL, 60),
    ('damage_check', 'Damage check', 'per_visit', NULL, 70)
) AS v(code, name, pm, unit, sort)
WHERE g.code='events'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 12.8 (floristry)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='floristry'
, (VALUES
    ('floral_consultation', 'Floral consultation', 'per_visit', NULL, 10),
    ('bouquet_preparation', 'Bouquet preparation', 'hourly', NULL, 20),
    ('venue_flowers', 'Venue flowers', 'hourly', NULL, 30),
    ('table_flowers', 'Table flowers', 'hourly', NULL, 40),
    ('delivery', 'Delivery', 'hourly', NULL, 50),
    ('setup', 'Setup', 'hourly', NULL, 60),
    ('breakdown', 'Breakdown', 'hourly', NULL, 70)
) AS v(code, name, pm, unit, sort)
WHERE g.code='events'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 12.9 (events_catering)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='events_catering'
, (VALUES
    ('catering_staff', 'Catering staff', 'hourly', NULL, 10),
    ('bar_staff', 'Bar staff', 'hourly', NULL, 20),
    ('service_shift', 'Service shift', 'hourly', NULL, 30),
    ('setup', 'Setup', 'hourly', NULL, 40),
    ('breakdown', 'Breakdown', 'hourly', NULL, 50),
    ('stock_check', 'Stock check', 'per_visit', NULL, 60),
    ('guest_service', 'Guest service', 'hourly', NULL, 70)
) AS v(code, name, pm, unit, sort)
WHERE g.code='events'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 12.10 (photobooth_hire)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='photobooth_hire'
, (VALUES
    ('photo_booth_hire', 'Photo booth hire', 'hourly', NULL, 10),
    ('delivery', 'Delivery', 'hourly', NULL, 20),
    ('setup', 'Setup', 'hourly', NULL, 30),
    ('operation', 'Operation', 'hourly', NULL, 40),
    ('print_supply', 'Print supply', 'hourly', NULL, 50),
    ('breakdown', 'Breakdown', 'hourly', NULL, 60),
    ('gallery_delivery', 'Gallery delivery', 'hourly', NULL, 70)
) AS v(code, name, pm, unit, sort)
WHERE g.code='events'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 13.1 (private_tutoring)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='private_tutoring'
, (VALUES
    ('tutoring_session', 'Tutoring session', 'per_visit', NULL, 10),
    ('student_assessment', 'Student assessment', 'per_visit', NULL, 20),
    ('lesson_planning', 'Lesson planning', 'per_visit', NULL, 30),
    ('homework_setting', 'Homework setting', 'hourly', NULL, 40),
    ('progress_report', 'Progress report', 'per_visit', NULL, 50),
    ('parent_communication', 'Parent communication', 'hourly', NULL, 60),
    ('exam_preparation', 'Exam preparation', 'hourly', NULL, 70)
) AS v(code, name, pm, unit, sort)
WHERE g.code='education'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 13.2 (music_tuition)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='music_tuition'
, (VALUES
    ('music_lesson', 'Music lesson', 'per_visit', NULL, 10),
    ('practice_plan', 'Practice plan', 'hourly', NULL, 20),
    ('progress_review', 'Progress review', 'per_visit', NULL, 30),
    ('exam_preparation', 'Exam preparation', 'hourly', NULL, 40),
    ('instrument_support', 'Instrument support', 'hourly', NULL, 50),
    ('parent_communication', 'Parent communication', 'hourly', NULL, 60)
) AS v(code, name, pm, unit, sort)
WHERE g.code='education'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 13.3 (driving_instruction)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='driving_instruction'
, (VALUES
    ('driving_lesson', 'Driving lesson', 'per_visit', NULL, 10),
    ('mock_test', 'Mock test', 'hourly', NULL, 20),
    ('theory_support', 'Theory support', 'hourly', NULL, 30),
    ('progress_review', 'Progress review', 'per_visit', NULL, 40),
    ('test_booking_support', 'Test booking support', 'hourly', NULL, 50),
    ('route_practice', 'Route practice', 'hourly', NULL, 60)
) AS v(code, name, pm, unit, sort)
WHERE g.code='education'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 13.4 (language_teaching)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='language_teaching'
, (VALUES
    ('language_lesson', 'Language lesson', 'per_visit', NULL, 10),
    ('assessment', 'Assessment', 'per_visit', NULL, 20),
    ('homework_setting', 'Homework setting', 'hourly', NULL, 30),
    ('speaking_practice', 'Speaking practice', 'hourly', NULL, 40),
    ('writing_practice', 'Writing practice', 'hourly', NULL, 50),
    ('progress_report', 'Progress report', 'per_visit', NULL, 60)
) AS v(code, name, pm, unit, sort)
WHERE g.code='education'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 13.5 (corporate_training)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='corporate_training'
, (VALUES
    ('training_session', 'Training session', 'per_visit', NULL, 10),
    ('training_plan', 'Training plan', 'hourly', NULL, 20),
    ('workshop_delivery', 'Workshop delivery', 'hourly', NULL, 30),
    ('attendance_tracking', 'Attendance tracking', 'hourly', NULL, 40),
    ('feedback_collection', 'Feedback collection', 'hourly', NULL, 50),
    ('certificate_preparation', 'Certificate preparation', 'hourly', NULL, 60)
) AS v(code, name, pm, unit, sort)
WHERE g.code='education'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 13.6 (vocational_courses)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='vocational_courses'
, (VALUES
    ('course_session', 'Course session', 'per_visit', NULL, 10),
    ('practical_assessment', 'Practical assessment', 'per_visit', NULL, 20),
    ('theory_session', 'Theory session', 'per_visit', NULL, 30),
    ('progress_tracking', 'Progress tracking', 'per_visit', NULL, 40),
    ('portfolio_support', 'Portfolio support', 'hourly', NULL, 50),
    ('certification_support', 'Certification support', 'hourly', NULL, 60)
) AS v(code, name, pm, unit, sort)
WHERE g.code='education'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 13.7 (online_courses)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='online_courses'
, (VALUES
    ('online_lesson', 'Online lesson', 'per_visit', NULL, 10),
    ('course_setup', 'Course setup', 'hourly', NULL, 20),
    ('student_support', 'Student support', 'hourly', NULL, 30),
    ('progress_review', 'Progress review', 'per_visit', NULL, 40),
    ('content_upload', 'Content upload', 'hourly', NULL, 50),
    ('feedback', 'Feedback', 'hourly', NULL, 60)
) AS v(code, name, pm, unit, sort)
WHERE g.code='education'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 13.8 (childcare_education)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='childcare_education'
, (VALUES
    ('childcare_session', 'Childcare session', 'per_visit', NULL, 10),
    ('activity_planning', 'Activity planning', 'hourly', NULL, 20),
    ('parent_update', 'Parent update', 'hourly', NULL, 30),
    ('incident_note', 'Incident note', 'hourly', NULL, 40),
    ('attendance_record', 'Attendance record', 'hourly', NULL, 50),
    ('development_note', 'Development note', 'hourly', NULL, 60)
) AS v(code, name, pm, unit, sort)
WHERE g.code='education'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 13.9 (arts_creative)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='arts_creative'
, (VALUES
    ('workshop_planning', 'Workshop planning', 'hourly', NULL, 10),
    ('material_preparation', 'Material preparation', 'hourly', NULL, 20),
    ('session_delivery', 'Session delivery', 'per_visit', NULL, 30),
    ('student_feedback', 'Student feedback', 'hourly', NULL, 40),
    ('artwork_review', 'Artwork review', 'per_visit', NULL, 50),
    ('cleanup', 'Cleanup', 'hourly', NULL, 60)
) AS v(code, name, pm, unit, sort)
WHERE g.code='education'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 13.10 (sports_instruction)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='sports_instruction'
, (VALUES
    ('sports_lesson', 'Sports lesson', 'per_visit', NULL, 10),
    ('skill_assessment', 'Skill assessment', 'per_visit', NULL, 20),
    ('training_plan', 'Training plan', 'hourly', NULL, 30),
    ('progress_review', 'Progress review', 'per_visit', NULL, 40),
    ('safety_note', 'Safety note', 'hourly', NULL, 50),
    ('group_session', 'Group session', 'per_visit', NULL, 60)
) AS v(code, name, pm, unit, sort)
WHERE g.code='education'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 14.1 (it_support)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='it_support'
, (VALUES
    ('support_ticket', 'Support ticket', 'hourly', NULL, 10),
    ('remote_support', 'Remote support', 'hourly', NULL, 20),
    ('on_site_support', 'On site support', 'hourly', NULL, 30),
    ('device_setup', 'Device setup', 'hourly', NULL, 40),
    ('software_installation', 'Software installation', 'hourly', NULL, 50),
    ('fault_diagnosis', 'Fault diagnosis', 'hourly', NULL, 60),
    ('user_support', 'User support', 'hourly', NULL, 70),
    ('documentation', 'Documentation', 'hourly', NULL, 80)
) AS v(code, name, pm, unit, sort)
WHERE g.code='it_tech'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 14.2 (web_development)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='web_development'
, (VALUES
    ('website_design', 'Website design', 'hourly', NULL, 10),
    ('website_build', 'Website build', 'hourly', NULL, 20),
    ('website_update', 'Website update', 'hourly', NULL, 30),
    ('cms_setup', 'CMS setup', 'hourly', NULL, 40),
    ('bug_fix', 'Bug fix', 'hourly', NULL, 50),
    ('seo_setup', 'SEO setup', 'hourly', NULL, 60),
    ('hosting_setup', 'Hosting setup', 'hourly', NULL, 70),
    ('maintenance', 'Maintenance', 'hourly', NULL, 80)
) AS v(code, name, pm, unit, sort)
WHERE g.code='it_tech'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 14.3 (software_development)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='software_development'
, (VALUES
    ('feature_development', 'Feature development', 'hourly', NULL, 10),
    ('bug_fix', 'Bug fix', 'hourly', NULL, 20),
    ('api_integration', 'API integration', 'hourly', NULL, 30),
    ('database_work', 'Database work', 'hourly', NULL, 40),
    ('testing', 'Testing', 'hourly', NULL, 50),
    ('deployment', 'Deployment', 'hourly', NULL, 60),
    ('documentation', 'Documentation', 'hourly', NULL, 70),
    ('code_review', 'Code review', 'per_visit', NULL, 80)
) AS v(code, name, pm, unit, sort)
WHERE g.code='it_tech'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 14.4 (network_infrastructure)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='network_infrastructure'
, (VALUES
    ('network_installation', 'Network installation', 'hourly', NULL, 10),
    ('router_setup', 'Router setup', 'hourly', NULL, 20),
    ('switch_setup', 'Switch setup', 'hourly', NULL, 30),
    ('wifi_setup', 'WiFi setup', 'hourly', NULL, 40),
    ('cable_installation', 'Cable installation', 'hourly', NULL, 50),
    ('network_troubleshooting', 'Network troubleshooting', 'hourly', NULL, 60),
    ('performance_test', 'Performance test', 'hourly', NULL, 70)
) AS v(code, name, pm, unit, sort)
WHERE g.code='it_tech'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 14.5 (cybersecurity)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='cybersecurity'
, (VALUES
    ('security_review', 'Security review', 'per_visit', NULL, 10),
    ('vulnerability_check', 'Vulnerability check', 'per_visit', NULL, 20),
    ('access_audit', 'Access audit', 'hourly', NULL, 30),
    ('mfa_setup', 'MFA setup', 'hourly', NULL, 40),
    ('incident_response', 'Incident response', 'hourly', NULL, 50),
    ('policy_setup', 'Policy setup', 'hourly', NULL, 60),
    ('backup_review', 'Backup review', 'per_visit', NULL, 70)
) AS v(code, name, pm, unit, sort)
WHERE g.code='it_tech'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 14.6 (cloud_services)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='cloud_services'
, (VALUES
    ('cloud_setup', 'Cloud setup', 'hourly', NULL, 10),
    ('cloud_migration', 'Cloud migration', 'hourly', NULL, 20),
    ('account_setup', 'Account setup', 'hourly', NULL, 30),
    ('storage_configuration', 'Storage configuration', 'hourly', NULL, 40),
    ('backup_setup', 'Backup setup', 'hourly', NULL, 50),
    ('monitoring', 'Monitoring', 'hourly', NULL, 60),
    ('cost_review', 'Cost review', 'per_visit', NULL, 70)
) AS v(code, name, pm, unit, sort)
WHERE g.code='it_tech'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 14.7 (digital_marketing)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='digital_marketing'
, (VALUES
    ('seo_audit', 'SEO audit', 'hourly', NULL, 10),
    ('keyword_research', 'Keyword research', 'hourly', NULL, 20),
    ('website_optimisation', 'Website optimisation', 'hourly', NULL, 30),
    ('content_planning', 'Content planning', 'hourly', NULL, 40),
    ('campaign_setup', 'Campaign setup', 'hourly', NULL, 50),
    ('analytics_review', 'Analytics review', 'per_visit', NULL, 60),
    ('report_preparation', 'Report preparation', 'fixed', NULL, 70)
) AS v(code, name, pm, unit, sort)
WHERE g.code='it_tech'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 14.8 (graphic_design)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='graphic_design'
, (VALUES
    ('logo_design', 'Logo design', 'hourly', NULL, 10),
    ('brand_design', 'Brand design', 'hourly', NULL, 20),
    ('print_design', 'Print design', 'hourly', NULL, 30),
    ('digital_design', 'Digital design', 'hourly', NULL, 40),
    ('social_media_design', 'Social media design', 'hourly', NULL, 50),
    ('layout_work', 'Layout work', 'hourly', NULL, 60),
    ('file_preparation', 'File preparation', 'hourly', NULL, 70)
) AS v(code, name, pm, unit, sort)
WHERE g.code='it_tech'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 14.9 (data_analytics)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='data_analytics'
, (VALUES
    ('data_import', 'Data import', 'hourly', NULL, 10),
    ('report_creation', 'Report creation', 'hourly', NULL, 20),
    ('dashboard_setup', 'Dashboard setup', 'hourly', NULL, 30),
    ('data_cleaning', 'Data cleaning', 'hourly', NULL, 40),
    ('analysis', 'Analysis', 'hourly', NULL, 50),
    ('kpi_report', 'KPI report', 'hourly', NULL, 60),
    ('automation', 'Automation', 'hourly', NULL, 70)
) AS v(code, name, pm, unit, sort)
WHERE g.code='it_tech'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 14.10 (ecommerce)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='ecommerce'
, (VALUES
    ('shop_setup', 'Shop setup', 'hourly', NULL, 10),
    ('product_listing', 'Product listing', 'hourly', NULL, 20),
    ('payment_setup', 'Payment setup', 'hourly', NULL, 30),
    ('order_flow_setup', 'Order flow setup', 'hourly', NULL, 40),
    ('inventory_setup', 'Inventory setup', 'hourly', NULL, 50),
    ('shipping_setup', 'Shipping setup', 'hourly', NULL, 60),
    ('store_maintenance', 'Store maintenance', 'hourly', NULL, 70)
) AS v(code, name, pm, unit, sort)
WHERE g.code='it_tech'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 15.1 (physical_retail)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='physical_retail'
, (VALUES
    ('product_sale', 'Product sale', 'hourly', NULL, 10),
    ('customer_service', 'Customer service', 'hourly', NULL, 20),
    ('stock_check', 'Stock check', 'per_visit', NULL, 30),
    ('stock_adjustment', 'Stock adjustment', 'hourly', NULL, 40),
    ('refund', 'Refund', 'hourly', NULL, 50),
    ('return', 'Return', 'hourly', NULL, 60),
    ('supplier_order', 'Supplier order', 'hourly', NULL, 70),
    ('till_reconciliation', 'Till reconciliation', 'hourly', NULL, 80),
    ('display_setup', 'Display setup', 'hourly', NULL, 90)
) AS v(code, name, pm, unit, sort)
WHERE g.code='retail'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 15.2 (online_retail)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='online_retail'
, (VALUES
    ('online_order', 'Online order', 'hourly', NULL, 10),
    ('order_fulfilment', 'Order fulfilment', 'hourly', NULL, 20),
    ('product_listing', 'Product listing', 'hourly', NULL, 30),
    ('customer_support', 'Customer support', 'hourly', NULL, 40),
    ('refund', 'Refund', 'hourly', NULL, 50),
    ('return', 'Return', 'hourly', NULL, 60),
    ('shipping', 'Shipping', 'hourly', NULL, 70),
    ('stock_update', 'Stock update', 'hourly', NULL, 80)
) AS v(code, name, pm, unit, sort)
WHERE g.code='retail'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 15.3 (wholesale)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='wholesale'
, (VALUES
    ('wholesale_order', 'Wholesale order', 'hourly', NULL, 10),
    ('account_customer_support', 'Account customer support', 'hourly', NULL, 20),
    ('bulk_pricing', 'Bulk pricing', 'hourly', NULL, 30),
    ('stock_allocation', 'Stock allocation', 'hourly', NULL, 40),
    ('invoice_preparation', 'Invoice preparation', 'hourly', NULL, 50),
    ('delivery_coordination', 'Delivery coordination', 'hourly', NULL, 60)
) AS v(code, name, pm, unit, sort)
WHERE g.code='retail'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 15.4 (market_stall)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='market_stall'
, (VALUES
    ('stall_setup', 'Stall setup', 'hourly', NULL, 10),
    ('stall_sale', 'Stall sale', 'hourly', NULL, 20),
    ('stock_packing', 'Stock packing', 'hourly', NULL, 30),
    ('stock_unpacking', 'Stock unpacking', 'hourly', NULL, 40),
    ('payment_handling', 'Payment handling', 'hourly', NULL, 50),
    ('end_of_day_count', 'End of day count', 'hourly', NULL, 60),
    ('display_setup', 'Display setup', 'hourly', NULL, 70)
) AS v(code, name, pm, unit, sort)
WHERE g.code='retail'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 15.5 (specialist_retail)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='specialist_retail'
, (VALUES
    ('trade_counter_sale', 'Trade counter sale', 'hourly', NULL, 10),
    ('special_order', 'Special order', 'hourly', NULL, 20),
    ('technical_advice', 'Technical advice', 'hourly', NULL, 30),
    ('product_sourcing', 'Product sourcing', 'hourly', NULL, 40),
    ('quote_preparation', 'Quote preparation', 'hourly', NULL, 50),
    ('account_customer_support', 'Account customer support', 'hourly', NULL, 60)
) AS v(code, name, pm, unit, sort)
WHERE g.code='retail'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 16.1 (manned_guarding)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='manned_guarding'
, (VALUES
    ('guard_shift', 'Guard shift', 'per_visit', NULL, 10),
    ('site_patrol', 'Site patrol', 'per_visit', NULL, 20),
    ('access_control', 'Access control', 'hourly', NULL, 30),
    ('visitor_log', 'Visitor log', 'per_visit', NULL, 40),
    ('incident_report', 'Incident report', 'hourly', NULL, 50),
    ('handover', 'Handover', 'hourly', NULL, 60),
    ('welfare_check', 'Welfare check', 'per_visit', NULL, 70),
    ('lock_up', 'Lock up', 'hourly', NULL, 80),
    ('unlock', 'Unlock', 'hourly', NULL, 90)
) AS v(code, name, pm, unit, sort)
WHERE g.code='security'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 16.2 (door_supervision)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='door_supervision'
, (VALUES
    ('door_shift', 'Door shift', 'per_visit', NULL, 10),
    ('entry_control', 'Entry control', 'hourly', NULL, 20),
    ('id_check', 'ID check', 'per_visit', NULL, 30),
    ('incident_handling', 'Incident handling', 'hourly', NULL, 40),
    ('queue_management', 'Queue management', 'hourly', NULL, 50),
    ('refusal_log', 'Refusal log', 'hourly', NULL, 60),
    ('end_of_shift_report', 'End of shift report', 'hourly', NULL, 70)
) AS v(code, name, pm, unit, sort)
WHERE g.code='security'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 16.3 (cctv_monitoring)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='cctv_monitoring'
, (VALUES
    ('cctv_monitoring_shift', 'CCTV monitoring shift', 'per_visit', NULL, 10),
    ('incident_detection', 'Incident detection', 'hourly', NULL, 20),
    ('incident_report', 'Incident report', 'hourly', NULL, 30),
    ('camera_check', 'Camera check', 'per_visit', NULL, 40),
    ('evidence_export', 'Evidence export', 'hourly', NULL, 50),
    ('alarm_verification', 'Alarm verification', 'hourly', NULL, 60)
) AS v(code, name, pm, unit, sort)
WHERE g.code='security'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 16.4 (alarm_response)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='alarm_response'
, (VALUES
    ('alarm_response', 'Alarm response', 'per_visit', NULL, 10),
    ('key_holding_visit', 'Key holding visit', 'per_visit', NULL, 20),
    ('site_attendance', 'Site attendance', 'hourly', NULL, 30),
    ('police_coordination', 'Police coordination', 'hourly', NULL, 40),
    ('reset_alarm', 'Reset alarm', 'hourly', NULL, 50),
    ('incident_report', 'Incident report', 'hourly', NULL, 60),
    ('key_audit', 'Key audit', 'hourly', NULL, 70)
) AS v(code, name, pm, unit, sort)
WHERE g.code='security'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 16.5 (retail_security)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='retail_security'
, (VALUES
    ('store_patrol', 'Store patrol', 'per_visit', NULL, 10),
    ('shoplifting_incident', 'Shoplifting incident', 'hourly', NULL, 20),
    ('incident_report', 'Incident report', 'hourly', NULL, 30),
    ('cctv_review', 'CCTV review', 'per_visit', NULL, 40),
    ('staff_support', 'Staff support', 'hourly', NULL, 50),
    ('loss_prevention_audit', 'Loss prevention audit', 'hourly', NULL, 60)
) AS v(code, name, pm, unit, sort)
WHERE g.code='security'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 16.6 (mobile_patrol)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='mobile_patrol'
, (VALUES
    ('mobile_patrol', 'Mobile patrol', 'per_visit', NULL, 10),
    ('site_check', 'Site check', 'per_visit', NULL, 20),
    ('lock_check', 'Lock check', 'per_visit', NULL, 30),
    ('unlock_service', 'Unlock service', 'hourly', NULL, 40),
    ('incident_response', 'Incident response', 'hourly', NULL, 50),
    ('patrol_report', 'Patrol report', 'per_visit', NULL, 60),
    ('route_completion', 'Route completion', 'hourly', NULL, 70)
) AS v(code, name, pm, unit, sort)
WHERE g.code='security'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 17.1 (arable_farming)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='arable_farming'
, (VALUES
    ('field_inspection', 'Field inspection', 'per_visit', NULL, 10),
    ('cultivation', 'Cultivation', 'hourly', NULL, 20),
    ('drilling', 'Drilling', 'hourly', NULL, 30),
    ('spraying_coordination', 'Spraying coordination', 'hourly', NULL, 40),
    ('fertiliser_application', 'Fertiliser application', 'hourly', NULL, 50),
    ('harvest_work', 'Harvest work', 'hourly', NULL, 60),
    ('crop_monitoring', 'Crop monitoring', 'hourly', NULL, 70),
    ('irrigation_check', 'Irrigation check', 'per_visit', NULL, 80),
    ('machinery_operation', 'Machinery operation', 'hourly', NULL, 90),
    ('weather_dependent_task', 'Weather dependent task', 'hourly', NULL, 100)
) AS v(code, name, pm, unit, sort)
WHERE g.code='agriculture'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 17.2 (livestock_farming)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='livestock_farming'
, (VALUES
    ('livestock_check', 'Livestock check', 'per_visit', NULL, 10),
    ('feeding', 'Feeding', 'hourly', NULL, 20),
    ('animal_movement', 'Animal movement', 'hourly', NULL, 30),
    ('health_check', 'Health check', 'per_visit', NULL, 40),
    ('vet_coordination', 'Vet coordination', 'hourly', NULL, 50),
    ('fencing_check', 'Fencing check', 'per_m', 'm', 60),
    ('water_check', 'Water check', 'per_visit', NULL, 70),
    ('bedding', 'Bedding', 'hourly', NULL, 80),
    ('record_update', 'Record update', 'hourly', NULL, 90)
) AS v(code, name, pm, unit, sort)
WHERE g.code='agriculture'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 17.3 (horticulture)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='horticulture'
, (VALUES
    ('plant_production', 'Plant production', 'hourly', NULL, 10),
    ('greenhouse_work', 'Greenhouse work', 'hourly', NULL, 20),
    ('planting', 'Planting', 'hourly', NULL, 30),
    ('harvesting', 'Harvesting', 'hourly', NULL, 40),
    ('irrigation', 'Irrigation', 'hourly', NULL, 50),
    ('pest_check', 'Pest check', 'per_visit', NULL, 60),
    ('pruning', 'Pruning', 'hourly', NULL, 70),
    ('packing', 'Packing', 'hourly', NULL, 80),
    ('dispatch_preparation', 'Dispatch preparation', 'hourly', NULL, 90)
) AS v(code, name, pm, unit, sort)
WHERE g.code='agriculture'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 17.4 (equestrian)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='equestrian'
, (VALUES
    ('stable_work', 'Stable work', 'hourly', NULL, 10),
    ('yard_maintenance', 'Yard maintenance', 'hourly', NULL, 20),
    ('horse_care', 'Horse care', 'hourly', NULL, 30),
    ('feeding', 'Feeding', 'hourly', NULL, 40),
    ('turnout', 'Turnout', 'hourly', NULL, 50),
    ('mucking_out', 'Mucking out', 'hourly', NULL, 60),
    ('fencing_repair', 'Fencing repair', 'per_m', 'm', 70),
    ('arena_maintenance', 'Arena maintenance', 'hourly', NULL, 80)
) AS v(code, name, pm, unit, sort)
WHERE g.code='agriculture'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 17.5 (land_management)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='land_management'
, (VALUES
    ('estate_inspection', 'Estate inspection', 'per_visit', NULL, 10),
    ('woodland_maintenance', 'Woodland maintenance', 'hourly', NULL, 20),
    ('boundary_check', 'Boundary check', 'per_visit', NULL, 30),
    ('track_maintenance', 'Track maintenance', 'hourly', NULL, 40),
    ('drainage_check', 'Drainage check', 'per_visit', NULL, 50),
    ('vegetation_control', 'Vegetation control', 'hourly', NULL, 60),
    ('fencing', 'Fencing', 'per_m', 'm', 70),
    ('habitat_management', 'Habitat management', 'hourly', NULL, 80)
) AS v(code, name, pm, unit, sort)
WHERE g.code='agriculture'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 17.6 (agricultural_contracting)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='agricultural_contracting'
, (VALUES
    ('tractor_work', 'Tractor work', 'daily', NULL, 10),
    ('machinery_hire_with_operator', 'Machinery hire with operator', 'daily', NULL, 20),
    ('field_work', 'Field work', 'hourly', NULL, 30),
    ('hedge_cutting', 'Hedge cutting', 'hourly', NULL, 40),
    ('ditching', 'Ditching', 'hourly', NULL, 50),
    ('baling', 'Baling', 'hourly', NULL, 60),
    ('spraying', 'Spraying', 'hourly', NULL, 70),
    ('spreading', 'Spreading', 'hourly', NULL, 80),
    ('harvest_support', 'Harvest support', 'hourly', NULL, 90)
) AS v(code, name, pm, unit, sort)
WHERE g.code='agriculture'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- 17.7 (forestry)
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, s.id, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
JOIN crm.industry_subtypes s ON s.industry_group_id=g.id AND s.code='forestry'
, (VALUES
    ('tree_planting', 'Tree planting', 'hourly', NULL, 10),
    ('woodland_thinning', 'Woodland thinning', 'hourly', NULL, 20),
    ('timber_cutting', 'Timber cutting', 'hourly', NULL, 30),
    ('brash_clearance', 'Brash clearance', 'hourly', NULL, 40),
    ('track_clearance', 'Track clearance', 'hourly', NULL, 50),
    ('fencing', 'Fencing', 'per_m', 'm', 60),
    ('tree_inspection', 'Tree inspection', 'per_visit', NULL, 70),
    ('storm_clearance', 'Storm clearance', 'hourly', NULL, 80)
) AS v(code, name, pm, unit, sort)
WHERE g.code='agriculture'
ON CONFLICT (industry_subtype_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

-- Group 18 (other) — no subtype
INSERT INTO crm.activity_templates
    (industry_group_id, industry_subtype_id, code, name, default_pricing_method, default_unit, sort_order)
SELECT g.id, NULL, v.code, v.name, v.pm, v.unit, v.sort
FROM crm.industry_groups g
, (VALUES
    ('general_client_task', 'General client task', 'hourly', NULL, 10),
    ('admin_task', 'Admin task', 'hourly', NULL, 20),
    ('sales_call', 'Sales call', 'hourly', NULL, 30),
    ('follow_up', 'Follow up', 'per_visit', NULL, 40),
    ('appointment', 'Appointment', 'per_visit', NULL, 50),
    ('quote_preparation', 'Quote preparation', 'hourly', NULL, 60),
    ('invoice_preparation', 'Invoice preparation', 'hourly', NULL, 70),
    ('document_preparation', 'Document preparation', 'hourly', NULL, 80),
    ('file_upload', 'File upload', 'hourly', NULL, 90),
    ('client_message', 'Client message', 'hourly', NULL, 100),
    ('internal_note', 'Internal note', 'hourly', NULL, 110),
    ('payment_reminder', 'Payment reminder', 'hourly', NULL, 120),
    ('meeting', 'Meeting', 'hourly', NULL, 130),
    ('project_task', 'Project task', 'hourly', NULL, 140),
    ('support_request', 'Support request', 'hourly', NULL, 150),
    ('custom_work_type', 'Custom work type', 'hourly', NULL, 160),
    ('research_task', 'Research task', 'hourly', NULL, 170),
    ('data_entry', 'Data entry', 'hourly', NULL, 180),
    ('customer_support', 'Customer support', 'hourly', NULL, 190),
    ('supplier_communication', 'Supplier communication', 'hourly', NULL, 200),
    ('scheduling', 'Scheduling', 'hourly', NULL, 210),
    ('report_preparation', 'Report preparation', 'fixed', NULL, 220),
    ('basic_service_item', 'Basic service item', 'hourly', NULL, 230)
) AS v(code, name, pm, unit, sort)
WHERE g.code='other'
ON CONFLICT (industry_group_id, code) DO UPDATE SET
    name=EXCLUDED.name, default_pricing_method=EXCLUDED.default_pricing_method,
    default_unit=EXCLUDED.default_unit, sort_order=EXCLUDED.sort_order;

INSERT INTO crm.migration_log (filename)
VALUES ('2026_05_06_activity_templates_020.sql')
ON CONFLICT (filename) DO NOTHING;

COMMIT;