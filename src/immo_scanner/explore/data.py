"""Load enriched data into pandas for notebooks and the Streamlit app."""

import re
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import text

from immo_scanner.db.session import get_engine
from immo_scanner.explore.education import attach_listing_education
from immo_scanner.explore.gymnasiums import attach_locations
from immo_scanner.explore.score_weights import load_weights
from immo_scanner.explore.scoring import INCIDENT_WINDOW_YEARS, Weights, apply_weights
from immo_scanner.explore.sekundarschulen import (
    attach_locations as attach_sekundarschule_locations,
)

# Whole word, so "Rohlinge" and compounds without a boundary do not count.
_ROHLING_WORD = re.compile(r"\brohling\b", re.IGNORECASE)
# Unfinished shells and permit-stage ads. Unausgebautes is the inflected form.
_SHELL_WORD = re.compile(
    r"\b(?:rohling|baugenehmigung|unausgebaut(?:es)?|bauvorbescheid)\b",
    re.IGNORECASE,
)


def titles_are_rohling(titles: pd.Series) -> pd.Series:
    """True when a title contains the word Rohling, ignoring case."""
    text_titles = titles.fillna("").astype(str)
    return text_titles.str.contains(_ROHLING_WORD, regex=True)


def titles_are_shell(titles: pd.Series) -> pd.Series:
    """True for Rohling, Baugenehmigung, Unausgebaut, Unausgebautes, or Bauvorbescheid.

    The match is a whole word, ignoring case, so a compound such as Rohlingwohnung
    stays out of this set.
    """
    text_titles = titles.fillna("").astype(str)
    return text_titles.str.contains(_SHELL_WORD, regex=True)


def load_features(active_only: bool = True, weights: Weights | None = None) -> pd.DataFrame:
    """All columns of listing_features except the raw geometry.

    Group scores and `total_score` are recomputed with `weights`, or the saved weights.
    `is_rohling` is true when the title contains the word Rohling.
    """
    where = "WHERE status = 'active'" if active_only else ""
    query = f"SELECT * FROM listing_features {where}"
    with get_engine().connect() as conn:
        df = pd.read_sql(text(query), conn)
    df = df.drop(columns=["geom"], errors="ignore")
    df = apply_weights(df, weights if weights is not None else load_weights())
    if "title" in df.columns:
        df["is_rohling"] = titles_are_rohling(df["title"])
    return attach_listing_education(df)


def load_plr_hover_stats() -> pd.DataFrame:
    """Planungsraum figures for listing hover text.

    Weighted Abitur and Schultypisierung come from `area_indicators`. Migration
    background is the latest A I 16 share and resident count (`origin_key` `mh`).
    """
    query = """
        SELECT
            COALESCE(i.plr_id, m.plr_id) AS plr_id,
            i.nearby_school_abi_average,
            i.nearby_school_type_level,
            m.mh_share,
            m.mh_population
        FROM (
            SELECT area_id AS plr_id,
                   MAX(value) FILTER (
                       WHERE indicator = 'nearby_school_abi_average'
                   ) AS nearby_school_abi_average,
                   MAX(value) FILTER (
                       WHERE indicator = 'nearby_school_type_level'
                   ) AS nearby_school_type_level
            FROM area_indicators
            WHERE level = 'plr'
              AND indicator IN (
                  'nearby_school_abi_average', 'nearby_school_type_level'
              )
            GROUP BY area_id
        ) i
        FULL OUTER JOIN (
            SELECT DISTINCT ON (plr_id)
                   plr_id,
                   share AS mh_share,
                   count AS mh_population
            FROM plr_origin_stats
            WHERE origin_key = 'mh'
            ORDER BY plr_id, ref_date DESC
        ) m ON m.plr_id = i.plr_id
    """
    columns = [
        "plr_id",
        "nearby_school_abi_average",
        "nearby_school_type_level",
        "mh_share",
        "mh_population",
    ]
    with get_engine().connect() as conn:
        df = pd.read_sql(text(query), conn)
    if df.empty:
        return pd.DataFrame(columns=columns)
    return df


def _cell(value: object) -> object:
    if isinstance(value, list | dict):
        return value
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, np.generic):
        return value.item()
    return value


