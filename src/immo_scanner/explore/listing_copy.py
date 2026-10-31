"""Plain-text facts and the original listing link for one flat.

The Streamlit detail dialog and the Flat detail page render these. Nothing here
imports Streamlit, so the choice of which text and which URL to show can be tested
on its own.
"""

import json
import math
import re
from collections.abc import Mapping
from typing import Any

from immo_scanner.explore.scoring import avg_commute_minutes

# Neubaukompass stores "street, postcode Berlin / Ortsteil (Bezirk)". The district
# is already its own column, and leaving it in makes the address cell so long that
# the street sits off to the left of the visible column.
_DISTRICT_SUFFIX = re.compile(r" / [^/]+ \([^)]*\)\s*$")

SOURCE_LABELS = {
    "bsk": "BSK Immobilien",
    "engelvoelkers": "Engel & Völkers",
    "immoscout24": "ImmoScout24",
    "immowelt": "Immowelt",
    "kleinanzeigen": "Kleinanzeigen",
    "neubaukompass": "Neubaukompass",
    "thinkimmo": "ThinkImmo",
}

# Portal exposé text, in the order a reader expects. listing_details.description is
# often the same prose joined together, so it is only used when no exposé has text.
_PORTAL_SECTIONS: dict[str, tuple[tuple[str, str], ...]] = {
    "immoscout24": (
        ("Description", "is24_description"),
        ("Equipment", "is24_equipment"),
        ("Location", "is24_location"),
        ("Other", "is24_other"),
    ),
    "immowelt": (
        ("Description", "iw_description"),
        ("Location", "iw_location"),
        ("Other", "iw_further"),
    ),
    "kleinanzeigen": (("Description", "ka_description"),),
    "neubaukompass": (
        ("Description", "nbk_description"),
        ("Equipment", "nbk_equipment"),
        ("Location", "nbk_location"),
    ),
    "bsk": (
        ("Equipment", "bsk_equipment"),
        ("Description", "bsk_description"),
        ("Location", "bsk_location"),
    ),
    "engelvoelkers": (
        ("Description", "ev_description"),
        ("Location", "ev_location"),
    ),
    "thinkimmo": (("Description", "ti_description"),),
}
_FALLBACK_ORDER = (
    "immoscout24",
    "immowelt",
    "kleinanzeigen",
    "neubaukompass",
    "bsk",
    "engelvoelkers",
    "thinkimmo",
)

_THINKIMMO_API = "api.thinkimmo.com"


