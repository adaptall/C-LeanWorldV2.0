"""
Port detail panel — single-port deep dive with charts and vessel table.
"""

from __future__ import annotations

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from src.analytics_v2 import (
    visit_summary,
    visits_by_vessel_type,
    visits_by_flag,
    monthly_visit_counts,
    visits_by_size_class,
    duration_histogram_data,
)


def render_port_detail(
    events_df: pd.DataFrame,
    port_label: str,
    port_info: dict | None = None,
):
    """
    Render detailed analytics for a single port.

    Parameters
    ----------
    events_df : all events (will be filtered to the selected port)
    port_label : the port label to show detail for
    port_info : optional dict with port metadata (iso3, cell_count, etc.)
    """
    # Filter to selected port
    col = "matched_label" if "matched_label" in events_df else "port_name"
    port_df = events_df[events_df[col] == port_label].copy()

    if port_df.empty:
        st.warning(f"No visit data for {port_label}.")
        return

    st.header(f"🏗️ {port_label}")

    # KPIs
    summary = visit_summary(port_df)
    cols = st.columns(5)
    cols[0].metric("Visits", f"{summary['total_visits']:,}")
    cols[1].metric("Unique Vessels", f"{summary.get('unique_vessels', 0):,}")
    cols[2].metric("Flags", f"{summary.get('unique_flags', 0):,}")
    cols[3].metric(
        "Median Stay",
        f"{summary.get('median_duration_h', 0):.1f}h"
        if summary.get("median_duration_h")
        else "—",
    )
    cols[4].metric(
        "P90 Stay",
        f"{summary.get('p90_duration_h', 0):.1f}h"
        if summary.get("p90_duration_h")
        else "—",
    )

    # Dock info
    if port_info:
        info_cols = st.columns(3)
        info_cols[0].metric("Country", port_info.get("iso3", "—"))
        info_cols[1].metric("S2 Cells", port_info.get("cell_count", "—"))
        dock_status = "Yes" if port_info.get("has_dock") else "No"
        info_cols[2].metric("Has Dock", dock_status)

    # Charts row 1: Duration histogram + Vessel type breakdown
    c1, c2 = st.columns(2)

    with c1:
        st.subheader("Stay Duration (hours)")
        dur_data = port_df["duration_hours"].dropna()
        if not dur_data.empty:
            fig = px.histogram(
                dur_data,
                nbins=30,
                labels={"value": "Hours", "count": "Visits"},
            )
            fig.update_layout(
                height=350,
                margin=dict(l=10, r=10, t=10, b=10),
                showlegend=False,
                xaxis_title="Duration (hours)",
                yaxis_title="Count",
            )
            st.plotly_chart(fig, use_container_width=True)

            # Box plot
            fig_box = px.box(dur_data, x="duration_hours", labels={"duration_hours": "Hours"})
            fig_box.update_layout(
                height=100,
                margin=dict(l=10, r=10, t=5, b=5),
                showlegend=False,
            )
            st.plotly_chart(fig_box, use_container_width=True)

    with c2:
        st.subheader("Vessel Type Mix")
        vt = visits_by_vessel_type(port_df)
        if not vt.empty:
            fig = px.pie(vt, values="count", names="type", hole=0.4)
            fig.update_layout(
                height=350,
                margin=dict(l=10, r=10, t=10, b=10),
            )
            st.plotly_chart(fig, use_container_width=True)

    # Charts row 2: Monthly trend + Size distribution
    c3, c4 = st.columns(2)

    with c3:
        st.subheader("Monthly Visits")
        mc = monthly_visit_counts(port_df)
        if not mc.empty:
            fig = px.bar(mc, x="month", y="count")
            fig.update_layout(
                height=300,
                margin=dict(l=10, r=10, t=10, b=10),
                xaxis_title="",
                yaxis_title="Visits",
            )
            st.plotly_chart(fig, use_container_width=True)

    with c4:
        if "size_class" in port_df:
            st.subheader("Vessel Size Distribution")
            sc = visits_by_size_class(port_df)
            if not sc.empty:
                size_order = ["Small", "Medium", "Large", "Very Large", "Unknown"]
                sc["size_class"] = pd.Categorical(sc["size_class"], categories=size_order, ordered=True)
                sc = sc.sort_values("size_class")
                fig = px.bar(
                    sc,
                    x="size_class",
                    y="count",
                    color="size_class",
                    color_discrete_map={
                        "Small": "#e74c3c",
                        "Medium": "#f39c12",
                        "Large": "#2ecc71",
                        "Very Large": "#3498db",
                        "Unknown": "#95a5a6",
                    },
                )
                fig.update_layout(
                    height=300,
                    margin=dict(l=10, r=10, t=10, b=10),
                    showlegend=False,
                    xaxis_title="",
                    yaxis_title="Visits",
                )
                st.plotly_chart(fig, use_container_width=True)

    # Top visiting vessels table
    st.subheader("🚢 Top Visiting Vessels")
    vessel_cols = ["vessel_name", "vessel_id", "vessel_flag"]
    if "vessel_category" in port_df:
        vessel_cols.append("vessel_category")
    elif "vessel_type" in port_df:
        vessel_cols.append("vessel_type")
    if "imo" in port_df:
        vessel_cols.append("imo")
    if "tonnage_gt" in port_df:
        vessel_cols.append("tonnage_gt")
    if "length_m" in port_df:
        vessel_cols.append("length_m")

    vessel_agg = (
        port_df.groupby("vessel_id", dropna=False)
        .agg(
            vessel_name=("vessel_name", "first"),
            vessel_flag=("vessel_flag", "first"),
            vessel_type=("vessel_type", "first"),
            visit_count=("event_id", "count"),
            total_hours=("duration_hours", "sum"),
        )
        .reset_index()
        .sort_values("visit_count", ascending=False)
    )

    # Merge extra columns if available
    extra_cols = {}
    for col_name in ["imo", "tonnage_gt", "length_m", "vessel_category"]:
        if col_name in port_df:
            extra = port_df.drop_duplicates("vessel_id")[["vessel_id", col_name]]
            vessel_agg = vessel_agg.merge(extra, on="vessel_id", how="left")

    if "total_hours" in vessel_agg:
        vessel_agg["total_hours"] = vessel_agg["total_hours"].round(1)

    st.dataframe(
        vessel_agg.head(50),
        hide_index=True,
        use_container_width=True,
    )
