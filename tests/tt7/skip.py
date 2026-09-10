# -*- coding: utf-8 -*-
"""覆寫檔被忽略的那一輪,apply_statement 絕不能回頭覆寫 overlay.enc。"""
import sys, os, re, json, datetime
os.chdir(os.path.dirname(os.path.abspath(__file__)))
fails = []

mo = open('merge_overlay.py', encoding='utf-8').read()
ap = open('apply_statement.py', encoding='utf-8').read()

print('[1] merge_overlay:sha 只在「真的要套用」之後才寫')
i_skip = mo.index('open("overlay_sha.txt", "w").write("SKIP")')
i_sha  = mo.index('open("overlay_sha.txt", "w").write(sha or "")')
i_da   = mo.index('da, oa = _t(P.get("data_at"))')
i_fut  = mo.index('覆寫檔時間戳在未來')
for name, ok in (('SKIP 寫在 data_at 檢查裡', i_da < i_skip < i_fut),
                 ('真 sha 寫在兩道檢查之後', i_sha > i_fut),
                 ('讀完就寫的舊寫法已移除', mo.count('overlay_sha.txt') == 2)):
    print(('  ok   ' if ok else '  FAIL ') + name)
    if not ok: fails.append(name)

print('\n[2] apply_statement:讀到 SKIP 就不寫,而且是在 gh_write 之前')
i_chk = ap.index('if _sha == "SKIP":')
i_w   = ap.index('gh_write(OVERLAY', i_chk - 2000)
for name, ok in (('SKIP 檢查在 gh_write 之前', i_chk < i_w),
                 ('SKIP 分支直接 return', 'return' in ap[i_chk:i_w])):
    print(('  ok   ' if ok else '  FAIL ') + name)
    if not ok: fails.append(name)

print('\n[3] 實際跑一次:忽略分支要留下 SKIP,套用分支要留下真 sha')
blk = mo[mo.index('P = json.load(open("portfolio.json"))'):mo.index('\n', i_sha)]
for name, data_at, ov_at, want in (
        ('覆寫檔比 bundle 舊 → SKIP', '2026-09-09T00:00:00Z', '2026-09-08T00:00:00Z', 'SKIP'),
        ('覆寫檔比 bundle 新 → 真 sha', '2026-09-08T00:00:00Z', '2026-09-09T00:00:00Z', 'abc123'),
        ('bundle 沒有 data_at → 真 sha', None, '2026-09-09T00:00:00Z', 'abc123')):
    json.dump({'data_at': data_at} if data_at else {}, open('portfolio.json', 'w'))
    if os.path.exists('overlay_sha.txt'): os.remove('overlay_sha.txt')
    ns = {'json': json, 'o': {'at': ov_at}, 'sha': 'abc123', 'print': lambda *a, **k: None}
    try: exec(blk, ns)
    except SystemExit: pass
    got = open('overlay_sha.txt').read().strip() if os.path.exists('overlay_sha.txt') else '(沒寫)'
    ok = got == want
    print(('  ok   ' if ok else '  FAIL ') + f'{name}: 檔案內容={got} 期望={want}')
    if not ok: fails.append(name)
for f in ('portfolio.json', 'overlay_sha.txt'):
    if os.path.exists(f): os.remove(f)

print('\n' + ('PASS' if not fails else 'FAIL: ' + ', '.join(fails)))
sys.exit(1 if fails else 0)
