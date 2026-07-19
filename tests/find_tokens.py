"""Verify NSE equity tokens by attempting a 1-day candle fetch for each."""
import sys, os, datetime
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from feed.angel_auth import get_session
from feed.data_feed import fetch_candles

api, _, _ = get_session()

# Confirmed from instrument master + known Angel One token list
candidates = {
    "RELIANCE" : ("2885",  "NSE"),
    "HDFCBANK" : ("1333",  "NSE"),   # well-known token, verify via fetch
    "ICICIBANK": ("4963",  "NSE"),
    "INFY"     : ("1594",  "NSE"),
    "TCS"      : ("11536", "NSE"),
    "HCLTECH"  : ("7229",  "NSE"),
}

for sym, (token, exch) in candidates.items():
    df = fetch_candles(api, token, exch, days_back=3)
    if df.empty:
        print(f"  {sym:12s}  token={token:7s}  FAILED — no candles returned")
    else:
        last = df.iloc[-1]
        print(f"  {sym:12s}  token={token:7s}  OK  last={last['close']:.2f}  candles={len(df)}")
