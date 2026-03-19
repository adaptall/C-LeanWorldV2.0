"""
Comparison view — side-by-side comparison of 2–5 ports.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from src.analytics_v2 import (
    visit_summary,
    visits_by_vessel_type,
    deployment_score,
)


def render_comparison_view(
    events_df: pd.DataFrame,
    selected_ports: list[str],
):
    """
    Render side-by-side port comparison.

    Parameters
    ----------
    events_df : full events DataFrame
    selected_ports : list of 2–5 port labels to compare
    """
    if len(selected_ports) < 2:
        st.info("Select at least 2 ports to compare.")
        return

    st.header("⚖️ Port Comparison")

    col_name = "matched_label" if "matched_label" in events_df else "port_name"

    # Compute per-port summaries
    port_data = []
    for port in selected_ports:
        pdf = events_df[events_df[col_name] == port]
        if pdf.empty:
            continue
        summary = visit_summary(pdf)

        # Size distribution
        large_pct = None
        if "tonnage_gt" in pdf:
            valid_gt = pdf["tonnage_gt"].dropna()
            if len(valid_gt) > 0:
                large_pct = float((valid_gt >= 25000).mean() * 100)

        # Type mix
        ct_pct = None
        if "vessel_category" in pdf:
            total = len(pdf)
            ct = pdf["vessel_category"].isin(["Container", "Tanker"]).sum()
            ct_pct = float(ct / total * 100) if total > 0 else 0

        score = deployment_score(
            summary["total_visits"],
            summary.get("median_duration_h"),
            large_pct,
            ct_pct,
        )

        port_data.append({
            "Port": port,
            "Visits": summary["total_visits"],
            "Unique Vessels": summary.get("unique_vessels", 0),
            "Median Stay (h)": round(summary.get("median_duration_h", 0) or 0, 1),
            "Container+Tanker %": round(ct_pct or 0, 1),
            "Large Vessel %": round(large_pct or 0, 1),
            "Score": score,
        })

    if not port_data:
        st.warning("No data available for selected ports.")
        return

    comparison_df = pd.DataFrame(port_data)

    # KPI cards
    cols = st.columns(len(port_data))
    for i, row in enumerate(port_data):
        with cols[i]:
            st.markdown(f"### {row['Port']}")
            st.metric("Visits", f"{row['Visits']:,}")
            st.metric("Unique Vessels", f"{row['Unique Vessels']:,}")
            st.metric("Median Stay", f"{row['Median Stay (h)']}h")
            st.metric("Container+Tanker", f"{row['Container+Tanker %']}%")
            st.metric("Large Vessels (>25k GT)", f"{row['Large Vessel %']}%")
            st.metric("⭐ Score", f"{row['Score']}")

    # Radar chart
    st.subheader("Radar Comparison")
    categories = ["Visits", "Unique Vessels", "Median Stay (h)", "Container+Tanker %", "Large Vessel %", "Score"]

    fig = go.Figure()
    for _, row in comparison_df.iterrows():
        # Normalise each dimension to 0-1
        values = []
        for cat in categories:
            max_val = comparison_df[cat].max()
            values.append(row[cat] / max_val if max_val > 0 else 0)
        values.append(values[0])  # close the polygon

        fig.add_trace(go.Scatterpolar(
            r=values,
            theta=categories + [categories[0]],
            fill="toself",
            name=row["Port"],
        ))

    fig.update_layout(
        polar=dict(radialaxis=dict(visible=True, range=[0, 1])),
        height=500,
        margin=dict(l=60, r=60, t=40, b=40),
    )
    st.plotly_chart(fig, use_container_width=True)

    # Vessel type stacked bar
    st.subheader("Vessel Type Comparison")
    type_data = []
    for port in selected_ports:
        pdf = events_df[events_df[col_name] == port]
        vt = visits_by_vessel_type(pdf)
        for _, row in vt.iterrows():
            type_data.append({"Port": port, "Type": row["type"], "Count": row["count"]})

    if type_data:
        type_df = pd.DataFrame(type_data)
        fig = px.bar(
            type_df,
            x="Port",
            y="Count",
            color="Type",
            barmode="stack",
        )
        fig.update_layout(
            height=400,
            margin=dict(l=10, r=10, t=10, b=10),
        )
        st.plotly_chart(fig, use_container_width=True)

    # Summary table
    st.subheader("Summary Table")
    st.dataframe(
        comparison_df,
        hide_index=True,
        use_container_width=True,
        column_config={
            "Score": st.column_config.ProgressColumn(
                "Score", min_value=0, max_value=100, format="%.0f"
            ),
        },
    )
