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

def _load_time_weights():
    """Load the mathematically derived zone weights calculated from Open Data."""
    if not os.path.exists(WEIGHTS_JSON):
        raise FileNotFoundError(f"Missing {WEIGHTS_JSON}. Run data_preparation/fetch_toronto_traffic.py first.")
    with open(WEIGHTS_JSON, "r") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Simulation Constants (Magic Numbers)
# ---------------------------------------------------------------------------
# Temporal (Arrival Time) Constants
MORNING_PEAK_HOUR = 8.25        # 8:15 AM
MORNING_PEAK_STD = 1.0          # 1 hour standard deviation
MORNING_START_CLIP = 6.0        # Earliest arrival 6:00 AM
MORNING_END_CLIP = 11.5         # Latest arrival 11:30 AM

EVENING_PEAK_HOUR = 17.5        # 5:30 PM
EVENING_PEAK_STD = 1.5          # 1.5 hours standard deviation
EVENING_START_CLIP = 14.0       # Earliest arrival 2:00 PM
EVENING_END_CLIP = 21.0         # Latest arrival 9:00 PM

# State of Charge (Battery) Constants
SOC_GAMMA_SHAPE = 3.0
SOC_GAMMA_SCALE = 5.0
SOC_BASE_KWH = 5.0

# Winter Physics Constants
OPTIMAL_TEMP_C = 20.0           # Ideal battery temperature
TEMP_EFFICIENCY_LOSS_PER_DEGREE = 0.0085  # ~0.85% loss per degree below optimal
MAX_EFFICIENCY_LOSS = 0.40      # Cap maximum efficiency loss at 40%

