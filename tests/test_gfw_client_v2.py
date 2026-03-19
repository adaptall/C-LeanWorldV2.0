"""Tests for the GFW V2 client — event parsing and vessel identity parsing."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.gfw_client_v2 import parse_events_to_df, parse_vessel_identities


def test_parse_events_to_df(sample_raw_events):
    """parse_events_to_df should produce flat records from raw events."""
    records = parse_events_to_df(sample_raw_events)

    assert len(records) == 1
    r = records[0]
    assert r["event_id"] == "evt1"
    assert r["vessel_id"] == "vid1"
    assert r["vessel_name"] == "MAERSK ALPHA"
    assert r["vessel_flag"] == "DNK"
    assert r["vessel_type"] == "CONTAINER"
    assert r["duration_hours"] == 36.0
    assert r["port_name"] == "SINGAPORE"
    assert r["lat"] == 1.25
    assert r["lon"] == 103.8
    assert r["at_dock"] is False


def test_parse_events_missing_duration():
    """Should compute duration from start/end when durationHrs is missing."""
    events = [
        {
            "id": "e1",
            "vessel": {"id": "v1"},
            "position": {},
            "start": "2024-01-01T00:00:00Z",
            "end": "2024-01-01T12:00:00Z",
            "port_visit": {
                "startAnchorage": {"name": "TEST"},
                "endAnchorage": {},
                "intermediateAnchorage": {},
            },
        }
    ]
    records = parse_events_to_df(events)
    assert abs(records[0]["duration_hours"] - 12.0) < 0.01


def test_parse_vessel_identities():
    """parse_vessel_identities should extract key fields from registry."""
    raw = [
        {
            "id": "vid1",
            "registryInfo": [
                {
                    "latestVesselInfo": True,
                    "imo": "9123456",
                    "shipname": "MAERSK ALPHA",
                    "callsign": "ABCD",
                    "shiptype": "CONTAINER",
                    "flag": "DNK",
                    "tonnageGt": 120000,
                    "lengthM": 350,
                    "builtYear": 2015,
                }
            ],
            "selfReportedInfo": [],
        }
    ]
    records = parse_vessel_identities(raw)
    assert len(records) == 1
    r = records[0]
    assert r["vessel_id"] == "vid1"
    assert r["imo"] == "9123456"
    assert r["shipname"] == "MAERSK ALPHA"
    assert r["tonnage_gt"] == 120000
    assert r["length_m"] == 350
    assert r["built_year"] == 2015
