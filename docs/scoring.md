# Listing scores

Each active listing gets a set of sub-scores and one `total_score`. The numbers live on
`listing_features` and are shown in the Streamlit table and the Metabase “Listing scores”
card. Missing inputs stay `NULL` and are left out of the averages.

Group scores are the weighted mean of the sub-scores that could be computed.
`total_score` is the weighted mean of the group scores that could be computed.
A missing input is left out and the other weights are rescaled; a weight of 0 switches
an input off.

The weights are set on the Streamlit Scores page (tab “Scoring and weights”) and saved
to `data/score_weights.json` (`IMMO_DATA_DIR`). Without that file every weight is 1, which
gives the plain means. The sub-scores come from `listing_features`; `load_features()`
recomputes the group scores and `total_score` with the saved weights, so every Streamlit
page uses the same numbers. The view itself keeps the equal-weight group and total
columns, and Metabase reads those.

## Distance

`avg_commute_time = (3 × commute to workplace 1 + 2 × commute to workplace 2) / 5`

If only one workplace time exists, that time is used.

`commute_score = max(0, min(1, (60 − avg_commute_time) / 60))`

0 minutes → 1, 60 minutes or more → 0. The written form
`1 − max(0, 60 − avg) / 60` *increases* with commute time; the implemented form is
the shorter-is-better version of the same numbers.

`distance_score = commute_score`

## Building

`energy_class_score`: A+ and A = 1.25, B = 1.00, C = 0.8, D = 0.33, E or worse = 0.
Taken from the ImmoScout24 or Neubaukompass exposé.

`hausgeld_score = max(0, min(1, (800 − hausgeld) / 500))`

≤ 300 €/month → 1, ≥ 800 €/month → 0. Same inversion as commute: the written
`1 − max(0, 800 − hausgeld) / 500` rewards *higher* Hausgeld.

`building_age_score = max(0, 1 − max(0, age_of_building) / 100)`

`building_age_class_score = climate_risk_score / 100`

Looked up on `building_age_classes` from `year_built`. The table’s 0–100 score is
scaled to 0–1 so it sits next to the other sub-scores.

`heating_demand_score = max(0, 1 − typical_heating_demand_kwh / 220)`

The table stores ranges. The number used is the mid-point (`typical_heating_demand_kwh`):
220 for “> 200”, 190 for 160–220, 130 for 100–160, 85 for 70–100, 40 for “< 50”.

`building_score` = weighted mean of the building sub-scores that are present.

## Flat

`rooms_score`: 2 rooms = 0, 3 = 1, 4 = 1.5, 5 = 2. Values in between are interpolated;
more than 5 rooms stays 2. The spec left “5 rooms =” blank; 2 is the assumed value.

`floor_area_score = living_area / 75` (uncapped).

`basement_score`: cellar = 1, no cellar = 0.8.

`balconies_score`: 2 or more = 1.5, 1 = 1, 0 = 0. A “has balcony” flag with no count
counts as 1.

`bathrooms_score`: 2 or more = 1, 1 = 0.5, 0 = 0.

`kitchen_score`: fitted kitchen = 1, none = 0.95.

`parking_score`: garage or parking space = 1, none = 0.

`flat_score` = weighted mean of the flat sub-scores that are present, including parking.

## Neighbourhood

`education_score = 1 − (mean Abitur average of the closest three Gymnasiums) / 6`

Schools without a published average are skipped.

`school_type_score = max(0, min(1, (7 − level) / 6))`

`level` is the distance-weighted Schultypisierungsstufe of the six nearest Gymnasiums
or Sekundarschulen (`nearby_school_type_level` in `area_indicators` for the listing’s
Planungsraum). Stufe 1 → 1, Stufe 7 → 0. A missing level stays NULL.

`tolerance_score = 1 / ((`
AfD × 0.29 + CDU × 0.06 + SPD × 0.04 + Grüne × 0.02 + Linke × 0.05
+ FDP × 0.05 + BSW × 0.15 + Volt × 0.01
`) / 8.7)`

Shares are Abgeordnetenhaus 2026 Zweitstimmen in percent. A missing party is 0.
Volt is read from `agh_party_shares` under `voltdeutschland` (or `volt`).

`origin_diversity_score = max(0, min(1, (index − 78) / 10))`

`index` is the latest Gini-Simpson origin-diversity figure for the Planungsraum
(`plr_origin_stats.origin_key = 'simpson'`, 0–100). It measures how mixed the origins
are among residents with an assignable migration background. 78 → 0, 88 → 1. Areas
under 500 residents have no index, so the score stays NULL.

`citizenship_score = max(0, 1 − |german_share − 76| / 14)`

`german_share` is the MSS German-citizen share (`german_share_mss` in `area_indicators`,
31.12.2024), in percent of residents. 76% → 1. A share 14 points either side (62% or 90%) → 0.

`oic_score = max(0, min(1, (25 − oic_share) / 20))`

`oic_share` is the latest published share of residents with a migration background from
Islamic countries (OIC), `plr_origin_stats.origin_key = 'oic'`. That column stops at
30.06.2025. 5% or less → 1, 25% or more → 0.

`crime_score = max(0, min(1, (4600 − rate) / 3200))`

`rate` is neighbourhood offences (Kieztaten) per 100,000 residents in the listing’s
Bezirksregion, the latest year in `crime_stats`. The Bezirksregion key is the first 6
digits of `plr_id`. 1,400 or fewer → 1, 4,600 or more → 0. Those anchors are the 2025
10th percentile (1,379) and 90th percentile (4,572), each rounded to the nearest 100.
A missing rate stays NULL.

`incident_score = max(0, min(1, 1 − rate / 0.6))`

`rate` is Berliner Register incidents per 1,000 residents per year. Only incidents placed
at a street or station count. The window is the three years ending on the newest incident
date, and the count is divided by 3. 0 → 1, 0.6 or more → 0. 0.6 is the 90th percentile
of Planungsräume with at least 200 residents (0.608), rounded to the nearest tenth.
Smaller areas stay NULL. The register counts what is reported, so busier local offices
show more.

`neighborhood_score` = weighted mean of education, Schultypisierung, tolerance, origin
diversity, citizenship, OIC, crime and racist incidents (skipping any that are NULL).

## Total

`total_score = Σ wᵍ × group_score / Σ wᵍ` over `distance_score`, `building_score`,
`flat_score` and `neighborhood_score`, skipping any that are NULL. Each group score is
`Σ wˢ × sub_score / Σ wˢ` over its known sub-scores.

Some sub-scores go above 1: `energy_class_score` (up to 1.25), `rooms_score` (up to 2),
`balconies_score` (up to 1.5), and `floor_area_score` and `tolerance_score` (no cap).
They pull their group harder than their weight alone suggests.
