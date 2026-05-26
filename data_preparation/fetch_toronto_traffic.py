import os
import sys
import json
import pandas as pd
import geopandas as gpd

# Add backend to path so we can import the spatial assembler
sys.path.append(os.path.join(os.path.dirname(__file__), "..", "backend"))
from spatial_assembler import load_enriched_geodataframe

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "backend", "data")
OUTPUT_JSON = os.path.join(DATA_DIR, "zone_weights.json")
OUTPUT_FSA_CSV = os.path.join(DATA_DIR, "toronto_traffic_fsa_counts.csv")

# Toronto Open Data CSV Download URL
DATASET_URL = "https://ckan0.cf.opendata.inter.prod-toronto.ca/datastore/dump/6afa3b1f-f6a5-4235-8bd6-7568411c19f4"

def fetch_and_calculate_zone_weights():
    print("============================================================")
    print("Toronto Open Data: Dynamic Zone Weights Calculator")
    print("============================================================")
    
    # 1. Load the fully enriched map (which contains the 'zone_type' column)
    print("Loading Master Map Database (with zone classifications)...")
    base_gdf = load_enriched_geodataframe()
    if base_gdf.crs.to_epsg() != 4326:
        base_gdf = base_gdf.to_crs(epsg=4326)
    
    # 2. Load the raw traffic data
    print(f"Downloading traffic dataset from Toronto Open Data...")
    traffic_df = pd.read_csv(DATASET_URL)
    traffic_df = traffic_df.dropna(subset=['longitude', 'latitude', 'am_peak_vehicle', 'pm_peak_vehicle'])
    
    # 3. Convert to GeoDataFrame
    print("Converting intersections to geospatial points...")
    geometry = gpd.points_from_xy(traffic_df['longitude'], traffic_df['latitude'])
    intersections_gdf = gpd.GeoDataFrame(traffic_df, geometry=geometry, crs="EPSG:4326")
    
    # 4. Perform Spatial Join (Which postal code / zone does each intersection fall into?)
    print("Performing Spatial Join...")
    joined_gdf = gpd.sjoin(intersections_gdf, base_gdf, how="inner", predicate="within")
    
    # 5. Aggregate Volumes by ZONE TYPE
    print("Calculating exact traffic volumes for each zone type...")
    fsa_counts = joined_gdf.groupby(["fsa", "zone_type"], as_index=False).agg(
        am_peak_vehicle=("am_peak_vehicle", "sum"),
        pm_peak_vehicle=("pm_peak_vehicle", "sum"),
        total_vehicle=("total_vehicle", "sum"),
        count_points=("_id", "count"),
    )
    fsa_counts["source"] = "toronto_open_data_intersection_counts"
    fsa_counts.to_csv(OUTPUT_FSA_CSV, index=False)

    zone_volumes = joined_gdf.groupby("zone_type").agg({
        "am_peak_vehicle": "sum",
        "pm_peak_vehicle": "sum"
    }).reset_index()
    
    # 6. Ensure all 5 zone types exist (even if they had 0 intersections in Toronto)
    required_zones = ["residential", "office_park", "leisure", "retail_hub", "transit_hub"]
    for zone in required_zones:
        if zone not in zone_volumes["zone_type"].values:
            zone_volumes = pd.concat([zone_volumes, pd.DataFrame({"zone_type": [zone], "am_peak_vehicle": [0], "pm_peak_vehicle": [0]})], ignore_index=True)
            
    # Add a small baseline (smoothing) so no zone ever has exactly 0 probability (causes divide by zero errors)
    zone_volumes["am_peak_vehicle"] += 1000
    zone_volumes["pm_peak_vehicle"] += 1000
    
    # 7. Convert raw counts to Probability Percentages
    print("Generating JSON weights...")
    total_am = zone_volumes["am_peak_vehicle"].sum()
    total_pm = zone_volumes["pm_peak_vehicle"].sum()
    
    zone_volumes["morning_weight"] = zone_volumes["am_peak_vehicle"] / total_am
    zone_volumes["evening_weight"] = zone_volumes["pm_peak_vehicle"] / total_pm
    
    # 8. Format as the TIME_WEIGHTS JSON structure
    weights_dict = {
        "Morning": {},
        "Evening": {}
    }
    
    for _, row in zone_volumes.iterrows():
        weights_dict["Morning"][row["zone_type"]] = round(row["morning_weight"], 4)
        weights_dict["Evening"][row["zone_type"]] = round(row["evening_weight"], 4)
        
    # Print the math to the terminal for verification
    print("\n--- CALCULATED ZONE WEIGHTS (PROBABILITIES) ---")
    print(json.dumps(weights_dict, indent=4))
    print("-----------------------------------------------\n")
    
    # Save to file
    with open(OUTPUT_JSON, "w") as f:
        json.dump(weights_dict, f, indent=4)
        
    print(f"[OK] Successfully saved mathematical zone weights to {OUTPUT_JSON}")
    print(f"[OK] Successfully saved FSA traffic counts to {OUTPUT_FSA_CSV}")
    print("============================================================")

if __name__ == "__main__":
    fetch_and_calculate_zone_weights()
