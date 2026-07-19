"""
run_signals.py — Headless CLI runner (no UI).
Polls Angel One every 5 minutes and prints signals to the console + CSV.
Use this if you don't want the Streamlit UI.

Session window and force-exit time are read from config.py (SESSION_START,
SESSION_END, FORCE_EXIT) — no hard-coded times in this file.

Initial candle fetch uses days_back=5 to ensure indicators have enough
warm-up data (≥ 26 candles required for EMA21 / RSI / Supertrend).

Run with:
    python run_signals.py
"""

from __future__ import annotations
import time
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

import config
from feed.angel_auth import get_session
from feed.data_feed import fetch_candles, refresh_candles
from engine.signal_engine import evaluate_signal
from alerts.notifier import send_signal_alert
import logzero
from logzero import logger

logzero.logfile(os.path.join(config.LOG_DIR, "runner.log"), maxBytes=2_000_000, backupCount=3)
os.makedirs(config.LOG_DIR, exist_ok=True)


def _in_session() -> bool:
    now = datetime.datetime.now().time()
    h, m = map(int, config.SESSION_START.split(":"))
    start = datetime.time(h, m)
    h, m  = map(int, config.SESSION_END.split(":"))
    end   = datetime.time(h, m)
    return start <= now <= end


def main():
    print("=" * 60)
    print("  Nifty / Bank Nifty Signal Runner")
    print("  Strategy: Supertrend + EMA + RSI + VWAP")
    print(f"  Session: {config.SESSION_START} – {config.FORCE_EXIT} IST")
    print("=" * 60)

    api, _, _ = get_session()
    candles      = {sym: None for sym in config.INSTRUMENTS}
    last_signal  = {sym: None for sym in config.INSTRUMENTS}
    trade_counts = {sym: 0    for sym in config.INSTRUMENTS}

    while True:
        now = datetime.datetime.now()

        # Force exit check
        h, m = map(int, config.FORCE_EXIT.split(":"))
        force_exit_time = datetime.time(h, m)
        if now.time() >= force_exit_time:
            logger.info("Past %s — stopping runner for today.", config.FORCE_EXIT)
            print(f"Past {config.FORCE_EXIT}. Shutting down for today.")
            break

        if not _in_session():
            wait_secs = 60
            print(f"[{now.strftime('%H:%M:%S')}] Outside session. Sleeping {wait_secs}s...")
            time.sleep(wait_secs)
            continue

        today_weekday = datetime.datetime.now().weekday()

        for sym, cfg in config.INSTRUMENTS.items():
            try:
                # ── Expiry-day skip ───────────────────────────────────────────
                skip_weekday = getattr(config, "SKIP_EXPIRY_DAY", {}).get(sym)
                if skip_weekday is not None and today_weekday == skip_weekday:
                    print(f"[{now.strftime('%H:%M:%S')}] {sym:12s} | Expiry day — skipping all entries today.")
                    continue

                if candles[sym] is None or candles[sym].empty:
                    # Fetch 5 days so indicators (need ≥26 candles) warm up on first load
                    df = fetch_candles(api, cfg["token"], cfg["exchange"], days_back=5)
                else:
                    df = refresh_candles(api, candles[sym], cfg["token"], cfg["exchange"])

                candles[sym] = df

                if df.empty:
                    logger.warning("%s: empty candle data", sym)
                    continue

                sig = evaluate_signal(df, symbol=sym)
                direction = sig["signal"]

                ct = sig.get("candle_time")
                print(
                    f"[{now.strftime('%H:%M:%S')}] {sym:12s} | "
                    f"Signal: {direction:4s} | "
                    f"Price: {sig.get('entry','—')} | "
                    f"RSI: {sig.get('rsi','—')} | "
                    f"ADX: {sig.get('adx','—')} | "
                    f"VWAP: {sig.get('vwap','—')} | "
                    f"{sig.get('reason','')}"
                )

                if direction != "NONE" and ct != last_signal[sym]:
                    if trade_counts[sym] < config.MAX_TRADES_PER_SYMBOL:
                        send_signal_alert(sym, sig)
                        last_signal[sym]   = ct
                        trade_counts[sym] += 1
                    else:
                        print(f"  ⚠️  Max trades ({config.MAX_TRADES_PER_SYMBOL}) reached for {sym} today.")

            except Exception as exc:
                logger.error("%s error: %s", sym, exc)

        # Sleep until the next 5-min candle closes
        sleep_secs = config.TIMEFRAME_MINUTES * 60
        print(f"  → Next check in {sleep_secs}s ...\n")
        time.sleep(sleep_secs)


if __name__ == "__main__":
    main()
