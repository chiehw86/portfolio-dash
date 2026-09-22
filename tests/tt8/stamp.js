// 自動換到最新一版(v143):用本機 http 伺服器供應 replica 頁面與 build.txt,驗四種情形。
//   [1] build.txt 與頁面戳記相同 → 不換頁
//   [2] build.txt 比較新 → 換到 ?v=<新戳記>(且把舊的 ?t= 拿掉)
//   [3] 已在 ?v=<新戳記> 但伺服器還是給同一個新戳記(快取沒追上)→ 不再換(防迴圈)
//   [4] build.txt 404(例如 Worker 入口)→ 不換頁、無錯誤
//   [5] 回到前景會再查一次
const { chromium } = require('playwright');
const http = require('http'); const fs = require('fs'); const path = require('path');
const DIR = path.resolve(__dirname, '..', '..', 'replica');
const fails = [];
const ok = (n, c, d) => { console.log((c ? '  ok   ' : '  FAIL ') + n + (d ? '  ' + d : '')); if (!c) fails.push(n); };

let stampBody = null;            // null = 404
let hits = 0;
const srv = http.createServer((req, res) => {
  const u = new URL(req.url, 'http://x');
  if (u.pathname === '/build.txt') {
    hits++;
    if (stampBody === null) { res.writeHead(404); return res.end(); }
    res.writeHead(200, {'content-type': 'text/plain'}); return res.end(stampBody);
  }
  const f = path.join(DIR, u.pathname === '/' ? 'dashboard.html' : u.pathname);
  if (!fs.existsSync(f)) { res.writeHead(404); return res.end(); }
  res.writeHead(200, {'content-type': 'text/html; charset=utf-8'}); res.end(fs.readFileSync(f));
});

(async () => {
  await new Promise(r => srv.listen(0, r));
  const base = `http://127.0.0.1:${srv.address().port}/`;
  const pageStamp = fs.readFileSync(path.join(DIR, 'build.txt'), 'utf8').trim();
  const b = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium' });
  const p = await b.newPage();
  const errs = []; p.on('pageerror', e => errs.push(String(e)));
  const load = async (url) => { await p.goto(url); await p.waitForTimeout(3200); return p.url(); };

  console.log('[1] 戳記相同 → 不換頁');
  stampBody = pageStamp + '\n'; hits = 0;
  let u = await load(base);
  ok('留在原網址', u === base, u);
  ok('有去問過 build.txt', hits >= 1, `hits=${hits}`);
  ok('頁面的 STAMP 與 build.txt 相同', (await p.evaluate(() => STAMP)) === pageStamp);

  console.log('\n[2] 伺服器有更新的一版 → 換到 ?v=<新戳記>,舊的 ?t= 拿掉');
  stampBody = 'NEWER-STAMP\n';
  u = await load(base + '?t=123');
  ok('網址帶 v=NEWER-STAMP', new URL(u).searchParams.get('v') === 'NEWER-STAMP', u);
  ok('舊的 t 參數被拿掉', new URL(u).searchParams.get('t') === null, u);

  console.log('\n[3] 已在 ?v=<新戳記>,伺服器仍給同一個 → 不再換(防迴圈)');
  hits = 0;
  u = await load(base + '?v=NEWER-STAMP');
  ok('留在 ?v=NEWER-STAMP', u === base + '?v=NEWER-STAMP', u);
  ok('確實有查(不是沒跑)', hits >= 1, `hits=${hits}`);

  console.log('\n[4] build.txt 404 → 不換頁、無錯誤');
  stampBody = null;
  u = await load(base);
  ok('留在原網址', u === base, u);

  console.log('\n[5] 回到前景會再查一次');
  stampBody = pageStamp + '\n'; hits = 0;
  await load(base);
  const before = hits;
  await p.evaluate(() => document.dispatchEvent(new Event('visibilitychange', {bubbles: true})));
  await p.waitForTimeout(800);
  ok('visibilitychange 觸發再查', hits > before, `${before} → ${hits}`);

  ok('全程無 JS 錯誤', errs.length === 0, errs.join(' | '));
  await b.close(); srv.close();
  console.log('\n' + (fails.length ? '失敗:' + fails.join(', ') : '全部通過'));
  process.exit(fails.length ? 1 : 0);
})();
