from secretary_clean.catalogue.source_parser import load_catalogue


def test_catalogue_source_invariants():
    catalogue = load_catalogue()
    summary = catalogue.validation_summary()

    assert summary["industry_count"] > 0
    assert summary["subtype_count"] > 0
    assert summary["activity_count"] > 1000
    assert summary["every_subtype_has_activities"] is True
    assert summary["every_activity_has_all_pricing_methods"] is True
    assert summary["every_activity_has_exactly_one_default_method"] is True
    assert len(catalogue.pricing_methods) == 17
    assert len(catalogue.additional_charges) == 12
