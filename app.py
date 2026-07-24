"""
app.py — Combined Streamlit app: Live Dashboard + Backtest on one port.

Run with:
    streamlit run app.py
"""

from __future__ import annotations
import datetime
import os
import sys
import pandas as pd
import streamlit as st
from streamlit_autorefresh import st_autorefresh

sys.path.insert(0, os.path.dirname(__file__))

import config
from feed.angel_auth import get_session
from feed.data_feed import fetch_candles, refresh_candles
from engine.signal_engine import evaluate_signal, SIGNAL_BUY, SIGNAL_SELL
from alerts.notifier import send_signal_alert
from engine.backtester import run_backtest, summary_stats

# ─── Page config ─────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Nifty / BankNifty / Stocks Signals",
    page_icon="📈",
    layout="wide",
)

# ─── Shared CSS ──────────────────────────────────────────────────────────────
st.markdown("""
<style>
    .main { background-color: #0f1117; }
    /* signal cards */
    .signal-buy  { background:#0d3321; border:1.5px solid #22c55e; border-radius:10px; padding:18px; }
    .signal-sell { background:#3b0f0f; border:1.5px solid #ef4444; border-radius:10px; padding:18px; }
    .signal-none { background:#1a1d26; border:1.5px solid #444;    border-radius:10px; padding:18px; }
    .big-label   { font-size:13px; color:#8b949e; margin-bottom:2px; }
    .big-value   { font-size:28px; font-weight:700; margin-bottom:0; }
    .buy-color   { color:#22c55e; }
    .sell-color  { color:#ef4444; }
    .none-color  { color:#8b949e; }
    .metric-row  { display:flex; gap:16px; flex-wrap:wrap; margin-top:12px; }
    .metric-box  { background:#1a1d26; border:1px solid #30363d; border-radius:8px;
                   padding:10px 14px; min-width:100px; }
    .mbox-label  { font-size:11px; color:#8b949e; }
    .mbox-val    { font-size:15px; font-weight:600; color:#e6edf3; }
    .ts          { font-size:11px; color:#8b949e; margin-top:8px; }
    /* backtest stat cards */
    .stat-card   { background:#1a1d26; border:1px solid #30363d; border-radius:8px;
                   padding:14px 10px; text-align:center; }
    .stat-val    { font-size:24px; font-weight:700; margin-bottom:2px; }
    .stat-lbl    { font-size:11px; color:#8b949e; }
    .win-clr     { color:#22c55e; }
    .loss-clr    { color:#ef4444; }
    .neu-clr     { color:#3b82d4; }
    .warn-clr    { color:#f59e0b; }
    /* filter status grid */
    .filter-grid { display:flex; gap:8px; flex-wrap:wrap; margin-top:10px; }
    .filter-pill { display:inline-flex; align-items:center; gap:5px; font-size:11px;
                   font-weight:600; padding:4px 9px; border-radius:12px; }
    .filter-pass { background:#0d3321; color:#22c55e; border:1px solid #22c55e44; }
    .filter-fail { background:#3b0f0f; color:#ef4444; border:1px solid #ef444444; }
    .filter-na   { background:#1a1d26; color:#8b949e; border:1px solid #30363d; }
    /* session badge */
    .session-badge { display:inline-block; font-size:11px; font-weight:700;
                     padding:3px 10px; border-radius:10px; margin-left:10px;
                     vertical-align:middle; }
    .sess-pre   { background:#1a1d26; color:#8b949e;  border:1px solid #444; }
    .sess-live  { background:#0d2e1a; color:#22c55e;  border:1px solid #22c55e66; }
    .sess-noent { background:#2e2200; color:#f59e0b;  border:1px solid #f59e0b66; }
    .sess-eod   { background:#2e0f0f; color:#ef4444;  border:1px solid #ef444466; }
    /* hold duration / SL distance badges */
    .info-pill  { font-size:11px; color:#8b949e; background:#1a1d26;
                  border:1px solid #30363d; border-radius:8px; padding:3px 8px;
                  display:inline-block; }
    .info-pill b { color:#e6edf3; }
    /* regime pulse bar */
    .pulse-bar  { display:flex; gap:12px; flex-wrap:wrap; margin:6px 0 14px;
                  padding:10px 14px; background:#1a1d26; border-radius:10px;
                  border:1px solid #30363d; }
    .pulse-sym  { display:flex; flex-direction:column; gap:2px; min-width:160px; }
    .pulse-name { font-size:11px; color:#8b949e; font-weight:600; }
    .pulse-val  { font-size:13px; font-weight:700; }
    .regime-trend    { color:#22c55e; }
    .regime-border   { color:#f59e0b; }
    .regime-sideways { color:#ef4444; }
    .regime-dead     { color:#f97316; }
    /* dead-zone warning banner */
    .dead-banner { background:#2e2200; border:1px solid #f59e0b66; border-radius:8px;
                   padding:8px 14px; font-size:12px; color:#f59e0b;
                   margin-bottom:10px; font-weight:600; }
    /* heatmap table */
    .heat-table { width:100%; border-collapse:collapse; font-size:12px; }
    .heat-table th { background:#1a1d26; color:#8b949e; padding:5px 8px;
                     border:1px solid #30363d; text-align:center; font-weight:700; }
    .heat-table td { padding:5px 8px; border:1px solid #30363d; text-align:center; }
    /* today P&L tally bar */
    .pnl-bar { display:flex; gap:18px; flex-wrap:wrap; padding:10px 16px;
               background:#1a1d26; border:1px solid #30363d; border-radius:10px;
               align-items:center; margin-bottom:14px; }
    .pnl-item { display:flex; flex-direction:column; gap:1px; min-width:80px; }
    .pnl-lbl  { font-size:10px; color:#8b949e; font-weight:600; letter-spacing:.3px; text-transform:uppercase; }
    .pnl-val  { font-size:17px; font-weight:700; }
    /* progress bar toward target */
    .prog-wrap { background:#1a1d26; border-radius:6px; height:8px;
                 overflow:hidden; margin-top:6px; border:1px solid #30363d; }
    .prog-fill  { height:100%; border-radius:6px; transition:width .3s; }
    /* strategy score bar (stocks tab) */
    .score-bar-wrap { height:6px; border-radius:4px; background:#1a1d26;
                      overflow:hidden; margin-top:3px; }
    .score-bar-fill { height:100%; border-radius:4px; }
    /* streak dots */
    .streak-dot-row { display:flex; flex-wrap:wrap; gap:4px; margin-top:6px; }
    .sdot { width:12px; height:12px; border-radius:50%; display:inline-block; }
    .sdot-win  { background:#22c55e; }
    .sdot-loss { background:#ef4444; }
    .sdot-be   { background:#8b949e; }
    /* context alignment badge */
    .ctx-badge { display:inline-block; font-size:10px; font-weight:700;
                 padding:2px 8px; border-radius:8px; margin-left:6px; }
    .ctx-align   { background:#0d2e1a; color:#22c55e; border:1px solid #22c55e44; }
    .ctx-against { background:#2e0f0f; color:#ef4444; border:1px solid #ef444444; }
    .ctx-neutral { background:#1a1d26; color:#8b949e; border:1px solid #30363d; }
    /* countdown pill */
    .cdwn-pill { font-size:11px; color:#f59e0b; background:#2e2200;
                 border:1px solid #f59e0b44; border-radius:8px;
                 padding:2px 8px; display:inline-block; }
</style>
""", unsafe_allow_html=True)

