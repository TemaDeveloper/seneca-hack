import os
import json
import numpy as np
import pandas as pd
from typing import Literal

# Import the map database from Phase 1
from spatial_assembler import load_enriched_geodataframe, DATA_DIR

# ---------------------------------------------------------------------------
# Probability Weights (Gravity Models)
# ---------------------------------------------------------------------------
WEIGHTS_JSON = os.path.join(DATA_DIR, "zone_weights.json")

def load_time_weights():
    """Load the mathematically derived zone weights calculated from Open Data."""
    if not os.path.exists(WEIGHTS_JSON):
        raise FileNotFoundError(f"Missing {WEIGHTS_JSON}. Run data_preparation/fetch_toronto_traffic.py first.")
    with open(WEIGHTS_JSON, "r") as f:
        return json.load(f)

TIME_WEIGHTS = load_time_weights()

def float_to_time(hours: float) -> str:
    """Convert a float hour (e.g. 8.5) to a formatted string (08:30 AM)."""
    hours = max(0.0, min(23.99, hours))  # clamp
    h = int(hours)
    m = int((hours - h) * 60)
    period = "AM" if h < 12 else "PM"
    h_12 = h if h <= 12 else h - 12
    h_12 = 12 if h_12 == 0 else h_12
    return f"{h_12:02d}:{m:02d} {period}"

class SimulationEngine:
    """
    Phase 2: Monte Carlo Agent-Based Simulation Engine.
    Tracks every individual EV with arrival times and battery deficiencies.
    """
    
    def __init__(self):
        print("Loading Master Map Database...")
        self.base_gdf = load_enriched_geodataframe()

    def run_simulation(self, num_evs: int, time_of_day: Literal["Morning", "Evening"]) -> pd.DataFrame:
        """
        Run the granular Monte Carlo lottery to generate thousands of individual EVs.
        """
        # 1. Get the probability weights for the chosen time of day
        current_weights = TIME_WEIGHTS[time_of_day]
        
        # 2. Assign a weight to every postal code based on its zone type
        fsa_weights = self.base_gdf["zone_type"].map(current_weights).fillna(0)
        
        # 3. Normalize the weights so they all add up to exactly 1.0 (a perfect probability distribution)
        probabilities = fsa_weights / fsa_weights.sum()
        
        # 4. SPATIAL SAMPLING (Where do they park?)
        # Roll a weighted die `num_evs` times to pick destinations
        chosen_fsas = np.random.choice(
            self.base_gdf["fsa"], 
            size=num_evs, 
            p=probabilities
        )
        
        # 5. TEMPORAL SAMPLING (When do they arrive?)
        if time_of_day == "Morning":
            # Bell curve clustered around 8:15 AM
            raw_times = np.random.normal(loc=8.25, scale=1.0, size=num_evs)
            raw_times = np.clip(raw_times, 6.0, 11.5)
        else:
            # Bell curve clustered around 5:30 PM
            raw_times = np.random.normal(loc=17.5, scale=1.5, size=num_evs)
            raw_times = np.clip(raw_times, 14.0, 21.0)
            
        formatted_times = [float_to_time(t) for t in raw_times]
        
        # 6. SOC DEFICIENCY SAMPLING (How much battery do they need?)
        # Use a Gamma distribution: Mean = 15kWh. Add base of 5kWh so minimum is 5kWh. Total Mean ~20kWh.
        soc_needed = np.random.gamma(shape=3.0, scale=5.0, size=num_evs) + 5.0
        soc_needed = np.round(soc_needed, 1)
        
        # 7. ASSEMBLE THE AGENT DATASET
        ev_df = pd.DataFrame({
            "vehicle_id": [f"EV_{str(i).zfill(5)}" for i in range(1, num_evs + 1)],
            "fsa": chosen_fsas,
            "arrival_time": formatted_times,
            "soc_needed_kwh": soc_needed
        })
        
        # Merge to attach the zone_type to the granular dataset
        ev_df = ev_df.merge(self.base_gdf[["fsa", "zone_type"]], on="fsa", how="left")
        
        # Reorder columns cleanly
        ev_df = ev_df[["vehicle_id", "fsa", "zone_type", "arrival_time", "soc_needed_kwh"]]
        
        return ev_df


# ---------------------------------------------------------------------------
# CLI Test Runner
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    engine = SimulationEngine()
    
    # Run a test simulation
    sim_results = engine.run_simulation(num_evs=15000, time_of_day="Morning")
    
    print("\n========================================================")
    print("GRANULAR SIMULATION RESULTS: 15,000 EVs (Morning Commute)")
    print("========================================================")
    
    # Print a beautiful sample of the granular dataset
    print(sim_results.head(15).to_string(index=False))
    
    print("\n[OK] Engine successfully generated 15,000 individual agents.")
    print("========================================================")
