import logging
import sys
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.logging import RichHandler
from rich.table import Table

from immo_scanner.config import get_settings

for _stream in (sys.stdout, sys.stderr):
    # Windows consoles default to cp1252, which can't encode every character in logs.
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(errors="replace")

app = typer.Typer(help="Berlin property scanner", no_args_is_help=True)
console = Console()
log = logging.getLogger("immo_scanner")


class Source(StrEnum):
    all = "all"
    kleinanzeigen = "kleinanzeigen"
    immowelt = "immowelt"
    neubaukompass = "neubaukompass"
    immoscout24 = "immoscout24"
    bsk = "bsk"
    engelvoelkers = "engelvoelkers"


class ReferenceDataset(StrEnum):
    all = "all"
    stations = "stations"
    lor = "lor"
    population = "population"
    elections = "elections"
    agh_elections = "agh-elections"
    ortsteile = "ortsteile"
    schools = "schools"
    gymnasium_stats = "gymnasium-stats"
    sekundarschule_stats = "sekundarschule-stats"
    grundschulen = "grundschulen"
    mss = "mss"
    migration = "migration"
    origins = "origins"
    citizenship = "citizenship"
    religion = "religion"
    religion_bezirk = "religion-bezirk"
    income = "income"
    education = "education"
    crime = "crime"
    milieuschutz = "milieuschutz"
    milieuschutz_areas = "milieuschutz-areas"
    berliner_register = "berliner-register"


# No public download exists for these; they only load from --file and are skipped by `all`.
FILE_ONLY_DATASETS = {
    ReferenceDataset.religion_bezirk,
    ReferenceDataset.income,
}
# Scraped page by page (the first run takes about 25 minutes); only loaded when named.
EXPLICIT_DATASETS = {ReferenceDataset.berliner_register}


@app.callback()
def main(verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(message)s",
        handlers=[RichHandler(console=console, show_path=False)],
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)


EnrichOption = Annotated[
    bool,
    typer.Option(
        "--enrich/--no-enrich",
        help="Geocode new listings and refresh listing_features afterwards",
    ),
]


def _geocode(limit: int | None = None) -> None:
    """Geocode listings without a point; a failure is logged, never raised."""
    from immo_scanner.db.session import session_scope
    from immo_scanner.enrich import geocode

    try:
        with session_scope() as session:
            expired = geocode.expire_failed_geocodes(session, get_settings().geocode_retry_days)
        if expired:
            log.info("Expired %d old geocoder misses", expired)
        with session_scope() as session:
            log.info("Geocoded %d listings", geocode.geocode_listings(session, limit))
    except Exception:
        log.exception("Geocoding failed, continuing without it")


def _refresh_and_report() -> None:
    """Refresh listing_features, then warn about active listings without a Planungsraum."""
    from immo_scanner.db.session import session_scope
    from immo_scanner.enrich import features
    from immo_scanner.explore.gaps import (
        LOCATION_STATES,
        load_location_coverage,
        location_state_counts,
    )

    with session_scope() as session:
        features.refresh_features(session)
    log.info("listing_features refreshed")
    try:
        totals = location_state_counts(load_location_coverage()).set_index("key")["Total"]
    except Exception:
        log.exception("Could not count listings without a location")
        return
    labels = dict(LOCATION_STATES)
    missing = {key: int(totals[key]) for key in labels if key != "located" and totals[key]}
    if missing:
        detail = ", ".join(
            f"{count:,} {labels[key][0].lower()}{labels[key][1:]}" for key, count in missing.items()
        )
        total = sum(missing.values())
        log.warning(
            "%s active %s no Planungsraum (%s); see the Data gaps page",
            f"{total:,}",
            "listing has" if total == 1 else "listings have",
            detail,
        )


def _locate_listings(limit: int | None = None) -> None:
    _geocode(limit)
    _refresh_and_report()


def _commute() -> None:
    """Travel times for listings that have a position but no time yet. Failures are logged."""
    from immo_scanner.db.session import session_scope
    from immo_scanner.enrich import commute

    try:
        with session_scope() as session:
            log.info("Computed %d commute times", commute.compute_commute_times(session))
    except commute.CommuteUnavailable as exc:
        log.warning("Skipping commute times: %s", exc)
    except Exception:
        log.exception("Commute computation failed, continuing without it")