def street_address(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return _DISTRICT_SUFFIX.sub("", value.strip()).strip()


def source_label(source: object) -> str:
    if not isinstance(source, str) or not source.strip():
        return ""
    return SOURCE_LABELS.get(source, source)


def listing_choice(
    title: object,
    address: object,
    place: object,
    source: object,
    listing_id: object,
) -> str:
    """Select-list label: title, street or place, and portal."""
    name = title.strip() if isinstance(title, str) and title.strip() else f"Listing {listing_id}"
    where = street_address(address)
    if not where and isinstance(place, str):
        where = place.strip()
    origin = source_label(source)
    return " · ".join(part for part in (name, where, origin) if part)


def platforms_of(value: object) -> list[dict[str, Any]]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return []
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _http(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    url = value.strip()
    if url.startswith(("http://", "https://")):
        return url
    return None


def detail_page_url(listing_id: object) -> str | None:
    """Path of this app's flat page, or None when the id is missing."""
    if isinstance(listing_id, bool) or listing_id is None:
        return None
    if isinstance(listing_id, float) and math.isnan(listing_id):
        return None
    try:
        number = int(listing_id)
    except (TypeError, ValueError):
        return None
    return f"/flat?listing={number}"


def original_url(listing_url: object, platforms: object) -> str | None:
    """Page where the listing was published.

    ThinkImmo's own API address is not that page. A portal URL from the property's
    platform list is used instead when the stored listing URL points there.
    """
    direct = _http(listing_url)
    portals = [
        url
        for item in platforms_of(platforms)
        if (url := _http(item.get("url"))) and _THINKIMMO_API not in url
    ]
    if direct and _THINKIMMO_API not in direct:
        return direct
    return portals[0] if portals else None


def platform_links(platforms: object, skip_url: str | None = None) -> list[tuple[str, str]]:
    """Other portal pages for the same flat, skipping the link already shown."""
    links: list[tuple[str, str]] = []
    seen = {skip_url} if skip_url else set()
    for item in platforms_of(platforms):
        url = _http(item.get("url"))
        if url is None or _THINKIMMO_API in url or url in seen:
            continue
        seen.add(url)
        name = item.get("name")
        label = name.strip() if isinstance(name, str) and name.strip() else "Portal"
        links.append((label, url))
    return links


def _prose(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def description_sections(source: object, fields: Mapping[str, Any]) -> list[tuple[str, str]]:
    """Heading and body pairs for the saved exposé text.

    The listing's own portal comes first. Another portal's text is used only when
    that one is empty, and the generic detail-page copy only when nothing else is.
    """
    order: list[str] = []
    if isinstance(source, str) and source in _PORTAL_SECTIONS:
        order.append(source)
    order.extend(name for name in _FALLBACK_ORDER if name not in order)
    for name in order:
        sections = [
            (heading, text)
            for heading, column in _PORTAL_SECTIONS[name]
            if (text := _prose(fields.get(column)))
        ]
        if sections:
            return sections
    details = _prose(fields.get("details_description"))
    if details:
        return [("Description", details)]
    return []


def _as_int(value: object) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value) or not value.is_integer():
            return None
        return int(value)
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def listing_id_from_map(selection: object) -> int | None:
    """Listing id from a pydeck single-object selection on the listings layer."""
    if not isinstance(selection, Mapping):
        return None
    objects = selection.get("objects")
    if not isinstance(objects, Mapping):
        return None
    rows = objects.get("listings")
    if not isinstance(rows, list) or not rows or not isinstance(rows[0], Mapping):
        return None
    return _as_int(rows[0].get("id"))


def listing_id_from_chart(selection: object, name: str = "listing") -> int | None:
    """Listing id from an Altair point selection that stores the `id` field.

    Vega-Lite sends either a list of row objects or one object whose `id` value
    is a list. Both shapes are accepted.
    """
    if not isinstance(selection, Mapping):
        return None
    payload = selection.get(name)
    if isinstance(payload, list):
        for item in payload:
            if isinstance(item, Mapping) and (found := _as_int(item.get("id"))) is not None:
                return found
        return None
    if isinstance(payload, Mapping):
        raw = payload.get("id")
        if isinstance(raw, list):
            for item in raw:
                if (found := _as_int(item)) is not None:
                    return found
            return None
        return _as_int(raw)
    return None


def _missing(value: object) -> bool:
    if value is None or isinstance(value, bool):
        return False if isinstance(value, bool) else True
    if isinstance(value, float) and not math.isfinite(value):
        return True
    if isinstance(value, str) and not value.strip():
        return True
    return False


def _number(value: object) -> float | None:
    if isinstance(value, bool) or _missing(value):
        return None
    if isinstance(value, int | float):
        return float(value)
    return None


def _count(value: object) -> str | None:
    number = _number(value)
    if number is None:
        return None
    if number.is_integer():
        return str(int(number))
    return f"{number:.1f}"


def _score(value: object) -> str | None:
    number = _number(value)
    if number is None:
        return None
    return f"{number:.2f}"


def _euro(value: object, *, cents: bool = False) -> str | None:
    number = _number(value)
    if number is None:
        return None
    return f"{number:,.2f} €" if cents else f"{number:,.0f} €"


def _m2(value: object) -> str | None:
    number = _number(value)
    if number is None:
        return None
    return f"{number:,.0f} m²"


def _minutes(value: object) -> str | None:
    number = _number(value)
    if number is None:
        return None
    return f"{number:.0f} min"


def _meters(value: object) -> str | None:
    number = _number(value)
    if number is None:
        return None
    return f"{number:,.0f} m"


def _pct(value: object, digits: int = 1, *, signed: bool = False) -> str | None:
    number = _number(value)
    if number is None:
        return None
    spec = f"+.{digits}f" if signed else f".{digits}f"
    return f"{number:{spec}}%"


def _text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def _yesno(value: object) -> str | None:
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "yes", "y", "1"}:
            return "Yes"
        if lowered in {"false", "no", "n", "0"}:
            return "No"
        return None
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, int | float) and not isinstance(value, bool):
        if isinstance(value, float) and not math.isfinite(value):
            return None
        if value in (0, 1):
            return "Yes" if value else "No"
    return None


