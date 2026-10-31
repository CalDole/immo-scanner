"""Numbers behind the Flat detail page: ranks, score inputs, purchase costs, media.

Nothing here imports Streamlit, so each choice can be tested on its own.
"""

import math
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from immo_scanner.explore.scoring import (
    DEFAULT_WEIGHTS,
    GROUPS,
    Weights,
    avg_commute_minutes,
    subscores_of,
)


def _number(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _finite(values: Iterable[object]) -> list[float]:
    return [number for number in (_number(value) for value in values) if number is not None]


def rank(
    value: object, values: Iterable[object], *, higher_is_better: bool = True
) -> tuple[int, int] | None:
    """Place of `value` among `values` (1 is best) and how many values were ranked."""
    own = _number(value)
    pool = _finite(values)
    if own is None or not pool:
        return None
    better = sum(1 for item in pool if (item > own if higher_is_better else item < own))
    return better + 1, len(pool)


def median_of(values: Iterable[object]) -> float | None:
    pool = sorted(_finite(values))
    if not pool:
        return None
    middle = len(pool) // 2
    if len(pool) % 2:
        return pool[middle]
    return (pool[middle - 1] + pool[middle]) / 2


def delta_to_median(value: object, values: Iterable[object]) -> float | None:
    own = _number(value)
    median = median_of(values)
    if own is None or median is None:
        return None
    return own - median


@dataclass(frozen=True)
class ScorePart:
    label: str
    value: str | None
    score: float | None
    rule: str
    weight: float = 1.0


@dataclass(frozen=True)
class ScoreGroup:
    label: str
    column: str
    score: float | None
    parts: list[ScorePart]
    weight: float = 1.0

    @property
    def known(self) -> int:
        return sum(1 for part in self.parts if part.score is not None)


def _yes_no(value: object) -> str | None:
    if isinstance(value, bool):
        return "Yes" if value else "No"
    return None


def _of_100(value: object) -> str | None:
    number = _number(value)
    if number is None:
        return None
    return f"{number:.1f} of 100"


def _fmt(value: object, unit: str = "", digits: int = 0) -> str | None:
    number = _number(value)
    if number is None:
        return None
    return f"{number:,.{digits}f}{unit}"


def _age_class_label(age_class: Mapping[str, Any] | None) -> str | None:
    if not age_class or not age_class.get("class_code"):
        return None
    start, end = age_class.get("year_from"), age_class.get("year_to")
    if start is None:
        period = f"until {end}"
    elif end is None:
        period = f"from {start}"
    else:
        period = f"{start}–{end}"
    risk = _number(age_class.get("climate_risk_score"))
    risk_text = f", climate risk score {risk:.0f} of 100" if risk is not None else ""
    return f"Class {age_class['class_code']} ({period}){risk_text}"


def _balconies(row: Mapping[str, Any]) -> str | None:
    count = _number(row.get("balcony_count"))
    if count is not None:
        return f"{count:.0f}"
    flag = row.get("has_balcony")
    if flag is True:
        return "Yes, count not stated"
    return "None" if flag is False else None


def _abi(averages: list[float | None] | None) -> str | None:
    if not averages or all(_number(item) is None for item in averages):
        return None
    return ", ".join("–" if _number(item) is None else f"{float(item):.2f}" for item in averages)


def _crime_input(row: Mapping[str, Any]) -> str | None:
    rate = _number(row.get("crime_rate"))
    if rate is None:
        return None
    text = f"{rate:,.0f} per 100,000"
    year = _number(row.get("crime_year"))
    if year is not None:
        text += f" ({year:.0f})"
    return text


def _incident_input(row: Mapping[str, Any]) -> str | None:
    rate = _number(row.get("incident_rate"))
    if rate is None:
        return None
    rate_text = f"{rate:.2f} per 1,000 per year"
    count = _number(row.get("incident_count"))
    if count is None:
        return rate_text
    noun = "incident" if count == 1 else "incidents"
    return f"{count:.0f} {noun}, {rate_text}"


def _parties(row: Mapping[str, Any]) -> str | None:
    parts = [
        f"{label} {share:.0f}%"
        for column, label in (
            ("agh_vote_afd", "AfD"),
            ("agh_vote_bsw", "BSW"),
            ("agh_vote_cdu", "CDU"),
        )
        if (share := _number(row.get(column))) is not None
    ]
    return ", ".join(parts) or None


def score_breakdown(
    row: Mapping[str, Any],
    age_class: Mapping[str, Any] | None = None,
    abi_averages: list[float | None] | None = None,
    weights: Weights | None = None,
) -> list[ScoreGroup]:
    """Each group score with its sub-scores, the input behind each and the rule."""
    commute = avg_commute_minutes(row.get("commute_minutes"), row.get("commute_workplace2_minutes"))

    inputs: dict[str, str | None] = {
        "commute_score": _fmt(commute, " min"),
        "energy_class_score": row.get("energy_efficiency_class") or None,
        "hausgeld_score": _fmt(row.get("hausgeld"), " €/month"),
        "building_age_score": _fmt(row.get("building_age_years"), " years"),
        "building_age_class_score": _age_class_label(age_class),
        "heating_demand_score": (age_class or {}).get("typical_heating_demand") or None,
        "rooms_score": _fmt(row.get("rooms"), digits=1),
        "floor_area_score": _fmt(row.get("living_area"), " m²"),
        "basement_score": _yes_no(row.get("has_cellar")),
        "parking_score": _yes_no(row.get("has_parking")),
        "balconies_score": _balconies(row),
        "bathrooms_score": _fmt(row.get("bathrooms")),
        "kitchen_score": _yes_no(row.get("has_fitted_kitchen")),
        "education_score": _abi(abi_averages),
        "school_type_score": _fmt(row.get("school_type_level"), digits=2),
        "tolerance_score": _parties(row),
        "origin_diversity_score": _of_100(row.get("origin_diversity_index")),
        "citizenship_score": _fmt(row.get("german_citizen_share"), "%", 1),
        "oic_score": _fmt(row.get("oic_share"), "%", 1),
        "crime_score": _crime_input(row),
        "incident_score": _incident_input(row),
    }
    weights = weights or DEFAULT_WEIGHTS
    return [
        ScoreGroup(
            group.label,
            group.column,
            _number(row.get(group.column)),
            [
                ScorePart(
                    sub.label,
                    inputs.get(sub.column),
                    _number(row.get(sub.column)),
                    sub.rule,
                    weights.subscore(sub.column),
                )
                for sub in subscores_of(group.column)
            ],
            weights.group(group.column),
        )
        for group in GROUPS
    ]


_NO_COMMISSION = ("provisionsfrei", "keine provision", "courtagefrei", "ohne provision")
_PERCENT = re.compile(r"(\d+(?:[.,]\d+)?)\s*%")


def parse_commission(text: object) -> float | None:
    """Buyer's commission in percent from portal text such as '3,57 % inkl. MwSt.'."""
    if not isinstance(text, str) or not text.strip():
        return None
    lowered = text.strip().lower()
    if lowered in {"nein", "no", "keine", "0", "0%"} or any(
        phrase in lowered for phrase in _NO_COMMISSION
    ):
        return 0.0
    match = _PERCENT.search(lowered)
    if match:
        return float(match.group(1).replace(",", "."))
    return None


@dataclass(frozen=True)
class CostLine:
    label: str
    pct: float | None
    amount: float | None


@dataclass(frozen=True)
class PurchaseCosts:
    lines: list[CostLine]
    total: float | None
    commission_known: bool


def purchase_costs(
    price: object,
    commission_text: object,
    commission_pct: object,
    *,
    tax_pct: float,
    notary_pct: float,
) -> PurchaseCosts:
    """Price plus Grunderwerbsteuer, notary and land registry, and the buyer's commission.

    An unknown commission is left out of the total and flagged, so the total is a floor.
    """
    amount = _number(price)
    commission = parse_commission(commission_text)
    if commission is None:
        commission = _number(commission_pct)

    def share(pct: float | None) -> float | None:
        if amount is None or pct is None:
            return None
        return amount * pct / 100

    lines = [
        CostLine("Price", None, amount),
        CostLine("Grunderwerbsteuer", tax_pct, share(tax_pct)),
        CostLine("Notary and land registry", notary_pct, share(notary_pct)),
        CostLine("Buyer's commission", commission, share(commission)),
    ]
    total = None
    if amount is not None:
        total = sum(line.amount for line in lines if line.amount is not None)
    return PurchaseCosts(lines, total, commission is not None)


def price_history(own: pd.DataFrame, thinkimmo: object) -> pd.DataFrame:
    """date, price, origin from the crawler's snapshots and ThinkImmo's portal history."""
    frames = []
    if not own.empty and {"scraped_at", "price"} <= set(own.columns):
        frames.append(
            pd.DataFrame(
                {
                    "date": pd.to_datetime(own["scraped_at"], utc=True),
                    "price": pd.to_numeric(own["price"], errors="coerce"),
                    "origin": "Scanner",
                }
            )
        )
    entries = thinkimmo if isinstance(thinkimmo, list) else []
    rows = [
        {
            "date": item.get("creationDate"),
            "price": _number(item.get("buyingPrice")),
            "origin": str(item.get("platformName") or "ThinkImmo"),
        }
        for item in entries
        if isinstance(item, Mapping)
    ]
    if rows:
        extra = pd.DataFrame(rows)
        extra["date"] = pd.to_datetime(extra["date"], utc=True, errors="coerce")
        frames.append(extra)
    if not frames:
        return pd.DataFrame(columns=["date", "price", "origin"])
    history = pd.concat(frames, ignore_index=True).dropna(subset=["date", "price"])
    return history.sort_values("date", kind="stable").reset_index(drop=True)


def price_cuts(history: pd.DataFrame) -> int:
    """How often the asking price went down, over all origins in date order."""
    if history.empty:
        return 0
    prices = history["price"].tolist()
    return sum(1 for before, after in zip(prices, prices[1:], strict=False) if after < before)


@dataclass(frozen=True)
class MediaItem:
    image: str
    caption: str | None
    origin: str = ""


def _media_item(
    item: Mapping[str, Any], origin: str, exists: Callable[[Path], bool]
) -> MediaItem | None:
    caption = item.get("caption") or item.get("title") or None
    path = item.get("path")
    if isinstance(path, str) and path.strip() and exists(Path(path)):
        return MediaItem(path, caption, origin)
    url = item.get("url") or item.get("originalUrl")
    if isinstance(url, str) and url.startswith(("http://", "https://")):
        return MediaItem(url, caption, origin)
    return None


def _kind(item: Mapping[str, Any]) -> str:
    if "floorPlan" in item:
        return "FLOORPLAN" if item.get("floorPlan") else "PICTURE"
    return str(item.get("type") or "PICTURE").upper()


def gallery(
    groups: list[tuple[str, list[dict[str, Any]]]],
    source: object = None,
    exists: Callable[[Path], bool] = Path.exists,
) -> tuple[list[MediaItem], list[MediaItem]]:
    """All photos and all floor plans, each from the portal that has the most.

    Portals copy the same photos, so they are not merged. On a tie the listing's
    own portal wins and ThinkImmo, which keeps at most five, comes last. A local
    copy is preferred over the portal URL when the file is still there.
    """
    ordered = sorted(groups, key=lambda group: (group[0] != source, group[0] == "thinkimmo"))

    def pick(kind: str) -> list[MediaItem]:
        best: list[MediaItem] = []
        for origin, media in ordered:
            items = [
                found
                for item in media
                if isinstance(item, Mapping)
                and _kind(item) == kind
                and (found := _media_item(item, origin, exists)) is not None
            ]
            if len(items) > len(best):
                best = items
        return best

    return pick("PICTURE"), pick("FLOORPLAN")


def match_gymnasium(name: object, stats: pd.DataFrame) -> dict[str, Any] | None:
    """The gymnasium_statistics row for an OpenStreetMap school name.

    Same rule as the education score: an exact name first, else one name
    containing the other, and the lower Abitur average on a tie.
    """
    if not isinstance(name, str) or not name.strip() or stats.empty:
        return None
    wanted = name.strip().casefold()
    names = stats["name"].fillna("").str.casefold()
    exact = stats[names == wanted]
    if exact.empty:
        contains = names.map(lambda other: bool(other) and (other in wanted or wanted in other))
        exact = stats[contains]
    if exact.empty:
        return None
    return plain_record(exact.sort_values("abi_average", na_position="last").iloc[0])


def nearest_gymnasium_pass_rates(frame: pd.DataFrame, stats: pd.DataFrame) -> pd.Series:
    """Published Abitur pass rate of each listing's nearest Gymnasium.

    Names match `match_gymnasium`. A school with no published rate stays missing.
    """
    if (
        "nearest_gymnasium" not in frame.columns
        or stats.empty
        or "abi_pass_rate_pct" not in stats.columns
    ):
        return pd.Series(np.nan, index=frame.index, dtype="float64")
    lookup: dict[object, float] = {}
    for name in pd.unique(frame["nearest_gymnasium"].dropna()):
        match = match_gymnasium(name, stats)
        rate = None if match is None else match.get("abi_pass_rate_pct")
        if rate is not None:
            lookup[name] = float(rate)
    return frame["nearest_gymnasium"].map(lookup)


def plain_record(row: pd.Series) -> dict[str, Any]:
    """A frame row as Python scalars, with missing values as None."""
    record: dict[str, Any] = {}
    for key, value in row.items():
        if isinstance(value, np.generic):
            value = value.item()
        if isinstance(value, float) and math.isnan(value):
            value = None
        record[str(key)] = value
    return record
