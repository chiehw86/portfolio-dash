#!/usr/bin/env python3
"""v3:Chieh 實際組合 dashboard(USD/K)。讀 portfolio.json + quotes3.json(+ 可選 units.json),
上市部位以隱含股數連動現價,基金/PE 為靜態 NAV。輸出 dashboard.html 並回寫 units.json。"""
import json, os, datetime, sys, math
# 未捕捉的例外預設會把訊息(可能含股數/金額)印進公開的 Actions log:只留型別。
def _quiet(t, v, tb): print(f"build: 中止({t.__name__})", file=sys.stderr)
sys.excepthook = _quiet

P = json.load(open("portfolio.json"))
Q = json.load(open("quotes3.json"))

# ── 最近一次結算的時刻 ───────────────────────────────────────────────────
# 「今日變動」不能把已經結算過的那一場再算一次。以前只擋「該市場今天沒開盤」,
# 漏掉美股國定假日隔天:最後一場落在前一次結算之前,於是同一段漲跌被算兩遍
# (2026-09-08 那次 +36.7K)。改成用場次的實際收盤時刻跟結算時刻比 ——
# 這條規則同時涵蓋亞股開盤前那種情況,而且不必知道是不是國定假日。
_SETTLE = None
try:
    _h = json.load(open("history.json")) if os.path.exists("history.json") else []
    if _h:
        _SETTLE = datetime.datetime.strptime(_h[-1]["t"][:16], "%Y-%m-%d %H:%M").replace(
            tzinfo=datetime.timezone(datetime.timedelta(hours=8)))     # 結算時戳是台北時間
except Exception:
    _SETTLE = None


def _settled(q):
    """這筆報價代表的場次,是不是在最近一次結算之前就收盤了?"""
    _ae = q.get("asof_end")
    if not (_SETTLE and _ae):
        return False
    try:
        return datetime.datetime.fromisoformat(_ae) <= _SETTLE
    except Exception:
        return False
# 已出清部位的報價:這些代號 fetch_action 有一併抓,但它們不掛在任何持倉上,
# 前端拿不到,得由建置端交出去。
#
# 關鍵:報價「不可以」寫回 closed_ytd 的項目裡。closed_ytd 是網頁會存回覆寫檔的
# 結構,一旦把 now 塞進項目,瀏覽器下次儲存就把那個價格一起寫進 overlay.enc,
# 之後每次載入都用覆寫檔的舊值蓋掉新抓的報價(實測發生過:某檔的出清價凍住不動,
# 新加的 chg 欄永遠出不來,因為項目被覆寫檔整個換掉)。
# 所以報價走一個獨立的 __CLOSEDQ__(以代號為鍵),並在這裡把舊版留下的
# now/chg/qcur 清掉,讓瀏覽器下次儲存時順手把覆寫檔洗乾淨。
# ── 已出清 / 減碼紀錄 ───────────────────────────────────────────────
# 這兩份紀錄住在 overlay.enc(網頁存回去的加密覆寫檔)裡,建置端只做清理與去重。
# 2026-09-08 資安複審:以前把「補登的出清」與「回推的減碼」當種子寫死在這支腳本,
# 而這支腳本以 base64 內嵌在公開 repo 的 build.yml —— 等於把代號、股數、出場價、
# 已實現損益明文放在公開網址上,整套加密白做。種子已全部進了 overlay.enc,
# 這裡不再留任何持倉資料;要作廢某筆出清,用雜湊(見 CLOSED_DROP)而不是寫名字。
def _fnv(s):
    """FNV-1a 32 位元。用途只是「同一筆紀錄的識別碼」,不是密碼學雜湊;
    前端 v3.js 有一模一樣的實作,兩邊算出的值必須相同。"""
    h = 0x811c9dc5
    for _b in str(s).encode("utf-8"):
        h ^= _b
        h = (h * 0x01000193) & 0xFFFFFFFF
    return format(h, "08x")

def _drop_key(c):
    try: _k = math.floor(float(c.get("usd_k") or 0) + 0.5)   # 與 v3.js 的 Math.floor(x+0.5) 一致(Python round 是四捨六入五成雙)
    except (TypeError, ValueError): _k = 0
    return _fnv(f"{c.get('name')}|{c.get('on')}|{_k}")