def load_listing_texts(listing_id: int) -> dict[str, Any]:
    """Saved exposé prose and ThinkImmo portal links for one listing.

    An empty dict means the listing has no detail row. Callers still have the
    listing URL from listing_features.
    """
    query = """
        SELECT
            d.description AS details_description,
            is24.description AS is24_description,
            is24.equipment_text AS is24_equipment,
            is24.location_text AS is24_location,
            is24.other_text AS is24_other,
            iw.description AS iw_description,
            iw.location_text AS iw_location,
            iw.further_text AS iw_further,
            ka.description AS ka_description,
            nbk.description AS nbk_description,
            nbk.equipment_text AS nbk_equipment,
            nbk.location_text AS nbk_location,
            bsk.equipment_text AS bsk_equipment,
            bsk.description AS bsk_description,
            bsk.location_text AS bsk_location,
            ev.description AS ev_description,
            ev.location_text AS ev_location,
            ti.description AS ti_description,
            ti.platforms AS ti_platforms
        FROM listings l
        LEFT JOIN listing_details d ON d.listing_id = l.id
        LEFT JOIN immoscout24_exposes is24 ON is24.listing_id = l.id
        LEFT JOIN immowelt_exposes iw ON iw.listing_id = l.id
        LEFT JOIN kleinanzeigen_ads ka ON ka.listing_id = l.id
        LEFT JOIN neubaukompass_units nbk ON nbk.listing_id = l.id
        LEFT JOIN bsk_exposes bsk ON bsk.listing_id = l.id
        LEFT JOIN engelvoelkers_exposes ev ON ev.listing_id = l.id
        LEFT JOIN LATERAL (
            SELECT description, platforms
            FROM thinkimmo_properties t
            WHERE t.listing_id = l.id
            ORDER BY t.last_seen DESC
            LIMIT 1
        ) ti ON true
        WHERE l.id = :id
    """
    with get_engine().connect() as conn:
        frame = pd.read_sql(text(query), conn, params={"id": listing_id})
    if frame.empty:
        return {}
    return {str(key): _cell(value) for key, value in frame.iloc[0].items()}


def load_price_history(listing_ids: list[int] | None = None) -> pd.DataFrame:
    query = """
        SELECT s.listing_id, s.scraped_at, s.price, s.living_area, s.rooms, l.source, l.title
        FROM listing_snapshots s JOIN listings l ON l.id = s.listing_id
    """
    params = {}
    if listing_ids:
        query += " WHERE s.listing_id = ANY(:ids)"
        params["ids"] = list(listing_ids)
    query += " ORDER BY s.listing_id, s.scraped_at"
    with get_engine().connect() as conn:
        return pd.read_sql(text(query), conn, params=params)


def load_stations() -> pd.DataFrame:
    query = """
        SELECT name, is_sbahn, is_ubahn, ST_Y(geom) AS lat, ST_X(geom) AS lon FROM stations
    """
    with get_engine().connect() as conn:
        return pd.read_sql(text(query), conn)


def load_gymnasiums() -> pd.DataFrame:
    """Published Gymnasium statistics, with Bezirk and Ortsteil where a school point matches."""
    stats_query = "SELECT * FROM gymnasium_statistics"
    schools_query = """
        SELECT
            s.name,
            ST_Y(s.geom) AS lat,
            ST_X(s.geom) AS lon,
            lor.bezirk,
            ot.name AS ortsteil
        FROM schools s
        LEFT JOIN LATERAL (
            SELECT l.bezirk
            FROM lor_planungsraeume l
            WHERE ST_Intersects(l.geom, s.geom)
            LIMIT 1
        ) lor ON true
        LEFT JOIN LATERAL (
            SELECT o.name
            FROM ortsteile o
            WHERE ST_Intersects(o.geom, s.geom)
            LIMIT 1
        ) ot ON true
        WHERE s.kind = 'gymnasium'
    """
    with get_engine().connect() as conn:
        stats = pd.read_sql(text(stats_query), conn)
        schools = pd.read_sql(text(schools_query), conn)
    return attach_locations(stats, schools)


def load_school_sites() -> pd.DataFrame:
    """Berlin school number, kind and map point from the Geoportal `schulen` table."""
    query = """
        SELECT bsn, schulart, bezirk, ortsteil,
               ST_Y(geom) AS lat, ST_X(geom) AS lon
        FROM schulen
    """
    with get_engine().connect() as conn:
        return pd.read_sql(text(query), conn)


