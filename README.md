# Berlin property scanner

Crawls flats for sale in Berlin from ImmoScout24, Immowelt (incl. former Immonet),
Kleinanzeigen and Neubaukompass, plus the ThinkImmo aggregator (~40 portals, with
market value, rent estimate and cash flow). It stores them in PostgreSQL/PostGIS with
price history and enriches them with:

- distance to the nearest S-Bahn and U-Bahn station (OpenStreetMap),
- distance and public-transport commute time to your workplace (r5py + VBB timetable),
- population structure of the LOR Planungsraum (Amt für Statistik Berlin-Brandenburg),
- Bezirk, Ortsteil, Prognoseraum and Kiez of the listing's location,
- election results of the voting district (Abgeordnetenhauswahl 2026 and Bundestagswahl 2025).

Explore the data with Jupyter, Metabase dashboards or a Streamlit map app.

```
crawlers ──> listings + listing_snapshots ──┐
open data ─> stations, LOR, wahlbezirke ────┼──> listing_features (materialized view)
geocoding + r5py ─> geom, commute_times ────┘        │
                                                     ├── Jupyter (notebooks/)
                                                     ├── Metabase (localhost:3000)
                                                     └── Streamlit (app/)
```

## Streamlit locally, Neon, and Community Cloud

The map reads PostgreSQL through `get_settings()` (`src/immo_scanner/config.py`) and
`get_engine()` (`src/immo_scanner/db/session.py`). While `streamlit run` is active, a
filled-in `.streamlit/secrets.toml` wins over `.env`. The `immo` CLI keeps using
`IMMO_DATABASE_URL`. Passwords stay out of the source tree: `.env` and
`.streamlit/secrets.toml` are listed in `.gitignore`.

### Run the app locally

```powershell
copy .env.example .env
copy .streamlit\secrets.toml.example .streamlit\secrets.toml
uv sync --extra app
uv run streamlit run app/streamlit_app.py
```

Leave `DATABASE_URL` empty in `secrets.toml` to keep the local Docker database from `.env`.
To try Neon from your machine, paste the Neon connection string into `DATABASE_URL`.

Load the schema into Neon from the CLI (secrets are not read outside Streamlit). Put the
Neon URL in `IMMO_DATABASE_URL`, then `uv run immo init-db`. Neon needs `CREATE EXTENSION postgis;`.
`postgresql://` URLs are rewritten to `postgresql+psycopg://` (psycopg 3).

### Streamlit Community Cloud

- Repository Python is 3.12 (`.python-version`).
- Main file path: `app/streamlit_app.py`.
- App settings → Secrets. Paste the same TOML as `.streamlit/secrets.toml.example`.

One connection string is enough:

```toml
DATABASE_URL = "postgresql://USER:PASSWORD@ep-xxxx.region.aws.neon.tech/neondb?sslmode=require"
```

Or the separate fields (a remote host gets `sslmode=require`):

```toml
[postgres]
host = "ep-xxxx.region.aws.neon.tech"
username = "neondb_owner"
password = "..."
database = "neondb"
port = 5432
```

Community Cloud installs `requirements.txt` only. Crawlers stay on your machine and write
to the same Neon database via `IMMO_DATABASE_URL`. The hosted app only reads that database.

## Setup (Windows)

