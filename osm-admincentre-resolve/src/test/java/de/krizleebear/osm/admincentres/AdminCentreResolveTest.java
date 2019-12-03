package de.krizleebear.osm.admincentres;

import java.io.BufferedWriter;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.stream.Stream;

import org.junit.jupiter.api.Test;

import de.krizleebear.osm.admincentres.AdminCentreResolve.ResolvedType;

public class AdminCentreResolveTest {

	@Test
	void resolve() throws IOException {
		Path placesPBF = Paths.get("places.pbf");
		Path adminPBF = Paths.get("admins.osm.pbf");

		Stream<ResolvedAdminCentre> results = AdminCentreResolve.resolveAdminCentres(placesPBF, adminPBF);

		Path resolvedTSV = Paths.get("admins-resolved.tsv");
		try (BufferedWriter out = Files.newBufferedWriter(resolvedTSV)) {

			ResolvedAdminCentre.writeTSVHeader(out);

			results.forEach(result -> {
				print(out, result);
			});
		}
	}

	private void print(BufferedWriter out, ResolvedAdminCentre result) {
		if (result.result == ResolvedType.RESOLVED) {
			try {
				result.writeToTSV(out);
			} catch (IOException e) {
				throw new RuntimeException(e);
			}
		}
	}

}
