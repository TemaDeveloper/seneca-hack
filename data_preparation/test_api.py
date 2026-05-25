import requests
import json

url = 'https://geocode.arcgis.com/arcgis/rest/services/World/GeocodeServer/reverseGeocode'
params = {
    'location': '{"x": -79.383, "y": 43.65}',
    'f': 'json',
    'featureTypes': 'POI,StreetInt,Postal'
}
res = requests.get(url, params=params).json()
print(json.dumps(res, indent=2))
