import urllib.request
import json

url = "https://services8.arcgis.com/SnGTjuDV2RIxBTxw/arcgis/rest/services/PRD_FeederLayers/FeatureServer/0?f=json"
req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
resp = urllib.request.urlopen(req)
data = json.loads(resp.read().decode('utf-8'))

print("Layer name:", data.get("name"))
print("Description:", data.get("description", ""))
print("\nFields:")
for f in data.get("fields", []):
    print(f"- {f.get('name')} ({f.get('type')}) - {f.get('alias')}")
