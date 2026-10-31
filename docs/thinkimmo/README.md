# ThinkImmo

[ThinkImmo](https://thinkimmo.com) aggregiert Inserate von rund 40 Portalen (u. a. ImmoScout24,
Immowelt, DeinNeuesZuhause/Wohnglück, FAZ) und reichert sie mit Bewertungen, Mietschätzungen,
Lagedaten und einer Cashflow-Rechnung an.

Stand der Untersuchung: 05.10.2026.

## Mein Suchauftrag „Auftrag2“

Suchauftrag-ID `235511` (`https://thinkimmo.com/search-alert/235511`).
Treffer zum Zeitpunkt der Aufnahme: **2.551**, ca. **47 neue Objekte pro Woche**.

| Filter            | Wert                                                                                   |
| ----------------- | -------------------------------------------------------------------------------------- |
| Immobilientyp     | Wohnungen (Kauf)                                                                       |
| Standort          | Berlin, BE (Stadt, kein Umkreis)                                                       |
| Wohnungstyp       | alle außer **Souterrain**                                                              |
| Kaufpreis         | bis 600.000 €                                                                          |
| Wohnfläche        | 65 – 150 m²                                                                            |
| Zimmer            | 3 – 6                                                                                  |
| Objektzustand     | alle außer **Renovierungsbedürftig** und **Reif für Abriss**                           |
| Vermietung        | nur unvermietet                                                                        |
| Unbekannte Werte  | ausgeschlossen (`allowUnknown=false`)                                                  |

Cashflow-Annahmen des Suchauftrags (Standardwerte, beeinflussen nur die berechneten Felder):

| Parameter                         | Wert                         |
| --------------------------------- | ---------------------------- |
| Eigenkapital                      | 10 % des Kaufpreises         |
| Nebenkosten aus Eigenkapital      | nein                         |
| Bewirtschaftungskosten            | 25 % der Miete               |
| Zinssatz                          | 4 %                          |
| Tilgung                           | 1,5 %                        |
| Notar & Grundbuch                 | 2 %                          |
| Renovierungskosten                | keine                        |

### Entsprechende API-Abfrage

```text
GET https://api.thinkimmo.com/immo
  ?active=true
  &type=APARTMENTBUY
  &sortBy=publishDate,desc
  &from=0&size=50
  &geoSearches=[{"geoSearchQuery":"Berlin","geoSearchType":"city","region":"Berlin"}]
  &buyingPriceTo=600000
  &sqmFrom=65&sqmTo=150
  &roomsFrom=3&roomsTo=6
  &rented=false
  &apartmentType=GROUND_FLOOR,RAISED_GROUND_FLOOR,APARTMENT,LOFT,MAISONETTE,TERRACED_FLAT,PENTHOUSE,ROOF_STOREY,OTHER,NO_INFORMATION
  &condition=FIRST_TIME_USE,WELL_KEPT,MINT_CONDITION,MODERNIZED,FIRST_TIME_USE_AFTER_REFURBISHMENT,REFURBISHED,FULLY_RENOVATED,NEGOTIABLE,NO_INFORMATION
  &allowUnknown=false
  &grossReturnAnd=false
  &favorite=false
  &excludedFields=true
```

`geoSearches` muss URL-kodiert werden. Antwort: `{ total, results[], aggregations }`.
Blättern über `from` (Offset) und `size`; 50 pro Seite funktionieren.

Der Crawler übernimmt Kaufpreis, Wohnfläche und Zimmer nicht aus diesem Suchauftrag.
Diese Parameter kommen aus `IMMO_MIN_PRICE`, `IMMO_MAX_PRICE`, `IMMO_MIN_LIVING_AREA`
und `IMMO_MIN_ROOMS`, denselben Grenzen wie bei den anderen Portalen.

## API

Die Weboberfläche ist eine Next.js-App, die ihre Daten von `api.thinkimmo.com` lädt.

