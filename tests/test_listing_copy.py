from immo_scanner.explore.listing_copy import (
    NOT_STATED,
    age_class_facts,
    availability_facts,
    building_facts,
    commute_facts,
    description_sections,
    feature_checklist,
    grundschule_facts,
    gymnasium_facts,
    headline,
    is_approximate,
    layout_facts,
    listing_choice,
    listing_id_from_chart,
    listing_id_from_map,
    move_in,
    neighbourhood_indicator_facts,
    original_url,
    party_shares,
    platform_links,
    source_facts,
    station_facts,
    status_badges,
    street_address,
)


def test_listing_choice_uses_the_street_and_the_portal():
    label = listing_choice(
        "Dachgeschoss",
        "Hauptstr. 1, 10245 Berlin / Friedrichshain (Friedrichshain-Kreuzberg)",
        "Friedrichshain",
        "immowelt",
        7,
    )
    assert label == "Dachgeschoss · Hauptstr. 1, 10245 Berlin · Immowelt"
    assert listing_choice(None, None, "Kreuzberg", "immoscout24", 8) == (
        "Listing 8 · Kreuzberg · ImmoScout24"
    )


def test_street_address_drops_the_district_suffix():
    raw = "Hauptstr. 1, 10245 Berlin / Friedrichshain (Friedrichshain-Kreuzberg)"
    assert street_address(raw) == "Hauptstr. 1, 10245 Berlin"
    assert street_address(None) == ""
    assert street_address(float("nan")) == ""


def test_original_url_prefers_a_portal_page_over_the_thinkimmo_api():
    platforms = [
        {"name": "ImmoScout24", "url": "https://www.immobilienscout24.de/expose/1"},
        {"name": "Immowelt", "url": "https://www.immowelt.de/expose/2"},
    ]
    assert (
        original_url("https://api.thinkimmo.com/immo/abc", platforms)
        == "https://www.immobilienscout24.de/expose/1"
    )
    assert original_url("https://www.immowelt.de/expose/9", platforms) == (
        "https://www.immowelt.de/expose/9"
    )
    assert original_url("https://api.thinkimmo.com/immo/abc", []) is None


def test_platform_links_skip_the_link_already_shown():
    platforms = [
        {"name": "ImmoScout24", "url": "https://www.immobilienscout24.de/expose/1"},
        {"name": "Immowelt", "url": "https://www.immowelt.de/expose/2"},
        {"name": "ThinkImmo", "url": "https://api.thinkimmo.com/immo/abc"},
    ]
    assert platform_links(platforms, "https://www.immobilienscout24.de/expose/1") == [
        ("Immowelt", "https://www.immowelt.de/expose/2")
    ]


def test_description_uses_the_listings_portal_and_ignores_the_joined_copy():
    fields = {
        "is24_description": "Helle Wohnung",
        "is24_equipment": "Einbauküche",
        "details_description": "Helle Wohnung\n\nEinbauküche",
        "ti_description": "ThinkImmo text",
    }
    assert description_sections("immoscout24", fields) == [
        ("Description", "Helle Wohnung"),
        ("Equipment", "Einbauküche"),
    ]


def test_description_falls_back_when_the_portal_text_is_blank():
    fields = {
        "is24_description": "  ",
        "ka_description": "Kleinanzeigen text",
        "details_description": "Generic page text",
    }
    assert description_sections("immoscout24", fields) == [("Description", "Kleinanzeigen text")]
    assert description_sections("immoscout24", {"details_description": "Generic page text"}) == [
        ("Description", "Generic page text")
    ]
    assert description_sections("immoscout24", {}) == []


def test_click_payloads_yield_the_listing_id():
    map_selection = {"objects": {"listings": [{"id": 12.0, "title": "A"}]}}
    assert listing_id_from_map(map_selection) == 12
    assert listing_id_from_map({"objects": {}}) is None
    assert listing_id_from_chart({"listing": [{"id": 7}]}) == 7
    assert listing_id_from_chart({"listing": {"id": [8]}}) == 8
    assert listing_id_from_chart({}) is None


ROW = {
    "price": 450_000,
    "price_per_m2": 5000,
    "living_area": 90,
    "rooms": 3,
    "total_score": 0.8,
    "thinkimmo_price_to_market_pct": 96.4,
    "has_elevator": False,
    "is_rented": True,
    "hausgeld": 270,
    "commute_minutes": 20,
    "commute_workplace2_minutes": 30,
    "nearest_sbahn": "Ostkreuz",
    "nearest_sbahn_m": 400,
}


