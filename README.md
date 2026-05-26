# EV Charging Demand & Grid Planning

Predictive Location Optimization & Grid Impact Model for the Greater Toronto Area. Built for the Seneca Energy Hackathon 2026.

## What It Does

EV adoption is accelerating, but utilities don't know where charging demand will spike. This tool simulates thousands of electric vehicles across the GTA, identifies which neighborhoods will overload the power grid, and prescribes exactly where to build new charging infrastructure.

**Three-view story arc:**

1. **Energy Spikes** — heatmap showing where EV charging demand concentrates
2. **Grid Vulnerability** — which neighborhoods turn red as the grid fails
3. **Optimal Placement** — where to build chargers to prevent blackouts

## Quick Start

```bash
# Install dependencies
curl -LsSf https://astral.sh/uv/install.sh | sh
source $HOME/.local/bin/env
uv sync

# Run the app through the pinned Python environment
uv run streamlit run app.py
```

Opens at `http://localhost:8501`. Use the sidebar to adjust EV adoption rate, temperature, and station budget, then click **Run Simulation**.

## How It Works

### Phase 1: Spatial Assembler (`backend/spatial_assembler.py`)

Loads 260 GTA postal code (FSA) boundaries from Statistics Canada, classifies each by land-use type via ArcGIS reverse geocoding, and assigns proxy grid capacity:

| Zone Type | Capacity | Examples |
|-----------|----------|---------|
| Residential | 300 kW | Suburban neighborhoods |
| Leisure | 500 kW | Parks, museums |
| Office Park | 1,200 kW | Corporate buildings |
| Retail Hub | 1,500 kW | Shopping centers |
| Transit Hub | 3,000 kW | Airports |

### Phase 2: Weekly Road-Grid Mobility Simulation (`backend/mobility_simulator.py`)

Builds a Monday-Sunday agent itinerary, routes every leg on the road graph, and patches charging decisions from SoC, future trip need, dwell time, charger access, and charger proximity:

- **Real road graph** — cached OSM drive graph for GTA FSAs: routes, travel time, route paths, and edge-flow aggregation
- **Hackathon data mapping** — FSA polygons, zone types, grid proxy capacity, population weights, and Toronto traffic counts are mapped onto the road graph
- **Public chargers** — AFDC public EV chargers are mapped into FSAs and snapped to OSM road nodes
- **Time conditioning** — weekday/weekend, day-of-week, commute arrival targets, dwell windows, and static time-of-day traffic multipliers
- **SoC model** — weekly SoC propagation, home/work/public charging probabilities, deterministic reserve protection, and patch charging without failed trips
- **Grid load** — charge sessions aggregate to FSA/day/hour load, then compare against explicit baseline headroom

`backend/monte_carlo.py` remains as the legacy Phase 2 baseline and constant source for older tests.

### Phase 3: Optimization Solver (`backend/optimizer.py`)

PuLP mixed-integer linear programming solver for the Facility Location Problem:

- Selects optimal charging station sites from overloaded zones
- Haversine distance-weighted coverage matrix
- Budget constraint (user-configurable, 5-20 stations)
- Deficit-scaled prescriptions: charger count = `ceil(deficit / kw_per_unit)`
- BESS (Battery Energy Storage System) sizing: `deficit * 2 hours`

### Phase 4: Interactive Dashboard (`app.py`)

Streamlit + Folium web app backed by `backend/road_grid_dashboard.py`, which calls the weekly OSM/AFDC road-grid model:

- **Sidebar:** EV adoption slider (10-50%), temperature (-20 to +30C), time window, sampled drivers, station budget
- **View 1:** Yellow-to-red choropleth of peak EV charging load
- **View 2:** Binary green/red grid vulnerability map
- **View 3:** Purple marker pins at optimal sites with prescription popup cards

## Project Structure

```
seneca-hack/
├── app.py                          # Streamlit entry point
├── .streamlit/config.toml          # Dark theme config
├── backend/
│   ├── spatial_assembler.py        # Phase 1 — GeoJSON + zone classification + capacity
│   ├── mobility_simulator.py       # Phase 2 — weekly agent model over road-grid routes
│   ├── road_network.py             # OSM/FSA road graph routing and edge mapping
│   ├── charger_catalog.py          # AFDC/OSM/proxy charger catalog and road-node snapping
│   ├── road_grid_dashboard.py      # App/runtime adapter for weekly road-grid outputs
│   ├── simulation_validation.py    # Real-grid sanity and observed-target validation gates
│   ├── monte_carlo.py              # Legacy Phase 2 baseline
│   ├── optimizer.py                # Phase 3 — PuLP FLP solver + prescriptions
│   ├── map_builder.py              # Phase 4 — Folium map construction
│   └── data/
│       ├── gta_fsa_boundaries.geojson
│       ├── fsa_zone_classification.csv
│       ├── ieso_load_profile.csv
│       ├── zone_weights.json
│       ├── fsa_population_scaling.csv
│       └── cache/
│           ├── gta_drive.graphml
│           ├── gta_drive_graph.pkl
│           └── afdc_on_ev_chargers.csv
├── data_preparation/
│   ├── prepare_data.py             # Downloads StatsCan boundaries + ArcGIS classification
│   ├── fetch_real_world_grid.py    # Fetches/caches OSM road graph and chargers
│   ├── fetch_toronto_traffic.py    # Fetches Toronto Open Data traffic volumes
│   ├── run_model_validation.py     # Real-grid validation and calibration CLI
│   └── benchmark_simulation_scale.py # High-scale throughput benchmark
├── tests/                          # 82 tests, including road-grid integration tests
└── docs/
    ├── blueprint.md
    ├── architecture.md
    └── implementation_plan.md
```

