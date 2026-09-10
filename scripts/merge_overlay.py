#!/usr/bin/env python3
"""Actions 端:把使用者在網頁上的修改(加密覆寫檔)併回 portfolio.json。
覆寫檔含 regions(結構與手動欄位)、hedges、fx_track、fx_manual、closed_ytd、trims、
dropped、stmt_asof;不含報價。

安全前提(2026-09-08 複審):覆寫檔的明文要當成「不可信的輸入」——任何知道觀看密碼的人
都拿得到頁面內嵌的 SYNC 金鑰與 token,因此能寫任意內容進 overlay.enc。這支腳本是它進入
建置流程的唯一入口,所以型別、數值與大小的檢查都做在這裡:
  * 數字欄位一律轉成有限的 float,轉不了就拿掉(字串型的 mv 曾能繞過前端的 esc()
    直接進 innerHTML;NaN/Infinity 會進歷史檔並讓基準線永久重算)。
  * 字串欄位一律轉成 str 並截長;q(報價)與底線開頭的暫存欄位一律丟掉,報價只能由
    fetch_action 產生。
  * 部位總數與各清單長度設上限,避免把 6 小時的 job 拖死。"""
import json, math, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sync_crypto import decrypt, gh_read_sha

def _quiet(t, v, tb): print(f"overlay: 中止({t.__name__})", file=sys.stderr)
sys.excepthook = _quiet

SK = os.environ.get("SYNC_KEY", "")

if not SK:
    print("overlay: 未設定 SYNC_KEY,略過")
    raise SystemExit(0)

def _reject_const(c):
    # Python 的 json 預設接受 NaN / Infinity 這些非標準字面值,瀏覽器的 JSON.stringify
    # 永遠不會產生它們 —— 出現就是人為構造,直接拒收。
    raise ValueError("non-standard JSON constant")

try:
    # 走帶 token 的 Contents API:repo 之後轉為私有時公開 raw 會 404,
    # 那會讓網頁上的修改被靜默丟棄,所以這裡不能只靠公開路徑。
    blob, sha = gh_read_sha("overlay.enc")
    if blob is None:
        # 覆寫檔「不存在」與「解不開」要一樣當成異常:有 token 的人把檔刪掉,
        # 下一輪就會用 bundle 的舊持倉發佈、網頁上累積的修改全部消失而建置綠燈。
        # 真的要從零開始(第一次部署)必須顯式設 OVERLAY_BOOTSTRAP=1。
        if os.environ.get("OVERLAY_BOOTSTRAP") == "1":
            print("overlay: 尚無覆寫檔(OVERLAY_BOOTSTRAP),沿用 bundle 原始持倉")
            raise SystemExit(0)
        print("overlay: 覆寫檔不存在,中止本輪(第一次部署請設 OVERLAY_BOOTSTRAP=1)")
        raise SystemExit(1)
    _plain = decrypt(SK, blob.decode().strip())
    if len(_plain) > 2_000_000:
        print("overlay: 覆寫檔過大,中止本輪"); raise SystemExit(1)
    o = json.loads(_plain, parse_constant=_reject_const)
except SystemExit:
    raise
except Exception as e:
    # 原本這裡是 exit 0「靜默沿用 bundle」——對無人看管的排程是錯的取捨:
    # 網頁上累積的修改會被無聲丟棄,而畫面看起來一切正常。
    # 紅字中止才安全:gh-pages 保留上一版好內容,GitHub 也會為失敗的排程寄信,
    # 那是這套系統唯一的告警管道。
    print(f"overlay: 讀不到或解不開({type(e).__name__}),中止本輪")
    raise SystemExit(1)

if not isinstance(o, dict) or o.get("v") != 1 or not isinstance(o.get("regions"), list) or not o["regions"]:
    print("overlay: 格式不符,中止本輪")
    raise SystemExit(1)

P = json.load(open("portfolio.json"))

import datetime
def _t(x):
    try: return datetime.datetime.fromisoformat(str(x).replace("Z", "+00:00"))
    except Exception: return None
