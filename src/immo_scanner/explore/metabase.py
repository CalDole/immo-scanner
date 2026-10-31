"""Configure Metabase via its REST API: admin user, database connection, dashboard."""

import logging
import time
from typing import Any

import httpx
from sqlalchemy.engine import make_url

from immo_scanner.config import Settings

log = logging.getLogger(__name__)

DATABASE_NAME = "Immo Scanner"
DASHBOARD_NAME = "Berlin property market"

CARDS: list[dict[str, Any]] = [
    {
        "name": "Active listings",
        "display": "scalar",
        "sql": "SELECT count(*) AS listings FROM listing_features WHERE status = 'active'",
        "viz": {},
        "size": (6, 3),
    },
    {
        "name": "Median price per m² (active)",
        "display": "scalar",
        "sql": """SELECT round(percentile_cont(0.5) WITHIN GROUP (ORDER BY price_per_m2)::numeric)
                  AS eur_per_m2 FROM listing_features WHERE status = 'active'""",
        "viz": {},
        "size": (6, 3),
    },
    {
        "name": "Median days on market (removed listings)",
        "display": "scalar",
        "sql": """SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY days_on_market)
                  AS days FROM listing_features WHERE status = 'removed'""",
        "viz": {},
        "size": (6, 3),
    },
    {
        "name": "Listings with price drops",
        "display": "scalar",
        "sql": """SELECT count(*) AS listings FROM listing_features
                  WHERE status = 'active' AND price_change_pct < 0""",
        "viz": {},
        "size": (6, 3),
    },
    {
        "name": "Median price per m² by Bezirk",
        "display": "bar",
        "sql": """SELECT bezirk,
                         round(percentile_cont(0.5) WITHIN GROUP (ORDER BY price_per_m2)::numeric)
                         AS median_eur_m2,
                         count(*) AS listings
                  FROM listing_features
                  WHERE status = 'active' AND bezirk IS NOT NULL AND price_per_m2 IS NOT NULL
                  GROUP BY bezirk ORDER BY median_eur_m2 DESC""",
        "viz": {"graph.dimensions": ["bezirk"], "graph.metrics": ["median_eur_m2"]},
        "size": (12, 8),
    },
    {
        "name": "New listings per week by source",
        "display": "line",
        "sql": """SELECT date_trunc('week', first_seen)::date AS week, source, count(*) AS listings
                  FROM listing_features GROUP BY 1, 2 ORDER BY 1""",
        "viz": {"graph.dimensions": ["week", "source"], "graph.metrics": ["listings"]},
        "size": (12, 8),
    },
    {
        "name": "Map of active listings",
        "display": "map",
        "sql": """SELECT title, price, price_per_m2, rooms, living_area, commute_minutes,
                         commute_workplace2_minutes, commute_citycenter_minutes,
                         nearest_rail_m, lat, lon, url
                  FROM listing_features
                  WHERE status = 'active' AND lat IS NOT NULL""",
        "viz": {"map.type": "pin", "map.latitude_column": "lat", "map.longitude_column": "lon"},
        "size": (24, 12),
    },
    {
        "name": "Price per m² vs. commute time",
        "display": "scatter",
        "sql": """SELECT commute_minutes, price_per_m2, source FROM listing_features
                  WHERE status = 'active' AND commute_minutes IS NOT NULL
                    AND price_per_m2 IS NOT NULL""",
        "viz": {
            "graph.dimensions": ["commute_minutes", "source"],
            "graph.metrics": ["price_per_m2"],
        },
        "size": (12, 8),
    },
    {
        "name": "Recent price drops",
        "display": "table",
        "sql": """SELECT title, bezirk, ortsteil, prognoseraum, kiez, price, first_price,
                         price_change_pct, price_per_m2, days_on_market, url
                  FROM listing_features
                  WHERE status = 'active' AND price_change_pct < 0
                  ORDER BY price_change_pct LIMIT 100""",
        "viz": {},
        "size": (12, 8),
    },
    {
        "name": "Candidates: best value near rail",
        "display": "table",
        "sql": """SELECT title, bezirk, ortsteil, prognoseraum, kiez, price, living_area,
                         rooms, bedrooms, bathrooms, apartment_type, floor, total_floors,
                         year_built, available_from, provision, posted_on,
                         has_balcony, has_terrace, has_elevator, has_parking, has_cellar,
                         has_fitted_kitchen, balcony_count, heating_type, price_per_m2,
                         commute_minutes, commute_workplace2_minutes, commute_citycenter_minutes,
                         nearest_rail_m, nearest_sbahn, nearest_ubahn,
                         grundschule_name, nearest_grundschule, nearest_grundschule_m,
                         nearest_gymnasium, nearest_gymnasium_m, nearest_gymnasium_2,
                         nearest_gymnasium_2_m, nearest_gymnasium_3, nearest_gymnasium_3_m,
                         nearest_montessori_kita, nearest_montessori_kita_m, url
                  FROM listing_features
                  WHERE status = 'active' AND nearest_rail_m < 1000 AND price_per_m2 IS NOT NULL
                  ORDER BY price_per_m2 LIMIT 200""",
        "viz": {},
        "size": (24, 10),
    },
    {
        "name": "Abgeordnetenhaus 2026 (Zweitstimmen) per property",
        "display": "table",
        "sql": """SELECT title, bezirk, ortsteil, prognoseraum, kiez, address, price, price_per_m2,
                         agh_wahlbezirk, agh_turnout_pct,
                         agh_vote_linke, agh_vote_spd, agh_vote_gruene,
                         agh_vote_cdu, agh_vote_afd, url
                  FROM listing_features
                  WHERE status = 'active' AND agh_wahlbezirk IS NOT NULL
                  ORDER BY agh_vote_linke DESC NULLS LAST LIMIT 500""",
        "viz": {},
        "size": (24, 10),
    },
    {
        "name": "Listing scores",
        "display": "table",
        "sql": """SELECT title, source, bezirk, ortsteil, price, price_per_m2,
                         total_score, distance_score, building_score, flat_score,
                         parking_score, neighborhood_score,
                         commute_score, energy_class_score, hausgeld_score,
                         building_age_score, building_age_class_score, heating_demand_score,
                         rooms_score, floor_area_score, basement_score, balconies_score,
                         bathrooms_score, kitchen_score, education_score, school_type_score,
                         tolerance_score,
                         origin_diversity_score, citizenship_score, oic_score,
                         crime_score, incident_score, url
                  FROM listing_features
                  WHERE status = 'active'
                  ORDER BY total_score DESC NULLS LAST LIMIT 500""",
        "viz": {},
        "size": (24, 10),
    },
]


