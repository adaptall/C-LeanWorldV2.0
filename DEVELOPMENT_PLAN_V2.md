# C-LeanWorld V2 — Country-Level Bulk Analysis

## 1. Motivation & Key Differences from V1

### V1 Limitations (Lessons Learned)
| Issue | Impact |
|---|---|
| **Port-by-port approach** — User must select a single port, then fetch data | Slow exploration; can't easily compare ports across a country |
| **POST /events with geometry** — One polygon per query, tight bbox | Only sees visits at the selected port; no country-wide picture |
| **Sequential vessel enrichment** — One-by-one GFW detail fetch, then VesselFinder scrape | Very slow for 100+ vessels; VesselFinder scraping is fragile |
| **No bulk vessel lookup** — Fetched vessel details individually via `GET /v3/vessels/{id}` | Rate-limited; O(n) requests for n vessels |
| **Data loaded on demand** — User clicks buttons to fetch visits, then currents | Multi-step workflow feels slow; no overview until data is loaded |

### V2 Approach: "Pull Everything, Then Explore"
1. **Select a country** → bulk-fetch ALL port visit events using `GET /v3/events` with EEZ `regions` parameter
2. **One batch request** for all unique vessel details using `GET /v3/vessels?ids[]=...` to get IMO numbers
3. **All data in memory** → user filters, compares, drills down interactively — no more waiting
4. **Maps double as selectors** — click regions, draw boxes, or lasso ports on the map for detailed analysis

---

## 2. Data Sources (Updated)

### 2.1 Global Fishing Watch — V3 API

#### GET /v3/events — Bulk Event Fetch by Region
```
GET https://gateway.api.globalfishingwatch.org/v3/events
  ?datasets[]=public-global-port-visits-events:latest
  &start-date=2024-01-01
  &end-date=2024-12-31
  &regions[]=eez:{MRGID}
  &limit=99999
  &offset=0
```

| Parameter | Details |
|---|---|
| `datasets[]` | `public-global-port-visits-events:latest` |
| `start-date`, `end-date` | ISO date strings |
| `regions[]` | EEZ region identifier, e.g. `eez:5668` (Singapore), `eez:5696` (Netherlands). Uses Marine Regions MRGID. Multiple regions can be combined for countries with multiple EEZ zones. |
| `limit` | Max 99999 per request |
| `offset` | For pagination if > 99999 results |

**Why GET with regions instead of POST with geometry:**
- No need to construct or manage polygon geometries
- EEZ boundaries are pre-indexed on GFW's server → faster queries
- One request covers an entire country's maritime area (all ports + anchorages)
- Simpler code with fewer moving parts

#### GET /v3/vessels — Batch Vessel Lookup by IDs
```
GET https://gateway.api.globalfishingwatch.org/v3/vessels
  ?datasets[]=public-global-vessel-identity:latest
  &ids[]=vessel_id_1
  &ids[]=vessel_id_2
  ...
```

| Parameter | Details |
|---|---|
| `datasets[]` | `public-global-vessel-identity:latest` |
| `ids[]` | Array of GFW vessel IDs (from events response) |

Returns vessel identity for all requested IDs in a single response, including:
- IMO number
- Ship name, callsign
- Vessel type (CARRIER, CARGO, TANKER, FISHING, etc.)
- Flag state
- Tonnage (GT), length, built year

**Why batch instead of one-by-one:**
- V1 made N sequential requests → N × 0.5s minimum = minutes for large fleets
- Batch endpoint returns all vessels in one round-trip
- Reduces total API calls from O(n) to O(n/batch_size)

### 2.2 EEZ → Country Mapping

GFW's `regions` parameter uses **Marine Regions MRGID** (Marineregions.org) identifiers for EEZ boundaries.

**Mapping strategy:**
- Bundle a lookup table: `ISO3 → [MRGID, ...]` (some countries have multiple EEZ zones, e.g. France has metropolitan + overseas territories)
- The lookup table can be derived from Marine Regions data or hardcoded for the ~50 countries with significant commercial shipping
- Store as a JSON/CSV in `Base Data/eez_country_mapping.json`

**Source:** https://www.marineregions.org/downloads.php — "Union of ESRI Country shapefile and EEZ (version 4)" provides the MRGID↔ISO3↔country name mapping.

### 2.3 Port & Anchorage Reference Data (Same as V1)

The GFW anchorage CSV (`Base Data/named_anchorages_v2_pipe_v3_202601.csv`) with 166k S2 cells remains the foundation for:
- Mapping event coordinates to named ports/anchorages
- Showing port locations on the map
- Computing port-level aggregations

### 2.4 Copernicus Marine (Unchanged — Phase 2)

