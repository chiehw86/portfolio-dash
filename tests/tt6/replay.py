# -*- coding: utf-8 -*-
"""把過去一週真實出過的五種價格錯誤重放一次,驗證價格帳本有沒有把它們擋掉。

每個情境都給「帳本裡我們當時親眼看到的收盤」+「資料源這一輪給的(壞掉的)日線」,
然後看抓價端算出來的前收與漲跌對不對。
"""
import json, os, subprocess, sys, datetime
sys.path.insert(0, '.')
os.chdir(os.path.dirname(os.path.abspath(__file__)))
import sync_crypto

KEY = "k" * 43 + "="
FAILS = []

def run(name, spec, book, want_prev=None, want_chg=None, want_note=None, tk="AAA:NYSE"):
    json.dump(spec, open('spec.json', 'w'))
    json.dump({"portfolio": {"regions": [{"name": "r", "groups": [{"name": "g", "positions":
              [{"name": "x", "ticker": tk, "kind": "live", "cur": "USD"}]}]}]},
               "baseline": {}}, open('bundle.json', 'w'), ensure_ascii=False)
    blob = sync_crypto.encrypt(KEY, json.dumps({"v": 2, "px": book})).encode() if book else None
    stub = f'''
import sync_crypto, runpy
_px = {blob!r}
_orig = sync_crypto.gh_read
sync_crypto.gh_read = lambda p, ref="main", timeout=20: (_px if p == "data/px.enc" else None)
sync_crypto.gh_write = lambda *a, **k: None
runpy.run_path("fetch_action.py", run_name="__main__")
'''
    r = subprocess.run([sys.executable, '-c', stub], capture_output=True, text=True,
                       env={**os.environ, "SYNC_KEY": KEY})
    q = json.load(open('quotes3.json'))['quotes'].get(tk)
    if q is None:
        print(f"  FAIL {name}: 沒有報價\n{r.stdout[-400:]}{r.stderr[-400:]}"); FAILS.append(name); return
    chg, note = q.get('change_pct'), q.get('note', '')
    ok = True
    if want_chg is not None:
        ok = ok and (chg is not None and abs(chg - want_chg) < 0.06)
    if want_note is not None:
        ok = ok and (want_note in note)
    print(("  ok   " if ok else "  FAIL") + f" {name}: chg={chg} note={note}")
    if not ok:
        FAILS.append(name)
        print(f"        期望 chg≈{want_chg} note含「{want_note}」")

# 1) 日線最後一根落後一場(2026-08-22 實況)。帳本有 09-08 的真實收盤 2410。
run("日線落後一場(帳本有正解)",
    {"AAA": {"bars": [["2026-09-04", 2340.0], ["2026-09-05", 2375.0]], "last": 2375.0, "pc": 2340.0}},
    {"AAA:NYSE": {"2026-09-05": 2375.0, "2026-09-08": 2410.0}},
    want_chg=None)

# 2) 日線缺根(某倫敦 ETF 09-03 實況):最後一根 09-08,但倒數第二根是 09-03,
#    真正的前一場 09-05 帳本裡有。資料源會算成 −3.8%,正解是 −0.3%。
run("日線缺根(前收差兩場)",
    {"AAA": {"bars": [["2026-09-03", 32.77], ["2026-09-08", 31.52]], "last": 31.52, "pc": 31.62}},
    {"AAA:NYSE": {"2026-09-03": 32.77, "2026-09-05": 31.62}},
    want_chg=-0.32, want_note="")

# 3a) 除息(某台股 09-01 實況):資料源把歷史收盤調低 5%,於是算出 +9.98%(接近漲停)。
#     帳本記的是還原前的原始收盤 → 市值真的掉了那一塊,以帳本為準才對。
run("除息還原(帳本為準)",
    {"AAA": {"bars": [["2026-09-05", 7030.0], ["2026-09-08", 7730.0]], "last": 7730.0, "pc": 7030.0}},
    {"AAA:NYSE": {"2026-09-05": 7400.0}},
    want_chg=4.46)

# 3b) 分割/合併:資料源把歷史價砍半,我們的股數還是舊的 → 用資料源的百分比,
#     但必須大聲標出來要求確認股數。
run("分割(用資料源、標記股數)",
    {"AAA": {"bars": [["2026-09-05", 100.0], ["2026-09-08", 103.0]], "last": 103.0, "pc": 100.0}},
    {"AAA:NYSE": {"2026-09-05": 200.0}},
    want_chg=3.0, want_note="疑似分割/合併")

# 3c) 帳本太舊(連假 / 新標的 / 長期抓不到)→ 不採用,退回資料源
run("帳本過舊(退回資料源)",
    {"AAA": {"bars": [["2026-09-05", 100.0], ["2026-09-08", 103.0]], "last": 103.0, "pc": 100.0}},
    {"AAA:NYSE": {"2026-07-01": 60.0}},
    want_chg=3.0)

# 4) 兩源前收不一致(四檔美股 09-01 實況):帳本說了算。
run("兩源不一致(帳本說了算)",
    {"AAA": {"bars": [["2026-09-05", 953.83], ["2026-09-08", 911.93]], "last": 911.93, "pc": 898.53}},
    {"AAA:NYSE": {"2026-09-05": 914.09}},
    want_chg=-0.24)

# 5) 沒有帳本(第一次跑):行為必須與舊版相同,不能中斷
run("帳本是空的(退回舊行為)",
    {"AAA": {"bars": [["2026-09-05", 100.0], ["2026-09-08", 103.0]], "last": 103.0, "pc": 100.0}},
    None, want_chg=3.0)

# 6) 單日 ±40% 以上 → 不報漲跌
run("單日變動異常(不報)",
    {"AAA": {"bars": [["2026-09-05", 100.0], ["2026-09-08", 190.0]], "last": 190.0, "pc": 100.0}},
    {"AAA:NYSE": {"2026-09-05": 100.0}},
    want_note="單日變動異常")

print("\nPASS" if not FAILS else "\nFAILED: " + ", ".join(FAILS))
sys.exit(1 if FAILS else 0)
