"""
Road-network routing support for the mobility simulator.

The preferred backend is an OSMnx drive graph cached on disk. Test and offline
runs use a deterministic FSA-adjacency graph built from the supplied hackathon
FSA polygons. Both backends expose the same FSA-to-FSA route matrix so the
agent model can account for network distance, travel time, and route paths.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import pickle
from typing import Iterable

import geopandas as gpd
import networkx as nx
import numpy as np
import pandas as pd
from scipy.spatial import cKDTree


DATA_DIR = Path(__file__).resolve().parent / "data"
CACHE_DIR = DATA_DIR / "cache"
OSM_GRAPHML = CACHE_DIR / "gta_drive.graphml"
OSM_GRAPH_PICKLE = CACHE_DIR / "gta_drive_graph.pkl"
OSM_ROUTE_CACHE = CACHE_DIR / "gta_fsa_routes_osm.pkl"
FSA_ROUTE_CACHE = CACHE_DIR / "gta_fsa_routes_adjacency.pkl"
OSM_EDGE_TEMPLATE_CACHE = CACHE_DIR / "gta_fsa_edge_templates_osm.pkl"
FSA_EDGE_TEMPLATE_CACHE = CACHE_DIR / "gta_fsa_edge_templates_adjacency.pkl"
ROUTE_CACHE_VERSION = 3
OSM_GRAPH_PICKLE_VERSION = 1
EDGE_TEMPLATE_CACHE_VERSION = 2


FALLBACK_SPEED_KPH_BY_ZONE = {
    "residential": 34.0,
    "leisure": 34.0,
    "office_park": 42.0,
    "retail_hub": 38.0,
    "transit_hub": 48.0,
}

OSM_SPEED_KPH_BY_HIGHWAY = {
    "motorway": 95.0,
    "trunk": 80.0,
    "primary": 60.0,
    "secondary": 50.0,
    "tertiary": 45.0,
    "unclassified": 40.0,
    "residential": 32.0,
    "living_street": 20.0,
    "service": 24.0,
}


@dataclass(frozen=True)
class RouteResult:
    origin_idx: int
    dest_idx: int
    distance_km: float
    freeflow_time_h: float
    path: tuple[int, ...]
    reachable: bool


@dataclass(frozen=True)
class RouteEdgeSegment:
    u: int
    v: int
    distance_km: float
    freeflow_time_h: float


@dataclass(frozen=True)
class RouteEdgeTemplate:
    segments: tuple[RouteEdgeSegment, ...]
    fsa_indices: tuple[int, ...]
    distance_km: float
    freeflow_time_h: float


@dataclass(frozen=True)
class RoadNetworkSummary:
    source: str
    node_count: int
    edge_count: int
    component_count: int
    isolated_nodes: int
    unreachable_od_pairs: int
    median_snap_distance_m: float
    p95_snap_distance_m: float
    median_circuity: float
    p90_circuity: float


def haversine_km(lat1: float | np.ndarray, lon1: float | np.ndarray, lat2: float | np.ndarray, lon2: float | np.ndarray) -> float | np.ndarray:
    lat1_rad = np.radians(lat1)
    lon1_rad = np.radians(lon1)
    lat2_rad = np.radians(lat2)
    lon2_rad = np.radians(lon2)
    dlat = lat2_rad - lat1_rad
    dlon = lon2_rad - lon1_rad
    a = np.sin(dlat / 2) ** 2 + np.cos(lat1_rad) * np.cos(lat2_rad) * np.sin(dlon / 2) ** 2
    return 6371.0 * 2.0 * np.arcsin(np.sqrt(a))


def load_or_fetch_osm_drive_graph(fsa_gdf: gpd.GeoDataFrame, *, force_download: bool = False) -> nx.MultiDiGraph:
    """
    Load a cached OSMnx graph or fetch one for the FSA union polygon.

    This is intentionally not called by tests. Fetching GTA-wide OSM data is
    network- and Overpass-dependent, so production/research runs should call it
    once, cache the GraphML, then reuse the cache.
    """
    import osmnx as ox

    ox.settings.use_cache = True
    if hasattr(ox.settings, "requests_timeout"):
        ox.settings.requests_timeout = 300

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    if OSM_GRAPH_PICKLE.exists() and not force_download:
        try:
            return _load_osm_graph_pickle(OSM_GRAPH_PICKLE)
        except Exception:
            OSM_GRAPH_PICKLE.unlink(missing_ok=True)

    if OSM_GRAPHML.exists() and not force_download:
        graph = ox.load_graphml(OSM_GRAPHML)
        _save_osm_graph_pickle(graph, OSM_GRAPH_PICKLE)
        return graph

    polygon = fsa_gdf.to_crs(epsg=4326).geometry.union_all()
    graph = ox.graph_from_polygon(polygon, network_type="drive", simplify=True, retain_all=False)
    graph = ox.add_edge_speeds(graph, fallback=40.0)
    graph = ox.add_edge_travel_times(graph)
    ox.save_graphml(graph, OSM_GRAPHML)
    _save_osm_graph_pickle(graph, OSM_GRAPH_PICKLE)
    return graph


def _load_osm_graph_pickle(path: Path) -> nx.MultiDiGraph:
    with path.open("rb") as fh:
        cached = pickle.load(fh)
    if not isinstance(cached, dict) or cached.get("version") != OSM_GRAPH_PICKLE_VERSION:
        raise ValueError("stale OSM graph pickle")
    graph = cached.get("graph")
    if not isinstance(graph, nx.MultiDiGraph):
        raise ValueError("invalid OSM graph pickle")
    return graph


def _save_osm_graph_pickle(graph: nx.MultiDiGraph, path: Path) -> None:
    _atomic_pickle_dump({"version": OSM_GRAPH_PICKLE_VERSION, "graph": graph}, path)


def _atomic_pickle_dump(payload: object, path: Path) -> None:
    tmp_path = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    with tmp_path.open("wb") as fh:
        pickle.dump(payload, fh, protocol=pickle.HIGHEST_PROTOCOL)
    tmp_path.replace(path)


class RoadNetwork:
    """FSA-level route matrix over either a real OSM graph or an offline fallback graph."""

    def __init__(self, fsa_gdf: gpd.GeoDataFrame, *, source: str = "auto", force_osm_download: bool = False):
        self.fsa_gdf = fsa_gdf.reset_index(drop=True).copy()
        self.fsas = self.fsa_gdf["fsa"].to_numpy()
        self.zone_types = self.fsa_gdf["zone_type"].to_numpy()
        self.centroid_lat = self.fsa_gdf["centroid_lat"].to_numpy(dtype=float)
        self.centroid_lon = self.fsa_gdf["centroid_lon"].to_numpy(dtype=float)
        self.source_requested = source
        self.source = "fsa_adjacency"
        self.graph: nx.Graph | nx.MultiDiGraph
        self.fsa_node_ids: list[int]
        self.snap_distance_m = np.zeros(len(self.fsa_gdf), dtype=float)
        self._osm_node_to_fsa: dict[int, int] = {}
        self._edge_segment_cache: dict[tuple[int, ...], tuple[RouteEdgeSegment, ...]] = {}
        self._edge_template_cache: dict[tuple[int, int], RouteEdgeTemplate] = {}
        self._edge_template_cache_dirty = False

        if source in {"auto", "osm"}:
            try:
                if source == "auto" and not OSM_GRAPHML.exists() and not force_osm_download:
                    raise FileNotFoundError("No cached OSM graph; using offline FSA graph")
                self.graph = load_or_fetch_osm_drive_graph(self.fsa_gdf, force_download=force_osm_download)
                self.source = "osm"
                self._prepare_osm_graph()
            except Exception as exc:  # pragma: no cover - depends on live OSM/network state
                if source == "osm":
                    raise RuntimeError(f"Unable to load OSM road graph: {exc}") from exc
                self.graph = self._build_fsa_adjacency_graph()
                self.source = "fsa_adjacency"
        else:
            self.graph = self._build_fsa_adjacency_graph()
            self.source = "fsa_adjacency"

        if self.source == "fsa_adjacency":
            self.fsa_node_ids = list(range(len(self.fsa_gdf)))

        self.routes = self._load_or_compute_fsa_routes()
        self._load_edge_template_cache()
        self.distance_km = self._route_matrix("distance_km")
        self.freeflow_time_h = self._route_matrix("freeflow_time_h")
        self.route_km = self.distance_km

    def route(self, origin_idx: int, dest_idx: int) -> RouteResult:
        return self.routes[int(origin_idx)][int(dest_idx)]

    def travel_time_h(self, origin_idx: int, dest_idx: int, depart_hour_abs: float, *, route: RouteResult | None = None) -> float:
        route = route or self.route(origin_idx, dest_idx)
        return max(0.05, route.freeflow_time_h * self.time_multiplier(depart_hour_abs))

    def time_multiplier(self, hour_abs: float) -> float:
        day = int(hour_abs // 24)
        hour = hour_abs % 24
        if day < 5 and 7 <= hour < 9.5:
            return 1.32
        if day < 5 and 15.5 <= hour < 18.5:
            return 1.38
        if day < 5 and 11.5 <= hour < 13.5:
            return 1.10
        if day >= 5 and 11 <= hour < 18:
            return 1.14
        if 22 <= hour or hour < 5:
            return 0.92
        return 1.0

    def nearest_fsa_idx(self, lat: float, lon: float) -> int:
        d = haversine_km(self.centroid_lat, self.centroid_lon, lat, lon)
        return int(np.nanargmin(d))

    def nearest_graph_nodes(self, lat: Iterable[float], lon: Iterable[float]) -> tuple[np.ndarray, np.ndarray]:
        """Return nearest road-graph node ids and snap distances in metres."""
        lat_arr = np.asarray(list(lat), dtype=float)
        lon_arr = np.asarray(list(lon), dtype=float)
        if len(lat_arr) == 0:
            return np.array([], dtype=int), np.array([], dtype=float)

        if self.source == "osm":
            import osmnx as ox

            node_ids, dist_m = ox.distance.nearest_nodes(self.graph, lon_arr, lat_arr, return_dist=True)
            return np.asarray(node_ids, dtype=int), np.asarray(dist_m, dtype=float)

        distances = haversine_km(lat_arr[:, None], lon_arr[:, None], self.centroid_lat[None, :], self.centroid_lon[None, :])
        nearest_idx = np.nanargmin(distances, axis=1)
        node_ids = np.asarray([self.fsa_node_ids[int(idx)] for idx in nearest_idx], dtype=int)
        return node_ids, distances[np.arange(len(nearest_idx)), nearest_idx] * 1000.0

    def route_fsa_indices(self, route: RouteResult) -> tuple[int, ...]:
        if self.source == "fsa_adjacency":
            return route.path
        mapped: list[int] = []
        for node in route.path:
            fsa_idx = self._osm_node_to_fsa.get(int(node))
            if fsa_idx is not None and (not mapped or mapped[-1] != fsa_idx):
                mapped.append(fsa_idx)
        if not mapped:
            return (route.origin_idx, route.dest_idx)
        return tuple(mapped)

    def route_edge_segments(self, path: Iterable[int]) -> list[RouteEdgeSegment]:
        """Return per-edge distance/time for a routed path."""
        nodes = tuple(int(node) for node in path)
        cached = self._edge_segment_cache.get(nodes)
        if cached is not None:
            return list(cached)

        segments: list[RouteEdgeSegment] = []
        for u, v in zip(nodes, nodes[1:]):
            distance_km = _edge_metric(self.graph, u, v, "distance_km")
            time_h = _edge_metric(self.graph, u, v, "time_h")
            if distance_km <= 0:
                distance_km = float(haversine_km(
                    float(self.graph.nodes[u].get("y", self.centroid_lat[0])),
                    float(self.graph.nodes[u].get("x", self.centroid_lon[0])),
                    float(self.graph.nodes[v].get("y", self.centroid_lat[0])),
                    float(self.graph.nodes[v].get("x", self.centroid_lon[0])),
                ))
            if time_h <= 0:
                time_h = max(distance_km / 38.0, 0.001)
            segments.append(RouteEdgeSegment(u=u, v=v, distance_km=max(distance_km, 0.001), freeflow_time_h=max(time_h, 0.001)))
        self._edge_segment_cache[nodes] = tuple(segments)
        return segments

    def route_edge_template(self, origin_idx: int, dest_idx: int) -> RouteEdgeTemplate:
        """Return cached edge segments and FSA buckets for one FSA OD route."""
        key = (int(origin_idx), int(dest_idx))
        cached = self._edge_template_cache.get(key)
        if cached is not None:
            return cached

        route = self.route(*key)
        segments = tuple(self.route_edge_segments(route.path))
        fsa_indices = tuple(self.edge_fsa_idx(segment.u, segment.v) for segment in segments)
        template = RouteEdgeTemplate(
            segments=segments,
            fsa_indices=fsa_indices,
            distance_km=sum(segment.distance_km for segment in segments),
            freeflow_time_h=sum(segment.freeflow_time_h for segment in segments),
        )
        self._edge_template_cache[key] = template
        self._edge_template_cache_dirty = True
        return template

    def persist_edge_template_cache(self) -> None:
        """Persist lazily expanded OD edge templates for reuse across runs."""
        if not self._edge_template_cache_dirty:
            return
        cache_path = self._edge_template_cache_path()
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": EDGE_TEMPLATE_CACHE_VERSION,
            "source": self.source,
            "fingerprint": self._cache_fingerprint(),
            "templates": self._edge_template_cache,
        }
        _atomic_pickle_dump(payload, cache_path)
        self._edge_template_cache_dirty = False

    def warm_edge_template_cache(self) -> int:
        """Expand all OD route templates once and persist them."""
        before = len(self._edge_template_cache)
        for origin_idx in range(len(self.fsa_gdf)):
            for dest_idx in range(len(self.fsa_gdf)):
                self.route_edge_template(origin_idx, dest_idx)
        self.persist_edge_template_cache()
        return len(self._edge_template_cache) - before

    def edge_fsa_idx(self, u: int, v: int) -> int:
        """Return the FSA index used to spatially bucket an edge traversal."""
        if self.source == "fsa_adjacency":
            return int(u) if 0 <= int(u) < len(self.fsa_gdf) else int(v)
        u_idx = self._osm_node_to_fsa.get(int(u))
        if u_idx is not None:
            return u_idx
        v_idx = self._osm_node_to_fsa.get(int(v))
        if v_idx is not None:
            return v_idx
        return 0

    def summary(self) -> RoadNetworkSummary:
        undirected = self.graph.to_undirected() if self.graph.is_directed() else self.graph
        components = list(nx.connected_components(undirected))
        isolated = sum(1 for _, degree in undirected.degree() if degree == 0)
        unreachable = sum(1 for row in self.routes for route in row if not route.reachable)
        direct = self._direct_distance_matrix()
        mask = direct > 0.05
        circuity = np.divide(self.distance_km[mask], direct[mask], out=np.ones(mask.sum()), where=direct[mask] > 0)
        return RoadNetworkSummary(
            source=self.source,
            node_count=self.graph.number_of_nodes(),
            edge_count=self.graph.number_of_edges(),
            component_count=len(components),
            isolated_nodes=isolated,
            unreachable_od_pairs=unreachable,
            median_snap_distance_m=float(np.median(self.snap_distance_m)),
            p95_snap_distance_m=float(np.quantile(self.snap_distance_m, 0.95)),
            median_circuity=float(np.median(circuity)) if len(circuity) else 1.0,
            p90_circuity=float(np.quantile(circuity, 0.90)) if len(circuity) else 1.0,
        )

    def _prepare_osm_graph(self) -> None:
        import osmnx as ox

        xs = self.centroid_lon
        ys = self.centroid_lat
        node_ids, dist_m = ox.distance.nearest_nodes(self.graph, xs, ys, return_dist=True)
        self.fsa_node_ids = [int(n) for n in node_ids]
        self.snap_distance_m = np.asarray(dist_m, dtype=float)
        self._osm_node_to_fsa = self._map_osm_nodes_to_fsa()
        for _, _, _, data in self.graph.edges(keys=True, data=True):
            length_km = float(data.get("length", 0.0)) / 1000.0
            travel_time_h = float(data.get("travel_time", 0.0)) / 3600.0
            if travel_time_h <= 0:
                speed = _speed_from_highway(data.get("highway"))
                travel_time_h = length_km / max(speed, 1.0)
            data["distance_km"] = max(length_km, 0.001)
            data["time_h"] = max(travel_time_h, 0.001)

    def _map_osm_nodes_to_fsa(self) -> dict[int, int]:
        nodes = list(self.graph.nodes(data=True))
        if not nodes:
            return {}
        node_ids = np.array([int(node_id) for node_id, _ in nodes], dtype=int)
        node_lat = np.array([float(data.get("y", np.nan)) for _, data in nodes], dtype=float)
        node_lon = np.array([float(data.get("x", np.nan)) for _, data in nodes], dtype=float)
        finite = np.isfinite(node_lat) & np.isfinite(node_lon)
        mapping: dict[int, int] = {}

        valid_nodes = gpd.GeoDataFrame(
            {
                "node_id": node_ids[finite],
                "node_order": np.arange(len(node_ids))[finite],
            },
            geometry=gpd.points_from_xy(node_lon[finite], node_lat[finite]),
            crs="EPSG:4326",
        )
        fsa_polygons = self.fsa_gdf[["geometry"]].copy()
        fsa_polygons["fsa_idx"] = np.arange(len(fsa_polygons), dtype=int)
        joined = gpd.sjoin(
            valid_nodes,
            fsa_polygons[["fsa_idx", "geometry"]],
            predicate="within",
            how="left",
        )
        contained = joined.dropna(subset=["fsa_idx"]).drop_duplicates(subset=["node_id"], keep="first")
        for node_id, fsa_idx in zip(contained["node_id"].astype(int), contained["fsa_idx"].astype(int)):
            mapping[int(node_id)] = int(fsa_idx)

        missing = valid_nodes[~valid_nodes["node_id"].isin(mapping.keys())]
        if not missing.empty:
            fsa_m = self.fsa_gdf.to_crs(epsg=32617)
            centroids = np.column_stack([fsa_m.geometry.centroid.x.to_numpy(), fsa_m.geometry.centroid.y.to_numpy()])
            tree = cKDTree(centroids)
            missing_m = missing.to_crs(epsg=32617)
            points = np.column_stack([missing_m.geometry.x.to_numpy(), missing_m.geometry.y.to_numpy()])
            _, nearest = tree.query(points, k=1)
            for node_id, fsa_idx in zip(missing["node_id"].astype(int), np.asarray(nearest, dtype=int)):
                mapping[int(node_id)] = int(fsa_idx)
        return mapping

    def _build_fsa_adjacency_graph(self) -> nx.Graph:
        gdf_m = self.fsa_gdf.to_crs(epsg=32617)
        centroids = np.column_stack([gdf_m.geometry.centroid.x.to_numpy(), gdf_m.geometry.centroid.y.to_numpy()])
        tree = cKDTree(centroids)
        graph = nx.Graph()
        for idx, row in self.fsa_gdf.iterrows():
            graph.add_node(
                int(idx),
                fsa=row["fsa"],
                zone_type=row["zone_type"],
                y=float(row["centroid_lat"]),
                x=float(row["centroid_lon"]),
            )

        sindex = self.fsa_gdf.sindex
        for idx, geom in enumerate(self.fsa_gdf.geometry):
            for other in sindex.query(geom):
                other = int(other)
                if other <= idx:
                    continue
                if geom.touches(self.fsa_gdf.geometry.iloc[other]) or geom.intersects(self.fsa_gdf.geometry.iloc[other]):
                    self._add_fsa_edge(graph, idx, other)

        # Add k-nearest bridge edges so rural/non-touching polygons do not create
        # artificial unreachable OD pairs in the offline graph.
        for idx in range(len(self.fsa_gdf)):
            _, nearest = tree.query(centroids[idx], k=min(5, len(self.fsa_gdf)))
            for other in np.atleast_1d(nearest):
                other = int(other)
                if other != idx:
                    self._add_fsa_edge(graph, idx, other)

        return graph

    def _add_fsa_edge(self, graph: nx.Graph, a: int, b: int) -> None:
        if graph.has_edge(a, b):
            return
        distance = float(haversine_km(self.centroid_lat[a], self.centroid_lon[a], self.centroid_lat[b], self.centroid_lon[b]))
        distance *= 1.18
        zone_speed = min(
            FALLBACK_SPEED_KPH_BY_ZONE.get(str(self.zone_types[a]), 38.0),
            FALLBACK_SPEED_KPH_BY_ZONE.get(str(self.zone_types[b]), 38.0),
        )
        graph.add_edge(a, b, distance_km=max(distance, 0.05), time_h=max(distance / zone_speed, 0.01))

    def _compute_fsa_routes(self) -> list[list[RouteResult]]:
        if self.source == "osm":
            return self._compute_osm_fsa_routes()
        return self._compute_fallback_fsa_routes()

    def _load_or_compute_fsa_routes(self) -> list[list[RouteResult]]:
        cache_path = OSM_ROUTE_CACHE if self.source == "osm" else FSA_ROUTE_CACHE
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        if cache_path.exists():
            with cache_path.open("rb") as fh:
                cached = pickle.load(fh)
            if isinstance(cached, dict):
                routes = cached.get("routes")
                if cached.get("version") == ROUTE_CACHE_VERSION and cached.get("fingerprint") == self._cache_fingerprint():
                    return routes
                if self._cached_routes_compatible(routes):
                    self._write_route_cache(cache_path, routes)
                    return routes
        routes = self._compute_fsa_routes()
        self._write_route_cache(cache_path, routes)
        return routes

    def _write_route_cache(self, cache_path: Path, routes: list[list[RouteResult]]) -> None:
        _atomic_pickle_dump(
            {"version": ROUTE_CACHE_VERSION, "fingerprint": self._cache_fingerprint(), "routes": routes},
            cache_path,
        )

    def _edge_template_cache_path(self) -> Path:
        return OSM_EDGE_TEMPLATE_CACHE if self.source == "osm" else FSA_EDGE_TEMPLATE_CACHE

    def _load_edge_template_cache(self) -> None:
        cache_path = self._edge_template_cache_path()
        if not cache_path.exists():
            return
        try:
            with cache_path.open("rb") as fh:
                cached = pickle.load(fh)
        except Exception:
            cache_path.unlink(missing_ok=True)
            return
        if not isinstance(cached, dict):
            return
        if cached.get("version") != EDGE_TEMPLATE_CACHE_VERSION:
            return
        if cached.get("source") != self.source:
            return
        if cached.get("fingerprint") != self._cache_fingerprint():
            return
        templates = cached.get("templates")
        if not isinstance(templates, dict):
            return
        self._edge_template_cache = {
            (int(origin), int(dest)): template
            for (origin, dest), template in templates.items()
            if isinstance(template, RouteEdgeTemplate)
        }
        self._edge_template_cache_dirty = False

    def _cache_fingerprint(self) -> dict[str, object]:
        return {
            "source": self.source,
            "fsas": tuple(str(fsa) for fsa in self.fsas),
            "centroid_lat": tuple(round(float(value), 6) for value in self.centroid_lat),
            "centroid_lon": tuple(round(float(value), 6) for value in self.centroid_lon),
            "fsa_node_ids": tuple(int(node) for node in self.fsa_node_ids),
            "node_count": int(self.graph.number_of_nodes()),
            "edge_count": int(self.graph.number_of_edges()),
        }

    def _cached_routes_compatible(self, routes: object) -> bool:
        if not isinstance(routes, list) or len(routes) != len(self.fsa_gdf):
            return False
        for origin_idx, row in enumerate(routes):
            if not isinstance(row, list) or len(row) != len(self.fsa_gdf):
                return False
            for dest_idx, route in enumerate(row):
                if not isinstance(route, RouteResult):
                    return False
                if route.origin_idx != origin_idx or route.dest_idx != dest_idx:
                    return False
                if self.source == "osm" and route.reachable and route.path:
                    if int(route.path[0]) != int(self.fsa_node_ids[origin_idx]):
                        return False
                    if origin_idx != dest_idx and int(route.path[-1]) != int(self.fsa_node_ids[dest_idx]):
                        return False
        return True

    def _compute_fallback_fsa_routes(self) -> list[list[RouteResult]]:
        routes: list[list[RouteResult]] = []
        for origin_idx in range(len(self.fsa_gdf)):
            lengths, paths = nx.single_source_dijkstra(self.graph, origin_idx, weight="time_h")
            row: list[RouteResult] = []
            for dest_idx in range(len(self.fsa_gdf)):
                if origin_idx == dest_idx:
                    distance = 1.8
                    time_h = distance / 25.0
                    path = (origin_idx,)
                    reachable = True
                elif dest_idx in paths:
                    path = tuple(int(n) for n in paths[dest_idx])
                    distance = _path_sum(self.graph, path, "distance_km")
                    time_h = float(lengths[dest_idx])
                    reachable = True
                else:
                    direct = float(haversine_km(self.centroid_lat[origin_idx], self.centroid_lon[origin_idx], self.centroid_lat[dest_idx], self.centroid_lon[dest_idx]))
                    distance = direct * 1.35
                    time_h = distance / 38.0
                    path = (origin_idx, dest_idx)
                    reachable = False
                row.append(RouteResult(origin_idx, dest_idx, distance, time_h, path, reachable))
            routes.append(row)
        return routes

    def _compute_osm_fsa_routes(self) -> list[list[RouteResult]]:
        routes: list[list[RouteResult]] = []
        undirected = self.graph.to_undirected()
        for origin_idx, node_id in enumerate(self.fsa_node_ids):
            lengths, paths = nx.single_source_dijkstra(self.graph, node_id, weight="time_h")
            row: list[RouteResult] = []
            for dest_idx, dest_node in enumerate(self.fsa_node_ids):
                if origin_idx == dest_idx:
                    row.append(RouteResult(origin_idx, dest_idx, 1.2, 1.2 / 25.0, (int(node_id),), True))
                elif dest_node in paths:
                    path_nodes = tuple(int(n) for n in paths[dest_node])
                    row.append(RouteResult(
                        origin_idx=origin_idx,
                        dest_idx=dest_idx,
                        distance_km=_path_sum(self.graph, path_nodes, "distance_km"),
                        freeflow_time_h=float(lengths[dest_node]),
                        path=path_nodes,
                        reachable=True,
                    ))
                else:
                    try:
                        path_nodes = tuple(int(n) for n in nx.shortest_path(undirected, node_id, dest_node, weight="time_h"))
                        row.append(RouteResult(
                            origin_idx=origin_idx,
                            dest_idx=dest_idx,
                            distance_km=_path_sum(undirected, path_nodes, "distance_km"),
                            freeflow_time_h=_path_sum(undirected, path_nodes, "time_h"),
                            path=path_nodes,
                            reachable=True,
                        ))
                    except nx.NetworkXNoPath:
                        direct = float(haversine_km(self.centroid_lat[origin_idx], self.centroid_lon[origin_idx], self.centroid_lat[dest_idx], self.centroid_lon[dest_idx]))
                        row.append(RouteResult(origin_idx, dest_idx, direct * 1.35, direct / 38.0, (origin_idx, dest_idx), False))
            routes.append(row)
        return routes

    def _route_matrix(self, attr: str) -> np.ndarray:
        return np.array([[getattr(route, attr) for route in row] for row in self.routes], dtype=float)

    def _direct_distance_matrix(self) -> np.ndarray:
        lat = self.centroid_lat
        lon = self.centroid_lon
        return haversine_km(lat[:, None], lon[:, None], lat[None, :], lon[None, :])


def _path_sum(graph: nx.Graph, path: Iterable[int], attr: str) -> float:
    total = 0.0
    nodes = list(path)
    for a, b in zip(nodes, nodes[1:]):
        total += _edge_metric(graph, a, b, attr)
    return total


def _edge_metric(graph: nx.Graph, a: int, b: int, attr: str) -> float:
    data = graph.get_edge_data(a, b)
    if data is None and graph.is_directed():
        data = graph.get_edge_data(b, a)
    if data is None:
        return 0.0
    if isinstance(data, dict) and data and all(isinstance(value, dict) for value in data.values()):
        return float(min(edge_data.get(attr, 0.0) for edge_data in data.values()))
    return float(data.get(attr, 0.0))


def _speed_from_highway(highway: object) -> float:
    if isinstance(highway, list):
        values = highway
    else:
        values = [highway]
    speeds = [OSM_SPEED_KPH_BY_HIGHWAY.get(str(value), 40.0) for value in values]
    return max(speeds) if speeds else 40.0