# 作廢清單:name|出清日|金額(整數) 的 FNV 雜湊。舊裝置的本機副本可能還留著這些
# 錯誤紀錄,合併時要靠這份清單擋掉,所以不能只從覆寫檔刪除。
CLOSED_DROP = ["7742743e"]          # 2026-09-02 那筆錯誤出清(值已預先算好,不留名字)
_dropped = set(CLOSED_DROP) | {str(x) for x in (P.get("dropped") or []) if isinstance(x, str)}
CLOSED_DROP = sorted(_dropped)
_cy = [c for c in (P.get("closed_ytd") or [])
       if isinstance(c, dict) and _drop_key(c) not in _dropped]
P["closed_ytd"] = _cy
P["dropped"] = CLOSED_DROP

CLOSEDQ = {}
for _c in (P.get("closed_ytd") or []):
    for _k in ("now", "chg", "qcur"):
        _c.pop(_k, None)
    _t = _c.get("ticker")
    _q = (Q.get("quotes") or {}).get(_t) if _t else None
    if _q and _q.get("price"):
        CLOSEDQ[_t] = {"price": _q["price"], "chg": _q.get("change_pct"),
                       "cur": _q.get("cur"), "note": _q.get("note")}

TRIM_KEYS = ("name", "ticker", "cur", "code", "on", "to", "u0", "u1",
             "exit", "est", "real_k", "src")
_tr = [ {k: t[k] for k in TRIM_KEYS if k in t}
        for t in (P.get("trims") or []) if isinstance(t, dict) ]
_seen = set(); _uniq = []
for _t in _tr:                       # 同一筆(name, code, on, to)只留一份;拆分列同 code 不同 name,各留各的
    _k = (_t.get("name"), _t.get("code"), _t.get("on"), _t.get("to"))
    if _k in _seen: continue
    _seen.add(_k); _uniq.append(_t)
P["trims"] = _uniq
# 減碼的標的多半還在持倉裡,但報價一樣走 __CLOSEDQ__:不能寫進 trims 項目,
# 否則會被瀏覽器存回覆寫檔而凍住(closed_ytd 踩過這個坑)。
for _t in P["trims"]:
    _tk = _t.get("ticker")
    _q = (Q.get("quotes") or {}).get(_tk) if _tk else None
    if _q and _q.get("price") and _tk not in CLOSEDQ:
        CLOSEDQ[_tk] = {"price": _q["price"], "chg": _q.get("change_pct"),
                        "cur": _q.get("cur"), "note": _q.get("note")}

# ── 建置版本標記 ──────────────────────────────────────────────────────
# 「我貼上去的那一份到底生效了沒有」,以前只能繞去 GitHub 比檔案大小。
# 版本號由交付 yml 時手動遞增;後面六碼是四個腳本內容的雜湊,
# 就算版本號忘了改也會跟著變,所以它不會說謊。
BUILD_TAG = "v134"
import hashlib as _hl
_sig = _hl.md5(b"".join(
    open(_f, "rb").read()
    for _f in ("v3.js", "v3.css", "build_dashboard_v3.py", "fetch_action.py")
    if os.path.exists(_f))).hexdigest()[:6]

fxu = Q["fx_usd"]
units_store = json.load(open("units.json")) if os.path.exists("units.json") else {}
TODAY_TPE = (datetime.datetime.now(datetime.timezone.utc)
             + datetime.timedelta(hours=8)).strftime("%Y-%m-%d")

