"""Estimated qualification mix from Abgeordnetenhaus votes and party_education.

Profiled parties are renormalized to 100%. Other parties are left out. Turnout then
blends that voter mix with the Nichtwähler profile. Planungsraum headcounts apply the
result to residents aged 18 and over. A listing uses its own voting district.
"""

import math
from collections.abc import Mapping
from dataclasses import dataclass

import pandas as pd
from sqlalchemy import text

from immo_scanner.db.session import get_engine
from immo_scanner.explore.elections import PARTY_ALIASES

# Not a party result. Its weight is the share of eligible voters who stayed home.
ABSTAIN_KEY = "nichtwaehler"

# level, profile column, label. Shares in a profile sum to 100 and are mutually exclusive.
LEVELS: tuple[tuple[str, str, str], ...] = (
    ("hauptschule", "hauptschule_pct", "Hauptschule (einfache Bildung)"),
    ("mittlere_reife", "mittlere_reife_pct", "Mittlere Reife (mittlere Bildung)"),
    ("abitur", "abitur_pct", "Abitur / Fachabi (höhere Schulbildung)"),
    ("hochschule", "hochschule_pct", "Hochschule / Uni (akademische Bildung)"),
)

LISTING_LEVELS = ("abitur", "hochschule")
REFERENCE_KEYS = {"abitur": "est_abitur_pct", "hochschule": "est_hochschule_pct"}

_AREA_COLUMNS = [
    "area_id",
    "name",
    "bezirk",
    "level",
    "label",
    "pct",
    "people",
    "adults",
    "covered_vote_pct",
]


@dataclass(frozen=True)
class QualificationMix:
    """`pct` is the share of each qualification. `covered_vote_pct` is the vote share
    of parties that have a profile."""

    pct: dict[str, float]
    covered_vote_pct: float


def _shares(value: object) -> dict[str, float]:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return {}
    if not isinstance(value, Mapping):
        return {}
    clean: dict[str, float] = {}
    for party, share in value.items():
        if isinstance(share, bool):
            continue
        try:
            number = float(share)
        except (TypeError, ValueError):
            continue
        if math.isnan(number) or number < 0:
            continue
        clean[str(party)] = number
    return clean


def _profile_key(party: str) -> str:
    """Map a loader key onto the education-table key. Nichtwähler is not a party share."""
    key = PARTY_ALIASES.get(party, party)
    return "" if key == ABSTAIN_KEY else key


def _number(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number):
        return None
    return number


def qualification_mix(
    shares: Mapping[str, object],
    profiles: Mapping[str, Mapping[str, float]],
) -> QualificationMix | None:
    """Qualification percents from a district or area party-share dict.

    Shares and profile cells are percents. `voltdeutschland` uses the Volt profile.
    Parties absent from `profiles` are dropped and the rest are renormalized.
    Returns None when none of the votes have a profile.
    """
    clean = {party: share for party, share in _shares(shares).items() if _profile_key(party)}
    total = sum(clean.values())
    if total <= 0:
        return None
    weighted = dict.fromkeys((level for level, _, _ in LEVELS), 0.0)
    covered = 0.0
    for party, share in clean.items():
        profile = profiles.get(_profile_key(party))
        if profile is None:
            continue
        covered += share
        for level, column, _ in LEVELS:
            weighted[level] += share * float(profile[column])
    if covered <= 0:
        return None
    return QualificationMix(
        pct={level: weighted[level] / covered for level, _, _ in LEVELS},
        covered_vote_pct=covered / total * 100,
    )


def blend_turnout(
    mix: QualificationMix,
    turnout_pct: object,
    profiles: Mapping[str, Mapping[str, float]],
) -> QualificationMix:
    """final = turnout × voter mix + (100 − turnout) × Nichtwähler profile.

    Turnout is the percent of eligible voters who voted. A missing turnout, or a
    missing Nichtwähler profile, leaves the voter mix unchanged. Invalid ballots
    stay inside the voter mix.
    """
    profile = profiles.get(ABSTAIN_KEY)
    turnout = _number(turnout_pct)
    if profile is None or turnout is None:
        return mix
    voted = min(100.0, max(0.0, turnout)) / 100
    stayed = 1 - voted
    return QualificationMix(
        pct={
            level: voted * mix.pct[level] + stayed * float(profile[column])
            for level, column, _ in LEVELS
        },
        covered_vote_pct=mix.covered_vote_pct,
    )


def allocate_people(adults: float, pct: Mapping[str, float]) -> dict[str, int]:
    """Integer headcounts that sum to the rounded adult population."""
    target = max(0, int(round(float(adults))))
    levels = list(pct)
    if target == 0 or not levels:
        return dict.fromkeys(levels, 0)
    raw = {level: target * float(pct[level]) / 100 for level in levels}
    counts = {level: math.floor(raw[level] + 1e-9) for level in levels}
    leftover = target - sum(counts.values())
    ranked = sorted(
        levels,
        key=lambda level: (raw[level] - counts[level], level),
        reverse=True,
    )
    for level in ranked[: max(leftover, 0)]:
        counts[level] += 1
    return counts