# Charger Power Draw Constants (kW)
CHARGER_POWER_KW = {
    "residential": 7.0,     # Level 2 home charger
    "leisure":     7.0,     # Level 2 public charger
    "office_park": 50.0,    # DC Fast charger at workplace
    "retail_hub":  50.0,    # DC Fast charger at mall
    "transit_hub": 150.0,   # Ultra-fast charger at transit hub
}

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
        self.time_weights = _load_time_weights()
        self.ieso_profile = self._load_ieso_profile()

    @staticmethod
    def _load_ieso_profile() -> pd.DataFrame:
        """Load the 24-hour IESO baseline load profile."""
        ieso_path = os.path.join(DATA_DIR, "ieso_load_profile.csv")
        if not os.path.exists(ieso_path):
            raise FileNotFoundError(f"Missing {ieso_path}. Run data_preparation/prepare_data.py first.")
        return pd.read_csv(ieso_path)

    def run_simulation(self, num_evs: int, time_of_day: Literal["Morning", "Evening"], temperature_celsius: float = 20.0) -> pd.DataFrame:
        """
        Run the granular Monte Carlo lottery to generate thousands of individual EVs.
        """
        # 1. Get the probability weights for the chosen time of day
        current_weights = self.time_weights[time_of_day]
        
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
            # Bell curve clustered around Morning Peak
            raw_times = np.random.normal(loc=MORNING_PEAK_HOUR, scale=MORNING_PEAK_STD, size=num_evs)
            raw_times = np.clip(raw_times, MORNING_START_CLIP, MORNING_END_CLIP)
        else:
            # Bell curve clustered around Evening Peak
            raw_times = np.random.normal(loc=EVENING_PEAK_HOUR, scale=EVENING_PEAK_STD, size=num_evs)
            raw_times = np.clip(raw_times, EVENING_START_CLIP, EVENING_END_CLIP)
            
        formatted_times = [float_to_time(t) for t in raw_times]
        
        # 6. SOC DEFICIENCY SAMPLING (How much battery do they need?)
        soc_needed = np.random.gamma(shape=SOC_GAMMA_SHAPE, scale=SOC_GAMMA_SCALE, size=num_evs) + SOC_BASE_KWH
        
        # Apply Canadian Winter Battery Drain (Efficiency Loss)
        if temperature_celsius < OPTIMAL_TEMP_C:
            temp_diff = OPTIMAL_TEMP_C - temperature_celsius
            efficiency_loss = min(MAX_EFFICIENCY_LOSS, temp_diff * TEMP_EFFICIENCY_LOSS_PER_DEGREE)
            efficiency_factor = 1.0 - efficiency_loss
            
            # The car must pull MORE electricity to cover the exact same driving distance
            soc_needed = soc_needed / efficiency_factor
            
        soc_needed = np.round(soc_needed, 1)
        
        # 7. ASSEMBLE THE AGENT DATASET
        ev_df = pd.DataFrame({
            "vehicle_id": [f"EV_{str(i).zfill(5)}" for i in range(1, num_evs + 1)],
            "fsa": chosen_fsas,
            "arrival_time": formatted_times,
            "arrival_hour_float": raw_times,
            "soc_needed_kwh": soc_needed
        })

        # Merge to attach the zone_type to the granular dataset
        ev_df = ev_df.merge(self.base_gdf[["fsa", "zone_type"]], on="fsa", how="left")

        # Reorder columns cleanly
        ev_df = ev_df[["vehicle_id", "fsa", "zone_type", "arrival_time", "arrival_hour_float", "soc_needed_kwh"]]
        
        return ev_df

    def aggregate_grid_load(self, ev_df: pd.DataFrame) -> pd.DataFrame:
        """
        Aggregate individual EV records into per-FSA grid load results.

        Pipeline:
            1. Assign charger power draw (kW) based on zone type
            2. Extract arrival hour as integer
            3. GroupBy (FSA, hour) to compute concurrent load
            4. Find peak hour per FSA
            5. Add IESO baseline load
            6. Run grid inequality test

        Returns:
            DataFrame with columns: fsa, zone_type, proxy_capacity_kw,
            peak_hour, peak_ev_load_kw, baseline_load_kw, total_load_kw,
            overloaded, deficit_kw, centroid_lat, centroid_lon
        """
        # 1. Assign charger power draw based on zone type
        ev_df = ev_df.copy()
        ev_df["charger_kw"] = ev_df["zone_type"].map(CHARGER_POWER_KW).fillna(7.0)

        # 2. Extract arrival hour as integer for grouping
        ev_df["arrival_hour"] = ev_df["arrival_hour_float"].astype(int)

        # 3. GroupBy (FSA, hour) to get concurrent charger draw
        hourly_load = ev_df.groupby(["fsa", "arrival_hour"])["charger_kw"].sum().reset_index()
        hourly_load.rename(columns={"charger_kw": "ev_load_kw"}, inplace=True)

        # 4. Find peak hour per FSA (hour with max EV load)
        idx_peak = hourly_load.groupby("fsa")["ev_load_kw"].idxmax()
        peak_load = hourly_load.loc[idx_peak].rename(
            columns={"ev_load_kw": "peak_ev_load_kw", "arrival_hour": "peak_hour"}
        )

        # 5. Merge with base GeoDataFrame to get capacity and coordinates
        result = peak_load.merge(
            self.base_gdf[["fsa", "zone_type", "proxy_capacity_kw", "centroid_lat", "centroid_lon"]],
            on="fsa", how="left"
        )

        # 6. Add IESO baseline load at peak hour
        ieso_lookup = self.ieso_profile.set_index("hour")["load_fraction"]
        result["baseline_load_kw"] = result.apply(
            lambda r: ieso_lookup.get(int(r["peak_hour"]), 0.85) * r["proxy_capacity_kw"],
            axis=1
        ).round(1)

        # 7. Grid inequality test
        result["total_load_kw"] = (result["peak_ev_load_kw"] + result["baseline_load_kw"]).round(1)
        result["overloaded"] = result["total_load_kw"] > result["proxy_capacity_kw"]
        result["deficit_kw"] = (result["total_load_kw"] - result["proxy_capacity_kw"]).clip(lower=0).round(1)

        # Clean column order
        result = result[["fsa", "zone_type", "proxy_capacity_kw", "peak_hour",
                         "peak_ev_load_kw", "baseline_load_kw", "total_load_kw",
                         "overloaded", "deficit_kw", "centroid_lat", "centroid_lon"]]

        return result.sort_values("deficit_kw", ascending=False).reset_index(drop=True)


# ---------------------------------------------------------------------------
# CLI Test Runner
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    engine = SimulationEngine()
    
    # Run a test simulation in deep Canadian Winter
    sim_results = engine.run_simulation(num_evs=15000, time_of_day="Morning", temperature_celsius=-15.0)
    
    print("\n========================================================")
    print("GRANULAR SIMULATION RESULTS: 15,000 EVs (Morning Commute @ -15°C)")
    print("========================================================")
    
    # Print a beautiful sample of the granular dataset
    print(sim_results.head(15).to_string(index=False))
    
    print("\n[OK] Engine successfully generated 15,000 individual agents.")
    print("========================================================")
