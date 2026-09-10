#!/usr/bin/env python3
"""把每次執行的資產數字追加到歷史紀錄。
gh-pages 是公開的,故歷史檔以觀看密碼加密後才存(history.enc);
同時輸出明文 history.json 供 build script 嵌入(dashboard 本身也是加密的)。

兩個必須守住的原則:
1. 股數與幣別分桶的口徑必須與 dashboard 完全一致(units_manual 優先、exp_cur 優先),
   否則走勢圖與畫面上的 KPI 會長期對不上。
2. 讀不到或解不開既有紀錄時,絕不寫檔 —— gh-pages 是 git init + push -f,
   一旦寫出只有一筆的新檔,整段歷史就永久消失,沒有舊 commit 可救。
"""
import json, os, sys, base64, hashlib, datetime, logging, warnings
for _n in ("yfinance", "urllib3", "peewee"):
    logging.getLogger(_n).setLevel(logging.CRITICAL)
logging.getLogger().setLevel(logging.CRITICAL)
warnings.filterwarnings("ignore")


def _quiet(exc_type, exc, tb):    # traceback 的訊息會夾帶欄位值(金額、股數),
    print(f"history: 中止({exc_type.__name__})")   # 公開 repo 的 log 不能出現
    sys.exit(1)


sys.excepthook = _quiet

from cryptography.fernet import Fernet, InvalidToken

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sync_crypto import gh_read, gh_read_sha, gh_write, pad_text

STATE_CSV, STATE_SHA = "history_state.csv", "history_state.sha"   # backfill 的交棒檔

HIST_PATH = "data/history.enc"   # 放在 main,不隨網站發佈出去

MAX_ROWS = 20000
# 走勢圖的對照線:每天用「當日各區域配置比重 × 該區域大盤指數(換算成 USD)」
# 串成一條基準。權重與指數位階都存進列裡,是為了讓隔天能用「昨天的權重」乘
# 「今天的報酬」(當天才知道的權重不能拿來賺當天的報酬),漏記幾天也能正確接續。
# src: 0=當日實際結算,1=事後回補的近似值(回補列的 live 欄不可拿來偵測資金進出)。
BKEYS = ["thematic", "china", "taiwan", "japan", "semi"]
HEADER = ("datetime_taipei,total_usd_k,ytd_pl_usd_k,day_usd_k,jpy_exp,krw_exp,nonusd_exp,"
          "bench_usd_k,live_usd_k,"
          + ",".join("w_" + k for k in BKEYS) + ","
          + ",".join("ix_" + k for k in BKEYS) + ",src")

# (2026-09-08 移除)舊制的 key = SHA-256(觀看密碼) 讀取路徑:它讓任何知道觀看密碼的人
# 都能偽造一份歷史檔、被這裡「以舊金鑰讀入」後再用正式金鑰重新加密 —— 等於把偽造品洗白。
# 現行歷史檔早已全部用 HISTORY_KEY 寫入(實測解得開),這條後門沒有存在的必要。
# 輪替金鑰請用 HISTORY_KEY_OLD。

P = json.load(open("portfolio.json"))
Q = json.load(open("quotes3.json"))
U = json.load(open("units.json")) if os.path.exists("units.json") else {}
fx = Q["fx_usd"]


def _fx(cur):
    """缺匯率時回 None,絕不用 1.0 頂替。以前 eff()/day()/cost_usd() 都寫
    fx.get(cur, 1):抓匯率失敗(該幣別會被 fetch 端整個移除)時,韓元部位會被
    當成 1:1,一千四百倍的市值就這樣結算進 history.enc,而且事後改不掉。"""
    v = fx.get(cur)
    return v if (v is not None and v == v and v > 0) else None

def _live(p):
    t = p.get("ticker")
    q = Q["quotes"].get(t) if t else None
    return (t, q) if (q and p.get("kind") == "live") else (None, None)

def units_of(p, t):
    """與 build_dashboard_v3.py / v3.js 的 unitsOf 同一套:對帳單股數優先。
    units_manual 已是「本列」的股數(拆分列建置時就乘過 wgt),不可再乘一次。"""
    um = p.get("units_manual")
    if um not in (None, ""):
        return float(um)
    return U[t] * (p.get("wgt") or 1.0) if t in U else None

