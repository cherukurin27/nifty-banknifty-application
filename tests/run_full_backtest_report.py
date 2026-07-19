"""
tests/run_full_backtest_report.py
Runs the full walk-forward backtest for NIFTY and BANKNIFTY across
5 time windows (10 / 20 / 30 / 60 / 90 days) and dumps all data
to JSON so the HTML report generator can read it offline.

Usage:
    python tests/run_full_backtest_report.py
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.makedirs("logs", exist_ok=True)

import json
import datetime
import pandas as pd

from feed.angel_auth import get_session
from feed.data_feed import fetch_candles
from engine.backtester import run_backtest, summary_stats
import config

PERIODS = [10, 20, 30, 60, 90]

# ── login ─────────────────────────────────────────────────────────────────────
print("Logging into Angel One...")
api, _, _ = get_session()
print("Login OK\n")

all_results   = {}   # { sym: { days: stats_dict } }
all_trades    = {}   # { sym: DataFrame of all 90-day trades }
all_by_day    = {}   # { sym: { date_str: { wins, losses, points } } }
all_by_exit   = {}   # { sym: { exit_reason: count } }
all_by_dir    = {}   # { sym: { "BUY": {...}, "SELL": {...} } }
all_monthly   = {}   # { sym: { "YYYY-MM": { trades, wins, points } } }
data_range    = {}   # { sym: { from, to, candles } }

for sym in config.INSTRUMENTS:
    cfg = config.INSTRUMENTS[sym]
    print(f"Fetching 90 days for {sym} ...")
    df_full = fetch_candles(api, cfg["token"], cfg["exchange"],
                            interval="FIVE_MINUTE", days_back=93)

    if df_full.empty:
        print(f"  ERROR: no data for {sym}")
        continue

    df_full["datetime"] = pd.to_datetime(df_full["datetime"])
    df_full = df_full.sort_values("datetime").reset_index(drop=True)
    max_date = df_full["datetime"].max()

    data_range[sym] = {
        "from"   : df_full["datetime"].min().strftime("%Y-%m-%d"),
        "to"     : max_date.strftime("%Y-%m-%d"),
        "candles": len(df_full),
    }
    print(f"  {len(df_full)} candles  |  {data_range[sym]['from']} to {data_range[sym]['to']}")

    all_results[sym] = {}

    for days in PERIODS:
        cutoff = max_date - pd.Timedelta(days=days)
        df_slice = df_full[df_full["datetime"] >= cutoff].reset_index(drop=True)
        trades, _ = run_backtest(df_slice, sym)
        stats      = summary_stats(trades) if not trades.empty else {}

        if not stats:
            stats = {
                "total_trades": 0, "wins": 0, "losses": 0, "breakeven": 0,
                "win_rate_pct": 0.0, "total_points": 0.0,
                "avg_win_pts": 0.0, "avg_loss_pts": 0.0,
                "risk_reward": 0.0, "max_consec_loss": 0, "max_drawdown": 0.0,
            }
        stats["days"] = days
        all_results[sym][days] = stats

        s = stats
        print(f"  [{days:2d}d] trades={s['total_trades']:3d}  WR={s['win_rate_pct']:5.1f}%  "
              f"pts={s['total_points']:+8.1f}  RR={s['risk_reward']:.2f}  "
              f"DD={s['max_drawdown']:+.1f}  maxCL={s['max_consec_loss']}")

    # ── full 90-day trades for drill-downs ────────────────────────────────────
    cutoff90 = max_date - pd.Timedelta(days=90)
    df90 = df_full[df_full["datetime"] >= cutoff90].reset_index(drop=True)
    trades90, _ = run_backtest(df90, sym)

    if not trades90.empty:
        all_trades[sym] = trades90

        # by-day equity curve
        trades90["date_str"] = pd.to_datetime(trades90["entry_time"]).dt.strftime("%Y-%m-%d")
        day_grp = trades90.groupby("date_str")
        all_by_day[sym] = {
            d: {
                "wins"  : int((g["result"] == "WIN").sum()),
                "losses": int((g["result"] == "LOSS").sum()),
                "points": round(g["points"].sum(), 2),
            }
            for d, g in day_grp
        }

        # exit breakdown
        all_by_exit[sym] = trades90["exit_reason"].value_counts().to_dict()

        # direction breakdown
        for direction in ["BUY", "SELL"]:
            sub = trades90[trades90["direction"] == direction]
            all_by_dir.setdefault(sym, {})[direction] = {
                "trades"       : len(sub),
                "wins"         : int((sub["result"] == "WIN").sum()),
                "losses"       : int((sub["result"] == "LOSS").sum()),
                "win_rate_pct" : round((sub["result"] == "WIN").mean() * 100, 1) if len(sub) else 0.0,
                "total_points" : round(sub["points"].sum(), 2) if len(sub) else 0.0,
                "avg_win_pts"  : round(sub.loc[sub["result"]=="WIN","points"].mean(), 2) if (sub["result"]=="WIN").any() else 0.0,
                "avg_loss_pts" : round(sub.loc[sub["result"]=="LOSS","points"].mean(), 2) if (sub["result"]=="LOSS").any() else 0.0,
            }

        # monthly summary
        trades90["month"] = pd.to_datetime(trades90["entry_time"]).dt.strftime("%Y-%m")
        mon_grp = trades90.groupby("month")
        all_monthly[sym] = {
            m: {
                "trades": len(g),
                "wins"  : int((g["result"] == "WIN").sum()),
                "losses": int((g["result"] == "LOSS").sum()),
                "points": round(g["points"].sum(), 2),
                "wr"    : round((g["result"] == "WIN").mean() * 100, 1),
            }
            for m, g in mon_grp
        }

    print()

# ── save JSON for report generator ───────────────────────────────────────────
out = {
    "generated"  : datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
    "config"     : {
        "RSI_BUY_LOW"        : config.RSI_BUY_LOW,
        "RSI_BUY_HIGH"       : config.RSI_BUY_HIGH,
        "RSI_SELL_LOW"       : config.RSI_SELL_LOW,
        "RSI_SELL_HIGH"      : config.RSI_SELL_HIGH,
        "ADX_THRESHOLD"      : config.ADX_THRESHOLD,
        "ADX_MAX"            : config.ADX_MAX,
        "ST_PERIOD"          : config.ST_PERIOD,
        "ST_MULTIPLIER"      : config.ST_MULTIPLIER,
        "NO_NEW_ENTRY_AFTER" : config.NO_NEW_ENTRY_AFTER,
        "SESSION_START"      : config.SESSION_START,
        "SESSION_END"        : config.SESSION_END,
        "FORCE_EXIT"         : config.FORCE_EXIT,
        "MAX_TRADES_PER_SYMBOL": config.MAX_TRADES_PER_SYMBOL,
        "SL_CAP_NIFTY"       : config.SL_CAP_PTS.get("NIFTY"),
        "ATR_SL_MULT_BANKNIFTY": config.ATR_SL_MULT_BANKNIFTY,
        "SKIP_EXPIRY_DAY"    : {k: v for k, v in config.SKIP_EXPIRY_DAY.items()},
    },
    "data_range"  : data_range,
    "period_stats": {sym: {str(d): v for d, v in inner.items()} for sym, inner in all_results.items()},
    "by_day"      : all_by_day,
    "by_exit"     : all_by_exit,
    "by_direction": all_by_dir,
    "monthly"     : all_monthly,
}

json_path = "logs/backtest_report_data.json"
with open(json_path, "w") as f:
    json.dump(out, f, indent=2, default=str)

print(f"\nData saved → {json_path}")
print("DONE")
