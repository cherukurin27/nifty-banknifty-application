"""
tests/validate_bnkn_improvements.py
A/B validation of BankNifty-specific improvement candidates.
Tests each filter in isolation against the baseline (90-day real data).

Candidates:
  C1: Skip 10:00–10:30 slot (28.6% WR, -856 pts in 14 trades)
  C2: Skip BUY 10:00–10:30 only (keep SELL entries in that slot)
  C3: RSI_BUY_HIGH 65→60 for BankNifty (RSI 62-65 BUY: 41.7% WR, -87 pts)
  C4: ADX_MIN raise 20→25 for BankNifty (ADX 20-25 still good at 63%; but removing low-ADX weak entries)
  C5: ADX 30-35 skip (42.1% WR bucket)
  C6: Skip Friday BankNifty (40% WR, +62 pts on 15 trades — marginal)
  C7: Tighter ATR SL cap: 1.5x instead of 2x (reduce large losses)
  C8: C1 + C3 combined
  C9: C1 + C7 combined
  C10: C2 + C3 combined (best of each)
"""

import sys, os, copy
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.makedirs("logs", exist_ok=True)

import json
import datetime
import numpy as np
import pandas as pd

import config
from feed.angel_auth import get_session
from feed.data_feed import fetch_candles
from engine.indicators import add_indicators
from engine.signal_engine import SIGNAL_BUY, SIGNAL_SELL, SIGNAL_NONE, eval_entry_signal
from engine.utils import strip_tz, force_exit_dt
from engine.backtester import summary_stats, _open, _close

print("Logging into Angel One...")
api, _, _ = get_session()
print("Login OK\n")

cfg = config.INSTRUMENTS["BANKNIFTY"]
print("Fetching 90 days BANKNIFTY...")
df_full = fetch_candles(api, cfg["token"], cfg["exchange"],
                        interval="FIVE_MINUTE", days_back=93)
df_full["datetime"] = pd.to_datetime(df_full["datetime"])
df_full = df_full.sort_values("datetime").reset_index(drop=True)
max_date = df_full["datetime"].max()
cutoff   = max_date - pd.Timedelta(days=90)
df90     = df_full[df_full["datetime"] >= cutoff].reset_index(drop=True)
print(f"  {len(df90)} candles  {df90['datetime'].min().date()} to {max_date.date()}\n")


# ─── Parameterised backtester (BankNifty only) ────────────────────────────────

