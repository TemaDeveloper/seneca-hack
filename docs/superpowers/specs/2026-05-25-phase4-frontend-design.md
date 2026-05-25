# Phase 4: Streamlit Frontend — Design Spec

## Goal

Build an interactive Streamlit web app that visualizes the EV charging demand simulation, grid vulnerability analysis, and optimal charger placement across the GTA. Three progressive map views tell a story: problem → conflict → solution.

## Files

| File | Responsibility |
|------|---------------|
| `app.py` (project root) | Streamlit entry point — sidebar controls, session state, layout, renders maps |
| `backend/map_builder.py` | Builds 3 Folium map objects from data. Pure functions, no Streamlit imports |
| `.streamlit/config.toml` | Dark theme, teal accent, wide layout |

## Sidebar Controls

| Widget | Type | Range / Options | Drives |
|--------|------|-----------------|--------|
| EV Adoption Rate | Slider | 10%–50%, step 5% | Fleet size = `GTA_BASE_FLEET * pct`. GTA_BASE_FLEET = 3,000,000. Capped at 30,000 sampled EVs for performance. |
| Temperature (C) | Slider | -20 to +30, step 5, default 20 | `temperature_celsius` param in `run_simulation` |
| Time of Day | Selectbox | Morning / Evening / Full Day | `time_of_day` param in `run_simulation` |
| Max Charging Stations | Slider | 5–20, default 10 | `max_stations` param in `optimize_placement` |
| Run Simulation | Button | — | Runs Phase 2, stores results in `st.session_state` |
| Optimize Placement | Button | — | Runs Phase 3. Disabled until simulation has run. |

## Map Views

### View 1: Energy Spikes (The Problem)

- **Layer:** Folium `Choropleth` or `GeoJson` with `style_function`
- **Color:** Yellow → orange → red gradient, scaled by `peak_ev_load_kw`
- **Tooltip:** FSA code, zone type, peak EV load (kW)
- **Section header:** "Where EV Charging Demand Concentrates"
- **Metrics row above map:** Total EVs simulated, total peak demand (MW), peak hour

### View 2: Grid Vulnerability (The Conflict)

- **Layer:** Folium `GeoJson` with conditional `style_function`
- **Color:** Green fill (`overloaded == False`) vs red fill (`overloaded == True`)
- **Tooltip:** FSA, zone type, total load kW, capacity kW, deficit kW
- **Section header:** "Where the Grid Will Fail"
- **Metrics row:** Overloaded count / total FSAs, max deficit kW

### View 3: Optimal Placement (The Solution)

- **Base layer:** Same green/red polygons as View 2
- **Overlay:** Purple `folium.Marker` pins at optimized station centroids
- **Popup:** HTML prescription card:
  ```
  Optimal Site: FSA {fsa} ({zone_type})
  Peak Deficit: +{deficit_kw} kW
  Prescribed: {units}x {charger_type} ({total_kw} kW)
  BESS Buffer: {bess_kwh} kWh
  ```
- **Section header:** "Where to Build New Infrastructure"
- **Metrics row:** Stations deployed, total charger capacity (kW), total BESS (kWh)

## `backend/map_builder.py` API

Three pure functions, each returning a `folium.Map` object:

```python
def build_demand_heatmap(gdf, grid_df) -> folium.Map
def build_vulnerability_map(gdf, grid_df) -> folium.Map
def build_placement_map(gdf, grid_df, optimizer_df) -> folium.Map
```

- `gdf`: enriched GeoDataFrame from `spatial_assembler` (has `geometry` for polygon rendering)
- `grid_df`: output of `aggregate_grid_load()` (has `peak_ev_load_kw`, `overloaded`, `deficit_kw`)
- `optimizer_df`: output of `optimize_placement()` (has prescription columns)

Each function merges `grid_df` onto `gdf` by FSA to get both geometry and simulation results in one frame, then builds the Folium map.

Map defaults: center on GTA (43.7, -79.4), zoom 9, dark tile layer (CartoDB dark_matter).

## State Management

- `st.session_state["grid_df"]` — persists simulation results across reruns
- `st.session_state["optimizer_df"]` — persists optimizer results
- `st.session_state["ev_count"]` — the actual number of EVs simulated
- Simulation clears optimizer results (user must re-optimize after re-simulating)
- `@st.cache_data` wraps `load_enriched_geodataframe()` to avoid reloading GeoJSON on every interaction

## `.streamlit/config.toml`

```toml
[theme]
primaryColor = "#00BCD4"
backgroundColor = "#0E1117"
secondaryBackgroundColor = "#262730"
textColor = "#FAFAFA"
font = "sans serif"
```

## What This Spec Does NOT Cover

- Deployment to Streamlit Community Cloud (deferred)
- Mobile responsiveness (Streamlit handles this natively)
- User authentication (not needed for hackathon)
