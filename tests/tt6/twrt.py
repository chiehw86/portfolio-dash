# -*- coding: utf-8 -*-
"""台股盤中改用證交所即時行情(v137):四道檢查每一道都要擋得住,端點壞掉要退回原路。
沙盒連不到 mis.twse.com.tw,所以這裡用假回應驗邏輯;真端點的格式上線後對頁面驗。
代號一律用假的(9xxx),不碰持倉。"""
import json, os, sys, datetime, types
os.chdir(os.path.dirname(os.path.abspath(__file__)))

src = open('fetch_action.py', encoding='utf-8').read()
i = src.index('TW_RT = {}'); j = src.index('\n\n\ndef ', src.index('def _load_tw_realtime'))
BLK = src[i:j]
k = src.index('            _rt = TW_RT.get'); m = src.index('\n', src.index('price, sess = _rt[0]', k))
OVR = '\n'.join(l[12:] for l in src[k:m].splitlines())

fails = []
def ok(name, c, d=''):
    print(('  ok   ' if c else '  FAIL ') + name + (('  ' + d) if d else ''))
    if not c: fails.append(name)

TODAY = datetime.date(2026, 9, 16)              # 週三
def mk(rows, raise_on_api=False, hour=9.5, weekday_date=TODAY, official=None, codes=('9901', '9902', '9903'), body=None):
    """執行 _load_tw_realtime,回傳 (訊息, TW_RT, 打了幾次網路)。"""
    calls = []
    class _Resp:
        def __init__(self, b): self.b = b
        def read(self): return self.b
    class _Op:
        addheaders = []
        def open(self, url, timeout=0):
            calls.append(url)
            if 'getStockInfo' in url:
                if raise_on_api: raise OSError('blocked')
                return _Resp(body if body is not None else json.dumps({"msgArray": rows, "rtcode": "0000"}).encode())
            return _Resp(b'')
    ur = types.SimpleNamespace(build_opener=lambda *a: _Op(), HTTPCookieProcessor=lambda *a: None)
    ns = {'json': json, 'urllib': types.SimpleNamespace(request=ur), 'time': __import__('time'),
          'WINDOWS': {"TPE": (9.0, 13.5)}, '_local': lambda k: (weekday_date, hour),
          'TW_CLOSE': official or {}}
    exec(BLK, ns)
    msg = ns['_load_tw_realtime'](set(codes))
    return msg, ns['TW_RT'], calls

def row(c, z, y, d='20260916', b='100.5_100.0_99.5_', trade=None):
    r = {"@": f"{c}.tw", "tv": "-", "ps": "-", "pz": "-", "c": c, "ex": "tse", "z": z, "y": y, "d": d,
         "t": "09:31:05", "b": b, "a": "101_101.5_", "tlong": "1789525684000", "n": "x", "ts": "0"}
    if trade is not None: r["trade"] = trade
    return r

print('[1] 正常:今天的場次、成交價合理、昨收與官方一致 → 採用')
msg, rt, calls = mk([row('9901', '101', '100'), row('9902', '55.5', '55')], codes=('9901', '9902'),
                    official={'9901': (100.0, TODAY - datetime.timedelta(days=1))})
ok('訊息=採用', msg == '採用', msg)
ok('兩檔都進 TW_RT,值正確', rt == {'9901': (101.0, 100.0), '9902': (55.5, 55.0)}, str(rt))
ok('先拿 cookie 再打 API(兩次請求)', len(calls) == 2 and 'index.jsp' in calls[0] and 'getStockInfo' in calls[1])
ok('上市 / 上櫃兩個頻道都問', 'tse_9901.tw' in calls[1] and 'otc_9901.tw' in calls[1])

print('\n[2] 不是今天的場次 → 不採用(昨天的資料絕不能當盤中價)')
msg, rt, _ = mk([row('9901', '101', '100', d='20260915')], codes=('9901',))
ok('跳過,訊息講明是場次日期', rt == {} and msg == '未取得(檢查未過:場次日期)', f'{msg} {rt}')

print('\n[3] 真實格式:z = "-"(這 5 秒沒成交)→ 用 trade.z;沒有 trade → 最佳買價;買價也沒有 → 跳過')
msg, rt, _ = mk([row('9901', '-', '100', b='100.5_100.0_', trade={"t": "10:27:48", "v": 1, "z": "100.9", "ft": 20}),
                 row('9902', '-', '55', b='55.5_55.0_'),
                 row('9903', '-', '55', b='-')])
