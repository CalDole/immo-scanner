"""Election results per voting district (Wahlbezirk).

Berlin publishes results for polling-station districts (Urnenwahlbezirke) and for
postal-vote districts (Briefwahlbezirke), each of which covers several polling-station
districts. Postal votes are a large share of all votes, so results are aggregated to
the Briefwahlbezirk level when possible: polygons are dissolved and all votes summed.

Several elections can be stored side by side; each is identified by a `scope`
("bundestag", "abgeordnetenhaus") and loaded independently.

Column names differ between elections, so keys are detected heuristically. The loader
logs which columns it used and how many districts matched.
"""

import logging
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from itertools import product
from pathlib import Path

import geopandas as gpd
import pandas as pd
from sqlalchemy import delete

from immo_scanner.config import Settings, get_settings
from immo_scanner.db.models import Wahlbezirk
from immo_scanner.db.session import session_scope
from immo_scanner.reference.common import (
    find_column,
    read_csv_flexible,
    read_geo,
    to_multipolygon,
    to_number,
)
from immo_scanner.reference.download import download

log = logging.getLogger(__name__)

PARTY_KEYS = {
    "cdu": "cdu",
    "spd": "spd",
    "grune": "gruene",
    "gruene": "gruene",
    "bundnis90diegrunen": "gruene",
    "dielinke": "linke",
    "linke": "linke",
    "afd": "afd",
    "fdp": "fdp",
    "bsw": "bsw",
}

# Matched against the slug when it is a full party name rather than an abbreviation.
PARTY_PATTERNS = (
    ("bsw", r"bundnissahrawagenknecht"),
    ("gruene", r"bundnis90|grunen?$"),
    ("linke", r"^dielinke"),
    ("cdu", r"christlichdemokratische"),
    ("spd", r"^sozialdemokratischepartei"),
    ("afd", r"^alternativefurdeutschland"),
    ("fdp", r"^freiedemokratischepartei"),
)

PARTY_CODE_RE = re.compile(r"^P\d+$")
UNUSED_PARTY_RE = re.compile(r"nicht besetzt", re.IGNORECASE)


def party_key(column: str) -> str:
    ascii_name = unicodedata.normalize("NFKD", column).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]", "", ascii_name.lower())
    if slug in PARTY_KEYS:
        return PARTY_KEYS[slug]
    for key, pattern in PARTY_PATTERNS:
        if re.search(pattern, slug):
            return key
    return slug


def read_party_codes(path: Path) -> dict[str, str]:
    """Parse the Datensatzbeschreibung that maps the P01..P120 columns to party names."""
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp1252", errors="replace")
    codes = {}
    for line in text.splitlines():
        code, _, name = line.partition(";")
        code, name = code.strip(), name.strip().strip(";").strip()
        if PARTY_CODE_RE.fullmatch(code) and name and not UNUSED_PARTY_RE.search(name):
            codes[code] = name
    return codes


def expand_party_codes(
    results: pd.DataFrame, codes: Mapping[str, str]
) -> tuple[pd.DataFrame, list[str] | None]:
    """Rename anonymous P-code vote columns to party names and drop the percent columns.

    Returns the frame unchanged (and no party columns) if it does not use P-codes.
    """
    columns = list(results.columns)
    coded = [c for c in columns if PARTY_CODE_RE.fullmatch(c)]
    if not coded:
        return results, None
    # Every count column is followed by its percentage twin, e.g. "Gueltig" and "Gueltigp".
    percent_twins = {c for c in columns if c.endswith("p") and c[:-1] in columns}
    meta = [c for c in columns if c not in coded and c not in percent_twins]
    parties = [c for c in coded if c in codes]
    log.info("Named %d of %d party columns from the dataset description", len(parties), len(coded))
    frame = results[meta + parties].rename(columns={c: codes[c] for c in parties})
    return frame, [codes[c] for c in parties]


@dataclass(frozen=True)
class ElectionSource:
    scope: str
    name: str
    results_url: str
    geometry_url: str
    file_prefix: str
    # Only set when the results file uses P-codes instead of party names.
    parties_url: str | None = None


def election_sources(settings: Settings) -> dict[str, ElectionSource]:
    return {
        "bundestag": ElectionSource(
            scope="bundestag",
            name=settings.election_name,
            results_url=settings.election_results_url,
            geometry_url=settings.election_geometry_url,
            file_prefix="election",
        ),
        "abgeordnetenhaus": ElectionSource(
            scope="abgeordnetenhaus",
            name=settings.agh_election_name,
            results_url=settings.agh_election_results_url,
            geometry_url=settings.agh_election_geometry_url,
            parties_url=settings.agh_election_parties_url,
            file_prefix="election_agh",
        ),
    }


def normalize_key(value) -> str:
    return re.sub(r"[^0-9A-Z]", "", str(value).upper()).lstrip("0")


def _best_key_pair(
    result_candidates: dict[str, pd.Series], geo_candidates: dict[str, pd.Series]
) -> tuple[str, str, float]:
    best = ("", "", 0.0)
    for (r_name, r_keys), (g_name, g_keys) in product(
        result_candidates.items(), geo_candidates.items()
    ):
        g_set = set(g_keys.map(normalize_key))
        r_set = set(r_keys.map(normalize_key))
        if not g_set:
            continue
        rate = len(g_set & r_set) / len(g_set)
        if rate > best[2]:
            best = (r_name, g_name, rate)
    return best


def _candidates(df: pd.DataFrame, key_col: str | None, bez_col: str | None) -> dict:
    out = {}
    if key_col:
        keys = df[key_col].astype(str).str.strip()
        out[key_col] = keys
        if bez_col:
            out[f"{bez_col}+{key_col}"] = df[bez_col].astype(str).str.strip().str.zfill(2) + keys
    return out


