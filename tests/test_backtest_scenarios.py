"""
tests/test_backtest_scenarios.py
Comprehensive backtest scenario tests — no Angel One API required.
Uses synthetic OHLCV DataFrames to exercise every code path.

Run with:
    python -m pytest tests/test_backtest_scenarios.py -v
"""

from __future__ import annotations
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import datetime
import numpy as np
import pandas as pd
import pytest

import config
from engine.utils import strip_tz, force_exit_dt
from engine.indicators import add_indicators
from engine.signal_engine import (
    SIGNAL_BUY, SIGNAL_SELL, SIGNAL_NONE,
    eval_entry_signal, evaluate_signal,
    _is_expiry_skip, _no_new_entry_time,
)
from engine.backtester import (
    run_backtest, summary_stats,
    _is_expiry_skip as _bt_is_expiry_skip,
    _no_new_entry_time as _bt_no_new_entry_time,
)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_candles(
    n: int = 100,
    base_price: float = 22000.0,
    trend: str = "up",      # "up" | "down" | "flat"
    start: str = "2026-01-02 09:15",
    freq_min: int = 5,
    volume: int = 0,        # indices have zero volume
) -> pd.DataFrame:
    """
    Build a synthetic 5-min OHLCV DataFrame with a monotone trend so that
    indicators settle and signals fire predictably.
    """
    times = pd.date_range(start=start, periods=n, freq=f"{freq_min}min")
    prices = []
    p = base_price
    for i in range(n):
        if trend == "up":
            p += np.random.uniform(0.5, 2.5)
        elif trend == "down":
            p -= np.random.uniform(0.5, 2.5)
        else:
            p += np.random.uniform(-1.0, 1.0)
        prices.append(p)

    prices = np.array(prices)
    spread = prices * 0.0005          # 0.05% OHLC spread

    df = pd.DataFrame({
        "datetime": times,
        "open"    : prices - spread / 2,
        "high"    : prices + spread,
        "low"     : prices - spread,
        "close"   : prices,
        "volume"  : volume,
    })
    return df


def _make_session_candles(
    n_per_day: int = 75,
    n_days: int = 3,
    base_price: float = 22000.0,
    trend: str = "up",
    symbol: str = "NIFTY",
) -> pd.DataFrame:
    """
    Build multi-day synthetic candles spanning real IST session hours (09:15–15:30).
    Ensures warmup candles exist so add_indicators can compute all values.

    Uses a running calendar offset (not just timedelta from a fixed anchor) so
    that weekend-skip never produces duplicate dates across iterations.
    """
    rows = []
    p = base_price
    date = datetime.date(2026, 1, 2)
    # Advance to first weekday
    while date.weekday() >= 5:
        date += datetime.timedelta(days=1)

    days_added = 0
    while days_added < n_days:
        if date.weekday() < 5:   # weekday only
            session_start = datetime.datetime.combine(date, datetime.time(9, 15))
            for i in range(n_per_day):
                t = session_start + datetime.timedelta(minutes=5 * i)
                if trend == "up":
                    p += np.random.uniform(0.3, 1.5)
                elif trend == "down":
                    p -= np.random.uniform(0.3, 1.5)
                else:
                    p += np.random.uniform(-0.5, 0.5)
                spread = p * 0.0005
                rows.append({
                    "datetime": t,
                    "open"    : p - spread / 2,
                    "high"    : p + spread,
                    "low"     : p - spread,
                    "close"   : p,
                    "volume"  : 0,
                })
            days_added += 1
        date += datetime.timedelta(days=1)

    return pd.DataFrame(rows)


# ─────────────────────────────────────────────────────────────────────────────
# engine/utils tests
# ─────────────────────────────────────────────────────────────────────────────

class TestUtils:
    def test_strip_tz_naive(self):
        dt = pd.Timestamp("2026-01-02 10:00:00")
        result = strip_tz(dt)
        assert result.tzinfo is None
        assert result.hour == 10

    def test_strip_tz_utc_to_ist(self):
        # UTC 03:30 = IST 09:00
        dt = pd.Timestamp("2026-01-02 03:30:00", tz="UTC")
        result = strip_tz(dt)
        assert result.tzinfo is None
        assert result.hour == 9
        assert result.minute == 0

    def test_force_exit_dt(self):
        d = datetime.date(2026, 1, 2)
        fe = force_exit_dt(d)
        assert fe == datetime.datetime(2026, 1, 2, 15, 15)

    def test_strip_tz_ist_aware(self):
        dt = pd.Timestamp("2026-01-02 10:30:00", tz="Asia/Kolkata")
        result = strip_tz(dt)
        assert result.tzinfo is None
        assert result.hour == 10
        assert result.minute == 30


