import os
import xml.etree.ElementTree as ET
import pandas as pd
from datetime import datetime

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
XML_FILE = os.path.join(DATA_DIR, "greenbutton_sample.xml")
OUTPUT_CSV = os.path.join(DATA_DIR, "greenbutton_profile.csv")

# ESPI XML Namespaces
NAMESPACES = {
    'atom': 'http://www.w3.org/2005/Atom',
    'espi': 'http://naesb.org/espi'
}

# ---------------------------------------------------------------------------
# Core Functions
# ---------------------------------------------------------------------------

def create_mock_xml():
    """Generates a realistic ESPI Green Button XML file for testing."""
    if os.path.exists(XML_FILE):
        return
        
    print(f"Generating mock Green Button XML at {XML_FILE}...")
    
    # Base load curve (Wh per hour) peaking in the evening
    hourly_wh = [
        300, 280, 250, 250, 280, 400, 800, 1200, 1000, 900, 
        850, 850, 900, 950, 1000, 1100, 1500, 2200, 2500, 2400, 
        2000, 1500, 800, 400
    ]
    
    # Jan 1 2024, 00:00:00 UTC
    start_time = 1704067200
    
    xml_content = ['<?xml version="1.0" encoding="UTF-8"?>']
    xml_content.append('<feed xmlns="http://www.w3.org/2005/Atom" xmlns:espi="http://naesb.org/espi">')
    xml_content.append('  <entry>')
    xml_content.append('    <content>')
    xml_content.append('      <espi:IntervalBlock>')
    xml_content.append('        <espi:interval>')
    xml_content.append('          <espi:duration>86400</espi:duration>')
    xml_content.append(f'          <espi:start>{start_time}</espi:start>')
    xml_content.append('        </espi:interval>')
    
    for i, wh in enumerate(hourly_wh):
        ts = start_time + (i * 3600)
        xml_content.append('        <espi:IntervalReading>')
        xml_content.append('          <espi:timePeriod>')
        xml_content.append('            <espi:duration>3600</espi:duration>')
        xml_content.append(f'            <espi:start>{ts}</espi:start>')
        xml_content.append('          </espi:timePeriod>')
        xml_content.append(f'          <espi:value>{wh}</espi:value>')
        xml_content.append('        </espi:IntervalReading>')
        
    xml_content.append('      </espi:IntervalBlock>')
    xml_content.append('    </content>')
    xml_content.append('  </entry>')
    xml_content.append('</feed>')
    
    with open(XML_FILE, "w") as f:
        f.write("\n".join(xml_content))

def parse_greenbutton_xml():
    """Parses ESPI XML to extract an hourly load profile."""
    if not os.path.exists(XML_FILE):
        print(f"[ERROR] XML file not found: {XML_FILE}")
        return

    print(f"Parsing Green Button data from {XML_FILE}...")
    
    try:
        tree = ET.parse(XML_FILE)
        root = tree.getroot()
    except Exception as e:
        print(f"[ERROR] Failed to parse XML: {e}")
        return

    readings = []
    
    # Find all IntervalReading elements
    # Using the namespace map to find espi tags
    for block in root.findall('.//espi:IntervalBlock', NAMESPACES):
        for reading in block.findall('espi:IntervalReading', NAMESPACES):
            start_elem = reading.find('.//espi:start', NAMESPACES)
            value_elem = reading.find('espi:value', NAMESPACES)
            
            if start_elem is not None and value_elem is not None:
                timestamp = int(start_elem.text)
                value = float(value_elem.text)
                
                # Convert timestamp to hour of day
                dt = datetime.utcfromtimestamp(timestamp)
                readings.append({"hour": dt.hour, "usage_wh": value})
                
    if not readings:
        print("[ERROR] No IntervalReading tags found in the XML. Check the ESPI schema.")
        return
        
    df = pd.DataFrame(readings)
    
    # Average the usage by hour of day (in case the XML has multiple days)
    hourly_profile = df.groupby("hour")["usage_wh"].mean().reset_index()
    
    # Convert absolute usage to a load fraction (0.0 to 1.0)
    max_usage = hourly_profile["usage_wh"].max()
    hourly_profile["load_fraction"] = (hourly_profile["usage_wh"] / max_usage).round(4)
    
    # Save the normalized profile
    output_df = hourly_profile[["hour", "load_fraction"]]
    output_df.to_csv(OUTPUT_CSV, index=False)
    
    print("\n--- EXTRACTED GREEN BUTTON LOAD PROFILE ---")
    print(output_df.to_string(index=False))
    print(f"\n[OK] Successfully saved household profile to {OUTPUT_CSV}")


if __name__ == "__main__":
    create_mock_xml()
    parse_greenbutton_xml()