def _engelvoelkers_details(settings) -> None:
    """Exposés for Engel & Völkers listings that have none yet.

    The search page has no coordinates. The exposé does, and commute times need them.
    """
    from immo_scanner.db.repository import apply_rented_filter
    from immo_scanner.db.session import session_scope
    from immo_scanner.details.runner import fetch_details

    try:
        stats = fetch_details(settings, "engelvoelkers", limit=1000)
        log.info("engelvoelkers details: %s", stats)
    except Exception:
        log.exception("Fetching engelvoelkers exposés failed, continuing")
    with session_scope() as session:
        changed = apply_rented_filter(session, settings.exclude_rented)
    log.info("Rented filter: %d listings changed", changed)


@app.command("init-db")
def init_db() -> None:
    """Create or migrate the database schema."""
    from immo_scanner.db.migrate import upgrade

    upgrade()
    console.print("[green]Database schema is up to date.[/green]")


@app.command()
def crawl(
    source: Annotated[Source, typer.Argument()] = Source.all,
    max_pages: Annotated[int | None, typer.Option(help="Override IMMO_CRAWL_MAX_PAGES")] = None,
    enrich: EnrichOption = True,
) -> None:
    """Crawl listing sites and store results with price history.

    For Engel & Völkers, --enrich also fetches exposés that are still missing and
    computes commute times. The search page has no coordinates; the exposé does.
    """
    from immo_scanner.crawlers import CRAWLERS
    from immo_scanner.crawlers.criteria import Criteria
    from immo_scanner.db.repository import (
        apply_rented_filter,
        delete_listings,
        save_crawl_result,
    )
    from immo_scanner.db.session import session_scope

    settings = get_settings()
    criteria = Criteria.from_settings(settings)
    pages = max_pages or settings.crawl_max_pages
    names = list(CRAWLERS) if source == Source.all else [source.value]
    failures = []
    for name in names:
        started_at = datetime.now(UTC)
        log.info("Crawling %s (max %d pages)", name, pages)
        try:
            with CRAWLERS[name](settings) as crawler:
                result = crawler.crawl(max_pages=pages)
            matching = [listing for listing in result.listings if criteria.matches(listing)]
            rejected = {listing.external_id for listing in result.listings} - {
                listing.external_id for listing in matching
            }
            log.info(
                "%s: %d of %d listings match the search criteria",
                name, len(matching), len(result.listings),
            )
            result.listings = matching
            with session_scope() as session:
                stats = save_crawl_result(session, name, result, started_at)
                stats["no_longer_matching"] = delete_listings(session, name, rejected)
            log.info("%s: %s", name, stats)
        except Exception:
            log.exception("Crawler %s failed", name)
            failures.append(name)
    with session_scope() as session:
        changed = apply_rented_filter(session, settings.exclude_rented)
    log.info("Rented filter: %d listings changed", changed)
    if enrich:
        if "engelvoelkers" in names and "engelvoelkers" not in failures:
            _engelvoelkers_details(settings)
            _geocode()
            _commute()
            _refresh_and_report()
        else:
            _locate_listings()
    if failures:
        raise typer.Exit(code=1)


