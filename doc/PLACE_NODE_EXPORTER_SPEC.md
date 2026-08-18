# Spezifikation: OSM Place-Node Export für das `osm-polygons` Projekt

> **Ziel-Repository:** `krizleebear/osm-polygons` / `osm-tools` (Upstream-Exporter)
> **Auftraggeber:** `osm-geocoder` (verarbeitet die GeoJSONSeq-Boundary-Dateien)
> **Status:** Entwurf — Umsetzung im Upstream-Projekt erforderlich

---

## 1. Problemstellung

Der `osm-geocoder` leitet das administrative Zentrum (`centerpoint`) eines Stadt- bzw.
Stadtteil-Elements aus **OSM Place-Nodes** ab (Features mit `place=city|town|village|suburb|…`),
die im Boundary-GeoJSONSeq enthalten sind. Die Auflösung erfolgt über einen
Namens-Match (`name` des Place-Nodes == `name` des Admin-Polygons).

**Der aktuelle Exporter exportiert nur Point-Features, die selbst ein `admin_level`-Tag tragen.**
Siedlungs-Sitz-Nodes (Städte, Dörfer) haben in OSM in den meisten Ländern **kein** `admin_level`-Tag
und werden daher nicht exportiert. Ergebnis (gemessen an den aktuellen Release-Dateien):

| Land | Point-Features im Export | davon Place-Nodes |
|---|---:|---:|
| AT (Österreich) | 3 | 3 → nur **Wien** (Hauptstadt-Node hat `admin_level=2` + `capital=yes`) |
| DE (Deutschland) | 44 | 41 (nur Kommunen, deren Node zufällig `admin_level` trägt) |
| CH (Schweiz) | 13 | 13 |
| FR (Frankreich) | 229 | 229 (Frankreich taggt Gemeindesitze systematisch mit `admin_level=8`) |

**Konsequenz:** Für Österreich bekommt ausschließlich `Wien` einen `centerpoint`;
alle anderen Städte (Graz, Linz, Innsbruck, Salzburg, Klagenfurt am Wörthersee, …) fallen auf
den JTS-Interior-Point zurück.

---

## 2. Ziel

Der Exporter liefert **alle Siedlungs-Place-Nodes innerhalb des jeweils exportierten Landes**
mit, sodass der Konsument flächendeckend Centerpoints auflösen kann — unabhängig davon, ob die
Nodes ein `admin_level`-Tag tragen.

> ⚠️ **Nicht-Ziel / bewusst ausgeschlossen:** Overture-Maps-Locality-Punkte als Quelle. Deren
> Datenqualität (Namen, Koordinaten) ist für dieses Vorhaben nicht ausreichend zuverlässig.
> Die Place-Nodes kommen **ausschließlich aus OSM**.

---

## 3. Funktionaler Anforderungen

### 3.1 Auswahl der Features

- Exportiere **alle Point-Features** (`@type=node`) im Bereich des exportierten Landes, deren
  `place`-Tag in folgender **Whitelist** liegt:

  ```
  city, town, village, municipality, commune,
  suburb, hamlet,
  quarter, borough, neighbourhood, neighborhood, city_block,
  locality, isolated_dwelling, townlet
  ```

- **Ausgeschlossen** (auch wenn `place` gesetzt): `country`, `state`, `province`, `region`,
  `county`, `sea`, `ocean`, `water`, `island`, `islet`, `glacier`, `continent`, `island_group`.

- Die bisherige Regel „Point-Features nur mit `admin_level`-Tag“ entfällt für Place-Nodes.
  (Bestehende `admin_level`-Nodes bleiben selbstverständlich weiterhin enthalten — sie sind
  nach dieser Regel eine Teilmenge.)

### 3.2 Geografischer Filter (Point-in-Polygon)

- Ein Place-Node wird **nur dann** exportiert, wenn sein Koordinatenpunkt innerhalb des
  exportierten Länder-Polygons liegt (Admin-Level-2-Relation, inkl. MultiPolygon-Fälle).
- **Nicht** nur innerhalb der Bounding-Box prüfen — verhindert das Durchsickern fremder
  Nachbarorte (z. B. Basel in der DE-Datei, Monaco in der FR-Datei).
- Umsetzung: JTS/`PreparedGeometry` des Länder-Polygons oder STRtree über die Länderflächen,
  Abfrage `contains(point)`.

### 3.3 Properties

Alle vorhandenen Properties des OSM-Nodes werden **unverändert übernommen**, insbesondere:

- `name`, alle `name:*`-Sprachvarianten
- `place` (für den Rank-Import beim Konsumenten)
- `capital` (sofern vorhanden, z. B. `capital=yes`)
- `population`, `population:date`
- `wikidata`, `wikipedia`
- `admin_level`, `boundary` (falls auf dem Node gesetzt)
- `ISO3166-*`, `loc_name`, `int_name`, `official_name` usw.

Pflicht-Properties für den Konsumenten sind `name` und `place`. Nodes ohne `name` werden
**übersprungen** (der Konsument kann sie ohnehin nicht matchen).

### 3.4 Feature-Schema (unverändert zum bisherigen Export)

```
{"type":"Feature","id":"node/12345678","properties":{...},"geometry":{"type":"Point","coordinates":[lon,lat]}}
```

