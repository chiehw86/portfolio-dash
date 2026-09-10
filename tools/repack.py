"""把 src/ 裡的原始檔重新打包進 build.yml 的 base64 blob。

repo 的檔案分工(不要搞混):
  src/                  ← **唯一可編輯的來源**。改程式改這裡。
  .github/workflows/    ← 由 src/ 打包產生。**執行的是這一份**
  scripts/              ← 建置自己在執行時寫回去的鏡像,只給人 diff 用,改它沒有作用

為什麼執行的那份要留在 workflow 檔裡:改 .github/workflows/ 需要 token 的 workflows 權限,
改一般檔案只要 contents 權限,而解密後的頁面裡嵌著一顆只有 contents 讀寫的 token。
一旦建置改成去跑 scripts/ 或 src/,拿到觀看密碼就等於可以改一段會拿到全部 secret 的程式碼。

原本的說明:把 yml 裡的 base64 blob 改成 gzip+base64(GitHub 對 workflow 檔有 500 KB 上限,超過就永遠 Queued)。
用法:repack_gz.py <src.yml> <dst.yml>;src 可以是舊格式(base64 -d > f)或新格式(base64 -d | gunzip > f)。"""
import re, base64, gzip, sys, hashlib, os, subprocess, py_compile, yaml
src, dst = sys.argv[1], sys.argv[2]
s = open(src).read()
pat = re.compile(r"^([ \t]*)echo '([A-Za-z0-9+/=]+)' \| base64 -d( \| gunzip)? > (\S+)$", re.M)
SRC = 'src'
def _path(fn):
    """原始檔一律先找 src/,找不到才退回目前目錄(舊的平鋪式工作目錄還能用)。"""
    if fn == 'package.json':      p = os.path.join(SRC, 'package-dash.json')
    elif fn == 'package-lock.json': p = os.path.join(SRC, 'package-lock-dash.json')
    else:                         p = os.path.join(SRC, fn)
    if os.path.exists(p): return p
    for alt in (('package-dash.json' if fn == 'package.json' else
                 'package-lock-dash.json' if fn == 'package-lock.json' else fn),):
        if os.path.exists(alt): return alt
    return p
changed = []
def rep(m):
    ind, old, _, fn = m.groups()
    data = open(_path(fn), 'rb').read()
    new = base64.b64encode(gzip.compress(data, 9, mtime=0)).decode()
    try:
        prev = base64.b64decode(old)
        if _: prev = gzip.decompress(prev)
    except Exception: prev = None
    if prev != data: changed.append(fn)
    return f"{ind}echo '{new}' | base64 -d | gunzip > {fn}"
out = pat.sub(rep, s)
open(dst, 'w').write(out)
yaml.safe_load(out)
for m in pat.finditer(out):
    fn = m.group(4)
    assert gzip.decompress(base64.b64decode(m.group(2))) == open(_path(fn), 'rb').read(), fn
    assert m.group(3), fn
for f in ('build_dashboard_v3.py','fetch_action.py','apply_statement.py','append_history.py',
          'merge_overlay.py','sync_crypto.py','backfill_history.py','leak_check.py'):
    py_compile.compile(_path(f), doraise=True)
subprocess.check_call(['node', '--check', _path('v3.js')])
# 500 KB 是 GitHub 的硬上限,超過的 run 永遠停在 Queued 而且不報錯(2026-09-08 凍結 24 小時)。
# 這道 assert 以前只活在文件裡的程式碼片段;搬進 tools/ 才會每次都跑到。
assert os.path.getsize(dst) < 512_000, f"yml {os.path.getsize(dst)} bytes 超過 500 KB,GitHub 不會啟動 run"
print('changed:', changed)
print('md5', hashlib.md5(open(dst,'rb').read()).hexdigest()[:8], 'size', os.path.getsize(dst))