@app.command("crawl-thinkimmo")
def crawl_thinkimmo(
    max_pages: Annotated[
        int | None, typer.Option(help="Search pages of 50 (default: all)")
    ] = None,
    detail_limit: Annotated[int, typer.Option(help="Max exposés to fetch")] = 300,
    refresh_days: Annotated[
        int | None, typer.Option(help="Also re-fetch exposés older than this many days")
    ] = None,
    enrich: EnrichOption = True,
) -> None:
    """Crawl the ThinkImmo search (IMMO_THINKIMMO_SEARCH_QUERY) into thinkimmo_properties.

    Price, area and rooms in that request are the shared IMMO_* criteria. Properties
    link to the matching ImmoScout24/Immowelt/... listing; the others become `thinkimmo`
    listings, filtered by the same criteria.
    """
    from immo_scanner.crawlers.base import BlockedError
    from immo_scanner.crawlers.criteria import Criteria
    from immo_scanner.crawlers.thinkimmo import SOURCE, ThinkImmoClient
    from immo_scanner.db.repository import (
        link_thinkimmo_listings,
        mark_removed,
        mark_thinkimmo_removed,
        save_thinkimmo_detail,
        save_thinkimmo_properties,
        thinkimmo_ids_needing_details,
    )
    from immo_scanner.db.session import session_scope
    from immo_scanner.enrich import features

    settings = get_settings()
    started_at = datetime.now(UTC)
    with ThinkImmoClient(settings) as client:
        result = client.search(max_pages)
        with session_scope() as session:
            stats = save_thinkimmo_properties(session, result.records, started_at)
            session.flush()
            stats.update(
                link_thinkimmo_listings(
                    session, started_at, Criteria.from_settings(settings).matches
                )
            )
            if result.complete and result.records:
                stats["removed"] = mark_thinkimmo_removed(session, started_at)
                stats["listings_removed"] = mark_removed(session, SOURCE, started_at)
            else:
                log.info("thinkimmo: search incomplete, not marking unseen properties as removed")
        log.info("thinkimmo search (%s total): %s", result.total, stats)

        with session_scope() as session:
            ids = thinkimmo_ids_needing_details(session, detail_limit, refresh_days)
        fetched = 0
        blocked = False
        for number, property_id in enumerate(ids, 1):
            try:
                record = client.property(property_id)
            except BlockedError:
                log.warning("thinkimmo blocked, stopping after %d exposés", fetched)
                blocked = True
                break
            except Exception:
                log.exception("thinkimmo exposé %s failed", property_id)
                continue
            with session_scope() as session:
                save_thinkimmo_detail(session, property_id, record, datetime.now(UTC))
            fetched += 1
            if number % 25 == 0:
                log.info("thinkimmo exposés: %d of %d", number, len(ids))
        log.info("thinkimmo exposés fetched: %d of %d", fetched, len(ids))
    if enrich:
        _locate_listings()
    else:
        with session_scope() as session:
            features.refresh_features(session)
    if blocked:
        raise typer.Exit(code=1)


@app.command("import-is24-emails")
def import_is24_emails(
    directory: Annotated[Path | None, typer.Argument(help="Folder with .eml files")] = None,
    enrich: EnrichOption = True,
) -> None:
    """Import listings from saved ImmoScout24 search-alert emails (.eml)."""
    from immo_scanner.crawlers.criteria import Criteria
    from immo_scanner.crawlers.is24_email import mark_processed, parse_email_dir
    from immo_scanner.db.repository import save_listings
    from immo_scanner.db.session import session_scope

    settings = get_settings()
    criteria = Criteria.from_settings(settings)
    folder = directory or settings.is24_email_dir
    imported = 0
    for item in parse_email_dir(folder):
        listings = [listing for listing in item.listings if criteria.matches(listing)]
        with session_scope() as session:
            stats = save_listings(session, "immoscout24", listings, item.received_at)
        mark_processed(item)
        imported += 1
        console.print(f"{item.path.name}: {stats}")
    if enrich and imported:
        _locate_listings()


