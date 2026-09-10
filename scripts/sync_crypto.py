#!/usr/bin/env python3
"""覆寫檔加解密:AES-GCM,key = SHA-256(SYNC_KEY)。
格式 = base64( iv(12 bytes) || ciphertext||tag )。與瀏覽器 Web Crypto 相容。"""
import base64, hashlib, os
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

def _key(sk): return hashlib.sha256(sk.encode()).digest()

def encrypt(sk, plaintext: str) -> str:
    iv = os.urandom(12)
    ct = AESGCM(_key(sk)).encrypt(iv, plaintext.encode(), None)
    return base64.b64encode(iv + ct).decode()

def decrypt(sk, blob: str) -> str:
    raw = base64.b64decode(blob)
    return AESGCM(_key(sk)).decrypt(raw[:12], raw[12:], None).decode()

# ── 以下為隱私強化新增(2026-08-20)──────────────────────────────────────────
import json as _json, urllib.error as _ue, urllib.request as _ur

def pad_json(obj, block=8192) -> str:
    """把 JSON 補到 block 的整數倍再加密。
    密文長度會洩漏明文長度 —— 覆寫檔大小 = 持倉規模,歷史檔大小 = 累積筆數,
    這兩者放在公開網址上等於免費的側信道。補齊後只剩「落在哪個級距」。"""
    s = _json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    if len(s.encode()) % block == 0:
        return s
    obj = dict(obj); obj["_pad"] = ""
    base = len(_json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode())
    obj["_pad"] = "0" * ((-base) % block)
    s = _json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    assert len(s.encode()) % block == 0
    return s

def pad_text(s: str, block=8192) -> str:
    """純文字版(CSV 用):補在結尾的註解行,解析時會被略過。"""
    n = len(s.encode())
    if n % block == 0:
        return s
    need = (-(n + 2)) % block          # "#" + "\n"
    return s + "#" + "0" * need + "\n"

def _gh_req(path, ref, accept, method="GET", body=None):
    repo = os.environ.get("GITHUB_REPOSITORY", "chiehw86/portfolio-dash")
    tok = os.environ.get("GITHUB_TOKEN", "")
    url = f"https://api.github.com/repos/{repo}/contents/{path}"
    if ref and method == "GET":
        url += f"?ref={ref}"
    hdr = {"Accept": accept, "User-Agent": "dashboard-bot"}
    if tok:
        hdr["Authorization"] = f"Bearer {tok}"
    elif method != "GET":
        raise RuntimeError("寫入需要 GITHUB_TOKEN")
    if not tok and method == "GET":       # 沒有 token 才退回公開 raw
        req = _ur.Request(f"https://raw.githubusercontent.com/{repo}/{ref}/{path}",
                          headers={"User-Agent": "dashboard-bot"})
    else:
        req = _ur.Request(url, headers=hdr, method=method,
                          data=_json.dumps(body).encode() if body else None)
        if body:
            req.add_header("Content-Type", "application/json")
    return _ur.urlopen(req, timeout=30)


def gh_read(path, ref="main", timeout=20):
    """讀取本 repo 某分支上的檔案,回傳 bytes;檔案不存在回傳 None;其他失敗 raise。

    走帶 token 的 Contents API:repo 轉為私有仍可用(公開 raw 會 404),
    也不受 raw / Pages CDN 的分鐘級快取影響。"""
    try:
        return _gh_req(path, ref, "application/vnd.github.raw").read()
    except _ue.HTTPError as e:
        if e.code == 404:
            return None
        raise