def load_sekundarschulen() -> pd.DataFrame:
    """Published Sekundarschule statistics, with a map point where the school number matches."""
    query = "SELECT * FROM sekundarschule_statistics"
    with get_engine().connect() as conn:
        stats = pd.read_sql(text(query), conn)
    return attach_sekundarschule_locations(stats, load_school_sites())


def load_grundschulen() -> pd.DataFrame:
    """Grundschulen and Gemeinschaftsschulen with offers and a map point."""
    query = """
        SELECT
            bsn, name, schulart, traeger, address, bezirk, ortsteil, school_year,
            ganztag, europa_schule, partner_language, first_foreign_language,
            bilingual, students, teachers,
            ST_Y(geom) AS lat, ST_X(geom) AS lon
        FROM grundschulen
    """
    with get_engine().connect() as conn:
        frame = pd.read_sql(text(query), conn)
    teachers = pd.to_numeric(frame["teachers"], errors="coerce") if "teachers" in frame else None
    students = pd.to_numeric(frame["students"], errors="coerce") if "students" in frame else None
    if teachers is not None and students is not None:
        frame["students_per_teacher"] = students / teachers.where(teachers > 0)
    return frame


def load_einschulbereiche() -> pd.DataFrame:
    """Catchment polygons as GeoJSON, named from the linked school."""
    query = """
        SELECT e.bsn, e.esb, e.bezirk, g.name, ST_AsGeoJSON(e.geom) AS geojson
        FROM einschulbereiche e
        LEFT JOIN grundschulen g ON g.bsn = e.bsn
        WHERE e.bsn IS NOT NULL
    """
    with get_engine().connect() as conn:
        return pd.read_sql(text(query), conn)


def load_workplaces() -> pd.DataFrame:
    """Both configured workplaces, as lat/lon rows for the map."""
    query = """
        SELECT ST_Y(geom) AS lat, ST_X(geom) AS lon
        FROM places
        WHERE name IN ('workplace', 'workplace2')
    """
    with get_engine().connect() as conn:
        return pd.read_sql(text(query), conn)


def load_listing_media(listing_id: int) -> list[tuple[str, list[dict[str, Any]]]]:
    """Saved media lists per portal for one listing; ThinkImmo images come last."""
    query = """
        SELECT 'immoscout24' AS origin, media FROM immoscout24_exposes WHERE listing_id = :id
        UNION ALL
        SELECT 'neubaukompass', media FROM neubaukompass_units WHERE listing_id = :id
        UNION ALL
        SELECT 'kleinanzeigen', media FROM kleinanzeigen_ads WHERE listing_id = :id
        UNION ALL
        SELECT 'bsk', media FROM bsk_exposes WHERE listing_id = :id
        UNION ALL
        SELECT 'engelvoelkers', media FROM engelvoelkers_exposes WHERE listing_id = :id
        UNION ALL
        SELECT 'thinkimmo', images FROM (
            SELECT images FROM thinkimmo_properties
            WHERE listing_id = :id
            ORDER BY last_seen DESC
            LIMIT 1
        ) ti
    """
    with get_engine().connect() as conn:
        rows = conn.execute(text(query), {"id": listing_id}).fetchall()
    return [(origin, media if isinstance(media, list) else []) for origin, media in rows]


def load_thinkimmo_extras(thinkimmo_id: str) -> dict[str, Any]:
    """Market comparison, rent and price history of one ThinkImmo property."""
    query = """
        SELECT
            market_price_per_m2, market_price_per_m2_min, market_price_per_m2_max,
            district_price_per_m2, city_price_per_m2, similar_price_per_m2,
            aggregations -> 'district' ->> 'name' AS district_name,
            estimated_rent, estimated_rent_per_m2, current_rent, current_rent_per_m2,
            gross_return, gross_return_current, cash_flow, own_capital_return,
            commission_pct, realtor_company, is_private_offer,
            published_at, running_time_days, buying_price_history,
            detail_fetched_at, last_seen
        FROM thinkimmo_properties
        WHERE id = :id
    """
    with get_engine().connect() as conn:
        frame = pd.read_sql(text(query), conn, params={"id": thinkimmo_id})
    if frame.empty:
        return {}
    return {str(key): _cell(value) for key, value in frame.iloc[0].items()}