| Endpunkt                         | Zweck                                                        |
| -------------------------------- | ------------------------------------------------------------ |
| `GET /immo?...`                  | Suche, liefert Listen-Datensätze (ca. 55 Felder)             |
| `GET /immo/{id}?...`             | Exposé, liefert alle Felder (ca. 60 Felder plus Mikrolage)   |
| `GET /immo/rangeAggregation?...` | Verteilungen (Preis/m², Kaufpreis, Rendite) für die Filter   |
| `GET /osm?formattedAddress=...`  | Geokodierung des Standort-Filters                            |

Beobachtungen:

- `/immo` und `/immo/{id}` haben ohne Login-Token geantwortet. Nur das konto-bezogene Feld
  `detailedReportId` fehlt dann. Das kann sich jederzeit ändern; der Crawler sollte mit
  Rate-Limit und sauberem Fehlerhandling arbeiten.
- `excludedFields=true/false` macht keinen Unterschied in den gelieferten Feldern.
- Die Cashflow-Felder werden aus den Query-Parametern berechnet:
  `ownCapital`, `ownCapitalInPercent`, `ownCapitalAdditionalCost`, `managementCost`,
  `managementCostInPercent`, `interestRate`, `repaymentRate`, `additionalPurchaseCost`,
  `renovationCost`, `renovationCostInPercent`.
- Die Weboberfläche bietet zusätzlich einen CSV-Export der aktuellen Ansicht.

## Verfügbare Filter

Aus dem Filter-Dialog. API-Parameter nur dort angegeben, wo sie beobachtet wurden.

**Standort & Typ**

- Standort (Bundesland, Stadt, Ort, PLZ) mit Umkreis in km, alternativ Suchgebiet zeichnen
  (`geoSearches`)
- Immobilientyp: Wohnungen, Häuser, Grundstücke, Garagen/Stellplätze, Büros/Praxen,
  amtl. Zwangsversteigerungen (`type`, z. B. `APARTMENTBUY`)
- Wohnungstyp (`apartmentType`): `HALF_BASEMENT` Souterrain, `GROUND_FLOOR` Erdgeschoss,
  `RAISED_GROUND_FLOOR` Hochparterre, `APARTMENT` Etagenwohnung, `LOFT`, `MAISONETTE`,
  `TERRACED_FLAT` Terrassenwohnung, `PENTHOUSE`, `ROOF_STOREY` Dachgeschoss, `OTHER`,
  `NO_INFORMATION`

**Finanzielles**

- Kaufpreis von/bis (`buyingPriceFrom`/`buyingPriceTo`)
- Bruttorendite potentiell von/bis, ODER Bruttorendite Ist von/bis (`grossReturnAnd`)
- Kaufpreis/m² von/bis
- Preis zum Marktwert von/bis (%)
- Kaltmiete/m² potentiell von/bis
- Hausgeld von/bis

**Objekt-Eigenschaften**

- Wohnfläche von/bis (`sqmFrom`/`sqmTo`), Zimmer von/bis (`roomsFrom`/`roomsTo`)
- Baujahr von/bis, letzte Modernisierung von/bis
- Objektzustand (`condition`): `FIRST_TIME_USE` Erstbezug, `WELL_KEPT` Gepflegt,
  `MINT_CONDITION` Wie neu, `MODERNIZED`, `FIRST_TIME_USE_AFTER_REFURBISHMENT`,
  `REFURBISHED` Saniert, `NEED_OF_RENOVATION` Renovierungsbedürftig, `FULLY_RENOVATED`,
  `NEGOTIABLE` Nach Vereinbarung, `RIPE_FOR_DEMOLITION` Reif für Abriss, `NO_INFORMATION`
- Energieeffizienzklasse, Ausstattungsqualität
- Balkon ja/nein, Aufzug ja/nein
- Anzahl Parkplätze, Anzahl Etagen, Etage (jeweils von/bis)

**Sonstige Eigenschaften**

- Inseratsalter in Tagen von/bis
- Vermietet / unvermietet (`rented`)
- Privat / gewerblich
- Zwangsversteigerung / Bieterverfahren ein- oder ausschließen
- Plattformen ausschließen
- Nur reduzierte Angebote
- Erbbaurecht ein- oder ausschließen
- Mit / ohne Energieausweis
- Mit / ohne Provision

