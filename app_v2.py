"""
C-LeanWorld V2 — Country-Level Bulk Analysis
==============================================
Main Streamlit application.

Run with:  streamlit run app_v2.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

# Ensure project root is on the path
sys.path.insert(0, str(Path(__file__).resolve().parent))

# Load .env if present
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from src.port_data import (
    load_raw_cells,
    build_port_groups,
    build_sublabel_groups,
    load_eez_mapping,
    get_country_list,
    match_events_to_ports,
    build_port_visit_summary,
    filter_by_country,
    get_cells_for_port,
    port_bbox_coords,
)
from src.gfw_client_v2 import (
    fetch_country_events,
    parse_events_to_df,
    fetch_vessels_batch,
    parse_vessel_identities,
)
from src.vessel_cache import (
    get_many_by_id,
    set_many_by_id,
    get_cached_events,
    set_cached_events,
    cache_stats,
)
from src.analytics_v2 import (
    add_classifications,
    compute_port_scores,
)
from src.copernicus_client import fetch_currents, add_speed_direction

from components.country_selector import render_country_selector
from components.country_map import render_country_map, render_map_legend
from components.filters import render_filters, apply_filters
from components.overview_dashboard import render_overview_dashboard
from components.port_detail import render_port_detail
from components.comparison_view import render_comparison_view
from components.vessel_table import render_vessel_table
from components.current_dashboard import render_current_dashboard

# ── Page config ──────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="C-LeanWorld V2",
    page_icon="🧹",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.title("C-LeanWorld V2  🧹🚢")
st.caption("Country-Level Hull-Cleaning Robot Deployment Planner")


# ── Cache expensive data loads ───────────────────────────────────────────────

@st.cache_data(show_spinner="Loading port & anchorage reference data…")
def _load_reference_data():
    raw = load_raw_cells()
    ports = build_port_groups(raw)
    subs = build_sublabel_groups(raw)
    eez = load_eez_mapping()
    country_list = get_country_list(eez)
    return raw, ports, subs, eez, country_list


raw_cells, port_groups, sublabel_groups, eez_mapping, country_list = _load_reference_data()


# ── Sidebar: Country selector ────────────────────────────────────────────────

selection = render_country_selector(country_list)
iso3 = selection["iso3"]
country_name = selection["country_name"]
start_date = selection["start_date"]
end_date = selection["end_date"]
load_clicked = selection["load_clicked"]


# ── Data loading orchestrator ────────────────────────────────────────────────

def _load_country_data(iso3: str, country_name: str, start_date, end_date):
    """
    Run the full data pipeline:
    1. Fetch all events (with caching)
    2. Parse to DataFrame
    3. Batch vessel lookup (with caching)
    4. Match events to ports
    5. Add classifications
    6. Store in session_state
    """
    eez_info = eez_mapping.get(iso3)
    if not eez_info:
        st.error(f"No EEZ mapping found for {iso3}")
        return

    mrgids = eez_info["mrgids"]
    sd = str(start_date)
    ed = str(end_date)

    progress = st.progress(0, text="Starting data load…")
    status = st.empty()

    # Step 1: Fetch events (check cache first)
    status.info("Step 1/4: Fetching port visit events…")
    progress.progress(5, text="Checking event cache…")

    cached_events = get_cached_events(iso3, sd, ed)
    if cached_events is not None:
        raw_events = cached_events
        status.info(f"Step 1/4: Loaded {len(raw_events):,} events from cache")
    else:
        def _event_progress(msg):
            status.info(f"Step 1/4: {msg}")

        raw_events = fetch_country_events(
            mrgids=mrgids,
            start_date=sd,
            end_date=ed,
            progress_callback=_event_progress,
        )
        set_cached_events(iso3, sd, ed, raw_events)
        status.info(f"Step 1/4: Fetched {len(raw_events):,} events from GFW API")

    progress.progress(30, text=f"Fetched {len(raw_events):,} events")

    if not raw_events:
        progress.empty()
        status.warning("No port visit events found for this country and date range.")
        return

    # Step 2: Parse events
    status.info("Step 2/4: Parsing events…")
    records = parse_events_to_df(raw_events)
    events_df = pd.DataFrame(records)
    progress.progress(40, text="Events parsed")

    # Step 3: Batch vessel lookup
    unique_vessel_ids = events_df["vessel_id"].dropna().unique().tolist()
    status.info(f"Step 3/4: Looking up {len(unique_vessel_ids)} vessels…")

    cached_vessels, missing_ids = get_many_by_id(unique_vessel_ids)
    status.info(
        f"Step 3/4: {len(cached_vessels)} cached, "
        f"fetching {len(missing_ids)} from API…"
    )

    if missing_ids:
        def _vessel_progress(msg):
            status.info(f"Step 3/4: {msg}")

        raw_vessels = fetch_vessels_batch(
            missing_ids,
            progress_callback=_vessel_progress,
        )
        new_vessels = parse_vessel_identities(raw_vessels)

        # Cache new results
        new_cache = {}
        for v in new_vessels:
            vid = v.get("vessel_id")
            if vid:
                new_cache[vid] = v
        set_many_by_id(new_cache)
        cached_vessels.update(new_cache)

    progress.progress(70, text="Vessel data loaded")

    # Merge vessel details into events
    if cached_vessels:
        vessels_df = pd.DataFrame(cached_vessels.values())
        if "vessel_id" in vessels_df.columns:
            events_df = events_df.merge(
                vessels_df[["vessel_id", "imo", "shipname", "tonnage_gt", "length_m",
                            "vessel_type_registry", "flag_registry", "built_year"]],
                on="vessel_id",
                how="left",
            )

    # Step 4: Match events to ports
    status.info("Step 4/4: Matching events to port reference data…")
    country_ports = filter_by_country(port_groups, iso3)
    events_df = match_events_to_ports(events_df, raw_cells, country_ports)
    # Retry unmatched against ALL ports (events near borders / multi-country EEZs)
    still_unmatched = events_df["matched_label"].isna().sum()
    if still_unmatched > 0:
        events_df = match_events_to_ports(events_df, raw_cells, port_groups)
    progress.progress(85, text="Events matched to ports")

    # Add classifications
    events_df = add_classifications(events_df)

    # Build port summary
    port_summary = build_port_visit_summary(events_df)

    # Compute scores
    port_scores = compute_port_scores(events_df)

    progress.progress(100, text="Done!")

    # Store in session_state
    st.session_state["events_df"] = events_df
    st.session_state["port_summary"] = port_summary
    st.session_state["port_scores"] = port_scores
    st.session_state["loaded_iso3"] = iso3
    st.session_state["loaded_country_name"] = country_name
    st.session_state["loaded_dates"] = (sd, ed)
    st.session_state["n_unique_vessels"] = len(unique_vessel_ids)

    stats = cache_stats()
    status.success(
        f"✅ Loaded {len(events_df):,} events · "
        f"{len(unique_vessel_ids):,} vessels · "
        f"{len(port_summary):,} ports  "
        f"(Cache: {stats['total_entries']} entries, {stats['size_mb']:.1f} MB)"
    )
    progress.empty()


# Trigger load
if load_clicked:
    _load_country_data(iso3, country_name, start_date, end_date)
    st.rerun()


# ── Main content (only if data is loaded) ────────────────────────────────────

if "events_df" not in st.session_state or st.session_state.get("events_df") is None:
    st.markdown("---")
    st.info(
        "👈 Select a country and date range, then click **Load Country Data** to begin.\n\n"
        "**How it works:**\n"
        "1. Select a country from the dropdown\n"
        "2. Choose a date range\n"
        "3. Click **Load Country Data** — this fetches all port visits in the country's EEZ\n"
        "4. Explore: filter, compare ports, view vessel details\n"
    )
    st.stop()

events_df = st.session_state["events_df"]
port_summary = st.session_state.get("port_summary", pd.DataFrame())
port_scores = st.session_state.get("port_scores", pd.DataFrame())
loaded_country = st.session_state.get("loaded_country_name", "")

# ── Sidebar: Filters ─────────────────────────────────────────────────────────

filters = render_filters(events_df)
filtered_df = apply_filters(events_df, filters)

n_filtered = len(filtered_df)
n_total = len(events_df)
if n_filtered < n_total:
    st.sidebar.caption(f"Showing {n_filtered:,} of {n_total:,} events")

# Export button
st.sidebar.markdown("---")
csv_data = filtered_df.to_csv(index=False)
st.sidebar.download_button(
    "📥 Export filtered data (CSV)",
    data=csv_data,
    file_name=f"c_leanworld_{st.session_state.get('loaded_iso3', 'data')}.csv",
    mime="text/csv",
)

# Comparison selector
st.sidebar.markdown("---")
st.sidebar.header("⚖️ Compare Ports")
if not port_summary.empty:
    compare_ports = st.sidebar.multiselect(
        "Select ports to compare",
        port_summary["label"].tolist(),
        max_selections=5,
        help="Select 2–5 ports for side-by-side comparison",
    )
else:
    compare_ports = []

# ── Tabs ─────────────────────────────────────────────────────────────────────

tab_overview, tab_map, tab_detail, tab_vessels, tab_compare, tab_currents = st.tabs([
    "📊 Overview",
    "🗺️ Map",
    "🏗️ Port Detail",
    "🚢 Vessels",
    "⚖️ Compare",
    "🌊 Currents",
])

# Recompute port summary from filtered data
filtered_summary = build_port_visit_summary(filtered_df)
filtered_scores = compute_port_scores(filtered_df)

# ── Tab: Overview ─────────────────────────────────────────────────────────────
with tab_overview:
    render_overview_dashboard(filtered_df, loaded_country, filtered_scores)

# ── Tab: Map ──────────────────────────────────────────────────────────────────
with tab_map:
    st.header(f"🗺️ {loaded_country} — Port Map")

    selected_port = st.session_state.get("selected_port")

    # Show S2 cells if a port is selected
    cell_df = None
    if selected_port:
        cell_df = get_cells_for_port(raw_cells, selected_port)
        if cell_df.empty:
            cell_df = None

    clicked_port = render_country_map(
        filtered_summary,
        port_scores=filtered_scores,
        selected_port=selected_port,
        cell_df=cell_df,
    )
    render_map_legend()

    if clicked_port and clicked_port != selected_port:
        st.session_state["selected_port"] = clicked_port
        st.rerun()

# ── Tab: Port Detail ──────────────────────────────────────────────────────────
with tab_detail:
    if not filtered_summary.empty:
        port_options = filtered_summary["label"].tolist()
        current_port = st.session_state.get("selected_port")
        default_idx = 0
        if current_port and current_port in port_options:
            default_idx = port_options.index(current_port)

        detail_port = st.selectbox(
            "Select port",
            port_options,
            index=default_idx,
            key="detail_port_select",
        )

        if detail_port:
            st.session_state["selected_port"] = detail_port

            # Get port metadata
            port_row = port_groups[port_groups["label"] == detail_port]
            port_info = port_row.iloc[0].to_dict() if not port_row.empty else None

            render_port_detail(filtered_df, detail_port, port_info)
    else:
        st.info("No port data available. Load country data first.")

# ── Tab: Vessels ──────────────────────────────────────────────────────────────
with tab_vessels:
    render_vessel_table(filtered_df, port_filter=st.session_state.get("selected_port"))

# ── Tab: Compare ──────────────────────────────────────────────────────────────
with tab_compare:
    if compare_ports and len(compare_ports) >= 2:
        render_comparison_view(filtered_df, compare_ports)
    else:
        st.info(
            "Select 2–5 ports from the sidebar comparison panel to see "
            "side-by-side analysis."
        )

# ── Tab: Currents ─────────────────────────────────────────────────────────────
with tab_currents:
    selected_port_for_currents = st.session_state.get("selected_port")
    st.header("🌊 Ocean Current Analysis")

    if selected_port_for_currents:
        st.markdown(f"**Selected port:** {selected_port_for_currents}")

        if st.button("Fetch ocean currents", type="primary"):
            with st.spinner("Querying Copernicus Marine…"):
                try:
                    bbox = port_bbox_coords(raw_cells, selected_port_for_currents, pad_deg=0.02)
                    ds = fetch_currents(
                        min_lon=bbox["minimum_longitude"],
                        max_lon=bbox["maximum_longitude"],
                        min_lat=bbox["minimum_latitude"],
                        max_lat=bbox["maximum_latitude"],
                        start_date=str(start_date),
                        end_date=str(end_date),
                    )
                    ds = add_speed_direction(ds)
                    st.session_state["current_ds"] = ds
                    st.session_state["current_port"] = selected_port_for_currents
                except Exception as e:
                    st.error(f"Copernicus error: {e}")

        if (
            st.session_state.get("current_port") == selected_port_for_currents
            and "current_ds" in st.session_state
        ):
            render_current_dashboard(
                st.session_state["current_ds"],
                selected_port_for_currents,
            )
    else:
        st.info("Select a port from the Map or Port Detail tab first, then fetch current data here.")
