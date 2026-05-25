"""
Data preparation script — downloads GTA FSA boundaries from Statistics Canada
and prepares the data files needed for Phase 1.

Run once: python scripts/prepare_data.py
"""

import os
import sys
import zipfile
import json

# We'll need these — check they're installed
try:
    import geopandas as gpd
    import pandas as pd
    import requests
except ImportError:
    print("Missing dependencies. Run: uv add geopandas pandas requests")
    sys.exit(1)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "backend", "data")
os.makedirs(DATA_DIR, exist_ok=True)

# Statistics Canada 2021 Census FSA boundary file (cartographic)
# This is the official public download URL for the shapefile
STATCAN_FSA_URL = (
    "https://www12.statcan.gc.ca/census-recensement/2021/geo/sip-pis/"
    "boundary-limites/files-fichiers/lfsa000b21a_e.zip"
)

# GTA FSA prefixes — M (Toronto), L (surrounding regions: Peel, York, Durham, Halton)
GTA_PREFIXES = ("M", "L")

# Output paths
FSA_GEOJSON_PATH = os.path.join(DATA_DIR, "gta_fsa_boundaries.geojson")
ZONE_CSV_PATH = os.path.join(DATA_DIR, "fsa_zone_classification.csv")
IESO_CSV_PATH = os.path.join(DATA_DIR, "ieso_load_profile.csv")

class ArcGisLivingAtlasClient:
    """
    Client for ArcGIS REST API.
    Uses public Esri Reverse Geocoding to determine predominant land-use by POI density.
    """
    def __init__(self):
        # Using a 100% public Esri endpoint that requires no API keys!
        self.base_url = "https://geocode.arcgis.com/arcgis/rest/services/World/GeocodeServer/reverseGeocode"
        
    def get_zone_type(self, lon: float, lat: float, fsa: str) -> str:
        """
        Send a spatial coordinate query to the ArcGIS REST API.
        """
        params = {
            "location": json.dumps({"x": lon, "y": lat}),
            "f": "json",
            "featureTypes": "POI,StreetInt,Postal"
        }
        
        print(f"    -> [API CALL] Requesting {fsa} at ({lon:.3f}, {lat:.3f})")
        
        try:
            # The actual HTTP call to Esri ArcGIS REST API
            response = requests.get(self.base_url, params=params, timeout=3)
            if response.status_code == 200:
                data = response.json()
                
                # Parse the Geocode response
                if "address" in data:
                    addr_type = data["address"].get("Addr_type", "")
                    poi_type = data["address"].get("Type", "")
                    
                    print(f"       [API RESPONSE] Type: {addr_type} | Detail: {poi_type}")
                    
                    poi_lower = poi_type.lower()
                    
                    # Map ArcGIS POI types to our 5 core simulation tiers
                    if addr_type == "POI":
                        if any(x in poi_lower for x in ["airport", "station", "transit", "train", "bus"]):
                            return "transit_hub"
                        if any(x in poi_lower for x in ["park", "museum", "monument", "cemetery", "arts", "entertainment"]):
                            return "leisure"
                        if any(x in poi_lower for x in ["office", "corporate", "bank", "school", "college", "university", "doctor", "hospital", "religious"]):
                            return "office_park"
                        # All other POIs (Restaurant, Grocery, Shopping Center, Footwear) default to retail_hub
                        return "retail_hub"
                    
                    # If it's a generic street address or postal code, it's mostly residential
                    return "residential"
                    
        except requests.RequestException as e:
            print(f"       [API ERROR] {e}")
            pass
            
        print("       [FALLBACK] Using heuristic fallback...")
        return self._heuristic_fallback(fsa)
        
    def _heuristic_fallback(self, fsa: str) -> str:
        # Fallback values for live-demo safety
        office_cores = {"M5A", "M5B", "M5C", "M5E", "M5G", "M5H", "M5J", "M5K", "M5L", "M5R", "M5S", "M5T", "M5V", "M5W", "M5X"}
        retail_cores = {"M2N", "M2K", "M1P", "L5B", "L5A", "L3R", "L3T", "L4B", "L4K", "L6T", "L6S", "L7L", "L6H"}
        transit_cores = {"M9W", "L4W", "L5S"} # Pearson Airport corridors
        
        if fsa in office_cores: return "office_park"
        if fsa in retail_cores: return "retail_hub"
        if fsa in transit_cores: return "transit_hub"
        return "residential"


