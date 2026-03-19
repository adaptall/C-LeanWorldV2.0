"""
Overview dashboard component — country-wide summary, KPIs, top-N charts.
"""

from __future__ import annotations

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from src.analytics_v2 import (
    country_summary,
    visits_by_vessel_type,
    visits_by_flag,
    monthly_visit_counts,
    visits_by_size_class,
    top_ports,
)


def render_overview_dashboard(
    events_df: pd.DataFrame,
    country_name: str,
    port_scores: pd.DataFrame | None = None,
):
    """Render the country-level overview dashboard."""
    st.header(f"📊 {country_name} — Overview")

    if events_df.empty:
        st.warning("No data loaded.")
        return

    # KPI row
    summary = country_summary(events_df)
    cols = st.columns(4)
    cols[0].metric("Total Visits", f"{summary['total_visits']:,}")
    cols[1].metric("Unique Vessels", f"{summary.get('unique_vessels', 0):,}")
    cols[2].metric("Ports", f"{summary.get('unique_ports', 0):,}")
    cols[3].metric(
        "Median Stay",
        f"{summary.get('median_duration_h', 0):.1f}h"
        if summary.get("median_duration_h")
        else "—",
    )

    # Charts row 1: Top ports + Vessel type pie
    chart_col1, chart_col2 = st.columns(2)

    with chart_col1:
        st.subheader("Top 15 Ports")
        tp = top_ports(events_df, n=15)
        if not tp.empty:
            fig = px.bar(
                tp,
                x="count",
                y="port",
                orientation="h",
                color="count",
                color_continuous_scale="Blues",
            )
            fig.update_layout(
                yaxis=dict(autorange="reversed"),
                showlegend=False,
                height=450,
                margin=dict(l=10, r=10, t=10, b=10),
                coloraxis_showscale=False,
            )
            st.plotly_chart(fig, use_container_width=True)

    with chart_col2:
        st.subheader("Vessel Type Mix")
        vt = visits_by_vessel_type(events_df)
        if not vt.empty:
            fig = px.pie(
                vt,
                values="count",
                names="type",
                hole=0.4,
            )
            fig.update_layout(
                height=450,
                margin=dict(l=10, r=10, t=10, b=10),
            )
            st.plotly_chart(fig, use_container_width=True)

    # Charts row 2: Monthly trend + Flag distribution
    chart_col3, chart_col4 = st.columns(2)

    with chart_col3:
        st.subheader("Monthly Visits")
        mc = monthly_visit_counts(events_df)
        if not mc.empty:
            fig = px.line(
                mc,
                x="month",
                y="count",
                markers=True,
            )
            fig.update_layout(
                height=350,
                margin=dict(l=10, r=10, t=10, b=10),
                xaxis_title="",
                yaxis_title="Visits",
            )
            st.plotly_chart(fig, use_container_width=True)

    with chart_col4:
        st.subheader("Flag Distribution (Top 15)")
        vf = visits_by_flag(events_df).head(15)
        if not vf.empty:
            fig = px.bar(
                vf,
                x="count",
                y="flag",
                orientation="h",
                color="count",
                color_continuous_scale="Oranges",
            )
            fig.update_layout(
                yaxis=dict(autorange="reversed"),
                showlegend=False,
                height=350,
                margin=dict(l=10, r=10, t=10, b=10),
                coloraxis_showscale=False,
            )
            st.plotly_chart(fig, use_container_width=True)

    # Size distribution (if tonnage data available)
    if "size_class" in events_df:
        st.subheader("Vessel Size Distribution")
        sc = visits_by_size_class(events_df)
        if not sc.empty:
            # Order by size
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

    # Port scores table
    if port_scores is not None and not port_scores.empty:
        st.subheader("🎯 Port Deployment Scores")
        display_scores = port_scores.copy()
        for col in ["median_duration_h", "large_vessel_pct", "container_tanker_pct", "score"]:
            if col in display_scores:
                display_scores[col] = display_scores[col].round(1)
        st.dataframe(
            display_scores,
            hide_index=True,
            use_container_width=True,
            column_config={
                "score": st.column_config.ProgressColumn(
                    "Score",
                    min_value=0,
                    max_value=100,
                    format="%.0f",
                ),
            },
        )
