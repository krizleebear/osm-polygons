# 🅿️ Upstream Data Contract: Facility Parking Areas & Delivery Access Points

**Dataset Target:** `osm-facilities-{CC}.parquet`  
**Upstream Repository:** `osm-polygons` / `osm-tools`  
**Format:** GeoParquet / Apache Parquet (EPSG:4326, OGC CRS84)  
**Status:** Proposed / Requirement  

---

## 1. Motivation & Problemstellung

Große Einrichtungen und Areas of Interest (AOIs) wie **Shopping Malls, Stadien, Kliniken, Veranstaltungshallen und Gewerbeparks** bestehen in der realen Welt aus einem zusammenhängenden Areal:
1. **Kunden-Zufahrt:** Erfolgt fast ausnahmslos über vorgelagerte Kundenparkplätze, Parkhäuser oder Schrankenanlagen. In OpenStreetMap sind die Facility-Gebäude (z. B. `shop = mall`) und die Parkplätze (`amenity = parking`) jedoch als **getrennte Polygone** modelliert. Die Parkschranken und Parkhauseinfahrten liegen räumlich auf/am Parkplatz (oft 100–250 Meter vom Hauptgebäude entfernt). Ohne die Parkplatz-Polygone im Facility-Datensatz können diese Schranken nicht der Facility zugeordnet werden.
2. **Warenanlieferung / Lastverkehr:** Befindet sich typischerweise auf der Rückseite der Anlage (getrennt vom Kundenverkehr). In OSM ist die Anlieferung häufig **rein als Straße** modelliert (`highway = service` mit `service = delivery` oder `tunnel = building_passage`), ohne dass an der Gebäudewand ein expliziter `entrance = delivery`-Knoten existiert. Dadurch fehlen Anlieferungspunkte im Geocoder vollständig.

**Ziel dieser Anforderung:**
1. Bereitstellung von **Parkplatz-Polygonen (`feature_class = 'parking'`)** in `osm-facilities-{CC}.parquet`, damit der Geocoder Facilities mit ihren Parkflächen geometrisch vereinigen und deren Schranken/Einfahrten als Navigationspunkte übernehmen kann.
2. Extraktion von **Anlieferungs-Access-Points** aus Zufahrtswegen mit `service = delivery` oder `access = delivery`.

---

## 2. Tabellenschema (`osm-facilities-{CC}.parquet`)

Alle Features nutzen die bestehende Tabellenstruktur von `osm-facilities-{CC}.parquet`:

| Spalte | DuckDB / Parquet Typ | Beschreibung & Format |
| :--- | :--- | :--- |
| `continent` | `VARCHAR` | Kontinent in Kleinbuchstaben (z. B. `'europe'`). |
| `country_code` | `VARCHAR` | ISO 3166-1 Alpha-2 Code (z. B. `'DE'`, `'AT'`). |
| `osm_id` | `BIGINT` | Eindeutige OpenStreetMap Element-ID. |
| `osm_type` | `VARCHAR` | `'W'` (Way) oder `'R'` (Relation) für Polygone; `'N'` (Node) für Access Points. |
| `feature_class` | `VARCHAR` | `'parking'` für Flächen; `'entrance'` für Zufahrts-/Anlieferungspunkte. |
| `geom` | `GEOMETRY` | WGS84 (`EPSG:4326`): `Polygon`/`MultiPolygon` für Parkplätze; `Point` für Access Points. |
| `tags` | `VARCHAR` (JSON) | Vollständiges JSON-Objekt mit relevanten OSM Key-Value-Paaren. |

---

## 3. Anforderung 1: Parkplatz-Polygone (`feature_class = 'parking'`)

### 3.1 Extraktionsfilter (OSM)
Erfasst werden Flächen (Ways und Multipolygon-Relations) mit:
```sql
WHERE tags->>'amenity' = 'parking'
  AND (
    -- Entweder namentlich benannt (z. B. "Westpark", "P1", "Besucherparkplatz")
    tags->>'name' IS NOT NULL
    -- Oder expliziter Kunden-, Besucher- oder Facility-Bezug
    OR tags->>'access' IN ('customers', 'yes', 'permissive')
    -- Oder bauliche Großanlagen
    OR tags->>'parking' IN ('multi-storey', 'underground', 'surface')
  )
```

