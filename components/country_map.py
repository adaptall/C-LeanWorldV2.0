"""
Country map component — shows all ports in a country with visit counts.

Uses pydeck for fast rendering. Circle size ∝ visit count, colour ∝ score.
"""

from __future__ import annotations

from typing import Optional

import pandas as pd
import pydeck as pdk
import streamlit as st


# Score → colour (green = good, yellow = medium, red = low)
def _score_to_colour(score: float) -> list[int]:
    """Map a 0–100 score to an RGBA colour."""
    if score >= 70:
        return [46, 204, 113, 200]   # green
    elif score >= 40:
        return [241, 196, 15, 200]   # yellow
    else:
        return [231, 76, 60, 200]    # red


def _visit_count_to_radius(count: int, max_count: int) -> float:
    """Map visit count to circle radius (meters) for pydeck."""
    if max_count <= 0:
        return 500
    import math
    normalised = math.sqrt(count / max(max_count, 1))
    return max(300, normalised * 5000)


def render_country_map(
    port_summary: pd.DataFrame,
    port_scores: Optional[pd.DataFrame] = None,
    selected_port: Optional[str] = None,
    cell_df: Optional[pd.DataFrame] = None,
) -> Optional[str]:
    """
    Render the country-level map with port markers.

    Parameters
    ----------
    port_summary : DataFrame with label, visit_count, centroid_lat, centroid_lon
    port_scores : optional DataFrame with port, score columns
    selected_port : currently selected port label (highlighted)
    cell_df : optional DataFrame of S2 cells to show for a selected port

    Returns
    -------
    Clicked port label (if any) or None
    """
    if port_summary.empty:
        st.info("No port data to display on the map.")
        return None

    # Merge scores if available
    map_data = port_summary.copy()
    if port_scores is not None and not port_scores.empty:
        score_map = dict(zip(port_scores["port"], port_scores["score"]))
        map_data["score"] = map_data["label"].map(score_map).fillna(50)
    else:
        map_data["score"] = 50

    max_count = map_data["visit_count"].max() if "visit_count" in map_data else 1

    # Compute display properties
    map_data["radius"] = map_data["visit_count"].apply(
        lambda c: _visit_count_to_radius(c, max_count)
    )
    map_data["colour"] = map_data["score"].apply(_score_to_colour)

    # Format tooltip text
    map_data["tooltip_text"] = map_data.apply(
        lambda r: (
            f"{r['label']}\n"
            f"Visits: {r['visit_count']:,}\n"
            f"Vessels: {r.get('unique_vessels', '?')}\n"
            f"Score: {r['score']:.0f}"
        ),
        axis=1,
    )

    # Centre map on country
    centre_lat = map_data["centroid_lat"].mean()
    centre_lon = map_data["centroid_lon"].mean()

    layers = []

    # Port markers
    layers.append(
        pdk.Layer(
            "ScatterplotLayer",
            data=map_data,
            get_position=["centroid_lon", "centroid_lat"],
            get_radius="radius",
            get_fill_color="colour",
            pickable=True,
            auto_highlight=True,
            opacity=0.8,
        )
    )

    # Port labels (for top ports)
    top_ports = map_data.nlargest(20, "visit_count")
    layers.append(
        pdk.Layer(
            "TextLayer",
            data=top_ports,
            get_position=["centroid_lon", "centroid_lat"],
            get_text="label",
            get_size=12,
            get_color=[0, 0, 0, 200],
            get_alignment_baseline="'bottom'",
            get_pixel_offset=[0, -15],
        )
    )

    # S2 cell scatter if a port is selected
    if cell_df is not None and not cell_df.empty:
        cell_data = cell_df.copy()
        cell_data["colour"] = cell_data["is_dock"].apply(
            lambda d: [52, 152, 219, 160] if d else [230, 126, 34, 160]
        )
        layers.append(
            pdk.Layer(
                "ScatterplotLayer",
                data=cell_data,
                get_position=["lon", "lat"],
                get_radius=100,
                get_fill_color="colour",
                pickable=False,
                opacity=0.6,
            )
        )
        # Zoom to selected port
        centre_lat = cell_data["lat"].mean()
        centre_lon = cell_data["lon"].mean()
        zoom = 12
    else:
        # Auto-zoom based on port spread
        lat_range = map_data["centroid_lat"].max() - map_data["centroid_lat"].min()
        lon_range = map_data["centroid_lon"].max() - map_data["centroid_lon"].min()
        spread = max(lat_range, lon_range)
        if spread > 20:
            zoom = 4
        elif spread > 10:
            zoom = 5
        elif spread > 5:
            zoom = 6
        elif spread > 2:
            zoom = 7
        else:
            zoom = 9

    view_state = pdk.ViewState(
        latitude=centre_lat,
        longitude=centre_lon,
        zoom=zoom,
        pitch=0,
    )

    deck = pdk.Deck(
        layers=layers,
        initial_view_state=view_state,
        tooltip={"text": "{tooltip_text}"},
        map_style="light",
    )

    st.pydeck_chart(deck, use_container_width=True)

    # Port selector dropdown (since pydeck click events are limited in Streamlit)
    if not map_data.empty:
        port_options = ["(none)"] + map_data.sort_values("visit_count", ascending=False)["label"].tolist()
        default_idx = 0
        if selected_port and selected_port in port_options:
            default_idx = port_options.index(selected_port)

        chosen = st.selectbox(
            "🔍 Select port for detail view",
            port_options,
            index=default_idx,
            key="port_selector_dropdown",
        )
        return chosen if chosen != "(none)" else None

    return None


def render_map_legend():
    """Show a simple colour legend below the map."""
    cols = st.columns(4)
    cols[0].markdown("🟢 **High score** (≥70)")
    cols[1].markdown("🟡 **Medium** (40–69)")
    cols[2].markdown("🔴 **Low** (<40)")
    cols[3].markdown("⭕ Size = visit count")
