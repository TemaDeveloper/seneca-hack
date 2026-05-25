# Phase 1: Spatial Assembler & Data Preparation

## What is Phase 1?
Phase 1 is the "Data Foundation" of the project. It does not predict car movement or perform any simulations. Its only job is to gather, clean, and organize all the geographic and zoning data into a single, highly optimized "Master Map Database" that the rest of the application can use.

---

## 1. Data Preparation (`backend/scripts/prepare_data.py`)

This is a one-time setup script. When you run `prepare_data.py`, it executes three major tasks to gather the raw data:

### A. Downloading the Map Geography (GeoJSON)
- **Action:** The script downloads a massive 150MB `.zip` shapefile from the official Statistics Canada servers.
- **Processing:** It extracts the map, filters out the rest of Canada to isolate only the 260 Greater Toronto Area postal codes (M and L prefixes). It recalculates the GPS coordinates to match standard web maps (EPSG:4326), simplifies the polygon shapes to reduce file size, and saves it as `gta_fsa_boundaries.geojson`.

### B. ArcGIS API Zoning Integration (CSV)
- **Action:** It determines the advanced zone classification of each postal code (`residential`, `retail_hub`, `office_park`, `transit_hub`, `leisure`).
- **Processing:** The script uses an `ArcGisLivingAtlasClient` to send 260 separate HTTP requests across the internet to the Esri ArcGIS Reverse Geocode API. It sends the center coordinates of each postal code to the server, and the server replies with the specific Point of Interest (POI) type (e.g., "Airport", "Shopping Center", "College"). The script then translates these POI types into our 5 core simulation categories.
- **Output:** It saves this zoning mapping into `fsa_zone_classification.csv`. (Note: It includes a fail-safe fallback so if the API crashes during a live demo, the script silently uses an offline backup).

### C. The Grid Stress Curve (CSV)
- **Action:** It mathematically generates a 24-hour "Load Profile" curve (from 0.0 to 1.0) that mimics typical Ontario daily electricity demand (trough at 3 AM, peak at 6 PM).
- **Output:** Saves to `ieso_load_profile.csv`.

---

## 2. The Assembler Engine (`backend/spatial_assembler.py`)

When Phase 2 or Phase 3 runs, they need a clean map. They call the `load_enriched_geodataframe()` function inside `spatial_assembler.py`. This function instantly:

1. Loads the Map (GeoJSON).
2. Merges the ArcGIS Zoning Data (CSV) directly onto the map polygons.
3. Assigns a **Proxy Grid Capacity** to each zone based on its architectural type:
   - `residential`: 300 kW (Weak grid)
   - `leisure`: 500 kW (Parks/Monuments)
   - `office_park`: 1,200 kW (Corporate buildings)
   - `retail_hub`: 1,500 kW (Malls/Groceries)
   - `transit_hub`: 3,000 kW (Airports/Stations)
4. Calculates the exact `centroid_lat` and `centroid_lon` (the exact center point of the postal code) so the optimization algorithm knows exactly where to place chargers later.

## 3. The Output

The output of Phase 1 is an active Python `GeoDataFrame` (a spreadsheet mixed with a map) that lives in the computer's memory. It looks like this:

| fsa | geometry | zone_type | proxy_capacity_kw | centroid_lat | centroid_lon |
|-----|----------|-----------|-------------------|--------------|--------------|
| M5V | POLYGON(...) | retail_hub | 1500 | 43.642 | -79.387 |

This highly intelligent table is what powers the Monte Carlo car simulation in Phase 2!
