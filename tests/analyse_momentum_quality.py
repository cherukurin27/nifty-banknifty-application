"""
analyse_momentum_quality.py
Deep analysis of the 1-year backtest trades to find high-momentum vs low-quality filters.
Examines: ADX level, RSI sub-bands, Entry time, ST gap, Hold duration, monthly patterns.
"""
import re
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

HTML_FILE = "nifty-bank-nifty-full-backtest-report.html"

with open(HTML_FILE, "r") as f:
    html = f.read()

# Parse all trades
pattern = (r'<tr><td>([\d-]+)</td><td>(BUY|SELL)</td><td>([\d:]+)</td>'
           r'<td>([\d.]+)</td><td>([\d.]+)</td><td>([\d:]+)</td>'
           r'<td>([\d.]+)</td><td>(SL Hit|EOD Exit)</td>'
           r"<td class='(?:pos|neg)'>([-+][\d.]+)</td>"
           r'<td>([\d.]+)</td><td>([\d.]+)</td></tr>')

matches = re.findall(pattern, html)
print(f"Total trades parsed: {len(matches)}")

nifty_trades = []
bnkn_trades  = []
for m in matches:
    date, side, entry_t, entry, sl, exit_t, exit_p, reason, pts, rsi, adx = m
    rec = {
        "date": date,
        "side": side,
        "entry_t": entry_t,
        "entry": float(entry),
        "sl": float(sl),
        "exit_t": exit_t,
        "exit": float(exit_p),
        "reason": reason,
        "pts": float(pts),
        "rsi": float(rsi),
        "adx": float(adx),
        "win": float(pts) > 0,
    }
    # Determine entry-to-SL gap (proxy for ST band width at entry)
    if rec["side"] == "BUY":
        rec["sl_gap"] = abs(rec["entry"] - rec["sl"])
    else:
        rec["sl_gap"] = abs(rec["sl"] - rec["entry"])

    # Hold duration in minutes (crude: hour*60+min)
    eh, em = map(int, entry_t.split(":"))
    xh, xm = map(int, exit_t.split(":"))
    rec["hold_min"] = (xh * 60 + xm) - (eh * 60 + em)

    if rec["entry"] < 35000:
        rec["sym"] = "NIFTY"
        nifty_trades.append(rec)
    else:
        rec["sym"] = "BNKN"
        bnkn_trades.append(rec)

print(f"NIFTY: {len(nifty_trades)}  BNKN: {len(bnkn_trades)}")


