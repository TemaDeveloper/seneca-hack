from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import pandas as pd
import math
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from spatial_assembler import load_enriched_geodataframe
from map_builder import (
    build_demand_heatmap,
    build_vulnerability_map,
    build_placement_map,
)

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
    adoption_pct: int
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
    scale_factor = params.adoption_pct / 10.0
    
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
    ev_count = int(params.adoption_pct * 30000)
    
    m1 = build_demand_heatmap(gdf, grid_df)
    m2 = build_vulnerability_map(gdf, grid_df)
    
    return {
        "ev_count": ev_count,
        "total_peak_demand_mw": float(grid_df["peak_ev_load_kw"].sum() / 1000),
        "overloaded_count": int(grid_df["overloaded"].sum()),
        "total_fsas": len(grid_df),
        "max_deficit_kw": float(grid_df["deficit_kw"].max()),
        "demand_map_html": m1._repr_html_(),
        "vulnerability_map_html": m2._repr_html_(),
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
    
    m3 = build_placement_map(gdf, grid_df, opt_df)
    
    return {
        "stations_deployed": len(opt_df),
        "total_charger_kw": float(opt_df["total_charger_kw"].sum()),
        "total_bess_kwh": int(opt_df["bess_kwh"].sum()),
        "placement_map_html": m3._repr_html_(),
        "prescriptions": opt_df[["fsa", "zone_type", "deficit_kw", "charger_type", "charger_units", "total_charger_kw", "bess_kwh"]].to_dict("records")
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
        
    m3 = build_placement_map(gdf, grid_df, opt_df)
    
    return {
        "stations_deployed": len(opt_df),
        "total_charger_kw": float(opt_df["total_charger_kw"].sum()) if not opt_df.empty else 0.0,
        "total_bess_kwh": int(opt_df["bess_kwh"].sum()) if not opt_df.empty else 0,
        "placement_map_html": m3._repr_html_(),
        "prescriptions": opt_df[["fsa", "zone_type", "deficit_kw", "charger_type", "charger_units", "total_charger_kw", "bess_kwh"]].to_dict("records") if not opt_df.empty else []
    }
