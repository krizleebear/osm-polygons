# Plan: Vollständige Erfassung benannter OSM-Entities (Catch-All mit Rauschfilter)

## 1. Zielbild & Grundsatz

Aktuell werden in unserem GeoXML viele benannte OSM-Objekte (wie z. B. die *Piazza di Sant'Anastasia* `relation/1862149`, benannte Parks, Berggipfel oder Flüsse) nicht erfasst, weil sie nicht in die engen fachlichen Filter der Upstream-Projekte passen.

Ziel ist eine **Catch-All-Strategie für benannte Objekte (`name IS NOT NULL`)**:
- Alles mit `name` soll prinzipiell ins GeoXML überführt werden (+20–30 % Datenvolumen gegenüber dem aktuellen Stand, absolut beherrschbar).
- **Rauschfilter:** Gezielter Ausschluss rein technischer Kleinstobjekte (Parkbänke, Trafohäuschen, einzelne Haltestellen-Steige, Mülleimer).
- **Klare Aufgabentrennung nach Upstream-Projekten:** Jedes Upstream-Projekt (`osm-polygons`, `osm-pois`, `osm-addresses`) bekommt eine eindeutige Zuständigkeit nach Geometrietypen und Rollen.

---

## 2. Globale Mapping-Strategie: Was wird wie gemapped?

Jedes benannte OSM-Objekt landet deterministisch in genau einer der folgenden GeoXML-Rollen:

| Geometrie / Charakter | Upstream-Zuständigkeit | GeoXML-Ziel | Attributierung & Typ |
|---|---|---|---|
| **Flächen mit Eigenname** (Plätze, Parks, Friedhöfe, AOIs, Siedlungen) | `osm-polygons` (`osm-facilities` / `osm-areas`) | `<AREA>` | `name="..."` `type="square\|park\|cemetery\|..."` `source_id="osm:relation/...\|osm:way/..."` |
| **Befahrbare Straßenlinien** (`highway=*`) | `osm-addresses` (`roads.parquet`) | `<STREET>` | `name="..."` `source_id="osm:way/..."` |
| **Gewässer-Linien & Kanäle** (`waterway=*`) | `osm-addresses` (neues `waterways.parquet` oder `roads.parquet`) | `<STREET>` oder gesondertes Linien-Feature | `name="..."` `type="waterway"` |
| **Adresspunkte & Hausnummern** | `osm-addresses` (`addresses.parquet`) | `<HOUSENUMBER>` / `<ADDRPOINTS>` | `number="..."` |
| **Punkthafte Ziele, Geschäfte & Natur-Landmarks** (Gipfel, Quellen, POIs) | `osm-pois` (`places.parquet`) | `<POI>` | `name="..."` `category="..."` |

---

## 3. Upstream-Projekt: `osm-polygons`

### Zuständigkeit
Alle geschlossenen Flächengeometrien (`type=multipolygon` oder geschlossene Ways mit Flächencharakter), die **einen Eigennamen tragen**, aber **keine Gebäude-Adressen** sind.

### Anforderungen an `osm-polygons`
1. **Flächen-Plätze (`highway=pedestrian` & Multipolygone/Flächen):**
   - *Filter:* `(tags['highway'] = 'pedestrian' OR tags['place'] = 'square' OR tags['amenity'] = 'plaza') AND name IS NOT NULL`
   - *Feature-Klasse:* `square` $\rightarrow$ landet in `osm-facilities` bzw. neuem `osm-areas`.
   - *Löst direkt:* **Piazza di Sant'Anastasia (1862149)** und historische Stadtplätze in ganz Europa.
2. **Parkanlagen & Grünflächen:**
   - *Filter:* `tags['leisure'] IN ('park', 'garden', 'nature_reserve', 'recreation_ground') AND name IS NOT NULL`
   - *Feature-Klasse:* `park`.
3. **Friedhöfe:**
   - *Filter:* `(tags['landuse'] = 'cemetery' OR tags['amenity'] = 'grave_yard') AND name IS NOT NULL`
   - *Feature-Klasse:* `cemetery`.
4. **Kleingärten & Freizeitanlagen:**
   - *Filter:* `tags['landuse'] = 'allotments' AND name IS NOT NULL`
   - *Feature-Klasse:* `allotments`.
5. **Generischer Flächen-Fallback:**
   - Alle verbleibenden Relationen mit `type=multipolygon` und `name IS NOT NULL`, die nicht `building=*` oder `boundary=administrative` sind $\rightarrow$ `feature_class='area'`.

---

## 4. Upstream-Projekt: `osm-pois`

### Zuständigkeit
Alle benannten Punkt- und Zielobjekte (Nodes und POI-Polygone), insbesondere Natur-Landmarks und touristische Ziele.

### Anforderungen an `osm-pois`
1. **Berggipfel & Erhebungen (Top-Lücke in Taginfo):**
   - *Filter:* `tags['natural'] = 'peak' AND name IS NOT NULL`
   - *Mapping:* `categories.primary = 'peak'`, Attribut `ele` (Höhe) in Tags erhalten.
2. **Naturobjekte & Wasserstellen:**
   - *Filter:* `tags['natural'] IN ('water', 'spring', 'cave_entrance', 'beach', 'cliff', 'bay') AND name IS NOT NULL`
   - *Mapping:* `categories.primary = 'natural_feature'`.
3. **Denkmäler & historische Objekte:**
   - *Filter:* `tags['historic'] IN ('monument', 'memorial', 'castle', 'ruins', 'archaeological_site') AND name IS NOT NULL`
   - *Mapping:* `categories.primary = 'historic_site'`.
4. **Rausch-Blockliste (Blacklist für technisches Kleinst-Inventar):**
   *Ausschließen, selbst wenn `name IS NOT NULL`:*
   - `amenity IN ('bench', 'waste_basket', 'recycling', 'vending_machine', 'hunting_stand', 'post_box')`
   - `highway IN ('bus_stop', 'platform', 'street_lamp', 'traffic_signals', 'crossing')` *(sofern Steig/Platform; Haltestellen-Zusammenfassung erfolgt separat)*
   - `power IN ('pole', 'tower', 'substation', 'generator')`
   - `barrier IN ('fence', 'wall', 'gate', 'bollard', 'lift_gate')`

---

## 5. Upstream-Projekt: `osm-addresses`

### Zuständigkeit
Alle linearen Transport- und Gewässernetze sowie Hausnummern.

### Anforderungen an `osm-addresses`
1. **Gewässernetz (Waterways):**
   - *Status Quo:* Flüsse und Kanäle fehlen komplett (~600.000 benannte Abschnitte in DE).
   - *Anforderung:* Extraktion von Linien mit `waterway IN ('river', 'canal', 'stream') AND name IS NOT NULL`.
   - *Format:* Entweder neues Asset `<CC>_<region>.waterways.parquet` oder integriert in `roads.parquet` mit Attribut `waterway`.
2. **Klarstellung zu Straßen (`roads.parquet`):**
   - Plätze mit Flächengeometrie (`type=multipolygon`) gehören **nicht** in `roads.parquet`, sondern nach `osm-polygons` (siehe oben).
   - `roads.parquet` bleibt rein für linienhafte Straßenachsen und Wege zuständig.

---

## 6. Downstream-Anpassungen in `osm-geocoder`

Sobald die Upstream-Projekte die erweiterten Daten liefern:

1. **`AreaType.java` erweitern:**
   - Hinzufügen von `SQUARE("square", false)`, `PARK("park", false)`, `CEMETERY("cemetery", false)`, `ALLOTMENTS("allotments", false)`, `WATER_BODY("water_body", false)`.
2. **`FacilityQualifier.java` anpassen:**
   - Aliase für die neuen Feature-Klassen (`square`, `park`, `cemetery`, `allotments`) mappen.
3. **`geocoder_data.xsd` aktualisieren:**
   - Dokumentation der neuen Typen unter `AREA/@type`.
4. **Verwaiste Flächen erlauben (`OrphanArea`):**
   - Sicherstellen, dass benannte Flächen auch dann als `<AREA>` im GeoXML ausgegeben werden, wenn sie im Inneren keine Adresspunkte oder POIs beherbergen (`orphan="true"`).