def adult_population(
    population: object,
    share_18_to_64_pct: object,
    share_65_plus_pct: object,
) -> float | None:
    """Residents aged 18 and over. Falls back to the total when the age shares are missing."""
    if population is None or (isinstance(population, float) and math.isnan(population)):
        return None
    try:
        residents = float(population)
    except (TypeError, ValueError):
        return None
    if math.isnan(residents):
        return None
    try:
        working = float(share_18_to_64_pct) if share_18_to_64_pct is not None else math.nan
        older = float(share_65_plus_pct) if share_65_plus_pct is not None else math.nan
    except (TypeError, ValueError):
        return residents
    if math.isnan(working) or math.isnan(older):
        return residents
    return residents * (working + older) / 100


def _area_turnout(turnout: Mapping[object, object] | pd.Series | None, area_id: object) -> object:
    if turnout is None:
        return None
    if isinstance(turnout, pd.Series):
        if area_id not in turnout.index:
            return None
        value = turnout.loc[area_id]
        return value.iloc[0] if isinstance(value, pd.Series) else value
    return turnout.get(area_id)


def estimate_areas(
    shares: pd.DataFrame,
    profiles: Mapping[str, Mapping[str, float]],
    population: pd.DataFrame,
    turnout: Mapping[object, object] | pd.Series | None = None,
) -> pd.DataFrame:
    """One row per Planungsraum and qualification.

    `shares` has area_id, party and value (percent). `population` has area_id, name,
    bezirk, population, share_18_to_64_pct and share_65_plus_pct. `turnout` maps
    area_id to the eligible-voter-weighted turnout percent.
    """
    if shares.empty:
        return pd.DataFrame(columns=_AREA_COLUMNS)
    population_by_area = population.set_index("area_id")
    rows: list[dict[str, object]] = []
    for area_id, group in shares.groupby("area_id", sort=False):
        party_shares = dict(zip(group["party"], group["value"], strict=True))
        mix = qualification_mix(party_shares, profiles)
        if mix is None:
            continue
        mix = blend_turnout(mix, _area_turnout(turnout, area_id), profiles)
        info = population_by_area.loc[area_id] if area_id in population_by_area.index else None
        if info is None:
            adults = None
            name = None
            bezirk = None
        else:
            adults = adult_population(
                info["population"], info["share_18_to_64_pct"], info["share_65_plus_pct"]
            )
            name = None if pd.isna(info["name"]) else info["name"]
            bezirk = None if pd.isna(info["bezirk"]) else info["bezirk"]
        people = (
            allocate_people(adults, mix.pct)
            if adults is not None
            else dict.fromkeys((level for level, _, _ in LEVELS), None)
        )
        for level, _, label in LEVELS:
            rows.append(
                {
                    "area_id": area_id,
                    "name": name,
                    "bezirk": bezirk,
                    "level": level,
                    "label": label,
                    "pct": mix.pct[level],
                    "people": people[level],
                    "adults": None if adults is None else int(round(adults)),
                    "covered_vote_pct": mix.covered_vote_pct,
                }
            )
    return pd.DataFrame(rows, columns=_AREA_COLUMNS)


def load_profiles() -> dict[str, dict[str, float]]:
    """party_key -> qualification percents from party_education."""
    columns = [column for _, column, _ in LEVELS]
    query = "SELECT party_key, " + ", ".join(columns) + " FROM party_education"
    with get_engine().connect() as conn:
        rows = conn.execute(text(query)).mappings().all()
    return {
        str(row["party_key"]): {column: float(row[column]) for column in columns} for row in rows
    }


def load_adult_population() -> pd.DataFrame:
    query = """
        SELECT plr_id AS area_id, plr_name AS name, bezirk, population,
               share_18_to_64_pct, share_65_plus_pct
        FROM lor_planungsraeume
    """
    with get_engine().connect() as conn:
        return pd.read_sql(text(query), conn)


def load_planungsraum_education(shares: pd.DataFrame | None = None) -> pd.DataFrame:
    """Qualification headcounts per Planungsraum.

    Pass Abgeordnetenhaus shares (area_id, party, value, and optionally scope) to reuse
    a frame already built from the election helpers. Otherwise those helpers are called.
    Turnout is the eligible-voter-weighted share of each Planungsraum.
    """
    from immo_scanner.explore.elections import load_plr_turnout

    if shares is None:
        from immo_scanner.explore.elections import (
            load_election_districts,
            load_plr_weights,
            party_shares_long,
            plr_party_shares,
        )

        shares = plr_party_shares(load_plr_weights(), party_shares_long(load_election_districts()))
    if "scope" in shares.columns:
        shares = shares.loc[shares["scope"] == "abgeordnetenhaus"]
    return estimate_areas(shares, load_profiles(), load_adult_population(), load_plr_turnout())


