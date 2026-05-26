from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import pandas as pd
import math
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from spatial_assembler import load_enriched_geodataframe
# Map builder imports removed as we now render natively in React

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

print("Loading Master Map Database...")
gdf = load_enriched_geodataframe()
base_grid = pd.DataFrame(gdf.drop(columns='geometry'))

class SimParams(BaseModel):
    num_cars: int
    temperature: int
    time_of_day: int
    max_stations: int

class PlacementParams(BaseModel):
    fsa: str
    charger_type: str
    charger_units: int

class CustomPlacements(SimParams):
    placements: list[PlacementParams]

def _generate_mock_grid(params: SimParams):
    grid_df = base_grid.copy()
    scale_factor = params.num_cars / 30000.0
    
    # Deterministic pseudo-random load based on fsa length/hash
    grid_df["peak_ev_load_kw"] = (grid_df["proxy_capacity_kw"] * 0.4 * scale_factor).round(1)
    grid_df["baseline_load_kw"] = grid_df["proxy_capacity_kw"] * 0.6
    
    grid_df["total_load_kw"] = grid_df["peak_ev_load_kw"] + grid_df["baseline_load_kw"]
    grid_df["overloaded"] = grid_df["total_load_kw"] > grid_df["proxy_capacity_kw"]
    grid_df["deficit_kw"] = (grid_df["total_load_kw"] - grid_df["proxy_capacity_kw"]).clip(lower=0).round(1)
    
    return grid_df

@app.post("/api/simulate")
def simulate(params: SimParams):
    grid_df = _generate_mock_grid(params)
    
    import random
    ev_dict = {}
    car_id_counter = 1
    
    for _, row in grid_df.iterrows():
        fsa = row["fsa"]
        zone_type = row["zone_type"]
        
        # Calculate how many cars are theoretically in this FSA based on peak load (assume 7kW per EV)
        theoretical_evs = int(row["peak_ev_load_kw"] / 7.0)
        
        mock_cars = []
        for _ in range(min(5, theoretical_evs)):
            arr_hour = random.uniform(14.0, 19.0)
            
            # Format time manually since float_to_time is not imported here
            h = int(arr_hour)
            m = int((arr_hour - h) * 60)
            period = "AM" if h < 12 else "PM"
            h_12 = h if h <= 12 else h - 12
            h_12 = 12 if h_12 == 0 else h_12
            formatted_time = f"{h_12:02d}:{m:02d} {period}"
            
            soc = round(random.uniform(15.0, 45.0), 1)
            
            mock_cars.append({
                "vehicle_id": f"EV_{str(car_id_counter).zfill(5)}",
                "fsa": fsa,
                "zone_type": zone_type,
                "arrival_time": formatted_time,
                "arrival_hour_float": round(arr_hour, 2),
                "soc_needed_kwh": soc
            })
            car_id_counter += 1
        ev_dict[fsa] = {
            "total_fsa_cars": theoretical_evs,
            "sample_cars": mock_cars
        }
    
    return {
        "ev_count": params.num_cars,
        "total_peak_demand_mw": float(grid_df["peak_ev_load_kw"].sum() / 1000),
        "overloaded_count": int(grid_df["overloaded"].sum()),
        "total_fsas": len(grid_df),
        "max_deficit_kw": float(grid_df["deficit_kw"].max()),
        "grid_data": grid_df.to_dict(orient="records"),
        "ev_data": ev_dict
    }

@app.post("/api/optimize")
def optimize(params: SimParams):
    grid_df = _generate_mock_grid(params)
    
    candidates = grid_df[grid_df["overloaded"]].sort_values("deficit_kw", ascending=False)
    opt_df = candidates.head(params.max_stations).copy()
    
    opt_df["charger_type"] = "DC Fast Charging Array"
    opt_df["charger_kw_per_unit"] = 50
    opt_df["charger_units"] = (opt_df["deficit_kw"] / 50).apply(math.ceil).clip(lower=1)
    opt_df["total_charger_kw"] = opt_df["charger_units"] * 50
    opt_df["bess_kwh"] = (opt_df["deficit_kw"] * 2).astype(int)
    
    return {
        "stations_deployed": len(opt_df),
        "total_charger_kw": float(opt_df["total_charger_kw"].sum()),
        "total_bess_kwh": int(opt_df["bess_kwh"].sum()),
        "prescriptions": opt_df[["fsa", "zone_type", "deficit_kw", "centroid_lat", "centroid_lon", "charger_type", "charger_units", "total_charger_kw", "bess_kwh"]].to_dict("records")
    }

@app.post("/api/custom_placement")
def custom_placement(params: CustomPlacements):
    grid_df = _generate_mock_grid(params)
    
    placements = pd.DataFrame([p.model_dump() for p in params.placements])
    if placements.empty:
        opt_df = pd.DataFrame(columns=["fsa", "zone_type", "deficit_kw", "centroid_lat", "centroid_lon", "charger_type", "charger_units", "charger_kw_per_unit", "total_charger_kw", "bess_kwh"])
    else:
        opt_df = pd.merge(placements, grid_df[["fsa", "zone_type", "centroid_lat", "centroid_lon", "deficit_kw"]], on="fsa", how="left")
        opt_df["deficit_kw"] = opt_df["deficit_kw"].fillna(0)
        opt_df["charger_kw_per_unit"] = 50
        opt_df["total_charger_kw"] = opt_df["charger_units"] * opt_df["charger_kw_per_unit"]
        opt_df["bess_kwh"] = (opt_df["deficit_kw"] * 2).astype(int)
        
    return {
        "stations_deployed": len(opt_df),
        "total_charger_kw": float(opt_df["total_charger_kw"].sum()) if not opt_df.empty else 0.0,
        "total_bess_kwh": int(opt_df["bess_kwh"].sum()) if not opt_df.empty else 0,
        "prescriptions": opt_df[["fsa", "zone_type", "deficit_kw", "centroid_lat", "centroid_lon", "charger_type", "charger_units", "total_charger_kw", "bess_kwh"]].to_dict("records") if not opt_df.empty else []
    }
