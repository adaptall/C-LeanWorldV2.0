"""Tests for analytics V2 — classification, KPIs, scoring."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from src.analytics_v2 import (
    classify_vessel_type,
    classify_vessel_size,
    add_classifications,
    country_summary,
    visit_summary,
    visits_by_vessel_type,
    visits_by_flag,
    monthly_visit_counts,
    top_ports,
    deployment_score,
    compute_port_scores,
)


class TestVesselClassification:
    def test_container_types(self):
        assert classify_vessel_type("CONTAINER") == "Container"
        assert classify_vessel_type("CONTAINER_REEFER") == "Container"

    def test_tanker_types(self):
        assert classify_vessel_type("TANKER") == "Tanker"
        assert classify_vessel_type("LNG_TANKER") == "Tanker"

    def test_bulk_types(self):
        assert classify_vessel_type("BULK_CARRIER") == "Bulk Carrier"

    def test_other_types(self):
        assert classify_vessel_type("FISHING") == "Other"
        assert classify_vessel_type("TUG") == "Other"
        assert classify_vessel_type(None) == "Other"
        assert classify_vessel_type("") == "Other"

    def test_size_classification(self):
        assert classify_vessel_size(3000) == "Small"
        assert classify_vessel_size(15000) == "Medium"
        assert classify_vessel_size(40000) == "Large"
        assert classify_vessel_size(80000) == "Very Large"
        assert classify_vessel_size(None) == "Unknown"


class TestCountrySummary:
    def test_basic_summary(self, sample_events_df):
        summary = country_summary(sample_events_df)
        assert summary["total_visits"] == 4
        assert summary["unique_vessels"] == 4
        assert summary["unique_ports"] == 3  # SINGAPORE, WEST JURONG, CHANGI
        assert "median_duration_h" in summary

    def test_empty_df(self):
        summary = country_summary(pd.DataFrame())
        assert summary["total_visits"] == 0


class TestVisitAnalytics:
    def test_visits_by_type(self, sample_events_df):
        vt = visits_by_vessel_type(sample_events_df)
        assert not vt.empty
        assert "type" in vt.columns
        assert "count" in vt.columns

    def test_visits_by_flag(self, sample_events_df):
        vf = visits_by_flag(sample_events_df)
        assert not vf.empty

    def test_monthly_counts(self, sample_events_df):
        mc = monthly_visit_counts(sample_events_df)
        assert not mc.empty
        assert "month" in mc.columns

    def test_top_ports(self, sample_events_df):
        tp = top_ports(sample_events_df, n=5)
        assert not tp.empty
        # SINGAPORE has 2 visits, should be first
        assert tp.iloc[0]["port"] == "SINGAPORE"
        assert tp.iloc[0]["count"] == 2


class TestScoring:
    def test_deployment_score_basic(self):
        score = deployment_score(
            visit_count=200,
            median_duration_h=24.0,
            large_vessel_pct=80.0,
            container_tanker_pct=60.0,
        )
        assert 0 <= score <= 100
        assert score > 50  # should be a reasonable score

    def test_deployment_score_zero(self):
        score = deployment_score(0, 0, 0, 0)
        assert score == 0.0

    def test_compute_port_scores(self, sample_events_df):
        scores = compute_port_scores(sample_events_df)
        assert not scores.empty
        assert "port" in scores.columns
        assert "score" in scores.columns
        assert all(0 <= s <= 100 for s in scores["score"])