def load_elections(
    scope: str = "bundestag",
    results_file: Path | None = None,
    geometry_file: Path | None = None,
    parties_file: Path | None = None,
) -> int:
    """Load one election's results, replacing any previously loaded data for that scope."""
    settings = get_settings()
    source = election_sources(settings)[scope]
    reference_dir = settings.reference_dir
    results_path = results_file or download(
        source.results_url, reference_dir / f"{source.file_prefix}_results.csv"
    )
    geo_path = geometry_file or download(
        source.geometry_url, reference_dir / f"{source.file_prefix}_districts.zip"
    )
    results = read_csv_flexible(results_path)
    party_cols = None
    if source.parties_url:
        codes_path = parties_file or download(
            source.parties_url, reference_dir / f"{source.file_prefix}_parties.csv"
        )
        results, party_cols = expand_party_codes(results, read_party_codes(codes_path))

    districts = build_districts(results, read_geo(geo_path), party_cols)
    rows = [
        Wahlbezirk(
            scope=source.scope,
            election=source.name,
            geom=to_multipolygon(district.pop("geometry")),
            **district,
        )
        for district in districts
    ]
    with session_scope() as session:
        session.execute(delete(Wahlbezirk).where(Wahlbezirk.scope == source.scope))
        session.add_all(rows)
    return len(rows)


def build_districts(
    results: pd.DataFrame, geo: gpd.GeoDataFrame, party_cols: list[str] | None = None
) -> list[dict]:
    """Aggregate votes per district and attach the (dissolved) district polygon.

    `party_cols` overrides the heuristic detection of which columns hold party votes.
    """
    stimmart = find_column(results.columns, r"^stimmart$")
    if stimmart and results[stimmart].nunique() > 1:
        codes = results[stimmart].astype(str).str.strip()
        results = results[codes.str.contains("zweit", case=False, na=False) | (codes == "2")]
    results = results.copy()
    geo = geo.copy()
    log.info("Result columns: %s", list(results.columns))
    log.info("Geometry columns: %s", [c for c in geo.columns if c != "geometry"])

    # Prefer Briefwahlbezirk level (includes postal votes), fall back to Urnenwahlbezirk.
    r_bwb = find_column(results.columns, r"briefwahlbezirk|^bwb")
    g_bwb = find_column(geo.columns, r"^bwb|briefwahl")
    r_uwb = find_column(results.columns, r"^(urnen)?wahlbezirk$|^uwb|wahlbezirksnummer")
    g_uwb = find_column(geo.columns, r"^uwb|^wahlbezirk|^wbz")
    r_bez = find_column(results.columns, r"bezirksnummer|^bez(nr)?$")
    g_bez = find_column(geo.columns, r"^bez(nr|irk)?$")

    level = "briefwahlbezirk" if r_bwb and g_bwb else "urnenwahlbezirk"
    r_key, g_key = (r_bwb, g_bwb) if level == "briefwahlbezirk" else (r_uwb, g_uwb)
    if not r_key or not g_key:
        raise ValueError(
            "Could not detect district key columns; see logged column names and pass "
            "matching files via --file/--geometry-file"
        )

    r_name, g_name, rate = _best_key_pair(
        _candidates(results, r_key, r_bez), _candidates(geo, g_key, g_bez)
    )
    log.info(
        "Joining %s: results[%s] = geometry[%s] (%.0f%% matched)",
        level,
        r_name,
        g_name,
        rate * 100,
    )
    if rate < 0.5:
        raise ValueError(f"Only {rate:.0%} of districts matched - check the key columns")

    results["_key"] = _candidates(results, r_key, r_bez)[r_name].map(normalize_key)
    geo["_key"] = _candidates(geo, g_key, g_bez)[g_name].map(normalize_key)

    # Umlauts are spelled out in some exports ("Gueltig", "Waehler"), hence the optional "e".
    valid_col = find_column(results.columns, r"^g[üu]e?ltig", exclude=r"^ung")
    eligible_col = find_column(results.columns, r"wahlberechtigt|^wberins")
    voters_col = find_column(results.columns, r"^w[äa]e?hler|w[äa]e?hlende")
    if not valid_col:
        raise ValueError("Could not find the column with valid votes ('Gültige Stimmen')")
    if party_cols is None:
        party_cols = [
            c
            for c in results.columns[list(results.columns).index(valid_col) + 1 :]
            if c != "_key"
            and not re.search(r"ung[üu]ltig", c, re.IGNORECASE)
            and to_number(results[c]).notna().any()
        ]
    log.info("Party columns: %s", party_cols)

    numeric_cols = [c for c in (valid_col, eligible_col, voters_col) if c] + party_cols
    numeric = results[["_key"]].copy()
    for column in numeric_cols:
        numeric[column] = to_number(results[column]).fillna(0)
    totals = numeric.groupby("_key").sum()

    shapes = geo[["_key", "geometry"]].dissolve(by="_key")

    districts = []
    for key, shape in shapes.geometry.items():
        if key not in totals.index:
            continue
        t = totals.loc[key]
        valid = float(t[valid_col])
        eligible = float(t[eligible_col]) if eligible_col else None
        voters = float(t[voters_col]) if voters_col else None
        shares = {
            party_key(c): round(float(t[c]) / valid * 100, 2) for c in party_cols if valid > 0
        }
        districts.append(
            {
                "id": key[:32],
                "eligible_voters": int(eligible) if eligible else None,
                "voters": int(voters) if voters else None,
                "valid_votes": int(valid),
                "turnout_pct": round(voters / eligible * 100, 1) if eligible and voters else None,
                "party_shares": shares,
                "geometry": shape,
            }
        )
    return districts