**Makro- und Mikrolage**

- Standortbewertung von/bis
- Einwohnerzahl der Stadt, Stadt mit/ohne Universität, Anzahl Studierende
- Wachstumsprognose Bevölkerung 2030 (%), Arbeitslosenquote (%)
- Max. Entfernung zu ÖPNV, Supermarkt, Schule, Kita (m)

**Cashflow**

- Monatlicher Cashflow von/bis, Eigenkapitalrendite von/bis
- Finanzierungsannahmen (siehe oben)

**Suchbegriffe**

- Freitext; mit `!` vorangestellt werden Begriffe ausgeschlossen

## Verfügbare Daten pro Inserat

Ausgewertet an 10 Exposés aus „Auftrag2“ (`GET /immo/{id}`). „Befüllt“ = Anzahl der 10
Exposés mit einem Wert.

### Stammdaten

| Feld                                  | Befüllt | Beispiel / Bemerkung                                      |
| ------------------------------------- | ------: | --------------------------------------------------------- |
| `id`                                  | 10      | `ab4bf3e55db255b81eae1bd22bcfb7b3` (ThinkImmo-ID)         |
| `title`                               | 10      |                                                           |
| `description`                         | 7       | 1.200 – 2.200 Zeichen; fehlt bei Immowelt-Inseraten       |
| `buyingPrice`                         | 9       | fehlt z. B. bei „Preis auf Anfrage“                       |
| `pricePerSqm`                         | 9       |                                                           |
| `squareMeter`, `rooms`                | 10      |                                                           |
| `comission`                           | 5       | Provision in %, z. B. 3,57                                |
| `houseMoney`                          | 0 – 9   | Hausgeld; in der Listenstichprobe 9 von 22                |
| `realtorCompany`                      | 1       | Maklerfirma                                               |
| `privateOffer`                        | 10      | privat vs. gewerblich                                     |
| `publishDate`, `lastUpdatedAt`        | 10      |                                                           |
| `runningTime`                         | 10      | Inseratsdauer in Tagen                                    |
| `active`                              | 10      |                                                           |

### Objekt

| Feld                                  | Befüllt | Werte                                                     |
| ------------------------------------- | ------: | --------------------------------------------------------- |
| `apartmentType`                       | 10      | Enum (siehe Filter), oft `NO_INFORMATION`                 |
| `condition`                           | 10      | Enum, oft `NO_INFORMATION`                                |
| `interiorQuality`                     | 10      | in der Stichprobe immer `NO_INFORMATION`                  |
| `heatingType`                         | 10      | z. B. `CENTRAL_HEATING`, oft `NO_INFORMATION`             |
| `constructionYear`                    | 5       |                                                           |
| `lastRefurbishment`                   | 0       | in der Listenstichprobe 4 von 22                          |
| `floor`, `numberOfFloors`             | 1       | in der Listenstichprobe 8 bzw. 10 von 22                  |
| `numberOfParkingSpaces`               | 1       |                                                           |
| `lift`, `cellar`, `balcony`, `garden`, `builtInKitchen` | 10 | Boolean                                    |
| `energyCertificate`                   | 10      | Boolean                                                   |
| `energyEfficiencyClass`               | 4       | `A` … `H`, `NO_INFORMATION`                               |
| `rented`, `leasehold`, `foreClosure`  | 10      | vermietet, Erbbaurecht, Zwangsversteigerung               |

### Lage

| Feld                                  | Befüllt | Bemerkung                                                 |
| ------------------------------------- | ------: | --------------------------------------------------------- |
| `zip`, `region`                       | 10      |                                                           |
| `address.*`                           | 10      | geokodiert: `city`, `borough` (Bezirk), `suburb` (Ortsteil), `postcode`, `lat`, `lon`; selten `road`, `house_number` |
| `location.lat/lon`                    | 10      |                                                           |
| `originalAddress.*`                   | 10      | Adresse laut Portal; Straße meist leer                    |
| `oAddress.<portal>.*`                 | 10      | Adresse je Quellportal                                    |

