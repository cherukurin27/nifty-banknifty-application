"""
tests/analyse_momentum_score.py
================================================
Step 1: Compare ALL winners vs ALL losers across every indicator.
Step 2: Build a Momentum Score (0–100) from statistically significant separators.
Step 3: Test Score >= 60 / 70 / 80 / 90 thresholds on 365-day trade log.
Step 4: Pick the threshold that maximises Profit Factor, Expectancy, MDD, yearly stability.

Run:  python tests/analyse_momentum_score.py
"""
import re, sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

HTML_FILE = "nifty-bank-nifty-full-backtest-report.html"

# ─── Parse trades from HTML report ───────────────────────────────────────────

with open(HTML_FILE, "r") as f:
    html = f.read()

pattern = (r"<tr><td>([\d-]+)</td><td>(BUY|SELL)</td><td>([\d:]+)</td>"
           r"<td>([\d.]+)</td><td>([\d.]+)</td><td>([\d:]+)</td>"
           r"<td>([\d.]+)</td><td>(SL Hit|EOD Exit)</td>"
           r"<td class='(?:pos|neg)'>([-+][\d.]+)</td>"
           r"<td>([\d.]+)</td><td>([\d.]+)</td></tr>")

matches = re.findall(pattern, html)
print(f"Trades parsed: {len(matches)}")

trades = []
for m in matches:
    date, side, entry_t, entry, sl, exit_t, exit_p, reason, pts, rsi, adx = m
    entry_f = float(entry)
    sl_f    = float(sl)
    sym     = "NIFTY" if entry_f < 35000 else "BNKN"

    # ST gap = distance from entry to SL at moment of entry
    # (SL is set to st_value capped by hard SL; gap proxies the ST band width)
    st_gap = abs(entry_f - sl_f)

    eh, em = map(int, entry_t.split(":"))
    xh, xm = map(int, exit_t.split(":"))
    hold_min = (xh * 60 + xm) - (eh * 60 + em)
    entry_min = eh * 60 + em   # minutes since midnight

    trades.append({
        "date"     : date,
        "sym"      : sym,
        "side"     : side,
        "entry_t"  : entry_t,
        "entry_m"  : entry_min,
        "entry"    : entry_f,
        "sl"       : sl_f,
        "exit_t"   : exit_t,
        "exit"     : float(exit_p),
        "reason"   : reason,
        "pts"      : float(pts),
        "rsi"      : float(rsi),
        "adx"      : float(adx),
        "st_gap"   : st_gap,
        "hold_min" : hold_min,
        "win"      : float(pts) > 0,
    })

winners = [t for t in trades if t["win"]]
losers  = [t for t in trades if not t["win"]]
print(f"Winners: {len(winners)}  Losers: {len(losers)}")


# ─── Helper ───────────────────────────────────────────────────────────────────

def avg(lst, key):
    vals = [t[key] for t in lst if not (isinstance(t[key], float) and t[key] != t[key])]
    return sum(vals) / len(vals) if vals else 0.0


def pct_below(lst, key, threshold):
    return sum(1 for t in lst if t[key] < threshold) / len(lst) * 100 if lst else 0


def pct_above(lst, key, threshold):
    return sum(1 for t in lst if t[key] >= threshold) / len(lst) * 100 if lst else 0


def profit_factor(tlist):
    gross_win  = sum(t["pts"] for t in tlist if t["pts"] > 0)
    gross_loss = abs(sum(t["pts"] for t in tlist if t["pts"] < 0))
    return round(gross_win / gross_loss, 3) if gross_loss else float("inf")


def expectancy(tlist):
    if not tlist: return 0.0
    wr = sum(1 for t in tlist if t["win"]) / len(tlist)
    aw = avg([t for t in tlist if t["win"]],  "pts") if any(t["win"] for t in tlist) else 0
    al = avg([t for t in tlist if not t["win"]], "pts") if any(not t["win"] for t in tlist) else 0
    return round(wr * aw + (1 - wr) * al, 2)


def max_drawdown(tlist):
    if not tlist: return 0.0
    pts_list = [t["pts"] for t in tlist]
    peak = cur = 0.0
    mdd  = 0.0
    for p in pts_list:
        cur  += p
        peak  = max(peak, cur)
        mdd   = min(mdd, cur - peak)
    return round(mdd, 2)


def max_consec_loss(tlist):
    streak = best = 0
    for t in tlist:
        streak = streak + 1 if not t["win"] else 0
        best   = max(best, streak)
    return best


