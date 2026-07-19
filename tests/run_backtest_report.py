"""
tests/run_backtest_report.py
Fetches 90 days of live Angel One data for NIFTY and BANKNIFTY,
runs the full walk-forward backtest, and writes a self-contained HTML
report to nifty-bank-nifty-full-backtest-report.html in the project root.

Usage:
    python tests/run_backtest_report.py
"""

from __future__ import annotations
import sys, os, datetime, json
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import config
from feed.angel_auth import get_session
from feed.data_feed import fetch_candles
from engine.backtester import run_backtest, summary_stats

DAYS_BACK   = 93        # slight over-fetch to absorb indicator warmup
PERIODS     = [10, 20, 30, 60, 90]
INSTRUMENTS = config.INSTRUMENTS   # NIFTY + BANKNIFTY only
OUTPUT      = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                           "nifty-bank-nifty-full-backtest-report.html")


# ─── helpers ─────────────────────────────────────────────────────────────────

def _stats_for_period(trades_full, p):
    import pandas as pd
    max_date = trades_full["date"].max()
    cutoff   = (datetime.date.fromisoformat(max_date) - datetime.timedelta(days=p)).isoformat()
    sub      = trades_full[trades_full["date"] >= cutoff]
    s        = summary_stats(sub)
    if s:
        s["days"] = p
    return s or {}


def _by_direction(trades_full):
    result = {}
    for direction in ["BUY", "SELL"]:
        sub = trades_full[trades_full["direction"] == direction]
        if sub.empty:
            continue
        w   = int((sub["result"] == "WIN").sum())
        l   = int((sub["result"] == "LOSS").sum())
        pts = round(float(sub["points"].sum()), 2)
        aw  = round(float(sub.loc[sub["result"] == "WIN",  "points"].mean() or 0), 2)
        al  = round(float(sub.loc[sub["result"] == "LOSS", "points"].mean() or 0), 2)
        result[direction] = {"trades": len(sub), "wins": w, "losses": l,
                             "win_rate_pct": round(w / len(sub) * 100, 1),
                             "total_points": pts, "avg_win_pts": aw, "avg_loss_pts": al}
    return result


def _monthly(trades_full):
    import pandas as pd
    trades_full = trades_full.copy()
    trades_full["month"] = trades_full["date"].astype(str).str[:7]
    result = {}
    for month, grp in trades_full.groupby("month"):
        w = int((grp["result"] == "WIN").sum())
        result[month] = {
            "trades": len(grp), "wins": w,
            "losses": int((grp["result"] == "LOSS").sum()),
            "points": round(float(grp["points"].sum()), 2),
            "wr": round(w / len(grp) * 100, 1),
        }
    return result