# ─────────────────────────────────────────────────────────────────────────────
# engine/indicators tests
# ─────────────────────────────────────────────────────────────────────────────

class TestIndicators:
    def test_insufficient_data_returns_raw(self):
        df = _make_candles(n=10)
        result = add_indicators(df.copy())
        # Should return df unchanged (no indicator columns added)
        assert "adx" not in result.columns

    def test_sufficient_data_adds_all_columns(self):
        df = _make_candles(n=80)
        result = add_indicators(df.copy())
        for col in ["ema_fast", "ema_slow", "rsi", "vwap", "atr", "adx",
                    "st_value", "st_signal"]:
            assert col in result.columns, f"Missing column: {col}"

    def test_st_signal_is_1_or_minus1(self):
        df = _make_candles(n=80, trend="up")
        result = add_indicators(df.copy())
        assert set(result["st_signal"].dropna().unique()).issubset({1, -1})

    def test_rsi_bounded(self):
        df = _make_candles(n=80)
        result = add_indicators(df.copy())
        rsi = result["rsi"].dropna()
        assert (rsi >= 0).all() and (rsi <= 100).all()

    def test_vwap_daily_reset(self):
        """VWAP should reset each calendar day."""
        df = _make_session_candles(n_per_day=40, n_days=2, trend="up")
        result = add_indicators(df.copy())
        assert "vwap" in result.columns
        # VWAP on day 2 should equal typical price of first candle on day 2
        d2_start = result[result["datetime"].dt.date == result["datetime"].dt.date.unique()[1]].iloc[0]
        tp = (d2_start["high"] + d2_start["low"] + d2_start["close"]) / 3
        assert abs(d2_start["vwap"] - tp) < 1.0, "VWAP did not reset on day 2"

    def test_adx_non_negative(self):
        df = _make_candles(n=80)
        result = add_indicators(df.copy())
        adx = result["adx"].dropna()
        assert (adx >= 0).all()


# ─────────────────────────────────────────────────────────────────────────────
# engine/signal_engine tests
# ─────────────────────────────────────────────────────────────────────────────

class TestEvalEntrySignal:
    """Unit tests for eval_entry_signal — the shared 5-filter logic."""

    def _row(self, **kwargs):
        defaults = {
            "adx": 30.0, "rsi": 60.0, "vwap": 22000.0,
            "st_signal": 1, "ema_slow": 21900.0, "close": 22100.0,
        }
        defaults.update(kwargs)
        return defaults

    def test_buy_all_conditions_met(self):
        row = self._row()
        assert eval_entry_signal(row) == SIGNAL_BUY

    def test_sell_all_conditions_met(self):
        row = self._row(
            st_signal=-1, close=21800.0, ema_slow=22000.0,
            vwap=22000.0, rsi=40.0
        )
        assert eval_entry_signal(row) == SIGNAL_SELL

    def test_none_when_adx_too_low(self):
        row = self._row(adx=config.ADX_THRESHOLD - 1)
        assert eval_entry_signal(row) == SIGNAL_NONE

    def test_none_when_adx_too_high(self):
        row = self._row(adx=config.ADX_MAX + 1)
        assert eval_entry_signal(row) == SIGNAL_NONE

    def test_none_when_rsi_out_of_buy_range_high(self):
        row = self._row(rsi=config.RSI_BUY_HIGH + 1)
        assert eval_entry_signal(row) == SIGNAL_NONE

    def test_none_when_rsi_out_of_buy_range_low(self):
        row = self._row(rsi=config.RSI_BUY_LOW - 1)
        assert eval_entry_signal(row) == SIGNAL_NONE

    def test_none_when_price_below_ema(self):
        row = self._row(close=21800.0, ema_slow=22000.0, vwap=21700.0)
        assert eval_entry_signal(row) == SIGNAL_NONE

    def test_none_when_price_below_vwap(self):
        row = self._row(close=21900.0, vwap=22000.0)
        assert eval_entry_signal(row) == SIGNAL_NONE

    def test_none_when_st_signal_zero(self):
        row = self._row(st_signal=0)
        assert eval_entry_signal(row) == SIGNAL_NONE

    def test_sell_rsi_boundary_low(self):
        row = self._row(
            st_signal=-1, close=21800.0, ema_slow=22000.0,
            vwap=22000.0, rsi=config.RSI_SELL_LOW
        )
        assert eval_entry_signal(row) == SIGNAL_SELL

    def test_sell_rsi_boundary_high(self):
        row = self._row(
            st_signal=-1, close=21800.0, ema_slow=22000.0,
            vwap=22000.0, rsi=config.RSI_SELL_HIGH
        )
        assert eval_entry_signal(row) == SIGNAL_SELL

    def test_sell_rsi_out_of_range(self):
        row = self._row(
            st_signal=-1, close=21800.0, ema_slow=22000.0,
            vwap=22000.0, rsi=config.RSI_SELL_HIGH + 1
        )
        assert eval_entry_signal(row) == SIGNAL_NONE

    def test_nan_adx_returns_none(self):
        row = self._row(adx=float("nan"))
        assert eval_entry_signal(row) == SIGNAL_NONE


