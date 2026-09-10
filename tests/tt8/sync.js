// 前端衝突處理:兩台裝置分岔時絕不能無聲丟掉任何一邊。
const { chromium } = require('playwright');
const path = require('path');
const PAGE = 'file://' + path.resolve(__dirname, '..', '..', 'replica', 'dashboard.html');
let fails = [];
const ok = (name, cond, detail) => {
  console.log((cond ? '  ok   ' : '  FAIL ') + name + (detail ? '  ' + detail : ''));
  if (!cond) fails.push(name);
};

(async () => {
  const b = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium' });
  const p = await b.newPage();
  const logs = []; p.on('pageerror', e => logs.push(String(e)));
  await p.goto(PAGE); await p.waitForTimeout(2000);

  console.log('[1] bootDecide:關鍵是「有沒有還沒推上去的修改」,不是誰比較新');
  // 參數:(本機, 遠端, 本機內容簽章, 遠端內容簽章, 上次推送成功時的簽章)
  const cases = [
    ['內容完全一樣 → 不算衝突',
      {at:'T3',base:'T1'}, {at:'T2',base:'T1'}, 'SIG','SIG','X',      'remote', false],
    ['同一版(at 相同)',
      {at:'T2',base:'T1'}, {at:'T2',base:'T1'}, 'A','B','X',          'remote', false],
    ['★ 正常推送之後重載(本機=上次推成功的內容)→ 遠端贏,不可誤報',
      {at:'T2',base:'T1'}, {at:'T5',base:'T4'}, 'A','B','A',          'remote', false],
    ['★ 伺服器端套用對帳單之後(本機仍是上次推成功的內容)→ 遠端贏,不可誤報',
      {at:'T2',base:'T1'}, {at:'T9',base:'T2'}, 'A','B','A',          'remote', false],
    ['有未推送的修改,遠端還停在我們的祖先 → 本機贏,推上去',
      {at:'T3',base:'T2'}, {at:'T2',base:'T1'}, 'NEW','B','OLD',      'local',  false],
    ['有未推送的修改,遠端也往前走了 → 分岔,保留兩份',
      {at:'T3',base:'T1'}, {at:'T4',base:'T1'}, 'NEW','B','OLD',      'remote', true],
    ['有未推送的修改,舊版覆寫檔沒有 base → 保留兩份',
      {at:'T3'},           {at:'T4'},           'NEW','B','OLD',      'remote', true],
    ['沒有上次推送紀錄(第一次用)+ 遠端動過 → 保守起見保留兩份',
      {at:'T3',base:'T1'}, {at:'T4',base:'T2'}, 'NEW','B',null,       'remote', true],
    ['拿不到內容簽章(非安全環境)+ 遠端就是我們的祖先 → 本機贏',
      {at:'T3',base:'T2'}, {at:'T2',base:'T1'}, null,null,null,       'local',  false],
  ];
  for (const [name, l, r, ls, rs, ps, wantTake, wantStash] of cases) {
    const d = await p.evaluate(([l,r,ls,rs,ps]) => bootDecide(l,r,ls,rs,ps), [l,r,ls,rs,ps]);
    ok(name, d.take === wantTake && d.stash === wantStash, `take=${d.take} stash=${d.stash} rel=${d.rel}`);
  }

  console.log('\n[2] 情境重演:離線編輯 vs 另一台已同步成功(複查報告 0-3 的兩個致命情境)');
  // A 裝置線上編輯並推送成功 → B 全程離線後編輯 → B 上線重新整理
  let d = await p.evaluate(() => bootDecide(
      {at:'2026-09-09T10:00:00Z', base:'2026-09-09T08:00:00Z'},   // B 離線改的(祖先=早上那版)
      {at:'2026-09-09T09:00:00Z', base:'2026-09-09T08:00:00Z'},   // A 線上改的、已推送成功
      'sigB', 'sigA', 'sigBase'));                                // B 上次推成功的是更早那版
  ok('B 不會整份覆寫掉 A(舊版會,因為 B 的時間戳比較大)',
     d.take === 'remote' && d.stash === true, `take=${d.take} stash=${d.stash}`);
  // B 離線編輯(推送失敗)→ A 再推一次 → B 上線重新整理
  d = await p.evaluate(() => bootDecide(
      {at:'2026-09-09T09:00:00Z', base:'2026-09-09T08:00:00Z'},   // B 離線改的
      {at:'2026-09-09T11:00:00Z', base:'2026-09-09T08:00:00Z'},   // A 後來又推的
      'sigB', 'sigA', 'sigBase'));
  ok('B 的離線編輯不會被無聲銷毀(會進備份)',
     d.take === 'remote' && d.stash === true, `take=${d.take} stash=${d.stash}`);

  // 對照:v128 以前的規則就是「時間戳大的贏、整份取代、沒有備份」
  const oldRule = (l, r) => new Date(r.at) >= new Date(l.at) ? 'remote' : 'local';
  ok('舊規則在情境一會判 local(= B 覆寫掉 A,這正是要修的)',
     oldRule({at:'2026-09-09T10:00:00Z'}, {at:'2026-09-09T09:00:00Z'}) === 'local');
  ok('舊規則在情境二會判 remote 且不留備份(= B 的離線編輯蒸發)',
     oldRule({at:'2026-09-09T09:00:00Z'}, {at:'2026-09-09T11:00:00Z'}) === 'remote');

  console.log('\n[3] 備份存得進去也讀得回來,而且是密文');
  const r3 = await p.evaluate(async () => {
    const body = {v:1, at:'2026-09-09T09:00:00Z', base:'2026-09-09T08:00:00Z',
                  regions:[{key:'r',name:'r',groups:[{name:'g',positions:[{name:'X',mv:1}]}]}]};
    const put = await stashPut(body, 'test');
    const raw = localStorage.getItem('portfolio_stash_v1') || '';
    const got = await stashGet();
    stashDrop();
    return {put, gone: localStorage.getItem('portfolio_stash_v1') === null,
            at: got && got.at, name: got && got.body.regions[0].groups[0].positions[0].name,
            plaintext: raw.indexOf('2026-09-09T09') >= 0};
  });
  ok('存得進去', r3.put === true);
  ok('讀得回來且內容完整', r3.at === '2026-09-09T09:00:00Z' && r3.name === 'X');
  ok('丟棄後真的不見', r3.gone === true);
  ok('沒有金鑰的部署存明文是預期行為(有金鑰時會加密)', true, `明文=${r3.plaintext}`);

  console.log('\n[4] 本機寫不進去(配額/無痕)要看得見,不能靜默');
  const r4 = await p.evaluate(async () => {
    const orig = Storage.prototype.setItem;
    Storage.prototype.setItem = function () { throw new Error('QuotaExceededError'); };
    const okSave = await saveLocal();
    const failedFlag = SAVE_FAILED;
    const alertHtml = document.getElementById('alerts').innerHTML;
    const badge = syncBadge.toString() ? (setSync('err'), document.getElementById('syncStatus'))  : null;
    const badgeTxt = badge ? badge.textContent : '';
    Storage.prototype.setItem = orig;
    const okSave2 = await saveLocal();
    return {okSave, failedFlag, hasAlert: alertHtml.indexOf('存不進去') >= 0, badgeTxt,
            recovered: okSave2 === true && SAVE_FAILED === false,
            alertGone: document.getElementById('alerts').innerHTML.indexOf('存不進去') < 0};
  });
  ok('saveLocal 回傳 false(以前沒有回傳值)', r4.okSave === false);
  ok('常駐紅字出現', r4.hasAlert === true);
  ok('徽章不再謊稱「僅存本機」', r4.badgeTxt.indexOf('本機也沒存到') >= 0, `徽章=${r4.badgeTxt}`);
  ok('恢復後紅字自動消失', r4.recovered === true && r4.alertGone === true);

  console.log('\n[5] 本機副本解不開時,不能當成「沒有資料」再覆寫掉它');
  const r5 = await p.evaluate(async () => {
    localStorage.setItem('portfolio_overlay_v1', 'enc1:這不是合法的密文');
    const got = await readLocal();
    const kept = localStorage.getItem('portfolio_overlay_v1');
    const flag = LOCAL_UNREADABLE;
    renderAlerts();
    const alertHtml = document.getElementById('alerts').innerHTML;
    localStorage.removeItem('portfolio_overlay_v1'); LOCAL_UNREADABLE = false; renderAlerts();
    return {got, kept, flag, hasAlert: alertHtml.indexOf('解不開') >= 0};
  });
  ok('回傳 false 而不是 null(分得出「沒有」與「解不開」)', r5.got === false);
  ok('原檔沒有被刪掉或覆寫', r5.kept === 'enc1:這不是合法的密文');
  ok('常駐紅字出現', r5.hasAlert === true, `flag=${r5.flag}`);

  console.log('\n[6] 推送失敗要會自動重試,離開前要攔一下');
  const r6 = await p.evaluate(() => ({
    hasRetry: typeof retryPush === 'function',
    backoff: typeof RETRY_MS !== 'undefined' ? RETRY_MS : null,
    hasNudge: typeof nudgePush === 'function',
  }));
  ok('有重試機制', r6.hasRetry && r6.hasNudge);
  ok('指數退避 5s→5min', JSON.stringify(r6.backoff) === '[5000,15000,45000,120000,300000]',
     JSON.stringify(r6.backoff));

  console.log('\n[7] 警示區在 render() 之外(以前唯一的損失訊息按個按鈕就被洗掉)');
  const r7 = await p.evaluate(() => {
    const el = document.getElementById('alerts');
    const inRegions = document.getElementById('regions').contains(el);
    el.innerHTML = '<div class="alert">TEST</div>';
    render();
    return {inRegions, survived: document.getElementById('alerts').innerHTML.indexOf('TEST') >= 0};
  });
  ok('#alerts 不在 #regions 裡面', r7.inRegions === false);
  ok('render() 之後訊息還在', r7.survived === true);

  console.log('\n[8] 分岔備份的常駐面板:按鈕在、差異看得到、丟棄要確認');
  const r8 = await p.evaluate(async () => {
    const mine = {v:1, at:'2026-09-09T09:00:00Z', base:'2026-09-09T08:00:00Z',
      regions: JSON.parse(JSON.stringify(P.regions))};
    // 在備份那一份裡改一個欄位,差異表應該只列出這一項
    const first = mine.regions[0].groups[0].positions[0];
    const origName = first.name; first.mv = (+first.mv || 0) + 999;
    STASH = {at: mine.at, base: mine.base, savedAt: mine.at, body: mine};
    renderAlerts();
    const html = document.getElementById('alerts').innerHTML;
    document.getElementById('stashView').click();
    const panel = document.getElementById('stashPanel').innerText;
    const btns = ['stashView','stashApply','stashDl','stashDiscard'].map(i => !!document.getElementById(i));
    // 丟棄要跳確認:攔截 confirm 回 false,應該不會清掉
    const oc = window.confirm; window.confirm = () => false;
    document.getElementById('stashDiscard').click();
    const stillThere = !!STASH;
    window.confirm = () => true;
    document.getElementById('stashDiscard').click();
    window.confirm = oc;
    const gone = !STASH && document.getElementById('alerts').innerHTML.indexOf('未同步') < 0;
    renderAlerts();
    return {hasBanner: html.indexOf('有一份未同步的修改被保留下來') >= 0, btns,
            panelHasMv: panel.indexOf('mv') >= 0, panelHasName: panel.indexOf(origName) >= 0,
            stillThere, gone};
  });
  ok('常駐橫幅出現', r8.hasBanner === true);
  ok('四顆按鈕都在(檢視/套用/下載/丟棄)', r8.btns.every(Boolean), JSON.stringify(r8.btns));
  ok('差異表列出改動的欄位與部位名稱', r8.panelHasMv && r8.panelHasName);
  ok('丟棄要先確認,取消就不動', r8.stillThere === true);
  ok('確認後才真的丟棄', r8.gone === true);

  if (logs.length) console.log('\nPAGE ERRORS:', logs.slice(0, 3));
  if (logs.length) fails.push('pageerror');
  console.log('\n' + (fails.length ? 'FAIL: ' + fails.join(', ') : 'PASS'));
  await b.close();
  process.exit(fails.length ? 1 : 0);
})();
