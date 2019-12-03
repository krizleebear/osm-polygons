package de.krizleebear.osm.admincentres;

import java.io.BufferedInputStream;
import java.io.BufferedWriter;
import java.io.IOException;
import java.io.InputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.HashMap;
import java.util.Map;
import java.util.Optional;
import java.util.stream.Stream;

import org.wololo.geojson.Feature;
import org.wololo.jts2geojson.GeoJSONWriter;

import com.slimjars.dist.gnu.trove.map.TLongObjectMap;
import com.vividsolutions.jts.geom.Geometry;

import de.topobyte.osm4j.core.access.OsmIterator;
import de.topobyte.osm4j.core.dataset.InMemoryMapDataSet;
import de.topobyte.osm4j.core.dataset.MapDataSetLoader;
import de.topobyte.osm4j.core.model.iface.OsmNode;
import de.topobyte.osm4j.core.model.iface.OsmRelation;
import de.topobyte.osm4j.core.model.iface.OsmRelationMember;
import de.topobyte.osm4j.core.model.util.OsmModelUtil;
import de.topobyte.osm4j.core.resolve.EntityNotFoundException;
import de.topobyte.osm4j.geometry.GeometryBuilder;
import de.topobyte.osm4j.geometry.MissingEntitiesStrategy;
import de.topobyte.osm4j.geometry.MissingWayNodeStrategy;
import de.topobyte.osm4j.pbf.seq.PbfIterator;

public class AdminPolygons {

	private final GeometryBuilder geometryBuilder = new GeometryBuilder();
	private InMemoryMapDataSet data;

	public AdminPolygons() {
		data = new InMemoryMapDataSet();
		geometryBuilder.setMissingWayNodeStrategy(MissingWayNodeStrategy.OMIT_VERTEX_FROM_POLYLINE);
		geometryBuilder.setMissingEntitiesStrategy(MissingEntitiesStrategy.BUILD_PARTIAL);
	}

	public void readAdmins(Path adminPBF) throws IOException {
		try (InputStream in = Files.newInputStream(adminPBF); //
				BufferedInputStream bin = new BufferedInputStream(in);) {
			final boolean fetchMetadata = true;
			OsmIterator iterator = new PbfIterator(bin, fetchMetadata);
			data = MapDataSetLoader.read(iterator, false, false, true);
		}
	}

	public Stream<OsmRelation> streamRelations() {
		return data.getRelations().valueCollection().stream();
	}

	public Stream<OsmRelation> streamAdmins() {
		return streamRelations().filter(OsmUtil::isAdmin);
	}

	void exportAdminPolygons(Path adminPBF) throws IOException {

		Path polygonOutput = Paths.get("admins.polygons.geojsonseq");

		try (BufferedWriter out = Files.newBufferedWriter(polygonOutput);) {
			TLongObjectMap<OsmRelation> relations = data.getRelations();
			if (relations.isEmpty()) {
				System.out.println("No relation found");
				return;
			}

			relations.valueCollection().forEach(relation -> {

				if (!OsmUtil.isAdmin(relation)) {
					return;
				}

				try {
					String geoJSON = relationToGeoJSON(relation);
					out.write(geoJSON);
					out.write("\n");
				} catch (EntityNotFoundException e) {
					e.printStackTrace();
				} catch (IOException e) {
					throw new RuntimeException(e);
				}
			});
		}
	}

	private String relationToGeoJSON(OsmRelation relation) throws EntityNotFoundException {
		Map<String, Object> properties = new HashMap<>();
		geometryBuilder.setMissingEntitiesStrategy(MissingEntitiesStrategy.BUILD_PARTIAL);
		Geometry polygon = relationToPolygon(relation);

		polygon = com.vividsolutions.jts.simplify.TopologyPreservingSimplifier.simplify(polygon, 0.0001);

		Optional<OsmRelationMember> adminCentre = OsmUtil.getAdminCentreMember(relation);
		adminCentre.ifPresent(center -> {
			long centerID = center.getId();
			try {
				OsmNode centerNode = data.getNode(centerID);
				double latitude = centerNode.getLatitude();
				double longitude = centerNode.getLongitude();
				properties.put("centerLat", latitude);
				properties.put("centerLon", longitude);
			} catch (EntityNotFoundException e) {
				throw new RuntimeException(e);
			}
		});

		return toGeoJSON(relation, polygon, properties);
	}

	public Geometry relationToPolygon(OsmRelation relation) throws EntityNotFoundException {
		Geometry polygon;
		polygon = geometryBuilder.build(relation, data);
		return polygon;
	}

	private String toGeoJSON(OsmRelation relation, Geometry polygon, Map<String, Object> properties) {

		Map<String, String> tags = OsmModelUtil.getTagsAsMap(relation);
		for (String key : tags.keySet()) {
			properties.put(key, tags.get(key));
		}

		GeoJSONWriter writer = new GeoJSONWriter();
		org.wololo.geojson.Geometry g = writer.write(polygon);
		Feature feature = new Feature(g, properties);

		return feature.toString();
	}
}