@app.command("load-reference")
def load_reference(
    dataset: Annotated[ReferenceDataset, typer.Argument()] = ReferenceDataset.all,
    file: Annotated[
        Path | None, typer.Option(help="Use a local file instead of downloading")
    ] = None,
    geometry_file: Annotated[
        Path | None, typer.Option(help="Local election geometry file (zip/shp/geojson)")
    ] = None,
    full: Annotated[
        bool, typer.Option(help="berliner-register: re-read every page, not just new incidents")
    ] = False,
) -> None:
    """Load reference data: stations, schools, LOR areas, population, elections, demographics.

    Demographics: `mss` (unemployment, low-wage share, migration and foreigner shares),
    `migration` (AfS register), `origins` (residents by origin per Planungsraum since 2021,
    Statistischer Bericht A I 16), `citizenship` and `religion` (Zensus 2022 grid), and the
    file-only `income`, `education` and `religion-bezirk`. `crime` is the Berlin crime atlas
    (cases and cases per 100,000 residents, per Bezirksregion, 2016–2025).
    `berliner-register` scrapes the Berliner Register incident chronicle (not part of `all`;
    geocode it afterwards with `immo geocode-incidents`). `milieuschutz` is the Berliner
    Mieterverein street list and sets Yes / Closeby / No on every listing. A listing with
    no street is In quarter when its Planungsraum overlaps a Milieuschutzgebiet.
    `milieuschutz-areas` is the official Milieuschutzgebiet polygons (Geoportal Berlin,
    § 172 Abs. 1 Nr. 2 BauGB) used by the Demographics map and that In quarter status.
    """
    from immo_scanner.reference import (
        berliner_register,
        crime,
        demographics,
        elections,
        grundschulen,
        gymnasium_stats,
        lor,
        milieuschutz,
        milieuschutz_gebiete,
        origins,
        ortsteile,
        schools,
        sekundarschule_stats,
        stations,
    )

    steps = {
        ReferenceDataset.stations: lambda: stations.load_stations(),
        ReferenceDataset.lor: lambda: lor.load_lor_geometries(file),
        ReferenceDataset.schools: lambda: schools.load_schools(),
        ReferenceDataset.gymnasium_stats: lambda: gymnasium_stats.load_gymnasium_stats(),
        ReferenceDataset.sekundarschule_stats: lambda: (
            sekundarschule_stats.load_sekundarschule_stats()
        ),
        ReferenceDataset.grundschulen: lambda: grundschulen.load_grundschulen(file),
        ReferenceDataset.ortsteile: lambda: ortsteile.load_ortsteile(file),
        ReferenceDataset.population: lambda: lor.load_population(file),
        ReferenceDataset.elections: lambda: elections.load_elections(
            "bundestag", file, geometry_file
        ),
        ReferenceDataset.agh_elections: lambda: elections.load_elections(
            "abgeordnetenhaus", file, geometry_file
        ),
        ReferenceDataset.mss: lambda: demographics.load_mss(),
        ReferenceDataset.migration: lambda: demographics.load_migration(file),
        ReferenceDataset.origins: lambda: origins.load_origins(file),
        ReferenceDataset.citizenship: lambda: demographics.load_citizenship(file),
        ReferenceDataset.religion: lambda: demographics.load_religion(file),
        ReferenceDataset.religion_bezirk: lambda: demographics.load_religion_bezirk(file),
        ReferenceDataset.income: lambda: demographics.load_income(file),
        ReferenceDataset.education: lambda: demographics.load_education(file),
        ReferenceDataset.crime: lambda: crime.load_crime(file),
        ReferenceDataset.milieuschutz: lambda: milieuschutz.load_milieuschutz(file),
        ReferenceDataset.milieuschutz_areas: lambda: (
            milieuschutz_gebiete.load_milieuschutz_gebiete(file)
        ),
        ReferenceDataset.berliner_register: lambda: (
            berliner_register.load_berliner_register(full)
        ),
    }
    if dataset == ReferenceDataset.all:
        selected = [
            name for name in steps if name not in FILE_ONLY_DATASETS | EXPLICIT_DATASETS
        ]
    else:
        selected = [dataset]
    if dataset == ReferenceDataset.all and (file or geometry_file):
        raise typer.BadParameter("--file/--geometry-file require a specific dataset")
    failures = []
    for name in selected:
        log.info("Loading %s", name.value)
        try:
            count = steps[name]()
        except Exception:
            log.exception("Loading %s failed", name.value)
            failures.append(name.value)
            continue
        log.info("%s: %d rows", name.value, count)
    if failures:
        console.print(f"[red]Failed: {', '.join(failures)}[/red]")
        raise typer.Exit(code=1)


@app.command("geocode-incidents")
def geocode_incidents(
    limit: Annotated[int | None, typer.Option(help="Max incidents to geocode")] = None,
    retry: Annotated[
        bool, typer.Option(help="Also retry incidents that found no point before")
    ] = False,
) -> None:
    """Place Berliner Register incidents on the map via Nominatim.

    Newest incidents first; safe to stop and resume because lookups are cached.
    """
    from immo_scanner.db.session import session_scope
    from immo_scanner.enrich.incidents import geocode_incidents as run

    with session_scope() as session:
        log.info("Placed %d incidents", run(session, limit, retry))


@app.command("download-commute-data")
def download_commute_data() -> None:
    """Download the Berlin OSM extract and VBB GTFS feed used for commute times."""
    from immo_scanner.enrich.commute import download_inputs

    paths = download_inputs(force=True)
    for path in paths:
        console.print(f"Downloaded {path}")


