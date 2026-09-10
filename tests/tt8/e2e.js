// 端對端:假的 GitHub Contents API + 真的頁面,跑完整條開機同步路徑。
const { chromium } = require('playwright');
const path = require('path');
const PAGE = 'file://' + path.resolve(__dirname, '..', '..', 'replica', 'dashboard.html');
const SYNC = {token: 'tok', key: 'test-key-123', repo: 'a/b', path: 'overlay.enc', branch: 'main'};
const fails = [];
const ok = (n, c, d) => { console.log((c ? '  ok   ' : '  FAIL ') + n + (d ? '  ' + d : '')); if (!c) fails.push(n); };

// 與 v3.js 完全相同的加解密
const enc = new TextEncoder(), dec = new TextDecoder();
async function key() {
  const h = await crypto.subtle.digest('SHA-256', enc.encode(SYNC.key));
  return crypto.subtle.importKey('raw', h, 'AES-GCM', false, ['encrypt', 'decrypt']);
}
async function syncEnc(txt) {
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const ct = new Uint8Array(await crypto.subtle.encrypt({name:'AES-GCM', iv}, await key(), enc.encode(txt)));
  const out = new Uint8Array(12 + ct.length); out.set(iv); out.set(ct, 12);
  return Buffer.from(out).toString('base64');
}
async function syncDec(b64) {
  const raw = Buffer.from(b64, 'base64');
  return dec.decode(await crypto.subtle.decrypt(
    {name:'AES-GCM', iv: raw.subarray(0,12)}, await key(), raw.subarray(12)));
}

