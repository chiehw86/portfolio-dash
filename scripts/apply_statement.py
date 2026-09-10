#!/usr/bin/env python3
"""伺服器端套用券商對帳單。

流程:把最新的券商對帳單「加密後」放到 repo main 的 data/statement.enc,這一步就會
自動比對並套用,不必開網頁。套用結果寫回 overlay.enc —— 也就是網頁版匯入寫的同一個
地方,所以所有裝置都會看到,而且下一輪建置照樣生效。

加密方式與 overlay.enc 相同(AES-GCM,SYNC_KEY):
    SYNC_KEY=... python sync_crypto.py enc-file < FMGAR140.xlsx > statement.enc
repo 是公開的:明文的 data/statement.xlsx 一律拒收並讓建置失敗(紅字才會有人看到),
絕不能讓一份放錯位置的對帳單被靜靜地讀進來、還留在 git 歷史裡。

規則與網頁版完全一致(v3.js 的 stmtDiff/apply):
  * 用 stmt_code 對應,可多對一(同一檔在兩個帳戶)
  * 拆分列依 wgt 分配股數
  * 對帳單幣別與報價幣別不同者依 stmt_cur 換算
  * 靜態 NAV 部位更新市值而不是股數
  * 對帳單股數為 0 或整筆消失 → 移除該部位(含跨區鏡像列)

三個安全閘:
  1. 同一份檔案不重複套用(比對內容雜湊)
  2. 一次移除超過 1/3 且達 3 個以上 → 中止,不套用(可能只匯出了部分帳戶)
  3. 任何解析失敗 → 中止本輪,不寫任何東西
"""
import base64, hashlib, json, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sync_crypto import decrypt, encrypt, gh_read, gh_write, pad_json

# 未捕捉的例外預設會把訊息印進公開的 Actions log —— 對帳單的儲存格內容(金額、股數)
# 會跟著 ValueError 一起出現。只留型別。
def _quiet(t, v, tb): print(f"statement: 中止({t.__name__})", file=sys.stderr)
sys.excepthook = _quiet

STMT_PATH = "data/statement.enc"
STMT_PLAIN = "data/statement.xlsx"
MARK_PATH = "data/statement-applied.txt"     # 記錄已套用過的檔案雜湊
OVERLAY = "overlay.enc"
SK = os.environ.get("SYNC_KEY", "")


def parse(blob):
    import io, openpyxl
    ws = openpyxl.load_workbook(io.BytesIO(blob), data_only=True, read_only=True).worksheets[0]
    rows = []
    for r in ws.iter_rows(values_only=True):
        rows.append(list(r))
        if len(rows) > 5000:                 # 對帳單頂多幾百列;更多就是別的東西
            raise ValueError("too many rows")
    norm = lambda x: str(x).replace("\n", "").replace(" ", "") if x is not None else ""
    hi = next((i for i, r in enumerate(rows)
               if any(norm(c).startswith("股票代碼") for c in r)
               and any(norm(c).startswith("累積成本") for c in r)), -1)
    if hi < 0:
        raise ValueError("找不到表頭(需有「股票代碼」與「累積成本」)")
    col = {}
    for j, c in enumerate(rows[hi]):
        h = norm(c)
        for k, n in (("幣別", "cur"), ("股票代碼", "code"), ("股票名稱", "name"),
                     ("帳上庫存餘額", "sh"), ("當日市值", "mv"),
                     ("累積成本", "cost"), ("累積已實現", "real"),
                     ("BreakevenReturn", "be")):
            if h.startswith(k) and n not in col:
                col[n] = j
    recs = {}
    # 儲存格若是文字(千分位、"--"),float() 會連同內容一起丟例外;一律當 0 / None
    def _f(v, default=0.0):
        # 空格就當預設;是文字(千分位、"--")就中止 —— 悄悄當 0 會把市值 0 套進部位並推到雲端。
        # excepthook 只印型別,儲存格內容不會進公開 log。
        if v is None or v == "": return default
        if isinstance(v, (int, float)) and not isinstance(v, bool): return float(v)
        raise ValueError("non-numeric cell")
    for r in rows[hi + 1:]:
        code = r[col["code"]] if col.get("code") is not None else None
        sh = r[col["sh"]] if col.get("sh") is not None else None
        if not code or not isinstance(sh, (int, float)):
            continue
        recs[str(code).strip()[:40]] = {
            "cur": str(r[col["cur"]]).strip().upper()[:8], "sh": float(sh),
            "cost": _f(r[col["cost"]]), "mv": _f(r[col["mv"]]),
            "real": _f(r[col["real"]]) if col.get("real") is not None else 0.0,
            "be": _f(r[col["be"]], None) if col.get("be") is not None else None,
        }
    if not recs:
        raise ValueError("表頭找到了,但沒有任何資料列")
    return recs