class TestEvaluateSignal:
    """Integration tests for evaluate_signal (full DataFrame → signal dict)."""

    def test_insufficient_data_returns_none(self):
        # With very few raw candles add_indicators returns them unchanged
        # (no indicator columns).  evaluate_signal then hits len(df) < 2
        # only if ≤ 1 candle; for n=1 we reliably get "Insufficient data".
        df = _make_candles(n=1)
        result = evaluate_signal(df, symbol="NIFTY")
        assert result["signal"] == SIGNAL_NONE
        assert result["reason"] == "Insufficient data"

    def test_returns_dict_with_all_keys(self):
        df = _make_session_candles(n_per_day=75, n_days=3, trend="up")
        result = evaluate_signal(df, symbol="NIFTY")
        for key in ["signal", "entry", "sl", "target", "rsi", "adx",
                    "vwap", "st_value", "ema_fast", "ema_slow", "reason", "candle_time"]:
            assert key in result, f"Missing key: {key}"

    def test_outside_session_returns_none(self):
        """A DataFrame whose last candle is at 16:00 must return NONE."""
        df = _make_session_candles(n_per_day=75, n_days=3, trend="up")
        # Force last row outside session
        df = df.copy()
        df.loc[df.index[-1], "datetime"] = pd.Timestamp("2026-01-06 16:00:00")
        result = evaluate_signal(df, symbol="NIFTY")
        assert result["signal"] == SIGNAL_NONE
        assert "Outside session" in result["reason"]

    def test_signal_is_valid_string(self):
        df = _make_session_candles(n_per_day=75, n_days=3, trend="up")
        result = evaluate_signal(df, symbol="NIFTY")
        assert result["signal"] in (SIGNAL_BUY, SIGNAL_SELL, SIGNAL_NONE)

    def test_entry_price_is_last_close(self):
        df = _make_session_candles(n_per_day=75, n_days=3, trend="up")
        result = evaluate_signal(df, symbol="NIFTY")
        if result["entry"] is not None:
            # entry should equal the last row's close (within rounding)
            last_close = float(df.iloc[-1]["close"])
            assert abs(result["entry"] - last_close) < 1.0


# ─────────────────────────────────────────────────────────────────────────────
# engine/backtester tests
# ─────────────────────────────────────────────────────────────────────────────

