import os
import sys

import geopandas as gpd
import pandas as pd
from shapely.geometry import Point, Polygon

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))


def _toy_fsa_gdf():
    return gpd.GeoDataFrame(
        {
            "fsa": ["A1A", "B2B"],
            "zone_type": ["residential", "retail_hub"],
            "population_2021": [1000, 500],
            "centroid_lat": [43.0, 43.1],
            "centroid_lon": [-79.0, -79.1],
        },
        geometry=[
            Polygon([(-79.05, 42.95), (-78.95, 42.95), (-78.95, 43.05), (-79.05, 43.05)]),
            Polygon([(-79.15, 43.05), (-79.05, 43.05), (-79.05, 43.15), (-79.15, 43.15)]),
        ],
        crs="EPSG:4326",
    )


def test_proxy_activity_catalog_has_all_activity_fsa_weights():
    from activity_poi_catalog import ACTIVITY_TYPES, build_proxy_activity_fsa_attractions, load_or_fetch_activity_pois

    gdf = _toy_fsa_gdf()
    catalog = load_or_fetch_activity_pois(gdf, source="none")

    assert catalog.source == "zone_proxy"
    assert catalog.pois.empty
    assert set(catalog.fsa_attractions["activity_type"]) == set(ACTIVITY_TYPES)
    assert len(catalog.fsa_attractions) == len(ACTIVITY_TYPES) * len(gdf)
    assert (catalog.fsa_attractions["attraction_weight"] > 0).all()

    direct = build_proxy_activity_fsa_attractions(gdf)
    retail = direct[direct["activity_type"] == "retail"].sort_values("fsa_idx")
    assert retail.iloc[1]["attraction_weight"] > retail.iloc[0]["attraction_weight"]


def test_activity_poi_cache_requires_complete_coverage_metadata(tmp_path, monkeypatch):
    import activity_poi_catalog as catalog

    monkeypatch.setattr(catalog, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(catalog, "ACTIVITY_POIS_CSV", tmp_path / "activity_pois.csv")
    monkeypatch.setattr(catalog, "ACTIVITY_FSA_ATTRACTIONS_CSV", tmp_path / "activity_fsa_attractions.csv")
    monkeypatch.setattr(catalog, "ACTIVITY_NODE_ATTRACTIONS_CSV", tmp_path / "activity_node_attractions.csv")
    monkeypatch.setattr(catalog, "ACTIVITY_POI_METADATA_JSON", tmp_path / "activity_poi_metadata.json")

    gdf = _toy_fsa_gdf()
    pois = pd.DataFrame(columns=catalog.POI_COLUMNS)
    fsa_attractions = catalog.build_proxy_activity_fsa_attractions(gdf)
    node_attractions = pd.DataFrame(columns=catalog.NODE_ATTRACTION_COLUMNS)

    pois.to_csv(catalog.ACTIVITY_POIS_CSV, index=False)
    fsa_attractions.to_csv(catalog.ACTIVITY_FSA_ATTRACTIONS_CSV, index=False)
    node_attractions.to_csv(catalog.ACTIVITY_NODE_ATTRACTIONS_CSV, index=False)
    assert not catalog._cache_complete(len(gdf))
    assert catalog.load_or_fetch_activity_pois(gdf, source="auto").source == "zone_proxy"

    catalog._write_cache(
        pois,
        fsa_attractions,
        node_attractions,
        metadata={"source": "osm", "fsa_count": len(gdf), "fetch_fsa_count": 1, "limit_fsas": 1, "complete": False},
    )
    assert not catalog._cache_complete(len(gdf))
    try:
        catalog.load_or_fetch_activity_pois(gdf, source="cache")
    except FileNotFoundError:
        pass
    else:
        raise AssertionError("partial activity POI cache should not load as complete")

    catalog._write_cache(
        pois,
        fsa_attractions,
        node_attractions,
        metadata={"source": "osm", "fsa_count": len(gdf), "fetch_fsa_count": len(gdf), "limit_fsas": None, "complete": True},
    )
    assert catalog._cache_complete(len(gdf))
    assert catalog.load_or_fetch_activity_pois(gdf, source="cache").source == "cache"


def test_activity_poi_chunk_status_filters_by_graph_fingerprint(tmp_path, monkeypatch):
    import activity_poi_catalog as catalog

    monkeypatch.setattr(catalog, "ACTIVITY_POI_CHUNK_DIR", tmp_path)
    tmp_path.mkdir(parents=True, exist_ok=True)

    row = {column: "" for column in catalog.POI_COLUMNS}
    row.update({
        "poi_id": "OSM_1",
        "source": "osm",
        "activity_type": "retail",
        "lat": 43.0,
        "lon": -79.0,
        "weight": 1.0,
        "fsa": "A1A",
        "fsa_idx": 0,
        "zone_type": "retail_hub",
        "dedupe_key": "retail:test:43.0:-79.0",
        "graph_fingerprint": "graph_a",
        "mapping_version": catalog.ACTIVITY_POI_MAPPING_VERSION,
    })
    pd.DataFrame([row], columns=catalog.POI_COLUMNS).to_csv(tmp_path / "activity_pois_000_001.csv", index=False)
    pd.DataFrame(columns=catalog.POI_COLUMNS).to_csv(tmp_path / "activity_pois_001_002.csv", index=False)

    matching = catalog.activity_poi_chunk_status(2, graph_fingerprint="graph_a")
    assert matching["covered_fsa_count"] == 2
    assert matching["complete"] is True

    mismatched = catalog.activity_poi_chunk_status(2, graph_fingerprint="graph_b")
    assert mismatched["covered_fsa_count"] == 1
    assert mismatched["complete"] is False
    assert mismatched["missing_fsa_ranges"] == [[0, 1]]


def test_osm_feature_normalization_maps_categories_to_activity_types():
    from activity_poi_catalog import normalize_osm_features

    gdf = _toy_fsa_gdf()
    features = gpd.GeoDataFrame(
        {
            "osmid": [1, 2, 3, 4],
            "name": ["Office", "Noodles", "Night Place", "Clinic"],
            "office": ["company", None, None, None],
            "amenity": [None, "restaurant", "bar", "clinic"],
            "shop": [None, None, None, None],
            "leisure": [None, None, None, None],
            "tourism": [None, None, None, None],
            "building": [None, None, None, None],
            "landuse": [None, None, None, None],
            "public_transport": [None, None, None, None],
            "railway": [None, None, None, None],
            "aeroway": [None, None, None, None],
        },
        geometry=[Point(-79.0, 43.0), Point(-79.1, 43.1), Point(-79.1, 43.1), Point(-79.0, 43.0)],
        crs="EPSG:4326",
    )

    pois = normalize_osm_features(gdf, features)

    assert {"work", "restaurant", "bar_nightlife", "errand"}.issubset(set(pois["activity_type"]))
    assert {"poi_id", "fsa", "fsa_idx", "weight", "raw_tags_json"}.issubset(pois.columns)
    assert pd.to_numeric(pois["weight"], errors="coerce").gt(0).all()