class Metabase:
    def __init__(self, base_url: str) -> None:
        self.client = httpx.Client(base_url=base_url.rstrip("/") + "/api", timeout=60)

    def wait_until_ready(self, timeout_s: int = 300) -> None:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            try:
                if self.client.get("/health").json().get("status") == "ok":
                    return
            except (httpx.HTTPError, ValueError):
                pass
            log.info("Waiting for Metabase to start...")
            time.sleep(5)
        raise TimeoutError("Metabase did not become ready")

    def request(self, method: str, path: str, **kwargs: Any) -> Any:
        response = self.client.request(method, path, **kwargs)
        if response.is_error:
            raise RuntimeError(f"Metabase {method} {path}: {response.status_code} {response.text}")
        return response.json() if response.content else None

    def login(self, email: str, password: str) -> None:
        properties = self.request("GET", "/session/properties")
        if not properties.get("has-user-setup"):
            log.info("Creating Metabase admin user %s", email)
            session = self.request(
                "POST",
                "/setup",
                json={
                    "token": properties["setup-token"],
                    "user": {
                        "email": email,
                        "password": password,
                        "first_name": "Immo",
                        "last_name": "Scanner",
                        "site_name": "Immo Scanner",
                    },
                    "prefs": {"site_name": "Immo Scanner", "allow_tracking": False},
                },
            )
        else:
            session = self.request(
                "POST", "/session", json={"username": email, "password": password}
            )
        self.client.headers["X-Metabase-Session"] = session["id"]

    def ensure_database(self, settings: Settings) -> int:
        databases = self.request("GET", "/database")
        databases = databases.get("data", databases) if isinstance(databases, dict) else databases
        for db in databases:
            if db["name"] == DATABASE_NAME:
                return db["id"]
        url = make_url(settings.database_url)
        db = self.request(
            "POST",
            "/database",
            json={
                "engine": "postgres",
                "name": DATABASE_NAME,
                "details": {
                    "host": settings.metabase_db_host,
                    "port": url.port or 5432,
                    "dbname": url.database,
                    "user": url.username,
                    "password": url.password,
                    "ssl": False,
                },
            },
        )
        self.request("POST", f"/database/{db['id']}/sync_schema")
        return db["id"]

    def find_dashboard(self) -> int | None:
        for dashboard in self.request("GET", "/dashboard"):
            if dashboard["name"] == DASHBOARD_NAME and not dashboard.get("archived"):
                return dashboard["id"]
        return None

    def _create_card(self, database_id: int, spec: dict[str, Any]) -> dict:
        return self.request(
            "POST",
            "/card",
            json={
                "name": spec["name"],
                "display": spec["display"],
                "visualization_settings": spec["viz"],
                "dataset_query": {
                    "type": "native",
                    "database": database_id,
                    "native": {"query": spec["sql"]},
                },
            },
        )

    def update_cards(self, dashboard_id: int, database_id: int) -> None:
        """Refresh the cards on the dashboard and append the ones that are new."""
        specs = {spec["name"]: spec for spec in CARDS}
        dashboard = self.request("GET", f"/dashboard/{dashboard_id}")
        dashcards = dashboard.get("dashcards", [])
        present = set()
        for dashcard in dashcards:
            card = dashcard.get("card") or {}
            spec = specs.get(card.get("name"))
            if spec is None or not card.get("id"):
                continue
            present.add(spec["name"])
            current = self.request("GET", f"/card/{card['id']}")
            query = current.get("dataset_query") or {}
            if "native" in query:
                query["native"]["query"] = spec["sql"]
            else:
                for stage in query.get("stages", []):
                    if "native" in stage:
                        stage["native"] = spec["sql"]
            current["display"] = spec["display"]
            current["visualization_settings"] = spec["viz"]
            self.request("PUT", f"/card/{card['id']}", json=current)
            log.info("Updated Metabase card %s", spec["name"])

        missing = [spec for spec in CARDS if spec["name"] not in present]
        if not missing:
            return
        row = max((d.get("row", 0) + d.get("size_y", 0) for d in dashcards), default=0)
        added = []
        for i, spec in enumerate(missing):
            card = self._create_card(database_id, spec)
            width, height = spec["size"]
            added.append(
                {
                    "id": -(i + 1),
                    "card_id": card["id"],
                    "row": row,
                    "col": 0,
                    "size_x": width,
                    "size_y": height,
                }
            )
            row += height
            log.info("Added Metabase card %s", spec["name"])
        self.request("PUT", f"/dashboard/{dashboard_id}", json={"dashcards": dashcards + added})

    def create_dashboard(self, database_id: int) -> int:
        dashboard = self.request("POST", "/dashboard", json={"name": DASHBOARD_NAME})
        dashcards = []
        row = col = 0
        row_height = 0
        for i, spec in enumerate(CARDS):
            card = self._create_card(database_id, spec)
            width, height = spec["size"]
            if col + width > 24:
                row += row_height
                col = row_height = 0
            dashcards.append(
                {
                    "id": -(i + 1),
                    "card_id": card["id"],
                    "row": row,
                    "col": col,
                    "size_x": width,
                    "size_y": height,
                }
            )
            col += width
            row_height = max(row_height, height)
        self.request("PUT", f"/dashboard/{dashboard['id']}", json={"dashcards": dashcards})
        return dashboard["id"]


def setup_metabase(settings: Settings) -> str:
    if not settings.metabase_admin_email or not settings.metabase_admin_password:
        raise ValueError("Set IMMO_METABASE_ADMIN_EMAIL and IMMO_METABASE_ADMIN_PASSWORD")
    metabase = Metabase(settings.metabase_url)
    metabase.wait_until_ready()
    metabase.login(settings.metabase_admin_email, settings.metabase_admin_password)
    database_id = metabase.ensure_database(settings)
    dashboard_id = metabase.find_dashboard()
    if dashboard_id is None:
        dashboard_id = metabase.create_dashboard(database_id)
    else:
        metabase.update_cards(dashboard_id, database_id)
    return f"{settings.metabase_url.rstrip('/')}/dashboard/{dashboard_id}"
