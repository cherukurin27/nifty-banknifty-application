"""
backtest_app.py — Streamlit backtest dashboard.
Fetches last N days of 5-min data from Angel One and runs the strategy.

Run with:
    streamlit run backtest_app.py
"""

from __future__ import annotations
import os, sys
import datetime
import pandas as pd
import streamlit as st

sys.path.insert(0, os.path.dirname(__file__))

import config
from feed.angel_auth import get_session
from feed.data_feed import fetch_candles
from engine.backtester import run_backtest, summary_stats

# ─── Page config ─────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Backtest — Nifty & Bank Nifty",
    page_icon="🔬",
    layout="wide",
)

# ─── CSS ─────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
body, .main { background:#0f1117; color:#e6edf3; }
.stat-card {
    background:#1a1d26; border:1px solid #30363d; border-radius:8px;
    padding:14px 18px; text-align:center;
}
.stat-val  { font-size:26px; font-weight:700; margin-bottom:2px; }
.stat-lbl  { font-size:11px; color:#8b949e; }
.win-clr   { color:#22c55e; }
.loss-clr  { color:#ef4444; }
.neu-clr   { color:#3b82d4; }
.warn-clr  { color:#f59e0b; }
table      { width:100%; }
thead th   { background:#1a1d26 !important; }
</style>
""", unsafe_allow_html=True)

# ─── Header ──────────────────────────────────────────────────────────────────
st.markdown("## 🔬 Backtest — Nifty & Bank Nifty")
st.markdown(
    "<span style='color:#8b949e;font-size:13px;'>"
    "Strategy: <b>Supertrend(7,3) + EMA(9/21) + RSI(14) + VWAP + ADX&gt;25</b>"
    " &nbsp;|&nbsp; Timeframe: <b>5-min candles</b>"
    " &nbsp;|&nbsp; Session: <b>09:30 – 14:30 IST</b>"
    "</span>",
    unsafe_allow_html=True,
)
st.markdown("---")

# ─── Controls ────────────────────────────────────────────────────────────────
c1, c2, c3 = st.columns([1, 1, 2])
with c1:
    days_back = st.selectbox("Historical data (trading days)", [5, 7, 10, 15, 20], index=1)
with c2:
    symbols_sel = st.multiselect(
        "Symbols", list(config.INSTRUMENTS.keys()),
        default=list(config.INSTRUMENTS.keys()),
    )
with c3:
    st.markdown("<br>", unsafe_allow_html=True)
    run_btn = st.button("▶ Run Backtest", type="primary", use_container_width=False)

# ─── Session state ────────────────────────────────────────────────────────────
if "bt_results" not in st.session_state:
    st.session_state.bt_results = {}
if "bt_api" not in st.session_state:
    st.session_state.bt_api = None

# ─── Login helper ─────────────────────────────────────────────────────────────
def ensure_api():
    if st.session_state.bt_api is None:
        with st.spinner("Logging into Angel One…"):
            try:
                api, _, _ = get_session()
                st.session_state.bt_api = api
            except Exception as exc:
                st.error(f"Login failed: {exc}")
                st.stop()
    return st.session_state.bt_api

# ─── Run backtest ─────────────────────────────────────────────────────────────
if run_btn:
    if not symbols_sel:
        st.warning("Select at least one symbol.")
        st.stop()

    api = ensure_api()
    st.session_state.bt_results = {}

    for sym in symbols_sel:
        cfg = config.INSTRUMENTS[sym]
        with st.spinner(f"Fetching {days_back} days of 5-min data for {sym}…"):
            df_raw = fetch_candles(
                api, cfg["token"], cfg["exchange"],
                interval="FIVE_MINUTE",
                days_back=days_back + 3,   # +3 for weekends/holidays
            )

        if df_raw.empty:
            st.error(f"No data returned for {sym}. Check market hours or token.")
            continue

        # Keep only session candles for display purposes
        df_raw["datetime"] = pd.to_datetime(df_raw["datetime"])

        with st.spinner(f"Running backtest for {sym}…"):
            trades = run_backtest(df_raw, sym)
            stats  = summary_stats(trades) if not trades.empty else {}

        st.session_state.bt_results[sym] = {
            "trades" : trades,
            "stats"  : stats,
            "df_raw" : df_raw,
        }

# ─── Display results ─────────────────────────────────────────────────────────
if st.session_state.bt_results:
    for sym, res in st.session_state.bt_results.items():
        trades = res["trades"]
        stats  = res["stats"]
        df_raw = res["df_raw"]

        st.markdown(f"### {sym} — {config.INSTRUMENTS[sym]['symbol']}")

        if trades.empty or not stats:
            st.info(f"No signals generated for {sym} in this period. "
                    "Market may have been sideways (ADX < 25) or no 4-filter confirmation occurred.")
            st.markdown("---")
            continue

        # ── Summary stat cards ────────────────────────────────────────────────
        s = stats
        win_clr  = "win-clr"  if s["total_points"] >= 0 else "loss-clr"
        wr_clr   = "win-clr"  if s["win_rate_pct"] >= 55  else ("warn-clr" if s["win_rate_pct"] >= 45 else "loss-clr")

        cols = st.columns(9)
        cards = [
            ("Total Trades",     s["total_trades"],      "neu-clr"),
            ("Wins",             s["wins"],               "win-clr"),
            ("Losses",           s["losses"],             "loss-clr"),
            ("Breakeven",        s["breakeven"],          "neu-clr"),
            ("Win Rate",         f"{s['win_rate_pct']}%", wr_clr),
            ("Total Points",     s["total_points"],       win_clr),
            ("Avg Win (pts)",    s["avg_win_pts"],        "win-clr"),
            ("Avg Loss (pts)",   s["avg_loss_pts"],       "loss-clr"),
            ("Risk:Reward",      s["risk_reward"],        "neu-clr"),
        ]
        for col, (lbl, val, clr) in zip(cols, cards):
            col.markdown(
                f"<div class='stat-card'>"
                f"<div class='stat-val {clr}'>{val}</div>"
                f"<div class='stat-lbl'>{lbl}</div>"
                f"</div>",
                unsafe_allow_html=True,
            )

        st.markdown("<br>", unsafe_allow_html=True)

        # ── Equity curve ─────────────────────────────────────────────────────
        eq = trades["points"].cumsum().reset_index(drop=True)
        eq_df = pd.DataFrame({"Cumulative Points": eq})
        st.markdown("**📈 Equity Curve (Cumulative Points)**")
        st.line_chart(eq_df, height=200, use_container_width=True)

        # ── Daily P&L bar chart ────────────────────────────────────────────────
        daily_pnl = (
            trades.groupby("date")["points"].sum()
            .reset_index()
            .rename(columns={"points": "Points"})
        )
        daily_pnl["date"] = daily_pnl["date"].astype(str)
        st.markdown("**📊 Daily P&L (Points)**")
        st.bar_chart(daily_pnl.set_index("date")["Points"], height=180, use_container_width=True)

        # ── Trades table ─────────────────────────────────────────────────────
        st.markdown("**📋 Trade-by-Trade Log**")
        display_cols = [
            "date", "direction", "entry_time", "entry_price",
            "exit_time", "exit_price", "exit_reason",
            "points", "result", "rsi", "adx", "vwap",
        ]
        disp = trades[display_cols].copy()
        disp["entry_time"] = pd.to_datetime(disp["entry_time"]).dt.strftime("%H:%M")
        disp["exit_time"]  = pd.to_datetime(disp["exit_time"]).dt.strftime("%H:%M")

        def _color_result(val):
            if val == "WIN":
                return "background-color:#0d3321; color:#22c55e"
            if val == "LOSS":
                return "background-color:#3b0f0f; color:#ef4444"
            return ""

        def _color_points(val):
            try:
                return "color:#22c55e" if float(val) > 0 else ("color:#ef4444" if float(val) < 0 else "")
            except Exception:
                return ""

        styled = (
            disp.style
            .applymap(_color_result, subset=["result"])
            .applymap(_color_points, subset=["points"])
        )
        st.dataframe(styled, use_container_width=True, hide_index=True)

        # ── Price + close chart ───────────────────────────────────────────────
        st.markdown("**📉 Close Price History (5-min)**")
        chart_df = df_raw[["datetime", "close"]].set_index("datetime").tail(400)
        st.line_chart(chart_df, height=200, use_container_width=True)

        # ── Win/Loss by direction ─────────────────────────────────────────────
        st.markdown("**Buy vs Sell breakdown**")
        d1, d2 = st.columns(2)
        for col_w, direction in zip([d1, d2], ["BUY", "SELL"]):
            sub = trades[trades["direction"] == direction]
            if sub.empty:
                col_w.info(f"No {direction} trades.")
                continue
            w = (sub["result"] == "WIN").sum()
            l = (sub["result"] == "LOSS").sum()
            t = len(sub)
            wr = round(w / t * 100, 1)
            pts = round(sub["points"].sum(), 2)
            col_w.markdown(
                f"**{direction}** — {t} trades &nbsp;|&nbsp; "
                f"<span style='color:#22c55e'>{w}W</span> / "
                f"<span style='color:#ef4444'>{l}L</span> &nbsp;|&nbsp; "
                f"Win rate: **{wr}%** &nbsp;|&nbsp; Points: **{pts}**",
                unsafe_allow_html=True,
            )

        st.markdown("---")

    # ── Download all trades ───────────────────────────────────────────────────
    all_trades = pd.concat(
        [r["trades"] for r in st.session_state.bt_results.values() if not r["trades"].empty],
        ignore_index=True,
    )
    if not all_trades.empty:
        csv_bytes = all_trades.to_csv(index=False).encode()
        st.download_button(
            label="⬇ Download All Trades CSV",
            data=csv_bytes,
            file_name=f"backtest_{datetime.date.today()}.csv",
            mime="text/csv",
        )

else:
    st.info("👆 Select settings above and click **▶ Run Backtest** to start.")
    st.markdown(
        "<div style='color:#57606a;font-size:12px;margin-top:8px;'>"
        "The backtest fetches real 5-min OHLCV data from Angel One and replays "
        "the strategy candle-by-candle with no look-ahead bias. "
        "VWAP resets each day. Session filter (09:30–14:30) is applied. "
        "Max 4 trades per symbol per day enforced."
        "</div>",
        unsafe_allow_html=True,
    )

# ─── Footer ──────────────────────────────────────────────────────────────────
st.markdown(
    "<div style='text-align:center;color:#57606a;font-size:11px;"
    "border-top:1px solid #30363d;padding-top:12px;margin-top:40px;'>"
    "Backtest uses real Angel One historical data · No look-ahead bias · "
    "Past performance does not guarantee future results."
    "</div>",
    unsafe_allow_html=True,
)