Ocean current data remains the same as V1; fetched on demand for specific anchorage areas the user selects for detailed analysis.

---

## 3. Architecture Overview

```
┌──────────────────────────────────────────────────────────────────────────┐
│                          Streamlit Frontend                             │
│                                                                         │
│  ┌──────────────────┐  ┌─────────────────────┐  ┌────────────────────┐  │
│  │   Country Map    │  │   Filter & Compare  │  │   Detail Panel     │  │
│  │   (pydeck)       │  │   (sidebar + tabs)  │  │   (charts/tables)  │  │
│  │                  │  │                     │  │                    │  │
│  │ • All ports in   │  │ • Vessel type       │  │ • Port visits      │  │
│  │   country shown  │  │ • Vessel size (GT)  │  │ • Duration distrib │  │
│  │ • Colour by      │  │ • Flag state        │  │ • Vessel mix       │  │
│  │   visit count    │  │ • Min stay duration │  │ • Size breakdown   │  │
│  │ • Click/lasso    │  │ • Time period       │  │ • Trend over time  │  │
│  │   to select area │  │ • Port type (dock/  │  │ • Current analysis │  │
│  │ • Heatmap layer  │  │   anchorage/both)   │  │ • Compare selected │  │
│  └────────┬─────────┘  └──────────┬──────────┘  └────────┬───────────┘  │
│           │                       │                      │              │
│  ─────────┴───────────────────────┴──────────────────────┴───────────── │
│              st.session_state (all data cached in memory)               │
└──────────────────────────────┬──────────────────────────────────────────┘
                               │
              ┌────────────────┴──────────────────┐
              │        Python Backend              │
              │  ┌─────────────────────────────┐   │
              │  │  gfw_client_v2.py            │   │  ← GET events + batch vessels
              │  │  port_data.py (enhanced)     │   │  ← Port ref + EEZ mapping
              │  │  analytics_v2.py             │   │  ← Country-level aggregations
              │  │  copernicus_client.py        │   │  ← Ocean currents (on demand)
              │  │  vessel_cache.py (enhanced)  │   │  ← Disk cache for vessel data
              │  └─────────────────────────────┘   │
              └───────┬──────────────┬─────────────┘
                      │              │
           ┌──────────┘              └──────────┐
           ▼                                    ▼
  ┌──────────────────┐             ┌────────────────────┐
  │  GFW V3 API      │             │  Copernicus Marine  │
  │  (GET /events)   │             │  Toolbox (on demand)│
  │  (GET /vessels)  │             │                    │
  └──────────────────┘             └────────────────────┘
```

---

## 4. Data Flow

### 4.1 Startup: Load Reference Data (instant, cached)
```
App start
  │
  ├── Load anchorage CSV (166k cells) → @st.cache_data
  ├── Build port_groups by label + country (iso3)
  ├── Load EEZ→country mapping JSON
  └── Render country selector dropdown (209 countries, sorted by name)
```

### 4.2 Country Selection: Bulk Data Pull

```
User selects country (e.g. "Singapore" → ISO3 "SGP")
  │
  ├── Look up MRGID(s) for SGP → [5668]
  │
  ├── Step 1: Fetch ALL port visit events ─────────────────────────────┐
  │   GET /v3/events                                                    │
  │     ?datasets[]=public-global-port-visits-events:latest             │
  │     &start-date=2024-01-01 &end-date=2024-12-31                    │
  │     &regions[]=eez:5668                                             │
  │     &limit=99999                                                    │
  │   Paginate if needed (offset += limit until no more entries)        │
  │                                                                     │
  │   → Raw events list (may be 1k–50k+ events)                        │
  │   → Parse to DataFrame: vessel_id, vessel_name, MMSI, flag, type,  │
  │     start, end, duration_hours, port_name, port_id, lat, lon,      │
  │     at_dock, confidence                                             │
  ├─────────────────────────────────────────────────────────────────────┘
  │
  ├── Step 2: Batch vessel lookup ─────────────────────────────────────┐
  │   Extract unique vessel_ids from all events                         │
  │   Check disk cache → split into (cached, missing)                   │
  │                                                                     │
  │   GET /v3/vessels                                                   │
  │     ?datasets[]=public-global-vessel-identity:latest                │
  │     &ids[]=<missing_id_1>&ids[]=<missing_id_2>...                   │
  │   (batch in chunks of ~50 per request if needed)                    │
  │                                                                     │
  │   → Vessel detail: IMO, shipname, type, flag, tonnage_gt, length_m  │
  │   → Cache to disk (persistent across sessions)                      │
  │   → Merge into events DataFrame                                     │
  ├─────────────────────────────────────────────────────────────────────┘
  │
  ├── Step 3: Enrich with port reference data ─────────────────────────┐
  │   Match event port_name / coordinates to anchorage CSV entries       │
  │   → Add: sublabel, is_dock, distance_from_shore, label_source       │
  │   → Group events by port (label) for map and aggregations           │
  ├─────────────────────────────────────────────────────────────────────┘
  │
  └── Store in session_state → ready for interactive exploration
```

