package de.krizleebear.osm.admincentres;

import java.io.BufferedWriter;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Optional;
import java.util.stream.Stream;

import de.topobyte.osm4j.core.model.iface.OsmNode;
import de.topobyte.osm4j.core.model.iface.OsmRelation;

public class ResolvedAdminCentre {
	public enum ResolvedType {
		UNRESOLVED, WAS_ALREAY_DEFINED, SUCCESSFULLY_RESOLVED
	}

	public final OsmRelation adminRelation;
	public final Optional<OsmNode> placeNode;
	public final ResolvedAdminCentre.ResolvedType result;

	public ResolvedAdminCentre(OsmRelation adminRelation, Optional<OsmNode> placeNode,
			ResolvedAdminCentre.ResolvedType result) {
		this.adminRelation = adminRelation;
		this.placeNode = placeNode;
		this.result = result;
	}

	public boolean couldBeResolved() {
		return ResolvedType.SUCCESSFULLY_RESOLVED == result;
	}

	/**
	 * Write all results that could be successfully resolved to a tab separated
	 * values file with the given path.
	 * 
	 * @param results
	 * @param resolvedTSV
	 * @throws IOException
	 */
	public static void writeResolvedToTSV(Stream<ResolvedAdminCentre> results, Path resolvedTSV) throws IOException {
		try (BufferedWriter out = Files.newBufferedWriter(resolvedTSV)) {
			writeTSVHeader(out);
			results.filter(ResolvedAdminCentre::couldBeResolved).forEach(result -> {
				try {
					result.writeToTSV(out);
				} catch (IOException e) {
					throw new RuntimeException(e);
				}
			});
		}
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