def eff(p):
    t, q = _live(p)
    if not t: return p.get("mv") or 0
    u = units_of(p, t)
    f = _fx(q["cur"])
    if u is None or not f: return p.get("mv") or 0
    return u * (q["price"] / f) / 1000.0

def day(p):
    t, q = _live(p)
    if not t: return 0.0
    u = units_of(p, t)
    if u is None: return 0.0
    # change_pct 可能是 None:抓價那一步判定前收兩個來源對不起來、不猜一個數字。
    # 那一檔的當日變動就當 0(與前端 day_k 的處理一致),不能讓它把整輪炸掉 ——
    # 2026-09-01 的 build #165 就是這裡沒防,TypeError 讓結算整個沒寫進去。
    # 三種都要擋,不是只有 None:
    #   None → 抓價端判定前收不可信(build #165 就是這裡沒防而炸掉)
    #   NaN  → 通得過 `is None`,然後把 dchg 變成 nan 寫進 history.enc 永久留存
    #   -100 → 1 + (-100)/100 = 0,ZeroDivisionError,整輪結算再次全滅
    # 台北換日後、亞股開盤前:那一場昨天已經結算過,不能再記一次
    if q.get("pending_open"): return 0.0
    cp = q.get("change_pct")
    if cp is None or cp != cp or cp <= -100: return 0.0
    f = _fx(q["cur"])
    if not f: return 0.0
    pu = q["price"] / f
    prev = pu / (1 + cp / 100.0)
    return u * (pu - prev) / 1000.0

nd = [(r, p) for r in P["regions"] for g in r["groups"] for p in g["positions"]
      if not p.get("dup") and p.get("kind") != "derived"]

def cur(r, p):
    """與 v3.js 的 curOf 同一套:曝險幣別優先,其次日本區一律 JPY,再其次計價幣別。"""
    if p.get("exp_cur"): return p["exp_cur"]
    if r["key"] == "japan": return "JPY"
    q = Q["quotes"].get(p.get("ticker")) if p.get("ticker") else None
    return p.get("cur") or (q or {}).get("cur") or "USD"

# ── 覆蓋率閘門 ───────────────────────────────────────────────────────
# eff() 在「該有報價卻抓不到」與「缺匯率」時會退回 p["mv"](上一次報表的靜態值)。
# 那對畫面來說可以接受(前端會標紅字),但結算是寫進 history.enc 的永久紀錄,
# 一旦用舊市值結算,那一天的資產與當日變動就永遠是錯的,而且事後看不出來。
# 2026-09-01 最大部位(佔兩成)整檔抓不到就是這個情形。
# 規則:受影響部位的市值超過總資產 2% → 這一輪不結算,而且要紅字失敗,
# 讓 GitHub 寄信通知,不要靜靜地留一個錯的資料點。
_miss = 0.0
for _r, _p in nd:
    if _p.get("kind") != "live" or not _p.get("ticker"):
        continue
    _q = Q["quotes"].get(_p["ticker"])
    if not _q or not _fx(_q.get("cur")):
        _miss += abs(_p.get("mv") or 0)

tot     = sum(eff(p) for _, p in nd)
if tot and _miss > abs(tot) * 0.02:
    # 公開 log:只講比例級距,不講是哪一檔、也不講幾檔
    print("history: 報價覆蓋率不足(缺口超過總資產 2%),本輪不結算")
    raise SystemExit(1)
def cost_usd(p):
    """與 v3.js 的 costK 同一套:每股成本(報價幣)× 股數。"""
    t, q = _live(p)
    if t and p.get("cost") not in (None, ""):
        u = units_of(p, t)
        if u is not None:
            f = _fx(q["cur"])
            if f: return u * float(p["cost"]) / f / 1000.0
    return p.get("cost_k")


def ytd(p):
    """與 v3.js 的 ytdOf 同一套口徑:只算目前仍持有的部位。
    有對帳單資料 → (現市值 − 累積成本 + 累積已實現) − 年初累積損益;
    沒有(Activist/PE/海外基金)→ 沿用報表 YTD。

    拆分列(依地區拆成多列)的 stmt_real_k / ytd_base 存的是整檔數字,
    要按 wgt 分攤 —— units_of() 回傳的股數已經乘過 wgt,市值成本本來就是
    這一列的份,只有這兩個對帳單欄位沒分攤過。"""
    if p.get("stmt_real_k") is None:
        return p.get("pl") or 0
    q = Q["quotes"].get(p.get("ticker")) if p.get("ticker") else None
    cur = p.get("stmt_cur") or (q or {}).get("cur") or p.get("cur")
    f, c = _fx(cur), cost_usd(p)
    if not f or c is None:
        return p.get("pl") or 0
    w = p.get("wgt") or 1.0
    return eff(p) - c + (p["stmt_real_k"] - (p.get("ytd_base") or 0)) * w / f


