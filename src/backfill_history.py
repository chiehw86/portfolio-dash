#!/usr/bin/env python3
"""一次性回補走勢圖:補回缺掉的每日快照,並替舊資料列補上基準線欄位。

兩件事,都是補完就永遠 no-op:

1) 缺日回補(2026-08-03 ~ 2026-08-28)。歷史檔 8/25 清理重建,更早的快照不在
   檔內;8/27 碰上 GitHub Actions 全站故障、8/28 舊結算窗口太窄,各漏一筆。
   即時快照救不回來,改用各市場「每日收盤價 × 目前持倉」回算:
     - 亞股(台/日/韓/港/陸)用 D 日收盤;美/歐股與匯率用 ≤ D-1 的最後一根
       (對齊台北 18:00 的日終切片 —— 當時美股尚未開盤,頁面顯示的就是前收)。
     - 台灣境內基金(TWFUND)抓不到歷史淨值,以本輪淨值持平回算。
     - 持倉組合用「現在」的:8 月內調倉與已出清部位差異佔總資產 <0.3%。

2) 基準線欄位。每天用「當日各區域配置比重 × 該區域大盤指數(換算成 USD)」串成
   一條對照線;靜態部位(PE/Activist/海外基金)原樣帶過,只把有市價的部位換成
   指數 —— 那些淨值一個月才更新一次,逐日拿去比大盤只會製造假的追蹤誤差。
   兩條線對靜態部位的處理一致,落差因此只反映個股與 ETF 的選擇成效。

已經有基準欄的資料列一律原封不動,只拿來接續鏈:那些是當天實際結算的真值,
重算會把 src=0 蓋成估算值,也會被本檔抓不到那天行情的區間問題污染。

歷史檔只有這一份、又是整檔覆寫,所以:讀不到、解不開、表頭不認得 → 一律不寫。
注意:公開 repo 的 Actions log 任何人都看得到,不可印出金額、代號或筆數,
連例外的 traceback 都不行(訊息裡會夾帶欄位值),因此下面換掉了 excepthook。
"""
import json, os, sys, datetime, bisect, logging, warnings
for _n in ("yfinance", "urllib3", "peewee"):
    logging.getLogger(_n).setLevel(logging.CRITICAL)
logging.getLogger().setLevel(logging.CRITICAL)   # yfinance 失敗時會印出代號=持倉內容
warnings.filterwarnings("ignore")


def _quiet(exc_type, exc, tb):        # traceback 會夾帶金額/股數,公開 log 不能出現
    print(f"backfill: 中止({exc_type.__name__})")
    sys.exit(1)


sys.excepthook = _quiet

from cryptography.fernet import Fernet, InvalidToken

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sync_crypto import gh_read_sha, gh_write, pad_text

HIST_PATH = "data/history.enc"
STATE_CSV, STATE_SHA = "history_state.csv", "history_state.sha"   # 交棒給同一輪的 append
BKEYS = ["thematic", "china", "taiwan", "japan", "semi"]
HEADER = ("datetime_taipei,total_usd_k,ytd_pl_usd_k,day_usd_k,jpy_exp,krw_exp,nonusd_exp,"
          "bench_usd_k,live_usd_k,"
          + ",".join("w_" + k for k in BKEYS) + ","
          + ",".join("ix_" + k for k in BKEYS) + ",src")
NCOL, BASECOL = 20, 7
MAX_ROWS = 20000
RANGE_START, RANGE_END = "2026-08-03", "2026-08-28"

# ── 1. 先看有沒有事要做(沒有就零成本結束)────────────────────────────────
hk = os.environ.get("HISTORY_KEY", "").strip()
if not hk:
    print("backfill: 未設定金鑰,略過"); raise SystemExit(0)
f = Fernet(hk.encode())
raw, cur_sha = gh_read_sha(HIST_PATH)
if raw is None:
    print("backfill: 歷史檔不存在,略過(先讓 append_history 建檔)"); raise SystemExit(0)
try:
    lines = f.decrypt(raw).decode().strip().splitlines()
except InvalidToken:
    print("backfill: 歷史檔解不開,略過"); raise SystemExit(0)
lines = [l for l in lines if l.strip() and not l.startswith("#")]
if lines and not lines[0].startswith("datetime_taipei"):
    # 解得開但表頭不認得 —— 可能是別的檔或格式變了。這裡若當成空檔重建,
    # 整段歷史就永久消失(gh-pages 是強制推送,沒有舊 commit 可救)。
    print("backfill: 表頭不認得,中止(不覆寫)"); raise SystemExit(1)