def run_bnkn(df_full: pd.DataFrame,
             skip_slot_start: datetime.time | None = None,
             skip_slot_end:   datetime.time | None = None,
             skip_slot_buy_only: bool = False,
             rsi_buy_high: float = config.RSI_BUY_HIGH,
             adx_min: float = config.ADX_THRESHOLD,
             adx_skip_lo: float | None = None,
             adx_skip_hi: float | None = None,
             skip_friday: bool = False,
             atr_sl_mult: float = config.ATR_SL_MULT_BANKNIFTY,
             ) -> pd.DataFrame:
    """Walk-forward backtest with per-candidate overrides for BankNifty."""
    symbol = "BANKNIFTY"
    df = df_full.copy()
    df["datetime"] = pd.to_datetime(df["datetime"])
    df = df.sort_values("datetime").reset_index(drop=True)
    df_ind = add_indicators(df.copy())

    trades     = []
    open_trade = None
    pending_rev = None
    prev_date   = None
    fixed_cap   = None          # BankNifty uses ATR cap
    atr_cap_mult = atr_sl_mult

    warmup = max(config.EMA_SLOW, config.RSI_PERIOD, config.ST_PERIOD) + 10

    h_ne, m_ne = map(int, config.NO_NEW_ENTRY_AFTER.split(":"))
    no_entry_after = datetime.time(h_ne, m_ne)

    for i in range(warmup, len(df_ind)):
        row    = df_ind.iloc[i]
        cdt    = pd.to_datetime(row["datetime"])
        cdt_n  = strip_tz(cdt)
        cdate  = cdt_n.date()
        close  = float(row["close"])
        high   = float(row["high"])
        low    = float(row["low"])
        rsi_now  = float(row.get("rsi")      or 0)
        atr_val  = float(row.get("atr")      or 0)
        st_val   = float(row.get("st_value") or 0)
        st_sig   = int(row.get("st_signal")  or 0)
        adx_val  = float(row.get("adx")      or 0)
        vwap_val = float(row.get("vwap")     or 0)
        ema_s    = float(row.get("ema_slow") or 0)

        if cdate != prev_date:
            if open_trade is not None:
                _close(trades, open_trade, cdt, close, "Day Change")
                open_trade = None
            pending_rev = None
            prev_date   = cdate

        # ── base signal (shared 5-filter logic) ──────────────────────────────
        adx_max  = config.ADX_MAX
        adx_ok   = (not np.isnan(adx_val)) and adx_min <= adx_val <= adx_max
        buy_ok   = (adx_ok and st_sig == 1 and close > ema_s
                    and config.RSI_BUY_LOW <= rsi_now <= rsi_buy_high
                    and close > vwap_val)
        sell_ok  = (adx_ok and st_sig == -1 and close < ema_s
                    and config.RSI_SELL_LOW <= rsi_now <= config.RSI_SELL_HIGH
                    and close < vwap_val)

        # ── per-candidate extra filters ───────────────────────────────────────
        t = cdt_n.time()

        # ADX band skip (C5)
        if adx_skip_lo is not None and adx_skip_hi is not None:
            if adx_skip_lo <= adx_val <= adx_skip_hi:
                buy_ok = False; sell_ok = False

        # Slot skip (C1 / C2)
        if skip_slot_start and skip_slot_end:
            in_skip_slot = skip_slot_start <= t < skip_slot_end
            if in_skip_slot:
                if skip_slot_buy_only:
                    buy_ok = False
                else:
                    buy_ok = False; sell_ok = False

        # Friday skip (C6)
        if skip_friday and cdate.weekday() == 4:
            buy_ok = False; sell_ok = False

        sig_label = SIGNAL_BUY if buy_ok else (SIGNAL_SELL if sell_ok else SIGNAL_NONE)

        # ── session bounds ─────────────────────────────────────────────────────
        in_session = datetime.time(9, 30) <= t <= datetime.time(14, 30)

        # ── open trade management ─────────────────────────────────────────────
        if open_trade is not None:
            direction = open_trade["direction"]

            st_trail = float(row.get("st_value") or 0)
            if st_trail and not np.isnan(st_trail):
                if direction == SIGNAL_BUY:
                    open_trade["sl"] = max(open_trade["sl"], st_trail)
                else:
                    open_trade["sl"] = min(open_trade["sl"], st_trail)

            entry_p = open_trade["entry_price"]
            live_cap = round(atr_val * atr_cap_mult, 2) if atr_val else open_trade["entry_atr"] * atr_cap_mult
            if direction == SIGNAL_BUY:
                hard_sl = round(entry_p - live_cap, 2)
                open_trade["sl"] = max(open_trade["sl"], hard_sl)
            else:
                hard_sl = round(entry_p + live_cap, 2)
                open_trade["sl"] = min(open_trade["sl"], hard_sl)

            sl = open_trade["sl"]
            sl_hit = ((direction == SIGNAL_BUY  and low  <= sl) or
                      (direction == SIGNAL_SELL and high >= sl))
            if sl_hit:
                _close(trades, open_trade, cdt, sl, "SL Hit")
                open_trade = None; pending_rev = None; continue

            rsi_exit = ((direction == SIGNAL_BUY  and rsi_now < config.RSI_EXIT_BUY) or
                        (direction == SIGNAL_SELL and rsi_now > config.RSI_EXIT_SELL))
            if rsi_exit:
                _close(trades, open_trade, cdt, close,
                       f"RSI Exit ({'BUY' if direction==SIGNAL_BUY else 'SELL'})")
                open_trade = None; pending_rev = None; continue

            if cdt_n >= force_exit_dt(cdate):
                _close(trades, open_trade, cdt, close, "EOD Exit")
                open_trade = None; pending_rev = None; continue

            reverse = SIGNAL_SELL if direction == SIGNAL_BUY else SIGNAL_BUY
            if pending_rev == reverse and sig_label == reverse:
                _close(trades, open_trade, cdt, close, "Reverse Signal")
                open_trade = None; pending_rev = None
                if t <= no_entry_after:
                    open_trade = _open(symbol, cdate, cdt, close, sig_label,
                                       st_val, atr_val, rsi_now, adx_val, vwap_val,
                                       fixed_cap, atr_cap_mult)
                continue
            pending_rev = sig_label if sig_label == reverse else None

        else:
            if not in_session:
                if open_trade is not None and cdt_n >= force_exit_dt(cdate):
                    _close(trades, open_trade, cdt, close, "EOD Exit")
                    open_trade = None; pending_rev = None
                continue
            if cdt_n >= force_exit_dt(cdate): continue
            if t > no_entry_after:             continue
            if t < datetime.time(9, 40):       continue

            if sig_label != SIGNAL_NONE:
                open_trade  = _open(symbol, cdate, cdt, close, sig_label,
                                    st_val, atr_val, rsi_now, adx_val, vwap_val,
                                    fixed_cap, atr_cap_mult)
                pending_rev = None

    if open_trade is not None:
        last = df_ind.iloc[-1]
        _close(trades, open_trade,
               pd.to_datetime(last["datetime"]), float(last["close"]), "Data End")

    cols = ["symbol","date","direction","entry_time","entry_price","sl","target",
            "exit_time","exit_price","exit_reason","points","result","rsi","adx","vwap"]
    return pd.DataFrame(trades, columns=cols) if trades else pd.DataFrame()


