# EV Charging Demand & Grid Planning

Predictive Location Optimization & Grid Impact Model for the Greater Toronto Area. Built for the Seneca Energy Hackathon 2026.

## What It Does

EV adoption is accelerating, but utilities don't know where charging demand will spike. This tool simulates thousands of electric vehicles across the GTA, identifies which neighborhoods will overload the power grid, and prescribes exactly where to build new charging infrastructure.

**Three-view story arc:**

1. **Energy Spikes** — heatmap showing where EV charging demand concentrates
2. **Grid Vulnerability** — which neighborhoods turn red as the grid fails
3. **Optimal Placement** — where to build chargers to prevent blackouts

### 1. Start the Backend API (FastAPI)
The backend is powered by FastAPI and Uvicorn. To install dependencies and run the server:
```bash
# Install uv package manager (if not already installed)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install Python dependencies and run the server (from the root folder)
uv run uvicorn backend.apis.api:app --reload --port 8000
```
The API documentation is available at `http://localhost:8000/docs`.

### 2. Start the Frontend Application (Vite + React)
The frontend is built using React and Leaflet for native map rendering. To install dependencies and start the dev server:
```bash
# Go to the frontend folder
cd frontend

# Install Node dependencies
npm install

# Start Vite dev server
npm run dev
```
Open your browser to the URL displayed (usually `http://localhost:5173`).

---

## Connecting the API to the Real Simulation & Optimizer Scripts

