// 一鍵隱藏未上市部位(v136,使用者 2026-09-14 決定的口徑):
//   範圍 = 所有報表 NAV 的靜態部位(PE / Activist / 海外基金),台灣基金是每日淨值算上市;
//   只藏列,KPI / 小計 / 配置比例一律不變;偏好只存本機;編輯模式一律全部顯示。
// 用線上真實資料(replica)驗,數字不能寫死在這裡 —— 檔數與金額都從畫面上量。
const { chromium } = require('playwright');
const path = require('path');
const PAGE = 'file://' + path.resolve(__dirname, '..', '..', 'replica', 'dashboard.html');
const fails = [];
const ok = (n, c, d) => { console.log((c ? '  ok   ' : '  FAIL ') + n + (d ? '  ' + d : '')); if (!c) fails.push(n); };

const snap = () => ({
  kpi: document.getElementById('kpis').innerText.replace(/\s+/g, ' ').trim(),
  subtotals: [...document.querySelectorAll('#regions tr.subtotal')].map(r => r.innerText.replace(/\s+/g, ' ').trim()),
  regionHeads: [...document.querySelectorAll('#regions .card > h2')].map(h => h.innerText.replace(/\s+/g, ' ').trim()),
  rowsAll: document.querySelectorAll('#regions tbody tr:not(.subtotal):not(.unlnote)').length,
  rowsUnl: document.querySelectorAll('#regions tr.unl').length,
  unlVisible: [...document.querySelectorAll('#regions tr.unl')].filter(r => r.offsetParent !== null).length,
  otherVisible: [...document.querySelectorAll('#regions tbody tr:not(.unl):not(.subtotal):not(.unlnote)')]
    .filter(r => r.offsetParent !== null && !r.closest('.grp.collapsed')).length,
  notesVisible: [...document.querySelectorAll('#regions tr.unlnote')].filter(r => r.offsetParent !== null).length,
  noteSum: [...document.querySelectorAll('#regions tr.unlnote')]
    .map(r => +(r.innerText.match(/已隱藏 (\d+) 檔/) || [0, 0])[1]).reduce((s, v) => s + v, 0),
  btn: (document.getElementById('unlToggle') || {}).textContent || '',
  hideCls: document.body.classList.contains('hideUNL'),
  expected: allPos().filter(isUnlisted).length,
  staticNonDup: nonDup().filter(p => p.kind !== 'live' && p.kind !== 'pending').length,
  ls: (() => { try { return localStorage.getItem('hide_unlisted_v1'); } catch (e) { return 'ERR'; } })(),
});