def _add(pairs: list[tuple[str, str]], label: str, value: str | None) -> None:
    if value:
        pairs.append((label, value))


def _named_distance(row: Mapping[str, Any], name_key: str, dist_key: str) -> str | None:
    name = _text(row.get(name_key))
    if name is None:
        return None
    dist = _meters(row.get(dist_key))
    return f"{name} ({dist})" if dist else name


def headline(row: Mapping[str, Any]) -> list[tuple[str, str]]:
    """The eight figures shown above the sections. Missing ones stay as a dash."""
    commute = avg_commute_minutes(row.get("commute_minutes"), row.get("commute_workplace2_minutes"))
    return [
        ("Price", _euro(row.get("price")) or "–"),
        ("€/m²", _count(row.get("price_per_m2")) or "–"),
        ("Living area", _m2(row.get("living_area")) or "–"),
        ("Rooms", _count(row.get("rooms")) or "–"),
        ("Score", _score(row.get("total_score")) or "–"),
        ("Of market value", _pct(row.get("thinkimmo_price_to_market_pct"), 0) or "–"),
        ("Commute", _minutes(commute) or "–"),
        ("Move-in", move_in(row)[0]),
    ]


_NOW = re.compile(r"sofort|bezugsfrei|immediately", re.IGNORECASE)


def move_in(row: Mapping[str, Any]) -> tuple[str, str | None, str]:
    """Whether you can move in yourself: value, when, and how sure that is.

    A let flat (vermietet) is only free once the tenancy ends. Most listings do
    not say either way; a move-in date without a rented flag then counts as free.
    """
    rented = _yesno(row.get("is_rented"))
    available = _text(row.get("available_from"))
    when = None
    if available:
        when = "now" if _NOW.search(available) else f"from {available}"
    if rented == "Yes":
        return "Let", when, "The flat is let. You can only move in once the tenancy ends."
    if rented == "No":
        return "Free", when, "The listing says the flat is vacant (unvermietet)."
    if when:
        return "Free", when, "Not marked as let. The date is the listing's move-in date."
    return NOT_STATED, None, "The listing does not say whether the flat is let."


def status_badges(row: Mapping[str, Any]) -> list[str]:
    """Facts that change the decision on their own, shown as badges under the title."""
    badges: list[str] = []
    if row.get("status") == "removed":
        badges.append("Removed")
    if row.get("is_new_build") is True:
        badges.append("New build")
    rented = _yesno(row.get("is_rented"))
    badges.append({"Yes": "Rented", "No": "Vacant"}.get(rented or "", "Rental status not stated"))
    badges.append(_milieuschutz_badge(row.get("milieuschutz")))
    if row.get("is_foreclosure") is True:
        badges.append("Foreclosure")
    if row.get("is_leasehold") is True:
        badges.append("Leasehold")
    if _yesno(row.get("has_monument_protection")) == "Yes":
        badges.append("Listed monument")
    return badges


def _milieuschutz_badge(value: object) -> str:
    return {
        "Yes": "Milieuschutz",
        "Closeby": "Milieuschutz nearby",
        "No": "No Milieuschutz",
        "In quarter": "Milieuschutz in quarter",
    }.get(_text(value) or "", "Milieuschutz not stated")


