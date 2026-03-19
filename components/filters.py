"""
Sidebar filters component — vessel type, size, flag, stay duration, port type.

All filters apply to the in-memory DataFrame instantly (no API calls).
"""

from __future__ import annotations

import streamlit as st
import pandas as pd


# Default vessel categories for the multi-select
DEFAULT_CATEGORIES = ["Container", "Tanker", "Bulk Carrier", "General Cargo", "Passenger", "Other"]


def render_filters(events_df: pd.DataFrame) -> dict:
    """
    Render sidebar filter controls.

    Returns a dict of filter settings that can be applied to the events DataFrame.
    """
    if events_df is None or events_df.empty:
        return {}

    st.sidebar.markdown("---")
    st.sidebar.header("🔧 Filters")

    filters = {}

    # Vessel type
    if "vessel_category" in events_df:
        available_types = sorted(events_df["vessel_category"].dropna().unique().tolist())
    elif "vessel_type" in events_df:
        available_types = sorted(events_df["vessel_type"].dropna().unique().tolist())
    else:
        available_types = []

    if available_types:
        filters["vessel_types"] = st.sidebar.multiselect(
            "Vessel type",
            available_types,
            default=available_types,
            help="Filter by vessel category",
        )

    # Vessel size (GT range)
    if "tonnage_gt" in events_df:
        valid_gt = events_df["tonnage_gt"].dropna()
        if not valid_gt.empty:
            min_gt = int(valid_gt.min())
            max_gt = int(valid_gt.max())
            if min_gt < max_gt:
                filters["gt_range"] = st.sidebar.slider(
                    "Gross tonnage (GT)",
                    min_value=min_gt,
                    max_value=max_gt,
                    value=(min_gt, max_gt),
                    step=1000,
                    help="Filter by vessel size",
                )

    # Flag state
    if "vessel_flag" in events_df:
        available_flags = sorted(events_df["vessel_flag"].dropna().unique().tolist())
        if len(available_flags) > 1:
            all_flags = st.sidebar.checkbox("All flags", value=True, key="all_flags_cb")
            if not all_flags:
                filters["flags"] = st.sidebar.multiselect(
                    "Flag state",
                    available_flags,
                    default=available_flags[:10],
                )
            else:
                filters["flags"] = available_flags

    # Stay duration
    if "duration_hours" in events_df:
        valid_dur = events_df["duration_hours"].dropna()
        if not valid_dur.empty:
            max_dur = min(int(valid_dur.quantile(0.99)), 720)
            filters["min_stay_h"] = st.sidebar.slider(
                "Min stay (hours)",
                min_value=0,
                max_value=max_dur,
                value=0,
                help="Minimum port stay duration",
            )
            filters["max_stay_h"] = st.sidebar.slider(
                "Max stay (hours)",
                min_value=0,
                max_value=max_dur,
                value=max_dur,
                help="Maximum port stay duration",
            )

    # Port type
    if "at_dock" in events_df or "matched_is_dock" in events_df:
        filters["port_type"] = st.sidebar.radio(
            "Port type",
            ["All", "Dock", "Anchorage"],
            horizontal=True,
        )

    return filters


def apply_filters(events_df: pd.DataFrame, filters: dict) -> pd.DataFrame:
    """Apply sidebar filters to the events DataFrame."""
    if not filters or events_df.empty:
        return events_df

    df = events_df.copy()

    # Vessel type filter
    if "vessel_types" in filters:
        type_col = "vessel_category" if "vessel_category" in df else "vessel_type"
        if type_col in df:
            df = df[df[type_col].isin(filters["vessel_types"])]

    # GT range filter
    if "gt_range" in filters and "tonnage_gt" in df:
        low, high = filters["gt_range"]
        mask = df["tonnage_gt"].isna() | (
            (df["tonnage_gt"] >= low) & (df["tonnage_gt"] <= high)
        )
        df = df[mask]

    # Flag filter
    if "flags" in filters and "vessel_flag" in df:
        df = df[df["vessel_flag"].isin(filters["flags"])]

    # Stay duration filter
    if "min_stay_h" in filters and "duration_hours" in df:
        df = df[df["duration_hours"].isna() | (df["duration_hours"] >= filters["min_stay_h"])]
    if "max_stay_h" in filters and "duration_hours" in df:
        df = df[df["duration_hours"].isna() | (df["duration_hours"] <= filters["max_stay_h"])]

    # Port type filter
    if "port_type" in filters and filters["port_type"] != "All":
        dock_col = "matched_is_dock" if "matched_is_dock" in df else "at_dock"
        if dock_col in df:
            if filters["port_type"] == "Dock":
                df = df[df[dock_col] == True]
            elif filters["port_type"] == "Anchorage":
                df = df[df[dock_col] == False]

    return df
