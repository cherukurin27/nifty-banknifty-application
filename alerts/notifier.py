"""
alerts/notifier.py — Desktop notification + Telegram + console alert on new signals.

Telegram setup (optional):
  1. Set TELEGRAM_BOT_TOKEN in config.py
  2. Set TELEGRAM_CHAT_ID   in config.py
  Leave both empty ("") to use desktop-only alerts.
"""

from __future__ import annotations
import os
import sys
import datetime
import csv
from logzero import logger

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import config

# ── Try desktop notifications (optional dependency) ──────────────────────────
try:
    from plyer import notification as _notif
    _NOTIF_AVAILABLE = True
except ImportError:
    _NOTIF_AVAILABLE = False

# ── Telegram (uses requests, already in requirements) ─────────────────────────
try:
    import requests as _requests
    _REQUESTS_AVAILABLE = True
except ImportError:
    _REQUESTS_AVAILABLE = False


def _desktop_alert(title: str, message: str) -> None:
    if _NOTIF_AVAILABLE:
        try:
            _notif.notify(title=title, message=message, timeout=8)
        except Exception as exc:
            logger.warning("Desktop notify failed: %s", exc)


def _telegram_alert(title: str, message: str) -> None:
    """Send a Telegram message if bot token + chat_id are configured."""
    token   = getattr(config, "TELEGRAM_BOT_TOKEN", "")
    chat_id = getattr(config, "TELEGRAM_CHAT_ID",   "")
    if not (token and chat_id and _REQUESTS_AVAILABLE):
        return
    text = f"*{title}*\n{message}"
    url  = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        resp = _requests.post(url, data={"chat_id": chat_id, "text": text,
                                         "parse_mode": "Markdown"}, timeout=5)
        if resp.status_code != 200:
            logger.warning("Telegram alert failed: %s", resp.text)
        else:
            logger.debug("Telegram alert sent: %s", title)
    except Exception as exc:
        logger.warning("Telegram request error: %s", exc)


def send_signal_alert(symbol: str, sig: dict) -> None:
    """
    Send desktop + Telegram notification and log to CSV when a BUY or SELL fires.
    """
    direction = sig["signal"]
    if direction == "NONE":
        return

    title   = f"🔔 {direction} — {symbol}"
    message = (
        f"Entry : {sig['entry']}\n"
        f"SL    : {sig['sl']}\n"
        f"Target: {sig['target']}\n"
        f"RSI   : {sig['rsi']}  ADX: {sig['adx']}\n"
        f"Time  : {sig['candle_time']}"
    )

    # Console
    print(f"\n{'='*50}")
    print(f"  {title}")
    print(message)
    print('='*50)

    # Desktop
    _desktop_alert(title, message)

    # Telegram
    _telegram_alert(title, message)

    # CSV log
    _append_to_log(symbol, sig)


def _append_to_log(symbol: str, sig: dict) -> None:
    os.makedirs(config.LOG_DIR, exist_ok=True)
    file_exists = os.path.isfile(config.SIGNAL_LOG)

    with open(config.SIGNAL_LOG, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "timestamp", "symbol", "signal", "entry", "sl", "target",
            "rsi", "adx", "vwap", "st_value", "ema_fast", "ema_slow", "candle_time",
        ])
        if not file_exists:
            writer.writeheader()
        writer.writerow({
            "timestamp"  : datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "symbol"     : symbol,
            "signal"     : sig["signal"],
            "entry"      : sig["entry"],
            "sl"         : sig["sl"],
            "target"     : sig["target"],
            "rsi"        : sig["rsi"],
            "adx"        : sig["adx"],
            "vwap"       : sig["vwap"],
            "st_value"   : sig["st_value"],
            "ema_fast"   : sig["ema_fast"],
            "ema_slow"   : sig["ema_slow"],
            "candle_time": sig["candle_time"],
        })
    logger.info("Signal logged to CSV: %s %s @ %s", symbol, sig["signal"], sig["entry"])
