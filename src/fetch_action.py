#!/usr/bin/env python3
"""GitHub Actions 用報價抓取:yfinance 抓所有 live 部位 + 指數 + 匯率,
失敗的標的退回 bundle 內的 baseline 快取。輸出 quotes3.json。"""
import json, os, re, datetime, logging, warnings
import urllib.request                     # 官方收盤來源在檔案較前面就要用到
import time                               # 日線重試的退避等待

# 公開 repo 的 Actions log 任何人都看得到。yfinance 在標的抓不到時會把「代號」
# 印到 stderr(例如 "$XXXX.TW: possibly delisted"),那等於直接公開持倉內容。
for _n in ("yfinance", "urllib3", "peewee"):
    logging.getLogger(_n).setLevel(logging.CRITICAL)
logging.getLogger().setLevel(logging.CRITICAL)
warnings.filterwarnings("ignore")

bundle = json.load(open("bundle.json"))
P = bundle["portfolio"]
baseline = bundle.get("baseline", {})

def to_yahoo(t):
    code, _, ex = t.partition(":")
    return {"TPE": code + ".TW", "TYO": code + ".T", "KRX": code + ".KS",
            "HKG": code.zfill(4) + ".HK", "SHA": code + ".SS", "SHE": code + ".SZ",
            "TWFUND": None,   # 台灣境內基金:走鉅亨網淨值,不走 yfinance
            "LON": code + ".L", "AMS": code + ".AS", "ETR": code + ".DE",
            "EPA": code + ".PA", "BIT": code + ".MI", "STO": code + ".ST",
            }.get(ex, code)

# 交易時段(UTC,週一–五)→ 盤中/收盤 標示
# 交易時段改記「交易所當地時間」+ 時區,不再記死 UTC。
# 原本 US 記 (13.5, 20.0) 是夏令時的 UTC 區間;冬令時實際是 14:30–21:00 UTC,
# 於是每年約五個月、每天最後一小時會被判成「已收盤」,expected_session 又把
# 今天當成應收盤場次 —— 還沒成交的標的就被誤判成報價落後。歐股同一個問題。
WINDOWS = {"TPE": (9.0, 13.5), "TYO": (9.0, 15.0), "KRX": (9.0, 15.5),
           "HKG": (9.5, 16.2), "SHA": (9.5, 15.0), "SHE": (9.5, 15.0),
           "LON": (8.0, 16.5), "AMS": (9.0, 17.5), "ETR": (9.0, 17.5),
           "EPA": (9.0, 17.5), "BIT": (9.0, 17.5), "STO": (9.0, 17.5),
           "US": (9.5, 16.0)}
TZ = {"TPE": "Asia/Taipei", "TYO": "Asia/Tokyo", "KRX": "Asia/Seoul",
      "HKG": "Asia/Hong_Kong", "SHA": "Asia/Shanghai", "SHE": "Asia/Shanghai",
      "LON": "Europe/London", "AMS": "Europe/Amsterdam", "ETR": "Europe/Berlin",
      "EPA": "Europe/Paris", "BIT": "Europe/Rome", "STO": "Europe/Stockholm",
      "US": "America/New_York"}
now = datetime.datetime.now(datetime.timezone.utc)
hnow = now.hour + now.minute / 60.0

try:
    from zoneinfo import ZoneInfo
    _ZONES = {k: ZoneInfo(v) for k, v in TZ.items()}
except Exception:
    _ZONES = {}                      # 容器沒有時區資料庫 → 退回 UTC,行為與舊版相近


def _local(key):
    """該交易所此刻的當地日期與小時(浮點)。"""
    z = _ZONES.get(key)
    if z is None:
        return now.date(), hnow
    l = now.astimezone(z)
    return l.date(), l.hour + l.minute / 60.0

# ── 台股:證交所 / 櫃買的官方收盤 ────────────────────────────────────────
# 為什麼要這一層:2026-09-01 某檔台股盤中顯示 +9.98%(接近漲停)。該用的前收是
# 08/28 還是 08/31,光看 Yahoo 的日線分不出來 —— 「上一場的日線缺一根」與
# 「那天根本沒開市」兩種情況長得一模一樣。交易所自己會講最後一個交易日是哪天、
# 收盤多少,拿它當前收就沒有猜的成分,連國定假日與颱風假也一併解決。
# 免金鑰、每輪兩個請求;抓不到就整個略過,行為退回原本的樣子。
TW_CLOSE, TW_LAST = {}, None      # code -> (收盤價, 交易日);TW_LAST = 最後交易日


def _load_tw_official():
    global TW_LAST

    def _num(x):
        try: return float(str(x).replace(",", ""))
        except Exception: return None

    def _roc(s):                   # 1150828 或 115/08/28 → date
        d = re.sub(r"\D", "", str(s or ""))
        if len(d) != 7: return None
        try: return datetime.date(int(d[:3]) + 1911, int(d[3:5]), int(d[5:7]))
        except ValueError: return None

    for url in ("https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL",
                "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes"):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            rows = json.loads(urllib.request.urlopen(req, timeout=25)
                              .read().decode("utf-8", "ignore"))
            for r in rows if isinstance(rows, list) else []:
                code = str(r.get("Code") or r.get("SecuritiesCompanyCode") or "").strip()
                px = _num(r.get("ClosingPrice") or r.get("Close"))
                dt = _roc(r.get("Date"))
                if code and px and px > 0:
                    TW_CLOSE[code] = (px, dt)
                    if dt and (TW_LAST is None or dt > TW_LAST): TW_LAST = dt
        except Exception:
            pass                   # 官方來源掛了就當沒有,不影響其他市場


_load_tw_official()
# 公開 repo 的 log:只印場次日期,不印筆數(這個專案一律不在 log 留下數量資訊)
print("tw-official: " + (f"最後交易日 {TW_LAST}" if TW_LAST else "未取得,本輪沿用原邏輯"))


def expected_session(t):
    """該標的「此刻應該要有」的最後交易日(以交易所當地時間推算,不含國定假日)。
    只用來偵測資料源落後 —— 假日會誤判成落後,但那只是多一個警示標記,
    比反過來(拿舊價當今天)安全得多。"""
    ex = t.partition(":")[2]
    # 台股:交易所直接告訴我們最後一個交易日,不必用「往前退到平日」硬推
    # (那個推法碰到國定假日或颱風假就會誤判)。
    if ex == "TPE" and TW_LAST: return TW_LAST
    key = ex if ex in WINDOWS else "US"
    lo, hi = WINDOWS[key]
    # 一律用交易所當地日期與當地時間判斷,不再混用 UTC —— 混用過一次:台北凌晨的
    # 建置(UTC 前一天 21 點)把亞股的 expected 推到還沒發生的「今天」,整排誤標。
    d, h = _local(key)
    if not (d.weekday() < 5 and h > hi):               # 當地今天的場次還沒收完
        d -= datetime.timedelta(days=1)
    while d.weekday() >= 5:                            # 往前退到最近的平日
        d -= datetime.timedelta(days=1)
    return d

# 亞洲市場:台北日換日之後、該市場開盤之前,「今日」不可以再算前一場的漲跌 ——
# 那一場昨天已經算過一次了。這六個市場與台北時差不到兩小時,行為一致。
ASIA_RESET = {"TPE", "TYO", "KRX", "HKG", "SHA", "SHE"}