(async () => {
  const b = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium' });
  const ctx = await b.newContext();
  // 頁面自己會 window.__SYNC__ = null(本機建置沒有 secret),所以要鎖住這個屬性,
  // 讓它那一行寫不進去(非嚴格模式下靜默失敗),測試才拿得到同步路徑。
  await ctx.addInitScript(s => {
    Object.defineProperty(window, '__SYNC__', {value: s, writable: false, configurable: false});
  }, SYNC);

  let server = null, shaN = 0, puts = [];      // 假的遠端:{sha, content(base64 of ciphertext-b64)}
  await ctx.route('https://api.github.com/**', async route => {
    const req = route.request();
    if (req.method() === 'GET') {
      if (!server) return route.fulfill({status: 404, body: '{}'});
      return route.fulfill({status: 200, contentType: 'application/json',
        body: JSON.stringify({sha: server.sha, content: Buffer.from(server.blob).toString('base64')})});
    }
    const body = JSON.parse(req.postData());
    puts.push(body);
    if (server && body.sha !== server.sha)
      return route.fulfill({status: 409, contentType: 'application/json', body: '{"message":"conflict"}'});
    server = {sha: 'sha' + (++shaN), blob: Buffer.from(body.content, 'base64').toString()};
    return route.fulfill({status: 200, contentType: 'application/json',
      body: JSON.stringify({content: {sha: server.sha}})});
  });

  const p = await ctx.newPage();
  const errs = []; p.on('pageerror', e => errs.push(String(e)));
  const load = async () => { await p.goto(PAGE); await p.waitForTimeout(2200); };
  const firstName = () => p.evaluate(() => P.regions[0].groups[0].positions[0].name);
  const setName = async v => p.evaluate(n => { P.regions[0].groups[0].positions[0].name = n; commit(); }, v);
  const serverName = async () => {
    const o = JSON.parse(await syncDec(server.blob));
    return o.regions[0].groups[0].positions[0].name;
  };

  console.log('[1] 第一次:遠端還沒有覆寫檔,本機編輯要推得上去');
  await load();
  const base0 = await firstName();
  await setName('EDIT-A');
  await p.waitForTimeout(3800);
  ok('推上去了', !!server);
  ok('遠端內容正確', server && await serverName() === 'EDIT-A');
  ok('徽章顯示已同步', (await p.evaluate(() => syncState)) === 'ok');

  console.log('\n[2] 乾淨重載(沒有未推送的修改)→ 直接吃遠端,不可跳警示');
  await load();
  ok('內容一致', await firstName() === 'EDIT-A');
  ok('沒有分岔警示', (await p.evaluate(() => document.getElementById('alerts').innerText)).trim() === '');

  console.log('\n[3] 另一台裝置改了遠端,本機沒有未推送的修改 → 遠端贏,不可跳警示');
  {
    const o = JSON.parse(await syncDec(server.blob));
    o.regions[0].groups[0].positions[0].name = 'EDIT-B-FROM-OTHER-DEVICE';
    o.at = new Date(Date.now() + 60000).toISOString(); o.base = null;
    server = {sha: 'sha' + (++shaN), blob: await syncEnc(JSON.stringify(o))};
  }
  await load();
  ok('畫面換成另一台的版本', await firstName() === 'EDIT-B-FROM-OTHER-DEVICE');
  ok('沒有誤報分岔', (await p.evaluate(() => document.getElementById('alerts').innerText)).trim() === '');

  console.log('\n[4] 離線編輯(推送失敗)→ 另一台又推了 → 上線重載:必須保留兩份');
  await p.unroute('https://api.github.com/**');
  await ctx.route('https://api.github.com/**', r => r.abort());     // 斷網
  await load();
  await setName('MY-OFFLINE-EDIT');
  await p.waitForTimeout(4000);
  ok('徽章顯示同步失敗', (await p.evaluate(() => syncState)) === 'err');
  ok('本機副本有存到(這一份不能丟)',
     (await p.evaluate(async () => (await readLocal()).regions[0].groups[0].positions[0].name)) === 'MY-OFFLINE-EDIT');
  {   // 這期間另一台裝置又推了一版
    const o = JSON.parse(await syncDec(server.blob));
    o.regions[0].groups[0].positions[0].name = 'OTHER-DEVICE-NEWER';
    o.at = new Date(Date.now() + 120000).toISOString();
    server = {sha: 'sha' + (++shaN), blob: await syncEnc(JSON.stringify(o))};
  }
  await p.unroute('https://api.github.com/**');
  await ctx.route('https://api.github.com/**', async route => {     // 恢復連線
    const req = route.request();
    if (req.method() === 'GET')
      return route.fulfill({status: 200, contentType: 'application/json',
        body: JSON.stringify({sha: server.sha, content: Buffer.from(server.blob).toString('base64')})});
    const body = JSON.parse(req.postData());
    if (body.sha !== server.sha) return route.fulfill({status: 409, body: '{}'});
    server = {sha: 'sha' + (++shaN), blob: Buffer.from(body.content, 'base64').toString()};
    return route.fulfill({status: 200, contentType: 'application/json', body: JSON.stringify({content:{sha: server.sha}})});
  });
  await load();
  const alertTxt = await p.evaluate(() => document.getElementById('alerts').innerText);
  ok('畫面以遠端為準(所有裝置一致)', await firstName() === 'OTHER-DEVICE-NEWER');
  ok('★ 離線那份沒有被丟掉,常駐紅字提示', alertTxt.indexOf('未同步的修改被保留下來') >= 0);
  ok('★ 備份裡就是離線編輯的內容',
     (await p.evaluate(async () => (await stashGet()).body.regions[0].groups[0].positions[0].name)) === 'MY-OFFLINE-EDIT');
  ok('徽章不再謊稱已同步而讓人以為沒事', alertTxt.length > 0);

  console.log('\n[5] 按「整份套用我的那份」→ 內容回來,而且推得上雲端');
  await p.evaluate(() => { window.confirm = () => true; });
  await p.click('#stashApply');
  await p.waitForTimeout(4200);
  ok('畫面換回自己那份', await firstName() === 'MY-OFFLINE-EDIT');
  ok('雲端也更新了', await serverName() === 'MY-OFFLINE-EDIT');
  ok('警示消失', (await p.evaluate(() => document.getElementById('alerts').innerText)).trim() === '');

  console.log('\n[6] 重載一次,確認狀態是乾淨的(不會又跳一次)');
  await load();
  ok('內容維持', await firstName() === 'MY-OFFLINE-EDIT');
  ok('沒有殘留警示', (await p.evaluate(() => document.getElementById('alerts').innerText)).trim() === '');

  if (errs.length) { console.log('\nPAGE ERRORS:', errs.slice(0, 4)); fails.push('pageerror'); }
  console.log('\n' + (fails.length ? 'FAIL: ' + fails.join(' | ') : 'PASS'));
  await b.close();
  process.exit(fails.length ? 1 : 0);
})();
