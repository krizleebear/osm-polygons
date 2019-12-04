package de.krizleebear.osm.admincentres;

import static org.assertj.core.api.Assertions.assertThat;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.List;
import java.util.stream.Stream;

import org.junit.jupiter.api.Test;

public class AdminCentreResolveTest {

	static final String srcPath = "src/test/resources";

	@Test
	void resolve() throws IOException {
		Path placesPBF = Paths.get(srcPath, "palling.place.pbf");
		Path adminPBF = Paths.get(srcPath, "palling.admin.pbf");

		Stream<ResolvedAdminCentre> results = AdminCentreResolve.resolveAdminCentres(placesPBF, adminPBF);

		Path resolvedTSV = Paths.get("admins-resolved.tsv");
		ResolvedAdminCentre.writeResolvedToTSV(results, resolvedTSV);
		
		assertThat(resolvedTSV.toFile()).exists();
		List<String> lines = Files.readAllLines(resolvedTSV);
		assertThat(lines).hasSize(2);

		String headerLine = lines.get(0);
		assertThat(headerLine).contains("ID", "\t");
		
		String resultLine = lines.get(1);
		assertThat(resultLine).contains("Palling", "941652", "240041384");
	}
}
