"""
tests/analyse_losses.py — Deep loss-trade analysis on 90 days of real Angel One data.
Runs entirely offline after data fetch. No code changes — analysis only.

Run with:
    python tests/analyse_losses.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import numpy as np
import pandas as pd
import datetime

from feed.angel_auth import get_session
from feed.data_feed import fetch_candles
from engine.indicators import add_indicators
from engine.backtester import run_backtest, summary_stats
import config

DAYS_BACK = 93   # fetch ~90 trading days worth of calendar days

# ─── colour helpers ──────────────────────────────────────────────────────────
W  = "\033[0m"
R  = "\033[91m"    # red
G  = "\033[92m"    # green
Y  = "\033[93m"    # yellow
B  = "\033[94m"    # blue
M  = "\033[95m"    # magenta
C  = "\033[96m"    # cyan
BLD= "\033[1m"

SEP  = "=" * 70
SEP2 = "-" * 70

def h(text): print(f"\n{BLD}{B}{text}{W}")
def sub(text): print(f"  {BLD}{text}{W}")
def row(*cols, widths=None):
    if widths:
        parts = [str(c).ljust(w) for c, w in zip(cols, widths)]
    else:
        parts = [str(c) for c in cols]
    print("  " + "  ".join(parts))

# ─── login ───────────────────────────────────────────────────────────────────
print(SEP)
print("  Logging into Angel One…")
api, _, _ = get_session()
print("  Login OK\n")

all_data = {}  # { sym: { "df": df_raw, "trades": df_trades, "stats": stats, "diag": diag } }

for sym in ["NIFTY", "BANKNIFTY"]:
    cfg = config.INSTRUMENTS[sym]
    print(f"  Fetching {DAYS_BACK} calendar days for {sym}…")
    df_raw = fetch_candles(api, cfg["token"], cfg["exchange"],
                           interval="FIVE_MINUTE", days_back=DAYS_BACK)
    if df_raw.empty:
        print(f"  ERROR: No data for {sym}")
        continue
    df_raw["datetime"] = pd.to_datetime(df_raw["datetime"])
    df_raw = df_raw.sort_values("datetime").reset_index(drop=True)
    print(f"  {sym}: {len(df_raw)} candles  "
          f"({df_raw['datetime'].min().date()} to {df_raw['datetime'].max().date()})")

    trades, diag = run_backtest(df_raw, sym)
    stats = summary_stats(trades) if not trades.empty else {}
    all_data[sym] = {"df": df_raw, "trades": trades, "stats": stats, "diag": diag}

print()

# ─── helper: add indicator columns to a trades DataFrame ─────────────────────
def enrich_trades(trades: pd.DataFrame, df_raw: pd.DataFrame) -> pd.DataFrame:
    """Add ATR, day-of-week, hour, session_phase to each trade."""
    df_ind = add_indicators(df_raw.copy())
    atr_map = dict(zip(df_ind["datetime"], df_ind["atr"]))
    t = trades.copy()
    t["entry_dt"]    = pd.to_datetime(t["entry_time"])
    t["exit_dt"]     = pd.to_datetime(t["exit_time"])
    t["dow"]         = t["entry_dt"].dt.day_name()
    t["hour"]        = t["entry_dt"].dt.hour
    t["minute"]      = t["entry_dt"].dt.minute
    t["entry_atr"]   = t["entry_dt"].map(atr_map).fillna(0)
    # session phase
    def phase(row):
        t_val = row["entry_dt"].time()
        if t_val < datetime.time(10, 30): return "09:40–10:30"
        if t_val < datetime.time(12, 0):  return "10:30–12:00"
        if t_val < datetime.time(13, 0):  return "12:00–13:00"
        return "13:00–13:30"
    t["session_phase"] = t.apply(phase, axis=1)
    # hold duration in minutes
    t["hold_mins"] = ((t["exit_dt"] - t["entry_dt"]).dt.total_seconds() / 60).round(1)
    # risk (entry → initial SL)
    t["initial_risk"] = (t["entry_price"] - t["sl"]).abs().round(2)
    # R-multiple: how many R did we make/lose?
    t["r_multiple"] = (t["points"] / t["initial_risk"].replace(0, np.nan)).round(2)
    return t

# ═══════════════════════════════════════════════════════════════════════════
#  PER-SYMBOL ANALYSIS
# ═══════════════════════════════════════════════════════════════════════════

results = {}

for sym, data in all_data.items():
    trades = data["trades"]
    stats  = data["stats"]
    diag   = data["diag"]
    df_raw = data["df"]

    if trades.empty:
        print(f"{sym}: No trades — skipping")
        continue

    print(f"\n{SEP}")
    print(f"  {BLD}{sym} — 90-Day Loss Analysis{W}")
    print(SEP)

    t = enrich_trades(trades, df_raw)
    losses  = t[t["result"] == "LOSS"]
    wins    = t[t["result"] == "WIN"]
    be      = t[t["result"] == "BE"]
    total   = len(t)
    n_loss  = len(losses)
    n_win   = len(wins)

    # ── 1. HEADLINE STATS ────────────────────────────────────────────────────
    h("1. HEADLINE STATS")
    row("Total trades",     total)
    row("Wins",             f"{G}{n_win}{W}",  f"({round(n_win/total*100,1)}%)")
    row("Losses",           f"{R}{n_loss}{W}", f"({round(n_loss/total*100,1)}%)")
    row("Breakeven",        len(be))
    row("Total points",     f"{stats.get('total_points',0):+.1f}")
    row("Avg WIN",          f"{G}+{stats.get('avg_win_pts',0):.1f}{W}")
    row("Avg LOSS",         f"{R}{stats.get('avg_loss_pts',0):.1f}{W}")
    row("Risk:Reward",      stats.get("risk_reward", 0))
    row("Max drawdown",     f"{R}{stats.get('max_drawdown',0):.1f}{W}")
    row("Max consec loss",  stats.get("max_consec_loss", 0))

    # ── 2. EXIT REASON BREAKDOWN ─────────────────────────────────────────────
    h("2. EXIT REASONS (all trades)")
    er = t.groupby("exit_reason").agg(
        count=("points","count"),
        total_pts=("points","sum"),
        avg_pts=("points","mean"),
        wins=("result", lambda x: (x=="WIN").sum()),
        losses=("result", lambda x: (x=="LOSS").sum()),
    ).reset_index().sort_values("total_pts")
    row("Exit Reason".ljust(35), "N".rjust(4), "WinPts".rjust(8),
        "AvgPts".rjust(8), "W".rjust(4), "L".rjust(4))
    print("  " + "-"*65)
    for _, r in er.iterrows():
        clr = G if r["total_pts"] >= 0 else R
        row(r["exit_reason"].ljust(35),
            str(int(r["count"])).rjust(4),
            f"{clr}{r['total_pts']:+.1f}{W}".rjust(8+len(clr)+len(W)),
            f"{r['avg_pts']:+.1f}".rjust(8),
            str(int(r["wins"])).rjust(4),
            str(int(r["losses"])).rjust(4))

    # ── 3. LOSSES BY EXIT REASON ─────────────────────────────────────────────
    h("3. LOSSES — WHERE DO THEY COME FROM?")
    lr = losses.groupby("exit_reason").agg(
        count=("points","count"),
        total_pts=("points","sum"),
        avg_pts=("points","mean"),
        pct_of_total_loss=("points", lambda x: round(x.sum() / losses["points"].sum() * 100, 1))
    ).reset_index().sort_values("total_pts")
    row("Exit Reason".ljust(35), "N".rjust(4), "TotalPts".rjust(10),
        "AvgPts".rjust(8), "% of Loss$".rjust(11))
    print("  " + "-"*72)
    for _, r in lr.iterrows():
        row(r["exit_reason"].ljust(35),
            str(int(r["count"])).rjust(4),
            f"{r['total_pts']:+.1f}".rjust(10),
            f"{r['avg_pts']:+.1f}".rjust(8),
            f"{r['pct_of_total_loss']:.1f}%".rjust(11))

    # ── 4. LOSS TRADES BY DIRECTION ──────────────────────────────────────────
    h("4. LOSSES BY DIRECTION")
    for direction in ["BUY", "SELL"]:
        sub_t = t[t["direction"] == direction]
        sub_l = losses[losses["direction"] == direction]
        if sub_t.empty:
            continue
        wr = round((sub_t["result"] == "WIN").sum() / len(sub_t) * 100, 1)
        lpts = sub_l["points"].sum() if not sub_l.empty else 0
        avg_l = sub_l["points"].mean() if not sub_l.empty else 0
        row(f"{direction}:",
            f"trades={len(sub_t)}",
            f"WR={wr}%",
            f"losses={len(sub_l)}",
            f"loss_pts={lpts:.1f}",
            f"avg_loss={avg_l:.1f}")

    # ── 5. LOSSES BY SESSION PHASE ───────────────────────────────────────────
    h("5. LOSSES BY SESSION PHASE (entry time)")
    phase_data = t.groupby("session_phase").agg(
        total=("result","count"),
        wins=("result", lambda x: (x=="WIN").sum()),
        losses=("result", lambda x: (x=="LOSS").sum()),
        total_pts=("points","sum"),
        avg_pts=("points","mean"),
    ).reset_index()
    phase_data["wr"] = (phase_data["wins"] / phase_data["total"] * 100).round(1)
    row("Phase".ljust(16), "N".rjust(4), "WR%".rjust(6),
        "Wins".rjust(5), "Loss".rjust(5), "TotalPts".rjust(10), "AvgPts".rjust(8))
    print("  " + "-"*60)
    for _, r in phase_data.sort_values("session_phase").iterrows():
        clr = G if r["total_pts"] >= 0 else R
        row(r["session_phase"].ljust(16),
            str(int(r["total"])).rjust(4),
            f"{r['wr']:.1f}%".rjust(6),
            str(int(r["wins"])).rjust(5),
            str(int(r["losses"])).rjust(5),
            f"{clr}{r['total_pts']:+.1f}{W}".rjust(10),
            f"{r['avg_pts']:+.1f}".rjust(8))

    # ── 6. LOSSES BY DAY OF WEEK ─────────────────────────────────────────────
    h("6. LOSSES BY DAY OF WEEK")
    dow_order = ["Monday","Tuesday","Wednesday","Thursday","Friday"]
    dow_data = t.groupby("dow").agg(
        total=("result","count"),
        wins=("result", lambda x: (x=="WIN").sum()),
        losses=("result", lambda x: (x=="LOSS").sum()),
        total_pts=("points","sum"),
    ).reindex(dow_order).dropna()
    dow_data["wr"] = (dow_data["wins"] / dow_data["total"] * 100).round(1)
    row("Day".ljust(11), "N".rjust(4), "WR%".rjust(6),
        "Wins".rjust(5), "Loss".rjust(5), "TotalPts".rjust(10))
    print("  " + "-"*50)
    for day, r in dow_data.iterrows():
        clr = G if r["total_pts"] >= 0 else R
        row(day.ljust(11),
            str(int(r["total"])).rjust(4),
            f"{r['wr']:.1f}%".rjust(6),
            str(int(r["wins"])).rjust(5),
            str(int(r["losses"])).rjust(5),
            f"{clr}{r['total_pts']:+.1f}{W}".rjust(10))

    # ── 7. ADX AT LOSS ENTRY vs WIN ENTRY ───────────────────────────────────
    h("7. ADX AT ENTRY — Wins vs Losses")
    for result_label, subset in [("WIN", wins), ("LOSS", losses)]:
        if subset.empty: continue
        adx_vals = subset["adx"]
        row(f"{result_label} ADX:  mean={adx_vals.mean():.1f}  "
            f"median={adx_vals.median():.1f}  "
            f"min={adx_vals.min():.1f}  max={adx_vals.max():.1f}")

    # ADX bucket analysis
    sub("ADX buckets at entry:")
    t["adx_bucket"] = pd.cut(t["adx"], bins=[0,25,30,35,40,50,60,100],
                              labels=["20-25","25-30","30-35","35-40","40-50","50-60","60+"])
    adx_buck = t.groupby("adx_bucket", observed=True).agg(
        total=("result","count"),
        wins=("result", lambda x: (x=="WIN").sum()),
        losses=("result", lambda x: (x=="LOSS").sum()),
        pts=("points","sum"),
    )
    adx_buck["wr"] = (adx_buck["wins"] / adx_buck["total"] * 100).round(1)
    row("ADX bucket".ljust(10), "N".rjust(4), "WR%".rjust(6),
        "Wins".rjust(5), "Loss".rjust(5), "Pts".rjust(8))
    for bucket, r in adx_buck.iterrows():
        clr = G if r["pts"] >= 0 else R
        row(str(bucket).ljust(10),
            str(int(r["total"])).rjust(4),
            f"{r['wr']:.1f}%".rjust(6),
            str(int(r["wins"])).rjust(5),
            str(int(r["losses"])).rjust(5),
            f"{clr}{r['pts']:+.1f}{W}".rjust(8))

    # ── 8. RSI AT LOSS ENTRY ──────────────────────────────────────────────────
    h("8. RSI AT ENTRY — Wins vs Losses")
    for result_label, subset in [("WIN", wins), ("LOSS", losses)]:
        if subset.empty: continue
        rsi_vals = subset["rsi"]
        row(f"{result_label} RSI: mean={rsi_vals.mean():.1f}  "
            f"median={rsi_vals.median():.1f}  "
            f"min={rsi_vals.min():.1f}  max={rsi_vals.max():.1f}")

    t["rsi_bucket"] = pd.cut(t["rsi"], bins=[0,50,55,60,65,70,80],
                              labels=["45-50","50-55","55-60","60-65","65-70","70-80"])
    rsi_buck = t[t["direction"]=="BUY"].groupby("rsi_bucket", observed=True).agg(
        total=("result","count"),
        wins=("result", lambda x: (x=="WIN").sum()),
        losses=("result", lambda x: (x=="LOSS").sum()),
        pts=("points","sum"),
    )
    rsi_buck["wr"] = (rsi_buck["wins"] / rsi_buck["total"].replace(0,np.nan) * 100).round(1)
    sub("BUY trades — RSI bucket at entry:")
    row("RSI bucket".ljust(10), "N".rjust(4), "WR%".rjust(6),
        "Wins".rjust(5), "Loss".rjust(5), "Pts".rjust(8))
    for bucket, r in rsi_buck.iterrows():
        if r["total"] == 0: continue
        clr = G if r["pts"] >= 0 else R
        row(str(bucket).ljust(10),
            str(int(r["total"])).rjust(4),
            f"{r['wr']:.1f}%".rjust(6),
            str(int(r["wins"])).rjust(5),
            str(int(r["losses"])).rjust(5),
            f"{clr}{r['pts']:+.1f}{W}".rjust(8))

    # ── 9. HOLD DURATION ─────────────────────────────────────────────────────
    h("9. HOLD DURATION (minutes)")
    for result_label, subset in [("WIN", wins), ("LOSS", losses), ("ALL", t)]:
        if subset.empty: continue
        hm = subset["hold_mins"]
        row(f"{result_label}:  mean={hm.mean():.0f}m  "
            f"median={hm.median():.0f}m  "
            f"p25={hm.quantile(0.25):.0f}m  p75={hm.quantile(0.75):.0f}m  "
            f"max={hm.max():.0f}m")

    sub("Hold-duration buckets (loss trades):")
    if not losses.empty:
        losses_copy = losses.copy()
        losses_copy["hold_bucket"] = pd.cut(
            losses_copy["hold_mins"],
            bins=[0, 15, 30, 60, 120, 9999],
            labels=["0-15m","15-30m","30-60m","1-2h","2h+"]
        )
        hb = losses_copy.groupby("hold_bucket", observed=True).agg(
            n=("points","count"), pts=("points","sum")
        )
        for bucket, r in hb.iterrows():
            row(f"  {bucket}:  n={int(r['n'])}  pts={r['pts']:+.1f}")

    # ── 10. SL TIGHT vs WIDE ─────────────────────────────────────────────────
    h("10. INITIAL RISK SIZE — How wide was the SL at entry?")
    for result_label, subset in [("WIN", wins), ("LOSS", losses)]:
        if subset.empty: continue
        ir = subset["initial_risk"]
        row(f"{result_label} SL width: mean={ir.mean():.1f}  "
            f"median={ir.median():.1f}  "
            f"p25={ir.quantile(0.25):.1f}  p75={ir.quantile(0.75):.1f}")

    # ── 11. R-MULTIPLE DISTRIBUTION ──────────────────────────────────────────
    h("11. R-MULTIPLE DISTRIBUTION")
    t_r = t.dropna(subset=["r_multiple"])
    sub("Win R-multiples (how many R were captured on wins):")
    if not wins.empty:
        wr_vals = wins.dropna(subset=["r_multiple"])["r_multiple"]
        row(f"  mean={wr_vals.mean():.2f}R  median={wr_vals.median():.2f}R  "
            f"max={wr_vals.max():.2f}R  p25={wr_vals.quantile(0.25):.2f}R")
    sub("Loss R-multiples (how much R was lost on losses):")
    if not losses.empty:
        lr_vals = losses.dropna(subset=["r_multiple"])["r_multiple"]
        row(f"  mean={lr_vals.mean():.2f}R  median={lr_vals.median():.2f}R  "
            f"min={lr_vals.min():.2f}R  p75={lr_vals.quantile(0.75):.2f}R")

    # ── 12. VWAP PROXIMITY AT ENTRY ───────────────────────────────────────────
    h("12. VWAP PROXIMITY AT ENTRY (entry vs VWAP, pct)")
    t["vwap_dist_pct"] = ((t["entry_price"] - t["vwap"]) / t["vwap"] * 100).round(3)
    for result_label, subset in [("WIN", wins), ("LOSS", losses)]:
        if subset.empty: continue
        vd = t.loc[subset.index, "vwap_dist_pct"]
        row(f"{result_label}: mean={vd.mean():.3f}%  "
            f"median={vd.median():.3f}%  "
            f"min={vd.min():.3f}%  max={vd.max():.3f}%")

    # ── 13. WORST INDIVIDUAL LOSSES ───────────────────────────────────────────
    h("13. TOP-10 WORST INDIVIDUAL LOSS TRADES")
    worst = losses.nsmallest(10, "points")[[
        "date","direction","entry_time","entry_price","sl","exit_price",
        "exit_reason","points","rsi","adx","vwap","hold_mins","initial_risk"
    ]]
    pd.set_option("display.width", 140)
    pd.set_option("display.max_columns", 15)
    print(worst.to_string(index=False))

    # ── 14. CONSECUTIVE LOSS STREAKS ──────────────────────────────────────────
    h("14. CONSECUTIVE LOSS STREAKS")
    streaks = []
    cur = 0
    cur_start = None
    for idx2, row2 in t.iterrows():
        if row2["result"] == "LOSS":
            if cur == 0:
                cur_start = row2["entry_dt"]
            cur += 1
        else:
            if cur > 0:
                streaks.append({"length": cur, "started": cur_start})
            cur = 0
    if cur > 0:
        streaks.append({"length": cur, "started": cur_start})

    streak_df = pd.DataFrame(streaks).sort_values("length", ascending=False) if streaks else pd.DataFrame()
    if not streak_df.empty:
        row("Streak lengths distribution:")
        vc = streak_df["length"].value_counts().sort_index()
        for l, c in vc.items():
            row(f"  {l} consecutive losses: {c} times")
        print()
        row("Top 5 longest loss streaks:")
        for _, sr in streak_df.head(5).iterrows():
            row(f"  {int(sr['length'])} losses starting {sr['started'].date()}")

    # ── 15. MARKET CONTEXT AT LOSS ENTRY ─────────────────────────────────────
    h("15. MARKET CONTEXT DURING LOSSES")
    sub("Were losses clustered on specific calendar dates?")
    loss_by_date = losses.groupby("date").agg(n=("points","count"), pts=("points","sum"))
    bad_days = loss_by_date[loss_by_date["n"] >= 2].sort_values("pts")
    if not bad_days.empty:
        row("  Dates with 2+ losses:")
        for dt, r in bad_days.head(10).iterrows():
            row(f"    {dt}  losses={int(r['n'])}  pts={r['pts']:+.1f}")
    else:
        row("  No dates had 2+ losses in the same day.")

    results[sym] = t

# ═══════════════════════════════════════════════════════════════════════════
#  CROSS-SYMBOL SUMMARY
# ═══════════════════════════════════════════════════════════════════════════

print(f"\n{SEP}")
print(f"  {BLD}CROSS-SYMBOL SUMMARY — Potential Improvement Areas{W}")
print(SEP)

for sym, t in results.items():
    if t is None: continue
    losses = t[t["result"] == "LOSS"]
    wins   = t[t["result"] == "WIN"]

    print(f"\n  {BLD}{sym}:{W}")

    # Identify which session phase is drag
    phase_wr = t.groupby("session_phase").apply(
        lambda g: round((g["result"]=="WIN").sum() / len(g) * 100, 1) if len(g) else 0
    )
    for phase, wr in phase_wr.sort_values().items():
        flag = f"  {R}⚠ LOW WR{W}" if wr < 50 else (f"  {G}✓{W}" if wr >= 60 else "")
        print(f"    Phase {phase}: WR={wr:.1f}%{flag}")

    # ADX sweet spot
    t2 = t.copy()
    t2["adx_bucket"] = pd.cut(t2["adx"], bins=[0,25,30,35,40,50,60,100],
                               labels=["20-25","25-30","30-35","35-40","40-50","50-60","60+"])
    ab = t2.groupby("adx_bucket", observed=True).apply(
        lambda g: round((g["result"]=="WIN").sum() / len(g) * 100, 1) if len(g) >= 3 else None
    ).dropna()
    best_adx  = ab.idxmax() if not ab.empty else "—"
    worst_adx = ab.idxmin() if not ab.empty else "—"
    print(f"    ADX sweet spot: {G}{best_adx}{W} (WR={ab.max():.1f}%)  "
          f"worst: {R}{worst_adx}{W} (WR={ab.min():.1f}%)")

    # Dow
    dow_order = ["Monday","Tuesday","Wednesday","Thursday","Friday"]
    dow_wr = t.groupby("dow").apply(
        lambda g: round((g["result"]=="WIN").sum() / len(g) * 100, 1) if len(g) >= 3 else None
    ).reindex(dow_order).dropna()
    if not dow_wr.empty:
        best_dow  = dow_wr.idxmax()
        worst_dow = dow_wr.idxmin()
        print(f"    Best day: {G}{best_dow}{W} (WR={dow_wr.max():.1f}%)  "
              f"worst: {R}{worst_dow}{W} (WR={dow_wr.min():.1f}%)")

    # RSI sweet spot for BUY
    buy_t = t[t["direction"]=="BUY"].copy()
    if not buy_t.empty:
        buy_t["rsi_bucket"] = pd.cut(buy_t["rsi"],
                                      bins=[0,50,55,60,65,70,80],
                                      labels=["45-50","50-55","55-60","60-65","65-70","70-80"])
        rb = buy_t.groupby("rsi_bucket", observed=True).apply(
            lambda g: round((g["result"]=="WIN").sum() / len(g) * 100, 1) if len(g) >= 3 else None
        ).dropna()
        if not rb.empty:
            print(f"    BUY RSI sweet spot: {G}{rb.idxmax()}{W} (WR={rb.max():.1f}%)  "
                  f"worst: {R}{rb.idxmin()}{W} (WR={rb.min():.1f}%)")

    # Exit reason contribution to losses
    loss_er = losses.groupby("exit_reason")["points"].sum().sort_values()
    if not loss_er.empty:
        print(f"    Biggest loss contributor: {R}{loss_er.index[0]}{W} "
              f"({loss_er.iloc[0]:+.1f} pts)")

    # Hold duration comparison
    if not wins.empty and not losses.empty:
        avg_win_hold  = wins["hold_mins"].mean()
        avg_loss_hold = losses["hold_mins"].mean()
        print(f"    Avg hold: Wins={avg_win_hold:.0f}m  Losses={avg_loss_hold:.0f}m")

    # R:R summary
    avg_r_win  = wins.dropna(subset=["r_multiple"])["r_multiple"].mean() if not wins.empty else 0
    avg_r_loss = losses.dropna(subset=["r_multiple"])["r_multiple"].mean() if not losses.empty else 0
    print(f"    Avg R captured: Wins=+{avg_r_win:.2f}R  Losses={avg_r_loss:.2f}R")

print(f"\n{SEP}")
print("  ANALYSIS COMPLETE")
print(SEP)