ok('9901 用 trade.z 100.9、9902 用買價 55.5、9903 跳過', rt == {'9901': (100.9, 100.0), '9902': (55.5, 55.0)}, str(rt))
ok('訊息=部分採用,類別是成交價或昨收', msg == '部分採用(檢查未過:成交價或昨收)', msg)
ok('z 有值時優先用 z,不用 trade.z', mk([row('9901', '101', '100', trade={"z": "100.9"})], codes=('9901',))[1] == {'9901': (101.0, 100.0)})

print('\n[4] 昨收與官方收盤對不上 → 不信這一筆')
msg, rt, _ = mk([row('9901', '101', '100')], official={'9901': (97.0, TODAY)}, codes=('9901',))
ok('跳過,訊息講明', rt == {} and msg == '未取得(檢查未過:昨收與官方不符)', f'{msg} {rt}')
msg, rt, _ = mk([row('9901', '101', '100')], official={'9901': (100.4, TODAY)})
ok('差 0.4% 以內仍採用', rt == {'9901': (101.0, 100.0)}, str(rt))

print('\n[5] 漲跌超過 ±11% → 不採用(台股限制 10%)')
msg, rt, _ = mk([row('9901', '112', '100'), row('9902', '88', '100'), row('9903', '110', '100')])
ok('只有 +10% 那檔採用', rt == {'9903': (110.0, 100.0)}, str(rt))
ok('訊息=部分採用(漲跌幅度)', msg == '部分採用(檢查未過:漲跌幅度)', msg)

print('\n[6] 端點掛掉 / 格式不對 → 整段略過,TW_RT 空,訊息說沿用 Yahoo')
msg, rt, _ = mk([], raise_on_api=True)
ok('連線失敗', rt == {} and msg == '未取得(連線失敗)', msg)
msg, rt, _ = mk([], body=b'<html>blocked</html>')
ok('回應不是 JSON', rt == {} and msg == '未取得(回應不是 JSON)', msg)
msg, rt, _ = mk([], body=b'{"rtcode":"0000"}')
ok('沒有 msgArray', rt == {} and msg == '未取得(回應沒有 msgArray)', msg)
msg, rt, _ = mk([], codes=('9901',))
ok('空陣列(cookie 沒拿到那種)', rt == {} and msg == '未取得(回應裡沒有這些代號)', msg)
msg, rt, _ = mk([{"foo": 1}, "junk", None, row('9901', '101', '100')], codes=('9901',))
ok('垃圾列跳過,正常列照收', rt == {'9901': (101.0, 100.0)} and msg == '採用', f'{msg} {rt}')

print('\n[7] 非交易時段(收盤後 / 週末)→ 連網路都不打')
msg, rt, calls = mk([row('9901', '101', '100')], hour=14.0)
ok('收盤後不打', calls == [] and rt == {} and '略過' in msg, msg)
msg, rt, calls = mk([row('9901', '101', '100')], hour=10.0, weekday_date=datetime.date(2026, 9, 12))
ok('週六不打', calls == [] and rt == {}, msg)
msg, rt, calls = mk([row('9901', '101', '100')], hour=8.9)
ok('開盤前不打', calls == [] and rt == {}, msg)

print('\n[8] 覆蓋:盤中且有即時價 → 現價換成即時、場次 = 今天;沒有即時價 / 非盤中 / 非台股 → 一切不動')
def ovr(t, live, rt, price=90.0, sess=TODAY - datetime.timedelta(days=1)):
    ns = {'TW_RT': rt, 't': t, 'live': live, 'price': price, 'sess': sess, '_local': lambda k: (TODAY, 9.5)}
    exec(OVR, ns); return ns['price'], ns['sess']
ok('台股盤中有即時價 → 換', ovr('9901:TPE', True, {'9901': (101.0, 100.0)}) == (101.0, TODAY))
ok('台股盤中沒即時價 → 不動', ovr('9901:TPE', True, {}) == (90.0, TODAY - datetime.timedelta(days=1)))
ok('台股收盤後即使有即時價 → 不動(收盤價走原路,帳本才會記)', ovr('9901:TPE', False, {'9901': (101.0, 100.0)})[0] == 90.0)
ok('非台股 → 不動', ovr('9901:TYO', True, {'9901': (101.0, 100.0)})[0] == 90.0)

print('\n[9] log 不得含數量、代號、價格:所有訊息都不能出現數字')
import re as _re
msgs = [mk([row('9901', '101', '100')])[0], mk([], raise_on_api=True)[0], mk([], hour=14.0)[0],
        mk([row('9901', '112', '100'), row('9902', '101', '100', d='20260915')])[0],
        mk([row('9901', '101', '100')], official={'9901': (97.0, TODAY)})[0]]
ok('沒有任何數字', all(not _re.search(r'\d', x) for x in msgs), ' | '.join(msgs))

print('\n' + ('全部通過' if not fails else '失敗:' + ', '.join(fails)))
sys.exit(1 if fails else 0)
