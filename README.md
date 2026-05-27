# EV Charging Demand & Grid Planning

Predictive Location Optimization & Grid Impact Model for the Greater Toronto Area. Built for the Seneca Hackathon 2026 by **Team CXC**.

## What It Does

EV adoption is accelerating, but utilities don't know where charging demand will spike. This system simulates **300,000 electric vehicles** across the GTA using agent-based Monte Carlo simulation with a Semi-Markov decision layer, identifies which neighborhoods will overload the power grid, and prescribes exactly where to build new charging infrastructure.

**Three-view story arc:**

1. **Energy Spikes** — heatmap showing where EV charging demand concentrates
2. **Grid Vulnerability** — which neighborhoods turn red as the grid fails
3. **Optimal Placement** — where to build chargers to prevent blackouts

## Quick Start

### 1. Start the Backend API (FastAPI)

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
source $HOME/.local/bin/env
uv sync
uv run uvicorn backend.apis.api:app --reload --port 8000
```

API docs at `http://localhost:8000/docs`.

### 2. Start the Frontend (Vite + React)

```bash
cd frontend
npm install
npm run dev
```

Opens at `http://localhost:5173`.

### 3. Run the Streamlit Dashboard (alternative)

```bash
uv run streamlit run app.py
```

Opens at `http://localhost:8501`.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                        Data Sources                                 │
│  StatsCan FSA Boundaries  ·  Toronto Hydro Feeders  ·  Hydro One   │
│  IESO Load Profiles  ·  City of Toronto Traffic  ·  AFDC Chargers  │
│  OSM Road Network (145K nodes, 383K edges)                         │
└──────────────────────────┬──────────────────────────────────────────┘
                           │
              ┌────────────▼────────────┐
              │   Phase 1: Spatial      │
              │   Assembler             │
              │   260 FSA zones         │
              │   Real grid capacity    │
              └────────────┬────────────┘
                           │
              ┌────────────▼────────────┐
              │   Phase 2: Monte Carlo  │
              │   + Semi-Markov Layer   │
              │   300K agents           │
              │   3.5M weekly trips     │
              └────────────┬────────────┘
                           │
              ┌────────────▼────────────┐
              │   Phase 3: MILP         │
              │   Optimizer             │
              │   Facility Location     │
              │   Problem (PuLP)        │
              └────────────┬────────────┘
                           │
              ┌────────────▼────────────┐
              │   Phase 4: Dashboard    │
              │   React + Leaflet       │
              │   Streamlit + Folium    │
              └─────────────────────────┘
```

---

## How It Works

### Phase 1: Spatial Assembler (`backend/spatial_assembler.py`)

Loads 260 GTA postal code (FSA) boundaries from Statistics Canada, classifies each by land-use type, and assigns grid capacity from real utility data.

**Proxy capacity (fallback):**

| Zone Type | Capacity | Examples |
|-----------|----------|---------|
| Residential | 300 kW | Suburban neighborhoods |
| Leisure | 500 kW | Parks, museums |
| Office Park | 1,200 kW | Corporate buildings |
| Retail Hub | 1,500 kW | Shopping centers |
| Transit Hub | 3,000 kW | Airports |

**Real utility overlays:**

- **Toronto Hydro** — ArcGIS spatial join maps real feeder capacity to 102 M-prefix (Toronto core) FSAs. Uses `min(overlapping_feeder_capacities)` as the weakest-link constraint.
- **Hydro One** — Station long-term ratings allocated proportionally to 158 L-prefix (suburban) FSAs. Per-FSA headroom = `(station_ltr_mw * 0.30) / n_fsas_served`, bounded to [100 kW, 5,000 kW].

---

### Phase 2: Monte Carlo Simulation (`backend/monte_carlo.py`)

Simulates up to **300,000 individual EV agents** using probability distributions derived from real City of Toronto data. Think of it like flipping a coin — flip it 10 times and you get roughly 5 heads. Flip it 300,000 times and you get the exact odds. That's Monte Carlo, but instead of coins, it's cars.

#### Spatial Sampling (Where do EVs park?)

- Weighted random choice using zone-type probability weights
- Source: Toronto traffic intersection volumes (6,400 intersections mapped to 260 FSAs)
- Vectorized: `np.random.choice(fsa_list, size=num_evs, p=probabilities)`

#### Temporal Sampling (When do they arrive?)

Mixture of normal distributions:

| Period | Distribution | Weight | Clip Range |
|--------|-------------|--------|------------|
| Evening peak | N(17.5, 1.5) | 70% | 2 PM – 9 PM |
| Midday peak | N(12.0, 2.0) | 30% | 9 AM – 3 PM |
| Morning peak | N(8.25, 1.0) | 100% (morning mode) | 6 AM – 11:30 AM |

#### Battery State-of-Charge Sampling

- **Initialization:** Beta(6, 2) distribution (mean 75% SoC)
- **Charge deficiency:** Gamma(3, 5) + 5 kWh base
- **Charge decision:** Sigmoid function: `sigmoid(soc_need + range_anxiety + proximity + dwell - detour) * availability`

#### Winter Temperature Efficiency Penalty

Physics-based battery degradation:

```
if temperature < 20°C:
    efficiency_loss = min(0.40, (20 - temp) * 0.0085)
    soc_needed = soc_needed / (1.0 - efficiency_loss)
