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

def run(name, px_book, quotes, bars, want_write, check=None, nav_sk=KEY, local=None, tw_close=None):
    wrote = {}
    def _ghwrite(path, blob, message=None):
        wrote['path'] = path; wrote['blob'] = blob
    ns = {'px_book': px_book, 'quotes': quotes, 'BARS_SEEN': bars,
          'PX_PATH': 'data/px.enc', 'PX_KEEP': 15, 'NAV_SK': nav_sk,
          'to_yahoo': lambda t: t.partition(':')[0], '_ghwrite': _ghwrite,
          'WINDOWS': {'TPE': (9.0, 13.5), 'US': (9.5, 16.0)},
          '_ex_of': lambda t: t.partition(':')[2] if t.partition(':')[2] in ('TPE',) else 'US',
          '_local': local or _local_stub,
          'QUOTE_DELAY_MIN': {'TPE': 20, 'TYO': 20, 'KRX': 20, 'HKG': 15, 'SHA': 30, 'SHE': 30},
          'TW_CLOSE': tw_close or {},
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
run('bootstrap', {}, {'XYZ:NASDAQ': Q(120.0, '2026-09-08')},
    {'XYZ': {'2026-09-04': 118.0, '2026-09-05': 119.0}}, True,
    check=lambda b, _: b['XYZ:NASDAQ'] == {'2026-09-04': 118.0, '2026-09-05': 119.0,
                                          '2026-09-08': 120.0})

print('\n[2] 同一場次第二次看到不同的價(事後還原)→ 不覆寫第一次看到的')
run('first-wins', {'XYZ:NASDAQ': {'2026-09-08': 120.0}},
    {'XYZ:NASDAQ': Q(114.5, '2026-09-08')}, {'XYZ': {'2026-09-08': 114.5}}, False,
    check=lambda b, _: True)

print('\n[3] 完全沒有變動 → 不寫(省 commit,也不動 mtime)')
run('no-change', {'XYZ:NASDAQ': {'2026-09-08': 120.0}},
    {'XYZ:NASDAQ': Q(120.0, '2026-09-08')}, {}, False)

print('\n[4] 盤中報價不入帳(live=True)')
run('live-skip', {}, {'XYZ:NASDAQ': Q(130.0, '2026-09-08', live=True)}, {}, False)

print('\n[5] 快取價不入帳')
run('cache-skip', {}, {'XYZ:NASDAQ': Q(130.0, '2026-09-08', note='快取 09/08')}, {}, False)

print('\n[6] 台灣基金不入帳(它走 nav-cache)')
run('twfund-skip', {}, {'0050:TWFUND': Q(50.0, '2026-09-08')}, {}, False)

print('\n[7] 超過 PX_KEEP 場 → 只留最近 15 場')
old = {f'2026-08-{d:02d}': 100.0 + d for d in range(1, 21)}
run('trim', {'XYZ:NASDAQ': old}, {'XYZ:NASDAQ': Q(120.0, '2026-09-08')}, {}, True,
    check=lambda b, _: len(b['XYZ:NASDAQ']) == 15
                       and min(b['XYZ:NASDAQ']) == '2026-08-07'
                       and '2026-09-08' in b['XYZ:NASDAQ'])

print('\n[8] 沒有 SYNC_KEY → 絕不落明文')
run('no-key', {}, {'XYZ:NASDAQ': Q(120.0, '2026-09-08')}, {}, False, nav_sk=None)

print('\n[10] 盤中的日線最後一根不入帳(v126 的 bug:亞股當日快照被當成收盤,再也改不掉)')
import datetime as _dt
_TPE = _dt.timezone(_dt.timedelta(hours=8))
_tw_now = _dt.datetime.now(_TPE)
_tw_today = _tw_now.date().isoformat()
_tw_yday = (_tw_now.date() - _dt.timedelta(days=1)).isoformat()
_tw_closed = (_tw_now.hour + _tw_now.minute / 60.0) >= 13.5 + 25 / 60.0   # v138:收盤 + 資料源延遲 20 分 + 5
run('intraday-bar', {}, {'9901:TPE': Q(100.0, _tw_yday)},
    {'9901': {_tw_yday: 99.0, _tw_today: 100.5}}, True,
    check=lambda b, _: (_tw_today in b['9901:TPE']) == _tw_closed
                       and b['9901:TPE'][_tw_yday] == 99.0)

print('\n[12] 收盤後、延遲還沒過(台股 13:30 那一輪,約 13:35–13:42):當天的價不入帳;過了才入(v138)')
_D = _dt.date(2026, 9, 16)
for _h, _want in ((13.6, False), (13.85, False), (13.95, True), (14.1, True)):
    run(f'台股 {_h:.2f} 時', {}, {'9901:TPE': Q(1040.0, '2026-09-16', note='收盤')},
        {'9901': {'2026-09-15': 1035.0, '2026-09-16': 1040.0}}, True,
        local=lambda k, _h=_h: (_D, _h),
        check=lambda b, _, _want=_want: ('2026-09-16' in b['9901:TPE']) == _want and b['9901:TPE']['2026-09-15'] == 1035.0)
run('昨天的場次不受延遲守門影響', {}, {'9901:TPE': Q(1035.0, '2026-09-15', note='收盤')}, {}, True,
    local=lambda k: (_D, 13.6), check=lambda b, _: b['9901:TPE'] == {'2026-09-15': 1035.0})

print('\n[13] 台股:交易所官方收盤一律覆寫帳本同一天的值(帳本記到延遲價時靠它修回來)(v138)')
run('官方覆寫錯的那一天', {'9901:TPE': {'2026-09-15': 1040.0, '2026-09-14': 1015.0}}, {'9901:TPE': Q(1035.0, '2026-09-16', live=True)}, {}, True,
    local=lambda k: (_D, 10.0), tw_close={'9901': (1035.0, _dt.date(2026, 9, 15))},
    check=lambda b, _: b['9901:TPE'] == {'2026-09-14': 1015.0, '2026-09-15': 1035.0})
run('官方與帳本一致 → 不寫', {'9901:TPE': {'2026-09-15': 1035.0}}, {'9901:TPE': Q(1035.0, '2026-09-16', live=True)}, {}, False,
    local=lambda k: (_D, 10.0), tw_close={'9901': (1035.0, _dt.date(2026, 9, 15))})
run('官方只影響台股', {'XYZ:NASDAQ': {'2026-09-15': 120.0}}, {'XYZ:NASDAQ': Q(121.0, '2026-09-15')}, {}, False,
    tw_close={'XYZ': (999.0, _dt.date(2026, 9, 15))})

print('\n[11] v1 的帳本整份作廢(混進過盤中價,而且改不掉)')
import re as _re, gzip as _gz
_src = open('fetch_action.py', encoding='utf-8').read()
_i = _src.index('PX_PATH, PX_KEEP =')
_j = _src.index('BARS_SEEN = {}', _i)
_LOAD = _src[_i:_j]
for _v, _want in ((1, 0), (2, 1)):
    _blob = _enc(KEY, _padj({'v': _v, 'px': {'XYZ:NASDAQ': {'2026-09-08': 120.0}}}, 32768)).encode()
    _ns = {'_ghread': lambda p: _blob, 'NAV_SK': KEY, '_dec': _dec, 'json': json}
    exec(_LOAD, _ns)
    _ok = len(_ns['px_book']) == _want
    print(('  ok   ' if _ok else '  FAIL ') + f'v{_v} 的帳本 → 採用 {len(_ns["px_book"])} 檔(期望 {_want})')
    if not _ok: fails.append(f'load-v{_v}')

print('\n[9] 寫出去的是密文,而且補到 32KB 級距')
run('padded', {}, {'XYZ:NASDAQ': Q(120.0, '2026-09-08')}, {}, True,
    check=lambda b, blob: b'XYZ:NASDAQ' not in blob and b'"px"' not in blob
                          and len(_dec(KEY, blob.decode()).encode()) % 32768 == 0)

print('\n' + ('PASS' if not fails else 'FAIL: ' + ', '.join(fails)))
sys.exit(1 if fails else 0)