def bucket_analysis(trades, field, width, label):
    buckets = {}
    for t in trades:
        v = t[field]
        b = int(v // width) * width
        if b not in buckets:
            buckets[b] = {"W": 0, "L": 0, "pts": 0.0, "eod_w": 0, "eod_l": 0}
        if t["win"]:
            buckets[b]["W"] += 1
        else:
            buckets[b]["L"] += 1
        buckets[b]["pts"] += t["pts"]
        if t["reason"] == "EOD Exit" and t["win"]:
            buckets[b]["eod_w"] += 1
        elif t["reason"] == "EOD Exit":
            buckets[b]["eod_l"] += 1
    print(f"\n  {label}")
    print(f"  {'Range':12s}  {'Total':>6}  {'WR':>6}  {'Net Pts':>10}  {'Avg':>8}  {'EOD W':>6}  {'EOD L':>6}")
    print(f"  {'-'*72}")
    for b in sorted(buckets):
        d = buckets[b]
        total = d["W"] + d["L"]
        wr = d["W"] / total * 100 if total else 0
        avg = d["pts"] / total if total else 0
        print(f"  {b:4d}–{b+width:4d}     {total:>6}  {wr:>5.0f}%  {d['pts']:>+10.1f}  {avg:>+8.1f}  {d['eod_w']:>6}  {d['eod_l']:>6}")


def time_slot_analysis(trades, label):
    slots = {}
    for t in trades:
        h, m = map(int, t["entry_t"].split(":"))
        slot_m = (h * 60 + m) // 30 * 30
        sh = slot_m // 60
        sm = slot_m % 60
        key = f"{sh:02d}:{sm:02d}"
        if key not in slots:
            slots[key] = {"W": 0, "L": 0, "pts": 0.0, "eod": 0}
        if t["win"]:
            slots[key]["W"] += 1
        else:
            slots[key]["L"] += 1
        slots[key]["pts"] += t["pts"]
        if t["reason"] == "EOD Exit":
            slots[key]["eod"] += 1
    print(f"\n  {label} — Entry Time Slot")
    print(f"  {'Slot':8s}  {'Total':>6}  {'WR':>6}  {'Net Pts':>10}  {'Avg':>8}  {'EOD':>6}")
    print(f"  {'-'*60}")
    for k in sorted(slots):
        d = slots[k]
        total = d["W"] + d["L"]
        wr = d["W"] / total * 100 if total else 0
        avg = d["pts"] / total if total else 0
        print(f"  {k:8s}  {total:>6}  {wr:>5.0f}%  {d['pts']:>+10.1f}  {avg:>+8.1f}  {d['eod']:>6}")


def sl_gap_analysis(trades, label, width):
    buckets = {}
    for t in trades:
        g = t["sl_gap"]
        b = int(g // width) * width
        if b not in buckets:
            buckets[b] = {"W": 0, "L": 0, "pts": 0.0, "eod_w": 0}
        if t["win"]:
            buckets[b]["W"] += 1
        else:
            buckets[b]["L"] += 1
        buckets[b]["pts"] += t["pts"]
        if t["reason"] == "EOD Exit" and t["win"]:
            buckets[b]["eod_w"] += 1
    print(f"\n  {label} — Entry ST Gap (entry to initial SL)")
    print(f"  {'Gap':12s}  {'Total':>6}  {'WR':>6}  {'Net Pts':>10}  {'Avg':>8}  {'EOD W':>6}")
    print(f"  {'-'*65}")
    for b in sorted(buckets):
        d = buckets[b]
        total = d["W"] + d["L"]
        wr = d["W"] / total * 100 if total else 0
        avg = d["pts"] / total if total else 0
        print(f"  {b:4.0f}–{b+width:4.0f} pts  {total:>6}  {wr:>5.0f}%  {d['pts']:>+10.1f}  {avg:>+8.1f}  {d['eod_w']:>6}")


def side_time_analysis(trades, label):
    combos = {}
    for t in trades:
        h, m = map(int, t["entry_t"].split(":"))
        slot_m = (h * 60 + m) // 30 * 30
        sh = slot_m // 60
        sm = slot_m % 60
        key = f"{t['side']}@{sh:02d}:{sm:02d}"
        if key not in combos:
            combos[key] = {"W": 0, "L": 0, "pts": 0.0}
        if t["win"]:
            combos[key]["W"] += 1
        else:
            combos[key]["L"] += 1
        combos[key]["pts"] += t["pts"]
    print(f"\n  {label} — BUY vs SELL by Entry Slot")
    print(f"  {'Combo':14s}  {'Total':>6}  {'WR':>6}  {'Net Pts':>10}  {'Avg':>8}")
    print(f"  {'-'*60}")
    for k in sorted(combos):
        d = combos[k]
        total = d["W"] + d["L"]
        wr = d["W"] / total * 100 if total else 0
        avg = d["pts"] / total if total else 0
        flag = " [HI]" if wr >= 50 else (" [WEAK]" if wr < 20 else "")
        print(f"  {k:14s}  {total:>6}  {wr:>5.0f}%  {d['pts']:>+10.1f}  {avg:>+8.1f}{flag}")


def adx_rsi_cross(trades, label):
    """High ADX + specific RSI zones — momentum quality matrix"""
    hi_adx  = [t for t in trades if t["adx"] >= 28]
    lo_adx  = [t for t in trades if t["adx"] < 28]
    print(f"\n  {label} — ADX Quality Split (threshold = 28)")
    for group, name in [(hi_adx, "ADX >= 28 (strong trend)"), (lo_adx, "ADX 20-28 (weak trend)")]:
        if not group:
            continue
        W = sum(1 for t in group if t["win"])
        L = len(group) - W
        pts = sum(t["pts"] for t in group)
        eod = sum(1 for t in group if t["reason"] == "EOD Exit" and t["win"])
        wr = W / len(group) * 100
        print(f"    {name}: {len(group)} trades  WR={wr:.0f}%  Net={pts:+.1f}  EOD_wins={eod}")


def momentum_score_filter(trades, label):
    """
    Composite momentum score: ADX >= 28 AND RSI in sweet spot AND entry time in good zone.
    Identifies which combination eliminates the most losers while keeping the most winners.
    """
    print(f"\n  {label} — Composite Momentum Filter")
    # For NIFTY BUY: RSI 53-57, ADX >= 25, entry 09:40-11:30 or 12:30-13:00
    # For SELL: RSI 25-50, ADX >= 25
    all_W = sum(1 for t in trades if t["win"])
    all_L = sum(1 for t in trades if not t["win"])
    all_eod = sum(1 for t in trades if t["reason"] == "EOD Exit")

    # High quality: ADX >= 25
    hq25 = [t for t in trades if t["adx"] >= 25]
    hq28 = [t for t in trades if t["adx"] >= 28]
    hq30 = [t for t in trades if t["adx"] >= 30]

    for cutoff, group in [(25, hq25), (28, hq28), (30, hq30)]:
        rejected = [t for t in trades if t["adx"] < cutoff]
        rej_W = sum(1 for t in rejected if t["win"])
        rej_L = sum(1 for t in rejected if not t["win"])
        rej_eod = sum(1 for t in rejected if t["reason"] == "EOD Exit")
        kept_W = sum(1 for t in group if t["win"])
        kept_eod = sum(1 for t in group if t["reason"] == "EOD Exit")
        kept_pts = sum(t["pts"] for t in group)
        print(f"    ADX >= {cutoff}: KEEP {len(group)} trades (WR={kept_W/len(group)*100:.0f}%, Net={kept_pts:+.1f}, EOD={kept_eod})")
        print(f"           REJECT {len(rejected)} trades (would lose {rej_W}W + {rej_L}L, {rej_eod} EOD wins)")


# ─── Run all analyses ────────────────────────────────────────────────────────

print("\n" + "=" * 75)
print("NIFTY ANALYSIS (365d — 220 trades)")
print("=" * 75)
bucket_analysis(nifty_trades, "adx", 5, "ADX Buckets")
bucket_analysis(nifty_trades, "rsi", 3, "RSI Buckets")
time_slot_analysis(nifty_trades, "NIFTY")
sl_gap_analysis(nifty_trades, "NIFTY", 10)
side_time_analysis(nifty_trades, "NIFTY")
adx_rsi_cross(nifty_trades, "NIFTY")
momentum_score_filter(nifty_trades, "NIFTY")

print("\n" + "=" * 75)
print("BANKNIFTY ANALYSIS (365d — 270 trades)")
print("=" * 75)
bucket_analysis(bnkn_trades, "adx", 5, "ADX Buckets")
bucket_analysis(bnkn_trades, "rsi", 5, "RSI Buckets")
time_slot_analysis(bnkn_trades, "BNKN")
sl_gap_analysis(bnkn_trades, "BNKN", 30)
side_time_analysis(bnkn_trades, "BNKN")
adx_rsi_cross(bnkn_trades, "BNKN")
momentum_score_filter(bnkn_trades, "BNKN")

# ─── Combined EOD survivors: what makes a winner ─────────────────────────────
print("\n" + "=" * 75)
print("EOD WINNERS PROFILE (Both symbols)")
print("=" * 75)
all_trades = nifty_trades + bnkn_trades
eod_wins  = [t for t in all_trades if t["reason"] == "EOD Exit" and t["win"]]
eod_loss  = [t for t in all_trades if t["reason"] == "EOD Exit" and not t["win"]]
sl_wins   = [t for t in all_trades if t["reason"] == "SL Hit"   and t["win"]]
sl_loss   = [t for t in all_trades if t["reason"] == "SL Hit"   and not t["win"]]

print(f"\n  EOD Winners ({len(eod_wins)}): avg ADX={sum(t['adx'] for t in eod_wins)/len(eod_wins):.1f}  avg RSI={sum(t['rsi'] for t in eod_wins)/len(eod_wins):.1f}")
print(f"  EOD Losers  ({len(eod_loss)}): avg ADX={sum(t['adx'] for t in eod_loss)/len(eod_loss):.1f}  avg RSI={sum(t['rsi'] for t in eod_loss)/len(eod_loss):.1f}")
print(f"  SL Winners  ({len(sl_wins)}):  avg ADX={sum(t['adx'] for t in sl_wins)/len(sl_wins):.1f}  avg RSI={sum(t['rsi'] for t in sl_wins)/len(sl_wins):.1f}")
print(f"  SL Losers   ({len(sl_loss)}): avg ADX={sum(t['adx'] for t in sl_loss)/len(sl_loss):.1f}  avg RSI={sum(t['rsi'] for t in sl_loss)/len(sl_loss):.1f}")

print(f"\n  EOD Winners: avg hold = {sum(t['hold_min'] for t in eod_wins)/len(eod_wins):.0f} min")
print(f"  SL Losers:   avg hold = {sum(t['hold_min'] for t in sl_loss)/len(sl_loss):.0f} min")

# ADX distribution of EOD winners vs SL losers
print("\n  ADX distribution: EOD Winners vs SL Losers")
print(f"  {'ADX Range':12s}  {'EOD Wins':>10}  {'SL Loss':>10}")
for lo in range(15, 45, 5):
    hi = lo + 5
    ew = sum(1 for t in eod_wins if lo <= t["adx"] < hi)
    sl = sum(1 for t in sl_loss if lo <= t["adx"] < hi)
    print(f"  {lo:4d}–{hi:4d}     {ew:>10}  {sl:>10}")