def pending_open(t, sess):
    """回傳 (今天還沒有新場次的資料, 該市場此刻是否已開盤)。

    兩件事要分開,因為它們的處置一樣、講法不一樣:
      * 今日金額一律歸零 —— 沒有今天的場次,就沒有今天的漲跌可算。
      * 但「還沒開盤」與「開盤了、資料源還沒給新報價」是兩回事,
        標成前者會騙人:2026-09-03 台北 09:27(台股已開盤 27 分鐘)五檔台股
        全部顯示「尚未開盤」,因為 Yahoo 的即時價還停在 09/02 收盤。
    """
    ex = t.partition(":")[2]
    if ex not in ASIA_RESET:
        return False, False
    d, h = _local(ex)
    lo, _ = WINDOWS[ex]
    opened = d.weekday() < 5 and h >= lo
    if sess is not None:
        return sess < d, opened
    return (not opened), opened


def market_note(t):
    ex = t.partition(":")[2]
    key = ex if ex in WINDOWS else "US"
    lo, hi = WINDOWS[key]
    d, h = _local(key)
    live = d.weekday() < 5 and lo <= h <= hi
    label = "盤中" if live else ("美股收盤" if key == "US" else "收盤")
    if not live and ex in ("LON", "AMS", "ETR", "EPA", "BIT", "STO"): label = "歐股收盤"
    return live, label

# ── 台灣基金淨值:一天只抓一次(台北 07:00 那輪),其餘時段沿用 gh-pages 上的快取
TPE_DATE = (now + datetime.timedelta(hours=8)).strftime("%Y-%m-%d")
TPE_HOUR = (now + datetime.timedelta(hours=8)).hour
# 快取內容 = 你持有哪幾檔台灣基金 + 各自淨值。原本是明文放在公開網址上,
# 等於免密碼公開一部分持倉;改為與覆寫檔同一把 AES-GCM 金鑰加密。
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from sync_crypto import (decrypt as _dec, encrypt as _enc, gh_read as _ghread,
                         gh_write as _ghwrite, pad_json as _padj)

NAV_PATH = "data/nav-cache.enc"   # 放在 main,不隨網站發佈出去

NAV_SK = _os.environ.get("SYNC_KEY", "")
nav_cache = {}
try:
    _blob = _ghread(NAV_PATH) or _ghread("nav-cache.enc", ref="gh-pages")   # 一次性搬遷
    if _blob and NAV_SK:
        nav_cache = json.loads(_dec(NAV_SK, _blob.decode().strip()))
except Exception:
    pass
# 快取不是今天的(含首次執行)就重抓;正常情況下當天第一輪 = 台北 07:00
NAV_REFRESH = nav_cache.get("date") != TPE_DATE

# ── 價格帳本(data/px.enc)──────────────────────────────────────────────
# 為什麼要有這個:「上一場收盤是多少」以前每一輪都重新去問資料源,而資料源至少有
# 四種說錯的方式 —— 最後一根不夠新、中間缺根、除權息還原、即時價與日線互相矛盾。
# 我們一天抓 22 輪,昨天的收盤自己就看過,卻每天丟掉再去問別人。
# 改成記在自己的帳本裡:前收從「猜」變成「查」,上面四種問題就影響不到它。
#
# 規則:
#   * 只記「已收盤的場次」(live=False 且有 asof),記的是當時看到的原始價;
#   * 同一個 (代號, 場次) 只寫第一次看到的值,之後不覆寫 —— 事後被還原調整的
#     歷史價不能回頭改寫我們當時真的看到的數字;
#   * 每檔留最近 PX_KEEP 個場次;檔案補到 32KB 級距(大小否則會洩漏持有幾檔)。
PX_PATH, PX_KEEP = "data/px.enc", 15
px_book = {}
try:
    _pb = _ghread(PX_PATH)
    if _pb and NAV_SK:
        _pj = json.loads(_dec(NAV_SK, _pb.decode().strip()))
        # v1 的帳本作廢:那一版的日線種子沒有擋「還在交易中的當天」,亞股當日的
        # 盤中快照被當成收盤寫了進去,而且「同一場只寫第一次」會讓它永遠改不掉。
        # 整份丟掉重建即可 —— 日線種子一輪就能補回十場已收盤的歷史。
        if (isinstance(_pj, dict) and isinstance(_pj.get("px"), dict)
                and int(_pj.get("v") or 0) >= 2):
            px_book = {k: v for k, v in _pj["px"].items() if isinstance(v, dict)}
except Exception:
    px_book = {}                      # 讀不到就當空的:這一輪退回舊行為,不讓建置停擺
BARS_SEEN = {}                        # 本輪各代號的日線(yahoo 代號 → {場次: 收盤}),供帳本補種


def _ex_of(t):
    ex = t.partition(":")[2]
    return ex if ex in WINDOWS else "US"


def _sess_end(t, sess):
    """該場次實際收盤的時刻(UTC ISO)。建置端拿它跟「最近一次結算」比,
    判斷這一場的漲跌是不是已經被結算過了 —— 美股國定假日隔天會重複計入,
    就是因為以前只看「今天有沒有開盤」而沒看「這一場算過了沒」。"""
    if sess is None:
        return None
    key = _ex_of(t)
    z = _ZONES.get(key)
    if z is None:
        return None
    hi = WINDOWS[key][1]
    h, m = int(hi), int(round((hi - int(hi)) * 60))
    try:
        lt = datetime.datetime(sess.year, sess.month, sess.day, h, m, tzinfo=z)
        return lt.astimezone(datetime.timezone.utc).isoformat()
    except Exception:
        return None


def px_prev(t, sess, today_local):
    """帳本裡「目前這一場之前」最近一場的收盤,回傳 (場次日期, 收盤) 或 (None, None)。

    sess 有值 = 這筆報價屬於那一場,要找嚴格早於它的那一場;
    sess 是 None(盤中即時,今天這一場還沒收)= 找嚴格早於今天的那一場。
    """
    rec = px_book.get(t)
    if not rec:
        return None, None
    cutd = sess or today_local
    cut = cutd.isoformat()
    ds = [d for d in rec if isinstance(d, str) and d < cut]
    if not ds:
        return None, None
    d = max(ds)
    # 太舊的不算數:帳本斷過(新標的、連假、長期抓不到)時,拿十天前的收盤當前收
    # 會把一整段行情壓成「今天」的漲跌。超過就退回資料源,行為與舊版相同。
    try:
        if (cutd - datetime.date.fromisoformat(d)).days > 10:
            return None, None
    except Exception:
        return None, None
    v = rec[d]
    return (d, float(v)) if isinstance(v, (int, float)) and v == v and v > 0 else (None, None)

pos_all = [p for r in P["regions"] for g in r["groups"] for p in g["positions"]]
cur_of = {p["ticker"]: p.get("cur", "USD") for p in pos_all if p.get("ticker")}
tickers = sorted({p["ticker"] for p in pos_all if p.get("ticker") and p.get("kind") == "live"})

# 已出清部位也要抓價(「出清後表現」追蹤用)。closed_ytd 可能由網頁匯入寫入,
# 存在 merge_overlay 產出的 portfolio.json,優先讀它;沒有再退回 bundle。
try:
    _Pm = json.load(open("portfolio.json")) if os.path.exists("portfolio.json") else P
except Exception:
    _Pm = P
# 這些代號來自覆寫檔(任何持觀看密碼者都能寫),只收「像代號」的字串並設上限:
# 不能讓它塞進路徑符號、也不能讓上千個代號把整輪抓價拖到超時。
import re as _re
_TK_OK = _re.compile(r"^[A-Za-z0-9.^=\-]{1,24}(:[A-Za-z]{2,10})?$")
_closed_tk = [c.get("ticker") for c in (_Pm.get("closed_ytd") or [])
              if isinstance(c, dict) and isinstance(c.get("ticker"), str) and _TK_OK.match(c["ticker"])]
