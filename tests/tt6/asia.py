# -*- coding: utf-8 -*-
"""台北換日後、亞股開盤前:今日金額要歸零,但漲跌幅照顯示前一場的。"""
import json, os, subprocess, sys, datetime
os.chdir(os.path.dirname(os.path.abspath(__file__)))
n = datetime.datetime.now(datetime.timezone.utc)
print('現在  UTC', n.strftime('%Y-%m-%d %H:%M'), '/ 台北',
      (n + datetime.timedelta(hours=8)).strftime('%Y-%m-%d %H:%M'))

# 場次日期一律相對於「現在」算,不能寫死 —— 寫死的日期過幾天就會被
# 「報價停在 MM/DD」的落後偵測攔下來,測試變成每天結果不同(2026-09-09 實測:
# 早上跑得過、台股收盤後就掛,而程式其實沒問題)。
import zoneinfo
def _last_sessions(tz, close_h, n=2):
    """該市場最近 n 個「已經收盤」的場次日期,新的在後。"""
    lt = datetime.datetime.now(zoneinfo.ZoneInfo(tz))
    d = lt.date()
    if not (lt.weekday() < 5 and (lt.hour + lt.minute / 60) >= close_h):
        d -= datetime.timedelta(days=1)          # 今天這一場還沒收
    out = []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d -= datetime.timedelta(days=1)
    return out[::-1]

_tw = _last_sessions('Asia/Taipei', 13.5)
_hk = _last_sessions('Asia/Hong_Kong', 16.2)
_jp = _last_sessions('Asia/Tokyo', 15.0)
_us = _last_sessions('America/New_York', 16.0)

SPEC = {
  # 台股:資料源給到最近一場收盤
  "2330.TW": {"bars": [[_tw[0], 2405.0], [_tw[1], 2440.0]], "last": 2440.0, "pc": 2405.0},
  # 港股
  "0700.HK": {"bars": [[_hk[0], 600.0], [_hk[1], 610.0]], "last": 610.0, "pc": 600.0},
  # 日股:盤中時 last 高於最後一根收盤 → 應判定為盤中
  "8306.T":  {"bars": [[_jp[0], 3400.0], [_jp[1], 3467.0]], "last": 3500.0, "pc": 3467.0},
  # 美股:不在亞股重置範圍內
  "MU":      {"bars": [[_us[0], 101.0], [_us[1], 103.0]], "last": 103.0, "pc": 101.0},
}
json.dump(SPEC, open('spec.json', 'w'))
pos = [{"name": k, "ticker": k, "kind": "live", "cur": c} for k, c in
       [("2330:TPE", "TWD"), ("0700:HKG", "HKD"), ("8306:TYO", "JPY"), ("MU:NASDAQ", "USD")]]
json.dump({"portfolio": {"regions": [{"name": "r", "groups": [{"name": "g", "positions": pos}]}]},
           "baseline": {}}, open('bundle.json', 'w'), ensure_ascii=False)
subprocess.run([sys.executable, 'fetch_action.py'], capture_output=True, text=True)
Q = json.load(open('quotes3.json'))['quotes']
fails = []
def chk(t, want_pending):
    q = Q.get(t)
    got = bool(q and q.get('pending_open'))
    ok = got == want_pending
    print(('  OK   ' if ok else '  FAIL ') + f"{t:14s} pending={got} 期望={want_pending}  "
          f"chg={q and q.get('change_pct')}  note={q and q.get('note')}")
    if not ok: fails.append(t)
    return q
# 日股的期望值要依當下時鐘算,不能寫死 —— 09:00 JST 開盤前後結果本來就不同
import zoneinfo
_jst = datetime.datetime.now(zoneinfo.ZoneInfo('Asia/Tokyo'))
_jp_open = _jst.weekday() < 5 and (_jst.hour + _jst.minute/60) >= 9.0
_tpe = datetime.datetime.now(zoneinfo.ZoneInfo('Asia/Taipei'))
_tw_open = _tpe.weekday() < 5 and (_tpe.hour + _tpe.minute/60) >= 9.0
print(f'台北現在 {_tpe:%H:%M} → 台股{"已" if _tw_open else "尚未"}開盤')
print(f'東京現在 {_jst:%H:%M} → 日股{"已" if _jp_open else "尚未"}開盤,8306 期望 pending={not _jp_open}')
print()
# 期望值一律從時鐘推,不能寫死:fixture 給的是「資料源到最近一場已收盤的場次」,
# 所以該市場今天還沒收盤時,資料源就沒有今天這一場 —— 不論是還沒開盤(尚未開盤)
# 還是已開盤但資料源沒跟上(尚無新報價),兩者都會標 pending_open,今日金額歸零。
def _closed_today(tz, close_h):
    lt = datetime.datetime.now(zoneinfo.ZoneInfo(tz))
    return lt.weekday() < 5 and (lt.hour + lt.minute / 60) >= close_h

_tw_done, _hk_done = _closed_today('Asia/Taipei', 13.5), _closed_today('Asia/Hong_Kong', 16.2)
print(f'台股今天{"已" if _tw_done else "還沒"}收盤 → 資料源{"有" if _tw_done else "沒有"}今天這一場,'
      f'期望 pending={not _tw_done}')
print(f'港股今天{"已" if _hk_done else "還沒"}收盤 → 期望 pending={not _hk_done}')
print()
q = chk('2330:TPE', not _tw_done)
if q:
    assert q['change_pct'] is not None, '漲跌幅要保留(不論開盤與否)'
    print('       → 漲跌幅保留(%+.2f%%);沒有新場次時今日金額歸零,漲跌幅照顯示' % q['change_pct'])
chk('0700:HKG', not _hk_done)
chk('8306:TYO', not _jp_open)      # 日股:即時價高於最後一根收盤 → 判定為盤中
chk('MU:NASDAQ', False)
print('\n' + ('全部通過' if not fails else '失敗:' + ', '.join(fails)))
sys.exit(1 if fails else 0)
