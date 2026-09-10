// 把「使用者裝置上那種備份」真的種進 localStorage,再重載,看 v131 會不會清掉。
const { chromium } = require('playwright');
const fs = require('fs');
(async () => {
  const b = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium' });
  const ctx = await b.newContext();
  const p = await ctx.newPage();
  const errs = []; p.on('pageerror', e => errs.push(String(e)));
  const unlock = async () => {
    await p.goto('file:///tmp/live_index.html'); await p.waitForTimeout(1400);
    await p.fill('#staticrypt-password', process.env.VIEW_PASSWORD);
    await p.evaluate(() => document.getElementById('staticrypt-form')
      .dispatchEvent(new Event('submit', {cancelable: true})));
    await p.waitForTimeout(6500);
  };
  const ov = JSON.parse(fs.readFileSync('/tmp/ov_now.json', 'utf8'));

  // ── 情境 A:備份內容其實與雲端相同(使用者現在那一份就是這種)
  await unlock();
  await p.evaluate(async o => {
    const body = JSON.parse(JSON.stringify(o));
    body.at = '2026-09-09T01:44:00Z';
    // 模擬舊副本:還沒跑過 migrateClass、鏡像列數字是舊行情、清單沒被清洗過
    body.regions.forEach(r => r.groups.forEach(g => g.positions.forEach(q => {
      if (q.geo) delete q.geo;
      if (q.derived) { q.mv = (+q.mv || 0) - 16.1; q.be = (+q.be || 0) - 2.3; }
    })));
    await stashPut(body, 'seed-equal');
  }, ov);
  const seeded = await p.evaluate(() => localStorage.getItem('portfolio_stash_v1') !== null);
  await unlock();                                  // ← 重載,走完整開機路徑
  const afterA = await p.evaluate(() => ({
    stash: localStorage.getItem('portfolio_stash_v1') !== null,
    alert: document.getElementById('alerts').innerText.trim(),
  }));

  // ── 情境 B:備份裡真的有一筆未同步的修改
  await p.evaluate(async o => {
    const body = JSON.parse(JSON.stringify(o));
    body.at = '2026-09-09T01:44:00Z';
    body.regions[0].groups[0].positions[0].units_manual = 424242;   // 真的改過
    await stashPut(body, 'seed-real');
  }, ov);
  await unlock();
  const afterB = await p.evaluate(() => {
    const html = document.getElementById('alerts').innerHTML;
    let rows = -1;
    if (document.getElementById('stashView')) {
      document.getElementById('stashView').click();
      rows = document.getElementById('stashPanel').innerText.split('\n').filter(x=>x.trim()).length;
    }
    return {stash: localStorage.getItem('portfolio_stash_v1') !== null,
            banner: html.indexOf('未同步') >= 0, panelLines: rows,
            panel: (document.getElementById('stashPanel')||{}).innerText};
  });
  console.log(JSON.stringify({
    種進去了: seeded,
    情境A_內容其實相同: {備份還在: afterA.stash, 紅字: afterA.alert || '(沒有)'},
    情境B_真的有未同步修改: {備份還在: afterB.stash, 紅字出現: afterB.banner,
                            差異表: (afterB.panel||'').replace(/\n+/g,' | ').slice(0,200)},
    頁面錯誤: errs.slice(0,3),
  }, null, 1));
  await b.close();
})();
