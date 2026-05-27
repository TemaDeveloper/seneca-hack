"""
Activity point-of-interest catalog for intraday route planning.

The route planner samples destinations at FSA scale for performance, but it
needs a concrete attraction layer so retail, restaurants, nightlife, errands,
schools, work clusters, and leisure are not all hidden behind one broad zone
class. This module fetches/caches OSM POIs when explicitly requested and keeps
an offline FSA-level proxy for tests and deterministic development runs.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Callable, Iterable

import geopandas as gpd
import numpy as np
import pandas as pd
import requests
from shapely import from_wkt
from shapely.geometry import Point

from road_network import RoadNetwork


DATA_DIR = Path(__file__).resolve().parent / "data"
CACHE_DIR = DATA_DIR / "cache"
ACTIVITY_POI_CHUNK_DIR = CACHE_DIR / "activity_poi_chunks"
ACTIVITY_OSM_PBF = CACHE_DIR / "ontario-latest.osm.pbf"
ACTIVITY_OSM_PBF_URL = "https://download.geofabrik.de/north-america/canada/ontario-latest.osm.pbf"
ACTIVITY_POIS_CSV = CACHE_DIR / "activity_pois.csv"
ACTIVITY_FSA_ATTRACTIONS_CSV = CACHE_DIR / "activity_fsa_attractions.csv"
ACTIVITY_NODE_ATTRACTIONS_CSV = CACHE_DIR / "activity_node_attractions.csv"
ACTIVITY_POI_METADATA_JSON = CACHE_DIR / "activity_poi_metadata.json"
ACTIVITY_POI_CACHE_SCHEMA_VERSION = 1
ACTIVITY_POI_MAPPING_VERSION = 1
ACTIVITY_POI_CHUNK_RE = re.compile(r"activity_pois_(\d{3})_(\d{3})\.csv$")

ACTIVITY_TYPES = [
    "work",
    "school",
    "retail",
    "restaurant",
    "bar_nightlife",
    "leisure",
    "errand",
    "transit_hub",
    "other",
]

POI_COLUMNS = [
    "poi_id",
    "source",
    "source_id",
    "source_layer",
    "name",
    "raw_tags_json",
    "activity_type",
    "activity_subtype",
    "lat",
    "lon",
    "geometry_wkt",
    "area_m2",
    "weight",
    "capacity_proxy",
    "confidence",
    "fsa",
    "fsa_idx",
    "zone_type",
    "road_node_id",
    "road_snap_distance_m",
    "snap_status",
    "dedupe_key",
    "source_fingerprint",
    "graph_fingerprint",
    "mapping_version",
]

FSA_ATTRACTION_COLUMNS = [
    "activity_type",
    "fsa",
    "fsa_idx",
    "zone_type",
    "poi_count",
    "weighted_poi_count",
    "population_weight",
    "employment_weight",
    "traffic_weight",
    "attraction_weight",
    "source_mix_json",
    "confidence",
]

NODE_ATTRACTION_COLUMNS = [
    "activity_type",
    "fsa_idx",
    "road_node_id",
    "lat",
    "lon",
    "weighted_poi_count",
    "representative_poi_id",
    "snap_distance_m",
]

OSM_TAGS: dict[str, bool | list[str]] = {
    "office": True,
    "building": ["office", "commercial", "industrial", "retail"],
    "landuse": ["commercial", "industrial", "retail", "recreation_ground"],
    "amenity": [
        "school",
        "kindergarten",
        "college",
        "university",
        "marketplace",
        "restaurant",
        "cafe",
        "fast_food",
        "food_court",
        "bar",
        "pub",
        "nightclub",
        "cinema",
        "theatre",
        "library",
        "community_centre",
        "pharmacy",
        "doctors",
        "clinic",
        "hospital",
        "dentist",
        "bank",
        "post_office",
        "townhall",
        "courthouse",
        "bus_station",
    ],
    "shop": True,
    "leisure": True,
    "tourism": ["attraction", "museum", "gallery"],
    "public_transport": ["station"],
    "railway": ["station"],
    "aeroway": ["aerodrome"],
}

ZONE_PROXY_ATTRACTION = {
    "work": {"residential": 0.20, "leisure": 0.35, "office_park": 4.50, "retail_hub": 1.60, "transit_hub": 1.20},
    "school": {"residential": 1.20, "leisure": 0.75, "office_park": 2.00, "retail_hub": 0.35, "transit_hub": 0.25},
    "retail": {"residential": 0.55, "leisure": 0.55, "office_park": 0.85, "retail_hub": 4.50, "transit_hub": 1.10},
    "restaurant": {"residential": 0.45, "leisure": 1.10, "office_park": 1.00, "retail_hub": 3.80, "transit_hub": 1.20},
    "bar_nightlife": {"residential": 0.10, "leisure": 2.40, "office_park": 0.35, "retail_hub": 2.40, "transit_hub": 1.10},
    "leisure": {"residential": 0.40, "leisure": 4.20, "office_park": 0.25, "retail_hub": 1.30, "transit_hub": 0.50},
    "errand": {"residential": 1.30, "leisure": 0.70, "office_park": 1.00, "retail_hub": 2.20, "transit_hub": 0.70},
    "transit_hub": {"residential": 0.08, "leisure": 0.12, "office_park": 0.25, "retail_hub": 0.45, "transit_hub": 18.00},
    "other": {"residential": 1.00, "leisure": 1.00, "office_park": 1.00, "retail_hub": 1.00, "transit_hub": 1.00},
}

ACTIVITY_BASE_WEIGHT = {
    "work": 3.0,
    "school": 2.5,
    "retail": 1.0,
    "restaurant": 0.9,
    "bar_nightlife": 0.8,
    "leisure": 1.0,
    "errand": 0.8,
    "transit_hub": 2.0,
    "other": 0.4,
}


@dataclass(frozen=True)
class ActivityPOICatalog:
    fsa_gdf: gpd.GeoDataFrame
    pois: pd.DataFrame
    fsa_attractions: pd.DataFrame
    node_attractions: pd.DataFrame
    source: str

    def attraction_vector(self, activity_type: str) -> np.ndarray:
        activity_type = _canonical_activity(activity_type)
        rows = self.fsa_attractions[self.fsa_attractions["activity_type"] == activity_type]
        values = np.ones(len(self.fsa_gdf), dtype=float)
        if rows.empty:
            return values
        idx = rows["fsa_idx"].astype(int).to_numpy()
        weights = pd.to_numeric(rows["attraction_weight"], errors="coerce").fillna(0.0).to_numpy(dtype=float)
        valid = (idx >= 0) & (idx < len(values))
        values[idx[valid]] = np.maximum(weights[valid], 0.001)
        return values

    def summary(self) -> dict[str, object]:
        return {
            "source": self.source,
            "poi_count": int(len(self.pois)),
            "activity_counts": self.pois["activity_type"].value_counts().to_dict() if not self.pois.empty else {},
            "fsa_attraction_rows": int(len(self.fsa_attractions)),
            "node_attraction_rows": int(len(self.node_attractions)),
        }


def load_or_fetch_activity_pois(
    fsa_gdf: gpd.GeoDataFrame,
    *,
    road_network: RoadNetwork | None = None,
    source: str = "auto",
    force_download: bool = False,
    chunk_size: int = 10,
    limit_fsas: int | None = None,
    start_fsa: int | None = None,
    stop_fsa: int | None = None,
    pbf_path: Path | str | None = None,
    request_timeout: int = 300,
    overpass_url: str | None = None,
    continue_on_error: bool = False,
    progress: Callable[[str], None] | None = None,
) -> ActivityPOICatalog:
    """
    Load cached activity POIs or fetch OSM POIs when explicitly requested.

    `source="auto"` uses cache when available and otherwise returns the
    deterministic FSA proxy. It does not hit Overpass or download PBFs
    unexpectedly. `source="osm"` is the explicit Overpass path, and
    `source="pbf"` parses a local OpenStreetMap extract.
    """
    source = str(source)
    if source not in {"auto", "cache", "osm", "pbf", "none"}:
        raise ValueError("activity POI source must be one of auto/cache/osm/pbf/none")

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    expected_fsa_count = int(len(fsa_gdf))
    if source in {"auto", "cache"} and _cache_complete(expected_fsa_count) and not force_download:
        return ActivityPOICatalog(
            fsa_gdf=fsa_gdf.reset_index(drop=True).copy(),
            pois=pd.read_csv(ACTIVITY_POIS_CSV),
            fsa_attractions=pd.read_csv(ACTIVITY_FSA_ATTRACTIONS_CSV),
            node_attractions=pd.read_csv(ACTIVITY_NODE_ATTRACTIONS_CSV),
            source="cache",
        )
    if source == "cache":
        raise FileNotFoundError("Activity POI cache is incomplete. Run data_preparation/fetch_activity_pois.py.")
    if source == "osm":
        pois = fetch_osm_activity_pois_chunked(
            fsa_gdf,
            road_network=road_network,
            force_download=force_download,
            chunk_size=chunk_size,
            limit_fsas=limit_fsas,
            start_fsa=start_fsa,
            stop_fsa=stop_fsa,
            request_timeout=request_timeout,
            overpass_url=overpass_url,
            continue_on_error=continue_on_error,
            progress=progress,
        )
        return rebuild_activity_poi_cache_from_chunks(
            fsa_gdf,
            road_network=road_network,
            source="osm",
            chunk_size=chunk_size,
            limit_fsas=limit_fsas,
            start_fsa=start_fsa,
            stop_fsa=stop_fsa,
            fetched_pois=pois,
        )
    if source == "pbf":
        path = Path(pbf_path) if pbf_path is not None else ACTIVITY_OSM_PBF
        pois = fetch_pbf_activity_pois(
            fsa_gdf,
            path,
            road_network=road_network,
            progress=progress,
        )
        fsa_attractions = aggregate_activity_fsa_attractions(fsa_gdf, pois)
        node_attractions = aggregate_activity_node_attractions(pois)
        cache_metadata = {
            "source": "pbf",
            "pbf_path": str(path),
            "pbf_fingerprint": _path_fingerprint(path),
            "fsa_count": expected_fsa_count,
            "fetch_fsa_count": expected_fsa_count,
            "missing_fsa_ranges": [],
            "limit_fsas": None,
            "start_fsa": None,
            "stop_fsa": None,
            "chunk_size": None,
            "complete": True,
            "graph_fingerprint": _graph_fingerprint(road_network),
            "road_graph_source": road_network.summary().source if road_network is not None else None,
            "compatible_chunk_count": None,
            "chunk_count": None,
        }
        _write_cache(pois, fsa_attractions, node_attractions, metadata=cache_metadata)
        return ActivityPOICatalog(fsa_gdf=fsa_gdf.reset_index(drop=True).copy(), pois=pois, fsa_attractions=fsa_attractions, node_attractions=node_attractions, source="pbf")

    fsa_attractions = build_proxy_activity_fsa_attractions(fsa_gdf)
    return ActivityPOICatalog(
        fsa_gdf=fsa_gdf.reset_index(drop=True).copy(),
        pois=pd.DataFrame(columns=POI_COLUMNS),
        fsa_attractions=fsa_attractions,
        node_attractions=pd.DataFrame(columns=NODE_ATTRACTION_COLUMNS),
        source="zone_proxy",
    )


def fetch_osm_activity_pois(fsa_gdf: gpd.GeoDataFrame, *, road_network: RoadNetwork | None = None) -> pd.DataFrame:
    import osmnx as ox

    ox.settings.use_cache = True
    if hasattr(ox.settings, "requests_timeout"):
        ox.settings.requests_timeout = 300

    fsa_gdf = fsa_gdf.to_crs(epsg=4326).reset_index(drop=True).copy()
    polygon = fsa_gdf.geometry.union_all()
    features = ox.features_from_polygon(polygon, OSM_TAGS)
    if features.empty:
        return pd.DataFrame(columns=POI_COLUMNS)
    return normalize_osm_features(fsa_gdf, features, road_network=road_network)


def fetch_osm_activity_pois_chunked(
    fsa_gdf: gpd.GeoDataFrame,
    *,
    road_network: RoadNetwork | None = None,
    force_download: bool = False,
    chunk_size: int = 10,
    limit_fsas: int | None = None,
    start_fsa: int | None = None,
    stop_fsa: int | None = None,
    request_timeout: int = 300,
    overpass_url: str | None = None,
    continue_on_error: bool = False,
    progress: Callable[[str], None] | None = None,
) -> pd.DataFrame:
    """
    Fetch OSM POIs in FSA chunks and persist each chunk.

    The all-GTA polygon is large enough that Overpass can run for many minutes
    and lose all progress if interrupted. Chunk files make the fetch resumable.
    """
    import osmnx as ox

    ox.settings.use_cache = True
    if hasattr(ox.settings, "requests_timeout"):
        ox.settings.requests_timeout = int(max(request_timeout, 1))
    if overpass_url:
        ox.settings.overpass_url = str(overpass_url).rstrip("/")

    full_fsa_gdf = fsa_gdf.to_crs(epsg=4326).reset_index(drop=True).copy()
    full_fsa_gdf["fsa_idx"] = np.arange(len(full_fsa_gdf), dtype=int)
    start = 0 if start_fsa is None else max(int(start_fsa), 0)
    stop = len(full_fsa_gdf) if stop_fsa is None else min(max(int(stop_fsa), 0), len(full_fsa_gdf))
    if limit_fsas is not None:
        stop = min(stop, start + max(int(limit_fsas), 0))
    if start >= stop:
        return pd.DataFrame(columns=POI_COLUMNS)

    chunk_size = max(int(chunk_size), 1)
    graph_fingerprint = _graph_fingerprint(road_network)
    ACTIVITY_POI_CHUNK_DIR.mkdir(parents=True, exist_ok=True)
    chunks: list[pd.DataFrame] = []
    for chunk_start in range(start, stop, chunk_size):
        chunk_stop = min(chunk_start + chunk_size, stop)
        chunk = full_fsa_gdf.iloc[chunk_start:chunk_stop].reset_index(drop=True)
        chunk_path = _activity_poi_chunk_path(chunk_start, chunk_stop)
        cached = _read_compatible_chunk(chunk_path, graph_fingerprint)
        if cached is not None and not force_download:
            if progress is not None:
                progress(f"cached FSA chunk {chunk_start}:{chunk_stop} rows={len(cached)}")
            chunks.append(cached)
            continue

        if progress is not None:
            progress(f"fetching FSA chunk {chunk_start}:{chunk_stop}")
        polygon = chunk.geometry.union_all()
        try:
            features = ox.features_from_polygon(polygon, OSM_TAGS)
        except Exception as exc:
            if progress is not None:
                progress(f"failed FSA chunk {chunk_start}:{chunk_stop}: {type(exc).__name__}: {exc}")
            if continue_on_error:
                continue
            raise
        pois = normalize_osm_features(chunk, features, road_network=road_network)
        pois["source_layer"] = pois["source_layer"].astype(str) + f":fsa_chunk_{chunk_start:03d}_{chunk_stop:03d}"
        pois.to_csv(chunk_path, index=False)
        if progress is not None:
            progress(f"wrote FSA chunk {chunk_start}:{chunk_stop} rows={len(pois)}")
        chunks.append(pois)

    if not chunks:
        return pd.DataFrame(columns=POI_COLUMNS)
    combined = pd.concat(chunks, ignore_index=True)
    if combined.empty:
        return pd.DataFrame(columns=POI_COLUMNS)
    combined = _dedupe_pois(combined)
    return combined[POI_COLUMNS].reset_index(drop=True)


def rebuild_activity_poi_cache_from_chunks(
    fsa_gdf: gpd.GeoDataFrame,
    *,
    road_network: RoadNetwork | None = None,
    source: str = "osm",
    chunk_size: int | None = None,
    limit_fsas: int | None = None,
    start_fsa: int | None = None,
    stop_fsa: int | None = None,
    fetched_pois: pd.DataFrame | None = None,
) -> ActivityPOICatalog:
    graph_fingerprint = _graph_fingerprint(road_network)
    pois = load_cached_activity_poi_chunks(len(fsa_gdf), graph_fingerprint=graph_fingerprint)
    if pois.empty and fetched_pois is not None:
        pois = fetched_pois.copy()
    fsa_attractions = aggregate_activity_fsa_attractions(fsa_gdf, pois)
    node_attractions = aggregate_activity_node_attractions(pois)
    status = activity_poi_chunk_status(len(fsa_gdf), graph_fingerprint=graph_fingerprint)
    cache_metadata = {
        "source": source,
        "fsa_count": int(len(fsa_gdf)),
        "fetch_fsa_count": int(status["covered_fsa_count"]),
        "missing_fsa_ranges": status["missing_fsa_ranges"],
        "limit_fsas": None if limit_fsas is None else int(limit_fsas),
        "start_fsa": None if start_fsa is None else int(start_fsa),
        "stop_fsa": None if stop_fsa is None else int(stop_fsa),
        "chunk_size": None if chunk_size is None else int(max(chunk_size, 1)),
        "complete": bool(status["complete"]),
        "graph_fingerprint": graph_fingerprint,
        "road_graph_source": road_network.summary().source if road_network is not None else None,
        "compatible_chunk_count": int(status["compatible_chunk_count"]),
        "chunk_count": int(status["chunk_count"]),
    }
    _write_cache(pois, fsa_attractions, node_attractions, metadata=cache_metadata)
    catalog_source = "cache" if status["complete"] else "osm_partial"
    return ActivityPOICatalog(
        fsa_gdf=fsa_gdf.reset_index(drop=True).copy(),
        pois=pois,
        fsa_attractions=fsa_attractions,
        node_attractions=node_attractions,
        source=catalog_source,
    )


def load_cached_activity_poi_chunks(fsa_count: int, *, graph_fingerprint: str | None = None) -> pd.DataFrame:
    if not ACTIVITY_POI_CHUNK_DIR.exists():
        return pd.DataFrame(columns=POI_COLUMNS)
    frames = []
    for path in sorted(ACTIVITY_POI_CHUNK_DIR.glob("activity_pois_*.csv")):
        parsed = _parse_activity_poi_chunk_path(path)
        if parsed is None:
            continue
        start, stop = parsed
        if start < 0 or stop > int(fsa_count) or start >= stop:
            continue
        frame = _read_compatible_chunk(path, graph_fingerprint)
        if frame is not None:
            frames.append(frame)
    if not frames:
        return pd.DataFrame(columns=POI_COLUMNS)
    combined = pd.concat(frames, ignore_index=True)
    if combined.empty:
        return pd.DataFrame(columns=POI_COLUMNS)
    return _dedupe_pois(combined)[POI_COLUMNS].reset_index(drop=True)


def activity_poi_chunk_status(fsa_count: int, *, graph_fingerprint: str | None = None) -> dict[str, object]:
    covered = np.zeros(int(fsa_count), dtype=bool)
    chunk_count = 0
    compatible_count = 0
    rows = []
    if ACTIVITY_POI_CHUNK_DIR.exists():
        for path in sorted(ACTIVITY_POI_CHUNK_DIR.glob("activity_pois_*.csv")):
            parsed = _parse_activity_poi_chunk_path(path)
            if parsed is None:
                continue
            start, stop = parsed
            chunk_count += 1
            frame = _read_compatible_chunk(path, graph_fingerprint)
            compatible = frame is not None and 0 <= start < stop <= int(fsa_count)
            if compatible:
                compatible_count += 1
                covered[start:stop] = True
            rows.append({
                "path": str(path),
                "start_fsa": int(start),
                "stop_fsa": int(stop),
                "compatible": bool(compatible),
                "rows": 0 if frame is None else int(len(frame)),
            })
    missing = _missing_ranges(covered)
    return {
        "fsa_count": int(fsa_count),
        "covered_fsa_count": int(covered.sum()),
        "missing_fsa_count": int((~covered).sum()),
        "complete": bool(covered.all()) if len(covered) else False,
        "missing_fsa_ranges": missing,
        "chunk_count": int(chunk_count),
        "compatible_chunk_count": int(compatible_count),
        "chunks": rows,
    }


def download_activity_osm_pbf(
    *,
    url: str = ACTIVITY_OSM_PBF_URL,
    path: Path | str = ACTIVITY_OSM_PBF,
    progress: Callable[[str], None] | None = None,
) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    headers = {}
    mode = "wb"
    existing = path.stat().st_size if path.exists() else 0
    if existing > 0:
        headers["Range"] = f"bytes={existing}-"
        mode = "ab"
    with requests.get(url, stream=True, timeout=60, headers=headers) as response:
        if response.status_code == 416 and existing > 0:
            if progress is not None:
                progress(f"pbf ready {path} size={existing / (1024 ** 2):.1f} MiB")
            return path
        if response.status_code == 200 and existing and "Range" in headers:
            mode = "wb"
            existing = 0
        response.raise_for_status()
        total_raw = response.headers.get("content-length")
        total = (int(total_raw) + existing) if total_raw and str(total_raw).isdigit() else None
        downloaded = existing
        next_report = downloaded
        with path.open(mode) as handle:
            for chunk in response.iter_content(chunk_size=8 * 1024 * 1024):
                if not chunk:
                    continue
                handle.write(chunk)
                downloaded += len(chunk)
                if progress is not None and downloaded - next_report >= 64 * 1024 * 1024:
                    if total:
                        progress(f"downloaded {downloaded / (1024 ** 2):.1f}/{total / (1024 ** 2):.1f} MiB")
                    else:
                        progress(f"downloaded {downloaded / (1024 ** 2):.1f} MiB")
                    next_report = downloaded
    if progress is not None:
        progress(f"pbf ready {path} size={path.stat().st_size / (1024 ** 2):.1f} MiB")
    return path


def fetch_pbf_activity_pois(
    fsa_gdf: gpd.GeoDataFrame,
    pbf_path: Path | str,
    *,
    road_network: RoadNetwork | None = None,
    progress: Callable[[str], None] | None = None,
) -> pd.DataFrame:
    try:
        import osmium
        import osmium.geom
    except ImportError as exc:
        raise ImportError("Install osmium to parse local OSM PBF activity POIs.") from exc

    pbf_path = Path(pbf_path)
    if not pbf_path.exists():
        raise FileNotFoundError(f"OSM PBF not found: {pbf_path}")

    fsa = fsa_gdf.to_crs(epsg=4326).reset_index(drop=True).copy()
    fsa["fsa_idx"] = np.arange(len(fsa), dtype=int)
    minx, miny, maxx, maxy = fsa.total_bounds
    bbox = (float(minx), float(miny), float(maxx), float(maxy))
    source_fingerprint = _path_fingerprint(pbf_path)
    graph_fingerprint = _graph_fingerprint(road_network)

    class Handler(osmium.SimpleHandler):
        def __init__(self) -> None:
            super().__init__()
            self.rows: list[dict[str, object]] = []
            self.wkt_factory = osmium.geom.WKTFactory()

        def node(self, obj) -> None:
            tags = _tags_to_dict(obj.tags)
            activity, subtype = _activity_from_osm_row(tags)
            if activity is None or not obj.location.valid():
                return
            lon = float(obj.location.lon)
            lat = float(obj.location.lat)
            if not _point_in_bbox(lon, lat, bbox):
                return
            self.rows.append(_pbf_candidate_row(
                source_id=str(obj.id),
                source_layer="osm_pbf_node",
                tags=tags,
                activity=activity,
                subtype=subtype,
                point=Point(lon, lat),
                geometry_wkt=f"POINT ({lon} {lat})",
                area_m2=0.0,
                source_fingerprint=source_fingerprint,
                graph_fingerprint=graph_fingerprint,
            ))

        def way(self, obj) -> None:
            if obj.is_closed():
                return
            tags = _tags_to_dict(obj.tags)
            activity, subtype = _activity_from_osm_row(tags)
            if activity is None:
                return
            try:
                geometry = from_wkt(self.wkt_factory.create_linestring(obj))
            except Exception:
                return
            point = geometry.representative_point()
            if not _point_in_bbox(float(point.x), float(point.y), bbox):
                return
            self.rows.append(_pbf_candidate_row(
                source_id=str(obj.id),
                source_layer="osm_pbf_way",
                tags=tags,
                activity=activity,
                subtype=subtype,
                point=point,
                geometry_wkt=geometry.wkt,
                area_m2=0.0,
                source_fingerprint=source_fingerprint,
                graph_fingerprint=graph_fingerprint,
            ))

        def area(self, obj) -> None:
            tags = _tags_to_dict(obj.tags)
            activity, subtype = _activity_from_osm_row(tags)
            if activity is None:
                return
            try:
                geometry = from_wkt(self.wkt_factory.create_multipolygon(obj))
            except Exception:
                return
            point = geometry.representative_point()
            if not _point_in_bbox(float(point.x), float(point.y), bbox):
                return
            self.rows.append(_pbf_candidate_row(
                source_id=str(obj.id),
                source_layer="osm_pbf_area",
                tags=tags,
                activity=activity,
                subtype=subtype,
                point=point,
                geometry_wkt=geometry.wkt,
                area_m2=_geometry_area_m2(geometry),
                source_fingerprint=source_fingerprint,
                graph_fingerprint=graph_fingerprint,
            ))

    if progress is not None:
        progress(f"parsing OSM PBF {pbf_path}")
    handler = Handler()
    handler.apply_file(str(pbf_path), locations=True, idx="flex_mem")
    if progress is not None:
        progress(f"parsed {len(handler.rows)} candidate OSM features inside GTA bbox")
    if not handler.rows:
        return pd.DataFrame(columns=POI_COLUMNS)

    candidates = gpd.GeoDataFrame(handler.rows, geometry="point", crs="EPSG:4326")
    fsa_lookup = fsa[["fsa", "zone_type", "fsa_idx", "geometry"]].copy()
    joined = gpd.sjoin(candidates, fsa_lookup, predicate="within", how="inner").reset_index(drop=True)
    rows = []
    for output_idx, row in joined.iterrows():
        point = row["point"]
        area_m2 = float(row["area_m2"])
        weight = _poi_weight(str(row["activity_type"]), str(row["activity_subtype"]), area_m2)
        rows.append({
            "poi_id": f"PBF_{output_idx:08d}",
            "source": "osm_pbf",
            "source_id": str(row["source_id"]),
            "source_layer": str(row["source_layer"]),
            "name": str(row.get("name", "") or ""),
            "raw_tags_json": str(row["raw_tags_json"]),
            "activity_type": str(row["activity_type"]),
            "activity_subtype": str(row["activity_subtype"]),
            "lat": float(point.y),
            "lon": float(point.x),
            "geometry_wkt": str(row["geometry_wkt"]),
            "area_m2": area_m2,
            "weight": weight,
            "capacity_proxy": max(area_m2, weight),
            "confidence": float(row["confidence"]),
            "fsa": str(row["fsa"]),
            "fsa_idx": int(row["fsa_idx"]),
            "zone_type": str(row["zone_type"]),
            "road_node_id": pd.NA,
            "road_snap_distance_m": np.nan,
            "snap_status": "unsnapped",
            "dedupe_key": _dedupe_key(str(row["activity_type"]), str(row.get("name", "") or ""), float(point.y), float(point.x)),
            "source_fingerprint": source_fingerprint,
            "graph_fingerprint": graph_fingerprint,
            "mapping_version": ACTIVITY_POI_MAPPING_VERSION,
        })
    pois = pd.DataFrame(rows, columns=POI_COLUMNS)
    pois = _dedupe_pois(pois)
    if road_network is not None:
        pois = snap_pois_to_road(pois, road_network)
    if progress is not None:
        progress(f"mapped {len(pois)} activity POIs to GTA FSAs")
    return pois[POI_COLUMNS].reset_index(drop=True)


def normalize_osm_features(
    fsa_gdf: gpd.GeoDataFrame,
    features: gpd.GeoDataFrame,
    *,
    road_network: RoadNetwork | None = None,
) -> pd.DataFrame:
    fsa_gdf = fsa_gdf.to_crs(epsg=4326).reset_index(drop=True).copy()
    rows = []
    source_fingerprint = _source_fingerprint(features)
    graph_fingerprint = _graph_fingerprint(road_network)

    features = features.copy()
    if features.crs is None:
        features = features.set_crs(epsg=4326)
    else:
        features = features.to_crs(epsg=4326)
    features = features.reset_index(drop=False)
    features["_source_row"] = np.arange(len(features), dtype=int)
    feature_points = features.copy()
    feature_points["geometry"] = feature_points.geometry.representative_point()
    fsa_lookup = fsa_gdf[["fsa", "zone_type", "geometry"]].copy()
    if "fsa_idx" in fsa_gdf.columns:
        fsa_lookup["fsa_idx"] = pd.to_numeric(fsa_gdf["fsa_idx"], errors="coerce").fillna(-1).astype(int)
    else:
        fsa_lookup["fsa_idx"] = np.arange(len(fsa_gdf), dtype=int)
    joined = gpd.sjoin(
        feature_points,
        fsa_lookup,
        predicate="within",
        how="inner",
    ).reset_index()

    for output_idx, row in joined.iterrows():
        activity, subtype = _activity_from_osm_row(row)
        if activity is None:
            continue
        point = row.geometry
        source_row = int(row.get("_source_row", output_idx))
        original_geom = features.iloc[source_row].geometry if 0 <= source_row < len(features) else row.geometry
        area_m2 = _geometry_area_m2(original_geom)
        tags = _raw_tags(row)
        name = str(row.get("name", "") or "")
        source_id = str(row.get("osmid", row.get("index", output_idx)))
        weight = _poi_weight(activity, subtype, area_m2)
        rows.append({
            "poi_id": f"OSM_{output_idx:07d}",
            "source": "osm",
            "source_id": source_id,
            "source_layer": "osm_features",
            "name": name,
            "raw_tags_json": json.dumps(tags, sort_keys=True),
            "activity_type": activity,
            "activity_subtype": subtype,
            "lat": float(point.y),
            "lon": float(point.x),
            "geometry_wkt": original_geom.wkt if original_geom is not None else "",
            "area_m2": area_m2,
            "weight": weight,
            "capacity_proxy": max(area_m2, weight),
            "confidence": 0.75 if area_m2 > 0 else 0.60,
            "fsa": str(row["fsa"]),
            "fsa_idx": int(row["fsa_idx"]),
            "zone_type": str(row["zone_type"]),
            "road_node_id": pd.NA,
            "road_snap_distance_m": np.nan,
            "snap_status": "unsnapped",
            "dedupe_key": _dedupe_key(activity, name, float(point.y), float(point.x)),
            "source_fingerprint": source_fingerprint,
            "graph_fingerprint": graph_fingerprint,
            "mapping_version": ACTIVITY_POI_MAPPING_VERSION,
        })

    pois = pd.DataFrame(rows, columns=POI_COLUMNS)
    if pois.empty:
        return pois
    pois = _dedupe_pois(pois)
    if road_network is not None:
        pois = snap_pois_to_road(pois, road_network)
    return pois[POI_COLUMNS].reset_index(drop=True)


def snap_pois_to_road(pois: pd.DataFrame, road_network: RoadNetwork) -> pd.DataFrame:
    if pois.empty:
        return pois.assign(road_node_id=pd.Series(dtype="Int64"), road_snap_distance_m=pd.Series(dtype=float), snap_status=pd.Series(dtype=object))
    snapped = pois.copy()
    nodes, snap_m = road_network.nearest_graph_nodes(snapped["lat"], snapped["lon"])
    snapped["road_node_id"] = pd.Series(nodes, index=snapped.index, dtype="Int64")
    snapped["road_snap_distance_m"] = snap_m
    snapped["snap_status"] = np.where(snapped["road_snap_distance_m"].astype(float) <= 2_000.0, "snapped", "far_snap")
    snapped["graph_fingerprint"] = _graph_fingerprint(road_network)
    return snapped


def aggregate_activity_fsa_attractions(fsa_gdf: gpd.GeoDataFrame, pois: pd.DataFrame) -> pd.DataFrame:
    if pois.empty:
        return build_proxy_activity_fsa_attractions(fsa_gdf)

    fsa_gdf = fsa_gdf.reset_index(drop=True).copy()
    population = pd.to_numeric(fsa_gdf.get("population_2021"), errors="coerce").fillna(0.0).to_numpy(dtype=float)
    pop_scale = population / population[population > 0].mean() if np.any(population > 0) else np.ones(len(fsa_gdf), dtype=float)
    pop_scale = np.clip(np.nan_to_num(pop_scale, nan=1.0), 0.15, 6.0)

    grouped = (
        pois.groupby(["activity_type", "fsa", "fsa_idx", "zone_type"], as_index=False)
        .agg(poi_count=("poi_id", "count"), weighted_poi_count=("weight", "sum"))
    )
    rows = []
    for activity in ACTIVITY_TYPES:
        activity_rows = grouped[grouped["activity_type"] == activity].set_index("fsa_idx")
        for fsa_idx, fsa_row in fsa_gdf.iterrows():
            fsa_idx = int(fsa_idx)
            row = activity_rows.loc[fsa_idx] if fsa_idx in activity_rows.index else None
            poi_count = int(row["poi_count"]) if row is not None else 0
            weighted_poi = float(row["weighted_poi_count"]) if row is not None else 0.0
            zone_proxy = ZONE_PROXY_ATTRACTION[activity].get(str(fsa_row["zone_type"]), 1.0)
            population_weight = float(pop_scale[fsa_idx])
            employment_weight = zone_proxy if activity == "work" else 1.0
            traffic_weight = 1.0
            attraction = (weighted_poi + 0.15 * zone_proxy) * max(population_weight if activity in {"school", "errand", "other"} else 1.0, 0.2)
            if activity == "work":
                attraction *= employment_weight
            rows.append({
                "activity_type": activity,
                "fsa": str(fsa_row["fsa"]),
                "fsa_idx": fsa_idx,
                "zone_type": str(fsa_row["zone_type"]),
                "poi_count": poi_count,
                "weighted_poi_count": round(weighted_poi, 6),
                "population_weight": round(population_weight, 6),
                "employment_weight": round(float(employment_weight), 6),
                "traffic_weight": traffic_weight,
                "attraction_weight": round(max(float(attraction), 0.001), 6),
                "source_mix_json": json.dumps({"osm_poi": poi_count, "zone_proxy": zone_proxy}, sort_keys=True),
                "confidence": 0.70 if poi_count > 0 else 0.35,
            })
    return pd.DataFrame(rows, columns=FSA_ATTRACTION_COLUMNS)


def aggregate_activity_node_attractions(pois: pd.DataFrame) -> pd.DataFrame:
    if pois.empty or "road_node_id" not in pois.columns:
        return pd.DataFrame(columns=NODE_ATTRACTION_COLUMNS)
    snapped = pois[pois["road_node_id"].notna()].copy()
    if snapped.empty:
        return pd.DataFrame(columns=NODE_ATTRACTION_COLUMNS)
    grouped = (
        snapped.groupby(["activity_type", "fsa_idx", "road_node_id"], as_index=False)
        .agg(
            lat=("lat", "mean"),
            lon=("lon", "mean"),
            weighted_poi_count=("weight", "sum"),
            representative_poi_id=("poi_id", "first"),
            snap_distance_m=("road_snap_distance_m", "median"),
        )
    )
    return grouped[NODE_ATTRACTION_COLUMNS].reset_index(drop=True)


def build_proxy_activity_fsa_attractions(fsa_gdf: gpd.GeoDataFrame) -> pd.DataFrame:
    fsa_gdf = fsa_gdf.reset_index(drop=True).copy()
    population = pd.to_numeric(fsa_gdf.get("population_2021"), errors="coerce").fillna(0.0).to_numpy(dtype=float)
    if np.any(population > 0):
        population_weight = np.clip(population / float(population[population > 0].mean()), 0.15, 6.0)
    else:
        population_weight = np.ones(len(fsa_gdf), dtype=float)

    rows = []
    for activity in ACTIVITY_TYPES:
        zone_weights = ZONE_PROXY_ATTRACTION[activity]
        for fsa_idx, row in fsa_gdf.iterrows():
            zone_weight = float(zone_weights.get(str(row["zone_type"]), 1.0))
            pop_weight = float(population_weight[int(fsa_idx)])
            attraction = zone_weight
            if activity in {"school", "errand", "other"}:
                attraction *= max(pop_weight, 0.2)
            rows.append({
                "activity_type": activity,
                "fsa": str(row["fsa"]),
                "fsa_idx": int(fsa_idx),
                "zone_type": str(row["zone_type"]),
                "poi_count": 0,
                "weighted_poi_count": 0.0,
                "population_weight": round(pop_weight, 6),
                "employment_weight": round(zone_weight if activity == "work" else 1.0, 6),
                "traffic_weight": 1.0,
                "attraction_weight": round(max(attraction, 0.001), 6),
                "source_mix_json": json.dumps({"zone_proxy": zone_weight}, sort_keys=True),
                "confidence": 0.25,
            })
    return pd.DataFrame(rows, columns=FSA_ATTRACTION_COLUMNS)


def read_activity_poi_cache_metadata() -> dict[str, object]:
    if not ACTIVITY_POI_METADATA_JSON.exists():
        return {}
    try:
        payload = json.loads(ACTIVITY_POI_METADATA_JSON.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _cache_complete(expected_fsa_count: int | None = None) -> bool:
    if not (ACTIVITY_POIS_CSV.exists() and ACTIVITY_FSA_ATTRACTIONS_CSV.exists() and ACTIVITY_NODE_ATTRACTIONS_CSV.exists()):
        return False
    metadata = read_activity_poi_cache_metadata()
    if not metadata:
        return False
    try:
        schema_version = int(metadata.get("cache_schema_version", -1))
        mapping_version = int(metadata.get("mapping_version", -1))
        fsa_count = int(metadata.get("fsa_count", -1))
        fetch_fsa_count = int(metadata.get("fetch_fsa_count", -1))
    except (TypeError, ValueError):
        return False
    if schema_version != ACTIVITY_POI_CACHE_SCHEMA_VERSION:
        return False
    if mapping_version != ACTIVITY_POI_MAPPING_VERSION:
        return False
    if not bool(metadata.get("complete")):
        return False
    if metadata.get("limit_fsas") is not None:
        return False
    if metadata.get("missing_fsa_ranges") not in (None, []):
        return False
    if expected_fsa_count is not None and fsa_count != int(expected_fsa_count):
        return False
    if expected_fsa_count is not None and fetch_fsa_count < int(expected_fsa_count):
        return False
    return True


def _write_cache(
    pois: pd.DataFrame,
    fsa_attractions: pd.DataFrame,
    node_attractions: pd.DataFrame,
    *,
    metadata: dict[str, object] | None = None,
) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    pois.to_csv(ACTIVITY_POIS_CSV, index=False)
    fsa_attractions.to_csv(ACTIVITY_FSA_ATTRACTIONS_CSV, index=False)
    node_attractions.to_csv(ACTIVITY_NODE_ATTRACTIONS_CSV, index=False)
    if "activity_type" in fsa_attractions:
        activity_values = fsa_attractions["activity_type"]
    elif "activity_type" in pois:
        activity_values = pois["activity_type"]
    else:
        activity_values = pd.Series(dtype=object)
    payload: dict[str, object] = {
        "cache_schema_version": ACTIVITY_POI_CACHE_SCHEMA_VERSION,
        "mapping_version": ACTIVITY_POI_MAPPING_VERSION,
        "source": "unknown",
        "complete": False,
        "fsa_count": None,
        "fetch_fsa_count": None,
        "limit_fsas": None,
        "chunk_size": None,
        "poi_count": int(len(pois)),
        "fsa_attraction_rows": int(len(fsa_attractions)),
        "node_attraction_rows": int(len(node_attractions)),
        "activity_types": sorted(str(value) for value in activity_values.dropna().unique()),
        "written_utc": datetime.now(timezone.utc).isoformat(),
    }
    if metadata:
        payload.update(metadata)
    ACTIVITY_POI_METADATA_JSON.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def activity_poi_cache_status(fsa_gdf: gpd.GeoDataFrame, road_network: RoadNetwork | None = None) -> dict[str, object]:
    return activity_poi_chunk_status(len(fsa_gdf), graph_fingerprint=_graph_fingerprint(road_network))


def _activity_poi_chunk_path(start: int, stop: int) -> Path:
    return ACTIVITY_POI_CHUNK_DIR / f"activity_pois_{int(start):03d}_{int(stop):03d}.csv"


def _parse_activity_poi_chunk_path(path: Path) -> tuple[int, int] | None:
    match = ACTIVITY_POI_CHUNK_RE.match(path.name)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def _read_compatible_chunk(path: Path, graph_fingerprint: str | None) -> pd.DataFrame | None:
    if not path.exists():
        return None
    try:
        frame = pd.read_csv(path)
    except (OSError, pd.errors.EmptyDataError):
        return None
    if not set(POI_COLUMNS).issubset(frame.columns):
        return None
    frame = frame[POI_COLUMNS].copy()
    if not frame.empty:
        mapping_versions = pd.to_numeric(frame["mapping_version"], errors="coerce").dropna().astype(int).unique()
        if len(mapping_versions) and set(mapping_versions) != {ACTIVITY_POI_MAPPING_VERSION}:
            return None
        if graph_fingerprint is not None:
            fingerprints = set(str(value) for value in frame["graph_fingerprint"].dropna().unique())
            if fingerprints and fingerprints != {str(graph_fingerprint)}:
                return None
    return frame


def _missing_ranges(covered: np.ndarray) -> list[list[int]]:
    ranges: list[list[int]] = []
    start: int | None = None
    for idx, is_covered in enumerate(covered.tolist()):
        if not is_covered and start is None:
            start = idx
        elif is_covered and start is not None:
            ranges.append([start, idx])
            start = None
    if start is not None:
        ranges.append([start, int(len(covered))])
    return ranges


def _canonical_activity(activity_type: str) -> str:
    if activity_type == "bar":
        return "bar_nightlife"
    if activity_type in ACTIVITY_TYPES:
        return activity_type
    return "other"


def _activity_from_osm_row(row: pd.Series) -> tuple[str | None, str]:
    amenity = str(row.get("amenity", "") or "")
    shop = str(row.get("shop", "") or "")
    leisure = str(row.get("leisure", "") or "")
    tourism = str(row.get("tourism", "") or "")
    office = str(row.get("office", "") or "")
    building = str(row.get("building", "") or "")
    landuse = str(row.get("landuse", "") or "")
    public_transport = str(row.get("public_transport", "") or "")
    railway = str(row.get("railway", "") or "")
    aeroway = str(row.get("aeroway", "") or "")

    if amenity in {"school", "kindergarten", "college", "university"}:
        return "school", amenity
    if amenity in {"restaurant", "cafe", "fast_food", "food_court"}:
        return "restaurant", amenity
    if amenity in {"bar", "pub", "nightclub"}:
        return "bar_nightlife", amenity
    if amenity in {"pharmacy", "doctors", "clinic", "hospital", "dentist", "bank", "post_office", "townhall", "courthouse"}:
        return "errand", amenity
    if amenity in {"bus_station"} or public_transport == "station" or railway == "station" or aeroway == "aerodrome":
        return "transit_hub", amenity or public_transport or railway or aeroway
    if amenity in {"cinema", "theatre", "library", "community_centre"}:
        return "leisure", amenity
    if shop or amenity == "marketplace" or landuse == "retail" or building == "retail":
        return "retail", shop or amenity or landuse or building
    if leisure or tourism in {"attraction", "museum", "gallery"} or landuse == "recreation_ground":
        return "leisure", leisure or tourism or landuse
    if office or building in {"office", "commercial", "industrial"} or landuse in {"commercial", "industrial"}:
        return "work", office or building or landuse
    return None, ""


def _tags_to_dict(tags: object) -> dict[str, object]:
    return {str(tag.k): str(tag.v) for tag in tags}


def _point_in_bbox(lon: float, lat: float, bbox: tuple[float, float, float, float]) -> bool:
    minx, miny, maxx, maxy = bbox
    return minx <= lon <= maxx and miny <= lat <= maxy


def _pbf_candidate_row(
    *,
    source_id: str,
    source_layer: str,
    tags: dict[str, object],
    activity: str,
    subtype: str,
    point: Point,
    geometry_wkt: str,
    area_m2: float,
    source_fingerprint: str,
    graph_fingerprint: str,
) -> dict[str, object]:
    return {
        "source_id": source_id,
        "source_layer": source_layer,
        "name": str(tags.get("name", "") or ""),
        "raw_tags_json": json.dumps({key: value for key, value in tags.items() if key in OSM_TAGS or key == "name"}, sort_keys=True),
        "activity_type": activity,
        "activity_subtype": subtype,
        "point": point,
        "geometry_wkt": geometry_wkt,
        "area_m2": float(max(area_m2, 0.0)),
        "confidence": 0.78 if area_m2 > 0 else 0.62,
        "source_fingerprint": source_fingerprint,
        "graph_fingerprint": graph_fingerprint,
    }


def _geometry_area_m2(geometry: object) -> float:
    if geometry is None:
        return 0.0
    try:
        series = gpd.GeoSeries([geometry], crs="EPSG:4326").to_crs(epsg=32617)
        return float(max(series.area.iloc[0], 0.0))
    except Exception:
        return 0.0


def _poi_weight(activity: str, subtype: str, area_m2: float) -> float:
    base = ACTIVITY_BASE_WEIGHT.get(activity, 1.0)
    area_bonus = np.sqrt(max(area_m2, 0.0)) / 120.0 if area_m2 > 0 else 0.0
    subtype_bonus = 1.0
    if subtype in {"university", "college", "hospital", "aerodrome", "station", "bus_station"}:
        subtype_bonus = 2.0
    elif subtype in {"nightclub", "marketplace"}:
        subtype_bonus = 1.5
    return round(float(base * subtype_bonus + area_bonus), 6)


def _raw_tags(row: pd.Series) -> dict[str, object]:
    tags: dict[str, object] = {}
    for key in OSM_TAGS:
        value = row.get(key)
        if pd.notna(value):
            tags[key] = value
    if pd.notna(row.get("name")):
        tags["name"] = row.get("name")
    return tags


def _dedupe_key(activity: str, name: str, lat: float, lon: float) -> str:
    name_key = " ".join(str(name).lower().split())[:80]
    return f"{activity}:{name_key}:{round(lat, 4)}:{round(lon, 4)}"


def _dedupe_pois(pois: pd.DataFrame) -> pd.DataFrame:
    if pois.empty:
        return pois
    ordered = pois.sort_values(["confidence", "weight"], ascending=[False, False])
    return ordered.drop_duplicates(subset=["dedupe_key", "activity_type"], keep="first").sort_values("poi_id").reset_index(drop=True)


def _source_fingerprint(frame: pd.DataFrame) -> str:
    payload = f"{len(frame)}:{','.join(str(column) for column in frame.columns[:25])}"
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


def _graph_fingerprint(road_network: RoadNetwork | None) -> str:
    if road_network is None:
        return ""
    summary = road_network.summary()
    payload = f"{summary.source}:{summary.node_count}:{summary.edge_count}"
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


def _path_fingerprint(path: Path) -> str:
    if not path.exists():
        return ""
    stat = path.stat()
    payload = f"{path.name}:{stat.st_size}:{stat.st_mtime_ns}"
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


def describe_required_external_data() -> pd.DataFrame:
    """Return the public-data sources needed for a fully concrete activity layer."""
    return pd.DataFrame([
        {"data": "OSM POIs", "purpose": "GTA-wide activity attractions", "status": "implemented via explicit source='osm' fetch"},
        {"data": "OSM land-use/building polygons", "purpose": "commercial/industrial/retail/leisure area weights", "status": "implemented via OSM tags"},
        {"data": "municipal schools/parks/facilities", "purpose": "authoritative local POI enrichment", "status": "not yet implemented"},
        {"data": "employment areas/zoning", "purpose": "work anchor weighting", "status": "not yet implemented"},
        {"data": "trip-purpose/time survey", "purpose": "fit transition/time-window curves", "status": "not present in repo"},
        {"data": "observed travel speeds", "purpose": "fit time-of-day speed multipliers", "status": "not present in repo"},
    ])
