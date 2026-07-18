"""
debug_backtest.py — Run this directly to see exactly what the backtester sees.
    python debug_backtest.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
os.makedirs("logs", exist_ok=True)

import pandas as pd
import numpy as np

print("=" * 60)
print("Logging into Angel One...")
from feed.angel_auth import get_session
from feed.data_feed import fetch_candles
import config

api, _, _ = get_session()
print("Login OK")

DAYS = 30   # change to 7, 14, 30, 60, 90 as needed

for sym in ["NIFTY", "BANKNIFTY"]:
    cfg = config.INSTRUMENTS[sym]
    print("\n" + "=" * 60)
    print(f"Symbol: {sym}  |  days_back={DAYS}")

    df = fetch_candles(api, cfg["token"], cfg["exchange"], interval="FIVE_MINUTE", days_back=DAYS)

    if df.empty:
        print("ERROR: No data returned!")
        continue

    print(f"Total candles  : {len(df)}")
    print(f"Date range     : {df['datetime'].min()}  to  {df['datetime'].max()}")

    # ── Full add_indicators ──────────────────────────────────────────────────
    from engine.indicators import add_indicators
    df_ind = add_indicators(df.copy())
    print(f"  vwap NaN    : {df_ind['vwap'].isna().sum()} / {len(df_ind)}")
    print(f"  ST GREEN    : {(df_ind['st_signal']==1).sum()} / {len(df_ind)}")
    print(f"  RSI range   : {df_ind['rsi'].min():.1f} – {df_ind['rsi'].max():.1f}")
    print(f"  ADX range   : {df_ind['adx'].min():.1f} – {df_ind['adx'].max():.1f}")
    print(f"  ADX>=20     : {(df_ind['adx']>=20).sum()} / {len(df_ind)}")
    print(f"  st_value NaN: {df_ind['st_value'].isna().sum()} / {len(df_ind)}")

    # ── Run backtest ─────────────────────────────────────────────────────────
    from engine.backtester import run_backtest, summary_stats
    trades, diag = run_backtest(df, sym)
    stats = summary_stats(trades)

    if trades.empty:
        print("  NO TRADES generated — check filters")
        if not diag.empty:
            print("  Signal counts:", diag["signal"].value_counts().to_dict())
        continue

    print(f"\n{'='*50}")
    print(f"  Trades   : {stats['total_trades']}")
    print(f"  Wins     : {stats['wins']}")
    print(f"  Losses   : {stats['losses']}")
    print(f"  Win Rate : {stats['win_rate_pct']}%")
    print(f"  Total pts: {stats['total_points']}")
    print(f"  Avg WIN  : +{stats['avg_win_pts']} pts")
    print(f"  Avg LOSS : {stats['avg_loss_pts']} pts")
    print(f"  R:R      : {stats['risk_reward']}")
    print(f"  Max consec loss: {stats['max_consec_loss']}")

    print(f"\n  --- Trade list ---")
    pd.set_option("display.width", 120)
    pd.set_option("display.max_rows", 200)
    print(trades[["date","direction","entry_price","sl","exit_price",
                  "exit_reason","points","result"]].to_string(index=False))

    # ── Exit reason breakdown ────────────────────────────────────────────────
    print(f"\n  --- Exit reasons ---")
    print(trades["exit_reason"].value_counts().to_string())

    # ── Win rate by direction ────────────────────────────────────────────────
    for d in ["BUY", "SELL"]:
        sub = trades[trades["direction"] == d]
        if len(sub):
            wr = round((sub["result"]=="WIN").sum() / len(sub) * 100, 1)
            print(f"  {d}: {len(sub)} trades, WR={wr}%, pts={round(sub['points'].sum(),1)}")

print("\nDEBUG DONE")
