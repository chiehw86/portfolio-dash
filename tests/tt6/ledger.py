# -*- coding: utf-8 -*-
"""價格帳本的寫回:第一次跑(檔案不存在)也要能長出帳本,而且只在有變動時才寫。"""
import sys, os, json, datetime
os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, '.')
from sync_crypto import decrypt as _dec, encrypt as _enc, pad_json as _padj

src = open('fetch_action.py', encoding='utf-8').read()
i = src.index('# ── 價格帳本:把這一輪看到的')
j = src.index('# 淨值快取:抓到就更新日期', i)
BLK = src[i:j]

KEY = 'rMrN1Zaik3nKh8GJNcPziEmqInkD8vXbk3Bqn6z/0bA='
fails = []

_TPE = datetime.timezone(datetime.timedelta(hours=8))
_NY = datetime.timezone(datetime.timedelta(hours=-4))
def _local_stub(key):
    l = datetime.datetime.now(_TPE if key == 'TPE' else _NY)
    return l.date(), l.hour + l.minute / 60.0

def run(name, px_book, quotes, bars, want_write, check=None, nav_sk=KEY):
    wrote = {}
    def _ghwrite(path, blob, message=None):
        wrote['path'] = path; wrote['blob'] = blob
    ns = {'px_book': px_book, 'quotes': quotes, 'BARS_SEEN': bars,
          'PX_PATH': 'data/px.enc', 'PX_KEEP': 15, 'NAV_SK': nav_sk,
          'to_yahoo': lambda t: t.partition(':')[0], '_ghwrite': _ghwrite,
          'WINDOWS': {'TPE': (9.0, 13.5), 'US': (9.5, 16.0)},
          '_ex_of': lambda t: t.partition(':')[2] if t.partition(':')[2] in ('TPE',) else 'US',
          '_local': _local_stub,
          '_enc': _enc, '_padj': _padj, 'print': lambda *a, **k: None}
    exec(BLK, ns)
    got = 'blob' in wrote
    ok = got == want_write
    detail = ''
    if got:
        book = json.loads(_dec(KEY, wrote['blob'].decode()))['px']
        if check:
            ok = ok and check(book, wrote['blob'])
        detail = '  ' + json.dumps(book, ensure_ascii=False)
    print(('  ok   ' if ok else '  FAIL ') + name + f'  寫回={got} 期望={want_write}' + detail)
    if not ok: fails.append(name)

Q = lambda price, asof, live=False, note='美股收盤': {
    'price': price, 'asof': asof, 'live': live, 'note': note}

print('\n[1] 第一次跑:帳本不存在(px_book 空的)→ 要長出帳本')
run('bootstrap', {}, {'MU:NASDAQ': Q(120.0, '2026-09-08')},
    {'MU': {'2026-09-04': 118.0, '2026-09-05': 119.0}}, True,
    check=lambda b, _: b['MU:NASDAQ'] == {'2026-09-04': 118.0, '2026-09-05': 119.0,
                                          '2026-09-08': 120.0})

print('\n[2] 同一場次第二次看到不同的價(事後還原)→ 不覆寫第一次看到的')
run('first-wins', {'MU:NASDAQ': {'2026-09-08': 120.0}},
    {'MU:NASDAQ': Q(114.5, '2026-09-08')}, {'MU': {'2026-09-08': 114.5}}, False,
    check=lambda b, _: True)

print('\n[3] 完全沒有變動 → 不寫(省 commit,也不動 mtime)')
run('no-change', {'MU:NASDAQ': {'2026-09-08': 120.0}},
    {'MU:NASDAQ': Q(120.0, '2026-09-08')}, {}, False)

print('\n[4] 盤中報價不入帳(live=True)')
run('live-skip', {}, {'MU:NASDAQ': Q(130.0, '2026-09-08', live=True)}, {}, False)

print('\n[5] 快取價不入帳')
run('cache-skip', {}, {'MU:NASDAQ': Q(130.0, '2026-09-08', note='快取 09/08')}, {}, False)

print('\n[6] 台灣基金不入帳(它走 nav-cache)')
run('twfund-skip', {}, {'0050:TWFUND': Q(50.0, '2026-09-08')}, {}, False)

print('\n[7] 超過 PX_KEEP 場 → 只留最近 15 場')
old = {f'2026-08-{d:02d}': 100.0 + d for d in range(1, 21)}
run('trim', {'MU:NASDAQ': old}, {'MU:NASDAQ': Q(120.0, '2026-09-08')}, {}, True,
    check=lambda b, _: len(b['MU:NASDAQ']) == 15
                       and min(b['MU:NASDAQ']) == '2026-08-07'
                       and '2026-09-08' in b['MU:NASDAQ'])

print('\n[8] 沒有 SYNC_KEY → 絕不落明文')
run('no-key', {}, {'MU:NASDAQ': Q(120.0, '2026-09-08')}, {}, False, nav_sk=None)

print('\n[10] 盤中的日線最後一根不入帳(v126 的 bug:亞股當日快照被當成收盤,再也改不掉)')
import datetime as _dt
_TPE = _dt.timezone(_dt.timedelta(hours=8))
_tw_now = _dt.datetime.now(_TPE)
_tw_today = _tw_now.date().isoformat()
_tw_yday = (_tw_now.date() - _dt.timedelta(days=1)).isoformat()
_tw_closed = (_tw_now.hour + _tw_now.minute / 60.0) >= 13.5
run('intraday-bar', {}, {'2330:TPE': Q(100.0, _tw_yday)},
    {'2330': {_tw_yday: 99.0, _tw_today: 100.5}}, True,
    check=lambda b, _: (_tw_today in b['2330:TPE']) == _tw_closed
                       and b['2330:TPE'][_tw_yday] == 99.0)

print('\n[11] v1 的帳本整份作廢(混進過盤中價,而且改不掉)')
import re as _re, gzip as _gz
_src = open('fetch_action.py', encoding='utf-8').read()
_i = _src.index('PX_PATH, PX_KEEP =')
_j = _src.index('BARS_SEEN = {}', _i)
_LOAD = _src[_i:_j]
for _v, _want in ((1, 0), (2, 1)):
    _blob = _enc(KEY, _padj({'v': _v, 'px': {'MU:NASDAQ': {'2026-09-08': 120.0}}}, 32768)).encode()
    _ns = {'_ghread': lambda p: _blob, 'NAV_SK': KEY, '_dec': _dec, 'json': json}
    exec(_LOAD, _ns)
    _ok = len(_ns['px_book']) == _want
    print(('  ok   ' if _ok else '  FAIL ') + f'v{_v} 的帳本 → 採用 {len(_ns["px_book"])} 檔(期望 {_want})')
    if not _ok: fails.append(f'load-v{_v}')

print('\n[9] 寫出去的是密文,而且補到 32KB 級距')
run('padded', {}, {'MU:NASDAQ': Q(120.0, '2026-09-08')}, {}, True,
    check=lambda b, blob: b'MU:NASDAQ' not in blob and b'"px"' not in blob
                          and len(_dec(KEY, blob.decode()).encode()) % 32768 == 0)

print('\n' + ('PASS' if not fails else 'FAIL: ' + ', '.join(fails)))
sys.exit(1 if fails else 0)
