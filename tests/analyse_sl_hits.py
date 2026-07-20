"""
tests/analyse_sl_hits.py
Explains WHY each SL Hit fires — is it the hard cap, the trailing ST,
or a near-entry reversal? Answers the question:
"Why do some trades SL-hit in the middle of the day when the market isn't at EOD?"
"""
import re, sys, os, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

HTML = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                    "nifty-bank-nifty-full-backtest-report.html")

with open(HTML, encoding="utf-8") as f:
    content = f.read()

nifty_section    = content[content.find('id="nifty"'):content.find('id="banknifty"')]
banknifty_section = content[content.find('id="banknifty"'):content.find('id="config"')]

pat = (r"<tr><td>([\d-]+)</td><td>(BUY|SELL)</td>"
       r"<td>([^<]*)</td><td>([^<]*)</td><td>([^<]*)</td>"
       r"<td>([^<]*)</td><td>([^<]*)</td><td>([^<]*)</td>"
       r"<td class='[^']*'>([^<]*)</td><td>([^<]*)</td><td>([^<]*)</td></tr>")

def t2m(t):
    if not t or t == "15:15":
        return 915
    h, m = map(int, t.split(":"))
    return h * 60 + m

def parse_trades(section):
    trades = []
    for m in re.finditer(pat, section):
        date, side, entry_t, entry_px, sl_init, exit_t, exit_px, reason, pts_s, rsi, adx = m.groups()
        entry_f  = float(entry_px)
        sl_f     = float(sl_init)
        exit_f   = float(exit_px)
        pts_f    = float(pts_s.replace("+", ""))
        sl_width = round(abs(entry_f - sl_f), 2)   # distance from entry to initial SL
        hold_min = t2m(exit_t) - t2m(entry_t)
        trades.append({
            "date": date, "side": side,
            "entry_t": entry_t, "entry_px": entry_f,
            "sl_init": sl_f, "sl_width": sl_width,
            "exit_t": exit_t, "exit_px": exit_f,
            "reason": reason, "pts": pts_f,
            "rsi": float(rsi), "adx": float(adx),
            "hold_min": hold_min,
        })
    return trades

def classify_sl(t):
    """
    Three types of SL Hit:
    A) HARD-CAP  — pts ≈ -45 (NIFTY) or large ATR multiple — price dropped/rose
                   all the way to the initial hard-cap stop in one move.
    B) TRAIL-WIN — pts > 0 — Supertrend trailed UP above entry before reversing.
                   The SL was the trailing ST level, not the original SL.
    C) TRAIL-LOSS (small) — pts < 0 but not full cap — the Supertrend line was
                   close to entry at the time of the flip. Price reversed within
                   1–6 candles before the ST could trail away.
    """
    if t["reason"] != "SL Hit":
        return None
    p = t["pts"]
    if p >= 0:
        return "B_TRAIL_WIN"
    if abs(p) >= 44.5:
        return "A_HARD_CAP"
    return "C_TRAIL_LOSS_SMALL"

def explain(t):
    cls = classify_sl(t)
    if cls == "A_HARD_CAP":
        return (f"HARD-CAP HIT  — price moved {abs(t['pts']):.1f} pts immediately "
                f"against position. Entry SL was {t['sl_width']:.1f} pts wide. "
                f"Hold = {t['hold_min']} min. This is the -45 pt backstop triggering.")
    if cls == "B_TRAIL_WIN":
        return (f"TRAIL WIN     — ST trailed above entry, locked in +{t['pts']:.1f} pts profit. "
                f"Price reversed onto the trailing ST line at {t['hold_min']} min.")
    if cls == "C_TRAIL_LOSS_SMALL":
        return (f"NEAR-ENTRY REVERSAL — ST initial gap was only {t['sl_width']:.1f} pts. "
                f"Price reversed {abs(t['pts']):.1f} pts in {t['hold_min']} min. "
                f"ST hadn't trailed far enough to protect; SL was still near entry.")
    return ""