def load_building_age_class(year_built: int) -> dict[str, Any]:
    """The building_age_classes row whose period contains `year_built`."""
    query = """
        SELECT class_code, year_from, year_to, exterior_wall_u_value, window_u_value,
               roof_ceiling_u_value, typical_heating_demand, typical_heating_demand_kwh,
               climate_risk_score, risk_level, legal_obligations_on_purchase
        FROM building_age_classes
        WHERE (year_from IS NULL OR :year >= year_from)
          AND (year_to IS NULL OR :year <= year_to)
        ORDER BY id
        LIMIT 1
    """
    with get_engine().connect() as conn:
        frame = pd.read_sql(text(query), conn, params={"year": year_built})
    if frame.empty:
        return {}
    return {str(key): _cell(value) for key, value in frame.iloc[0].items()}


def load_listing_points(listing_id: int) -> pd.DataFrame:
    """The listing's nearest stations and Gymnasien as map points, with station lines.

    listing_features stores only their names, so each name is matched to the
    point with that name closest to the listing.
    """
    query = """
        WITH f AS (
            SELECT f.*, l.geom AS point
            FROM listing_features f JOIN listings l ON l.id = f.id
            WHERE f.id = :id AND l.geom IS NOT NULL
        ),
        wanted AS (
            SELECT 'sbahn' AS kind, nearest_sbahn AS name, point FROM f
            UNION ALL SELECT 'ubahn', nearest_ubahn, point FROM f
            UNION ALL SELECT 'gymnasium', nearest_gymnasium, point FROM f
            UNION ALL SELECT 'gymnasium', nearest_gymnasium_2, point FROM f
            UNION ALL SELECT 'gymnasium', nearest_gymnasium_3, point FROM f
        )
        SELECT w.kind, w.name, p.lines, ST_Y(p.geom) AS lat, ST_X(p.geom) AS lon
        FROM wanted w
        JOIN LATERAL (
            SELECT c.geom, c.lines
            FROM (
                SELECT s.geom, s.lines FROM stations s
                WHERE w.kind IN ('sbahn', 'ubahn') AND s.name = w.name
                UNION ALL
                SELECT k.geom, NULL FROM schools k
                WHERE w.kind = 'gymnasium' AND k.kind = 'gymnasium' AND k.name = w.name
            ) c
            ORDER BY c.geom <-> w.point
            LIMIT 1
        ) p ON true
        WHERE w.name IS NOT NULL
    """
    with get_engine().connect() as conn:
        return pd.read_sql(text(query), conn, params={"id": listing_id})


def load_catchment(bsn: str) -> str | None:
    """GeoJSON of the Einschulbereich linked to the Grundschule `bsn`."""
    query = """
        SELECT ST_AsGeoJSON(ST_Union(geom)) FROM einschulbereiche WHERE bsn = :bsn
    """
    with get_engine().connect() as conn:
        return conn.execute(text(query), {"bsn": bsn}).scalar()


def load_listing_sources(listing_id: int) -> dict[str, Any]:
    """When each portal's data was fetched, and how the listing was geocoded."""
    query = """
        SELECT
            l.source, l.geo_precision, l.geocoded_query, l.first_seen, l.last_seen,
            d.fetched_at AS details_fetched_at,
            is24.fetched_at AS immoscout24_fetched_at,
            iw.fetched_at AS immowelt_fetched_at,
            ka.fetched_at AS kleinanzeigen_fetched_at,
            nbk.fetched_at AS neubaukompass_fetched_at,
            bsk.fetched_at AS bsk_fetched_at,
            ev.fetched_at AS engelvoelkers_fetched_at,
            ti.detail_fetched_at AS thinkimmo_fetched_at,
            ti.platform_names AS thinkimmo_platforms
        FROM listings l
        LEFT JOIN listing_details d ON d.listing_id = l.id
        LEFT JOIN immoscout24_exposes is24 ON is24.listing_id = l.id
        LEFT JOIN immowelt_exposes iw ON iw.listing_id = l.id
        LEFT JOIN kleinanzeigen_ads ka ON ka.listing_id = l.id
        LEFT JOIN neubaukompass_units nbk ON nbk.listing_id = l.id
        LEFT JOIN bsk_exposes bsk ON bsk.listing_id = l.id
        LEFT JOIN engelvoelkers_exposes ev ON ev.listing_id = l.id
        LEFT JOIN LATERAL (
            SELECT detail_fetched_at, platform_names
            FROM thinkimmo_properties t
            WHERE t.listing_id = l.id
            ORDER BY t.last_seen DESC
            LIMIT 1
        ) ti ON true
        WHERE l.id = :id
    """
    with get_engine().connect() as conn:
        frame = pd.read_sql(text(query), conn, params={"id": listing_id})
    if frame.empty:
        return {}
    return {str(key): _cell(value) for key, value in frame.iloc[0].items()}


