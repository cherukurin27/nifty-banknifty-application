"""
tests/analyse_post_sl_reentry.py

Analyses: after a SELL SL-hit, does a valid BUY signal fire within the
next N candles (25 min / 30 min / 60 min) — and what would the outcome be?

This answers: "should we re-enter on the reverse after an SL hit?"

Usage:
    python tests/analyse_post_sl_reentry.py
"""

from __future__ import annotations
import sys, os, datetime
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pandas as pd
import numpy as np

import config
from feed.angel_auth import get_session
from feed.data_feed import fetch_candles
from engine.backtester import run_backtest
from engine.indicators import add_indicators
from engine.signal_engine import eval_entry_signal, SIGNAL_BUY, SIGNAL_SELL

DAYS_BACK   = 186   # same as main backtest — absorbs warmup
LOOK_AHEAD  = [3, 6, 12]   # candles after SL hit to look for reverse (15min / 30min / 60min)
SYMBOLS     = list(config.INSTRUMENTS.keys())


def analyse(symbol: str, df_raw: pd.DataFrame):
    print(f"\n{'='*60}")
    print(f"  {symbol} -- Post-SL-Hit Re-entry Analysis")
    print(f"{'='*60}")

    # ── Run standard backtest to get all trades ──────────────────────────────
    trades_df, _ = run_backtest(df_raw, symbol)
    if trades_df.empty:
        print("  No trades found.")
        return

    # ── Filter to SELL SL-hits only ──────────────────────────────────────────
    sell_sl = trades_df[
        (trades_df["direction"] == SIGNAL_SELL) &
        (trades_df["exit_reason"] == "SL Hit")
    ].copy()

    print(f"\n  Total SELL trades     : {len(trades_df[trades_df['direction']==SIGNAL_SELL])}")
    print(f"  SELL SL-hits          : {len(sell_sl)}")
    if sell_sl.empty:
        print("  No SELL SL-hits to analyse.")
        return

    # ── Build indicator df for candle-level lookup ───────────────────────────
    df_ind = add_indicators(df_raw.copy())
    df_ind["datetime"] = pd.to_datetime(df_ind["datetime"])
    df_ind = df_ind.sort_values("datetime").reset_index(drop=True)

    # ── For each SELL SL-hit, scan ahead for a BUY signal ───────────────────
    results = []   # {exit_time, look_candles, signal_found, entry_px, outcome_pts}

    for _, trade in sell_sl.iterrows():
        exit_dt = pd.to_datetime(trade["exit_time"])

        # Find index of the SL-hit candle in df_ind
        idx_matches = df_ind.index[df_ind["datetime"] >= exit_dt].tolist()
        if not idx_matches:
            continue
        sl_idx = idx_matches[0]

        # Scan forward up to max(LOOK_AHEAD) candles
        max_look = max(LOOK_AHEAD)
        for offset in range(1, max_look + 1):
            fwd_idx = sl_idx + offset
            if fwd_idx >= len(df_ind):
                break

            fwd_row  = df_ind.iloc[fwd_idx]
            fwd_dt   = pd.to_datetime(fwd_row["datetime"])
            fwd_time = fwd_dt.time()

            # Must still be within session and before NO_NEW_ENTRY_AFTER
            no_new = datetime.time(
                *map(int, config.NO_NEW_ENTRY_AFTER.split(":"))
            )
            if fwd_time >= no_new:
                break

            # Evaluate entry signal on this candle
            prev_row = df_ind.iloc[fwd_idx - 1]
            row_dict = dict(fwd_row)
            row_dict["prev_st_signal"] = int(prev_row.get("st_signal") or 0)
            sig = eval_entry_signal(row_dict)

            if sig == SIGNAL_BUY:
                # Found a BUY — now compute what the outcome would be:
                # Look ahead up to 60 more candles for target / SL hit
                buy_entry = float(fwd_row["close"])
                atr_val   = float(fwd_row.get("atr") or 0)
                buy_sl    = buy_entry - min(
                    config.SL_CAP_PTS.get(symbol, atr_val * 2.0),
                    atr_val * 2.0 if symbol != "NIFTY" else config.SL_CAP_PTS.get(symbol, 45)
                )
                buy_tgt   = buy_entry + atr_val * config.ATR_MULTIPLIER

                outcome_pts = None
                outcome_why = "timeout"
                for k in range(1, 61):
                    if fwd_idx + k >= len(df_ind):
                        break
                    c = df_ind.iloc[fwd_idx + k]
                    c_high  = float(c["high"])
                    c_low   = float(c["low"])
                    c_dt    = pd.to_datetime(c["datetime"])

                    # EOD exit
                    eod = datetime.datetime.combine(fwd_dt.date(), datetime.time(15, 15))
                    if c_dt >= eod:
                        outcome_pts = round(float(c["close"]) - buy_entry, 2)
                        outcome_why = "EOD"
                        break
                    # SL hit
                    if c_low <= buy_sl:
                        outcome_pts = round(buy_sl - buy_entry, 2)
                        outcome_why = "SL"
                        break
                    # Target hit
                    if c_high >= buy_tgt:
                        outcome_pts = round(buy_tgt - buy_entry, 2)
                        outcome_why = "TGT"
                        break

                for look in LOOK_AHEAD:
                    if offset <= look:
                        results.append({
                            "exit_dt"      : exit_dt,
                            "look_candles" : look,
                            "found_at"     : offset,
                            "entry_px"     : round(buy_entry, 2),
                            "outcome_pts"  : outcome_pts,
                            "outcome_why"  : outcome_why,
                        })
                break   # found a signal — no need to keep scanning for this trade

    if not results:
        print("\n  No BUY signals found after any SELL SL-hit within look-ahead windows.")
        return

    df_res = pd.DataFrame(results)

    # ── Summary per look-ahead window ────────────────────────────────────────
    print(f"\n  {'Window':<12} {'BUY found':>10} {'% of SLs':>10} {'Wins':>6} {'WR%':>7} {'Total pts':>11} {'Avg pts':>9}")
    print(f"  {'-'*70}")

    total_sl = len(sell_sl)
    for look in LOOK_AHEAD:
        sub = df_res[df_res["look_candles"] == look].drop_duplicates("exit_dt")
        found    = len(sub)
        pct      = round(found / total_sl * 100, 1) if total_sl else 0
        valid    = sub.dropna(subset=["outcome_pts"])
        wins     = int((valid["outcome_pts"] > 0).sum())
        wr       = round(wins / len(valid) * 100, 1) if len(valid) else 0
        tot_pts  = round(valid["outcome_pts"].sum(), 1)
        avg_pts  = round(valid["outcome_pts"].mean(), 1) if len(valid) else 0
        mins     = look * 5
        print(f"  {mins} min ({look}c)  {found:>10} {pct:>9}% {wins:>6} {wr:>6}% {tot_pts:>11} {avg_pts:>9}")

    # ── Detailed breakdown for 6-candle (30min) window ──────────────────────
    print(f"\n  -- Detailed: 30-min window --")
    sub30 = df_res[df_res["look_candles"] == 6].drop_duplicates("exit_dt")
    for _, r in sub30.iterrows():
        pts_str = f"{r['outcome_pts']:+.1f}" if r["outcome_pts"] is not None else "N/A"
        win_str = "WIN " if (r["outcome_pts"] or 0) > 0 else "LOSS"
        print(f"    {str(r['exit_dt'])[:16]}  found@+{r['found_at']}c  entry={r['entry_px']}  "
              f"{pts_str} pts  {win_str}  ({r['outcome_why']})")

    print(f"\n  Conclusion:")
    sub_best = df_res[df_res["look_candles"] == 6].drop_duplicates("exit_dt").dropna(subset=["outcome_pts"])
    if sub_best.empty:
        print("  Not enough data.")
        return
    wr_best = round((sub_best["outcome_pts"] > 0).mean() * 100, 1)
    net     = round(sub_best["outcome_pts"].sum(), 1)
    if wr_best >= 50 and net > 0:
        print(f"  [OK] Re-entry looks VIABLE - WR={wr_best}%, net={net:+.1f} pts over 30-min window")
        print(f"       Consider implementing post-SL BUY re-entry with relaxed RSI filter.")
    else:
        print(f"  [NO] Re-entry NOT viable - WR={wr_best}%, net={net:+.1f} pts - too many false reversals")
        print(f"       Current behaviour (wait for clean 5-filter setup) is safer.")


if __name__ == "__main__":
    print("Logging in to Angel One...")
    api, _, _ = get_session()
    print(f"Fetching {DAYS_BACK} days of candle data...\n")

    for sym, cfg in config.INSTRUMENTS.items():
        df_raw = fetch_candles(api, cfg["token"], cfg["exchange"], days_back=DAYS_BACK)
        if df_raw.empty:
            print(f"{sym}: no data fetched.")
            continue
        analyse(sym, df_raw)

    print("\nDone.")
