import pytest
import geopandas as gpd
import pandas as pd
from shapely.geometry import Polygon

from spatial_assembler import (
    load_enriched_geodataframe,
    CAPACITY_MAP,
    _load_fsa_boundaries,
    _load_zone_classification,
)


def test_capacity_map_has_five_zone_types():
    """CAPACITY_MAP must cover all 5 zone types used in the project."""
    expected = {"residential", "leisure", "office_park", "retail_hub", "transit_hub"}
    assert set(CAPACITY_MAP.keys()) == expected


def test_load_enriched_geodataframe_returns_expected_columns():
    """The enriched GeoDataFrame must have all required columns."""
    gdf = load_enriched_geodataframe()
    required = {"fsa", "geometry", "zone_type", "proxy_capacity_kw", "centroid_lat", "centroid_lon"}
    assert required.issubset(set(gdf.columns))


def test_load_enriched_geodataframe_no_nan_centroids():
    """Centroids must never contain NaN values (index alignment bug)."""
    gdf = load_enriched_geodataframe()
    assert gdf["centroid_lat"].isna().sum() == 0, "centroid_lat has NaN values"
    assert gdf["centroid_lon"].isna().sum() == 0, "centroid_lon has NaN values"


def test_load_enriched_geodataframe_no_duplicate_fsas():
    """Each FSA should appear exactly once in the output."""
    gdf = load_enriched_geodataframe()
    assert gdf["fsa"].is_unique, f"Duplicate FSAs found: {gdf[gdf['fsa'].duplicated()]['fsa'].tolist()}"


def test_load_enriched_geodataframe_crs_is_4326():
    """Output CRS must be EPSG:4326."""
    gdf = load_enriched_geodataframe()
    assert gdf.crs is not None
    assert gdf.crs.to_epsg() == 4326


def test_all_zone_types_have_capacity():
    """Every zone_type in the data must map to a capacity value."""
    gdf = load_enriched_geodataframe()
    assert gdf["proxy_capacity_kw"].isna().sum() == 0