```

At **-15°C**, vehicles need **~42% more energy**, producing longer charging sessions and higher concurrent grid stress.

#### Duration-Aware Grid Load Aggregation

Grid stress depends on charging *duration*, not just energy needed:

1. `duration_h = soc_needed_kwh / charger_kw` (7 kW residential, 50 kW DCFC, 150 kW ultra-fast)
2. For each hour H: sum concurrent kW across all EVs where `arrival < H+1 AND departure > H`
3. Peak hour per FSA: `argmax(hourly_load[fsa])`

#### Grid Inequality Test

```
total_load = peak_ev_load + (ieso_baseline_fraction * capacity)
overloaded = total_load > feeder_capacity
deficit = max(0, total_load - feeder_capacity)
```

---

### Semi-Markov Decision Layer (`backend/mobility_simulator.py`)

People aren't random — they make decisions. The Semi-Markov layer models **state transitions** between daily activities with **conditional probabilities** that shift based on real-world events.

#### State Space

Each person occupies one of 5 destination states:

| State | Icon | Population Share |
|-------|------|-----------------|
| Home | House | Base state |
| Work | Briefcase | 62% (workers) |
| School | Book | 8% (students) |
| Shopping/Retail | Cart | Variable |
| Leisure | Park | Variable |

#### Transition Probabilities

Transitions between states are time-dependent and day-dependent (weekday vs. weekend). Example weekday transitions from Home:

```
Home → Work:     0.45
Home → School:   0.25
Home → Shopping: 0.15
Home → Home:     0.15 (stay home)
```

#### Conditional Event Shifts

When external events occur, transition probabilities **redistribute**:

- **School cancelled (snow day):** `P(Home → School)` drops to 0, its probability mass shifts to `P(Home → Home)`. More people staying home reshapes charging demand across the entire city.
- **Work-from-home day:** `P(Home → Work)` reduces, increasing residential charging load.
- **Weekend:** Work/school probabilities collapse, retail/leisure probabilities increase.

#### Semi-Markov Properties

Unlike a standard Markov chain, dwell times at each state are **not memoryless** — they follow realistic duration distributions:

- Work dwell: ~8 hours (weekday)
- School dwell: ~6 hours
- Shopping dwell: ~1-2 hours
- Leisure dwell: ~2-3 hours

The time spent in each state directly affects charging opportunity, SoC evolution, and grid load timing.

---

### Road Network (`backend/road_network.py`)

Real road network grounding for the mobility simulation:

- **Dual backend:** OpenStreetMap via OSMnx (primary) + FSA-adjacency graph (offline fallback)
- **Scale:** 145,540 OSM nodes, 383,093 road edges, 1 connected component across 260 FSAs
- **Speed profiles:** 11 OSM highway classes (motorway: 95 kph → residential: 32 kph)
- **Traffic multipliers:** Hour-based congestion (AM peak +32%, PM peak +38%, overnight -8%)
- **Routing accuracy:** Median FSA centroid snap 90.8m, p95 892.8m. Network circuity median 1.317.
- **Edge-flow aggregation:** Each route path is walked over travel time, assigning traversals to the hour the vehicle reaches each segment (not departure hour)

---

### Mobility Engine (`backend/mobility_simulator.py`)

Full agent-based weekly mobility simulation:

- **Scale:** 300,000 people in **324 seconds** (927 people/sec) on arm64 macOS
- **Output:** 3.5M weekly trip legs, 184K charge events, 3.1M MWh grid load
- **Population:** StatCan 2021 Census (8.39M GTA residents), distributed across 260 FSAs
- **Person types:** Worker 62%, student 8%, retired 12%, other 18%
- **EV sampling:** Baseline 3% fleet penetration (StatCan 2.8% national, Ontario 8.1% new sales)
- **Itineraries:** 5 destination types with time/day-dependent attraction weights
- **Trip legs:** Weekly patterns (weekday vs. weekend), purpose-driven routing
- **Battery tracking:** SoC forward-propagated through ordered trips per agent per day

---

### Charger Catalog (`backend/charger_catalog.py`)

Real-world charger infrastructure:

- **Public chargers:** 2,889 NREL/AFDC Ontario stations (cached locally)
- **Enrichment:** OSM `amenity=charging_station` overlay
- **Snap accuracy:** AFDC-to-OSM p95 snap distance: 261m

| Zone Type | Charger Power | Public Density (per FSA) |
|-----------|--------------|--------------------------|
| Residential | 50 kW | 0.35 |
| Leisure | 90 kW | 1.25 |
| Office Park | 50 kW | 2.25 |
| Retail Hub | 50 kW | 3.50 |
| Transit Hub | 150 kW | 5.00 |

**Private charging access probabilities:** 70% home, 35% work, 30% nearby-home public, 25% work public, 45% retail public.

---

### Model Calibration (`backend/model_calibration.py`)

30+ fit targets with low/high/ideal ranges and weights:

| Category | Target | Ideal Value |
|----------|--------|-------------|
| Itinerary | Legs per person per week | 13.0 |
| Itinerary | Weekly km per person | 240.0 |
| Itinerary | Route p50 / p90 | 18 km / 55 km |
| Charging | Events per EV per week | 2.8 |
| Charging | Median detour penalty | 5 min |
| SoC | Final weekly drift | ±8% |
| SoC | Full-charge departure rate | 18% |
| Zones | Work/school purpose accuracy | 95% |
| Zones | Retail/leisure accuracy | 96% |

Candidate generation via grid search over public-charger access parameters (15-30% home, 22-30% work, 45-55% retail) and SoC targets.

---

### Phase 3: Optimization Solver (`backend/optimizer.py`)

PuLP mixed-integer linear programming solver for the Facility Location Problem.

**Problem formulation:**

- **Sets:** I = overloaded FSAs (demand), J = candidate station sites
- **Variables:** `y[j] ∈ {0,1}` (open station?), `x[i][j] ∈ [0,1]` (fraction served)
- **Objective:** Maximize `Σ deficit[i] * coverage_weight[i][j] * x[i][j]`
- **Coverage weight:** `1 / (1 + haversine_km(i, j))` — inverse distance decay
- **Budget:** `Σ y[j] ≤ max_stations` (user-configurable, 5-20)
- **Linking:** `x[i][j] ≤ y[j]` — can only serve from open stations
- **Demand cap:** `Σ x[i][j] ≤ 1` — each zone counted at most once
- **Fallback:** Greedy top-N by deficit if solver doesn't find optimal

**Asset prescription rules:**

| Zone Type | Charger Type | Power | BESS Sizing |
|-----------|-------------|-------|-------------|
| Residential | Level 2 Smart-Charging Hub | 7 kW/unit | deficit * 2h |
| Leisure | Level 2 Smart-Charging Hub | 7 kW/unit | deficit * 2h |
| Office Park | DC Fast Charging Array | 50 kW/unit | deficit * 2h |
| Retail Hub | DC Fast Charging Array | 50 kW/unit | deficit * 2h |
| Transit Hub | Ultra-Fast Charging Array | 150 kW/unit | deficit * 2h |

Unit calculation: `charger_units = ceil(deficit_kw / kw_per_unit)`

---

### Phase 4: Data Representation & Dashboard

Two frontend implementations:

#### React + Leaflet Dashboard (`frontend/`)

Interactive React dashboard powered by `react-leaflet`:

- **Native Leaflet maps** with smooth zoom/pan and dynamic region styling
- **Three toggleable views:** Energy Spikes (demand), Grid Vulnerability (overloads), Placements
- **Interactive controls:** EV count, temperature, time-of-day sliders
- **Context-menu EV details:** Right-click any FSA to see sample EV arrivals
- **Custom Charger Editor:** Manually add/remove chargers by FSA code
- **Error resilience:** Global ErrorBoundary, inline API error banners, loading overlays

#### Streamlit + Folium Dashboard (`app.py`)

Single-page Streamlit app with reactive controls:

- **Sidebar:** Adoption rate (10-50%), temperature (-20 to +30°C), time of day, station budget
- **Caching:** `@st.cache_data` for simulation results, `@st.cache_resource` for engine instance
- **Maps:** Folium choropleth (yellow→red demand), binary green/red vulnerability, purple marker placement
- **Prescription cards:** HTML popups with charger type, unit count, total kW, BESS sizing

---

### Validation & Benchmarking (`backend/simulation_validation.py`)

**30+ validation gates** tied to observed real-world data:

| Category | Validation | Source |
|----------|-----------|--------|
| Road graph | Snap distance, circuity, connectivity | OSM network metrics |
| Mobility | Legs/person, route km distributions | TTS origin-destination data |
| Charging | Charge frequency, SoC before/after | AFDC charger usage |
| Load profiles | Hourly energy, grid peaks, correlation | IESO baseline curves |
| Traffic | AM/PM FSA vehicle distributions | City of Toronto turning-movement counts (107 FSAs) |
| Reproducibility | Multi-seed consistency | Seeds 101, 202, 303 |

**300K high-scale benchmark:**

| Metric | Value |
|--------|-------|
| Population simulated | 300,000 people |
| Runtime | 324 seconds (927 ppl/sec) |
| Weekly trip legs | 3.5M |
| Charge events | 184K |
| Grid load | 3.1M MWh |
| Edge routes | 87.5B km across 161K corridors |
| EV edge traversals | 689K |
| Chargers mapped | 2,889 |
| Hourly grid timestamps | 43,680 |

---

## Project Structure

```
seneca-hack/
├── backend/
│   ├── apis/
│   │   └── api.py                      # FastAPI entry point
│   ├── spatial_assembler.py            # Phase 1 — GeoJSON + zone classification + capacity
│   ├── monte_carlo.py                  # Phase 2 — Monte Carlo simulation + grid test
│   ├── mobility_simulator.py           # Semi-Markov agent-based weekly simulation
│   ├── road_network.py                 # OSM road graph + routing + edge-flow aggregation
│   ├── charger_catalog.py              # AFDC/NREL charger database + zone mapping
│   ├── model_calibration.py            # 30+ fit targets + grid search calibration
│   ├── simulation_validation.py        # 30+ validation gates + benchmark runner
│   ├── observed_targets.py             # Real-world validation targets (traffic, chargers, IESO)
│   ├── optimizer.py                    # Phase 3 — PuLP FLP solver + prescriptions
│   ├── map_builder.py                  # Phase 4 — Folium map visualization
│   ├── road_grid_dashboard.py          # Road-grid Streamlit dashboard view
│   ├── scripts/
│   │   └── fetch_hydro_capacity.py     # Toronto Hydro + Hydro One data ingestion
│   └── data/
│       ├── gta_fsa_boundaries.geojson  # 260 FSA polygons (StatsCan 2021)
│       ├── fsa_zone_classification.csv # Zone type per FSA
│       ├── ieso_load_profile.csv       # 24-hour baseline grid load curve
│       ├── zone_weights.json           # Spatial probability weights (Toronto traffic)
│       ├── toronto_hydro_feeders.geojson # Real Toronto Hydro feeder capacity
│       ├── hydro_one_stations.csv      # Suburban Hydro One station ratings
│       ├── fsa_population_scaling.csv  # StatCan population per FSA
│       └── toronto_traffic_fsa_counts.csv # Toronto turning-movement traffic counts
├── frontend/
│   ├── public/
│   │   └── gta_fsa_boundaries.geojson
│   ├── src/
│   │   ├── api.js                      # Frontend API client
│   │   ├── App.jsx                     # Dashboard UI
│   │   ├── MapComponent.jsx            # Leaflet native map
│   │   └── index.css                   # Theme styles
│   ├── package.json
│   └── vite.config.js
├── presentation/
│   ├── index.html                      # Reveal.js 12-slide presentation
│   ├── capture_screenshots.py          # Automated Folium → Chrome screenshot pipeline
│   └── screenshots/                    # Dashboard screenshots for presentation
├── data_preparation/
│   ├── prepare_data.py                 # StatsCan boundaries + ArcGIS classification
│   ├── fetch_toronto_traffic.py        # Toronto Open Data traffic volumes
│   ├── fetch_statcan_fsa_population.py # StatCan population data
│   ├── fetch_real_world_grid.py        # Real-world grid data fetcher
│   ├── run_model_validation.py         # Full validation suite runner
│   └── benchmark_simulation_scale.py   # 300K high-scale benchmark
├── tests/
│   ├── test_spatial_assembler.py
│   ├── test_monte_carlo.py
│   ├── test_optimizer.py
│   ├── test_mobility_simulator.py      # 1,336 lines of mobility tests
│   ├── test_road_grid_mapping.py
│   ├── test_road_grid_dashboard.py
│   ├── test_run_model_validation.py
│   ├── test_benchmark_simulation_scale.py
│   └── test_model_assumption_coverage.py
├── docs/
│   ├── architecture.md
│   ├── model_assumptions.md            # All constants, sources, and rationale
│   ├── implementation_plan.md
│   └── benchmarks/
│       └── high_scale_benchmark_300000.json
├── app.py                              # Streamlit dashboard entry point
└── pyproject.toml                      # Python 3.14 dependencies
```

## Data Sources

| Source | Data | Usage |
|--------|------|-------|
| Statistics Canada | 2021 Census FSA boundaries + population (8.39M) | 260 GTA postal zones, population scaling |
| Toronto Hydro | ArcGIS feeder capacity data | Real grid capacity for 102 Toronto FSAs |
| Hydro One | Station long-term MW ratings | Suburban grid capacity for 158 FSAs |
| IESO | Ontario hourly load profile | 24-hour baseline grid stress curve |
| City of Toronto | Traffic intersection volumes (6,400 intersections) | Spatial probability weights |
| City of Toronto | Turning-movement counts (107 FSAs) | Validation targets |
| NREL/AFDC | 2,889 Ontario public EV charger stations | Charger catalog + spatial mapping |
| OpenStreetMap | Road network (145K nodes, 383K edges) | Routing, travel times, edge-flow aggregation |

## Running Tests

```bash
uv run pytest tests/ -v
```

97 tests covering spatial data integrity, simulation correctness, grid inequality logic, optimizer constraints, mobility patterns, road network metrics, charging behavior, validation gates, and benchmark reproducibility.

## Tech Stack

Python 3.14 | FastAPI | Uvicorn | React (Vite) | React Leaflet | Streamlit | Folium | GeoPandas | Shapely | NumPy | Pandas | SciPy | PuLP | OSMnx | NetworkX

## Team CXC

Seneca Hackathon 2026
