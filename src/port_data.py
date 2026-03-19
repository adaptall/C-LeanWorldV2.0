"""
Port & anchorage reference data loader (V2 — enhanced).

Reads the GFW named-anchorages CSV (166k S2 cells) and the EEZ→country
mapping JSON. Provides:
  1. Raw cell-level DataFrame
  2. Port groups (label-level aggregation)
  3. Sublabel groups (sub-location-level aggregation)
  4. Country filtering
  5. Event → port matching by name, anchorage ID, or coordinates
  6. EEZ country mapping loader
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from .utils import haversine_km

# ---------------------------------------------------------------------------
# Default paths
# ---------------------------------------------------------------------------
_BASE_DIR = Path(__file__).resolve().parent.parent / "Base Data"
_DEFAULT_CSV = _BASE_DIR / "named_anchorages_v2_pipe_v3_202601.csv"
_DEFAULT_EEZ_JSON = _BASE_DIR / "eez_country_mapping.json"


# ---------------------------------------------------------------------------
# 1. Load raw cells
# ---------------------------------------------------------------------------

def load_raw_cells(csv_path: Optional[str | Path] = None) -> pd.DataFrame:
    """Load the full cell-level CSV into a DataFrame with proper types."""
    path = Path(csv_path) if csv_path else _DEFAULT_CSV
    if not path.exists():
        raise FileNotFoundError(f"Anchorage CSV not found at {path}")

    df = pd.read_csv(
        path,
        dtype={
            "s2id": str,
            "label": str,
            "sublabel": str,
            "label_source": str,
            "iso3": str,
            "dock": str,
        },
    )

    df["lat"] = pd.to_numeric(df["lat"], errors="coerce")
    df["lon"] = pd.to_numeric(df["lon"], errors="coerce")
    df["distance_from_shore_m"] = pd.to_numeric(df["distance_from_shore_m"], errors="coerce")
    df["drift_radius"] = pd.to_numeric(df["drift_radius"], errors="coerce")
    df["is_dock"] = df["dock"].str.lower() == "true"
    df["label"] = df["label"].fillna("UNKNOWN")
    df["sublabel"] = df["sublabel"].fillna(df["label"])
    df["iso3"] = df["iso3"].fillna("")

    return df


# ---------------------------------------------------------------------------
# 2. Group by sublabel (sub-location level)
# ---------------------------------------------------------------------------

def build_sublabel_groups(raw: pd.DataFrame) -> pd.DataFrame:
    """Aggregate S2 cells to the sublabel (sub-location) level."""
    grouped = (
        raw.groupby(["label", "sublabel"], as_index=False)
        .agg(
            iso3=("iso3", "first"),
            label_source=("label_source", "first"),
            centroid_lat=("lat", "mean"),
            centroid_lon=("lon", "mean"),
            cell_count=("s2id", "count"),
            has_dock=("is_dock", "any"),
            has_anchorage=("is_dock", lambda s: (~s).any()),
            mean_distance_from_shore_m=("distance_from_shore_m", "mean"),
            mean_drift_radius=("drift_radius", "mean"),
            min_lat=("lat", "min"),
            max_lat=("lat", "max"),
            min_lon=("lon", "min"),
            max_lon=("lon", "max"),
        )
    )
    return grouped


# ---------------------------------------------------------------------------
# 3. Group by label (port level)
# ---------------------------------------------------------------------------

def build_port_groups(raw: pd.DataFrame) -> pd.DataFrame:
    """Aggregate S2 cells to the port (label) level."""
    grouped = (
        raw.groupby("label", as_index=False)
        .agg(
            iso3=("iso3", "first"),
            centroid_lat=("lat", "mean"),
            centroid_lon=("lon", "mean"),
            cell_count=("s2id", "count"),
            sublabel_count=("sublabel", "nunique"),
            has_dock=("is_dock", "any"),
            has_anchorage=("is_dock", lambda s: (~s).any()),
            mean_distance_from_shore_m=("distance_from_shore_m", "mean"),
            min_lat=("lat", "min"),
            max_lat=("lat", "max"),
            min_lon=("lon", "min"),
            max_lon=("lon", "max"),
        )
    )
    return grouped


# ---------------------------------------------------------------------------
# 4. Filter helpers
# ---------------------------------------------------------------------------

def get_cells_for_port(raw: pd.DataFrame, label: str) -> pd.DataFrame:
    return raw[raw["label"] == label].copy()


def get_cells_for_sublabel(raw: pd.DataFrame, label: str, sublabel: str) -> pd.DataFrame:
    return raw[(raw["label"] == label) & (raw["sublabel"] == sublabel)].copy()


def filter_by_country(port_groups: pd.DataFrame, iso3: str) -> pd.DataFrame:
    return port_groups[port_groups["iso3"] == iso3.upper()].copy()


def search_ports(port_groups: pd.DataFrame, query: str, limit: int = 20) -> pd.DataFrame:
    mask = port_groups["label"].str.contains(query.upper(), case=False, na=False)
    return port_groups[mask].head(limit)


# ---------------------------------------------------------------------------
# 5. EEZ country mapping
# ---------------------------------------------------------------------------

def load_eez_mapping(json_path: Optional[str | Path] = None) -> dict:
    """
    Load the ISO3 → MRGID(s) EEZ mapping.

    Returns dict: {iso3: {"name": str, "mrgids": [int, ...]}}
    """
    path = Path(json_path) if json_path else _DEFAULT_EEZ_JSON
    if not path.exists():
        raise FileNotFoundError(f"EEZ mapping not found at {path}")
    with open(path, "r") as f:
        return json.load(f)


def get_country_list(eez_mapping: dict) -> list[tuple[str, str]]:
    """Return sorted list of (display_name, iso3) tuples for the dropdown."""
    items = [(info["name"], iso3) for iso3, info in eez_mapping.items()]
    items.sort(key=lambda x: x[0])
    return items


# ---------------------------------------------------------------------------
# 6. Event → port matching
# ---------------------------------------------------------------------------

def match_events_to_ports(
    events_df: pd.DataFrame,
    raw_cells: pd.DataFrame,
    port_groups: pd.DataFrame,
) -> pd.DataFrame:
    """
    Enrich events with anchorage CSV data by matching port names.

    Matching strategy (in priority order):
    1. event.port_id (e.g. "pan-balboa") → extract port name → CSV "label"
    2. event.port_name → anchorage CSV "label" (case-insensitive)
    3. event.anchorage_id → raw S2 cell s2id → get label from CSV
    4. event (lat, lon) → nearest anchorage CSV port centroid within 10 km

    Adds columns: matched_label, matched_sublabel, matched_is_dock, matched_iso3
    """
    df = events_df.copy()
    # Initialize match columns if not present; preserve existing matches
    for col in ("matched_label", "matched_sublabel", "matched_is_dock", "matched_iso3"):
        if col not in df.columns:
            df[col] = None

    # Build lookup: uppercase port name → port group row
    port_lookup = {}
    for _, row in port_groups.iterrows():
        port_lookup[row["label"].upper()] = row

    # Build s2id → label lookup from raw cells for anchorage_id matching
    s2id_lookup = {}
    if "s2id" in raw_cells.columns:
        for _, row in raw_cells[["s2id", "label", "iso3"]].drop_duplicates("s2id").iterrows():
            s2id_lookup[row["s2id"]] = row["label"]

    def _try_match(port_name_val, port_id_val, anch_id_val):
        """Try multiple match strategies, return matched port_group row or None."""
        # Strategy 1: port_id → extract the port name part (e.g. "pan-balboa" → "BALBOA")
        if port_id_val and isinstance(port_id_val, str) and "-" in port_id_val:
            # port_id format is "iso3-portname" e.g. "pan-balboa", "esp-vigo"
            extracted = port_id_val.split("-", 1)[1].upper().strip().replace("-", " ")
            if extracted in port_lookup:
                return port_lookup[extracted]

        # Strategy 2: direct port_name match
        if port_name_val and isinstance(port_name_val, str):
            pn_upper = port_name_val.upper().strip()
            if pn_upper in port_lookup:
                return port_lookup[pn_upper]

        # Strategy 3: anchorage_id → s2id lookup in raw cells
        if anch_id_val and isinstance(anch_id_val, str):
            label = s2id_lookup.get(anch_id_val)
            if label and label.upper() in port_lookup:
                return port_lookup[label.upper()]

        return None

    # Apply matching strategies (skip already-matched rows)
    for idx, row in df.iterrows():
        if pd.notna(row.get("matched_label")):
            continue
        pr = _try_match(row.get("port_name"), row.get("port_id"), row.get("anchorage_id"))
        if pr is not None:
            df.at[idx, "matched_label"] = pr["label"]
            df.at[idx, "matched_is_dock"] = pr.get("has_dock", None)
            df.at[idx, "matched_iso3"] = pr.get("iso3", None)

    # Fallback: nearest port by coordinates (for still-unmatched events)
    unmatched = df[df["matched_label"].isna()]
    if not unmatched.empty and not port_groups.empty:
        pg_lats = port_groups["centroid_lat"].values
        pg_lons = port_groups["centroid_lon"].values
        pg_labels = port_groups["label"].values

        for idx, row in unmatched.iterrows():
            ev_lat = row.get("lat")
            ev_lon = row.get("lon")
            if ev_lat is None or ev_lon is None or np.isnan(ev_lat) or np.isnan(ev_lon):
                continue

            # Vectorised distance computation
            dists = np.array([
                haversine_km(ev_lat, ev_lon, plat, plon)
                for plat, plon in zip(pg_lats, pg_lons)
            ])
            min_idx = np.argmin(dists)
            if dists[min_idx] < 10.0:  # 10 km threshold
                pr = port_groups.iloc[min_idx]
                df.at[idx, "matched_label"] = pr["label"]
                df.at[idx, "matched_is_dock"] = pr.get("has_dock", None)
                df.at[idx, "matched_iso3"] = pr.get("iso3", None)

    return df


# ---------------------------------------------------------------------------
# 7. Build port summary from events
# ---------------------------------------------------------------------------

def build_port_visit_summary(events_df: pd.DataFrame) -> pd.DataFrame:
    """
    Aggregate events by matched port label to produce a summary per port.

    Returns DataFrame with columns:
        label, visit_count, unique_vessels, median_duration_h,
        mean_duration_h, vessel_types, centroid_lat, centroid_lon
    """
    df = events_df.dropna(subset=["matched_label"]).copy()
    if df.empty:
        return pd.DataFrame()

    summary = (
        df.groupby("matched_label")
        .agg(
            visit_count=("event_id", "count"),
            unique_vessels=("vessel_id", "nunique"),
            median_duration_h=("duration_hours", "median"),
            mean_duration_h=("duration_hours", "mean"),
            centroid_lat=("lat", "mean"),
            centroid_lon=("lon", "mean"),
        )
        .reset_index()
        .rename(columns={"matched_label": "label"})
        .sort_values("visit_count", ascending=False)
    )

    # Add vessel type diversity
    type_counts = (
        df.groupby("matched_label")["vessel_type"]
        .apply(lambda x: x.nunique())
        .reset_index(name="vessel_type_count")
        .rename(columns={"matched_label": "label"})
    )
    summary = summary.merge(type_counts, on="label", how="left")

    return summary


# ---------------------------------------------------------------------------
# 8. Bounding box helpers (for Copernicus, etc.)
# ---------------------------------------------------------------------------

def port_bounding_box(raw: pd.DataFrame, label: str, pad_deg: float = 0.05) -> dict:
    """Return a GeoJSON Polygon bbox for a port's cells."""
    cells = get_cells_for_port(raw, label)
    if cells.empty:
        raise ValueError(f"No cells found for label '{label}'")

    min_lat = float(cells["lat"].min() - pad_deg)
    max_lat = float(cells["lat"].max() + pad_deg)
    min_lon = float(cells["lon"].min() - pad_deg)
    max_lon = float(cells["lon"].max() + pad_deg)

    return {
        "type": "Polygon",
        "coordinates": [[
            [min_lon, min_lat],
            [max_lon, min_lat],
            [max_lon, max_lat],
            [min_lon, max_lat],
            [min_lon, min_lat],
        ]],
    }


def port_bbox_coords(raw: pd.DataFrame, label: str, pad_deg: float = 0.05) -> dict:
    """Return flat dict with min/max lat/lon for Copernicus subset."""
    cells = get_cells_for_port(raw, label)
    if cells.empty:
        raise ValueError(f"No cells found for label '{label}'")

    return {
        "minimum_latitude": float(cells["lat"].min() - pad_deg),
        "maximum_latitude": float(cells["lat"].max() + pad_deg),
        "minimum_longitude": float(cells["lon"].min() - pad_deg),
        "maximum_longitude": float(cells["lon"].max() + pad_deg),
    }
