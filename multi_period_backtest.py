"""
multi_period_backtest.py — Runs backtest across all timeframes: 10, 20, 30, 60, 90 days.
Prints a clean summary table for NIFTY and BANKNIFTY.

Usage:
    python multi_period_backtest.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
os.makedirs("logs", exist_ok=True)

import pandas as pd

from feed.angel_auth import get_session
from feed.data_feed import fetch_candles
from engine.backtester import run_backtest, summary_stats
import config

PERIODS = [10, 20, 30, 60, 90]   # days to test

print("=" * 60)
print("Logging into Angel One...")
api, _, _ = get_session()
print("Login OK\n")

# Pre-fetch maximum data once per symbol, then slice for shorter periods
all_results = {}   # { sym: { days: stats_dict } }

for sym in ["NIFTY", "BANKNIFTY"]:
    cfg = config.INSTRUMENTS[sym]
    print(f"Fetching 90 days of data for {sym}...")
    df_full = fetch_candles(api, cfg["token"], cfg["exchange"],
                            interval="FIVE_MINUTE", days_back=93)
    if df_full.empty:
        print(f"  ERROR: No data for {sym}")
        continue

    df_full["datetime"] = pd.to_datetime(df_full["datetime"])
    df_full = df_full.sort_values("datetime").reset_index(drop=True)

    max_date = df_full["datetime"].max()
    print(f"  Total candles: {len(df_full)}  |  range: {df_full['datetime'].min().date()} to {max_date.date()}")

    all_results[sym] = {}

    for days in PERIODS:
        cutoff = max_date - pd.Timedelta(days=days)
        df_slice = df_full[df_full["datetime"] >= cutoff].reset_index(drop=True)

        trades, _ = run_backtest(df_slice, sym)
        stats = summary_stats(trades) if not trades.empty else {}

        if not stats:
            all_results[sym][days] = {
                "days": days, "trades": 0, "wins": 0, "losses": 0,
                "win_rate_pct": 0.0, "total_points": 0.0,
                "avg_win_pts": 0.0, "avg_loss_pts": 0.0, "risk_reward": 0.0,
                "max_consec_loss": 0,
            }
        else:
            stats["days"] = days
            all_results[sym][days] = stats

        s = all_results[sym][days]
        print(f"  [{days:2d}d] trades={s['total_trades']:3d}  WR={s['win_rate_pct']:5.1f}%  "
              f"pts={s['total_points']:8.1f}  avgW=+{s['avg_win_pts']:.1f}  "
              f"avgL={s['avg_loss_pts']:.1f}  RR={s['risk_reward']:.2f}  "
              f"maxConsecL={s['max_consec_loss']}")

# ── Print final comparison tables ────────────────────────────────────────────
SEP = "-" * 90

print("\n\n" + "=" * 90)
print("  NIFTY — Results across all timeframes")
print("=" * 90)
print(f"  {'Period':>8}  {'Trades':>7}  {'Wins':>5}  {'Loss':>5}  {'WR%':>6}  "
      f"{'Tot Pts':>9}  {'Avg WIN':>8}  {'Avg LOSS':>9}  {'R:R':>5}  {'MaxCL':>6}")
print(SEP)
for days in PERIODS:
    s = all_results.get("NIFTY", {}).get(days, {})
    if not s:
        print(f"  {str(days)+'d':>8}  -- no data --")
        continue
    wr_flag = "**" if s['win_rate_pct'] >= 70 else ("* " if s['win_rate_pct'] >= 60 else "  ")
    print(f"  {str(days)+'d':>8}  {s['total_trades']:>7}  {s['wins']:>5}  {s['losses']:>5}  "
          f"{s['win_rate_pct']:>5.1f}%{wr_flag}  {s['total_points']:>9.1f}  "
          f"+{s['avg_win_pts']:>7.1f}  {s['avg_loss_pts']:>9.1f}  "
          f"{s['risk_reward']:>5.2f}  {s['max_consec_loss']:>6}")
print(SEP)
print("  ** = WR >= 70%    * = WR >= 60%\n")

print("=" * 90)
print("  BANKNIFTY — Results across all timeframes")
print("=" * 90)
print(f"  {'Period':>8}  {'Trades':>7}  {'Wins':>5}  {'Loss':>5}  {'WR%':>6}  "
      f"{'Tot Pts':>9}  {'Avg WIN':>8}  {'Avg LOSS':>9}  {'R:R':>5}  {'MaxCL':>6}")
print(SEP)
for days in PERIODS:
    s = all_results.get("BANKNIFTY", {}).get(days, {})
    if not s:
        print(f"  {str(days)+'d':>8}  -- no data --")
        continue
    wr_flag = "**" if s['win_rate_pct'] >= 70 else ("* " if s['win_rate_pct'] >= 60 else "  ")
    print(f"  {str(days)+'d':>8}  {s['total_trades']:>7}  {s['wins']:>5}  {s['losses']:>5}  "
          f"{s['win_rate_pct']:>5.1f}%{wr_flag}  {s['total_points']:>9.1f}  "
          f"+{s['avg_win_pts']:>7.1f}  {s['avg_loss_pts']:>9.1f}  "
          f"{s['risk_reward']:>5.2f}  {s['max_consec_loss']:>6}")
print(SEP)
print("  ** = WR >= 70%    * = WR >= 60%\n")

# ── Save CSV ──────────────────────────────────────────────────────────────────
rows = []
for sym in all_results:
    for days, s in all_results[sym].items():
        rows.append({"symbol": sym, **s})
out_df = pd.DataFrame(rows)
out_path = "logs/multi_period_results.csv"
out_df.to_csv(out_path, index=False)
print(f"Results saved to {out_path}")
print("\nDONE")
