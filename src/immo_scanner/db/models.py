from datetime import date, datetime
from typing import Any

from geoalchemy2 import Geometry
from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Listing(Base):
    __tablename__ = "listings"
    __table_args__ = (UniqueConstraint("source", "external_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(32), index=True)
    external_id: Mapped[str] = mapped_column(String(128))
    url: Mapped[str] = mapped_column(Text)
    title: Mapped[str | None] = mapped_column(Text)
    address: Mapped[str | None] = mapped_column(Text)
    postcode: Mapped[str | None] = mapped_column(String(5), index=True)
    district: Mapped[str | None] = mapped_column(String(128))
    geom = mapped_column(Geometry("POINT", srid=4326, spatial_index=True), nullable=True)
    # source | exact | street | postcode | district
    geo_precision: Mapped[str | None] = mapped_column(String(16))
    geocoded_query: Mapped[str | None] = mapped_column(Text)
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)
    is_new_build: Mapped[bool] = mapped_column(Boolean, default=False)
    # Yes | Closeby | No from the street list. In quarter when only the Planungsraum overlaps
    # a Milieuschutzgebiet. Empty when neither is known.
    milieuschutz: Mapped[str | None] = mapped_column(String(16))

    snapshots: Mapped[list["ListingSnapshot"]] = relationship(
        back_populates="listing", order_by="ListingSnapshot.scraped_at"
    )


class ListingSnapshot(Base):
    __tablename__ = "listing_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    listing_id: Mapped[int] = mapped_column(
        ForeignKey("listings.id", ondelete="CASCADE"), index=True
    )
    scraped_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    price: Mapped[float | None] = mapped_column(Float)
    living_area: Mapped[float | None] = mapped_column(Float)
    rooms: Mapped[float | None] = mapped_column(Float)
    floor: Mapped[int | None] = mapped_column(Integer)
    year_built: Mapped[int | None] = mapped_column(Integer)
    hausgeld: Mapped[float | None] = mapped_column(Float)
    raw: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)

    listing: Mapped[Listing] = relationship(back_populates="snapshots")


class GeocodeCache(Base):
    __tablename__ = "geocode_cache"

    query: Mapped[str] = mapped_column(Text, primary_key=True)
    lat: Mapped[float | None] = mapped_column(Float)
    lon: Mapped[float | None] = mapped_column(Float)
    precision: Mapped[str | None] = mapped_column(String(16))
    raw: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Place(Base):
    """Named points of interest, e.g. 'workplace'."""

    __tablename__ = "places"

    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    address: Mapped[str | None] = mapped_column(Text)
    geom = mapped_column(Geometry("POINT", srid=4326), nullable=False)


