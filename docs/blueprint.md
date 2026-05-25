# EV Charging Demand & Grid Planning: Hackathon Project Master Blueprint

This document serves as your team's comprehensive operational guide to building, running, and presenting your predictive location optimization model.

## The Vision & Hackathon Strategy

### The Core Problem

EV adoption is accelerating rapidly, but utilities lack visibility into exactly where charging demand will spike, threatening to overload localized electrical feeders. Without a predictive mechanism, infrastructure planning remains reactive, risking local grid blackouts or inefficient capital deployment.

### The Winning Pivot

Your team is building an interactive, predictive **Location Optimization & Grid Impact Model**. Instead of a static data tracker, this system functions as a dynamic software tool that ingests mobility behaviors and baseline grid capacities, flags future grid failures, and programmatically prescribes the exact coordinates for new infrastructure.

## Deep Dive: The Algorithmic Flow & Simulation Engine

Your model bridges the gap between vehicle movement (demand) and electrical grid capabilities (supply) using a structured three-step mathematical pipeline.

### Step 1: Statistical Spatial-Temporal Probability Mapping

To run a realistic simulation over a short hackathon weekend, you must establish Probability Density Functions (PDFs) derived from real-world Ontario data. This ensures your simulation mirrors human behavior rather than pure randomness.

**Spatial Probability (The "Where"):** Utilizing regional GIS layers from the ArcGIS Living Atlas, you map and weight destination coordinates. Points of interest are weighted based on land-use types. High-density commercial nodes (e.g., shopping centers, corporate parks) and high-density residential hubs (e.g., condo complexes) receive high probability weights, while standard transit corridors receive lower weights.

> **Core Behavioral Axiom:** Drivers charge where they park, not where they drive. Therefore, destination parking "dwell time" is your strongest proxy for spatial demand.

**Temporal Probability (The "When"):** By analyzing historical regional load profiles from IESO (Independent Electricity System Operator) Power Data, you construct an arrival time probability curve. This curve heavily weights typical residential commuting patterns, establishing a steep probability peak between 5:00 PM and 8:00 PM when standard domestic arrivals occur.

### Step 2: Executing Vectorized Monte Carlo Simulations

With your probability distributions built, the core engine simulates the behavior of a regional fleet (e.g., 1,000 to 10,000 EV drivers within the Greater Toronto Area) to prevent system lag during your live presentation.

```
[Living Atlas Spatial PDF] ──┐
                             ├──► [Monte Carlo Engine] ──► Aggregated Peak Load (kW)
[IESO Temporal PDF] ─────────┘
```

**Iterative Sampling:** For every simulated vehicle, the algorithm samples from your spatial and temporal PDFs to assign a specific parking node, an arrival timestamp, and an estimated State of Charge (SoC) deficiency based on average trip distances.

**Load Accumulation:** The engine computes the required charging power (kW) and duration for each vehicle. Whenever a simulated car "plugs in," its electrical draw is appended to that specific geographic coordinate.

**Peak Calculation:** The simulation groups these data points into hourly intervals, vectorizing the calculations via NumPy/Pandas to instantly isolate localized peak power spikes across the grid network.

### Step 3: Spatial Join Data Alignment

Because your datasets operate on different geographic scales, your backend must align them via a **Spatial Join** (`gpd.sjoin` in GeoPandas):

- **IESO data** provides system capacity limitations mapped over 21 massive Electricity Planning Regions (e.g., "Toronto" or "GTA North").
- **Living Atlas data** tracks infrastructure at the ultra-granular coordinate or Forward Sortation Area (FSA) postal code level.

By programmatically intersecting these boundary polygons, your algorithm dynamically maps high-resolution coordinate spikes directly to the broader electricity subsystem responsible for feeding them, revealing exactly which localized neighborhoods threaten to push their parent substation past its breaking point.

## App Architecture & The "Wow Factor"

To make your project stand out to the judges, build your user interface around an interactive story arc divided into three progressive visual insights within a single **Streamlit** web framework.

### View 1: The Energy Spikes (The Problem)

The application initializes by displaying an interactive geospatial heatmap (rendered beautifully via Folium or PyDeck). This layer visualizes the aggregated result of your Monte Carlo simulation, showcasing bright, localized megawatt (MW) spikes during peak hours (5:00 PM to 8:00 PM) where vehicle density and charging requirements are concentrated.

### View 2: Grid Vulnerability (The Conflict)

To demonstrate actual utility risk, implement a dynamic **EV Adoption "What-If" Slider** in your app's sidebar.

As a user adjusts the slider from 10% up to 50% EV market penetration, your backend instantly scales up the simulated load curves.

The map calculates a real-time mathematical inequality:

$$\text{Simulated EV Demand} + \text{IESO Baseline Load} > \text{Localized Feeder Capacity}$$

Wherever this inequality holds true, the map coordinates dynamically transition from **Stable Green** to **Warning Dark Red**, giving judges immediate visual feedback on where the grid will suffer localized blackouts or line failures.

### View 3: Prescriptive Charger Placement (The Solution)

The final step uses a mathematical optimization solver (like PuLP) to execute a **Facility Location Problem (FLP)** algorithm. Instead of leaving the map covered in red failure zones, clicking an "Optimize" button triggers a script that evaluates the red zones and programmatically drops precise marker pins on the map.

These pins represent optimal infrastructure placement sites, strategically calculated to fulfill driver accessibility demands while minimizing grid strain. Clicking a marker pin pops up an engineered prescription card for the utility provider:

```
📍 Optimal Site Location Found
* Coordinate Zone: FSA L4C (GTA North Region)
* Predicted Peak Deficit: +450 kW
* Prescribed Infrastructure: 4x Level 2 Smart-Charging Stations integrated
  with a 200 kWh local Battery Energy Storage System (BESS) to buffer the
  feeder during peak IESO hours.
```

## Hackathon Deployment Strategy

**Skip the Desktop App Wrapper:** Do not dedicate time to compiling your scripts into `.exe` or `.app` desktop applications. Packaging Python dependencies across different operating systems is deeply time-consuming and often bugs out during quick turnarounds. More importantly, hackathon judges will not download unverified local executable files onto their presentation computers.

**The Winner's Route:** Push your codebase directly to a GitHub repository and deploy it via **Streamlit Community Cloud**. This builds a completely free, live public URL (`https://your-app.streamlit.app`). Because it runs on a dedicated cloud server and renders standard HTML5 within any web browser, it guarantees your system looks and performs flawlessly across Windows, Mac, Linux, or mobile devices during your live pitch.