# 已出清部位的年度損益(出清時凍結的快照,存於 closed_ytd)一併計入,
# 讓走勢圖的 YTD 線與 KPI 及券商年度口徑一致。
_closed = sum(float(c.get("usd_k") or 0) for c in (P.get("closed_ytd") or []))
pl      = sum(ytd(p) for _, p in nd) + _closed
dchg    = sum(day(p) for _, p in nd)
jpy     = sum(eff(p) for r, p in nd if cur(r, p) == "JPY")
krw     = sum(eff(p) for r, p in nd if cur(r, p) == "KRW")
nonusd  = sum(eff(p) for r, p in nd if cur(r, p) != "USD")

stamp = Q.get("fetched_at_taipei") or (datetime.datetime.now(datetime.timezone.utc)
        + datetime.timedelta(hours=8)).strftime("%Y-%m-%d %H:%M")
# ── 基準線:靜態部位(PE/Activist/海外基金)原樣帶過,只把有市價的部位換成指數 ──
# 兩條線對靜態部位的處理完全一致,落差因此只反映個股與 ETF 的選擇成效;
# 那些淨值一個月才更新一次,拿去跟日經逐日比對只會製造假的追蹤誤差。
# 只有 BKEYS 這幾個區域有對應指數;其他(將來新增的區域)連同靜態部位一起
# 原樣帶過,不然它的市值會留在分母裡、卻沒有權重,把基準稀釋成假的超額報酬。
_BQ = Q.get("bench") or {}
_live_mv, _live_tot = {}, 0.0
for _r, _p in nd:
    if not (_p.get("kind") == "live" and _p.get("ticker")): continue
    if _r["key"] not in BKEYS: continue
    _v = eff(_p)
    _live_mv[_r["key"]] = _live_mv.get(_r["key"], 0.0) + _v
    _live_tot += _v
_pass = tot - _live_tot                    # 基準線裡原樣帶過的那一段
_w  = [(_live_mv.get(k, 0.0) / _live_tot if _live_tot else 0.0) for k in BKEYS]
_ix = [(_BQ.get(k) or {}).get("usd") for k in BKEYS]

import math as _math
if not all(isinstance(v, (int, float)) and _math.isfinite(v) for v in (tot, pl, dchg, jpy, krw, nonusd)):
    # NaN/inf 一旦寫進去,下一輪的基準線會從那一列重算、而且永遠留在檔裡
    print("history: 本輪數值異常(非有限值),不結算"); raise SystemExit(1)
row = [stamp] + [f"{v:.1f}" for v in (tot, pl, dchg, jpy, krw, nonusd)]

# ── 結算時點:台北時間週一~週五 18:00 那一輪才記一筆 ──────────────────────
# 18:00 的好處是台/日/韓都收盤了、美股還沒開,所以是一個乾淨的每日切片。
# 改成每日一筆也順帶解決兩件事:頁面不再每天長 3.4KB(走勢資料是內嵌的),
# 補齊級距不會每兩週跳一級而洩漏「這個看板跑多久了」。
try:
    _t = datetime.datetime.strptime(stamp, "%Y-%m-%d %H:%M")
except ValueError:
    _t = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None) \
        + datetime.timedelta(hours=8)
# GitHub 排程常延遲(實測 8/24 遲 67 分、8/28 遲逾 2 小時害當天沒記到),
# 放寬到 18–20 點:美股 21:30 才開盤,20 點台北仍是乾淨的日終切片;一天只記一筆。
#
# 但光是放寬窗口沒有用 —— 窗口裡原本只排了 18:00 一輪,下一輪是 21:30(窗口外),
# 那一輪延遲超過兩小時就整天沒有第二次機會,而且是 exit 0 全綠,走勢圖永久缺一格。
# v128 起窗口裡排四輪(18:00 / 19:00 / 20:00 / 20:40),任一輪跑到就記得到。
SLOT = (18 <= _t.hour <= 20 and _t.weekday() < 5)     # weekday(): 0=一 … 5=六 6=日
DAY = _t.strftime("%Y-%m-%d")

