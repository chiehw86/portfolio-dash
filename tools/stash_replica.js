// 分岔備份的誤報關卡(出版前)。用 replica(= 線上真實資料)種一份「內容其實相同的
// 舊副本」:還沒跑過 migrateClass、鏡像列是舊行情、清單沒清洗過。
// **不該掛紅字。** 掛了就是誤報,誤報會把整條防線報廢(使用者學會忽略它)。
//
// 實測:同一個種子,v129 誤跳、v132 沒有 —— 這一關若當初存在,
// v129→v130→v131→v132 那三輪修不對的版本會收斂成一版。
// 用法:cd replica && node ../tools/stash_replica.js       (exit 1 = 誤報)
const { chromium } = require('playwright');
(async () => {
  const b = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium' });
  const ctx = await b.newContext(); const p = await ctx.newPage();
  const go = async () => { await p.goto('file://' + process.cwd() + '/dashboard.html'); await p.waitForTimeout(2500); };
  await go();
  await p.evaluate(async () => {
    const body = {v:1, at:'2026-09-09T01:44:00Z', regions: JSON.parse(JSON.stringify(stripQ(P.regions))),
                  hedges:P.hedges, fx_track:P.fx_track, fx_manual:P.fx_manual, stmt_asof:P.stmt_asof,
                  closed_ytd:P.closed_ytd, trims:P.trims, dropped:[...DROPPED]};
    body.regions.forEach(r=>r.groups.forEach(g=>g.positions.forEach(q=>{
      if (q.geo) delete q.geo;                       // 還沒 migrateClass 的舊副本
      if (q.derived) { q.mv=(+q.mv||0)-16.1; }       // 鏡像列是舊行情
    })));
    await stashPut(body, 'seed');
  });
  await go();
  const el = await p.evaluate(() => (document.getElementById('alerts')||{innerText:'(沒有 #alerts)'}).innerText.trim());
  console.log(el ? '  → 紅字誤跳了:' + el.split('\n')[0] : '  → 沒有紅字(正確)');
  await b.close();
  process.exit(el ? 1 : 0);
})();
