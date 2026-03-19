"""
Country selector component — country dropdown, date range picker, load button.
"""

from __future__ import annotations

from datetime import date, timedelta

import streamlit as st


def render_country_selector(
    country_list: list[tuple[str, str]],
) -> dict:
    """
    Render the country selector in the sidebar.

    Parameters
    ----------
    country_list : list of (display_name, iso3) tuples

    Returns
    -------
    dict with keys: iso3, country_name, start_date, end_date, load_clicked
    """
    st.sidebar.header("🌍 Country Selection")

    # Country dropdown
    display_names = [f"{name} ({iso3})" for name, iso3 in country_list]
    default_idx = 0
    for i, (name, iso3) in enumerate(country_list):
        if iso3 == "SGP":
            default_idx = i
            break

    selected_display = st.sidebar.selectbox(
        "Country",
        display_names,
        index=default_idx,
        help="Select a country to fetch all port visit data for its EEZ",
    )
    selected_idx = display_names.index(selected_display)
    country_name, iso3 = country_list[selected_idx]

    # Date range
    st.sidebar.markdown("---")
    st.sidebar.subheader("📅 Date Range")
    col1, col2 = st.sidebar.columns(2)
    with col1:
        start_date = st.date_input(
            "Start",
            value=date.today() - timedelta(days=365),
            max_value=date.today(),
        )
    with col2:
        end_date = st.date_input(
            "End",
            value=date.today(),
            max_value=date.today(),
        )

    if start_date > end_date:
        st.sidebar.error("Start date must be before end date.")

    # Load button
    st.sidebar.markdown("---")
    load_clicked = st.sidebar.button(
        "🚀 Load Country Data",
        type="primary",
        use_container_width=True,
    )

    # Show status of loaded data
    if "loaded_iso3" in st.session_state:
        loaded = st.session_state["loaded_iso3"]
        loaded_name = st.session_state.get("loaded_country_name", loaded)
        n_events = len(st.session_state.get("events_df", []))
        n_vessels = st.session_state.get("n_unique_vessels", 0)
        st.sidebar.success(
            f"✅ **{loaded_name}** loaded\n\n"
            f"📊 {n_events:,} visits · {n_vessels:,} vessels"
        )

    return {
        "iso3": iso3,
        "country_name": country_name,
        "start_date": start_date,
        "end_date": end_date,
        "load_clicked": load_clicked,
    }