body = lines[1:] if lines else []
if any(len(l.split(",")) < BASECOL for l in body):
    print("backfill: 有資料列欄位不足,中止(不覆寫)"); raise SystemExit(1)
have = {l[:10] for l in body}


def _weekdays(a, b):
    d, end = datetime.date.fromisoformat(a), datetime.date.fromisoformat(b)
    while d <= end:
        if d.weekday() < 5: yield d.isoformat()
        d += datetime.timedelta(days=1)


missing = [d for d in _weekdays(RANGE_START, RANGE_END) if d not in have]
need_bench = [l for l in body if len(l.split(",")) < NCOL]
if not missing and not need_bench:
    print("backfill: nothing to do"); raise SystemExit(0)

# ── 2. 歷史收盤價(yfinance;runner 內才有 Yahoo 網路)────────────────────
import yfinance as yf

P = json.load(open("portfolio.json"))
Q = json.load(open("quotes3.json"))
U = json.load(open("units.json")) if os.path.exists("units.json") else {}
fx_now = Q["fx_usd"]


def to_yahoo(t):
    code, _, ex = t.partition(":")
    return {"TPE": code + ".TW", "TYO": code + ".T", "KRX": code + ".KS",
            "HKG": code.zfill(4) + ".HK", "SHA": code + ".SS", "SHE": code + ".SZ",
            "TWFUND": None, "LON": code + ".L", "AMS": code + ".AS",
            "ETR": code + ".DE", "EPA": code + ".PA", "BIT": code + ".MI",
            "STO": code + ".ST"}.get(ex, code)


ASIA = {"TPE", "TYO", "KRX", "HKG", "SHA", "SHE", "TWFUND"}
# (區域, yahoo 代號, 計價幣, 是否亞洲時區)。亞股取 D 日收盤,美股取 D-1,與持倉同規則。
BENCH = [("thematic", "ACWI",  "USD", False), ("china", "^HSI",  "HKD", True),
         ("taiwan",   "^TWII", "TWD", True),  ("japan", "^N225", "JPY", True),
         ("semi",     "SOXX",  "USD", False)]

nd = [(r, p) for r in P["regions"] for g in r["groups"] for p in g["positions"]
      if not p.get("dup") and p.get("kind") != "derived"]
live_t = sorted({p["ticker"] for _, p in nd if p.get("kind") == "live" and p.get("ticker")})
fx_curs = sorted({c for c in fx_now if c not in ("USD", "as_of", "source")})

# 抓取區間由實際要計算的日期決定,不寫死:若某輪 append 先寫了比 RANGE_END 更新的
# 資料列,寫死的區間會拿舊行情去算它,把那一列的 live 算歪、下一輪的鏈也跟著錯。
_days = sorted(set(list(_weekdays(RANGE_START, RANGE_END)) + [l[:10] for l in body]))
_from = (datetime.date.fromisoformat(_days[0]) - datetime.timedelta(days=14)).isoformat()
_to = (datetime.date.fromisoformat(_days[-1]) + datetime.timedelta(days=1)).isoformat()


def _bars(sym):
    """date(str) 升冪的 [(日期, 收盤)];失敗回空(改用本輪價持平)。"""
    for _ in range(2):
        try:
            h = yf.Ticker(sym).history(start=_from, end=_to, interval="1d",
                                       auto_adjust=False)
            if h is not None and len(h):
                return sorted((i.date().isoformat(), float(v))
                              for i, v in h["Close"].items() if v == v and v > 0)
        except Exception:
            pass
    return []


BARS = {t: _bars(y) for t in live_t for y in [to_yahoo(t)] if y}
FXB = {c: _bars(f"{c}=X") for c in fx_curs}
IXB = {k: _bars(y) for k, y, _c, _a in BENCH}


def _at(bars, day):
    """最後一根日期 ≤ day 的 (收盤, 前一根收盤, 該根的日期)。"""
    if not bars: return None, None, None
    i = bisect.bisect_right([b[0] for b in bars], day)
    if i == 0: return None, None, None
    return bars[i - 1][1], (bars[i - 2][1] if i >= 2 else None), bars[i - 1][0]


def _prev_day(day):
    return (datetime.date.fromisoformat(day) - datetime.timedelta(days=1)).isoformat()


def fx_at(cur, day):
    if cur == "USD" or cur is None: return 1.0
    v, _, _ = _at(FXB.get(cur) or [], _prev_day(day))
    now = fx_now.get(cur)
    if v and now and abs(v / now - 1) <= 0.25: return v      # ±25% 防呆
    return now or 1.0


