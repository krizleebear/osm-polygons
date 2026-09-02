# 🚪 Upstream Data Contract: Navigational Access Points, Entrances & Gates

**Dataset Target:** `osm-facilities-{CC}.parquet`  
**Upstream Repository:** `osm-polygons` / `osm-tools`  
**Format:** GeoParquet / Apache Parquet (EPSG:4326)

---

## 1. Übersicht & Zielsetzung

Dieser Data Contract spezifiziert die Erweiterung des bestehenden Datensatzes `osm-facilities-{CC}.parquet` um punktförmige Zugangsinfrastruktur (**Access Points, Entrances, Gates, Parking Entrances**).

Die Daten dienen der präzisen Anfahrts- und Zugangspunkt-Erkennung für komplexe Gebäude, Areale und Einrichtungen. Die Bereitstellung erfolgt direkt über das existierende Schema von `osm-facilities-{CC}.parquet` als punktförmige Features (`geom: Point`).

---

## 2. Parquet Tabellenschema

Alle neuen Features nutzen exakt die bestehende Tabellenstruktur von `osm-facilities-{CC}.parquet`:

| Spalte | DuckDB / Parquet Typ | Beschreibung & Format |
| :--- | :--- | :--- |
| `continent` | `VARCHAR` | Kontinent-Kürzel in Kleinbuchstaben (z. B. `'europe'`). |
| `country_code` | `VARCHAR` | ISO 3166-1 Alpha-2 Ländercode (z. B. `'DE'`, `'FR'`, `'MC'`). |
| `osm_id` | `BIGINT` | Eindeutige OpenStreetMap Element-ID. |
| `osm_type` | `VARCHAR` | OSM-Objekttyp: `'N'` (Node), `'W'` (Way), `'R'` (Relation). |
| `feature_class` | `VARCHAR` | Klassifikations-Schlüssel (siehe Abschnitt 3). |
| `geom` | `GEOMETRY` | WGS84 Point (`EPSG:4326`), OGC CRS84. |
| `tags` | `VARCHAR` (JSON) | JSON-Objekt mit relevanten OSM Key-Value-Paaren (siehe Abschnitt 4). |

---

## 3. Feature Classes & OSM Extraktionsregeln

Folgende vier neue `feature_class`-Werte werden für Zugänge und Schranken definiert:

### 3.1 `feature_class = 'entrance'` (Gebäude- & Arealzugänge)

Erfasst alle explizit deklarierten Eingänge und Zugänge.

* **OSM Filterkriterien:**
  ```sql
  WHERE tags->>'entrance' IS NOT NULL
    AND tags->>'entrance' NOT IN ('no', 'closed')
  ```
* **Empfohlener Filterumfang (Relevanzsteuerung):**
  Um das Datenvolumen gering zu halten und nav-relevante Zugänge zu priorisieren, sollten folgende Zugänge zwingend enthalten sein:
  1. Alle typisierten Eingänge: `entrance IN ('main', 'emergency', 'service', 'delivery', 'staircase', 'shop', 'office', 'garage')`
  2. Alle Eingänge mit Eigennamen oder Referenz: `name IS NOT NULL OR ref IS NOT NULL`
  3. Alle Eingänge mit expliziten Zugangs- oder Barrierefreiheitsbeschränkungen: `access IS NOT NULL OR wheelchair IS NOT NULL`
  4. *(Optional)* Allgemeine Eingänge (`entrance = 'yes'`), sofern sie an öffentlichen Gebäuden, Gewerbebauten oder Facility-Polygonen liegen.

### 3.2 `feature_class = 'gate'` (Tore, Schranken & Barrieren)

Erfasst Zufahrts- und Zugangssperren, Werktore, Schranken und Mautstellen.

* **OSM Filterkriterien:**
  ```sql
  WHERE tags->>'barrier' IN (
      'gate',
      'lift_gate',
      'toll_booth',
      'sliding_gate',
      'swing_gate',
      'stile',
      'turnstile',
      'cycle_barrier'
  )
  ```

### 3.3 `feature_class = 'parking_entrance'` (Parkhaus- & Tiefgaragenzufahrten)

Erfasst bauliche Zufahrten und Abfahrten zu Parkhäusern, Tiefgaragen und Parkplätzen.

* **OSM Filterkriterien:**
  ```sql
  WHERE tags->>'amenity' = 'parking_entrance'
     OR (tags->>'parking' IN ('underground', 'multi-storey') AND tags->>'entrance' IS NOT NULL)
  ```

### 3.4 `feature_class = 'emergency_entrance'` (Notaufnahmen & Rettungszufahrten)

Erfasst explizite Notaufnahme-Zufahrten von Kliniken sowie Rettungswachen-Zufahrten.

* **OSM Filterkriterien:**
  ```sql
  WHERE tags->>'emergency' IN (
      'emergency_ward_entrance',
      'ambulance_station'
  )
  ```

---

## 4. Tag-Projektion im `tags` JSON-Feld

Um Speicherplatz zu sparen, sollten die Roh-Tags auf ein sauberes JSON-Objekt mit relevanten Attributen gefiltert werden. Folgende OSM-Tags sollen – sofern am Objekt vorhanden – in das JSON aufgenommen werden:

```json
{
  "name": "string",
  "ref": "string",
  "description": "string",
  "entrance": "string",
  "barrier": "string",
  "amenity": "string",
  "emergency": "string",
  "access": "string",
  "motor_vehicle": "string",
  "motorcar": "string",
  "goods": "string",
  "hgv": "string",
  "foot": "string",
  "bicycle": "string",
  "maxheight": "string",
  "maxwidth": "string",
  "maxweight": "string",
  "level": "string",
  "direction": "string",
  "wheelchair": "string",
  "operator": "string",
  "fee": "string"
}
```

*Beispiel 1 (Klinik-Notaufnahme):*
```json
{"emergency": "emergency_ward_entrance", "name": "Zentrale Notaufnahme (ZNA)", "access": "emergency"}
```

*Beispiel 2 (Industrietor / LKW-Zufahrt):*
```json
{"barrier": "gate", "ref": "Tor 3", "name": "Warenannahme Nord", "goods": "yes", "hgv": "yes", "maxheight": "4.2"}
```

*Beispiel 3 (Tiefgarageneinfahrt):*
```json
{"amenity": "parking_entrance", "parking": "underground", "maxheight": "2.10", "fee": "yes"}
```

---

## 5. Geometrie- & Qualitätsanforderungen

1. **Geometrietyp:**
   * Ausschließlich `Point` (EPSG:4326).
   * Falls eine Barriere oder ein Tor in OSM als Linie (`Way`) modelliert ist (z. B. ein Schiebetor über eine Straße), wird der Mittelpunkt (`Centroid`) oder der Schnittpunkt mit dem Straßennetz als Repräsentationspunkt herangezogen.
2. **Koordinaten-Gültigkeit:**
   * $-180.0 \le \text{lon} \le 180.0$ und $-90.0 \le \text{lat} \le 90.0$.
   * Keine `NULL`-, `NaN`- oder leeren Geometrien (`GEOMETRYCOLLECTION EMPTY`).
3. **Deduplizierung:**
   * Jeder `osm_id`-Knoten darf pro Land nur einmal in der Tabelle vorkommen.
4. **Vermeidung von Negativ-Tags:**
   * Objekte mit `access = 'no'`, die keine Sonderberechtigung besitzen (z. B. `emergency = 'yes'` oder `goods = 'yes'`), können für öffentliche Routings irrelevant sein, sollten aber bei Werksgeländen mit `ref` oder `name` (z. B. "Tor 1") erhalten bleiben.
