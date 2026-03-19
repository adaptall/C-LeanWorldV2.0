"""
Vessel table component — filterable, sortable list of all unique vessels.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st


def render_vessel_table(events_df: pd.DataFrame, port_filter: str | None = None):
    """
    Render a filterable table of all unique vessels.

    Parameters
    ----------
    events_df : full events DataFrame (enriched with vessel details)
    port_filter : optional port label to filter to
    """
    st.header("🚢 Vessel Directory")

    if events_df.empty:
        st.warning("No vessel data available.")
        return

    df = events_df.copy()
    col_name = "matched_label" if "matched_label" in df else "port_name"

    if port_filter:
        df = df[df[col_name] == port_filter]

    if df.empty:
        st.warning("No vessels to show for this selection.")
        return

    # Aggregate per vessel
    agg_dict = {
        "vessel_name": "first",
        "vessel_flag": "first",
        "vessel_type": "first",
        "event_id": "count",
        "duration_hours": "sum",
    }
    # Add optional columns
    for col in ["vessel_category", "imo", "tonnage_gt", "length_m", "size_class", "shipname"]:
        if col in df:
            agg_dict[col] = "first"

    # Count unique ports visited
    if col_name in df:
        agg_dict[col_name] = "nunique"

    vessel_df = (
        df.groupby("vessel_id", dropna=False)
        .agg(agg_dict)
        .reset_index()
        .rename(columns={
            "event_id": "visit_count",
            "duration_hours": "total_hours",
            col_name: "ports_visited",
        })
        .sort_values("visit_count", ascending=False)
    )

    if "total_hours" in vessel_df:
        vessel_df["total_hours"] = vessel_df["total_hours"].round(1)

    # Use shipname from registry if available
    if "shipname" in vessel_df:
        vessel_df["display_name"] = vessel_df["shipname"].fillna(vessel_df["vessel_name"])
    else:
        vessel_df["display_name"] = vessel_df["vessel_name"]

    # Display columns
    display_cols = ["display_name", "vessel_id"]
    if "imo" in vessel_df:
        display_cols.append("imo")
    col_label = "vessel_category" if "vessel_category" in vessel_df else "vessel_type"
    display_cols.append(col_label)
    display_cols.append("vessel_flag")
    if "tonnage_gt" in vessel_df:
        display_cols.append("tonnage_gt")
    if "length_m" in vessel_df:
        display_cols.append("length_m")
    if "size_class" in vessel_df:
        display_cols.append("size_class")
    display_cols.extend(["visit_count", "total_hours"])
    if "ports_visited" in vessel_df:
        display_cols.append("ports_visited")

    # Filter only existing columns
    display_cols = [c for c in display_cols if c in vessel_df.columns]

    st.caption(f"Showing {len(vessel_df):,} unique vessels")

    # Column config
    col_config = {
        "display_name": st.column_config.TextColumn("Vessel Name"),
        "visit_count": st.column_config.NumberColumn("Visits", format="%d"),
        "total_hours": st.column_config.NumberColumn("Total Hours", format="%.1f"),
    }
    if "tonnage_gt" in display_cols:
        col_config["tonnage_gt"] = st.column_config.NumberColumn("GT", format="%,.0f")
    if "length_m" in display_cols:
        col_config["length_m"] = st.column_config.NumberColumn("Length (m)", format="%.0f")

    st.dataframe(
        vessel_df[display_cols],
        hide_index=True,
        use_container_width=True,
        column_config=col_config,
        height=600,
    )

    # Export
    csv = vessel_df[display_cols].to_csv(index=False)
    st.download_button(
        "📥 Download vessel list (CSV)",
        data=csv,
        file_name="vessels.csv",
        mime="text/csv",
    )
