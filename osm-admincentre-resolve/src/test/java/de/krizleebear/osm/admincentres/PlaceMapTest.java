package de.krizleebear.osm.admincentres;

import static org.assertj.core.api.Assertions.assertThat;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.List;

import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;

import de.krizleebear.osm.admincentres.PlaceMap;

public class PlaceMapTest {

	static PlaceMap map = new PlaceMap();
	
	@BeforeAll
	static void readPlaces() throws IOException
	{
		Path pbf = Paths.get("src/test/resources/palling.place.pbf");
		map.readPlaces(pbf);
	}
	
	@Test
	void placesMustBeRead() throws IOException
	{
		assertThat(map.getPlaces().valueCollection()).hasSize(1);
	}
	
	@Test
	void geoJsonMustBeWritten() throws IOException {
		Path placesOutPath = Paths.get("places.geojsonseq");
		map.writePlaces(placesOutPath);
		assertThat(placesOutPath.toFile()).exists();
		
		List<String> lines = Files.readAllLines(placesOutPath);
		assertThat(lines).hasSize(1);
		assertThat(lines.get(0)).contains("Palling");		
	}
}