class School(Base):
    """Gymnasium or Montessori kindergarten from OpenStreetMap."""

    __tablename__ = "schools"
    __table_args__ = (UniqueConstraint("osm_type", "osm_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    osm_type: Mapped[str] = mapped_column(String(16))
    osm_id: Mapped[int] = mapped_column(BigInteger)
    name: Mapped[str] = mapped_column(Text)
    # gymnasium | montessori_kindergarten
    kind: Mapped[str] = mapped_column(String(32), index=True)
    geom = mapped_column(Geometry("POINT", srid=4326, spatial_index=True), nullable=False)


class GymnasiumStatistic(Base):
    """One row per Berlin Gymnasium, from the tables on gymnasium-berlin.net.

    `migration_background_pct` is the NdH share (nicht deutscher Herkunftssprache)
    published on that site's Migrationshintergrund page. `type_level` is the
    Schultypisierungsstufe.
    """

    __tablename__ = "gymnasium_statistics"

    slug: Mapped[str] = mapped_column(String(128), primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    abi_average: Mapped[float | None] = mapped_column(Float)
    abi_year: Mapped[str | None] = mapped_column(String(16))
    abi_pass_rate_pct: Mapped[float | None] = mapped_column(Float)
    abi_pass_rate_year: Mapped[str | None] = mapped_column(String(16))
    admission_places: Mapped[int | None] = mapped_column(Integer)
    first_choices: Mapped[int | None] = mapped_column(Integer)
    first_choice_pct: Mapped[float | None] = mapped_column(Float)
    admissions_year: Mapped[str | None] = mapped_column(String(16))
    students: Mapped[int | None] = mapped_column(Integer)
    students_year: Mapped[str | None] = mapped_column(String(16))
    teachers: Mapped[int | None] = mapped_column(Integer)
    teachers_year: Mapped[str | None] = mapped_column(String(16))
    migration_background_pct: Mapped[float | None] = mapped_column(Float)
    migration_year: Mapped[str | None] = mapped_column(String(16))
    type_level: Mapped[int | None] = mapped_column(Integer)
    # Year 5 is a grundständiger Zug. Year 7 is a Regelplatz (the Anmeldezahlen
    # table, which excludes grundständige places).
    starts_year_5: Mapped[bool] = mapped_column(Boolean, default=False)
    starts_year_7: Mapped[bool] = mapped_column(Boolean, default=False)
    schnelllerner: Mapped[bool] = mapped_column(Boolean, default=False)
    europa_schule: Mapped[bool] = mapped_column(Boolean, default=False)
    profile_language: Mapped[bool] = mapped_column(Boolean, default=False)
    profile_music_art: Mapped[bool] = mapped_column(Boolean, default=False)
    profile_mint: Mapped[bool] = mapped_column(Boolean, default=False)
    profile_sport: Mapped[bool] = mapped_column(Boolean, default=False)


class SekundarschuleStatistic(Base):
    """One row per Berlin Sekundarschule, from the tables on sekundarschulen-berlin.de.

    `migration_background_pct` is the latest NdH share (nicht deutscher
    Herkunftssprache). `type_level` is the Schultypisierungsstufe. `bsn` is the
    Berlin school number used to place the school on the official school map.
    """

    __tablename__ = "sekundarschule_statistics"

    slug: Mapped[str] = mapped_column(String(128), primary_key=True)
    bsn: Mapped[str | None] = mapped_column(String(8), index=True)
    name: Mapped[str] = mapped_column(Text)
    abi_average: Mapped[float | None] = mapped_column(Float)
    abi_year: Mapped[str | None] = mapped_column(String(16))
    abi_pass_rate_pct: Mapped[float | None] = mapped_column(Float)
    abi_pass_rate_year: Mapped[str | None] = mapped_column(String(16))
    abi_pass_rate_prev_pct: Mapped[float | None] = mapped_column(Float)
    admission_places: Mapped[int | None] = mapped_column(Integer)
    first_choices: Mapped[int | None] = mapped_column(Integer)
    first_choice_pct: Mapped[float | None] = mapped_column(Float)
    admissions_year: Mapped[str | None] = mapped_column(String(16))
    migration_background_pct: Mapped[float | None] = mapped_column(Float)
    migration_background_prev_pct: Mapped[float | None] = mapped_column(Float)
    migration_year: Mapped[str | None] = mapped_column(String(16))
    type_level: Mapped[int | None] = mapped_column(Integer)


class Schule(Base):
    """One Berlin school site from the Geoportal WFS `schulen`.

    Distinct from `schools`, which holds Gymnasiums and Montessori kindergartens
    from OpenStreetMap. The Sekundarschulen map joins statistics on `bsn`.
    """

    __tablename__ = "schulen"

    bsn: Mapped[str] = mapped_column(String(8), primary_key=True)
    name: Mapped[str | None] = mapped_column(Text)
    schulart: Mapped[str | None] = mapped_column(Text)
    bezirk: Mapped[str | None] = mapped_column(Text)
    ortsteil: Mapped[str | None] = mapped_column(Text)
    geom = mapped_column(Geometry("POINT", srid=4326, spatial_index=True), nullable=False)


class Grundschule(Base):
    """Berlin Grundschule or Gemeinschaftsschule, from the Geoportal school sites.

    `ganztag` is `gebunden` (all-day) or `offen` (optional afternoon care).
    `partner_language` is the Staatliche Europa-Schule language beside German.
    """

    __tablename__ = "grundschulen"

    bsn: Mapped[str] = mapped_column(String(8), primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    # Grundschule | Gemeinschaftsschule
    schulart: Mapped[str] = mapped_column(String(64))
    traeger: Mapped[str | None] = mapped_column(String(32))
    address: Mapped[str | None] = mapped_column(Text)
    bezirk: Mapped[str | None] = mapped_column(Text)
    ortsteil: Mapped[str | None] = mapped_column(Text)
    school_year: Mapped[str | None] = mapped_column(String(16))
    ganztag: Mapped[str | None] = mapped_column(String(16))
    europa_schule: Mapped[bool] = mapped_column(Boolean, default=False)
    partner_language: Mapped[str | None] = mapped_column(Text)
    first_foreign_language: Mapped[str | None] = mapped_column(Text)
    bilingual: Mapped[bool] = mapped_column(Boolean, default=False)
    students: Mapped[int | None] = mapped_column(Integer)
    teachers: Mapped[int | None] = mapped_column(Integer)
    geom = mapped_column(Geometry("POINT", srid=4326, spatial_index=True), nullable=False)


class Einschulbereich(Base):
    """Public primary-school catchment for the current school year.

    `bsn` is the school whose site falls in the polygon. The WFS code `esb` does
    not match the Berlin school number.
    """

    __tablename__ = "einschulbereiche"

    esb: Mapped[str] = mapped_column(String(8), primary_key=True)
    bsn: Mapped[str | None] = mapped_column(String(8), index=True)
    bezirk: Mapped[str | None] = mapped_column(Text)
    school_year: Mapped[str | None] = mapped_column(String(16))
    geom = mapped_column(Geometry("MULTIPOLYGON", srid=4326, spatial_index=True), nullable=False)


class BuildingAgeClass(Base):
    """Typical U-values and retrofit obligations by German construction period."""

    __tablename__ = "building_age_classes"

    id: Mapped[int] = mapped_column(primary_key=True)
    class_code: Mapped[str] = mapped_column(String(1), nullable=False)
    year_from: Mapped[int | None] = mapped_column(Integer)
    year_to: Mapped[int | None] = mapped_column(Integer)
    exterior_wall_u_value: Mapped[str] = mapped_column(Text, nullable=False)
    window_u_value: Mapped[str] = mapped_column(Text, nullable=False)
    roof_ceiling_u_value: Mapped[str] = mapped_column(Text, nullable=False)
    typical_heating_demand: Mapped[str] = mapped_column(Text, nullable=False)
    typical_heating_demand_kwh: Mapped[float | None] = mapped_column(Float)
    climate_risk_score: Mapped[int] = mapped_column(Integer, nullable=False)
    risk_level: Mapped[str] = mapped_column(Text, nullable=False)
    legal_obligations_on_purchase: Mapped[str] = mapped_column(Text, nullable=False)


class Station(Base):
    __tablename__ = "stations"

    id: Mapped[int] = mapped_column(primary_key=True)
    osm_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    name: Mapped[str] = mapped_column(Text)
    is_sbahn: Mapped[bool] = mapped_column(Boolean, default=False)
    is_ubahn: Mapped[bool] = mapped_column(Boolean, default=False)
    lines: Mapped[str | None] = mapped_column(Text)
    geom = mapped_column(Geometry("POINT", srid=4326, spatial_index=True), nullable=False)


class Ortsteil(Base):
    """Official locality from the Berlin cadastre (ALKIS). 97 rows, e.g. Kreuzberg."""

    __tablename__ = "ortsteile"

    id: Mapped[str] = mapped_column(String(4), primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    geom = mapped_column(Geometry("MULTIPOLYGON", srid=4326, spatial_index=True), nullable=False)


class MilieuschutzStreet(Base):
    """One street segment of a Berlin Milieuschutzgebiet (Berliner Mieterverein list)."""

    __tablename__ = "milieuschutz_streets"

    id: Mapped[int] = mapped_column(primary_key=True)
    street: Mapped[str] = mapped_column(Text)
    street_key: Mapped[str] = mapped_column(String(200), index=True)
    bezirk: Mapped[str] = mapped_column(Text)
    gebiet: Mapped[str | None] = mapped_column(Text)
    covers_all: Mapped[bool] = mapped_column(Boolean, default=False)
    # [{"start", "end", "parity": all|even|odd}]
    ranges: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    raw_numbers: Mapped[str | None] = mapped_column(Text)


class MilieuschutzGebiet(Base):
    """One Berlin Milieuschutzgebiet (§ 172 Abs. 1 Nr. 2 BauGB)."""

    __tablename__ = "milieuschutz_gebiete"

    schluessel: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    bezirk: Mapped[str | None] = mapped_column(Text)
    in_force: Mapped[date | None] = mapped_column(Date)
    area_ha: Mapped[float | None] = mapped_column(Float)
    geom = mapped_column(Geometry("MULTIPOLYGON", srid=4326, spatial_index=True), nullable=False)


class LorPlanungsraum(Base):
    __tablename__ = "lor_planungsraeume"

    plr_id: Mapped[str] = mapped_column(String(8), primary_key=True)
    plr_name: Mapped[str] = mapped_column(Text)
    bzr_name: Mapped[str | None] = mapped_column(Text)
    pgr_name: Mapped[str | None] = mapped_column(Text)
    bezirk: Mapped[str | None] = mapped_column(Text)
    population: Mapped[int | None] = mapped_column(Integer)
    share_under_18_pct: Mapped[float | None] = mapped_column(Float)
    share_18_to_64_pct: Mapped[float | None] = mapped_column(Float)
    share_65_plus_pct: Mapped[float | None] = mapped_column(Float)
    population_date: Mapped[str | None] = mapped_column(String(16))
    demographics: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    geom = mapped_column(Geometry("MULTIPOLYGON", srid=4326, spatial_index=True), nullable=False)


class AreaIndicator(Base):
    """One demographic value for a Planungsraum (`plr`, 8-digit id) or Bezirk (name)."""

    __tablename__ = "area_indicators"

    level: Mapped[str] = mapped_column(String(8), primary_key=True)
    area_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    topic: Mapped[str] = mapped_column(String(32), primary_key=True)
    indicator: Mapped[str] = mapped_column(String(64), primary_key=True)
    label: Mapped[str] = mapped_column(Text)
    value: Mapped[float | None] = mapped_column(Float)
    # pct (share of residents), pp (percentage points), eur
    unit: Mapped[str] = mapped_column(String(8))
    ref_date: Mapped[str | None] = mapped_column(String(32))
    source: Mapped[str] = mapped_column(String(64))


class CrimeStat(Base):
    """Cases and frequency from the Berlin crime atlas (Polizei Berlin, PKS).

    One row per area, year and offence. `level` is `bzr` (6-digit Bezirksregion key),
    `bezirk` (district name) or `city` (`berlin`). `rate` is cases per 100,000 residents.
    """

    __tablename__ = "crime_stats"

    level: Mapped[str] = mapped_column(String(8), primary_key=True)
    area_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    year: Mapped[int] = mapped_column(Integer, primary_key=True)
    offense: Mapped[str] = mapped_column(String(32), primary_key=True)
    cases: Mapped[int | None] = mapped_column(Integer)
    rate: Mapped[float | None] = mapped_column(Float)
    name: Mapped[str] = mapped_column(Text)
    label: Mapped[str] = mapped_column(Text)


class RegisterIncident(Base):
    """A discriminatory or far-right incident from the Berliner Register chronicle.

    `geom` is the first location in the text that geocodes inside the reported Bezirk;
    `geo_precision` is `street`, `station`, `place` or `bezirk` (no point found).
    """

    __tablename__ = "register_incidents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    incident_date: Mapped[date] = mapped_column(Date, index=True)
    title: Mapped[str] = mapped_column(Text)
    bezirk: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str | None] = mapped_column(Text)
    geom = mapped_column(Geometry("POINT", srid=4326, spatial_index=True))
    geo_precision: Mapped[str | None] = mapped_column(String(16))
    geocoded_query: Mapped[str | None] = mapped_column(Text)
    plr_id: Mapped[str | None] = mapped_column(String(8))
    scraped_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class PlrOriginStat(Base):
    """Residents by origin per Planungsraum and reference date (Statistischer Bericht A I 16)."""

    __tablename__ = "plr_origin_stats"

    plr_id: Mapped[str] = mapped_column(String(8), primary_key=True)
    ref_date: Mapped[date] = mapped_column(Date, primary_key=True)
    origin_key: Mapped[str] = mapped_column(String(32), primary_key=True)
    # total | group | country | diversity
    kind: Mapped[str] = mapped_column(String(16))
    count: Mapped[int | None] = mapped_column(Integer)
    # Percent of all residents; for diversity the Gini-Simpson index x 100.
    share: Mapped[float | None] = mapped_column(Float)
    label: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(32))


class Wahlbezirk(Base):
    """Voting district results. One row per district and election `scope`."""

    __tablename__ = "wahlbezirke"

    scope: Mapped[str] = mapped_column(String(16), primary_key=True)
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    election: Mapped[str] = mapped_column(Text)
    eligible_voters: Mapped[int | None] = mapped_column(Integer)
    voters: Mapped[int | None] = mapped_column(Integer)
    valid_votes: Mapped[int | None] = mapped_column(Integer)
    turnout_pct: Mapped[float | None] = mapped_column(Float)
    party_shares: Mapped[dict[str, float]] = mapped_column(JSONB, default=dict)
    geom = mapped_column(Geometry("MULTIPOLYGON", srid=4326, spatial_index=True), nullable=False)


class PartyEducation(Base):
    """Share of a party's voters, or of non-voters, by school-leaving qualification.

    Percentages are the published approximate figures ("ca."). Each row sums to 100.
    `cdu` is the CDU/CSU column; Berlin elections report CDU. `volt` matches the
    loader key `voltdeutschland`. `nichtwaehler` is weighted by local turnout, not
    by a party share.
    """

    __tablename__ = "party_education"

    party_key: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    # Hauptschule (einfache Bildung)
    hauptschule_pct: Mapped[float] = mapped_column(Float)
    # Mittlere Reife (mittlere Bildung)
    mittlere_reife_pct: Mapped[float] = mapped_column(Float)
    # Abitur / Fachabi (höhere Schulbildung)
    abitur_pct: Mapped[float] = mapped_column(Float)
    # Hochschule / Uni (akademische Bildung)
    hochschule_pct: Mapped[float] = mapped_column(Float)


class ListingDetail(Base):
    """Attributes from a listing's detail page (structured fields plus description text)."""

    __tablename__ = "listing_details"

    listing_id: Mapped[int] = mapped_column(
        ForeignKey("listings.id", ondelete="CASCADE"), primary_key=True
    )
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    year_built: Mapped[int | None] = mapped_column(Integer)
    last_renovation_year: Mapped[int | None] = mapped_column(Integer)
    floor: Mapped[int | None] = mapped_column(Integer)
    total_floors: Mapped[int | None] = mapped_column(Integer)
    heating_type: Mapped[str | None] = mapped_column(Text)
    energy_source: Mapped[str | None] = mapped_column(Text)
    has_elevator: Mapped[bool | None] = mapped_column(Boolean)
    has_parking: Mapped[bool | None] = mapped_column(Boolean)
    has_cellar: Mapped[bool | None] = mapped_column(Boolean)
    has_balcony: Mapped[bool | None] = mapped_column(Boolean)
    balcony_count: Mapped[int | None] = mapped_column(Integer)
    description: Mapped[str | None] = mapped_column(Text)
    raw: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


class Immoscout24Expose(Base):
    """Full copy of an ImmoScout24 exposé; media files are stored under `media_dir`."""

    __tablename__ = "immoscout24_exposes"

    listing_id: Mapped[int] = mapped_column(
        ForeignKey("listings.id", ondelete="CASCADE"), primary_key=True
    )
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    scout_id: Mapped[str | None] = mapped_column(String(32), index=True)
    object_id: Mapped[str | None] = mapped_column(Text)
    url: Mapped[str] = mapped_column(Text)
    title: Mapped[str | None] = mapped_column(Text)
    address: Mapped[str | None] = mapped_column(Text)
    property_type: Mapped[str | None] = mapped_column(Text)
    is_rented: Mapped[bool | None] = mapped_column(Boolean)
    living_area: Mapped[float | None] = mapped_column(Float)
    usable_area: Mapped[float | None] = mapped_column(Float)
    available_from: Mapped[str | None] = mapped_column(Text)
    rooms: Mapped[float | None] = mapped_column(Float)
    bedrooms: Mapped[float | None] = mapped_column(Float)
    bathrooms: Mapped[float | None] = mapped_column(Float)
    price: Mapped[float | None] = mapped_column(Float)
    price_per_m2: Mapped[float | None] = mapped_column(Float)
    buyer_commission: Mapped[str | None] = mapped_column(Text)
    year_built: Mapped[int | None] = mapped_column(Integer)
    heating_type: Mapped[str | None] = mapped_column(Text)
    energy_source: Mapped[str | None] = mapped_column(Text)
    energy_certificate: Mapped[str | None] = mapped_column(Text)
    energy_certificate_type: Mapped[str | None] = mapped_column(Text)
    final_energy_kwh_m2a: Mapped[float | None] = mapped_column(Float)
    final_energy_type: Mapped[str | None] = mapped_column(String(32))
    energy_efficiency_class: Mapped[str | None] = mapped_column(String(8))
    energy_certificate_year_built: Mapped[int | None] = mapped_column(Integer)
    property_condition: Mapped[str | None] = mapped_column(Text)
    interior_quality: Mapped[str | None] = mapped_column(Text)
    has_balcony_terrace: Mapped[bool | None] = mapped_column(Boolean)
    has_terrace: Mapped[bool | None] = mapped_column(Boolean)
    has_cellar: Mapped[bool | None] = mapped_column(Boolean)
    has_parking: Mapped[bool | None] = mapped_column(Boolean)
    parking_type: Mapped[str | None] = mapped_column(Text)
    parking_spaces: Mapped[int | None] = mapped_column(Integer)
    parking_price: Mapped[float | None] = mapped_column(Float)
    has_fitted_kitchen: Mapped[bool | None] = mapped_column(Boolean)
    has_guest_toilet: Mapped[bool | None] = mapped_column(Boolean)
    internet_speed: Mapped[str | None] = mapped_column(Text)
    internet_mbit: Mapped[int | None] = mapped_column(Integer)
    description: Mapped[str | None] = mapped_column(Text)
    equipment_text: Mapped[str | None] = mapped_column(Text)
    other_text: Mapped[str | None] = mapped_column(Text)
    location_text: Mapped[str | None] = mapped_column(Text)
    photo_count: Mapped[int | None] = mapped_column(Integer)
    floor_plan_count: Mapped[int | None] = mapped_column(Integer)
    media_dir: Mapped[str | None] = mapped_column(Text)
    # [{"caption", "type": PICTURE|FLOORPLAN|DOCUMENT, "url", "path"}]
    media: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    facts: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    # Feature tags such as "lift", "guestToilet", "builtInKitchen".
    features: Mapped[list[str]] = mapped_column(JSONB, default=list)


class NeubaukompassUnit(Base):
    """A single flat ("Wohnbeispiel") of a Neubaukompass project, with its media copies."""

    __tablename__ = "neubaukompass_units"

    listing_id: Mapped[int] = mapped_column(
        ForeignKey("listings.id", ondelete="CASCADE"), primary_key=True
    )
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    unit_id: Mapped[str | None] = mapped_column(String(32), index=True)
    object_number: Mapped[str | None] = mapped_column(Text)
    project_id: Mapped[str | None] = mapped_column(String(32), index=True)
    project_name: Mapped[str | None] = mapped_column(Text)
    project_url: Mapped[str | None] = mapped_column(Text)
    url: Mapped[str] = mapped_column(Text)
    title: Mapped[str | None] = mapped_column(Text)
    address: Mapped[str | None] = mapped_column(Text)
    unit_type: Mapped[str | None] = mapped_column(Text)
    price: Mapped[float | None] = mapped_column(Float)
    price_per_m2: Mapped[float | None] = mapped_column(Float)
    floor: Mapped[int | None] = mapped_column(Integer)
    total_floors: Mapped[int | None] = mapped_column(Integer)
    rooms: Mapped[float | None] = mapped_column(Float)
    bedrooms: Mapped[float | None] = mapped_column(Float)
    bathrooms: Mapped[float | None] = mapped_column(Float)
    living_area: Mapped[float | None] = mapped_column(Float)
    year_built: Mapped[int | None] = mapped_column(Integer)
    last_renovation_year: Mapped[int | None] = mapped_column(Integer)
    available_from: Mapped[str | None] = mapped_column(Text)
    has_commission: Mapped[bool | None] = mapped_column(Boolean)
    buyer_commission: Mapped[str | None] = mapped_column(Text)
    seller_commission: Mapped[str | None] = mapped_column(Text)
    energy_certificate_type: Mapped[str | None] = mapped_column(Text)
    energy_source: Mapped[str | None] = mapped_column(Text)
    energy_certificate_date: Mapped[str | None] = mapped_column(Text)
    energy_efficiency_class: Mapped[str | None] = mapped_column(String(8))
    final_energy_kwh_m2a: Mapped[float | None] = mapped_column(Float)
    has_balcony_terrace: Mapped[bool | None] = mapped_column(Boolean)
    features: Mapped[list[str]] = mapped_column(JSONB, default=list)
    location_text: Mapped[str | None] = mapped_column(Text)
    equipment_text: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    floor_plan_url: Mapped[str | None] = mapped_column(Text)
    energy_certificate_url: Mapped[str | None] = mapped_column(Text)
    website_url: Mapped[str | None] = mapped_column(Text)
    virtual_tour_url: Mapped[str | None] = mapped_column(Text)
    provider_name: Mapped[str | None] = mapped_column(Text)
    provider_address: Mapped[str | None] = mapped_column(Text)
    contact_name: Mapped[str | None] = mapped_column(Text)
    contact_phone: Mapped[str | None] = mapped_column(Text)
    contact_email: Mapped[str | None] = mapped_column(Text)
    photo_count: Mapped[int | None] = mapped_column(Integer)
    floor_plan_count: Mapped[int | None] = mapped_column(Integer)
    media_dir: Mapped[str | None] = mapped_column(Text)
    # [{"caption", "type": PICTURE|FLOORPLAN|DOCUMENT, "url", "path"}]
    media: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    facts: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


class KleinanzeigenAd(Base):
    """A Kleinanzeigen exposé: the fact list, description and local photo copies."""

    __tablename__ = "kleinanzeigen_ads"

    listing_id: Mapped[int] = mapped_column(
        ForeignKey("listings.id", ondelete="CASCADE"), primary_key=True
    )
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ad_id: Mapped[str | None] = mapped_column(String(32), index=True)
    url: Mapped[str] = mapped_column(Text)
    title: Mapped[str | None] = mapped_column(Text)
    address: Mapped[str | None] = mapped_column(Text)
    posted_on: Mapped[date | None] = mapped_column(Date)
    living_area: Mapped[float | None] = mapped_column(Float)
    rooms: Mapped[float | None] = mapped_column(Float)
    bedrooms: Mapped[float | None] = mapped_column(Float)
    bathrooms: Mapped[float | None] = mapped_column(Float)
    floor: Mapped[int | None] = mapped_column(Integer)
    apartment_type: Mapped[str | None] = mapped_column(Text)
    year_built: Mapped[int | None] = mapped_column(Integer)
    provision: Mapped[str | None] = mapped_column(Text)
    has_balcony: Mapped[bool | None] = mapped_column(Boolean)
    has_terrace: Mapped[bool | None] = mapped_column(Boolean)
    has_fitted_kitchen: Mapped[bool | None] = mapped_column(Boolean)
    has_elevator: Mapped[bool | None] = mapped_column(Boolean)
    description: Mapped[str | None] = mapped_column(Text)
    photo_count: Mapped[int | None] = mapped_column(Integer)
    media_dir: Mapped[str | None] = mapped_column(Text)
    # [{"caption", "type": "PICTURE", "url", "path"}]
    media: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    facts: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    features: Mapped[list[str]] = mapped_column(JSONB, default=list)


class ImmoweltExpose(Base):
    """Structured fields from an Immowelt exposé's embedded classified object."""

    __tablename__ = "immowelt_exposes"

    listing_id: Mapped[int] = mapped_column(
        ForeignKey("listings.id", ondelete="CASCADE"), primary_key=True
    )
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    online_id: Mapped[str | None] = mapped_column(String(64), index=True)
    reference_number: Mapped[str | None] = mapped_column(Text)
    url: Mapped[str] = mapped_column(Text)
    title: Mapped[str | None] = mapped_column(Text)
    postcode: Mapped[str | None] = mapped_column(String(5))
    district: Mapped[str | None] = mapped_column(Text)
    address_published: Mapped[bool | None] = mapped_column(Boolean)
    price: Mapped[float | None] = mapped_column(Float)
    living_area: Mapped[float | None] = mapped_column(Float)
    rooms: Mapped[float | None] = mapped_column(Float)
    bedrooms: Mapped[float | None] = mapped_column(Float)
    bathrooms: Mapped[float | None] = mapped_column(Float)
    floor: Mapped[int | None] = mapped_column(Integer)
    total_floors: Mapped[int | None] = mapped_column(Integer)
    year_built: Mapped[int | None] = mapped_column(Integer)
    last_renovation_year: Mapped[int | None] = mapped_column(Integer)
    property_condition: Mapped[str | None] = mapped_column(Text)
    heating_type: Mapped[str | None] = mapped_column(Text)
    energy_source: Mapped[str | None] = mapped_column(Text)
    energy_certificate_type: Mapped[str | None] = mapped_column(Text)
    energy_efficiency_class: Mapped[str | None] = mapped_column(String(8))
    final_energy_kwh_m2a: Mapped[float | None] = mapped_column(Float)
    is_rented: Mapped[bool | None] = mapped_column(Boolean)
    available_from: Mapped[str | None] = mapped_column(Text)
    hausgeld: Mapped[float | None] = mapped_column(Float)
    buyer_commission: Mapped[str | None] = mapped_column(Text)
    has_balcony: Mapped[bool | None] = mapped_column(Boolean)
    balcony_count: Mapped[int | None] = mapped_column(Integer)
    has_terrace: Mapped[bool | None] = mapped_column(Boolean)
    has_cellar: Mapped[bool | None] = mapped_column(Boolean)
    has_elevator: Mapped[bool | None] = mapped_column(Boolean)
    has_parking: Mapped[bool | None] = mapped_column(Boolean)
    has_fitted_kitchen: Mapped[bool | None] = mapped_column(Boolean)
    has_garden: Mapped[bool | None] = mapped_column(Boolean)
    has_monument_protection: Mapped[bool | None] = mapped_column(Boolean)
    flooring: Mapped[str | None] = mapped_column(Text)
    bathroom_features: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    location_text: Mapped[str | None] = mapped_column(Text)
    further_text: Mapped[str | None] = mapped_column(Text)
    facts: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


class BskExpose(Base):
    """A BSK Immobilien exposé: the Objektdetails table, text sections and local photos."""

    __tablename__ = "bsk_exposes"

    listing_id: Mapped[int] = mapped_column(
        ForeignKey("listings.id", ondelete="CASCADE"), primary_key=True
    )
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    object_id: Mapped[str | None] = mapped_column(String(32), index=True)
    url: Mapped[str] = mapped_column(Text)
    title: Mapped[str | None] = mapped_column(Text)
    bezirk: Mapped[str | None] = mapped_column(Text)
    ortsteil: Mapped[str | None] = mapped_column(Text)
    property_type: Mapped[str | None] = mapped_column(Text)
    price: Mapped[float | None] = mapped_column(Float)
    living_area: Mapped[float | None] = mapped_column(Float)
    usable_area: Mapped[float | None] = mapped_column(Float)
    rooms: Mapped[float | None] = mapped_column(Float)
    bedrooms: Mapped[float | None] = mapped_column(Float)
    bathrooms: Mapped[float | None] = mapped_column(Float)
    floor: Mapped[int | None] = mapped_column(Integer)
    total_floors: Mapped[int | None] = mapped_column(Integer)
    year_built: Mapped[int | None] = mapped_column(Integer)
    property_condition: Mapped[str | None] = mapped_column(Text)
    interior_quality: Mapped[str | None] = mapped_column(Text)
    heating_type: Mapped[str | None] = mapped_column(Text)
    energy_source: Mapped[str | None] = mapped_column(Text)
    energy_certificate_type: Mapped[str | None] = mapped_column(Text)
    final_energy_type: Mapped[str | None] = mapped_column(String(32))
    final_energy_kwh_m2a: Mapped[float | None] = mapped_column(Float)
    energy_efficiency_class: Mapped[str | None] = mapped_column(String(8))
    energy_certificate_valid_until: Mapped[str | None] = mapped_column(Text)
    is_rented: Mapped[bool | None] = mapped_column(Boolean)
    monthly_rent: Mapped[float | None] = mapped_column(Float)
    available_from: Mapped[str | None] = mapped_column(Text)
    hausgeld: Mapped[float | None] = mapped_column(Float)
    buyer_commission: Mapped[str | None] = mapped_column(Text)
    flooring: Mapped[str | None] = mapped_column(Text)
    bathroom_features: Mapped[str | None] = mapped_column(Text)
    balcony_count: Mapped[int | None] = mapped_column(Integer)
    terrace_count: Mapped[int | None] = mapped_column(Integer)
    parking_spaces: Mapped[int | None] = mapped_column(Integer)
    parking_type: Mapped[str | None] = mapped_column(Text)
    has_balcony: Mapped[bool | None] = mapped_column(Boolean)
    has_terrace: Mapped[bool | None] = mapped_column(Boolean)
    has_cellar: Mapped[bool | None] = mapped_column(Boolean)
    has_elevator: Mapped[bool | None] = mapped_column(Boolean)
    has_parking: Mapped[bool | None] = mapped_column(Boolean)
    has_fitted_kitchen: Mapped[bool | None] = mapped_column(Boolean)
    has_garden: Mapped[bool | None] = mapped_column(Boolean)
    has_guest_toilet: Mapped[bool | None] = mapped_column(Boolean)
    has_monument_protection: Mapped[bool | None] = mapped_column(Boolean)
    is_barrier_free: Mapped[bool | None] = mapped_column(Boolean)
    equipment_text: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    location_text: Mapped[str | None] = mapped_column(Text)
    photo_count: Mapped[int | None] = mapped_column(Integer)
    floor_plan_count: Mapped[int | None] = mapped_column(Integer)
    media_dir: Mapped[str | None] = mapped_column(Text)
    # [{"caption", "type": "PICTURE" | "FLOORPLAN", "url", "path"}]
    media: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    # Every Objektdetails row as shown, label -> value.
    facts: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    # Values of the "Ausstattung/Merkmale" row.
    features: Mapped[list[str]] = mapped_column(JSONB, default=list)


class EngelvoelkersExpose(Base):
    """An Engel & Völkers exposé: the listing record of the page, mapped, plus local photos."""

    __tablename__ = "engelvoelkers_exposes"

    listing_id: Mapped[int] = mapped_column(
        ForeignKey("listings.id", ondelete="CASCADE"), primary_key=True
    )
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    listing_uuid: Mapped[str | None] = mapped_column(String(36), index=True)
    display_id: Mapped[str | None] = mapped_column(String(64))
    external_id: Mapped[str | None] = mapped_column(String(64))
    # Development units belong to a project listing.
    parent_id: Mapped[str | None] = mapped_column(String(36))
    url: Mapped[str] = mapped_column(Text)
    status: Mapped[str | None] = mapped_column(String(32))
    title: Mapped[str | None] = mapped_column(Text)
    property_type: Mapped[str | None] = mapped_column(Text)
    bezirk: Mapped[str | None] = mapped_column(Text)
    ortsteil: Mapped[str | None] = mapped_column(Text)
    # Shifted on purpose by up to ~150 m (fuzzyMoveByMeter).
    lat: Mapped[float | None] = mapped_column(Float)
    lon: Mapped[float | None] = mapped_column(Float)
    price: Mapped[float | None] = mapped_column(Float)
    living_area: Mapped[float | None] = mapped_column(Float)
    rooms: Mapped[float | None] = mapped_column(Float)
    bedrooms: Mapped[float | None] = mapped_column(Float)
    bathrooms: Mapped[float | None] = mapped_column(Float)
    floor: Mapped[int | None] = mapped_column(Integer)
    total_floors: Mapped[int | None] = mapped_column(Integer)
    year_built: Mapped[int | None] = mapped_column(Integer)
    property_condition: Mapped[str | None] = mapped_column(Text)
    heating_type: Mapped[str | None] = mapped_column(Text)
    energy_source: Mapped[str | None] = mapped_column(Text)
    energy_certificate_type: Mapped[str | None] = mapped_column(Text)
    final_energy_type: Mapped[str | None] = mapped_column(String(32))
    final_energy_kwh_m2a: Mapped[float | None] = mapped_column(Float)
    energy_efficiency_class: Mapped[str | None] = mapped_column(String(8))
    is_rented: Mapped[bool | None] = mapped_column(Boolean)
    available_from: Mapped[str | None] = mapped_column(Text)
    hausgeld: Mapped[float | None] = mapped_column(Float)
    commission_percent: Mapped[float | None] = mapped_column(Float)
    buyer_commission: Mapped[str | None] = mapped_column(Text)
    flooring: Mapped[str | None] = mapped_column(Text)
    balcony_count: Mapped[int | None] = mapped_column(Integer)
    has_balcony: Mapped[bool | None] = mapped_column(Boolean)
    has_terrace: Mapped[bool | None] = mapped_column(Boolean)
    has_cellar: Mapped[bool | None] = mapped_column(Boolean)
    has_elevator: Mapped[bool | None] = mapped_column(Boolean)
    has_parking: Mapped[bool | None] = mapped_column(Boolean)
    has_fitted_kitchen: Mapped[bool | None] = mapped_column(Boolean)
    has_garden: Mapped[bool | None] = mapped_column(Boolean)
    has_guest_toilet: Mapped[bool | None] = mapped_column(Boolean)
    has_monument_protection: Mapped[bool | None] = mapped_column(Boolean)
    has_fireplace: Mapped[bool | None] = mapped_column(Boolean)
    description: Mapped[str | None] = mapped_column(Text)
    location_text: Mapped[str | None] = mapped_column(Text)
    commission_text: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    photo_count: Mapped[int | None] = mapped_column(Integer)
    floor_plan_count: Mapped[int | None] = mapped_column(Integer)
    media_dir: Mapped[str | None] = mapped_column(Text)
    # [{"caption", "type": "PICTURE" | "FLOORPLAN", "url", "path"}]
    media: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    # The page's listing record without images, consent and tracking data.
    raw: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


class ThinkimmoProperty(Base):
    """A ThinkImmo property: portal data plus ThinkImmo's valuation, location and cash flow.

    List crawls fill the shared fields; the exposé adds the rest (description, micro
    location, PLZ statistics, views). `raw` holds the merged API record. `listing_id`
    is the matching listing of an own crawler, or else the property's `thinkimmo`
    listing.
    """

    __tablename__ = "thinkimmo_properties"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    listing_id: Mapped[int | None] = mapped_column(
        ForeignKey("listings.id", ondelete="SET NULL"), index=True
    )
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)
    detail_fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    url: Mapped[str | None] = mapped_column(Text)
    primary_platform: Mapped[str | None] = mapped_column(String(64))
    platform_names: Mapped[str | None] = mapped_column(Text)
    title: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    realtor_company: Mapped[str | None] = mapped_column(Text)
    is_private_offer: Mapped[bool | None] = mapped_column(Boolean)
    is_active: Mapped[bool | None] = mapped_column(Boolean)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    running_time_days: Mapped[int | None] = mapped_column(Integer)

    buying_price: Mapped[float | None] = mapped_column(Float)
    price_per_m2: Mapped[float | None] = mapped_column(Float)
    living_area: Mapped[float | None] = mapped_column(Float)
    rooms: Mapped[float | None] = mapped_column(Float)
    commission_pct: Mapped[float | None] = mapped_column(Float)
    hausgeld: Mapped[float | None] = mapped_column(Float)
    price_reduced: Mapped[bool | None] = mapped_column(Boolean)
    price_increased: Mapped[bool | None] = mapped_column(Boolean)

    apartment_type: Mapped[str | None] = mapped_column(String(32))
    condition: Mapped[str | None] = mapped_column(String(48))
    interior_quality: Mapped[str | None] = mapped_column(String(32))
    heating_type: Mapped[str | None] = mapped_column(String(48))
    year_built: Mapped[int | None] = mapped_column(Integer)
    last_renovation_year: Mapped[int | None] = mapped_column(Integer)
    floor: Mapped[int | None] = mapped_column(Integer)
    total_floors: Mapped[int | None] = mapped_column(Integer)
    parking_spaces: Mapped[int | None] = mapped_column(Integer)
    has_elevator: Mapped[bool | None] = mapped_column(Boolean)
    has_cellar: Mapped[bool | None] = mapped_column(Boolean)
    has_balcony: Mapped[bool | None] = mapped_column(Boolean)
    has_garden: Mapped[bool | None] = mapped_column(Boolean)
    has_fitted_kitchen: Mapped[bool | None] = mapped_column(Boolean)
    has_energy_certificate: Mapped[bool | None] = mapped_column(Boolean)
    energy_efficiency_class: Mapped[str | None] = mapped_column(String(16))
    is_rented: Mapped[bool | None] = mapped_column(Boolean)
    is_leasehold: Mapped[bool | None] = mapped_column(Boolean)
    is_foreclosure: Mapped[bool | None] = mapped_column(Boolean)

    postcode: Mapped[str | None] = mapped_column(String(5), index=True)
    city: Mapped[str | None] = mapped_column(Text)
    borough: Mapped[str | None] = mapped_column(Text)
    suburb: Mapped[str | None] = mapped_column(Text)
    street: Mapped[str | None] = mapped_column(Text)
    house_number: Mapped[str | None] = mapped_column(Text)
    lat: Mapped[float | None] = mapped_column(Float)
    lon: Mapped[float | None] = mapped_column(Float)

    market_price_per_m2: Mapped[float | None] = mapped_column(Float)
    market_price_per_m2_min: Mapped[float | None] = mapped_column(Float)
    market_price_per_m2_max: Mapped[float | None] = mapped_column(Float)
    # Buying price as a percentage of ThinkImmo's market value estimate.
    price_to_market_pct: Mapped[float | None] = mapped_column(Float)
    estimated_rent: Mapped[float | None] = mapped_column(Float)
    estimated_rent_per_m2: Mapped[float | None] = mapped_column(Float)
    estimated_rent_per_m2_min: Mapped[float | None] = mapped_column(Float)
    estimated_rent_per_m2_max: Mapped[float | None] = mapped_column(Float)
    current_rent: Mapped[float | None] = mapped_column(Float)
    current_rent_per_m2: Mapped[float | None] = mapped_column(Float)
    gross_return: Mapped[float | None] = mapped_column(Float)
    gross_return_current: Mapped[float | None] = mapped_column(Float)
    # Depend on the financing assumptions in IMMO_THINKIMMO_FINANCE_QUERY.
    cash_flow: Mapped[float | None] = mapped_column(Float)
    own_capital_return: Mapped[float | None] = mapped_column(Float)
    location_score: Mapped[float | None] = mapped_column(Float)
    district_price_per_m2: Mapped[float | None] = mapped_column(Float)
    city_price_per_m2: Mapped[float | None] = mapped_column(Float)
    similar_price_per_m2: Mapped[float | None] = mapped_column(Float)

    views: Mapped[int | None] = mapped_column(Integer)
    clicks: Mapped[int | None] = mapped_column(Integer)
    favorites: Mapped[int | None] = mapped_column(Integer)
    image_count: Mapped[int | None] = mapped_column(Integer)
    floor_plan_count: Mapped[int | None] = mapped_column(Integer)

    # [{"name", "id", "url", "creationDate", "publishDate", "active"}]
    platforms: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    # [{"id", "originalUrl", "title", "floorPlan"}]
    images: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    # [{"buyingPrice", "platformName", "creationDate"}]
    buying_price_history: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    change_log: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    address: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    original_address: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    # Address as published by each portal, keyed by portal name.
    portal_addresses: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    # Macro data (population, unemployment, scores) and microLocation (schools, ...).
    location_factor: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    # Prices of the district, city and similar listings.
    aggregations: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    # Offer statistics of the postcode, keyed by postcode.
    offer_prices: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    cash_flow_calc: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    raw: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


class CommuteTime(Base):
    __tablename__ = "commute_times"

    listing_id: Mapped[int] = mapped_column(
        ForeignKey("listings.id", ondelete="CASCADE"), primary_key=True
    )
    place_name: Mapped[str] = mapped_column(String(64), primary_key=True)
    minutes: Mapped[float | None] = mapped_column(Float)
    mode: Mapped[str] = mapped_column(String(32), default="transit")
    origin_lat: Mapped[float] = mapped_column(Float)
    origin_lon: Mapped[float] = mapped_column(Float)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
