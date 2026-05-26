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
import re
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

    unmapped = gdf["proxy_capacity_kw"].isna().sum()
    if unmapped > 0:
        print(f"Warning: {unmapped} FSAs had unknown zone type — using residential capacity")
        gdf["proxy_capacity_kw"] = gdf["proxy_capacity_kw"].fillna(CAPACITY_MAP["residential"])

    gdf["proxy_capacity_kw"] = gdf["proxy_capacity_kw"].astype(int)

    # Step 3.5: Overlay REAL Toronto Hydro data if available
    hydro_geojson = os.path.join(DATA_DIR, "toronto_hydro_feeders.geojson")
    if os.path.exists(hydro_geojson):
        print("Overlaying real Toronto Hydro ArcGIS Feeder Capacity data...")
        try:
            feeders_gdf = gpd.read_file(hydro_geojson)
            if feeders_gdf.crs.to_epsg() != 4326:
                feeders_gdf = feeders_gdf.to_crs(epsg=4326)
            
            # Clean the Feeder_Capacity string (e.g., if it has units or commas)
            def extract_kw(val):
                if pd.isna(val): return None
                val_str = str(val).lower().replace(",", "")
                # Find all numbers (including decimals)
                nums = re.findall(r'\d+(?:\.\d+)?', val_str)
                if not nums: return None
                
                # If there's a range like "0-499", take the upper bound (the last number)
                num = float(nums[-1])
                
                if 'mva' in val_str or 'mw' in val_str:
                    return num * 1000
                return num
                    
            feeders_gdf["real_capacity_kw"] = feeders_gdf["Feeder_Capacity"].apply(extract_kw)
            feeders_gdf = feeders_gdf.dropna(subset=["real_capacity_kw"])
            
            # Spatial join: Which feeders overlap which FSAs?
            # We use 'intersects' because feeders span across borders
            joined = gpd.sjoin(gdf, feeders_gdf, how="left", predicate="intersects")
            
            # For each FSA, find the minimum overlapping feeder capacity (the weakest link)
            real_caps = joined.groupby("fsa")["real_capacity_kw"].min()
            
            # Only apply real capacities to FSAs inside Toronto (typically M-prefix, though some L might overlap)
            mask = real_caps.notna() & (real_caps > 0)
            
            # Update the capacity
            gdf.loc[gdf["fsa"].isin(real_caps[mask].index), "proxy_capacity_kw"] = real_caps[mask].values
            
            # Cast back to int
            gdf["proxy_capacity_kw"] = gdf["proxy_capacity_kw"].astype(int)
            print(f"Successfully applied real Toronto Hydro capacity limits to {mask.sum()} FSAs.")
        except Exception as e:
            print(f"[WARNING] Failed to process real Toronto Hydro data: {e}")

    # Step 3.6: Overlay Hydro One station-level capacity for L-prefix FSAs
    # Source: Hydro One Needs Assessment Reports (GTA West 2024, GTA East 2024)
    # Station LTR (MW) assigned to nearest FSAs via closest-station matching
    station_csv = os.path.join(DATA_DIR, "hydro_one_stations.csv")
    if os.path.exists(station_csv):
        print("Overlaying Hydro One station capacity data for suburban GTA...")
        try:
            stations = pd.read_csv(station_csv)
            station_points = gpd.GeoDataFrame(
                stations,
                geometry=gpd.points_from_xy(stations["lon"], stations["lat"]),
                crs="EPSG:4326"
            )

            # Only apply to FSAs that still have proxy capacity (not already set by Toronto Hydro)
            # Identify FSAs with proxy defaults
            proxy_fsas = gdf[gdf["proxy_capacity_kw"].isin(CAPACITY_MAP.values())].copy()

            if not proxy_fsas.empty:
                # Project to UTM for accurate distance calculation
                proxy_utm = proxy_fsas.to_crs(epsg=32617)
                proxy_centroids = proxy_utm.geometry.centroid
                stations_utm = station_points.to_crs(epsg=32617)

                # For each proxy FSA, find the nearest station
                from shapely.ops import nearest_points

                nearest_ltr = []
                for idx, centroid in enumerate(proxy_centroids):
                    min_dist = float("inf")
                    best_ltr = None
                    for _, st in stations_utm.iterrows():
                        dist = centroid.distance(st.geometry)
                        if dist < min_dist:
                            min_dist = dist
                            best_ltr = st["ltr_mw"]
                    nearest_ltr.append(best_ltr)

                # Convert station MW to per-FSA kW estimate
                # Each station serves ~10-20 FSAs, so divide by estimated service count
                # and convert MW to kW
                proxy_fsas = proxy_fsas.copy()
                proxy_fsas["station_ltr_mw"] = nearest_ltr

                # Count how many proxy FSAs each station serves (for fair division)
                # Group by nearest station LTR to estimate share
                station_fsa_counts = pd.Series(nearest_ltr).value_counts()

                per_fsa_kw = []
                for ltr in nearest_ltr:
                    n_fsas = station_fsa_counts[ltr]
                    # Station LTR in MW / number of FSAs it serves * 1000 = kW per FSA
                    # But station capacity is shared with existing load (~70% used)
                    # Available headroom ≈ 30% of station capacity
                    headroom_fraction = 0.30
                    kw = (ltr * headroom_fraction * 1000) / n_fsas
                    per_fsa_kw.append(min(max(int(kw), 100), 5000))  # Floor 100 kW, cap 5000 kW

                # Apply via map to avoid index alignment issues
                fsa_to_cap = dict(zip(proxy_fsas["fsa"].values, per_fsa_kw))
                mapped = gdf["fsa"].map(fsa_to_cap)
                gdf["proxy_capacity_kw"] = mapped.fillna(gdf["proxy_capacity_kw"]).astype(int)

                print(f"Applied Hydro One station capacity to {len(proxy_fsas)} suburban FSAs.")
        except Exception as e:
            print(f"[WARNING] Failed to process Hydro One station data: {e}")

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
    print(gdf[["fsa", "zone_type", "proxy_capacity_kw", "centroid_lat", "centroid_lon"]].to_string())
    print(f"\nBounds:")
    bounds = gdf.total_bounds  # [minx, miny, maxx, maxy]
    print(f"  Lon: {bounds[0]:.4f} to {bounds[2]:.4f}")
    print(f"  Lat: {bounds[1]:.4f} to {bounds[3]:.4f}")