### 4.3 Interactive Exploration (no more API calls)

```
All data in memory — user freely filters and explores:
  │
  ├── MAP: All ports/anchorages in country shown
  │   • Size/colour by total visit count
  │   • Click port → detail panel
  │   • Draw rectangle / lasso → multi-port comparison
  │
  ├── FILTERS (sidebar, applied client-side):
  │   • Vessel type: Container, Tanker, Bulk, Cargo, Other
  │   • Vessel size: GT ranges (e.g. >10k GT, >30k GT, >50k GT)
  │   • Flag state: multi-select
  │   • Min/max stay duration
  │   • Dock vs anchorage
  │   • Date sub-range (within fetched period)
  │
  ├── OVERVIEW DASHBOARD:
  │   • Top-N ports by visit count, unique vessels, total vessel-hours
  │   • Country-wide KPIs: total visits, unique vessels, vessel type mix
  │   • Time series: monthly visits across country
  │   • Heatmap: port × month visit volume
  │
  ├── PORT DETAIL (click on map or select from list):
  │   • Visit statistics for that specific port
  │   • Duration distribution (histogram, box plot)
  │   • Vessel type and size breakdown
  │   • Trend over selected period
  │   • Top visiting vessels table
  │
  ├── COMPARISON (select 2-5 ports):
  │   • Side-by-side KPIs
  │   • Radar/spider chart: visits, avg duration, vessel diversity, size
  │   • Stacked bar: vessel type mix per port
  │
  └── OCEAN CURRENTS (on demand, for selected anchorage):
      • Fetch Copernicus data for specific bbox
      • Speed distribution, direction rose, hourly profile
      • Overlay on map
```

---

## 5. Project Structure (V2)

```
C-LeanWorld/
├── app_v2.py                         # V2 Streamlit entry point
├── DEVELOPMENT_PLAN_V2.md            # ← this file
├── requirements.txt                  # (updated with any new deps)
├── .env.example
├── Base Data/
│   ├── named_anchorages_v2_pipe_v3_202601.csv   # Port/anchorage S2 cells
│   └── eez_country_mapping.json      # ISO3 → MRGID(s) lookup
├── data/
│   └── vessel_cache/                 # diskcache persistent storage
├── src/
│   ├── __init__.py
│   ├── gfw_client_v2.py              # GET events by region + batch vessels
│   ├── copernicus_client.py          # Unchanged from V1
│   ├── port_data.py                  # Enhanced: country filtering + event→port matching
│   ├── analytics_v2.py               # Country-level + port-level aggregations
│   ├── vessel_cache.py               # Enhanced: batch get/set for vessel identity
│   └── utils.py                      # Haversine, bbox helpers
├── components/
│   ├── __init__.py
│   ├── country_selector.py           # Country + date range picker
│   ├── country_map.py                # Full-country map with port markers
│   ├── overview_dashboard.py         # Country-wide summary + top-N ports
│   ├── port_detail.py                # Single-port deep dive
│   ├── comparison_view.py            # Multi-port side-by-side
│   ├── vessel_table.py               # Filterable vessel list with details
│   └── current_dashboard.py          # Ocean current analysis (reuse from V1)
└── tests/
    ├── test_gfw_client_v2.py
    ├── test_analytics_v2.py
    └── conftest.py
```

---

## 6. Feature Breakdown & Milestones

### Phase 1 — Bulk Data Pipeline (Core)

