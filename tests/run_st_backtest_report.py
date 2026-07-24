"""
tests/run_st_backtest_report.py
Fetches historical Angel One data for all 6 stock instruments,
runs the walk-forward stock price backtest, and writes a self-contained HTML report.

Usage:
    # Default: 90-day backtest, all 6 stocks, report saved to project root
    python tests/run_st_backtest_report.py

    # Custom: 180 days, selected stocks only
    python tests/run_st_backtest_report.py --days 180 --symbols HDFCBANK ICICIBANK SBIN

    # Short run for a quick sanity check
    python tests/run_st_backtest_report.py --days 30

Arguments:
    --days N          Calendar days of history to fetch (default: 90)
    --symbols S ...   Space-separated list of stock symbols to include
                      (default: all 6 in STOCK_OPTIONS_INSTRUMENTS)
    --output PATH     Output HTML file path (default: st-options-backtest-report.html)

Output:
    st-options-backtest-report.html  (or --output path)
    Console summary printed while running.
"""

from __future__ import annotations
import argparse
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import config
from feed.angel_auth import get_session
from feed.data_feed import fetch_candles
from engine.st_backtester import run_stock_backtest, stock_summary_stats

# ─── CLI defaults ─────────────────────────────────────────────────────────────
DEFAULT_DAYS    = 90
DEFAULT_OUTPUT  = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),
    "st-options-backtest-report.html",
)
PERIODS         = [10, 20, 30, 60, 90, 180]


# ─── analytics helpers ────────────────────────────────────────────────────────

def _stats_for_period(trades_full, days: int) -> dict:
    """Slice the full trade log to the last `days` calendar days and compute stats."""
    import pandas as pd
    if trades_full.empty:
        return {}
    max_date = pd.to_datetime(trades_full["date"].astype(str)).max().date()
    cutoff   = (max_date - datetime.timedelta(days=days)).isoformat()
    sub      = trades_full[trades_full["date"].astype(str) >= cutoff]
    s        = stock_summary_stats(sub)
    if s:
        s["days"] = days
    return s or {}


def _by_direction(trades_full) -> dict:
    """Win/loss/points breakdown per direction (BUY / SELL)."""
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
        result[direction] = {
            "trades": len(sub), "wins": w, "losses": l,
            "win_rate_pct": round(w / len(sub) * 100, 1) if len(sub) else 0,
            "total_pts": pts, "avg_win_pts": aw, "avg_loss_pts": al,
        }
    return result


def _by_exit(trades_full) -> dict:
    return trades_full["exit_reason"].value_counts().to_dict()


def _monthly(trades_full) -> dict:
    """Per-calendar-month P&L breakdown."""
    import pandas as pd
    t = trades_full.copy()
    t["month"] = t["date"].astype(str).str[:7]
    result = {}
    for month, grp in t.groupby("month"):
        w   = int((grp["result"] == "WIN").sum())
        pts = round(float(grp["points"].sum()), 2)
        result[month] = {
            "trades": len(grp), "wins": w,
            "losses": int((grp["result"] == "LOSS").sum()),
            "pts"   : pts,
            "wr"    : round(w / len(grp) * 100, 1) if len(grp) else 0,
        }
    return result


def _weekly(trades_full) -> dict:
    """Per-calendar-week P&L breakdown."""
    import pandas as pd
    t = trades_full.copy()
    t["_date"] = pd.to_datetime(t["date"].astype(str), errors="coerce")
    t["week"]  = t["_date"].dt.to_period("W").astype(str)
    result = {}
    for week, grp in t.groupby("week"):
        w   = int((grp["result"] == "WIN").sum())
        pts = round(float(grp["points"].sum()), 2)
        buy  = int((grp["direction"] == "BUY").sum())
        sell = int((grp["direction"] == "SELL").sum())
        result[week] = {
            "trades": len(grp), "wins": w,
            "losses": int((grp["result"] == "LOSS").sum()),
            "buy": buy, "sell": sell, "pts": pts,
            "wr": round(w / len(grp) * 100, 1) if len(grp) else 0,
        }
    return result