def yearly_summary(tlist):
    by_year = {}
    for t in tlist:
        y = t["date"][:4]
        if y not in by_year:
            by_year[y] = []
        by_year[y].append(t)
    return {y: {"trades": len(v),
                "WR": f"{sum(1 for t in v if t['win'])/len(v)*100:.0f}%",
                "net": sum(t['pts'] for t in v),
                "PF": profit_factor(v)}
            for y, v in sorted(by_year.items())}


# ─── Step 1: Winner vs Loser Profile ─────────────────────────────────────────

print("\n" + "=" * 70)
print("STEP 1 — WINNER vs LOSER INDICATOR PROFILE (all 490 trades)")
print("=" * 70)

metrics = [
    ("ADX",    "adx",    "higher is more trendy"),
    ("RSI",    "rsi",    "range matters by direction"),
    ("ST Gap", "st_gap", "gap from entry to SL"),
    ("Hold",   "hold_min","minutes held"),
]

print(f"\n  {'Metric':12s}  {'Winners avg':>12}  {'Losers avg':>12}  {'Separation':>14}")
print(f"  {'-'*56}")
for label, key, note in metrics:
    wa = avg(winners, key)
    la = avg(losers, key)
    sep = wa - la
    print(f"  {label:12s}  {wa:>12.1f}  {la:>12.1f}  {sep:>+14.1f}  ({note})")

# ST Gap separation is the critical one — show distribution
print(f"\n  ST Gap distribution (entry-to-SL distance):")
print(f"  {'Bucket':12s}  {'Total':>6}  {'WR':>6}  {'Net':>10}  {'Avg':>8}")
print(f"  {'-'*50}")

