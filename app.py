"""
EV Charging Demand & Grid Planning — Interactive Dashboard

Streamlit entry point. Run with: streamlit run app.py
"""

import sys
import os

# Add backend to path so imports work from project root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "backend"))

import streamlit as st
from streamlit_folium import st_folium

from spatial_assembler import load_enriched_geodataframe
from monte_carlo import SimulationEngine
from optimizer import optimize_placement
from map_builder import (
    build_demand_heatmap,
    build_vulnerability_map,
    build_placement_map,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
GTA_BASE_FLEET = 3_000_000  # Registered vehicles in GTA
MAX_SAMPLED_EVS = 30_000    # Cap for simulation performance

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="EV Grid Planner — GTA",
    page_icon="⚡",
    layout="wide",
)

# ---------------------------------------------------------------------------
# Cached data loading
# ---------------------------------------------------------------------------
@st.cache_data
def load_gdf():
    return load_enriched_geodataframe()

@st.cache_resource
def get_engine():
    return SimulationEngine()

# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
st.sidebar.title("Simulation Controls")

adoption_pct = st.sidebar.slider(
    "EV Adoption Rate",
    min_value=10, max_value=50, value=20, step=5,
    format="%d%%",
    help="Percentage of GTA vehicles that are electric"
)

temperature = st.sidebar.slider(
    "Temperature (C)",
    min_value=-20, max_value=30, value=20, step=5,
    help="Ambient temperature — cold winters increase charging demand"
)

time_of_day = st.sidebar.selectbox(
    "Time of Day",
    options=["Full Day", "Morning", "Evening"],
    index=0,
)

max_stations = st.sidebar.slider(
    "Max Charging Stations",
    min_value=5, max_value=20, value=10, step=1,
    help="Budget constraint for the optimizer"
)

# Compute fleet size
raw_fleet = int(GTA_BASE_FLEET * adoption_pct / 100)
num_evs = min(raw_fleet, MAX_SAMPLED_EVS)
scale_factor = raw_fleet / num_evs

st.sidebar.markdown(f"**Fleet size:** {raw_fleet:,} EVs")
st.sidebar.markdown(f"**Sampling:** {num_evs:,} simulated, results scaled {scale_factor:.1f}x")
st.sidebar.markdown("---")

run_sim = st.sidebar.button("Run Simulation", type="primary", use_container_width=True)
run_opt = st.sidebar.button(
    "Optimize Placement",
    use_container_width=True,
    disabled="grid_df" not in st.session_state,
)

# ---------------------------------------------------------------------------
# Simulation logic
# ---------------------------------------------------------------------------
engine = get_engine()
gdf = load_gdf()

if run_sim:
    with st.spinner("Running Monte Carlo simulation..."):
        ev_df = engine.run_simulation(
            num_evs=num_evs,
            time_of_day=time_of_day,
            temperature_celsius=float(temperature),
        )
        grid_df = engine.aggregate_grid_load(ev_df)

        # Scale peak_ev_load_kw by the scale factor for display accuracy
        grid_df["peak_ev_load_kw"] = (grid_df["peak_ev_load_kw"] * scale_factor).round(1)
        grid_df["total_load_kw"] = (grid_df["peak_ev_load_kw"] + grid_df["baseline_load_kw"]).round(1)
        grid_df["overloaded"] = grid_df["total_load_kw"] > grid_df["proxy_capacity_kw"]
        grid_df["deficit_kw"] = (grid_df["total_load_kw"] - grid_df["proxy_capacity_kw"]).clip(lower=0).round(1)

        st.session_state["grid_df"] = grid_df
        st.session_state["ev_count"] = raw_fleet
        # Clear old optimizer results
        st.session_state.pop("optimizer_df", None)

if run_opt and "grid_df" in st.session_state:
    with st.spinner("Running PuLP optimization solver..."):
        optimizer_df = optimize_placement(st.session_state["grid_df"], max_stations=max_stations)
        st.session_state["optimizer_df"] = optimizer_df

# ---------------------------------------------------------------------------
# Main content
# ---------------------------------------------------------------------------
st.title("EV Charging Demand & Grid Planning")
st.markdown("*Predictive Location Optimization & Grid Impact Model for the Greater Toronto Area*")

if "grid_df" not in st.session_state:
    st.info("Configure simulation parameters in the sidebar and click **Run Simulation** to begin.")
    st.stop()

grid_df = st.session_state["grid_df"]
ev_count = st.session_state["ev_count"]

# ---------------------------------------------------------------------------
# View 1: Energy Spikes
# ---------------------------------------------------------------------------
st.markdown("---")
st.header("1. Where EV Charging Demand Concentrates")

col1, col2, col3 = st.columns(3)
col1.metric("Total EVs", f"{ev_count:,}")
col2.metric("Total Peak Demand", f"{grid_df['peak_ev_load_kw'].sum() / 1000:.1f} MW")
col3.metric("Temperature", f"{temperature}°C")

m1 = build_demand_heatmap(gdf, grid_df)
st_folium(m1, use_container_width=True, height=500, returned_objects=[])

# ---------------------------------------------------------------------------
# View 2: Grid Vulnerability
# ---------------------------------------------------------------------------
st.markdown("---")
st.header("2. Where the Grid Will Fail")

overloaded_count = int(grid_df["overloaded"].sum())
total_fsas = len(grid_df)
max_deficit = grid_df["deficit_kw"].max()

col1, col2, col3 = st.columns(3)
col1.metric("Overloaded Zones", f"{overloaded_count} / {total_fsas}")
col2.metric("Max Deficit", f"{max_deficit:.0f} kW")
col3.metric("Grid Failure Rate", f"{overloaded_count / total_fsas * 100:.0f}%")

m2 = build_vulnerability_map(gdf, grid_df)
st_folium(m2, use_container_width=True, height=500, returned_objects=[])

# ---------------------------------------------------------------------------
# View 3: Optimal Placement
# ---------------------------------------------------------------------------
st.markdown("---")
st.header("3. Where to Build New Infrastructure")

if "optimizer_df" not in st.session_state:
    st.warning("Click **Optimize Placement** in the sidebar to find optimal charging station locations.")
else:
    opt_df = st.session_state["optimizer_df"]

    if opt_df.empty:
        st.success("No grid failures detected — no infrastructure needed at this adoption level.")
    else:
        col1, col2, col3 = st.columns(3)
        col1.metric("Stations Deployed", len(opt_df))
        col2.metric("Total Charger Capacity", f"{opt_df['total_charger_kw'].sum():,} kW")
        col3.metric("Total BESS", f"{opt_df['bess_kwh'].sum():,} kWh")

        m3 = build_placement_map(gdf, grid_df, opt_df)
        st_folium(m3, use_container_width=True, height=500, returned_objects=[])

        # Prescription table
        st.subheader("Prescription Details")
        st.dataframe(
            opt_df[["fsa", "zone_type", "deficit_kw", "charger_type", "charger_units",
                     "total_charger_kw", "bess_kwh"]].reset_index(drop=True),
            use_container_width=True,
            hide_index=True,
        )
