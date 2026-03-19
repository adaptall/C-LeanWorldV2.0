"""
Persistent vessel cache backed by diskcache (V2 — enhanced).

Stores:
  1. Vessel identity data keyed by vessel_id (from GFW batch lookup)
  2. Country-level event cache keyed by (iso3, date_range)

Cache location: data/vessel_cache (auto-created, git-ignored).
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Optional

import diskcache

_CACHE_DIR = Path(__file__).parent.parent / "data" / "vessel_cache"
_cache: Optional[diskcache.Cache] = None

# TTLs
EVENT_CACHE_TTL = 24 * 3600  # 24 hours for country events
VESSEL_IDENTITY_TTL = None   # Permanent for vessel identity


def _get_cache() -> diskcache.Cache:
    global _cache
    if _cache is None:
        os.makedirs(_CACHE_DIR, exist_ok=True)
        _cache = diskcache.Cache(str(_CACHE_DIR), size_limit=500 * 1024 * 1024)
    return _cache


# ---------------------------------------------------------------------------
# Vessel identity (by vessel_id)
# ---------------------------------------------------------------------------

def get_vessel_by_id(vessel_id: str) -> Optional[dict]:
    """Return cached vessel identity for a GFW vessel_id, or None."""
    cache = _get_cache()
    return cache.get(f"vid:{vessel_id}")


def set_vessel_by_id(vessel_id: str, data: dict) -> None:
    """Store vessel identity keyed by vessel_id."""
    cache = _get_cache()
    cache.set(f"vid:{vessel_id}", data)


def get_many_by_id(vessel_ids: list[str]) -> tuple[dict[str, dict], list[str]]:
    """
    Look up many vessel_ids at once.

    Returns (found, missing):
        found:   {vessel_id: data_dict}
        missing: list of vessel_ids not in cache
    """
    cache = _get_cache()
    found: dict[str, dict] = {}
    missing: list[str] = []
    for vid in vessel_ids:
        val = cache.get(f"vid:{vid}")
        if val is not None:
            found[vid] = val
        else:
            missing.append(vid)
    return found, missing


def set_many_by_id(vessels: dict[str, dict]) -> None:
    """Batch store vessel identities."""
    cache = _get_cache()
    for vid, data in vessels.items():
        cache.set(f"vid:{vid}", data)


# ---------------------------------------------------------------------------
# Vessel identity (by IMO)
# ---------------------------------------------------------------------------

def get_vessel_by_imo(imo: str) -> Optional[dict]:
    cache = _get_cache()
    return cache.get(f"imo:{imo}")


def set_vessel_by_imo(imo: str, data: dict) -> None:
    cache = _get_cache()
    cache.set(f"imo:{imo}", data)


# ---------------------------------------------------------------------------
# Country event cache
# ---------------------------------------------------------------------------

def _event_cache_key(iso3: str, start_date: str, end_date: str) -> str:
    """Generate a deterministic cache key for a country + date range."""
    raw = f"{iso3}:{start_date}:{end_date}"
    return f"events:{hashlib.sha256(raw.encode()).hexdigest()[:16]}"


def get_cached_events(iso3: str, start_date: str, end_date: str) -> Optional[list[dict]]:
    """Return cached events for a country+date range if available and fresh."""
    cache = _get_cache()
    key = _event_cache_key(iso3, start_date, end_date)
    entry = cache.get(key)
    if entry is None:
        return None
    # Check TTL
    if time.time() - entry.get("ts", 0) > EVENT_CACHE_TTL:
        return None
    return entry.get("events")


def set_cached_events(
    iso3: str, start_date: str, end_date: str, events: list[dict]
) -> None:
    """Cache events for a country+date range."""
    cache = _get_cache()
    key = _event_cache_key(iso3, start_date, end_date)
    cache.set(key, {"ts": time.time(), "events": events})


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

def cache_stats() -> dict:
    """Return basic cache statistics."""
    cache = _get_cache()
    return {
        "total_entries": len(cache),
        "size_mb": cache.volume() / (1024 * 1024),
    }