def gh_read_sha(path, ref="main"):
    """同時取回內容與 blob sha,供「讀到什麼就只覆寫什麼」的樂觀鎖使用。
    歷史檔只有這一份、又是整檔覆寫,沒有這道鎖的話:同一輪裡前一步剛寫完、
    後一步讀到舊內容(Contents API 不保證 read-after-write),或兩輪執行重疊,
    都會靜靜地把對方的結果蓋掉。帶 sha 寫入時 GitHub 會回 409,寧可失敗也不要覆寫。"""
    # 內容與 sha 必須來自「同一個回應」。以前是分兩個請求(先 raw 拿內容、
    # 再 metadata 拿 sha),中間若有人寫入,就會拿到「舊內容 + 新 sha」——
    # 帶著那個 sha 寫回去 GitHub 會接受,對方那一筆就被無聲蓋掉,
    # 樂觀鎖等於沒有。Contents API 的 JSON 回應裡 content 與 sha 本來就同時給。
    import base64 as _b64
    try:
        meta = _json.loads(_gh_req(path, ref, "application/vnd.github+json").read())
    except _ue.HTTPError as e:
        if e.code == 404:
            return None, None
        raise
    if not isinstance(meta, dict) or meta.get("type") != "file":
        return None, None
    enc = (meta.get("encoding") or "").lower()
    if enc == "base64":
        data = _b64.b64decode(meta.get("content") or "")
    elif meta.get("content") is not None:
        data = str(meta["content"]).encode()
    else:
        # 超過 1MB 時 Contents API 不回 content。退回兩段式,並在寫入前
        # 重新確認 sha 沒變 —— 這條路目前用不到(歷史檔遠小於 1MB)。
        data = gh_read(path, ref)
        return data, (gh_sha(path, ref) if data is not None else None)
    return data, meta.get("sha")


def gh_sha(path, ref="main"):
    """取得檔案目前的 blob sha(更新時必須帶),不存在回傳 None。"""
    try:
        meta = _json.loads(_gh_req(path, ref, "application/vnd.github+json").read())
        return meta.get("sha")
    except _ue.HTTPError as e:
        if e.code == 404:
            return None
        raise


def gh_write(path, data: bytes, ref="main", message="update", expect_sha=None):
    """把檔案寫進本 repo 的某分支。

    為什麼不放 gh-pages:gh-pages 分支本身就是網站內容,放上去等於掛在公開網址上
    讓任何人下載密文(「先存起來,金鑰以後外洩再解」)。這些檔案前端根本不會 fetch
    ——走勢圖用的是頁面內嵌的 __HIST__、基金快取是建置時用 API 讀的——
    所以放在一般分支即可,repo 轉私有後就完全不對外。"""
    import base64 as _b64
    body = {"message": message, "content": _b64.b64encode(data).decode(), "branch": ref}
    # expect_sha:呼叫端讀到的那個版本。帶了就只覆寫那個版本(不符 GitHub 回 409),
    # 沒帶則沿用舊行為(現讀現寫),給不需要樂觀鎖的呼叫端用。
    sha = expect_sha if expect_sha is not None else gh_sha(path, ref)
    if sha:
        body["sha"] = sha
    resp = _gh_req(path, ref, "application/vnd.github+json", method="PUT", body=body)
    try:                                   # 回傳新的 blob sha,供同一輪後續步驟接手
        return (_json.loads(resp.read()).get("content") or {}).get("sha")
    except Exception:
        return None


if __name__ == "__main__":
    # 金鑰只從環境變數拿:放在 argv 會留在 shell 歷史與 /proc 裡。
    #   SYNC_KEY=... python sync_crypto.py enc      < plain.json  > x.enc   (文字)
    #   SYNC_KEY=... python sync_crypto.py enc-file < file.xlsx   > x.enc   (二進位,內層 base64)
    #   SYNC_KEY=... python sync_crypto.py dec / dec-file 反向
    import sys, json
    sk = os.environ.get("SYNC_KEY") or ""
    if not sk:
        print("請以環境變數 SYNC_KEY 提供金鑰", file=sys.stderr); sys.exit(2)
    mode = sys.argv[1] if len(sys.argv) > 1 else "enc"
    if mode == "enc": print(encrypt(sk, sys.stdin.read()))
    elif mode == "dec": print(decrypt(sk, sys.stdin.read().strip()))
    elif mode == "enc-file":
        # 二進位檔包成 JSON 再補到 64KB 級距:對帳單密文的大小否則會跟著對帳單長度變動
        _env = {"b64": base64.b64encode(sys.stdin.buffer.read()).decode()}
        print(encrypt(sk, pad_json(_env, 65536)))
    elif mode == "dec-file":
        _p = decrypt(sk, sys.stdin.read().strip())
        _b = json.loads(_p)["b64"] if _p.lstrip().startswith("{") else _p
        sys.stdout.buffer.write(base64.b64decode(_b))
    else: sys.exit(2)

