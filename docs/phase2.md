# Phase 2: The Movement Prediction Engine

## What is Phase 2?
Phase 2 is the **Movement Prediction Engine**. It takes the "Game Board" (the map built in Phase 1) and mathematically predicts where thousands of Electric Vehicles (EVs) will drive and park throughout the Greater Toronto Area.

Crucially, in our modular software architecture, Phase 2 is **strictly responsible for location tracking**. It answers a single question: *"How many cars parked in each postal code?"* It intentionally leaves all electrical grid math (transformers, kW loads, crashes) to Phase 3.

---

## 1. The Inputs (`backend/monte_carlo.py`)

The `SimulationEngine` accepts three main parameters to build a unique scenario:
1. **The Master Map**: The perfectly structured `GeoDataFrame` from Phase 1 containing all 260 postal codes and their 5-tier zone classifications.
2. **Number of EVs (`num_evs`)**: The total volume of cars to simulate in the city (e.g., 15,000).
3. **Time of Day (`time_of_day`)**: A string (e.g., "Morning" or "Evening") that completely alters the flow of traffic.

---

## 2. The Probability Density Function (PDF Weights)

To make the car movement highly realistic, the engine uses **Gravity Models**. We defined a dictionary of probability weights that act as a "PDF" (Probability Density Function). These weights determine how heavily a specific zone attracts cars.

**Morning Simulation:**
* `office_park` (4.0): Massive attraction. Everyone is driving to work.
* `retail_hub` (2.0): Moderate attraction for morning shopping/coffee.
* `transit_hub` (1.0): Moderate attraction for Park & Ride commuters and airport drop-offs.
* `leisure` (0.5): Very low attraction (parks are mostly empty).
* `residential` (0.1): Almost zero attraction (everyone has left home).

*If the user switches the simulation to "Evening", these weights dynamically invert (Residential spikes to 4.0, Office drops to 0.2).*

---

## 3. The Monte Carlo Lottery

Once the PDF weights are established, the actual prediction happens via a **Monte Carlo Algorithm**. 

1. **Normalization:** The engine takes the PDF weights and normalizes them across all 260 postal codes so they equal exactly `1.0` (100%).
2. **The Roll:** It uses `numpy.random.choice` to mathematically roll a weighted 260-sided die. It rolls this die exactly 15,000 times (once for every EV).
3. **The Result:** Because `office_parks` have the heaviest weights during the morning, the algorithm naturally "funnels" the highest volume of EVs into those specific postal codes.

---

## 4. The Output

The output of Phase 2 is the exact same map from Phase 1, but with a brand new data column: `ev_count`.

**Sample Output:**
| fsa | zone_type | proxy_capacity_kw | ev_count |
|-----|-----------|-------------------|----------|
| L6Z | office_park| 1200 | 603 |
| M6G | retail_hub| 1500 | 308 |
| L0A | residential| 300 | 2 |

This clean dataset is then officially handed over to **Phase 3 (The Grid Optimizer)**, which will calculate exactly how much electricity those 603 cars drain from the L6Z transformer!