def berlin_party_shares(scope: str = "abgeordnetenhaus") -> dict[str, float]:
    """Berlin-wide party shares in percent, weighted by valid votes per district."""
    query = """
        SELECT s.key, sum(s.value::float * w.valid_votes) / sum(w.valid_votes) AS share
        FROM wahlbezirke w, jsonb_each_text(w.party_shares) s
        WHERE w.scope = :scope AND w.valid_votes > 0 AND s.value ~ '^[0-9.]+$'
        GROUP BY s.key
    """
    with get_engine().connect() as conn:
        rows = conn.execute(text(query), {"scope": scope}).fetchall()
    return {str(key): float(share) for key, share in rows if share is not None}


def load_area_indicators(plr_id: str) -> pd.DataFrame:
    """One Planungsraum's indicators, each with the median over all Planungsräume."""
    query = """
        SELECT a.topic, a.indicator, a.label, a.value, a.unit, a.ref_date, a.source,
               m.berlin_median
        FROM area_indicators a
        JOIN (
            SELECT topic, indicator,
                   percentile_cont(0.5) WITHIN GROUP (ORDER BY value) AS berlin_median
            FROM area_indicators
            WHERE level = 'plr' AND value IS NOT NULL
            GROUP BY topic, indicator
        ) m USING (topic, indicator)
        WHERE a.level = 'plr' AND a.area_id = :plr_id
        ORDER BY a.topic, a.label
    """
    with get_engine().connect() as conn:
        return pd.read_sql(text(query), conn, params={"plr_id": plr_id})


