"""
GFW API client V2 — bulk event fetch by EEZ region + batch vessel lookup.

Key differences from V1:
- Uses GET /v3/events with regions[]=eez:{mrgid} instead of POST with geometry
- Batch vessel lookup via GET /v3/vessels?ids[]=... instead of one-by-one
- Pagination support for large result sets
"""

from __future__ import annotations

import os
import time
from datetime import datetime
from typing import Any, Optional

import httpx

GFW_BASE = "https://gateway.api.globalfishingwatch.org/v3"
PORT_VISIT_DATASET = "public-global-port-visits-events:latest"
VESSEL_IDENTITY_DATASET = "public-global-vessel-identity:latest"
MAX_LIMIT = 99999
VESSEL_BATCH_SIZE = 50


def _get_token() -> str:
    """Resolve GFW bearer token from env or Streamlit secrets."""
    token = os.environ.get("GFW_TOKEN", "")
    if not token:
        try:
            import streamlit as st
            token = st.secrets.get("GFW_TOKEN", "")
        except Exception:
            pass
    if not token:
        raise RuntimeError("GFW_TOKEN not set. Put it in .env or Streamlit secrets.")
    return token


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {_get_token()}",
    }


# ---------------------------------------------------------------------------
# Bulk event fetch by EEZ region
# ---------------------------------------------------------------------------


def fetch_country_events(
    mrgids: list[int],
    start_date: str,
    end_date: str,
    timeout: float = 120.0,
    progress_callback=None,
) -> list[dict[str, Any]]:
    """
    Fetch ALL port-visit events for a country's EEZ region(s).

    Uses POST /v3/events with regions in the JSON body.
    Handles pagination if total > limit.

    Parameters
    ----------
    mrgids : list of Marine Regions MRGID integers
    start_date, end_date : "YYYY-MM-DD"
    timeout : per-request timeout in seconds
    progress_callback : optional callable(message: str)

    Returns
    -------
    list of raw event dicts
    """
    all_events: list[dict] = []

    for mrgid in mrgids:
        offset = 0
        while True:
            body: dict[str, Any] = {
                "datasets": [PORT_VISIT_DATASET],
                "startDate": start_date,
                "endDate": end_date,
                "region": {
                    "id": mrgid,
                    "dataset": "public-eez-areas",
                },
            }
            params = {"offset": offset, "limit": MAX_LIMIT}

            if progress_callback:
                progress_callback(
                    f"Fetching events for EEZ {mrgid} (offset {offset})…"
                )

            resp = _request_with_retry(
                "POST", f"{GFW_BASE}/events", params=params,
                json_body=body, timeout=timeout,
            )
            data = resp.json()

            entries = data.get("entries", [])
            if isinstance(entries, dict):
                entries = entries.get("entries", [])

            if not entries:
                break

            all_events.extend(entries)

            total = data.get("total", len(entries))
            if offset + len(entries) >= total:
                break
            offset += len(entries)

    return all_events


# ---------------------------------------------------------------------------
# Parse events to flat records
# ---------------------------------------------------------------------------


def parse_events_to_df(events: list[dict]) -> list[dict]:
    """
    Flatten raw GFW port-visit event dicts into records for a DataFrame.

    Extracts vessel info, timing, anchorage details, and coordinates.
    """
    records = []
    for ev in events:
        vessel = ev.get("vessel", {}) or {}
        pos = ev.get("position", {}) or {}
        pv = ev.get("port_visit", {}) or {}

        start_anch = pv.get("startAnchorage", {}) or {}
        end_anch = pv.get("endAnchorage", {}) or {}
        int_anch = pv.get("intermediateAnchorage", {}) or {}

        # Duration: prefer API-provided value
        duration_h = pv.get("durationHrs")

        start = ev.get("start")
        end = ev.get("end")
        if duration_h is None and start and end:
            try:
                t0 = datetime.fromisoformat(start.replace("Z", "+00:00"))
                t1 = datetime.fromisoformat(end.replace("Z", "+00:00"))
                duration_h = (t1 - t0).total_seconds() / 3600
            except Exception:
                pass

        records.append({
            "event_id": ev.get("id"),
            "visit_id": pv.get("visitId"),
            "confidence": pv.get("confidence"),
            # Vessel info
            "vessel_id": vessel.get("id"),
            "vessel_name": vessel.get("name"),
            "vessel_mmsi": vessel.get("ssvid"),
            "vessel_flag": vessel.get("flag"),
            "vessel_type": vessel.get("type"),
            # Timing
            "start": start,
            "end": end,
            "duration_hours": duration_h,
            # Anchorage info (start)
            "port_name": start_anch.get("name") or start_anch.get("topDestination"),
            "port_id": start_anch.get("id"),
            "port_flag": start_anch.get("flag"),
            "at_dock": start_anch.get("atDock"),
            "anchorage_id": start_anch.get("anchorageId"),
            # Anchorage info (end)
            "end_port_name": end_anch.get("name") or end_anch.get("topDestination"),
            "end_port_id": end_anch.get("id"),
            # Position
            "lat": pos.get("lat"),
            "lon": pos.get("lon"),
        })
    return records


