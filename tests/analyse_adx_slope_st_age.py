"""
tests/analyse_adx_slope_st_age.py

Analyses two sideways-market filters:
  1. ADX Slope  — was ADX rising or falling at entry candle?
                  Rising = fresh trend forming (good)
                  Falling = trend exhausting (bad)
  2. ST Age     — how many consecutive candles has Supertrend been in same direction?
                  More candles = more confirmed trend (potentially better entries)

For each filter shows W/L/WR/net-pts breakdown to decide if worth implementing.

Usage:
    python tests/analyse_adx_slope_st_age.py
"""

from __future__ import annotations
import sys, os, datetime
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pandas as pd
import numpy as np

import config
from feed.angel_auth import get_session
from feed.data_feed import fetch_candles
from engine.indicators import add_indicators
from engine.signal_engine import eval_entry_signal, SIGNAL_BUY, SIGNAL_SELL, SIGNAL_NONE
from engine.utils import strip_tz, force_exit_dt
from engine.backtester import _open, _close, summary_stats

DAYS_BACK = 186
SYMBOLS   = list(config.INSTRUMENTS.keys())

def avg(lst): return round(sum(lst) / len(lst), 2) if lst else 0.0

def print_table(title, rows, cols):
    col_w = [max(len(c), max((len(str(r.get(c,""))) for r in rows), default=0)) + 2 for c in cols]
    header = "  " + "  ".join(f"{c:<{w}}" for c, w in zip(cols, col_w))
    sep    = "  " + "-" * (sum(col_w) + 2 * len(cols))
    print(f"\n  {title}")
    print(sep)
    print(header)
    print(sep)
    for r in rows:
        print("  " + "  ".join(f"{str(r.get(c,'')):<{w}}" for c, w in zip(cols, col_w)))


