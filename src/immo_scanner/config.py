import logging
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote_plus

from pydantic_settings import BaseSettings, SettingsConfigDict

log = logging.getLogger(__name__)

_LOCAL_DB_HOSTS = frozenset({"localhost", "127.0.0.1"})


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="IMMO_",
        extra="ignore",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
    )

    # No credentials in source. Streamlit reads st.secrets; the CLI reads
    # IMMO_DATABASE_URL from the environment or .env.
    database_url: str = ""
    data_dir: Path = Path("data")

    workplace_address: str | None = None
    workplace2_address: str | None = None
    citycenter_address: str | None = None
    workplace_lat: float | None = None
    workplace_lon: float | None = None
    commute_departure_hour: int = 8

    contact_email: str = ""
    # Local Nominatim from docker-compose. The public instance
    # (https://nominatim.openstreetmap.org) allows one request per second.
    nominatim_url: str = "http://localhost:8080"
    # Lookups that found nothing are asked again after this many days (0: never).
    geocode_retry_days: int = 14

    # Search criteria for all portals; leave unset for no limit.
    min_rooms: float | None = None
    min_living_area: float | None = None
    min_price: float | None = None
    max_price: float | None = None
    # Skip flats sold with a tenant ("vermietet"), from the title or the exposé.
    exclude_rented: bool = False

    crawl_max_pages: int = 20
    crawl_delay_min: float = 2.0
    crawl_delay_max: float = 5.0
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"
    )
    browser_headless: bool = False
    browser_profile_dir: Path = Path("data/browser-profile")

    kleinanzeigen_search_url: str = (
        "https://www.kleinanzeigen.de/s-wohnung-kaufen/berlin/anzeige:angebote/c196l3331"
    )
    # Price, area and room filters are added from the search criteria.
    immowelt_search_url: str = (
        "https://www.immowelt.de/classified-search?distributionTypes=Buy"
        "&estateTypes=Apartment&locations=AD08DE8634&order=DateDesc"
    )
    neubaukompass_search_url: str = "https://www.neubaukompass.de/neubau-immobilien/berlin/"
    immoscout24_search_url: str = (
        "https://www.immobilienscout24.de/Suche/de/berlin/berlin/wohnung-kaufen"
    )
    # Districts, flat types and sort order. Price, area and room groups are replaced
    # from the search criteria; rented flats are filtered after the crawl.
    bsk_search_url: str = (
        "https://www.bsk-immobilien.de/kaufen/suche/mitte-oder-steglitz-zehlendorf-oder-pankow"
        "-oder-lichtenberg-oder-treptow-koepenick-oder-tempelhof-schoeneberg-oder-spandau-oder"
        "-friedrichshain-kreuzberg-oder-marzahn-hellersdorf-oder-neukoelln-oder"
        "-charlottenburg-wilmersdorf-oder-reinickendorf--dachgeschoss-oder-terrassen-oder-etage"
        "-oder-erdgeschoss-oder-ferienwohnung-oder-maisonette-oder-keine_angabe-oder-apartment"
        "-oder-penthouse--neubau-egal--barrierefrei-egal--vermietet-egal--kaufpreis-bis-750000"
        "--wohnflaeche-ab-60--zimmer-ab-3--alle-ids--entry_date--desc"
    )
    # Place, property type and sorting. price.*, rooms.min and totalSurface.min are set
    # from the search criteria and `page` by the crawler.
    engelvoelkers_search_url: str = (
        "https://www.engelvoelkers.com/de/de/propertysearch?businessArea[]=residential"
        "&currency=EUR&hasSessionBounds=true&mapMode=place_mode&measurementSystem=metric"
        "&placeIds[]=ChIJAVkDPzdOqEcRcDteW0YgIQQ&placeName=Berlin%2C%20Deutschland"
        "&price.max=700000&propertyMarketingType[]=sale&propertyTypeSubType.apartment[]"
        "&rooms.min=3&searchMode=classic&searchRadius=0&sortingOptionId=SMART_SORT"
        "&totalSurface.min=60"
    )
    thinkimmo_api_url: str = "https://api.thinkimmo.com"
    # ThinkImmo search without from/size and without price, area or room limits.
    # Those bands are IMMO_MIN_ROOMS, IMMO_MIN_LIVING_AREA, IMMO_MIN_PRICE and
    # IMMO_MAX_PRICE. The rest is search alert "Auftrag2" (235511).
    thinkimmo_search_query: str = (
        "active=true&type=APARTMENTBUY&sortBy=publishDate,desc"
        "&geoSearches=%5B%7B%22geoSearchQuery%22%3A%22Berlin%22%2C%22geoSearchType%22%3A"
        "%22city%22%2C%22region%22%3A%22Berlin%22%7D%5D"
        "&rented=false"
        "&apartmentType=GROUND_FLOOR,RAISED_GROUND_FLOOR,APARTMENT,LOFT,MAISONETTE,"
        "TERRACED_FLAT,PENTHOUSE,ROOF_STOREY,OTHER,NO_INFORMATION"
        "&condition=FIRST_TIME_USE,WELL_KEPT,MINT_CONDITION,MODERNIZED,"
        "FIRST_TIME_USE_AFTER_REFURBISHMENT,REFURBISHED,FULLY_RENOVATED,NEGOTIABLE,NO_INFORMATION"
        "&allowUnknown=false&grossReturnAnd=false&favorite=false&excludedFields=true"
    )
    # Financing assumptions ThinkImmo uses for cashFlow and ownCapitalReturn.
    thinkimmo_finance_query: str = (
        "ownCapital=10&ownCapitalInPercent=true&ownCapitalAdditionalCost=false"
        "&managementCost=25&managementCostInPercent=true&interestRate=4&repaymentRate=1.5"
        "&additionalPurchaseCost=2&renovationCostInPercent=true"
    )
    # Purchase costs on top of the price, in percent, for the Flat detail page.
    grunderwerbsteuer_pct: float = 6.0
    notary_pct: float = 2.0
    is24_email_dir: Path = Path("data/is24-emails")
    # Local copies of ImmoScout24 photos, floor plans and documents.
    media_dir: Path = Path("data/media")
    # "large" (1106x830 WebP, ~100 KB), "original" (full JPEG, ~1.5 MB) or "none".
    is24_photo_size: Literal["large", "original", "none"] = "large"
    # "large" keeps the 950x1200 version the page shows, "original" the full-size file.
    neubaukompass_photo_size: Literal["large", "original", "none"] = "large"
    # "large" is the lightbox image ($_57); "none" keeps the URLs without downloading.
    kleinanzeigen_photo_size: Literal["large", "none"] = "large"
    # "large" is the lightbox WebP; "none" keeps the URLs without downloading.
    bsk_photo_size: Literal["large", "none"] = "large"
    # Uploadcare resized to 1600 px wide; "none" keeps the URLs without downloading.
    engelvoelkers_photo_size: Literal["large", "none"] = "large"

    overpass_url: str = "https://overpass-api.de/api/interpreter"
    lor_wfs_url: str = (
        "https://gdi.berlin.de/services/wfs/lor_2021?REQUEST=GetFeature&SERVICE=WFS"
        "&VERSION=2.0.0&TYPENAMES=lor_2021:a_lor_plr_2021&OUTPUTFORMAT=application/json"
        "&SRSNAME=EPSG:4326"
    )
    ortsteil_wfs_url: str = (
        "https://gdi.berlin.de/services/wfs/alkis_ortsteile?REQUEST=GetFeature&SERVICE=WFS"
        "&VERSION=2.0.0&TYPENAMES=alkis_ortsteile:ortsteile&OUTPUTFORMAT=application/json"
        "&SRSNAME=EPSG:4326"
    )
    # School sites and primary-school catchments. The catchment layer on this
    # service is schulen:schulen_esb; the separate /wfs/einschulbereiche path 404s.
    schulen_wfs_url: str = (
        "https://gdi.berlin.de/services/wfs/schulen?SERVICE=WFS&VERSION=2.0.0"
        "&REQUEST=GetFeature&TYPENAMES=schulen:schulen&OUTPUTFORMAT=application/json"
        "&SRSNAME=EPSG:4326&COUNT=10000"
    )
    einschulbereiche_wfs_url: str = (
        "https://gdi.berlin.de/services/wfs/schulen?SERVICE=WFS&VERSION=2.0.0"
        "&REQUEST=GetFeature&TYPENAMES=schulen:schulen_esb&OUTPUTFORMAT=application/json"
        "&SRSNAME=EPSG:4326&COUNT=10000"
    )
    population_csv_url: str = (
        "https://www.statistik-berlin-brandenburg.de/opendata/EWR_L21_202412E_Matrix.csv"
    )
    # Foreign residents per Planungsraum (Einwohnerregister). Origins: reference/origins.py.
    migration_csv_url: str = (
        "https://www.statistik-berlin-brandenburg.de/opendata/EWR_L21_202512A_Matrix.csv"
    )
    mss_index_url: str = (
        "https://www.berlin.de/sen/stadt/_assets/stadtdaten/stadtwissen/"
        "monitoring-soziale-stadtentwicklung/bericht-2025/21indexind_anteile_plr_mss2025.xlsx"
    )
    mss_context_url: str = (
        "https://www.berlin.de/sen/stadt/_assets/stadtdaten/stadtwissen/"
        "monitoring-soziale-stadtentwicklung/bericht-2025/41kontextind_anteile_plr_mss2025.xlsx"
    )
    # Mikrozensus Statistischer Bericht A I 10 / A I 11 / A VI 2 - j, Berlin. The link changes
    # with each yearly report: https://www.statistik-berlin-brandenburg.de/a-i-10-a-i-11-a-vi-2-j
    crime_atlas_url: str = (
        "https://www.berlin.de/polizei/_assets/dienststellen/lka/fallzahlen_hz-2016-2025.xlsx"
    )
    milieuschutz_pdf_url: str = (
        "https://www.berliner-mieterverein.de/downloads/"
        "strassenliste-milieuschutzgebiete-berlin.pdf"
    )
    # Milieuschutz only: Erhaltung der Zusammensetzung der Wohnbevölkerung.
    # erhaltgeb_es is städtebauliche Eigenart and is not this layer.
    milieuschutz_wfs_url: str = (
        "https://gdi.berlin.de/services/wfs/erhaltungsverordnungsgebiete?SERVICE=WFS"
        "&VERSION=2.0.0&REQUEST=GetFeature"
        "&TYPENAMES=erhaltungsverordnungsgebiete:erhaltgeb_em"
        "&OUTPUTFORMAT=application/json&SRSNAME=EPSG:4326&COUNT=1000"
    )
    mikrozensus_xlsx_url: str = (
        "https://download.statistik-berlin-brandenburg.de/d8300a3da8796e5a/140bf1d27d02/"
        "SB_A01-10-00_2025j01_BE.xlsx"
    )
    zensus_religion_grid_url: str = (
        "https://www.destatis.de/static/DE/zensus/gitterdaten/Religion.zip"
    )
    zensus_citizenship_grid_url: str = (
        "https://www.destatis.de/static/DE/zensus/gitterdaten/"
        "Zensus2022_Staatsangehoerigkeit_Gruppen_in_Gitterzellen.zip"
    )
    election_name: str = "Bundestagswahl 2025 (Zweitstimmen)"
    election_results_url: str = (
        "https://www.statistik-berlin-brandenburg.de/opendata/Berlin_BT25_W2.csv"
    )
    election_geometry_url: str = (
        "https://www.statistik-berlin-brandenburg.de/opendata/RBS_OD_UWB_BT25.zip"
    )
    agh_election_name: str = "Abgeordnetenhauswahl 2026 (Zweitstimmen)"
    agh_election_results_url: str = "https://wahlen-berlin.de/wahlen/BE2026/Afspraes/AGH/Datenexport_AGH2026_Zweitstimme_W_BE.csv"
    # The results file names parties as P01..P120; this description file maps codes to names.
    agh_election_parties_url: str = (
        "https://wahlen-berlin.de/wahlen/BE2026/Afspraes/AGH/DSB/"
        "DSB_Datenexport_AGH2026_Zweitstimme_W_BE.csv"
    )
    agh_election_geometry_url: str = (
        "https://www.statistik-berlin-brandenburg.de/opendata/RBS_OD_UWB_AH26.zip"
    )
    osm_pbf_url: str = "https://download.geofabrik.de/europe/germany/berlin-latest.osm.pbf"
    gtfs_url: str = "https://www.vbb.de/vbbgtfs"

    metabase_url: str = "http://localhost:3000"
    # How the Metabase container reaches PostgreSQL ("db" when Postgres runs in Compose).
    metabase_db_host: str = "host.docker.internal"
    metabase_admin_email: str = ""
    metabase_admin_password: str = ""

    @property
    def reference_dir(self) -> Path:
        return self.data_dir / "reference"

    @property
    def score_weights_path(self) -> Path:
        return self.data_dir / "score_weights.json"

    @property
    def http_contact_agent(self) -> str:
        contact = f" ({self.contact_email})" if self.contact_email else ""
        return f"immo-scanner/0.1{contact}"