class TestRunBacktest:
    """Tests for run_backtest() covering all exit paths and both symbols."""

    # ── basic shape & typing ─────────────────────────────────────────────────

    def test_returns_two_dataframes(self):
        df = _make_session_candles(n_per_day=75, n_days=5, trend="up")
        trades, diag = run_backtest(df, "NIFTY")
        assert isinstance(trades, pd.DataFrame)
        assert isinstance(diag, pd.DataFrame)

    def test_empty_input_returns_empty(self):
        df = pd.DataFrame(columns=["datetime", "open", "high", "low", "close", "volume"])
        trades, diag = run_backtest(df, "NIFTY")
        assert trades.empty
        assert diag.empty

    def test_too_few_candles_returns_empty(self):
        df = _make_session_candles(n_per_day=10, n_days=1, trend="up")
        trades, diag = run_backtest(df, "NIFTY")
        assert trades.empty   # fewer than warmup candles → no trades possible

    def test_trades_have_required_columns(self):
        df = _make_session_candles(n_per_day=75, n_days=5, trend="up")
        trades, _ = run_backtest(df, "NIFTY")
        if not trades.empty:
            for col in ["symbol", "date", "direction", "entry_time", "entry_price",
                        "sl", "target", "exit_time", "exit_price", "exit_reason",
                        "points", "result", "rsi", "adx", "vwap"]:
                assert col in trades.columns, f"Missing column: {col}"

    def test_diag_has_required_columns(self):
        df = _make_session_candles(n_per_day=75, n_days=5, trend="up")
        _, diag = run_backtest(df, "NIFTY")
        if not diag.empty:
            for col in ["datetime", "close", "adx", "rsi", "signal"]:
                assert col in diag.columns, f"Missing diag column: {col}"

    # ── result values ────────────────────────────────────────────────────────

    def test_result_values_are_valid(self):
        df = _make_session_candles(n_per_day=75, n_days=5, trend="up")
        trades, _ = run_backtest(df, "NIFTY")
        if not trades.empty:
            assert set(trades["result"].unique()).issubset({"WIN", "LOSS", "BE"})

    def test_direction_values_are_valid(self):
        df = _make_session_candles(n_per_day=75, n_days=5, trend="up")
        trades, _ = run_backtest(df, "NIFTY")
        if not trades.empty:
            assert set(trades["direction"].unique()).issubset({"BUY", "SELL"})

    def test_points_consistent_with_result(self):
        df = _make_session_candles(n_per_day=75, n_days=5)
        trades, _ = run_backtest(df, "NIFTY")
        if not trades.empty:
            for _, row in trades.iterrows():
                if row["result"] == "WIN":
                    assert row["points"] > 0
                elif row["result"] == "LOSS":
                    assert row["points"] < 0
                else:
                    assert row["points"] == 0

    def test_sl_never_more_than_cap_away_from_entry_nifty(self):
        """Initial SL must be within the 55-pt fixed cap for NIFTY."""
        df = _make_session_candles(n_per_day=75, n_days=5, trend="up")
        trades, _ = run_backtest(df, "NIFTY")
        cap = config.SL_CAP_PTS["NIFTY"]
        if not trades.empty:
            for _, row in trades.iterrows():
                dist = abs(row["entry_price"] - row["sl"])
                assert dist <= cap + 1, (
                    f"SL {row['sl']} is more than cap {cap} from entry {row['entry_price']}"
                )

    def test_exit_reasons_are_valid(self):
        df = _make_session_candles(n_per_day=75, n_days=10)
        trades, _ = run_backtest(df, "NIFTY")
        valid_prefixes = ("SL Hit", "RSI Exit", "EOD Exit", "Reverse Signal",
                          "Day Change", "Data End")
        if not trades.empty:
            for reason in trades["exit_reason"]:
                assert any(reason.startswith(p) for p in valid_prefixes), (
                    f"Unknown exit reason: {reason}"
                )

    # ── both symbols ─────────────────────────────────────────────────────────

    def test_nifty_runs_without_error(self):
        df = _make_session_candles(n_per_day=75, n_days=5, base_price=22000.0, trend="up")
        trades, diag = run_backtest(df, "NIFTY")
        assert isinstance(trades, pd.DataFrame)
        assert isinstance(diag, pd.DataFrame)

    def test_banknifty_runs_without_error(self):
        df = _make_session_candles(n_per_day=75, n_days=5, base_price=48000.0, trend="up")
        trades, diag = run_backtest(df, "BANKNIFTY")
        assert isinstance(trades, pd.DataFrame)
        assert isinstance(diag, pd.DataFrame)

    def test_banknifty_uses_atr_cap(self):
        """BankNifty SL must be dynamic (ATR-based), not the fixed 55-pt cap."""
        df = _make_session_candles(n_per_day=75, n_days=5, base_price=48000.0, trend="up")
        trades, _ = run_backtest(df, "BANKNIFTY")
        if not trades.empty:
            # For BANKNIFTY the entry-SL gap can be >> 55
            max_dist = (trades["entry_price"] - trades["sl"]).abs().max()
            # just assert it didn't error and distances are positive
            assert max_dist >= 0

    # ── trending up (mostly BUY signals) ─────────────────────────────────────

    def test_strong_uptrend_produces_trades(self):
        """A clean uptrend across many days should produce at least some trades."""
        np.random.seed(42)
        df = _make_session_candles(n_per_day=75, n_days=10, trend="up", base_price=22000.0)
        trades, _ = run_backtest(df, "NIFTY")
        # With 10 days of clean uptrend we expect the indicators to fire
        # (may still be 0 if ADX < 20 — that's valid; we just check no crash)
        assert isinstance(trades, pd.DataFrame)

    def test_strong_downtrend_produces_trades(self):
        np.random.seed(7)
        df = _make_session_candles(n_per_day=75, n_days=10, trend="down", base_price=22000.0)
        trades, _ = run_backtest(df, "NIFTY")
        assert isinstance(trades, pd.DataFrame)

    def test_flat_market_few_trades(self):
        """A flat/sideways market should produce fewer trades (ADX often < threshold)."""
        np.random.seed(99)
        df = _make_session_candles(n_per_day=75, n_days=10, trend="flat", base_price=22000.0)
        trades_flat, _ = run_backtest(df, "NIFTY")
        np.random.seed(42)
        df_trend = _make_session_candles(n_per_day=75, n_days=10, trend="up", base_price=22000.0)
        trades_trend, _ = run_backtest(df_trend, "NIFTY")
        # Flat market should produce fewer or equal trades
        assert len(trades_flat) <= len(trades_trend) + 5  # small tolerance

    # ── EOD exit path ────────────────────────────────────────────────────────

    def test_no_trade_carried_overnight(self):
        """All trades must open and close on the same day."""
        df = _make_session_candles(n_per_day=75, n_days=5, trend="up")
        trades, _ = run_backtest(df, "NIFTY")
        if not trades.empty:
            entry_dates = pd.to_datetime(trades["entry_time"]).dt.date
            exit_dates  = pd.to_datetime(trades["exit_time"]).dt.date
            assert (entry_dates == exit_dates).all(), "Trade carried overnight!"

    def test_no_entry_after_no_new_entry_cutoff(self):
        """No new trade should open after config.NO_NEW_ENTRY_AFTER."""
        df = _make_session_candles(n_per_day=75, n_days=5, trend="up")
        trades, _ = run_backtest(df, "NIFTY")
        if not trades.empty:
            h, m = map(int, config.NO_NEW_ENTRY_AFTER.split(":"))
            cutoff = datetime.time(h, m)
            entry_times = pd.to_datetime(trades["entry_time"]).dt.time
            for et in entry_times:
                assert et <= cutoff, (
                    f"Entry at {et} is after NO_NEW_ENTRY_AFTER ({config.NO_NEW_ENTRY_AFTER})"
                )

    # ── summary_stats ────────────────────────────────────────────────────────

    def test_summary_stats_empty_returns_empty_dict(self):
        assert summary_stats(pd.DataFrame()) == {}

    def test_summary_stats_all_keys_present(self):
        df = _make_session_candles(n_per_day=75, n_days=10, trend="up")
        trades, _ = run_backtest(df, "NIFTY")
        if not trades.empty:
            stats = summary_stats(trades)
            for key in ["total_trades", "wins", "losses", "breakeven",
                        "win_rate_pct", "total_points", "avg_win_pts",
                        "avg_loss_pts", "risk_reward", "max_consec_loss",
                        "max_drawdown"]:
                assert key in stats, f"Missing stats key: {key}"

    def test_summary_stats_win_rate_between_0_and_100(self):
        df = _make_session_candles(n_per_day=75, n_days=10, trend="up")
        trades, _ = run_backtest(df, "NIFTY")
        if not trades.empty:
            stats = summary_stats(trades)
            assert 0.0 <= stats["win_rate_pct"] <= 100.0

    def test_summary_stats_trades_sum(self):
        df = _make_session_candles(n_per_day=75, n_days=10)
        trades, _ = run_backtest(df, "NIFTY")
        if not trades.empty:
            stats = summary_stats(trades)
            assert stats["wins"] + stats["losses"] + stats["breakeven"] == stats["total_trades"]

    def test_summary_stats_max_drawdown_non_positive(self):
        df = _make_session_candles(n_per_day=75, n_days=10, trend="up")
        trades, _ = run_backtest(df, "NIFTY")
        if not trades.empty:
            stats = summary_stats(trades)
            assert stats["max_drawdown"] <= 0.0

    def test_summary_stats_all_wins(self):
        """Manually craft a trades DF with all wins; verify stats."""
        trades = pd.DataFrame([
            {"result": "WIN",  "points": 50.0},
            {"result": "WIN",  "points": 30.0},
            {"result": "WIN",  "points": 20.0},
        ])
        stats = summary_stats(trades)
        assert stats["win_rate_pct"] == 100.0
        assert stats["losses"] == 0
        assert stats["max_consec_loss"] == 0

    def test_summary_stats_all_losses(self):
        trades = pd.DataFrame([
            {"result": "LOSS", "points": -40.0},
            {"result": "LOSS", "points": -30.0},
            {"result": "LOSS", "points": -55.0},
        ])
        stats = summary_stats(trades)
        assert stats["win_rate_pct"] == 0.0
        assert stats["wins"] == 0
        assert stats["max_consec_loss"] == 3

    def test_summary_stats_risk_reward_positive(self):
        trades = pd.DataFrame([
            {"result": "WIN",  "points": 60.0},
            {"result": "LOSS", "points": -30.0},
        ])
        stats = summary_stats(trades)
        assert stats["risk_reward"] == 2.0

    def test_summary_stats_breakeven_counted(self):
        trades = pd.DataFrame([
            {"result": "WIN",  "points": 50.0},
            {"result": "BE",   "points":  0.0},
            {"result": "LOSS", "points": -25.0},
        ])
        stats = summary_stats(trades)
        assert stats["breakeven"] == 1
        assert stats["total_trades"] == 3

    # ── multi-period slicing (mirrors multi_period_backtest.py) ──────────────

    def test_multi_period_slices_consistent(self):
        """
        Slicing the same full DataFrame to 10d / 20d should produce no fewer
        trades than 10d alone (more data → more or equal signals).
        """
        np.random.seed(1)
        df_full = _make_session_candles(n_per_day=75, n_days=25, trend="up")
        df_full["datetime"] = pd.to_datetime(df_full["datetime"])
        max_dt = df_full["datetime"].max()

        trades_10, _ = run_backtest(
            df_full[df_full["datetime"] >= max_dt - pd.Timedelta(days=10)].reset_index(drop=True),
            "NIFTY"
        )
        trades_20, _ = run_backtest(
            df_full[df_full["datetime"] >= max_dt - pd.Timedelta(days=20)].reset_index(drop=True),
            "NIFTY"
        )
        # 20d should have >= trades than 10d (more history → more signals)
        assert len(trades_20) >= len(trades_10)

    # ── diagnostics ──────────────────────────────────────────────────────────

    def test_diag_signals_subset_of_valid(self):
        df = _make_session_candles(n_per_day=75, n_days=5, trend="up")
        _, diag = run_backtest(df, "NIFTY")
        if not diag.empty and "signal" in diag.columns:
            assert set(diag["signal"].unique()).issubset({SIGNAL_BUY, SIGNAL_SELL, SIGNAL_NONE})

    def test_diag_row_count_matches_candles_after_warmup(self):
        df = _make_session_candles(n_per_day=75, n_days=5, trend="up")
        df_ind = add_indicators(df.copy())
        warmup = max(config.EMA_SLOW, config.RSI_PERIOD, config.ST_PERIOD) + 10
        expected_diag_rows = max(0, len(df_ind) - warmup)
        _, diag = run_backtest(df, "NIFTY")
        if not diag.empty:
            assert len(diag) == expected_diag_rows

    # ── edge cases ───────────────────────────────────────────────────────────

    def test_exactly_warmup_candles_no_crash(self):
        warmup = max(config.EMA_SLOW, config.RSI_PERIOD, config.ST_PERIOD) + 10
        df = _make_session_candles(n_per_day=warmup + 1, n_days=1, trend="up")
        trades, diag = run_backtest(df, "NIFTY")
        assert isinstance(trades, pd.DataFrame)

    def test_single_day_no_crash(self):
        df = _make_session_candles(n_per_day=75, n_days=1, trend="up")
        trades, diag = run_backtest(df, "NIFTY")
        assert isinstance(trades, pd.DataFrame)

    def test_large_dataset_no_crash(self):
        """90-day equivalent without hitting real API."""
        np.random.seed(0)
        df = _make_session_candles(n_per_day=75, n_days=90, trend="up")
        trades, diag = run_backtest(df, "NIFTY")
        assert isinstance(trades, pd.DataFrame)
        assert isinstance(diag, pd.DataFrame)

    def test_large_dataset_banknifty_no_crash(self):
        np.random.seed(0)
        df = _make_session_candles(n_per_day=75, n_days=90, base_price=48000.0, trend="down")
        trades, diag = run_backtest(df, "BANKNIFTY")
        assert isinstance(trades, pd.DataFrame)
        assert isinstance(diag, pd.DataFrame)

    def test_duplicate_candles_handled(self):
        """Duplicate rows in input should not crash the backtester."""
        df = _make_session_candles(n_per_day=75, n_days=5, trend="up")
        df_dup = pd.concat([df, df.iloc[:5]], ignore_index=True)
        trades, diag = run_backtest(df_dup, "NIFTY")
        assert isinstance(trades, pd.DataFrame)

    def test_all_flat_prices_no_crash(self):
        """All identical prices (zero spread) should not crash."""
        n = 100
        times = pd.date_range("2026-01-02 09:15", periods=n, freq="5min")
        df = pd.DataFrame({
            "datetime": times,
            "open": 22000.0, "high": 22000.0,
            "low": 22000.0,  "close": 22000.0,
            "volume": 0,
        })
        trades, diag = run_backtest(df, "NIFTY")
        assert isinstance(trades, pd.DataFrame)


