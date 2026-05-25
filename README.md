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

# Run the app
streamlit run app.py
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

### Phase 4: Interactive Dashboard (`app.py`)

Streamlit + Folium web app with:

- **Sidebar:** EV adoption slider (10-50%), temperature (-20 to +30C), time of day, station budget
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
│   ├── monte_carlo.py              # Phase 2 — Monte Carlo simulation + grid test
│   ├── optimizer.py                # Phase 3 — PuLP FLP solver + prescriptions
│   ├── map_builder.py              # Phase 4 — Folium map construction
│   └── data/
│       ├── gta_fsa_boundaries.geojson
│       ├── fsa_zone_classification.csv
│       ├── ieso_load_profile.csv
│       └── zone_weights.json
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

Python 3.14 | Streamlit | Folium | GeoPandas | NumPy | Pandas | PuLP | streamlit-folium

## Team

Seneca Energy Hackathon 2026
