"""
engine/utils.py — Shared utility helpers used across backtester,
analyse_options, and analyse_exits.
"""

from __future__ import annotations
import datetime
import pandas as pd


def strip_tz(dt) -> datetime.datetime:
    """Convert any tz-aware or tz-naive timestamp to a tz-naive IST datetime."""
    dt = pd.Timestamp(dt)
    if dt.tzinfo is not None:
        dt = dt.tz_convert("Asia/Kolkata").tz_localize(None)
    return dt.to_pydatetime()


def force_exit_dt(date: datetime.date) -> datetime.datetime:
    """Return the 15:15 IST force-exit datetime for the given date."""
    return datetime.datetime.combine(date, datetime.time(15, 15))