def test_headline_has_eight_figures_including_commute_and_move_in():
    assert headline(ROW) == [
        ("Price", "450,000 €"),
        ("€/m²", "5000"),
        ("Living area", "90 m²"),
        ("Rooms", "3"),
        ("Score", "0.80"),
        ("Of market value", "96%"),
        ("Commute", "24 min"),
        ("Move-in", "Let"),
    ]
    assert [value for _, value in headline({})] == ["–"] * 7 + [NOT_STATED]
    assert headline({"commute_minutes": 35})[6] == ("Commute", "35 min")


def test_move_in_reads_the_rented_flag_and_the_move_in_date():
    assert move_in({"is_rented": True, "available_from": "01.04.2026"})[:2] == (
        "Let",
        "from 01.04.2026",
    )
    assert move_in({"is_rented": False})[:2] == ("Free", None)
    assert move_in({"is_rented": False, "available_from": "frei ab sofort"})[:2] == (
        "Free",
        "now",
    )
    unflagged = move_in({"available_from": "3.Quartal 2027"})
    assert unflagged[:2] == ("Free", "from 3.Quartal 2027")
    assert "Not marked as let" in unflagged[2]
    assert move_in({})[:2] == (NOT_STATED, None)


def test_feature_checklist_keeps_not_stated_apart_from_no():
    checklist = dict(feature_checklist({**ROW, "balcony_count": 2, "has_balcony": True}))
    assert checklist["Elevator"] == "No"
    assert checklist["Cellar"] == NOT_STATED
    assert checklist["Balcony"] == "Yes (2)"
    assert dict(feature_checklist({"balcony_count": 0}))["Balcony"] == "No"


def test_section_builders_keep_known_values():
    assert ("Rented", "Vermietet") in availability_facts(ROW)
    assert ("Hausgeld per m²", "3.00 €/m² per month") in building_facts(ROW)
    assert ("Weighted average (3:2)", "24 min") in commute_facts(ROW)
    stations = station_facts(ROW, {"Ostkreuz": "S3, S5"})
    assert ("Nearest S-Bahn", "Ostkreuz (400 m) · S3, S5") in stations
    assert layout_facts({}) == []


def test_status_badges_flag_decisive_facts():
    assert status_badges(ROW) == ["Rented", "Milieuschutz not stated"]
    assert status_badges({"is_rented": False, "is_new_build": True, "milieuschutz": "Yes"}) == [
        "New build",
        "Vacant",
        "Milieuschutz",
    ]
    assert status_badges({"milieuschutz": "Closeby"}) == [
        "Rental status not stated",
        "Milieuschutz nearby",
    ]
    assert status_badges({"milieuschutz": "No"}) == [
        "Rental status not stated",
        "No Milieuschutz",
    ]
    assert status_badges({"milieuschutz": "In quarter"}) == [
        "Rental status not stated",
        "Milieuschutz in quarter",
    ]
    flags = {"status": "removed", "is_leasehold": True, "has_monument_protection": "true"}
    assert status_badges(flags) == [
        "Removed",
        "Rental status not stated",
        "Milieuschutz not stated",
        "Leasehold",
        "Listed monument",
    ]


def test_age_class_and_school_facts():
    age = age_class_facts(
        {"class_code": "B", "year_from": None, "year_to": 1870, "climate_risk_score": 0}
    )
    assert ("Age class", "B (until 1870)") in age
    assert age_class_facts(None) == []
    school = dict(
        grundschule_facts(
            {"ganztag": "gebunden", "europa_schule": True, "partner_language": "Französisch"}
        )
    )
    assert school["All-day"].startswith("Gebunden")
    assert school["Europa-Schule"] == "Yes, Französisch"
    gym = dict(
        gymnasium_facts(
            {
                "abi_average": 1.9,
                "abi_year": "2025",
                "admission_places": 120,
                "first_choices": 210,
                "starts_year_5": True,
                "profile_mint": True,
            }
        )
    )
    assert gym["Abitur average"] == "1.90 (2025)"
    assert gym["First choices / places"] == "210 / 120"
    assert gym["Starts in"] == "year 5"
    assert gym["Profile"] == "MINT"


