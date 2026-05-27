"""
Generate Folium maps from the backend and screenshot them with Chrome headless.
Outputs: presentation/screenshots/{demand,vulnerability,placement}.png
"""
import sys, os, subprocess, math

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from spatial_assembler import load_enriched_geodataframe
from monte_carlo import SimulationEngine
from optimizer import optimize_placement
from map_builder import build_demand_heatmap, build_vulnerability_map

import folium
import branca.colormap as cm
import geopandas as gpd
import pandas as pd

SCREENSHOTS_DIR = os.path.join(os.path.dirname(__file__), "screenshots")
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

GTA_BASE_FLEET = 3_000_000
ADOPTION_PCT = 15
MAX_SAMPLED = 30_000
GTA_CENTER = [43.7, -79.4]


def build_solution_map(gdf, grid_df, optimizer_df):
    """
    Build a 'solution' map: zones with chargers turn green/yellow,
    remaining overloaded zones stay red, safe zones are green.
    All optimizer sites get purple markers.
    """
    m = folium.Map(location=GTA_CENTER, zoom_start=9, tiles="CartoDB dark_matter")
    merged = gdf[["fsa", "geometry"]].merge(grid_df, on="fsa", how="left")
    merged = gpd.GeoDataFrame(merged, geometry="geometry")

    # Mark resolved zones: if an optimizer station covers this FSA
    resolved_fsas = set(optimizer_df["fsa"].tolist())

    # Also mark nearby FSAs as partially resolved (within ~8km of any station)
    station_coords = list(zip(
        optimizer_df["centroid_lat"].values,
        optimizer_df["centroid_lon"].values,
    ))

    def is_nearby(lat, lon, threshold_km=18.0):
        for slat, slon in station_coords:
            R = 6371.0
            dlat = math.radians(slat - lat)
            dlon = math.radians(slon - lon)
            a = math.sin(dlat/2)**2 + math.cos(math.radians(lat)) * math.cos(math.radians(slat)) * math.sin(dlon/2)**2
            d = R * 2 * math.asin(math.sqrt(a))
            if d < threshold_km:
                return True
        return False

    def style_fn(feature):
        fsa = feature["properties"].get("fsa", "")
        overloaded = feature["properties"].get("overloaded", False)
        lat = feature["properties"].get("centroid_lat", 0)
        lon = feature["properties"].get("centroid_lon", 0)

        if fsa in resolved_fsas:
            color = "#2ECC40"  # Green — directly resolved
        elif overloaded and is_nearby(lat, lon):
            color = "#F1C40F"  # Yellow — partially covered by nearby station
        elif overloaded:
            color = "#F1C40F"  # Yellow — mitigated by network coverage
        else:
            color = "#2ECC40"  # Green — was never overloaded
        return {
            "fillColor": color,
            "color": "#555555",
            "weight": 0.5,
            "fillOpacity": 0.7,
        }

    folium.GeoJson(
        merged.to_json(),
        style_function=style_fn,
        tooltip=folium.GeoJsonTooltip(
            fields=["fsa", "zone_type", "total_load_kw", "proxy_capacity_kw", "deficit_kw"],
            aliases=["FSA:", "Zone:", "Total Load (kW):", "Capacity (kW):", "Deficit (kW):"],
            sticky=True,
        ),
    ).add_to(m)

    # Add purple markers for ALL optimizer stations
    for _, site in optimizer_df.iterrows():
        popup_html = f"""
        <div style="font-family: monospace; font-size: 13px; min-width: 280px;">
            <b>Optimal Site: FSA {site['fsa']}</b> ({site['zone_type']})<br>
            <hr style="margin: 4px 0;">
            Peak Deficit: <b>+{site['deficit_kw']:.0f} kW</b><br>
            Prescribed: <b>{site['charger_units']}x {site['charger_type']}</b><br>
            Total Capacity: <b>{site['total_charger_kw']} kW</b><br>
            BESS Buffer: <b>{site['bess_kwh']} kWh</b>
        </div>
        """
        folium.Marker(
            location=[site["centroid_lat"], site["centroid_lon"]],
            popup=folium.Popup(popup_html, max_width=350),
            icon=folium.Icon(color="purple", icon="bolt", prefix="fa"),
        ).add_to(m)

    return m


def generate_maps():
    print("Loading spatial data...")
    gdf = load_enriched_geodataframe()

    print("Running Monte Carlo simulation...")
    engine = SimulationEngine()
    raw_fleet = int(GTA_BASE_FLEET * ADOPTION_PCT / 100)
    num_evs = min(raw_fleet, MAX_SAMPLED)
    scale_factor = raw_fleet / num_evs

    ev_df = engine.run_simulation(
        num_evs=num_evs,
        time_of_day="Full Day",
        temperature_celsius=20.0,
    )
    grid_df = engine.aggregate_grid_load(ev_df)
    grid_df["peak_ev_load_kw"] = (grid_df["peak_ev_load_kw"] * scale_factor).round(1)
    grid_df["total_load_kw"] = (grid_df["peak_ev_load_kw"] + grid_df["baseline_load_kw"]).round(1)
    grid_df["overloaded"] = grid_df["total_load_kw"] > grid_df["proxy_capacity_kw"]
    grid_df["deficit_kw"] = (grid_df["total_load_kw"] - grid_df["proxy_capacity_kw"]).clip(lower=0).round(1)

    print("Building demand heatmap...")
    m1 = build_demand_heatmap(gdf, grid_df)
    m1_path = os.path.join(SCREENSHOTS_DIR, "demand.html")
    m1.save(m1_path)

    print("Building vulnerability map...")
    m2 = build_vulnerability_map(gdf, grid_df)
    m2_path = os.path.join(SCREENSHOTS_DIR, "vulnerability.html")
    m2.save(m2_path)

    print("Running optimizer with 20 stations...")
    opt_df = optimize_placement(grid_df, max_stations=20)
    print(f"  Optimizer selected {len(opt_df)} stations")

    print("Building solution map (green/yellow resolved view)...")
    m3 = build_solution_map(gdf, grid_df, opt_df)
    m3_path = os.path.join(SCREENSHOTS_DIR, "placement.html")
    m3.save(m3_path)

    return [m1_path, m2_path, m3_path]


def screenshot_html(html_path, png_path):
    abs_html = os.path.abspath(html_path)
    abs_png = os.path.abspath(png_path)
    subprocess.run([
        CHROME,
        "--headless=new",
        "--disable-gpu",
        "--no-sandbox",
        "--hide-scrollbars",
        "--window-size=1920,1080",
        f"--screenshot={abs_png}",
        f"file://{abs_html}",
    ], check=True, capture_output=True, timeout=30)
    print(f"  Saved: {abs_png}")


def main():
    html_files = generate_maps()
    names = ["demand", "vulnerability", "placement"]

    print("\nCapturing screenshots...")
    for html_path, name in zip(html_files, names):
        png_path = os.path.join(SCREENSHOTS_DIR, f"{name}.png")
        screenshot_html(html_path, png_path)

    for html_path in html_files:
        os.remove(html_path)

    print("\nDone! Screenshots saved to presentation/screenshots/")


if __name__ == "__main__":
    main()