for _t in _closed_tk[:60]:
    if _t not in tickers:
        tickers.append(_t)
        cur_of.setdefault(_t, "USD")

import yfinance as yf

# 前收對不起來的標的 → (日線推出來的前收, 報價源給的前收)。
# 兩個候選值都留著:光靠推理已經猜錯兩次(先賭日線、再賭報價源,兩檔美股
# 各打臉一次),把實際數字帶進頁面,隔天早上的檢查就能拿外部行情直接判定
# 哪一個系統性正確,不必再猜。執行緒池只寫不讀,dict 指派在 CPython 是原子的。
PREV_WARN = {}


# 指數、商品、黃金、基準指數、匯率這幾組原本是一檔一檔序列抓的:數量只佔全部的
# 六分之一,卻因為沒有並行而佔掉一半以上的牆鐘時間。被限流那種最壞情況下,
# 整個抓價步驟會逼近排程間隔(30 分鐘),而工作流程開了 cancel-in-progress ——
# 下一輪一啟動就把這一輪殺掉,quotes3.json 根本沒寫成,而限流本身又會讓下一輪也慢,
# 於是自我維持。這裡先用同一個執行緒池把要用到的代號一次抓完存進 _PRE,
# 底下的序列程式碼原封不動,只是改成從快取拿。
_PRE = {}


def _prefetch(symbols):
    """把 exp=None / live=False 的那批代號並行抓好。失敗的不放進快取,
    之後序列那段會照原路自己再試一次,行為與以前相同。"""
    todo = [y for y in dict.fromkeys(symbols) if y and y not in _PRE]
    if not todo:
        return

    def one(y):
        try:
            return y, _get_price_prev(y)
        except Exception:
            return y, None
    from concurrent.futures import ThreadPoolExecutor as _TP
    with _TP(max_workers=6) as _p:
        for y, r in _p.map(one, todo):
            if r is not None:
                _PRE[y] = r


def get_price_prev(y, exp=None, live=False):
    if exp is None and not live and y in _PRE:
        return _PRE[y]
    return _get_price_prev(y, exp, live)


def _get_price_prev(y, exp=None, live=False):
    """回傳 (現價, 前一場次收盤價, 該場次日期)。

    兩個踩過的坑:

    1) 前收一律取自未經除權息調整的日線(auto_adjust=False)。fast_info.previous_close
       會被還原調整,除息後偏低(2026-08-19 某檔台股因此誤顯示 +5.1%,實為 +4.91%)。

    2) Yahoo 的「日線」與「即時報價」兩個端點更新速度不同,亞洲交易所尤其明顯。
       2026-08-22 04:55 那一輪實測:某檔台股日線最後一根還停在 08/20,
       但當天 08/21 其實已經收盤;某檔韓股同樣落後一場。
       同一個交易所裡還逐檔不一樣(兩檔韓國 ETF 有 08/21,兩檔個股沒有)。
       所以不能無條件相信日線的最後一根 —— last_price 比它新時,把 last_price
       當成「更新一場的收盤價」,日線最後一根則降級為前收。

    3) 但「日線最後一根 = 前收」只有在它確實是上一個應收盤場次時才成立。
       2026-09-01 盤中實測:某檔台股的日線缺了 08/31 那一根(最後一根還停在 08/28),
       於是拿 08/28 當前收,把兩天的漲跌算成今天的 —— 畫面顯示 +9.98%(接近漲停),
       實際只有 +1.57%。改成依日期去取「上一個應收盤場次」那一根;真的缺,才退回
       報價源的 previous_close;連那個也不合理就回 None,寧可不報漲跌。
       exp = expected_session(t),呼叫端傳進來。"""
    bars_fresh = False
    prev_bar_day = last_day = None    # 倒數第二根的日期:缺根偵測用(見下方交叉驗證)
    tk = yf.Ticker(y)
    # ── 即時報價這一步也會失敗,而且失敗的代價比想像中大 ────────────────
    # 以前是 `last = float(tk.fast_info.last_price)` 裸寫在 try 外面。限流時
    # fast_info 會整個拋例外,於是 _fetch_one 回 None,那一檔就「完全沒有報價」——
    # 畫面上變成「報價待接」,不進今日變動、不進上市連動計數,而且沒有任何警示,
    # 看起來就只是這檔今天沒動。2026-09-01 晚間最大部位就是這樣整檔消失。
    # 改成:重試三次;三次都拿不到就退回日線,日線也沒有才真的放棄。
    fi, last = None, None
    for _t in range(3):
        try:
            fi = tk.fast_info
            _l = float(fi.last_price)
            if _l > 0:
                last = _l
                break
        except Exception:
            fi = None
        if _t < 2:
            time.sleep(1.0 * (_t + 1))
    price, prev, sess = last, None, None

    def _quote_prev():
        """退路:報價源的前收。除息後會被還原調整而偏低(2026-08-19 某檔台股
        因此誤顯示 +5.1%,實為 +4.91%),所以只在日線真的缺那一根時才用,
        而且要落在合理區間才接受。"""
        if fi is None or not last:
            return None
        try:
            pc = float(fi.previous_close)
            return pc if pc > 0 and 0.7 < last / pc < 1.43 else None
        except Exception:
            return None
    try:
        # ── 日線可能整串是舊的,而且是逐檔隨機發生 ──────────────────────
        # 2026-09-01 實測八檔美股:即時報價全部是正確的 8/31 收盤,但日線那串
        # 落後好幾場 —— 收盤價與日線最後一根差 1.9%~11.6%,那不是一天的漲跌。
        # 同一個交易所、同一類標的裡有的正常有的不正常(兩檔 ETF 一壞一好、
        # 兩檔個股一壞一好),所以不是標的的性質問題,而是這一次請求拿到舊快取
        # (同時抓約 50 檔,Yahoo 會限流)。
        # 對策:拿「上一個應收盤場次」當新鮮度標準,不夠新就退避重抓。
        closes = None
        for _try in range(3):
            try:
                _c = tk.history(period="10d", interval="1d",
                                auto_adjust=False)["Close"].dropna()
            except Exception:
                _c = None
            if _c is not None and len(_c):
                closes = _c                                  # 至少留住拿到的
                if exp is None or _c.index[-1].date() >= exp:
                    break                                    # 夠新,不用再試
            if _try < 2:
                time.sleep(1.2 * (_try + 1))                 # 退避,順便錯開限流
        # 日線到底夠不夠新 —— 下面選前收候選時要用
        bars_fresh = bool(closes is not None and len(closes) and
                          (exp is None or closes.index[-1].date() >= exp))
        if closes is not None and len(closes) >= 2:
            tz = closes.index.tz
            today_local = datetime.datetime.now(tz).date() if tz else None
            last_day = closes.index[-1].date()
            bar, prev_bar = float(closes.iloc[-1]), float(closes.iloc[-2])
            prev_bar_day = closes.index[-2].date()
            by_date = {i.date(): float(v) for i, v in closes.items()}
            # 帳本的種子:只在我們沒看過那一場時才補,不覆寫自己記過的值。
            # 單一指派,不與其他執行緒共寫。
            BARS_SEEN[y] = {d.isoformat(): v for d, v in by_date.items()
                            if isinstance(v, float) and v == v and v > 0}

            if last is None:
                # 即時報價拿不到 → 純用日線:最後一根當現價,前一根當前收。
                # 這是「這一檔完全沒有數字」與「有一組略舊但正確的數字」之間的取捨,
                # 後者明顯好;真的舊的話下面的場次檢查會標出來並拿掉今日漲跌。
                price, prev, sess = bar, prev_bar, last_day
            elif today_local and last_day == today_local:
                # 日線已含當地今天 → 盤中或剛收盤,現價用即時報價,前收取前一根
                price, prev, sess = last, prev_bar, last_day
            elif abs(last / bar - 1) > 1e-9:
                # 日線落後於即時報價。此時「日線最後一根算不算前收」完全取決於
                # 現在有沒有開盤 —— 兩種情況的日期長相一模一樣,分不出來:
                #   收盤後:last 就是最新一場的收盤,日線最後一根確實是前收。
                #   盤中  :last 是今天的即時價,日線最後一根是「前一場的前一場」,
                #           拿它當前收就會把兩天的漲跌報成今天的。
                if live:
                    p = by_date.get(exp) if exp else None
                    if p is None: p = _quote_prev()      # 日線缺那一根 → 用報價源前收
                    price, prev, sess = last, p, None
                else:
                    # 現價一律用即時報價 —— 以前這裡外面套了 0.8 < last/bar < 1.25,
                    # 超出區間就整個掉到下面的 else,把「日線最後一根」當成現價回傳。
                    # 那等於在真正的大行情(收購、解除停牌、日韓漲跌停)那天把昨天的
                    # 收盤價當成今天的價格,漲跌還會被算成 0%。±25% 只能用來判斷
                    # 「日線那一根還能不能當前收」,不能用來否定現價。
                    price, sess = last, None
                    prev = bar if 0.8 < last / bar < 1.25 else _quote_prev()
            else:
                # 兩邊一致 → 該場次確實已結束,現價就是那根收盤價
                price, prev, sess = bar, prev_bar, last_day
        elif closes is not None and len(closes) == 1:
            if last is None: price = float(closes.iloc[-1])
            else: prev = float(closes.iloc[-1])
    except Exception:
        pass
    # NaN 會通過 `is None` 檢查然後污染下游每一個加總,而且會被寫進 history.enc
    # 永久留存(f"{nan:.1f}" 是 "nan"),所以在這裡就擋掉。
    _ok = lambda v: v is not None and v == v and v > 0
    if not _ok(price):
        raise ValueError("no price")          # 即時報價與日線都拿不到,交給呼叫端
    if not _ok(prev):
        prev = _quote_prev()                  # 退路走有區間檢查的那條,不要裸取
    if not _ok(prev):
        prev = None

    # ── 兩個獨立的前收互相驗證 ──────────────────────────────────────────
    # 一個是日線推出來的,一個是報價源自己給的 previous_close。正常情況相同;
    # 除權息還原調整會讓它們差一點點(2026-08-19 某檔台股差 0.2 個百分點),
    # 1% 以內碰不到下面的門檻。差距上到 2% 以上時分兩種情況處理:
    #
    # 上面的重試已經把「日線整串是舊的」擋掉大半;重試三次仍舊時才會走到這裡。
    # 那種情況下兩個候選都可能是更早某一場的收盤,誰對誰錯沒有固定規律。
    qp = _quote_prev()
    if prev and qp and abs(prev / qp - 1) > 0.02:
        PREV_WARN[y] = (round(prev, 4), round(qp, 4), bars_fresh)
        # 日線確實是最新那一場時,前收就以日線為準(未經除權息還原,那正是我們要的),
        # 只標記不換人。「取離現價近的那個」這條規則偏向報出比較小的漲跌,
        # 用在日線本來就對的情況會把真正的大行情壓成小行情。
        # 兩個候選誰對?2026-09-01 拿四檔外部行情實測過(收盤價 / 真前收 /
        # 日線候選 / 報價源候選):
        #   四檔美股實測:三檔是報價源對、一檔是日線對。
        #   (數字略去 —— 這支腳本放在公開 repo)
        #
        #
        # 固定選某一邊都會錯(先賭日線錯一檔,後賭報價源又錯另一檔)。
        # 兩個壞掉的候選都是「更早某一場的收盤」,而單日漲跌通常不大,
        # 所以改取「離現價比較近」的那一個 —— 這條規則上面四檔全中,
        # 台股某檔盤中那次也選對。仍然標記出來,
        # 因為這終究是推斷:真正的判定交給每天早上那次外部行情比對。
        if not bars_fresh:
            prev = min((prev, qp), key=lambda c: abs(price / c - 1))
        # 「最後一根夠新」不代表「倒數第二根就是上一場」。2026-09-03 晨檢實測
        # 某檔倫敦 ETF:日線只剩 …08/28、09/02 兩根(08/31、09/01 缺根),bars_fresh
        # 成立、v96 的重試不觸發,前收卻拿到 08/28 的 32.77 → 顯示 -3.81%,
        # 實際 -0.32%(真前收 31.62,正是報價源候選,與 AJ Bell 一致)。
        # 所以日線夠新時還要驗第二件事:前收那根的日期不得早於「最後一根往前退
        # 一個平日」;更早就是中間缺根,那一根是更早場次的收盤,前收改採報價源。
        # 國定假日會讓「往前退一個平日」誤觸,但那種情況兩個候選同指上一場收盤、
        # 差不到 2%,根本進不了這個分支,所以不會誤傷。
        elif prev_bar_day is not None and last_day is not None and prev == prev_bar:
            _e = last_day - datetime.timedelta(days=1)
            while _e.weekday() >= 5:
                _e -= datetime.timedelta(days=1)
            if prev_bar_day < _e:
                prev = qp
    return price, prev, sess