(async () => {
  const b = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium' });
  const p = await b.newPage({ viewport: { width: 1500, height: 1400 } });
  const errs = []; p.on('pageerror', e => errs.push(String(e)));
  await p.goto(PAGE); await p.waitForTimeout(2200);
  // 全部展開,offsetParent 才量得準(收合的組整張表都不可見)
  await p.evaluate(() => { COLLAPSED.clear(); render(); });
  await p.waitForTimeout(300);

  console.log('[1] 預設:全部顯示,按鈕在,標記的列數 = 靜態部位數');
  const s0 = await p.evaluate(snap);
  ok('沒有 JS 錯誤', errs.length === 0, errs.join(' | '));
  ok('工具列有「隱藏未上市部位」按鈕', /隱藏未上市部位/.test(s0.btn), s0.btn);
  ok('按鈕上的檔數 = isUnlisted 的列數', s0.btn.includes(`(${s0.expected})`), s0.btn);
  ok('標記為 unl 的列數 = isUnlisted 的列數(> 0)', s0.rowsUnl === s0.expected && s0.expected > 0,
     `unl=${s0.rowsUnl} expected=${s0.expected}`);
  ok('isUnlisted 至少涵蓋所有非重複的靜態部位', s0.expected >= s0.staticNonDup,
     `expected=${s0.expected} 非重複靜態=${s0.staticNonDup}`);
  ok('預設沒有藏(unl 列全部可見、提示列不可見)', !s0.hideCls && s0.unlVisible === s0.rowsUnl && s0.notesVisible === 0);

  console.log('\n[2] 按一下:未上市列消失、其他列不動、KPI / 小計 / 區塊標題一個字都不變');
  await p.click('#unlToggle'); await p.waitForTimeout(200);
  const s1 = await p.evaluate(snap);
  ok('body 有 hideUNL', s1.hideCls);
  ok('未上市列全部不可見', s1.unlVisible === 0, `visible=${s1.unlVisible}`);
  ok('其他列一列都沒少', s1.otherVisible === s0.otherVisible, `${s0.otherVisible} → ${s1.otherVisible}`);
  ok('KPI 完全不變', s1.kpi === s0.kpi);
  ok('每一組小計完全不變', JSON.stringify(s1.subtotals) === JSON.stringify(s0.subtotals));
  ok('每一區標題(市值 / 佔比 / YTD)完全不變', JSON.stringify(s1.regionHeads) === JSON.stringify(s0.regionHeads));
  ok('提示列出現,且合計的檔數 = 藏掉的列數', s1.notesVisible > 0 && s1.noteSum === s0.rowsUnl,
     `notes=${s1.notesVisible} sum=${s1.noteSum}`);
  ok('按鈕文字變成「顯示」', /顯示未上市部位/.test(s1.btn), s1.btn);
  ok('偏好寫進 localStorage(hide_unlisted_v1 = 1)', s1.ls === '1', String(s1.ls));

  console.log('\n[3] 重新整理:偏好留著,列仍然是藏的;重繪(render)之後也還是藏的');
  await p.reload(); await p.waitForTimeout(2200);
  await p.evaluate(() => { COLLAPSED.clear(); render(); });
  await p.waitForTimeout(300);
  const s2 = await p.evaluate(snap);
  ok('重載後仍隱藏', s2.hideCls && s2.unlVisible === 0 && /顯示未上市部位/.test(s2.btn));
  ok('重載後 KPI 與小計仍與原本相同', s2.kpi === s0.kpi && JSON.stringify(s2.subtotals) === JSON.stringify(s0.subtotals));

  console.log('\n[4] 編輯模式一律全部顯示(不然藏起來的部位沒辦法編);離開編輯模式回到隱藏');
  await p.click('#editToggle'); await p.waitForTimeout(300);
  const s3 = await p.evaluate(snap);
  ok('編輯模式:unl 列全部可見、沒有 hideUNL', !s3.hideCls && s3.unlVisible === s3.rowsUnl && s3.rowsUnl > 0,
     `visible=${s3.unlVisible}/${s3.rowsUnl}`);
  await p.click('#editToggle'); await p.waitForTimeout(300);
  await p.evaluate(() => { COLLAPSED.clear(); render(); });
  await p.waitForTimeout(200);
  const s4 = await p.evaluate(snap);
  ok('離開編輯模式:又藏起來', s4.hideCls && s4.unlVisible === 0);

  console.log('\n[5] 再按一下:全部回來,偏好清掉');
  await p.click('#unlToggle'); await p.waitForTimeout(200);
  const s5 = await p.evaluate(snap);
  ok('全部顯示、提示列消失、localStorage = 0', !s5.hideCls && s5.unlVisible === s5.rowsUnl && s5.notesVisible === 0 && s5.ls === '0');

  console.log('\n[6] 隱藏不會產生任何修改:不寫覆寫檔、不排推送');
  const s6 = await p.evaluate(async () => {
    const before = await contentSig(snapshot());
    document.getElementById('unlToggle').click();
    await new Promise(r => setTimeout(r, 200));
    const after = await contentSig(snapshot());
    return { same: before === after, pending: !!PENDING_PUSH };
  });
  ok('內容簽章不變', s6.same);
  ok('沒有待推送', !s6.pending);
  ok('全程沒有 JS 錯誤', errs.length === 0, errs.join(' | '));

  await b.close();
  console.log('\n' + (fails.length ? '失敗:' + fails.join(', ') : '全部通過'));
  process.exit(fails.length ? 1 : 0);
})();
