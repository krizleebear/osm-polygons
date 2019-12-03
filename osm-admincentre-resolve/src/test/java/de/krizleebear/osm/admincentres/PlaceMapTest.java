package de.krizleebear.osm.admincentres;

import java.io.IOException;
import java.nio.file.Path;
import java.nio.file.Paths;

import org.junit.jupiter.api.Test;

import de.krizleebear.osm.admincentres.PlaceMap;

public class PlaceMapTest {

	@Test
	void readPlaces() throws IOException
	{
		Path pbf = Paths.get("germany-latest.osm.pbf");
		PlaceMap map = new PlaceMap();
		map.readPlaces(pbf);
		
		Path placesOutPath = Paths.get("places.geojsonseq");
		map.writePlaces(placesOutPath);
	}
	
}