import re, urllib.request

NAV_PATS = [r'"nav"\s*:\s*"?(-?[0-9]+\.?[0-9]*)', r'"netValue"\s*:\s*"?(-?[0-9]+\.?[0-9]*)',
            r'"netAssetValue"\s*:\s*"?(-?[0-9]+\.?[0-9]*)', r'"price"\s*:\s*"?(-?[0-9]+\.?[0-9]*)']
CHG_PATS = [r'"changePercent"\s*:\s*"?(-?[0-9]+\.?[0-9]*)', r'"navChangePercent"\s*:\s*"?(-?[0-9]+\.?[0-9]*)',
            r'"changeRate"\s*:\s*"?(-?[0-9]+\.?[0-9]*)', r'"changePct"\s*:\s*"?(-?[0-9]+\.?[0-9]*)']
# 淨值日期。台灣境內基金的淨值當天傍晚才公布,這一輪(台北 07 點)抓到的一定是
# 前一個營業日的。以前標籤直接寫抓取當天的日期,2026-09-01 早上就顯示成
# 「基金淨值 09/01」,但頁面上白紙黑字是 2026/08/31 —— 差一天,而且看起來
# 像是今天的淨值。改成從頁面解析真正的淨值日,解不到才退回推算前一個營業日。
DATE_PATS = [r'"navDate"\s*:\s*"?(\d{4})[/-](\d{1,2})[/-](\d{1,2})',
             r'"date"\s*:\s*"?(\d{4})[/-](\d{1,2})[/-](\d{1,2})',
             r'淨值日期[^0-9]{0,20}(\d{4})[/-](\d{1,2})[/-](\d{1,2})',
             r'(\d{4})[/-](\d{1,2})[/-](\d{1,2})[^0-9]{0,12}淨值']

import time, threading
_last_fund_call = [0.0]
_fund_lock = threading.Lock()

