"""
feed/data_feed.py — Fetches historical + live 5-minute OHLCV candles
from Angel One SmartAPI for Nifty and Bank Nifty.

Angel One API limit: 5-min candles available for max ~30 calendar days per call.
For longer periods (up to 90 days) we split into 30-day chunks and stitch.

Key fix: fromdate/todate must carry proper HH:MM times.
  fromdate = <date> 09:15 (market open)
  todate   = <date> 15:30, capped at now() for today to avoid future timestamps

Initial fetch uses days_back=5 (not 1) so indicators have ≥26 warm-up candles.
"""

from __future__ import annotations
import datetime
import time
import pandas as pd
from logzero import logger

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import config

# Angel One hard limit per single getCandleData call for 5-min interval
_MAX_DAYS_PER_CALL = 30


# ─── single chunk fetch ───────────────────────────────────────────────────────

_RATE_LIMIT_MSG = "exceeding access rate"   # substring Angel One returns on quota hit

def _fetch_chunk(api, token: str, exchange: str, interval: str,
                 from_date: datetime.date, to_date: datetime.date,
                 _retries: int = 3) -> pd.DataFrame:
    """Fetch one chunk of candles between from_date and to_date.

    Angel One requires HH:MM in fromdate/todate.  We always open at 09:15 and
    close at 15:30 IST.  For 'today' we cap todate at the current time so the
    API does not reject a future timestamp.

    Retries up to _retries times with exponential back-off on rate-limit errors.
    """
    today_date = datetime.date.today()
    from_ts = datetime.datetime.combine(from_date, datetime.time(9, 15))
    if to_date >= today_date:
        now = datetime.datetime.now()
        to_ts = min(datetime.datetime.combine(to_date, datetime.time(15, 30)), now)
    else:
        to_ts = datetime.datetime.combine(to_date, datetime.time(15, 30))

    params = {
        "exchange"    : exchange,
        "symboltoken" : token,
        "interval"    : interval,
        "fromdate"    : from_ts.strftime("%Y-%m-%d %H:%M"),
        "todate"      : to_ts.strftime("%Y-%m-%d %H:%M"),
    }

    for attempt in range(1, _retries + 1):
        try:
            resp = api.getCandleData(params)
            break   # success — exit retry loop
        except Exception as exc:
            err_str = str(exc)
            if _RATE_LIMIT_MSG in err_str and attempt < _retries:
                wait = 10 * attempt   # 10s, 20s, 30s
                logger.warning("Rate limit hit (attempt %d/%d) — waiting %ds …",
                               attempt, _retries, wait)
                time.sleep(wait)
            else:
                logger.error("getCandleData error (%s – %s): %s", from_date, to_date, exc)
                return pd.DataFrame()

    if not resp or resp.get("status") is False:
        logger.error("getCandleData failed (%s – %s): %s", from_date, to_date, resp)
        return pd.DataFrame()

    raw = resp.get("data") or []
    if not raw:
        return pd.DataFrame()

    df = pd.DataFrame(raw, columns=["datetime", "open", "high", "low", "close", "volume"])

    # Normalise to tz-naive IST — required by the entire indicator/signal pipeline
    dt = pd.to_datetime(df["datetime"], utc=True)
    dt = dt.dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
    df["datetime"] = dt

    df[["open", "high", "low", "close", "volume"]] = df[
        ["open", "high", "low", "close", "volume"]
    ].apply(pd.to_numeric)

    logger.debug("Chunk %s – %s: %d candles", from_date, to_date, len(df))
    return df


# ─── main fetch (chunked for > 30 days) ──────────────────────────────────────