def _floor_label(row: Mapping[str, Any]) -> str | None:
    floor = _count(row.get("floor"))
    total = _count(row.get("total_floors"))
    if floor and total:
        return f"{floor} of {total}"
    return floor or (f"{total} in the building" if total else None)


Pairs = list[tuple[str, str]]

NOT_STATED = "Not stated"


def layout_facts(row: Mapping[str, Any]) -> Pairs:
    pairs: Pairs = []
    _add(pairs, "Bedrooms", _count(row.get("bedrooms")))
    _add(pairs, "Bathrooms", _count(row.get("bathrooms")))
    _add(pairs, "Type", _text(row.get("apartment_type")))
    _add(pairs, "Floor", _floor_label(row))
    _add(pairs, "Flooring", _text(row.get("flooring")))
    _add(pairs, "Bathroom", _text(row.get("bathroom_features")))
    return pairs


def feature_checklist(row: Mapping[str, Any]) -> Pairs:
    """Yes, No or Not stated per feature, so a missing fact is not read as a No."""
    balcony = _yesno(row.get("has_balcony"))
    count = _number(row.get("balcony_count"))
    if count is not None:
        balcony = f"Yes ({count:.0f})" if count > 0 else "No"
    return [
        ("Balcony", balcony or NOT_STATED),
        ("Terrace", _yesno(row.get("has_terrace")) or NOT_STATED),
        ("Elevator", _yesno(row.get("has_elevator")) or NOT_STATED),
        ("Parking", _yesno(row.get("has_parking")) or NOT_STATED),
        ("Cellar", _yesno(row.get("has_cellar")) or NOT_STATED),
        ("Fitted kitchen", _yesno(row.get("has_fitted_kitchen")) or NOT_STATED),
    ]


def availability_facts(row: Mapping[str, Any]) -> Pairs:
    pairs: Pairs = []
    _add(pairs, "Available from", _text(row.get("available_from")))
    rented = _yesno(row.get("is_rented"))
    _add(pairs, "Rented", {"Yes": "Vermietet", "No": "Unvermietet"}.get(rented or ""))
    _add(pairs, "Provision", _text(row.get("provision")))
    return pairs


def building_facts(row: Mapping[str, Any]) -> Pairs:
    pairs: Pairs = []
    _add(pairs, "Year built", _count(row.get("year_built")))
    age = _count(row.get("building_age_years"))
    _add(pairs, "Building age", f"{age} years" if age else None)
    _add(pairs, "Modernised", _count(row.get("last_renovation_year")))
    _add(pairs, "Condition", _text(row.get("property_condition")))
    _add(pairs, "Heating", _text(row.get("heating_type")))
    _add(pairs, "Energy source", _text(row.get("energy_source")))
    _add(pairs, "Energy class", _text(row.get("energy_efficiency_class")))
    use = _count(row.get("final_energy_kwh_m2a"))
    _add(pairs, "Energy use", f"{use} kWh/m²a" if use else None)
    hausgeld = _number(row.get("hausgeld"))
    area = _number(row.get("living_area"))
    _add(pairs, "Hausgeld", _euro(hausgeld, cents=True))
    if hausgeld is not None and area:
        _add(pairs, "Hausgeld per m²", f"{hausgeld / area:.2f} €/m² per month")
    _add(pairs, "Listed monument", _yesno(row.get("has_monument_protection")))
    return pairs


def age_class_facts(age_class: Mapping[str, Any] | None) -> Pairs:
    """Typical values of the construction period, from building_age_classes."""
    if not age_class:
        return []
    pairs: Pairs = []
    start, end = _count(age_class.get("year_from")), _count(age_class.get("year_to"))
    if start and end:
        period = f"{start}–{end}"
    else:
        period = f"until {end}" if end else (f"from {start}" if start else "")
    code = _text(age_class.get("class_code"))
    _add(pairs, "Age class", f"{code} ({period})" if code and period else code)
    _add(pairs, "Typical heating demand", _text(age_class.get("typical_heating_demand")))
    _add(pairs, "Exterior wall U-value", _text(age_class.get("exterior_wall_u_value")))
    _add(pairs, "Window U-value", _text(age_class.get("window_u_value")))
    _add(pairs, "Roof or ceiling U-value", _text(age_class.get("roof_ceiling_u_value")))
    risk = _count(age_class.get("climate_risk_score"))
    level = _text(age_class.get("risk_level"))
    if risk:
        _add(pairs, "Climate risk score", f"{risk} of 100" + (f" ({level})" if level else ""))
    return pairs