# 為 live 部位計算/沿用隱含股數;把報價與衍生欄位塞進 position
for reg in P["regions"]:
    for g in reg["groups"]:
        for p in g["positions"]:
            t = p.get("ticker")
            q = Q["quotes"].get(t) if t else None
            if q and p.get("kind") == "live" and fxu.get(q["cur"]):
                fx = fxu[q["cur"]]
                price_usd = q["price"] / fx
                w = p.get("wgt")          # 拆分列:本列佔該 ticker 全部部位的比重
                um = p.get("units_manual")
                if um not in (None, ""):  # 手動輸入的股數優先,且不參與隱含股數推算
                    u = float(um)
                else:
                    if t not in units_store:   # 首次:用「該 ticker 的完整部位市值」÷ 現價
                        base_mv = p.get("mv")
                        if base_mv in (None, ""):
                            continue           # 新部位未填股數也沒市值 → 無從計算,略過
                        units_store[t] = (base_mv / w if w else base_mv) * 1000.0 / price_usd
                    u = units_store[t] * (w if w else 1.0)
                # 前收覆寫:韓股等市場的免費資料源只取盤中收盤價,Bloomberg 含盤後故基準不同。
                # 需帶當日日期才生效,避免隔天沿用到過期的覆寫值。
                ov = p.get("prev_override")
                if ov and ov.get("date") == TODAY_TPE:
                    prev_usd = ov["value"] / fx
                elif q.get("change_pct") is None or q["change_pct"] != q["change_pct"] \
                        or q["change_pct"] <= -100:
                    # None:抓價端判定前收不可信。NaN:通得過 is None 卻會污染每個加總。
                    # -100:1 + (-100)/100 = 0,除以零會讓整輪建置掛掉。
                    # 抓價那一步判定前收不可信(日線缺了上一場)。這裡不能硬湊一個
                    # 數字:寧可讓「今日」顯示 —,也不要把兩天的漲跌報成今天的。
                    prev_usd = None
                else:
                    prev_usd = price_usd / (1 + q["change_pct"] / 100.0)
                p["q"] = {
                    "price": q["price"], "cur": q["cur"],
                    # 前收有兩種說法時把兩個候選值都帶到前端(滑鼠移上去看得到),
                    # 隔天早上的檢查才能拿外部行情判定哪一個是對的。
                    "prev_bar": q.get("prev_bar"), "prev_quote": q.get("prev_quote"),
                    "chg": None if prev_usd is None else (price_usd / prev_usd - 1) * 100,
                    "chg_src": "override" if (ov and ov.get("date") == TODAY_TPE) else "feed",
                    "live": q["live"], "qnote": q.get("note", "盤中" if q["live"] else "收盤"),
                    # 今天這一場還沒開始:漲跌幅照顯示(前一場的),但不進今日加總
                    "pending": bool(q.get("pending_open")),
                    # 這筆報價實際上是哪一場次。抓價端算出來了卻沒人帶到前端 ——
                    # 每天早上那次外部行情比對需要它才能判斷資料源有沒有落後。
                    "asof": q.get("asof"),
                    # 價格帳本記的前收。有值就代表前收是查來的、不是每輪重推的 ——
                    # 早上的檢查要靠它區分「帳本擋下了一次資料源出錯」與「沒擋到」。
                    "prev_book": q.get("prev_book"),
                    # 這一場已經結算過了:漲跌幅照顯示,但不再進今日加總
                    "settled": _settled(q),
                    "units": round(u, 2),
                    "mv_live": round(u * price_usd / 1000.0, 1),
                    "day_k": (0.0 if (prev_usd is None or q.get("pending_open")
                                      or _settled(q))
                              else round(u * (price_usd - prev_usd) / 1000.0, 1)),
                }
json.dump(units_store, open("units.json", "w"), indent=1)


def j(obj):
    """序列化成可安全嵌進 <script> 的 JSON。

    json.dumps 不會轉義 "</script>" —— 而 P["regions"] 的內容來自公開 repo 上的
    overlay.enc,任何拿到觀看密碼(因而拿到頁面內的 SYNC_KEY)的人都能改。
    部位名稱塞一個 </script> 就能在已解密的頁面上取得持久化的 JS 執行權。
    轉義 < 即可,\u003c 在 JSON 裡等價,瀏覽器解析 script 時也不會提前結束標籤。
    """
    return json.dumps(obj, ensure_ascii=False).replace("<", "\\u003c")


def n(x, d=2): return f"{x:,.{d}f}"
def _tile(ix):
    pc, bp = ix["change_pct"], ix.get("chg_bp")
    if bp is not None:                                  # 殖利率:變動以基點表示
        c = "up" if bp >= 0 else "down"
        chg = f'<span class="{c}">{"+" if bp >= 0 else ""}{bp} bp</span>'
    elif pc is not None:
        c = "up" if pc >= 0 else "down"
        chg = f'<span class="{c}">{"+" if pc >= 0 else ""}{pc:.2f}%</span>'
    else:
        chg = '<span class="mut">—</span>'
    dp = ix.get("dp", 2)
    return (f'<div class="tile sm"><div class="t">{ix["name"]}'
            f'<span class="badge {"lv" if ix["live"] else "st"}">{ix["note"]}</span></div>'
            f'<div class="v sm2">{ix["price"]:,.{dp}f}{ix.get("unit", "")}</div><div class="d">{chg}</div></div>')

