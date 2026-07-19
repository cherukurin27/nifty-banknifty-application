"""
tests/analyse_bnkn_losses.py
Deep analysis of all BankNifty 90-day losses — no speculation, only data.

Slices the 90-day trade list and measures loss concentration across:
  - Hour of entry
  - Day of week
  - Direction (BUY / SELL)
  - ADX bucket at entry
  - RSI bucket at entry
  - Exit reason
  - Points bucket (small loss vs large loss)
  - VWAP distance at entry

Outputs JSON to logs/bnkn_loss_analysis.json
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.makedirs("logs", exist_ok=True)

import json
import datetime
import numpy as np
import pandas as pd

from feed.angel_auth import get_session
from feed.data_feed import fetch_candles
from engine.backtester import run_backtest
import config

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
print(f"  {len(df_full)} candles  {df_full['datetime'].min().date()} to {max_date.date()}\n")

cutoff = max_date - pd.Timedelta(days=90)
df90 = df_full[df_full["datetime"] >= cutoff].reset_index(drop=True)
trades, _ = run_backtest(df90, "BANKNIFTY")
print(f"Total trades: {len(trades)}  |  Wins: {(trades['result']=='WIN').sum()}  |  Losses: {(trades['result']=='LOSS').sum()}\n")

wins   = trades[trades["result"] == "WIN"].copy()
losses = trades[trades["result"] == "LOSS"].copy()

# Add derived columns
for df in [trades, wins, losses]:
    df["entry_hour"]    = pd.to_datetime(df["entry_time"]).dt.hour
    df["entry_minute"]  = pd.to_datetime(df["entry_time"]).dt.minute
    df["entry_hhmm"]    = pd.to_datetime(df["entry_time"]).dt.strftime("%H:%M")
    df["entry_weekday"] = pd.to_datetime(df["entry_time"]).dt.day_name()
    df["adx_bucket"]    = pd.cut(df["adx"],  bins=[0,20,25,30,35,40], labels=["<20","20-25","25-30","30-35","35-40"])
    df["rsi_bucket"]    = pd.cut(df["rsi"],  bins=[0,53,56,59,62,65], labels=["53-56","56-59","59-62","62-65","65+"])
    df["points_bucket"] = pd.cut(df["points"].abs(), bins=[0,50,100,150,200,300,9999],
                                  labels=["0-50","50-100","100-150","150-200","200-300","300+"])

# ── Hour of entry ─────────────────────────────────────────────────────────────
print("=== LOSS CONCENTRATION BY ENTRY HOUR ===")
hour_stats = {}
for h in sorted(losses["entry_hour"].unique()):
    l_cnt  = (losses["entry_hour"] == h).sum()
    w_cnt  = (wins["entry_hour"]   == h).sum()
    total  = l_cnt + w_cnt
    wr     = round(w_cnt / total * 100, 1) if total else 0
    lpts   = round(losses.loc[losses["entry_hour"]==h, "points"].sum(), 1)
    wpts   = round(wins.loc[wins["entry_hour"]==h,     "points"].sum(), 1)
    hour_stats[h] = {"wins": int(w_cnt), "losses": int(l_cnt), "total": int(total),
                     "win_rate": wr, "loss_pts": lpts, "win_pts": wpts, "net": round(lpts+wpts,1)}
    print(f"  {h:02d}:xx  trades={total:3d}  W={w_cnt:2d}  L={l_cnt:2d}  WR={wr:5.1f}%  net={lpts+wpts:+8.1f}")

# ── 30-min slot ───────────────────────────────────────────────────────────────
print("\n=== LOSS CONCENTRATION BY 30-MIN SLOT ===")
trades["slot"] = pd.to_datetime(trades["entry_time"]).dt.floor("30min").dt.strftime("%H:%M")
slot_stats = {}
for slot, grp in trades.groupby("slot"):
    w = (grp["result"]=="WIN").sum(); l = (grp["result"]=="LOSS").sum()
    wr = round(w/len(grp)*100,1) if len(grp) else 0
    net = round(grp["points"].sum(),1)
    slot_stats[slot] = {"wins":int(w),"losses":int(l),"total":len(grp),"win_rate":wr,"net":net}
    print(f"  {slot}  trades={len(grp):3d}  W={w:2d}  L={l:2d}  WR={wr:5.1f}%  net={net:+8.1f}")

# ── Day of week ───────────────────────────────────────────────────────────────
print("\n=== LOSS CONCENTRATION BY DAY OF WEEK ===")
dow_order = ["Monday","Tuesday","Wednesday","Thursday","Friday"]
dow_stats = {}
for day in dow_order:
    l_cnt = (losses["entry_weekday"]==day).sum()
    w_cnt = (wins["entry_weekday"]==day).sum()
    total = l_cnt + w_cnt
    wr    = round(w_cnt/total*100,1) if total else 0
    lpts  = round(losses.loc[losses["entry_weekday"]==day,"points"].sum(),1)
    wpts  = round(wins.loc[wins["entry_weekday"]==day,"points"].sum(),1)
    dow_stats[day] = {"wins":int(w_cnt),"losses":int(l_cnt),"total":int(total),
                      "win_rate":wr,"net":round(lpts+wpts,1)}
    print(f"  {day:12s}  trades={total:3d}  W={w_cnt:2d}  L={l_cnt:2d}  WR={wr:5.1f}%  net={lpts+wpts:+8.1f}")

# ── Direction ─────────────────────────────────────────────────────────────────
print("\n=== DIRECTION BREAKDOWN ===")
dir_stats = {}
for d in ["BUY","SELL"]:
    sub = trades[trades["direction"]==d]
    w = (sub["result"]=="WIN").sum(); l = (sub["result"]=="LOSS").sum()
    wr = round(w/len(sub)*100,1) if len(sub) else 0
    net = round(sub["points"].sum(),1)
    aw = round(sub.loc[sub["result"]=="WIN","points"].mean(),1) if w else 0
    al = round(sub.loc[sub["result"]=="LOSS","points"].mean(),1) if l else 0
    dir_stats[d] = {"wins":int(w),"losses":int(l),"total":len(sub),"win_rate":wr,
                    "net":net,"avg_win":aw,"avg_loss":al}
    print(f"  {d:4s}  trades={len(sub):3d}  W={w:2d}  L={l:2d}  WR={wr:5.1f}%  avgW={aw:+.1f}  avgL={al:+.1f}  net={net:+8.1f}")

# ── ADX bucket ────────────────────────────────────────────────────────────────
print("\n=== LOSS CONCENTRATION BY ADX AT ENTRY ===")
adx_stats = {}
for bucket in ["<20","20-25","25-30","30-35","35-40"]:
    l_cnt = (losses["adx_bucket"]==bucket).sum()
    w_cnt = (wins["adx_bucket"]==bucket).sum()
    total = l_cnt + w_cnt
    wr    = round(w_cnt/total*100,1) if total else 0
    lpts  = round(losses.loc[losses["adx_bucket"]==bucket,"points"].sum(),1)
    wpts  = round(wins.loc[wins["adx_bucket"]==bucket,"points"].sum(),1)
    adx_stats[bucket] = {"wins":int(w_cnt),"losses":int(l_cnt),"total":int(total),
                          "win_rate":wr,"net":round(lpts+wpts,1)}
    print(f"  ADX {bucket:7s}  trades={total:3d}  W={w_cnt:2d}  L={l_cnt:2d}  WR={wr:5.1f}%  net={lpts+wpts:+8.1f}")

# ── RSI bucket ────────────────────────────────────────────────────────────────
print("\n=== LOSS CONCENTRATION BY RSI AT ENTRY (BUY only) ===")
buy_trades = trades[trades["direction"]=="BUY"]
rsi_stats = {}
for bucket in ["53-56","56-59","59-62","62-65"]:
    l_cnt = ((buy_trades["result"]=="LOSS") & (buy_trades["rsi_bucket"]==bucket)).sum()
    w_cnt = ((buy_trades["result"]=="WIN")  & (buy_trades["rsi_bucket"]==bucket)).sum()
    total = l_cnt + w_cnt
    wr    = round(w_cnt/total*100,1) if total else 0
    lpts  = round(buy_trades.loc[(buy_trades["result"]=="LOSS")&(buy_trades["rsi_bucket"]==bucket),"points"].sum(),1)
    wpts  = round(buy_trades.loc[(buy_trades["result"]=="WIN") &(buy_trades["rsi_bucket"]==bucket),"points"].sum(),1)
    rsi_stats[bucket] = {"wins":int(w_cnt),"losses":int(l_cnt),"total":int(total),
                          "win_rate":wr,"net":round(lpts+wpts,1)}
    print(f"  RSI BUY {bucket:7s}  trades={total:3d}  W={w_cnt:2d}  L={l_cnt:2d}  WR={wr:5.1f}%  net={lpts+wpts:+8.1f}")

# ── Exit reason ───────────────────────────────────────────────────────────────
print("\n=== EXIT REASON BREAKDOWN (losses only) ===")
exit_stats = losses["exit_reason"].value_counts().to_dict()
for k, v in sorted(exit_stats.items(), key=lambda x: -x[1]):
    pts = round(losses.loc[losses["exit_reason"]==k,"points"].sum(),1)
    print(f"  {k:40s}  count={v:3d}  total_pts={pts:+8.1f}")

# ── Loss size distribution ────────────────────────────────────────────────────
print("\n=== LOSS SIZE DISTRIBUTION ===")
loss_size_stats = {}
for bucket in ["0-50","50-100","100-150","150-200","200-300","300+"]:
    cnt = (losses["points_bucket"]==bucket).sum()
    pts = round(losses.loc[losses["points_bucket"]==bucket,"points"].sum(),1)
    loss_size_stats[bucket] = {"count":int(cnt),"total_pts":pts}
    print(f"  {bucket:10s}  count={cnt:3d}  total_pts={pts:+8.1f}")

# ── Large losses deep dive (> 150 pts) ────────────────────────────────────────
print("\n=== LARGE LOSSES > 150 pts ===")
large_losses = losses[losses["points"] < -150].sort_values("points")
large_loss_list = []
for _, row in large_losses.iterrows():
    print(f"  {str(row['date']):12s}  {row['direction']:4s}  entry={row['entry_price']:8.2f}  "
          f"exit={row['exit_price']:8.2f}  pts={row['points']:+8.1f}  "
          f"ADX={row['adx']:5.1f}  RSI={row['rsi']:5.1f}  "
          f"exit={row['exit_reason']}")
    large_loss_list.append({
        "date": str(row["date"]), "direction": row["direction"],
        "entry": float(row["entry_price"]), "exit": float(row["exit_price"]),
        "points": float(row["points"]), "adx": float(row["adx"]),
        "rsi": float(row["rsi"]), "exit_reason": row["exit_reason"],
        "entry_time": str(row["entry_time"]),
    })

# ── Candidate filters to test ─────────────────────────────────────────────────
print("\n=== CANDIDATE IMPROVEMENTS TO TEST ===")
# What % of losses come from BUY direction?
buy_loss_pct = round(len(losses[losses["direction"]=="BUY"]) / len(losses) * 100, 1)
sell_loss_pct = round(len(losses[losses["direction"]=="SELL"]) / len(losses) * 100, 1)
print(f"  BUY  losses: {len(losses[losses['direction']=='BUY']):2d}  ({buy_loss_pct:.1f}% of all losses)")
print(f"  SELL losses: {len(losses[losses['direction']=='SELL']):2d}  ({sell_loss_pct:.1f}% of all losses)")

# Which hour has worst WR?
worst_hours = sorted([(h, v["win_rate"]) for h,v in hour_stats.items() if v["total"]>=3], key=lambda x: x[1])
print(f"\n  Worst WR hours (min 3 trades): {worst_hours[:3]}")

# Which day has worst WR?
worst_days = sorted([(d, v["win_rate"]) for d,v in dow_stats.items() if v["total"]>=3], key=lambda x: x[1])
print(f"  Worst WR days  (min 3 trades): {worst_days[:3]}")

# ADX sweet spot
best_adx = sorted([(b, v["win_rate"]) for b,v in adx_stats.items() if v["total"]>=3], key=lambda x: -x[1])
print(f"  Best ADX buckets (min 3 trades): {best_adx[:3]}")

# ── Save JSON ─────────────────────────────────────────────────────────────────
out = {
    "generated"         : datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
    "total_trades"      : len(trades),
    "total_wins"        : int((trades["result"]=="WIN").sum()),
    "total_losses"      : int((trades["result"]=="LOSS").sum()),
    "by_hour"           : {str(k): v for k,v in hour_stats.items()},
    "by_30min_slot"     : slot_stats,
    "by_dow"            : dow_stats,
    "by_direction"      : dir_stats,
    "by_adx"            : adx_stats,
    "by_rsi_buy"        : rsi_stats,
    "by_exit_loss"      : exit_stats,
    "loss_size"         : loss_size_stats,
    "large_losses"      : large_loss_list,
    "all_trades"        : trades[["date","direction","entry_time","entry_price","exit_price",
                                   "points","result","adx","rsi","vwap","exit_reason",
                                   "entry_hour","entry_weekday"]].to_dict(orient="records"),
}

json_path = "logs/bnkn_loss_analysis.json"
with open(json_path, "w") as f:
    json.dump(out, f, indent=2, default=str)
print(f"\nSaved to {json_path}")
print("DONE")