def fetch_candles(api, token: str, exchange: str, interval: str = "FIVE_MINUTE",
                  days_back: int = 1) -> pd.DataFrame:
    """
    Fetch historical OHLCV candles from Angel One SmartAPI.

    Transparently handles requests > 30 days by splitting into 30-day chunks
    with a small delay between calls to respect API rate limits.

    Parameters
    ----------
    api       : logged-in SmartConnect instance
    token     : instrument token string
    exchange  : "NSE" / "BSE" / "NFO"
    interval  : Angel One interval string (default: "FIVE_MINUTE")
    days_back : calendar days of history to fetch (up to 90)

    Returns
    -------
    DataFrame with columns: datetime, open, high, low, close, volume
    Sorted ascending by datetime, deduplicated.
    """
    now      = datetime.datetime.now()
    end_dt   = now.date()
    start_dt = end_dt - datetime.timedelta(days=days_back)

    chunks = []
    chunk_end = end_dt

    # Walk backwards in 30-day windows until we cover the full range
    while chunk_end > start_dt:
        chunk_start = max(chunk_end - datetime.timedelta(days=_MAX_DAYS_PER_CALL), start_dt)
        df_chunk = _fetch_chunk(api, token, exchange, interval, chunk_start, chunk_end)
        if not df_chunk.empty:
            chunks.append(df_chunk)
        chunk_end = chunk_start - datetime.timedelta(days=1)

        # Small delay between API calls to avoid rate limiting
        if chunk_end > start_dt:
            time.sleep(1.5)

    if not chunks:
        return pd.DataFrame()

    # Combine all chunks, deduplicate, sort
    df = pd.concat(chunks, ignore_index=True)
    df = df.drop_duplicates(subset="datetime").sort_values("datetime").reset_index(drop=True)

    logger.info("fetch_candles: %d total candles over %d days for token %s",
                len(df), days_back, token)
    return df


# ─── India VIX daily fetch ───────────────────────────────────────────────────

def fetch_vix_daily(api, days_back: int = 400) -> dict:
    """
    Fetch India VIX daily open values for the last `days_back` calendar days.
    Returns a dict mapping date string "YYYY-MM-DD" → float (VIX open value).

    Uses config.VIX_TOKEN and config.VIX_EXCHANGE.
    Returns an empty dict if VIX_FILTER is disabled or fetch fails.
    """
    import config as _cfg
    if not getattr(_cfg, "VIX_FILTER", False):
        return {}

    token    = getattr(_cfg, "VIX_TOKEN",    "99919000")
    exchange = getattr(_cfg, "VIX_EXCHANGE", "NSE")

    now      = datetime.datetime.now()
    end_dt   = now.date()
    start_dt = end_dt - datetime.timedelta(days=days_back)

    # ONE_DAY interval — single call covers the full range (no 30-day chunking needed)
    params = {
        "exchange"    : exchange,
        "symboltoken" : token,
        "interval"    : "ONE_DAY",
        "fromdate"    : f"{start_dt} 09:15",
        "todate"      : f"{end_dt} 15:30",
    }
    try:
        resp = api.getCandleData(params)
    except Exception as exc:
        logger.error("VIX fetch error: %s", exc)
        return {}

    if not resp or resp.get("status") is False:
        logger.warning("VIX fetch failed: %s", resp)
        return {}

    raw = resp.get("data") or []
    result = {}
    for candle in raw:
        try:
            dt_str = str(pd.to_datetime(candle[0], utc=True)
                         .tz_convert("Asia/Kolkata")
                         .date())
            vix_open = float(candle[1])   # index 1 = open
            result[dt_str] = vix_open
        except Exception:
            continue

    logger.info("VIX daily: %d days fetched", len(result))
    return result


# ─── Latest candle refresh ────────────────────────────────────────────────────

def refresh_candles(api, existing: pd.DataFrame, token: str, exchange: str) -> pd.DataFrame:
    """
    Append any new candles since the last row in `existing`.
    Returns a combined, deduplicated DataFrame.
    """
    new_df = fetch_candles(api, token, exchange, days_back=1)
    if new_df.empty:
        return existing

    combined = pd.concat([existing, new_df], ignore_index=True)
    combined = combined.drop_duplicates(subset="datetime").sort_values("datetime").reset_index(drop=True)
    return combined