### Bewertung und Markt

| Feld                                  | Befüllt | Bemerkung                                                 |
| ------------------------------------- | ------: | --------------------------------------------------------- |
| `spPricePerSqm`                       | 10      | geschätzter Marktwert pro m²                              |
| `metaSpPricePerSqm`                   | 10      | `model` (Monat), `standarderror`, `score`, `range.min/max` |
| `priceInMarket`                       | 10      | Kaufpreis in % des geschätzten Marktwerts (z. B. 51 – 103) |
| `rentPrice`, `rentPricePerSqm`        | 10      | geschätzte Kaltmiete gesamt bzw. pro m²                   |
| `metaSpRentPricePerSqm`               | 10      | wie oben, für die Miete                                   |
| `rentPriceCurrent`, `rentPriceCurrentPerSqm` | 0 | Ist-Miete, nur bei vermieteten Objekten               |
| `grossReturn`                         | 9       | potentielle Bruttorendite in %                            |
| `grossReturnCurrent`                  | 0       | Ist-Rendite, nur bei vermieteten Objekten                 |
| `aggregations.district.*`             | 10      | Median Ortsteil: Kaufpreis, Preis/m², Rendite             |
| `aggregations.location.*`             | 10      | dasselbe für die Stadt                                    |
| `aggregations.similarListing.*`       | 10      | dasselbe für vergleichbare Inserate                       |
| `offerPrices.<PLZ>.*`                 | 10      | Angebotsstatistik der PLZ: Anzahl, Ø/min/max von Kaufpreis, Preis/m², Fläche, Baujahr, Rendite |

### Preisentwicklung und Nachfrage

| Feld                                  | Befüllt | Bemerkung                                                 |
| ------------------------------------- | ------: | --------------------------------------------------------- |
| `buyingPriceHistory[]`                | 9       | `buyingPrice`, `platformName`, `creationDate`             |
| `priceReduced`, `priceIncreased`      | 10      |                                                           |
| `changeLog[]`                         | 0       | in der Stichprobe leer (alle Inserate von heute)          |
| `numberOfViews`, `numberOfClicks`, `numberOfFavorites` | 10 | Aufrufe auf ThinkImmo                         |

### Cashflow (abhängig von den Finanzierungsparametern)

| Feld                                  | Befüllt | Bemerkung                                                 |
| ------------------------------------- | ------: | --------------------------------------------------------- |
| `cashFlow`, `cashFlowPerLivingUnit`   | 9       | monatlicher Cashflow in €                                 |
| `ownCapitalReturn`                    | 9       | Eigenkapitalrendite in %                                  |
| `cashFlowCalc.*`                      | 9       | Rechenweg: `creditCost`, `managementCostTotal`, `fullBuyingPrice`, `ownCapital`, `additionalCost` (Kaufnebenkosten-Satz), `regionCost`, Zins, Tilgung usw. |

### Makrolage (`locationFactor`)

| Feld                                  | Bemerkung                                                 |
| ------------------------------------- | --------------------------------------------------------- |
| `score`                               | Standortbewertung (Berlin: 67)                            |
| `population`, `populationTrend.from/to` | Einwohner, Prognose 2030 in %                           |
| `populationMeta.historicValues`       | Einwohner 2011 – 2022                                     |
| `unemploymentRate`, `unemploymentRateMeta.historicValues` | Arbeitslosenquote, monatliche Werte |
| `hasUniversity`, `numberOfStudents`   |                                                           |
| `*Score`                              | Teilscores (Bevölkerung, Trend, Uni, Arbeitslosigkeit)    |

Für Berlin sind diese Werte bei allen Inseraten gleich (Stadtebene).

### Mikrolage (`locationFactor.microLocation`, nur im Exposé)

Je 6 nächstgelegene Einträge mit `name`, `distance` (km) und teilweise `address`, `contact`,
`openingHours`:

- `schools`
- `supermarkets`
- `kindergarten`
- `publicTransport` (mit `transportTypes`, z. B. `bus`)

### Quellen und Bilder