def fetch_tw_fund(code, base_nav, _tries=3):
    """抓台灣境內基金淨值(鉅亨網)。base_nav 為上次已知淨值,用來擋掉解析到錯誤欄位:
    偏離超過 ±40% 一律視為失敗,寧可退回快取也不要顯示錯的數字。
    連續請求會被對方限流(2026-08-20 首次上線時 5 檔中有 2 檔失敗),
    故每次呼叫間隔 ≥2 秒,失敗再退避重試。"""
    html = ""
    for attempt in range(_tries):
        # 讀→算→睡→寫 必須是原子的,否則六條執行緒會讀到同一個時間戳、
        # 睡同樣久、然後同時發出請求 —— 實測五檔基金的最小間隔是 0.00 秒,
        # 這個 2 秒節流形同虛設,而它正是為了 2026-08-20 那次被限流才加的。
        with _fund_lock:
            gap = 2.0 - (time.time() - _last_fund_call[0])
            if gap > 0: time.sleep(gap)
            _last_fund_call[0] = time.time()
        try:
            req = urllib.request.Request(f"https://fund.cnyes.com/detail/x/{code}/",
                                         headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                                                  "Accept-Language": "zh-TW,zh;q=0.9",
                                                  "Accept": "text/html,application/xhtml+xml"})
            html = urllib.request.urlopen(req, timeout=25).read().decode("utf-8", "ignore")
            if len(html) > 2000: break
        except Exception:
            if attempt == _tries - 1: raise
        time.sleep(3 * (attempt + 1))
    nav = chg = navdate = None
    for pat in NAV_PATS:
        for m in re.finditer(pat, html):
            v = float(m.group(1))
            if base_nav and abs(v / base_nav - 1) <= 0.40:
                nav = v; break
            if not base_nav and v > 0:
                nav = v; break
        if nav is not None: break
    for pat in CHG_PATS:
        m = re.search(pat, html)
        if m and abs(float(m.group(1))) <= 30:   # 單日淨值變動超過 30% 幾無可能
            chg = float(m.group(1)); break
    _today = datetime.date.fromisoformat(TPE_DATE)      # 以台北日為基準,runner 跑在 UTC
    for pat in DATE_PATS:
        for m in re.finditer(pat, html):
            try:
                d = datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            except ValueError:
                continue
            # 只接受近期且不在未來的日期,免得抓到頁面上其他無關的年月日
            if 0 <= (_today - d).days <= 10:
                navdate = d; break
        if navdate: break
    if nav is None:
        raise ValueError("nav parse failed")   # 不帶代號與金額:公開 log 看得到
    # 漲跌解析不到就回 None,不要補 0.0 —— 補 0 是把「抓不到」寫成「今天沒漲跌」,
    # 對方網站改一次版就會讓所有基金當天顯示 +0.00%、今日金額 0,而且沒有 ⚠、
    # 沒有 —、建置全綠,整天沿用快取。淨值本身照樣是對的,只有漲跌顯示「—」。
    return nav, chg, navdate

quotes, fails = {}, []

# nav-cache.enc 放在 repo main,持 SYNC 金鑰與 token 的人寫得進去。快取內容不能原樣
# 當報價用:只挑白名單欄位、逐一驗型別與範圍,其餘丟掉。
_CACHE_NUM = ("price", "change_pct", "day_k", "units", "mv_live", "prev")
_CACHE_STR = ("cur", "note", "asof")
def _cache_ok(c):
    return (isinstance(c, dict) and isinstance(c.get("price"), (int, float))
            and not isinstance(c.get("price"), bool) and c["price"] == c["price"] and 0 < c["price"] < 1e12)
def _cache_pick(c):
    q = {}
    for k in _CACHE_NUM:
        v = c.get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and abs(v) < 1e15:
            q[k] = float(v)
    for k in _CACHE_STR:
        v = c.get(k)
        if isinstance(v, str): q[k] = v[:64]
    if "asof" in q and not _re.match(r"^\d{4}-\d{2}-\d{2}$", q["asof"]):
        q.pop("asof")
    q["live"] = False                     # 基金淨值永遠不是即時
    return q