def _exit_time_analysis(trades_full):
    """
    Bucket exits into 30-min time slots.
    Returns list of dicts: {slot, buy_win, buy_loss, sell_win, sell_loss, net_pts}
    Only looks at the exit time (HH:MM) of each trade.
    """
    import pandas as pd

    result = {}
    for _, row in trades_full.iterrows():
        et = row.get("exit_time", "")
        if not et:
            continue
        # exit_time may be a string like "2026-04-21 09:45:00" or a Timestamp
        try:
            t = pd.to_datetime(str(et))
            # round down to 30-min bucket
            bucket_min = (t.minute // 30) * 30
            slot = f"{t.hour:02d}:{bucket_min:02d}"
        except Exception:
            continue
        if slot not in result:
            result[slot] = {"buy_win": 0, "buy_loss": 0,
                            "sell_win": 0, "sell_loss": 0, "net_pts": 0.0}
        d   = row.get("direction", "")
        res = row.get("result", "")
        pts = float(row.get("points", 0) or 0)
        result[slot]["net_pts"] = round(result[slot]["net_pts"] + pts, 2)
        if d == "BUY"  and res == "WIN":   result[slot]["buy_win"]  += 1
        elif d == "BUY"  and res == "LOSS": result[slot]["buy_loss"] += 1
        elif d == "SELL" and res == "WIN":  result[slot]["sell_win"] += 1
        elif d == "SELL" and res == "LOSS": result[slot]["sell_loss"] += 1
    return result


# ─── HTML builder ─────────────────────────────────────────────────────────────

def _html(data: dict) -> str:
    cfg  = data["config"]
    syms = list(data["results"].keys())

    # ── KPI cards per symbol ─────────────────────────────────────────────────
    def kpi_cards(sym):
        s = data["results"][sym]["period_stats"].get("90", {})
        if not s:
            return "<p>No trades in period.</p>"
        wr   = s.get("win_rate_pct", 0)
        pts  = s.get("total_points", 0)
        rr   = s.get("risk_reward", 0)
        mdd  = s.get("max_drawdown", 0)
        mcl  = s.get("max_consec_loss", 0)
        tot  = s.get("total_trades", 0)
        color = "#2a7a2a" if pts >= 0 else "#b22222"
        return f"""
        <div class="kpi-row">
          <div class="kpi"><div class="kpi-val">{tot}</div><div class="kpi-lbl">Trades (90d)</div></div>
          <div class="kpi"><div class="kpi-val">{wr}%</div><div class="kpi-lbl">Win Rate</div></div>
          <div class="kpi"><div class="kpi-val" style="color:{color}">{pts:+.0f}</div><div class="kpi-lbl">Net Points</div></div>
          <div class="kpi"><div class="kpi-val">{rr}</div><div class="kpi-lbl">Risk:Reward</div></div>
          <div class="kpi"><div class="kpi-val">{mdd:.0f}</div><div class="kpi-lbl">Max Drawdown</div></div>
          <div class="kpi"><div class="kpi-val">{mcl}</div><div class="kpi-lbl">Max Consec Loss</div></div>
        </div>"""

    # ── period slice table ────────────────────────────────────────────────────
    def period_table(sym):
        ps = data["results"][sym]["period_stats"]
        rows = ""
        for p in PERIODS:
            s = ps.get(str(p), {})
            if not s:
                continue
            color = "pos" if s.get("total_points", 0) >= 0 else "neg"
            rows += (f"<tr><td>{p}d</td><td>{s.get('total_trades',0)}</td>"
                     f"<td>{s.get('win_rate_pct',0)}%</td>"
                     f"<td class='{color}'>{s.get('total_points',0):+.1f}</td>"
                     f"<td>{s.get('avg_win_pts',0):.1f}</td>"
                     f"<td>{s.get('avg_loss_pts',0):.1f}</td>"
                     f"<td>{s.get('risk_reward',0)}</td>"
                     f"<td>{s.get('max_drawdown',0):.1f}</td>"
                     f"<td>{s.get('max_consec_loss',0)}</td></tr>")
        return f"""
        <table><thead><tr>
          <th>Period</th><th>Trades</th><th>WR</th><th>Net Pts</th>
          <th>Avg Win</th><th>Avg Loss</th><th>RR</th><th>MDD</th><th>MCL</th>
        </tr></thead><tbody>{rows}</tbody></table>"""

    # ── buy/sell table ────────────────────────────────────────────────────────
    def direction_table(sym):
        bd = data["results"][sym]["by_direction"]
        rows = ""
        for d in ["BUY", "SELL"]:
            s = bd.get(d, {})
            if not s:
                continue
            color = "pos" if s.get("total_points", 0) >= 0 else "neg"
            rows += (f"<tr><td><b>{d}</b></td><td>{s.get('trades',0)}</td>"
                     f"<td>{s.get('win_rate_pct',0)}%</td>"
                     f"<td class='{color}'>{s.get('total_points',0):+.1f}</td>"
                     f"<td>{s.get('avg_win_pts',0):.1f}</td>"
                     f"<td>{s.get('avg_loss_pts',0):.1f}</td></tr>")
        return f"""
        <table><thead><tr>
          <th>Side</th><th>Trades</th><th>WR</th><th>Net Pts</th><th>Avg Win</th><th>Avg Loss</th>
        </tr></thead><tbody>{rows}</tbody></table>"""

    # ── monthly table ─────────────────────────────────────────────────────────
    def monthly_table(sym):
        m = data["results"][sym]["monthly"]
        rows = ""
        for month in sorted(m):
            s = m[month]
            color = "pos" if s.get("points", 0) >= 0 else "neg"
            rows += (f"<tr><td>{month}</td><td>{s.get('trades',0)}</td>"
                     f"<td>{s.get('wins',0)}W / {s.get('losses',0)}L</td>"
                     f"<td>{s.get('wr',0)}%</td>"
                     f"<td class='{color}'>{s.get('points',0):+.1f}</td></tr>")
        return f"""
        <table><thead><tr>
          <th>Month</th><th>Trades</th><th>W/L</th><th>WR</th><th>Net Pts</th>
        </tr></thead><tbody>{rows}</tbody></table>"""

    # ── exit reason table ─────────────────────────────────────────────────────
    def exit_table(sym):
        be = data["results"][sym]["by_exit"]
        rows = "".join(f"<tr><td>{k}</td><td>{v}</td></tr>" for k, v in sorted(be.items(), key=lambda x: -x[1]))
        return f"<table><thead><tr><th>Exit Reason</th><th>Count</th></tr></thead><tbody>{rows}</tbody></table>"

    # ── exit time-of-day analysis table ──────────────────────────────────────
    def exit_time_table(sym):
        eta = data["results"][sym].get("exit_time_analysis", {})
        if not eta:
            return "<p>No exit time data.</p>"
        rows = ""
        for slot in sorted(eta.keys()):
            s       = eta[slot]
            total   = s["buy_win"] + s["buy_loss"] + s["sell_win"] + s["sell_loss"]
            net_pts = s["net_pts"]
            color   = "pos" if net_pts >= 0 else "neg"
            rows += (f"<tr>"
                     f"<td>{slot}</td>"
                     f"<td>{total}</td>"
                     f"<td class='pos'>{s['buy_win']}</td>"
                     f"<td class='neg'>{s['buy_loss']}</td>"
                     f"<td class='pos'>{s['sell_win']}</td>"
                     f"<td class='neg'>{s['sell_loss']}</td>"
                     f"<td class='{color}'>{net_pts:+.1f}</td>"
                     f"</tr>")
        return (f"<table><thead><tr>"
                f"<th>Exit Time</th><th>Total</th>"
                f"<th>BUY W</th><th>BUY L</th>"
                f"<th>SELL W</th><th>SELL L</th>"
                f"<th>Net Pts</th>"
                f"</tr></thead><tbody>{rows}</tbody></table>")

    # ── equity curve (SVG sparkline) ──────────────────────────────────────────
    def equity_svg(sym):
        trades = data["results"][sym].get("all_trades", [])
        if not trades:
            return ""
        cum, running = [], 0.0
        for t in sorted(trades, key=lambda x: x.get("entry_time", "")):
            running += t.get("points", 0)
            cum.append(running)
        if len(cum) < 2:
            return ""
        mn, mx = min(cum), max(cum)
        rng = mx - mn if mx != mn else 1
        W, H = 600, 120
        pts_str = " ".join(
            f"{round(i / (len(cum)-1) * W, 1)},{round(H - (v - mn) / rng * (H - 10) - 5, 1)}"
            for i, v in enumerate(cum)
        )
        color = "#2a7a2a" if cum[-1] >= 0 else "#b22222"
        return (f'<svg viewBox="0 0 {W} {H}" style="width:100%;max-width:{W}px;height:{H}px;display:block;margin:8px 0">'
                f'<polyline points="{pts_str}" fill="none" stroke="{color}" stroke-width="2"/>'
                f'</svg>')

    # ── all-trades table ──────────────────────────────────────────────────────
    def trades_table(sym):
        trades = data["results"][sym].get("all_trades", [])
        if not trades:
            return "<p>No trades.</p>"
        rows = ""
        for t in sorted(trades, key=lambda x: x.get("entry_time", "")):
            res_cls = "pos" if t.get("result") == "WIN" else "neg"
            pts     = t.get("points", 0)
            rows += (f"<tr>"
                     f"<td>{str(t.get('date',''))[:10]}</td>"
                     f"<td>{t.get('direction','')}</td>"
                     f"<td>{str(t.get('entry_time',''))[11:16]}</td>"
                     f"<td>{t.get('entry_price','')}</td>"
                     f"<td>{t.get('sl','')}</td>"
                     f"<td>{str(t.get('exit_time',''))[11:16]}</td>"
                     f"<td>{t.get('exit_price','')}</td>"
                     f"<td>{t.get('exit_reason','')}</td>"
                     f"<td class='{res_cls}'>{pts:+.1f}</td>"
                     f"<td>{t.get('rsi','')}</td>"
                     f"<td>{t.get('adx','')}</td>"
                     f"</tr>")
        return f"""
        <div style="overflow-x:auto">
        <table class="trade-tbl"><thead><tr>
          <th>Date</th><th>Side</th><th>Entry T</th><th>Entry</th><th>SL</th>
          <th>Exit T</th><th>Exit</th><th>Reason</th><th>Pts</th><th>RSI</th><th>ADX</th>
        </tr></thead><tbody>{rows}</tbody></table></div>"""

    # ── symbol sections ───────────────────────────────────────────────────────
    sections = ""
    for sym in syms:
        dr = data["data_range"].get(sym, {})
        sections += f"""
        <section id="{sym.lower()}">
          <h2>{sym}</h2>
          <p class="muted">Data: {dr.get('from','')} to {dr.get('to','')} &mdash; {dr.get('candles',0):,} candles</p>
          {kpi_cards(sym)}
          <h3>Period Slices</h3>{period_table(sym)}
          <h3>BUY vs SELL (90d)</h3>{direction_table(sym)}
          <h3>Monthly Breakdown</h3>{monthly_table(sym)}
          <h3>Exit Reasons (90d)</h3>{exit_table(sym)}
          <h3>Exit Time-of-Day Analysis</h3>{exit_time_table(sym)}
          <h3>Equity Curve (all trades)</h3>{equity_svg(sym)}
          <h3>All Trades</h3>{trades_table(sym)}
        </section>"""

    # ── config summary ────────────────────────────────────────────────────────
    cfg_rows = "".join(f"<tr><td>{k}</td><td>{v}</td></tr>" for k, v in cfg.items())
    cfg_section = f"""
    <section id="config">
      <h2>Active Strategy Config</h2>
      <table><thead><tr><th>Parameter</th><th>Value</th></tr></thead>
      <tbody>{cfg_rows}</tbody></table>
    </section>"""

    nav_links = "".join(f'<a href="#{s.lower()}">{s}</a>' for s in syms)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Nifty / BankNifty Backtest Report</title>
<style>
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{font-family:-apple-system,"Segoe UI",system-ui,sans-serif;font-size:14px;
        line-height:1.6;background:#fff;color:#1f2328}}
  .wrap{{max-width:900px;margin:0 auto;padding:24px 16px}}
  h1{{font-size:22px;margin-bottom:4px}}
  h2{{font-size:17px;margin:28px 0 8px;border-bottom:1px solid #e5e7eb;padding-bottom:4px}}
  h3{{font-size:14px;font-weight:600;margin:18px 0 6px;color:#444}}
  .muted{{color:#57606a;font-size:13px;margin-bottom:8px}}
  nav{{background:#f7f8fa;border:1px solid #e5e7eb;border-radius:6px;
       padding:10px 14px;margin-bottom:24px;display:flex;gap:14px;flex-wrap:wrap}}
  nav a{{color:#3b82d4;text-decoration:none;font-weight:500}}
  nav a:hover{{text-decoration:underline}}
  table{{width:100%;border-collapse:collapse;font-size:13px;margin-bottom:10px}}
  th{{background:#f7f8fa;text-align:left;padding:6px 8px;border:1px solid #e5e7eb;
      font-weight:600;white-space:nowrap}}
  td{{padding:5px 8px;border:1px solid #e5e7eb;white-space:nowrap}}
  tr:nth-child(even) td{{background:#fafafa}}
  .pos{{color:#2a7a2a;font-weight:600}}
  .neg{{color:#b22222;font-weight:600}}
  .kpi-row{{display:flex;flex-wrap:wrap;gap:10px;margin:10px 0 18px}}
  .kpi{{background:#f7f8fa;border:1px solid #e5e7eb;border-radius:6px;
        padding:10px 16px;min-width:110px;text-align:center}}
  .kpi-val{{font-size:20px;font-weight:700;color:#1f2328}}
  .kpi-lbl{{font-size:11px;color:#57606a;margin-top:2px}}
  section{{margin-bottom:40px}}
  .trade-tbl td,.trade-tbl th{{font-size:12px;padding:4px 6px}}
  footer{{margin-top:40px;padding-top:12px;border-top:1px solid #e5e7eb;
          text-align:center;font-size:12px;color:#57606a}}
</style>
</head>
<body>
<div class="wrap">
  <h1>Nifty / BankNifty Backtest Report</h1>
  <p class="muted">Generated: {data['generated']} &nbsp;|&nbsp; 90-day walk-forward, 5-min candles, Angel One live data</p>
  <nav>{nav_links} <a href="#config">Config</a></nav>
  {sections}
  {cfg_section}
  <footer>Made with IBM Bob</footer>
</div>
</body>
</html>"""


# ─── main ─────────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("  Nifty / BankNifty Backtest Report Generator")
    print(f"  Instruments: {list(INSTRUMENTS.keys())}")
    print("=" * 60)

    api, _, _ = get_session()
    print("  Login OK\n")

    results    = {}
    data_range = {}

    for sym, cfg_inst in INSTRUMENTS.items():
        print(f"  Fetching {DAYS_BACK}d candles for {sym} ...", end=" ", flush=True)
        df = fetch_candles(api, cfg_inst["token"], cfg_inst["exchange"], days_back=DAYS_BACK)
        if df.empty:
            print("NO DATA - skipping")
            continue
        print(f"{len(df)} candles")

        data_range[sym] = {
            "from"   : str(df["datetime"].min())[:10],
            "to"     : str(df["datetime"].max())[:10],
            "candles": len(df),
        }

        print(f"  Running backtest for {sym} ...", end=" ", flush=True)
        trades_full, _ = run_backtest(df, sym)
        print(f"{len(trades_full)} trades")

        if trades_full.empty:
            results[sym] = {"period_stats": {}, "by_direction": {},
                            "by_exit": {}, "monthly": {}, "all_trades": []}
            continue

        trades_full["date"]       = trades_full["date"].astype(str)
        trades_full["entry_time"] = trades_full["entry_time"].astype(str)
        trades_full["exit_time"]  = trades_full["exit_time"].astype(str)

        ps = {str(p): _stats_for_period(trades_full, p) for p in PERIODS}
        s90 = ps.get("90", {})
        if s90:
            print(f"    90d -> {s90.get('total_trades',0)} trades | "
                  f"WR {s90.get('win_rate_pct',0)}% | "
                  f"Net {s90.get('total_points',0):+.1f} pts | "
                  f"RR {s90.get('risk_reward',0)} | "
                  f"MDD {s90.get('max_drawdown',0):.1f}")

        results[sym] = {
            "period_stats"     : ps,
            "by_direction"     : _by_direction(trades_full),
            "by_exit"          : trades_full["exit_reason"].value_counts().to_dict(),
            "monthly"          : _monthly(trades_full),
            "exit_time_analysis": _exit_time_analysis(trades_full),
            "all_trades"       : json.loads(trades_full.to_json(orient="records", default_handler=str)),
        }
        print()

    data = {
        "generated" : datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "config"    : {
            "RSI_BUY_LOW"              : config.RSI_BUY_LOW,
            "RSI_BUY_HIGH"             : config.RSI_BUY_HIGH,
            "RSI_BUY_HIGH_BNKN"        : config.RSI_BUY_HIGH_BANKNIFTY,
            "RSI_SELL_LOW"             : config.RSI_SELL_LOW,
            "RSI_SELL_HIGH"            : config.RSI_SELL_HIGH,
            "ADX_THRESHOLD"            : config.ADX_THRESHOLD,
            "ADX_MAX"                  : config.ADX_MAX,
            "ST_PERIOD"                : config.ST_PERIOD,
            "ST_MULTIPLIER"            : config.ST_MULTIPLIER,
            "NO_NEW_ENTRY_AFTER"       : config.NO_NEW_ENTRY_AFTER,
            "SESSION_START"            : config.SESSION_START,
            "SESSION_END"              : config.SESSION_END,
            "FORCE_EXIT"               : config.FORCE_EXIT,
            "MAX_TRADES_PER_SYMBOL"    : config.MAX_TRADES_PER_SYMBOL,
            "SL_CAP_NIFTY_PTS"         : config.SL_CAP_PTS.get("NIFTY"),
            "ATR_SL_MULT_BANKNIFTY"    : config.ATR_SL_MULT_BANKNIFTY,
            "BNKN_DEAD_ZONE"           : f"{config.BNKN_SKIP_SLOT_START}-{config.BNKN_SKIP_SLOT_END}",
            "SKIP_EXPIRY_NIFTY"        : "Thursday",
            "SKIP_EXPIRY_BANKNIFTY"    : "None",
            "EOD_SLIPPAGE_NIFTY"       : getattr(config, "EOD_SLIPPAGE_PTS", {}).get("NIFTY", 0),
            "EOD_SLIPPAGE_BNKN"        : getattr(config, "EOD_SLIPPAGE_PTS", {}).get("BANKNIFTY", 0),
        },
        "data_range": data_range,
        "results"   : results,
    }

    html = _html(data)
    with open(OUTPUT, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"  Report saved to: {OUTPUT}")
    print("  Open in a browser to view.\n")


if __name__ == "__main__":
    main()
