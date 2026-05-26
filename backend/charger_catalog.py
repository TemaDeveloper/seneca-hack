"""
EV charger catalog mapped to FSA and road-network nodes.

Primary source is the AFDC/NREL public station API, cached locally as CSV. OSM
`amenity=charging_station` is an optional enrichment source. If no real cache
exists, deterministic proxy public chargers by FSA zone keep offline tests
stable. Home/work private charging is modeled separately as access probabilities.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import requests
from monte_carlo import CHARGER_POWER_KW
from road_network import RoadNetwork, haversine_km


DATA_DIR = Path(__file__).resolve().parent / "data"
CACHE_DIR = DATA_DIR / "cache"
OSM_CHARGERS_CSV = CACHE_DIR / "osm_ev_chargers.csv"
AFDC_CHARGERS_CSV = CACHE_DIR / "afdc_on_ev_chargers.csv"
AFDC_STATIONS_URL = "https://developer.nrel.gov/api/alt-fuel-stations/v1.json"


PUBLIC_CHARGERS_PER_FSA_BY_ZONE = {
    "residential": 0.35,
    "leisure": 1.25,
    "office_park": 2.25,
    "retail_hub": 3.50,
    "transit_hub": 5.00,
}


@dataclass(frozen=True)
class ChargerChoice:
    charger_id: str
    fsa: str
    fsa_idx: int
    zone_type: str
    lat: float
    lon: float
    charger_kw: float
    source: str
    detour_km: float
    road_node_id: int | None = None
    road_snap_distance_m: float = float("nan")


CHARGER_COLUMNS = [
    "charger_id",
    "fsa",
    "fsa_idx",
    "zone_type",
    "lat",
    "lon",
    "charger_kw",
    "source",
]


def load_or_fetch_osm_chargers(fsa_gdf: gpd.GeoDataFrame, *, force_download: bool = False) -> pd.DataFrame:
    """
    Load cached OSM chargers or fetch them for the supplied FSA union polygon.

    Live fetching is intentionally opt-in because Overpass availability should
    not control local tests or basic simulator execution.
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    if OSM_CHARGERS_CSV.exists() and not force_download:
        return pd.read_csv(OSM_CHARGERS_CSV)

    import osmnx as ox

    ox.settings.use_cache = True
    if hasattr(ox.settings, "requests_timeout"):
        ox.settings.requests_timeout = 300

    polygon = fsa_gdf.to_crs(epsg=4326).geometry.union_all()
    features = ox.features_from_polygon(polygon, {"amenity": "charging_station"})
    if features.empty:
        chargers = pd.DataFrame(columns=CHARGER_COLUMNS)
        chargers.to_csv(OSM_CHARGERS_CSV, index=False)
        return chargers

    points = features.copy()
    points["geometry"] = points.geometry.representative_point()
    points = points.set_geometry("geometry").set_crs(epsg=4326, allow_override=True)
    joined = gpd.sjoin(
        points[["geometry"]],
        fsa_gdf[["fsa", "zone_type", "geometry"]].to_crs(epsg=4326),
        predicate="within",
        how="inner",
    ).reset_index(drop=True)
    if joined.empty:
        chargers = pd.DataFrame(columns=CHARGER_COLUMNS)
    else:
        chargers = pd.DataFrame({
            "charger_id": [f"OSM_{i:05d}" for i in range(len(joined))],
            "fsa": joined["fsa"].astype(str).to_numpy(),
            "zone_type": joined["zone_type"].astype(str).to_numpy(),
            "lat": joined.geometry.y.to_numpy(dtype=float),
            "lon": joined.geometry.x.to_numpy(dtype=float),
            "source": "osm",
        })
        chargers["charger_kw"] = chargers["zone_type"].map(CHARGER_POWER_KW).fillna(50.0)
        fsa_to_idx = {fsa: i for i, fsa in enumerate(fsa_gdf["fsa"].astype(str))}
        chargers["fsa_idx"] = chargers["fsa"].map(fsa_to_idx).astype(int)
        chargers = chargers[CHARGER_COLUMNS]

    chargers.to_csv(OSM_CHARGERS_CSV, index=False)
    return chargers


