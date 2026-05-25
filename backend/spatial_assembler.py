"""
Phase 1: Spatial Assembler

Loads GTA FSA boundary polygons, joins zone classification data,
and assigns proxy grid capacity headroom per zone type.

Public API:
    load_enriched_geodataframe() -> gpd.GeoDataFrame
        Returns a GeoDataFrame with columns:
            fsa            — Forward Sortation Area code (e.g. "M5H")
            geometry       — Shapely polygon (EPSG:4326)
            zone_type      — "residential" | "leisure" | "office_park" | "retail_hub" | "transit_hub"
            proxy_capacity_kw — grid headroom ceiling in kW
            centroid_lat   — polygon centroid latitude
            centroid_lon   — polygon centroid longitude
"""

import os
import geopandas as gpd
import pandas as pd

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")

FSA_GEOJSON = os.path.join(DATA_DIR, "gta_fsa_boundaries.geojson")
ZONE_CSV = os.path.join(DATA_DIR, "fsa_zone_classification.csv")

# Proxy grid headroom by zone type (kW)
# Mirrors real-world transformer capacity differences across urban zones
CAPACITY_MAP: dict[str, int] = {
    "residential": 300,    # Fragile neighborhood transformers
    "leisure":     500,    # Parks / Monuments / Cemeteries
    "office_park": 1200,   # Corporate buildings / Institutions
    "retail_hub":  1500,   # Malls / Shopping Centers
    "transit_hub": 3000,   # Airports / Heavy transit
}

EXPECTED_CRS_EPSG = 4326


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------

def _load_fsa_boundaries() -> gpd.GeoDataFrame:
    """Load GTA FSA boundary polygons and ensure EPSG:4326."""
    if not os.path.exists(FSA_GEOJSON):
        raise FileNotFoundError(
            f"FSA boundary file not found: {FSA_GEOJSON}\n"
            "Run `python scripts/prepare_data.py` first to download the data."
        )

    gdf = gpd.read_file(FSA_GEOJSON)

    # Validate / reproject CRS
    if gdf.crs is None:
        gdf = gdf.set_crs(epsg=EXPECTED_CRS_EPSG)
    elif gdf.crs.to_epsg() != EXPECTED_CRS_EPSG:
        gdf = gdf.to_crs(epsg=EXPECTED_CRS_EPSG)

    # Drop any rows with null geometry
    null_geom_count = gdf["geometry"].isna().sum()
    if null_geom_count > 0:
        gdf = gdf.dropna(subset=["geometry"])
        gdf = gdf.reset_index(drop=True)
        print(f"Warning: dropped {null_geom_count} FSAs with null geometry")

    return gdf


def _load_zone_classification() -> pd.DataFrame:
    """Load FSA → zone_type mapping from CSV."""
    if not os.path.exists(ZONE_CSV):
        raise FileNotFoundError(
            f"Zone classification file not found: {ZONE_CSV}\n"
            "Run `python scripts/prepare_data.py` first to generate it."
        )

    df = pd.read_csv(ZONE_CSV)

    # Validate expected columns
    if "fsa" not in df.columns or "zone_type" not in df.columns:
        raise ValueError(
            f"Zone CSV must have 'fsa' and 'zone_type' columns. "
            f"Found: {list(df.columns)}"
        )

    return df


def load_enriched_geodataframe() -> gpd.GeoDataFrame:
    """
    Build the enriched GeoDataFrame for downstream phases.

    Pipeline:
        1. Load FSA boundary polygons (EPSG:4326)
        2. Join zone classification (residential / leisure / office_park / retail_hub / transit_hub)
        3. Assign proxy grid capacity headroom per zone type
        4. Compute polygon centroids for marker placement

    Returns:
        GeoDataFrame with columns:
            fsa, geometry, zone_type, proxy_capacity_kw,
            centroid_lat, centroid_lon
    """
    # Step 1: Load boundaries
    gdf = _load_fsa_boundaries()

    # Step 2: Join zone classification
    zones = _load_zone_classification()
    gdf = gdf.merge(zones, on="fsa", how="left")

    # Guard against duplicate FSAs from zone CSV
    if gdf["fsa"].duplicated().any():
        print(f"Warning: duplicate FSAs after zone merge — keeping first occurrence")
        gdf = gdf.drop_duplicates(subset="fsa", keep="first").reset_index(drop=True)

    # Any FSAs missing classification default to residential
    unclassified = gdf["zone_type"].isna().sum()
    if unclassified > 0:
        print(f"Warning: {unclassified} FSAs had no zone classification — defaulting to 'residential'")
        gdf["zone_type"] = gdf["zone_type"].fillna("residential")

    # Step 3: Assign proxy capacity
    gdf["proxy_capacity_kw"] = gdf["zone_type"].map(CAPACITY_MAP)

    # Safety check — any unmapped zone types get residential capacity
    unmapped = gdf["proxy_capacity_kw"].isna().sum()
    if unmapped > 0:
        print(f"Warning: {unmapped} FSAs had unknown zone type — using residential capacity")
        gdf["proxy_capacity_kw"] = gdf["proxy_capacity_kw"].fillna(CAPACITY_MAP["residential"])

    gdf["proxy_capacity_kw"] = gdf["proxy_capacity_kw"].astype(int)

    # Step 4: Compute centroids (for marker placement in later phases)
    # Project to UTM 17N for accurate centroid, then extract lat/lon
    gdf_projected = gdf.to_crs(epsg=32617)
    centroids_projected = gdf_projected["geometry"].centroid
    centroids_wgs84 = gpd.GeoSeries(centroids_projected.values, crs=32617).to_crs(epsg=4326)
    gdf["centroid_lat"] = centroids_wgs84.y.values
    gdf["centroid_lon"] = centroids_wgs84.x.values

    return gdf


# ---------------------------------------------------------------------------
# CLI sanity check
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("Loading enriched GeoDataFrame...")
    gdf = load_enriched_geodataframe()

    print(f"\nShape: {gdf.shape}")
    print(f"CRS: {gdf.crs}")
    print(f"\nColumns: {list(gdf.columns)}")
    print(f"\nZone type distribution:")
    print(gdf["zone_type"].value_counts().to_string())
    print(f"\nCapacity distribution:")
    print(gdf.groupby("zone_type")["proxy_capacity_kw"].first().to_string())
    print(f"\nSample rows:")
    print(gdf[["fsa", "zone_type", "proxy_capacity_kw", "centroid_lat", "centroid_lon"]].head(10).to_string())
    print(f"\nBounds:")
    bounds = gdf.total_bounds  # [minx, miny, maxx, maxy]
    print(f"  Lon: {bounds[0]:.4f} to {bounds[2]:.4f}")
    print(f"  Lat: {bounds[1]:.4f} to {bounds[3]:.4f}")
