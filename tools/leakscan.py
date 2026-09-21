#!/usr/bin/env python3
"""公開 repo 的全面洩漏掃描(出版前第 9 關的第二半):tests/、tools/、CLAUDE.md 也不得含持倉。

建置期的 leak_check.py 只掃「進 build.yml 的那幾支腳本」。2026-09-16 資安複查發現 tests/ 裡的情境測試
拿真實代號當樣本(台股、日股、港股、美股各一檔,還有兩個名稱),而 tests/ 在 2026-09-11 起是公開 repo 的
一部分 —— 等於把幾檔持倉寫在公開的地方。這一支把同一份拒絕名單套到 repo 裡所有會公開的檔案。

用法(從 repo 根目錄;拒絕名單來自 replica/portfolio.json = 線上真實持倉):
    python3 tools/leakscan.py
只印「檔名:命中數」,不印命中的內容。任何命中 → exit 1。
"""
import json, os, re, sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
os.chdir(ROOT)
SRC = "replica/portfolio.json"
if not os.path.exists(SRC):
    sys.exit("找不到 replica/portfolio.json,先跑 tools/replica.py")
P = json.load(open(SRC, encoding="utf-8"))

CUR = {"USD", "TWD", "JPY", "KRW", "HKD", "CNY", "EUR", "GBP", "CHF"}
# 與 leak_check.py 相同的例外(基準指數代號、鏡像列標籤),存雜湊不存名字
def _fnv(s):
    h = 0x811c9dc5
    for b in str(s).encode("utf-8"):
        h ^= b; h = (h * 0x01000193) & 0xFFFFFFFF
    return format(h, "08x")
ALLOW_H = {"f997245f", "f8d866d7", "0ca1bb26"}

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
deny = {d for d in deny if not (d.isdigit() and len(d) < 4)}

def _hit(d, line):
    # 純數字代號要整個 token 相符(避免 1234 命中 41234);其餘子字串即算
    if d.isdigit():
        return re.search(r"(?<![\d.])" + re.escape(d) + r"(?![\d])", line) is not None
    if re.fullmatch(r"[A-Z]{2,6}", d):
        return re.search(r"(?<![A-Za-z])" + re.escape(d) + r"(?![A-Za-z])", line) is not None
    return d in line

# 要掃的公開檔:src/ 交給 leak_check(它有 // mig 例外);這裡掃其餘一切會進 repo 的東西
targets = ["CLAUDE.md", ".gitignore"]
for top in ("tests", "tools"):
    for dp, dn, fn in os.walk(top):
        dn[:] = [d for d in dn if d != "__pycache__"]
        for f in fn:
            if f.endswith((".py", ".js", ".json", ".sh", ".md", ".txt", ".csv")):
                targets.append(os.path.join(dp, f))

bad = 0
for f in targets:
    if not os.path.exists(f):
        continue
    n = 0
    for line in open(f, encoding="utf-8", errors="ignore"):
        if any(_hit(d, line) for d in deny):
            n += 1
    if n:
        print(f"leakscan: {f}:{n}")
        bad += n
print("leakscan: " + ("ok" if not bad else f"命中 {bad} 行,不得出版"))
sys.exit(1 if bad else 0)