def _entry_slot_analysis(trades_full) -> dict:
    """30-min entry time slot Win Rate analysis."""
    import pandas as pd
    result = {}
    for _, row in trades_full.iterrows():
        et = row.get("entry_time", "")
        if not et:
            continue
        try:
            t   = pd.to_datetime(str(et))
            bm  = (t.minute // 30) * 30
            slot = f"{t.hour:02d}:{bm:02d}"
        except Exception:
            continue
        if slot not in result:
            result[slot] = {"buy_win": 0, "buy_loss": 0,
                            "sell_win": 0, "sell_loss": 0, "pts": 0.0}
        d   = row.get("direction", "")
        res = row.get("result", "")
        pts = float(row.get("points", 0) or 0)
        result[slot]["pts"] = round(result[slot]["pts"] + pts, 2)
        if d == "BUY"  and res == "WIN":    result[slot]["buy_win"]   += 1
        elif d == "BUY"  and res == "LOSS": result[slot]["buy_loss"]  += 1
        elif d == "SELL" and res == "WIN":  result[slot]["sell_win"]  += 1
        elif d == "SELL" and res == "LOSS": result[slot]["sell_loss"] += 1
    return result


def _strategy_breakdown(trades_full) -> dict:
    """Count BUY/SELL wins+losses per sig_reason (strategy combination)."""
    result = {}
    for _, row in trades_full.iterrows():
        reason = str(row.get("sig_reason", "Unknown"))[:60]
        if reason not in result:
            result[reason] = {"trades": 0, "wins": 0, "pts": 0.0}
        result[reason]["trades"] += 1
        if row.get("result") == "WIN":
            result[reason]["wins"] += 1
        result[reason]["pts"] = round(
            result[reason]["pts"] + float(row.get("points", 0) or 0), 2
        )
    return dict(sorted(result.items(), key=lambda x: -x[1]["trades"]))


def _equity_points(trades_full) -> list[float]:
    """Return sorted cumulative points list for SVG sparkline."""
    import pandas as pd
    t = trades_full.sort_values("entry_time").reset_index(drop=True)
    cum, running = [], 0.0
    for _, row in t.iterrows():
        running += float(row.get("points", 0) or 0)
        cum.append(running)
    return cum


# ─── HTML builder ─────────────────────────────────────────────────────────────

def _html(data: dict) -> str:
    syms   = list(data["results"].keys())
    rp     = data["report_period"]
    rpd    = f"{rp}d"
    gen    = data["generated"]
    days   = data["days_fetched"]

    # ── KPI cards ────────────────────────────────────────────────────────────
    def kpi_cards(sym):
        s = data["results"][sym]["period_stats"].get(str(rp), {})
        if not s:
            return "<p class='muted'>No trades in this period.</p>"
        wr  = s.get("win_rate_pct", 0)
        pts = s.get("total_points", 0)
        pf  = s.get("profit_factor", 0)
        exp = s.get("expectancy", 0)
        mdd = s.get("max_drawdown", 0)
        mcl = s.get("max_consec_loss", 0)
        rr  = s.get("risk_reward", 0)
        tot = s.get("total_trades", 0)
        w   = s.get("wins", 0)
        l   = s.get("losses", 0)
        pts_clr = "#2a7a2a" if pts >= 0 else "#b22222"
        wr_clr  = "#2a7a2a" if wr >= 55 else ("#b07000" if wr >= 45 else "#b22222")
        pf_str  = f"{pf:.2f}" if pf != float("inf") else "∞"
        pf_clr  = "#2a7a2a" if (isinstance(pf, float) and pf >= 1.5) else (
                  "#b07000" if (isinstance(pf, float) and pf >= 1.0) else "#b22222")
        exp_clr = "#2a7a2a" if exp >= 0 else "#b22222"
        return f"""
        <div class="kpi-row">
          <div class="kpi"><div class="kpi-val">{tot}</div><div class="kpi-lbl">Trades ({rpd})</div></div>
          <div class="kpi"><div class="kpi-val" style="color:{wr_clr}">{wr}%</div><div class="kpi-lbl">Win Rate</div></div>
          <div class="kpi"><div class="kpi-val" style="color:{pts_clr}">{pts:+.2f}</div><div class="kpi-lbl">Net Points</div></div>
          <div class="kpi"><div class="kpi-val" style="color:{wr_clr}">{w}W / {l}L</div><div class="kpi-lbl">Wins / Losses</div></div>
          <div class="kpi"><div class="kpi-val">{rr}</div><div class="kpi-lbl">Risk : Reward</div></div>
          <div class="kpi"><div class="kpi-val" style="color:{pf_clr}">{pf_str}</div><div class="kpi-lbl">Profit Factor</div></div>
          <div class="kpi"><div class="kpi-val" style="color:{exp_clr}">{exp:+.2f}</div><div class="kpi-lbl">Expectancy (pts)</div></div>
          <div class="kpi"><div class="kpi-val">{mdd:.2f}</div><div class="kpi-lbl">Max Drawdown</div></div>
          <div class="kpi"><div class="kpi-val">{mcl}</div><div class="kpi-lbl">Max Consec Loss</div></div>
        </div>"""

    # ── period slice table ────────────────────────────────────────────────────
    def period_table(sym):
        ps   = data["results"][sym]["period_stats"]
        rows = ""
        for p in PERIODS:
            s = ps.get(str(p), {})
            if not s:
                continue
            pts   = s.get("total_points", 0)
            pf    = s.get("profit_factor", 0)
            exp   = s.get("expectancy", 0)
            pc    = "pos" if pts >= 0 else "neg"
            pfc   = "pos" if (isinstance(pf, float) and pf >= 1.5) else (
                    ""    if (isinstance(pf, float) and pf >= 1.0) else "neg")
            expc  = "pos" if exp >= 0 else "neg"
            pf_s  = f"{pf:.2f}" if pf != float("inf") else "∞"
            rows += (f"<tr><td>{p}d</td><td>{s.get('total_trades',0)}</td>"
                     f"<td>{s.get('win_rate_pct',0)}%</td>"
                     f"<td class='{pc}'>{pts:+.2f}</td>"
                     f"<td>{s.get('avg_win_pts',0):.2f}</td>"
                     f"<td>{s.get('avg_loss_pts',0):.2f}</td>"
                     f"<td>{s.get('risk_reward',0)}</td>"
                     f"<td>{s.get('max_drawdown',0):.2f}</td>"
                     f"<td>{s.get('max_consec_loss',0)}</td>"
                     f"<td class='{pfc}'>{pf_s}</td>"
                     f"<td class='{expc}'>{exp:+.2f}</td></tr>")
        return f"""
        <table><thead><tr>
          <th>Period</th><th>Trades</th><th>WR</th><th>Net Points</th>
          <th>Avg Win</th><th>Avg Loss</th><th>RR</th><th>MDD</th><th>MCL</th>
          <th>PF</th><th>Exp</th>
        </tr></thead><tbody>{rows}</tbody></table>"""

    # ── BUY vs SELL table ─────────────────────────────────────────────────────
    def direction_table(sym):
        bd   = data["results"][sym]["by_direction"]
        rows = ""
        for d in ["BUY", "SELL"]:
            s = bd.get(d, {})
            if not s:
                continue
            c = "pos" if s.get("total_pts", 0) >= 0 else "neg"
            rows += (f"<tr><td><b>{d}</b></td><td>{s.get('trades',0)}</td>"
                     f"<td>{s.get('win_rate_pct',0)}%</td>"
                     f"<td class='{c}'>{s.get('total_pts',0):+.2f}</td>"
                     f"<td>{s.get('avg_win_pts',0):.2f}</td>"
                     f"<td>{s.get('avg_loss_pts',0):.2f}</td></tr>")
        return f"""
        <table><thead><tr>
          <th>Direction</th><th>Trades</th><th>WR</th><th>Net Pts</th>
          <th>Avg Win</th><th>Avg Loss</th>
        </tr></thead><tbody>{rows}</tbody></table>"""

    # ── weekly table ──────────────────────────────────────────────────────────
    def weekly_table(sym):
        wk   = data["results"][sym].get("weekly", {})
        rows = ""
        for week in sorted(wk.keys()):
            s   = wk[week]
            c   = "pos" if s.get("pts", 0) >= 0 else "neg"
            wrc = "pos" if s.get("wr", 0) >= 55 else ("" if s.get("wr", 0) >= 45 else "neg")
            rows += (f"<tr><td>{week}</td><td>{s.get('trades',0)}</td>"
                     f"<td>{s.get('buy',0)} BUY / {s.get('sell',0)} SELL</td>"
                     f"<td>{s.get('wins',0)}W / {s.get('losses',0)}L</td>"
                     f"<td class='{wrc}'>{s.get('wr',0)}%</td>"
                     f"<td class='{c}'>{s.get('pts',0):+.2f}</td></tr>")
        return f"""
        <table><thead><tr>
          <th>Week</th><th>Trades</th><th>BUY/SELL</th><th>W/L</th><th>WR</th><th>Net Pts</th>
        </tr></thead><tbody>{rows}</tbody></table>"""

    # ── monthly table ─────────────────────────────────────────────────────────
    def monthly_table(sym):
        m    = data["results"][sym].get("monthly", {})
        rows = ""
        for month in sorted(m.keys()):
            s   = m[month]
            c   = "pos" if s.get("pts", 0) >= 0 else "neg"
            wrc = "pos" if s.get("wr", 0) >= 55 else ("" if s.get("wr", 0) >= 45 else "neg")
            rows += (f"<tr><td>{month}</td><td>{s.get('trades',0)}</td>"
                     f"<td>{s.get('wins',0)}W / {s.get('losses',0)}L</td>"
                     f"<td class='{wrc}'>{s.get('wr',0)}%</td>"
                     f"<td class='{c}'>{s.get('pts',0):+.2f}</td></tr>")
        return f"""
        <table><thead><tr>
          <th>Month</th><th>Trades</th><th>W/L</th><th>WR</th><th>Net Pts</th>
        </tr></thead><tbody>{rows}</tbody></table>"""

    # ── entry slot heatmap ────────────────────────────────────────────────────
    def slot_table(sym):
        slots = data["results"][sym].get("entry_slots", {})
        if not slots:
            return "<p class='muted'>No entry time data.</p>"
        rows = ""
        for slot in sorted(slots.keys()):
            s     = slots[slot]
            total = s["buy_win"] + s["buy_loss"] + s["sell_win"] + s["sell_loss"]
            wins  = s["buy_win"] + s["sell_win"]
            wr    = round(wins / total * 100, 1) if total else 0
            c     = "pos" if s["pts"] >= 0 else "neg"
            wrc   = "pos" if wr >= 60 else ("" if wr >= 45 else "neg")
            rows += (f"<tr><td>{slot}</td><td>{total}</td>"
                     f"<td class='pos'>{s['buy_win']}</td><td class='neg'>{s['buy_loss']}</td>"
                     f"<td class='pos'>{s['sell_win']}</td><td class='neg'>{s['sell_loss']}</td>"
                     f"<td class='{wrc}'>{wr}%</td>"
                     f"<td class='{c}'>{s['pts']:+.2f}</td></tr>")
        return f"""
        <table><thead><tr>
          <th>Entry Slot</th><th>Total</th>
          <th>BUY W</th><th>BUY L</th><th>SELL W</th><th>SELL L</th>
          <th>WR</th><th>Net Pts</th>
        </tr></thead><tbody>{rows}</tbody></table>"""

    # ── strategy breakdown table ──────────────────────────────────────────────
    def strategy_table(sym):
        sb   = data["results"][sym].get("strategy_breakdown", {})
        rows = ""
        for reason, s in list(sb.items())[:20]:   # top 20
            w   = s.get("wins", 0)
            t   = s.get("trades", 0)
            wr  = round(w / t * 100, 1) if t else 0
            pts = s.get("pts", 0)
            c   = "pos" if pts >= 0 else "neg"
            wrc = "pos" if wr >= 55 else ("" if wr >= 45 else "neg")
            rows += (f"<tr><td>{reason}</td><td>{t}</td>"
                     f"<td class='{wrc}'>{wr}%</td>"
                     f"<td class='{c}'>{pts:+.2f}</td></tr>")
        return f"""
        <table><thead><tr>
          <th>Signal Reason (Strategy Combo)</th><th>Trades</th><th>WR</th><th>Net Pts</th>
        </tr></thead><tbody>{rows}</tbody></table>"""

    # ── exit reason table ─────────────────────────────────────────────────────
    def exit_table(sym):
        be   = data["results"][sym].get("by_exit", {})
        rows = "".join(f"<tr><td>{k}</td><td>{v}</td></tr>"
                       for k, v in sorted(be.items(), key=lambda x: -x[1]))
        return f"""
        <table><thead><tr>
          <th>Exit Reason</th><th>Count</th>
        </tr></thead><tbody>{rows}</tbody></table>"""

    # ── equity SVG sparkline ──────────────────────────────────────────────────
    def equity_svg(sym):
        cum = data["results"][sym].get("equity_curve", [])
        if len(cum) < 2:
            return ""
        mn, mx = min(cum), max(cum)
        rng    = mx - mn if mx != mn else 1
        W, H   = 700, 120
        pts_str = " ".join(
            f"{round(i / (len(cum) - 1) * W, 1)}"
            f",{round(H - (v - mn) / rng * (H - 10) - 5, 1)}"
            for i, v in enumerate(cum)
        )
        color = "#2a7a2a" if cum[-1] >= 0 else "#b22222"
        zero_y = round(H - (0 - mn) / rng * (H - 10) - 5, 1) if mn < 0 < mx else None
        zero_line = (f'<line x1="0" y1="{zero_y}" x2="{W}" y2="{zero_y}" '
                     f'stroke="#aaa" stroke-width="1" stroke-dasharray="4,3"/>') if zero_y else ""
        return (f'<svg viewBox="0 0 {W} {H}" '
                f'style="width:100%;max-width:{W}px;height:{H}px;display:block;margin:8px 0">'
                f'{zero_line}'
                f'<polyline points="{pts_str}" fill="none" stroke="{color}" stroke-width="2"/>'
                f'</svg>')

    # ── all trades table ──────────────────────────────────────────────────────
    def trades_table(sym):
        trades = data["results"][sym].get("all_trades", [])
        if not trades:
            return "<p class='muted'>No trades.</p>"
        rows = ""
        for t in sorted(trades, key=lambda x: str(x.get("entry_time", ""))):
            rc  = "pos" if t.get("result") == "WIN" else "neg"
            pts = t.get("points", 0)
            rows += (f"<tr>"
                     f"<td>{str(t.get('date',''))[:10]}</td>"
                     f"<td>{t.get('direction','')}</td>"
                     f"<td>{str(t.get('entry_time',''))[11:16]}</td>"
                     f"<td>{t.get('entry_price','')}</td>"
                     f"<td>{t.get('sl','')}</td>"
                     f"<td>{str(t.get('exit_time',''))[11:16]}</td>"
                     f"<td>{t.get('exit_price','')}</td>"
                     f"<td>{t.get('exit_reason','')}</td>"
                     f"<td class='{rc}'>{pts:+.2f}</td>"
                     f"<td>{str(t.get('sig_reason',''))[:40]}</td>"
                     f"</tr>")
        return f"""
        <div style="overflow-x:auto">
        <table class="trade-tbl"><thead><tr>
          <th>Date</th><th>Dir</th><th>Entry T</th><th>Entry Px</th>
          <th>SL</th><th>Exit T</th><th>Exit Px</th><th>Reason</th>
          <th>Points</th><th>Signal</th>
        </tr></thead><tbody>{rows}</tbody></table></div>"""

    # ── per-symbol sections ───────────────────────────────────────────────────
    sections = ""
    for sym in syms:
        dr    = data["data_range"].get(sym, {})
        cfg_s = config.STOCK_OPTIONS_INSTRUMENTS.get(sym, {})
        strats = ", ".join(cfg_s.get("strategy", []))
        sections += f"""
        <section id="{sym.lower()}">
          <h2>{sym}</h2>
          <p class="muted">
            Strategies: <b>{strats}</b> &nbsp;|&nbsp;
            SL: <b>2&times;ATR(14)</b> &nbsp;|&nbsp;
            Exit: <b>SL hit &middot; reverse signal &middot; EOD 15:15</b> &nbsp;|&nbsp;
            Data: {dr.get('from','')} to {dr.get('to','')} &mdash; {dr.get('candles',0):,} candles
          </p>
          {kpi_cards(sym)}
          <h3>Period Slices</h3>{period_table(sym)}
          <h3>BUY vs SELL Breakdown ({rpd})</h3>{direction_table(sym)}
          <h3>Monthly Breakdown</h3>{monthly_table(sym)}
          <h3>Weekly Breakdown</h3>{weekly_table(sym)}
          <h3>Entry Time Slot Analysis</h3>{slot_table(sym)}
          <h3>Signal / Strategy Breakdown (top 20)</h3>{strategy_table(sym)}
          <h3>Exit Reasons</h3>{exit_table(sym)}
          <h3>Equity Curve (cumulative points)</h3>{equity_svg(sym)}
          <h3>All Trades</h3>{trades_table(sym)}
        </section>"""

    # ── config section ────────────────────────────────────────────────────────
    cfg_rows = "".join(f"<tr><td>{k}</td><td>{v}</td></tr>"
                       for k, v in data["config_summary"].items())
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
<title>St Price Backtest Report &mdash; {rpd}</title>
<style>
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{font-family:-apple-system,"Segoe UI",system-ui,sans-serif;font-size:14px;
        line-height:1.6;background:#fff;color:#1f2328}}
  .wrap{{max-width:960px;margin:0 auto;padding:24px 16px}}
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
  section{{margin-bottom:48px}}
  .trade-tbl td,.trade-tbl th{{font-size:12px;padding:4px 6px}}
  footer{{margin-top:40px;padding-top:12px;border-top:1px solid #e5e7eb;
          text-align:center;font-size:12px;color:#57606a}}
</style>
</head>
<body>
<div class="wrap">
  <h1>St Price Backtest Report &mdash; {rpd}</h1>
  <p class="muted">
    Generated: {gen} &nbsp;|&nbsp; {days}-day fetch, 5-min candles, Angel One live data
    &nbsp;|&nbsp; SL = 2&times;ATR(14) &nbsp;|&nbsp; Exit: SL hit &middot; reverse signal &middot; EOD 15:15
  </p>
  <nav>{nav_links} <a href="#config">Config</a></nav>
  {sections}
  {cfg_section}
  <footer>Made with IBM Bob</footer>
</div>
</body>
</html>"""


# ─── main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Run st price backtest and generate an HTML report.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--days",    type=int, default=DEFAULT_DAYS,
                        help=f"Calendar days of history (default: {DEFAULT_DAYS})")
    parser.add_argument("--symbols", nargs="+",
                        default=list(config.STOCK_OPTIONS_INSTRUMENTS.keys()),
                        help="Stock symbols to backtest (default: all 6)")
    parser.add_argument("--output",  default=DEFAULT_OUTPUT,
                        help=f"Output HTML path (default: {DEFAULT_OUTPUT})")
    args = parser.parse_args()

    days        = args.days
    syms_to_run = [s.upper() for s in args.symbols]
    output_path = args.output

    # Validate symbol names
    valid = set(config.STOCK_OPTIONS_INSTRUMENTS.keys())
    bad   = [s for s in syms_to_run if s not in valid]
    if bad:
        print(f"ERROR: Unknown symbols: {bad}")
        print(f"Valid symbols: {sorted(valid)}")
        sys.exit(1)

    # Report period = largest standard period that fits within days fetched
    report_period = max((p for p in PERIODS if p <= days), default=days)

    print("=" * 64)
    print("  St Price Backtest Report Generator")
    print(f"  Symbols : {syms_to_run}")
    print(f"  Fetch   : {days} calendar days")
    print(f"  Report  : {report_period}-day period slice highlighted")
    print(f"  Output  : {output_path}")
    print("=" * 64)

    # ── Angel One login ───────────────────────────────────────────────────────
    print("\n  Logging into Angel One ...", end=" ", flush=True)
    try:
        api, _, _ = get_session()
        print("OK")
    except Exception as exc:
        print(f"FAILED\n  Error: {exc}")
        sys.exit(1)

    results    = {}
    data_range = {}
    fetch_days = days + 3   # small over-fetch for indicator warmup

    for sym in syms_to_run:
        cfg_s   = config.STOCK_OPTIONS_INSTRUMENTS[sym]
        chunks  = max(1, fetch_days // 30 + (1 if fetch_days % 30 else 0))
        print(f"\n  [{sym}] Fetching {days}d ({chunks} API call{'s' if chunks>1 else ''}) ...",
              end=" ", flush=True)

        df = fetch_candles(api, cfg_s["token"], cfg_s["exchange"],
                           interval="FIVE_MINUTE", days_back=fetch_days)
        if df.empty:
            print("NO DATA — skipping")
            continue
        print(f"{len(df)} candles  ({str(df['datetime'].min())[:10]} to {str(df['datetime'].max())[:10]})")

        data_range[sym] = {
            "from"   : str(df["datetime"].min())[:10],
            "to"     : str(df["datetime"].max())[:10],
            "candles": len(df),
        }

        print(f"  [{sym}] Running backtest ...", end=" ", flush=True)
        trades, _diag = run_stock_backtest(df, sym)
        print(f"{len(trades)} trades")

        if trades.empty:
            results[sym] = {
                "period_stats": {}, "by_direction": {}, "by_exit": {},
                "monthly": {}, "weekly": {}, "entry_slots": {},
                "strategy_breakdown": {}, "equity_curve": [], "all_trades": [],
            }
            print(f"  [{sym}] WARNING: No trades generated — check data quality or strategy thresholds.")
            continue

        # Coerce datetime columns to strings for JSON / HTML
        for col in ["entry_time", "exit_time", "date"]:
            if col in trades.columns:
                trades[col] = trades[col].astype(str)

        # Period stats
        ps = {str(p): _stats_for_period(trades, p) for p in PERIODS}

        # Print console summary for report period
        s_rp = ps.get(str(report_period), {})
        if s_rp:
            pf_disp = (f"{s_rp.get('profit_factor',0):.2f}"
                       if s_rp.get("profit_factor", 0) != float("inf") else "∞")
            print(f"  [{sym}] {report_period}d -> "
                  f"{s_rp.get('total_trades',0)} trades | "
                  f"WR {s_rp.get('win_rate_pct',0)}% | "
                  f"Net {s_rp.get('total_points',0):+.2f} pts | "
                  f"PF {pf_disp} | "
                  f"Exp {s_rp.get('expectancy',0):+.2f} pts/trade | "
                  f"MDD {s_rp.get('max_drawdown',0):.2f}")

        results[sym] = {
            "period_stats"      : ps,
            "by_direction"      : _by_direction(trades),
            "by_exit"           : _by_exit(trades),
            "monthly"           : _monthly(trades),
            "weekly"            : _weekly(trades),
            "entry_slots"       : _entry_slot_analysis(trades),
            "strategy_breakdown": _strategy_breakdown(trades),
            "equity_curve"      : _equity_points(trades),
            "all_trades"        : json.loads(
                trades.to_json(orient="records", default_handler=str)
            ),
        }

    if not results:
        print("\n  No results to write — all symbols failed.")
        sys.exit(1)

    # ── Build and write HTML ──────────────────────────────────────────────────
    from engine.st_signal_engine import (
        STOCK_ADX_MIN, STOCK_ADX_MAX, STOCK_ADX_DEAD_LOW, STOCK_ADX_DEAD_HIGH,
        STOCK_RSI_BUY_LOW, STOCK_RSI_BUY_HIGH, STOCK_RSI_SELL_LOW, STOCK_RSI_SELL_HIGH,
    )
    data = {
        "generated"     : datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "days_fetched"  : days,
        "report_period" : report_period,
        "config_summary": {
            "SL"                    : "2 × ATR(14)  +  Supertrend trailing SL",
            "Exit rules"            : "SL hit (trail) · reverse signal · EOD 15:15 IST",
            "SESSION_START"         : "09:30",
            "NO_NEW_ENTRY_AFTER"    : "13:30",
            "FORCE_EXIT"            : "15:15 IST",
            "MIN_SCORE"             : "2 of 4 strategies must agree",
            "Gate 1 — Supertrend"   : "Supertrend(7,3): BUY=GREEN(+1), SELL=RED(-1)",
            "Gate 2 — EMA21"        : "BUY: close > EMA21 | SELL: close < EMA21",
            "Gate 3 — ADX range"    : f"ADX(14) must be {STOCK_ADX_MIN}–{STOCK_ADX_MAX}",
            "Gate 4 — ADX dead zone": f"ADX {STOCK_ADX_DEAD_LOW}–{STOCK_ADX_DEAD_HIGH} blocked (exhausted trend)",
            "Gate 5 — RSI range"    : f"BUY: RSI {STOCK_RSI_BUY_LOW}–{STOCK_RSI_BUY_HIGH} | SELL: RSI {STOCK_RSI_SELL_LOW}–{STOCK_RSI_SELL_HIGH}",
            "VWAP volume filter"    : "volume >= avg_volume (1.0×) required for VWAP pullback",
            "Dead zones"              : "HDFCBANK/ICICIBANK: block <10:00",
            "HDFCBANK_strategies"     : str(config.STOCK_OPTIONS_INSTRUMENTS.get("HDFCBANK",{}).get("strategy",[])),
            "ICICIBANK_strategies"    : str(config.STOCK_OPTIONS_INSTRUMENTS.get("ICICIBANK",{}).get("strategy",[])),
            "RELIANCE_strategies"     : str(config.STOCK_OPTIONS_INSTRUMENTS.get("RELIANCE",{}).get("strategy",[])),
            "INFY_strategies"         : str(config.STOCK_OPTIONS_INSTRUMENTS.get("INFY",{}).get("strategy",[])),
            "BHARTIARTL_strategies"   : str(config.STOCK_OPTIONS_INSTRUMENTS.get("BHARTIARTL",{}).get("strategy",[])),
            "BAJFINANCE_strategies"   : str(config.STOCK_OPTIONS_INSTRUMENTS.get("BAJFINANCE",{}).get("strategy",[])),
        },
        "data_range" : data_range,
        "results"    : results,
    }

    html = _html(data)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"\n  Report saved -> {output_path}")
    print("  Open in a browser to view results.\n")


if __name__ == "__main__":
    main()