def _fetch_one(t):
    """抓單一標的,回傳 (t, quote_dict 或 None)。供執行緒池並行呼叫;
    只讀共用資料(baseline/nav_cache/cur_of),寫入由主執行緒統一做。"""
    live, label = market_note(t)
    _cand = None                       # 兩個前收候選值;TWFUND 路徑不會設,先給預設
    _q_book = None                     # 帳本採用的前收(同樣要先給預設,TWFUND 走不到)
    try:
        if t.endswith(":TWFUND"):
            b = baseline.get(t) or {}
            cached = (nav_cache.get("quotes") or {}).get(t)
            if not NAV_REFRESH and _cache_ok(cached):   # 當天已抓過 → 直接沿用,不再打對方網站
                return t, _cache_pick(cached)
            price, chg, navdate = fetch_tw_fund(t.split(":")[0], (cached or b).get("price"))
            if navdate is None:                      # 解不到 → 推算前一個營業日
                _d = datetime.date.fromisoformat(TPE_DATE) - datetime.timedelta(days=1)
                while _d.weekday() > 4: _d -= datetime.timedelta(days=1)
                navdate = _d
            live, label = False, f"基金淨值 {navdate.strftime('%m/%d')}"
            if chg is None:                    # 淨值有、漲跌沒有:講清楚為什麼顯示「—」
                label += " · 漲跌未取得"
        else:
            price, prev, sess = get_price_prev(to_yahoo(t), expected_session(t), live)
            # 台股:前收一律以交易所官方收盤為準。盤中時官方最新的那一筆就是前收
            # (今天還沒收),不必去猜日線缺的是哪一根。收盤後只做交叉比對:
            # 兩邊差超過 0.5% 就標出來,不默默採用其中一個。
            _cand = PREV_WARN.get(to_yahoo(t)) if not t.endswith(":TPE") else None
            if _cand:
                # 台股另有交易所官方收盤可依(下面就會覆蓋),所以只標非台股。
                # 日線是最新那一場時我們直接採用它,沒有「推斷」的成分,只是兩源不一致。
                label = ("⚠ 前收兩源不一致 " if _cand[2] else "⚠ 前收推斷 ") + label
            _off = TW_CLOSE.get(t.split(":")[0]) if t.endswith(":TPE") else None
            if _off:
                if live:
                    prev = _off[0]
                elif (abs(price / _off[0] - 1) > 0.005
                      # 官方收盤與「我們採用的前收」一致時不要標記。
                      # 2026-09-02 04:10(台北)那一輪:TWSE STOCK_DAY_ALL 還停在 08/31,
                      # 官方收盤(=8/31 收)當然和 9/1 的現價差超過 0.5% —— 於是台股五檔
                      # (五檔台股,約總資產三成)全數被誤標。
                      # 但那五檔的價格與漲跌逐檔比對 cnyes / Google Finance 全部正確,
                      # 官方那筆其實是在「佐證前收」而不是在「打臉現價」。
                      # 官方與現價、前收都對不上時才是真的有問題,照樣標。
                      and not (prev and abs(prev / _off[0] - 1) <= 0.005)
                      and (_off[1] is None or sess is None or _off[1] >= sess)):
                    label = "⚠ 與官方收盤不符"
            # ── 前收:以自己的價格帳本為準 ────────────────────────────
            # 帳本記的是「我們當時親眼看到的那一場收盤」,不受日線缺根、最後一根
            # 落後、事後除權息還原、即時價與日線互相矛盾影響 —— 那四種正是過去
            # 一週最常出錯的原因。資料源的前收退成交叉驗證。
            _pd, _pv = px_prev(t, sess, _local(_ex_of(t))[0])
            if _pv:
                if not prev:
                    prev = _pv
                elif abs(_pv / prev - 1) > 0.20:
                    # 落差這麼大,幾乎只可能是分割/合併 —— 資料源把歷史價按比例改寫了,
                    # 而我們的股數還是舊的。這時要用資料源的(百分比才對),而且必須
                    # 大聲標出來:股數沒更新之前,市值是錯的。
                    label = "⚠ 疑似分割/合併,請確認股數 " + label
                else:
                    # 其餘落差(資料源拿錯場次、除息把歷史價調低)一律以帳本為準:
                    # 帳本記的是我們當時看到的原始收盤,除息當天市值本來就會掉那一塊。
                    prev = _pv
                    # 兩個候選誰對已經有答案了,那個 ⚠ 就不該再掛著 —— 否則每次
                    # 帳本正確攔下一個錯誤,早上的檢查都要被叫起來看一次。
                    for _w in ("⚠ 前收兩源不一致 ", "⚠ 前收推斷 "):
                        label = label.replace(_w, "")
                _q_book = _pv
            chg = (price / prev - 1) * 100 if prev else None
            if chg is None:            # 兩個候選都拿不到 → 不報漲跌
                label = "⚠ 前收不明"
            elif abs(chg) > 40:
                # 最後一道網:單一場次 ±40% 在任何一個市場都幾乎只可能是資料錯誤
                # (韓國漲跌幅上限 ±30%、台灣 ±10%、日本有幅度限制)。寧可不報。
                label = "⚠ 單日變動異常,未報漲跌"
                chg = None
            # 顯示的是哪一場次:收盤後標出日期,免得「今日」看起來像沒動。
            # 「這一場是不是舊的」不在這裡判 —— 交給抓完之後的同交易所共識比對,
            # 因為日曆推不出國定假日,單看日期會把整個交易所的休市誤判成資料落後。
            if not live and sess:
                label = f"{label} {sess.strftime('%m/%d')}"
        # 台北換日之後、亞股開盤之前:漲跌幅照顯示(那是前一場的,看得到才有參考),
        # 但「今日」的金額必須歸零 —— 否則昨天那一場會被算第二次。
        # 兩件事因此拆開:change_pct 是顯示用,pending_open 決定要不要進今日加總。
        _pend, _opened = (pending_open(t, sess) if not t.endswith(":TWFUND")
                          else (False, False))
        if _pend and not label.startswith("⚠"):
            _prev_txt = f"前場 {sess.strftime('%m/%d')}" if sess else ""
            if _opened:
                # 開盤後的頭 20 分鐘,資料源本來就還在給前一場的收盤 —— 台北 08:00 / 09:00 /
                # 09:30 那三輪剛好各自落在日韓 / 台 / 港開盤後一兩分鐘,每個交易日早上都會
                # 撞上。那是常態不是故障,講成「尚無新報價」會讓人每天早上以為抓價壞了。
                _ex = t.partition(":")[2]
                _fresh = False
                if _ex in WINDOWS:
                    _, _h = _local(_ex)
                    _fresh = 0 <= _h - WINDOWS[_ex][0] < 0.34        # 20 分鐘
                label = ("剛開盤 · 報價未更新" if _fresh else "已開盤 · 尚無新報價") \
                        + (f"({_prev_txt})" if _prev_txt else "")
            else:
                label = "尚未開盤" + (f" · {_prev_txt}" if _prev_txt else "")
        _q = {"price": round(price, 4),
              "change_pct": None if chg is None else round(chg, 2),
              "cur": cur_of.get(t, "USD"), "live": live, "note": label}
        if _pend:
            _q["pending_open"] = True
        # 這筆報價實際上是哪一場次:每天早上的正確性檢查靠它判斷資料源有沒有落後
        if not t.endswith(":TWFUND") and sess:
            _q["asof"] = sess.isoformat()
            _ae = _sess_end(t, sess)
            if _ae:
                _q["asof_end"] = _ae
        if _cand:                      # 兩個候選前收都帶進頁面,供事後判定
            _q["prev_bar"], _q["prev_quote"] = _cand[0], _cand[1]
        if _q_book is not None:        # 帳本用的那個前收,早上的檢查可以三方比對
            _q["prev_book"] = round(_q_book, 4)
        return t, _q
    except Exception:
        return t, None

# 並行抓取:逐檔串行 ~50 檔要一分多鐘,是建置耗時的大宗。
# 6 執行緒在速度與對資料源的禮貌之間取衡;結果寫回由主執行緒做,無共享狀態競爭。
from concurrent.futures import ThreadPoolExecutor
with ThreadPoolExecutor(max_workers=6) as _ex:
    for t, q in _ex.map(_fetch_one, tickers):
        if q is not None:
            quotes[t] = q
        else:
            fails.append(t)
            b = baseline.get(t)
            if b:
                quotes[t] = {**b, "live": False, "note": "快取(抓取失敗)"}

# ── 場次新鮮度:以「同一交易所的共識」為準,而不是日曆 ─────────────────
# 為什麼不能只看日曆:expected_session 不知道國定假日。美股一年約 10 天、日韓港
# 各 15–17 天,那些日子每一檔的最後場次都會比「日曆推出來的應收盤日」舊 ——
# 只看日期就會把整個交易所的健康報價全部判成落後、漲跌整批清空。
#
# 真正要抓的是 2026-09-01 台北 19:01 那種情形:同一個交易所裡「有的檔停在 08/28、
# 有的檔已經是 08/31」。也就是說,落後是相對於同交易所其他標的,不是相對於日曆。
#
# 規則:
#   1) 比同交易所最新場次舊 → 這一檔落後,拿掉漲跌(舊場次的漲跌不是今天的)。
#   2) 整個交易所都停在同一場次、且離應收盤日在一週內 → 休市,照常顯示。
#   3) 整個交易所落後超過一週 → 不是假日,是資料源壞了,一樣拿掉漲跌。
# 分組要用「市場」而不是掛牌代號:NYSE 與 NASDAQ 是同一個市場、同一份行事曆,
# 照字面分組的話 NYSE 只有一檔,它永遠等於自己的共識,永遠不會被判成落後。
_mkt = lambda t: (lambda e: e if e in WINDOWS else "US")(t.partition(":")[2])
_EXL = {}
for _t, _q in quotes.items():
    _a = _q.get("asof")
    if _a:
        _k = _mkt(_t)
        if _a > _EXL.get(_k, ""):
            _EXL[_k] = _a
_any_stale = False
for _t, _q in quotes.items():
    _a = _q.get("asof")
    if not _a:
        continue
    _d = datetime.date.fromisoformat(_a)
    _exl = datetime.date.fromisoformat(_EXL[_mkt(_t)])
    _behind = _d < _exl or (_exl < expected_session(_t)
                            and (expected_session(_t) - _exl).days > 7)
    if _behind:
        _q["change_pct"] = None
        _q["note"] = "⚠ 報價停在 " + _d.strftime("%m/%d")
        _any_stale = True
# 公開 log:只說有沒有,不說幾檔、不說哪一檔
print("freshness: " + ("有標的落後於同交易所共識,已停報其當日漲跌"
                       if _any_stale else "各交易所場次一致"))

IDX = [("台灣加權", "^TWII"), ("日經225", "^N225"), ("KOSPI", "^KS11"),
       ("恒生指數", "^HSI"), ("滬深300", "000300.SS"),
       ("S&P 500", "^GSPC"), ("NASDAQ", "^IXIC")]
