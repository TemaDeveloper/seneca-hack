import urllib.request
import re

url = "https://www.torontohydro.com/contractors-and-developers/load-capacity-map"
req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})

try:
    html = urllib.request.urlopen(req).read().decode('utf-8', errors='ignore')
    matches = re.findall(r'https?://[^\s"\'<>]+(?:MapServer|FeatureServer)[^\s"\'<>]*', html)
    print(f"MapServer URLs found: {matches}")
    
    # Also search for standard esri iframe
    iframes = re.findall(r'<iframe[^>]*src=["\']([^"\']+)["\'][^>]*>', html)
    print(f"Iframes found: {iframes}")
except Exception as e:
    print(f"Error: {e}")
