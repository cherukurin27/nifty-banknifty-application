"""
tests/analyse_winners_vs_losers.py
Deep winner vs loser analysis across ALL available dimensions.
Parses trade data directly from the HTML report (no live fetch required).
Prints a structured console report.

Run:
    python tests/analyse_winners_vs_losers.py
"""
from __future__ import annotations
import sys, os, re, json
sys.stdout.reconfigure(encoding="utf-8", errors="replace") if hasattr(sys.stdout, "reconfigure") else None
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

HTML_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                         "nifty-bank-nifty-full-backtest-report.html")


# ── parse trades out of the HTML ─────────────────────────────────────────────

def _parse_trades(html: str) -> list[dict]:
    """
    Extract all trade rows from the HTML report.
    Each row: Date, Side, EntryT, EntryPx, SL, ExitT, ExitPx, Reason, Pts, RSI, ADX
    Symbol is inferred from which section the row appears in.
    """
    trades = []

    # Split into NIFTY / BANKNIFTY sections
    nifty_section    = re.split(r'id="banknifty"', html, maxsplit=1)[0]
    banknifty_section= re.split(r'id="banknifty"', html, maxsplit=1)[1] if 'id="banknifty"' in html else ""

    # Row pattern: <tr><td>DATE</td>...<td>ADX</td></tr>
    row_pat = re.compile(
        r"<tr><td>([\d-]+)</td>"       # date
        r"<td>(BUY|SELL)</td>"         # direction
        r"<td>(\d+:\d+)</td>"          # entry time
        r"<td>([\d.]+)</td>"           # entry price
        r"<td>([\d.]+)</td>"           # SL
        r"<td>([\d:]+)</td>"           # exit time
        r"<td>([\d.]+)</td>"           # exit price
        r"<td>([^<]+)</td>"            # reason
        r"<td class='(pos|neg)'>([+-][\d.]+)</td>"  # pts
        r"<td>([\d.]+)</td>"           # RSI
        r"<td>([\d.]+)</td>"           # ADX
        r"</tr>"
    )

    for sym, section in [("NIFTY", nifty_section), ("BANKNIFTY", banknifty_section)]:
        for m in row_pat.finditer(section):
            try:
                entry_t  = m.group(3)
                exit_t   = m.group(6)
                entry_h, entry_m = map(int, entry_t.split(":"))
                exit_h,  exit_m  = map(int, exit_t.split(":"))
                hold_min = (exit_h * 60 + exit_m) - (entry_h * 60 + entry_m)
                pts      = float(m.group(10))

                trades.append({
                    "symbol"    : sym,
                    "date"      : m.group(1),
                    "month"     : m.group(1)[:7],
                    "direction" : m.group(2),
                    "entry_time": entry_t,
                    "entry_h"   : entry_h,
                    "entry_min" : entry_h * 60 + entry_m,
                    "exit_time" : exit_t,
                    "exit_min"  : exit_h * 60 + exit_m,
                    "hold_min"  : hold_min,
                    "entry_px"  : float(m.group(4)),
                    "sl"        : float(m.group(5)),
                    "exit_px"   : float(m.group(7)),
                    "reason"    : m.group(8).strip(),
                    "pts"       : pts,
                    "result"    : "WIN" if pts > 0 else "LOSS",
                    "rsi"       : float(m.group(11)),
                    "adx"       : float(m.group(12)),
                })
            except Exception:
                continue
    return trades


# ── stat helpers ──────────────────────────────────────────────────────────────

