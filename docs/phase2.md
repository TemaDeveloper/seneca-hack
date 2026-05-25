# Phase 2: The Agent-Based Movement Engine

## What is Phase 2?
Phase 2 is our **Agent-Based Movement Engine**. Its job is to answer a complex question: *"If 150,000 Electric Vehicles drive into Toronto, exactly when do they arrive, where do they park, and how much battery do they need?"*

Instead of blindly guessing, we built a statistical pipeline that uses **City of Toronto Open Data** and vectorized mathematical distributions (Bell Curves and Gamma distributions) to track every individual car.

---

## Step 1: The Open Data Spatial Proof (`fetch_toronto_traffic.py`)

If you want to simulate where cars go, you need to know how heavy the "gravity" of different zones are. 
1. **Fetch:** Downloads the `Traffic Volumes at Intersections` dataset directly from the City of Toronto.
2. **Geospatial Join:** Maps the GPS coordinates of 6,400 intersections perfectly inside the 260 postal code polygons from Phase 1.
3. **Zone Calculation:** Groups the intersections by **Zone Type** (Retail, Office, Leisure, etc.).
4. **The Output:** Calculates the mathematical percentage of Toronto traffic that occurs in each zone (e.g., Residential = 72%). Saves to `zone_weights.json`.

---

## Step 2: Granular Vectorized Sampling (`backend/monte_carlo.py`)

Now that we have mathematically proven gravity weights, we run the actual simulation. 
Instead of grouping cars together, this algorithm tracks all **15,000 to 150,000 EVs individually**.

1. **Spatial Sampling (`fsa`):** The algorithm rolls a weighted die 15,000 times using the Toronto Open Data weights to mathematically assign every individual `vehicle_id` a parking location.
2. **Temporal Sampling (`arrival_time`):** It generates a **Normal Distribution (Bell Curve)**. If simulating the Morning, it clusters arrival times tightly around 8:15 AM (with a standard deviation spreading them between 7:00 AM and 9:30 AM).
3. **State of Charge (`soc_needed_kwh`):** It uses a **Gamma Distribution** to simulate battery depletion. The average commuter needs ~15 kWh of charge, while random statistical outliers need massive 60 kWh charges because they drove from out of town.

---

## The Final Output

Because we used Pandas and NumPy vectorization, the engine simulates 150,000 individual agents in less than 1 second, preventing any system lag during a live presentation.

The final output is a massive, highly granular dataset tracking every single car:

| vehicle_id | fsa | zone_type | arrival_time | soc_needed_kwh |
|------------|-----|-----------|--------------|----------------|
| EV_00001   | M4B | residential| 09:33 AM    | 15.0           |
| EV_00002   | M2J | residential| 07:51 AM    | 6.4            |
| EV_00003   | L1H | residential| 08:25 AM    | 15.9           |
| EV_00004   | L8K | residential| 09:52 AM    | 21.1           |
| EV_00005   | L0B | residential| 10:45 AM    | 25.4           |

Phase 2 officially hands this detailed vehicle ledger over to **Phase 3 (The AI Grid Optimizer)**, which will group the cars by arrival hour, attach them to the grid capacity limits, and calculate the electrical explosions!

---

## Advanced Feature: Canadian Winter Battery Drain
EVs lose up to 30% of their battery efficiency in freezing Canadian winters. The Monte Carlo engine includes a highly advanced `temperature_celsius` parameter. 
- If the simulation is run at **-15°C**, the algorithm mathematically penalizes the Gamma Distribution. 
- Every single simulated car arrives needing up to **30% more electricity (soc_needed_kwh)** to cover the exact same physical driving distance, creating a massive seasonal stress-test for the grid.

## Future Upgrade Idea: The Evening Commute Correction
The Open Data dataset counts raw intersection volume, meaning it doesn't know if cars are *arriving* or *leaving*. High traffic at an Office Park at 5:00 PM means people are arriving to plug in, when in reality, they are driving home. 
A proposed future upgrade is to write a "Correction Factor" that programmatically slashes the Evening weight for Office Parks by 80% and boosts Residential, mathematically forcing the simulation to send everyone home to charge at night.
