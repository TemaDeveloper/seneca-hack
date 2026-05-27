import os
import requests
import geopandas as gpd
import pandas as pd
from shapely.geometry import Point

def main():
    # 1. Define paths
    base_dir = os.path.dirname(os.path.dirname(__file__))
    fsa_path = os.path.join(base_dir, "backend", "data", "gta_fsa_boundaries.geojson")
    output_path = os.path.join(base_dir, "frontend", "public", "gta_pois.json")
    
    print(f"Loading FSA boundaries from {fsa_path}...")
    if not os.path.exists(fsa_path):
        raise FileNotFoundError(f"FSA boundary file not found at {fsa_path}")
        
    fsa_gdf = gpd.read_file(fsa_path)
    # Ensure CRS is EPSG:4326
    if fsa_gdf.crs is None:
        fsa_gdf = fsa_gdf.set_crs(epsg=4326)
    elif fsa_gdf.crs.to_epsg() != 4326:
        fsa_gdf = fsa_gdf.to_crs(epsg=4326)
        
    # 2. Query Overpass API
    # Bounding box covering the Greater Toronto Area
    # (min_lat, min_lon, max_lat, max_lon)
    bbox = "43.4,-80.0,44.2,-78.8"
    
    overpass_url = "https://overpass-api.de/api/interpreter"
    overpass_query = f"""
    [out:json][timeout:120];
    (
      node["amenity"="hospital"]["name"]({bbox});
      node["amenity"="school"]["name"]({bbox});
      node["leisure"="fitness_centre"]["name"]({bbox});
      node["office"]["name"]({bbox});
      node["amenity"="charging_station"]({bbox});
    );
    out body;
    """
    
    print("Fetching POI data from OpenStreetMap Overpass API (this may take a few seconds)...")
    headers = {
        "User-Agent": "SenecaHackEVPlanner/1.0 (artemiifrid@gmail.com)",
        "Accept": "application/json"
    }
    try:
        response = requests.post(overpass_url, data={"data": overpass_query}, headers=headers, timeout=120)
        response.raise_for_status()
        data = response.json()
    except Exception as e:
        print(f"Error querying Overpass API: {e}")
        return
        
    elements = data.get("elements", [])
    print(f"Downloaded {len(elements)} raw elements from OSM.")
    
    # 3. Parse elements
    poi_list = []
    for el in elements:
        lat = el.get("lat")
        lon = el.get("lon")
        if lat is None or lon is None:
            continue
            
        tags = el.get("tags", {})
        name = tags.get("name")
        
        # Determine POI type
        poi_type = None
        icon = None
        
        if "amenity" in tags and tags["amenity"] == "hospital":
            poi_type = "hospitals"
            icon = "🏥"
        elif "amenity" in tags and tags["amenity"] == "school":
            poi_type = "schools"
            icon = "🏫"
        elif "leisure" in tags and tags["leisure"] == "fitness_centre":
            poi_type = "gyms"
            icon = "🏋️"
        elif "office" in tags or tags.get("building") == "office":
            poi_type = "workplaces"
            icon = "💼"
        elif "amenity" in tags and tags["amenity"] == "charging_station":
            poi_type = "chargers"
            icon = "⚡"
            if not name:
                operator = tags.get("operator", "Public EV Charger")
                network = tags.get("network", "")
                name = f"{operator} {network}".strip() or "EV Charging Station"
                
        if not poi_type or not name:
            continue
            
        poi_list.append({
            "id": f"node/{el['id']}",
            "type": poi_type,
            "icon": icon,
            "name": name,
            "lat": float(lat),
            "lon": float(lon),
            "geometry": Point(lon, lat)
        })
        
    if not poi_list:
        print("No matching POIs found.")
        return
        
    print(f"Parsed {len(poi_list)} valid POIs. Performing spatial join...")
    
    # 4. Create GeoDataFrame for POIs
    poi_gdf = gpd.GeoDataFrame(poi_list, crs="EPSG:4326")
    
    # 5. Spatial Join: Which FSA contains each POI?
    joined = gpd.sjoin(poi_gdf, fsa_gdf[["fsa", "geometry"]], how="inner", predicate="within")
    
    print(f"Matched {len(joined)} POIs to GTA FSA boundaries.")
    
    # 6. Format and save to JSON
    output_df = joined[["id", "type", "icon", "name", "lat", "lon", "fsa"]]
    
    # Cap categories to prevent UI lag while maintaining realistic density
    dfs = []
    for t in ["hospitals", "schools", "gyms", "workplaces", "chargers"]:
        sub = output_df[output_df["type"] == t]
        if len(sub) > 300:
            sub = sub.sample(n=300, random_state=42)
        dfs.append(sub)
    output_df = pd.concat(dfs)
    
    print(f"Saving {len(output_df)} filtered POIs to {output_path}...")
    output_df.to_json(output_path, orient="records", indent=2)
    print("Done!")

if __name__ == "__main__":
    main()