1. Install PostgreSQL with PostGIS (via Stack Builder: *Spatial Extensions → PostGIS*),
   [Docker Desktop](https://www.docker.com/products/docker-desktop/) for Metabase and Nominatim, and
   [uv](https://docs.astral.sh/uv/getting-started/installation/).
2. Create the databases, configure, and start Metabase:
   ```powershell
   psql -U postgres -c "CREATE DATABASE immo;" -c "CREATE DATABASE metabase;"
   copy .env.example .env      # then edit DB URL, workplace, Metabase admin
   docker compose up -d        # Metabase and Nominatim; Metabase reaches Postgres via host.docker.internal
   ```
   The first Nominatim start downloads the Berlin OpenStreetMap extract and imports
   it. That often takes 20–40 minutes; `docker compose ps` shows the container as
   healthy once `http://localhost:8080/status` answers. Later starts reuse the
   imported database and apply Geofabrik's daily Berlin updates. To run PostGIS in
   Docker instead of locally, use `docker compose --profile docker-db up -d` and set
   `METABASE_DB_HOST=db`.
3. Install Python dependencies and the Playwright browser:
   ```powershell
   uv sync --all-extras
   uv run playwright install chromium
   ```
4. Create the schema and load reference data:
   ```powershell
   uv run immo init-db
   uv run immo load-reference
   ```
5. Optional, for commute times: install a Java 21 runtime
   (`winget install EclipseAdoptium.Temurin.21.JDK`). Then run
   `uv run immo download-commute-data`.

## Daily use

```powershell
uv run immo crawl kleinanzeigen --max-pages 3   # quick test of one source
uv run immo crawl                               # all sources
uv run immo crawl-thinkimmo                     # ThinkImmo, after the portal crawls
uv run immo enrich                              # geocode, commute times, refresh view
uv run immo stats                               # quick overview, incl. location gaps
```

`crawl`, `crawl-thinkimmo` and `import-is24-emails` geocode listings without a point and
refresh `listing_features` when they finish, so new flats get their Planungsraum and
neighbourhood data right away. Pass `--no-enrich` to skip that. `enrich` does the same and
adds commute times. Geocoding uses the local Nominatim. A listing
whose lookup fails is tried again on the next run. Lookups that found nothing are asked
again after `IMMO_GEOCODE_RETRY_DAYS` (default 14, 0 to never retry). Each run ends with a
warning when active listings are still without a Planungsraum; the Data gaps page lists them.

To run this daily, register a scheduled task. It runs while you are logged on, because
ImmoScout24 may need you to solve a captcha in the browser window:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\register_task.ps1 -Time 07:30
```

The task crawls the portals, imports IS24 alert emails, then crawls ThinkImmo (up to
`-ThinkImmoDetailLimit` exposés, default 300) and finally fetches details and enriches.
The crawl steps run with `--no-enrich`, since the final `enrich` does that work once.
Logs are written to `logs/`.

### Search criteria

`IMMO_MIN_ROOMS`, `IMMO_MIN_LIVING_AREA`, `IMMO_MIN_PRICE` and `IMMO_MAX_PRICE` in `.env`
are the only price, area and room limits. They are added to the ImmoScout24, Immowelt,
Kleinanzeigen and ThinkImmo searches, and every listing is checked again before saving. Neubaukompass is filtered per flat, except for projects
that advertise no individual flats - those are kept only when the smallest flat already
has enough rooms and living area and none of the prices is above the cap.
Listings without a value for a criterion are kept.
Stored listings that stop matching (e.g. after a price increase) are deleted. After
tightening the criteria, run `uv run immo prune` to delete listings that no longer match.

### Price history and removed listings

Every crawl updates `last_seen`. A new row is added to `listing_snapshots` only when
price, area, rooms, floor, year built or Hausgeld change. Listings that were not seen in
a crawl are marked `removed`, but only if that crawl reached the last result page.
Increase `--max-pages` (the scheduled task uses 400) if a source has more pages.

### Immowelt

Immowelt only serves later result pages to a real browser, so the crawler opens Chromium
with the persistent profile `data/browser-profile/immowelt/` (also used for exposés).
It loads `IMMO_IMMOWELT_SEARCH_URL` (Berlin, newest first) with `priceMin`, `priceMax`,
`spaceMin` and `numberOfRoomsMin` from the search criteria and follows `page=N` to the
last page, 30 listings per page. With the default criteria that is about 80 pages. A
complete run marks Immowelt listings that dropped out of the search as `removed`. If a
captcha appears, solve it in the window.

### ImmoScout24

ImmoScout24 actively blocks bots. The crawler opens a visible Chromium with a persistent
profile (`data/browser-profile/`). If a captcha appears, solve it in the window; the
crawler waits up to 5 minutes. If it keeps getting blocked, use the email fallback:

1. Create a saved search on ImmoScout24 with email alerts.
2. Save alert emails as `.eml` files into `data/is24-emails/`.
3. Run `uv run immo import-is24-emails`. Processed files move to `processed/`.

### Listing details and ImmoScout24 exposés

`uv run immo details` opens the detail page of every listing without details (up to
`--limit` per source) and stores year built, last renovation, floor, heating, elevator,
parking, cellar and balconies in `listing_details` (also in `listing_features`).

For ImmoScout24 it additionally stores the full exposé in `immoscout24_exposes`: Scout-ID,
Objekt-ID, title, address, rented status, living and usable area, availability, rooms,
bedrooms, bathrooms, price, price per m², buyer commission, year built, heating, energy
certificate data, balcony/terrace, cellar, internet speed and the description, features,
other and location texts. Photos, floor plans and documents are downloaded to
`data/media/immoscout24/<Scout-ID>/` (paths in the `media` column). `IMMO_IS24_PHOTO_SIZE`
picks `large` (1106×830 WebP, ~1.8 MB per listing), `original` (full JPEGs, ~10× larger)
or `none`.

### Neubaukompass

Neubaukompass search pages only show new-build *projects*. The crawler therefore opens
every project page and reads its "Wohnbeispiele" - the individual flats, each with its
own price, floor and exposé page. **One flat becomes one listing**, so the criteria,
price per m², commute time and election results all apply per flat.

Wohnbeispiele are a published sample, not the full inventory: a project with 23 units
typically advertises a handful. A project that lists none stays a single listing, with
`price`, `living_area` and `rooms` holding the lower end of the advertised ranges and
the full ranges plus the number of units in `listing_snapshots.raw`.

`uv run immo details neubaukompass` stores the full exposé in `neubaukompass_units`:
address, price, price per m², floor and total floors, rooms, bedrooms, bathrooms,
living area, year built, last renovation, availability, commission for buyer and
seller, ID and Objektnummer, balcony/terrace, energy certificate data, the Lage,
Ausstattung and Beschreibung texts, links to the floor plan, the Energieausweis, the
project website and the 360° tour, and the provider with its contact details. The site
lists tour, brochure and website under one heading, so they are told apart by their URL.
Photos, floor plans, Energieausweise and brochures are downloaded to
`data/media/neubaukompass/<ID>/` (paths in the `media`
column). `IMMO_NEUBAUKOMPASS_PHOTO_SIZE` picks `large` (950×1200, the size the page
shows), `original` (the full-size file) or `none`.

The site is behind Cloudflare, so both pages and images are fetched through the
persistent browser profile rather than a plain HTTP client.

### ThinkImmo

[ThinkImmo](https://thinkimmo.com) aggregates listings from about 40 portals
(ImmoScout24, Immowelt, Kleinanzeigen, DeinNeuesZuhause, FAZ, ...) and adds its own
market value and rent estimate, gross return, cash flow, location scores and nearby
schools, supermarkets, Kitas and stops. Its web app reads a JSON API that answers
without login, so `uv run immo crawl-thinkimmo` uses plain HTTP:

1. It runs the search in `IMMO_THINKIMMO_SEARCH_QUERY`, 50 results per page, and upserts
   every result into `thinkimmo_properties`. The default is the search alert "Auftrag2":
   Berlin, up to 600,000 €, 65–150 m², 3–6 rooms, unrented, no Souterrain, not in need of
   renovation (see `docs/thinkimmo/README.md`). To use another search, set up the
   filters on thinkimmo.com, copy the query string of the browser's `api.thinkimmo.com/immo`
   request and drop `from` and `size`.
2. It links each property to listings. If one of its portal ads (ImmoScout24, Immowelt,
   Kleinanzeigen or Neubaukompass) is already a listing of an own crawler, the property
   attaches to it (`thinkimmo_properties.listing_id`). Otherwise it becomes a listing with
   source `thinkimmo`, checked against the search criteria above and with price history
   like any other source. When an own crawler finds the ad later, the next run removes
   the `thinkimmo` listing and links the property to the portal listing.
3. It fetches up to `--detail-limit` (default 300) exposés: properties without one first,
   then those ThinkImmo changed since. The exposé adds the description, micro location,
   postcode statistics and view counts. `--refresh-days N` also re-fetches exposés older
   than N days.
4. After a complete search pass, properties and `thinkimmo` listings that dropped out of
   the search are marked `removed`.

The first run fetches about 2,500 search results in about 4 minutes. Getting every
exposé takes around eight daily runs at 300 per run. `IMMO_THINKIMMO_FINANCE_QUERY` sets
the financing assumptions behind `cash_flow` and `own_capital_return` (default: 10 %
equity, 4 % interest, 1.5 % repayment, management cost 25 % of rent, 2 % notary).

`thinkimmo_properties` keeps the main facts as columns and the nested parts as JSON
(`location_factor` with micro location, `aggregations`, `offer_prices`, `cash_flow_calc`,
`platforms`, `images`, `buying_price_history`). `raw` holds the full API record.

Foreclosures (`is_foreclosure`, Zwangsversteigerungen) are stored too. Their price is a
minimum bid or appraised value, so the Streamlit filter "Hide foreclosures" is on by
default.

## Exploring the data

All tools read the `listing_features` materialized view: one row per listing with the
latest price, price per m², `total_score` (see `docs/scoring.md`), days on market, price
change, rooms, bedrooms, bathrooms,
floor, year built, provision, elevator, balcony, parking, cellar, nearest stations,
commute time, LOR demographics and vote shares. `bezirk`, `ortsteil`, `prognoseraum`
and `kiez` name the area a listing falls in. Kiez is the LOR Planungsraum (for example
Wrangelkiez); Prognoseraum is the LOR level above that. Ortsteil is the official
locality, such as Kreuzberg. Listings with ThinkImmo data also have
`thinkimmo_price_to_market_pct`, `thinkimmo_market_price_per_m2`,
`thinkimmo_estimated_rent`, `thinkimmo_gross_return`, `thinkimmo_cash_flow`,
`thinkimmo_views`, `thinkimmo_platforms` and `is_foreclosure`. Portal data wins where
both have a value; ThinkImmo only fills gaps in year built, floor, Hausgeld, energy
class and amenities. ThinkImmo reports unstated facts as "no", so its elevator, cellar,
balcony, fitted-kitchen and rented flags are used only when they say "yes". Rented
status comes from the portal exposé first, then from "vermietet"/"unvermietet" in the
title, then from ThinkImmo. `thinkimmo_properties` keeps ThinkImmo's values unchanged.

- **Metabase:** `uv run immo metabase-setup` creates the admin user (from `.env`),
  connects the database and builds a starter dashboard at http://localhost:3000.
- **Streamlit:** `uv run streamlit run app/streamlit_app.py`. Connection settings are in
  [Streamlit locally, Neon, and Community Cloud](#streamlit-locally-neon-and-community-cloud).
  The Flats page maps
  listings with filters for price, size, commute, rail, elevator, balcony, parking,
  cellar and foreclosures, plus a table of those amenities, the ThinkImmo valuation
  and price-history charts. The
  Gymnasiums page maps each school's Abitur average,
  demand and student-body figures. The Sekundarschulen page does the same for integrated
  secondary schools and Gemeinschaftsschulen. The Grundschulen page maps each primary school and
  Gemeinschaftsschule, coloured by all-day form, with Europa-Schule and language from the
  Schulporträts and an optional layer of Einschulbereiche. The Demographics page has one map each for migration
  background, German citizens, religion, age, unemployment, income and education, per
  Planungsraum where the data exists and per Bezirk otherwise. The Crime tab maps the
  Berlin crime atlas (cases and cases per 100,000 residents) per Bezirksregion for 2016–2025.
  The combined map at the top
  overlays the indicators chosen below, either as stacked translucent layers or as the
  intersection of the areas that pass a threshold on every overlay. The Elections tab maps
  a chosen party, the strongest party, AfD, Grüne and the tolerance score
  (`docs/scoring.md`) per Briefwahlbezirk for either loaded election. In the combined map, party shares are projected onto Planungsräume by the
  valid votes of the overlapping districts. Only the open tab draws its maps, because
  browsers keep at most 16 WebGL contexts per page and each map uses two.
- **Jupyter:** `uv run jupyter lab notebooks/`. `01_market_overview.ipynb` covers
  prices by Bezirk, location factors, a hedonic price model that flags flats priced
  below expectation, and price changes over time.

## Reference data notes

| Dataset | Source | Command |
| --- | --- | --- |
| S/U-Bahn stations | OpenStreetMap via Overpass | `immo load-reference stations` |
| LOR Planungsräume 2021 | Geoportal Berlin WFS `lor_2021` | `immo load-reference lor` |
| Ortsteile | Geoportal Berlin WFS `alkis_ortsteile` | `immo load-reference ortsteile` |
| Population by LOR | `EWR_L21_202412E_Matrix.csv` (daten.berlin.de) | `immo load-reference population` |
| Bundestagswahl 2025 | `Berlin_BT25_W2.csv` + `RBS_OD_UWB_BT25.zip` | `immo load-reference elections` |
| Abgeordnetenhauswahl 2026 | `Datenexport_AGH2026_Zweitstimme_W_BE.csv` (wahlen-berlin.de) + `RBS_OD_UWB_AH26.zip` | `immo load-reference agh-elections` |
| Gymnasium statistics | [gymnasium-berlin.net](https://www.gymnasium-berlin.net/abiturdaten) (Abiturschnitt, Erfolgsquote, Anmeldungen, Schüler, Lehrer, Typisierung, Migrationshintergrund, Jahrgang 5/7, Schnelllerner, Europa-Schule, Profile) | `immo load-reference gymnasium-stats` |
| Sekundarschule statistics | [sekundarschulen-berlin.de](https://www.sekundarschulen-berlin.de/abitur/2024) (Abiturschnitt 2024, Erfolgsquote, Anmeldungen, Typisierung, Migrationshintergrund, Schulnummer) | `immo load-reference sekundarschule-stats` |
| Grundschulen and Einschulbereiche | Geoportal Berlin WFS `schulen` (sites) and `schulen_esb` (catchments); Ganztag, Europa-Schule and first foreign language from [Schulporträts](https://www.bildung.berlin.de/Schulverzeichnis/SchulListe.aspx). Pass `--file` a spreadsheet with BSN, student and teacher columns to fill headcounts. The same load stores every school site in `schulen`, which the Sekundarschulen map reads | `immo load-reference grundschulen` |
| Unemployment, low-wage share, migration background and foreign citizens per PLR | Monitoring Soziale Stadtentwicklung 2025 (berlin.de), `21indexind_anteile_plr_mss2025.xlsx` + `41kontextind_anteile_plr_mss2025.xlsx` (31.12.2024) | `immo load-reference mss` |
| Foreign citizens per PLR | AfS `EWR_L21_202512A_Matrix.csv` (Einwohnerregister; foreigner columns only, no origin breakdown) | `immo load-reference migration` |
| Residents by origin (EU, Islamic/Arab countries, former USSR/Yugoslavia, 19–23 countries) and origin diversity per PLR, half-yearly since 30.06.2021 | AfS Statistischer Bericht [A I 16 – hj](https://www.statistik-berlin-brandenburg.de/a-i-16-hj), tables T1 and T4. All editions are downloaded from the [Statistische Bibliothek](https://www.statistischebibliothek.de/) into `data/reference/ewr_lor/` | `immo load-reference origins` |
| Citizenship by world region per PLR | Zensus 2022 100 m grid, `Zensus2022_Staatsangehoerigkeit_Gruppen_in_Gitterzellen.zip` | `immo load-reference citizenship` |
| Catholic, Protestant, other/none per PLR | Zensus 2022 100 m grid, `Religion.zip` | `immo load-reference religion` |
| Islam, Orthodox, Jewish per Bezirk | none official; your own table | `immo load-reference religion-bezirk --file ...` |
| Median gross monthly pay per PLR | AfS, on request | `immo load-reference income --file ...` |
| School and vocational certificates per Bezirk (residents 15+) | AfS Mikrozensus report [A I 10 / A I 11 / A VI 2 – j](https://www.statistik-berlin-brandenburg.de/a-i-10-a-i-11-a-vi-2-j) 2025, tables 7.1 and 7.2. The download link changes with each report; update `IMMO_MIKROZENSUS_XLSX_URL` | `immo load-reference education` |
| Crime per Bezirksregion, 2016–2025 (17 offence groups, cases and cases per 100,000 residents) | [Kriminalitätsatlas](https://www.berlin.de/polizei/service/kriminalitaetsatlas/), Polizei Berlin, `fallzahlen_hz-2016-2025.xlsx` | `immo load-reference crime` |
| Milieuschutzgebiete (§ 172 Abs. 1 Nr. 2 BauGB) and the share of each Planungsraum they cover | Geoportal Berlin WFS `erhaltungsverordnungsgebiete:erhaltgeb_em` | `immo load-reference milieuschutz-areas` |

### Demographics

All demographic indicators go into `area_indicators` (one row per area, topic and
indicator), and each loader replaces only the rows from its own source. Age comes from
the population columns of `lor_planungsraeume`.

The Zensus grid loaders sum the 100 m cells whose centre lies in a Planungsraum. Cells
with very few residents are suppressed in the source and count as zero, so shares use the
sum of the published categories as the base.

`religion-bezirk` and `income` have no public download and only load from `--file`;
`immo load-reference all` skips them. `education` downloads the Mikrozensus workbook and
reads only its school and vocational certificate sheets; it accepts `--file` as well. Each accepts the original AfS Excel layout
(Bezirk or PLR rows, keyword-matched headers) or a tidy CSV with `bezirk;indicator;value`
(or `plr_id;indicator;value` for income), optionally with `label`, `unit` and `ref_date`.
Faith indicators for `religion-bezirk` are `islam`, `orthodox`, `jewish` and `none`.

The Amt für Statistik portal serves its `/opendata/` files through a JavaScript app.
The loader retries with a headless browser. If that fails, download the file in your
browser and pass it explicitly:

```powershell
uv run immo load-reference population --file C:\Downloads\EWR_L21_202412E_Matrix.csv
uv run immo load-reference elections --file Berlin_BT25_W2.csv --geometry-file RBS_OD_UWB_BT25.zip
```

### Election results

Results are aggregated to postal-vote districts (Briefwahlbezirke) when possible, so
postal votes are included. The loader logs the columns it detected and the share of
districts it matched. Each election is stored under its own `scope` in `wahlbezirke`, so
loading one never touches the other.

Every listing is matched to the Briefwahlbezirk its coordinates fall into, which happens
in the `listing_features` view. New listings therefore pick up their election results on
the next `immo enrich`, with no extra lookup per property. The columns are
`agh_vote_linke`, `agh_vote_spd`, `agh_vote_gruene`, `agh_vote_cdu`, `agh_vote_afd`
(plus `fdp`, `bsw`) as a percentage of valid Zweitstimmen, `agh_turnout_pct`, and
`agh_party_shares` holding every party that stood. The Bundestagswahl equivalents use a
`bt_` prefix.

Note that the Abgeordnetenhaus 2026 figures published so far are the preliminary result
(Vorläufiges Ergebnis). Re-run `immo load-reference agh-elections` once the final result
is out; the loader replaces the previous rows.

To use a different election, set `IMMO_ELECTION_RESULTS_URL`, `IMMO_ELECTION_GEOMETRY_URL`
and `IMMO_ELECTION_NAME` (or their `IMMO_AGH_ELECTION_*` counterparts).

Geocoding uses the Nominatim container from Docker Compose (`IMMO_NOMINATIM_URL`,
default `http://localhost:8080`), cached in `geocode_cache`. The public instance at
`https://nominatim.openstreetmap.org` is limited to one request per second and needs
`IMMO_CONTACT_EMAIL`. Each listing
gets a `geo_precision` of `source`, `exact`, `street`, `postcode` or `district`. Treat
station distances for `postcode`/`district` listings as approximate.

## Development

```powershell
uv run pytest
uv run ruff check .
```

Scraping may conflict with the sites' terms of use. Keep it private, low-volume and
limited to what you need.
