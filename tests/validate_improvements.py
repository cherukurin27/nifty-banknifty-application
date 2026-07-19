"""
tests/validate_improvements.py
Run A/B validation on each of the 7 improvement candidates against the
exact same 90-day real data used in the loss analysis.

Runs baseline + each candidate independently so we can see the pure delta
of each change before combining them.

Usage:  python tests/validate_improvements.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd
import datetime

from feed.angel_auth import get_session
from feed.data_feed import fetch_candles
from engine.indicators import add_indicators
from engine.signal_engine import SIGNAL_BUY, SIGNAL_SELL, SIGNAL_NONE, eval_entry_signal
from engine.utils import strip_tz, force_exit_dt
import config

# ─── helpers ─────────────────────────────────────────────────────────────────

def summary(trades: pd.DataFrame) -> dict:
    if trades.empty:
        return dict(trades=0, wins=0, losses=0, wr=0.0, pts=0.0, avg_w=0.0, avg_l=0.0,
                    rr=0.0, max_dd=0.0, mcl=0)
    total = len(trades)
    wins  = (trades["result"] == "WIN").sum()
    losses= (trades["result"] == "LOSS").sum()
    wr    = round(wins / total * 100, 1)
    pts   = round(trades["points"].sum(), 2)
    avg_w = round(trades.loc[trades["result"]=="WIN",  "points"].mean() or 0, 2)
    avg_l = round(trades.loc[trades["result"]=="LOSS", "points"].mean() or 0, 2)
    rr    = round(abs(avg_w / avg_l), 2) if avg_l else float("inf")
    cum   = trades["points"].cumsum()
    max_dd= round((cum - cum.cummax()).min(), 2)
    streak = mcl = 0
    for r in trades["result"]:
        streak = streak + 1 if r == "LOSS" else 0
        mcl    = max(mcl, streak)
    return dict(trades=total, wins=int(wins), losses=int(losses), wr=wr, pts=pts,
                avg_w=avg_w, avg_l=avg_l, rr=rr, max_dd=max_dd, mcl=mcl)


def delta(base: dict, cand: dict) -> dict:
    out = {}
    for k in ["trades","wins","losses","pts","max_dd"]:
        out[k] = round(cand[k] - base[k], 2)
    for k in ["wr","avg_w","avg_l","rr"]:
        out[k] = round(cand[k] - base[k], 2)
    out["mcl"] = cand["mcl"] - base["mcl"]
    return out


def _open_trade(symbol, cdate, cdt, close, direction, st_val, atr_val,
                rsi_val, adx_val, vwap_val, fixed_cap, atr_cap_mult):
    cap = (round(atr_val * atr_cap_mult, 2) if atr_cap_mult and atr_val
           else (fixed_cap or 9999))
    if direction == SIGNAL_BUY:
        sl_st  = round(st_val, 2) if st_val else round(close - cap, 2)
        sl_cap = round(close - cap, 2)
        sl     = max(sl_st, sl_cap)
    else:
        sl_st  = round(st_val, 2) if st_val else round(close + cap, 2)
        sl_cap = round(close + cap, 2)
        sl     = min(sl_st, sl_cap)
    target = (round(close + atr_val * config.ATR_MULTIPLIER, 2)
              if direction == SIGNAL_BUY
              else round(close - atr_val * config.ATR_MULTIPLIER, 2))
    return dict(symbol=symbol, date=cdate, direction=direction,
                entry_time=cdt, entry_price=round(close,2),
                sl=round(sl,2), target=target, entry_atr=round(atr_val,2),
                rsi=round(rsi_val,2), adx=round(adx_val,2), vwap=round(vwap_val,2))


def _close_trade(trades, trade, cdt, exit_px, reason):
    d  = trade["direction"]
    ep = round(exit_px, 2)
    pts= round((ep - trade["entry_price"]) if d == SIGNAL_BUY
               else (trade["entry_price"] - ep), 2)
    trades.append(dict(
        symbol=trade["symbol"], date=trade["date"], direction=d,
        entry_time=trade["entry_time"], entry_price=trade["entry_price"],
        sl=trade["sl"], target=trade["target"],
        exit_time=cdt, exit_price=ep, exit_reason=reason,
        points=pts, result="WIN" if pts>0 else ("LOSS" if pts<0 else "BE"),
        rsi=trade.get("rsi",0), adx=trade.get("adx",0), vwap=trade.get("vwap",0),
    ))


# ─── Core flexible backtester ─────────────────────────────────────────────────

def run_variant(
    df_full: pd.DataFrame,
    symbol: str,
    # Candidate switches
    no_new_entry_after: str  = "13:30",   # C1
    daily_loss_limit: int    = 999,        # C2: max consecutive losses before stopping day
    skip_thursday_nifty: bool= False,      # C3
    adx_max_override: float  = None,       # C4: per-symbol ADX_MAX override
    rsi_buy_high_override: float = None,   # C5: per-symbol RSI upper bound for BUY
    rsi_buy_low_override: float  = None,   # C6: per-symbol RSI lower bound for BUY
    vwap_proximity_pct: float    = None,   # C7: BUY rejected if entry > VWAP*(1+this)
) -> pd.DataFrame:

    df_full = df_full.copy()
    df_full["datetime"] = pd.to_datetime(df_full["datetime"])
    df_full = df_full.sort_values("datetime").reset_index(drop=True)
    df_ind  = add_indicators(df_full.copy())

    # Config values (may be overridden)
    _adx_max    = adx_max_override   if adx_max_override  is not None else config.ADX_MAX
    _rsi_buy_hi = rsi_buy_high_override if rsi_buy_high_override is not None else config.RSI_BUY_HIGH
    _rsi_buy_lo = rsi_buy_low_override  if rsi_buy_low_override  is not None else config.RSI_BUY_LOW
    h, m = map(int, no_new_entry_after.split(":"))
    _no_new_entry = datetime.time(h, m)

    trades      = []
    open_trade  = None
    pending_rev = None
    prev_date   = None
    fixed_cap   = config.SL_CAP_PTS.get(symbol)
    atr_cap_mult= getattr(config, "ATR_SL_MULT_BANKNIFTY", 2.0) if symbol == "BANKNIFTY" else None

    warmup = max(config.EMA_SLOW, config.RSI_PERIOD, config.ST_PERIOD) + 10

    # Per-day state for circuit-breaker
    day_consec_losses = 0

    for i in range(warmup, len(df_ind)):
        row   = df_ind.iloc[i]
        cdt   = pd.to_datetime(row["datetime"])
        cdt_n = strip_tz(cdt)
        cdate = cdt_n.date()
        close = float(row["close"])
        high  = float(row["high"])
        low   = float(row["low"])
        rsi_now  = float(row.get("rsi")      or 0)
        atr_val  = float(row.get("atr")      or 0)
        st_val   = float(row.get("st_value") or 0)
        st_sig   = int(row.get("st_signal")  or 0)
        adx_val  = float(row.get("adx")      or 0)
        vwap_val = float(row.get("vwap")     or 0)
        ema_s    = float(row.get("ema_slow") or 0)

        # New day reset
        if cdate != prev_date:
            if open_trade is not None:
                _close_trade(trades, open_trade, cdt, close, "Day Change")
                open_trade = None
            pending_rev = None
            prev_date   = cdate
            day_consec_losses = 0   # reset circuit-breaker each day

        # C3 — skip Thursday for Nifty
        if skip_thursday_nifty and symbol == "NIFTY" and cdate.weekday() == 3:
            if open_trade is not None and cdt_n >= force_exit_dt(cdate):
                _close_trade(trades, open_trade, cdt, close, "EOD Exit")
                open_trade = None; pending_rev = None
            continue

        # Evaluate raw signal using the variant's ADX/RSI thresholds
        import numpy as np_inner
        adx_ok = (not np_inner.isnan(adx_val)) and config.ADX_THRESHOLD <= adx_val <= _adx_max
        buy_all = (adx_ok and st_sig == 1
                   and close > ema_s
                   and _rsi_buy_lo  <= rsi_now <= _rsi_buy_hi
                   and close > vwap_val)
        sell_all = (adx_ok and st_sig == -1
                    and close < ema_s
                    and config.RSI_SELL_LOW <= rsi_now <= config.RSI_SELL_HIGH
                    and close < vwap_val)

        # C7 — VWAP proximity filter for BUY
        if vwap_proximity_pct is not None and buy_all and vwap_val > 0:
            if close > vwap_val * (1 + vwap_proximity_pct):
                buy_all = False

        sig_label = SIGNAL_BUY if buy_all else (SIGNAL_SELL if sell_all else SIGNAL_NONE)

        # Outside session
        t_now = cdt_n.time()
        if not (datetime.time(9, 30) <= t_now <= datetime.time(14, 30)):
            if open_trade is not None and cdt_n >= force_exit_dt(cdate):
                _close_trade(trades, open_trade, cdt, close, "EOD Exit")
                open_trade = None; pending_rev = None
            continue

        # Open trade management
        if open_trade is not None:
            direction = open_trade["direction"]

            # Supertrend trail
            st_trail = float(row.get("st_value") or 0)
            if st_trail and not np_inner.isnan(st_trail):
                open_trade["sl"] = (max(open_trade["sl"], st_trail) if direction == SIGNAL_BUY
                                    else min(open_trade["sl"], st_trail))

            # Hard cap
            entry = open_trade["entry_price"]
            live_cap = (round(atr_val * atr_cap_mult, 2) if atr_cap_mult and atr_val
                        else (fixed_cap or 9999))
            if direction == SIGNAL_BUY:
                open_trade["sl"] = max(open_trade["sl"], round(entry - live_cap, 2))
            else:
                open_trade["sl"] = min(open_trade["sl"], round(entry + live_cap, 2))

            sl = open_trade["sl"]

            # SL Hit
            if (direction == SIGNAL_BUY and low <= sl) or (direction == SIGNAL_SELL and high >= sl):
                _close_trade(trades, open_trade, cdt, sl, "SL Hit")
                if trades[-1]["result"] == "LOSS":
                    day_consec_losses += 1
                else:
                    day_consec_losses = 0
                open_trade = None; pending_rev = None
                continue

            # RSI exit
            rsi_exit = ((direction == SIGNAL_BUY  and rsi_now < config.RSI_EXIT_BUY) or
                        (direction == SIGNAL_SELL and rsi_now > config.RSI_EXIT_SELL))
            if rsi_exit:
                label = (f"RSI Exit (BUY RSI<{config.RSI_EXIT_BUY})"
                         if direction == SIGNAL_BUY
                         else f"RSI Exit (SELL RSI>{config.RSI_EXIT_SELL})")
                _close_trade(trades, open_trade, cdt, close, label)
                if trades[-1]["result"] == "LOSS":
                    day_consec_losses += 1
                else:
                    day_consec_losses = 0
                open_trade = None; pending_rev = None
                continue

            # EOD
            if cdt_n >= force_exit_dt(cdate):
                _close_trade(trades, open_trade, cdt, close, "EOD Exit")
                if trades[-1]["result"] == "LOSS":
                    day_consec_losses += 1
                else:
                    day_consec_losses = 0
                open_trade = None; pending_rev = None
                continue

            # Reverse (2-candle)
            rev = SIGNAL_SELL if direction == SIGNAL_BUY else SIGNAL_BUY
            if pending_rev == rev and sig_label == rev:
                _close_trade(trades, open_trade, cdt, close, "Reverse Signal")
                if trades[-1]["result"] == "LOSS":
                    day_consec_losses += 1
                else:
                    day_consec_losses = 0
                open_trade = None; pending_rev = None
                if cdt_n.time() <= _no_new_entry:
                    open_trade = _open_trade(symbol, cdate, cdt, close, sig_label,
                                             st_val, atr_val, rsi_now, adx_val, vwap_val,
                                             fixed_cap, atr_cap_mult)
                continue

            pending_rev = sig_label if sig_label == rev else None

        else:
            if cdt_n >= force_exit_dt(cdate):              continue
            if cdt_n.time() > _no_new_entry:               continue  # C1
            if cdt_n.time() < datetime.time(9, 40):        continue

            # C2 — daily circuit-breaker
            if day_consec_losses >= daily_loss_limit:
                continue

            if sig_label != SIGNAL_NONE:
                open_trade  = _open_trade(symbol, cdate, cdt, close, sig_label,
                                          st_val, atr_val, rsi_now, adx_val, vwap_val,
                                          fixed_cap, atr_cap_mult)
                pending_rev = None

    if open_trade is not None:
        last = df_ind.iloc[-1]
        _close_trade(trades, open_trade,
                     pd.to_datetime(last["datetime"]), float(last["close"]), "Data End")

    cols = ["symbol","date","direction","entry_time","entry_price","sl","target",
            "exit_time","exit_price","exit_reason","points","result","rsi","adx","vwap"]
    return pd.DataFrame(trades, columns=cols) if trades else pd.DataFrame()


# ─── MAIN ────────────────────────────────────────────────────────────────────

print("=" * 70)
print("  Logging into Angel One...")
api, _, _ = get_session()
print("  Login OK\n")

raw_data = {}
for sym in ["NIFTY", "BANKNIFTY"]:
    cfg = config.INSTRUMENTS[sym]
    print(f"  Fetching 93 days for {sym}...")
    df = fetch_candles(api, cfg["token"], cfg["exchange"],
                       interval="FIVE_MINUTE", days_back=93)
    df["datetime"] = pd.to_datetime(df["datetime"])
    df = df.sort_values("datetime").reset_index(drop=True)
    raw_data[sym] = df
    print(f"    {len(df)} candles, {df['datetime'].min().date()} to {df['datetime'].max().date()}")

print()

# ─── Define all variants ─────────────────────────────────────────────────────
VARIANTS = {
    "Baseline": {},
    "C1: Entry cut-off 13:00": {"no_new_entry_after": "13:00"},
    "C1b: Entry cut-off 12:30": {"no_new_entry_after": "12:30"},
    "C2: Circuit-breaker @2": {"daily_loss_limit": 2},
    "C2b: Circuit-breaker @3": {"daily_loss_limit": 3},
    "C3: Skip Thu (Nifty)": {"skip_thursday_nifty": True},
    "C4: ADX_MAX=45": {"adx_max_override": 45},
    "C4b: ADX_MAX=40": {"adx_max_override": 40},
    "C5: BUY RSI_HI=65": {"rsi_buy_high_override": 65},
    "C6: BUY RSI_LO=53": {"rsi_buy_low_override": 53},
    "C7: VWAP prox 0.10%": {"vwap_proximity_pct": 0.001},
    # Combined best candidates
    "COMBINED: C1+C2+C3": {
        "no_new_entry_after": "13:00",
        "daily_loss_limit": 2,
        "skip_thursday_nifty": True,
    },
    "COMBINED: ALL": {
        "no_new_entry_after": "13:00",
        "daily_loss_limit": 2,
        "skip_thursday_nifty": True,
        "adx_max_override": 45,
        "rsi_buy_high_override": 65,
        "rsi_buy_low_override": 53,
        "vwap_proximity_pct": 0.001,
    },
}

# ─── Run all variants for each symbol ────────────────────────────────────────
all_results = {sym: {} for sym in raw_data}

for sym, df in raw_data.items():
    print(f"{'='*70}")
    print(f"  {sym} — Running {len(VARIANTS)} variants")
    print(f"{'='*70}")

    baseline_s = None

    for vname, kwargs in VARIANTS.items():
        trades = run_variant(df, sym, **kwargs)
        s = summary(trades)
        all_results[sym][vname] = s

        if vname == "Baseline":
            baseline_s = s
            d = {k: 0 for k in s}
        else:
            d = delta(baseline_s, s)

        # Delta arrows
        pts_arrow   = "+" if d["pts"]    >= 0 else ""
        wr_arrow    = "+" if d["wr"]     >= 0 else ""
        dd_arrow    = "+" if d["max_dd"] >= 0 else ""
        trade_arrow = "+" if d["trades"] >= 0 else ""

        print(f"  {vname:<32} "
              f"T={s['trades']:3d}({trade_arrow}{d['trades']:+d})  "
              f"WR={s['wr']:5.1f}%({wr_arrow}{d['wr']:+.1f}pp)  "
              f"Pts={s['pts']:8.1f}({pts_arrow}{d['pts']:+.1f})  "
              f"MaxDD={s['max_dd']:8.1f}({dd_arrow}{d['max_dd']:+.1f})  "
              f"MCL={s['mcl']:2d}({d['mcl']:+d})")

    print()

# ─── Final combined comparison table ─────────────────────────────────────────
print(f"\n{'='*70}")
print("  FINAL COMBINED COMPARISON (Baseline vs Combined)")
print(f"{'='*70}")

header_printed = False
for sym in ["NIFTY", "BANKNIFTY"]:
    print(f"\n  {sym}:")
    base = all_results[sym]["Baseline"]
    comb1 = all_results[sym]["COMBINED: C1+C2+C3"]
    comb2 = all_results[sym]["COMBINED: ALL"]

    rows = [
        ("Trades",    base["trades"],    comb1["trades"],    comb2["trades"],    ""),
        ("Wins",      base["wins"],      comb1["wins"],      comb2["wins"],      ""),
        ("Losses",    base["losses"],    comb1["losses"],    comb2["losses"],    ""),
        ("Win Rate",  f"{base['wr']}%",  f"{comb1['wr']}%", f"{comb2['wr']}%",  ""),
        ("Total Pts", base["pts"],       comb1["pts"],       comb2["pts"],       ""),
        ("Avg WIN",   base["avg_w"],     comb1["avg_w"],     comb2["avg_w"],     ""),
        ("Avg LOSS",  base["avg_l"],     comb1["avg_l"],     comb2["avg_l"],     ""),
        ("Max DD",    base["max_dd"],    comb1["max_dd"],    comb2["max_dd"],    ""),
        ("MCL",       base["mcl"],       comb1["mcl"],       comb2["mcl"],       ""),
    ]
    print(f"    {'Metric':<12} {'Baseline':>12} {'C1+C2+C3':>12} {'ALL 7':>12}  {'Delta (ALL)':>12}")
    print(f"    {'-'*60}")
    for label, bv, c1v, c2v, _ in rows:
        try:
            dv = round(float(c2v) - float(bv), 2) if isinstance(bv, (int, float)) else "—"
            dv_str = f"{dv:+.2f}" if isinstance(dv, float) else dv
        except:
            dv_str = "—"
        print(f"    {label:<12} {str(bv):>12} {str(c1v):>12} {str(c2v):>12}  {dv_str:>12}")

print(f"\n{'='*70}")
print("  VALIDATION COMPLETE")
print(f"{'='*70}")

# Output JSON for the report generator
import json
out_path = "tests/validation_results.json"
with open(out_path, "w") as f:
    json.dump(all_results, f, indent=2)
print(f"\n  Results saved to {out_path}")
