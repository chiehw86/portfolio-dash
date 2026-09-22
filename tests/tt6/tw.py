# -*- coding: utf-8 -*-
"""台股官方收盤標記:誤報要消失,真正對不上的還是要標。
情境取自 2026-09-02 04:10 台北那一輪(TWSE 還停在 08/31)。"""
import json, os, subprocess, sys, datetime
os.chdir(os.path.dirname(os.path.abspath(__file__)))

src = open('fetch_action.py', encoding='utf-8').read()
i = src.index('            _off = TW_CLOSE.get')
j = src.index('            chg = (price / prev', i)
BLK = src[i:j]

fails = []
def run(name, price, prev, off_px, off_date, sess, live, want, book=(None, None), want_prev=None, expected=None, want_price=None, want_sess='(不檢查)', now=None):
    # 這支測的是「與官方收盤不符」那段,價格帳本不是主角:給空帳本的樁,
    # 讓抽出來的區塊跑得起來,順便確認帳本是空的時候行為與舊版一致。
    ns = {'t': '9901:TPE', 'price': price, 'prev': prev, 'live': live, 'sess': sess,
          'label': '收盤', 'TW_CLOSE': {'9901': (off_px, off_date)},
          'px_prev': lambda *a: book,
          '_ex_of': lambda t: 'TPE',
          '_local': lambda k: now if now else (sess or datetime.date.today(), 12.0),
          'WINDOWS': {'TPE': (9.0, 13.5)},
          '_q_book': None,
          'expected_session': lambda t: expected}
    exec(BLK.replace('            ', '', 1).replace('\n            ', '\n'), ns)
    got = '⚠' in ns['label']
    ok = (got == want and (want_prev is None or ns['prev'] == want_prev)
          and (want_price is None or ns['price'] == want_price)
          and (want_sess == '(不檢查)' or ns['sess'] == want_sess))
    print(('  OK   ' if ok else '  FAIL ') + name + f"  標記={got} 期望={want}  prev={ns['prev']}")
    if not ok: fails.append(name)

D = datetime.date
print('\n[1] 誤報情境:官方停在 8/31,現價是 9/1,但前收就是 8/31 那根 → 不該標')
run('台股甲 9/1', price=2440.0, prev=2405.0, off_px=2405.0, off_date=D(2026,8,31),
    sess=None, live=False, want=False)
print('\n[2] 同上,另一檔(漲 3.77%,與官方差更多)→ 不該標')
run('台股乙 9/1', price=1100.0, prev=1060.0, off_px=1060.0, off_date=D(2026,8,31),
    sess=None, live=False, want=False)
print('\n[3] 真的對不上:官方收盤與現價、前收都差很多 → 要標')
run('現價與前收都對不上', price=2440.0, prev=2405.0, off_px=2200.0, off_date=D(2026,8,31),
    sess=None, live=False, want=True)
print('\n[4] 官方是同一場且與現價相符 → 不該標')
run('官方=現價', price=2440.0, prev=2405.0, off_px=2438.0, off_date=D(2026,9,1),
    sess=D(2026,9,1), live=False, want=False)
print('\n[5] 盤中:前收直接採用官方收盤')
run('盤中', price=2460.0, prev=None, off_px=2405.0, off_date=D(2026,9,1),
    sess=None, live=True, want=False)
print('\n[6] 沒有前收可比(prev=None)且官方與現價差很多 → 仍要標')
run('無前收', price=2440.0, prev=None, off_px=2200.0, off_date=D(2026,8,31),
    sess=None, live=False, want=True)

print('\n[7] v138:官方收盤就是上一場時,前收以官方為準,帳本記到延遲價也蓋不掉(2026-09-16 實例:官方 1035、帳本 1040)')
run('盤中,帳本錯', price=1035.0, prev=None, off_px=1035.0, off_date=D(2026,9,15),
    sess=D(2026,9,16), live=True, want=False, book=('2026-09-15', 1040.0), want_prev=1035.0)
run('收盤後官方還沒公布今天的(官方 = 昨天)', price=1035.0, prev=1035.0, off_px=1035.0, off_date=D(2026,9,15),
    sess=D(2026,9,16), live=False, want=False, book=('2026-09-15', 1040.0), want_prev=1035.0)
run('官方已是今天的 → 不是上一場,前收照原邏輯(帳本)', price=1035.0, prev=1035.0, off_px=1035.0, off_date=D(2026,9,16),
    sess=D(2026,9,16), live=False, want=False, book=('2026-09-15', 1040.0), want_prev=1040.0)

print('\n[8] v142:開盤前資料源給「還沒發生的場次」(試撮價、甚至標成週日)→ 場次退回上一場、價格用官方收盤;開盤後不套')
MON = D(2026,9,21); FRI = D(2026,9,18)
run('週一 08:32,日線標成週日、價格是試撮價 → 退回週五', price=1045.0, prev=1125.0, off_px=1125.0, off_date=FRI,
    sess=D(2026,9,20), live=False, want=False, book=('2026-09-17', 1030.0), expected=FRI, now=(MON, 8.53),
    want_price=1125.0, want_sess=FRI, want_prev=1030.0)
run('週一 08:32,日線標成今天(還沒開盤)→ 退回週五', price=2460.0, prev=2460.0, off_px=2460.0, off_date=FRI,
    sess=MON, live=False, want=False, book=('2026-09-17', 2425.0), expected=FRI, now=(MON, 8.53),
    want_price=2460.0, want_sess=FRI, want_prev=2425.0)
run('盤中不套(sess = 今天是正常的)', price=1050.0, prev=None, off_px=1125.0, off_date=FRI,
    sess=MON, live=True, want=False, book=('2026-09-18', 1125.0), expected=FRI, now=(MON, 10.0),
    want_price=1050.0, want_sess=MON, want_prev=1125.0)
run('★ 13:37 收盤後、官方還停在週五(v141 會誤退回週五)→ 今天這一場照算', price=1130.0, prev=1125.0, off_px=1125.0, off_date=FRI,
    sess=MON, live=False, want=False, book=('2026-09-18', 1125.0), expected=FRI, now=(MON, 13.62),
    want_price=1130.0, want_sess=MON, want_prev=1125.0)
run('收盤後日線標成週日 → 就是今天這一場', price=1130.0, prev=1125.0, off_px=1125.0, off_date=FRI,
    sess=D(2026,9,20), live=False, want=False, book=('2026-09-18', 1125.0), expected=FRI, now=(MON, 14.1),
    want_price=1130.0, want_sess=MON, want_prev=1125.0)
run('收盤後正常的場次(sess = expected)不動', price=1125.0, prev=1030.0, off_px=1125.0, off_date=FRI,
    sess=FRI, live=False, want=False, book=('2026-09-17', 1030.0), expected=FRI, now=(FRI, 14.1),
    want_price=1125.0, want_sess=FRI, want_prev=1030.0)

print('\n' + ('全部通過' if not fails else '失敗:' + ', '.join(fails)))
sys.exit(1 if fails else 0)