| Feld          | Bemerkung                                                                         |
| ------------- | --------------------------------------------------------------------------------- |
| `platforms[]` | `name` (`is24`, `immowelt`, `DeinNeuesZuhause`, …), `id`, `url` zum Originalinserat, `creationDate`, `publishDate`, `active`. Ein Objekt kann auf mehreren Portalen gelistet sein. |
| `images[]`    | `id`, `originalUrl`, `title`, `floorPlan` (Grundriss ja/nein); 2 – 18 Bilder      |

### Stichprobe

| Titel (gekürzt)                         | Preis     | m²  | Zi. | Ortsteil            | Portal           | Marktwert % | Rendite | Cashflow |
| --------------------------------------- | --------: | --: | --: | ------------------- | ---------------- | ----------: | ------: | -------: |
| Angenehme Wohnung mit Südwestbalkon     | 295.000   | 72  | 3   | Charlottenburg-Nord | is24             | 85          | 3,08    | -807     |
| Wohnung in Berlin (Nikolassee)          | 349.900   | 77  | 3   | Grunewald           | DeinNeuesZuhause | 83          | 4,79    | -579     |
| Wohnung in Berlin (Baumschulenweg)      | 499.000   | 90  | 3   | Baumschulenweg      | DeinNeuesZuhause | 103         | 3,74    | -1.156   |
| Etagenwohnung (Charlottenburg)          | 435.000   | 93  | 3   | Charlottenburg      | DeinNeuesZuhause | 75          | 4,83    | -639     |
| Etagenwohnung (Charlottenburg)          | 470.000   | 116 | 3   | Charlottenburg      | DeinNeuesZuhause | 59          | 5,93    | -371     |
| Etagenwohnung (Charlottenburg)          | 450.000   | 127 | 4   | Charlottenburg      | DeinNeuesZuhause | 51          | 6,78    | -115     |
| Etagenwohnung (Charlottenburg)          | 480.000   | 129 | 3   | Charlottenburg      | DeinNeuesZuhause | 53          | 6,46    | -217     |
| LE CORBUSIER: Maisonette                | 575.000   | 106 | 3   | Grunewald           | immowelt         | 92          | 3,74    | -1.331   |
| PENTHOUSE mit Aufdachterrasse           | –         | 150 | 4   | Charlottenburg      | immowelt         | –           | –       | –        |
| 3-Zi.-EG-Wohnung                        | 300.000   | 71  | 3   | Charlottenburg      | immowelt         | 74          | 4,84    | -490     |

## Hinweise für den Crawler

- Liste über `/immo` mit den Filtern oben abrufen, dann pro neuer oder geänderter `id`
  das Exposé über `/immo/{id}` holen (Mikrolage, `offerPrices`, Aufrufe, Cashflow-Rechnung
  gibt es nur dort).
- Änderungen über `lastUpdatedAt` und `buyingPriceHistory` erkennen.
- Verschachtelte Strukturen (`address`, `locationFactor`, `aggregations`, `offerPrices`,
  `platforms`, `images`, `buyingPriceHistory`) zusätzlich als JSON-Rohdaten speichern,
  damit keine Information verloren geht.
- Über `platforms[].url` lassen sich ThinkImmo-Datensätze mit bestehenden IS24- und
  Immowelt-Inseraten verknüpfen. Ein Objekt kann auf bis zu fünf Portalen gleichzeitig
  stehen (z. B. `immowelt/kip/wohnungjetzt/ohnemakler/is24`); ThinkImmo führt sie bereits
  zu einem Datensatz zusammen.
- DeinNeuesZuhause-Titel sind generisch („Wohnung in Berlin (Ortsteil)“). Die vier
  DeinNeuesZuhause-Exposés mit 51 – 75 % des Marktwerts haben alle `foreClosure=true`
  (Zwangsversteigerung, der Preis ist also ein Verkehrswert bzw. Mindestgebot). In den
  ersten 50 Treffern betrifft das 5 von 12 DeinNeuesZuhause-Inseraten und kein Inserat
  anderer Portale. Für die Bewertung sollte man diese Fälle trennen oder ausfiltern.