# Per-symbol gap buckets (gaps differ by symbol scale)
for sym, sym_trades, bucket_w in [("NIFTY", [t for t in trades if t["sym"]=="NIFTY"], 10),
                                   ("BNKN",  [t for t in trades if t["sym"]=="BNKN"],  30)]:
    print(f"\n  --- {sym} ---")
    buckets = {}
    for t in sym_trades:
        b = int(t["st_gap"] // bucket_w) * bucket_w
        if b not in buckets:
            buckets[b] = {"W": 0, "L": 0, "pts": 0.0}
        if t["win"]:
            buckets[b]["W"] += 1
        else:
            buckets[b]["L"] += 1
        buckets[b]["pts"] += t["pts"]
    for b in sorted(buckets):
        d = buckets[b]
        tot = d["W"] + d["L"]
        wr  = d["W"] / tot * 100 if tot else 0
        avg_pts = d["pts"] / tot if tot else 0
        print(f"  {b:4.0f}-{b+bucket_w:4.0f}  {tot:>6}  {wr:>5.0f}%  {d['pts']:>+10.1f}  {avg_pts:>+8.1f}")


# ─── Step 2: Momentum Score Design ───────────────────────────────────────────

print("\n" + "=" * 70)
print("STEP 2 - MOMENTUM SCORE DESIGN (0-100)")
print("=" * 70)

print(
    "  Score components (chosen from winner/loser statistical separation):\n"
    "\n"
    "  4 components, max 100 pts total:\n"
    "  ST Gap  (40 pts): gap<10->40, gap<20->35, gap<30->25, gap<40->15, gap>=40->0\n"
    "  RSI     (30 pts): BUY RSI 53-57->30; SELL RSI 27-42->30, 42-55->12\n"
    "  ADX     (20 pts): ADX 20-25->20, 35-40->15, 30-35->10, 25-30->5\n"
    "  Time    (10 pts): NIFTY SELL 11:00-13:00->10; BNKN 12:00-13:00->10\n"
)

# ─── Score function ───────────────────────────────────────────────────────────

def momentum_score(t: dict) -> int:
    score = 0
    sym   = t["sym"]
    gap   = t["st_gap"]
    rsi_v = t["rsi"]
    adx_v = t["adx"]
    em    = t["entry_m"]   # entry minute since midnight
    side  = t["side"]

    # ── ST Gap (40 pts) ──────────────────────────────────────────────────────
    if sym == "NIFTY":
        if   gap < 10:  score += 40
        elif gap < 20:  score += 35
        elif gap < 30:  score += 25
        elif gap < 40:  score += 15
        else:           score += 0   # >= 40: kill zone
    else:  # BNKN
        if   gap < 30:  score += 40
        elif gap < 60:  score += 35
        elif gap < 90:  score += 20
        elif gap < 120: score += 5
        else:           score += 0   # >= 120: kill zone

    # ── RSI Zone (30 pts) ────────────────────────────────────────────────────
    if side == "BUY":
        # NIFTY BUY RSI 53–57, BNKN BUY RSI 55–60
        if sym == "NIFTY":
            if 53 <= rsi_v <= 57:  score += 30
            elif 57 < rsi_v <= 60: score += 15
            else:                  score += 5
        else:
            if 55 <= rsi_v <= 60:  score += 30
            elif 53 <= rsi_v < 55: score += 20
            else:                  score += 5
    else:  # SELL
        # SELL RSI 25–42 is the strong zone; 42–55 is weak
        if   rsi_v <= 30:  score += 30
        elif rsi_v <= 36:  score += 28
        elif rsi_v <= 42:  score += 22
        elif rsi_v <= 48:  score += 8   # dead zone: 13–21% WR
        else:              score += 12  # RSI 48–55 SELL — marginal

    # ── ADX Zone (20 pts) ────────────────────────────────────────────────────
    if   20 <= adx_v < 25:  score += 20   # best net bucket
    elif 35 <= adx_v <= 40: score += 15
    elif 30 <= adx_v < 35:  score += 10
    elif 25 <= adx_v < 30:  score += 5    # worst bucket (-914 NIFTY)
    else:                   score += 0

    # ── Time Zone (10 pts) ───────────────────────────────────────────────────
    # 570 = 09:30, 600 = 10:00, 690 = 11:30, 720 = 12:00, 750 = 12:30, 780 = 13:00
    if sym == "NIFTY":
        if 660 <= em < 780:   score += 10  # 11:00–13:00 SELL slot performs best
        elif 600 <= em < 660: score += 5   # 10:00–11:00
        elif em < 600:        score += 2   # pre-10:00 opening noise
    else:  # BNKN
        if 720 <= em < 780:   score += 10  # 12:00–13:00 gold zone (BUY 50%, SELL 44%)
        elif 690 <= em < 720: score += 7   # 11:30–12:00 marginal positive
        elif em < 600:        score += 2   # opening

    return min(score, 100)


# Attach score to every trade
for t in trades:
    t["score"] = momentum_score(t)

print(f"\n  Score distribution across all 490 trades:")
print(f"  {'Score':8s}  {'Total':>6}  {'WR':>6}  {'Net':>10}  {'PF':>6}  {'Exp':>8}")
print(f"  {'-'*55}")
for lo in range(0, 101, 10):
    hi = lo + 10
    bucket = [t for t in trades if lo <= t["score"] < hi]
    if not bucket:
        continue
    wr  = sum(1 for t in bucket if t["win"]) / len(bucket) * 100
    net = sum(t["pts"] for t in bucket)
    pf  = profit_factor(bucket)
    exp = expectancy(bucket)
    print(f"  {lo:3d}-{hi:3d}   {len(bucket):>6}  {wr:>5.0f}%  {net:>+10.1f}  {pf:>6.2f}  {exp:>+8.1f}")


# ─── Step 3: Threshold Testing ────────────────────────────────────────────────

print("\n" + "=" * 70)
print("STEP 3 — THRESHOLD COMPARISON (Score >= 60 / 70 / 80 / 90)")
print("=" * 70)

# Baseline: all trades
baseline = trades
print(f"\n  BASELINE (all {len(baseline)} trades)")
print(f"    WR={sum(1 for t in baseline if t['win'])/len(baseline)*100:.1f}%  "
      f"Net={sum(t['pts'] for t in baseline):+.1f}  "
      f"PF={profit_factor(baseline):.3f}  "
      f"Exp={expectancy(baseline):+.1f}  "
      f"MDD={max_drawdown(baseline):.1f}  "
      f"MCL={max_consec_loss(baseline)}")
ys = yearly_summary(baseline)
for y, d in ys.items():
    print(f"      {y}: {d['trades']}t  WR={d['WR']}  Net={d['net']:+.1f}  PF={d['PF']:.2f}")

best_threshold = None
best_score_val = -999999

for thresh in [60, 70, 80, 90]:
    kept     = [t for t in trades if t["score"] >= thresh]
    rejected = [t for t in trades if t["score"] <  thresh]
    if not kept:
        continue

    wr   = sum(1 for t in kept if t["win"]) / len(kept) * 100
    net  = sum(t["pts"] for t in kept)
    pf   = profit_factor(kept)
    exp  = expectancy(kept)
    mdd  = max_drawdown(kept)
    mcl  = max_consec_loss(kept)

    rej_wins = sum(1 for t in rejected if t["win"])
    rej_eod  = sum(1 for t in rejected if t["reason"] == "EOD Exit" and t["win"])
    rej_net  = sum(t["pts"] for t in rejected)

    print(f"\n  THRESHOLD >= {thresh} — keeping {len(kept)} trades, rejecting {len(rejected)}")
    print(f"    WR={wr:.1f}%  Net={net:+.1f}  PF={pf:.3f}  Exp={exp:+.1f}  MDD={mdd:.1f}  MCL={mcl}")
    print(f"    Rejected: {len(rejected)} trades  ({rej_wins} winners lost, {rej_eod} EOD wins lost, net of rejected={rej_net:+.1f})")

    ys = yearly_summary(kept)
    for y, d in ys.items():
        print(f"      {y}: {d['trades']}t  WR={d['WR']}  Net={d['net']:+.1f}  PF={d['PF']:.2f}")

    # Composite score: PF * net - abs(mdd)*0.1 + exp*10
    composite = pf * net - abs(mdd) * 0.1 + exp * 10
    if composite > best_score_val:
        best_score_val = composite
        best_threshold = thresh

print(f"\n  WINNER: Score >= {best_threshold} has the best composite (PF x Net - 0.1xMDD + 10xExp)")


# ─── Step 4: Deep-dive on the winning threshold ───────────────────────────────

print("\n" + "=" * 70)
print(f"STEP 4 — DEEP DIVE: Score >= {best_threshold}")
print("=" * 70)

kept = [t for t in trades if t["score"] >= best_threshold]
n_kept = [t for t in kept if t["sym"] == "NIFTY"]
b_kept = [t for t in kept if t["sym"] == "BNKN"]

for sym_name, sym_trades in [("NIFTY", n_kept), ("BNKN", b_kept)]:
    if not sym_trades:
        continue
    wins  = [t for t in sym_trades if t["win"]]
    loss  = [t for t in sym_trades if not t["win"]]
    eod_w = [t for t in sym_trades if t["reason"] == "EOD Exit" and t["win"]]
    net   = sum(t["pts"] for t in sym_trades)
    wr    = len(wins) / len(sym_trades) * 100
    pf    = profit_factor(sym_trades)
    mdd   = max_drawdown(sym_trades)
    mcl   = max_consec_loss(sym_trades)

    print(f"\n  {sym_name}: {len(sym_trades)} trades  WR={wr:.1f}%  Net={net:+.1f}  "
          f"PF={pf:.3f}  MDD={mdd:.1f}  MCL={mcl}")
    print(f"    EOD wins: {len(eod_w)}  SL hits: {len(loss)+len(wins)-len(eod_w)}")
    if wins:
        print(f"    Avg win: {avg(wins,'pts'):+.1f} pts   Avg loss: {avg(loss,'pts'):+.1f} pts")
    print(f"    Avg score of kept trades: {avg(sym_trades,'score'):.1f}")

    # Monthly breakdown
    print(f"\n    Monthly (Score >= {best_threshold}):")
    months = {}
    for t in sym_trades:
        mo = t["date"][:7]
        if mo not in months:
            months[mo] = []
        months[mo].append(t)
    for mo, mt in sorted(months.items()):
        mw = sum(1 for t in mt if t["win"])
        mnet = sum(t["pts"] for t in mt)
        mwr  = mw / len(mt) * 100
        flag = " [+]" if mnet > 0 else " [-]"
        print(f"      {mo}: {len(mt):3d}t  WR={mwr:.0f}%  Net={mnet:+.1f}{flag}")

# ─── Step 5: What score components to add to config ──────────────────────────

print("\n" + "=" * 70)
print("STEP 5 — CONFIG PARAMETERS DERIVED FROM SCORE ANALYSIS")
print("=" * 70)
kept_all = [t for t in trades if t["score"] >= best_threshold]
rej_all  = [t for t in trades if t["score"] <  best_threshold]

rej_wins   = sum(1 for t in rej_all if t["win"])
rej_eod    = sum(1 for t in rej_all if t["reason"] == "EOD Exit" and t["win"])
rej_net    = sum(t["pts"] for t in rej_all)
print(f"  Recommended config: ST_GAP_MAX = {{NIFTY:40, BANKNIFTY:90}}")
print(f"  Momentum score minimum: MOMENTUM_SCORE_MIN = {best_threshold}")
print(f"  Trades kept : {len(kept_all)}")
print(f"  Trades reject: {len(rej_all)}  ({rej_wins} winners lost, {rej_eod} EOD wins lost)")
print(f"  Net of rejected: {rej_net:+.1f} pts  (negative = rejecting them helps P&L)")