# 寫入金鑰:優先用獨立的 HISTORY_KEY(32 bytes 隨機,與觀看密碼解耦)。
# 好處有二:(a) 拿掉上面那條捷徑;(b) 換觀看密碼不會再讓歷史解不開而被清空。
hk  = os.environ.get("HISTORY_KEY", "").strip()
# 沒有 HISTORY_KEY 就不寫:絕不能退回用觀看密碼派生的金鑰寫檔,那會把公開網址上的
# 密文變成觀看密碼的離線爆破靶子(比 StatiCrypt 的 600k 次迭代快約 39 萬倍)。
f = Fernet(hk.encode()) if hk else None
# 讀取用的備用金鑰:輪替 HISTORY_KEY 時把舊值放進 HISTORY_KEY_OLD,
# 這一輪會用舊金鑰讀入、新金鑰寫回,遷移完成後即可移除該 secret。
_olds = []
_ko = os.environ.get("HISTORY_KEY_OLD", "").strip()
if _ko: _olds.append(Fernet(_ko.encode()))

# ── 讀取既有紀錄。只有兩種情況可以往下寫:成功解開,或確定是 404(還沒有歷史檔)──
lines = None
cur_sha = None
if not f:
    print("history: 未設定 HISTORY_KEY,中止本輪(歷史不能無聲停擺)"); raise SystemExit(1)
if os.path.exists(STATE_CSV):
    # 同一輪的 backfill 剛寫過:直接接手它的結果與 sha。Contents API 不保證
    # read-after-write,這裡若回頭讀 API 可能拿到舊內容,寫回去就把回補整個蓋掉。
    lines = open(STATE_CSV).read().strip().splitlines()
    cur_sha = open(STATE_SHA).read().strip() if os.path.exists(STATE_SHA) else None
    print("history: 沿用本輪回補的結果")
else:
    try:
        raw, cur_sha = gh_read_sha(HIST_PATH)
        if raw is None:
            # 一次性搬遷:舊版把歷史放在 gh-pages 根目錄(等於掛在公開網址上)。
            # 搬到 main 的 data/ 之後,第一輪從舊位置讀進來、寫回新位置,使用者無感。
            raw = gh_read("history.enc", ref="gh-pages")
            if raw is not None:
                print("history: 自舊位置搬遷")
        if raw is None:
            # 404 有兩種可能:真的第一次跑,或有人把 gh-pages 上的檔刪了。
            # 後者只要有 repo 寫入權就辦得到,而「重建成 1 筆」會永久抹掉歷史,
            # 所以預設不允許自動重建,要重建必須顯式設 HISTORY_BOOTSTRAP=1。
            if os.environ.get("HISTORY_BOOTSTRAP") == "1":
                lines = [HEADER]
                print("history: 依 HISTORY_BOOTSTRAP 建立新檔")
            else:
                print("history: 歷史檔不存在。若確實要從頭建立,"
                      "請在 workflow 加上 HISTORY_BOOTSTRAP=1 跑一次。")
                raise SystemExit(1)
        else:
            # 回滾偵測:Fernet token 帶有加密時間。歷史檔每個交易日都會重寫一次,
            # 一份「加密於 10 天前」的檔只可能是有人把舊密文放回來(持 token 者做得到,
            # 而且 Fernet 本身照樣解得開)。真的需要用舊檔重建時設 HISTORY_ACCEPT_OLD=1。
            try:
                import time as _time
                _ts = Fernet.extract_timestamp if hasattr(Fernet, "extract_timestamp") else None
                _age = (_time.time() - f.extract_timestamp(raw)) if _ts else 0
                if _age > 10 * 86400 and os.environ.get("HISTORY_ACCEPT_OLD") != "1":
                    print("history: 既有紀錄的加密時間過舊(疑似被換回舊版),中止本輪;確認無誤請設 HISTORY_ACCEPT_OLD=1")
                    raise SystemExit(1)
            except SystemExit:
                raise
            except Exception:
                pass                     # 舊金鑰的 token 用現行金鑰取不出時間,交給下面的解密迴圈判斷
            for idx, k in enumerate([f] + _olds):
                try:
                    lines = k.decrypt(raw).decode().strip().splitlines()
                    if idx: print("history: 以舊金鑰讀入,本輪起改用現行金鑰")
                    break
                except InvalidToken:
                    continue
            if lines is None:
                print("history: 既有紀錄解不開(金鑰不符或檔案被竄改),中止本輪。"
                      "若剛輪替過金鑰,請把舊金鑰放進 HISTORY_KEY_OLD 讓它遷移。")
                raise SystemExit(1)
    except SystemExit:
        raise
    except Exception as e:
        print(f"history: 取得既有紀錄失敗({type(e).__name__}),中止本輪")
        raise SystemExit(1)