| # | Task | Details | Depends on |
|---|---|---|---|
| 1.1 | **EEZ→country mapping** | Build/bundle `eez_country_mapping.json` — a JSON mapping of ISO3 code → list of MRGID integers. Derive from Marine Regions data or curate manually for top 50+ shipping countries. Include country display name. | — |
| 1.2 | **GFW GET events client** | New `gfw_client_v2.py` with `fetch_country_events(mrgids, start_date, end_date)`. Uses `GET /v3/events` with `regions[]=eez:{mrgid}`. Handles pagination (offset/limit loop). Handles 429 rate-limit. Returns raw event list. | — |
| 1.3 | **Event parser (enhanced)** | `parse_events_to_df()` → DataFrame with all fields from V1 plus coordinates for spatial matching. Handle edge cases: missing duration, missing anchorage names. | 1.2 |
| 1.4 | **Batch vessel lookup** | `fetch_vessels_batch(vessel_ids)` using `GET /v3/vessels?ids[]=...`. Chunk into batches of 50. Merge with disk cache. Returns DataFrame: vessel_id → IMO, name, type, flag, tonnage_gt, length_m. | — |
| 1.5 | **Event→port matching** | Match events to anchorage CSV entries by port_name/anchorage_id/coordinates. Attach: label, sublabel, is_dock, iso3. Group by port for aggregations. | 1.3 |
| 1.6 | **Disk cache enhancements** | Extend `vessel_cache.py` for batch vessel identity storage (by IMO and by vessel_id). Add country-level event cache: cache fetched events per (iso3, date_range) to avoid re-fetching. TTL 24h for events, permanent for vessel identity. | 1.4 |
| 1.7 | **Data loading orchestrator** | Streamlit component that runs the full pipeline on country selection: (1) fetch events → (2) batch vessel lookup → (3) match to ports → (4) store in session_state. Show progress bar for each step. | 1.2–1.6 |

### Phase 2 — Interactive Exploration UI

| # | Task | Details | Depends on |
|---|---|---|---|
| 2.1 | **Country selector** | Sidebar: country dropdown (searchable), date range picker, "Load data" button. Show status of loaded data. | 1.7 |
| 2.2 | **Country map** | pydeck map showing all ports in the country. Circle size = visit count, colour = vessel type diversity or a selected metric. Tooltip: port name, visit count, top vessel types. Click → select port for detail. | 1.7 |
| 2.3 | **Sidebar filters** | Vessel type multi-select, GT range slider, flag multi-select, dock/anchorage toggle, min stay slider, date sub-range. All filters apply to the in-memory DataFrame instantly. | 1.7 |
| 2.4 | **Overview dashboard** | Country-level KPIs: total visits, unique vessels, unique ports, vessel type breakdown. Charts: top-15 ports bar chart, monthly time series, vessel type pie, flag distribution. | 1.7 |
| 2.5 | **Port detail panel** | On port selection (map click or dropdown): visit count, duration histogram, vessel type/size breakdown, monthly trend, top visiting vessels table with IMO/GT/length. | 2.2 |
| 2.6 | **Vessel table** | Full list of unique vessels visiting the country (or selected port). Columns: name, IMO, type, flag, GT, length, visit count, total hours, ports visited. Sortable, filterable. Link to MarineTraffic/VesselFinder by IMO. | 1.7 |

### Phase 3 — Comparison & Business Case

| # | Task | Details | Depends on |
|---|---|---|---|
| 3.1 | **Multi-port comparison** | Select 2–5 ports from map (checkbox) or from a list. Side-by-side: visit volume, duration distribution, vessel type mix, vessel size distribution. Radar chart for normalised scores. | 2.5 |
| 3.2 | **Deployment suitability score** | Per-port score combining: visit volume (log-scaled), large-vessel share (% GT > threshold), median dwell time, vessel type mix favouring container/tanker. Configurable weights. Colour map markers by score. | 2.4 |
| 3.3 | **Ocean current overlay** | For selected port/anchorage: fetch Copernicus data (on demand). Speed histogram, direction rose, feasibility %. Integrate into port detail and comparison views. | V1 Copernicus client |
| 3.4 | **Export** | Download full dataset (filtered) as CSV. Download port comparison summary as CSV/Excel. Download individual charts as PNG. | 2.4, 3.1 |

### Phase 4 — Polish & Performance

| # | Task | Details | Depends on |
|---|---|---|---|
| 4.1 | **Caching & performance** | Pre-aggregate port summaries on load (avoid recomputing on every filter change). Use `st.cache_data` for expensive transforms. Lazy-load detail panels. | 2.x |
| 4.2 | **Map interactions** | Lasso / rectangle selection on map to select ports in an area. Map layer toggles (heatmap, individual ports, port areas). | 2.2 |
| 4.3 | **UI polish** | Loading states, error toasts, help tooltips, responsive layout. Colour scheme: green/yellow/red for suitability. | All |
| 4.4 | **Testing** | Unit tests for event parsing, analytics, vessel cache. Integration tests with mocked GFW responses. | All |
| 4.5 | **Documentation** | README with setup instructions, screenshots, architecture diagram. | All |

---

## 7. API Integration Details (V2)

### 7.1 GET Events by EEZ Region — Full Flow

