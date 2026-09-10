# -*- coding: utf-8 -*-
"""開盤後前 20 分鐘的標籤:應說「剛開盤 · 報價未更新」,20 分鐘後才說「尚無新報價」。
用真實時鐘 + 改 WINDOWS 的開盤時刻來模擬「剛剛開盤」與「開盤很久了」。"""
import json, os, re, subprocess, sys, datetime, zoneinfo, shutil
os.chdir(os.path.dirname(os.path.abspath(__file__)))
jst = datetime.datetime.now(zoneinfo.ZoneInfo("Asia/Tokyo"))
h = jst.hour + jst.minute / 60.0
print("現在東京", jst.strftime("%Y-%m-%d %H:%M"), "(週%d)" % jst.weekday())
if jst.weekday() >= 5:
    print("週末,pending_open 的 opened 恆為 False,跳過"); sys.exit(0)

# 日線最後一根是「昨天」,fast_info 的 last 等於那根收盤 → 走 pending 分支
y = (jst.date() - datetime.timedelta(days=1))
while y.weekday() >= 5: y -= datetime.timedelta(days=1)
SPEC = {"8306.T": {"bars": [[(y - datetime.timedelta(days=1)).isoformat(), 3400.0],
                            [y.isoformat(), 3467.0]], "last": 3467.0, "pc": 3400.0}}
json.dump(SPEC, open("spec.json", "w"))
json.dump({"portfolio": {"regions": [{"name": "r", "groups": [{"name": "g", "positions":
          [{"name": "t", "ticker": "8306:TYO", "kind": "live", "cur": "JPY"}]}]}]},
           "baseline": {}}, open("bundle.json", "w"), ensure_ascii=False)

src = open("fetch_action.py").read()
fails = []
for label, open_hour, want in [("剛開盤(開盤後 5 分)", h - 5/60.0, "剛開盤 · 報價未更新"),
                               ("開盤 1 小時後",        h - 1.0,     "已開盤 · 尚無新報價")]:
    if open_hour < 0: print("  (東京時間太早,跳過", label, ")"); continue
    patched = re.sub(r'"TYO": \(9\.0, 15\.0\)', '"TYO": (%.4f, 23.9)' % open_hour, src)
    assert patched != src, "WINDOWS 沒被改到"
    open("fa_t.py", "w").write(patched)
    subprocess.run([sys.executable, "fa_t.py"], capture_output=True, text=True)
    q = json.load(open("quotes3.json"))["quotes"]["8306:TYO"]
    got = q["note"]
    ok = got.startswith(want) and q.get("pending_open") is True
    print(("  ok  " if ok else "  FAIL") + f" {label:18} → {got}")
    if not ok: fails.append(label)
os.path.exists("fa_t.py") and os.remove("fa_t.py")
print("PASS" if not fails else "FAILED: " + ", ".join(fails))
sys.exit(1 if fails else 0)
