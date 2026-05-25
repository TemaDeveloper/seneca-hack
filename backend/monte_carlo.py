import numpy as np
import pandas as pd
from typing import Literal

# Import the map database from Phase 1
from spatial_assembler import load_enriched_geodataframe

# (Power Draw math moved to Phase 3)# ---------------------------------------------------------------------------
# Probability Weights (Gravity Models)
# ---------------------------------------------------------------------------
# Defines the mathematical attraction of each zone depending on the time of day.
TIME_WEIGHTS = {
    "Morning": {
        "office_park": 4.0,   # Everyone going to work
        "retail_hub": 2.0,    # Some morning coffee/shopping
        "transit_hub": 1.0,   # Park & Ride commuters / Airport drop-offs
        "leisure": 0.5,       # Empty parks
        "residential": 0.1,   # Everyone left home
    },
    "Evening": {
        "residential": 4.0,   # Everyone going home
        "retail_hub": 3.0,    # Evening shopping/dinner
        "leisure": 2.5,       # Parks, events
        "transit_hub": 0.5,   # Late flights / pickups
        "office_park": 0.2,   # Empty offices
    }
}


class SimulationEngine:
    """
    Phase 2: Monte Carlo Simulation Engine.
    Drops simulated EVs onto the map and calculates grid stress.
    """
    
    def __init__(self):
        print("Loading Master Map Database...")
        self.base_gdf = load_enriched_geodataframe()

    def run_simulation(self, num_evs: int, time_of_day: Literal["Morning", "Evening"]) -> pd.DataFrame:
        """
        Run the Monte Carlo lottery to distribute cars and calculate grid load.
        """
        # 1. Get the probability weights for the chosen time of day
        current_weights = TIME_WEIGHTS[time_of_day]
        
        # 2. Assign a weight to every postal code based on its zone type
        fsa_weights = self.base_gdf["zone_type"].map(current_weights).fillna(0)
        
        # 3. Normalize the weights so they all add up to exactly 1.0 (a perfect probability distribution)
        probabilities = fsa_weights / fsa_weights.sum()
        
        # 4. THE MONTE CARLO LOTTERY
        # Roll a weighted die `num_evs` times to pick destinations
        print(f"Simulating {num_evs:,} electric vehicles driving in the {time_of_day}...")
        chosen_fsas = np.random.choice(
            self.base_gdf["fsa"], 
            size=num_evs, 
            p=probabilities
        )
        
        # 5. Count how many cars landed in each postal code
        ev_counts = pd.Series(chosen_fsas).value_counts().reset_index()
        ev_counts.columns = ["fsa", "ev_count"]
        
        # 6. Merge the car counts back onto the master map
        result_df = self.base_gdf.copy()
        result_df = result_df.merge(ev_counts, on="fsa", how="left")
        result_df["ev_count"] = result_df["ev_count"].fillna(0).astype(int)
        
        # (Grid Math moved to Phase 3)
        return result_df


# ---------------------------------------------------------------------------
# CLI Test Runner
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    engine = SimulationEngine()
    
    # Run a test simulation
    sim_results = engine.run_simulation(num_evs=15000, time_of_day="Morning")
    
    print("\n========================================================")
    print("SIMULATION RESULTS: 15,000 EVs (Morning Commute)")
    print("========================================================")
    
    # Sort by where the most cars went
    top_destinations = sim_results.sort_values(by="ev_count", ascending=False).head(10)
    
    # Print the clean output
    display_cols = ["fsa", "zone_type", "ev_count"]
    print(top_destinations[display_cols].to_string(index=False))