# 大宗商品與加密貨幣(Yahoo 代號):期貨近 24 小時交易、BTC 全年無休,
# 不套交易所時段,一律標「即時」。dp = 顯示小數位(金與 BTC 取整較好讀)。
CMD = [("白銀", "SI=F", 2), ("WTI 原油", "CL=F", 2), ("Bitcoin", "BTC-USD", 0)]
# 指數與商品先一次並行抓好(見 _prefetch 的說明);底下的序列程式碼改從快取拿
_prefetch(["GC=F"] + [y for _, y, _ in CMD] + [y for _, y in IDX])
# 黃金:使用者持倉以 XAU 計價,優先抓現貨。Yahoo 對 XAU 現貨的代號支援不穩
# (XAUUSD=X 曾整檔抓不到,v52 上線後金價 tile 直接消失),所以走候選鏈:
# 現貨兩種寫法都試,最後退回 GC=F 期貨並如實標示。price>500 的下限是防呆 ——
# 若某代號被解析成費城金銀指數(^XAU,約一兩百點),會被擋掉而不是掛著錯數字。
indices = []
# 黃金第一優先:gold-api.com 的 XAU 現貨(免金鑰,實測可用)。它只給現價沒有前收,
# 當日漲跌 % 借 GC=F 期貨的日變化 —— 現貨與近月期貨的單日變動幅度基本同步,
# 誤差通常在 0.1% 內,遠比沒有漲跌可看好;拿不到期貨變化就顯示「—」。
_gold_done = False
try:
    _req = urllib.request.Request("https://api.gold-api.com/price/XAU",
                                  headers={"User-Agent": "Mozilla/5.0"})
    _spot = float(json.loads(urllib.request.urlopen(_req, timeout=20).read())["price"])
    if _spot > 500:
        _chg = None
        try:
            _fp, _fprev, _ = get_price_prev("GC=F")
            if _fprev:
                _chg = round((_fp / _fprev - 1) * 100, 2)
        except Exception:
            pass
        indices.append({"name": "黃金 XAU", "price": round(_spot, 2),
                        "change_pct": _chg, "live": True, "note": "即時",
                        "cat": "cmd", "dp": 0})
        _gold_done = True
except Exception:
    pass
# 備援:Yahoo 現貨兩種寫法 → 最後退 COMEX 期貨(如實標示)
if not _gold_done:
    for y, label in [("XAUUSD=X", "黃金 XAU"), ("XAU=X", "黃金 XAU"),
                     ("GC=F", "黃金(COMEX 期貨)")]:
        try:
            price, prev, _sess = get_price_prev(y)
            if price and price > 500:
                indices.append({"name": label, "price": round(price, 2),
                                "change_pct": round((price / prev - 1) * 100, 2) if prev else None,
                                "live": True, "note": "即時", "cat": "cmd", "dp": 0})
                break
        except Exception:
            continue
# ── 公債殖利率(2/10/20/30Y)────────────────────────────────────
# 美債:財政部官方日報 CSV(T+1)為基準;10Y/30Y 另有 Yahoo 即時指數(^TNX/^TYX,
# 報價 = 殖利率×10),抓得到就用即時值覆蓋。日債:財務省官方 CSV(T+1)。
# 變動一律以基點(bp)表示;防呆:數值須落在 0~25% 區間,否則整格不顯示。
def _add_yld(name, y, yp, live, note, dp):
    if y is None or not (0 < y < 25):
        return
    indices.append({"name": name, "price": round(y, dp), "change_pct": None,
                    "chg_bp": round((y - yp) * 100) if yp else None,
                    "unit": "%", "live": live, "note": note, "cat": "yld", "dp": dp})