# ---------------------------------------------------------------------------
# IESO baseline load profile (synthetic from public hourly data)
# ---------------------------------------------------------------------------

def generate_ieso_profile() -> pd.DataFrame:
    """
    Generate a 24-hour normalized baseline load profile based on
    typical Ontario demand patterns from IESO public reports.

    The shape: overnight trough (hours 2-5), morning ramp, midday plateau,
    evening peak (hours 17-20), then decline.
    """
    import numpy as np

    hours = list(range(24))
    # Normalized load fractions (0–1) based on typical Ontario daily shape
    load_fractions = [
        0.55,  # 00:00 — overnight low
        0.50,  # 01:00
        0.47,  # 02:00 — minimum
        0.45,  # 03:00
        0.45,  # 04:00
        0.48,  # 05:00 — early morning ramp begins
        0.55,  # 06:00
        0.65,  # 07:00 — morning commute
        0.75,  # 08:00
        0.82,  # 09:00 — business hours
        0.85,  # 10:00
        0.87,  # 11:00
        0.88,  # 12:00 — midday plateau
        0.87,  # 13:00
        0.86,  # 14:00
        0.85,  # 15:00
        0.88,  # 16:00 — afternoon ramp
        0.95,  # 17:00 — evening peak starts
        1.00,  # 18:00 — PEAK
        0.97,  # 19:00
        0.90,  # 20:00 — decline
        0.82,  # 21:00
        0.72,  # 22:00
        0.62,  # 23:00
    ]

    return pd.DataFrame({"hour": hours, "load_fraction": load_fractions})


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def download_fsa_boundaries():
    """Download and extract Statistics Canada FSA boundary shapefile."""
    import urllib.request

    zip_path = os.path.join(DATA_DIR, "fsa_boundaries_raw.zip")
    extract_dir = os.path.join(DATA_DIR, "_raw_fsa")

    if os.path.exists(FSA_GEOJSON_PATH):
        print(f"[OK] GTA FSA GeoJSON already exists: {FSA_GEOJSON_PATH}")
        return

    print(f"Downloading FSA boundary file from Statistics Canada...")
    print(f"  URL: {STATCAN_FSA_URL}")
    urllib.request.urlretrieve(STATCAN_FSA_URL, zip_path)
    print(f"  Downloaded: {os.path.getsize(zip_path) / 1024 / 1024:.1f} MB")

    print("Extracting...")
    os.makedirs(extract_dir, exist_ok=True)
    with zipfile.ZipFile(zip_path, "r") as z:
        z.extractall(extract_dir)

    # Find the shapefile (may be nested in subdirectories)
    shp_files = []
    for root, dirs, files in os.walk(extract_dir):
        for f in files:
            if f.endswith(".shp"):
                shp_files.append(os.path.join(root, f))
    if not shp_files:
        print("ERROR: No .shp file found in downloaded archive")
        sys.exit(1)

    shp_path = shp_files[0]
    print(f"  Loading shapefile: {os.path.basename(shp_path)}")

    # Load, filter to GTA, reproject, and save
    gdf = gpd.read_file(shp_path)
    print(f"  Total FSAs in Canada: {len(gdf)}")

    # The FSA code column is typically 'CFSAUID' in StatsCan files
    fsa_col = None
    for candidate in ["CFSAUID", "FSAUID", "FSA", "PRFSA"]:
        if candidate in gdf.columns:
            fsa_col = candidate
            break

    if fsa_col is None:
        print(f"  Available columns: {list(gdf.columns)}")
        print("ERROR: Cannot identify FSA code column")
        sys.exit(1)

    print(f"  FSA column identified: {fsa_col}")

    # Filter to GTA
    gta_mask = gdf[fsa_col].str.startswith(GTA_PREFIXES)
    gdf_gta = gdf[gta_mask].copy()
    print(f"  GTA FSAs (M/L prefix): {len(gdf_gta)}")

    # Rename to standard column name
    gdf_gta = gdf_gta.rename(columns={fsa_col: "fsa"})

    # Reproject to EPSG:4326 (WGS84)
    if gdf_gta.crs and gdf_gta.crs.to_epsg() != 4326:
        print(f"  Reprojecting from {gdf_gta.crs} to EPSG:4326")
        gdf_gta = gdf_gta.to_crs(epsg=4326)

    # Keep only fsa + geometry
    gdf_gta = gdf_gta[["fsa", "geometry"]]

    # Simplify geometry to reduce file size (tolerance in degrees ≈ ~50m)
    gdf_gta["geometry"] = gdf_gta["geometry"].simplify(0.0005)

    # Save as GeoJSON
    gdf_gta.to_file(FSA_GEOJSON_PATH, driver="GeoJSON")
    file_size = os.path.getsize(FSA_GEOJSON_PATH) / 1024 / 1024
    print(f"  [OK] Saved: {FSA_GEOJSON_PATH} ({file_size:.1f} MB)")

    # Cleanup
    import shutil
    os.remove(zip_path)
    shutil.rmtree(extract_dir)
    print("  [OK] Cleaned up temporary files")