def main():
    if not SK:
        print("statement: 未設定 SYNC_KEY,略過"); return
    # 明文對帳單一律拒收:這是公開 repo,而且檔案一旦 commit 就永遠留在歷史裡。
    # 用中止(非零)而不是略過,讓 GitHub 為失敗的排程寄信 —— 那是唯一的告警管道。
    if gh_read(STMT_PLAIN) is not None:
        print("::error::statement: 偵測到明文對帳單,拒絕讀取。請改放加密的 data/statement.enc,"
              "並把明文從 repo(含歷史)移除")
        raise SystemExit(1)
    blob = gh_read(STMT_PATH)
    if blob is None:
        print("statement: 尚未放入 data/statement.enc,略過"); return
    try:
        _p = decrypt(SK, blob.decode().strip())
        blob = base64.b64decode(json.loads(_p)["b64"] if _p.lstrip().startswith("{") else _p)
    except Exception as e:
        print(f"statement: 對帳單解不開({type(e).__name__}),略過"); return

    if len(blob) > 5_000_000:
        print("statement: 檔案過大,略過"); return
    digest = hashlib.sha256(blob).hexdigest()[:16]
    mark = gh_read(MARK_PATH)
    if mark and mark.decode().strip() == digest:
        print("statement: 與上次套用的是同一份,略過"); return

    recs = parse(blob)

    # 以「目前這一輪合併後的持倉」為基礎(merge_overlay 已經跑過)
    P = json.load(open("portfolio.json"))
    _Q = json.load(open("quotes3.json")) if os.path.exists("quotes3.json") else {}
    FX = _Q.get("fx_usd", {})
    _QT = _Q.get("quotes", {})
    if not FX:
        print("statement: 這一輪還沒有匯率,等下一輪再套用"); return

    import datetime as _dt0
    _today_tpe = (_dt0.datetime.now(_dt0.timezone.utc)
                  + _dt0.timedelta(hours=8)).strftime("%Y-%m-%d")

    changed, drop, need_fx = 0, [], []
    for r in P["regions"]:
        for g in r["groups"]:
            for p in g["positions"]:
                codes = p.get("stmt_code")
                if not codes:
                    continue
                hits = [recs[c] for c in codes if c in recs]
                if not hits or not sum(h["sh"] for h in hits):
                    drop.append(p); continue
                if p.get("kind") != "live":                    # 靜態 NAV:更新市值
                    if any(h["cur"] not in FX for h in hits):
                        need_fx.append(p.get("name")); continue
                    mv = round(sum(h["mv"] / FX[h["cur"]] for h in hits), 1)
                    _bv = [h for h in hits if h.get("be") is not None and h["cost"]]
                    if _bv:
                        _be = round(sum(h["be"] * h["cost"] for h in _bv)
                                    / sum(h["cost"] for h in _bv) * 100, 1)
                        if p.get("be") is None or abs(_be - p["be"]) > 0.05:
                            p["be"] = _be; changed += 1
                    if p.get("mv") is None or abs(p["mv"] - mv) > 0.5:
                        p["mv"] = mv; changed += 1
                        import datetime as _dt          # 蓋「資料截至」章,前端 badge 會顯示
                        p["nav_asof"] = (_dt.datetime.now(_dt.timezone.utc)
                                         + _dt.timedelta(hours=8)).strftime("%Y-%m-%d")
                    continue
                shares = sum(h["sh"] for h in hits)
                stmt_cur = p.get("stmt_cur") or hits[0]["cur"]
                q_cur = (p.get("q") or {}).get("cur") or p.get("cur") or stmt_cur
                per = sum(h["cost"] for h in hits) * 1000.0 / shares
                if stmt_cur != q_cur and FX.get(stmt_cur) and FX.get(q_cur):
                    per = per / FX[stmt_cur] * FX[q_cur]
                u = round(shares * (p.get("wgt") or 1), 4)
                per = round(per, 6)
                real = round(sum(h["real"] for h in hits), 3)
                # 容差用相對值:存檔時四捨五入過,用絕對比較會把捨入誤差當成「有變動」
                near = lambda a, b: (a is not None and
                                     abs(a - b) <= max(1e-6, abs(b) * 1e-6))
                same = (near(p.get("units_manual"), u) and near(p.get("cost"), per)
                        and near(p.get("stmt_real_k"), real))
                # ── 減碼(部分出脫)自動補記 ──────────────────────────
                # 股數一被覆蓋就再也回不去,所以要在覆蓋「之前」留紀錄。
                # 出場價由「本次新增的已實現損益 ÷ 減碼股數 + 減碼前每股成本」
                # 反推(與 SEED_TRIMS 同一套公式),三道防呆與種子一致:
                # 成本/股變動超過 25% 視為單位重估或轉換,不是減碼;
                # 已實現增額不得超過賣出金額;反推價須為正。
                _u0 = p.get("units_manual")
                # dup/derived 的判斷要與 v3.js 的 isDup 一致:derived 在這份組合裡
                # 是 dict 不是 True,`is not True` 擋不掉它,kind=="derived" 也沒檢查。
                _skip = bool(p.get("dup")) or bool(p.get("derived")) or p.get("kind") == "derived"
                if not _skip and _u0 not in (None, ""):
                    try:
                        _u0 = float(_u0)
                        _c0 = p.get("cost")
                        _sold = _u0 - u
                        _w = p.get("wgt") or 1.0
                        # 已實現要先按 wgt 分攤(拆分列的 stmt_real_k 是整檔數字),
                        # 再從對帳單幣別換到報價幣別(_c0 已經是報價幣別)。
                        # 沒有前一次的累積已實現時不要拿 0 頂替 —— 那會把「終生累積」
                        # 整個當成這一次的減碼實現,與前端的處理也不一致。
                        _prev_real = p.get("stmt_real_k")
                        _drS = None if (_prev_real is None or real is None) \
                            else (real - _prev_real) * _w
                        _fs, _fq = FX.get(stmt_cur), FX.get(q_cur)
                        _dr = (_drS / _fs * _fq) if (_drS is not None and _fs and _fq) else None
                        if _u0 > 0 and u >= 0 and _sold > _u0 * 0.01 and _c0:
                            _px = (sum(h["mv"] for h in hits) * 1000.0 * _w / u
                                   if u else 0)
                            _reval = abs(per / float(_c0) - 1) > 0.25
                            _too_big = (_drS is not None and _px > 0
                                        and abs(_drS) > _sold * _px / 1000.0 * 1.05)
                            _ex = (float(_c0) + _dr * 1000.0 / _sold) if _dr is not None else None
                            _prev = P.get("stmt_asof")
                            P.setdefault("trims", [])
                            _key = (p.get("name"), _prev, _today_tpe)
                            if not _reval and not any(
                                    (x.get("name"), x.get("on"), x.get("to")) == _key
                                    for x in P["trims"]):
                                P["trims"].append({
                                    "name": p.get("name"), "ticker": p.get("ticker"),
                                    "cur": q_cur, "code": (p.get("stmt_code") or [None])[0]
                                            if isinstance(p.get("stmt_code"), list) else p.get("stmt_code"),
                                    "on": _prev, "to": _today_tpe,
                                    "u0": round(_u0, 4), "u1": round(u, 4),
                                    "exit": round(_ex, 4) if (_ex is not None and _ex > 0
                                                              and not _too_big) else None,
                                    "est": 1,
                                    "real_k": (round(_dr, 1) if (_dr is not None and not _too_big)
                                               else None),
                                    "src": "stmt"})
                    except Exception:
                        pass
                p["units_manual"], p["cost"], p["cost_src"] = u, per, "stmt"
                p["stmt_real_k"] = real
                _bv = [h for h in hits if h.get("be") is not None and h["cost"]]
                if _bv:
                    p["be"] = round(sum(h["be"] * h["cost"] for h in _bv)
                                    / sum(h["cost"] for h in _bv) * 100, 1)
                p.setdefault("stmt_cur", stmt_cur)
                if not same:
                    changed += 1

    live_total = sum(1 for r in P["regions"] for g in r["groups"] for p in g["positions"]
                     if p.get("stmt_code"))
    if len(drop) >= 3 and len(drop) >= live_total / 3:
        # 不印數量:公開 log 上的部位數就是持倉規模
        print("::error::statement: 這份對帳單會移除超過三分之一的部位,可能只匯出了部分帳戶。本輪不套用。")
        raise SystemExit(1)

    if drop:
        # 出清快照:以最後看到的價格凍結該部位的年度損益,記入 closed_ytd
        # (與前端 importStmt 的 gone 路徑同一套口徑)。
        import datetime as _dt
        _today = (_dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(hours=8)).strftime("%Y-%m-%d")
        P.setdefault("closed_ytd", [])
        for p in drop:
            if p.get("dup"):
                continue
            try:
                q = _QT.get(p.get("ticker")) or {}
                cur = p.get("stmt_cur") or q.get("cur") or p.get("cur")
                f = FX.get(cur)
                u = p.get("units_manual")
                if p.get("kind") == "live" and u and q.get("price") and f and p.get("cost"):
                    mv = float(u) * q["price"] / FX.get(q.get("cur"), 1) / 1000.0
                    ck = float(u) * float(p["cost"]) / FX.get(q.get("cur"), 1) / 1000.0
                    w = p.get("wgt") or 1.0
                    v = mv - ck + ((p.get("stmt_real_k") or 0) - (p.get("ytd_base") or 0)) * w / f
                else:
                    v = p.get("pl") or 0
                # 以前這裡有 abs(v) >= 0.5 的門檻:算不出可靠的年度損益(例如那一輪
                # 剛好抓不到報價)就整筆不記,部位從此消失、追蹤表裡也看不到,
                # 而且沒有任何痕跡。2026-09-02 兩檔韓股就是這樣不見的。
                # 改成一律記錄 —— 金額 0 對加總無害,但那一列會出現在追蹤表上,
                # 看得到才有機會用「補登」把正確數字補上去。
                if not any(x.get("name") == p.get("name") and x.get("on") == _today
                           for x in P["closed_ytd"]):
                    # units:出清當下是最後一次知道股數的時點,不記下來就再也算不出
                    # 「當初沒賣的話今天賺賠多少」(與前端 importStmt 的 gone 路徑一致)。
                    _um = p.get("units_manual")
                    P["closed_ytd"].append({"name": p.get("name"), "usd_k": round(v, 1), "on": _today,
                                            "ticker": p.get("ticker"),
                                            "exit": q.get("price"),
                                            "cur": q.get("cur") or p.get("cur"),
                                            "units": float(_um) if _um not in (None, "") else None})
            except Exception:
                pass
        ids = {id(p) for p in drop}
        for r in P["regions"]:
            for g in r["groups"]:
                g["positions"] = [p for p in g["positions"] if id(p) not in ids]

    if not changed and not drop:
        gh_write(MARK_PATH, digest.encode(), message="data")
        print("statement: 內容與目前持倉一致,無需變更"); return

    # 這一份對帳單的日期:下一次偵測到減碼時,期間起點就是它
    # (第一次套用時還沒有,期間起點會留空,只顯示結束日)。
    P["stmt_asof"] = _today_tpe

    # 寫回覆寫檔:與網頁版同一個格式與路徑,所有裝置下次開啟就會拿到,
    # 下一輪建置的 merge_overlay 也會沿用,不會被打回原狀。
    import copy, datetime
    regs = copy.deepcopy(P["regions"])
    for r in regs:
        for g in r["groups"]:
            for p in g["positions"]:
                p.pop("q", None)              # 報價不進覆寫檔,每輪重抓
    payload = {"v": 1,
               "at": datetime.datetime.now(datetime.timezone.utc)
                        .isoformat(timespec="milliseconds").replace("+00:00", "Z"),
               "regions": regs, "hedges": P.get("hedges"),
               "fx_track": P.get("fx_track"), "fx_manual": P.get("fx_manual"),
               "closed_ytd": P.get("closed_ytd"), "trims": P.get("trims"),
               "stmt_asof": P.get("stmt_asof"), "dropped": P.get("dropped")}
    # 樂觀鎖:只覆寫 merge_overlay 這一輪讀到的那一版。中間若有裝置存檔(網頁每次
    # 編輯都會推),GitHub 回 409,本輪不寫、不記 mark,下一輪重新比對再套。
    _sha = open("overlay_sha.txt").read().strip() if os.path.exists("overlay_sha.txt") else None
    if _sha == "SKIP":
        # merge_overlay 這一輪忽略了覆寫檔(它早於 bundle 的 data_at),所以手上的
        # portfolio 是 bundle 原樣、不含使用者累積的修改。這時候寫回去等於把那些
        # 修改就地抹掉,而且 sha 是對的、樂觀鎖攔不住、建置還是綠燈。
        print("statement: 本輪覆寫檔被忽略,不寫回覆寫檔(避免抹掉裝置端的修改)")
        return
    try:
        gh_write(OVERLAY, encrypt(SK, pad_json(payload, 16384)).encode(), message="sync",
                 expect_sha=_sha or None)
    except Exception as e:
        print(f"statement: 覆寫檔在本輪期間被更動({type(e).__name__}),本輪不套用,下一輪重試")
        return
    gh_write(MARK_PATH, digest.encode(), message="data")
    # 雲端寫成功了,這一輪的建置才用新持倉;先寫本地再失敗會讓這一輪發佈的數字與覆寫檔不一致
    json.dump(P, open("portfolio.json", "w"), ensure_ascii=False, allow_nan=False)
    print("statement: 已套用" + ("(有部位缺匯率,未更新)" if need_fx else ""))


main()