Currently, the backend API endpoints in [api.py](file:///c:/programming/SenecaHack/seneca-hack/backend/apis/api.py) use a mock function `_generate_mock_grid` to keep the development server instantly responsive. 

To connect the frontend dashboard with the real mathematical engines ([monte_carlo.py](file:///c:/programming/SenecaHack/seneca-hack/backend/monte_carlo.py) and [optimizer.py](file:///c:/programming/SenecaHack/seneca-hack/backend/optimizer.py)), update [api.py](file:///c:/programming/SenecaHack/seneca-hack/backend/apis/api.py) with the following integration structure:

### 1. Import the Real Engines
In `backend/apis/api.py`, add:
```python
from backend.monte_carlo import SimulationEngine
from backend.optimizer import optimize_placement

# Instantiate the engine once at startup
engine = SimulationEngine()
```

### 2. Connect the Simulation Endpoint
Replace the current `/api/simulate` endpoint with:
```python
@app.post("/api/simulate")
def simulate(params: SimParams):
    # 1. Run the stochastic Monte Carlo engine
    ev_df = engine.run_simulation(
        num_evs=params.num_cars,
        time_of_day="Full Day",  # Can map to a dropdown in the UI later
        temperature_celsius=float(params.temperature)
    )
    
    # 2. Stress-test grid load and find overloads
    grid_df = engine.aggregate_grid_load(ev_df)
    
    # 3. Format EV arrivals for FSA tooltips
    ev_dict = {}
    for fsa, group in ev_df.groupby("fsa"):
        # Select first 5 arrivals as representative samples
        sample_cars = group.head(5).to_dict(orient="records")
        ev_dict[fsa] = {
            "total_fsa_cars": len(group),
            "sample_cars": sample_cars
        }
        
    return {
        "ev_count": params.num_cars,
        "total_peak_demand_mw": float(grid_df["peak_ev_load_kw"].sum() / 1000),
        "overloaded_count": int(grid_df["overloaded"].sum()),
        "total_fsas": len(grid_df),
        "max_deficit_kw": float(grid_df["deficit_kw"].max()) if not grid_df.empty else 0.0,
        "grid_data": grid_df.to_dict(orient="records"),
        "ev_data": ev_dict
    }
```

### 3. Connect the Optimization Endpoint
Replace the `/api/optimize` endpoint with:
```python
@app.post("/api/optimize")
def optimize(params: SimParams):
    # 1. Run simulation to get grid deficit data
    ev_df = engine.run_simulation(
        num_evs=params.num_cars,
        time_of_day="Full Day",
        temperature_celsius=float(params.temperature)
    )
    grid_df = engine.aggregate_grid_load(ev_df)
    
    # 2. Run the PuLP Facility Location Problem solver
    opt_df = optimize_placement(grid_df, max_stations=params.max_stations)
    
    return {
        "stations_deployed": len(opt_df),
        "total_charger_kw": float(opt_df["total_charger_kw"].sum()) if not opt_df.empty else 0.0,
        "total_bess_kwh": int(opt_df["bess_kwh"].sum()) if not opt_df.empty else 0,
        "prescriptions": opt_df.to_dict(orient="records") if not opt_df.empty else []
    }
```

---

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

### Phase 2: Monte Carlo Simulation (`backend/monte_carlo.py`)

Simulates up to 30,000 individual EVs using probability distributions derived from real City of Toronto traffic data:

- **Spatial PDF** — zone weights from Toronto Open Data traffic intersection volumes
- **Temporal PDF** — Full Day mixture model: 70% evening peak N(17.5, 1.5) + 30% midday peak N(12.0, 2.0)
- **Battery deficiency** — Gamma(3, 5) + 5 kWh base, with winter efficiency penalty
- **Duration-aware load model** — charging time = `soc_needed / charger_kw`. Only vehicles actively charging during each hour count toward grid load
- **Grid inequality test** — `EV load + IESO baseline > feeder capacity` flags overloaded zones

Winter mode: at -15C, batteries need ~42% more energy, producing longer charging sessions and higher concurrent grid stress.

### Phase 3: Optimization Solver (`backend/optimizer.py`)

PuLP mixed-integer linear programming solver for the Facility Location Problem:

- Selects optimal charging station sites from overloaded zones
- Haversine distance-weighted coverage matrix
- Budget constraint (user-configurable, 5-20 stations)
- Deficit-scaled prescriptions: charger count = `ceil(deficit / kw_per_unit)`
- BESS (Battery Energy Storage System) sizing: `deficit * 2 hours`

## Project Structure

```
seneca-hack/
├── backend/
│   ├── apis/
│   │   └── api.py                  # FastAPI Entrypoint (runs Uvicorn)
│   ├── spatial_assembler.py        # Phase 1 — GeoJSON + zone classification + capacity
│   ├── monte_carlo.py              # Phase 2 — Monte Carlo simulation + grid test
│   ├── optimizer.py                # Phase 3 — PuLP FLP solver + prescriptions
│   └── data/
│       ├── gta_fsa_boundaries.geojson
│       ├── fsa_zone_classification.csv
│       ├── ieso_load_profile.csv
│       └── zone_weights.json
├── frontend/
│   ├── public/
│   │   └── gta_fsa_boundaries.geojson
│   ├── src/
│   │   ├── api.js                  # Frontend API requests client
│   │   ├── App.jsx                 # Dashboard Command Center UI
│   │   ├── MapComponent.jsx        # Leaflet Native Map Component
│   │   └── index.css               # Brutalist theme styles
│   ├── package.json
│   └── vite.config.js
├── data_preparation/
│   ├── prepare_data.py             # Downloads StatsCan boundaries + ArcGIS classification
│   └── fetch_toronto_traffic.py    # Fetches Toronto Open Data traffic volumes
├── tests/
│   ├── test_spatial_assembler.py   # 6 tests
│   ├── test_monte_carlo.py         # 8 tests
│   └── test_optimizer.py           # 7 tests
└── docs/
    ├── blueprint.md
    ├── architecture.md
    └── implementation_plan.md
```

## Data Sources

| Source | Data | Usage |
|--------|------|-------|
| Statistics Canada | 2021 Census FSA boundary polygons | GTA map (260 postal zones) |
| ArcGIS Living Atlas | Reverse geocode POI classification | Zone type per FSA |
| City of Toronto Open Data | Traffic volumes at intersections | Spatial probability weights |
| IESO | Ontario hourly load profile | Baseline grid stress curve |

## Running Tests

```bash
uv run pytest tests/ -v
```

21 tests covering spatial data integrity, simulation correctness, grid inequality logic, optimizer constraints, and winter temperature effects.

## Tech Stack

Python 3.14 | FastAPI | React (Vite) | React Leaflet | GeoPandas | NumPy | Pandas | PuLP

## Team

Seneca Energy Hackathon 2026