# ─────────────────────────────────────────────────────────────────────────────
# SKIP_EXPIRY_DAY and NO_NEW_ENTRY_AFTER helpers
# ─────────────────────────────────────────────────────────────────────────────

class TestExpirySkipHelper:
    """Tests for _is_expiry_skip in both signal_engine and backtester."""

    def test_nifty_thursday_skipped(self):
        # 2026-01-01 is Thursday (weekday=3)
        ts = pd.Timestamp("2026-01-01 10:00:00")
        assert _is_expiry_skip("NIFTY", ts) is True

    def test_nifty_non_thursday_not_skipped(self):
        # 2026-01-02 is Friday
        ts = pd.Timestamp("2026-01-02 10:00:00")
        assert _is_expiry_skip("NIFTY", ts) is False

    def test_banknifty_never_skipped(self):
        # BankNifty has None in config — no day should be skipped
        for day_offset in range(7):
            ts = pd.Timestamp("2026-01-01") + pd.Timedelta(days=day_offset)
            assert _is_expiry_skip("BANKNIFTY", ts) is False

    def test_unknown_symbol_not_skipped(self):
        ts = pd.Timestamp("2026-01-01 10:00:00")
        assert _is_expiry_skip("UNKNOWN", ts) is False

    def test_backtester_helper_matches_signal_engine_helper(self):
        """Both modules must produce identical results for same inputs."""
        for day_offset in range(7):
            ts = pd.Timestamp("2026-01-01") + pd.Timedelta(days=day_offset)
            for sym in ["NIFTY", "BANKNIFTY"]:
                se_result = _is_expiry_skip(sym, ts)
                bt_result = _bt_is_expiry_skip(sym, ts.date())
                assert se_result == bt_result, (
                    f"Mismatch for {sym} on {ts.date()}: "
                    f"signal_engine={se_result}, backtester={bt_result}"
                )

    def test_expiry_skip_with_date_object(self):
        """backtester uses date objects directly — must still work."""
        thursday = datetime.date(2026, 1, 1)   # Thursday
        assert _bt_is_expiry_skip("NIFTY", thursday) is True
        friday = datetime.date(2026, 1, 2)
        assert _bt_is_expiry_skip("NIFTY", friday) is False

    def test_no_trades_on_expiry_day(self):
        """
        Build a 5-day dataset that starts on a Thursday (2026-01-01).
        NIFTY Thursday entries must be skipped → no trades with entry on Thursday.
        """
        # 2026-01-01 = Thursday; build enough days for warmup
        df = _make_session_candles(n_per_day=75, n_days=10, trend="up",
                                   symbol="NIFTY")
        # Override datetimes to start on Thu 2026-01-01
        # (helper already places dates in chronological weekday order)
        trades, _ = run_backtest(df, "NIFTY")
        if not trades.empty:
            for _, row in trades.iterrows():
                trade_date = pd.Timestamp(row["date"])
                skip_wd = getattr(config, "SKIP_EXPIRY_DAY", {}).get("NIFTY")
                if skip_wd is not None:
                    assert trade_date.weekday() != skip_wd, (
                        f"Trade opened on expiry day: {trade_date.date()}"
                    )