def load_neighbourhood_references(plr_id: str) -> pd.DataFrame:
    """Weighted Bezirk and Berlin references for the Flat page's headline indicators.

    Election shares are weighted by valid votes. Area indicators use PLR population;
    OIC shares sum the published origin counts over the corresponding resident counts.
    Crime uses the police's own Bezirk and Berlin rates for the latest year. Register
    incidents pool the street- and station-placed incidents over PLR population, in the
    same window as the listing's incident rate.
    """
    query = f"""
        WITH target AS (
            SELECT bezirk
            FROM lor_planungsraeume
            WHERE plr_id = :plr_id
        ),
        area_refs AS (
            SELECT
                a.indicator,
                SUM(a.value * l.population) FILTER (
                    WHERE l.bezirk = t.bezirk AND a.value IS NOT NULL
                ) / NULLIF(SUM(l.population) FILTER (
                    WHERE l.bezirk = t.bezirk AND a.value IS NOT NULL
                ), 0) AS bezirk_average,
                SUM(a.value * l.population) FILTER (
                    WHERE a.value IS NOT NULL
                ) / NULLIF(SUM(l.population) FILTER (
                    WHERE a.value IS NOT NULL
                ), 0) AS berlin_average
            FROM area_indicators a
            JOIN lor_planungsraeume l ON l.plr_id = a.area_id
            CROSS JOIN target t
            WHERE a.level = 'plr'
              AND a.indicator IN (
                  'nearby_school_abi_average', 'nearby_school_type_level',
                  'german_share_mss', 'sgb2_share'
              )
            GROUP BY a.indicator
        ),
        election_districts AS (
            SELECT w.valid_votes, w.party_shares, b.bezirk
            FROM wahlbezirke w
            LEFT JOIN LATERAL (
                SELECT l.bezirk
                FROM lor_planungsraeume l
                WHERE ST_Contains(l.geom, ST_PointOnSurface(w.geom))
                LIMIT 1
            ) b ON true
            WHERE w.scope = 'abgeordnetenhaus' AND w.valid_votes > 0
        ),
        election_refs AS (
            SELECT
                p.indicator,
                SUM((d.party_shares ->> p.party)::float * d.valid_votes) FILTER (
                    WHERE d.bezirk = t.bezirk AND d.party_shares ? p.party
                ) / NULLIF(SUM(d.valid_votes) FILTER (
                    WHERE d.bezirk = t.bezirk AND d.party_shares ? p.party
                ), 0) AS bezirk_average,
                SUM((d.party_shares ->> p.party)::float * d.valid_votes) FILTER (
                    WHERE d.party_shares ? p.party
                ) / NULLIF(SUM(d.valid_votes) FILTER (
                    WHERE d.party_shares ? p.party
                ), 0) AS berlin_average
            FROM election_districts d
            CROSS JOIN target t
            CROSS JOIN (
                VALUES ('agh_vote_gruene', 'gruene'), ('agh_vote_afd', 'afd')
            ) p(indicator, party)
            GROUP BY p.indicator
        ),
        latest_origin AS (
            SELECT MAX(ref_date) AS ref_date
            FROM plr_origin_stats
            WHERE origin_key = 'oic'
        ),
        origin_values AS (
            SELECT o.plr_id, o.count AS oic_count, r.count AS residents
            FROM plr_origin_stats o
            JOIN latest_origin d ON d.ref_date = o.ref_date
            JOIN plr_origin_stats r
              ON r.plr_id = o.plr_id
             AND r.ref_date = o.ref_date
             AND r.origin_key = 'residents'
            WHERE o.origin_key = 'oic'
        ),
        origin_refs AS (
            SELECT
                'oic_share' AS indicator,
                SUM(o.oic_count) FILTER (
                    WHERE l.bezirk = t.bezirk AND o.oic_count IS NOT NULL
                ) * 100.0 / NULLIF(SUM(o.residents) FILTER (
                    WHERE l.bezirk = t.bezirk AND o.oic_count IS NOT NULL
                ), 0) AS bezirk_average,
                SUM(o.oic_count) FILTER (
                    WHERE o.oic_count IS NOT NULL
                ) * 100.0 / NULLIF(SUM(o.residents) FILTER (
                    WHERE o.oic_count IS NOT NULL
                ), 0) AS berlin_average
            FROM origin_values o
            JOIN lor_planungsraeume l ON l.plr_id = o.plr_id
            CROSS JOIN target t
        ),
        crime_refs AS (
            SELECT
                'crime_rate' AS indicator,
                MAX(c.rate) FILTER (
                    WHERE c.level = 'bezirk' AND c.area_id = t.bezirk
                ) AS bezirk_average,
                MAX(c.rate) FILTER (WHERE c.level = 'city') AS berlin_average
            FROM crime_stats c
            CROSS JOIN target t
            WHERE c.offense = 'neighbourhood'
              AND c.year = (
                  SELECT MAX(year) FROM crime_stats
                  WHERE level = 'bzr' AND offense = 'neighbourhood'
              )
        ),
        incident_counts AS (
            SELECT plr_id, COUNT(*)::float AS incidents
            FROM register_incidents
            WHERE geo_precision IN ('street', 'station')
              AND incident_date > (
                  SELECT MAX(incident_date) - INTERVAL '{INCIDENT_WINDOW_YEARS:.0f} years'
                  FROM register_incidents
              )
            GROUP BY plr_id
        ),
        incident_refs AS (
            SELECT
                'incident_rate' AS indicator,
                SUM(COALESCE(i.incidents, 0)) FILTER (WHERE l.bezirk = t.bezirk)
                    * 1000.0 / {INCIDENT_WINDOW_YEARS}
                    / NULLIF(SUM(l.population) FILTER (WHERE l.bezirk = t.bezirk), 0)
                    AS bezirk_average,
                SUM(COALESCE(i.incidents, 0)) * 1000.0 / {INCIDENT_WINDOW_YEARS}
                    / NULLIF(SUM(l.population), 0) AS berlin_average
            FROM lor_planungsraeume l
            LEFT JOIN incident_counts i ON i.plr_id = l.plr_id
            CROSS JOIN target t
            WHERE l.population > 0
        )
        SELECT * FROM area_refs
        UNION ALL SELECT * FROM election_refs
        UNION ALL SELECT * FROM origin_refs
        UNION ALL SELECT * FROM crime_refs
        UNION ALL SELECT * FROM incident_refs
    """
    with get_engine().connect() as conn:
        return pd.read_sql(text(query), conn, params={"plr_id": plr_id})