```
User selects "Singapore" (ISO3: SGP)
  │
  ▼
Look up eez_country_mapping.json
  → SGP: { "name": "Singapore", "mrgids": [5668] }
  │
  ▼
GET /v3/events?datasets[]=public-global-port-visits-events:latest
              &start-date=2024-01-01
              &end-date=2024-12-31
              &regions[]=eez:5668
              &limit=99999
              &offset=0
  │
  ▼
Response: { "entries": [...], "total": 12345, "limit": 99999, "offset": 0 }
  │
  ├── If total > limit: paginate (offset += limit, repeat)
  │
  ▼
Parse each event → flat record:
  {
    event_id, visit_id, confidence,
    vessel_id, vessel_name, vessel_mmsi, vessel_flag, vessel_type,
    start, end, duration_hours,
    port_name, port_id, port_flag, at_dock,
    lat, lon
  }
  │
  ▼
→ events_df (pandas DataFrame, all events in country)
```

### 7.2 Batch Vessel Lookup — Full Flow

```
Extract unique vessel_ids from events_df
  → e.g. 850 unique vessels
  │
  ▼
Check disk cache (vessel_cache.py)
  → 600 found in cache, 250 missing
  │
  ▼
Chunk missing IDs into batches of 50:
  [batch_1: 50 IDs, batch_2: 50 IDs, ..., batch_5: 50 IDs]
  │
  ▼
For each batch:
  GET /v3/vessels?datasets[]=public-global-vessel-identity:latest
                 &ids[]=id1&ids[]=id2&...&ids[]=id50
  │
  ▼
Parse response → for each vessel:
  {
    vessel_id, imo, shipname, callsign,
    vessel_type, flag, tonnage_gt, length_m
  }
  │
  ▼
Cache new results to disk (permanent TTL)
  │
  ▼
Merge all vessel details into events_df:
  events_df.merge(vessels_df, on="vessel_id")
  │
  ▼
→ Enriched events_df with IMO, tonnage, length per vessel
```

### 7.3 Event → Port Matching

Port visit events from GFW contain anchorage names and coordinates, but these may not perfectly align with the bundled anchorage CSV labels. Matching strategy:

```
For each event in events_df:
  │
  ├── Primary match: event.port_name → anchorage CSV "label" (exact, case-insensitive)
  │   If match → assign label, sublabel, is_dock from CSV
  │
  ├── Secondary match: event.anchorage_id → anchorage CSV "s2id"
  │   If match → assign all CSV fields
  │
  └── Tertiary match: event (lat, lon) → nearest anchorage CSV cell (haversine < 5km)
      If match → assign CSV fields
      If no match → mark as "unmatched" (still usable, just no CSV enrichment)
```

### 7.4 Vessel Classification

For business-case analysis, we classify vessels into actionable categories:

| Category | GFW type values | Business relevance |
|---|---|---|
| **Container** | CONTAINER, CONTAINER_REEFER | Primary target — regular schedules, large hulls |
| **Tanker** | TANKER, OIL_CHEMICAL_TANKER, LNG_TANKER, LPG_TANKER | Primary target — long stays, large hulls |
| **Bulk Carrier** | BULK_CARRIER, ORE_CARRIER | Secondary — large hulls, often long stays |
| **General Cargo** | CARGO, GENERAL_CARGO, VEHICLE_CARRIER | Secondary — varies by segment |
| **Passenger** | PASSENGER, CRUISE, FERRY | Low priority — short stays, scheduled |
| **Other** | FISHING, TUG, SUPPLY_VESSEL, etc. | Not target market |

Additional size classification by GT:
| Size class | GT range | Typical hull area |
|---|---|---|
| Small | < 5,000 GT | Not commercially viable |
| Medium | 5,000 – 25,000 GT | Lower priority |
| Large | 25,000 – 60,000 GT | Core market |
| Very Large | > 60,000 GT | Premium market |

---

## 8. UI Wireframe (V2)

### 8.1 Main View — Country Overview

