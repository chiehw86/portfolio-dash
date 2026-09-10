#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""replica —— 把「線上此刻的真實狀態」拉下來,在本機組出一份等價的建置輸入。

為什麼要這個:本機 `portfolio.json` 是 bundle,**沒有覆寫檔** —— `trims`、`closed_ytd`、
`dropped`、所有手動修改都不在裡面。拿它建出來的頁面去驗算,結論會是錯的
(2026-09-09 的 YTD 誤判、v129→v132 三輪修不對,全部出自這一點)。

    python3 tools/replica.py            # 產生 replica/ 目錄
    cd replica && python3 build_dashboard_v3.py && node ../tt1/kpi.js

產出:replica/{portfolio.json, quotes3.json, history.json} + 目前工作目錄的
v3.js / v3.css / build_dashboard_v3.py。改完程式先在這裡驗,再決定要不要出版。
"""
import json, os, shutil, subprocess, sys, tempfile, urllib.request

WORK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(WORK, "replica")
RAW = "https://raw.githubusercontent.com/chiehw86/portfolio-dash/gh-pages/index.html"

EXTRACT = r"""
const { chromium } = require('playwright');
(async () => {
  const b = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium' });
  const p = await b.newPage();
  await p.goto('file://' + process.env.PAGE);
  await p.waitForTimeout(1500);
  await p.fill('#staticrypt-password', process.env.VIEW_PASSWORD);
  await p.evaluate(() => document.getElementById('staticrypt-form')
    .dispatchEvent(new Event('submit', {cancelable: true})));
  await p.waitForTimeout(6500);
  const out = await p.evaluate(() => {
    // __P__ 是建置端寫進頁面的「bundle + 伺服器端已併入的覆寫檔」= 線上真實狀態。
    // 報價從各部位的 p.q 反推回 quotes3.json 的形狀。
    const src = JSON.parse(JSON.stringify(window.__P__ || P));
    const quotes = {};
    (src.regions || []).forEach(r => (r.groups || []).forEach(g => (g.positions || []).forEach(q => {
      if (!q.q || !q.ticker || q.derived) return;
      const x = q.q;
      if (quotes[q.ticker]) return;
      const o = {price: x.price, cur: x.cur, live: !!x.live};
      if (x.chg != null && x.price) {
        // 頁面上的 chg 是 USD 口徑換算過的,這裡用 prev 反推回原幣別漲跌
        o.change_pct = Math.round(x.chg * 100) / 100;
      }
      if (x.qnote) o.note = x.qnote;
      if (x.asof) o.asof = x.asof;
      if (x.settled) o.settled = true;
      if (x.pending) o.pending_open = true;
      if (x.prev_bar != null) o.prev_bar = x.prev_bar;
      if (x.prev_quote != null) o.prev_quote = x.prev_quote;
      if (x.prev_book != null) o.prev_book = x.prev_book;
      quotes[q.ticker] = o;
    })));
    const meta = (document.querySelector('.meta') || {}).innerText || '';
    const at = (meta.match(/報價時間:([^·]+)/) || [])[1] || '';
    return {P: src, quotes, fetched_at_taipei: at.trim(),
            hist: window.__HIST__ || [], fx: (typeof FX !== 'undefined') ? FX : {},
            build: (document.body.innerText.match(/建置 v\d+ · \w+/) || [])[0] || ''};
  });
  console.log(JSON.stringify(out));
  await b.close();
})();
"""


def main():
    if not os.environ.get("VIEW_PASSWORD"):
        sys.exit("請設定 VIEW_PASSWORD")
    d = tempfile.mkdtemp(prefix="replica_")
    page = os.path.join(d, "index.html")
    with urllib.request.urlopen(RAW, timeout=40) as r:
        open(page, "wb").write(r.read())
    open(os.path.join(d, "x.js"), "w").write(EXTRACT)
    env = dict(os.environ, PAGE=page)
    r = subprocess.run(["node", os.path.join(d, "x.js")], capture_output=True, text=True,
                       env=env, cwd=WORK)
    if r.returncode:
        sys.exit("抽取失敗:\n" + (r.stderr or "")[-1500:])
    o = json.loads(r.stdout)

    os.makedirs(OUT, exist_ok=True)
    json.dump(o["P"], open(f"{OUT}/portfolio.json", "w"), ensure_ascii=False)
    # 建置端要的是 fx_usd(幣別 → 每 1 USD 幾單位),頁面上的 FX 就是這個
    json.dump({"fetched_at_taipei": o["fetched_at_taipei"], "quotes": o["quotes"],
               "fx_usd": o["fx"], "indices": []},
              open(f"{OUT}/quotes3.json", "w"), ensure_ascii=False)
    json.dump(o["hist"], open(f"{OUT}/history.json", "w"), ensure_ascii=False)
    # units.json:建置端用它沿用隱含股數,沒有的話每輪重算(對驗算無影響但會有微小差異)
    src = os.path.join(WORK, "src") if os.path.isdir(os.path.join(WORK, "src")) else WORK
    for f in ("v3.js", "v3.css", "build_dashboard_v3.py", "leak_check.py", "fetch_action.py"):
        shutil.copy(os.path.join(src, f), OUT)

    n = sum(len(g["positions"]) for r_ in o["P"]["regions"] for g in r_["groups"])
    print(f"replica/ 已建立 —— 線上 {o['build']} · {o['fetched_at_taipei']}")
    print(f"  部位 {n} 列 · 報價 {len(o['quotes'])} 檔 · 走勢圖 {len(o['hist'])} 列 "
          f"· 減碼 {len(o['P'].get('trims') or [])} 筆 · 已出清 {len(o['P'].get('closed_ytd') or [])} 筆")
    print("  下一步:cd replica && python3 build_dashboard_v3.py")


if __name__ == "__main__":
    main()