# ─── Shared session-state init ────────────────────────────────────────────────
def _init_state():
    defaults = {
        "api"          : None,
        "login_err"    : None,
        "candles"      : {s: pd.DataFrame() for s in config.INSTRUMENTS},
        "signals"      : {s: {} for s in config.INSTRUMENTS},
        "last_alert"   : {s: None for s in config.INSTRUMENTS},
        "trade_counts" : {s: 0   for s in config.INSTRUMENTS},
        "bt_results"   : {},
        # ── open-trade state per symbol ──────────────────────────────────────
        # Each entry: {"direction": "BUY"|"SELL", "entry_price": float,
        #              "entry_time": datetime, "sl": float, "entry_atr": float}
        # None means no open trade.
        "open_trades"  : {s: None for s in config.INSTRUMENTS},
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

_init_state()


# ─── Login helper ─────────────────────────────────────────────────────────────
def _ensure_api(spinner_text="Connecting to Angel One…"):
    if st.session_state.api is None:
        with st.spinner(spinner_text):
            try:
                api, _, _ = get_session()
                st.session_state.api       = api
                st.session_state.login_err = None
            except Exception as exc:
                st.session_state.login_err = str(exc)
    return st.session_state.api


# ─── Market-hours refresh guard ──────────────────────────────────────────────
def _market_refresh_interval() -> int | None:
    """
    Return the auto-refresh interval in milliseconds, or None to disable.

    Rules (all times IST = Asia/Kolkata):
      Weekends (Sat/Sun)        → None  (market closed, no refresh)
      Weekdays before 09:10     → None  (pre-open, nothing to fetch yet)
      Weekdays 09:10 – 15:30    → 60_000 ms  (active session + 15-min buffer after close)
      Weekdays after 15:30      → None  (session over, all positions force-exited at 15:15)

    The 5-min gap before 09:15 is intentional: allows the app to warm up and
    the first batch of candles to be available the moment the session opens.
    """
    try:
        import pytz
        ist = pytz.timezone("Asia/Kolkata")
        now = datetime.datetime.now(ist)
    except ImportError:
        # pytz not available — fall back to local time (works when running on IST host)
        now = datetime.datetime.now()

    # Weekend check — Monday=0 … Sunday=6
    if now.weekday() >= 5:
        return None

    t = now.time()
    if t < datetime.time(9, 10):
        return None          # too early — market not open yet
    if t >= datetime.time(15, 30):
        return None          # session over — stop all API calls

    return 60_000            # active session: refresh every 60 s


def _is_market_hours() -> bool:
    """Return True only during active IST trading hours on weekdays (09:00–15:30)."""
    return _market_refresh_interval() is not None


# ═══════════════════════════════════════════════════════════════════════════════
#  TAB DEFINITIONS
# ═══════════════════════════════════════════════════════════════════════════════
tab_live, tab_bt, tab_journal, tab_stocks = st.tabs([
    "📈  Live Signals", "🔬  Backtest", "📒  Journal", "🏦  Stocks"
])


# ══════════════════════════════════════════════════
#  TAB 1 — LIVE SIGNALS
# ══════════════════════════════════════════════════
with tab_live:

    # Auto-refresh — only during IST market hours on weekdays
    _live_interval = _market_refresh_interval()
    if _live_interval:
        st_autorefresh(interval=_live_interval, key="live_refresh")
    else:
        _now_disp = datetime.datetime.now()
        _is_weekend = _now_disp.weekday() >= 5
        _after_close = _now_disp.time() > datetime.time(15, 30)
        if _is_weekend:
            st.info("📴 Auto-refresh paused — market closed on weekends.", icon="🗓️")
        elif _after_close:
            st.info("📴 Auto-refresh stopped — market closed (after 15:30 IST). "
                    "Refresh manually if needed.", icon="🔒")
        # before 09:10 — no banner needed, just don't refresh yet

    st.markdown("## 📈 Nifty & Bank Nifty — Live Intraday Signal Dashboard")
    st.markdown(
        "<span style='color:#8b949e;font-size:13px;'>"
        "Strategy: <b>Supertrend(7,3) + EMA(9/21) + RSI(14) + VWAP + ADX</b>"
        " &nbsp;|&nbsp; Timeframe: <b>5-min</b>"
        " &nbsp;|&nbsp; Session: <b>09:40 – 13:30 entry, 15:15 exit</b>"
        f" &nbsp;|&nbsp; ADX threshold: <b>&gt;{config.ADX_THRESHOLD}</b>"
        " &nbsp;|&nbsp; Exit: <b>Supertrend trail + Hard SL cap</b>"
        "</span>",
        unsafe_allow_html=True,
    )
    st.markdown("---")

    # ── Today's P&L tally bar ────────────────────────────────────────────────
    def _today_pnl_bar():
        """Read signal_log.csv and show today's cumulative P&L, trade count, and win rate."""
        try:
            if not os.path.isfile(config.SIGNAL_LOG):
                return
            _sdf = pd.read_csv(config.SIGNAL_LOG)
            if _sdf.empty:
                return
            _sdf.columns = [c.strip().lower() for c in _sdf.columns]
            # filter to EXIT signals today (signals with points column)
            if "timestamp" in _sdf.columns:
                _sdf["_ts"] = pd.to_datetime(_sdf["timestamp"], errors="coerce")
                today_str = datetime.date.today().isoformat()
                _sdf = _sdf[_sdf["timestamp"].astype(str).str.startswith(today_str)]
            if "points" not in _sdf.columns:
                return
            _sdf["points"] = pd.to_numeric(_sdf["points"], errors="coerce").fillna(0)
            _exits = _sdf[_sdf["points"] != 0]
            if _exits.empty:
                return
            total_pts  = round(_exits["points"].sum(), 2)
            n_trades   = len(_exits)
            n_wins     = (_exits["points"] > 0).sum()
            n_loss     = (_exits["points"] < 0).sum()
            wr         = round(n_wins / n_trades * 100, 1) if n_trades else 0
            pts_clr    = "#22c55e" if total_pts >= 0 else "#ef4444"
            wr_clr     = "#22c55e" if wr >= 55 else ("#f59e0b" if wr >= 45 else "#ef4444")

            st.markdown(
                f"<div class='pnl-bar'>"
                f"<div class='pnl-item'><div class='pnl-lbl'>Today P&amp;L</div>"
                f"<div class='pnl-val' style='color:{pts_clr};'>{total_pts:+.1f} pts</div></div>"
                f"<div class='pnl-item'><div class='pnl-lbl'>Trades Today</div>"
                f"<div class='pnl-val' style='color:#e6edf3;'>{n_trades}</div></div>"
                f"<div class='pnl-item'><div class='pnl-lbl'>Wins / Losses</div>"
                f"<div class='pnl-val'>"
                f"<span style='color:#22c55e;'>{n_wins}W</span>"
                f"<span style='color:#8b949e;'> / </span>"
                f"<span style='color:#ef4444;'>{n_loss}L</span></div></div>"
                f"<div class='pnl-item'><div class='pnl-lbl'>Win Rate</div>"
                f"<div class='pnl-val' style='color:{wr_clr};'>{wr}%</div></div>"
                f"<div style='margin-left:auto;font-size:11px;color:#8b949e;align-self:center;'>"
                f"Live signals — based on <code>signal_log.csv</code></div>"
                f"</div>",
                unsafe_allow_html=True,
            )
        except Exception:
            pass   # never crash the live tab

    # ── Regime Pulse Bar — rendered after data refresh (below) ───────────────
    def _regime_pulse_bar():
        """One-line ADX regime indicator for all symbols. Shown before signal cards."""
        adx_max   = config.ADX_MAX if isinstance(config.ADX_MAX, (int, float)) else 60
        dz_lo     = getattr(config, "ADX_DEAD_ZONE_LOW",  None)
        dz_hi     = getattr(config, "ADX_DEAD_ZONE_HIGH", None)
        sym_html  = ""
        for sym in config.INSTRUMENTS:
            sig   = st.session_state.signals.get(sym, {})
            adx_v = sig.get("adx")
            rsi_v = sig.get("rsi")
            st_s  = sig.get("st_value")
            if adx_v is None:
                regime_cls  = "regime-sideways"
                regime_lbl  = "No data"
                regime_icon = "—"
            else:
                in_dead = (dz_lo is not None and dz_hi is not None
                           and dz_lo <= adx_v < dz_hi)
                if in_dead:
                    regime_cls  = "regime-dead"
                    regime_lbl  = f"Dead Zone (ADX {adx_v:.1f})"
                    regime_icon = "🟠"
                elif adx_v > adx_max:
                    regime_cls  = "regime-dead"
                    regime_lbl  = f"Overextended (ADX {adx_v:.1f})"
                    regime_icon = "🟠"
                elif adx_v >= config.ADX_THRESHOLD + 6:
                    regime_cls  = "regime-trend"
                    regime_lbl  = f"Trending (ADX {adx_v:.1f})"
                    regime_icon = "🟢"
                elif adx_v >= config.ADX_THRESHOLD:
                    regime_cls  = "regime-border"
                    regime_lbl  = f"Borderline (ADX {adx_v:.1f})"
                    regime_icon = "🟡"
                else:
                    regime_cls  = "regime-sideways"
                    regime_lbl  = f"Sideways (ADX {adx_v:.1f})"
                    regime_icon = "🔴"

            rsi_txt = f"RSI {rsi_v:.1f}" if rsi_v else "RSI —"
            ot      = st.session_state.open_trades.get(sym)
            trade_txt = "📌 IN TRADE" if ot else ""
            sym_html += (
                f"<div class='pulse-sym'>"
                f"<div class='pulse-name'>{sym} {trade_txt}</div>"
                f"<div class='pulse-val {regime_cls}'>{regime_icon} {regime_lbl}"
                f"&nbsp;<span style='font-size:11px;font-weight:400;color:#8b949e;'>"
                f"| {rsi_txt}</span></div>"
                f"</div>"
            )
        now_dt  = datetime.datetime.now()
        now_str = now_dt.strftime("%H:%M:%S")
        # countdown to next 5-min candle (seconds until next multiple of 5 min)
        secs_past = (now_dt.minute % 5) * 60 + now_dt.second
        secs_left = 300 - secs_past
        cdwn_str  = f"{secs_left // 60}m {secs_left % 60:02d}s"
        st.markdown(
            f"<div class='pulse-bar'>{sym_html}"
            f"<div class='pulse-sym' style='margin-left:auto;text-align:right;'>"
            f"<div class='pulse-name'>Last refresh</div>"
            f"<div class='pulse-val' style='color:#8b949e;font-size:12px;'>{now_str}</div>"
            f"<div style='margin-top:3px;'>"
            f"<span class='cdwn-pill'>⏱ Next candle ~{cdwn_str}</span>"
            f"</div></div></div>",
            unsafe_allow_html=True,
        )

    # ── refresh data & manage open trades ────────────────────────────────────
    def _refresh_live():
        api = _ensure_api()
        if api is None:
            return
        import numpy as np
        from engine.indicators import add_indicators
        from engine.utils import strip_tz, force_exit_dt
        from engine.signal_engine import SIGNAL_BUY, SIGNAL_SELL, SIGNAL_NONE

        for sym, cfg_inst in config.INSTRUMENTS.items():
            existing = st.session_state.candles[sym]
            if existing.empty:
                # Always fetch on cold start — charts need historical data even outside hours
                df = fetch_candles(api, cfg_inst["token"], cfg_inst["exchange"], days_back=5)
            elif _is_market_hours():
                # Incremental refresh only during live session
                df = refresh_candles(api, existing, cfg_inst["token"], cfg_inst["exchange"])
            else:
                continue   # outside hours + already have data — nothing to update
            st.session_state.candles[sym] = df
            if df.empty:
                continue

            sig      = evaluate_signal(df, symbol=sym)
            st.session_state.signals[sym] = sig
            ot       = st.session_state.open_trades[sym]   # may be None

            # ── candle values ────────────────────────────────────────────────
            # iloc[-1] = currently forming (incomplete) candle — live price only
            # iloc[-2] = last fully CLOSED candle — used for all SL/signal logic
            df_ind      = add_indicators(df.copy())
            df_ind["symbol"] = sym
            live_row    = df_ind.iloc[-1]   # forming candle — display only
            row         = df_ind.iloc[-2] if len(df_ind) >= 2 else live_row
            close       = float(row["close"])
            high        = float(row["high"])
            low         = float(row["low"])
            atr_val     = float(row.get("atr") or 0)
            st_val      = float(row.get("st_value") or 0)
            now_dt      = strip_tz(pd.to_datetime(row["datetime"]))
            now_time    = now_dt.time()
            now_date    = now_dt.date()

            # SL cap helpers (same logic as backtester)
            # BankNifty: min(2×ATR, 150) — tighter of dynamic ATR floor and hard ceiling
            # Nifty    : fixed 45 pts
            # Stocks   : pure 2×ATR, no fixed ceiling
            fixed_cap    = config.SL_CAP_PTS.get(sym)
            if sym == "BANKNIFTY":
                atr_cap_mult = getattr(config, "ATR_SL_MULT_BANKNIFTY", 2.0)
                # fixed_cap (150) used as ceiling — applied in cap computation below
            elif sym in config.SL_CAP_PTS:
                atr_cap_mult = None   # Nifty: fixed pts only
            else:
                atr_cap_mult = getattr(config, "ATR_SL_MULT_STOCKS", {}).get(sym, 2.0)
                fixed_cap    = None   # stocks: pure ATR, no ceiling

            # ── A. Manage existing open trade ────────────────────────────────
            if ot is not None:
                direction = ot["direction"]
                entry     = ot["entry_price"]
                sl_frozen = ot["sl"]   # SL active at this candle's open

                exited      = False
                exit_reason = None
                exit_price  = None

                # 1. Hard SL hit
                sl_hit = ((direction == SIGNAL_BUY  and low  <= sl_frozen) or
                          (direction == SIGNAL_SELL and high >= sl_frozen))
                if sl_hit:
                    exit_reason = "SL Hit"
                    exit_price  = sl_frozen
                    exited      = True

                # 2. EOD force exit at 15:15 IST
                if not exited and now_dt >= force_exit_dt(now_date):
                    slip = getattr(config, "EOD_SLIPPAGE_PTS", {}).get(sym, 0)
                    exit_price  = round((close - slip) if direction == SIGNAL_BUY else (close + slip), 2)
                    exit_reason = "EOD Exit"
                    exited      = True

                # 3. Opposite signal confirmed (1-candle — live is single candle resolution)
                if not exited:
                    reverse = SIGNAL_SELL if direction == SIGNAL_BUY else SIGNAL_BUY
                    if sig.get("signal") == reverse:
                        exit_price  = close
                        exit_reason = "Reverse Signal"
                        exited      = True

                if exited:
                    pts = round((exit_price - entry) if direction == SIGNAL_BUY
                                else (entry - exit_price), 2)
                    send_signal_alert(sym, {
                        "signal"    : f"EXIT ({direction})",
                        "entry"     : entry,
                        "sl"        : sl_frozen,
                        "target"    : ot.get("target"),
                        "exit_price": exit_price,
                        "points"    : pts,
                        "reason"    : exit_reason,
                        "candle_time": now_dt,
                    })
                    st.session_state.open_trades[sym] = None
                    ot = None
                else:
                    # 4. Trail the Supertrend SL
                    if st_val and not np.isnan(st_val):
                        if direction == SIGNAL_BUY:
                            ot["sl"] = round(max(ot["sl"], st_val), 2)
                        else:
                            ot["sl"] = round(min(ot["sl"], st_val), 2)

                    # 5. Apply hard SL cap — BankNifty: min(ATR×mult, 150); Nifty: fixed pts
                    if atr_cap_mult is not None:
                        atr_c = round(atr_val * atr_cap_mult, 2) if atr_val else round(ot["entry_atr"] * atr_cap_mult, 2)
                        live_cap = min(atr_c, fixed_cap) if fixed_cap is not None else atr_c
                    else:
                        live_cap = fixed_cap
                    if direction == SIGNAL_BUY:
                        ot["sl"] = round(max(ot["sl"], entry - live_cap), 2)
                    else:
                        ot["sl"] = round(max(ot["sl"], entry + live_cap), 2)

                    st.session_state.open_trades[sym] = ot

            # ── B. No open trade — check for new entry signal ────────────────
            if (ot is None
                    and sig.get("signal") not in (None, SIGNAL_NONE)
                    and now_time < datetime.time(13, 0)
                    and now_time >= datetime.time(9, 40)
                    and st.session_state.trade_counts[sym] < config.MAX_TRADES_PER_SYMBOL):

                direction    = sig["signal"]
                entry_price  = sig["entry"]
                sl_initial   = sig["sl"]
                if atr_cap_mult is not None and atr_val:
                    atr_cap = round(atr_val * atr_cap_mult, 2)
                    cap = min(atr_cap, fixed_cap) if fixed_cap is not None else atr_cap
                else:
                    cap = fixed_cap or 9999
                if direction == SIGNAL_BUY:
                    sl_cap = round(entry_price - cap, 2)
                    sl     = max(sl_initial, sl_cap) if sl_initial else sl_cap
                else:
                    sl_cap = round(entry_price + cap, 2)
                    sl     = max(sl_initial, sl_cap) if sl_initial else sl_cap

                ct = sig.get("candle_time")
                if ct != st.session_state.last_alert[sym]:
                    new_trade = {
                        "direction"  : direction,
                        "entry_price": entry_price,
                        "entry_time" : now_dt,
                        "sl"         : round(sl, 2),
                        "target"     : sig.get("target"),
                        "entry_atr"  : round(atr_val, 2),
                    }
                    st.session_state.open_trades[sym] = new_trade
                    send_signal_alert(sym, sig)
                    st.session_state.last_alert[sym] = ct
                    st.session_state.trade_counts[sym] += 1

    try:
        _refresh_live()
    except Exception as _rf_err:
        _rf_msg = str(_rf_err)
        if "exceeding access rate" in _rf_msg:
            st.warning(
                "⚠️ Angel One rate limit reached — data will refresh on the next cycle (60 s). "
                "This happens after many rapid API calls (e.g. running the backtest). "
                "No action needed.",
                icon="⏳",
            )
        else:
            st.error(f"⚠️ Data refresh error: {_rf_msg}")

    # ── login error ───────────────────────────────────────────────────────────
    if st.session_state.login_err:
        st.error(f"⚠️ Angel One Login Error: {st.session_state.login_err}")
        if st.button("🔄 Retry Login", key="live_retry"):
            st.session_state.api = None
            st.rerun()

    # ── Today P&L tally ──────────────────────────────────────────────────────
    _today_pnl_bar()

    # ── Regime Pulse Bar ─────────────────────────────────────────────────────
    _regime_pulse_bar()

    # ── helpers ───────────────────────────────────────────────────────────────
    def _session_badge() -> str:
        """Return HTML badge showing current session state."""
        now = datetime.datetime.now()
        t   = now.time()
        def _pt(s): h, m = map(int, s.split(":")); return datetime.time(h, m)
        if t < _pt(config.SESSION_START):
            return "<span class='session-badge sess-pre'>⏳ Pre-Session</span>"
        if t <= _pt(config.NO_NEW_ENTRY_AFTER):
            return "<span class='session-badge sess-live'>🟢 Session Active</span>"
        if t <= _pt(config.SESSION_END):
            return "<span class='session-badge sess-noent'>⚠️ No New Entries</span>"
        if t <= _pt(config.FORCE_EXIT):
            return "<span class='session-badge sess-eod'>🔴 EOD — Manage Only</span>"
        return "<span class='session-badge sess-eod'>🔴 After Hours</span>"

    def _st_streak_label(sym: str, sig: dict) -> str:
        """Return e.g. '2/4' ST streak label for BUY, and '1/2' for SELL, using st_history."""
        # st_history is a list of recent st_signals: [prev1, prev2, ...] newest-first.
        # It is populated by evaluate_signal() but lives on the raw sig dict only when
        # the signal engine puts it there.  We use the candle df from session state.
        df = st.session_state.candles.get(sym, pd.DataFrame())
        if df.empty or len(df) < 2:
            return ""
        from engine.indicators import add_indicators as _ai
        df_i = _ai(df.copy())
        # count consecutive same-direction candles going backward from last closed candle
        st_vals = df_i["st_signal"].tolist()
        if len(st_vals) < 2:
            return ""
        last_sig = int(st_vals[-2] or 0)   # last CLOSED candle
        if last_sig == 0:
            return ""
        streak = 0
        for s in reversed(st_vals[:-1]):   # walk backwards through closed candles
            if int(s or 0) == last_sig:
                streak += 1
            else:
                break
        if sym == "NIFTY":
            required = int(getattr(config, "ST_CONFIRM_CANDLES_NIFTY", config.ST_CONFIRM_CANDLES))
        else:
            required = int(getattr(config, "ST_CONFIRM_CANDLES", 1))
        direction_word = "GREEN" if last_sig == 1 else "RED"
        colour = "#22c55e" if last_sig == 1 else "#ef4444"
        confirmed = streak >= required
        tick = "✅" if confirmed else "⏳"
        return (f"<span style='font-size:11px;font-weight:600;color:{colour};'>"
                f"{tick} ST {direction_word}: {min(streak, required)}/{required} candles</span>")

    def _filter_grid_buy(sym: str, sig: dict) -> str:
        """Build filter-status pill row for BUY conditions, with ST streak progress."""
        entry  = sig.get("entry") or 0
        rsi_v  = sig.get("rsi")  or 0
        adx_v  = sig.get("adx")  or 0
        vwap_v = sig.get("vwap") or 0
        ema_s  = sig.get("ema_slow") or 0
        from engine.signal_engine import _rsi_buy_low, _rsi_buy_high
        rsi_lo = _rsi_buy_low(sym)
        rsi_hi = _rsi_buy_high(sym)
        adx_max = config.ADX_MAX if isinstance(config.ADX_MAX, (int, float)) else 60
        adx_dz_lo = getattr(config, "ADX_DEAD_ZONE_LOW", None)
        adx_dz_hi = getattr(config, "ADX_DEAD_ZONE_HIGH", None)
        in_dead = (adx_dz_lo is not None and adx_dz_hi is not None
                   and adx_dz_lo <= adx_v < adx_dz_hi)
        # derive ST BUY ok from latest candle's st_signal (positive = GREEN)
        df = st.session_state.candles.get(sym, pd.DataFrame())
        st_buy_ok = None
        if not df.empty and len(df) >= 2:
            from engine.indicators import add_indicators as _ai2
            _df_i = _ai2(df.copy())
            _st   = int(_df_i["st_signal"].iloc[-2] or 0)
            st_buy_ok = (_st == 1)
        filters = [
            ("ST GREEN",        st_buy_ok),
            (f"RSI {rsi_lo:.0f}–{rsi_hi:.0f}", rsi_lo <= rsi_v <= rsi_hi if rsi_v else None),
            ("Price > EMA21",   entry > ema_s  if entry and ema_s  else None),
            ("Price > VWAP",    entry > vwap_v if entry and vwap_v else None),
            (f"ADX {config.ADX_THRESHOLD}–{adx_max}",
             config.ADX_THRESHOLD <= adx_v <= adx_max and not in_dead if adx_v else None),
        ]
        passed = sum(1 for _, ok in filters if ok is True)
        counter_clr = "#22c55e" if passed == 5 else ("#f59e0b" if passed >= 3 else "#8b949e")
        counter_html = (f"<span style='font-size:11px;font-weight:700;color:{counter_clr};"
                        f"margin-left:6px;'>{passed}/5 passing</span>")
        pills = ""
        for label, ok in filters:
            if ok is True:
                pills += f"<span class='filter-pill filter-pass'>✅ {label}</span>"
            elif ok is False:
                pills += f"<span class='filter-pill filter-fail'>❌ {label}</span>"
            else:
                pills += f"<span class='filter-pill filter-na'>— {label}</span>"
        return (f"<div style='display:flex;align-items:center;gap:0;margin-bottom:4px;'>"
                f"<span style='font-size:11px;color:#8b949e;font-weight:600;'>BUY filters</span>"
                f"{counter_html}</div>"
                f"<div class='filter-grid'>{pills}</div>")

    def _filter_grid_sell(sym: str, sig: dict) -> str:
        """Build filter-status pill row for SELL conditions, with ST streak progress."""
        entry  = sig.get("entry") or 0
        rsi_v  = sig.get("rsi")  or 0
        adx_v  = sig.get("adx")  or 0
        vwap_v = sig.get("vwap") or 0
        ema_s  = sig.get("ema_slow") or 0
        from engine.signal_engine import _rsi_sell_high
        rsi_hi_sell = _rsi_sell_high(sym)
        adx_max = config.ADX_MAX if isinstance(config.ADX_MAX, (int, float)) else 60
        adx_dz_lo = getattr(config, "ADX_DEAD_ZONE_LOW", None)
        adx_dz_hi = getattr(config, "ADX_DEAD_ZONE_HIGH", None)
        in_dead = (adx_dz_lo is not None and adx_dz_hi is not None
                   and adx_dz_lo <= adx_v < adx_dz_hi)
        df = st.session_state.candles.get(sym, pd.DataFrame())
        st_sell_ok = None
        if not df.empty and len(df) >= 2:
            from engine.indicators import add_indicators as _ai3
            _df_i3 = _ai3(df.copy())
            _st3   = int(_df_i3["st_signal"].iloc[-2] or 0)
            st_sell_ok = (_st3 == -1)
        filters = [
            ("ST RED",          st_sell_ok),
            (f"RSI {config.RSI_SELL_LOW}–{rsi_hi_sell:.0f}",
             config.RSI_SELL_LOW <= rsi_v <= rsi_hi_sell if rsi_v else None),
            ("Price < EMA21",   entry < ema_s  if entry and ema_s  else None),
            ("Price < VWAP",    entry < vwap_v if entry and vwap_v else None),
            (f"ADX {config.ADX_THRESHOLD}–{adx_max}",
             config.ADX_THRESHOLD <= adx_v <= adx_max and not in_dead if adx_v else None),
        ]
        passed = sum(1 for _, ok in filters if ok is True)
        counter_clr = "#22c55e" if passed == 5 else ("#f59e0b" if passed >= 3 else "#8b949e")
        counter_html = (f"<span style='font-size:11px;font-weight:700;color:{counter_clr};"
                        f"margin-left:6px;'>{passed}/5 passing</span>")
        pills = ""
        for label, ok in filters:
            if ok is True:
                pills += f"<span class='filter-pill filter-pass'>✅ {label}</span>"
            elif ok is False:
                pills += f"<span class='filter-pill filter-fail'>❌ {label}</span>"
            else:
                pills += f"<span class='filter-pill filter-na'>— {label}</span>"
        return (f"<div style='display:flex;align-items:center;gap:0;margin-bottom:4px;margin-top:10px;'>"
                f"<span style='font-size:11px;color:#8b949e;font-weight:600;'>SELL filters</span>"
                f"{counter_html}</div>"
                f"<div class='filter-grid'>{pills}</div>")

    # ── signal cards ─────────────────────────────────────────────────────────
    def _signal_card(sym: str, sig: dict):
        ot        = st.session_state.open_trades.get(sym)
        direction = sig.get("signal", "NONE")
        rsi_v     = sig.get("rsi")
        adx_v     = sig.get("adx")
        vwap_v    = sig.get("vwap")
        st_v      = sig.get("st_value")
        ema_f     = sig.get("ema_fast")
        ema_s     = sig.get("ema_slow")
        ct        = sig.get("candle_time")
        reason    = sig.get("reason", "—")
        left      = config.MAX_TRADES_PER_SYMBOL - st.session_state.trade_counts.get(sym, 0)

        # ── IN TRADE — show open position panel ──────────────────────────────
        if ot is not None:
            ot_dir    = ot["direction"]
            ot_entry  = ot["entry_price"]
            ot_sl     = ot["sl"]
            ot_target = ot.get("target", "—")
            ot_time   = ot.get("entry_time")

            # Estimate unrealised P&L from latest signal price
            live_px    = sig.get("entry") or ot_entry
            unrealised = round((live_px - ot_entry) if ot_dir == SIGNAL_BUY
                               else (ot_entry - live_px), 2)
            unr_clr    = "#22c55e" if unrealised >= 0 else "#ef4444"
            css        = "signal-buy" if ot_dir == SIGNAL_BUY else "signal-sell"
            clr        = "buy-color"  if ot_dir == SIGNAL_BUY else "sell-color"

            # SL distance from live price
            sl_dist_pts = round(abs(live_px - ot_sl), 1) if live_px and ot_sl else "—"
            sl_dist_pct = f"{round(abs(live_px - ot_sl) / live_px * 100, 2):.2f}%" if live_px and ot_sl else "—"

            # Risk to reward on open trade
            if ot_target and ot_target != "—" and isinstance(ot_target, (int, float)):
                reward_pts = abs(ot_target - ot_entry)
                risk_pts   = abs(ot_entry - ot_sl) if ot_sl else 1
                rr_display = f"1 : {round(reward_pts / risk_pts, 2)}" if risk_pts else "—"
            else:
                rr_display = "—"

            # Hold duration
            hold_str = "—"
            if ot_time:
                held = datetime.datetime.now() - ot_time
                held_mins = int(held.total_seconds() // 60)
                hold_str = f"{held_mins // 60}h {held_mins % 60}m" if held_mins >= 60 else f"{held_mins}m"

            # ATR of the entry candle
            entry_atr = ot.get("entry_atr")
            atr_disp  = f"{entry_atr:.1f}" if entry_atr else (f"{rsi_v or '—'}")

            # Progress bar: 0% = entry, 100% = target; clamp [0,110]
            prog_pct = 0
            prog_clr = "#22c55e"
            prog_lbl = "—"
            if (ot_target and ot_target != "—" and isinstance(ot_target, (int, float))
                    and live_px is not None):
                total_range = abs(ot_target - ot_entry)
                moved       = (live_px - ot_entry) if ot_dir == SIGNAL_BUY else (ot_entry - live_px)
                prog_pct    = max(0, min(110, round(moved / total_range * 100, 1))) if total_range else 0
                prog_clr    = "#22c55e" if prog_pct >= 0 else "#ef4444"
                prog_lbl    = f"{prog_pct:.0f}% to target"

            # VWAP / EMA alignment badges
            vwap_badge = ""
            ema_badge  = ""
            if vwap_v and live_px:
                if ot_dir == SIGNAL_BUY:
                    vwap_badge = ("<span class='ctx-badge ctx-align'>▲ above VWAP</span>" if live_px > vwap_v
                                  else "<span class='ctx-badge ctx-against'>▼ below VWAP</span>")
                else:
                    vwap_badge = ("<span class='ctx-badge ctx-align'>▼ below VWAP</span>" if live_px < vwap_v
                                  else "<span class='ctx-badge ctx-against'>▲ above VWAP</span>")
            if ema_s and live_px:
                if ot_dir == SIGNAL_BUY:
                    ema_badge = ("<span class='ctx-badge ctx-align'>▲ above EMA21</span>" if live_px > ema_s
                                 else "<span class='ctx-badge ctx-against'>▼ below EMA21</span>")
                else:
                    ema_badge = ("<span class='ctx-badge ctx-align'>▼ below EMA21</span>" if live_px < ema_s
                                 else "<span class='ctx-badge ctx-against'>▲ above EMA21</span>")

            st.markdown(f"""
            <div class="{css}">
              <div class="big-label">{sym} — IN TRADE {_session_badge()}</div>
              <div class="big-value {clr}">{'🟢 LONG (BUY)' if ot_dir == SIGNAL_BUY else '🔴 SHORT (SELL)'}
                &nbsp;{vwap_badge}{ema_badge}
              </div>
              <div style="margin-top:6px;">
                <div style="display:flex;justify-content:space-between;font-size:11px;color:#8b949e;margin-bottom:2px;">
                  <span>Progress to target</span>
                  <span style="font-weight:700;color:{prog_clr};">{prog_lbl}</span>
                </div>
                <div class="prog-wrap">
                  <div class="prog-fill" style="width:{min(prog_pct,100)}%;background:{prog_clr};"></div>
                </div>
              </div>
              <div class="metric-row">
                <div class="metric-box"><div class="mbox-label">Entry</div><div class="mbox-val">{ot_entry}</div></div>
                <div class="metric-box"><div class="mbox-label">🔄 Trailing SL</div><div class="mbox-val" style="color:#f59e0b">{ot_sl}</div></div>
                <div class="metric-box"><div class="mbox-label">SL Distance</div><div class="mbox-val" style="color:#f59e0b">{sl_dist_pts} pts ({sl_dist_pct})</div></div>
                <div class="metric-box"><div class="mbox-label">Target</div><div class="mbox-val">{ot_target}</div></div>
                <div class="metric-box"><div class="mbox-label">Risk : Reward</div><div class="mbox-val">{rr_display}</div></div>
                <div class="metric-box"><div class="mbox-label">Live Price</div><div class="mbox-val">{live_px}</div></div>
                <div class="metric-box"><div class="mbox-label">Unrealised P&L</div><div class="mbox-val" style="color:{unr_clr}">{unrealised:+.1f} pts</div></div>
                <div class="metric-box"><div class="mbox-label">Hold Duration</div><div class="mbox-val">{hold_str}</div></div>
                <div class="metric-box"><div class="mbox-label">RSI (live)</div><div class="mbox-val">{rsi_v or '—'}</div></div>
                <div class="metric-box"><div class="mbox-label">ADX (live)</div><div class="mbox-val">{adx_v or '—'}</div></div>
                <div class="metric-box"><div class="mbox-label">Supertrend</div><div class="mbox-val">{st_v or '—'}</div></div>
                <div class="metric-box"><div class="mbox-label">ATR at Entry</div><div class="mbox-val">{entry_atr or '—'}</div></div>
              </div>
              <div style="margin-top:8px;font-size:12px;color:#8b949e;">
                Entry time: <b style="color:#e6edf3">{str(ot_time)[11:16] if ot_time else '—'}</b>
                &nbsp;|&nbsp; Exits: SL trail hit &nbsp;·&nbsp; reverse signal &nbsp;·&nbsp; 15:15 IST force close
              </div>
              <div class="ts">Last candle: {ct or 'N/A'} &nbsp;|&nbsp; Refreshed: {datetime.datetime.now().strftime('%H:%M:%S')}</div>
            </div>
            """, unsafe_allow_html=True)

            # Manual exit button
            if st.button(f"🚪 Exit {sym} trade now (market)", key=f"exit_{sym}"):
                st.session_state.open_trades[sym] = None
                st.success(f"✅ {sym} trade manually exited at market.")
                st.rerun()
            return

        # ── NO OPEN TRADE — show entry signal / waiting ───────────────────────
        entry  = sig.get("entry")
        sl     = sig.get("sl")
        target = sig.get("target")

        css  = "signal-buy" if direction == SIGNAL_BUY else ("signal-sell" if direction == SIGNAL_SELL else "signal-none")
        clr  = "buy-color"  if direction == SIGNAL_BUY else ("sell-color"  if direction == SIGNAL_SELL else "none-color")
        lbl  = "🟢 BUY SIGNAL" if direction == SIGNAL_BUY else ("🔴 SELL SIGNAL" if direction == SIGNAL_SELL else "⚪ WAITING FOR SIGNAL")

        vwap_ok_buy  = entry and vwap_v and entry > vwap_v
        vwap_ok_sell = entry and vwap_v and entry < vwap_v
        vwap_status  = "✅ Above VWAP" if vwap_ok_buy else ("✅ Below VWAP" if vwap_ok_sell else "❌ Near VWAP")

        # SL risk distance
        sl_dist_pts = f"{round(abs(entry - sl), 1)} pts" if entry and sl else "—"
        # Target R:R
        if entry and sl and target:
            risk   = abs(entry - sl)
            reward = abs(target - entry)
            rr_txt = f"1 : {round(reward / risk, 2)}" if risk else "—"
        else:
            rr_txt = "—"

        # ── PDH / PDL / ORB context from candle data ─────────────────────────
        pdh_val = pdl_val = orb_high = None
        df_live = st.session_state.candles.get(sym, pd.DataFrame())
        if not df_live.empty and len(df_live) >= 2:
            from engine.indicators import add_indicators as _ai_pdh, prev_day_levels, first_15min_range
            _df_ctx = _ai_pdh(df_live.copy())
            _df_ctx = prev_day_levels(_df_ctx)
            _df_ctx = first_15min_range(_df_ctx)
            _last   = _df_ctx.iloc[-2]   # last closed candle
            pdh_val  = _last.get("prev_day_high")
            pdl_val  = _last.get("prev_day_low")
            orb_high = _last.get("first15_high")
            import numpy as _np
            if pdh_val  and _np.isnan(float(pdh_val)):  pdh_val  = None
            if pdl_val  and _np.isnan(float(pdl_val)):  pdl_val  = None
            if orb_high and _np.isnan(float(orb_high)): orb_high = None

        def _level_box(label: str, val, current: float) -> str:
            if val is None or current is None:
                return f"<div class='metric-box'><div class='mbox-label'>{label}</div><div class='mbox-val'>—</div></div>"
            val_r = round(float(val), 1)
            cur_r = round(float(current), 1)
            above = cur_r >= val_r
            arrow = f"<span style='color:#22c55e;font-size:10px;'>▲ above</span>" if above else f"<span style='color:#ef4444;font-size:10px;'>▼ below</span>"
            return (f"<div class='metric-box'><div class='mbox-label'>{label}</div>"
                    f"<div class='mbox-val'>{val_r} {arrow}</div></div>")

        pdh_box  = _level_box("Prev Day High", pdh_val,  entry)
        pdl_box  = _level_box("Prev Day Low",  pdl_val,  entry)
        orb_box  = _level_box("ORB High (15m)", orb_high, entry)

        # ── ST streak label ───────────────────────────────────────────────────
        st_streak_html = _st_streak_label(sym, sig)

        # ── Dead-zone / time-rule banner ──────────────────────────────────────
        dead_banner_html = ""
        _reason_lc = reason.lower()
        if "dead zone" in _reason_lc or "expiry day" in _reason_lc or "no new entries" in _reason_lc or "opening noise" in _reason_lc:
            dead_banner_html = f"<div class='dead-banner'>⏰ Blocked: {reason}</div>"

        # Build filter pill rows
        buy_pill_html  = _filter_grid_buy(sym, sig)
        sell_pill_html = _filter_grid_sell(sym, sig)

        # Session state badge
        sess_html = _session_badge()

        # Trades left indicator colour
        left_clr = "#22c55e" if left >= 3 else ("#f59e0b" if left >= 1 else "#ef4444")

        st.markdown(f"""
        <div class="{css}">
          {dead_banner_html}
          <div class="big-label">{sym} {sess_html}</div>
          <div class="big-value {clr}">{lbl}</div>
          <div style="margin-top:6px;">{st_streak_html}</div>
          <div class="metric-row">
            <div class="metric-box"><div class="mbox-label">Entry (last close)</div><div class="mbox-val">{entry or '—'}</div></div>
            <div class="metric-box"><div class="mbox-label">Initial SL</div><div class="mbox-val">{sl or '—'}</div></div>
            <div class="metric-box"><div class="mbox-label">SL Risk (pts)</div><div class="mbox-val" style="color:#f59e0b">{sl_dist_pts}</div></div>
            <div class="metric-box"><div class="mbox-label">Target</div><div class="mbox-val">{target or '—'}</div></div>
            <div class="metric-box"><div class="mbox-label">Risk : Reward</div><div class="mbox-val">{rr_txt}</div></div>
            <div class="metric-box"><div class="mbox-label">RSI</div><div class="mbox-val">{rsi_v or '—'}</div></div>
            <div class="metric-box"><div class="mbox-label">ADX</div><div class="mbox-val">{adx_v or '—'}</div></div>
            <div class="metric-box"><div class="mbox-label">VWAP</div><div class="mbox-val">{vwap_v or '—'}</div></div>
            <div class="metric-box"><div class="mbox-label">Supertrend line</div><div class="mbox-val">{st_v or '—'}</div></div>
            <div class="metric-box"><div class="mbox-label">EMA 9</div><div class="mbox-val">{ema_f or '—'}</div></div>
            <div class="metric-box"><div class="mbox-label">EMA 21</div><div class="mbox-val">{ema_s or '—'}</div></div>
            {pdh_box}{pdl_box}{orb_box}
            <div class="metric-box"><div class="mbox-label">Trades Left</div><div class="mbox-val" style="color:{left_clr}">{left}</div></div>
          </div>
          <div style="margin-top:10px;">
            {buy_pill_html}
            {sell_pill_html}
          </div>
          <div style="margin-top:8px;font-size:12px;color:#8b949e;">
            <b>VWAP:</b> {vwap_status} &nbsp;|&nbsp; <b>Reason:</b> {reason}
          </div>
          <div class="ts">Last candle: {ct or 'N/A'} &nbsp;|&nbsp; Refreshed: {datetime.datetime.now().strftime('%H:%M:%S')}</div>
        </div>
        """, unsafe_allow_html=True)

    def _mini_chart(df: pd.DataFrame, sym: str, sig: dict, ot: dict | None):
        """Full candlestick chart: candles + Supertrend + EMA9/21 + VWAP + RSI + ADX+DI panels."""
        try:
            import plotly.graph_objects as go
            from plotly.subplots import make_subplots
            from engine.indicators import add_indicators, ema as _ema, atr as _atr
            import numpy as np

            if df.empty or len(df) < 10:
                return

            df_c = add_indicators(df.copy()).copy()
            df_c["datetime"] = pd.to_datetime(df_c["datetime"])

            # Compute +DI / −DI for the ADX panel
            period = config.ADX_PERIOD
            up_move   = df_c["high"].diff()
            down_move = -df_c["low"].diff()
            plus_dm   = np.where((up_move > down_move) & (up_move > 0),   up_move,   0.0)
            minus_dm  = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
            _atr_s    = _atr(df_c, period)
            plus_di   = 100 * pd.Series(plus_dm,  index=df_c.index).ewm(alpha=1/period, adjust=False).mean() / _atr_s
            minus_di  = 100 * pd.Series(minus_dm, index=df_c.index).ewm(alpha=1/period, adjust=False).mean() / _atr_s
            df_c["+di"] = plus_di
            df_c["-di"] = minus_di

            # Keep last 2 trading days so user can pan into yesterday
            today = df_c["datetime"].iloc[-1].date()
            two_days_ago = today - pd.Timedelta(days=3)   # 3 calendar days covers Mon+Fri weekend gap
            df_c = df_c[df_c["datetime"].dt.date >= two_days_ago].tail(150)

            # Default view: today's candles only — pan left to see yesterday
            today_start = df_c[df_c["datetime"].dt.date == today]["datetime"].iloc[0] - pd.Timedelta(minutes=10)
            x_default_end = df_c["datetime"].iloc[-1] + pd.Timedelta(minutes=10)

            # ── Split Supertrend into green/red segments ──────────────────────
            st_green_y = df_c["st_value"].where(df_c["st_signal"] == 1)
            st_red_y   = df_c["st_value"].where(df_c["st_signal"] == -1)

            fig = make_subplots(
                rows=3, cols=1,
                shared_xaxes=True,
                row_heights=[0.58, 0.21, 0.21],
                vertical_spacing=0.03,
                subplot_titles=("", "RSI (14)", "ADX + DI"),
            )

            # ── Row 1: Candlesticks ───────────────────────────────────────────
            fig.add_trace(go.Candlestick(
                x=df_c["datetime"],
                open=df_c["open"], high=df_c["high"],
                low=df_c["low"],  close=df_c["close"],
                name="Price",
                increasing_line_color="#22c55e",
                decreasing_line_color="#ef4444",
                increasing_fillcolor="#22c55e",
                decreasing_fillcolor="#ef4444",
            ), row=1, col=1)

            # Supertrend GREEN
            fig.add_trace(go.Scatter(
                x=df_c["datetime"], y=st_green_y,
                mode="lines", name="ST Green",
                line=dict(color="#22c55e", width=2),
            ), row=1, col=1)

            # Supertrend RED
            fig.add_trace(go.Scatter(
                x=df_c["datetime"], y=st_red_y,
                mode="lines", name="ST Red",
                line=dict(color="#ef4444", width=2),
            ), row=1, col=1)

            # EMA9 (fast) — teal thin line
            fig.add_trace(go.Scatter(
                x=df_c["datetime"], y=df_c["ema_fast"],
                mode="lines", name="EMA 9",
                line=dict(color="#2dd4bf", width=1, dash="dot"),
            ), row=1, col=1)

            # EMA21 (slow) — amber
            fig.add_trace(go.Scatter(
                x=df_c["datetime"], y=df_c["ema_slow"],
                mode="lines", name="EMA 21",
                line=dict(color="#f59e0b", width=1.2, dash="dot"),
            ), row=1, col=1)

            # VWAP
            fig.add_trace(go.Scatter(
                x=df_c["datetime"], y=df_c["vwap"],
                mode="lines", name="VWAP",
                line=dict(color="#818cf8", width=1.2, dash="dash"),
            ), row=1, col=1)

            # ── Entry / SL / Target lines (if in trade) ───────────────────────
            if ot:
                x0, x1 = df_c["datetime"].iloc[0], df_c["datetime"].iloc[-1]
                clr = "#22c55e" if ot["direction"] == SIGNAL_BUY else "#ef4444"
                for val, label, color in [
                    (ot["entry_price"], "Entry",  clr),
                    (ot["sl"],          "SL",     "#f59e0b"),
                    (ot.get("target"),  "Target", "#38bdf8"),
                ]:
                    if val:
                        fig.add_shape(type="line", x0=x0, x1=x1, y0=val, y1=val,
                                      line=dict(color=color, width=1, dash="dot"),
                                      row=1, col=1)
                        fig.add_annotation(x=x1, y=val, text=f" {label} {val}",
                                           showarrow=False, xanchor="left",
                                           font=dict(color=color, size=11),
                                           row=1, col=1)

            # ── Buy/Sell signal marker on last candle ─────────────────────────
            sig_dir = sig.get("signal")
            if sig_dir in (SIGNAL_BUY, SIGNAL_SELL):
                marker_y  = df_c["low"].iloc[-1]  * 0.9995 if sig_dir == SIGNAL_BUY else df_c["high"].iloc[-1] * 1.0005
                marker_sym = "triangle-up" if sig_dir == SIGNAL_BUY else "triangle-down"
                marker_clr = "#22c55e"     if sig_dir == SIGNAL_BUY else "#ef4444"
                fig.add_trace(go.Scatter(
                    x=[df_c["datetime"].iloc[-1]], y=[marker_y],
                    mode="markers",
                    marker=dict(symbol=marker_sym, size=14, color=marker_clr),
                    name=sig_dir, showlegend=False,
                ), row=1, col=1)

            # ── Row 2: RSI ────────────────────────────────────────────────────
            fig.add_trace(go.Scatter(
                x=df_c["datetime"], y=df_c["rsi"],
                mode="lines", name="RSI",
                line=dict(color="#a78bfa", width=1.5),
            ), row=2, col=1)

            # RSI buy zone shading (between RSI_BUY_LOW and RSI_BUY_HIGH)
            from engine.signal_engine import _rsi_buy_high as _rbh
            rsi_buy_hi = _rbh(sym)
            fig.add_hrect(
                y0=config.RSI_BUY_LOW, y1=rsi_buy_hi,
                fillcolor="rgba(34,197,94,0.08)", line_width=0,
                row=2, col=1,
            )
            # RSI sell zone shading (between RSI_SELL_LOW and RSI_SELL_HIGH)
            from engine.signal_engine import _rsi_sell_high as _rsh
            rsi_sell_hi = _rsh(sym)
            fig.add_hrect(
                y0=config.RSI_SELL_LOW, y1=rsi_sell_hi,
                fillcolor="rgba(239,68,68,0.08)", line_width=0,
                row=2, col=1,
            )
            # RSI reference lines
            for level, color in [
                (rsi_sell_hi,          "#ef4444"),   # top of SELL zone
                (50,                   "#57606a"),   # midline
                (config.RSI_BUY_LOW,   "#22c55e"),   # bottom of BUY zone
            ]:
                fig.add_hline(y=level, line_dash="dot", line_color=color,
                              line_width=1, row=2, col=1)

            # ── Row 3: ADX + +DI / −DI ────────────────────────────────────────
            fig.add_trace(go.Scatter(
                x=df_c["datetime"], y=df_c["adx"],
                mode="lines", name="ADX",
                line=dict(color="#38bdf8", width=1.8),
            ), row=3, col=1)
            fig.add_trace(go.Scatter(
                x=df_c["datetime"], y=df_c["+di"],
                mode="lines", name="+DI",
                line=dict(color="#22c55e", width=1, dash="dot"),
            ), row=3, col=1)
            fig.add_trace(go.Scatter(
                x=df_c["datetime"], y=df_c["-di"],
                mode="lines", name="−DI",
                line=dict(color="#ef4444", width=1, dash="dot"),
            ), row=3, col=1)
            # ADX threshold and max lines
            fig.add_hline(y=config.ADX_THRESHOLD, line_dash="dot",
                          line_color="#f59e0b", line_width=1, row=3, col=1)
            fig.add_hline(y=config.ADX_MAX, line_dash="dot",
                          line_color="#ef4444", line_width=1, row=3, col=1)
            # ADX dead zone shading
            adx_dz_lo = getattr(config, "ADX_DEAD_ZONE_LOW", None)
            adx_dz_hi = getattr(config, "ADX_DEAD_ZONE_HIGH", None)
            if adx_dz_lo and adx_dz_hi:
                fig.add_hrect(
                    y0=adx_dz_lo, y1=adx_dz_hi,
                    fillcolor="rgba(245,158,11,0.10)", line_width=0,
                    row=3, col=1,
                )

            # ── Layout ────────────────────────────────────────────────────────
            fig.update_layout(
                height=560,
                paper_bgcolor="#0f1117",
                plot_bgcolor="#0f1117",
                font=dict(color="#e6edf3", size=11),
                showlegend=True,
                legend=dict(orientation="h", y=1.02, x=0,
                            bgcolor="rgba(0,0,0,0)", font=dict(size=10)),
                margin=dict(l=10, r=80, t=24, b=10),
                xaxis_rangeslider_visible=False,
            )
            for row_n in [1, 2, 3]:
                fig.update_xaxes(
                    gridcolor="#1e2430", zeroline=False,
                    showticklabels=(row_n == 3),
                    range=[today_start, x_default_end],
                    row=row_n, col=1,
                )
                fig.update_yaxes(
                    gridcolor="#1e2430", zeroline=False, row=row_n, col=1,
                )

            # Y-axis labels
            fig.update_yaxes(title_text="Price",      title_font_size=10, row=1, col=1)
            fig.update_yaxes(title_text="RSI",        title_font_size=10, row=2, col=1)
            fig.update_yaxes(title_text="ADX / DI",   title_font_size=10, row=3, col=1)

            st.plotly_chart(fig, use_container_width=True, config={
                "displayModeBar": True,
                "modeBarButtonsToRemove": ["select2d", "lasso2d", "autoScale2d"],
                "scrollZoom": True,
            })

        except Exception as _chart_err:
            st.caption(f"Chart unavailable: {_chart_err}")

    def _trade_log():
        if not os.path.isfile(config.SIGNAL_LOG):
            st.info("No signals logged yet today.")
            return
        df = pd.read_csv(config.SIGNAL_LOG)
        if df.empty:
            st.info("No signals logged yet today.")
        else:
            st.dataframe(df.sort_values("timestamp", ascending=False).head(50),
                         use_container_width=True, hide_index=True)

    syms = list(config.INSTRUMENTS.keys())
    c1, c2 = st.columns(2)
    with c1:
        st.markdown(f"#### {config.INSTRUMENTS[syms[0]]['symbol']}")
        _signal_card(syms[0], st.session_state.signals.get(syms[0], {}))
    with c2:
        st.markdown(f"#### {config.INSTRUMENTS[syms[1]]['symbol']}")
        _signal_card(syms[1], st.session_state.signals.get(syms[1], {}))

    # Index charts — one per instrument, inside expanders (same layout as Stock Charts)
    st.markdown("---")
    st.markdown("### 📊 Index Charts — 5-min Candlestick + Supertrend + VWAP + EMA + RSI + ADX")
    for sym in syms:
        with st.expander(f"📈 {config.INSTRUMENTS[sym]['symbol']} Chart", expanded=False):
            _mini_chart(
                st.session_state.candles.get(sym, pd.DataFrame()),
                sym,
                st.session_state.signals.get(sym, {}),
                st.session_state.open_trades.get(sym),
            )

    st.markdown("---")
    b1, b2, _ = st.columns([1, 1, 5])
    with b1:
        if st.button("🔄 Refresh Now", key="live_refresh_btn"):
            st.rerun()
    with b2:
        if st.button("🔁 Reset Trade Count", key="live_reset"):
            st.session_state.trade_counts = {s: 0 for s in config.INSTRUMENTS}
            st.success("Trade counts reset.")

    st.markdown("### 📋 Today's Signal Log")
    _trade_log()

    st.markdown(
        "<div style='text-align:center;color:#57606a;font-size:11px;"
        "border-top:1px solid #30363d;padding-top:12px;margin-top:32px;'>"
        "Nifty &amp; Bank Nifty Signal App &nbsp;|&nbsp; Angel One SmartAPI"
        " &nbsp;|&nbsp; For informational purposes only — not financial advice."
        "</div>", unsafe_allow_html=True)



# ─── Diagnostics helper (shared between tabs) ────────────────────────────────
def _render_diagnostics(sym: str, diag: pd.DataFrame):
    """Show a collapsible table of every evaluated candle with filter pass/fail."""
    with st.expander(f"🔍 Diagnostics — {sym}: why each candle passed / failed ({len(diag)} candles evaluated)", expanded=False):
        # Summary of blocking reasons
        if "note" in diag.columns:
            reason_counts = diag[diag["signal"] == "NONE"]["note"].value_counts().head(10)
            if not reason_counts.empty:
                st.markdown("**Top reasons signals were blocked:**")
                for reason, cnt in reason_counts.items():
                    # shorten long notes
                    short = reason[:100] + "…" if len(reason) > 100 else reason
                    st.markdown(f"- `{short}` — **{cnt} candles**")
                st.markdown("---")

        # ADX distribution insight
        if "adx" in diag.columns:
            adx_below = (diag["adx"] < config.ADX_THRESHOLD).sum()
            adx_above = (diag["adx"] >= config.ADX_THRESHOLD).sum()
            c1, c2, c3 = st.columns(3)
            c1.metric("Candles with ADX ✅", adx_above)
            c2.metric(f"Candles with ADX ❌ (<{config.ADX_THRESHOLD})", adx_below)
            c3.metric("Signals fired", (diag["signal"] != "NONE").sum())

            st.markdown("**ADX over time (threshold line = dashed)**")
            adx_chart = diag[["datetime", "adx"]].set_index("datetime")
            st.line_chart(adx_chart, height=150, use_container_width=True)

        # Full candle table (last 100 session candles)
        st.markdown("**Last 100 session candles — indicator values**")
        show_cols = ["datetime", "close", "adx", "adx_ok", "rsi", "vwap",
                     "ema21", "price>ema", "price>vwap", "st_signal", "signal", "note"]
        show_cols = [c for c in show_cols if c in diag.columns]
        st.dataframe(
            diag[show_cols].tail(100).sort_values("datetime", ascending=False),
            use_container_width=True, hide_index=True
        )


# ══════════════════════════════════════════════════
#  TAB 2 — BACKTEST
# ══════════════════════════════════════════════════
with tab_bt:

    st.markdown("## 🔬 Backtest — Nifty & Bank Nifty")
    st.markdown(
        "<span style='color:#8b949e;font-size:13px;'>"
        "Replays strategy candle-by-candle on real historical data · No look-ahead bias"
        f" &nbsp;|&nbsp; VWAP daily reset · Entry 09:40–13:30"
        f" · Hard SL cap: Nifty {config.SL_CAP_PTS['NIFTY']}pts / BankNifty 2×ATR(14)"
        " &nbsp;|&nbsp; Exit: Supertrend trail + RSI momentum"
        "</span>", unsafe_allow_html=True)
    st.markdown("---")

    # ── controls ──────────────────────────────────────────────────────────────
    bc1, bc2, bc3 = st.columns([1, 1, 2])
    with bc1:
        days_back = st.selectbox(
            "Days of history",
            [10, 20, 30, 60, 90],
            index=1,
            key="bt_days",
            help="30/60/90 days = multiple API calls stitched together (~2-5s extra per chunk)"
        )
    with bc2:
        syms_sel = st.multiselect("Symbols", list(config.INSTRUMENTS.keys()),
                                  default=list(config.INSTRUMENTS.keys()), key="bt_syms")
    with bc3:
        st.markdown("<br>", unsafe_allow_html=True)
        run_bt = st.button("▶  Run Backtest", type="primary", key="bt_run")

    # ── run ───────────────────────────────────────────────────────────────────
    if run_bt:
        if not syms_sel:
            st.warning("Select at least one symbol.")
        else:
            api = _ensure_api("Logging into Angel One for backtest data…")
            if api:
                st.session_state.bt_results = {}
                for sym in syms_sel:
                    cfg = config.INSTRUMENTS[sym]
                    chunks_needed = max(1, (days_back + 3) // 30 + (1 if (days_back + 3) % 30 else 0))
                    fetch_msg = (f"Fetching {days_back} days for {sym} "
                                 f"({'1 API call' if chunks_needed == 1 else str(chunks_needed) + ' API calls stitched'})…")
                    with st.spinner(fetch_msg):
                        df_raw = fetch_candles(api, cfg["token"], cfg["exchange"],
                                               interval="FIVE_MINUTE",
                                               days_back=days_back + 3)
                    if df_raw.empty:
                        st.error(f"No data for {sym}. Check token or market hours.")
                        continue
                    with st.spinner(f"Running backtest for {sym}…"):
                        trades, diag = run_backtest(df_raw, sym)
                        stats  = summary_stats(trades) if not trades.empty else {}
                    st.session_state.bt_results[sym] = {
                        "trades": trades, "stats": stats, "df_raw": df_raw, "diag": diag
                    }

    # ── display ───────────────────────────────────────────────────────────────
    if st.session_state.bt_results:

        for sym, res in st.session_state.bt_results.items():
            trades = res["trades"]
            stats  = res["stats"]
            df_raw = res["df_raw"]
            diag   = res.get("diag", pd.DataFrame())

            st.markdown(f"### {sym} — {config.INSTRUMENTS[sym]['symbol']}")

            if trades.empty or not stats:
                st.warning(
                    f"⚠️ No signals generated for **{sym}**. "
                    f"ADX threshold: **{config.ADX_THRESHOLD}**, "
                    f"RSI buy: **{config.RSI_BUY_LOW}–{config.RSI_BUY_HIGH}**, "
                    f"RSI sell: **{config.RSI_SELL_LOW}–{config.RSI_SELL_HIGH}**. "
                    "See diagnostics below to understand which filter blocked signals."
                )
                if not diag.empty:
                    _render_diagnostics(sym, diag)
                st.markdown("---")
                continue

            s = stats
            total_pts_clr = "win-clr" if s["total_points"] >= 0 else "loss-clr"
            wr_clr        = "win-clr" if s["win_rate_pct"] >= 55 else ("warn-clr" if s["win_rate_pct"] >= 45 else "loss-clr")

            # Compute extra stats not in summary_stats
            _wins_pts  = trades.loc[trades["result"] == "WIN",  "points"]
            _loss_pts  = trades.loc[trades["result"] == "LOSS", "points"]
            gross_profit = _wins_pts.sum()  if not _wins_pts.empty else 0
            gross_loss   = abs(_loss_pts.sum()) if not _loss_pts.empty else 0
            profit_factor = round(gross_profit / gross_loss, 2) if gross_loss else "∞"
            pf_clr  = "win-clr" if isinstance(profit_factor, str) or profit_factor >= 1.5 else (
                       "warn-clr" if profit_factor >= 1.0 else "loss-clr")
            expectancy = round(s["total_points"] / s["total_trades"], 2) if s["total_trades"] else 0
            exp_clr = "win-clr" if expectancy > 0 else "loss-clr"

            # ── stat cards row 1 ──────────────────────────────────────────────
            cards1 = [
                ("Total Trades",      s["total_trades"],               "neu-clr"),
                ("Wins ✅",           s["wins"],                       "win-clr"),
                ("Losses ❌",         s["losses"],                     "loss-clr"),
                ("Breakeven",         s["breakeven"],                  "neu-clr"),
                ("Win Rate",          f"{s['win_rate_pct']}%",         wr_clr),
                ("Total Points",      s["total_points"],               total_pts_clr),
            ]
            cols1 = st.columns(len(cards1))
            for col, (lbl, val, clr) in zip(cols1, cards1):
                col.markdown(
                    f"<div class='stat-card'>"
                    f"<div class='stat-val {clr}'>{val}</div>"
                    f"<div class='stat-lbl'>{lbl}</div>"
                    f"</div>", unsafe_allow_html=True)

            st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

            # ── stat cards row 2 ──────────────────────────────────────────────
            cards2 = [
                ("Avg Win (pts)",     s["avg_win_pts"],                "win-clr"),
                ("Avg Loss (pts)",    s["avg_loss_pts"],               "loss-clr"),
                ("Risk : Reward",     s["risk_reward"],                "neu-clr"),
                ("Profit Factor",     profit_factor,                   pf_clr),
                ("Expectancy (pts)",  expectancy,                      exp_clr),
                ("Max Drawdown",      s.get("max_drawdown", "—"),      "warn-clr"),
                ("Max Consec Loss",   s.get("max_consec_loss", "—"),   "warn-clr"),
            ]
            cols2 = st.columns(len(cards2))
            for col, (lbl, val, clr) in zip(cols2, cards2):
                col.markdown(
                    f"<div class='stat-card'>"
                    f"<div class='stat-val {clr}'>{val}</div>"
                    f"<div class='stat-lbl'>{lbl}</div>"
                    f"</div>", unsafe_allow_html=True)

            st.markdown("<br>", unsafe_allow_html=True)

            # ── Win vs Loss hold time ─────────────────────────────────────────
            if "entry_time" in trades.columns and "exit_time" in trades.columns:
                _t = trades.copy()
                _t["_entry_dt"] = pd.to_datetime(_t["entry_time"], errors="coerce")
                _t["_exit_dt"]  = pd.to_datetime(_t["exit_time"],  errors="coerce")
                _t["_hold_min"] = (_t["_exit_dt"] - _t["_entry_dt"]).dt.total_seconds() / 60
                _wins_h  = _t.loc[_t["result"] == "WIN",  "_hold_min"].dropna()
                _loss_h  = _t.loc[_t["result"] == "LOSS", "_hold_min"].dropna()
                def _fmtmin(m):
                    m = int(m)
                    return f"{m // 60}h {m % 60}m" if m >= 60 else f"{m}m"
                avg_win_hold  = _fmtmin(_wins_h.mean())  if len(_wins_h)  else "—"
                avg_loss_hold = _fmtmin(_loss_h.mean())  if len(_loss_h)  else "—"
                _ratio = (f"1 : {round(_wins_h.mean() / _loss_h.mean(), 1)}"
                          if len(_wins_h) and len(_loss_h) and _loss_h.mean() > 0 else "—")
                _ht1, _ht2, _ht3 = st.columns(3)
                _ht1.markdown(
                    f"<div class='stat-card'><div class='stat-val win-clr'>{avg_win_hold}</div>"
                    f"<div class='stat-lbl'>Avg WIN hold time</div></div>", unsafe_allow_html=True)
                _ht2.markdown(
                    f"<div class='stat-card'><div class='stat-val loss-clr'>{avg_loss_hold}</div>"
                    f"<div class='stat-lbl'>Avg LOSS hold time</div></div>", unsafe_allow_html=True)
                _ht3.markdown(
                    f"<div class='stat-card'><div class='stat-val neu-clr'>{_ratio}</div>"
                    f"<div class='stat-lbl'>Win hold : Loss hold ratio</div></div>", unsafe_allow_html=True)
                st.markdown("<div style='height:6px'></div>", unsafe_allow_html=True)

            # ── charts ────────────────────────────────────────────────────────
            ch1, ch2 = st.columns(2)
            with ch1:
                st.markdown("**📈 Equity Curve + Drawdown**")
                try:
                    import plotly.graph_objects as go
                    from plotly.subplots import make_subplots as _msp
                    _cum  = trades["points"].cumsum().reset_index(drop=True)
                    _peak = _cum.cummax()
                    _dd   = _cum - _peak
                    _eq_fig = _msp(rows=2, cols=1, shared_xaxes=True,
                                   row_heights=[0.65, 0.35], vertical_spacing=0.05)
                    _eq_fig.add_trace(go.Scatter(
                        y=_cum.values, mode="lines", name="Equity",
                        line=dict(color="#22c55e", width=2),
                        fill="tozeroy",
                        fillcolor="rgba(34,197,94,0.08)",
                    ), row=1, col=1)
                    _eq_fig.add_hline(y=0, line_dash="dot", line_color="#57606a",
                                      line_width=1, row=1, col=1)
                    _eq_fig.add_trace(go.Scatter(
                        y=_dd.values, mode="lines", name="Drawdown",
                        line=dict(color="#ef4444", width=1.5),
                        fill="tozeroy",
                        fillcolor="rgba(239,68,68,0.12)",
                    ), row=2, col=1)
                    _eq_fig.update_layout(
                        height=260, paper_bgcolor="#0f1117", plot_bgcolor="#0f1117",
                        font=dict(color="#e6edf3", size=10),
                        margin=dict(l=6, r=6, t=10, b=6),
                        showlegend=True,
                        legend=dict(orientation="h", y=1.05, x=0,
                                    bgcolor="rgba(0,0,0,0)", font=dict(size=9)),
                    )
                    for _rn in [1, 2]:
                        _eq_fig.update_xaxes(gridcolor="#1e2430", zeroline=False, row=_rn, col=1)
                        _eq_fig.update_yaxes(gridcolor="#1e2430", zeroline=False, row=_rn, col=1)
                    _eq_fig.update_yaxes(title_text="pts",  title_font_size=9, row=1, col=1)
                    _eq_fig.update_yaxes(title_text="DD",   title_font_size=9, row=2, col=1)
                    st.plotly_chart(_eq_fig, use_container_width=True,
                                    config={"displayModeBar": False})
                except Exception:
                    _cum2 = pd.DataFrame({"Cumulative Points": trades["points"].cumsum().values})
                    st.line_chart(_cum2, height=200, use_container_width=True)
            with ch2:
                st.markdown("**📊 Daily P&L (Points)**")
                dpnl = (trades.groupby("date")["points"].sum()
                        .reset_index().rename(columns={"points": "Points"}))
                dpnl["date"] = dpnl["date"].astype(str)
                st.bar_chart(dpnl.set_index("date")["Points"], height=200, use_container_width=True)

            # ── buy/sell breakdown + exit reason breakdown ────────────────────
            st.markdown("**Buy vs Sell — and Exit Reason Breakdown**")
            d1, d2, d3 = st.columns(3)
            for col_w, direction in zip([d1, d2], ["BUY", "SELL"]):
                sub = trades[trades["direction"] == direction]
                if sub.empty:
                    col_w.info(f"No {direction} trades.")
                    continue
                w   = (sub["result"] == "WIN").sum()
                l   = (sub["result"] == "LOSS").sum()
                wr  = round(w / len(sub) * 100, 1)
                pts = round(sub["points"].sum(), 2)
                avg = round(sub["points"].mean(), 1)
                pts_clr = "#22c55e" if pts >= 0 else "#ef4444"
                col_w.markdown(
                    f"**{direction}** — {len(sub)} trades\n\n"
                    f"<span style='color:#22c55e'>{w}W</span> / "
                    f"<span style='color:#ef4444'>{l}L</span> &nbsp; "
                    f"WR: **{wr}%** &nbsp; Net: <span style='color:{pts_clr}'><b>{pts} pts</b></span> "
                    f"&nbsp; Avg: {avg} pts/trade",
                    unsafe_allow_html=True)

            # Exit reason summary
            with d3:
                if "exit_reason" in trades.columns:
                    er = (trades.groupby("exit_reason")
                          .agg(Count=("points","count"), Points=("points","sum"))
                          .reset_index())
                    er["Points"] = er["Points"].round(1)
                    er["Avg pts"] = (er["Points"] / er["Count"]).round(1)
                    st.markdown("**Exit Reasons**")
                    st.dataframe(er, use_container_width=True, hide_index=True, height=140)

            # ── trade table ───────────────────────────────────────────────────
            with st.expander("📋 Trade-by-Trade Log", expanded=False):
                avail_cols = ["date", "direction", "entry_time", "entry_price",
                              "exit_time", "exit_price", "exit_reason",
                              "points", "result", "rsi", "adx", "vwap"]
                disp = trades[[c for c in avail_cols if c in trades.columns]].copy()
                if "entry_time" in disp.columns:
                    disp["entry_time"] = pd.to_datetime(disp["entry_time"]).dt.strftime("%H:%M")
                if "exit_time" in disp.columns:
                    disp["exit_time"]  = pd.to_datetime(disp["exit_time"]).dt.strftime("%H:%M")

                def _cr(val):
                    if val == "WIN":  return "background-color:#0d3321;color:#22c55e"
                    if val == "LOSS": return "background-color:#3b0f0f;color:#ef4444"
                    return ""
                def _cp(val):
                    try: return "color:#22c55e" if float(val) > 0 else ("color:#ef4444" if float(val) < 0 else "")
                    except: return ""

                st.dataframe(
                    disp.style.map(_cr, subset=["result"]).map(_cp, subset=["points"]),
                    use_container_width=True, hide_index=True)

            # ── weekly P&L table ──────────────────────────────────────────────
            if "date" in trades.columns:
                with st.expander("📅 Weekly P&L Summary", expanded=False):
                    _wt = trades.copy()
                    _wt["_date"] = pd.to_datetime(_wt["date"], errors="coerce")
                    _wt["_week"] = _wt["_date"].dt.to_period("W").astype(str)
                    _wgrp = (_wt.groupby("_week")
                             .agg(
                                 Trades  = ("points", "count"),
                                 Wins    = ("result", lambda x: (x == "WIN").sum()),
                                 Points  = ("points", "sum"),
                             )
                             .reset_index()
                             .rename(columns={"_week": "Week"}))
                    _wgrp["WR %"]    = (_wgrp["Wins"] / _wgrp["Trades"] * 100).round(1)
                    _wgrp["Avg pts"] = (_wgrp["Points"] / _wgrp["Trades"]).round(1)
                    _wgrp["Points"]  = _wgrp["Points"].round(1)
                    def _wpts(v):
                        try: return "color:#22c55e;font-weight:700" if float(v) > 0 else ("color:#ef4444;font-weight:700" if float(v) < 0 else "")
                        except: return ""
                    def _wwr(v):
                        try:
                            f = float(v)
                            if f >= 60: return "background-color:#0d3321;color:#22c55e;font-weight:700"
                            if f >= 45: return "color:#f59e0b"
                            return "background-color:#3b0f0f;color:#ef4444;font-weight:700"
                        except: return ""
                    st.dataframe(
                        _wgrp.style
                            .map(_wpts, subset=["Points", "Avg pts"])
                            .map(_wwr,  subset=["WR %"]),
                        use_container_width=True, hide_index=True,
                    )
                    st.caption("Each row = one calendar week. Helps spot weeks of consistent wins vs bad regimes.")

            # ── per-hour-slot Win Rate heatmap ────────────────────────────────
            if "entry_time" in trades.columns:
                with st.expander("⏱ Entry Time Slot Analysis — Win Rate by 30-min window", expanded=True):
                    _sl = trades.copy()
                    _sl["_et"] = pd.to_datetime(_sl["entry_time"], errors="coerce")
                    _sl["_slot"] = _sl["_et"].dt.floor("30min").dt.strftime("%H:%M")
                    _slot_grp = (
                        _sl.groupby("_slot")
                        .agg(
                            Trades  = ("points",  "count"),
                            Wins    = ("result",  lambda x: (x == "WIN").sum()),
                            Points  = ("points",  "sum"),
                        )
                        .reset_index()
                        .rename(columns={"_slot": "Slot"})
                    )
                    _slot_grp["WR %"]    = (_slot_grp["Wins"] / _slot_grp["Trades"] * 100).round(1)
                    _slot_grp["Avg pts"] = (_slot_grp["Points"] / _slot_grp["Trades"]).round(1)
                    _slot_grp["Points"]  = _slot_grp["Points"].round(1)

                    def _slot_wr_style(val):
                        try:
                            v = float(val)
                            if v >= 60:   return "background-color:#0d3321;color:#22c55e;font-weight:700"
                            if v >= 45:   return "color:#f59e0b"
                            return "background-color:#3b0f0f;color:#ef4444;font-weight:700"
                        except: return ""
                    def _slot_pts_style(val):
                        try: return "color:#22c55e" if float(val) > 0 else ("color:#ef4444" if float(val) < 0 else "")
                        except: return ""

                    st.dataframe(
                        _slot_grp.style
                            .map(_slot_wr_style,  subset=["WR %"])
                            .map(_slot_pts_style, subset=["Points", "Avg pts"]),
                        use_container_width=True, hide_index=True,
                    )
                    st.caption(
                        "Slots with WR < 45% highlighted red. "
                        "Compare against active dead-zone rules: "
                        f"BNKN {config.BNKN_SKIP_SLOT_START}–{config.BNKN_SKIP_SLOT_END} · "
                        f"NIFTY BUY {config.NIFTY_BUY_SKIP_START}–{config.NIFTY_BUY_SKIP_END}."
                    )

            # ── diagnostics expander ──────────────────────────────────────────
            if not diag.empty:
                _render_diagnostics(sym, diag)

            # ── price chart ───────────────────────────────────────────────────
            st.markdown("**📉 5-min Close Price History**")
            st.line_chart(df_raw[["datetime","close"]].set_index("datetime").tail(500),
                          height=180, use_container_width=True)
            st.markdown("---")

        # ── download ──────────────────────────────────────────────────────────
        non_empty = [r["trades"] for r in st.session_state.bt_results.values() if not r["trades"].empty]
        if non_empty:
            all_trades = pd.concat(non_empty, ignore_index=True)
        else:
            all_trades = pd.DataFrame()
        if not all_trades.empty:
            st.download_button(
                "⬇  Download All Trades CSV",
                data=all_trades.to_csv(index=False).encode(),
                file_name=f"backtest_{datetime.date.today()}.csv",
                mime="text/csv",
                key="bt_download")

    else:
        st.info("👆 Select settings above and click **▶ Run Backtest** to fetch real data and replay the strategy.")

    st.markdown(
        "<div style='text-align:center;color:#57606a;font-size:11px;"
        "border-top:1px solid #30363d;padding-top:12px;margin-top:32px;'>"
        "Uses real Angel One historical data · No look-ahead bias · "
        "Past performance does not guarantee future results."
        "</div>", unsafe_allow_html=True)


# ══════════════════════════════════════════════════
#  TAB 3 — JOURNAL
# ══════════════════════════════════════════════════
with tab_journal:

    st.markdown("## 📒 Trade Journal")
    st.markdown(
        "<span style='color:#8b949e;font-size:13px;'>"
        "Daily P&amp;L, streaks, and performance breakdown from live + backtest trade logs."
        "</span>", unsafe_allow_html=True)
    st.markdown("---")

    # ── Load trade log ────────────────────────────────────────────────────────
    def _load_trade_log() -> pd.DataFrame:
        """Load trade_log.csv produced by backtest CSV download or run_signals.py."""
        path = config.TRADE_LOG
        if not os.path.isfile(path):
            return pd.DataFrame()
        try:
            df = pd.read_csv(path)
            if df.empty:
                return df
            # normalise columns
            df.columns = [c.strip().lower() for c in df.columns]
            if "date" in df.columns:
                df["date"] = pd.to_datetime(df["date"]).dt.date
            if "points" in df.columns:
                df["points"] = pd.to_numeric(df["points"], errors="coerce").fillna(0)
            return df
        except Exception as e:
            st.warning(f"Could not read trade log: {e}")
            return pd.DataFrame()

    # Upload or auto-load
    jc1, jc2 = st.columns([3, 1])
    with jc1:
        uploaded = st.file_uploader(
            "📂 Upload a backtest CSV (or use auto-detected trade_log.csv below)",
            type="csv", key="journal_upload")
    with jc2:
        st.markdown("<br>", unsafe_allow_html=True)
        if st.button("🔄 Reload from disk", key="journal_reload"):
            st.rerun()

    if uploaded is not None:
        try:
            jdf = pd.read_csv(uploaded)
            jdf.columns = [c.strip().lower() for c in jdf.columns]
            if "date" in jdf.columns:
                jdf["date"] = pd.to_datetime(jdf["date"]).dt.date
            if "points" in jdf.columns:
                jdf["points"] = pd.to_numeric(jdf["points"], errors="coerce").fillna(0)
        except Exception as e:
            st.error(f"Could not parse uploaded file: {e}")
            jdf = pd.DataFrame()
    else:
        jdf = _load_trade_log()

    if jdf.empty:
        st.info(
            "📭 No trade data found yet.\n\n"
            "**To populate this tab:**\n"
            "1. Run the **Backtest** tab → click ⬇ Download All Trades CSV → "
            "save it as `logs/trade_log.csv`, or\n"
            "2. Upload any backtest CSV using the uploader above."
        )
    else:
        # ── Ensure required columns exist ─────────────────────────────────────
        required = {"date", "symbol", "direction", "points", "result"}
        missing  = required - set(jdf.columns)
        if missing:
            st.warning(f"CSV is missing columns: {missing}. Upload a full backtest CSV.")
        else:
            # ── Symbol filter ─────────────────────────────────────────────────
            syms_avail = sorted(jdf["symbol"].unique().tolist()) if "symbol" in jdf.columns else []
            sym_filter = st.multiselect("Filter by symbol", syms_avail,
                                        default=syms_avail, key="journal_sym")
            jdf = jdf[jdf["symbol"].isin(sym_filter)] if sym_filter else jdf

            # ── Top KPIs ──────────────────────────────────────────────────────
            total_t = len(jdf)
            total_w = (jdf["result"] == "WIN").sum()
            total_l = (jdf["result"] == "LOSS").sum()
            total_p = round(jdf["points"].sum(), 2)
            wr      = round(total_w / total_t * 100, 1) if total_t else 0
            avg_day_pts = round(jdf.groupby("date")["points"].sum().mean(), 2) if total_t else 0

            # Max drawdown
            cum  = jdf["points"].cumsum()
            peak = cum.cummax()
            max_dd = round((cum - peak).min(), 2)

            # Streak
            streak = cur = 0
            for r in jdf["result"]:
                cur = cur + 1 if r == "LOSS" else 0
                streak = max(streak, cur)

            kpi_cards = [
                ("Total Trades",    total_t,         "neu-clr"),
                ("Wins ✅",         total_w,         "win-clr"),
                ("Losses ❌",       total_l,         "loss-clr"),
                ("Win Rate",        f"{wr}%",        "win-clr" if wr >= 55 else "warn-clr"),
                ("Total Points",    total_p,         "win-clr" if total_p >= 0 else "loss-clr"),
                ("Avg Day P&L",     avg_day_pts,     "win-clr" if avg_day_pts >= 0 else "loss-clr"),
                ("Max Drawdown",    max_dd,          "warn-clr"),
                ("Max Consec Loss", streak,          "warn-clr" if streak >= 3 else "neu-clr"),
            ]
            cols_kpi = st.columns(len(kpi_cards))
            for col, (lbl, val, clr) in zip(cols_kpi, kpi_cards):
                col.markdown(
                    f"<div class='stat-card'>"
                    f"<div class='stat-val {clr}'>{val}</div>"
                    f"<div class='stat-lbl'>{lbl}</div>"
                    f"</div>", unsafe_allow_html=True)

            st.markdown("<br>", unsafe_allow_html=True)

            # ── Daily P&L chart ───────────────────────────────────────────────
            daily = (jdf.groupby(["date", "symbol"])["points"]
                     .sum().reset_index()
                     .rename(columns={"points": "Points"}))
            daily["date"] = daily["date"].astype(str)

            ch_j1, ch_j2 = st.columns(2)
            with ch_j1:
                st.markdown("**📅 Daily P&L (all symbols combined)**")
                dpnl_all = daily.groupby("date")["Points"].sum().reset_index()
                dpnl_all["color"] = dpnl_all["Points"].apply(lambda x: "#22c55e" if x >= 0 else "#ef4444")
                st.bar_chart(dpnl_all.set_index("date")["Points"], height=220,
                             use_container_width=True)
            with ch_j2:
                st.markdown("**📈 Cumulative Equity Curve**")
                eq_all = pd.DataFrame({"Cumulative Points": jdf["points"].cumsum().values})
                st.line_chart(eq_all, height=220, use_container_width=True)

            # ── Rolling Win Rate ──────────────────────────────────────────────
            st.markdown("**📉 Rolling Win Rate — 5-trade & 10-trade windows**")
            _wr_series = (jdf["result"] == "WIN").astype(int).reset_index(drop=True)
            _rwr_df = pd.DataFrame()
            if len(_wr_series) >= 5:
                _rwr_df["WR 5-trade (%)"]  = (_wr_series.rolling(5).mean()  * 100).round(1)
            if len(_wr_series) >= 10:
                _rwr_df["WR 10-trade (%)"] = (_wr_series.rolling(10).mean() * 100).round(1)
            if not _rwr_df.empty and not _rwr_df.dropna(how="all").empty:
                st.line_chart(_rwr_df.dropna(how="all"), height=200, use_container_width=True)
                st.caption(
                    "Rising = consistently more wins. Falling = regime shift or parameter decay. "
                    "50% = breakeven. The 2-week validation goal: both lines stable at or above 50%."
                )
            else:
                st.caption("Need at least 5 trades to plot rolling win rate.")

            # ── Streak / trade result timeline ────────────────────────────────
            st.markdown("**🔴🟢 Trade Result Streak Timeline** *(green=WIN · red=LOSS · grey=BE)*")
            _results_list = jdf["result"].tolist()
            _dot_html = "<div class='streak-dot-row'>"
            for _i, _r in enumerate(_results_list[-100:]):   # last 100 trades max
                _cls = "sdot-win" if _r == "WIN" else ("sdot-loss" if _r == "LOSS" else "sdot-be")
                _dot_html += f"<span class='sdot {_cls}' title='Trade {_i+1}: {_r}'></span>"
            _dot_html += "</div>"
            if len(_results_list) > 100:
                _dot_html += f"<div style='font-size:10px;color:#8b949e;margin-top:4px;'>Showing last 100 of {len(_results_list)} trades</div>"
            st.markdown(_dot_html, unsafe_allow_html=True)
            st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

            # ── Per-symbol daily breakdown ────────────────────────────────────
            st.markdown("**📊 Daily P&L by Symbol**")
            if len(syms_avail) > 1:
                pivot = daily.pivot(index="date", columns="symbol", values="Points").fillna(0)
                st.dataframe(pivot.style.map(
                    lambda v: "color:#22c55e" if v > 0 else ("color:#ef4444" if v < 0 else "")
                ), use_container_width=True)
            else:
                st.bar_chart(daily.set_index("date")["Points"], height=180, use_container_width=True)

            # ── Best / worst days ─────────────────────────────────────────────
            daily_all = jdf.groupby("date")["points"].sum().reset_index().rename(columns={"points": "Points"})
            daily_all["date"] = daily_all["date"].astype(str)
            best5  = daily_all.nlargest(5, "Points")
            worst5 = daily_all.nsmallest(5, "Points")
            bw1, bw2 = st.columns(2)
            with bw1:
                st.markdown("**🏆 Top 5 Best Days**")
                st.dataframe(best5.style.map(
                    lambda v: "color:#22c55e" if isinstance(v, (int, float)) and v > 0 else ""),
                    use_container_width=True, hide_index=True)
            with bw2:
                st.markdown("**⚠️ Top 5 Worst Days**")
                st.dataframe(worst5.style.map(
                    lambda v: "color:#ef4444" if isinstance(v, (int, float)) and v < 0 else ""),
                    use_container_width=True, hide_index=True)

            # ── Direction breakdown (BUY vs SELL) ─────────────────────────────
            st.markdown("**🔀 BUY vs SELL Performance**")
            dir_grp = (jdf.groupby("direction")
                       .agg(Trades=("points", "count"),
                            Wins=("result", lambda x: (x == "WIN").sum()),
                            Points=("points", "sum"))
                       .reset_index())
            dir_grp["Win Rate"] = (dir_grp["Wins"] / dir_grp["Trades"] * 100).round(1).astype(str) + "%"
            dir_grp["Points"]   = dir_grp["Points"].round(2)
            st.dataframe(dir_grp, use_container_width=True, hide_index=True)

            # ── Month-over-month comparison ───────────────────────────────────
            if "date" in jdf.columns:
                _mom = jdf.copy()
                _mom["_date"]  = pd.to_datetime(_mom["date"], errors="coerce")
                _mom["_month"] = _mom["_date"].dt.to_period("M").astype(str)
                _mgrp = (_mom.groupby("_month")
                         .agg(
                             Trades  = ("points", "count"),
                             Wins    = ("result", lambda x: (x == "WIN").sum()),
                             Points  = ("points", "sum"),
                         )
                         .reset_index()
                         .rename(columns={"_month": "Month"}))
                _mgrp["WR %"]    = (_mgrp["Wins"] / _mgrp["Trades"] * 100).round(1)
                _mgrp["Avg pts"] = (_mgrp["Points"] / _mgrp["Trades"]).round(1)
                _mgrp["Points"]  = _mgrp["Points"].round(1)
                # MoM change column
                _mgrp["Δ pts MoM"] = _mgrp["Points"].diff().round(1)
                if len(_mgrp) >= 2:
                    st.markdown("**📆 Month-over-Month Performance**")
                    def _mpts(v):
                        try: return "color:#22c55e;font-weight:700" if float(v) > 0 else ("color:#ef4444;font-weight:700" if float(v) < 0 else "")
                        except: return ""
                    def _mwr(v):
                        try:
                            f = float(v)
                            if f >= 60: return "background-color:#0d3321;color:#22c55e;font-weight:700"
                            if f >= 45: return "color:#f59e0b"
                            return "background-color:#3b0f0f;color:#ef4444;font-weight:700"
                        except: return ""
                    st.dataframe(
                        _mgrp.style
                            .map(_mpts, subset=["Points", "Avg pts", "Δ pts MoM"])
                            .map(_mwr,  subset=["WR %"]),
                        use_container_width=True, hide_index=True,
                    )

            # ── Full trade log ─────────────────────────────────────────────────
            with st.expander("📋 Full Trade Log", expanded=False):
                show_j = [c for c in ["date", "symbol", "direction", "entry_time",
                                      "entry_price", "exit_time", "exit_price",
                                      "exit_reason", "points", "result", "rsi", "adx"]
                          if c in jdf.columns]
                def _jr(val):
                    if val == "WIN":  return "background-color:#0d3321;color:#22c55e"
                    if val == "LOSS": return "background-color:#3b0f0f;color:#ef4444"
                    return ""
                def _jp(val):
                    try: return "color:#22c55e" if float(val) > 0 else ("color:#ef4444" if float(val) < 0 else "")
                    except: return ""
                st.dataframe(
                    jdf[show_j].sort_values("date", ascending=False)
                    .style.map(_jr, subset=["result"] if "result" in show_j else [])
                           .map(_jp, subset=["points"] if "points" in show_j else []),
                    use_container_width=True, hide_index=True)

            # ── Download ──────────────────────────────────────────────────────
            st.download_button(
                "⬇ Download Journal CSV",
                data=jdf.to_csv(index=False).encode(),
                file_name=f"journal_{datetime.date.today()}.csv",
                mime="text/csv",
                key="journal_download")

    st.markdown(
        "<div style='text-align:center;color:#57606a;font-size:11px;"
        "border-top:1px solid #30363d;padding-top:12px;margin-top:32px;'>"
        "Trade Journal &nbsp;|&nbsp; Upload any backtest CSV or save to logs/trade_log.csv"
        "</div>", unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
#  TAB 4 — STOCKS OPTIONS DASHBOARD
# ══════════════════════════════════════════════════════════════════════════════
with tab_stocks:
    from engine.st_signal_engine import (
        evaluate_stock_signal, SIG_BUY, SIG_SELL, SIG_NONE,
    )
    from engine.st_backtester import run_stock_backtest, stock_summary_stats

    # Auto-refresh — only during IST market hours on weekdays
    _stocks_interval = _market_refresh_interval()
    if _stocks_interval:
        st_autorefresh(interval=_stocks_interval, key="stocks_refresh")
    # No banner here — Tab 1 already shows it; avoid duplicate messages

    st.markdown("## 🏦 Stocks — Intraday BUY/SELL Signal Dashboard")
    st.markdown(
        "<span style='color:#8b949e;font-size:13px;'>"
        "Composite scorer: <b>First-15m Breakout · VWAP Pullback · Opening Reversal · EMA Trend</b>"
        " &nbsp;|&nbsp; Timeframe: <b>5-min</b>"
        " &nbsp;|&nbsp; Entry: <b>09:30–13:30</b> &nbsp;|&nbsp; Force exit: <b>15:15 IST</b>"
        " &nbsp;|&nbsp; SL: <b>2×ATR(14)</b> &nbsp;|&nbsp; Exit: <b>SL hit · reverse signal · EOD</b>"
        "</span>", unsafe_allow_html=True)
    st.markdown("---")

    # ── session-state init for stocks ─────────────────────────────────────────
    if "stock_candles"  not in st.session_state:
        st.session_state.stock_candles  = {s: pd.DataFrame()
                                            for s in config.STOCK_OPTIONS_INSTRUMENTS}
    if "stock_signals"  not in st.session_state:
        st.session_state.stock_signals  = {s: {} for s in config.STOCK_OPTIONS_INSTRUMENTS}
    if "stock_trades"   not in st.session_state:
        st.session_state.stock_trades   = {s: None for s in config.STOCK_OPTIONS_INSTRUMENTS}
    if "stock_bt"       not in st.session_state:
        st.session_state.stock_bt       = {}

    # ── Nifty signal helper (read from existing live state) ──────────────────
    def _nifty_context() -> str:
        """Return current Nifty signal direction as 'BUY'|'SELL'|'NONE'."""
        nifty_sig = st.session_state.signals.get("NIFTY", {})
        return nifty_sig.get("signal", "NONE") or "NONE"

    # ── refresh stock data ────────────────────────────────────────────────────
    def _refresh_stocks():
        api = _ensure_api()
        if api is None:
            return
        nifty_ctx = _nifty_context()
        for sym, cfg_s in config.STOCK_OPTIONS_INSTRUMENTS.items():
            existing = st.session_state.stock_candles[sym]
            if existing.empty:
                # Always fetch on cold start — charts need historical data even outside hours
                df = fetch_candles(api, cfg_s["token"], cfg_s["exchange"], days_back=5)
            elif _is_market_hours():
                # Incremental refresh only during live session
                df = refresh_candles(api, existing, cfg_s["token"], cfg_s["exchange"])
            else:
                continue   # outside hours + already have data — nothing to update
            st.session_state.stock_candles[sym] = df
            if df.empty:
                continue
            sig = evaluate_stock_signal(df, sym, nifty_ctx)
            st.session_state.stock_signals[sym] = sig

            # ── open-trade management ─────────────────────────────────────────
            ot = st.session_state.stock_trades[sym]
            if ot is not None:
                close     = sig.get("entry") or ot["entry_price"]
                direction = ot["direction"]
                sl        = ot["sl"]

                exited      = False
                exit_reason = ""
                exit_price  = close

                # 1. Hard SL hit
                sl_hit = ((direction == SIG_BUY  and close <= sl) or
                          (direction == SIG_SELL and close >= sl))
                if sl_hit:
                    exit_reason = "SL Hit"; exit_price = sl; exited = True

                # 2. EOD
                now_t = datetime.datetime.now().time()
                if not exited and now_t >= datetime.time(15, 15):
                    exit_reason = "EOD Exit"; exited = True

                # 3. Reverse signal
                if not exited:
                    reverse = SIG_SELL if direction == SIG_BUY else SIG_BUY
                    if sig.get("signal") == reverse:
                        exit_reason = "Reverse Signal"; exited = True

                if exited:
                    pts = round((exit_price - ot["entry_price"]) if direction == SIG_BUY
                                else (ot["entry_price"] - exit_price), 2)
                    send_signal_alert(sym, {
                        "signal"    : f"EXIT {direction}",
                        "entry"     : ot["entry_price"],
                        "sl"        : sl,
                        "target"    : None,
                        "rsi"       : sig.get("rsi"),
                        "adx"       : 0,
                        "vwap"      : sig.get("vwap"),
                        "st_value"  : None,
                        "ema_fast"  : sig.get("ema9"),
                        "ema_slow"  : sig.get("ema20"),
                        "candle_time": sig.get("candle_time"),
                        "points"    : pts,
                    })
                    st.session_state.stock_trades[sym] = None
                    ot = None

            # ── new entry ─────────────────────────────────────────────────────
            if (ot is None
                    and sig.get("signal") not in (None, SIG_NONE)
                    and datetime.datetime.now().time() <= datetime.time(13, 30)):
                atr_v    = sig.get("atr") or 1
                sig_dir  = sig["signal"]
                entry_px = sig["entry"]
                sl_price = (round(entry_px - atr_v * 2, 2) if sig_dir == SIG_BUY
                            else round(entry_px + atr_v * 2, 2))
                new_trade = {
                    "direction"   : sig_dir,
                    "entry_price" : entry_px,
                    "sl"          : sl_price,
                    "entry_atr"   : atr_v,
                    "entry_time"  : datetime.datetime.now(),
                    "reason"      : sig.get("reason", ""),
                    "score_buy"   : sig.get("score_buy", 0),
                    "score_sell"  : sig.get("score_sell", 0),
                }
                st.session_state.stock_trades[sym] = new_trade
                send_signal_alert(sym, {
                    "signal"    : sig_dir,
                    "entry"     : entry_px,
                    "sl"        : sl_price,
                    "target"    : None,
                    "rsi"       : sig.get("rsi"),
                    "adx"       : 0,
                    "vwap"      : sig.get("vwap"),
                    "st_value"  : None,
                    "ema_fast"  : sig.get("ema9"),
                    "ema_slow"  : sig.get("ema20"),
                    "candle_time": sig.get("candle_time"),
                })

    try:
        _refresh_stocks()
    except Exception as _se:
        _se_msg = str(_se)
        if "exceeding access rate" in _se_msg:
            st.warning("⚠️ Angel One rate limit — will retry in 60 s.", icon="⏳")
        else:
            st.error(f"⚠️ Stocks data refresh error: {_se_msg}")

    # ─── signal card renderer ─────────────────────────────────────────────────
    def _stock_card(sym: str, sig: dict, cfg_s: dict):
        ot          = st.session_state.stock_trades.get(sym)
        direction   = sig.get("signal", SIG_NONE)
        entry       = sig.get("entry")
        f15h        = sig.get("f15_high")
        f15l        = sig.get("f15_low")
        vwap_v      = sig.get("vwap")
        ema9_v      = sig.get("ema9")
        ema20_v     = sig.get("ema20")
        rsi_v       = sig.get("rsi")
        atr_v       = sig.get("atr")
        vol         = sig.get("volume")
        vol_avg     = sig.get("vol_avg")
        ct          = sig.get("candle_time")
        reason      = sig.get("reason", "—")
        score_buy   = sig.get("score_buy", 0)
        score_sell  = sig.get("score_sell", 0)
        strat_buy   = sig.get("strategies_buy", [])
        strat_sell  = sig.get("strategies_sell", [])
        strategies  = cfg_s.get("strategy", [])
        now_str     = datetime.datetime.now().strftime("%H:%M:%S")

        # ── colour / CSS ──────────────────────────────────────────────────────
        if ot is not None:
            css = "signal-buy" if ot["direction"] == SIG_BUY else "signal-sell"
            clr = "buy-color"  if ot["direction"] == SIG_BUY else "sell-color"
        else:
            css = "signal-buy" if direction == SIG_BUY else (
                  "signal-sell" if direction == SIG_SELL else "signal-none")
            clr = "buy-color"  if direction == SIG_BUY else (
                  "sell-color"  if direction == SIG_SELL else "none-color")

        # ── position in 15-min range ──────────────────────────────────────────
        if entry and f15h and f15l and f15h > f15l:
            if entry > f15h:
                range_pos = f"<span style='color:#22c55e;font-weight:700;'>▲ Above ORB High ({f15h})</span>"
            elif entry < f15l:
                range_pos = f"<span style='color:#ef4444;font-weight:700;'>▼ Below ORB Low ({f15l})</span>"
            else:
                range_pos = f"<span style='color:#f59e0b;'>⟷ Inside ORB ({f15l}–{f15h})</span>"
        else:
            range_pos = "—"

        # ── Volume context ────────────────────────────────────────────────────
        if vol and vol_avg and vol_avg > 0:
            vol_ratio = round(vol / vol_avg, 1)
            vol_clr   = "#22c55e" if vol_ratio >= 1.2 else ("#f59e0b" if vol_ratio >= 0.8 else "#ef4444")
            vol_html  = f"<span style='color:{vol_clr};font-weight:700;'>{vol_ratio}× avg</span>"
        else:
            vol_html = "—"

        # ── Score bars + Nifty context badge ─────────────────────────────────
        buy_clr  = "#22c55e" if score_buy  >= 2 else ("#f59e0b" if score_buy  == 1 else "#8b949e")
        sell_clr = "#22c55e" if score_sell >= 2 else ("#f59e0b" if score_sell == 1 else "#8b949e")
        buy_strats  = " + ".join(strat_buy)  if strat_buy  else "—"
        sell_strats = " + ".join(strat_sell) if strat_sell else "—"
        _max_score  = max(len(strategies), 4)
        buy_bar_pct  = round(score_buy  / _max_score * 100, 0)
        sell_bar_pct = round(score_sell / _max_score * 100, 0)

        # Nifty alignment badge
        _nifty_dir = _nifty_context()
        if direction == SIG_BUY:
            nifty_badge = (
                "<span class='ctx-badge ctx-align'>🟢 Nifty aligned</span>" if _nifty_dir == "BUY" else
                "<span class='ctx-badge ctx-against'>⚠️ vs Nifty</span>" if _nifty_dir == "SELL" else
                "<span class='ctx-badge ctx-neutral'>Nifty neutral</span>"
            )
        elif direction == SIG_SELL:
            nifty_badge = (
                "<span class='ctx-badge ctx-align'>🔴 Nifty aligned</span>" if _nifty_dir == "SELL" else
                "<span class='ctx-badge ctx-against'>⚠️ vs Nifty</span>" if _nifty_dir == "BUY" else
                "<span class='ctx-badge ctx-neutral'>Nifty neutral</span>"
            )
        else:
            nifty_badge = ""

        # ── IN TRADE panel ────────────────────────────────────────────────────
        if ot is not None:
            ot_dir    = ot["direction"]
            entry_px  = ot["entry_price"]
            sl_px     = ot["sl"]
            live_px   = entry or entry_px
            unreal    = round((live_px - entry_px) if ot_dir == SIG_BUY
                              else (entry_px - live_px), 2)
            unr_clr   = "#22c55e" if unreal >= 0 else "#ef4444"
            sl_dist   = round(abs(live_px - sl_px), 2)

            held = datetime.datetime.now() - ot.get("entry_time", datetime.datetime.now())
            held_mins = int(held.total_seconds() // 60)
            hold_str  = (f"{held_mins // 60}h {held_mins % 60}m" if held_mins >= 60
                         else f"{held_mins}m")

            # Nifty alignment for in-trade
            _nt2 = _nifty_context()
            if ot_dir == SIG_BUY:
                nt_badge = ("<span class='ctx-badge ctx-align'>🟢 Nifty aligned</span>" if _nt2 == "BUY" else
                            "<span class='ctx-badge ctx-against'>⚠️ vs Nifty</span>" if _nt2 == "SELL" else
                            "<span class='ctx-badge ctx-neutral'>Nifty neutral</span>")
            else:
                nt_badge = ("<span class='ctx-badge ctx-align'>🔴 Nifty aligned</span>" if _nt2 == "SELL" else
                            "<span class='ctx-badge ctx-against'>⚠️ vs Nifty</span>" if _nt2 == "BUY" else
                            "<span class='ctx-badge ctx-neutral'>Nifty neutral</span>")

            st.markdown(f"""
            <div class="{css}">
              <div class="big-label">{sym} — IN TRADE</div>
              <div class="big-value {clr}">
                {'🟢 LONG (BUY)' if ot_dir == SIG_BUY else '🔴 SHORT (SELL)'}
                &nbsp;{nt_badge}
              </div>
              <div class="metric-row">
                <div class="metric-box"><div class="mbox-label">Entry Price</div>
                  <div class="mbox-val">{entry_px}</div></div>
                <div class="metric-box"><div class="mbox-label">Hard SL (2×ATR)</div>
                  <div class="mbox-val" style="color:#f59e0b">{sl_px} &nbsp;
                  <span style='font-size:10px;'>({sl_dist:.1f} pts away)</span></div></div>
                <div class="metric-box"><div class="mbox-label">Live Price</div>
                  <div class="mbox-val">{live_px}</div></div>
                <div class="metric-box"><div class="mbox-label">Unrealised P&L</div>
                  <div class="mbox-val" style="color:{unr_clr}">{unreal:+.2f} pts</div></div>
                <div class="metric-box"><div class="mbox-label">Hold Duration</div>
                  <div class="mbox-val">{hold_str}</div></div>
                <div class="metric-box"><div class="mbox-label">ATR at Entry</div>
                  <div class="mbox-val">{ot.get("entry_atr", "—")}</div></div>
                <div class="metric-box"><div class="mbox-label">RSI</div>
                  <div class="mbox-val">{rsi_v or '—'}</div></div>
                <div class="metric-box"><div class="mbox-label">VWAP</div>
                  <div class="mbox-val">{vwap_v or '—'}</div></div>
              </div>
              <div style="margin-top:8px;font-size:12px;color:#8b949e;">
                Signals: <b style="color:#e6edf3">{ot.get("reason","—")}</b>
                &nbsp;|&nbsp; Exit: SL hit · reverse signal · EOD 15:15
              </div>
              <div class="ts">Candle: {ct or 'N/A'} &nbsp;|&nbsp; Refreshed: {now_str}</div>
            </div>
            """, unsafe_allow_html=True)

            if st.button(f"🚪 Exit {sym} now (market)", key=f"exit_stock_{sym}"):
                st.session_state.stock_trades[sym] = None
                st.success(f"✅ {sym} trade manually exited.")
                st.rerun()
            return

        # ── NO TRADE panel ─────────────────────────────────────────────────────
        sig_lbl = (f"🟢 BUY SIGNAL" if direction == SIG_BUY else
                   f"🔴 SELL SIGNAL" if direction == SIG_SELL else
                   "⚪ WAITING")

        # SL if entering now
        if entry and atr_v and atr_v > 0:
            sl_buy_est  = round(entry - atr_v * 2, 2)
            sl_sell_est = round(entry + atr_v * 2, 2)
            sl_est = sl_buy_est if direction == SIG_BUY else (
                     sl_sell_est if direction == SIG_SELL else "—")
            sl_dist_est = f"{round(atr_v * 2, 1)} pts" if direction != SIG_NONE else "—"
        else:
            sl_est = sl_dist_est = "—"

        # best strategy label
        active_strats = "/".join(s.capitalize() for s in strategies[:3])

        st.markdown(f"""
        <div class="{css}">
          <div class="big-label">{sym} &nbsp;
            <span style='font-size:11px;color:#8b949e;'>{active_strats}</span>
          </div>
          <div class="big-value {clr}">{sig_lbl}&nbsp;{nifty_badge}</div>
          <div style="margin-top:8px;display:flex;gap:20px;flex-wrap:wrap;align-items:flex-start;">
            <div style="min-width:120px;">
              <div style="display:flex;justify-content:space-between;font-size:11px;color:#8b949e;margin-bottom:2px;">
                <span>BUY <span style='color:{buy_clr};font-weight:700;'>{score_buy}/{_max_score}</span></span>
                <span style='font-size:10px;color:#8b949e;'>{buy_strats}</span>
              </div>
              <div class="score-bar-wrap">
                <div class="score-bar-fill" style="width:{buy_bar_pct}%;background:{buy_clr};"></div>
              </div>
            </div>
            <div style="min-width:120px;">
              <div style="display:flex;justify-content:space-between;font-size:11px;color:#8b949e;margin-bottom:2px;">
                <span>SELL <span style='color:{sell_clr};font-weight:700;'>{score_sell}/{_max_score}</span></span>
                <span style='font-size:10px;color:#8b949e;'>{sell_strats}</span>
              </div>
              <div class="score-bar-wrap">
                <div class="score-bar-fill" style="width:{sell_bar_pct}%;background:{sell_clr};"></div>
              </div>
            </div>
          </div>
          <div class="metric-row">
            <div class="metric-box"><div class="mbox-label">Price</div>
              <div class="mbox-val">{entry or '—'}</div></div>
            <div class="metric-box"><div class="mbox-label">SL (2×ATR)</div>
              <div class="mbox-val" style="color:#f59e0b">{sl_est} &nbsp;
              <span style='font-size:10px;'>({sl_dist_est})</span></div></div>
            <div class="metric-box"><div class="mbox-label">15m ORB High</div>
              <div class="mbox-val">{f15h or '—'}</div></div>
            <div class="metric-box"><div class="mbox-label">15m ORB Low</div>
              <div class="mbox-val">{f15l or '—'}</div></div>
            <div class="metric-box"><div class="mbox-label">Position in ORB</div>
              <div class="mbox-val">{range_pos}</div></div>
            <div class="metric-box"><div class="mbox-label">VWAP</div>
              <div class="mbox-val">{vwap_v or '—'}</div></div>
            <div class="metric-box"><div class="mbox-label">EMA 9</div>
              <div class="mbox-val">{ema9_v or '—'}</div></div>
            <div class="metric-box"><div class="mbox-label">EMA 20</div>
              <div class="mbox-val">{ema20_v or '—'}</div></div>
            <div class="metric-box"><div class="mbox-label">RSI</div>
              <div class="mbox-val">{rsi_v or '—'}</div></div>
            <div class="metric-box"><div class="mbox-label">ATR</div>
              <div class="mbox-val">{atr_v or '—'}</div></div>
            <div class="metric-box"><div class="mbox-label">Volume</div>
              <div class="mbox-val">{vol_html}</div></div>
          </div>
          <div style="margin-top:8px;font-size:12px;color:#8b949e;">
            <b>Rule:</b> {range_pos}
            &nbsp;|&nbsp; <b>Reason:</b> {reason}
          </div>
          <div class="ts">Candle: {ct or 'N/A'} &nbsp;|&nbsp; Refreshed: {now_str}</div>
        </div>
        """, unsafe_allow_html=True)

    # ─── stock chart ─────────────────────────────────────────────────────────
    def _stock_chart(df: pd.DataFrame, sym: str, sig: dict, ot: dict | None):
        try:
            import plotly.graph_objects as go
            from plotly.subplots import make_subplots
            from engine.st_signal_engine import _add_stock_indicators

            if df.empty or len(df) < 10:
                return

            df_c = _add_stock_indicators(df.copy())
            df_c["datetime"] = pd.to_datetime(df_c["datetime"])

            today = df_c["datetime"].iloc[-1].date()
            two_days_ago = today - pd.Timedelta(days=3)
            df_c = df_c[df_c["datetime"].dt.date >= two_days_ago].tail(150)

            today_start   = df_c[df_c["datetime"].dt.date == today]["datetime"].iloc[0] - pd.Timedelta(minutes=10)
            x_default_end = df_c["datetime"].iloc[-1] + pd.Timedelta(minutes=10)

            fig = make_subplots(
                rows=3, cols=1, shared_xaxes=True,
                row_heights=[0.60, 0.20, 0.20],
                vertical_spacing=0.03,
                subplot_titles=("", "RSI (14)", "Volume"),
            )

            # Candlesticks
            fig.add_trace(go.Candlestick(
                x=df_c["datetime"],
                open=df_c["open"], high=df_c["high"],
                low=df_c["low"],   close=df_c["close"],
                name="Price",
                increasing_line_color="#22c55e", decreasing_line_color="#ef4444",
                increasing_fillcolor="#22c55e",  decreasing_fillcolor="#ef4444",
            ), row=1, col=1)

            # EMA9 — teal
            if "ema9" in df_c.columns:
                fig.add_trace(go.Scatter(
                    x=df_c["datetime"], y=df_c["ema9"],
                    mode="lines", name="EMA 9",
                    line=dict(color="#2dd4bf", width=1, dash="dot"),
                ), row=1, col=1)

            # EMA20 — amber
            if "ema20" in df_c.columns:
                fig.add_trace(go.Scatter(
                    x=df_c["datetime"], y=df_c["ema20"],
                    mode="lines", name="EMA 20",
                    line=dict(color="#f59e0b", width=1.2, dash="dot"),
                ), row=1, col=1)

            # VWAP — purple
            if "vwap" in df_c.columns:
                fig.add_trace(go.Scatter(
                    x=df_c["datetime"], y=df_c["vwap"],
                    mode="lines", name="VWAP",
                    line=dict(color="#818cf8", width=1.2, dash="dash"),
                ), row=1, col=1)

            # ORB High/Low horizontal lines (today only)
            f15h = sig.get("f15_high")
            f15l = sig.get("f15_low")
            x0, x1 = today_start, x_default_end
            if f15h and f15h > 0:
                fig.add_shape(type="line", x0=x0, x1=x1, y0=f15h, y1=f15h,
                              line=dict(color="#22c55e", width=1, dash="dot"), row=1, col=1)
                fig.add_annotation(x=x1, y=f15h, text=f" ORB High {f15h}",
                                   showarrow=False, xanchor="left",
                                   font=dict(color="#22c55e", size=10), row=1, col=1)
            if f15l and f15l > 0:
                fig.add_shape(type="line", x0=x0, x1=x1, y0=f15l, y1=f15l,
                              line=dict(color="#ef4444", width=1, dash="dot"), row=1, col=1)
                fig.add_annotation(x=x1, y=f15l, text=f" ORB Low {f15l}",
                                   showarrow=False, xanchor="left",
                                   font=dict(color="#ef4444", size=10), row=1, col=1)

            # Open trade lines
            if ot:
                for val, label, color in [
                    (ot.get("entry_price"), "Entry", "#3b82d4"),
                    (ot.get("sl"),          "SL",    "#f59e0b"),
                ]:
                    if val:
                        fig.add_shape(type="line", x0=x0, x1=x1, y0=val, y1=val,
                                      line=dict(color=color, width=1, dash="dot"), row=1, col=1)
                        fig.add_annotation(x=x1, y=val, text=f" {label} {val}",
                                           showarrow=False, xanchor="left",
                                           font=dict(color=color, size=10), row=1, col=1)

            # Signal marker
            if sig.get("signal") in (SIG_BUY, SIG_SELL):
                is_buy   = sig["signal"] == SIG_BUY
                marker_y = df_c["low"].iloc[-1] * 0.9995 if is_buy else df_c["high"].iloc[-1] * 1.0005
                fig.add_trace(go.Scatter(
                    x=[df_c["datetime"].iloc[-1]], y=[marker_y],
                    mode="markers",
                    marker=dict(symbol="triangle-up" if is_buy else "triangle-down",
                                size=14, color="#22c55e" if is_buy else "#ef4444"),
                    name=sig["signal"], showlegend=False,
                ), row=1, col=1)

            # RSI
            if "rsi14" in df_c.columns:
                fig.add_trace(go.Scatter(
                    x=df_c["datetime"], y=df_c["rsi14"],
                    mode="lines", name="RSI",
                    line=dict(color="#a78bfa", width=1.5),
                ), row=2, col=1)
                for lvl, clr in [(70, "#ef4444"), (50, "#57606a"), (30, "#22c55e")]:
                    fig.add_hline(y=lvl, line_dash="dot", line_color=clr,
                                  line_width=1, row=2, col=1)

            # Volume bars
            if "volume" in df_c.columns:
                vol_colors = ["#22c55e" if c >= o else "#ef4444"
                              for c, o in zip(df_c["close"], df_c["open"])]
                fig.add_trace(go.Bar(
                    x=df_c["datetime"], y=df_c["volume"],
                    name="Volume", marker_color=vol_colors, showlegend=False,
                ), row=3, col=1)
                if "vol_avg" in df_c.columns:
                    fig.add_trace(go.Scatter(
                        x=df_c["datetime"], y=df_c["vol_avg"],
                        mode="lines", name="Vol Avg",
                        line=dict(color="#f59e0b", width=1, dash="dot"),
                    ), row=3, col=1)

            fig.update_layout(
                height=540,
                paper_bgcolor="#0f1117", plot_bgcolor="#0f1117",
                font=dict(color="#e6edf3", size=11),
                showlegend=True,
                legend=dict(orientation="h", y=1.02, x=0,
                            bgcolor="rgba(0,0,0,0)", font=dict(size=10)),
                margin=dict(l=10, r=100, t=24, b=10),
                xaxis_rangeslider_visible=False,
            )
            for rn in [1, 2, 3]:
                fig.update_xaxes(gridcolor="#1e2430", zeroline=False,
                                 showticklabels=(rn == 3),
                                 range=[today_start, x_default_end],
                                 row=rn, col=1)
                fig.update_yaxes(gridcolor="#1e2430", zeroline=False,
                                 row=rn, col=1)
            fig.update_yaxes(title_text="Price",  title_font_size=10, row=1, col=1)
            fig.update_yaxes(title_text="RSI",    title_font_size=10, row=2, col=1)
            fig.update_yaxes(title_text="Volume", title_font_size=10, row=3, col=1)

            st.plotly_chart(fig, use_container_width=True,
                            config={"displayModeBar": True,
                                    "modeBarButtonsToRemove": ["select2d","lasso2d"],
                                    "scrollZoom": True})
        except Exception as _ce:
            st.caption(f"Chart unavailable: {_ce}")

    # ─── Render signal cards — 3 per row ─────────────────────────────────────
    syms_stock = list(config.STOCK_OPTIONS_INSTRUMENTS.keys())
    nifty_ctx  = _nifty_context()

    # Global summary bar
    active_trades = sum(1 for s in syms_stock if st.session_state.stock_trades.get(s))
    buy_count  = sum(1 for s in syms_stock
                     if st.session_state.stock_signals.get(s, {}).get("signal") == SIG_BUY)
    sell_count = sum(1 for s in syms_stock
                     if st.session_state.stock_signals.get(s, {}).get("signal") == SIG_SELL)
    nifty_clr = "#22c55e" if nifty_ctx == "BUY" else (
                "#ef4444" if nifty_ctx == "SELL" else "#8b949e")

    st.markdown(
        f"<div class='pulse-bar' style='margin-bottom:16px;'>"
        f"<div class='pulse-sym'><div class='pulse-name'>Nifty Context</div>"
        f"<div class='pulse-val' style='color:{nifty_clr};'>"
        f"{'🟢 BUY' if nifty_ctx=='BUY' else ('🔴 SELL' if nifty_ctx=='SELL' else '⚪ NONE')}"
        f"</div></div>"
        f"<div class='pulse-sym'><div class='pulse-name'>BUY Signals</div>"
        f"<div class='pulse-val' style='color:#22c55e;'>{buy_count} stocks</div></div>"
        f"<div class='pulse-sym'><div class='pulse-name'>SELL Signals</div>"
        f"<div class='pulse-val' style='color:#ef4444;'>{sell_count} stocks</div></div>"
        f"<div class='pulse-sym'><div class='pulse-name'>Open Trades</div>"
        f"<div class='pulse-val' style='color:#3b82d4;'>{active_trades} active</div></div>"
        f"<div class='pulse-sym' style='margin-left:auto;'>"
        f"<div class='pulse-name'>Quick rule</div>"
        f"<div style='font-size:11px;color:#8b949e;'>"
        f"Above ORB High + Above VWAP → BUY &nbsp;|&nbsp; Below ORB Low + Below VWAP → SELL &nbsp;|&nbsp; Inside ORB → No Trade"
        f"</div></div></div>",
        unsafe_allow_html=True,
    )

    # Cards in rows of 3
    for row_i in range(0, len(syms_stock), 3):
        row_syms = syms_stock[row_i: row_i + 3]
        cols = st.columns(len(row_syms))
        for col, sym in zip(cols, row_syms):
            cfg_s = config.STOCK_OPTIONS_INSTRUMENTS[sym]
            with col:
                st.markdown(f"#### {sym}")
                _stock_card(sym,
                            st.session_state.stock_signals.get(sym, {}),
                            cfg_s)

    # Charts (one per stock, inside expanders to save space)
    st.markdown("---")
    st.markdown("### 📊 Stock Charts — 5-min Candlestick + ORB + VWAP + EMA + Volume")
    for sym in syms_stock:
        with st.expander(f"📈 {sym} Chart", expanded=False):
            _stock_chart(
                st.session_state.stock_candles.get(sym, pd.DataFrame()),
                sym,
                st.session_state.stock_signals.get(sym, {}),
                st.session_state.stock_trades.get(sym),
            )

    # ── Refresh / controls row ────────────────────────────────────────────────
    st.markdown("---")
    sb1, sb2, _ = st.columns([1, 1, 5])
    with sb1:
        if st.button("🔄 Refresh Now", key="stock_refresh_btn"):
            st.rerun()
    with sb2:
        if st.button("🗑 Clear All Stock Trades", key="stock_clear"):
            st.session_state.stock_trades = {s: None for s in config.STOCK_OPTIONS_INSTRUMENTS}
            st.success("All stock trades cleared.")
            st.rerun()

    # ── Backtest section ──────────────────────────────────────────────────────
    st.markdown("---")
    st.markdown("### 🔬 Stock Price Backtest")
    st.markdown(
        "<span style='color:#8b949e;font-size:13px;'>"
        "Walk-forward backtest of all 4 strategies on 5-min candle data. "
        "Trades stock price directly (BUY/SELL). SL = 2×ATR(14). Exit: SL hit · reverse signal · EOD 15:15. "
        "90 days = 3 API calls per stock · 180 days = 6 API calls per stock (~10–20 s each)."
        "</span>", unsafe_allow_html=True)

    sbt1, sbt2, sbt3 = st.columns([1, 1, 2])
    with sbt1:
        sbt_days = st.selectbox(
            "Days of history",
            [10, 20, 30, 60, 90, 180],
            index=2,   # default: 30 days
            key="sbt_days",
            help=(
                "30 days = 1 API call per stock (~2 s)\n"
                "90 days = 3 API calls per stock (~10 s)\n"
                "180 days = 6 API calls per stock (~20 s)\n"
                "Angel One hard limit: 30 calendar days per single call — "
                "longer periods are stitched automatically."
            ),
        )
    with sbt2:
        sbt_syms = st.multiselect("Symbols", syms_stock,
                                  default=syms_stock[:3], key="sbt_syms")
    with sbt3:
        st.markdown("<br>", unsafe_allow_html=True)
        run_sbt = st.button("▶  Run Stock Backtest", type="primary", key="sbt_run")

    # Warn for large runs so user knows it will take a while
    if sbt_days >= 90 and sbt_syms:
        _api_calls_est = (sbt_days // 30) * len(sbt_syms)
        _time_est      = _api_calls_est * 3   # ~3 s per call including sleep
        st.info(
            f"ℹ️ **{sbt_days}-day backtest** for {len(sbt_syms)} stock(s) will make "
            f"~{_api_calls_est} Angel One API calls (est. {_time_est}–{_time_est*2} s total). "
            "Run one symbol at a time if you hit rate limits.",
            icon="⏳",
        )

    if run_sbt:
        if not sbt_syms:
            st.warning("Select at least one symbol.")
        else:
            api = _ensure_api("Logging into Angel One for stock backtest data…")
            if api:
                st.session_state.stock_bt = {}
                for sym in sbt_syms:
                    cfg_s = config.STOCK_OPTIONS_INSTRUMENTS[sym]
                    _chunks = max(1, (sbt_days + 3) // 30 + (1 if (sbt_days + 3) % 30 else 0))
                    _fetch_msg = (
                        f"Fetching {sbt_days} days for {sym} "
                        f"({'1 API call' if _chunks == 1 else f'{_chunks} API calls stitched'})…"
                    )
                    with st.spinner(_fetch_msg):
                        df_raw = fetch_candles(api, cfg_s["token"], cfg_s["exchange"],
                                               interval="FIVE_MINUTE",
                                               days_back=sbt_days + 3)
                    if df_raw.empty:
                        st.error(f"No data for {sym}.")
                        continue
                    with st.spinner(f"Running stock backtest for {sym} ({len(df_raw)} candles)…"):
                        t_df, d_df = run_stock_backtest(df_raw, sym)
                        sts = stock_summary_stats(t_df) if not t_df.empty else {}
                    st.session_state.stock_bt[sym] = {
                        "trades": t_df, "stats": sts, "diag": d_df,
                        "days": sbt_days, "candles": len(df_raw),
                    }

    if st.session_state.stock_bt:
        for sym, res in st.session_state.stock_bt.items():
            t_df   = res["trades"]
            sts    = res["stats"]
            _days_run    = res.get("days", "?")
            _candles_run = res.get("candles", "?")

            st.markdown(
                f"#### {sym} "
                f"<span style='font-size:13px;color:#8b949e;font-weight:400;'>"
                f"— {_days_run}-day backtest &nbsp;·&nbsp; {_candles_run} candles</span>",
                unsafe_allow_html=True,
            )
            if t_df.empty or not sts:
                st.warning(f"No signals generated for {sym} over the selected period.")
                continue

            # ── Stat cards ────────────────────────────────────────────────────
            pts_clr = "win-clr" if sts["total_points"] >= 0 else "loss-clr"
            wr_clr  = "win-clr" if sts["win_rate_pct"] >= 55 else (
                      "warn-clr" if sts["win_rate_pct"] >= 45 else "loss-clr")
            pf      = sts.get("profit_factor", "—")
            pf_clr  = "win-clr" if (isinstance(pf, (int,float)) and pf >= 1.5) else (
                      "warn-clr" if (isinstance(pf, (int,float)) and pf >= 1.0) else "loss-clr")
            exp     = sts.get("expectancy", 0)
            exp_clr = "win-clr" if exp > 0 else "loss-clr"

            cards_s = [
                ("Trades",           sts["total_trades"],       "neu-clr"),
                ("Wins",             sts["wins"],               "win-clr"),
                ("Losses",           sts["losses"],             "loss-clr"),
                ("Win Rate",         f"{sts['win_rate_pct']}%", wr_clr),
                ("Net Prem Pts",     sts["total_points"],       pts_clr),
                ("Avg Win",          sts["avg_win_pts"],        "win-clr"),
                ("Avg Loss",         sts["avg_loss_pts"],       "loss-clr"),
                ("Profit Factor",    pf,                        pf_clr),
                ("Expectancy",       exp,                       exp_clr),
                ("Max Drawdown",     sts.get("max_drawdown","—"),     "warn-clr"),
                ("Max Consec Loss",  sts.get("max_consec_loss","—"),  "warn-clr"),
            ]
            c_cols = st.columns(len(cards_s))
            for col, (lbl, val, clr) in zip(c_cols, cards_s):
                col.markdown(
                    f"<div class='stat-card'>"
                    f"<div class='stat-val {clr}'>{val}</div>"
                    f"<div class='stat-lbl'>{lbl}</div>"
                    f"</div>", unsafe_allow_html=True)

            st.markdown("<br>", unsafe_allow_html=True)

            # ── Charts: Equity+Drawdown | Direction breakdown ─────────────────
            ch_s1, ch_s2 = st.columns(2)
            with ch_s1:
                st.markdown("**📈 Equity Curve + Drawdown**")
                try:
                    import plotly.graph_objects as go
                    from plotly.subplots import make_subplots as _msp2
                    _scum  = t_df["points"].cumsum().reset_index(drop=True)
                    _speak = _scum.cummax()
                    _sdd   = _scum - _speak
                    _sfig  = _msp2(rows=2, cols=1, shared_xaxes=True,
                                   row_heights=[0.65, 0.35], vertical_spacing=0.05)
                    _sfig.add_trace(go.Scatter(
                        y=_scum.values, mode="lines", name="Equity",
                        line=dict(color="#22c55e", width=2),
                        fill="tozeroy", fillcolor="rgba(34,197,94,0.08)",
                    ), row=1, col=1)
                    _sfig.add_hline(y=0, line_dash="dot", line_color="#57606a",
                                    line_width=1, row=1, col=1)
                    _sfig.add_trace(go.Scatter(
                        y=_sdd.values, mode="lines", name="Drawdown",
                        line=dict(color="#ef4444", width=1.5),
                        fill="tozeroy", fillcolor="rgba(239,68,68,0.12)",
                    ), row=2, col=1)
                    _sfig.update_layout(
                        height=260, paper_bgcolor="#0f1117", plot_bgcolor="#0f1117",
                        font=dict(color="#e6edf3", size=10),
                        margin=dict(l=6, r=6, t=10, b=6),
                        showlegend=True,
                        legend=dict(orientation="h", y=1.05, x=0,
                                    bgcolor="rgba(0,0,0,0)", font=dict(size=9)),
                    )
                    for _rn in [1, 2]:
                        _sfig.update_xaxes(gridcolor="#1e2430", zeroline=False, row=_rn, col=1)
                        _sfig.update_yaxes(gridcolor="#1e2430", zeroline=False, row=_rn, col=1)
                    _sfig.update_yaxes(title_text="pts", title_font_size=9, row=1, col=1)
                    _sfig.update_yaxes(title_text="DD",  title_font_size=9, row=2, col=1)
                    st.plotly_chart(_sfig, use_container_width=True,
                                    config={"displayModeBar": False})
                except Exception:
                    st.line_chart(
                        pd.DataFrame({"Cumulative Points": t_df["points"].cumsum().values}),
                        height=200, use_container_width=True)

            with ch_s2:
                st.markdown("**📊 BUY vs SELL Breakdown**")
                dir_s = (t_df.groupby("direction")
                         .agg(Trades=("points","count"),
                              Wins=("result", lambda x: (x=="WIN").sum()),
                              Points=("points","sum"))
                         .reset_index())
                dir_s["WR %"]   = (dir_s["Wins"] / dir_s["Trades"] * 100).round(1)
                dir_s["Points"] = dir_s["Points"].round(1)
                dir_s["Avg pts"] = (dir_s["Points"] / dir_s["Trades"]).round(1)
                def _dsty(v):
                    try: return "color:#22c55e;font-weight:700" if float(v) > 0 else ("color:#ef4444;font-weight:700" if float(v) < 0 else "")
                    except: return ""
                st.dataframe(
                    dir_s.style.map(_dsty, subset=["Points", "Avg pts"]),
                    use_container_width=True, hide_index=True)

                # Daily P&L bar chart
                if "date" in t_df.columns:
                    st.markdown("**📅 Daily P&L (pts)**")
                    _dpnl_s = (t_df.groupby("date")["points"].sum()
                               .reset_index().rename(columns={"points": "Points"}))
                    _dpnl_s["date"] = _dpnl_s["date"].astype(str)
                    st.bar_chart(_dpnl_s.set_index("date")["Points"],
                                 height=130, use_container_width=True)

            # ── Weekly P&L table ──────────────────────────────────────────────
            if "date" in t_df.columns:
                with st.expander("📅 Weekly P&L Summary", expanded=(_days_run >= 90)):
                    _swt = t_df.copy()
                    _swt["_date"] = pd.to_datetime(_swt["date"], errors="coerce")
                    _swt["_week"] = _swt["_date"].dt.to_period("W").astype(str)
                    _swgrp = (_swt.groupby("_week")
                              .agg(
                                  Trades  = ("points", "count"),
                                  Wins    = ("result", lambda x: (x == "WIN").sum()),
                                  BUY     = ("direction", lambda x: (x == "BUY").sum()),
                                  SELL    = ("direction", lambda x: (x == "SELL").sum()),
                                  Points  = ("points", "sum"),
                              )
                              .reset_index()
                              .rename(columns={"_week": "Week"}))
                    _swgrp.rename(columns={"_week": "Week"}, inplace=True)
                    _swgrp["WR %"]    = (_swgrp["Wins"] / _swgrp["Trades"] * 100).round(1)
                    _swgrp["Avg pts"] = (_swgrp["Points"] / _swgrp["Trades"]).round(2)
                    _swgrp["Points"]  = _swgrp["Points"].round(2)
                    def _swpts(v):
                        try: return "color:#22c55e;font-weight:700" if float(v) > 0 else ("color:#ef4444;font-weight:700" if float(v) < 0 else "")
                        except: return ""
                    def _swwr(v):
                        try:
                            f = float(v)
                            if f >= 60: return "background-color:#0d3321;color:#22c55e;font-weight:700"
                            if f >= 45: return "color:#f59e0b"
                            return "background-color:#3b0f0f;color:#ef4444;font-weight:700"
                        except: return ""
                    st.dataframe(
                        _swgrp.style
                              .map(_swpts, subset=["Points", "Avg pts"])
                              .map(_swwr,  subset=["WR %"]),
                        use_container_width=True, hide_index=True,
                    )
                    st.caption("Weekly view is especially useful at 90d/180d to spot regime patterns.")

            # ── Entry time slot heatmap ───────────────────────────────────────
            if "entry_time" in t_df.columns:
                with st.expander(f"⏱ {sym} Entry Slot Analysis", expanded=False):
                    _sl2 = t_df.copy()
                    _sl2["_et"]   = pd.to_datetime(_sl2["entry_time"], errors="coerce")
                    _sl2["_slot"] = _sl2["_et"].dt.floor("30min").dt.strftime("%H:%M")
                    _sg2 = (_sl2.groupby("_slot")
                            .agg(Trades=("points","count"),
                                 Wins=("result", lambda x: (x=="WIN").sum()),
                                 Points=("points","sum"))
                            .reset_index().rename(columns={"_slot":"Slot"}))
                    _sg2["WR %"]    = (_sg2["Wins"] / _sg2["Trades"] * 100).round(1)
                    _sg2["Avg pts"] = (_sg2["Points"] / _sg2["Trades"]).round(1)
                    _sg2["Points"]  = _sg2["Points"].round(1)

                    def _swr(v):
                        try:
                            f = float(v)
                            if f >= 60: return "background-color:#0d3321;color:#22c55e;font-weight:700"
                            if f >= 45: return "color:#f59e0b"
                            return "background-color:#3b0f0f;color:#ef4444;font-weight:700"
                        except: return ""
                    def _spt(v):
                        try: return "color:#22c55e" if float(v) > 0 else ("color:#ef4444" if float(v) < 0 else "")
                        except: return ""

                    st.dataframe(
                        _sg2.style.map(_swr, subset=["WR %"]).map(_spt, subset=["Points","Avg pts"]),
                        use_container_width=True, hide_index=True)

            # ── Trade log ─────────────────────────────────────────────────────
            with st.expander(f"📋 {sym} Trade Log ({len(t_df)} trades)", expanded=False):
                show_t = [c for c in ["date","direction","entry_time","entry_price","exit_price",
                                      "sl","exit_reason","points","result","sig_reason"]
                          if c in t_df.columns]
                disp_t = t_df[show_t].copy()
                if "entry_time" in disp_t.columns:
                    disp_t["entry_time"] = pd.to_datetime(disp_t["entry_time"]).dt.strftime("%H:%M")

                def _tr(v):
                    if v == "WIN":  return "background-color:#0d3321;color:#22c55e"
                    if v == "LOSS": return "background-color:#3b0f0f;color:#ef4444"
                    return ""
                def _tp(v):
                    try: return "color:#22c55e" if float(v) > 0 else ("color:#ef4444" if float(v) < 0 else "")
                    except: return ""

                st.dataframe(
                    disp_t.style.map(_tr, subset=["result"]).map(_tp, subset=["points"]),
                    use_container_width=True, hide_index=True)

            # ── Download ──────────────────────────────────────────────────────
            st.download_button(
                f"⬇ Download {sym} {_days_run}d Backtest CSV",
                data=t_df.to_csv(index=False).encode(),
                file_name=f"stock_bt_{sym}_{_days_run}d_{datetime.date.today()}.csv",
                mime="text/csv",
                key=f"sbt_dl_{sym}")

            st.markdown("---")

    st.markdown(
        "<div style='text-align:center;color:#57606a;font-size:11px;"
        "border-top:1px solid #30363d;padding-top:12px;margin-top:24px;'>"
        "Stocks Signal Dashboard &nbsp;|&nbsp; Angel One SmartAPI &nbsp;|&nbsp; "
        "For informational purposes only — not financial advice."
        "</div>", unsafe_allow_html=True)