def px_at(p, day, prev_settle=None):
    """(當日價, 前一根價)。前一根回 None 代表「這一根在上一列已經記過」,
    當日變動才不會把同一場漲跌記兩次。

    原本的判斷是 `d == ref`,嚴格要求基準日當天有一根。非亞洲市場的 ref 是
    台北日的前一天,所以台北週一的 ref 是週日 —— 取到的是週五那根、日期不等於
    週日,前一根就被丟掉,dchg 記成 0,但市值卻用了週五的收盤在動。
    2026 年八月的 08-03 / 08-10 / 08-17 / 08-24 四列都是這樣,週變化因此低估。
    正確的判準不是「日期剛好相等」,而是「這一根有沒有在上一列用過」。"""
    t = p.get("ticker")
    q = Q["quotes"].get(t) if t else None
    ex = (t or "").partition(":")[2]
    ref = day if ex in ASIA else _prev_day(day)
    v, pv, d = _at(BARS.get(t) or [], ref)
    if v:
        if d == ref:
            used = False
        elif prev_settle is None:
            used = True                      # 不知道上一列用到哪 → 保守,不記
        else:
            pref = prev_settle if ex in ASIA else _prev_day(prev_settle)
            _, _, pd = _at(BARS.get(t) or [], pref)
            used = (pd is not None and d <= pd)   # 上一列已經用過同一根
        return v, (None if used else pv)
    return ((q or {}).get("price"), None)          # TWFUND / 抓不到 → 本輪價持平


def units_of(p, t):
    um = p.get("units_manual")
    if um not in (None, ""): return float(um)
    return U[t] * (p.get("wgt") or 1.0) if t in U else None


def cur_of(r, p):
    if p.get("exp_cur"): return p["exp_cur"]
    if r["key"] == "japan": return "JPY"
    q = Q["quotes"].get(p.get("ticker")) if p.get("ticker") else None
    return p.get("cur") or (q or {}).get("cur") or "USD"


def qcur(p):
    q = Q["quotes"].get(p.get("ticker")) if p.get("ticker") else None
    return (q or {}).get("cur") or p.get("cur") or "USD"


def is_live(p):
    return p.get("kind") == "live" and bool(p.get("ticker"))


def eff_at(p, day):
    if not is_live(p): return p.get("mv") or 0
    u, (px, _) = units_of(p, p["ticker"]), px_at(p, day)
    if u is None or not px: return p.get("mv") or 0
    return u * px / fx_at(qcur(p), day) / 1000.0


def day_chg(p, day, prev_settle=None):
    if not is_live(p): return 0.0
    u, (px, pv) = units_of(p, p["ticker"]), px_at(p, day, prev_settle)
    if u is None or not px or not pv: return 0.0
    return u * (px - pv) / fx_at(qcur(p), day) / 1000.0


def ytd_at(p, day):
    """口徑同 append_history.ytd():現值−累積成本+(累積已實現−年初)×wgt/匯率。"""
    if p.get("stmt_real_k") is None: return p.get("pl") or 0
    cur = p.get("stmt_cur") or qcur(p)
    fxv, c = fx_at(cur, day), None
    if is_live(p) and p.get("cost") not in (None, ""):
        u = units_of(p, p["ticker"])
        if u is not None: c = u * float(p["cost"]) / fx_at(qcur(p), day) / 1000.0
    if c is None: c = p.get("cost_k")
    if not fxv or c is None: return p.get("pl") or 0
    return eff_at(p, day) - c + (p["stmt_real_k"] - (p.get("ytd_base") or 0)) \
        * (p.get("wgt") or 1.0) / fxv


def ix_at(day):
    """各區域指數當日的 USD 位階;抓不到回 0(下游會沿用前一根並重新標準化權重)。"""
    out = []
    for k, _y, c, asia in BENCH:
        v, _, _ = _at(IXB.get(k) or [], day if asia else _prev_day(day))
        out.append(round(v / fx_at(c, day), 6) if v else 0.0)
    return out


# 已出清部位的年度損益一律全額計入,與 append_history 同一口徑;
# 若這裡改用「出清日之前不算」,回補列與實際結算列的交界就會出現一個跳階。
CLOSED = sum(float(c.get("usd_k") or 0) for c in (P.get("closed_ytd") or []))

