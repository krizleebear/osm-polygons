package de.krizleebear.osm.admincentres;

import java.io.BufferedInputStream;
import java.io.BufferedWriter;
import java.io.IOException;
import java.io.InputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.HashMap;
import java.util.HashSet;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.TimeUnit;

import org.wololo.geojson.Feature;
import org.wololo.jts2geojson.GeoJSONWriter;

import com.google.common.base.Stopwatch;
import com.google.common.collect.Sets;
import com.slimjars.dist.gnu.trove.map.hash.TLongObjectHashMap;
import com.vividsolutions.jts.geom.Point;

import de.topobyte.osm4j.core.access.OsmIterator;
import de.topobyte.osm4j.core.model.iface.EntityContainer;
import de.topobyte.osm4j.core.model.iface.EntityType;
import de.topobyte.osm4j.core.model.iface.OsmEntity;
import de.topobyte.osm4j.core.model.iface.OsmNode;
import de.topobyte.osm4j.core.model.iface.OsmTag;
import de.topobyte.osm4j.core.model.util.OsmModelUtil;
import de.topobyte.osm4j.geometry.GeometryBuilder;
import de.topobyte.osm4j.pbf.seq.PbfIterator;

public class PlaceMap {

	private static final HashSet<String> REQUIRED_PLACE_KEYS = Sets.newHashSet("place", "name");
	private TLongObjectHashMap<OsmNode> places = new TLongObjectHashMap<>();

	void readPlaces(Path pbf) throws IOException {

		System.out.println("Reading " + pbf);
		Stopwatch watch = Stopwatch.createStarted();

		try (InputStream in = Files.newInputStream(pbf); //
				BufferedInputStream bin = new BufferedInputStream(in); //
		) {
			final boolean fetchMetadata = true;
			OsmIterator iterator = new PbfIterator(bin, fetchMetadata);

			long entityCount = 0;

			for (EntityContainer container : iterator) {

				entityCount++;
				if (entityCount % 1_000_000 == 0) {
					System.out.print(".");
				}

				if (container.getType() == EntityType.Node) {
					OsmNode node = (OsmNode) container.getEntity();

					if (!isPlace(node)) {
						continue;
					}

					// Get the node's tags as a map
//					Map<String, String> tags = OsmModelUtil.getTagsAsMap(node);
					places.put(node.getId(), node);

				} else {
					break;
				}
			}

			long totalMemory = Runtime.getRuntime().totalMemory();
			System.out.println();
			System.out.println("TotalMemory: " + totalMemory);

			Stopwatch duration = watch.stop();
			System.out.println("Duration [s]: " + duration.elapsed(TimeUnit.SECONDS));

			System.out.println("places: " + places.size());
		}
	}
	
	public void writePlaces(Path outPath) throws IOException
	{
		try(BufferedWriter bw = Files.newBufferedWriter(outPath);)
		{
			places.valueCollection().forEach(node -> {
				try {
					bw.write(toGeoJSON(node));
					bw.write("\n");
				} catch (IOException e) {
					throw new RuntimeException(e);
				}
			});
		}
	}

	private static boolean isPlace(OsmNode node) {
		return hasAllTags(node, REQUIRED_PLACE_KEYS);
	}

	public static boolean hasAllTags(OsmEntity entity, Set<String> keys) {

		if (entity.getNumberOfTags() < keys.size()) {
			return false;
		}

		int foundTags = 0;
		for (int i = 0; i < entity.getNumberOfTags(); i++) {
			OsmTag tag = entity.getTag(i);
			if (keys.contains(tag.getKey())) {
				foundTags++;

				if (foundTags == keys.size()) {
					return true;
				}
			}
		}
		return false;
	}

	private GeometryBuilder geometryBuilder = new GeometryBuilder();
	private GeoJSONWriter writer = new GeoJSONWriter();
	public String toGeoJSON(OsmNode node)
	{
		Point point = geometryBuilder.build(node);
		Map<String, Object> properties = new HashMap<>();
		Map<String, String> tags = OsmModelUtil.getTagsAsMap(node);
		for (String key : tags.keySet()) {
			properties.put(key, tags.get(key));
		}

		org.wololo.geojson.Geometry g = writer.write(point);
		Feature feature = new Feature(g, properties);

		return feature.toString();
	}
	
	public TLongObjectHashMap<OsmNode> getPlaces()
	{
		return places;
	}
}