da, oa = _t(P.get("data_at")), _t(o.get("at"))
if da and (not oa or oa <= da):
    # 這一輪的 portfolio.json 是 bundle 原樣,不含使用者累積的修改。
    # 後面的 apply_statement 若拿著 overlay.enc 的 sha 去覆寫,就會把使用者
    # 所有的修改就地抹掉,而且樂觀鎖會通過(sha 是對的)、建置全綠。
    # 寫 SKIP 讓它知道「這一輪不准動覆寫檔」—— 這個檔以前是在讀完就寫,
    # 忽略分支在它之後才 exit,等於每次重新校準持倉都有一次抹掉的機會。
    open("overlay_sha.txt", "w").write("SKIP")
    print("overlay: 覆寫檔早於 bundle 資料版本,忽略(本輪不得覆寫覆寫檔)")
    raise SystemExit(0)
# 時間戳不得在未來:帶著 2999 年的覆寫檔會在每台裝置上永遠贏過真正的修改
_now = datetime.datetime.now(datetime.timezone.utc)
if oa and oa.tzinfo and oa > _now + datetime.timedelta(days=1):
    print("overlay: 覆寫檔時間戳在未來,中止本輪"); raise SystemExit(1)

# 給後續步驟(apply_statement)的樂觀鎖:它要覆寫的是「這一輪讀到的這一版」。
# 必須放在上面兩道檢查之後 —— 只有真的要把這份覆寫檔併進 portfolio.json 時,
# 才允許後續步驟回頭覆寫它。
open("overlay_sha.txt", "w").write(sha or "")

# ── 型別與大小的白名單清洗 ─────────────────────────────────────────────
MAX_POS, MAX_LIST, MAX_STR = 400, 400, 200
NUM_FIELDS = ("mv", "pl", "ret", "be", "cost", "cost_k", "units_manual", "wgt",
              "stmt_real_k", "ytd_base", "ytd_base_mv", "ytd_base_cost")
STR_FIELDS = ("name", "ticker", "note", "cur", "exp_cur", "stmt_cur", "kind", "subgrp",
              "cost_src", "nav_asof", "geo", "theme")

def _num(v):
    """有限的 float 或 None。字串型數字(含千分位的文字)一律不收 —— 前端存檔時本來就是數字。"""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    # 1e15 以上的數字在這裡沒有任何合理意義,只會在後面乘出 inf
    if isinstance(v, int):
        return v if abs(v) < 1e15 else None
    return v if (math.isfinite(v) and abs(v) < 1e15) else None

import re as _re
_CTRL = _re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
def _str(v):
    return None if v is None else _CTRL.sub("", str(v))[:MAX_STR]

def _clean_pos(p):
    if not isinstance(p, dict):
        return None
    q = {}
    for k, v in p.items():
        if not isinstance(k, str) or k.startswith("_") or k == "q":
            continue                                    # 報價與暫存欄位不收
        if k in NUM_FIELDS:
            n = _num(v)
            if n is not None: q[k] = n
        elif k in STR_FIELDS:
            s = _str(v)
            if s is not None: q[k] = s
        elif k == "stmt_code":
            codes = v if isinstance(v, list) else ([v] if isinstance(v, str) else [])
            q[k] = [str(c)[:40] for c in codes if isinstance(c, (str, int))][:8]
        elif k in ("dup",):
            q[k] = bool(v)
        elif k == "derived":
            if isinstance(v, dict):
                q[k] = {"region": _str(v.get("region")) or "", "subgrp": _str(v.get("subgrp")) or ""}
        elif k == "prev_override":
            if isinstance(v, dict) and _num(v.get("value")) is not None:
                q[k] = {"date": _str(v.get("date")) or "", "value": _num(v.get("value"))}
        # 其他欄位一律丟掉:白名單以外的鍵沒有任何程式會讀,只會把頁面與覆寫檔撐大
    return q

_regions, _npos = [], 0
for r in o["regions"]:
    if not isinstance(r, dict): continue
    rr = {"key": _str(r.get("key")) or "", "name": _str(r.get("name")) or "", "groups": []}
    for k in ("report_total", "report_total_ex_pe"):         # 區域層只認這兩個純量字典
        v = r.get(k)
        if isinstance(v, dict):
            rr[k] = {kk: (_str(vv) if isinstance(vv, str) else vv) for kk, vv in list(v.items())[:20]
                     if isinstance(kk, str) and isinstance(vv, (str, int, float, bool))
                     and not (isinstance(vv, float) and not math.isfinite(vv))}
    for g in (r.get("groups") if isinstance(r.get("groups"), list) else []):
        if not isinstance(g, dict): continue
        gg = {"name": _str(g.get("name")) or "", "positions": []}
        if isinstance(g.get("report_total"), dict):
            gg["report_total"] = {k: (_num(v) if isinstance(v, (int, float)) else _str(v))
                                  for k, v in list(g["report_total"].items())[:20] if isinstance(k, str)}
        for p in (g.get("positions") if isinstance(g.get("positions"), list) else []):
            cp = _clean_pos(p)
            if cp is None: continue
            _npos += 1
            if _npos > MAX_POS:
                print("overlay: 部位數超過上限,中止本輪"); raise SystemExit(1)
            gg["positions"].append(cp)
        rr["groups"].append(gg)
    _regions.append(rr)