def test_sources_name_the_geocoding_precision():
    row = {"source": "immowelt", "geo_precision": "postcode", "first_seen": "2026-10-03 15:48"}
    pairs = dict(source_facts(row, {"geocoded_query": "postcode:10551"}))
    assert pairs["Location"] == "Postcode centre only"
    assert pairs["First seen"] == "2026-10-03"
    assert is_approximate(row)
    assert not is_approximate({"geo_precision": "exact"})


def test_party_shares_keep_known_parties_in_order():
    row = {"agh_vote_afd": 16.0, "agh_vote_linke": 25.5}
    assert party_shares(row) == [("linke", "Linke", 25.5), ("afd", "AfD", 16.0)]


def test_neighbourhood_indicator_facts_format_requested_values():
    row = {
        "agh_vote_gruene": 24.12,
        "agh_vote_afd": 8.34,
        "german_citizen_share": 79.45,
        "oic_share": 12.56,
        "crime_rate": 2345.67,
        "incident_rate": 0.214,
        "est_abitur_pct": 22.34,
        "est_hochschule_pct": 31.56,
    }
    areas = {
        "nearby_school_abi_average": 2.234,
        "nearby_school_type_level": 3.456,
        "sgb2_share": 5.67,
    }
    references = {
        "agh_vote_gruene": {"bezirk": 20.12, "berlin": 18.34},
        "agh_vote_afd": {"bezirk": 10.12, "berlin": 12.34},
        "nearby_school_abi_average": {"bezirk": 2.205, "berlin": 2.267},
        "nearby_school_type_level": {"bezirk": 3.114, "berlin": 3.678},
        "german_share_mss": {"bezirk": 75.12, "berlin": 76.34},
        "sgb2_share": {"bezirk": 4.12, "berlin": 5.34},
        "oic_share": {"bezirk": 14.12, "berlin": 15.34},
        "crime_rate": {"bezirk": 3456.1, "berlin": 3174.33},
        "incident_rate": {"bezirk": 0.1, "berlin": 0.156},
        "est_abitur_pct": {"bezirk": 20.12, "berlin": 18.34},
        "est_hochschule_pct": {"bezirk": 28.16, "berlin": 26.44},
    }

    assert neighbourhood_indicator_facts(row, areas, references) == [
        {
            "Indicator": "AGH Greens vote",
            "Here": "24.1%",
            "Bezirk average": "20.1%",
            "Berlin average": "18.3%",
        },
        {
            "Indicator": "AGH AfD vote",
            "Here": "8.3%",
            "Bezirk average": "10.1%",
            "Berlin average": "12.3%",
        },
        {
            "Indicator": "Abitur / Fachabi (est.)",
            "Here": "22.3%",
            "Bezirk average": "20.1%",
            "Berlin average": "18.3%",
        },
        {
            "Indicator": "Hochschule / Uni (est.)",
            "Here": "31.6%",
            "Bezirk average": "28.2%",
            "Berlin average": "26.4%",
        },
        {
            "Indicator": "Adjusted Abitur average",
            "Here": "2.23",
            "Bezirk average": "2.21",
            "Berlin average": "2.27",
        },
        {
            "Indicator": "Schultypisierung",
            "Here": "3.46",
            "Bezirk average": "3.11",
            "Berlin average": "3.68",
        },
        {
            "Indicator": "German citizens",
            "Here": "79.5%",
            "Bezirk average": "75.1%",
            "Berlin average": "76.3%",
        },
        {
            "Indicator": "Unemployed (SGB II)",
            "Here": "5.7%",
            "Bezirk average": "4.1%",
            "Berlin average": "5.3%",
        },
        {
            "Indicator": "OIC countries",
            "Here": "12.6%",
            "Bezirk average": "14.1%",
            "Berlin average": "15.3%",
        },
        {
            "Indicator": "Neighbourhood crime",
            "Here": "2,346 per 100k",
            "Bezirk average": "3,456 per 100k",
            "Berlin average": "3,174 per 100k",
        },
        {
            "Indicator": "Berliner Register incidents",
            "Here": "0.21 per 1,000/yr",
            "Bezirk average": "0.10 per 1,000/yr",
            "Berlin average": "0.16 per 1,000/yr",
        },
    ]


def test_neighbourhood_indicator_facts_show_missing_references():
    rows = neighbourhood_indicator_facts({}, {}, {})
    assert len(rows) == 11
    assert {row["Here"] for row in rows} == {NOT_STATED}
    assert {row["Bezirk average"] for row in rows} == {NOT_STATED}