lines = [l for l in lines if l.strip() and not l.startswith("#")]   # 去掉補齊行
if lines and not lines[0].startswith("datetime_taipei"):
    # 解得開但表頭不認得。當成空檔重建會讓整段歷史永久消失,寧可這一輪不寫。
    print("history: 表頭不認得,中止本輪(不覆寫)"); raise SystemExit(1)
if not lines:
    lines = [HEADER]
lines[0] = HEADER                      # 欄位是推導出來的,舊檔直接升級表頭

def _num(a, i):
    try:
        v = float(a[i])
        return v if v == v else None
    except Exception:
        return None

_prev = lines[-1].split(",") if len(lines) > 1 else None
_lb = None                            # 基準線裡「有市價那一段」的水位
if _prev and len(_prev) >= 20:
    _ptot, _pb, _plive = _num(_prev, 1), _num(_prev, 7), _num(_prev, 8)
    _pw  = [_num(_prev, 9 + i) or 0.0 for i in range(5)]
    _pix = [_num(_prev, 14 + i) for i in range(5)]
    if _pb is None or _plive is None or _ptot is None:
        # 前一列有基準欄卻讀不出來。這裡若默默重新起算,累積至今的落差會無聲消失。
        print("history: 前一列的基準欄無法解析,基準線自本列重新起算")
    else:
        # 某區今天沒抓到指數 → 沿用前一根,並把權重重新標準化到有資料的區域上;
        # 否則那一區會帶著 0 報酬留在算式裡,長期下來就是憑空的超額報酬。
        _ixu = [(_ix[i] if _ix[i] else _pix[i]) for i in range(5)]
        # 單日 ±25% 以上的指數跳動幾乎都是抓錯標的(這個專案已經被 ^TNX 少一個
        # 數量級、黃金抓成費城金銀指數咬過兩次),寧可當成沒抓到、沿用前一根。
        # 被擋掉的那一區「今天不計報酬」,但要把今天抓到的值存進去當新基準。
        # 以前是存回舊值(_ix[i] = None → 後面沿用 _pix[i]),那會永遠卡住:
        # ETF 分割、換代號、或一次寫進一個壞值之後,往後每天的正確值都會再被
        # 同一個舊基準擋掉,那一區從此被權重標準化排除,再也回不來。
        # 改成立刻換基準:最多損失一到兩天該區的報酬,不會變成永久性的。
        _rej = set()
        for i in range(5):
            if _ix[i] and _pix[i] and not (0.75 < _ix[i] / _pix[i] < 1.25):
                _rej.add(i); print(f"bench: 第 {i} 區指數跳動異常,今日不計、改以新值為基準")
        _ok = [i for i in range(5) if _pix[i] and _ix[i] and i not in _rej]
        _wsum = sum(_pw[i] for i in _ok)
        _ret = (sum(_pw[i] * (_ixu[i] / _pix[i] - 1.0) for i in _ok) / _wsum
                if _ok and _wsum > 0 else 0.0)
        if len(_ok) < 5: print(f"bench: {len(_ok)}/5 個區域有指數")
        _lb = (_pb - (_ptot - _plive)) * (1.0 + _ret)
        # 資金進出/大額調倉會讓有市價的部位憑空多出一塊,那不是報酬,基準必須
        # 同額跟進。dchg 只涵蓋「一個場次」的漲跌,跨越好幾天的話市場報酬會被
        # 誤判成入金,所以只在相鄰交易日(含週五→週一)判斷;門檻 3% 是為了不
        # 把匯率與雜訊當成金流;回補列(src=1)的 live 是估算值,一律不判斷。
        try:
            _gap = (_t.date() - datetime.date.fromisoformat(_prev[0][:10])).days
        except ValueError:
            _gap = 99
        _flow = _live_tot - _plive - dchg
        if (_num(_prev, 19) == 0 and _gap <= 3 and _plive > 0
                and abs(_flow) > 0.03 * _plive):
            _lb += _flow
        for i in range(5):
            if not _ix[i]: _ix[i] = _pix[i]   # 真的沒抓到才沿用;被擋掉的保留今天的值