def avg(lst): return round(sum(lst) / len(lst), 2) if lst else 0.0
def med(lst):
    if not lst: return 0.0
    s = sorted(lst)
    n = len(s)
    return round((s[n//2] if n % 2 else (s[n//2-1]+s[n//2])/2), 2)

def bucket_stat(trades, key, bins, label_fn=None):
    """Group by value bins and show W/L/WR/pts for each bin."""
    from collections import defaultdict
    groups = defaultdict(list)
    for t in trades:
        v = t[key]
        placed = False
        for lo, hi, lbl in bins:
            if lo <= v < hi:
                groups[lbl].append(t)
                placed = True
                break
        if not placed:
            groups["other"].append(t)
    return groups


def print_bucket(title, groups, bins_order):
    hdr = f"{'Bucket':<18} {'Trades':>6} {'Wins':>5} {'WR%':>6} {'Net Pts':>10} {'Avg Win':>8} {'Avg Loss':>9}"
    print(f"\n  {title}")
    print("  " + "-" * len(hdr))
    print("  " + hdr)
    print("  " + "-" * len(hdr))
    for lbl in bins_order:
        ts = groups.get(lbl, [])
        if not ts: continue
        wins   = [t for t in ts if t["result"] == "WIN"]
        losses = [t for t in ts if t["result"] == "LOSS"]
        wr     = round(len(wins) / len(ts) * 100, 1)
        net    = round(sum(t["pts"] for t in ts), 1)
        aw     = avg([t["pts"] for t in wins])
        al     = avg([t["pts"] for t in losses])
        flag   = " ◄ EDGE" if wr >= 60 else (" ✗ DRAIN" if wr < 25 and len(ts) >= 4 else "")
        print(f"  {lbl:<18} {len(ts):>6} {len(wins):>5} {wr:>5.1f}% {net:>10.1f} {aw:>8.1f} {al:>9.1f}{flag}")


# ── main analysis ─────────────────────────────────────────────────────────────

def main():
    with open(HTML_FILE, encoding="utf-8") as f:
        html = f.read()

    trades = _parse_trades(html)
    if not trades:
        print("ERROR: No trades parsed from HTML. Check the file.")
        return

    print("=" * 72)
    print("  WINNER vs LOSER DEEP ANALYSIS — 180d (all trades)")
    print(f"  Total trades parsed: {len(trades)}")
    print("=" * 72)

    for sym in ["NIFTY", "BANKNIFTY"]:
        ts = [t for t in trades if t["symbol"] == sym]
        if not ts: continue
        wins   = [t for t in ts if t["result"] == "WIN"]
        losses = [t for t in ts if t["result"] == "LOSS"]
        print(f"\n{'─'*72}")
        print(f"  {sym}  —  {len(ts)} trades  |  {len(wins)}W / {len(losses)}L  |  WR {round(len(wins)/len(ts)*100,1)}%")
        print(f"  Net pts: {round(sum(t['pts'] for t in ts),1)}")
        print(f"{'─'*72}")

        # ── 1. Indicators at entry ────────────────────────────────────────────
        print(f"\n  [1] INDICATOR PROFILE AT ENTRY")
        rows = [
            ("RSI",     "rsi"),
            ("ADX",     "adx"),
            ("Hold min","hold_min"),
        ]
        hdr2 = f"  {'Metric':<12} {'Win avg':>9} {'Win med':>9} {'Loss avg':>9} {'Loss med':>9} {'Δ avg':>8}"
        print(hdr2)
        print("  " + "-"*56)
        for label, key in rows:
            wa  = avg([t[key] for t in wins])
            wm  = med([t[key] for t in wins])
            la  = avg([t[key] for t in losses])
            lm  = med([t[key] for t in losses])
            d   = round(wa - la, 2)
            flag = "  ◄ DIFF" if abs(d) > 2 else ""
            print(f"  {label:<12} {wa:>9.2f} {wm:>9.2f} {la:>9.2f} {lm:>9.2f} {d:>+8.2f}{flag}")

        # ── 2. Entry time buckets ─────────────────────────────────────────────
        time_bins = [
            (0,    570,  "09:30–09:30"),
            (570,  600,  "09:30–10:00"),
            (600,  630,  "10:00–10:30"),
            (630,  660,  "10:30–11:00"),
            (660,  690,  "11:00–11:30"),
            (690,  720,  "11:30–12:00"),
            (720,  750,  "12:00–12:30"),
            (750,  780,  "12:30–13:00"),
            (780,  810,  "13:00–13:30"),
            (810,  870,  "13:30+"),
        ]
        groups = bucket_stat(ts, "entry_min", time_bins)
        print_bucket("[2] ENTRY TIME BUCKETS", groups,
                     [b[2] for b in time_bins])

        # ── 3. RSI buckets ────────────────────────────────────────────────────
        if sym == "NIFTY":
            rsi_bins_buy  = [(53,55,"RSI 53-55"),(55,57,"RSI 55-57"),(57,59,"RSI 57-59"),(59,62,"RSI 59-62")]
            rsi_bins_sell = [(25,30,"RSI 25-30"),(30,34,"RSI 30-34"),(34,38,"RSI 34-38"),(38,44,"RSI 38-44")]
        else:
            rsi_bins_buy  = [(57,59,"RSI 57-59"),(59,61,"RSI 59-61"),(61,64,"RSI 61-64")]
            rsi_bins_sell = [(25,30,"RSI 25-30"),(30,34,"RSI 30-34"),(34,38,"RSI 34-38"),(38,44,"RSI 38-44")]

        buys  = [t for t in ts if t["direction"] == "BUY"]
        sells = [t for t in ts if t["direction"] == "SELL"]

        if buys:
            groups_b = bucket_stat(buys, "rsi", rsi_bins_buy)
            print_bucket(f"[3a] RSI BUCKETS — BUY (n={len(buys)})", groups_b,
                         [b[2] for b in rsi_bins_buy])
        if sells:
            groups_s = bucket_stat(sells, "rsi", rsi_bins_sell)
            print_bucket(f"[3b] RSI BUCKETS — SELL (n={len(sells)})", groups_s,
                         [b[2] for b in rsi_bins_sell])

        # ── 4. ADX buckets ────────────────────────────────────────────────────
        adx_bins = [
            (20,23,"ADX 20-23"),(23,26,"ADX 23-26"),(26,29,"ADX 26-29"),
            (29,33,"ADX 29-33"),(33,38,"ADX 33-38"),(38,50,"ADX 38+"),
        ]
        groups_adx = bucket_stat(ts, "adx", adx_bins)
        print_bucket("[4] ADX BUCKETS", groups_adx, [b[2] for b in adx_bins])

        # ── 5. Hold duration buckets ──────────────────────────────────────────
        hold_bins = [
            (0,  15,  "0-15 min"),
            (15, 30,  "15-30 min"),
            (30, 60,  "30-60 min"),
            (60, 120, "1-2 hrs"),
            (120,180, "2-3 hrs"),
            (180,240, "3-4 hrs"),
            (240,360, "4-6 hrs (EOD)"),
        ]
        groups_h = bucket_stat(ts, "hold_min", hold_bins)
        print_bucket("[5] HOLD DURATION BUCKETS", groups_h,
                     [b[2] for b in hold_bins])

        # ── 6. Direction × time matrix ────────────────────────────────────────
        print(f"\n  [6] DIRECTION × ENTRY TIME (all 180d)")
        slot_names = ["09:40","10:00","10:30","11:00","11:30","12:00","12:30","13:00"]
        slot_mins  = [580,600,630,660,690,720,750,780]
        hdr3 = f"  {'Slot':<10} {'B-W':>5}{'B-L':>5}{'B-WR':>7}  {'S-W':>5}{'S-L':>5}{'S-WR':>7}  {'Net':>8}"
        print(hdr3)
        print("  " + "-"*60)
        for i, (slot, smin) in enumerate(zip(slot_names, slot_mins)):
            emin = slot_mins[i+1] if i+1 < len(slot_mins) else 840
            slot_trades = [t for t in ts if smin <= t["entry_min"] < emin]
            bw = sum(1 for t in slot_trades if t["direction"]=="BUY"  and t["result"]=="WIN")
            bl = sum(1 for t in slot_trades if t["direction"]=="BUY"  and t["result"]=="LOSS")
            sw = sum(1 for t in slot_trades if t["direction"]=="SELL" and t["result"]=="WIN")
            sl = sum(1 for t in slot_trades if t["direction"]=="SELL" and t["result"]=="LOSS")
            bwr = f"{round(bw/(bw+bl)*100):>5.0f}%" if bw+bl else "   -  "
            swr = f"{round(sw/(sw+sl)*100):>5.0f}%" if sw+sl else "   -  "
            net = round(sum(t["pts"] for t in slot_trades), 1)
            print(f"  {slot:<10} {bw:>5}{bl:>5}{bwr:>7}  {sw:>5}{sl:>5}{swr:>7}  {net:>8.1f}")

        # ── 7. Exit reason profile ────────────────────────────────────────────
        print(f"\n  [7] EXIT REASON PROFILE")
        for reason in ["SL Hit", "EOD Exit", "Reverse Signal"]:
            rts = [t for t in ts if t["reason"] == reason]
            if not rts: continue
            rw  = [t for t in rts if t["result"] == "WIN"]
            rl  = [t for t in rts if t["result"] == "LOSS"]
            rwr = round(len(rw)/len(rts)*100, 1)
            rnet= round(sum(t["pts"] for t in rts), 1)
            raw = avg([t["pts"] for t in rw])
            ral = avg([t["pts"] for t in rl])
            print(f"  {reason:<18} n={len(rts):>3}  WR={rwr:>5.1f}%  Net={rnet:>8.1f}  AvgW={raw:>7.1f}  AvgL={ral:>7.1f}")

        # ── 8. Month × direction ──────────────────────────────────────────────
        print(f"\n  [8] MONTHLY × DIRECTION")
        months = sorted(set(t["month"] for t in ts))
        hdr4 = f"  {'Month':<9} {'B-W':>5}{'B-L':>5}{'B-WR':>7}  {'S-W':>5}{'S-L':>5}{'S-WR':>7}  {'Net':>8}"
        print(hdr4)
        print("  " + "-"*60)
        for mo in months:
            mts = [t for t in ts if t["month"] == mo]
            bw = sum(1 for t in mts if t["direction"]=="BUY"  and t["result"]=="WIN")
            bl = sum(1 for t in mts if t["direction"]=="BUY"  and t["result"]=="LOSS")
            sw = sum(1 for t in mts if t["direction"]=="SELL" and t["result"]=="WIN")
            sl = sum(1 for t in mts if t["direction"]=="SELL" and t["result"]=="LOSS")
            bwr = f"{round(bw/(bw+bl)*100):>5.0f}%" if bw+bl else "    - "
            swr = f"{round(sw/(sw+sl)*100):>5.0f}%" if sw+sl else "    - "
            net = round(sum(t["pts"] for t in mts), 1)
            print(f"  {mo:<9} {bw:>5}{bl:>5}{bwr:>7}  {sw:>5}{sl:>5}{swr:>7}  {net:>8.1f}")

        # ── 9. Key differentiators ────────────────────────────────────────────
        print(f"\n  [9] TOP WINNER ENTRY PROFILE (what winners look like at entry)")
        if wins:
            w_eod  = [t for t in wins if t["reason"] == "EOD Exit"]
            w_sl   = [t for t in wins if t["reason"] == "SL Hit"]
            print(f"  EOD wins:        {len(w_eod):>3}  ({round(len(w_eod)/len(wins)*100,1)}% of all wins)  avg pts: {avg([t['pts'] for t in w_eod]):>7.1f}")
            print(f"  SL-hit wins:     {len(w_sl):>3}  ({round(len(w_sl)/len(wins)*100,1)}% of all wins)  avg pts: {avg([t['pts'] for t in w_sl]):>7.1f}")
            print(f"  Win avg hold:    {avg([t['hold_min'] for t in wins]):>6.1f} min")
            print(f"  Loss avg hold:   {avg([t['hold_min'] for t in losses]):>6.1f} min")
            print(f"  Win avg RSI:     {avg([t['rsi'] for t in wins]):>6.2f}")
            print(f"  Loss avg RSI:    {avg([t['rsi'] for t in losses]):>6.2f}")
            print(f"  Win avg ADX:     {avg([t['adx'] for t in wins]):>6.2f}")
            print(f"  Loss avg ADX:    {avg([t['adx'] for t in losses]):>6.2f}")

            # Entry time distribution of winners
            early_w = sum(1 for t in wins if t["entry_min"] < 720)    # before 12:00
            late_w  = sum(1 for t in wins if t["entry_min"] >= 720)   # 12:00+
            early_l = sum(1 for t in losses if t["entry_min"] < 720)
            late_l  = sum(1 for t in losses if t["entry_min"] >= 720)
            print(f"  Entry <12:00:    winners={early_w}, losers={early_l}  (WR {round(early_w/(early_w+early_l)*100,1) if early_w+early_l else 0}%)")
            print(f"  Entry >=12:00:   winners={late_w},  losers={late_l}  (WR {round(late_w/(late_w+late_l)*100,1) if late_w+late_l else 0}%)")

        # ── 10. The "trap" trades: entered late, lost fast ────────────────────
        print(f"\n  [10] FAST LOSERS (hold < 30 min, SL hit) — the 'fake signal' trades")
        fast_loss = [t for t in losses if t["hold_min"] < 30 and t["reason"] == "SL Hit"]
        if fast_loss:
            avg_hold = avg([t["hold_min"] for t in fast_loss])
            avg_rsi  = avg([t["rsi"] for t in fast_loss])
            avg_adx  = avg([t["adx"] for t in fast_loss])
            net_pts  = round(sum(t["pts"] for t in fast_loss), 1)
            buy_cnt  = sum(1 for t in fast_loss if t["direction"] == "BUY")
            sell_cnt = sum(1 for t in fast_loss if t["direction"] == "SELL")
            print(f"  Count: {len(fast_loss)}  |  Net pts: {net_pts}  |  Avg hold: {avg_hold:.1f} min")
            print(f"  BUY: {buy_cnt}  SELL: {sell_cnt}  |  Avg RSI: {avg_rsi:.2f}  Avg ADX: {avg_adx:.2f}")
            # entry time distribution
            et_dist = {}
            for t in fast_loss:
                slot = f"{t['entry_h']:02d}:{'00' if t['entry_min']%60<30 else '30'}"
                et_dist[slot] = et_dist.get(slot, 0) + 1
            print(f"  Entry slots: {dict(sorted(et_dist.items()))}")

    print(f"\n{'='*72}")
    print("  CROSS-SYMBOL SUMMARY")
    print(f"{'='*72}")

    all_wins   = [t for t in trades if t["result"] == "WIN"]
    all_losses = [t for t in trades if t["result"] == "LOSS"]
    eod_wins   = [t for t in all_wins if t["reason"] == "EOD Exit"]
    sl_losses  = [t for t in all_losses if t["reason"] == "SL Hit"]
    fast_l     = [t for t in sl_losses if t["hold_min"] < 30]

    print(f"  Total trades:        {len(trades)} ({len(all_wins)}W / {len(all_losses)}L)")
    print(f"  EOD wins:            {len(eod_wins)} = {round(len(eod_wins)/len(all_wins)*100,1)}% of all wins, {round(len(eod_wins)/len(trades)*100,1)}% of all trades")
    print(f"  EOD win pts share:   {round(sum(t['pts'] for t in eod_wins),1)} pts")
    print(f"  Fast SL losses(<30m):{len(fast_l)} = {round(len(fast_l)/len(all_losses)*100,1)}% of all losses")
    print(f"  Fast SL drain:       {round(sum(t['pts'] for t in fast_l),1)} pts")
    print(f"  Win avg hold:        {avg([t['hold_min'] for t in all_wins]):.1f} min")
    print(f"  Loss avg hold:       {avg([t['hold_min'] for t in all_losses]):.1f} min")

    print()


if __name__ == "__main__":
    main()