def commute_facts(row: Mapping[str, Any]) -> Pairs:
    pairs: Pairs = []
    work = row.get("commute_minutes")
    work2 = row.get("commute_workplace2_minutes")
    _add(pairs, "Workplace 1", _minutes(work))
    _add(pairs, "Workplace 2", _minutes(work2))
    _add(pairs, "City centre", _minutes(row.get("commute_citycenter_minutes")))
    if _number(work) is not None and _number(work2) is not None:
        _add(pairs, "Weighted average (3:2)", _minutes(avg_commute_minutes(work, work2)))
    return pairs


def station_facts(row: Mapping[str, Any], lines: Mapping[str, str] | None = None) -> Pairs:
    """Nearest stations with distance, and their lines where OpenStreetMap has them."""
    lines = lines or {}
    pairs: Pairs = []
    for label, name_key, dist_key in (
        ("Nearest S-Bahn", "nearest_sbahn", "nearest_sbahn_m"),
        ("Nearest U-Bahn", "nearest_ubahn", "nearest_ubahn_m"),
    ):
        text = _named_distance(row, name_key, dist_key)
        name = _text(row.get(name_key))
        if text and name and lines.get(name):
            text = f"{text} · {lines[name]}"
        _add(pairs, label, text)
    _add(pairs, "Nearest rail", _meters(row.get("nearest_rail_m")))
    return pairs


PARTIES = (
    ("linke", "Linke"),
    ("cdu", "CDU"),
    ("afd", "AfD"),
    ("gruene", "Greens"),
    ("spd", "SPD"),
    ("bsw", "BSW"),
    ("fdp", "FDP"),
)


def party_shares(row: Mapping[str, Any]) -> list[tuple[str, str, float]]:
    """key, label, AGH 2026 share in percent for the listing's voting district."""
    return [
        (key, label, share)
        for key, label in PARTIES
        if (share := _number(row.get(f"agh_vote_{key}"))) is not None
    ]


def people_facts(row: Mapping[str, Any]) -> Pairs:
    pairs: Pairs = []
    _add(pairs, "Population", _count(row.get("lor_population")))
    _add(pairs, "Under 18", _pct(row.get("share_under_18_pct")))
    _add(pairs, "65 and older", _pct(row.get("share_65_plus_pct")))
    _add(pairs, "AGH turnout", _pct(row.get("agh_turnout_pct")))
    return pairs