def stats(trades):
    if trades.empty:
        return {"total_trades":0,"wins":0,"losses":0,"win_rate_pct":0.0,
                "total_points":0.0,"avg_win_pts":0.0,"avg_loss_pts":0.0,
                "risk_reward":0.0,"max_consec_loss":0,"max_drawdown":0.0}
    s = summary_stats(trades)
    return s


# ─── Baseline ─────────────────────────────────────────────────────────────────
print("Running baseline...")
t_base  = run_bnkn(df90)
s_base  = stats(t_base)
print(f"  Baseline: {s_base['total_trades']} trades  WR={s_base['win_rate_pct']:.1f}%  "
      f"pts={s_base['total_points']:+.1f}  DD={s_base['max_drawdown']:+.1f}\n")

# ─── Candidates ───────────────────────────────────────────────────────────────
candidates = {
    "C1_skip_1000_1030": dict(
        skip_slot_start=datetime.time(10, 0),
        skip_slot_end=datetime.time(10, 30)),
    "C2_skip_1000_1030_buy_only": dict(
        skip_slot_start=datetime.time(10, 0),
        skip_slot_end=datetime.time(10, 30),
        skip_slot_buy_only=True),
    "C3_rsi_buy_high_60": dict(rsi_buy_high=60.0),
    "C3b_rsi_buy_high_62": dict(rsi_buy_high=62.0),
    "C4_adx_min_25": dict(adx_min=25.0),
    "C5_skip_adx_30_35": dict(adx_skip_lo=30.0, adx_skip_hi=35.0),
    "C6_skip_friday": dict(skip_friday=True),
    "C7_atr_cap_1p5x": dict(atr_sl_mult=1.5),
    "C7b_atr_cap_1p8x": dict(atr_sl_mult=1.8),
    "C8_C1_plus_C3": dict(
        skip_slot_start=datetime.time(10, 0),
        skip_slot_end=datetime.time(10, 30),
        rsi_buy_high=60.0),
    "C9_C1_plus_C7": dict(
        skip_slot_start=datetime.time(10, 0),
        skip_slot_end=datetime.time(10, 30),
        atr_sl_mult=1.5),
    "C10_C2_plus_C3b": dict(
        skip_slot_start=datetime.time(10, 0),
        skip_slot_end=datetime.time(10, 30),
        skip_slot_buy_only=True,
        rsi_buy_high=62.0),
    "C11_C1_C3b_C7b": dict(
        skip_slot_start=datetime.time(10, 0),
        skip_slot_end=datetime.time(10, 30),
        rsi_buy_high=62.0,
        atr_sl_mult=1.8),
}

results = {"baseline": {**s_base, "label": "Baseline (current)"}}

print(f"{'Candidate':<25}  {'Trades':>7}  {'WR':>6}  {'Total Pts':>10}  {'DD':>8}  {'MaxCL':>6}  {'Delta Pts':>10}")
print("-" * 85)
for name, kwargs in candidates.items():
    t = run_bnkn(df90, **kwargs)
    s = stats(t)
    delta = round(s["total_points"] - s_base["total_points"], 1)
    flag  = " <-- BETTER" if delta > 0 else ""
    print(f"  {name:<23}  {s['total_trades']:>7}  {s['win_rate_pct']:>5.1f}%  "
          f"{s['total_points']:>+10.1f}  {s['max_drawdown']:>+8.1f}  "
          f"{s['max_consec_loss']:>6}  {delta:>+10.1f}{flag}")
    results[name] = {**s, "label": name, "delta_pts": delta}

# ─── Save JSON ─────────────────────────────────────────────────────────────────
out_path = "logs/bnkn_candidate_results.json"
with open(out_path, "w") as f:
    json.dump(results, f, indent=2, default=str)
print(f"\nResults saved to {out_path}")
print("DONE")
