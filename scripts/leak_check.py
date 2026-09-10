#!/usr/bin/env python3
"""建置期的洩漏檢查:公開 repo 裡的每一支腳本都不得含有持倉名稱、代號或券商代碼。

2026-09-08 資安複審發現建置腳本裡被寫進了出清 / 減碼種子(名稱、代號、股數、出場價),而這些
腳本以 base64 內嵌在公開的 build.yml —— 任何人不用密碼都解得出來。每日排程當時只檢查價格,
沒有看公開檔案的內容,所以一週都沒發現。這一步把同樣的檢查放進建置本身:
拒絕名單由 bundle(secret)與覆寫檔合併後的持倉建立,命中就讓建置失敗,**不發佈**。

log 只印「檔名:行號」與數量,不印命中的內容(log 本身是公開的)。
允許的例外:行尾標 `// mig` 的遷移用對照表(v124 移除)、基準指數代號、鏡像列標籤。"""
import json, os, sys, re

def _quiet(t, v, tb): print(f"leak-check: 中止({t.__name__})", file=sys.stderr)
sys.excepthook = _quiet

FILES = ["v3.js", "v3.css", "build_dashboard_v3.py", "fetch_action.py", "sync_crypto.py",
         "merge_overlay.py", "append_history.py", "backfill_history.py", "apply_statement.py",
         "leak_check.py"]
CUR = {"USD", "TWD", "JPY", "KRW", "HKD", "CNY", "EUR", "GBP", "CHF"}
# 例外(基準指數代號、兩個鏡像列標籤)存 FNV 雜湊,連例外本身都不寫名字
def _fnv(s):
    h = 0x811c9dc5
    for b in str(s).encode("utf-8"):
        h ^= b; h = (h * 0x01000193) & 0xFFFFFFFF
    return format(h, "08x")
ALLOW_H = {"f997245f", "f8d866d7", "0ca1bb26"}

P = json.load(open("portfolio.json"))
deny = set()
def _add(v):
    if isinstance(v, str):
        v = v.strip()
        if len(v) >= 3 and v not in CUR and _fnv(v) not in ALLOW_H:
            deny.add(v)
for r in P.get("regions", []):
    for g in r.get("groups", []):
        for p in g.get("positions", []):
            _add(p.get("name")); _add(p.get("ticker"))
            if p.get("ticker"): _add(str(p["ticker"]).split(":")[0])
            for c in (p.get("stmt_code") or []):
                _add(c); _add(str(c).split(" ")[0])
for c in P.get("closed_ytd") or []:
    if isinstance(c, dict): _add(c.get("name")); _add(c.get("ticker"))
for t in P.get("trims") or []:
    if isinstance(t, dict): _add(t.get("name")); _add(t.get("ticker")); _add(t.get("code"))
# 太短或太泛的字串(如純數字 "1165")會誤判程式碼裡的常數,只留 4 位以上的純數字
deny = {d for d in deny if not (d.isdigit() and len(d) < 4)}
deny = sorted(deny, key=len, reverse=True)

hits = []
for f in FILES:
    if not os.path.exists(f):
        continue
    for i, line in enumerate(open(f, encoding="utf-8", errors="ignore"), 1):
        if line.rstrip().endswith("移除") and "// mig" in line:
            continue
        if "data:image" in line:                 # 內嵌圖示的 base64,3 字母組合什麼都像
            continue
        for d in deny:
            if d in line:
                hits.append(f); break
        # 看起來像金額 / 股數的樣式也擋:千分位數字、「N 股」
        if "rgb" in line or "rgba" in line:       # CSS 顏色的 255,255,255
            continue
        if re.search(r"\d{1,3}(,\d{3})+", line) or re.search(r"\d+\s*股\b", line):
            hits.append(f)
if hits:
    # 只印檔名與數量:行號會變成「這一行裡有持倉字串」的確認器,而腳本本身是公開的
    _files = sorted(set(hits))
    print(f"::error::leak-check: 公開腳本含持倉識別字串或金額樣式,共 {len(hits)} 處({', '.join(_files)}),本輪不發佈")
    raise SystemExit(1)
print("leak-check: ok")
