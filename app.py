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
    page_title="Nifty / BankNifty Signals",
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


# ═══════════════════════════════════════════════════════════════════════════════
#  TAB DEFINITIONS
# ═══════════════════════════════════════════════════════════════════════════════
tab_live, tab_bt, tab_journal = st.tabs(["📈  Live Signals", "🔬  Backtest", "📒  Journal"])


# ══════════════════════════════════════════════════
#  TAB 1 — LIVE SIGNALS
# ══════════════════════════════════════════════════
with tab_live:

    # Auto-refresh every 60 s only when this tab is active context
    st_autorefresh(interval=60_000, key="live_refresh")

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
                df = fetch_candles(api, cfg_inst["token"], cfg_inst["exchange"], days_back=5)
            else:
                df = refresh_candles(api, existing, cfg_inst["token"], cfg_inst["exchange"])
            st.session_state.candles[sym] = df
            if df.empty:
                continue

            sig      = evaluate_signal(df, symbol=sym)
            st.session_state.signals[sym] = sig
            ot       = st.session_state.open_trades[sym]   # may be None

            # ── latest candle values ─────────────────────────────────────────
            df_ind   = add_indicators(df.copy())
            df_ind["symbol"] = sym
            row      = df_ind.iloc[-1]
            close    = float(row["close"])
            high     = float(row["high"])
            low      = float(row["low"])
            atr_val  = float(row.get("atr") or 0)
            st_val   = float(row.get("st_value") or 0)
            now_dt   = strip_tz(pd.to_datetime(row["datetime"]))
            now_time = now_dt.time()
            now_date = now_dt.date()

            # SL cap helpers (same logic as backtester)
            fixed_cap    = config.SL_CAP_PTS.get(sym)
            atr_cap_mult = (None if fixed_cap is not None
                            else getattr(config, "ATR_SL_MULT_BANKNIFTY", 2.0)
                            if sym == "BANKNIFTY"
                            else getattr(config, "ATR_SL_MULT_STOCKS", {}).get(sym, 2.0))

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

                    # 5. Apply hard SL cap
                    if atr_cap_mult is not None:
                        live_cap = round(atr_val * atr_cap_mult, 2) if atr_val else ot["entry_atr"] * atr_cap_mult
                    else:
                        live_cap = fixed_cap
                    if direction == SIGNAL_BUY:
                        ot["sl"] = round(max(ot["sl"], entry - live_cap), 2)
                    else:
                        ot["sl"] = round(min(ot["sl"], entry + live_cap), 2)

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
                cap          = (fixed_cap if fixed_cap is not None
                                else round(atr_val * atr_cap_mult, 2) if atr_cap_mult and atr_val else 9999)
                if direction == SIGNAL_BUY:
                    sl_cap = round(entry_price - cap, 2)
                    sl     = max(sl_initial, sl_cap) if sl_initial else sl_cap
                else:
                    sl_cap = round(entry_price + cap, 2)
                    sl     = min(sl_initial, sl_cap) if sl_initial else sl_cap

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

            st.markdown(f"""
            <div class="{css}">
              <div class="big-label">{sym} — IN TRADE</div>
              <div class="big-value {clr}">{'🟢 LONG (BUY)' if ot_dir == SIGNAL_BUY else '🔴 SHORT (SELL)'}</div>
              <div class="metric-row">
                <div class="metric-box"><div class="mbox-label">Entry</div><div class="mbox-val">{ot_entry}</div></div>
                <div class="metric-box"><div class="mbox-label">🔄 Trailing SL</div><div class="mbox-val" style="color:#f59e0b">{ot_sl}</div></div>
                <div class="metric-box"><div class="mbox-label">Target</div><div class="mbox-val">{ot_target}</div></div>
                <div class="metric-box"><div class="mbox-label">Live Price</div><div class="mbox-val">{live_px}</div></div>
                <div class="metric-box"><div class="mbox-label">Unrealised</div><div class="mbox-val" style="color:{unr_clr}">{unrealised:+.1f}</div></div>
                <div class="metric-box"><div class="mbox-label">RSI</div><div class="mbox-val">{rsi_v or '—'}</div></div>
                <div class="metric-box"><div class="mbox-label">ADX</div><div class="mbox-val">{adx_v or '—'}</div></div>
                <div class="metric-box"><div class="mbox-label">Supertrend</div><div class="mbox-val">{st_v or '—'}</div></div>
              </div>
              <div style="margin-top:8px;font-size:12px;color:#8b949e;">
                Entry: {str(ot_time)[11:16] if ot_time else '—'}
                &nbsp;|&nbsp; Holding until: SL hit, reverse signal, or 15:15 IST
              </div>
              <div class="ts">Candle: {ct or 'N/A'} &nbsp;|&nbsp; Refreshed: {datetime.datetime.now().strftime('%H:%M:%S')}</div>
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

        st.markdown(f"""
        <div class="{css}">
          <div class="big-label">{sym}</div>
          <div class="big-value {clr}">{lbl}</div>
          <div class="metric-row">
            <div class="metric-box"><div class="mbox-label">Entry</div><div class="mbox-val">{entry or '—'}</div></div>
            <div class="metric-box"><div class="mbox-label">Initial SL</div><div class="mbox-val">{sl or '—'}</div></div>
            <div class="metric-box"><div class="mbox-label">Target</div><div class="mbox-val">{target or '—'}</div></div>
            <div class="metric-box"><div class="mbox-label">RSI</div><div class="mbox-val">{rsi_v or '—'}</div></div>
            <div class="metric-box"><div class="mbox-label">ADX</div><div class="mbox-val">{adx_v or '—'}</div></div>
            <div class="metric-box"><div class="mbox-label">VWAP</div><div class="mbox-val">{vwap_v or '—'}</div></div>
            <div class="metric-box"><div class="mbox-label">Supertrend</div><div class="mbox-val">{st_v or '—'}</div></div>
            <div class="metric-box"><div class="mbox-label">EMA 9 / 21</div><div class="mbox-val">{ema_f or '—'} / {ema_s or '—'}</div></div>
            <div class="metric-box"><div class="mbox-label">Trades Left</div><div class="mbox-val">{left}</div></div>
          </div>
          <div style="margin-top:10px;font-size:12px;color:#8b949e;">
            <b>VWAP Filter:</b> {vwap_status} &nbsp;|&nbsp; <b>Status:</b> {reason}
          </div>
          <div class="ts">Candle: {ct or 'N/A'} &nbsp;|&nbsp; Refreshed: {datetime.datetime.now().strftime('%H:%M:%S')}</div>
        </div>
        """, unsafe_allow_html=True)

    def _mini_chart(df: pd.DataFrame):
        if df.empty or len(df) < 5:
            return
        st.line_chart(df.tail(30)[["datetime", "close"]].set_index("datetime"), height=120, use_container_width=True)

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
        _mini_chart(st.session_state.candles.get(syms[0], pd.DataFrame()))
    with c2:
        st.markdown(f"#### {config.INSTRUMENTS[syms[1]]['symbol']}")
        _signal_card(syms[1], st.session_state.signals.get(syms[1], {}))
        _mini_chart(st.session_state.candles.get(syms[1], pd.DataFrame()))

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

            # ── stat cards ────────────────────────────────────────────────────
            cards = [
                ("Total Trades",      s["total_trades"],               "neu-clr"),
                ("Wins ✅",           s["wins"],                       "win-clr"),
                ("Losses ❌",         s["losses"],                     "loss-clr"),
                ("Breakeven",         s["breakeven"],                  "neu-clr"),
                ("Win Rate",          f"{s['win_rate_pct']}%",         wr_clr),
                ("Total Points",      s["total_points"],               total_pts_clr),
                ("Avg Win (pts)",     s["avg_win_pts"],                "win-clr"),
                ("Avg Loss (pts)",    s["avg_loss_pts"],               "loss-clr"),
                ("Risk : Reward",     s["risk_reward"],                "neu-clr"),
                ("Max Drawdown",      s.get("max_drawdown", "—"),      "warn-clr"),
                ("Max Consec Loss",   s.get("max_consec_loss", "—"),   "warn-clr"),
            ]
            cols = st.columns(len(cards))
            for col, (lbl, val, clr) in zip(cols, cards):
                col.markdown(
                    f"<div class='stat-card'>"
                    f"<div class='stat-val {clr}'>{val}</div>"
                    f"<div class='stat-lbl'>{lbl}</div>"
                    f"</div>", unsafe_allow_html=True)

            st.markdown("<br>", unsafe_allow_html=True)

            # ── charts ────────────────────────────────────────────────────────
            ch1, ch2 = st.columns(2)
            with ch1:
                st.markdown("**📈 Equity Curve (Cumulative Points)**")
                eq_df = pd.DataFrame({"Cumulative Points": trades["points"].cumsum().values})
                st.line_chart(eq_df, height=200, use_container_width=True)
            with ch2:
                st.markdown("**📊 Daily P&L (Points)**")
                dpnl = (trades.groupby("date")["points"].sum()
                        .reset_index().rename(columns={"points": "Points"}))
                dpnl["date"] = dpnl["date"].astype(str)
                st.bar_chart(dpnl.set_index("date")["Points"], height=200, use_container_width=True)

            # ── trade table ───────────────────────────────────────────────────
            st.markdown("**📋 Trade-by-Trade Log**")
            disp = trades[[
                "date", "direction", "entry_time", "entry_price",
                "exit_time", "exit_price", "exit_reason",
                "points", "result", "rsi", "adx", "vwap",
            ]].copy()
            disp["entry_time"] = pd.to_datetime(disp["entry_time"]).dt.strftime("%H:%M")
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

            # ── buy/sell breakdown ────────────────────────────────────────────
            st.markdown("**Buy vs Sell breakdown**")
            d1, d2 = st.columns(2)
            for col_w, direction in zip([d1, d2], ["BUY", "SELL"]):
                sub = trades[trades["direction"] == direction]
                if sub.empty:
                    col_w.info(f"No {direction} trades.")
                    continue
                w  = (sub["result"] == "WIN").sum()
                l  = (sub["result"] == "LOSS").sum()
                wr = round(w / len(sub) * 100, 1)
                pts = round(sub["points"].sum(), 2)
                col_w.markdown(
                    f"**{direction}** — {len(sub)} trades &nbsp;|&nbsp; "
                    f"<span style='color:#22c55e'>{w}W</span> / "
                    f"<span style='color:#ef4444'>{l}L</span> &nbsp;|&nbsp; "
                    f"Win rate: **{wr}%** &nbsp;|&nbsp; Points: **{pts}**",
                    unsafe_allow_html=True)

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