# ---------------------------------------------------------------------------
# Batch vessel lookup
# ---------------------------------------------------------------------------


def fetch_vessels_batch(
    vessel_ids: list[str],
    batch_size: int = VESSEL_BATCH_SIZE,
    timeout: float = 60.0,
    progress_callback=None,
) -> list[dict]:
    """
    Fetch vessel identity for multiple IDs using GET /v3/vessels.

    Chunks into batches of `batch_size` to avoid URL length limits.

    Returns list of raw vessel dicts from the API.
    """
    all_vessels: list[dict] = []
    total_batches = (len(vessel_ids) + batch_size - 1) // batch_size

    for batch_idx in range(total_batches):
        chunk = vessel_ids[batch_idx * batch_size : (batch_idx + 1) * batch_size]

        if progress_callback:
            progress_callback(
                f"Fetching vessel batch {batch_idx + 1}/{total_batches} "
                f"({len(chunk)} vessels)…"
            )

        # Build params with indexed bracket notation: datasets[0]=..., ids[0]=..., ids[1]=...
        params: dict[str, str] = {
            "datasets[0]": VESSEL_IDENTITY_DATASET,
        }
        for i, vid in enumerate(chunk):
            params[f"ids[{i}]"] = vid

        resp = _request_with_retry(
            "GET", f"{GFW_BASE}/vessels", params=params, timeout=timeout
        )
        data = resp.json()

        entries = data.get("entries", data) if isinstance(data, dict) else data
        if isinstance(entries, dict):
            entries = entries.get("entries", [])
        if entries:
            all_vessels.extend(entries)

    return all_vessels


def parse_vessel_identities(raw_vessels: list[dict]) -> list[dict]:
    """
    Parse raw vessel identity responses into flat records.

    Extracts: vessel_id, imo, shipname, callsign, vessel_type, flag,
              tonnage_gt, length_m, built_year.
    """
    records = []
    for entry in raw_vessels:
        vessel_id = entry.get("id") or entry.get("selfReportedInfo", [{}])[0].get("id", "")

        # Extract from registryInfo — take the latest record
        registry = entry.get("registryInfo", [])
        best = {}
        for reg in registry:
            if reg.get("latestVesselInfo"):
                best = reg
                break
        if not best and registry:
            best = registry[-1]

        # Also check selfReportedInfo for vessel type if missing
        self_info = entry.get("selfReportedInfo", [{}])
        self_best = self_info[0] if self_info else {}

        records.append({
            "vessel_id": vessel_id,
            "imo": best.get("imo") or self_best.get("imo"),
            "shipname": best.get("shipname") or self_best.get("shipname"),
            "callsign": best.get("callsign") or self_best.get("callsign"),
            "vessel_type_registry": best.get("shiptype") or self_best.get("shiptype"),
            "flag_registry": best.get("flag") or self_best.get("flag"),
            "tonnage_gt": best.get("tonnageGt") or self_best.get("tonnageGt"),
            "length_m": best.get("lengthM") or self_best.get("lengthM"),
            "built_year": best.get("builtYear"),
        })
    return records


# ---------------------------------------------------------------------------
# HTTP helper with retry on 429
# ---------------------------------------------------------------------------


def _request_with_retry(
    method: str,
    url: str,
    params=None,
    json_body=None,
    timeout: float = 60.0,
    max_retries: int = 3,
) -> httpx.Response:
    """Make an HTTP request with automatic retry on 429 rate-limit."""
    for attempt in range(max_retries):
        if method.upper() == "GET":
            resp = httpx.get(url, headers=_headers(), params=params, timeout=timeout)
        else:
            resp = httpx.post(
                url, headers=_headers(), params=params, json=json_body, timeout=timeout
            )

        if resp.status_code == 429:
            retry_after = int(resp.headers.get("Retry-After", "10"))
            time.sleep(retry_after)
            continue

        if resp.status_code >= 400:
            # Log the response body for debugging API errors
            try:
                detail = resp.json()
            except Exception:
                detail = resp.text[:500]
            raise httpx.HTTPStatusError(
                f"{resp.status_code} for {resp.url} — {detail}",
                request=resp.request,
                response=resp,
            )
        return resp

    # Final attempt — let it raise
    resp.raise_for_status()
    return resp
