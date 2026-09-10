# -*- coding: utf-8 -*-
"""結算窗口:四輪排程任一輪跑到就要記得到;全部沒跑到要留下紅字。"""
import sys, os, datetime, re
os.chdir(os.path.dirname(os.path.abspath(__file__)))
src = open('append_history.py', encoding='utf-8').read()

fails = []
def slot_of(stamp):
    t = datetime.datetime.strptime(stamp, "%Y-%m-%d %H:%M")
    return (18 <= t.hour <= 20 and t.weekday() < 5)

# 四輪排程(UTC cron → 台北)
CRONS = ['18:00', '19:00', '20:00', '20:40']
print('[1] 四輪排程都必須落在結算窗口內')
for c in CRONS:
    ok = slot_of(f'2026-09-09 {c}')
    print(('  ok   ' if ok else '  FAIL ') + f'台北 {c} → 窗口內={ok}')
    if not ok: fails.append(c)

print('\n[2] 每一輪各自延遲多久還來得及(以前只有 18:00 那一輪,容忍 3 小時)')
for c in CRONS:
    h, m = map(int, c.split(':'))
    base = datetime.datetime(2026, 9, 9, h, m)
    d = 0
    while slot_of((base + datetime.timedelta(minutes=d + 1)).strftime('%Y-%m-%d %H:%M')):
        d += 1
    print(f'  台北 {c} 可延遲 {d // 60} 小時 {d % 60} 分')
# 只要有一輪跑到就行 → 整體要全部失手才會漏。最後一輪的容忍度是下限。
print('  → 四輪全部落空才會漏掉一天(以前是 18:00 那一輪落空就漏)')

print('\n[3] 美股開盤前:窗口最晚 20:59,美股最早 21:30 開盤 → 日終切片仍乾淨')
ok = not slot_of('2026-09-09 21:30')
print(('  ok   ' if ok else '  FAIL ') + f'台北 21:30 在窗口外={ok}')
if not ok: fails.append('21:30')

print('\n[4] 週末不結算')
for d, name in ((12, '週六'), (13, '週日')):
    ok = not slot_of(f'2026-09-{d} 18:00')
    print(('  ok   ' if ok else '  FAIL ') + f'{name} 18:00 不結算={ok}')
    if not ok: fails.append(name)

print('\n[5] 窗口過了還沒記到 → 要印 ::error::(但不能讓建置失敗)')
blk = re.search(r'appended = False\n(.*?)\nif appended:', src, re.S).group(0)
for stamp, have, want in (('2026-09-09 21:30', False, True),   # 平日、過了窗口、沒記到
                          ('2026-09-09 21:30', True,  False),  # 已經記到了
                          ('2026-09-09 19:00', False, False),  # 還在窗口內,別亂叫
                          ('2026-09-09 04:00', False, False),  # 凌晨那輪,今天還沒到期
                          ('2026-09-12 21:30', False, False)): # 週六
    t = datetime.datetime.strptime(stamp, "%Y-%m-%d %H:%M")
    day = t.strftime('%Y-%m-%d')
    ns = {'SLOT': slot_of(stamp), '_t': t, 'DAY': day, 'MAX_ROWS': 500,
          'lines': ['hdr'] + ([day + ' 18:01,1'] if have else []), 'row': ['x']}
    out = []
    ns['print'] = lambda *a, **k: out.append(' '.join(map(str, a)))
    exec(blk.replace('\nif appended:', ''), ns)
    got = any('::error::' in o for o in out)
    ok = got == want
    print(('  ok   ' if ok else '  FAIL ') + f'{stamp} 已有紀錄={have} → 紅字={got} 期望={want}')
    if not ok: fails.append(stamp + str(have))

print('\n' + ('PASS' if not fails else 'FAIL: ' + ', '.join(map(str, fails))))
sys.exit(1 if fails else 0)