def analyse(symbol: str, df_raw: pd.DataFrame):
    print(f"\n{'='*65}")
    print(f"  {symbol} -- ADX Slope + ST Age Analysis")
    print(f"{'='*65}")

    df_ind = add_indicators(df_raw.copy())
    df_ind["datetime"] = pd.to_datetime(df_ind["datetime"])
    df_ind = df_ind.sort_values("datetime").reset_index(drop=True)
    df_ind["symbol"] = symbol

    warmup  = max(config.EMA_SLOW, config.RSI_PERIOD, config.ST_PERIOD) + 10
    fixed_cap    = config.SL_CAP_PTS.get(symbol)
    atr_cap_mult = (None if fixed_cap is not None
                    else getattr(config, "ATR_SL_MULT_BANKNIFTY", 2.0)
                    if symbol == "BANKNIFTY"
                    else 2.0)

    entries = []   # list of dicts — one per trade entry with metadata

    open_trade  = None
    trades      = []
    prev_date   = None

    for i in range(warmup, len(df_ind)):
        row    = df_ind.iloc[i]
        cdt    = pd.to_datetime(row["datetime"])
        cdt_n  = strip_tz(cdt)
        cdate  = cdt_n.date()
        close  = float(row["close"])
        high   = float(row["high"])
        low    = float(row["low"])
        atr_val = float(row.get("atr") or 0)
        st_sig  = int(row.get("st_signal") or 0)

        if cdate != prev_date:
            if open_trade is not None:
                _close(trades, open_trade, cdt, close, "Day Change")
                open_trade = None
            prev_date = cdate

        # Compute ADX slope: current ADX vs 3-candle avg before it
        adx_now  = float(row.get("adx") or 0)
        adx_prev = float(df_ind.iloc[i-1].get("adx") or 0) if i >= 1 else adx_now
        adx_prev3 = float(df_ind.iloc[max(0,i-3):i]["adx"].mean()) if i >= 3 else adx_prev
        adx_slope = round(adx_now - adx_prev3, 2)   # +ve = rising, -ve = falling

        # Compute ST age: how many consecutive candles in same direction
        st_age = 0
        for back in range(1, 20):
            if i - back < 0:
                break
            prev_st = int(df_ind.iloc[i - back].get("st_signal") or 0)
            if prev_st == st_sig:
                st_age += 1
            else:
                break

        prev_st_sig = int(df_ind.iloc[i-1].get("st_signal") or 0)
        row_dict = dict(row)
        row_dict["prev_st_signal"] = prev_st_sig
        sig_label = eval_entry_signal(row_dict)

        # Manage open trade
        if open_trade is not None:
            direction = open_trade["direction"]
            sl_frozen = open_trade["sl"]
            sl_hit = ((direction == SIGNAL_BUY  and low  <= sl_frozen) or
                      (direction == SIGNAL_SELL and high >= sl_frozen))
            if sl_hit:
                _close(trades, open_trade, cdt, sl_frozen, "SL Hit")
                open_trade = None
            elif cdt_n >= force_exit_dt(cdate):
                _close(trades, open_trade, cdt, close, "EOD Exit")
                open_trade = None
            else:
                # Trail SL
                st_val = float(row.get("st_value") or 0)
                if st_val and not np.isnan(st_val):
                    if direction == SIGNAL_BUY:
                        open_trade["sl"] = max(open_trade["sl"], st_val)
                    else:
                        open_trade["sl"] = max(open_trade["sl"], open_trade["entry_price"] +
                                               (fixed_cap or atr_val * 2.0))
                continue

        # New entry
        no_new_h, no_new_m = map(int, config.NO_NEW_ENTRY_AFTER.split(":"))
        if (open_trade is None
                and sig_label in (SIGNAL_BUY, SIGNAL_SELL)
                and cdt_n.time() < datetime.time(no_new_h, no_new_m)
                and cdt_n.time() >= datetime.time(9, 40)):

            st_val = float(row.get("st_value") or 0)
            open_trade = _open(symbol, cdate, cdt, close, sig_label,
                               st_val, atr_val,
                               float(row.get("rsi") or 0),
                               float(row.get("adx") or 0),
                               float(row.get("vwap") or 0),
                               fixed_cap, atr_cap_mult)

            entries.append({
                "entry_dt"   : cdt_n,
                "direction"  : sig_label,
                "adx"        : adx_now,
                "adx_slope"  : adx_slope,
                "adx_rising" : adx_slope > 0,
                "st_age"     : st_age,
                "trade_idx"  : len(trades),   # will link to trade after close
            })

    # Link outcomes to entries
    results = []
    for j, e in enumerate(entries):
        # Find the trade that started at this entry
        t_idx = e["trade_idx"]
        if t_idx < len(trades):
            t = trades[t_idx]
        else:
            continue
        results.append({**e,
                         "pts"   : t["points"],
                         "result": "WIN" if t["points"] > 0 else "LOSS",
                         "reason": t["exit_reason"]})

    if not results:
        print("  No trades found.")
        return

    df_r = pd.DataFrame(results)

    # ── 1. ADX Slope Analysis ─────────────────────────────────────────────────
    print(f"\n  [1] ADX SLOPE AT ENTRY  (rising = ADX increasing over last 3 candles)")
    rows_out = []
    for rising, label in [(True, "ADX Rising (+slope)"), (False, "ADX Falling (-slope)")]:
        sub = df_r[df_r["adx_rising"] == rising]
        if sub.empty: continue
        wins = sub[sub["result"] == "WIN"]
        loss = sub[sub["result"] == "LOSS"]
        wr   = round(len(wins) / len(sub) * 100, 1)
        net  = round(sub["pts"].sum(), 1)
        aw   = avg(wins["pts"].tolist())
        al   = avg(loss["pts"].tolist())
        flag = " <-- EDGE" if wr >= 55 else (" <-- DRAIN" if wr < 30 and len(sub) >= 5 else "")
        rows_out.append({"Slope": label, "Trades": len(sub), "Wins": len(wins),
                         "WR%": f"{wr}%", "Net Pts": net,
                         "Avg Win": aw, "Avg Loss": al, "": flag})
    print_table("ADX Slope breakdown", rows_out,
                ["Slope", "Trades", "Wins", "WR%", "Net Pts", "Avg Win", "Avg Loss", ""])

    # ADX slope sub-bands
    print(f"\n  ADX slope buckets (how much rising/falling):")
    slope_bins = [(-99,-2,"Falling fast (<-2)"),(-2,0,"Falling slow (-2 to 0)"),
                  (0,2,"Rising slow (0 to +2)"),(2,5,"Rising mod (+2 to +5)"),
                  (5,99,"Rising fast (>+5)")]
    rows2 = []
    for lo, hi, lbl in slope_bins:
        sub = df_r[(df_r["adx_slope"] >= lo) & (df_r["adx_slope"] < hi)]
        if sub.empty: continue
        wins = sub[sub["result"] == "WIN"]
        wr   = round(len(wins) / len(sub) * 100, 1)
        net  = round(sub["pts"].sum(), 1)
        flag = " <-- EDGE" if wr >= 55 and len(sub) >= 4 else (" <-- DRAIN" if wr < 30 and len(sub) >= 4 else "")
        rows2.append({"Slope Band": lbl, "Trades": len(sub), "Wins": len(wins),
                      "WR%": f"{wr}%", "Net Pts": net, "": flag})
    print_table("Slope band breakdown", rows2,
                ["Slope Band", "Trades", "Wins", "WR%", "Net Pts", ""])

    # ── 2. ST Age Analysis ────────────────────────────────────────────────────
    print(f"\n  [2] SUPERTREND AGE AT ENTRY (how many consecutive same-direction candles)")
    age_bins = [(0,2,"ST age 1 (fresh flip)"),(2,4,"ST age 2-3"),(4,7,"ST age 4-6"),
                (7,12,"ST age 7-11"),(12,99,"ST age 12+")]
    rows3 = []
    for lo, hi, lbl in age_bins:
        sub = df_r[(df_r["st_age"] >= lo) & (df_r["st_age"] < hi)]
        if sub.empty: continue
        wins = sub[sub["result"] == "WIN"]
        wr   = round(len(wins) / len(sub) * 100, 1)
        net  = round(sub["pts"].sum(), 1)
        flag = " <-- EDGE" if wr >= 55 and len(sub) >= 4 else (" <-- DRAIN" if wr < 30 and len(sub) >= 4 else "")
        rows3.append({"ST Age": lbl, "Trades": len(sub), "Wins": len(wins),
                      "WR%": f"{wr}%", "Net Pts": net, "": flag})
    print_table("ST age breakdown", rows3,
                ["ST Age", "Trades", "Wins", "WR%", "Net Pts", ""])

    # ── 3. Combined: Rising ADX + ST age ──────────────────────────────────────
    print(f"\n  [3] COMBINED: Rising ADX + ST age >= 3")
    for combo_label, mask in [
        ("Rising ADX + ST age >= 3", (df_r["adx_rising"]) & (df_r["st_age"] >= 3)),
        ("Rising ADX + ST age < 3",  (df_r["adx_rising"]) & (df_r["st_age"] < 3)),
        ("Falling ADX + any ST age", ~df_r["adx_rising"]),
    ]:
        sub = df_r[mask]
        if sub.empty: continue
        wins = sub[sub["result"] == "WIN"]
        wr   = round(len(wins) / len(sub) * 100, 1) if len(sub) else 0
        net  = round(sub["pts"].sum(), 1)
        print(f"    {combo_label:<35}  n={len(sub):>3}  WR={wr:>5.1f}%  Net={net:>+8.1f}")

    # ── 4. Recommendation ─────────────────────────────────────────────────────
    rising = df_r[df_r["adx_rising"]]
    falling = df_r[~df_r["adx_rising"]]
    wr_r = round((rising["result"] == "WIN").mean() * 100, 1) if len(rising) else 0
    wr_f = round((falling["result"] == "WIN").mean() * 100, 1) if len(falling) else 0
    net_r = round(rising["pts"].sum(), 1)
    net_f = round(falling["pts"].sum(), 1)

    print(f"\n  Recommendation:")
    if wr_r >= wr_f + 10 and net_f < 0:
        print(f"  [IMPLEMENT] ADX slope filter is WORTH it:")
        print(f"    Rising ADX  -> WR={wr_r}%, Net={net_r:+.1f} pts")
        print(f"    Falling ADX -> WR={wr_f}%, Net={net_f:+.1f} pts  (would be BLOCKED)")
        print(f"    Blocking falling ADX saves {abs(net_f):.1f} pts with {len(falling)} fewer trades")
    else:
        print(f"  [SKIP] ADX slope difference not large enough to justify filter:")
        print(f"    Rising ADX  -> WR={wr_r}%, Net={net_r:+.1f} pts")
        print(f"    Falling ADX -> WR={wr_f}%, Net={net_f:+.1f} pts")


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