def _csv_rows(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    return [[c.strip().strip('"') for c in l.split(",")]
            for l in urllib.request.urlopen(req, timeout=25)
            .read().decode("utf-8", "ignore").splitlines() if l.strip()]

try:                                                   # 美債(CSV 由新到舊)
    _yr = now.year
    _tsy = lambda yr: _csv_rows(
        f"https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
        f"daily-treasury-rates.csv/{yr}/all?type=daily_treasury_yield_curve"
        f"&field_tdr_date_value={yr}&_format=csv")
    _rows = _tsy(_yr)
    if len(_rows) < 3:                                 # 年初資料不足兩列 → 併入去年
        _rows += _tsy(_yr - 1)[1:]
    _hdr = _rows[0]
    # 一律用官方收盤,不接即時源:^TNX/^TYX 在不同環境回傳格式不一
    # (有時是殖利率×10、有時是殖利率本身),2026-08-28 實測誤差十倍
    # (顯示 0.47% 實為 4.67%)。收盤 CSV 沒有這種歧義。
    for _t, _cn in (("2Y", "2 Yr"), ("10Y", "10 Yr"), ("20Y", "20 Yr"), ("30Y", "30 Yr")):
        _i = _hdr.index(_cn)
        _v = [(r[0], float(r[_i])) for r in _rows[1:]
              if len(r) > _i and r[_i] not in ("", "N/A")][:2]
        if not _v:
            continue
        _m, _d2, _ = _v[0][0].split("/")               # MM/DD/YYYY
        _add_yld(f"美債 {_t}", _v[0][1], _v[1][1] if len(_v) > 1 else None,
                 False, f"收盤 {int(_m):02d}/{int(_d2):02d}", 2)
except Exception:
    pass

try:                                                   # 日債(CSV 由舊到新)
    _rows = _csv_rows("https://www.mof.go.jp/english/policy/jgbs/reference/"
                      "interest_rate/jgbcme.csv")
    _hdr = next(r for r in _rows if r[0].lower() == "date")
    for _t in ("2Y", "10Y", "20Y", "30Y"):
        _i = _hdr.index(_t)
        _d = [r for r in _rows if r[0][:2] == "20" and len(r) > _i
              and r[_i] not in ("", "-")]
        if not _d:
            continue
        _y = float(_d[-1][_i]); _yp = float(_d[-2][_i]) if len(_d) > 1 else None
        _md = _d[-1][0].split("/")                     # 2026/8/26
        _add_yld(f"日債 {_t}", _y, _yp, False,
                 f"收盤 {int(_md[1]):02d}/{int(_md[2]):02d}", 2)
except Exception:
    pass

for name, y, dp in CMD:
    try:
        price, prev, _sess = get_price_prev(y)
        indices.append({"name": name, "price": round(price, 2),
                        "change_pct": round((price / prev - 1) * 100, 2) if prev else None,
                        "live": True, "note": "即時", "cat": "cmd", "dp": dp})
    except Exception:
        pass
_idx_px = {}
for name, y in IDX:
    try:
        price, prev, sess = get_price_prev(y)
        _idx_px[y] = price
        us = y in ("^GSPC", "^IXIC")
        _k = "US" if us else {"^TWII": "TPE", "^N225": "TYO", "^KS11": "KRX",
                              "^HSI": "HKG"}.get(y, "SHA")
        lo, hi = WINDOWS[_k]
        _d, _h = _local(_k)
        live = _d.weekday() < 5 and lo <= _h <= hi
        indices.append({"name": name, "price": round(price, 2),
                        "change_pct": round((price / prev - 1) * 100, 2) if prev else None,
                        "live": live, "cat": "idx",
                        "note": "盤中" if live else ("收盤 " + sess.strftime("%m/%d") if sess else "收盤")})
    except Exception:
        pass

fx = {"USD": 1.0}
# CHF/GBP 是為了讓對帳單裡的海外基金(有的記瑞郎、有的記英鎊)
# 能換算成 USD;缺任一幣別時該部位會被標為「需要匯率」而不是算錯。
_FXC = ["TWD", "JPY", "KRW", "HKD", "CNY", "EUR", "GBP", "CHF"]


def _fx_one(c):
    """單一幣別,含重試與有限性檢查;回 (幣別, 值或 None)。"""
    for _i in range(3):
        try:
            _r = float(yf.Ticker(f"USD{c}=X").fast_info.last_price)
            if _r == _r and _r > 0:
                return c, round(_r, 6)
        except Exception:
            pass
        if _i < 2:
            time.sleep(1.0 * (_i + 1))
    return c, None


from concurrent.futures import ThreadPoolExecutor as _TPfx
with _TPfx(max_workers=6) as _pfx:
    _fxres = dict(_pfx.map(_fx_one, _FXC))
for c in _FXC:
    # 以前這裡是裸寫一行 fast_info.last_price,沒有重試也沒有數值檢查:
    # 限流時它照樣會拋例外,抓不到就整個幣別消失,該幣別的所有部位在下游被當成
    # 1:1 —— 韓元部位會膨脹一千四百倍。NaN 更糟,它是 truthy,會一路存進
    # history.enc 且無法回溯修復。
    v = _fxres.get(c)
    if v is None:
        _fb = bundle.get("fx_fallback", {}).get(c)
        v = _fb if (_fb is not None and _fb == _fb and _fb > 0) else None
    fx[c] = v
for _c in [c for c, v in fx.items() if v is None]:
    del fx[_c]                      # 抓不到又沒備援 → 移除,讓下游明確缺這個幣別
fx["as_of"] = now.strftime("%Y-%m-%d %H:%M UTC")
fx["source"] = "Yahoo Finance"

# ── 區域基準指數(走勢圖的對照線)──────────────────────────────────────
# 每個區域對到一個大盤指數,再除以匯率換成 USD:走勢圖的資產是 USD 計價,
# 基準若只算指數的本地報酬,日圓一動兩條線就會差在匯率而不是選股成效。
# 取值順序:上面 IDX 迴圈已抓的 → 持倉裡同代號的報價(若剛好也是持股)→ 現抓。
BENCH = [("thematic", "MSCI ACWI",   "ACWI",  "USD"),
         ("china",    "恒生指數",     "^HSI",  "HKD"),
         ("taiwan",   "台灣加權",     "^TWII", "TWD"),
         ("japan",    "日經225",      "^N225", "JPY"),
         ("semi",     "費城半導體",   "SOXX",  "USD")]
bench = {}
_prefetch([y for _, _, y, _ in BENCH])
for _bk, _blabel, _by, _bc in BENCH:
    try:
        _bp = _idx_px.get(_by)
        if _bp is None:
            _bq = next((q for t, q in quotes.items()
                        if t.split(":")[0] == _by and q.get("price")), None)
            _bp = _bq["price"] if _bq else None
        if _bp is None:
            _bp, _, _ = get_price_prev(_by)
        _bf = fx.get(_bc)
        if _bp and _bf and float(_bp) > 0:
            bench[_bk] = {"name": _blabel, "sym": _by, "cur": _bc,
                          "px": round(float(_bp), 4),
                          "usd": round(float(_bp) / _bf, 6)}
    except Exception:
        pass                      # 某一區抓不到 → 下游沿用前一根,不讓基準線斷掉

# ── 價格帳本:把這一輪看到的「已收盤場次」記下來 ──────────────────────────
# 只記已收盤的場次(live=False 且有 asof),而且同一個 (代號, 場次) 只寫第一次
# 看到的值 —— 事後被還原調整過的歷史價不能回頭改寫我們當時真的看到的數字。
# 日線當種子:第一次跑(或中間缺根)時補上我們沒親眼看過的場次,一樣不覆寫。
_yh = {t: to_yahoo(t) for t in quotes if not t.endswith(":TWFUND")}
_px_new, _px_chg = {}, 0
for _t, _q in quotes.items():
    if _t.endswith(":TWFUND"):
        continue
    _rec = dict(px_book.get(_t) or {})
    # 日線種子只收「確定已經收盤」的場次。資料源給的最後一根,在該市場還在交易時
    # 就是一張盤中快照 —— 寫進帳本會被當成收盤,而且再也改不掉(同一場只寫第一次)。
    _lex = _ex_of(_t)
    _ld, _lh = _local(_lex)
    _today = _ld.isoformat()
    _closed = _lh >= WINDOWS[_lex][1]                              # 當地時間過收盤了嗎
    for _d, _v in (BARS_SEEN.get(_yh.get(_t)) or {}).items():      # 日線種子
        if _d > _today or (_d == _today and not _closed):
            continue                                               # 這一場還沒收
        _rec.setdefault(_d, round(_v, 6))
    _a, _p = _q.get("asof"), _q.get("price")                       # 本輪親眼看到的
    if (not _q.get("live") and _a and isinstance(_p, (int, float))
            and _p == _p and _p > 0 and "快取" not in (_q.get("note") or "")):
        _rec.setdefault(_a, round(float(_p), 6))
    if len(_rec) > PX_KEEP:                                        # 只留最近幾場
        _rec = {d: _rec[d] for d in sorted(_rec)[-PX_KEEP:]}
    if _rec:
        _px_new[_t] = _rec
        if _rec != (px_book.get(_t) or {}):
            _px_chg += 1
if NAV_SK and _px_new and _px_chg:
    try:
        # 補到 32KB 級距:檔案大小否則會洩漏「持有幾檔、追蹤幾天」
        _ghwrite(PX_PATH, _enc(NAV_SK, _padj({"v": 2, "px": _px_new}, 32768)).encode(),
                 message="data")
        print("px: ok")
    except Exception:
        print("px: 寫回失敗,下一輪重試")     # 不致命:前收退回資料源,行為同舊版
elif not NAV_SK:
    print("px: 未設定 SYNC_KEY,本輪不寫帳本(不落明文)")

# 淨值快取:抓到就更新日期,沒抓到就原樣延續(避免把失敗寫成當天結果)
fund_q = {t: q for t, q in quotes.items() if t.endswith(":TWFUND") and "快取" not in q.get("note", "")}
_nav_out = ({"date": TPE_DATE, "quotes": fund_q, "hour_taipei": TPE_HOUR}
            if (NAV_REFRESH and fund_q) else (nav_cache or None))
if NAV_SK and _nav_out:
    # 補到 4KB 級距:檔案大小否則會洩漏「持有幾檔基金」
    try:
        _ghwrite(NAV_PATH, _enc(NAV_SK, _padj(_nav_out, 4096)).encode(), message="data")
    except Exception:
        print("nav-cache: 寫回失敗,下一輪會重抓")   # 不致命,最多當天多打幾次對方網站
elif not NAV_SK:
    print("nav-cache: 未設定 SYNC_KEY,本輪不寫快取(不落明文)")

tpe = now + datetime.timedelta(hours=8)
out = {"fetched_at_taipei": tpe.strftime("%Y-%m-%d %H:%M"), "quotes": quotes,
       "indices": indices, "fx_usd": fx, "bench": bench}
def _scrub(o):
    """NaN 與 Infinity 一旦寫進 quotes3.json 就會被下游全盤接受:
    json.dump 預設會輸出裸的 NaN 字面值,Python 端 json.load 照收,
    而 `is None` 檢查擋不住 NaN —— 它會污染當日損益並被寫進 history.enc 永久留存。
    這裡在輸出前一律洗成 null,讓下游走既有的「沒有數字」分支。"""
    if isinstance(o, float):
        return o if (o == o and o not in (float("inf"), float("-inf"))) else None
    if isinstance(o, dict):
        return {k: _scrub(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_scrub(v) for v in o]
    return o


json.dump(_scrub(out), open("quotes3.json", "w"), ensure_ascii=False,
          indent=1, allow_nan=False)
# 公開 repo 的 Actions log 任何人都看得到:只印數量,不印代號(代號即持倉內容)
print("quotes ok" if not fails else "quotes ok (some fell back to cache)")
print("nav: refreshed" if NAV_REFRESH else "nav: cached")