def attach_listing_education(
    frame: pd.DataFrame,
    profiles: Mapping[str, Mapping[str, float]] | None = None,
) -> pd.DataFrame:
    """Add est_abitur_pct and est_hochschule_pct from each row's district vote and turnout."""
    if profiles is None:
        profiles = load_profiles()
    abitur: list[float | None] = []
    hochschule: list[float | None] = []
    shares_column = frame["agh_party_shares"] if "agh_party_shares" in frame.columns else None
    if shares_column is None:
        frame["est_abitur_pct"] = None
        frame["est_hochschule_pct"] = None
        return frame
    turnouts = (
        frame["agh_turnout_pct"]
        if "agh_turnout_pct" in frame.columns
        else pd.Series([None] * len(frame), index=frame.index)
    )
    for value, turnout in zip(shares_column, turnouts, strict=True):
        mix = qualification_mix(value if isinstance(value, Mapping) else {}, profiles)
        if mix is None:
            abitur.append(None)
            hochschule.append(None)
            continue
        mix = blend_turnout(mix, turnout, profiles)
        abitur.append(round(mix.pct["abitur"], 1))
        hochschule.append(round(mix.pct["hochschule"], 1))
    frame["est_abitur_pct"] = abitur
    frame["est_hochschule_pct"] = hochschule
    return frame


def load_scoped_party_shares(
    bezirk: str | None,
) -> tuple[dict[str, float], dict[str, float], float | None, float | None]:
    """Vote-weighted AGH shares and eligible-weighted turnout for one Bezirk and Berlin."""
    query = """
        WITH districts AS (
            SELECT w.valid_votes, w.party_shares, b.bezirk
            FROM wahlbezirke w
            LEFT JOIN LATERAL (
                SELECT l.bezirk
                FROM lor_planungsraeume l
                WHERE ST_Contains(l.geom, ST_PointOnSurface(w.geom))
                LIMIT 1
            ) b ON true
            WHERE w.scope = 'abgeordnetenhaus' AND w.valid_votes > 0
        )
        SELECT s.key AS party,
               SUM(s.value::float * d.valid_votes) FILTER (
                   WHERE d.bezirk = :bezirk
               ) / NULLIF(SUM(d.valid_votes) FILTER (
                   WHERE d.bezirk = :bezirk
               ), 0) AS bezirk_share,
               SUM(s.value::float * d.valid_votes)
                   / NULLIF(SUM(d.valid_votes), 0) AS berlin_share
        FROM districts d, jsonb_each_text(d.party_shares) s
        WHERE s.value ~ '^[0-9.]+$'
        GROUP BY s.key
    """
    with get_engine().connect() as conn:
        rows = conn.execute(text(query), {"bezirk": bezirk}).all()
    bezirk_shares: dict[str, float] = {}
    berlin_shares: dict[str, float] = {}
    for party, local, city in rows:
        if city is not None:
            berlin_shares[str(party)] = float(city)
        if local is not None:
            bezirk_shares[str(party)] = float(local)
    turnout_query = """
        WITH districts AS (
            SELECT w.eligible_voters, w.voters, b.bezirk
            FROM wahlbezirke w
            LEFT JOIN LATERAL (
                SELECT l.bezirk
                FROM lor_planungsraeume l
                WHERE ST_Contains(l.geom, ST_PointOnSurface(w.geom))
                LIMIT 1
            ) b ON true
            WHERE w.scope = 'abgeordnetenhaus'
              AND w.eligible_voters > 0
              AND w.voters IS NOT NULL
        )
        SELECT
            (SUM(voters) FILTER (WHERE bezirk = :bezirk))::float
                / NULLIF(SUM(eligible_voters) FILTER (WHERE bezirk = :bezirk), 0) * 100
                AS bezirk_turnout,
            SUM(voters)::float / NULLIF(SUM(eligible_voters), 0) * 100 AS berlin_turnout
        FROM districts
    """
    with get_engine().connect() as conn:
        turnout_row = conn.execute(text(turnout_query), {"bezirk": bezirk}).one()
    return (
        bezirk_shares,
        berlin_shares,
        _number(turnout_row.bezirk_turnout),
        _number(turnout_row.berlin_turnout),
    )


def education_references(bezirk: str | None) -> dict[str, dict[str, float | None]]:
    """Bezirk and Berlin Abitur and Hochschule percents, blended with local turnout."""
    profiles = load_profiles()
    local_shares, berlin_shares, local_turnout, berlin_turnout = load_scoped_party_shares(bezirk)
    local = qualification_mix(local_shares, profiles)
    berlin = qualification_mix(berlin_shares, profiles)
    if local is not None:
        local = blend_turnout(local, local_turnout, profiles)
    if berlin is not None:
        berlin = blend_turnout(berlin, berlin_turnout, profiles)
    references: dict[str, dict[str, float | None]] = {}
    for level, key in REFERENCE_KEYS.items():
        references[key] = {
            "bezirk": None if local is None else local.pct[level],
            "berlin": None if berlin is None else berlin.pct[level],
        }
    return references