def generate_zone_classification():
    """Generate FSA zone classification CSV by querying ArcGIS Living Atlas."""
    if os.path.exists(ZONE_CSV_PATH):
        print(f"[OK] Zone classification already exists: {ZONE_CSV_PATH}")
        # Note: Delete this file to force a re-query of the ArcGIS API
        return

    # Load the GeoJSON to get the full list of GTA FSAs and their geometries
    if not os.path.exists(FSA_GEOJSON_PATH):
        print("ERROR: Run FSA boundary download first")
        sys.exit(1)

    gdf = gpd.read_file(FSA_GEOJSON_PATH)
    
    print("Querying ArcGIS Living Atlas REST API for land use data...")
    client = ArcGisLivingAtlasClient()
    
    records = []
    
    # Calculate centroids to send as point queries to ArcGIS
    gdf_projected = gdf.to_crs(epsg=32617)
    centroids_projected = gdf_projected["geometry"].centroid
    centroids = gpd.GeoSeries(centroids_projected, crs=32617).to_crs(epsg=4326)
    
    for idx, (_, row) in enumerate(gdf.iterrows()):
        fsa = row["fsa"]
        lon = centroids.iloc[idx].x
        lat = centroids.iloc[idx].y

        # Make the HTTP call
        zone_type = client.get_zone_type(lon, lat, fsa)
        records.append({"fsa": fsa, "zone_type": zone_type})

        if (idx + 1) % 50 == 0:
            print(f"  Processed {idx + 1}/{len(gdf)} FSAs...")

    df = pd.DataFrame(records)
    df.to_csv(ZONE_CSV_PATH, index=False)

    # Summary
    counts = df["zone_type"].value_counts()
    print(f"[OK] Zone classification saved: {ZONE_CSV_PATH}")
    for zt, count in counts.items():
        print(f"    {zt}: {count} FSAs")


def generate_ieso_load():
    """Save IESO baseline load profile."""
    if os.path.exists(IESO_CSV_PATH):
        print(f"[OK] IESO load profile already exists: {IESO_CSV_PATH}")
        return

    df = generate_ieso_profile()
    df.to_csv(IESO_CSV_PATH, index=False)
    print(f"[OK] IESO load profile saved: {IESO_CSV_PATH}")


def main():
    print("=" * 60)
    print("Seneca Hack - Data Preparation Pipeline")
    print("=" * 60)
    print()

    download_fsa_boundaries()
    print()

    generate_zone_classification()
    print()

    generate_ieso_load()
    print()

    print("=" * 60)
    print("All data files ready in:", DATA_DIR)
    print("=" * 60)


if __name__ == "__main__":
    main()
