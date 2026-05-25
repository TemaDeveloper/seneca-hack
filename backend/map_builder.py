"""
Phase 4: Map Builder

Builds Folium map objects for the three frontend views.
Pure functions — no Streamlit imports.

Public API:
    build_demand_heatmap(gdf, grid_df) -> folium.Map
    build_vulnerability_map(gdf, grid_df) -> folium.Map
    build_placement_map(gdf, grid_df, optimizer_df) -> folium.Map
"""

import folium
import branca.colormap as cm
import geopandas as gpd
import pandas as pd

# ---------------------------------------------------------------------------
# Map defaults
# ---------------------------------------------------------------------------
GTA_CENTER = [43.7, -79.4]
DEFAULT_ZOOM = 9
TILE_LAYER = "CartoDB dark_matter"


def _merge_grid_to_geo(gdf: gpd.GeoDataFrame, grid_df: pd.DataFrame) -> gpd.GeoDataFrame:
    """Merge simulation results onto the GeoDataFrame for rendering."""
    merged = gdf[["fsa", "geometry"]].merge(grid_df, on="fsa", how="left")
    return gpd.GeoDataFrame(merged, geometry="geometry")


def build_demand_heatmap(gdf: gpd.GeoDataFrame, grid_df: pd.DataFrame) -> folium.Map:
    """
    View 1: Energy Spikes — yellow-to-red choropleth by peak EV load.
    """
    m = folium.Map(location=GTA_CENTER, zoom_start=DEFAULT_ZOOM, tiles=TILE_LAYER)
    merged = _merge_grid_to_geo(gdf, grid_df)

    max_load = merged["peak_ev_load_kw"].max()
    if max_load == 0:
        max_load = 1

    colormap = cm.LinearColormap(
        colors=["#FFFF00", "#FF8C00", "#FF0000"],
        vmin=0,
        vmax=max_load,
        caption="Peak EV Charging Load (kW)"
    )

    def style_fn(feature):
        load = feature["properties"].get("peak_ev_load_kw", 0)
        return {
            "fillColor": colormap(load) if load else "#333333",
            "color": "#555555",
            "weight": 0.5,
            "fillOpacity": 0.7,
        }

    folium.GeoJson(
        merged.to_json(),
        style_function=style_fn,
        tooltip=folium.GeoJsonTooltip(
            fields=["fsa", "zone_type", "peak_ev_load_kw"],
            aliases=["FSA:", "Zone:", "Peak EV Load (kW):"],
            sticky=True,
        ),
    ).add_to(m)

    colormap.add_to(m)
    return m


def build_vulnerability_map(gdf: gpd.GeoDataFrame, grid_df: pd.DataFrame) -> folium.Map:
    """
    View 2: Grid Vulnerability — binary green (safe) / red (overloaded).
    """
    m = folium.Map(location=GTA_CENTER, zoom_start=DEFAULT_ZOOM, tiles=TILE_LAYER)
    merged = _merge_grid_to_geo(gdf, grid_df)

    def style_fn(feature):
        overloaded = feature["properties"].get("overloaded", False)
        return {
            "fillColor": "#DC143C" if overloaded else "#2ECC40",
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

    return m


def build_placement_map(
    gdf: gpd.GeoDataFrame,
    grid_df: pd.DataFrame,
    optimizer_df: pd.DataFrame,
) -> folium.Map:
    """
    View 3: Optimal Placement — green/red base + purple marker pins with prescription popups.
    """
    m = build_vulnerability_map(gdf, grid_df)

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
