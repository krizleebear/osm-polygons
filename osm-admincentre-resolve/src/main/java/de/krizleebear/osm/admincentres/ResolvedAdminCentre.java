package de.krizleebear.osm.admincentres;

import java.io.BufferedWriter;
import java.io.IOException;
import java.util.Optional;

import de.krizleebear.osm.admincentres.AdminCentreResolve.ResolvedType;
import de.topobyte.osm4j.core.model.iface.OsmNode;
import de.topobyte.osm4j.core.model.iface.OsmRelation;

public class ResolvedAdminCentre {
	public final OsmRelation adminRelation;
	public final Optional<OsmNode> placeNode;
	public final ResolvedType result;

	public ResolvedAdminCentre(OsmRelation adminRelation, Optional<OsmNode> placeNode, ResolvedType result) {
		this.adminRelation = adminRelation;
		this.placeNode = placeNode;
		this.result = result;
	}

	public static void writeTSVHeader(BufferedWriter out) throws IOException {
		out.append("relationID");
		out.append("\t");
		out.append("placeID");
		out.append("\t");

		out.append("name");
		out.append("\t");

		out.append("relation link");
		out.append("\t");
		out.append("place link");

		out.append("\n");
	}

	public void writeToTSV(BufferedWriter out) throws IOException {
		final long relationID = adminRelation.getId();
		final long placeID = placeNode.get().getId();
		final Optional<String> name = OsmUtil.getName(adminRelation);

		out.append(Long.toString(relationID));
		out.append("\t");
		out.append(Long.toString(placeID));
		out.append("\t");

		out.append(name.get());
		out.append("\t");

		out.append("https://www.openstreetmap.org/relation/" + relationID);
		out.append("\t");
		out.append("https://www.openstreetmap.org/node/" + placeID);

		out.append("\n");
	}
}