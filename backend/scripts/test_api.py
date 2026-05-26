import urllib.request
import json

url = "https://services8.arcgis.com/SnGTjuDV2RIxBTxw/arcgis/rest/services/PRD_FeederLayers/FeatureServer/0/query?where=1=1&outFields=*&f=geojson&outSR=4326&resultRecordCount=50"
req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
try:
    resp = urllib.request.urlopen(req, timeout=30)
    data = json.loads(resp.read().decode('utf-8'))
    features = data.get('features', [])
    print(f"Success! Downloaded {len(features)} features.")
    if features:
        print("Sample Feeder_Capacity:", features[0]['properties'].get('Feeder_Capacity'))
except Exception as e:
    print(f"Error: {e}")