### 3.2 Relevante Tags im `tags`-JSON
* `amenity`: `'parking'`
* `name`: Name des Parkplatzes (falls vorhanden)
* `access`: z. B. `'customers'`, `'yes'`, `'private'`
* `parking`: z. B. `'surface'`, `'multi-storey'`, `'underground'`
* `operator`: Betreiber (z. B. `'Westpark'`, `'Contipark'`)
* `fee`: `'yes'` / `'no'`
* `capacity`: Stellplatzzahl (falls erfasst)
* `maxheight`: Höhenbeschränkung

---

## 4. Anforderung 2: Anlieferungs-Punkte (`service = delivery`)

Wenn die Anlieferung für Lastverkehr in OSM nicht als Knoten (`entrance = delivery`), sondern als Straßenabschnitt erfasst ist:

### 4.1 Erkennungsfilter (OSM)
Wege mit:
```sql
WHERE tags->>'highway' = 'service'
  AND (
    tags->>'service' = 'delivery'
    OR tags->>'access' = 'delivery'
    OR (tags->>'hgv' = 'designated' AND tags->>'access' = 'private')
  )
```

### 4.2 Punkt-Generierung (`geom: Point`)
Für qualifizierte Anlieferungswege wird ein punktförmiger Access Point generiert:
* **Position:** Entweder der **Abzweigknoten** von der übergeordneten öffentlichen Straße (Anfahrtsknoten für LKW) ODER der **Schnittpunkt mit der Gebäudegrenze / Gebäude-Durchfahrt** (`tunnel = building_passage`).
* **Klassifikation:**
  * `feature_class = 'entrance'`
  * `tags`:
    ```json
    {
      "entrance": "delivery",
      "service": "delivery",
      "access": "delivery",
      "hgv": "yes",
      "name": "Warenannahme / Anlieferung"
    }
    ```

---

## 5. Referenzfall & Validierung: Westpark Ingolstadt (Bayern, DE)

Zur Verifikation der Extraktion dient der reale Testfall **Westpark Ingolstadt**:

| Objekt | OSM Element | Erwartetes Ergebnis in `osm-facilities-DE.parquet` |
| :--- | :--- | :--- |
| **Mall-Hauptgebäude** | `osm:way/28013665` | `feature_class = 'shopping_mall'`, `Polygon`, `name = 'Westpark'` |
| **Kundenparkplatz (Süd/West)** | `osm:way/28013712` | `feature_class = 'parking'`, `Polygon`, `name = 'Westpark'`, `access = 'customers'` |
| **Kundenparkplatz (Nord)** | `osm:way/158179324` | `feature_class = 'parking'`, `Polygon`, `name = 'Westpark'`, `amenity = 'parking'` |
| **Parkplatzschranken (West)** | `osm:node/13648179665`<br>`osm:node/13648179667` | `feature_class = 'gate'`, `Point`, `barrier = 'lift_gate'` |
| **Parkhauseinfahrt** | `osm:node/14023680245` | `feature_class = 'parking_entrance'`, `Point`, `amenity = 'parking_entrance'` |
| **LKW-Anlieferung (Ost)** | Weg `213233765` / `213233768` an Abzweig `456368155` (Richard-Wagner-Str.) | `feature_class = 'entrance'`, `Point`, `entrance = 'delivery'`, `hgv = 'yes'` |

### Erwartetes Geocoder-Ergebnis:
Nach Einlesen der Parquet-Datei verbindet der Geocoder die Mall `28013665` mit den gleichnamigen/angrenzenden Parkplätzen `28013712` und `158179324`. Die resultierende `<AREA type="shopping_mall" name="Westpark">` erhält:
1. `<ACCESS_POINT type="parking" mode="car" ...>` für die Schranken und Parkhauseinfahrten (Westseite).
2. `<ACCESS_POINT type="delivery" mode="truck" ...>` für die Warenannahme/Anlieferung (Ostseite).