def neighbourhood_indicator_facts(
    row: Mapping[str, Any],
    area_values: Mapping[str, Any],
    references: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, str]]:
    """Headline indicators with weighted Bezirk and Berlin comparison values."""

    formats = {
        "pct": "{:.1f}%",
        "grade": "{:.2f}",
        "per_100k": "{:,.0f} per 100k",
        "per_1000": "{:.2f} per 1,000/yr",
    }

    def formatted(value: object, kind: str) -> str:
        number = _number(value)
        if number is None:
            return NOT_STATED
        return formats[kind].format(number)

    specs = (
        ("AGH Greens vote", row.get("agh_vote_gruene"), "agh_vote_gruene", "pct"),
        ("AGH AfD vote", row.get("agh_vote_afd"), "agh_vote_afd", "pct"),
        ("Abitur / Fachabi (est.)", row.get("est_abitur_pct"), "est_abitur_pct", "pct"),
        (
            "Hochschule / Uni (est.)",
            row.get("est_hochschule_pct"),
            "est_hochschule_pct",
            "pct",
        ),
        (
            "Adjusted Abitur average",
            area_values.get("nearby_school_abi_average"),
            "nearby_school_abi_average",
            "grade",
        ),
        (
            "Schultypisierung",
            area_values.get("nearby_school_type_level"),
            "nearby_school_type_level",
            "grade",
        ),
        ("German citizens", row.get("german_citizen_share"), "german_share_mss", "pct"),
        ("Unemployed (SGB II)", area_values.get("sgb2_share"), "sgb2_share", "pct"),
        ("OIC countries", row.get("oic_share"), "oic_share", "pct"),
        ("Neighbourhood crime", row.get("crime_rate"), "crime_rate", "per_100k"),
        (
            "Berliner Register incidents",
            row.get("incident_rate"),
            "incident_rate",
            "per_1000",
        ),
    )
    result = []
    for label, here, key, kind in specs:
        reference = references.get(key, {})
        result.append(
            {
                "Indicator": label,
                "Here": formatted(here, kind),
                "Bezirk average": formatted(reference.get("bezirk"), kind),
                "Berlin average": formatted(reference.get("berlin"), kind),
            }
        )
    return result


def grundschule_facts(school: Mapping[str, Any] | None) -> Pairs:
    if not school:
        return []
    pairs: Pairs = []
    _add(pairs, "Type", _text(school.get("schulart")))
    _add(pairs, "Operator", _text(school.get("traeger")))
    _add(pairs, "Address", _text(school.get("address")))
    ganztag = _text(school.get("ganztag"))
    _add(
        pairs,
        "All-day",
        {
            "gebunden": "Gebunden (all-day for everyone)",
            "offen": "Offen (optional afternoon care)",
        }.get(ganztag or "", ganztag),
    )
    if school.get("europa_schule") is True:
        language = _text(school.get("partner_language"))
        _add(pairs, "Europa-Schule", f"Yes, {language}" if language else "Yes")
    _add(pairs, "First foreign language", _text(school.get("first_foreign_language")))
    if school.get("bilingual") is True:
        _add(pairs, "Bilingual", "Yes")
    _add(pairs, "Students", _count(school.get("students")))
    ratio = _number(school.get("students_per_teacher"))
    _add(pairs, "Students per teacher", f"{ratio:.1f}" if ratio is not None else None)
    return pairs


def gymnasium_facts(stats: Mapping[str, Any] | None) -> Pairs:
    if not stats:
        return []
    pairs: Pairs = []
    abi = _number(stats.get("abi_average"))
    year = _text(stats.get("abi_year"))
    if abi is not None:
        _add(pairs, "Abitur average", f"{abi:.2f}" + (f" ({year})" if year else ""))
    _add(pairs, "Abitur pass rate", _pct(stats.get("abi_pass_rate_pct")))
    places = _count(stats.get("admission_places"))
    choices = _count(stats.get("first_choices"))
    if places and choices:
        _add(pairs, "First choices / places", f"{choices} / {places}")
    starts = [
        label
        for column, label in (("starts_year_5", "year 5"), ("starts_year_7", "year 7"))
        if stats.get(column) is True
    ]
    _add(pairs, "Starts in", " and ".join(starts) or None)
    profiles = [
        label
        for column, label in (
            ("profile_language", "languages"),
            ("profile_music_art", "music and art"),
            ("profile_mint", "MINT"),
            ("profile_sport", "sport"),
            ("schnelllerner", "Schnelllerner"),
            ("europa_schule", "Europa-Schule"),
        )
        if stats.get(column) is True
    ]
    _add(pairs, "Profile", ", ".join(profiles) or None)
    ratio = _number(stats.get("students_per_teacher"))
    _add(pairs, "Students per teacher", f"{ratio:.1f}" if ratio is not None else None)
    _add(pairs, "NdH share", _pct(stats.get("migration_background_pct"), 0))
    return pairs


def _when(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"nat", "nan", "none"}:
        return None
    return text[:10]


