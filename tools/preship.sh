#!/bin/bash
# 出版前的固定關卡。任何一關紅就不交付。從 repo 根目錄執行。
#
#   VIEW_PASSWORD=… SYNC_KEY=… bash tools/preship.sh vNNN [來源.yml]
#
# 來源 yml 預設是 .github/workflows/build.yml(= 線上正在跑的那份)。
# **yml 本體也改過時(加/刪步驟)要明確指定改好的那份**,否則會拿舊的重打包,
# 把 yml 的改動默默吃掉(2026-09-10 踩到過)。
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
V="${1:?用法:bash tools/preship.sh vNNN [來源.yml]}"
BASE="${2:-.github/workflows/build.yml}"
OUTYML="build-$V.yml"
FAIL=0
step() { printf '%-46s' "$1"; }
res()  { if [ "$1" -eq 0 ]; then echo "ok"; else echo "FAIL"; FAIL=1; fi; }

echo "── 出版前關卡 · 目標 $V ──────────────────────────────"
echo "   來源 yml:$BASE"

step "1 語法 / 編譯"
python3 -m py_compile src/*.py 2>/dev/null && node --check src/v3.js >/dev/null 2>&1
res $?

step "2 版本號有遞增"
grep -q "BUILD_TAG = \"$V\"" src/build_dashboard_v3.py
res $?

step "3 情境測試(11 支)"
OUT=""
for d in tests/tt6 tests/tt7; do cp src/*.py "$d/" 2>/dev/null; done
for f in tests/tt6/replay.py tests/tt6/asia.py tests/tt6/tw.py tests/tt6/graceopen.py \
         tests/tt6/ledger.py tests/tt7/slot.py tests/tt7/skip.py tests/tt7/navchg.py; do
  (cd "$(dirname $f)" && python3 "$(basename $f)" >/dev/null 2>&1) || OUT="$OUT $(basename $f)"
done
[ -z "$OUT" ]; res $?
[ -n "$OUT" ] && echo "      失敗:$OUT"

step "4 拉線上真實狀態(replica)"
python3 tools/replica.py >/tmp/_rep.txt 2>&1
res $?
sed 's/^/      /' /tmp/_rep.txt | head -2

step "5 用線上真實資料建置 + 無 JS 錯誤"
(cd replica && python3 build_dashboard_v3.py >/dev/null 2>&1) && \
node -e "
const {chromium}=require('playwright');(async()=>{
const b=await chromium.launch({executablePath:'/opt/pw-browsers/chromium'});
const p=await b.newPage();const e=[];p.on('pageerror',x=>e.push(String(x)));
await p.goto('file://'+process.cwd()+'/replica/dashboard.html');await p.waitForTimeout(2500);
const n=await p.evaluate(()=>document.querySelectorAll('.card').length);
await b.close();process.exit(e.length||n<10?1:0);})();" >/dev/null 2>&1
res $?

step "6 replica 的數字與線上一致"
node -e "
const {chromium}=require('playwright');(async()=>{
const b=await chromium.launch({executablePath:'/opt/pw-browsers/chromium'});
const p=await b.newPage();
await p.goto('file://'+process.cwd()+'/replica/dashboard.html');await p.waitForTimeout(2500);
const r=await p.evaluate(()=>{const nd=nonDup();return {mv:Math.round(nd.reduce((s,x)=>s+effMv(x),0)),n:nd.length};});
require('fs').writeFileSync('/tmp/_rep.json',JSON.stringify(r));await b.close();})();" >/dev/null 2>&1
python3 - <<'PY'
import json, sys
sys.path.insert(0, 'tools')
rep = json.load(open('/tmp/_rep.json'))
import live
o = live.run_js("const nd=nonDup();return {mv:Math.round(nd.reduce((s,x)=>s+effMv(x),0)),n:nd.length};")["result"]
sys.exit(0 if (rep['mv'] == o['mv'] and rep['n'] == o['n']) else 1)
PY
res $?

step "7 前端測試(3 支,含端對端)"
OUT=""
for f in tests/tt8/sync.js tests/tt8/noise.js tests/tt8/e2e.js; do
  node "$f" >/dev/null 2>&1 || OUT="$OUT $(basename $f)"
done
[ -z "$OUT" ]; res $?
[ -n "$OUT" ] && echo "      失敗:$OUT"

step "8 分岔備份不誤報(用線上資料種一份舊副本)"
(cd replica && node ../tools/stash_replica.js >/dev/null 2>&1)
res $?

step "9 公開面洩漏檢查"
(cd replica && python3 leak_check.py dashboard.html >/dev/null 2>&1)
res $?

step "10 打包 + 500 KB 上限"
python3 tools/repack.py "$BASE" "$OUTYML" >/tmp/_pack.txt 2>&1
res $?
sed 's/^/      /' /tmp/_pack.txt | tail -1

echo "──────────────────────────────────────────────────────"
if [ $FAIL -eq 0 ]; then
  echo "全部通過。$OUTYML 可以進 .github/workflows/build.yml。"
  echo "上線後再跑:"
  echo "  python3 tools/live.py build"
  echo "  python3 tools/live.py check tools/claims.json $V"
  echo "  node tools/stash_live.js        # 動到同步/備份時"
else
  echo "有關卡沒過 —— 不要出版。"
fi
exit $FAIL
