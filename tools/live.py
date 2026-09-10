#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify-live —— 對「線上那一份」求證的統一入口。

存在的理由:2026-09-09/10 連續三次把「本機建置的頁面」或「讀碼推論」當成結論交出去
(YTD 報酬率、v129 的誤報成因、v130 的清理失效),三次都被線上實測推翻。
根因不是判斷力,是「驗證線上」以前要手動做七八個步驟,於是每次都被跳過。
把它變成一行指令,誠實的那條路才會是最省力的那條。

    python3 tools/live.py build                # 線上現在是哪一版
    python3 tools/live.py enc overlay          # 解開線上的加密檔並印摘要
    python3 tools/live.py enc px|nav|history
    python3 tools/live.py eval '<JS 運算式>'    # 在解密後的線上頁面上求值
    python3 tools/live.py evalfile x.js        # 同上,但從檔案讀(可多行、可用 await)
    python3 tools/live.py check claims.json vNNN  # 逐條驗證「我宣稱的事」,印 PASS/FAIL

金鑰從環境變數拿,不寫在檔案裡:VIEW_PASSWORD / SYNC_KEY / HISTORY_KEY。
沙盒只連得到 raw.githubusercontent.com(api.github.com 被擋),所以一律走 raw。
"""
import base64, json, os, subprocess, sys, tempfile, urllib.request

REPO = "chiehw86/portfolio-dash"
RAW = f"https://raw.githubusercontent.com/{REPO}"
ENC = {"overlay": ("main", "overlay.enc", "sync"),
       "px":      ("main", "data/px.enc", "sync"),
       "nav":     ("main", "data/nav-cache.enc", "sync"),
       "history": ("main", "data/history.enc", "fernet"),
       "statement": ("main", "data/statement.enc", "sync")}
CHROME = "/opt/pw-browsers/chromium"


def _get(path, ref="main"):
    with urllib.request.urlopen(f"{RAW}/{ref}/{path}", timeout=30) as r:
        return r.read()


def _need(var):
    v = os.environ.get(var, "").strip()
    if not v:
        sys.exit(f"請先設定環境變數 {var}(金鑰不寫進檔案)")
    return v


def dec_sync(blob):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    import hashlib
    raw = base64.b64decode(blob.decode().strip())
    key = hashlib.sha256(_need("SYNC_KEY").encode()).digest()
    return AESGCM(key).decrypt(raw[:12], raw[12:], None).decode()


def dec_fernet(blob):
    from cryptography.fernet import Fernet
    return Fernet(_need("HISTORY_KEY").encode()).decrypt(blob.strip()).decode()


def load_enc(name):
    ref, path, how = ENC[name]
    txt = (dec_sync if how == "sync" else dec_fernet)(_get(path, ref))
    try:
        return json.loads(txt)
    except Exception:
        return txt                      # history 是 CSV


# ── 在「解密後的線上頁面」上跑 JS ──────────────────────────────────────────
_JS = r"""
const { chromium } = require('playwright');
const fs = require('fs');
(async () => {
  const b = await chromium.launch({ executablePath: process.env.CHROME });
  const p = await b.newPage({ viewport: {width: 1500, height: 1400} });
  const errs = []; p.on('pageerror', e => errs.push(String(e)));
  await p.goto('file://' + process.env.PAGE);
  await p.waitForTimeout(1500);
  await p.fill('#staticrypt-password', process.env.VIEW_PASSWORD);
  await p.evaluate(() => document.getElementById('staticrypt-form')
    .dispatchEvent(new Event('submit', {cancelable: true})));
  await p.waitForTimeout(6500);
  const body = fs.readFileSync(process.env.SNIPPET, 'utf8');
  let out;
  try {
    out = await p.evaluate(new Function('EXTRA', 'return (async () => {' + body + '})()'),
                           JSON.parse(process.env.EXTRA || 'null'));
  } catch (e) { out = {__error: String(e)}; }
  console.log(JSON.stringify({build: (document => null)(0) ?? null, result: out, pageErrors: errs.slice(0,5)},
    null, 1));
  await b.close();
})();
"""


def _page():
    """把線上 gh-pages 的 index.html 抓下來存成暫存檔,回傳路徑。"""
    d = tempfile.mkdtemp(prefix="live_")
    fp = os.path.join(d, "index.html")
    open(fp, "wb").write(_get("index.html", "gh-pages"))
    return fp


def run_js(body, extra=None):
    fp = _page()
    d = os.path.dirname(fp)
    open(os.path.join(d, "run.js"), "w").write(_JS)
    open(os.path.join(d, "snippet.js"), "w").write(body)
    env = dict(os.environ, CHROME=CHROME, PAGE=fp,
               SNIPPET=os.path.join(d, "snippet.js"),
               VIEW_PASSWORD=_need("VIEW_PASSWORD"),
               EXTRA=json.dumps(extra) if extra is not None else "null")
    r = subprocess.run(["node", os.path.join(d, "run.js")],
                       capture_output=True, text=True, env=env,
                       cwd="/home/claude/work")
    if r.returncode:
        sys.exit("頁面執行失敗:\n" + (r.stderr or "")[-2000:])
    return json.loads(r.stdout)


def cmd_build():
    o = run_js("const m = document.body.innerText.match(/建置 v\\d+ · \\w+/);"
               "const t = document.body.innerText.match(/報價時間:[^·]+/);"
               "return {build: m && m[0], asof: t && t[0].trim(),"
               " positions: nonDup().length, total: Math.round(nonDup().reduce((s,p)=>s+effMv(p),0))};")
    print(json.dumps(o["result"], ensure_ascii=False, indent=1))
    if o["pageErrors"]:
        print("頁面 JS 錯誤:", o["pageErrors"])


def cmd_check(path, build=None):
    """claims.json:[{"name": "...", "js": "return <布林運算式>", "want": true}, …]

    檔案裡的 `__BUILD__` 會被第二個參數取代,版本號才不會寫死在斷言裡
    (2026-09-10 實測踩到:claims 裡寫死 v131,交付 v132 之後那條就永遠 FAIL)。
    """
    txt = open(path, encoding="utf-8").read()
    if build:
        txt = txt.replace("__BUILD__", build)
    elif "__BUILD__" in txt:
        sys.exit("這份 claims 需要版本號:python3 tools/live.py check <檔> <vNNN>")
    claims = json.loads(txt)
    body = ("const out = [];"
            "for (const c of EXTRA) {"
            "  let v, err = null;"
            "  try { v = await (new Function('return (async () => {' + c.js + '})()'))(); }"
            "  catch (e) { err = String(e); }"
            "  out.push({name: c.name, got: v, want: c.want, ok: JSON.stringify(v) === JSON.stringify(c.want), err});"
            "} return out;")
    o = run_js(body, claims)
    rows = o["result"]
    if isinstance(rows, dict) and rows.get("__error"):
        sys.exit("執行失敗:" + rows["__error"])
    bad = 0
    for r in rows:
        mark = "  ok   " if r["ok"] else "  FAIL "
        print(f"{mark}{r['name']}")
        if not r["ok"]:
            bad += 1
            print(f"         實際={json.dumps(r['got'], ensure_ascii=False)} "
                  f"期望={json.dumps(r['want'], ensure_ascii=False)}"
                  + (f" 錯誤={r['err']}" if r["err"] else ""))
    print(("\nPASS(全部與線上相符)" if not bad else f"\nFAIL:{bad} 條與線上不符"))
    sys.exit(1 if bad else 0)


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    c = sys.argv[1]
    if c == "build":
        cmd_build()
    elif c == "enc":
        name = sys.argv[2]
        d = load_enc(name)
        if isinstance(d, str):
            lines = d.strip().split("\n")
            print(f"{name}: {len(lines)-1} 列 · 首列 {lines[1][:19]} · 末列 {lines[-1][:19]}")
        else:
            print(json.dumps(d, ensure_ascii=False)[:4000])
    elif c == "eval":
        print(json.dumps(run_js(sys.argv[2])["result"], ensure_ascii=False, indent=1))
    elif c == "evalfile":
        print(json.dumps(run_js(open(sys.argv[2], encoding="utf-8").read())["result"],
                         ensure_ascii=False, indent=1))
    elif c == "check":
        cmd_check(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None)
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
