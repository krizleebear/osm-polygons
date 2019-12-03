package de.krizleebear.osm.admincentres;

import java.io.IOException;
import java.nio.file.Path;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.stream.Stream;

import com.slimjars.dist.gnu.trove.map.hash.TLongObjectHashMap;
import com.vividsolutions.jts.geom.Geometry;
import com.vividsolutions.jts.geom.GeometryCollection;
import com.vividsolutions.jts.geom.GeometryCollectionIterator;
import com.vividsolutions.jts.geom.Point;
import com.vividsolutions.jts.index.strtree.STRtree;

import de.topobyte.osm4j.core.model.iface.OsmNode;
import de.topobyte.osm4j.core.model.iface.OsmRelation;
import de.topobyte.osm4j.core.model.iface.OsmRelationMember;
import de.topobyte.osm4j.core.model.util.OsmModelUtil;
import de.topobyte.osm4j.core.resolve.EntityNotFoundException;
import de.topobyte.osm4j.geometry.GeometryBuilder;

public class AdminCentreResolve {

	private final TLongObjectHashMap<OsmNode> placeNodes;
	private final STRtree index = new STRtree();
	private final GeometryBuilder geometryBuilder = new GeometryBuilder();
	private final AdminPolygons polygons;

	public AdminCentreResolve(PlaceMap places, AdminPolygons polygons) {
		this(places.getPlaces(), polygons);
	}

	public AdminCentreResolve(TLongObjectHashMap<OsmNode> places, AdminPolygons polygons) {
		this.placeNodes = places;
		this.polygons = polygons;
		indexPlaces(places);
	}

	private void indexPlaces(TLongObjectHashMap<OsmNode> places) {
		places.valueCollection().forEach(node -> {
			Point point = geometryBuilder.build(node);
			index.insert(point.getEnvelopeInternal(), node);
		});
	}

	public Optional<OsmNode> resolvePlaceFor(OsmRelation adminRelation, Geometry adminPolygon) {

		Optional<OsmNode> definedAdminCentre = tryGetAdminCentreMember(adminRelation);
		if (definedAdminCentre.isPresent()) {
			return definedAdminCentre;
		}

		Map<String, String> relationTags = OsmModelUtil.getTagsAsMap(adminRelation);
		int adminLevel = getAdminLevel(relationTags);
		if (adminLevel < 6) {
			return Optional.empty();
		}

		String adminName = relationTags.get("name");
		if (hasNoName(adminName)) {
			return Optional.empty();
		}

		Optional<OsmNode> nodeWithSameName = findPlaceWithName(adminPolygon, adminName);
		return nodeWithSameName;
	}

	private Optional<OsmNode> findPlaceWithName(Geometry adminPolygon, String adminName) {

		@SuppressWarnings("unchecked")
		List<OsmNode> placeCandidates = index.query(adminPolygon.getEnvelopeInternal());
		for (OsmNode placeCandidate : placeCandidates) {

			if (differentName(adminName, placeCandidate)) {
				continue;
			}

			if (outsideOfPolygon(adminPolygon, placeCandidate)) {
				continue;
			}

			return Optional.of(placeCandidate);
		}

		return Optional.empty();
	}

	private boolean outsideOfPolygon(Geometry adminPolygon, OsmNode placeCandidate) {
		return !insideOfPolygon(adminPolygon, placeCandidate);
	}

	private boolean differentName(String adminName, OsmNode placeCandidate) {
		return !nameEquals(placeCandidate, adminName);
	}

	private static boolean nameEquals(OsmNode node, String name) {
		Map<String, String> placeTags = OsmModelUtil.getTagsAsMap(node);
		String placeName = placeTags.get("name");

		return name.equals(placeName);
	}

	private boolean insideOfPolygon(Geometry polygon, OsmNode node) {
		Point placePoint = geometryBuilder.build(node);

		// each element of a GeoCollection has to be checked individually
		GeometryCollectionIterator it = new GeometryCollectionIterator(polygon);
		while (it.hasNext()) {
			Geometry geometry = (Geometry) it.next();

			// the iterator returns the parent objects as well, ignore them
			if (geometry instanceof GeometryCollection) {
				continue;
			}

			if (geometry.covers(placePoint)) {
				return true;
			}
		}

		return false;
	}

	private boolean hasNoName(String adminName) {
		return adminName == null || adminName.isEmpty();
	}

	private Optional<OsmNode> tryGetAdminCentreMember(OsmRelation adminRelation) {
		Optional<OsmRelationMember> adminCentreMember = OsmUtil.getAdminCentreMember(adminRelation);
		if (adminCentreMember.isPresent()) {
			OsmRelationMember definedCentre = adminCentreMember.get();
			long centreID = definedCentre.getId();
			OsmNode centreNode = placeNodes.get(centreID);
			if (centreNode != null) {
				return Optional.of(centreNode);
			}
		}

		return Optional.empty();
	}

	private int getAdminLevel(Map<String, String> relationTags) {
		String adminLevelString = relationTags.get("admin_level");
		int adminLevel = 0;
		if (adminLevelString != null) {
			adminLevel = Integer.parseInt(adminLevelString);
		}
		return adminLevel;
	}

	public enum ResolvedType {
		UNRESOLVED, WAS_ALREAY_DEFINED, RESOLVED
	}

	public static Stream<ResolvedAdminCentre> resolveAdminCentres(Path placesPBF, Path adminPBF) throws IOException {
		PlaceMap places = new PlaceMap();
		places.readPlaces(placesPBF);

		AdminPolygons polygons = new AdminPolygons();
		polygons.readAdmins(adminPBF);

		AdminCentreResolve resolver = new AdminCentreResolve(places, polygons);

		return polygons.streamAdmins().map(resolver::resolve);
	}

	public ResolvedAdminCentre resolve(OsmRelation admin) {
		try {
			Optional<OsmRelationMember> adminCentreMember = OsmUtil.getAdminCentreMember(admin);
			if (adminCentreMember.isPresent()) {
				return new ResolvedAdminCentre(admin, Optional.empty(), ResolvedType.WAS_ALREAY_DEFINED);
			}

			Geometry polygon = polygons.relationToPolygon(admin);
			Optional<OsmNode> place = resolvePlaceFor(admin, polygon);
			if (place.isPresent()) {
				return new ResolvedAdminCentre(admin, place, ResolvedType.RESOLVED);
			}
		} catch (EntityNotFoundException e) {
			e.printStackTrace();
		}
		return new ResolvedAdminCentre(admin, Optional.empty(), ResolvedType.UNRESOLVED);
	}

}
