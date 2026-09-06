# Spezifikation: OSM Landuse Companion-Dataset (`osm-landuse-{CC}.parquet`)

> **Ziel-Repository:** `krizleebear/osm-polygons`  
> **Downstream-Konsument:** `osm-geocoder`  
> **Verwandtes Issue:** [Issue #7: feat: Extract & synthesize settlement polygons from landuse=residential + place nodes](https://github.com/krizleebear/osm-polygons/issues/7)  
> **Status:** Genehmigtes Konzept & Implementierungsplan  

---

## 1. Motivation & Problemstellung

In OpenStreetMap (OSM) existieren für viele Siedlungen, Dörfer, Weiler und Stadtviertel (`place=village`, `suburb`, `quarter`, `hamlet`) **keine administrativen Grenzrelationen** (`boundary=administrative`, `admin_level=9..10`). Stattdessen sind sie getrennt erfasst als:
1. **Repräsentativer Node:** Ein `place=*` Punkt (z. B. [Stätzling Node 130136492](https://www.openstreetmap.org/node/130136492)).
2. **Bebaute Fläche:** Polygone mit `landuse=residential` (z. B. [Way 89960316](https://www.openstreetmap.org/way/89960316)).

In Downstream-Geocodern (`osm-geocoder`) scheitert die Point-in-Polygon (PIP) Auflösung für Adressen und Straßen innerhalb solcher Siedlungen (z. B. *Am Schneidacker* in Stätzling):
- Es existiert kein Grenzpolygon für den Ortsteil.
- Ein Entfernungs-Fallback (z. B. 500 m um das Ortszentrum) erreicht die äußeren Bereiche größerer Orte nicht (der Stätzlinger Ortskern liegt ~990 m entfernt).
- **Folge:** Straßen und Adressen fallen direkt auf die Gemeindeebene zurück (`<CITY name="Friedberg">`), während der Ortsteil (`<AREA name="Stätzling">`) verloren geht.

### Lösungsweg: Entkoppelung Upstream / Downstream
Um die Upstream-Pipeline in `osm-polygons` schlank, robust und performant zu halten:
- **`osm-polygons` (Upstream):** Extrahiert alle bebauten `landuse`-Flächen (`residential`, `commercial`, `retail`) und stellt sie als standardisiertes, ZSTD-komprimiertes GeoParquet Companion-Dataset (`osm-landuse-{CC}.parquet`) bereit.
- **`osm-geocoder` (Downstream):** Verschneidet und partitioniert die Landuse-Polygone mit den `osm-places`-Punkten und `admin-polygons`-Gemeindegrenzen flexibel zur Geocoding-Laufzeit (z. B. via Voronoi-Diagramm).

---

## 2. Abgrenzung zu bestehenden Datensätzen

Landuse-Polygone werden bewusst als eigenständiges Companion-Dataset geführt:

| Datensatz | Semantik | Geometrie | Typische Objektanzahl (DE) |
| :--- | :--- | :--- | :--- |
| **`admin-polygons-{CC}.parquet`** | Amtliche Grenzen & Gebietskörperschaften | `Polygon / MultiPolygon` | ~19.000 |
| **`osm-places-{CC}.parquet`** | Benannte Siedlungspunkte / Ortskerne | `Point` | ~150.000 |
| **`osm-facilities-{CC}.parquet`** | POIs, Infrastruktur & Navigations-Zugänge | `Point, Line, Polygon` | ~15.000 |
| **`osm-landuse-{CC}.parquet`** | Bebaute Siedlungsflächen (`residential`, ...) | `Polygon / MultiPolygon` | ~400.000 |

**Vorteile der Trennung:**
1. **Keine Aufblähung von `admin-polygons`:** `admin-polygons` bleibt 100 % amtlich und schlank (19k statt 450k Zeilen in DE).
2. **Schema-Reinheit:** `osm-places` bleibt ein reines Point-Dataset für Namenssuchen.
3. **Parquet-Vorteil:** Konsumenten laden nur die Schichten, die sie für ihren Anwendungsfall benötigen.

---

## 3. Schema-Spezifikation (`osm-landuse-{CC}.parquet`)

Das Dataset folgt der OGC GeoParquet 1.1 Spezifikation (CRS `OGC:CRS84` / WGS84).

| Spalte | Typ | Nullable | Beschreibung |
| :--- | :--- | :---: | :--- |
| `continent` | `VARCHAR` | Nein | Kontinent (`europe`, `asia`, ...) |
| `country_code` | `VARCHAR` | Nein | ISO 3166-1 alpha-2 Code (`DE`, `FR`, ...) |
| `osm_id` | `BIGINT` | Nein | Numerische OSM-ID des Ways oder der Relation |
| `osm_type` | `VARCHAR` | Nein | `way` oder `relation` |
| `landuse` | `VARCHAR` | Nein | Landuse-Klasse (`residential`, `commercial`, `retail`) |
| `name` | `VARCHAR` | Ja | Name des Gebiets (falls in OSM erfasst, sonst `NULL`) |
| `name_en` | `VARCHAR` | Ja | Englischer Name (falls vorhanden) |
| `bbox_minx` | `DOUBLE` | Nein | Bounding Box Min Lon |
| `bbox_miny` | `DOUBLE` | Nein | Bounding Box Min Lat |
| `bbox_maxx` | `DOUBLE` | Nein | Bounding Box Max Lon |
| `bbox_maxy` | `DOUBLE` | Nein | Bounding Box Max Lat |
| `tags` | `JSON` | Nein | Bereinigtes Tag-Dictionary (ohne `source`, `created_by` etc.) |
| `geom` | `GEOMETRY` | Nein | Exakte Geometrie (`Polygon` oder `MultiPolygon`) |

---

## 4. Technische Umsetzung in `osm-polygons`

### 4.1 1-Pass PBF Extraktion (`scripts/extract_region.py`)
Gemäß Rule 28 der `AGENTS.md` (1-Pass PBF Extraction Invariant) wird der schwere Roh-PBF nur **einmal** gescannt:

```python
LANDUSE_FILTER_RULES = [
    "w/landuse=residential,commercial,retail",
    "r/landuse=residential,commercial,retail",
]
```

1. `extract_region.py` nimmt `LANDUSE_FILTER_RULES` in den ersten `osmium tags-filter` Durchlauf auf (`combined.pbf`).
2. Der schwere Eingangs-PBF wird sofort gelöscht.
3. `landuse.pbf` wird sekundenschnell aus `combined.pbf` extrahiert.
4. `osmium export landuse.pbf --output-format=geojsonseq --config=osmium-export-config.json` exportiert die geschlossenen Flächen.

### 4.2 Streaming-Filter (`scripts/filter_landuse.py`)
Ein speichereffizientes Python-Streaming-Skript:
- Liest GeoJSON-Features aus `stdin`.
- Filtert strikt auf `landuse IN ('residential', 'commercial', 'retail')`.
- Verwirft fehlerhafte oder nicht-polygonale Geometrien (`Point`, `LineString`).
- Bereinigt Metadaten-Tags (`source`, `created_by`, `osm_version`, etc.).
- Schreibt formatierte Zeilen als `.landuse.jsonl` (gemäß Rule 33 der `AGENTS.md`).

### 4.3 GeoParquet-Konvertierung (`convert_landuse_to_parquet.sh` & `export_landuse.sql`)
- DuckDB Spatial liest `*.landuse.jsonl` vektorisiert ein.
- Validiert `ST_IsValid(geom)` und Bounding Box Envelopes.
- Schreibt ZSTD-komprimierte Parquet-Dateien mit Row Group Size 5000.
- Aggregiert Regionen zu per-country Dateien `parquet/osm-landuse-${CC}.parquet`.

### 4.4 Pipeline-Anpassungen
- **`polygon-export-pipeline.yml`:**
  - Export-Job übergibt `--landuse-out "${EXPORT_DIR}/$(COUNTRY_CODE)_$(OSM_REGION).landuse.jsonl"`.
  - Parquet-Stage konvertiert `*.landuse.jsonl` und publiziert `osm-landuse-${CC}.parquet`.
- **`polygon-release-pipeline.yml`:**
  - Release-Job sammelt `osm-landuse-*.parquet` und lädt sie als Release-Assets hoch.

---

## 5. Verifikation & Qualitätssicherung

1. **Unit-Tests (`scripts/test_filter_landuse.py`):**
   - Prüfung korrekter Filterung (`residential`, `commercial`, `retail`).
   - Abweisung unerwünschter Tags (`forest`, `farmland`, `grass`).
   - Abweisung ungültiger Geometrietypen.
   - Tag-Pruning und Namens-Fallback.
2. **YAML-Validierung:**
   - Syntax-Prüfung aller Pipelines mittels `scripts/validate_azure_yaml.py`.
3. **End-to-End DuckDB-Diagnostik:**
   - Abfrage von Objektzahlen und Geometrie-Validität auf Test-Extrakten.