```
┌──────────────────────────────────────────────────────────────────────────┐
│  C-LeanWorld V2  🧹🚢    [Country: Singapore ▾]  [2024-01-01 to 2024-12-31]  [Load ▶]  │
├──────────────┬───────────────────────────────────────────────────────────┤
│  FILTERS     │                       MAP                                │
│              │                                                          │
│ Vessel type  │     ● SINGAPORE (236 visits)                             │
│ ☑ Container  │         ● JURONG (89)       ← size = visit count        │
│ ☑ Tanker     │         ● CHANGI (34)       ← colour = suitability      │
│ ☑ Bulk       │     ● WEST JURONG (156)                                  │
│ ☐ Cargo      │     ● EAST JOHOR (45)                                    │
│ ☐ Other      │                                                          │
│              │   [Click port for detail | Draw box to compare]          │
│ Size (GT)    │                                                          │
│ [5k ──── 200k]│                                                         │
│              ├──────────────────────────────────────────────────────────┤
│ Flag state   │              OVERVIEW DASHBOARD                          │
│ ☑ All        │  ┌───────────────────────────────────────────────────┐   │
│              │  │  Total visits: 2,456  │  Unique vessels: 847      │   │
│ Stay (hours) │  │  Ports: 12           │  Avg stay: 36h             │   │
│ [0 ──── 168] │  └───────────────────────────────────────────────────┘   │
│              │  ┌───────────┐ ┌──────────┐ ┌──────────────────────┐    │
│ Port type    │  │  Top 15   │ │  Vessel  │ │  Monthly visits      │    │
│ ○ All        │  │  ports    │ │  type    │ │  (time series)       │    │
│ ○ Dock       │  │  (bar)   │ │  (pie)   │ │  (line chart)        │    │
│ ○ Anchorage  │  └───────────┘ └──────────┘ └──────────────────────┘    │
│              │  ┌──────────────────────────────────────────────────┐    │
│ [Compare ☐]  │  │  Vessel Size Distribution (histogram by GT)     │    │
│  Port A ☑    │  │  stacked by vessel type                          │    │
│  Port B ☑    │  └──────────────────────────────────────────────────┘    │
│ [Compare ▶]  │                                                          │
│              │                                                          │
│ [Export CSV] │                                                          │
└──────────────┴──────────────────────────────────────────────────────────┘
```

### 8.2 Port Detail View (on click)

```
┌──────────────────────────────────────────────────────────────────────────┐
│  ← Back to Overview    PORT: WEST JURONG ANCHORAGE (Singapore)          │
├──────────────┬──────────────────────────────────────────────────────────┤
│  PORT INFO   │                    MAP (zoomed)                          │
│              │     ● S2 cells of the anchorage area                    │
│ Visits: 156  │     ○ Individual vessel visit positions                  │
│ Vessels: 98  │                                                          │
│ Med stay: 42h│                                                          │
│ Dock: No     │                                                          │
│              ├──────────────────────────────────────────────────────────┤
│ VESSEL MIX   │  ┌───────────┐ ┌──────────────┐ ┌─────────────────┐    │
│ Container 45%│  │  Stay     │ │  Vessel size │ │  Monthly trend  │    │
│ Tanker   30% │  │  duration │ │  distribution│ │  (bar chart)    │    │
│ Bulk     15% │  │  (box-   │ │  by GT (hist)│ │                 │    │
│ Other    10% │  │   plot)   │ │              │ │                 │    │
│              │  └───────────┘ └──────────────┘ └─────────────────┘    │
│ [Fetch       │  ┌────────────────────────────────────────────────┐     │
│  Currents]   │  │  Visiting Vessels Table                        │     │
│              │  │  Name | IMO | Type | GT | Flag | Visits | Hrs  │     │
│              │  │  MAERSK... | 9... | Container | 120k | DNK | 3│     │
│              │  │  EAGLE...  | 9... | Tanker    |  85k | LBR | 2│     │
│              │  │  ...                                           │     │
│              │  └────────────────────────────────────────────────┘     │
└──────────────┴────────────────────────────────────────────────────────┘
```

### 8.3 Comparison View

```
┌──────────────────────────────────────────────────────────────────────────┐
│  COMPARING: West Jurong  vs  East Johor  vs  Singapore Anchorage        │
├──────────────────────────────────────────────────────────────────────────┤
│  ┌──────────────┬──────────────┬──────────────┐                         │
│  │ West Jurong  │ East Johor   │ Singapore    │                         │
│  │ Visits: 156  │ Visits: 45   │ Visits: 236  │                         │
│  │ Vessels: 98  │ Vessels: 32  │ Vessels: 180 │                         │
│  │ Med stay: 42h│ Med stay: 28h│ Med stay: 18h│                         │
│  │ Container 45%│ Container 20%│ Container 55%│                         │
│  │ >25k GT: 78% │ >25k GT: 60% │ >25k GT: 82% │                         │
│  │ Score: 82    │ Score: 54    │ Score: 71    │                         │
│  └──────────────┴──────────────┴──────────────┘                         │
│                                                                          │
│  ┌────────────────────┐  ┌────────────────────────────────────────┐     │
│  │  Radar Chart       │  │  Vessel Type Stacked Bar               │     │
│  │  (normalised       │  │  (per port, stacked by type)           │     │
│  │   scores)          │  │                                        │     │
│  └────────────────────┘  └────────────────────────────────────────┘     │
│                                                                          │
│  ┌────────────────────────────────────────────────────────────────┐     │
│  │  Size Distribution Overlay (GT histograms, one per port)      │     │
│  └────────────────────────────────────────────────────────────────┘     │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## 9. Analytics Functions (V2)

### 9.1 Country-Level Aggregations

```python
# From enriched events_df:

country_summary(df)
  → total_visits, unique_vessels, unique_ports, unique_flags
  → median_duration, mean_duration, p90_duration
  → date_range (min/max event dates)

top_ports(df, n=15, metric="visits")
  → port_name, visit_count, unique_vessels, median_duration, vessel_type_breakdown

visits_by_vessel_type(df)
  → type_category → count, percentage

visits_by_vessel_size(df, bins=[0, 5000, 25000, 60000, float("inf")])
  → size_class → count, percentage

visits_by_flag(df, top_n=20)
  → flag → count, percentage

monthly_trend(df)
  → year_month → visit_count, unique_vessels

port_heatmap(df)
  → port × month matrix of visit counts
```

### 9.2 Port-Level Deep Dive

```python
# Filtered to single port:

port_summary(df, port_name)
  → All of country_summary but for one port
  → plus: has_dock, has_anchorage, distance_from_shore

duration_distribution(df, port_name)
  → histogram bins, box-plot stats (q1, median, q3, p90, p99, whiskers)

vessel_type_breakdown(df, port_name)
  → type_category → count, percentage, avg_gt, avg_length

vessel_size_distribution(df, port_name)
  → GT histogram, length histogram

top_vessels(df, port_name, n=50)
  → vessel_name, imo, type, flag, gt, length, visit_count, total_hours, avg_stay

monthly_port_trend(df, port_name)
  → year_month → visit_count by type
