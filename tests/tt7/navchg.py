# -*- coding: utf-8 -*-
"""基金漲跌抓不到時,要顯示「—」而不是 +0.00%。"""
import sys, os, re
os.chdir(os.path.dirname(os.path.abspath(__file__)))
fails = []
fa = open('fetch_action.py', encoding='utf-8').read()
bd = open('../../src/build_dashboard_v3.py', encoding='utf-8').read()

print('[1] fetch_tw_fund 不再補 0.0')
for name, ok in (('補 0.0 的寫法已移除', 'chg if chg is not None else 0.0' not in fa),
                 ('直接回傳 chg', 'return nav, chg, navdate' in fa),
                 ('淨值本身仍然解不到就 raise', 'raise ValueError("nav parse failed")' in fa)):
    print(('  ok   ' if ok else '  FAIL ') + name)
    if not ok: fails.append(name)

print('\n[2] chg=None 時徽章要說明原因')
i = fa.index('live, label = False, f"基金淨值')
ok = '漲跌未取得' in fa[i:i + 400]
print(('  ok   ' if ok else '  FAIL ') + f'徽章補上「漲跌未取得」={ok}')
if not ok: fails.append('label')

print('\n[3] 報價組裝:chg=None → change_pct=None(不是 0)')
ok = '"change_pct": None if chg is None else round(chg, 2)' in fa
print(('  ok   ' if ok else '  FAIL ') + f'change_pct 保留 None={ok}')
if not ok: fails.append('change_pct')

print('\n[4] 建置端:change_pct=None → 前收 None → 今日金額 0、漲跌顯示「—」')
blk = bd[bd.index('ov = p.get("prev_override")'):bd.index('p["q"] = {')]
blk = '\n'.join(l[16:] if l.startswith(' ' * 16) else l for l in blk.split('\n'))
for name, q, want_prev in (('change_pct 是 None', {'change_pct': None}, None),
                           ('change_pct 是 0.0(舊行為)', {'change_pct': 0.0}, 100.0),
                           ('change_pct 是 1.0', {'change_pct': 1.0}, 99.00990099009901)):
    ns = {'p': {}, 'q': q, 'price_usd': 100.0, 'fx': 1.0, 'TODAY_TPE': '2026-09-09'}
    exec(blk, ns)
    got = ns['prev_usd']
    ok = (got is None) if want_prev is None else (got is not None and abs(got - want_prev) < 1e-6)
    print(('  ok   ' if ok else '  FAIL ') + f'{name} → 前收={got}')
    if not ok: fails.append(name)
print('  → 舊行為把「抓不到」變成前收=現價,今日 0.00%;新行為前收 None,今日顯示「—」')

print('\n[5] 快取白名單:None 不會被寫進快取,下一輪也不會變成 0')
i = fa.index('def _cache_pick(c):')
ok = 'isinstance(v, (int, float))' in fa[i:i + 500]
print(('  ok   ' if ok else '  FAIL ') + f'快取只收數值,None 直接落掉={ok}')
if not ok: fails.append('cache')

print('\n' + ('PASS' if not fails else 'FAIL: ' + ', '.join(fails)))
sys.exit(1 if fails else 0)