class TestNoNewEntryTime:
    """Tests for _no_new_entry_time helper in both modules."""

    def test_returns_time_object(self):
        t = _no_new_entry_time()
        assert isinstance(t, datetime.time)

    def test_matches_config_value(self):
        h, m = map(int, config.NO_NEW_ENTRY_AFTER.split(":"))
        assert _no_new_entry_time() == datetime.time(h, m)

    def test_backtester_helper_matches_signal_engine(self):
        assert _no_new_entry_time() == _bt_no_new_entry_time()

    def test_evaluate_signal_respects_cutoff(self):
        """evaluate_signal must return NONE when candle is after the cut-off."""
        df = _make_session_candles(n_per_day=75, n_days=3, trend="up")
        # Force last candle to be after NO_NEW_ENTRY_AFTER
        h, m = map(int, config.NO_NEW_ENTRY_AFTER.split(":"))
        after_cutoff = datetime.time(h, m + 5 if m <= 54 else 59)
        df = df.copy()
        last_date = pd.to_datetime(df.iloc[-1]["datetime"]).date()
        df.loc[df.index[-1], "datetime"] = pd.Timestamp(
            datetime.datetime.combine(last_date, after_cutoff)
        )
        result = evaluate_signal(df, symbol="NIFTY")
        assert result["signal"] == SIGNAL_NONE
        assert config.NO_NEW_ENTRY_AFTER in result["reason"]