@app.command()
def enrich(
    skip_geocode: bool = False,
    skip_commute: bool = False,
    geocode_limit: Annotated[int | None, typer.Option(help="Max listings to geocode")] = None,
    reset_bad_geocodes: Annotated[
        bool,
        typer.Option(
            help=(
                "Clear obsolete postcode:* geocodes that pinned listings to Berlin centre, "
                "then re-geocode them"
            )
        ),
    ] = False,
) -> None:
    """Geocode listings, compute commute times and refresh the listing_features view."""
    from immo_scanner.db.session import session_scope
    from immo_scanner.enrich import features, geocode

    with session_scope() as session:
        features.ensure_places(session)
    if reset_bad_geocodes:
        with session_scope() as session:
            log.info(
                "Reset %d listings with bad postcode geocodes",
                geocode.reset_bad_postcode_geocodes(session),
            )
    if not skip_geocode:
        _geocode(geocode_limit)
    if not skip_commute:
        _commute()
    _refresh_and_report()


@app.command()
def details(
    source: Annotated[Source, typer.Argument()] = Source.all,
    limit: Annotated[int, typer.Option(help="Max detail pages per source")] = 200,
    refresh_days: Annotated[
        int | None, typer.Option(help="Also re-fetch details older than this many days")
    ] = None,
) -> None:
    """Fetch detail pages: year built, floor, photos, and the rest of each exposé."""
    from immo_scanner.crawlers import CRAWLERS
    from immo_scanner.db.repository import apply_rented_filter
    from immo_scanner.db.session import session_scope
    from immo_scanner.details.runner import fetch_details
    from immo_scanner.enrich import features

    settings = get_settings()
    names = list(CRAWLERS) if source == Source.all else [source.value]
    failures = []
    for name in names:
        try:
            stats = fetch_details(settings, name, limit, refresh_days)
            log.info("%s details: %s", name, stats)
        except Exception:
            log.exception("Fetching %s details failed", name)
            failures.append(name)
    with session_scope() as session:
        changed = apply_rented_filter(session, settings.exclude_rented)
        log.info("Rented filter: %d listings changed", changed)
    if "engelvoelkers" in names:
        _commute()
    with session_scope() as session:
        features.refresh_features(session)
    log.info("listing_features refreshed")
    if failures:
        raise typer.Exit(code=1)


@app.command()
def prune(
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Skip the confirmation")] = False,
) -> None:
    """Delete stored listings that don't match the search criteria (IMMO_MIN_ROOMS etc.)."""
    from immo_scanner.crawlers.criteria import Criteria
    from immo_scanner.db.repository import apply_rented_filter, prune_listings
    from immo_scanner.db.session import session_scope
    from immo_scanner.enrich import features

    criteria = Criteria.from_settings(get_settings())
    console.print(f"Search criteria: {criteria}")
    if not yes:
        typer.confirm("Delete all stored listings that don't match?", abort=True)
    with session_scope() as session:
        deleted = prune_listings(session, criteria.matches)
        rented = apply_rented_filter(session, criteria.exclude_rented)
        features.refresh_features(session)
    console.print(f"Deleted: {deleted or 'nothing'}; rented filter changed {rented} listings")


@app.command("metabase-setup")
def metabase_setup() -> None:
    """Connect Metabase to the database and create a starter dashboard."""
    from immo_scanner.explore.metabase import setup_metabase

    url = setup_metabase(get_settings())
    console.print(f"[green]Dashboard ready:[/green] {url}")


@app.command()
def stats() -> None:
    """Print a quick overview of the database."""
    from sqlalchemy import text

    from immo_scanner.db.session import get_engine
    from immo_scanner.explore.gaps import load_location_coverage, location_state_counts
    from immo_scanner.explore.listing_copy import source_label

    query = text(
        """
        SELECT source, status, count(*) AS n,
               round(avg(price_per_m2)::numeric, 0) AS avg_eur_m2,
               round(percentile_cont(0.5) WITHIN GROUP (ORDER BY price)::numeric, 0) AS median_price
        FROM listing_features GROUP BY source, status ORDER BY source, status
        """
    )
    with get_engine().connect() as conn:
        rows = conn.execute(query).all()
    table = Table("source", "status", "listings", "avg EUR/m2", "median price")
    for row in rows:
        table.add_row(*(str(v) if v is not None else "-" for v in row))
    console.print(table)

    locations = location_state_counts(load_location_coverage()).drop(columns="key")
    sources = [column for column in locations.columns if column not in ("state", "Total")]
    table = Table(
        "location (active)",
        *(source_label(name) for name in sources),
        "total",
    )
    for row in locations.itertuples(index=False):
        table.add_row(*(value if isinstance(value, str) else f"{int(value):,}" for value in row))
    console.print(table)


if __name__ == "__main__":
    app()