itiles = "".join(_tile(ix) for ix in Q["indices"] if ix.get("cat", "idx") == "idx")
ctiles = "".join(_tile(ix) for ix in Q["indices"] if ix.get("cat") == "cmd")
ytiles = "".join(_tile(ix) for ix in Q["indices"] if ix.get("cat") == "yld")
fxtiles = "".join(
    f'<div class="tile sm"><div class="t">{k}/USD</div><div class="v sm2">{n(v,2)}</div></div>'
    for k, v in fxu.items() if k not in ("USD", "as_of", "source"))

HIST = json.load(open("history.json")) if os.path.exists("history.json") else []
# 同步設定由環境變數注入(值來自 repo secret);只會出現在已加密的 HTML,
# 公開的 workflow 原始碼看不到。未設定時 SYNC=None,頁面就只用本機儲存。
SYNC = None
if os.environ.get("SYNC_TOKEN") and os.environ.get("SYNC_KEY"):
    SYNC = {"token": os.environ["SYNC_TOKEN"], "key": os.environ["SYNC_KEY"],
            "repo": os.environ.get("GITHUB_REPOSITORY", "chiehw86/portfolio-dash"),
            "path": "overlay.enc", "branch": "main"}
CSS = open("v3.css").read()
JS = open("v3.js").read()

