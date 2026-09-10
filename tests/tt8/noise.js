// 誤報:v129 對「沒有人改過的東西」跳紅字(2026-09-09 線上實際發生,使用者截圖)。
// 畫面上列出的兩類差異,都不是使用者的修改:
//   (a) 鏡像列 Japan Defense 的 mv / be / pl / ret —— resolveDerived() 每次重繪即時算的
//   (b) 五列 SHLD + INNIO + 瑞幸咖啡的 geo —— migrateClass() 一次性搬進來的
const { chromium } = require('playwright');
const path = require('path');
const PAGE = 'file://' + path.resolve(__dirname, '..', '..', 'replica', 'dashboard.html');
const fails = [];
const ok = (n, c, d) => { console.log((c ? '  ok   ' : '  FAIL ') + n + (d ? '  ' + d : '')); if (!c) fails.push(n); };

(async () => {
  const b = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium' });
  const p = await b.newPage();
  const errs = []; p.on('pageerror', e => errs.push(String(e)));
  await p.goto(PAGE); await p.waitForTimeout(2200);

  console.log('[1] 重現截圖裡的兩類雜訊');
  const r1 = await p.evaluate(async () => {
    const clone = o => JSON.parse(JSON.stringify(o));
    // 「雲端那一份」= 目前畫面的狀態(已跑過 migrateClass,鏡像列已被 resolveDerived 寫過)
    const cloud = {regions: clone(stripQ(P.regions)), hedges: P.hedges, fx_track: P.fx_track,
                   fx_manual: P.fx_manual, closed_ytd: P.closed_ytd, trims: P.trims,
                   dropped: [...DROPPED], stmt_asof: P.stmt_asof};
    // 「我的那份」= 一份還沒搬過 geo/theme、鏡像列數字是昨天行情的舊副本
    const mine = clone(cloud);
    let geoCleared = 0, calcChanged = 0;
    mine.regions.forEach(r => r.groups.forEach(g => g.positions.forEach(q => {
      if (q.geo && (GEO_BY_NAME[q.name] || (q.ticker && GEO_BY_TICKER[q.ticker]))) { delete q.geo; geoCleared++; }
      if (q.theme && q.ticker && THEME_BY_TICKER[q.ticker]) delete q.theme;
      if (q.derived) {                       // 昨天的行情算出來的鏡像列數字
        q.mv = (+q.mv || 0) - 16.1; q.be = (+q.be || 0) - 2.3;
        q.pl = (+q.pl || 0) - 15.25; q.ret = (+q.ret || 0) - 1.02; calcChanged++;
      }
    })));
    const [sa, sb] = [await contentSig(mine), await contentSig(cloud)];
    const rows = stashDiff(mine, cloud);
    return {geoCleared, calcChanged, same: sa === sb, rows: rows.length,
            sample: rows.slice(0, 5).map(x => String(x[0]))};
  });
  ok('確實造出了截圖裡那些差異(geo 被清空、鏡像列數字不同)',
     r1.geoCleared >= 5 && r1.calcChanged >= 1, `geo ${r1.geoCleared} 筆 · 鏡像列 ${r1.calcChanged} 筆`);
  ok('★ 正規化之後兩份的內容簽章相同 → 不會誤報分岔', r1.same === true);
  ok('★ 差異表也不再列出這些雜訊', r1.rows === 0, `rows=${r1.rows} ${JSON.stringify(r1.sample)}`);

  console.log('\n[2] 真的被改過的東西仍然要比得出來(不能為了消音就變成什麼都測不到)');
  const r2 = await p.evaluate(async () => {
    const clone = o => JSON.parse(JSON.stringify(o));
    const cloud = {regions: clone(stripQ(P.regions)), closed_ytd: P.closed_ytd, trims: P.trims};
    const out = {};
    for (const [name, mut] of [
      ['改股數',   q => { q.units_manual = (+q.units_manual || 0) + 100; }],
      ['改成本',   q => { q.cost = (+q.cost || 0) + 1; }],
      ['改名稱',   q => { q.name = q.name + '(改過)'; }],
      ['手動改 geo', q => { q.geo = 'ZZ'; }],
      ['改備註',   q => { q.note = '手動加註'; }],
    ]) {
      const mine = clone(cloud);
      const first = mine.regions[0].groups[0].positions[0];
      mut(first);
      const [sa, sb] = [await contentSig(mine), await contentSig(cloud)];
      out[name] = {differs: sa !== sb, rows: stashDiff(mine, cloud).length};
    }
    // 出清 / 減碼紀錄的增減也要測得到
    const mine2 = clone(cloud); mine2.closed_ytd = (cloud.closed_ytd || []).concat([{name:'X', on:'2026-09-09', usd_k:1}]);
    out['多一筆出清'] = {differs: (await contentSig(mine2)) !== (await contentSig(cloud)),
                       rows: stashDiff(mine2, cloud).length};
    return out;
  });
  Object.entries(r2).forEach(([k, v]) =>
    ok(`${k} 仍然偵測得到`, v.differs === true, `簽章不同=${v.differs} 差異列=${v.rows}`));

  console.log('\n[3] v129 誤存下來的舊備份,載入時要靜靜清掉(不要每次都跳一次)');
  const r3 = await p.evaluate(async () => {
    const clone = o => JSON.parse(JSON.stringify(o));
    const body = {v: 1, at: '2026-09-08T00:25:00Z', regions: clone(stripQ(P.regions)),
                  hedges: P.hedges, fx_track: P.fx_track, fx_manual: P.fx_manual,
                  stmt_asof: P.stmt_asof, closed_ytd: P.closed_ytd, trims: P.trims,
                  dropped: [...DROPPED]};
    body.regions.forEach(r => r.groups.forEach(g => g.positions.forEach(q => {
      if (q.geo) delete q.geo;
      if (q.derived) { q.mv = (+q.mv || 0) - 16.1; }
    })));
    await stashPut(body, 'v129-noise');
    // 模擬開機時那段清理
    const st = await stashGet();
    const [a, b] = [await contentSig(st.body),
                    await contentSig({regions: P.regions, hedges: P.hedges, fx_track: P.fx_track,
                                      fx_manual: P.fx_manual, closed_ytd: P.closed_ytd,
                                      trims: P.trims, dropped: [...DROPPED], stmt_asof: P.stmt_asof})];
    const cleaned = (a && b && a === b);
    if (cleaned) stashDrop();
    return {cleaned, gone: localStorage.getItem('portfolio_stash_v1') === null};
  });
  ok('★ 判定為沒有差異', r3.cleaned === true);
  ok('★ 備份被清掉,紅字不會再出現', r3.gone === true);

  console.log('\n[4] 真的有未同步修改的備份,不能被這段清理誤刪');
  const r4 = await p.evaluate(async () => {
    const clone = o => JSON.parse(JSON.stringify(o));
    const body = {v: 1, at: '2026-09-08T00:25:00Z', regions: clone(stripQ(P.regions)),
                  hedges: P.hedges, fx_track: P.fx_track, fx_manual: P.fx_manual,
                  stmt_asof: P.stmt_asof, closed_ytd: P.closed_ytd, trims: P.trims,
                  dropped: [...DROPPED]};
    body.regions[0].groups[0].positions[0].units_manual = 999999;   // 真的改過
    await stashPut(body, 'real');
    const st = await stashGet();
    const [a, b] = [await contentSig(st.body),
                    await contentSig({regions: P.regions, hedges: P.hedges, fx_track: P.fx_track,
                                      fx_manual: P.fx_manual, closed_ytd: P.closed_ytd,
                                      trims: P.trims, dropped: [...DROPPED], stmt_asof: P.stmt_asof})];
    const kept = !(a && b && a === b);
    stashDrop();
    return {kept};
  });
  ok('★ 保留(不會把真的修改當成雜訊清掉)', r4.kept === true);

  console.log('\n[5] v130 清不掉舊備份的真正原因:拿原始覆寫檔去比「合併後的 P」');
  // P = bundle + 覆寫檔。closed_ytd / trims 會多出 bundle 才有的那幾筆,
  // 而備份裡只有覆寫檔那一半 —— 永遠比得出差異,紅字永遠清不掉。
  const r5 = await p.evaluate(async () => {
    const clone = o => JSON.parse(JSON.stringify(o));
    const overlay = {v: 1, at: '2026-09-09T01:44:00Z', regions: clone(stripQ(P.regions)),
                     hedges: P.hedges, fx_track: P.fx_track, fx_manual: P.fx_manual,
                     stmt_asof: P.stmt_asof, closed_ytd: [{name: 'A', on: '2026-09-01', usd_k: 10}],
                     trims: [], dropped: [...DROPPED]};
    // 合併後的畫面狀態:bundle 另外帶了一筆出清、一筆減碼
    const savedC = P.closed_ytd, savedT = P.trims;
    P.closed_ytd = overlay.closed_ytd.concat([{name: 'BUNDLE-ONLY', on: '2026-08-01', usd_k: 5}]);
    P.trims = [{name: 'BUNDLE-TRIM', to: '2026-08-01', u0: 10, u1: 5}];

    REMOTE_SNAP = null;                                   // 先模擬 v130 的行為(跟 P 比)
    const vsP = stashDiff(overlay, stashPeer()).length;
    REMOTE_SNAP = overlay;                                // v131:跟原始覆寫檔比
    const vsRemote = stashDiff(overlay, stashPeer()).length;
    const [a, b] = [await contentSig(overlay), await contentSig(stashPeer())];

    P.closed_ytd = savedC; P.trims = savedT; REMOTE_SNAP = null;
    return {vsP, vsRemote, sigSame: a === b};
  });
  ok('★ 重現:拿備份比合併後的 P 會憑空多出差異', r5.vsP > 0, `差異列=${r5.vsP}`);
  ok('★ 改成比原始覆寫檔之後,差異歸零', r5.vsRemote === 0, `差異列=${r5.vsRemote}`);
  ok('★ 簽章也一致 → 開機時清得掉', r5.sigSame === true);

  console.log('\n[6] 字串型數字不該算成差異(本機副本是原始存檔,P 是正規化過的)');
  const r6 = await p.evaluate(async () => {
    const clone = o => JSON.parse(JSON.stringify(o));
    const a = {regions: clone(stripQ(P.regions))}, b = {regions: clone(stripQ(P.regions))};
    const q = a.regions[0].groups[0].positions[0];
    ['mv', 'cost', 'units_manual'].forEach(f => { if (q[f] != null) q[f] = String(q[f]); });
    return {rows: stashDiff(a, b).length, same: (await contentSig(a)) === (await contentSig(b))};
  });
  ok('差異表不列它', r6.rows === 0, `rows=${r6.rows}`);
  ok('簽章相同', r6.same === true);

  console.log('\n[7] 差異表是空的就不該掛紅字');
  const r7 = await p.evaluate(async () => {
    const clone = o => JSON.parse(JSON.stringify(o));
    const overlay = {v: 1, at: '2026-09-09T01:44:00Z', regions: clone(stripQ(P.regions)),
                     hedges: P.hedges, fx_track: P.fx_track, fx_manual: P.fx_manual,
                     stmt_asof: P.stmt_asof, closed_ytd: P.closed_ytd, trims: P.trims,
                     dropped: [...DROPPED]};
    REMOTE_SNAP = overlay;
    STASH = {at: overlay.at, body: overlay};
    await stashPut(overlay, 'empty-diff');
    renderAlerts();
    const shown = document.getElementById('alerts').innerHTML.indexOf('未同步') >= 0;
    const cleared = STASH === null && localStorage.getItem('portfolio_stash_v1') === null;
    // 真的有差異的那份不能被這條清掉
    const real = clone(overlay); real.regions[0].groups[0].positions[0].units_manual = 12345;
    STASH = {at: real.at, body: real}; await stashPut(real, 'real');
    renderAlerts();
    const keptShown = document.getElementById('alerts').innerHTML.indexOf('未同步') >= 0;
    STASH = null; stashDrop(); REMOTE_SNAP = null; renderAlerts();
    return {shown, cleared, keptShown};
  });
  ok('★ 沒有差異 → 紅字不出現', r7.shown === false);
  ok('★ 而且備份被清掉,不會下次再跳', r7.cleared === true);
  ok('★ 真的有差異 → 紅字照常出現', r7.keptShown === true);

  console.log('\n[8] 真正害備份清不掉的那一項:清單被 cleanTrims 重排過,內容一樣但簽章不同');
  // 線上實測(v130):trims 兩邊都是 24 筆、stashDiff 0 列,contentSig 卻對不起來,
  // 於是自動清理永遠不觸發。cleanTrims 會重排鍵的順序、丟掉不認識的欄位。
  const r8 = await p.evaluate(async () => {
    const clone = o => JSON.parse(JSON.stringify(o));
    const base = {regions: clone(stripQ(P.regions)),
                  trims: [{ticker: '2330:TPE', name: 'X', u0: 100, u1: 50, to: '2026-08-01',
                           cur: 'TWD', 沒人認識的欄位: 1}],
                  closed_ytd: [{name: 'A', on: '2026-09-01', usd_k: 10, junk: 'z'}]};
    const cleaned = clone(base);
    cleaned.trims = cleanTrims(base.trims);            // 畫面上那份會被清洗
    cleaned.closed_ytd = cleanClosed(base.closed_ytd);
    const rawDiffers = JSON.stringify(base.trims) !== JSON.stringify(cleaned.trims);
    const [a, b] = [await contentSig(base), await contentSig(cleaned)];
    // 順序不同也不該算差異
    const shuffled = clone(cleaned);
    shuffled.trims = [{name: 'B', u0: 9, u1: 1, to: '2026-07-01'}].concat(shuffled.trims);
    const withB = clone(cleaned);
    withB.trims = withB.trims.concat([{name: 'B', u0: 9, u1: 1, to: '2026-07-01'}]);
    const orderSame = (await contentSig(shuffled)) === (await contentSig(withB));
    // 但真的改過一筆就要測得到
    const edited = clone(cleaned); edited.trims[0].u1 = 60;
    const editedDiff = (await contentSig(edited)) !== (await contentSig(cleaned));
    const editedRows = stashDiff(edited, cleaned).length;
    return {rawDiffers, sigSame: a === b, orderSame, editedDiff, editedRows,
            diffRows: stashDiff(base, cleaned).length};
  });
  ok('確實重排過(原始 JSON 與清洗後不同)', r8.rawDiffers === true);
  ok('★ 正規化後簽章相同 → 自動清理觸發得了', r8.sigSame === true);
  ok('★ 差異表也是 0 列', r8.diffRows === 0, `rows=${r8.diffRows}`);
  ok('清單順序不同不算差異(合併順序不影響)', r8.orderSame === true);
  ok('★ 真的改過一筆減碼仍然測得到', r8.editedDiff === true && r8.editedRows > 0,
     `簽章不同=${r8.editedDiff} 差異列=${r8.editedRows}`);

  console.log('\n[9] 線上實測抓到的真正殘留:`"on": null` 與「沒有 on」');
  // 2026-09-10 對線上 v131 做種備份實測,唯一剩下的差異是這個:
  //   我的那份 {"name":"…","on":null,"to":"2026-09-02",…}
  //   目前雲端 {"name":"…",        "to":"2026-09-02",…}
  // 六筆自動記下的減碼沒有日期,覆寫檔存成 null、合併後那份省略掉,簽章永遠對不起來。
  const r9 = await p.evaluate(async () => {
    const j = x => JSON.stringify(x), clone = x => JSON.parse(JSON.stringify(x));
    const A = [{name: 'Z', ticker: 'ZZ:NYSE', cur: 'USD', on: null, to: '2026-09-02',
                u0: 3663, u1: 1963, src: 'stmt'}];
    const B = clone(A); delete B[0].on;
    const C = clone(B); C[0].u1 = 1000;
    const P1 = [{key: 'r', name: 'r', groups: [{name: 'g', positions: [{name: 'X', ticker: 'ZZ:NYSE', geo: null}]}]}];
    const P2 = clone(P1); delete P2[0].groups[0].positions[0].geo;
    const P3 = clone(P2); P3[0].groups[0].positions[0].geo = 'ZZ';
    // 鍵的順序也不該影響
    const D = [{to: '2026-09-02', u1: 1963, u0: 3663, cur: 'USD', ticker: 'ZZ:NYSE', name: 'Z', src: 'stmt'}];
    return {trimNull: j(normList('t', A)) === j(normList('t', B)),
            geoNull: j(normForSig(P1)) === j(normForSig(P2)),
            keyOrder: j(normList('t', B)) === j(normList('t', D)),
            realTrim: j(normList('t', B)) !== j(normList('t', C)),
            realGeo: j(normForSig(P2)) !== j(normForSig(P3))};
  });
  ok('★ on:null 與沒有 on 算相同', r9.trimNull === true);
  ok('★ geo:null 與沒有 geo 算相同', r9.geoNull === true);
  ok('鍵的順序不影響', r9.keyOrder === true);
  ok('★ 真的改過 u1 仍然測得到', r9.realTrim === true);
  ok('★ 真的填了 geo 仍然測得到', r9.realGeo === true);

  if (errs.length) { console.log('\nPAGE ERRORS:', errs.slice(0, 3)); fails.push('pageerror'); }
  console.log('\n' + (fails.length ? 'FAIL: ' + fails.join(' | ') : 'PASS'));
  await b.close();
  process.exit(fails.length ? 1 : 0);
})();
