package de.krizleebear.osm.admincentres;

import java.io.IOException;
import java.nio.file.Path;
import java.nio.file.Paths;

import org.junit.jupiter.api.Test;

import de.krizleebear.osm.admincentres.AdminPolygons;

public class AdminPolygonsTest {

	@Test
	void exportPolygons() throws IOException
	{
		Path admins = Paths.get("admins.osm.pbf");
		AdminPolygons export = new AdminPolygons();
		export.exportAdminPolygons(admins);
	}
	
}