def load_or_fetch_afdc_chargers(fsa_gdf: gpd.GeoDataFrame, *, force_download: bool = False) -> pd.DataFrame:
    """Load/cache public Ontario EV chargers from the AFDC/NREL station API."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    if AFDC_CHARGERS_CSV.exists() and not force_download:
        return pd.read_csv(AFDC_CHARGERS_CSV)

    api_key = os.environ.get("NREL_API_KEY", "DEMO_KEY")
    response = requests.get(
        AFDC_STATIONS_URL,
        params={
            "api_key": api_key,
            "fuel_type": "ELEC",
            "country": "CA",
            "state": "ON",
            "access": "public",
            "limit": "all",
        },
        timeout=60,
    )
    response.raise_for_status()
    stations = response.json().get("fuel_stations", [])
    if not stations:
        chargers = pd.DataFrame(columns=CHARGER_COLUMNS)
        chargers.to_csv(AFDC_CHARGERS_CSV, index=False)
        return chargers

    raw = pd.DataFrame(stations)
    raw = raw.dropna(subset=["latitude", "longitude"]).copy()
    points = gpd.GeoDataFrame(
        raw,
        geometry=gpd.points_from_xy(raw["longitude"].astype(float), raw["latitude"].astype(float)),
        crs="EPSG:4326",
    )
    joined = gpd.sjoin(
        points,
        fsa_gdf[["fsa", "zone_type", "geometry"]].to_crs(epsg=4326),
        predicate="within",
        how="inner",
    ).reset_index(drop=True)
    if joined.empty:
        chargers = pd.DataFrame(columns=CHARGER_COLUMNS)
    else:
        fsa_to_idx = {fsa: i for i, fsa in enumerate(fsa_gdf["fsa"].astype(str))}
        chargers = pd.DataFrame({
            "charger_id": "AFDC_" + joined["id"].astype(str),
            "fsa": joined["fsa"].astype(str),
            "fsa_idx": joined["fsa"].astype(str).map(fsa_to_idx).astype(int),
            "zone_type": joined["zone_type"].astype(str),
            "lat": joined["latitude"].astype(float),
            "lon": joined["longitude"].astype(float),
            "charger_kw": joined.apply(_afdc_station_power_kw, axis=1).astype(float),
            "source": "afdc",
        })
        chargers = chargers.drop_duplicates(subset=["charger_id"]).reset_index(drop=True)
        chargers = chargers[CHARGER_COLUMNS]

    chargers.to_csv(AFDC_CHARGERS_CSV, index=False)
    return chargers


class ChargerCatalog:
    def __init__(self, fsa_gdf: gpd.GeoDataFrame, *, source: str = "auto", force_osm_download: bool = False):
        self.fsa_gdf = fsa_gdf.reset_index(drop=True).copy()
        self.source_requested = source
        self.chargers = self._load_chargers(source, force_osm_download)
        self.public = self.chargers.reset_index(drop=True)
        self._fsa_road_node_ids: np.ndarray | None = None
        self._fsa_road_snap_m: np.ndarray | None = None

    def snap_to_road_network(self, road_network: RoadNetwork) -> None:
        """Attach nearest road-node ids to public chargers and FSA private-charger proxies."""
        fsa_nodes, fsa_snap_m = road_network.nearest_graph_nodes(self.fsa_gdf["centroid_lat"], self.fsa_gdf["centroid_lon"])
        self._fsa_road_node_ids = fsa_nodes
        self._fsa_road_snap_m = fsa_snap_m

        if self.chargers.empty:
            self.chargers = self.chargers.assign(road_node_id=pd.Series(dtype="Int64"), road_snap_distance_m=pd.Series(dtype=float))
            self.public = self.chargers.reset_index(drop=True)
            return

        nodes, snap_m = road_network.nearest_graph_nodes(self.chargers["lat"], self.chargers["lon"])
        snapped = self.chargers.copy()
        snapped["road_node_id"] = pd.Series(nodes, index=snapped.index, dtype="Int64")
        snapped["road_snap_distance_m"] = snap_m
        self.chargers = snapped
        self.public = snapped.reset_index(drop=True)

    def choose_activity_charger(self, fsa_idx: int, activity: str) -> ChargerChoice:
        fsa_idx = int(fsa_idx)
        zone_type = str(self.fsa_gdf.iloc[fsa_idx]["zone_type"])
        fsa = str(self.fsa_gdf.iloc[fsa_idx]["fsa"])
        private = activity in {"home", "work", "school"}
        if private:
            road_node_id, road_snap_distance_m = self._road_node_for_fsa(fsa_idx)
            return ChargerChoice(
                charger_id=f"private_{activity}_{fsa}",
                fsa=fsa,
                fsa_idx=fsa_idx,
                zone_type=zone_type,
                lat=float(self.fsa_gdf.iloc[fsa_idx]["centroid_lat"]),
                lon=float(self.fsa_gdf.iloc[fsa_idx]["centroid_lon"]),
                charger_kw=7.0,
                source="private",
                detour_km=0.0,
                road_node_id=road_node_id,
                road_snap_distance_m=road_snap_distance_m,
            )
        return self.nearest_public_to_fsa(fsa_idx)

    def nearest_public_to_fsa(self, fsa_idx: int) -> ChargerChoice:
        fsa_idx = int(fsa_idx)
        lat = float(self.fsa_gdf.iloc[fsa_idx]["centroid_lat"])
        lon = float(self.fsa_gdf.iloc[fsa_idx]["centroid_lon"])
        return self.nearest_public_to_point(lat, lon)

    def nearest_public_to_route(self, route_fsa_indices: tuple[int, ...], origin_idx: int, dest_idx: int) -> ChargerChoice:
        origin = self.fsa_gdf.iloc[int(origin_idx)]
        dest = self.fsa_gdf.iloc[int(dest_idx)]
        mid_lat = (float(origin["centroid_lat"]) + float(dest["centroid_lat"])) / 2
        mid_lon = (float(origin["centroid_lon"]) + float(dest["centroid_lon"])) / 2

        if self.public.empty:
            return self.nearest_public_to_point(mid_lat, mid_lon)
        route_set = set(int(i) for i in route_fsa_indices)
        candidates = self.public[self.public["fsa_idx"].isin(route_set)]
        if not candidates.empty:
            return self._best_public_patch_choice(candidates, mid_lat, mid_lon, detour_factor=1.5)

        choice = self.nearest_public_to_point(mid_lat, mid_lon)
        direct_detour = min(
            float(haversine_km(mid_lat, mid_lon, choice.lat, choice.lon)) * 2.0,
            12.0,
        )
        return ChargerChoice(**{**choice.__dict__, "detour_km": max(1.0, direct_detour)})

    def nearest_public_to_point(self, lat: float, lon: float) -> ChargerChoice:
        if self.public.empty:
            idx = 0
            row = self._fallback_charger_row(idx)
            detour = float(haversine_km(lat, lon, row["lat"], row["lon"])) * 2.0
            return self._row_to_choice(pd.Series(row), detour_km=detour)
        dist = haversine_km(self.public["lat"].to_numpy(dtype=float), self.public["lon"].to_numpy(dtype=float), lat, lon)
        row = self.public.iloc[int(np.nanargmin(dist))]
        return self._row_to_choice(row, detour_km=float(np.nanmin(dist)) * 2.0)

    def _best_public_patch_choice(self, candidates: pd.DataFrame, lat: float, lon: float, *, detour_factor: float) -> ChargerChoice:
        distances = haversine_km(
            candidates["lat"].to_numpy(dtype=float),
            candidates["lon"].to_numpy(dtype=float),
            lat,
            lon,
        )
        work = candidates.copy()
        work["_distance_km"] = distances
        min_distance = float(work["_distance_km"].min())
        fast = work[work["charger_kw"].astype(float) >= 50.0]
        if not fast.empty:
            eligible_fast = fast[(fast["_distance_km"] <= min_distance + 4.0) | (fast["_distance_km"] <= 8.0)]
            if not eligible_fast.empty:
                work = eligible_fast
        power_bonus = np.log1p(work["charger_kw"].astype(float).to_numpy()) / np.log1p(350.0)
        score = work["_distance_km"].to_numpy(dtype=float) - 3.0 * power_bonus
        row = work.iloc[int(np.nanargmin(score))]
        return self._row_to_choice(row, detour_km=max(0.5, float(row["_distance_km"]) * detour_factor))

    def summary(self) -> dict[str, object]:
        return {
            "charger_count": int(len(self.chargers)),
            "source_mix": self.chargers["source"].value_counts().to_dict() if not self.chargers.empty else {},
            "zone_mix": self.chargers["zone_type"].value_counts().to_dict() if not self.chargers.empty else {},
        }

    def _load_chargers(self, source: str, force_osm_download: bool) -> pd.DataFrame:
        if source in {"auto", "afdc"}:
            try:
                if source == "auto" and not AFDC_CHARGERS_CSV.exists() and not force_osm_download:
                    raise FileNotFoundError("No cached AFDC charger catalog; using zone proxy chargers")
                chargers = load_or_fetch_afdc_chargers(self.fsa_gdf, force_download=force_osm_download)
                if not chargers.empty:
                    return chargers[CHARGER_COLUMNS]
                if source == "afdc":
                    raise RuntimeError("AFDC charger query returned no chargers in the FSA boundary set")
            except Exception:
                if source == "afdc":
                    raise
        if source in {"auto", "osm"}:
            try:
                if source == "auto" and not OSM_CHARGERS_CSV.exists() and not force_osm_download:
                    raise FileNotFoundError("No cached OSM charger catalog; using zone proxy chargers")
                chargers = load_or_fetch_osm_chargers(self.fsa_gdf, force_download=force_osm_download)
                if not chargers.empty:
                    return chargers[CHARGER_COLUMNS]
                if source == "osm":
                    raise RuntimeError("OSM charger query returned no chargers")
            except Exception:
                if source == "osm":
                    raise
        return self._build_fallback_chargers()

    def _build_fallback_chargers(self) -> pd.DataFrame:
        rows = []
        for fsa_idx, row in self.fsa_gdf.iterrows():
            zone = str(row["zone_type"])
            expected = PUBLIC_CHARGERS_PER_FSA_BY_ZONE.get(zone, 0.5)
            count = max(0, int(np.floor(expected)))
            if expected - count >= 0.25:
                count += 1
            for j in range(count):
                offset = (j - (count - 1) / 2) * 0.003
                rows.append({
                    "charger_id": f"proxy_{row['fsa']}_{j+1}",
                    "fsa": str(row["fsa"]),
                    "fsa_idx": int(fsa_idx),
                    "zone_type": zone,
                    "lat": float(row["centroid_lat"]) + offset,
                    "lon": float(row["centroid_lon"]) - offset,
                    "charger_kw": float(CHARGER_POWER_KW.get(zone, 50.0)),
                    "source": "zone_proxy",
                })
        if not rows:
            rows.append(self._fallback_charger_row(0))
        return pd.DataFrame(rows, columns=CHARGER_COLUMNS)

    def _fallback_charger_row(self, fsa_idx: int) -> dict[str, object]:
        row = self.fsa_gdf.iloc[int(fsa_idx)]
        zone = str(row["zone_type"])
        return {
            "charger_id": f"proxy_{row['fsa']}_1",
            "fsa": str(row["fsa"]),
            "fsa_idx": int(fsa_idx),
            "zone_type": zone,
            "lat": float(row["centroid_lat"]),
            "lon": float(row["centroid_lon"]),
            "charger_kw": float(CHARGER_POWER_KW.get(zone, 50.0)),
            "source": "zone_proxy",
        }

    @staticmethod
    def _row_to_choice(row: pd.Series, detour_km: float) -> ChargerChoice:
        node_raw = row.get("road_node_id")
        road_node_id = None if node_raw is None or pd.isna(node_raw) else int(node_raw)
        snap_raw = row.get("road_snap_distance_m")
        road_snap_distance_m = float("nan") if snap_raw is None or pd.isna(snap_raw) else float(snap_raw)
        return ChargerChoice(
            charger_id=str(row["charger_id"]),
            fsa=str(row["fsa"]),
            fsa_idx=int(row["fsa_idx"]),
            zone_type=str(row["zone_type"]),
            lat=float(row["lat"]),
            lon=float(row["lon"]),
            charger_kw=float(row["charger_kw"]),
            source=str(row["source"]),
            detour_km=float(max(detour_km, 0.0)),
            road_node_id=road_node_id,
            road_snap_distance_m=road_snap_distance_m,
        )

    def _road_node_for_fsa(self, fsa_idx: int) -> tuple[int | None, float]:
        if self._fsa_road_node_ids is None or self._fsa_road_snap_m is None:
            return None, float("nan")
        return int(self._fsa_road_node_ids[int(fsa_idx)]), float(self._fsa_road_snap_m[int(fsa_idx)])


def _afdc_station_power_kw(row: pd.Series) -> float:
    powers: list[float] = []
    units = row.get("ev_charging_units")
    if isinstance(units, list):
        for unit in units:
            connectors = unit.get("connectors", {}) if isinstance(unit, dict) else {}
            for connector in connectors.values():
                if isinstance(connector, dict) and connector.get("port_count", 0):
                    power = connector.get("power_kw")
                    if power is not None:
                        powers.append(float(power))
            level = str(unit.get("charging_level", "")).lower() if isinstance(unit, dict) else ""
            if level in {"dc_fast", "dc fast", "3"}:
                powers.append(150.0)
            elif level in {"2", "level 2"}:
                powers.append(7.0)
    if pd.notna(row.get("ev_dc_fast_num")) and float(row.get("ev_dc_fast_num")) > 0:
        powers.append(150.0)
    if pd.notna(row.get("ev_level2_evse_num")) and float(row.get("ev_level2_evse_num")) > 0:
        powers.append(7.0)
    if pd.notna(row.get("ev_level1_evse_num")) and float(row.get("ev_level1_evse_num")) > 0:
        powers.append(1.4)
    return max(powers) if powers else 50.0