def _normalize_database_url(url: str) -> str:
    """Point libpq-style URLs at the psycopg 3 SQLAlchemy dialect."""
    cleaned = url.strip()
    for prefix in (
        "postgresql+psycopg2://",
        "postgresql+psycopg://",
        "postgresql://",
        "postgres://",
    ):
        if cleaned.startswith(prefix):
            return "postgresql+psycopg://" + cleaned.removeprefix(prefix)
    return cleaned


def _streamlit_script_running() -> bool:
    try:
        from streamlit.runtime.scriptrunner import get_script_run_ctx
    except Exception:
        return False
    try:
        return get_script_run_ctx() is not None
    except Exception:
        return False


def _secret_text(value: object) -> str:
    return str(value).strip()


def _database_url_from_parts(postgres: Any) -> str | None:
    host = _secret_text(postgres.get("host", ""))
    user = _secret_text(postgres.get("username") or postgres.get("user") or "")
    password = str(postgres.get("password", ""))
    database = _secret_text(postgres.get("database") or postgres.get("dbname") or "")
    port = postgres.get("port") or 5432
    if not any((host, user, database, password)):
        return None
    remote_without_password = password == "" and host not in _LOCAL_DB_HOSTS
    if not host or not user or not database or remote_without_password:
        log.warning(
            "Streamlit [postgres] secrets are incomplete; using IMMO_DATABASE_URL if it is set."
        )
        return None
    query = "" if host in _LOCAL_DB_HOSTS else "?sslmode=require"
    return (
        f"postgresql+psycopg://{quote_plus(user)}:{quote_plus(password)}"
        f"@{host}:{port}/{database}{query}"
    )


def _database_url_from_streamlit_secrets() -> str | None:
    """Connection string from `.streamlit/secrets.toml` while the app is running."""
    if not _streamlit_script_running():
        return None
    try:
        import streamlit as st

        secrets = st.secrets
    except Exception:
        log.warning("Streamlit secrets are unavailable; using IMMO_DATABASE_URL if it is set.")
        return None

    url = _secret_text(secrets.get("DATABASE_URL", ""))
    if url:
        return url

    postgres = secrets.get("postgres")
    if not postgres:
        return None
    nested = _secret_text(postgres.get("url", ""))
    if nested:
        return nested
    return _database_url_from_parts(postgres)


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    url = _database_url_from_streamlit_secrets() or settings.database_url
    url = _normalize_database_url(url)
    if url != settings.database_url:
        settings = settings.model_copy(update={"database_url": url})
    return settings