## Data Sources

| Source | Data | Usage |
|--------|------|-------|
| Statistics Canada | 2021 Census FSA boundary polygons | GTA map (260 postal zones) |
| Statistics Canada | 2021 FSA population/dwellings | Home-origin and population scaling weights |
| ArcGIS Living Atlas | Reverse geocode POI classification | Zone type per FSA |
| City of Toronto Open Data | Traffic volumes at intersections | Spatial probability weights |
| OpenStreetMap | GTA drive road graph | Route distance, travel time, and edge-flow mapping |
| AFDC/NREL | Ontario public EV chargers | Public charger locations snapped to road graph |
| IESO | Ontario hourly load profile | Baseline grid stress curve |

## Running Tests

```bash
PYTHONPATH=backend uv run pytest -q

PYTHONPATH=backend uv run python data_preparation/run_model_validation.py \
  --real-grid --observed-targets --repeat-week --num-people 500 --seeds 101 202 303 --sensitivity

PYTHONPATH=backend uv run python data_preparation/run_model_validation.py \
  --real-grid --observed-targets --num-people 500 --seeds 101 202 303 \
  --fit --fit-strategy adaptive --all-candidates \
  --fit-num-people 250 --fit-seeds 101 202 \
  --adaptive-stage1-people 120 --adaptive-stage2-top 32 \
  --adaptive-final-top 8 --fit-jobs 4 \
  --out-dir backend/data/validation/adaptive_fit --resume-fit
```

91 tests cover spatial data integrity, hackathon-data-to-road-grid mapping, real OSM graph routing, AFDC charger snapping, public charge-event-to-catalog membership, private charge-event-to-origin mapping, edge-flow artifact integrity, cache fingerprinting, validation run metadata, high-scale batched aggregation, charger concentration, purpose-zone alignment, weekly mobility, arrive-by timing, SoC/charging behavior, charge-event accounting, hourly load conservation, optimizer constraints, and dashboard runtime adaptation. The real-grid validation command requires the cached OSM graph and AFDC charger catalog, and sensitivity checks aggregate across supplied seeds. Validation runs with `--out-dir` write `run_metadata.json` with command args, runtime, config, seeds, artifact row counts, validation break counts, fit/sensitivity summaries, and cache-file metadata. The first OSM load writes a binary graph cache so later real-grid runs avoid repeatedly parsing the large GraphML file; expanded OD edge templates are also persisted after full edge aggregation. Route and edge-template caches carry FSA/centroid/graph-anchor fingerprints and are written atomically so parallel validation cannot leave partial cache files. Charging simulation and full OSM edge aggregation avoid pandas row churn in their hot loops; FSA-corridor flow remains the faster calibration proxy. The local machine has 4 performance CPU cores; `--fit-jobs 4` is the practical default. Validation multiprocessing uses normal spawned workers only from script entrypoints, with stdin/ad-hoc runs falling back to serial to avoid macOS native-library fork crashes. GPU acceleration is not currently useful without rewriting the Python agent loop into vectorized kernels. Long fit screens checkpoint candidate rows and can be resumed with `--resume-fit`; adaptive checkpoint filenames include stage size/seeds/detail to avoid mixing incompatible reruns.

For 100k+ population experiments, use bounded-memory batched aggregation:

```bash
PYTHONPATH=backend uv run python data_preparation/benchmark_simulation_scale.py \
  --real-grid --num-people 200000 --batch-size 25000 --edge-flow-detail fsa
```

This path runs exact per-person weekly itinerary and SoC/charging decisions, but aggregates each batch before moving to the next batch. It is the production-scale path for FSA-corridor road-flow studies; full OSM edge expansion remains reserved for smaller final validation/visual inspection runs. The Streamlit dashboard uses the same FSA-corridor batched path above 25k sampled drivers, so high-scale UI runs avoid retaining millions of raw leg rows in memory.

## Tech Stack

Python 3.13 | Streamlit | Folium | GeoPandas | NumPy | Pandas | PuLP | streamlit-folium

## Team

Seneca Energy Hackathon 2026