if _lb is None:
    _lb = _live_tot                   # 第一筆:兩條線同起點

row += [f"{_pass + _lb:.1f}", f"{_live_tot:.1f}"]
row += [f"{x:.4f}" for x in _w]
row += [f"{(x or 0):.6f}" for x in _ix]
row += ["0"]

appended = False
_have_today = any(l.startswith(DAY + " ") for l in lines[1:])
if SLOT and not _have_today:                                       # 一天只記一筆
    lines.append(",".join(row))
    lines = [lines[0]] + lines[1:][-MAX_ROWS:]
    appended = True
elif not _have_today and _t.weekday() < 5 and 21 <= _t.hour <= 23:
    # 平日已經過了結算窗口卻還是沒有今天那一列 —— 窗口裡四輪全部沒跑成。
    # 這一格補不回來(backfill 的日期範圍是寫死的一次性任務),所以要留下紅字紀錄:
    # 不讓建置失敗(那會在美股盤中停止發佈價格,代價更大),但 Actions 上會是
    # 紅色註記,每日 06:00 的檢查也看得到。
    print(f"::error::結算窗口已過但今天({DAY})沒有記到走勢圖,這一格補不回來")

if appended:
    # 補齊長度(即使搬離公開網址,repo 若仍是公開的,檔案大小一樣看得到)。
    # 帶 sha:只覆寫「我讀到的那個版本」。若期間有別的執行寫過,GitHub 回 409,
    # 這時重讀一次再接上去 —— 直接無條件覆寫會把對方那一筆整個抹掉。
    for _try in (0, 1):
        try:
            gh_write(HIST_PATH, f.encrypt(pad_text("\n".join(lines) + "\n").encode()),
                     message="data", expect_sha=cur_sha)
            break
        except Exception as _e:
            if _try:
                # 「下一輪會補」是錯的:結算時段(台北 18–20 點)裡只有一次排程,
                # 沒有下一輪。靜靜地 exit 0 會讓走勢圖從此少一天而沒有任何訊號,
                # 所以這裡要紅字失敗 —— GitHub 對失敗的排程會寄信,那是唯一的告警管道。
                print(f"history: 寫回失敗({type(_e).__name__}),本日結算未寫入")
                lines = lines[:-1]; appended = False      # history.json 要反映遠端真實內容
                raise SystemExit(1)
            print("history: 有其他寫入,重讀後再試一次")
            _raw, cur_sha = gh_read_sha(HIST_PATH)
            # 解密要走與初次讀取同一組金鑰。只用 f 的話,輪替金鑰那一輪
            # (內容還是舊金鑰加密)會在 except 內再拋 InvalidToken,
            # 整個 build 紅字失敗而且結算照樣沒寫成。
            _fresh = None
            if _raw:
                for _k in [f] + _olds:
                    try:
                        _fresh = _k.decrypt(_raw).decode().strip().splitlines()
                        break
                    except Exception:
                        continue
            _fresh = [l for l in (_fresh or []) if l.strip() and not l.startswith("#")]
            if not _fresh or not _fresh[0].startswith("datetime_taipei"):
                print("history: 重讀後內容不認得,本輪不寫"); lines = lines[:-1]; appended = False; break
            _fresh[0] = HEADER
            if any(l.startswith(DAY + " ") for l in _fresh[1:]):
                print("history: 這一天已被別的執行記錄,本輪略過")
                lines = _fresh; appended = False; break
            lines = [_fresh[0]] + (_fresh[1:] + [",".join(row)])[-MAX_ROWS:]
# 明文只留在 runner 內,供 build script 嵌入已加密的 dashboard
json.dump([{"t": l.split(",")[0], "v": [float(x) for x in l.split(",")[1:]]}
           for l in lines[1:] if l and not l.startswith("#")], open("history.json", "w"))
# 注意:公開 repo 的 Actions log 任何人都看得到,這裡不可印出任何金額
print("history: appended" if appended else "history: unchanged")