```

### 9.3 Deployment Suitability Score (V2)

| Factor | Weight (default) | Scoring | Rationale |
|---|---|---|---|
| **Visit volume** | 0.30 | log(visits) / log(500), capped at 1.0 | Need consistent demand |
| **Large vessel share** | 0.25 | % vessels > 25k GT, scaled 0–1 | Robot designed for large hulls |
| **Target type share** | 0.20 | % container + tanker, scaled 0–1 | Best-fit vessel types |
| **Median dwell time** | 0.15 | min(median_h / 48, 1.0) | Need enough time for cleaning |
| **Current feasibility** | 0.10 | % time currents < 1.5 kn (if data available, else 0.5) | Robot operational limit |

Score = weighted sum × 100 → integer 0–100.

---

## 10. Key Implementation Decisions

### 10.1 GET vs POST for events
**Decision:** Use `GET /v3/events` with `regions[]` parameter.
**Why:** EEZ regions are pre-indexed on GFW's server, producing faster queries than arbitrary polygons via POST. No need to construct/manage geometry. A single country-level query replaces dozens of port-level queries.

### 10.2 Pagination strategy
**Decision:** Paginate with offset/limit (99999 per page).
**Why:** Some countries (e.g., China, Singapore) may have >99,999 port visits in a year. Use a simple loop: `offset += limit` until `len(entries) < limit`.

### 10.3 No VesselFinder scraping in V2
**Decision:** Rely on GFW vessel identity data (via batch endpoint) for type, tonnage, length.
**Why:** VesselFinder scraping was fragile in V1 (HTML changes break it, rate limiting, legal grey area). GFW vessel identity provides the essential fields (IMO, type, GT, length) through a proper API. If more detail is needed later (e.g., TEU, year built), we can add VesselFinder as an optional enrichment step — but not as a primary data source.

### 10.4 Client-side filtering
**Decision:** Fetch all data for a country once, then filter in-memory.
**Why:** This is the core V2 philosophy — "pull everything, then explore". DataFrame operations on 10k–50k rows are instant. Avoids repeated API calls. Allows instant filter changes without loading states.

### 10.5 Caching levels
| Level | What | Storage | TTL |
|---|---|---|---|
| **L1: Session** | Loaded events DataFrame, aggregations | `st.session_state` | Session lifetime |
| **L2: Disk (events)** | Raw events per (iso3, date_range) | `diskcache` | 24 hours |
| **L3: Disk (vessels)** | Vessel identity by vessel_id | `diskcache` | Permanent (vessel details don't change) |
| **L4: Streamlit** | Reference data (anchorage CSV, EEZ mapping) | `@st.cache_data` | App lifetime |

---

## 11. EEZ Country Mapping — Data Structure

```json
{
  "SGP": {
    "name": "Singapore",
    "mrgids": [5668]
  },
  "NLD": {
    "name": "Netherlands",
    "mrgids": [5668]
  },
  "GBR": {
    "name": "United Kingdom",
    "mrgids": [5696]
  },
  "FRA": {
    "name": "France",
    "mrgids": [5677, 48935, 48940, 48946, ...]
  },
  "USA": {
    "name": "United States",
    "mrgids": [8456, 8480, ...]
  }
  // ... ~200 coastal countries
}
```

**How to build this:**
1. Download Marine Regions EEZ dataset metadata (CSV or Shapefile DBF)
2. Extract MRGID ↔ ISO_TER1 (ISO3 territory code) mapping
3. Group by ISO3, collecting all MRGIDs per country
4. Export as JSON
5. Alternatively: query the Marine Regions API or use the `marineregions` Python package

**Alternatively**, if GFW supports ISO3 directly in the regions parameter (e.g. `regions[]=eez:SGP`), the mapping simplifies to just country selection. This should be tested early in Phase 1.

---

## 12. Technology Stack (V2 Changes)

| Layer | V1 | V2 | Why change |
|---|---|---|---|
| **GFW events** | POST /events with geometry | GET /events with regions[] | EEZ pre-indexed, faster, simpler |
| **GFW vessels** | Sequential GET /vessels/{id} | Batch GET /vessels?ids[] | O(1) round-trips vs O(n) |
| **Vessel enrichment** | VesselFinder scraping | GFW vessel identity (batch) | Proper API, reliable, no scraping |
| **Data model** | Load per-port on demand | Load per-country upfront | Enables instant filtering & comparison |
| **UI flow** | Port → Fetch → View | Country → Load all → Explore | Much faster interactive experience |
| **Map** | pydeck (unchanged) | pydeck (unchanged) | Works well for 100s of port markers |
| **Everything else** | Same | Same | Streamlit, pandas, plotly, diskcache all proven in V1 |

---

## 13. Risks & Mitigations (V2)

| Risk | Impact | Mitigation |
|---|---|---|
| **Large countries return too many events** (>100k in a year) | Slow initial load, high memory | Pagination loop handles any size. Consider date range limits (max 6 months). Show progress bar during loading. |
| **GFW GET endpoint doesn't support regions[] for port visits** | Can't use EEZ-based queries | Fall back to POST with EEZ polygon geometry (download from Marine Regions). Test early. |
| **Batch vessel endpoint has a max IDs per request** | Can't send 1000 IDs at once | Chunk into batches of 50. Parallelize with short delays. |
| **GFW rate limits (100 req/min)** | Requests rejected during heavy loading | Exponential backoff with Retry-After header. Batch requests reduce total call count. |
| **Some events lack port_name / anchorage detail** | Incomplete port matching | Use coordinate-based fallback matching to anchorage CSV. Show "unmatched" events separately. |
| **EEZ includes offshore areas with no ports** | Noise from offshore events (e.g., STS transfers) | Filter by confidence level. Match against known port/anchorage locations. |
| **Memory usage with large DataFrames** | Streamlit session grows large | Keep only essential columns. Use category dtypes for strings. Clear old session data on country switch. |

---

## 14. Sequence of Work

| Step | Focus | Deliverable | Validates |
|---|---|---|---|
| 1 | EEZ mapping + GET events client | Can fetch all port visits for a country from CLI/script | API access, pagination, data volume |
| 2 | Event parsing + port matching | Events DataFrame with port labels, tested with 2-3 countries | Data quality, matching accuracy |
| 3 | Batch vessel lookup + cache | Enriched events with IMO, GT, type for all vessels | Batch API, cache performance |
| 4 | Country selector + map + overview dashboard | Minimal working app: select country → see map + KPIs | End-to-end flow, UX |
| 5 | Sidebar filters + port detail panel | Interactive filtering, click-to-detail on ports | Responsiveness, analytics |
| 6 | Comparison view + suitability score | Select ports, compare side-by-side, see scores | Business decision support |
| 7 | Ocean currents integration + export | Full feature parity with V1 plus V2 improvements | Complete product |
| 8 | Polish, testing, documentation | Production-ready app | Quality, maintainability |

---

## 15. Getting Started (V2)

```bash
# 1. Ensure .env has GFW_TOKEN
echo "GFW_TOKEN=your_token_here" >> .env

# 2. Build/verify EEZ mapping (one-time)
python -c "
import json
# Verify the mapping file exists and is valid
with open('Base Data/eez_country_mapping.json') as f:
    mapping = json.load(f)
print(f'{len(mapping)} countries mapped')
"

# 3. Test the GET events endpoint (quick validation)
python -c "
from src.gfw_client_v2 import fetch_country_events
events = fetch_country_events(mrgids=[5668], start_date='2024-06-01', end_date='2024-06-30')
print(f'Singapore June 2024: {len(events)} port visit events')
"

# 4. Run V2 app
streamlit run app_v2.py
```

---

*Document created: 19 March 2026*
*Project: C-LeanWorld V2 — Country-Level Bulk Analysis*
*Builds on: DEVELOPMENT_PLAN.md (V1)*