- `"@type": "node"`, `"@id": <OSM-Node-ID>` bleiben erhalten.
- Koordinaten: WGS84 `[lon, lat]` (wie im bisherigen Export).
- Ein Node darf **nicht doppelt** exportiert werden (Dedup über `@id`).

### 3.5 Reihenfolge & Stabilität

- Die Reihenfolge im GeoJSONSeq ist deterministisch (z. B. nach `@id`), damit
  Byte-identische Dateien bei gleichem Eingabedatum erzeugt werden.

---

## 4. Konfiguration

- Neuer Schalter im Exporter, z. B. `--place-nodes` / `export.placeNodes=true` (**Default: an**).
- Optionaler Level-Limit für Städte-Only-Exporte (`--place-min-rank city`) für Test-/Debug-Läufe —
  Default bleibt: **alle Whitelist-Places**.
- Länder mit sehr vielen Siedlungs-Nodes (DE ~10 700 Gemeinden, FR ~35 000) dürfen ein
  zusätzliches Konfigurations-Flag `--place-max-count <n>` erhalten, um Dateigröße
  gedrosselt zu halten. Der Konsument unterstützt fallback-frei: fehlende Place-Nodes
  bedeuten lediglich „kein refined Centerpoint“.

---

## 5. Datenvolumen & Performance (Orientierung)

- AT: ~2 095 Gemeinden → Zuwachs von ~14 MB auf ca. 15–16 MB pro Datei (überschaubar).
- DE: ~10 700 Gemeinden → Zuwachs von ~40 MB auf ca. 45–55 MB (mit `name:*`-Varianten).
- Point-in-Polygon-Check: O(n) Node-Checks gegen die (i. d. R. einzelne) Länderfläche —
  vernachlässigbar gegenüber dem bestehenden Polygon-Simplifizierungsaufwand.

---

## 6. Kompatibilität & Rückwärtskompatibilität

- Keine Änderung am Format oder an bestehenden Admin-Polygon-Features.
- Bestehende Konsumenten-Codebasis (`PolygonCache`):
  - ignoriert `place=country` (bereits implementiert),
  - ordnet unbekannten `place`-Werten den Rang 0 zu (harmlos),
  - matcht nur exakt auf den Polygon-Namen (case-insensitive) — zusätzliche Nodes
    verschlechtern also nichts.
- Re-Export erforderlich für: **alle Länder-Dateien** (insbesondere AT, DE, CH, …).

---

## 7. Akzeptanzkriterien

Für **AT_austria.admin-polygons.geojsonseq** (Referenz-Check):

1. Die Datei enthält Place-Nodes für alle Landeshauptstädte:
   `Wien`, `St. Pölten`, `Linz`, `Graz`, `Klagenfurt am Wörthersee`, `Innsbruck`, `Salzburg`,
   `Bregenz`, `Eisenstadt` — jeweils mit `geometry.type="Point"`, `place=city|town`,
   korrektem `[lon, lat]` und `name` (+ `name:de`, sofern vorhanden).
2. Die Datei enthält Place-Nodes für **alle** ~2 095 Gemeinden (mind. Stichprobenprüfung
   über alle 9 Bundesländer).
3. Es sind **keine** Place-Nodes außerhalb Österreichs enthalten (Stichproben: keine CH/DE/IT/
   SI/HU/CZ/SK/LI-Nodes).
4. Die Datei lädt mit dem bestehenden `osm-geocoder`-`PolygonCache` ohne Fehler;
   `resolveHierarchy(15.44, 47.07)` (Graz) liefert einen Centerpoint des Graz-Nodes.

**Generisch (alle Länder):**

5. Anzahl Point-Features == Anzahl Place-Nodes in der Whitelist innerhalb des Landes.
6. Jeder Point-Feature hat `name` und `place` und gültige `[lon, lat]`-Koordinaten.
7. Keine Duplikate (`@id` eindeutig).

---

## 8. Beispiel-Output (AT, Graz)

```json
{"type":"Feature","id":"node/2640449","properties":{"@type":"node","@id":2640449,"name":"Graz","name:en":"Graz","name:hu":"Gracl","name:sl":"Gradec","place":"city","population":"303419","population:date":"2024-01-01","wikidata":"Q13298","wikipedia":"de:Graz","capital":"4"},"geometry":{"type":"Point","coordinates":[15.4395,47.0707]}}
```

---

## 9. Offene Fragen / Hinweise

- **Namens-Varianten:** Der Konsument matcht aktuell exakt auf `name`. Falls Polygone und
  Nodes im `name` voneinander abweichen (z. B. `St. Pölten` vs. `Sankt Pölten`), bleibt der
  Centerpoint unrefined. Eine spätere Erweiterung des Konsumenten auf `name:*`- oder
  `official_name`-Matching ist geplant, ist aber **nicht** Bestandteil dieser Exporter-Spec.
- **Kapitale präferieren:** Enthält ein Land mehrere gleichnamige Place-Nodes
  (z. B. ein Dorf "Wien" neben der Stadt), wählt der Konsument den Rang-höchsten.
  Die Exporter-Spec erfordert kein zusätzliches Preferenzhandling.