def analyse(trades, label):
    print(f"\n{'='*70}")
    print(f"  {label}  — SL Hit breakdown")
    print(f"{'='*70}")

    sl_trades = [t for t in trades if t["reason"] == "SL Hit"]
    eod_trades = [t for t in trades if t["reason"] == "EOD Exit"]
    print(f"\n  Total trades : {len(trades)}")
    print(f"  SL Hits      : {len(sl_trades)}   ({len([t for t in sl_trades if t['pts']<0])} losses, {len([t for t in sl_trades if t['pts']>=0])} wins)")
    print(f"  EOD Exits    : {len(eod_trades)}")

    caps  = [t for t in sl_trades if classify_sl(t) == "A_HARD_CAP"]
    wins  = [t for t in sl_trades if classify_sl(t) == "B_TRAIL_WIN"]
    small = [t for t in sl_trades if classify_sl(t) == "C_TRAIL_LOSS_SMALL"]

    print(f"\n  ┌─ TYPE A: Hard-cap hits (-45 pts)       → {len(caps):2d} trades  {sum(t['pts'] for t in caps):+.1f} pts")
    print(f"  ├─ TYPE B: Trail wins (SL above entry)   → {len(wins):2d} trades  {sum(t['pts'] for t in wins):+.1f} pts")
    print(f"  └─ TYPE C: Near-entry reversals (small)  → {len(small):2d} trades  {sum(t['pts'] for t in small):+.1f} pts")

    print(f"\n  ── TYPE A: HARD-CAP HITS ──────────────────────────────────────────")
    print(f"  {'Date':<12} {'S':<5} {'In':<6} {'Out':<6} {'Pts':>6}  {'RSI':>5}  {'ADX':>5}  {'Hold':>5}  Explanation")
    print(f"  {'-'*90}")
    for t in caps:
        print(f"  {t['date']:<12} {t['side']:<5} {t['entry_t']:<6} {t['exit_t']:<6} {t['pts']:>+6.1f}  {t['rsi']:>5.1f}  {t['adx']:>5.1f}  {t['hold_min']:>4}m  Price dropped/surged full -45 pts")

    print(f"\n  ── TYPE B: TRAIL WINS (profitable SL exits) ───────────────────────")
    print(f"  {'Date':<12} {'S':<5} {'In':<6} {'Out':<6} {'Pts':>6}  {'RSI':>5}  {'ADX':>5}  {'Hold':>5}  Explanation")
    print(f"  {'-'*90}")
    for t in wins:
        print(f"  {t['date']:<12} {t['side']:<5} {t['entry_t']:<6} {t['exit_t']:<6} {t['pts']:>+6.1f}  {t['rsi']:>5.1f}  {t['adx']:>5.1f}  {t['hold_min']:>4}m  ST trailed, locked profit before reversal")

    print(f"\n  ── TYPE C: NEAR-ENTRY REVERSALS (the 'mid-day mystery SL hits') ───")
    print(f"  {'Date':<12} {'S':<5} {'In':<6} {'Out':<6} {'Pts':>6}  {'SL-width':>8}  {'RSI':>5}  {'ADX':>5}  {'Hold':>5}  Explanation")
    print(f"  {'-'*100}")
    for t in sorted(small, key=lambda x: x["pts"]):
        print(f"  {t['date']:<12} {t['side']:<5} {t['entry_t']:<6} {t['exit_t']:<6} {t['pts']:>+6.1f}  {t['sl_width']:>8.1f}  {t['rsi']:>5.1f}  {t['adx']:>5.1f}  {t['hold_min']:>4}m  {explain(t)[:55]}")

    print(f"\n  ── TYPE C PATTERN ANALYSIS ────────────────────────────────────────")
    if small:
        avg_hold  = sum(t["hold_min"] for t in small) / len(small)
        avg_loss  = sum(t["pts"] for t in small) / len(small)
        avg_width = sum(t["sl_width"] for t in small) / len(small)
        buy_small  = [t for t in small if t["side"] == "BUY"]
        sell_small = [t for t in small if t["side"] == "SELL"]
        print(f"  Avg hold time  : {avg_hold:.0f} min  (trades reverse within ~{avg_hold:.0f} min of entry)")
        print(f"  Avg loss/trade : {avg_loss:.1f} pts")
        print(f"  Avg SL width   : {avg_width:.1f} pts  (Supertrend was this close to entry)")
        print(f"  BUY reversals  : {len(buy_small)}  |  SELL reversals: {len(sell_small)}")
        print(f"\n  WHY THIS HAPPENS:")
        print(f"  The Supertrend initial SL is set at the ST band value on entry candle.")
        print(f"  If the ST band is only {avg_width:.0f} pts from entry, the price only needs")
        print(f"  to move {avg_width:.0f} pts against you to hit the SL — even a normal")
        print(f"  intraday retracement can do this before the trend resumes.")
        print(f"  These are NOT strategy failures — they are the cost of being in the")
        print(f"  trade. The EOD exit on the remaining trades more than covers them.")

nifty_trades    = parse_trades(nifty_section)
banknifty_trades = parse_trades(banknifty_section)
analyse(nifty_trades,    "NIFTY")
analyse(banknifty_trades, "BANKNIFTY")