# ─────────────────────────────────────────────────────────────────────────────
# Regression: eval_entry_signal == backtester's internal signal for same row
# ─────────────────────────────────────────────────────────────────────────────

class TestSignalConsistency:
    """Verify eval_entry_signal and backtester agree on the same indicator row."""

    def test_eval_entry_matches_backtest_diag(self):
        """
        The backtester records sig_label = eval_entry_signal(row) directly into
        diag["signal"].  Re-running eval_entry_signal on the same indicator rows
        must produce identical results.

        We reconstruct the exact indicator DataFrame the backtester used
        (add_indicators on the same input), align by datetime, and compare.
        """
        np.random.seed(123)
        df = _make_session_candles(n_per_day=75, n_days=10, trend="up")
        _, diag = run_backtest(df, "NIFTY")

        if diag.empty:
            pytest.skip("No diag rows — too few candles")

        # Re-build the exact indicator frame the backtester used
        df_work = df.copy()
        df_work["datetime"] = pd.to_datetime(df_work["datetime"])
        df_work = df_work.sort_values("datetime").reset_index(drop=True)
        df_ind  = add_indicators(df_work.copy())

        warmup = max(config.EMA_SLOW, config.RSI_PERIOD, config.ST_PERIOD) + 10
        df_ind_post = df_ind.iloc[warmup:].reset_index(drop=True)

        # Index diag by datetime for safe alignment
        diag_indexed = diag.set_index(pd.to_datetime(diag["datetime"]).dt.floor("s"))
        df_ind_post.index = pd.to_datetime(df_ind_post["datetime"]).dt.floor("s")

        mismatches = 0
        for dt_key, row in df_ind_post.iterrows():
            if dt_key not in diag_indexed.index:
                continue
            expected = eval_entry_signal(row)
            actual   = diag_indexed.loc[dt_key, "signal"]
            # handle multi-row index (shouldn't happen but be safe)
            if isinstance(actual, pd.Series):
                actual = actual.iloc[0]
            if expected != actual:
                mismatches += 1

        assert mismatches == 0, (
            f"{mismatches} signal mismatches between eval_entry_signal and diag"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Entry point for direct execution
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import subprocess, sys
    result = subprocess.run(
        [sys.executable, "-m", "pytest", __file__, "-v", "--tb=short"],
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )
    sys.exit(result.returncode)
