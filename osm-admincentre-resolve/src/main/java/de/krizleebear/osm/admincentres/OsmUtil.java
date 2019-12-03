package de.krizleebear.osm.admincentres;

import java.util.Optional;

import de.topobyte.osm4j.core.model.iface.OsmEntity;
import de.topobyte.osm4j.core.model.iface.OsmRelation;
import de.topobyte.osm4j.core.model.iface.OsmRelationMember;
import de.topobyte.osm4j.core.model.iface.OsmTag;

public class OsmUtil {

	public static Optional<String> getName(OsmEntity entity) {
		Optional<OsmTag> nameTag = getTag(entity, "name");
		if (nameTag.isPresent()) {
			return Optional.of(nameTag.get().getValue());
		}
		return Optional.empty();
	}

	public static Optional<OsmTag> getTag(OsmEntity entity, String key) {
		for (int i = 0; i < entity.getNumberOfTags(); i++) {
			OsmTag tag = entity.getTag(i);
			if (key.equals(tag.getKey())) {
				return Optional.of(tag);
			}
		}
		return Optional.empty();
	}

	public static boolean hasTag(OsmEntity entity, String key) {
		for (int i = 0; i < entity.getNumberOfTags(); i++) {
			OsmTag tag = entity.getTag(i);
			if (key.equals(tag.getKey())) {
				return true;
			}
		}
		return false;
	}

	public static Optional<OsmRelationMember> getAdminCentreMember(OsmRelation relation) {

		for (int i = 0; i < relation.getNumberOfMembers(); i++) {
			OsmRelationMember member = relation.getMember(i);
			switch (member.getRole()) {
			case "admin_centre":
			case "label":
				return Optional.of(member);
			default:
			}
		}

		return Optional.empty();
	}

	public static boolean isAdmin(OsmRelation relation) {

		return hasTag(relation, "admin_level");
	}

}