GEO_PRECISION = {
    "source": "Coordinates from the portal",
    "exact": "Exact address",
    "street": "Street, without house number",
    "postcode": "Postcode centre only",
    "district": "District centre only",
}


def is_approximate(row: Mapping[str, Any]) -> bool:
    return row.get("geo_precision") in {"postcode", "district"}


def source_facts(row: Mapping[str, Any], sources: Mapping[str, Any] | None = None) -> Pairs:
    sources = sources or {}
    pairs: Pairs = []
    _add(pairs, "Portal", source_label(row.get("source")) or None)
    _add(pairs, "Also on", _text(sources.get("thinkimmo_platforms")))
    precision = _text(row.get("geo_precision"))
    _add(pairs, "Location", GEO_PRECISION.get(precision or "", precision))
    _add(pairs, "Geocoded from", _text(sources.get("geocoded_query")))
    _add(pairs, "First seen", _when(row.get("first_seen")))
    _add(pairs, "Last seen", _when(row.get("last_seen")))
    _add(pairs, "Posted", _when(row.get("posted_on")))
    for key, label in (
        ("details_fetched_at", "Detail page fetched"),
        ("immoscout24_fetched_at", "ImmoScout24 exposé fetched"),
        ("immowelt_fetched_at", "Immowelt exposé fetched"),
        ("kleinanzeigen_fetched_at", "Kleinanzeigen ad fetched"),
        ("neubaukompass_fetched_at", "Neubaukompass unit fetched"),
        ("bsk_fetched_at", "BSK exposé fetched"),
        ("engelvoelkers_fetched_at", "Engel & Völkers exposé fetched"),
        ("thinkimmo_fetched_at", "ThinkImmo exposé fetched"),
    ):
        _add(pairs, label, _when(sources.get(key)))
    return pairs


def rent_facts(row: Mapping[str, Any], extras: Mapping[str, Any] | None = None) -> Pairs:
    extras = extras or {}
    pairs: Pairs = []
    _add(pairs, "Estimated rent", _euro(row.get("thinkimmo_estimated_rent")))
    per_m2 = _number(extras.get("estimated_rent_per_m2"))
    _add(pairs, "Estimated rent per m²", f"{per_m2:.2f} €" if per_m2 is not None else None)
    _add(pairs, "Current rent", _euro(extras.get("current_rent")))
    _add(pairs, "Gross return", _pct(row.get("thinkimmo_gross_return"), 2))
    _add(pairs, "Gross return on current rent", _pct(extras.get("gross_return_current"), 2))
    return pairs


def listing_market_facts(row: Mapping[str, Any], extras: Mapping[str, Any] | None = None) -> Pairs:
    """How long the flat has been on offer and how its price moved."""
    extras = extras or {}
    pairs: Pairs = []
    _add(pairs, "Days listed", _count(row.get("days_on_market")))
    _add(pairs, "Price change since first seen", _pct(row.get("price_change_pct"), signed=True))
    _add(pairs, "First price", _euro(row.get("first_price")))
    _add(pairs, "ThinkImmo views", _count(row.get("thinkimmo_views")))
    _add(pairs, "Realtor", _text(extras.get("realtor_company")))
    if extras.get("is_private_offer") is True:
        _add(pairs, "Offer", "Private")
    return pairs


def origin_facts(summary: Mapping[str, Any] | None) -> list[tuple[str, str]]:
    """Migration background, diversity, top countries and Vietnam from `kiez_summary`."""
    if not summary:
        return []
    pairs: list[tuple[str, str]] = []
    _add(pairs, "Migration background", _pct(summary.get("mh_share")))
    diversity = _number(summary.get("diversity"))
    _add(pairs, "Origin diversity", None if diversity is None else f"{diversity:.0f} of 100")
    top = list(summary.get("top") or [])
    for label, share in top:
        _add(pairs, label, _pct(share))
    if "Vietnam" not in {label for label, _share in top}:
        _add(pairs, "Vietnam", _pct(summary.get("vietnam")))
    return pairs