html = ("""<!DOCTYPE html>
<html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; connect-src https://api.github.com; base-uri 'none'; form-action 'none'">
<title>投資組合 Dashboard</title>
<meta name="theme-color" content="#0f172a">
<link rel="icon" type="image/png" href="data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAEAAAABACAIAAAAlC+aJAAABMUlEQVR42u3ZsQ6CMBDG8UIIq6MbkxMmvv9zmOjk5GM4OTQhBiht7670Tr5OxDj8f7TUEpvTeXSWR+uMDwAAAAAAAAAAAIAjAzrlffd+8Be3z9veDEz1s2sbgGXxqgEPMQCJ6yf0HLem6zVuo7P6ULeNZyBarw7we/tT6nUBQj9VNgC5S18XgFyvAsCprw9g1lcG8OtrAkTqqwGk6usABOtlAD6I9jPErHfONcx/aJbd202Ew0Kpl/rQLfefr8bRZqnIDKSnTBLZpc8ChF44CKoKgOiijzIE6/MAWW96hO+XBZBrxLcdCoB/L+/9UKI+CZC70+88uqI3fofRmq7fAixbFdanHiV0psdPo75bc73AaVT1DAAAwBEA69voZbwqbH09H384A9hGAQAAAAAAMD2+Obd63p9NicoAAAAASUVORK5CYII=">
<link rel="apple-touch-icon" href="data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAALQAAAC0CAIAAACyr5FlAAADFUlEQVR42u3du2obURCAYdkItynTuUrlgN//OQxJlUqP4SpdCEGK5T3Xmfn+LhCE0Pk0e9F69+HL15eTdK1HH4HgEByCQ3AIDsEhOASH4BAcEhyCQ3AIDsEhOASH4BAcgkOCQ3AIDsEhOASH4BAcgkNwSHAIDsEhOASH4BAcCtbZRxC9t6fnv//5+n7p9coP7kOahkV3InBkY9GRiH2OtDI+9T/hEBzGRqfhAYfgKDk2GocHHIJDcAgOwaEe+5WHz5PCkVyGyUFG/7EBh5kBR1UZjb/KutjH1gSOMnW8EgyOJGOjown7HGTAQQYcZMBBxjwZcJABBxlwCA5j4wIHGStlwEEGHGTAQQYcZMBBxlIZcAgOYwMOMuAgAw4y4CBjTxlwkAEHGXAIDmMDDjLgIGNPGXCQAQcZcJABBxlwCA5jY9vOyRZm/ueeVcYp9GO8bq3KzE8/sYzAOD5clQnLkFtGVBz3r8q49UgvIx6OY3df7L4wFWQEO1o5fF/OvjcBLiIjEo7GBX57el54n+igxdis9F3Xlq9ynbERA8egb/yBZSslIwCO0duC+9evmoytcczcRfhwIQvK2BfHkp3HWytaU8amONYeVvyztGVl7Iij/TRXu60/r1ZZxmm38xxdToC+vl8aF8lJkb0mx4jnYk5e4GRjYxccQ5+YOodIPhlb4JjzLN2hRFLKWI9j8q+sI4hklbEYx6rf3zsSSSxjGY75j2UfQSS3jDU4dpCx55vZrcfKMk49TorAkfxr+tnXL+Jp3mZlk8s/299knUkzCUcIGfe821LboOE44u7xLf9buuQ4HAvYISUjZ+etZGCRf3KQYXLYlJgcZMBBhgYeyha/HNfk6LbYZBQ9WiEDjoOrTkbdyfH/tSej+mbllgAyYjXpPqRYmBzXNZABx3UfZMQt8B2MFXhyCA7BITgkOASH4BAcgkNwCA7BIThUp6aLfb69fPcJ7t+vnz9MDtmsCA7BITgUMNeQyuQQHIJDcAgOwSE4BIfgEBwSHIJDcAgOwSE4BIfgEByCQ4JDcAgOwSE4tEe/AcmPXWpuUALfAAAAAElFTkSuQmCC">
<style>""" + CSS + """</style></head><body><div class="wrap">
<h1>投資組合 Dashboard</h1>
<div class="meta">報價時間:""" + Q["fetched_at_taipei"] + """ 台北時間 · 自動更新:亞股 08:00–13:30 每半小時 + 14:00,美股 21:30–00:00 每半小時 + 04:00 收盤 · 單位 USD 千元 · 紅漲綠跌 · 上市部位依隱含股數連動現價,基金/PE 為報表 NAV<span title="建置版本。貼上新的 build.yml 重跑之後這裡會跟著變;沒變就是還沒生效,或瀏覽器還在給快取(強制重新整理一次)。"> · 建置 """ + BUILD_TAG + " · " + _sig + """</span></div>
<nav id="topnav"></nav>
<!-- 常駐警示區。刻意放在 #regions 之外:render() 會整份重寫 #regions.innerHTML,
     以前唯一一處「告訴使用者資料被丟了」的訊息就住在那裡面,按任何按鈕就被洗掉。 -->
<div id="alerts"></div>
<div class="tiles" id="kpis"></div>
<div class="card" id="alloccard"><h2>配置分析(不含重複列示)</h2>
<div class="allocblk"><div class="rt">地區配置 <span class="mut">以底層資產所在國家為準,非掛牌地</span></div>
<div id="alloc"></div><div class="legend" id="alloclegend"></div></div>
<div class="allocblk"><div class="rt">主題配置 <span class="mut">以策略主題為準,可跨地區</span></div>
<div id="alloc2"></div><div class="legend" id="alloclegend2"></div></div>
<div class="note" style="margin-top:12px">兩個維度各自加總 100%,分母同為總資產(不含重複列示)。
<b>地區</b>看底層資產所在國家而非掛牌地:美元 ETF 依成分拆成的地區子列各歸其地,海外掛牌的 ADR 依公司所在國歸類;沒有代號的 PE 與 Activist 依所屬區歸類。
<b>主題</b>可跨地區:重工類個股計入國防、在半導體區重複列示的個股計入半導體;整組合計的主動基金沒有逐檔對應,維持原類。
要調整歸屬可在部位上填 geo / theme 欄位覆寫。</div></div>
<div class="card" id="riskcard"></div>
<div id="regions"></div>
<div class="card" id="closedcard"></div>
<div class="card" id="histcard"><h2>資產走勢(USD K)</h2><div id="histchart"></div></div>
<div class="card" id="fxcard"><h2>外幣曝險與避險比率</h2><div id="fxexp"></div></div>
<div class="card"><h2>YTD 損益主要貢獻(USD K,前 12 大)</h2><div id="plchart"></div></div>
<h2 style="margin:20px 0 10px" id="tilesec">市場指數</h2>
<div class="tiles">""" + itiles + """</div>
""" + (('<h2 style="margin:4px 0 10px">大宗商品與加密貨幣</h2><div class="tiles">' + ctiles + '</div>') if ctiles else "") + """
""" + (('<h2 style="margin:4px 0 10px">公債殖利率</h2><div class="tiles">' + ytiles + '</div>') if ytiles else "") + """
<h2 style="margin:4px 0 10px">匯率(1 USD 兌)</h2>
<div class="tiles">""" + fxtiles + """</div>
<a id="toTop" href="#" title="回頂端" onclick="window.scrollTo({top:0,behavior:'smooth'});return false">↑</a>
<div class="card note" id="footnote">
資料來源:上市部位報價取自 Yahoo Finance(公開網站版);台灣境內基金淨值取自鉅亨網(每日,T+1);匯率取自 Yahoo Finance(USDxxx=X),抓不到時沿用 bundle 內的備援值。
「隱含股數」= 報表市值 ÷ 建置日現價,之後市值隨現價連動;「NAV/靜態」部位維持報表數字,待你提供新報表更新。
「報價待接」= 已識別代號、將由排程逐步接上報價。標示 <b>dup</b> 的列為跨區重複列示,不計入總計。<br>
<b>拆分列口徑</b>:成分跨多國的 ETF 依持股明細拆為地區子列,權重取自官方揭露與第三方持股資料;子列共用同一個部位,故漲跌幅相同、市值合計等於原部位。
鏡像列(如「Japan Defense」)為另一區成分的連動合計,已標 dup 不重複計入。<br>
台灣境內主動型基金已接每日公開淨值,其「今日」為單日淨值變動;若你持有的是其他級別請告知。
「NAV/靜態」僅剩海外基金、Activist 與 PE 部位(無公開淨值來源,依報表 NAV)。
<b>韓股日漲跌幅基準</b>:Google/Yahoo/Investing 等來源一致採「盤中收盤價」為前收;Bloomberg 的參考價含盤後單一價時段,故日漲跌幅會不同 —— <b>市值不受影響</b>。
若某日需對齊 Bloomberg,可在該部位設 <code>prev_override</code>(需帶當日日期,隔日自動失效)。<br>
<b>YTD 口徑</b>:僅計算<b>目前仍持有</b>的部位。有對帳單資料的部位即時計算 —— (現市值 − 累積成本 + 累積已實現) 減去 2025-12-31 的累積損益;今年才買進的部位年初基準為 0。Activist、PE 與海外基金無逐日淨值,沿用報表 YTD。報酬率分母為<b>年初市值 + 年內買進成本</b>(年內減碼賣掉那部分的成本依減碼紀錄加回),與券商報表同口徑 —— 只用年初市值的話,年內大幅加碼的部位分母會偏小、報酬率虛高;只用剩餘成本的話,買進後又減碼的部位分母會縮水、報酬率被放大。群組小計與 KPI 的報酬率皆為 Σ損益 ÷ Σ分母。年內已清倉的部位以「已出清」卡片另計。本口徑以當地幣計算損益後換算美元,<b>不含本金的匯率換算損益</b>,故與券商報表的 YTD 不會一致。<br><b>與券商對帳單的差異</b>:股數與成本依對帳單校準;對帳單上另有少數未納入本表的項目(債券 ETF、黃金與一檔無報價的港股),故此處總資產略低於對帳單。<br>報表基準日待確認。非投資建議。
</div>
</div>
<div id="tip"></div>
<script>window.__P__ = """ + j(P) + """;
window.__FX__ = """ + j({k: v for k, v in fxu.items() if isinstance(v, (int, float))}) + """;
window.__HIST__ = """ + j(HIST) + """;
window.__SYNC__ = """ + j(SYNC) + """;
window.__CLOSEDQ__ = """ + j(CLOSEDQ) + """;
window.__CLOSEDDROP__ = """ + j(CLOSED_DROP) + """;</script>
<script>""" + JS + """</script>
</body></html>""")

# index.html 的長度 = dashboard.html 長度的線性函數,而後者每建置一次就 +160 bytes
# (歷史多一筆)、每新增一個部位 +800 bytes。任何人對公開網址發一個 HEAD 就能
# 逐輪量到這條曲線,等於免密碼觀測「你何時動了持倉、加了幾檔」。
# 補到 64KB 級距後,只剩「落在哪個級距」。
_B = 65536
_need = (-(len(html.encode()) + 8)) % _B          # "\n" + "<!--" + "-->" = 8 bytes
html += "\n<!--" + "0" * _need + "-->"
open("dashboard.html", "w").write(html)
print("built ok")
