"""
Analytics V2 — country-level and port-level aggregations, vessel classification, scoring.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Vessel classification
# ---------------------------------------------------------------------------

VESSEL_CATEGORY_MAP = {
    "CONTAINER": "Container",
    "CONTAINER_REEFER": "Container",
    "TANKER": "Tanker",
    "OIL_CHEMICAL_TANKER": "Tanker",
    "LNG_TANKER": "Tanker",
    "LPG_TANKER": "Tanker",
    "BULK_CARRIER": "Bulk Carrier",
    "ORE_CARRIER": "Bulk Carrier",
    "CARGO": "General Cargo",
    "GENERAL_CARGO": "General Cargo",
    "VEHICLE_CARRIER": "General Cargo",
    "PASSENGER": "Passenger",
    "CRUISE": "Passenger",
    "FERRY": "Passenger",
}

SIZE_CLASSES = [
    (0, 5_000, "Small"),
    (5_000, 25_000, "Medium"),
    (25_000, 60_000, "Large"),
    (60_000, float("inf"), "Very Large"),
]


def classify_vessel_type(vessel_type: str) -> str:
    """Map GFW vessel type to business category."""
    if not vessel_type or not isinstance(vessel_type, str):
        return "Other"
    return VESSEL_CATEGORY_MAP.get(vessel_type.upper(), "Other")


def classify_vessel_size(tonnage_gt) -> str:
    """Classify vessel by gross tonnage into size class."""
    if tonnage_gt is None or (isinstance(tonnage_gt, float) and np.isnan(tonnage_gt)):
        return "Unknown"
    try:
        gt = float(tonnage_gt)
    except (ValueError, TypeError):
        return "Unknown"
    for low, high, label in SIZE_CLASSES:
        if low <= gt < high:
            return label
    return "Unknown"


def add_classifications(df: pd.DataFrame) -> pd.DataFrame:
    """Add vessel_category and size_class columns to events DataFrame."""
    result = df.copy()
    result["vessel_category"] = result["vessel_type"].apply(classify_vessel_type)
    if "tonnage_gt" in result.columns:
        result["size_class"] = result["tonnage_gt"].apply(classify_vessel_size)
    return result


# ---------------------------------------------------------------------------
# Country-level KPIs
# ---------------------------------------------------------------------------

def country_summary(events_df: pd.DataFrame) -> dict:
    """High-level KPIs for the entire country."""
    if events_df.empty:
        return {"total_visits": 0}

    # Count ports — prefer matched_label, fall back to port_name
    port_col = "matched_label" if "matched_label" in events_df else "port_name"
    port_series = events_df[port_col].dropna()
    if port_series.empty and port_col == "matched_label" and "port_name" in events_df:
        port_series = events_df["port_name"].dropna()

    result = {
        "total_visits": len(events_df),
        "unique_vessels": events_df["vessel_id"].nunique(),
        "unique_ports": port_series.nunique(),
        "unique_flags": events_df["vessel_flag"].nunique(),
    }

    if "duration_hours" in events_df:
        dur = events_df["duration_hours"].dropna()
        if not dur.empty:
            result["median_duration_h"] = float(dur.median())
            result["mean_duration_h"] = float(dur.mean())
            result["p90_duration_h"] = float(dur.quantile(0.9))

    return result


# ---------------------------------------------------------------------------
# Port-level analytics
# ---------------------------------------------------------------------------

def visit_summary(visits_df: pd.DataFrame) -> dict:
    """KPIs for a selected port or filtered subset."""
    if visits_df.empty:
        return {"total_visits": 0}

    result = {
        "total_visits": len(visits_df),
        "unique_vessels": visits_df["vessel_id"].nunique(),
        "unique_flags": visits_df["vessel_flag"].nunique(),
    }

    if "duration_hours" in visits_df:
        dur = visits_df["duration_hours"].dropna()
        if not dur.empty:
            result["median_duration_h"] = float(dur.median())
            result["mean_duration_h"] = float(dur.mean())
            result["p90_duration_h"] = float(dur.quantile(0.9))

    return result


def visits_by_vessel_type(visits_df: pd.DataFrame) -> pd.DataFrame:
    """Group visits by vessel type/category."""
    col = "vessel_category" if "vessel_category" in visits_df else "vessel_type"
    if visits_df.empty or col not in visits_df:
        return pd.DataFrame(columns=["type", "count"])
    return (
        visits_df.groupby(col, dropna=False)
        .size()
        .reset_index(name="count")
        .rename(columns={col: "type"})
        .sort_values("count", ascending=False)
    )


def visits_by_flag(visits_df: pd.DataFrame) -> pd.DataFrame:
    """Group visits by flag state."""
    if visits_df.empty or "vessel_flag" not in visits_df:
        return pd.DataFrame(columns=["flag", "count"])
    return (
        visits_df.groupby("vessel_flag", dropna=False)
        .size()
        .reset_index(name="count")
        .rename(columns={"vessel_flag": "flag"})
        .sort_values("count", ascending=False)
    )


def monthly_visit_counts(visits_df: pd.DataFrame) -> pd.DataFrame:
    """Time series of visit counts per month."""
    if visits_df.empty or "start" not in visits_df:
        return pd.DataFrame(columns=["month", "count"])
    df = visits_df.copy()
    df["month"] = pd.to_datetime(df["start"]).dt.to_period("M").astype(str)
    return df.groupby("month").size().reset_index(name="count")


def visits_by_size_class(visits_df: pd.DataFrame) -> pd.DataFrame:
    """Group visits by vessel size class."""
    col = "size_class"
    if visits_df.empty or col not in visits_df:
        return pd.DataFrame(columns=["size_class", "count"])
    return (
        visits_df.groupby(col, dropna=False)
        .size()
        .reset_index(name="count")
        .sort_values("count", ascending=False)
    )


def top_ports(events_df: pd.DataFrame, n: int = 15) -> pd.DataFrame:
    """Return top N ports by visit count."""
    col = "matched_label" if "matched_label" in events_df else "port_name"
    if events_df.empty or col not in events_df:
        return pd.DataFrame(columns=["port", "count"])
    return (
        events_df.groupby(col, dropna=False)
        .size()
        .reset_index(name="count")
        .rename(columns={col: "port"})
        .sort_values("count", ascending=False)
        .head(n)
    )


# ---------------------------------------------------------------------------
# Duration distribution
# ---------------------------------------------------------------------------

def duration_histogram_data(visits_df: pd.DataFrame, bins: int = 30) -> dict:
    """Return histogram bin edges and counts for duration_hours."""
    dur = visits_df["duration_hours"].dropna()
    if dur.empty:
        return {"edges": [], "counts": []}
    counts, edges = np.histogram(dur, bins=bins)
    return {"edges": edges.tolist(), "counts": counts.tolist()}


# ---------------------------------------------------------------------------
# Site-suitability scoring
# ---------------------------------------------------------------------------

def deployment_score(
    visit_count: int,
    median_duration_h: float | None,
    large_vessel_pct: float | None,
    container_tanker_pct: float | None,
    weights: dict[str, float] | None = None,
) -> float:
    """
    Compute a 0–100 deployment suitability score for a port.

    Components (each normalised 0-1 before weighting):
      volume    — visit count (log-scaled, 500+ => 1.0)
      dwell     — median duration (12h+ => 1.0)
      size_mix  — % vessels > 25k GT (100% => 1.0)
      type_mix  — % container + tanker (100% => 1.0)
    """
    w = weights or {
        "volume": 0.3,
        "dwell": 0.25,
        "size_mix": 0.25,
        "type_mix": 0.2,
    }

    volume = min(1.0, np.log1p(visit_count) / np.log1p(500))
    dwell = min(1.0, max(0.0, (median_duration_h or 0) / 12.0))
    size_mix = min(1.0, (large_vessel_pct or 0) / 100.0)
    type_mix = min(1.0, (container_tanker_pct or 0) / 100.0)

    raw = (
        w["volume"] * volume
        + w["dwell"] * dwell
        + w["size_mix"] * size_mix
        + w["type_mix"] * type_mix
    )
    return round(raw * 100, 1)


def compute_port_scores(events_df: pd.DataFrame) -> pd.DataFrame:
    """Compute deployment scores for all ports in the events DataFrame."""
    col = "matched_label" if "matched_label" in events_df else "port_name"
    if events_df.empty:
        return pd.DataFrame()

    results = []
    for label, group in events_df.groupby(col, dropna=False):
        vc = len(group)
        med_dur = group["duration_hours"].median() if "duration_hours" in group else None

        large_pct = None
        if "tonnage_gt" in group:
            valid_gt = group["tonnage_gt"].dropna()
            if len(valid_gt) > 0:
                large_pct = float((valid_gt >= 25000).mean() * 100)

        ct_pct = None
        if "vessel_category" in group:
            total = len(group)
            ct = group["vessel_category"].isin(["Container", "Tanker"]).sum()
            ct_pct = float(ct / total * 100) if total > 0 else 0

        score = deployment_score(vc, med_dur, large_pct, ct_pct)
        results.append({
            "port": label,
            "visit_count": vc,
            "unique_vessels": group["vessel_id"].nunique(),
            "median_duration_h": med_dur,
            "large_vessel_pct": large_pct,
            "container_tanker_pct": ct_pct,
            "score": score,
        })

    return pd.DataFrame(results).sort_values("score", ascending=False)
