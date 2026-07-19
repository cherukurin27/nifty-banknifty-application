"""Print full backtest results from JSON for report generation."""
import json, sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

with open("logs/backtest_report_data.json") as f:
    d = json.load(f)

print("=== ALL SYMBOLS ===")
print(list(d["period_stats"].keys()))
print()

for sym in d["period_stats"]:
    s = d["period_stats"][sym].get("90", {})
    print(f"{sym:12s}  trades={s.get('total_trades',0):3d}  "
          f"WR={s.get('win_rate_pct',0):5.1f}%  pts={s.get('total_points',0):+9.1f}  "
          f"DD={s.get('max_drawdown',0):+8.1f}  RR={s.get('risk_reward',0):.2f}  "
          f"MCL={s.get('max_consec_loss',0)}  "
          f"avgW={s.get('avg_win_pts',0):+7.1f}  avgL={s.get('avg_loss_pts',0):+7.1f}")

print()
for sym in d["period_stats"]:
    print(f"=== {sym} all periods ===")
    for p, s in d["period_stats"][sym].items():
        print(f"  {p:3s}d  trades={s.get('total_trades',0):3d}  "
              f"WR={s.get('win_rate_pct',0):5.1f}%  pts={s.get('total_points',0):+9.1f}  "
              f"DD={s.get('max_drawdown',0):+8.1f}  RR={s.get('risk_reward',0):.2f}  "
              f"MCL={s.get('max_consec_loss',0)}")

print()
print("=== BY DIRECTION (90d) ===")
for sym, dirs in d.get("by_direction", {}).items():
    for dr, v in dirs.items():
        print(f"  {sym:12s}  {dr:4s}  trades={v['trades']:3d}  "
              f"WR={v['win_rate_pct']:5.1f}%  pts={v['total_points']:+9.1f}  "
              f"avgW={v['avg_win_pts']:+7.1f}  avgL={v['avg_loss_pts']:+7.1f}")

print()
print("=== MONTHLY (90d) ===")
for sym, months in d.get("monthly", {}).items():
    print(f"  {sym}:")
    for m, v in sorted(months.items()):
        print(f"    {m}  trades={v['trades']:3d}  WR={v['wr']:5.1f}%  pts={v['points']:+9.1f}")