# ── 3. 回補缺日 ───────────────────────────────────────────────────────
_prev_settle = None                    # 上一個已回補的結算日,用來判斷日線那根用過沒
for day in missing:
    tot = sum(eff_at(p, day) for _, p in nd)
    pl = sum(ytd_at(p, day) for _, p in nd) + CLOSED
    dchg = sum(day_chg(p, day, _prev_settle) for _, p in nd)
    _prev_settle = day
    jpy = sum(eff_at(p, day) for r, p in nd if cur_of(r, p) == "JPY")
    krw = sum(eff_at(p, day) for r, p in nd if cur_of(r, p) == "KRW")
    nonusd = sum(eff_at(p, day) for r, p in nd if cur_of(r, p) != "USD")
    body.append(",".join([day + " 18:00"] +
                         [f"{v:.1f}" for v in (tot, pl, dchg, jpy, krw, nonusd)]))

seen, uniq = set(), []                     # 一天只留一筆,時間字串排序 = 時序
for l in sorted(body):
    if l[:10] in seen: continue
    seen.add(l[:10]); uniq.append(l)
body = uniq

# ── 4. 補基準線欄位(只補缺的列,已有的原封不動,只拿來接續鏈)──────────────
def _chain(pw, pix, ix):
    """回傳 (加權報酬, 這一輪實際採用的指數位階)。
    某一區抓不到指數就沿用前一根,並把權重重新標準化到「有資料的區域」上 ——
    否則那一區的權重會帶著 0 報酬留在算式裡,基準被稀釋成假的超額報酬。"""
    # 單日 ±25% 以上的跳動幾乎都是抓錯標的,當成沒抓到、沿用前一根。
    ix = [(x if (not pix[i] or (x and 0.75 < x / pix[i] < 1.25)) else 0.0)
          for i, x in enumerate(ix)]
    ixu = [(ix[i] or pix[i]) for i in range(5)]
    ok = [i for i in range(5) if pix[i] and ix[i]]   # 看今天抓到沒,不是沿用後的值
    wsum = sum(pw[i] for i in ok)
    if not ok or wsum <= 0: return 0.0, ixu
    return sum(pw[i] * (ixu[i] / pix[i] - 1.0) for i in ok) / wsum, ixu


lb, pw, pix = None, None, None
out_rows, filled = [], set(missing)
for l in body:
    parts = l.split(",")
    day = parts[0][:10]
    if len(parts) >= NCOL:                 # 已經有基準欄:原封不動,只用來接續
        try:
            tot_, bench_, live_ = float(parts[1]), float(parts[7]), float(parts[8])
            pw = [float(x) for x in parts[9:14]]
            pix = [float(x) for x in parts[14:19]]
            lb = bench_ - (tot_ - live_)
        except ValueError:
            print("backfill: 既有基準欄無法解析,中止(不覆寫)"); raise SystemExit(1)
        out_rows.append(",".join(parts[:NCOL]))
        continue

    live_mv, pass_ = {}, 0.0
    for r, p in nd:
        v = eff_at(p, day)
        if is_live(p) and r["key"] in BKEYS:
            live_mv[r["key"]] = live_mv.get(r["key"], 0.0) + v
        else:
            pass_ += v                     # 靜態部位沒有歷史淨值,逐日是同一個數
    live_calc = sum(live_mv.values())
    w = [(live_mv.get(k, 0.0) / live_calc if live_calc else 0.0) for k in BKEYS]
    ix = ix_at(day)
    tot_ = float(parts[1])
    # 已存在的實際結算列只留下總額,不知道當天怎麼分。回算與實際的差額算在
    # 有市價那一段(靜態淨值一個月才動一次,當天的變化幾乎都出自有市價的部位);
    # 若反過來把差額算進原樣帶過的那段,基準線會跟著資產自己的漲跌走,失去對照意義。
    live_tot = tot_ - pass_
    if lb is None or pw is None:
        lb, ix_used = live_tot, ix         # 起點:基準與當天實際總資產同水位
    else:
        ret, ix_used = _chain(pw, pix, ix)
        lb *= (1.0 + ret)
    pw, pix = w, ix_used
    out_rows.append(",".join(parts[:BASECOL] +
                             [f"{pass_ + lb:.1f}", f"{live_tot:.1f}"] +
                             [f"{x:.4f}" for x in w] +
                             [f"{x:.6f}" for x in ix_used] +
                             ["1" if day in filled else "0"]))

csv = "\n".join([HEADER] + out_rows[-MAX_ROWS:]) + "\n"
new_sha = gh_write(HIST_PATH, f.encrypt(pad_text(csv).encode()),
                   message="data", expect_sha=cur_sha)
# 交棒:同一輪的 append_history 直接用這份,不再回頭讀 API
# (Contents API 不保證 read-after-write,讀到舊內容會把這次結果整個蓋掉)
open(STATE_CSV, "w").write(csv)
if new_sha: open(STATE_SHA, "w").write(new_sha)
print("backfill: done")
