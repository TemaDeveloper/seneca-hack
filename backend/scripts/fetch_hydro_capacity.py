import os
import json
import requests

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
OUTPUT_FILE = os.path.join(DATA_DIR, "toronto_hydro_feeders.geojson")

BASE_URL = "https://services8.arcgis.com/SnGTjuDV2RIxBTxw/arcgis/rest/services/PRD_FeederLayers/FeatureServer/0/query"

def fetch_feeders():
    print("============================================================")
    print("Toronto Hydro API: Fetching Live Capacity Polygons")
    print("============================================================")
    
    offset = 0
    record_count = 100
    all_features = []
    
    print("Beginning chunked download of 830 features to prevent timeouts...")
    session = requests.Session()
    
    while True:
        query_params = {
            "where": "1=1",
            "outFields": "*",
            "f": "geojson",
            "outSR": "4326",
            "resultOffset": offset,
            "resultRecordCount": record_count,
            "maxAllowableOffset": 0.001
        }
        
        print(f"Requesting offset {offset}...")
        
        try:
            resp = session.get(BASE_URL, params=query_params, timeout=30)
            resp.raise_for_status()
            json_data = resp.json()
            
            features = json_data.get('features', [])
            if not features:
                break
                
            all_features.extend(features)
            print(f"  -> Got {len(features)} features. Total so far: {len(all_features)}")
            
            if len(features) < record_count:
                break
                
            offset += record_count
            
        except Exception as e:
            print(f"[ERROR] Failed to fetch data from ArcGIS API at offset {offset}: {e}")
            break

    if not all_features:
        print("[ERROR] No real features downloaded. Check network connection.")
        return

    print(f"Successfully downloaded {len(all_features)} REAL Toronto Hydro feeder polygons.")
    
    final_geojson = {
        "type": "FeatureCollection",
        "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}},
        "features": all_features
    }
    
    with open(OUTPUT_FILE, "w", encoding='utf-8') as f:
        json.dump(final_geojson, f, ensure_ascii=False)
        
    print(f"[OK] Saved to {OUTPUT_FILE}")

if __name__ == "__main__":
    fetch_feeders()
