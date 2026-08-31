# Spezifikation: Datenexport `osm-facilities.parquet`

## 1. Zielsetzung
Für Downstream-Anwendungen (wie Geocoding, Routing-Kontext und räumliche Analysen) wird ein zentraler, unifizierter Geodaten-Layer benötigt, der wichtige Infrastrukturelemente, Trassen und die Grundrisse (Footprints) von Großanlagen bündelt. 

Das Upstream-Projekt (`osm-tools`) fungiert hierbei als reiner Datenbeschaffer (ETL). Sämtliche fachliche Logiken, räumliche Verschneidungen (Spatial Joins) und Vektorberechnungen erfolgen *downstream*.

**Zielartefakt:** Ein einziges OGC GeoParquet-File namens `osm-facilities.parquet`.

---

## 2. Parquet-Schema

Um hunderte leere (Sparse-)Spalten zu vermeiden und gleichzeitig verschiedene Geometrietypen (Punkte, Linien, Polygone) in einem Datensatz zu vereinen, ist das Schema wie folgt definiert:

| Spalte | Typ | Beschreibung |
| :--- | :--- | :--- |
| `osm_id` | `int64` | Die eindeutige OSM-ID. |
| `osm_type` | `varchar` | Typ des OSM-Elements: `'N'` (Node), `'W'` (Way), `'R'` (Relation). |
| `feature_class` | `varchar` | Eine normalisierte Hauptkategorie (siehe Mapping-Tabelle unten). |
| `geom` | `Geometry` | Gemischte Geometrien in einer Spalte (`Point`, `LineString`, `Polygon`). |
| `tags` | `JSON` | Ein Key-Value JSON-Objekt aller fachlich relevanten OSM-Tags am Objekt (z. B. `{"name": "A 8", "ref": "8"}`). |

---

## 3. Extraktions-Regeln & Feature Classes

Es sollen ausschließlich OSM-Objekte exportiert werden, die in eine der folgenden `feature_class`-Kategorien fallen. Andere Objekte werden verworfen.

| `feature_class` | OSM-Filter (Tags) | Erwarteter Geometrie-Typ |
| :--- | :--- | :--- |
| **`motorway`** | `highway IN ('motorway', 'trunk', 'motorway_link', 'trunk_link')` | `LineString` |
| **`junction`** | `highway = 'motorway_junction'` | `Point` |
| **`service_area`**| `highway IN ('services', 'rest_area')` | `Polygon` |
| **`airport`** | `aeroway = 'aerodrome'` | `Polygon` |
| **`shopping_mall`**| `shop = 'mall'` | `Polygon` |
| **`university`** | `amenity = 'university'` | `Polygon` |
| **`hospital`** | `amenity = 'hospital'` | `Polygon` |
| **`stadium`** | `leisure = 'stadium'` | `Polygon` |

### Wichtige geometrische Vorgaben:
1. **Linienrichtung bei Fahrbahnen (`motorway`):** 
   Die Knotenreihenfolge (Node Order) der extrahierten `LineString`-Geometrien darf beim Export **unter keinen Umständen verändert, vereinfacht oder umgekehrt werden**. Downstream-Anwendungen verlassen sich auf das implizite `oneway`-Verhalten von OSM-Autobahnen, um aus dem Linienvektor die exakte Fahrtrichtung (Heading in Grad) zu berechnen.
2. **Polygone statt Umrisse:** 
   Geschlossene Ways oder Relationen für Flächen (z. B. Universitäten, Raststätten) müssen als echtes (Multi-)Polygon in das GeoParquet geschrieben werden, nicht als LineString-Ring.

---

## 4. Tag-Filterung (`tags` JSON)

Das JSON-Objekt in der Spalte `tags` soll die Original-Tags des OSM-Objekts enthalten. Um die Dateigröße gering zu halten, sollten **Meta-Tags** verworfen werden.

**Einschließen (Beispiele):**
* Namen: `name`, `name:*`, `alt_name`, `int_name`
* Referenzen: `ref`, `iata`, `icao`, `wikidata`
* Spezifika: `highway`, `aeroway`, `amenity`, `shop`, `autohof`, `access`, `operator`, `brand`

**Verwerfen (Beispiele):**
* Metadaten: `source`, `created_by`, `note`, `fixme`
* Versionierungs-Infos (falls als Tag hinterlegt).