o["regions"] = _regions

def _clean_list(v, keys_num=(), keys_str=()):
    out = []
    for x in (v if isinstance(v, list) else [])[:MAX_LIST]:
        if not isinstance(x, dict): continue
        y = {}
        for k, val in x.items():
            if not isinstance(k, str) or k.startswith("_"): continue
            if k in keys_num:
                n = _num(val)
                if n is not None: y[k] = n
            elif k in keys_str:
                s = _str(val)
                if s is not None: y[k] = s
            # 白名單以外的鍵丟掉
        out.append(y)
    return out

_present = {k for k in ("hedges", "fx_track", "fx_manual", "closed_ytd", "trims", "stmt_asof", "dropped")
            if o.get(k) is not None}
o["closed_ytd"] = _clean_list(o.get("closed_ytd"), ("usd_k", "exit", "units"),
                              ("name", "on", "ticker", "cur"))
o["trims"] = _clean_list(o.get("trims"), ("u0", "u1", "exit", "real_k", "est"),
                         ("name", "ticker", "cur", "code", "on", "to", "src"))
o["fx_manual"] = _clean_list(o.get("fx_manual"), ("mv",), ("cur", "name", "note"))
o["fx_track"] = [str(x)[:8] for x in (o.get("fx_track") if isinstance(o.get("fx_track"), list) else [])][:20]
o["hedges"] = {str(k)[:8]: _num(v) for k, v in (o.get("hedges") or {}).items()
               if isinstance(k, str) and _num(v) is not None} if isinstance(o.get("hedges"), dict) else {}
o["dropped"] = sorted({str(x)[:16] for x in (o.get("dropped") if isinstance(o.get("dropped"), list) else [])
                       if isinstance(x, str)})[:MAX_LIST]
o["stmt_asof"] = _str(o.get("stmt_asof"))

# stmt_code / stmt_cur 是「設定」不是「使用者資料」:對帳單代號對應表由 bundle 擁有,
# 網頁上沒有任何介面可以改它。覆寫檔是在加這些欄位之前產生的,若讓它整份蓋掉 regions,
# 對應表就會被抹掉、對帳單匯入永遠比對不到東西。所以合併後要把它們補回去。
CFG_FIELDS      = ("stmt_code", "stmt_cur", "ytd_base", "ytd_base_mv", "ytd_base_cost")
BACKFILL_FIELDS = ("stmt_real_k",)

_cfg = {}
for _r in P["regions"]:
    for _g in _r["groups"]:
        for _p in _g["positions"]:
            k = (_p.get("ticker") or "", _p.get("name") or "")
            keep = {f: _p[f] for f in CFG_FIELDS + BACKFILL_FIELDS if f in _p}
            if keep:
                _cfg[k] = keep

P["regions"] = o["regions"]

_n = 0
for _r in P["regions"]:
    for _g in _r["groups"]:
        for _p in _g["positions"]:
            k = (_p.get("ticker") or "", _p.get("name") or "")
            for f, v in _cfg.get(k, {}).items():
                # 純設定欄位:bundle 說了算(網頁上沒有介面能改它們)。
                # 匯入會寫入的欄位(累積已實現):只在覆寫檔缺少時補,
                # 否則會把使用者剛匯入的新對帳單數字倒退回 bundle 的舊值。
                if f in BACKFILL_FIELDS and _p.get(f) is not None:
                    continue
                if _p.get(f) != v:
                    _p[f] = v; _n += 1
if _n:
    print("overlay: 已補回 bundle 端的對帳單對應設定")
for k in _present:
    P[k] = o[k]
# allow_nan=False:任何 NaN/inf 到這裡都該是 bug,寧可中止也不要寫進去
json.dump(P, open("portfolio.json", "w"), ensure_ascii=False, indent=1, allow_nan=False)

print("overlay: applied")
