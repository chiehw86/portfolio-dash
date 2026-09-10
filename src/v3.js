const P = window.__P__;
// 未實現損益欄的顯示狀態:預設隱藏。獨立存在 localStorage,不進覆寫檔,
// 所以切換它不會產生 commit、也不影響其他裝置的持倉資料。
const PL_KEY = 'hide_unreal_v1';
let HIDEPL = true;
try { const v = localStorage.getItem(PL_KEY); if (v !== null) HIDEPL = v === '1'; } catch (e) {}
function applyHidePL() {
  document.body.classList.toggle('hidePL', HIDEPL);
  const b = document.getElementById('plToggle');
  if (b) b.textContent = HIDEPL ? '＋ 顯示未實現損益' : '－ 隱藏未實現損益';
}
let EDIT = false;      // 編輯模式開關
// 靜態部位從未蓋章時的預設「資料截至」:目前這批 NAV 來自 2026-08-19 的報表
const NAV_ASOF_DEFAULT = '2026-08-19';
const todayTaipei = () => new Date(Date.now() + 8*3600e3).toISOString().slice(0, 10);
const stampNav = p => { if (p && p.kind !== 'live' && !p.derived) p.nav_asof = todayTaipei(); };
// 群組摺疊狀態(純顯示偏好,不含資料,明文存無妨)
const COLL_KEY = 'grp_collapsed_v2';   // v2:存 basket 名稱的雜湊,不存名稱本身(同 origin 的其他頁面讀得到 localStorage)
const COLLAPSED = new Set((() => {
  try { return JSON.parse(localStorage.getItem(COLL_KEY) || '[]'); } catch (e) { return []; }
})());
const saveColl = () => { try { localStorage.setItem(COLL_KEY, JSON.stringify([...COLLAPSED])); } catch (e) {} };

// ── 頂部導航:區域 chips + 點選後展開該區 basket 子列 ─────────────
let SUBREG = null;
function renderNav() {
  const nav = $('topnav');
  if (!nav) return;
  const short = r => r.name.replace('部位', '').replace('(跨區分析視角)', '').replace('全球', '');
  const chips = P.regions.map(r =>
    `<a href="#sec-${esc(r.key)}" class="regchip${SUBREG === r.key ? ' on' : ''}" data-reg="${esc(r.key)}">${esc(short(r))}${r.groups.length > 1 ? ' ▾' : ''}</a>`);
  if ((P.closed_ytd || []).length || (P.trims || []).length)
    chips.push('<a href="#closedcard">已出清/減碼</a>');
  chips.push('<a href="#alloccard">配置</a>', '<a href="#histcard">走勢</a>',
             '<a href="#fxcard">曝險</a>', '<a href="#tilesec">指數</a>');
  const sr = P.regions.find(r => r.key === SUBREG);
  const sub = (sr && sr.groups.length > 1)
    ? `<div class="subnav">${sr.groups.map(g =>
        `<a href="#" data-gkey="${esc(sr.key + '||' + g.name)}">${esc(g.name)}</a>`).join('')}</div>`
    : '';
  nav.innerHTML = `<div class="navrow">${chips.join('')}</div>${sub}`;
  nav.querySelectorAll('.regchip').forEach(a => a.addEventListener('click', () => {
    const k = a.dataset.reg;
    SUBREG = (SUBREG === k) ? null : k;      // 再點一次收合子列;href 照常跳區塊
    renderNav();
  }));
  nav.querySelectorAll('.subnav a').forEach(a => a.addEventListener('click', e => {
    e.preventDefault();
    const el = document.querySelector(`.grp[data-gkey="${CSS.escape(a.dataset.gkey)}"]`);
    if (el) el.scrollIntoView({behavior: 'smooth'});
  }));
}
let OPEN = null;       // 目前展開編輯的部位 key
const tip = document.getElementById('tip');
const $ = id => document.getElementById(id);
// 所有數字格式化一律先 Number():String.prototype.toLocaleString 會把字串原樣吐回,
// 覆寫檔裡若把 mv 存成字串(任何持觀看密碼者都做得到),那段字串就會不經 esc() 直接
// 進 innerHTML —— 2026-09-08 複審實測可注入 <img onerror>。非有限值一律顯示 —。
const _n = x => { const v = typeof x === 'number' ? x : Number(x); return Number.isFinite(v) ? v : null; };
const fmt0 = x => { const v = _n(x); return v == null ? '—' : v.toLocaleString('en-US', {maximumFractionDigits: 0}); };
const fmt1 = x => { const v = _n(x); return v == null ? '—' : v.toLocaleString('en-US', {minimumFractionDigits: 0, maximumFractionDigits: 1}); };
const sign0 = x => { const v = _n(x); return v == null ? '—' : (v >= 0 ? '+' : '') + fmt0(v); };
const spct = x => { const v = _n(x); return v == null ? '—' : (v >= 0 ? '+' : '') + v.toFixed(1) + '%'; };
const cls = x => (_n(x) ?? 0) >= 0 ? 'up' : 'down';
const SLOTS = ['s1','s2','s3','s4','s5','s6','s7','s8'];
const SLOTS12 = SLOTS.concat(['s9','s10','s11','s12']);   // 配置分析的類別比八個多

// ══ 兩個配置維度 ══════════════════════════════════════════════════════
// 儀表板的「區域」其實混了地理(中國/台股/日本)與主題(全球主題/半導體),
// 拆成兩個各自加總 100% 的維度來看:
//   地區 —— 底層資產在哪個國家,不是掛牌地(與日圓穿透、ETF 拆分列同一套想法)
//   主題 —— 這筆部位在做什麼策略,可以跨地區(日本重工算國防、台股晶圓廠算半導體)
// 兩者都可以直接在部位上寫 geo / theme 欄位指定,沒寫才走下面的推導。
const GEONAME = {US:'美國', JP:'日本', TW:'台灣', KR:'韓國', CN:'中國',
                 EU:'歐洲', IL:'以色列', OT:'其他 / 全球'};
const GEO_BY_NAME = {          // 拆分列直接照拆列歸屬(遷移用,見 migrateClass)
  'SHLD 美國成分':'US', 'SHLD 歐洲成分':'EU', 'SHLD 韓國成分':'KR',   // mig:v124 移除
  'SHLD 以色列成分':'IL', 'SHLD 其他/未列示':'OT'};   // mig:v124 移除
const GEO_BY_TICKER = {        // 掛牌地與底層國家不一致的幾檔
  'BESIY:OTCMKTS':'EU', 'CAMT:NASDAQ':'IL', 'INIO:NASDAQ':'EU', 'LKNCY:OTCMKTS':'CN'};   // mig:v124 移除
const EXGEO = {TPE:'TW', TWFUND:'TW', TYO:'JP', KRX:'KR', HKG:'CN', SHA:'CN', SHE:'CN',
               LON:'EU', AMS:'EU', ETR:'EU', EPA:'EU', BIT:'EU', STO:'EU'};
const EXPGEO = {JPY:'JP', KRW:'KR', TWD:'TW', CNY:'CN', HKD:'CN', EUR:'EU'};
// 2026-09-08:GEO_BY_NAME / GEO_BY_TICKER / THEME_BY_TICKER 這三張表把持股代號寫在
// 公開 repo 的程式碼裡。改成一次性搬進部位的 geo / theme 欄位(存進加密覆寫檔),
// 下一版就把這三張表刪掉。migrateClass() 在載入後執行,有搬動就推送一次。
function migrateClass() {
  let n = 0;
  P.regions.forEach(r => r.groups.forEach(g => g.positions.forEach(p => {
    if (!p || typeof p !== 'object') return;
    if (!p.geo) {
      const g2 = GEO_BY_NAME[p.name] || (p.ticker && GEO_BY_TICKER[p.ticker]);
      if (g2) { p.geo = g2; n++; }
    }
    if (!p.theme && p.ticker && THEME_BY_TICKER[p.ticker]) { p.theme = THEME_BY_TICKER[p.ticker]; n++; }
  })));
  return n;
}
function geoOf(p, regKey) {
  if (p.geo) return p.geo;
  if (GEO_BY_NAME[p.name]) return GEO_BY_NAME[p.name];
  if (p.ticker && GEO_BY_TICKER[p.ticker]) return GEO_BY_TICKER[p.ticker];
  if (p.exp_cur) return EXPGEO[p.exp_cur] || 'OT';
  if (regKey === 'japan') return 'JP';        // PE 與 Activist 沒有代號,靠所屬區
  if (regKey === 'china') return 'CN';
  if (regKey === 'taiwan') return 'TW';
  const ex = (p.ticker || '').split(':')[1];
  return (ex && EXGEO[ex]) || 'US';           // 其餘為美國掛牌/美元計價
}

// 區域|群組 → 主題:用群組名裡的關鍵字推,不在程式碼裡列 basket 名單(程式碼是公開的)。
const REGCN = {taiwan:'台股', japan:'日本', china:'中國'};
function grpTheme(regKey, gname) {
  const g = String(gname || ''), rc = REGCN[regKey] || '';
  if (/defen|國防/i.test(g)) return '國防';
  if (/power|nuclear|電力|核能/i.test(g)) return '電力與核能';
  if (/health|bio|pharma|醫療/i.test(g)) return '醫療';
  if (/packag|封裝/i.test(g)) return '先進封裝';
  if (/semi|半導體/i.test(g)) return '半導體';
  if (/active fund|主動/i.test(g)) return rc + '主動基金';
  if (/activist|行動/i.test(g)) return rc + '行動派';
  if (/\bPE\b|私募/i.test(g)) return 'PE / 私募';
  if (/個股|stock/i.test(g)) return rc + '個股';
  if (regKey === 'china') return '中國網路與消費';
  return g;
}
const THEME_BY_TICKER = {      // 日本重工四檔(遷移用,見 migrateClass)
  '7011:TYO':'國防', '7012:TYO':'國防', '7013:TYO':'國防', '5631:TYO':'國防'};   // mig:v124 移除
// 半導體區的重複列示本身就是「這檔也算半導體」的標記,拿它當主題歸屬,
// 同時掛在台股與半導體的部位才不會被算成台股個股。
// (「TW Active Funds」是整個群組的合計、沒有代號,所以台股基金仍歸台股主動基金。)
function semiTags() {
  const m = {};
  P.regions.forEach(r => { if (r.key !== 'semi') return;
    r.groups.forEach(g => g.positions.forEach(p => {
      if (p.dup && p.ticker) m[p.ticker] = grpTheme('semi', g.name); })); });
  return m;
}
function themeOf(p, regKey, gname, tags) {
  if (p.theme) return p.theme;
  if (p.ticker && THEME_BY_TICKER[p.ticker]) return THEME_BY_TICKER[p.ticker];
  if (p.ticker && tags[p.ticker]) return tags[p.ticker];
  return grpTheme(regKey, gname);
}
const esc = s => String(s ?? '').replace(/[&<>"'`]/g, c =>
  ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;','`':'&#96;'}[c]));
// FNV-1a 32 位元:給本機偏好與作廢清單當識別碼用(不是密碼學雜湊)。
// 與 build_dashboard_v3.py 的 _fnv 必須算出相同值。
const fnv = s => {
  let h = 0x811c9dc5;
  for (const b of new TextEncoder().encode(String(s ?? ''))) { h ^= b; h = Math.imul(h, 0x01000193) >>> 0; }
  return h.toString(16).padStart(8, '0');
};

const FX = window.__FX__ || {};
// 已出清部位的報價(以代號為鍵)。刻意不放進 closed_ytd 的項目裡:那個結構會被
// 網頁存回覆寫檔,價格一旦寫進去就會凍住,之後每次載入都拿舊值蓋掉新報價。
const CLOSEDQ = window.__CLOSEDQ__ || {};
// closed_ytd 只保留「使用者資料」;舊版把報價寫進項目裡,載入時一併清掉,
// 下次儲存覆寫檔就乾淨了。
const CLOSED_KEYS = ['name', 'usd_k', 'on', 'ticker', 'exit', 'cur', 'units'];
// 建置端判定要作廢的出清紀錄([名稱, 出清日, 四捨五入後的金額])。
// 光在建置端刪掉不夠:瀏覽器有一份本機副本、伺服器上有一份覆寫檔,
// 兩邊載入時都會把 closed_ytd 整份蓋回去,作廢的那筆會一直復活。
// 作廢清單存的是 fnv(name|出清日|四捨五入金額),不是名字 —— 建置腳本在公開 repo 上,
// 不能留任何持倉字串。DROPPED 是三方(建置、覆寫檔、本機)的聯集,刪除才會「黏住」:
// 以前刪掉一筆之後,重新載入時另一份副本會把它救回來,只有改建置腳本才刪得掉。
const DROPPED = new Set((Array.isArray(window.__CLOSEDDROP__) ? window.__CLOSEDDROP__ : []).map(String));
const dropKey = c => fnv(`${c && c.name}|${c && c.on}|${Math.floor((+(c && c.usd_k) || 0) + 0.5)}`);
const isDropped = c => DROPPED.has(dropKey(c));
const mergeDropped = arr => { (Array.isArray(arr) ? arr : []).forEach(x => { if (typeof x === 'string' && /^[0-9a-f]{8}$/.test(x)) DROPPED.add(x); }); };
const _numOrNull = v => { const n = Number(v); return (v == null || v === '' || !Number.isFinite(n)) ? null : n; };
const cleanClosed = arr => (Array.isArray(arr) ? arr : []).filter(c => c && typeof c === 'object' && !isDropped(c)).map(c => {
  const o = {};
  CLOSED_KEYS.forEach(k => { if (c[k] !== undefined) o[k] = c[k]; });
  // 遠端覆寫檔不經過伺服器清洗就進瀏覽器:型別在這裡統一,否則 (c.on||'').slice 這類呼叫會讓 render 掛掉
  ['name', 'on', 'ticker', 'cur'].forEach(k => { if (o[k] != null) o[k] = String(o[k]).slice(0, 200); });
  ['usd_k', 'exit', 'units'].forEach(k => { if (o[k] != null) { const n = _numOrNull(o[k]); if (n == null) delete o[k]; else o[k] = n; } });
  return o;
});
// 減碼(部分出脫)紀錄:出清有 closed_ytd,減碼以前沒有地方留 —— 對帳單一套用
// 舊股數就被蓋掉,「當初減的那批後來怎麼樣」再也算不出來。
// 同樣只留使用者資料,報價一律走 CLOSEDQ,寫進項目就會被覆寫檔凍住。
const TRIM_KEYS = ['name', 'ticker', 'cur', 'code', 'on', 'to', 'u0', 'u1',
                   'exit', 'est', 'real_k', 'src'];
const cleanTrims = arr => (Array.isArray(arr) ? arr : []).filter(t => t && typeof t === 'object').map(t => {
  const o = {};
  TRIM_KEYS.forEach(k => { if (t[k] !== undefined) o[k] = t[k]; });
  ['name', 'ticker', 'cur', 'code', 'on', 'to', 'src'].forEach(k => { if (o[k] != null) o[k] = String(o[k]).slice(0, 200); });
  ['u0', 'u1', 'exit', 'real_k', 'est'].forEach(k => { if (o[k] != null) { const n = _numOrNull(o[k]); if (n == null) delete o[k]; else o[k] = n; } });
  return o;
}).filter(t => t.name && +t.u0 > 0 && +t.u1 >= 0 && +t.u1 < +t.u0);
// 缺匯率時回 null,不要用 1.0 頂替 —— 那會讓韓元部位以 1:1 換算,
// 韓元部位的曝險會被放大上千倍。
const priceUsd = p => (p.q && p.q.price != null && FX[p.q.cur]) ? p.q.price / FX[p.q.cur] : null;
// 股數:手動輸入優先,否則用建置時算出的隱含股數
const unitsOf = p => (p.units_manual != null && p.units_manual !== '')
  ? +p.units_manual : (p.q ? p.q.units : null);
const effMv = p => {
  if (p.derived) return p.mv || 0;
  if (p.kind === 'live' && p.q) {
    const u = unitsOf(p), pu = priceUsd(p);
    if (u != null && pu != null) return u * pu / 1000;
    return p.q.mv_live;
  }
  return p.mv || 0;
};
const dayOf = p => {
  if (!p.q) return 0;
  if (p.derived) return p.q.day_k;
  const u = unitsOf(p);
  if (u == null || !p.q.units) return p.q.day_k;
  return p.q.day_k * (u / p.q.units);      // 手動股數等比例調整當日損益
};
// 成本:live 用「每股成本(原幣)×股數」,靜態部位用直接輸入的總成本(USD K)
const costK = p => {
  const cc = (p.q && p.q.cur) || p.cur;
  if (p.cost != null && p.cost !== '' && cc) {
    const u = unitsOf(p);
    if (u != null) return u * (+p.cost) / (FX[cc] || 1) / 1000;
  }
  if (p.cost_k != null && p.cost_k !== '') return +p.cost_k;
  return null;
};
const unrealK = p => { const c = costK(p); return c == null ? null : effMv(p) - c; };
// Breakeven(%):券商對帳單的「Breakeven Return」就是 市值 ÷ 累積成本 − 1(2026-09-07 以
// 45 列逐一驗證,差異全是仟元四捨五入)。p.be 只是匯入當天的快照,價格一動就過時 ——
// 曾有部位顯示 0.5%,旁邊的即時未實現卻是 +1.5%。有成本的部位一律即時算,
// 沒成本的(報表部位)才用快照。
const beLive = p => {
  const c = costK(p);
  if (c != null && c > 0) return (effMv(p) / c - 1) * 100;
  return p.be != null ? +p.be : null;
};
// 一組部位的報酬率:Σ損益 ÷ Σ分母(分母見 ytdDenOf)。
// 原本用「各檔報酬率依現在市值加權」,但現在市值是結果不是本金 —— 漲最多的
// 漲最多的部位權重被放大、減碼過的部位被縮小,整體會高估好幾個百分點。
// 回傳 {n, d, ret, cov}:cov 是有分母的部位佔這組市值的比例,不到 99.5% 時前端標「≈」。
const sumRet = ps => {
  let n = 0, d = 0, mvAll = 0, mvCov = 0;
  ps.forEach(p => {
    const y = ytdOf(p), den = ytdDenOf(p), mv = effMv(p);
    mvAll += mv;
    if (y != null && den) { n += y; d += den; mvCov += mv; }
  });
  return d ? {n, d, ret: n / d * 100, cov: mvAll ? mvCov / mvAll * 100 : 100} : null;
};
const retCell = r => r == null ? '—' : (r.cov < 99.5 ? '≈' : '') + spct(r.ret);
const allPos = () => P.regions.flatMap(r => r.groups.flatMap(g => g.positions.map(p => ({...p, _r: r, _g: g}))));

// ── 今日變動貢獻面板 ─────────────────────────────────────────
const DAYBK_KEY = 'day_breakdown_v1';
let DAYBK = (() => { try { return localStorage.getItem(DAYBK_KEY) === '1'; } catch (e) { return false; } })();

// 拆分列展開狀態(記憶展開偏好)
const SPLIT_KEY = 'split_open_v2';     // v2:存代號雜湊
const SPLITOPEN = new Set((() => {
  try { return JSON.parse(localStorage.getItem(SPLIT_KEY) || '[]'); } catch (e) { return []; }
})());
document.addEventListener('click', e => {
  const tr = e.target.closest && e.target.closest('tr.splitTg');
  if (!tr) return;
  const t = fnv(tr.dataset.t);
  SPLITOPEN.has(t) ? SPLITOPEN.delete(t) : SPLITOPEN.add(t);
  try { localStorage.setItem(SPLIT_KEY, JSON.stringify([...SPLITOPEN])); } catch (e2) {}
  render();
});

// 回頂端:捲超過一屏才出現
window.addEventListener('scroll', () => {
  const b = document.getElementById('toTop');
  if (b) b.classList.toggle('show', window.scrollY > 700);
}, {passive: true});

function dayBreakdown(nd, totDay) {
  const rows = nd.filter(p => p.q).map(p => ({p, v: dayOf(p)}))
    .filter(x => Math.abs(x.v) >= 0.5)
    .sort((a, b) => Math.abs(b.v) - Math.abs(a.v));
  const top = rows.slice(0, 10);
  const rest = rows.slice(10).reduce((s, x) => s + x.v, 0);
  const line = x => {
    const rname = (x.p._r ? x.p._r.name : '').replace('部位', '').replace('(跨區分析視角)', '');
    return `<tr><td>${esc(x.p.name)}<span class="tk">${esc(rname)}</span></td>` +
      `<td class="num ${cls(x.v)}">${sign0(x.v)}</td>` +
      `<td class="num ${x.p.q.chg == null ? 'mut' : cls(x.p.q.chg)}">${x.p.q.chg == null ? '—' : spct(x.p.q.chg)}</td></tr>`;
  };
  return `<div class="tile daybk" style="grid-column:1/-1">
    <div class="t">今日變動主要貢獻(USD K,依絕對值前 10)</div>
    <div style="overflow-x:auto"><table>
      <thead><tr><th>部位</th><th class="num">今日 USD K</th><th class="num">漲跌</th></tr></thead>
      <tbody>${top.map(line).join('')}
      ${Math.abs(rest) >= 0.5 ? `<tr><td class="mut">其餘 ${rows.length - top.length} 檔合計</td><td class="num ${cls(rest)}">${sign0(rest)}</td><td></td></tr>` : ''}
      <tr class="subtotal"><td>合計</td><td class="num ${cls(totDay)}">${sign0(totDay)}</td><td></td></tr>
      </tbody></table></div></div>`;
}
// 重複列示的判斷全站共用一個:有的地方寫 p.derived(物件),有的寫 kind==='derived',
// 兩邊不一致時總資產與各配置的分母會對不上,週/月 KPI 還會比出憑空的變化。
const isDup = p => !!p.dup || !!p.derived || p.kind === 'derived';
const nonDup = () => allPos().filter(p => !isDup(p));

function badge(p) {
  if (p.derived) return '<span class="badge lv">連動合計</span>';
  if (p.kind === 'live' && p.q)
    // title 帶上實際場次日期:每天早上的正確性比對靠它判斷資料源有沒有落後
    return `<span class="badge ${p.q.live ? 'lv' : 'st'}"${p.q.asof ? ` title="場次 ${esc(p.q.asof)}"` : ''}>${esc(p.q.qnote)}</span>`;
  if (p.kind === 'pending' || p.kind === 'live') return '<span class="badge warn">報價待接</span>';
  // 靜態 NAV:顯示「資料截至」。手改市值或匯入對帳單時會蓋章(nav_asof);
  // 從沒被更新過的沿用報表基準日。超過 45 天標黃,提醒該檔數字已經很舊。
  // nav_asof 可能來自匯入的 JSON 或覆寫檔:數字型別會讓 .slice 丟例外,而 badge()
  // 是在 render() 的區域迴圈裡呼叫的,一丟整頁就停在空白。先正規化成合法日期。
  const raw = String(p.nav_asof || NAV_ASOF_DEFAULT);
  const asof = /^\d{4}-\d{2}-\d{2}$/.test(raw) ? raw : NAV_ASOF_DEFAULT;
  const days = (Date.now() - new Date(asof + 'T00:00:00+08:00')) / 86400000;
  const lbl = 'NAV ' + asof.slice(5).replace('-', '/');
  return days > 45 ? `<span class="badge warn" title="靜態淨值已 ${Math.floor(days)} 天未更新">⚠ ${lbl}</span>`
                   : `<span class="badge st" title="靜態淨值,資料截至 ${esc(asof)}">${lbl}</span>`;
}

function attachTips() {
  document.querySelectorAll('.bar').forEach(b => {
    b.addEventListener('mousemove', e => { tip.textContent = b.dataset.tip; tip.style.opacity = 1;
      tip.style.left = Math.min(e.clientX + 14, innerWidth - 310) + 'px'; tip.style.top = (e.clientY + 14) + 'px'; });
    b.addEventListener('mouseleave', () => tip.style.opacity = 0);
  });
}

// 拆分列(同一檔美元 ETF 依成分拆的地區子列)的預設曝險幣別(使用者可在編輯器改)
const EXP_DEFAULT = [[/韓國/, 'KRW']];
function normalizeExp() {
  P.regions.forEach(r => r.groups.forEach(g => g.positions.forEach(p => {
    if (p.exp_cur || !p.wgt || !/:(NYSEARCA|NASDAQ|NYSE)$/.test(p.ticker || '')) return;
    const hit = EXP_DEFAULT.find(([re]) => re.test(p.name || ''));
    if (hit) p.exp_cur = hit[1];
  })));
}

function resolveDerived() {
  P.regions.forEach(r => r.groups.forEach(g => g.positions.forEach(p => {
    if (!p.derived) return;
    const reg = P.regions.find(x => x.key === p.derived.region);
    const src = (reg ? reg.groups : []).flatMap(gg => gg.positions)
                  .filter(x => x.subgrp === p.derived.subgrp);
    const mv = src.reduce((s, x) => s + effMv(x), 0);
    const day = src.filter(x => x.q).reduce((s, x) => s + dayOf(x), 0);
    p.mv = Math.round(mv * 10) / 10;
    p.pl = src.reduce((s, x) => s + (ytdOf(x) || 0), 0);
    // 報酬率也要跟著成分即時算;原本只更新 pl、ret 停在報表值,
    // 鏡像列曾顯示損益為負、旁邊卻掛著正的報酬率。
    const sr = sumRet(src);
    p._den = sr ? sr.d : null;
    if (sr) p.ret = sr.ret;
    // Breakeven 也照成分即時算(Σ市值 ÷ Σ成本 − 1);鏡像列曾停在減碼前的舊值,
    // 與成分合計差了十個百分點。
    const sc = src.map(costK).filter(v => v != null).reduce((s, v) => s + v, 0);
    if (sc > 0) p.be = Math.round((mv / sc - 1) * 1000) / 10;
    p.q = {day_k: day, chg: (mv - day) ? day / (mv - day) * 100 : 0,
           live: src.some(x => x.q && x.q.live), qnote: '連動合計',
           mv_live: mv, price: null, cur: '', units: 0};
  })));
}

// ══ YTD 損益 ═══════════════════════════════════════════════════════════════
// 口徑:只計算「目前仍持有」的部位。對帳單只列當前持倉,年內清光的部位會整列消失,
// 想涵蓋它們得另外拿已實現損益表 —— 所以這裡明確排除,不去猜。
//
// 有對帳單資料的部位(53 檔)即時計算:
//     YTD = (現在市值 − 累積成本 + 累積已實現) − 年初的累積損益
// 其中「現在市值 − 累積成本」就是即時的累積未實現,所以整式等於
// 「此刻的累積損益」減「年初的累積損益」= 今年以來的損益。
// 年初基準取自 2025-12-31 的對帳單;今年才買進的部位基準為 0。
//
// 沒有對帳單資料的部位(Activist、PE、海外基金等,約佔資產四到五成)
// 沿用報表上的 YTD 值 —— 它們本來就沒有逐日淨值,只能等新報表。
const stmtCurOf = p => p.stmt_cur || (p.q && p.q.cur) || p.cur;

// 拆分列(同一檔 ETF 依地區拆成多列)的 units_manual 建置時已乘過 wgt,所以市值/成本
// 本來就是「這一列的份」;但 stmt_real_k / ytd_base* 是對帳單上的整檔數字,
// 每一列都存了同一份整檔值 —— 必須在這裡按 wgt 分攤,否則五列各自扣掉整檔的
// 年初基準,YTD 會變成好幾倍的負數。
const wgtOf = p => p.wgt || 1;

function ytdOf(p) {
  if (p.stmt_real_k == null) return p.pl ?? null;        // 靜態部位:報表值
  const f = FX[stmtCurOf(p)];
  const mv = effMv(p), c = costK(p);
  if (!f || c == null) return p.pl ?? null;
  // 註:曾試過「年初市值與成本皆為 0 就把 ytd_base 視為目前已實現」的防呆,
  // 實測是錯的 —— 那兩個欄位「沒有值」與「確實是 0」意義不同,而且年初基準
  // 設定後累積已實現的後續變動本來就是今年賺的。某檔韓股 ETF 會因此漏掉
  // 一大筆已實現。維持原式。
  return mv - c + (p.stmt_real_k - (p.ytd_base || 0)) * wgtOf(p) / f;
}

// 報酬率分母 = 年初市值 + 年內買進成本(毛買進,見下方 soldCostK)。
// 只用年初市值的話,年內大幅加碼的部位分母會嚴重偏小,報酬率虛高
// (年內加碼四倍的部位,只用年初市值當分母會把報酬率算成五倍)。
// 這與券商報表的口徑一致 —— 逐檔反推驗證過 12 檔,11 檔誤差在 2% 以內。
//
// 「淨投入」要用毛買進,不是成本的淨變動:今年買進後又減碼的部位,賣掉那部分
// 的本金已經從累積成本裡扣掉,只看 cost − cost0 分母會縮水、報酬率被放大
// (減碼六成的部位曾顯示 −41.5%,依投入本金算是 −16.5%)。減碼紀錄
// (trims)留有 u0→u1,平均成本法下賣出不改每單位成本,所以賣掉的成本 =
// (u0 − u1) × 目前每單位成本;加回去就是毛買進。年初就持有、只賣不買的部位
// 加回去後 cost − cost0 + 賣出成本 ≈ 0,分母仍是年初市值,不受影響。
function soldCostK(p) {
  if (p.cost == null || p.cost === '') return 0;
  const cc = (p.q && p.q.cur) || p.cur, f = FX[cc];
  if (!f) return 0;
  const yr = new Date(Date.now() + 8 * 3600e3).toISOString().slice(0, 4);
  return (P.trims || []).reduce((s, t) => {
    if (t.name !== p.name || (t.ticker || '') !== (p.ticker || '')) return s;
    if (String(t.to || t.on || '').slice(0, 4) !== yr) return s;
    const du = (+t.u0 || 0) - (+t.u1 || 0);
    return du > 0 ? s + du * (+p.cost) / f / 1000 : s;
  }, 0);
}
function ytdBaseUsd(p) {
  const f = FX[stmtCurOf(p)], c = costK(p);
  if (!f) return c;
  const w = wgtOf(p);
  const mv0 = p.ytd_base_mv ? p.ytd_base_mv * w / f : 0;
  const c0  = p.ytd_base_cost ? p.ytd_base_cost * w / f : 0;
  const base = mv0 + Math.max(0, (c || 0) - c0 + soldCostK(p));
  return base || c;
}
// 任何部位的報酬率分母(USD K),與 ytdRetOf 走同一套判斷:
//   對帳單部位 → ytdBaseUsd;鏡像列 → 成分分母合計;
//   報表部位 → 報表損益 ÷ 報表報酬率 反推;損益為 0 反推不了時用「市值 − 損益」。
// 群組小計與 KPI 用 Σ損益 ÷ Σ分母,分子分母才是同一套帳。
function ytdDenOf(p) {
  const f = FX[stmtCurOf(p)];
  if (p.stmt_real_k != null && f && costK(p) != null) return ytdBaseUsd(p) || null;
  if (p._den != null) return p._den;
  if (p.pl && p.ret) return p.pl / p.ret * 100;
  const mv = effMv(p) - (p.pl || 0);
  return mv > 0 ? mv : null;
}
function ytdRetOf(p) {
  if (p.stmt_real_k == null) return p.ret ?? null;
  // ytdOf 在沒有成本或缺匯率時會退回報表的 p.pl。那時分子是報表口徑、分母卻是
  // 對帳單推出來的年初基準,兩者不是同一套帳:某檔海外基金因此顯示 +1.0%
  // 而報表上是 +19.8%。分子退回報表,分母就也要退回報表。
  const f = FX[stmtCurOf(p)];
  if (!f || costK(p) == null) return p.ret ?? null;
  const y = ytdOf(p), b = ytdBaseUsd(p);
  return (y != null && b) ? y / b * 100 : (p.ret ?? null);
}


// 覆寫檔 / 匯入檔的內容視為不可信:欄位型別在這裡統一。數字欄位轉不成有限數就拿掉,
// 字串欄位一律 String(),幣別只收三到五個英文字母(曾有 exp_cur 為 '__proto__' 時
// 讓幣別分桶的物件被改掉原型的案例)。伺服器端 merge_overlay.py 有同一套清洗,
// 這裡是給「本機副本」與「匯入的 JSON」走的第二道。
const NUM_FIELDS = ['mv', 'pl', 'ret', 'be', 'cost', 'cost_k', 'units_manual', 'wgt',
                    'stmt_real_k', 'ytd_base', 'ytd_base_mv', 'ytd_base_cost'];
const STR_FIELDS = ['name', 'ticker', 'note', 'kind', 'subgrp', 'cost_src', 'nav_asof', 'geo', 'theme'];
const CUR_FIELDS = ['cur', 'exp_cur', 'stmt_cur'];
function normalizeShape(o) {
  if (o.closed_ytd) o.closed_ytd = cleanClosed(o.closed_ytd);
  if (o.trims) o.trims = cleanTrims(o.trims);
  // 只修「有給但型別錯」的;沒給的維持 undefined,合併時才不會把 bundle 的值清掉
  if (o.fx_track != null && !Array.isArray(o.fx_track)) o.fx_track = [];
  if (o.fx_manual != null && !Array.isArray(o.fx_manual)) o.fx_manual = [];
  if (o.hedges != null && (typeof o.hedges !== 'object' || Array.isArray(o.hedges))) o.hedges = {};
  // 覆寫檔與匯入的 JSON 都可能缺欄位。render() 第一件事就是走訪 regions,
  // 這裡不補齊的話一個缺 positions 的群組就會讓整頁空白,而且重載也救不回來。
  o.regions = (Array.isArray(o.regions) ? o.regions : []).filter(r => r && typeof r === 'object');
  o.regions.forEach(r => {
    r.key = String(r.key ?? ''); r.name = String(r.name ?? '');
    r.groups = (Array.isArray(r.groups) ? r.groups : []).filter(g => g && typeof g === 'object');
    r.groups.forEach(g => {
      g.name = String(g.name ?? '');
      g.positions = (Array.isArray(g.positions) ? g.positions : []).filter(p => p && typeof p === 'object');
      g.positions.forEach(p => {
        NUM_FIELDS.forEach(k => {
          if (p[k] == null || p[k] === '') return;          // '' = 未設定,unitsOf 靠它判斷
          const v = Number(p[k]);
          if (Number.isFinite(v) && Math.abs(v) < 1e15) p[k] = v; else delete p[k];
        });
        STR_FIELDS.forEach(k => { if (p[k] != null && typeof p[k] !== 'string') p[k] = String(p[k]); });
        CUR_FIELDS.forEach(k => {
          if (p[k] == null || p[k] === '') return;
          const c = String(p[k]).toUpperCase();
          if (/^[A-Z]{3,5}$/.test(c)) p[k] = c; else delete p[k];
        });
        if (p.stmt_code != null && !Array.isArray(p.stmt_code)) p.stmt_code = [String(p.stmt_code)];
        if (p.derived != null && (typeof p.derived !== 'object' || Array.isArray(p.derived))) delete p.derived;
        if (p.q != null && (typeof p.q !== 'object' || Array.isArray(p.q))) delete p.q;
        if (p.prev_override != null && typeof p.prev_override !== 'object') delete p.prev_override;
      });
    });
  });
  return o;
}

// 週/月變化(見 render 內的說明):history 每日結算的市場變動累加 + 今日即時。
function weekMonth(totDay) {
  const H = window.__HIST__ || [];
  if (!H.length) return {};
  const now2 = new Date(Date.now() + 8 * 3600e3);               // 台北時間的「現在」
  const dow = (now2.getUTCDay() + 6) % 7;                       // 0=週一
  // 台北時間週一 00:00 / 當月 1 日 00:00 對應的真實時刻(台北比 UTC 早 8 小時)
  const cutW = Date.UTC(now2.getUTCFullYear(), now2.getUTCMonth(), now2.getUTCDate() - dow) - 8 * 3600e3;
  const cutM = Date.UTC(now2.getUTCFullYear(), now2.getUTCMonth(), 1) - 8 * 3600e3;
  const today = now2.toISOString().slice(0, 10);
  // 今天那一列(若已過 18 時結算)要排除,改用畫面上的即時今日變動,否則會算兩次
  const sum = cut => H.reduce((s, h) => {
    if (!h.v || h.v.length < 3 || h.t.slice(0, 10) === today) return s;
    const ts = new Date(h.t.replace(' ', 'T') + ':00+08:00').getTime();
    return ts >= cut ? s + (+h.v[2] || 0) : s;
  }, 0);
  return {w: sum(cutW) + totDay, m: sum(cutM) + totDay};
}

// ── 匯總報表(可列印 / 存成 PDF)────────────────────────────────────────
// 一組部位的彙總:與畫面上的小計同一套公式(市值含全部、其餘欄不含 dup 由呼叫端決定)。
function aggOf(ps) {
  const mv = ps.reduce((s, p) => s + effMv(p), 0);
  // 成本 / 未實現只看有成本資料的部位:日本區只有個股有成本,若用整區市值減成本,
  // 未實現會變成 +2394% 這種數字。涵蓋比例另外回報,不足時在報表標示。
  const withC = ps.filter(p => costK(p) != null);
  const cost = withC.length ? withC.reduce((s, p) => s + costK(p), 0) : null;
  const costMv = withC.reduce((s, p) => s + effMv(p), 0);
  const beSrc = ps.filter(p => beLive(p) != null && effMv(p) > 0 && (1 + beLive(p) / 100) > 0);
  const beMv = beSrc.reduce((s, p) => s + effMv(p), 0);
  const beBase = beSrc.reduce((s, p) => s + effMv(p) / (1 + beLive(p) / 100), 0);
  const q = ps.filter(p => p.q);
  const day = q.reduce((s, p) => s + dayOf(p), 0);
  // 今日 % 的分母只能用「今日金額算得出來的那些部位」的市值。用整組市值的話,
  // 分子分母的母體不同,百分比會被稀釋成沒有意義的數字 —— 日本區有 93% 是沒有
  // 日變動的 Activist / PE / 基金,同一個 −76 在區層顯示 −0.1%、在個股層顯示 −1.9%。
  const qmv = q.reduce((s, p) => s + effMv(p), 0);
  return {
    mv, cost, unreal: cost != null ? costMv - cost : null,
    costCov: mv > 0 ? costMv / mv * 100 : 0,
    be: (beSrc.length && beBase > 0) ? (beMv / beBase - 1) * 100 : null,
    beCov: mv > 0 ? beMv / mv * 100 : 0,
    ytd: ps.reduce((s, p) => s + (ytdOf(p) || 0), 0),
    ret: sumRet(ps),
    day, dayPct: (qmv - day) ? day / (qmv - day) * 100 : null, hasQ: q.length > 0,
    dayMv: qmv, dayCov: mv > 0 ? qmv / mv * 100 : 0,
  };
}

function buildReport() {
  normalizeShape(P); resolveDerived();
  const nd = nonDup();
  const tot = nd.reduce((s, p) => s + effMv(p), 0);
  const closedArr = P.closed_ytd || [];
  const closedSum = closedArr.reduce((s, c) => s + (+c.usd_k || 0), 0);
  const totPl = nd.reduce((s, p) => s + (ytdOf(p) || 0), 0) + closedSum;
  const totDay = nd.filter(p => p.q).reduce((s, p) => s + dayOf(p), 0);
  const rb = sumRet(nd);
  const retEst = rb ? (rb.n + closedSum) / rb.d * 100 : null;
  const wk = weekMonth(totDay);
  const uniq = arr => new Set(arr.map(p => p.ticker || p.name)).size;
  const staleN = uniq(nd.filter(p => p.q && p.q.chg == null));
  const noqN = uniq(nd.filter(p => p.kind === 'live' && !p.q));
  const all = aggOf(nd);
  const metaEl = document.querySelector('.meta');
  const mt = metaEl ? metaEl.textContent : '';
  const qAt = (mt.match(/報價時間:([^·]+?)台北時間/) || ['', ''])[1].trim();
  const build = (mt.match(/建置 v\d+ · [0-9a-f]+/) || [''])[0];
  const nowTpe = new Date(Date.now() + 8 * 3600e3).toISOString().slice(0, 16).replace('T', ' ');
  const n1 = v => v == null ? '—' : fmt0(v);
  const sg = v => v == null ? '—' : sign0(v);
  const pc = v => v == null ? '—' : spct(v);
  const c2 = v => v == null ? '' : cls(v);
  const retTxt = r => r == null ? '—' : (r.cov < 99.5 ? '≈' : '') + spct(r.ret);
  const row = (label, a, share, kind) =>
    `<tr class="${kind}"><td>${label}</td>` +
    `<td class="n">${n1(a.mv)}</td><td class="n">${share != null ? share.toFixed(1) + '%' : '—'}</td>` +
    `<td class="n">${n1(a.cost)}${a.cost != null && a.costCov < 99.5 ? `<span class="s">${a.costCov.toFixed(0)}% 涵蓋</span>` : ''}</td>` +
    `<td class="n ${c2(a.unreal)}">${sg(a.unreal)}</td>` +
    `<td class="n ${c2(a.unreal)}">${a.cost ? pc(a.unreal / a.cost * 100) : '—'}</td>` +
    `<td class="n">${a.be != null ? a.be.toFixed(1) + '%' + (a.beCov < 99.5 ? `<span class="s">${a.beCov.toFixed(0)}% 涵蓋</span>` : '') : '—'}</td>` +
    `<td class="n ${c2(a.ytd)}">${sg(a.ytd)}</td><td class="n ${c2(a.ytd)}">${retTxt(a.ret)}</td>` +
    `<td class="n ${a.hasQ ? c2(a.day) : ''}">${a.hasQ ? sg(a.day) : '—'}</td>` +
    `<td class="n ${a.hasQ ? c2(a.day) : ''}">${a.hasQ ? pc(a.dayPct) : '—'}</td></tr>`;
  let rows = '';
  P.regions.forEach(r => {
    const rnd = r.groups.flatMap(g => g.positions).filter(p => !isDup(p));
    const rAll = r.groups.flatMap(g => g.positions);
    const ra = aggOf(rnd);
    const dupMv = rAll.filter(isDup).reduce((s, p) => s + effMv(p), 0);
    const cross = /跨區/.test(r.name);
    rows += row(`${esc(r.name)}${dupMv ? `<span class="s">另有重複列示 ${fmt0(dupMv)},不計入</span>` : ''}${cross ? '<span class="s">分析視角,僅海外部分計入總計</span>' : ''}`,
                ra, tot ? ra.mv / tot * 100 : null, 'reg');
    r.groups.forEach(g => {
      const gnd = g.positions.filter(p => !isDup(p));
      if (!gnd.length) return;
      rows += row(`<span class="ind">${esc(g.name)}</span><span class="s">${gnd.length} 檔</span>`, aggOf(gnd), tot ? aggOf(gnd).mv / tot * 100 : null, 'grp');
    });
  });
  rows += row('合計(不含重複列示)', all, 100, 'tot');
  if (closedArr.length) {
    rows += `<tr class="grp"><td><span class="ind">已出清 ${closedArr.length} 檔(不在總資產)</span></td><td class="n">—</td><td class="n">—</td><td class="n">—</td><td class="n">—</td><td class="n">—</td><td class="n">—</td>` +
      `<td class="n ${cls(closedSum)}">${sign0(closedSum)}</td><td class="n">—</td><td class="n">—</td><td class="n">—</td></tr>`;
    rows += `<tr class="tot"><td>YTD 合計(含已出清)</td><td class="n"></td><td class="n"></td><td class="n"></td><td class="n"></td><td class="n"></td><td class="n"></td>` +
      `<td class="n ${cls(totPl)}">${sign0(totPl)}</td><td class="n ${cls(totPl)}">${retEst != null ? (rb.cov < 99.5 ? '≈' : '') + spct(retEst) : '—'}<span class="s">${spct(tot ? totPl / tot * 100 : 0)} 佔總資產</span></td><td class="n"></td><td class="n"></td></tr>`;
  }
  const fxTxt = Object.entries(FX).filter(([k, v]) => k !== 'USD' && isFinite(v))
    .map(([k, v]) => `${k} ${v >= 100 ? v.toFixed(2) : v.toFixed(4)}`).join(' · ');
  const closedTxt = closedArr.length
    ? closedArr.map(c => `${esc(c.name)} ${sign0(+c.usd_k || 0)}`).join('、') : '無';
  return `<!DOCTYPE html><html lang="zh-Hant"><head><meta charset="utf-8"><title>投資組合匯總報表 ${nowTpe}</title>
<style>
  @page { size: A4 landscape; margin: 12mm; }
  body { font: 12px/1.45 -apple-system, "Segoe UI", "Noto Sans TC", "PingFang TC", "Microsoft JhengHei", sans-serif; color: #111; background: #fff; margin: 0; padding: 18px 22px; }
  h1 { font-size: 18px; margin: 0 0 2px; }
  .meta { color: #555; font-size: 11px; margin-bottom: 12px; }
  .kpis { display: grid; grid-template-columns: repeat(5, 1fr); gap: 8px; margin-bottom: 14px; }
  .tile { border: 1px solid #ccc; border-radius: 6px; padding: 8px 10px; }
  .tile .t { font-size: 11px; color: #555; }
  .tile .v { font-size: 20px; font-weight: 600; margin: 2px 0; }
  .tile .d { font-size: 11px; color: #444; }
  table { border-collapse: collapse; width: 100%; }
  th, td { border-bottom: 1px solid #ddd; padding: 4px 6px; text-align: left; vertical-align: top; white-space: nowrap; }
  th { background: #f2f2f2; font-weight: 600; font-size: 11px; }
  td.n, th.n { text-align: right; font-variant-numeric: tabular-nums; }
  tr.reg td { font-weight: 600; background: #fafafa; border-top: 2px solid #bbb; }
  tr.tot td { font-weight: 700; border-top: 2px solid #333; background: #f2f2f2; }
  .ind { display: inline-block; padding-left: 14px; }
  .s { display: inline; margin-left: 6px; font-size: 10px; color: #777; font-weight: 400; }
  .up { color: #b42318; } .down { color: #067647; }
  .foot { margin-top: 12px; font-size: 10.5px; color: #555; line-height: 1.5; }
  .noprint { margin-bottom: 12px; }
  .noprint button { font: inherit; padding: 6px 14px; border: 1px solid #888; border-radius: 6px; background: #fff; cursor: pointer; }
  tr { page-break-inside: avoid; }
  @media print { .noprint { display: none; } body { padding: 0; font-size: 10.5px; }
    .kpis { gap: 6px; margin-bottom: 8px; } .tile { padding: 5px 8px; } .tile .v { font-size: 16px; }
    th, td { padding: 2px 5px; } h1 { font-size: 15px; } }
</style></head><body>
<div class="noprint"><button onclick="window.print()">⎙ 列印 / 存成 PDF</button> <span style="color:#777;font-size:11px">列印對話框中選「另存為 PDF」即可存檔;橫向 A4 一頁。</span></div>
<h1>投資組合匯總報表</h1>
<div class="meta">產出 ${nowTpe} 台北時間${qAt ? ` · 報價時間 ${qAt}` : ''}${build ? ` · ${build}` : ''} · 單位 USD 千元 · 紅漲綠跌</div>
<div class="kpis">
  <div class="tile"><div class="t">總資產 (USD K)</div><div class="v">${fmt0(tot)}</div><div class="d">≈ US$${(tot / 1000).toFixed(1)}M · ${nd.length} 檔(不含重複列示)</div></div>
  <div class="tile"><div class="t">YTD 損益 (USD K)</div><div class="v ${cls(totPl)}">${sign0(totPl)}</div><div class="d">${retEst != null ? `${rb.cov < 99.5 ? '≈' : ''}${spct(retEst)} 年初基準報酬率 · ` : ''}${spct(tot ? totPl / tot * 100 : 0)} 佔總資產${closedArr.length ? `<br>含已出清 ${closedArr.length} 檔 ${sign0(closedSum)}(不在分母)` : ''}</div></div>
  <div class="tile"><div class="t">今日變動 (USD K)</div><div class="v ${cls(totDay)}">${sign0(totDay)}</div><div class="d">僅含已連動報價部位${staleN ? ` · ${staleN} 檔報價落後未計入` : ''}</div></div>
  <div class="tile"><div class="t">週 / 月變化 (USD K)</div><div class="v ${cls(wk.w ?? 0)}">${wk.w != null ? sign0(wk.w) : '—'}</div><div class="d">本月 ${wk.m != null ? sign0(wk.m) : '—'} · 僅市場漲跌,不含買賣</div></div>
  <div class="tile"><div class="t">上市連動 / 全部部位</div><div class="v">${nd.filter(p => p.q).length} / ${nd.length}</div><div class="d">${noqN ? `⚠ ${noqN} 檔該有報價卻抓不到` : '其餘為 NAV/待接報價'}</div></div>
</div>
<table><thead><tr><th>區域 / 群組</th><th class="n">市值</th><th class="n">佔總資產</th><th class="n">成本</th><th class="n">未實現</th><th class="n">未實現 %</th><th class="n">Breakeven</th><th class="n">YTD 損益</th><th class="n">YTD 報酬率</th><th class="n">今日</th><th class="n">今日 %</th></tr></thead>
<tbody>${rows}</tbody></table>
<div class="foot">
已出清部位(今年):${closedTxt}。<br>
匯率(每 1 USD):${fxTxt}。<br>
口徑:市值以隱含股數連動現價,基金 / PE / Activist 為報表 NAV;成本、未實現、Breakeven(市值 ÷ 累積成本 − 1)只計有成本資料的部位,涵蓋不足時標示比例;
YTD 損益 = 今年以來的(未實現 + 已實現)變動,報酬率 = Σ損益 ÷ Σ(年初市值 + 年內買進成本),報表部位用報表分母;今日 = 各市場最近一個場次的漲跌。
</div>
</body></html>`;
}

function openReport() {
  const w = window.open('', '_blank');
  if (!w) { alert('瀏覽器擋下了新視窗,請允許此網站開啟彈出視窗後再試。'); return; }
  w.document.open(); w.document.write(buildReport()); w.document.close();
}

function render() {
  normalizeShape(P);
  document.body.classList.toggle('editmode', EDIT);
  normalizeExp();
  resolveDerived();
  const nd = nonDup();
  const tot = nd.reduce((s, p) => s + effMv(p), 0);
  const closedArr = P.closed_ytd || [];
  const closedSum = closedArr.reduce((s, c) => s + (+c.usd_k || 0), 0);
  const totPl = nd.reduce((s, p) => s + (ytdOf(p) || 0), 0) + closedSum;
  const totDay = nd.filter(p => p.q).reduce((s, p) => s + dayOf(p), 0);
  // 有報價但報不出今日漲跌的檔數:資料源落後一個場次時,抓價端會把 chg 拿掉
  // (舊場次的漲跌不是今天的漲跌),這些部位不進今日變動 —— 要在 KPI 上講出來,
  // 否則畫面看起來只是「今天比較平靜」。
  // 用代號去重:拆分列依地區拆成多列(都不是 dup),一檔抓不到會報成好幾檔,
  // 這個計數器又是大部位無聲掉出今日變動時的唯一訊號,灌水會讓人學會忽略它。
  const uniq = arr => new Set(arr.map(p => p.ticker || p.name)).size;
  const staleN = uniq(nd.filter(p => p.q && p.q.chg == null));
  // 這一場在上次結算前就收盤了(美股國定假日隔天最常見):漲跌幅照顯示,
  // 但金額已在上次結算計過,不再進今日加總。講出來,否則看起來只是「今天比較平靜」。
  const settledN = uniq(nd.filter(p => p.q && p.q.settled && p.q.chg != null));
  // 該有報價卻沒有的檔數。抓價端整檔失敗時這裡會是唯一的線索 ——
  // 2026-09-01 晚間最大部位就這樣從今日變動裡整個消失而畫面毫無異狀。
  const noqN = uniq(nd.filter(p => p.kind === 'live' && !p.q));
  // 整體報酬率 = Σ損益 ÷ Σ分母(年初市值 + 年內毛買進;報表部位用報表分母)。
  // 已出清部位的損益在分子(與券商年度報表同口徑),它們的投入本金對帳單拆不出來,
  // 所以不在分母 —— 與「佔總資產」那個數字的處理一致,tooltip 講明。
  const rb = sumRet(nd);
  const retEst = rb ? (rb.n + closedSum) / rb.d * 100 : null;
  const retCov = rb ? rb.cov : 0;

  // 週/月變化:每日市場漲跌的累加,不是「總資產相減」。
  //
  // 原本的做法是拿現在的總資產減掉上週五(或上月底)的結算值,但看板只記持股、
  // 不記賣出後的現金:2026-09-01 那次調倉賣掉一塊,總資產就直接少那一塊,
  // 週變化因此顯示一個大負數,其中真正的市場漲跌只佔一小部分,其餘全是錢離開了看板。
  // 改成累加每日結算的市場變動(history 的 dchg 欄)後,買賣進出不再污染這個數字。
  // 代價:靜態部位(PE/Activist/海外基金)的淨值變化不在 dchg 裡,那些是對帳單
  // 匯入時整批更新的,本來也不該算成某一天的市場漲跌。
  const wk = weekMonth(totDay);

  $('kpis').innerHTML = `
   <div class="tile"><div class="t">總資產 (USD K)</div><div class="v">${fmt0(tot)}</div><div class="d mut">≈ US$${(tot/1000).toFixed(1)}M</div></div>
   <div class="tile"><div class="t">YTD 損益 (USD K)</div><div class="v ${cls(totPl)}">${sign0(totPl)}</div><div class="d ${cls(totPl)}">${retEst != null
       ? `${retCov < 99.5 ? '≈' : ''}${spct(retEst)}<span class="mut" title="Σ損益 ÷ Σ分母。分母:有對帳單的部位 = 年初市值 + 年內買進成本(含後來減碼賣掉的那部分);報表部位 = 報表損益 ÷ 報表報酬率。已出清部位的損益計入分子、其投入本金不在分母。${retCov < 99.5 ? `涵蓋 ${retCov.toFixed(0)}% 市值,其餘部位沒有分母資料。` : ''}">(年初基準報酬率)</span> · <span class="mut" title="分母為目前總資產">${spct(tot ? totPl / tot * 100 : 0)}(佔總資產)</span>`
       : `${spct(tot ? totPl / tot * 100 : 0)}<span class="mut">(佔總資產)</span>`}${closedArr.length ? `<span class="mut" title="${esc(closedArr.map(c=>c.name+' '+sign0(+c.usd_k)).join('、'))}&#10;已出清部位的損益計入本年度損益(與券商年度報表同口徑),但那些部位已不在總資產裡,所以分子含它、分母不含。"> · 含已出清 ${closedArr.length} 檔 ${sign0(closedSum)}(不在分母)</span>` : ''}</div></div>
   <div class="tile daytile" id="dayTile" title="以台北日為準:亞股用當日盤中或收盤;美歐用前一場收盤(台北 21:30 美股開盤後改為即時);台灣境內基金用前一日淨值 —— 當日淨值傍晚才公布。點擊展開主要貢獻"><div class="t">今日變動 (USD K)<span class="mut" style="font-size:11px">${DAYBK ? '▴ 收合' : '▾ 明細'}</span></div><div class="v ${cls(totDay)}">${sign0(totDay)}</div><div class="d mut">僅含已連動報價部位${staleN ? ` · <span class="warnnote">${staleN} 檔報價落後未計入</span>` : ''}${settledN ? ` · <span title="這些部位最近一場的漲跌,已在上一次每日結算計入,不再重複算進今日">${settledN} 檔前一場已結算</span>` : ''} · 點擊看貢獻</div></div>
   ${(wk.w != null || wk.m != null) ? `<div class="tile"><div class="t">週 / 月變化 (USD K)</div><div class="v ${cls(wk.w ?? wk.m ?? 0)}">${wk.w != null ? sign0(wk.w) : '—'}</div><div class="d"><span class="${cls(wk.m ?? 0)}">本月 ${wk.m != null ? sign0(wk.m) : '—'}</span><span class="mut" title="每日結算的市場變動累加 + 今日即時;買進賣出不影響此數字,靜態部位的淨值調整也不計入"> · 僅市場漲跌,不含買賣</span></div></div>` : ''}
   <div class="tile"><div class="t">上市連動 / 全部部位</div><div class="v sm2" style="font-size:22px">${nd.filter(p=>p.q).length} / ${nd.length}</div><div class="d ${noqN ? 'warnnote' : 'mut'}">${noqN ? `⚠ ${noqN} 檔該有報價卻抓不到` : '其餘為 NAV/待接報價'}</div></div>
   ${DAYBK ? dayBreakdown(nd, totDay) : ''}`;

  const dt = $('dayTile');
  if (dt) dt.addEventListener('click', () => {
    DAYBK = !DAYBK;
    try { localStorage.setItem(DAYBK_KEY, DAYBK ? '1' : ''); } catch (e) {}
    render();
  });

  // ── 配置分析:地區與主題兩個維度,各自加總 100%(不含重複列示)──────────
  const tally = pick => {
    const m = new Map();
    P.regions.forEach(r => r.groups.forEach(g => g.positions.forEach(p => {
      if (isDup(p)) return;
      const v = effMv(p); if (!(v > 0)) return;
      const k = pick(p, r, g);
      m.set(k, (m.get(k) || 0) + v);
    })));
    return [...m].map(([name, mv]) => ({name, mv})).sort((a, b) => b.mv - a.mv);
  };
  // 類別太多時併尾:超過 11 類就把最小的併成「其他」,顏色與圖例才讀得完
  const capped = arr => {
    if (arr.length <= 12) return arr;
    const keep = arr.slice(0, 11), rest = arr.slice(11);
    return keep.concat([{name: '其他', mv: rest.reduce((s, x) => s + x.mv, 0),
                         sub: rest.map(x => x.name).join('、')}]);
  };
  const drawAlloc = (barId, legId, arr, tot2) => {
    const host = $(barId), lgh = $(legId);
    if (!host || !lgh) return;
    if (!tot2) { host.innerHTML = ''; lgh.innerHTML = ''; return; }   // 別留著舊圖
    const AW = 660, AH = 34;
    let x = 0, segs = '', leg = '';
    arr.forEach((r, i) => {
      const w = r.mv / tot2 * AW, pc = (r.mv / tot2 * 100).toFixed(1);
      const c = `var(--${SLOTS12[i % SLOTS12.length]})`;
      segs += `<g class="bar" data-tip="${esc(r.name)}:${fmt0(r.mv)} USD K(${pc}%)${r.sub ? ' — ' + esc(r.sub) : ''}">` +
        `<rect x="${x.toFixed(1)}" y="0" width="${Math.max(w - 2, 1).toFixed(1)}" height="${AH}" rx="4" fill="${c}"/></g>`;
      leg += `<span class="lg" title="${fmt0(r.mv)} USD K${r.sub ? ' — ' + esc(r.sub) : ''}">` +
             `<i style="background:${c}"></i>${esc(r.name)} ${pc}%</span>`;
      x += w;
    });
    host.innerHTML = `<svg viewBox="0 0 ${AW} ${AH}" role="img" aria-label="配置">${segs}</svg>`;
    lgh.innerHTML = leg;
  };
  const _tags = semiTags();
  drawAlloc('alloc',  'alloclegend',
            capped(tally((p, r) => GEONAME[geoOf(p, r.key)] || '其他 / 全球')), tot);
  drawAlloc('alloc2', 'alloclegend2',
            capped(tally((p, r, g) => themeOf(p, r.key, g.name, _tags))), tot);

  // ── 集中度與風險 ────────────────────────────────────────────
  // 口徑:非重複計入部位(nd),市值 = effMv。單檔 >10% 黃、>15% 紅。
  const rc = $('riskcard');
  if (rc && tot) {
    // 先依代號合併:拆分列逐列看每列都不到門檻,實際單一標的
    // 佔 4.6%。部位表本來就會把拆分列合起來顯示,這張卡不合併就會出現同一頁
    // 對同一檔有兩種排名(前十大 67.8% vs 合併後 69.7%)。
    const agg = new Map();
    nd.forEach(p => {
      const k = p.ticker || p.name;
      const cur2 = agg.get(k) || {n: p.name, mv: 0};
      cur2.mv += effMv(p);
      if (p.ticker) cur2.n = (p.name || '').replace(/\s*(美國|歐洲|韓國|以色列|其他).*$/, '') || p.name;
      agg.set(k, cur2);
    });
    const byMv = [...agg.values()].filter(x => x.mv > 0)
      .map(x => ({n: x.n, mv: x.mv, pc: x.mv / tot * 100}))
      .sort((a, b) => b.mv - a.mv);
    const top5 = byMv.slice(0, 5), top5pc = top5.reduce((s, x) => s + x.pc, 0);
    const top10pc = byMv.slice(0, 10).reduce((s, x) => s + x.pc, 0);
    const over = byMv.filter(x => x.pc > 10);
    const barrow = (label, pc, warn) =>
      `<div class="rrow${warn ? ' ' + warn : ''}"><span class="rl">${esc(label)}</span>` +
      `<span class="rbar"><i style="width:${Math.min(pc, 100).toFixed(1)}%"></i></span>` +
      `<span class="rv">${pc.toFixed(1)}%</span></div>`;
    // 幣別集中度:與外幣曝險同一套 curOf 口徑(部位本身,不含手動項目)
    const cbk = {};
    P.regions.forEach(r => r.groups.forEach(g => g.positions.forEach(p => {
      if (isDup(p)) return;
      const c = curOf(p, r.key);
      cbk[c] = (cbk[c] || 0) + effMv(p);
    })));
    // 手動曝險項目也要算進來,否則這裡的日圓佔比會與曝險卡差好幾個百分點
    (P.fx_manual || []).forEach(m => { if (m.cur) cbk[m.cur] = (cbk[m.cur] || 0) + (+m.mv || 0); });
    const cTot = Object.values(cbk).reduce((s, v) => s + v, 0) || tot;
    const curs = Object.entries(cbk).sort((a, b) => b[1] - a[1]).slice(0, 5);
    rc.innerHTML = `<h2>集中度與風險</h2><div class="riskgrid">
      <div><div class="rt">前五大持股 <span class="mut">合計 ${top5pc.toFixed(1)}% · 前十大 ${top10pc.toFixed(1)}%</span></div>
        ${top5.map(x => barrow(x.n, x.pc, x.pc > 15 ? 'r-red' : x.pc > 10 ? 'r-amber' : '')).join('')}</div>
      <div><div class="rt">單檔佔比警示 <span class="mut">門檻 10%</span></div>
        ${over.length ? over.map(x => barrow(x.n, x.pc, x.pc > 15 ? 'r-red' : 'r-amber')).join('')
          : '<div class="note">無單檔超過總資產 10%</div>'}
        <div class="note" style="margin-top:6px">>10% 黃、>15% 紅;分母為總資產(不含重複列示)。</div></div>
      <div><div class="rt">幣別集中度</div>
        ${curs.map(([c, v]) => barrow((typeof CURNAME !== 'undefined' && CURNAME[c]) || c, v / cTot * 100, v / cTot > 0.5 ? 'r-amber' : '')).join('')}</div>
    </div>`;
  }

  // 區域卡片
  let out = `<div class="btnrow"><button id="editToggle" class="${EDIT?'primary':''}">${EDIT?'✓ 編輯模式(開啟中)':'✎ 編輯模式'}</button>
    <button id="rptBtn" title="開新視窗:總覽 KPI 與各區 / 各組小計,可直接列印或存成 PDF">⎙ 匯總報表</button>
    <button id="expJson">${(SYNC && SYNC.token) ? "匯出備份 (JSON)" : "匯出目前組合 (JSON)"}</button>
    ${EDIT ? '<button id="deriveCost" title="成本 = 市值 ÷ (1 + Breakeven);只填尚未設定成本的部位">↧ 由 Breakeven 反推成本</button>' : ''}
    <button id="plToggle">${HIDEPL ? '＋ 顯示未實現損益' : '－ 隱藏未實現損益'}</button>
    <button onclick="document.getElementById('stmtFile').click()" class="primary">↥ 匯入對帳單</button>
    <input type="file" id="stmtFile" accept=".xlsx,.csv" hidden>
    <button onclick="document.getElementById('impFile').click()">匯入組合 (JSON)</button>
    <input type="file" id="impFile" accept=".json" hidden><span id="msg"></span>
    <span id="syncStatus">${syncBadge()}</span>
    ${inFreeze() ? `<span class="badge warn" title="Claude 重新校準了持倉,為了不被舊的網頁覆寫檔蓋掉而設的切點">⚠ 資料重整中,${esc(freezeTxt())} 前的修改不會保留</span>` : ''}
    ${(!inFreeze() && STALE_HIT) ? `<span class="badge">已忽略一份過期的網頁覆寫檔</span>` : ''}
    ${P.__localAt ? `<button id="clearLocal" title="捨棄本機修改,回到自動更新的版本">↺ 清除修改</button>` : ''}
    ${(P.__localAt && !(SYNC && SYNC.token)) ? `<span class="badge lv">本機已保存 ${esc(String(P.__localAt).slice(5,16).replace('T',' '))}</span>` : ''}
    <span class="note" style="margin-left:auto">${(SYNC && SYNC.token)
      ? (EDIT ? '股數與成本建議用「匯入對帳單」更新,不必手動打;編輯模式留給搬移 basket、靜態 NAV 與新增部位。修改會自動同步到所有裝置'
              : '靜態部位的市值可直接改,修改自動同步到所有裝置。匯出僅供備份')
      : (EDIT ? '編輯模式:修改只存在這台瀏覽器(未啟用同步),要正式生效請匯出 JSON 回傳給 Claude'
              : '靜態部位的市值可直接改;未啟用同步,改完請匯出傳回給 Claude')}</span></div>`;
  P.regions.forEach(reg => {
    const rpos = reg.groups.flatMap(g => g.positions).filter(p => !isDup(p));
    const rmv = rpos.reduce((s,p) => s + effMv(p), 0);
    const rpl = rpos.reduce((s,p) => s + (ytdOf(p)||0), 0);
    const rday = rpos.filter(p => p.q).reduce((s,p) => s + dayOf(p), 0);
    const rdayTxt = rpos.some(p => p.q) ? `· 今日 <span class="${cls(rday)}">${sign0(rday)}</span>` : '';
    out += `<div class="card" id="sec-${esc(reg.key)}"><h2>${esc(reg.name)} <span class="mut" style="font-weight:400">· ${fmt0(rmv)} USD K(${(rmv/tot*100).toFixed(1)}%)· YTD <span class="${cls(rpl)}">${sign0(rpl)}</span> ${rdayTxt}</span></h2>`;
    reg.groups.forEach(g => {
      // basket 小計採「含 dup」口徑,與報表一致(鏡像列屬該 basket 的一員);
      // dup 的部分不計入總資產,故另外標示 gnet。
      const gmvAll = g.positions.reduce((s,p) => s + effMv(p), 0);
      const gnd = g.positions.filter(p => !isDup(p));
      const gmv = gmvAll;
      const gdupMv = g.positions.filter(p => p.dup).reduce((s,p) => s + effMv(p), 0);
      const gnet = gnd.reduce((s,p) => s + effMv(p), 0);
      // 損益小計不含 dup:市值欄有「其中重複列示不計入」的揭露,損益欄沒有,
      // 含進去會讓半導體視角的小計變成實際的數倍。
      const gpl = gnd.reduce((s,p) => s + (ytdOf(p)||0), 0);
      const gkey = `${reg.key}||${g.name}`;
      const collapsed = !EDIT && COLLAPSED.has(fnv(gkey));
      out += `<div class="grp${collapsed ? ' collapsed' : ''}" data-gkey="${esc(gkey)}"><h3>${EDIT
          ? `<input class="grpName" data-reg="${esc(reg.key)}" data-grp="${esc(g.name)}" value="${esc(g.name)}" style="width:220px">
             <button class="del addPos" data-reg="${esc(reg.key)}" data-grp="${esc(g.name)}" title="新增部位">＋</button>
             ${g.positions.length ? '' : `<button class="del delGrp" data-reg="${esc(reg.key)}" data-grp="${esc(g.name)}" title="刪除空 basket">✕</button>`}`
          : `<span class="gtoggle">${collapsed ? '▸' : '▾'}</span> ` + esc(g.name)}</h3><div class="tblwrap"><table>
        <thead><tr><th>部位</th><th class="num">市值 USD K</th><th class="num xs-hide">佔本組</th>
        <th class="num xs-hide">成本 USD K</th><th class="num xs-hide">未實現損益</th>
        <th class="num xs-hide" title="市值 ÷ 累積成本 − 1(券商對帳單 Breakeven Return 的定義);有成本的部位以即時價計算,報表部位為匯入時的快照">Breakeven</th><th class="num">YTD P&L</th><th class="num">YTD Ret</th><th class="num">今日</th><th>狀態</th>${EDIT?'<th></th>':''}</tr></thead><tbody>`;
      const rowHtml = (p, pi, extraCls) => {
        const mv = effMv(p);
        const mvCell = ((p.kind === 'live' && p.q) || p.derived)
          ? `${fmt0(mv)}`
          : `<input class="mvI" data-reg="${esc(reg.key)}" data-grp="${esc(g.name)}" data-i="${pi}" value="${esc(p.mv ?? '')}">`;
        const dk = dayOf(p);
        // 前收兩源不一致時,把兩個候選值放進 title,滑鼠移上去就看得到實際數字
        // 前收有兩種說法時,把兩個候選值與實際採用的那個放進 title
        const pq = p.q || {};
        const pc = (pq.prev_bar && pq.prev_quote && pq.price)
          ? ` title="前收有兩種說法:日線 ${esc(pq.prev_bar)} · 報價源 ${esc(pq.prev_quote)}。已取離現價較近的那一個(單日漲跌通常不大),仍以每天早上的外部行情比對為準"` : '';
        const today = (pq.chg != null)
          ? `<span class="${cls(pq.chg)}"${pc}>${spct(pq.chg)}</span><span class="sub ${cls(dk)}">${sign0(dk)}</span>`
          : '<span class="mut">—</span>';
        const ck = costK(p), uk = unrealK(p);
        const key = `${reg.key}||${g.name}||${pi}`;
        let h = `<tr class="${p.dup ? 'dup' : ''}${extraCls ? ' ' + extraCls : ''}"><td>${esc(p.name)}${p.dup ? ' <span class="badge">dup</span>' : ''}` +
          `<span class="tk">${esc(p.ticker || p.note || '')}</span></td>` +
          `<td class="num">${mvCell}</td>` +
          `<td class="num xs-hide">${gmvAll ? (mv/gmvAll*100).toFixed(1) + '%' : '—'}</td>` +
          `<td class="num xs-hide">${ck != null ? fmt0(ck) + (p.cost_src === 'be' ? '<span class="sub mut">推算</span>' : '') : '<span class="mut">—</span>'}</td>` +
          `<td class="num xs-hide ${uk != null ? cls(uk) : 'mut'}">${uk != null
              ? sign0(uk) + `<span class="sub ${cls(uk)}">${ck ? spct(uk/ck*100) : ''}</span>` : '—'}</td>` +
          `<td class="num xs-hide">${beLive(p) != null ? beLive(p).toFixed(1) + '%' : '—'}</td>` +
          `<td class="num ${cls(ytdOf(p)||0)}">${ytdOf(p) != null ? sign0(ytdOf(p)) : '—'}</td>` +
          `<td class="num ${cls(ytdRetOf(p)||0)}">${ytdRetOf(p) != null ? spct(ytdRetOf(p)) : '—'}</td>` +
          `<td class="num">${today}</td><td>${badge(p)}</td>` +
          (EDIT ? `<td><button class="del editBtn" data-key="${esc(key)}" title="編輯">✎</button></td>` : '') +
          `</tr>`;
        if (EDIT && OPEN === key) h += editorRow(p, key, reg, g);
        return h;
      };

      // 拆分列(同 ticker + wgt 的地區成分)預設收合成一列合計,點擊展開。
      // 編輯模式一律展開,編輯器才點得到每一列。
      const items = [];
      g.positions.forEach((p, pi) => {
        const prev = items[items.length - 1];
        if (!EDIT && p.wgt && p.ticker) {
          if (prev && prev.agg && prev.ticker === p.ticker) { prev.parts.push({p, pi}); return; }
          items.push({agg: true, ticker: p.ticker, parts: [{p, pi}]}); return;
        }
        items.push({p, pi});
      });
      items.forEach(it => {
        if (!it.agg || it.parts.length < 2) {
          const one = it.agg ? it.parts[0] : it;
          out += rowHtml(one.p, one.pi); return;
        }
        const ps2 = it.parts.map(x => x.p);
        const sym = it.ticker.split(':')[0];
        const amv = ps2.reduce((s2, q2) => s2 + effMv(q2), 0);
        const ack = ps2.map(costK).filter(v => v != null).reduce((s2, v) => s2 + v, 0);
        const auk = amv - ack;
        const ay = ps2.reduce((s2, q2) => s2 + (ytdOf(q2) || 0), 0);
        const ab = ps2.reduce((s2, q2) => s2 + (ytdBaseUsd(q2) || 0), 0);
        const aday = ps2.reduce((s2, q2) => s2 + dayOf(q2), 0);
        const q0 = ps2[0], open = SPLITOPEN.has(fnv(it.ticker));
        const today = (q0.q && q0.q.chg != null)
          ? `<span class="${cls(q0.q.chg)}">${spct(q0.q.chg)}</span><span class="sub ${cls(aday)}">${sign0(aday)}</span>`
          : '<span class="mut">—</span>';
        out += `<tr class="aggrow splitTg" data-t="${esc(it.ticker)}" title="點擊${open ? '收合' : '展開'}成分">` +
          `<td><span class="gtoggle">${open ? '▾' : '▸'}</span> ${esc(sym)} 合計(${it.parts.length} 成分)` +
          `<span class="tk">${esc(it.ticker)}</span></td>` +
          `<td class="num">${fmt0(amv)}</td>` +
          `<td class="num xs-hide">${gmvAll ? (amv/gmvAll*100).toFixed(1) + '%' : '—'}</td>` +
          `<td class="num xs-hide">${fmt0(ack)}</td>` +
          `<td class="num xs-hide ${cls(auk)}">${sign0(auk)}<span class="sub ${cls(auk)}">${ack ? spct(auk/ack*100) : ''}</span></td>` +
          `<td class="num xs-hide">${ack ? ((amv / ack - 1) * 100).toFixed(1) + '%' : (q0.be != null ? (+q0.be).toFixed(1) + '%' : '—')}</td>` +
          `<td class="num ${cls(ay)}">${sign0(ay)}</td>` +
          `<td class="num ${cls(ay)}">${ab ? spct(ay/ab*100) : '—'}</td>` +
          `<td class="num">${today}</td><td>${badge(q0)}</td></tr>`;
        if (open) it.parts.forEach(x => { out += rowHtml(x.p, x.pi, 'subrow'); });
      });
      let warn = '';
      if (g.report_total && Math.abs(gmvAll - g.report_total.mv) / g.report_total.mv > 0.01)
        warn = ` <span class="warnnote">⚠ 報表小計 ${fmt0(g.report_total.mv)}</span>`;
      // 小計報酬率 = Σ損益 ÷ Σ分母(不含 dup,與左欄 P&L 同口徑)。
      // 原本優先用報表的 report_total.ret —— 那是報表日的舊值,左欄損益卻是即時的,
      // 曾有 basket 損益為負卻掛著正報酬率、也有小計高估八個百分點的案例。
      const gsr = sumRet(gnd);
      const gret = gsr
        ? `<span title="Σ損益 ÷ Σ(年初市值 + 年內買進成本)${gsr.cov < 99.5
            ? `;涵蓋本組 ${gsr.cov.toFixed(0)}% 市值` : ''}${g.report_total && g.report_total.ret != null
            ? `;報表小計 ${spct(g.report_total.ret)}(報表日口徑)` : ''}">${retCell(gsr)}</span>`
        : '—';
      // 成本、未實現、今日一律用非重複列(gnd),與右邊的 YTD 欄同口徑。
      // 原本這三欄用 g.positions(含 dup),半導體那一組的成本被算了兩次
      // (多算了近五成)、未實現跟著多出一大塊,各組「今日」加總還會變成
      // 遠大於 KPI 的數字 —— 同一頁上兩個數字互相打架。市值欄維持含 dup,
      // 因為它下面本來就標了「其中重複列示 X 不重複計入」。
      const gq = gnd.filter(p => p.q);
      const gday = gq.reduce((s, p) => s + dayOf(p), 0);
      // 分母必須與分子同一個母體:只算「有報價、且不是鏡像列」那些部位的市值。
      // 用 gmv(含 dup、含沒報價的靜態部位)會把百分比稀釋掉 —— 有一組的鏡像列
      // 佔了該組市值的九成五,一個 −5.8% 的日子會被顯示成 −0.3%,而匯出報表是
      // 對的,同一天同一組兩個地方給出差二十幾倍的數字。
      const gqmv = gq.reduce((s, p) => s + effMv(p), 0);
      const gdayCell = gq.length
        ? `<span class="${cls(gday)}">${sign0(gday)}</span>` +
          ((gqmv - gday) ? `<span class="sub ${cls(gday)}" title="以有連動報價的 ${fmt0(gqmv)} USD K 為分母(佔本組 ${(gqmv/(gmv||1)*100).toFixed(0)}%);其餘為鏡像列或無報價的報表部位">${spct(gday/(gqmv-gday)*100)}</span>` : '')
        : '<span class="mut">—</span>';
      // Breakeven 小計。be 的定義是「市值 ÷ 損益兩平值 − 1」(編輯模式那顆
      // 「由 Breakeven 反推成本」按鈕用的就是 cost = mv / (1 + be/100)),
      // 所以正確的合計是先把每一檔還原成兩平值再加總,而不是把百分比平均:
      //   小計 be = Σ市值 ÷ Σ(市值 ÷ (1 + be)) − 1
      // 這樣自動就是以金額加權,大部位不會被小部位的極端值稀釋。
      // 沒有 be 的部位整檔排除(分子分母都不計);排除的部分若佔比不小,
      // 在 title 裡講明白,免得看起來像涵蓋全組。
      const gbeSrc = gnd.filter(p => beLive(p) != null && effMv(p) > 0 && (1 + beLive(p) / 100) > 0);
      const gbeMv = gbeSrc.reduce((s, p) => s + effMv(p), 0);
      const gbeBase = gbeSrc.reduce((s, p) => s + effMv(p) / (1 + beLive(p) / 100), 0);
      const gbe = (gbeSrc.length && gbeBase > 0) ? (gbeMv / gbeBase - 1) * 100 : null;
      const gbeCov = gnet > 0 ? gbeMv / gnet * 100 : 0;
      // 狀態小計:只在這一組有問題時才出現。個股列的徽章要展開整組才看得到,
      // 收合起來的組出了問題完全沒有訊號 —— 大部位靜靜掉出今日變動就是這樣發生的。
      const gwarn = gnd.filter(p => p.q && String(p.q.qnote || '').startsWith('⚠'));
      const gnoq = gnd.filter(p => p.kind === 'live' && !p.q);
      const gstat = [
        gwarn.length ? `<span class="badge warn" title="${esc(gwarn.map(p =>
            p.name + ':' + p.q.qnote).join('、'))}">⚠ ${gwarn.length}</span>` : '',
        gnoq.length ? `<span class="badge warn" title="${esc(gnoq.map(p =>
            p.name).join('、'))}">待接報價 ${gnoq.length}</span>` : '',
      ].filter(Boolean).join(' ');
      const gck = gnd.map(costK).filter(v => v != null).reduce((s,v)=>s+v, 0);
      const guk = gnd.map(unrealK).filter(v => v != null).reduce((s,v)=>s+v, 0);
      const hasCost = gnd.some(p => costK(p) != null);
      out += `<tr class="subtotal"><td>小計<span class="sub">計入總資產 ${fmt0(gnet)}(${(gnet/tot*100).toFixed(1)}%)`
        + (gdupMv ? ` · 其中重複列示 ${fmt0(gdupMv)} 不重複計入` : '') + `</span>${warn}</td>` +
        `<td class="num">${fmt0(gmv)}</td><td class="num xs-hide">100.0%</td>` +
        `<td class="num xs-hide">${hasCost ? fmt0(gck) : ''}</td>` +
        `<td class="num xs-hide ${hasCost ? cls(guk) : ''}">${hasCost ? sign0(guk) + `<span class="sub ${cls(guk)}">${gck ? spct(guk/gck*100) : ''}</span>` : ''}</td>` +
        `<td class="num xs-hide${gbe == null ? ' mut' : ''}"${gbe != null
            ? ` title="以市值加權(Σ市值 ÷ Σ兩平值 − 1)${gbeCov < 99.5
                ? `;涵蓋本組 ${gbeCov.toFixed(0)}% 市值,其餘部位沒有 Breakeven 資料` : ''}"` : ''}>` +
          `${gbe != null ? gbe.toFixed(1) + '%' : '—'}${gbe != null && gbeCov < 99.5
            ? `<span class="sub mut">${gbeCov.toFixed(0)}% 涵蓋</span>` : ''}</td>` +
        `<td class="num ${cls(gpl)}">${sign0(gpl)}</td><td class="num ${cls(gpl)}">${gret}</td><td class="num">${gdayCell}</td><td>${gstat}</td>${EDIT?'<td></td>':''}</tr>`;
      out += `</tbody></table></div></div>`;
    });
    if (EDIT) out += `<div class="btnrow"><button class="addGrp" data-reg="${esc(reg.key)}">＋ 新增 basket</button></div>`;
    if (reg.key === 'japan') out += `<div class="note">報表口徑:日本合計不含 PE;此處小計含 PE 一併列示。</div>`;
    if (reg.key === 'semi') out += `<div class="note">此視角與台股部位重疊(dup 列),總計僅計入海外部分。</div>`;
    out += `</div>`;
  });
  $('regions').innerHTML = out;

  // ── 已出清部位追蹤:出清價 vs 現價,看賣對還是賣早 ──────────────
  // 現價由建置時從報價檔帶入(entry.now);頁面剛出清、還沒跑過建置的顯示 —。
  const cc = $('closedcard');
  if (cc) {
    // 以前這裡濾的是 c.ticker,沒有代號的紀錄整列看不到 —— 手動補登的通常就沒代號。
    const items = (P.closed_ytd || []).filter(c => c.ticker || c.name);
    if (!items.length && !EDIT) { cc.innerHTML = ''; }
    else {
      cc.innerHTML = `<h2>已出清部位追蹤${EDIT ? ' <button id="addClosed" class="del" style="font-size:12px;padding:2px 8px">＋ 補登</button>' : ''}</h2><div class="tblwrap"><table>
        <thead><tr><th>部位</th><th class="num">出清日</th><th class="num">出清時年度損益</th>
        <th class="num">出清價</th><th class="num">現價</th>
        <th class="num" title="該檔今天的漲跌">今日</th>
        <th class="num">出清後漲跌</th><th>判讀</th>${EDIT ? '<th></th>' : ''}</tr></thead><tbody>
        ${items.map(c => {
          const q = CLOSEDQ[c.ticker] || {};
          const now = q.price != null ? +q.price : null;
          const chg = (now != null && c.exit) ? (now / c.exit - 1) * 100 : null;
          const d = (q.chg == null || now == null) ? null : +q.chg;   // 今日漲跌 %
          const tag = chg == null ? '<span class="mut">待下輪報價</span>'
            : chg <= -3 ? '<span class="badge lv">賣對了</span>'
            : chg >= 3 ? '<span class="badge warn">賣早了</span>'
            : '<span class="badge st">差不多</span>';
          const ci = (P.closed_ytd || []).indexOf(c);
          return `<tr><td>${esc(c.name)}<span class="tk">${esc(c.ticker || '')}</span></td>` +
            `<td class="num">${esc((c.on || '').slice(5).replace('-', '/'))}</td>` +
            `<td class="num ${cls(+c.usd_k)}">${sign0(+c.usd_k)}</td>` +
            `<td class="num">${c.exit ? (+c.exit).toLocaleString('en-US', {maximumFractionDigits: 2}) : '—'}</td>` +
            `<td class="num">${now != null ? now.toLocaleString('en-US', {maximumFractionDigits: 2}) : '—'}</td>` +
            `<td class="num ${d != null ? cls(d) : 'mut'}">${d != null ? spct(d) : '—'}</td>` +
            `<td class="num ${chg != null ? cls(chg) : 'mut'}">${chg != null ? spct(chg) : '—'}</td>` +
            `<td>${tag}</td>` +
            (EDIT ? `<td><button class="del delClosed" data-i="${ci}" title="刪除這筆">✕</button></td>` : '') +
            `</tr>`;
        }).join('')}
        </tbody></table></div>
      <div class="note" style="margin-top:6px">出清價 = 出清當時最後成交價(快照);漲跌以原報價幣別計。出清後下跌 3% 以上判「賣對」,上漲 3% 以上判「賣早」。
      <b>今日</b>是該檔今天的漲跌。剛出清、還沒跑過建置的部位要等下一輪才有報價。</div>`;
    }
    // ── 減碼(部分出脫)──────────────────────────────────────────────
    // 與出清分開一張表:欄位不一樣(有減碼幅度、沒有「出清時年度損益」),
    // 而且判讀的意思也不同 —— 減碼是「留下來的那一半跑贏賣掉的那一半嗎」。
    // 只看近半年:再往前的減碼跟「現在該不該加回來」已經沒什麼關係,
    // 而且這張表會隨著每次對帳單一直長。以結束日為準,沒有日期的一律留著。
    const CUT = Date.now() - 190 * 864e5;
    const allTrims = (P.trims || []).filter(t => +t.u0 > 0 && +t.u1 < +t.u0);
    const trims = allTrims.filter(t => {
      const d = Date.parse(t.to || t.on || '');
      return isNaN(d) || d >= CUT;
    });
    const older = allTrims.length - trims.length;
    // 拆分列在這張表上要合成一列 —— 使用者看的是「整檔減了多少」,
    // 不是五個地區各減多少。股數與已實現相加,出場價以減碼股數加權(修好 wgt 分攤之後
    // 五列的出場價本來就相同,加權只是保險)。名稱取五列的共同前綴。
    const merged = (() => {
      const g = new Map();
      trims.forEach(t => {
        const k = [t.ticker || t.name, t.on || '', t.to || ''].join('|');
        (g.get(k) || g.set(k, []).get(k)).push(t);
      });
      const pre = ns => {
        let p = ns[0] || '';
        ns.forEach(n => { let i = 0; while (i < p.length && p[i] === n[i]) i++; p = p.slice(0, i); });
        p = p.replace(/[\s(（\-–—/、]+$/, '').trim();
        return p || ns[0];
      };
      return [...g.values()].map(rows => {
        if (rows.length === 1) return rows[0];
        const u0 = rows.reduce((s, t) => s + (+t.u0 || 0), 0);
        const u1 = rows.reduce((s, t) => s + (+t.u1 || 0), 0);
        const rk = rows.every(t => t.real_k == null) ? null
                 : rows.reduce((s, t) => s + (+t.real_k || 0), 0);
        let ex = null, w = 0;
        rows.forEach(t => {
          const sold = (+t.u0 || 0) - (+t.u1 || 0);
          if (t.exit != null && sold > 0) { ex = (ex || 0) + +t.exit * sold; w += sold; }
        });
        return {...rows[0], name: pre(rows.map(t => String(t.name || ''))),
                u0, u1, real_k: rk, exit: w > 0 ? ex / w : null, _n: rows.length};
      });
    })();
    if (trims.length) {
      const per = t => {
        const a = String(t.on || ''), b = String(t.to || '');
        const md = x => x.slice(5).replace('-', '/');            // MM/DD
        const ym = x => x.slice(0, 7).replace('-', '/');         // YYYY/MM
        if (a && b) {
          // 窗口只有幾天(兩份對帳單相鄰)就直接給日期,不要寫成「08–09」那種
          // 看起來橫跨兩個月的樣子 —— 08/31 賣的和 8 月初賣的不該長一樣。
          const days = (Date.parse(b) - Date.parse(a)) / 864e5;
          if (isFinite(days) && days <= 10) return a === b ? md(a) : md(a) + '–' + md(b);
          return ym(a) === ym(b) ? ym(a) : ym(a) + '–' + b.slice(5, 7);
        }
        return (a || b) ? ym(a || b) : '—';
      };
      cc.innerHTML += `<h2 style="margin-top:18px">減碼部位追蹤</h2><div class="tblwrap"><table>
        <thead><tr><th>部位</th><th class="num">期間</th><th class="num" title="股數變化">減碼幅度</th>
        <th class="num" title="這段期間該部位新增的已實現損益,換算 USD K">減碼已實現</th>
        <th class="num" title="由已實現損益反推的平均出場價,非帳面成交價">出場價 ~</th>
        <th class="num">現價</th><th class="num" title="該檔今天的漲跌">今日</th>
        <th class="num">出場後漲跌</th><th>判讀</th></tr></thead><tbody>
        ${merged.map(t => {
          // CLOSEDQ 是建置時產生的,剛在瀏覽器記下的減碼不在裡面。但減碼的部位
          // 依定義還在持倉中、本來就有報價,退回去拿它自己的即可。
          const held = t.ticker ? allPos().find(p => p.ticker === t.ticker && p.q) : null;
          const q = (t.ticker && CLOSEDQ[t.ticker])
                  || (held ? {price: held.q.price, chg: held.q.chg} : {});
          const now = q.price != null ? +q.price : null;
          const ex = t.exit != null ? +t.exit : null;
          // 出場價來自對帳單、現價來自報價源,兩邊幣別/計價單位不見得同一套
          // (英股的便士 vs 英鎊就差 100 倍)。差太多寧可不報漲跌。
          const ok = (now != null && ex > 0 && now / ex > 0.5 && now / ex < 2);
          const chg = ok ? (now / ex - 1) * 100 : null;
          const d = (q.chg == null || now == null) ? null : +q.chg;
          const cut = (+t.u1 / +t.u0 - 1) * 100;
          const f = FX[t.cur];
          const rk = (t.real_k != null && f) ? +t.real_k / f : null;
          const tag = chg == null ? '<span class="mut">—</span>'
            : chg <= -3 ? '<span class="badge lv">減對了</span>'
            : chg >= 3 ? '<span class="badge warn">減早了</span>'
            : '<span class="badge st">差不多</span>';
          const num = v => v == null ? '—' : v.toLocaleString('en-US', {maximumFractionDigits: 2});
          return `<tr><td>${esc(t.name)}${t.ticker ? `<span class="tk">${esc(t.ticker)}</span>` : ''}</td>` +
            `<td class="num">${esc(per(t))}</td>` +
            `<td class="num" title="${(+t.u0).toLocaleString('en-US')} → ${(+t.u1).toLocaleString('en-US')} 股">${cut.toFixed(1)}%</td>` +
            `<td class="num ${rk != null ? cls(rk) : 'mut'}">${rk != null ? sign0(rk) : '—'}</td>` +
            `<td class="num">${ex != null ? num(ex) : '—'}</td>` +
            `<td class="num">${num(now)}</td>` +
            `<td class="num ${d != null ? cls(d) : 'mut'}">${d != null ? spct(d) : '—'}</td>` +
            `<td class="num ${chg != null ? cls(chg) : 'mut'}">${chg != null ? spct(chg) : '—'}</td>` +
            `<td>${tag}</td></tr>`;
        }).join('')}
        </tbody></table></div>
      <div class="note" style="margin-top:6px">這裡列的是<b>減碼</b>(部位還在,只是變小),與上面的出清分開看。
      <b>期間</b>目前是區間不是日期:期初這批是用 2025/12/31 與 2026/08/26 兩份對帳單回推的,中間沒有其他對帳單,不知道確切減在哪一天。
      <b>出場價</b>標了 <b>~</b>,是用「該期間新增的已實現損益 ÷ 減碼股數 + 當時每股成本」反推的平均價,不是帳面成交價;
      同一期間內有買有賣時會被平均掉。反推值不合理(落在區間價格外、或已實現超過賣出金額)就留空。
      往後每次套用對帳單都會自動補記新的減碼,期間就會收斂成精確日期。${older ? `<br>另有 ${older} 筆超過半年的減碼未列示。` : ''}</div>`;
    }
  }

  // 補登 / 刪除已出清紀錄。為什麼需要:2026-09-02 那份對帳單把兩檔韓股
  // 從持倉移除了,但兩筆都沒有寫進 closed_ytd —— 部位消失、年度損益跟著少一塊,
  // 而「已出清部位追蹤」裡完全看不到,原本沒有任何補救管道。
  const _ac = $('addClosed');
  if (_ac) _ac.addEventListener('click', () => {
    const name = (prompt('部位名稱') || '').trim();
    if (!name) return;
    const tk = (prompt('報價代號,用來算「出清後漲跌」(格式 代號:交易所,例 XXXX:TPE;可留空)') || '').trim();
    const on = (prompt('出清日 YYYY-MM-DD', todayTaipei()) || todayTaipei()).trim();
    const ex = parseFloat(prompt('出清價(原幣別,可留空)') || '');
    const un = parseFloat(prompt('出清股數(可留空)') || '');
    const pl = parseFloat(prompt('出清時的年度損益(USD K,正負皆可)\n'
                                 + '這一筆會計入年度損益,請填券商對帳單上的數字') || '');
    if (!isFinite(pl)) { alert('年度損益沒填,取消補登。'); return; }
    const cur = (prompt('出清價的幣別(例:KRW;可留空)') || '').trim();
    P.closed_ytd = P.closed_ytd || [];
    const rec = {name, usd_k: +pl.toFixed(1), on,
                 ticker: tk || null, exit: isFinite(ex) ? ex : null,
                 cur: cur || null, units: isFinite(un) ? un : null};
    DROPPED.delete(dropKey(rec));          // 曾經刪過同一筆的話,補登要能救回來
    P.closed_ytd.push(rec);
    commit();
  });
  document.querySelectorAll('.delClosed').forEach(b => b.addEventListener('click', () => {
    const i = +b.dataset.i, c = (P.closed_ytd || [])[i];
    if (c && confirm(`刪除已出清紀錄「${c.name}」?年度損益會少 ${sign0(+c.usd_k || 0)} USD K。`)) {
      DROPPED.add(dropKey(c));            // 記進作廢清單,其他裝置的副本才不會把它救回來
      P.closed_ytd.splice(i, 1); commit();
    }
  }));

  renderNav();
  applyHidePL();          // 每次重繪都要重新套用(工具列按鈕文字也在這裡同步)

  // YTD P&L top contributors
  const top = [...nd].filter(p => ytdOf(p) != null).sort((a,b) => Math.abs(ytdOf(b)) - Math.abs(ytdOf(a))).slice(0, 12);
  const W = 660, BH = 24, GAP = 12, LAB = 170;
  const maxabs = Math.max(...top.map(p => Math.abs(ytdOf(p))), 1);
  const cw = (W - LAB - 90) / 2, cx = LAB + cw;
  const h = top.length * (BH + GAP) - GAP;
  let svg = `<svg viewBox="0 0 ${W} ${h}" role="img" aria-label="YTD 損益主要貢獻">` +
    `<line x1="${cx}" y1="0" x2="${cx}" y2="${h}" stroke="var(--baseline)" stroke-width="1"/>`;
  top.forEach((p, i) => {
    const v = ytdOf(p) || 0;
    const y = i * (BH + GAP), w = Math.abs(v) / maxabs * cw, xx = v >= 0 ? cx : cx - w;
    const lx = v >= 0 ? cx + w + 8 : cx - w - 8, anc = v >= 0 ? 'start' : 'end';
    svg += `<g class="bar" data-tip="${esc(p.name)}(${esc(p._r.name)}):YTD ${sign0(ytdOf(p)||0)} USD K(${ytdRetOf(p) != null ? spct(ytdRetOf(p)) : '—'})">` +
      `<rect x="${LAB}" y="${y}" width="${W-LAB}" height="${BH}" fill="transparent"/>` +
      `<rect x="${xx.toFixed(1)}" y="${y}" width="${Math.max(w,1).toFixed(1)}" height="${BH}" rx="4" fill="var(--${v >= 0 ? 'up' : 'down'})"/>` +
      `<text x="${LAB-10}" y="${y+BH/2}" text-anchor="end" dominant-baseline="central" class="lab">${esc(p.name)}</text>` +
      `<text x="${lx.toFixed(1)}" y="${y+BH/2}" text-anchor="${anc}" dominant-baseline="central" class="val ${cls(v)}">${sign0(v)}</text></g>`;
  });
  $('plchart').innerHTML = svg + '</svg>';

  // 欄位排序:點表頭切換 遞減→遞增→原順序(只在非編輯模式;小計列固定在底)
  if (!EDIT) document.querySelectorAll('.grp[data-gkey] thead th').forEach((th, _, all) => {
    if (!th.innerText.trim() || th.innerText === '部位' || th.innerText === '狀態') return;
    th.classList.add('sortable');
    th.addEventListener('click', e => {
      e.stopPropagation();
      const table = th.closest('table'), tb = table.querySelector('tbody');
      const idx = [...th.parentElement.children].indexOf(th);
      const dir = th.dataset.dir === 'desc' ? 'asc' : (th.dataset.dir === 'asc' ? '' : 'desc');
      table.querySelectorAll('th').forEach(x => { delete x.dataset.dir; x.classList.remove('s-asc','s-desc'); });
      const rows = [...tb.querySelectorAll('tr')].filter(r => !r.classList.contains('subtotal'));
      const sub = tb.querySelector('tr.subtotal');
      if (!dir) {           // 第三下:還原原始順序
        render(); return;
      }
      th.dataset.dir = dir; th.classList.add(dir === 'asc' ? 's-asc' : 's-desc');
      const num = r => {
        const td = r.children[idx]; if (!td) return -Infinity;
        // 只取第一行(主值)。整格取的話主值與小字百分比會被黏成
        // 1664383.5,排序就變成照一個不存在的數字排。
        const m = td.innerText.split('\n')[0].replace(/[,+%]/g, '').match(/-?[\d.]+/);
        return m ? +m[0] : -Infinity;
      };
      rows.sort((a, b) => dir === 'desc' ? num(b) - num(a) : num(a) - num(b));
      rows.forEach(r => tb.insertBefore(r, sub));
    });
  });

  // 群組摺疊:點標題切換(編輯模式下標題是輸入框,不啟用)
  if (!EDIT) document.querySelectorAll('.grp[data-gkey] > h3').forEach(h => {
    h.style.cursor = 'pointer';
    h.addEventListener('click', () => {
      const k = h.parentElement.dataset.gkey;
      COLLAPSED.has(fnv(k)) ? COLLAPSED.delete(fnv(k)) : COLLAPSED.add(fnv(k));
      saveColl(); render();
    });
  });

  // wire edits + import/export
  // 限定在部位表內:曝險卡的避險欄位與手動項目也帶 .mvI,被這裡掃到會因為沒有
  // data-reg 而丟例外(目前只是剛好在幾行之後被 renderFx 重畫掉才沒出事)。
  document.querySelectorAll('#regions .mvI').forEach(inp => inp.addEventListener('change', () => {
    const reg = P.regions.find(r => r.key === inp.dataset.reg);
    const g = reg && reg.groups.find(g => g.name === inp.dataset.grp);
    if (!reg || !g) return;              // 群組剛被改名 → 找不到,別丟例外把編輯吃掉
    const v = parseFloat(inp.value.replace(/,/g, ''));
    if (!isNaN(v)) { const p = g.positions[+inp.dataset.i]; if (p) { p.mv = v; stampNav(p); } }
    commit();
  }));
  $('rptBtn').addEventListener('click', openReport);
  $('expJson').addEventListener('click', () => {
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([JSON.stringify(P, null, 2)], {type: 'application/json'}));
    a.download = `portfolio-${todayTaipei().replace(/-/g, '')}.json`; a.click(); URL.revokeObjectURL(a.href);
  });
  $('impFile').addEventListener('change', ev => {
    const f = ev.target.files[0]; if (!f) return;
    const rd = new FileReader();
    rd.onload = e => { try { const j = JSON.parse(e.target.result);
        if (!j.regions) throw new Error('格式不符');
        P.regions = j.regions;
        if (j.hedges) P.hedges = j.hedges;
        if (j.fx_track) P.fx_track = j.fx_track;
        if (j.fx_manual) P.fx_manual = j.fx_manual;
        commit(); $('msg').textContent = '已匯入組合設定(已存到本機)。';
      } catch (err) { $('msg').textContent = '匯入失敗:' + err.message; } };
    rd.readAsText(f); ev.target.value = '';
  });
  attachTips();
  wireEditing();
  renderFx();
  renderHistory();
}

// 外部發佈版:每 15 分鐘自動重新載入,確保看到最新一版
// 重載帶時間戳:GitHub Pages/手機瀏覽器會把同網址的舊頁快取很久,
// 換版後「一直看到舊的」全是這個造成的 —— 帶參數保證每次都拿最新發布。
setTimeout(() => location.replace(location.pathname + '?t=' + Date.now()), 15 * 60 * 1000);

// ══ 外幣曝險與避險比率 ══
// 幣別認定:日本區一律 JPY(穿透口徑,含 PE 與美元計價級別);其餘依部位計價幣別
const CURNAME = {JPY:'日圓', KRW:'韓元', TWD:'台幣', USD:'美元', HKD:'港幣', CNY:'人民幣', EUR:'歐元', GBP:'英鎊'};
// 曝險幣別:exp_cur(底層資產所在幣別)優先,其次日本區一律 JPY,再其次看計價幣別。
// 用於「美元 ETF 但底層是他國股票」的拆分列。
const curOf = (p, regKey) => p.exp_cur || (regKey === 'japan' ? 'JPY' : (p.cur || (p.q && p.q.cur) || 'USD'));

// 幣別曝險明細的展開狀態(可同時展開多個幣別,偏好記在本機)
const FXOPEN_KEY = 'fx_open_v1';
const FXOPEN = new Set((() => {
  try { return JSON.parse(localStorage.getItem(FXOPEN_KEY) || '[]'); } catch (e) { return []; }
})());
const saveFxOpen = () => {
  // 只留還在追蹤中的幣別,免得取消追蹤又加回來時無故自己展開
  (P.fx_track || []).length && [...FXOPEN].forEach(c => {
    if (!P.fx_track.includes(c)) FXOPEN.delete(c);
  });
  try { localStorage.setItem(FXOPEN_KEY, JSON.stringify([...FXOPEN])); } catch (e) {}
};

function renderFx() {
  const host = $('fxexp'); if (!host) return;
  P.hedges   = P.hedges   || {};
  P.fx_track = P.fx_track || ['JPY', 'KRW'];        // 追蹤哪些幣別
  P.fx_manual= P.fx_manual|| [];                    // 手動曝險項目(部位以外)

  // 由部位計算的幣別曝險
  const buckets = {};
  P.regions.forEach(r => r.groups.forEach(g => g.positions.forEach(p => {
    if (isDup(p)) return;
    const c = curOf(p, r.key);
    (buckets[c] = buckets[c] || {mv: 0, n: 0, items: []});
    const mv = effMv(p);
    buckets[c].mv += mv; buckets[c].n++;
    // 明細用:拆分列同名多筆,靠群組名區分
    buckets[c].items.push({name: p.name || '(未命名)', grp: `${r.name} › ${g.name}`, mv});
  })));
  // 分母要與分子同一套:手動曝險項目算進各幣別的曝險合計,分母就不能只用
  // 部位市值(否則日圓會顯示成佔 47.8%,而風險卡上的同一個數字是 44.7%)。
  const manTot = (P.fx_manual || []).reduce((s, m) => s + (+m.mv || 0), 0);
  const tot = Object.values(buckets).reduce((s, b) => s + b.mv, 0) + manTot;
  const nameOf = c => CURNAME[c] || c;

  const rows = P.fx_track.map(c => {
    const b = buckets[c] || {mv: 0, n: 0, items: []};
    const man = P.fx_manual.filter(m => m.cur === c);
    const manMv = man.reduce((s, m) => s + (+m.mv || 0), 0);
    const mv = b.mv + manMv;
    const h = +(P.hedges[c] || 0);
    return {c, nm: nameOf(c), mv, posMv: b.mv, manMv, n: b.n, man, h, items: b.items,
            ratio: mv ? h / mv * 100 : 0, net: mv - h};
  });

  // 手動曝險項目自己就是一個幣別來源。只看 buckets(部位)的話,一個只有手動
  // 項目的幣別被取消追蹤後就不會出現在「追蹤其他幣別」裡,那筆錢從此看不到。
  const untracked = [...new Set([...Object.keys(buckets), ...P.fx_manual.map(m => m.cur)])]
    .filter(c => c && !P.fx_track.includes(c));
  const CURLIST = ['JPY','KRW','TWD','USD','HKD','CNY','EUR','GBP','SGD','AUD'];

  // 明細面板:放在表格「下方」而不是塞進 <td colspan>,否則手機上會被關進
  // 表格的橫向捲動區裡,要左右拉才看得到。
  const detail = r => {
    const items = r.items.map(x => ({...x, man: false}))
      .concat(r.man.map(m => ({name: m.name || '(未命名)', grp: '手動曝險項目',
                               mv: +m.mv || 0, man: true})))
      .filter(x => x.mv).sort((a, b) => b.mv - a.mv);
    if (!items.length) return `<div class="grp" style="margin-top:12px">` +
      `<h3>${esc(r.nm)}曝險明細</h3><div class="note">此幣別目前沒有有金額的部位。</div></div>`;
    const mx = items[0].mv || 1;
    // 分成上市股票 / 私募 兩類。差別在能不能在市場上處理掉 —— 這正是看幣別
    // 曝險時最需要知道的事:上市的那段隨時能調整或避險,私募那段在基金到期或
    // 出場前動不了,避險比率要對照著看才有意義。
    // 判斷靠群組名稱(PE 群組 = 私募),手動項目自成一類;之後要覆寫可在部位上
    // 填 liq 欄位。Activist 與 Active Fund 歸在上市股票 —— 底層是上市日股,
    // 只是包在基金裡、淨值月更,這點寫在下面的附註裡。
    const liqOf = x => x.liq || (x.man ? '手動' : (/PE|私募|創投|VC/i.test(x.grp) ? '私募' : '上市'));
    const bag = {上市: 0, 私募: 0, 手動: 0};
    items.forEach(x => { bag[liqOf(x)] += x.mv; });
    const pctOf = v => r.mv ? (v / r.mv * 100).toFixed(1) : '0.0';
    const parts = [['上市', '上市股票'], ['私募', '私募 / 未上市'], ['手動', '手動項目']]
      .filter(([k]) => bag[k] > 0)
      .map(([k, lbl]) => `${lbl} ${fmt0(bag[k])}(${pctOf(bag[k])}%)`).join(' · ');
    const TAG = {上市: 'lq-pub', 私募: 'lq-pe', 手動: 'lq-man'};
    return `<div class="grp" style="margin-top:12px">
      <h3>${esc(r.nm)}曝險明細 <span class="mut" style="font-weight:400;font-size:12.5px">
        ${fmt0(r.mv)} · ${items.length} 項(含手動)· 佔總資產 ${tot ? (r.mv / tot * 100).toFixed(1) : '0.0'}%</span></h3>
      <div class="note" style="margin:2px 0 8px">${parts}</div>
      <div class="fxdet">${items.map(x => { const k = liqOf(x); return `
        <div class="fr">
          <span class="fl" title="${esc(x.name)} — ${esc(x.grp)}">${esc(x.name)}
            <span class="lqt ${TAG[k]}">${k}</span><span class="fg">${esc(x.grp)}</span></span>
          <span class="fb"><i style="width:${(x.mv / mx * 100).toFixed(1)}%"></i></span>
          <span class="fv">${fmt0(x.mv)}</span>
          <span class="fp">${r.mv ? (x.mv / r.mv * 100).toFixed(1) : '0.0'}%</span>
        </div>`; }).join('')}</div>
      <div class="note" style="margin-top:8px">依曝險金額排序;比率為佔${esc(r.nm)}曝險合計。避險名目不在此表內。
      <b>上市</b>=底層為上市股票,含 Activist 與 Active Fund(包在基金裡、淨值月更,但持有的是上市日股);
      <b>私募</b>=PE 與不動產基金,出場前無法在市場上處理,避險比率要扣掉這一段來看;
      <b>手動</b>=部位表以外自行輸入的項目。</div>
    </div>`;
  };

  host.innerHTML = `
   <div class="tiles" style="margin-bottom:14px">
     ${rows.map(r => `
     <div class="tile fxtog" data-cur="${esc(r.c)}"><div class="t">${esc(r.nm)}曝險 (USD K)</div><div class="v">${fmt0(r.mv)}</div>
       <div class="d mut">佔資產${manTot ? '(含手動)' : ''} ${tot ? (r.mv / tot * 100).toFixed(1) : '0.0'}%${r.manMv ? ` · 含手動 ${fmt0(r.manMv)}` : ''}</div></div>
     <div class="tile"><div class="t">${esc(r.nm)}避險比率</div><div class="v">${r.ratio.toFixed(1)}%</div>
       <div class="d mut">淨曝險 ${fmt0(r.net)}</div></div>`).join('')}
   </div>
   <div style="overflow-x:auto"><table class="fxtbl">
     <thead><tr><th>幣別</th><th class="num">部位曝險</th><th class="num">手動項目</th><th class="num">曝險合計</th>
     <th class="num">佔資產</th><th class="num">避險名目(可輸入)</th><th class="num">避險比率</th>
     <th class="num">淨曝險</th><th class="num">淨佔資產</th><th></th></tr></thead>
     <tbody>${rows.map(r => `<tr>
       <td class="fxtog" data-cur="${esc(r.c)}" title="點擊看明細">
         <span class="cev">${FXOPEN.has(r.c) ? '▾' : '▸'}</span> ${esc(r.nm)}
         <span class="tk">${esc(r.c)} · ${r.n} 個部位${r.man.length ? ` + ${r.man.length} 手動` : ''}</span></td>
       <td class="num">${fmt0(r.posMv)}</td>
       <td class="num">${r.manMv ? fmt0(r.manMv) : '<span class="mut">—</span>'}</td>
       <td class="num"><b>${fmt0(r.mv)}</b></td>
       <td class="num">${tot ? (r.mv / tot * 100).toFixed(1) : '0.0'}%</td>
       <td class="num"><input class="mvI hedgeI" data-cur="${esc(r.c)}" value="${esc(r.h)}"></td>
       <td class="num">${r.ratio.toFixed(1)}%</td>
       <td class="num">${fmt0(r.net)}</td>
       <td class="num">${tot ? (r.net / tot * 100).toFixed(1) : '0.0'}%</td>
       <td>${P.fx_track.length > 1 ? `<button class="del untrack" data-cur="${esc(r.c)}" title="取消追蹤">✕</button>` : ''}</td></tr>`).join('')}
     </tbody></table></div>

   ${rows.filter(r => FXOPEN.has(r.c)).map(detail).join('')}

   ${(EDIT || P.fx_manual.length) ? `<div class="grp" style="margin-top:14px">
     <h3>手動曝險項目(部位表以外的資產)</h3>
     <div style="overflow-x:auto"><table>
       <thead><tr><th>幣別</th><th>項目</th><th class="num">金額 USD K</th><th></th></tr></thead>
       <tbody>
       ${EDIT ? P.fx_manual.map((m, i) => `<tr>
         <td><select class="fxmF" data-i="${i}" data-k="cur">${CURLIST.map(c=>`<option${m.cur===c?' selected':''}>${esc(c)}</option>`).join('')}</select></td>
         <td><input class="fxmF" data-i="${i}" data-k="name" value="${esc(m.name || '')}" style="min-width:220px"></td>
         <td class="num"><input class="mvI fxmF" data-i="${i}" data-k="mv" value="${esc(m.mv ?? '')}"></td>
         <td><button class="del fxmDel" data-i="${i}" title="刪除">✕</button></td></tr>`).join('')
       : P.fx_manual.map(m => `<tr>
         <td>${esc(m.cur)}</td><td>${esc(m.name || '')}</td>
         <td class="num">${fmt0(+m.mv || 0)}</td><td></td></tr>`).join('')}
       ${EDIT ? `<tr><td><select id="nfCur">${CURLIST.map(c=>`<option${c==='JPY'?' selected':''}>${esc(c)}</option>`).join('')}</select></td>
         <td><input id="nfName" placeholder="例:日圓定存 / 日本不動產" style="min-width:220px"></td>
         <td class="num"><input id="nfMv" class="mvI" placeholder="USD K"></td>
         <td><button id="nfAdd" class="primary" style="padding:4px 12px">＋ 新增</button></td></tr>` : ''}
       </tbody></table></div>
     ${EDIT ? '' : '<div class="note">要新增或修改,請開啟上方「✎ 編輯模式」。</div>'}
   </div>` : ''}

   ${untracked.length ? `<div class="btnrow" style="margin-top:8px">
     <span class="note">追蹤其他幣別:</span>
     ${untracked.map(c => `<button class="trackCur" data-cur="${esc(c)}">＋ ${esc(nameOf(c))} (${buckets[c] ? fmt0(buckets[c].mv) : '手動'})</button>`).join('')}
   </div>` : ''}

   <div class="note" style="margin-top:10px">
     各幣別分別計算避險比率,不做合計。<b>日圓</b>採穿透口徑 —— 日本區全部部位計入(含 PE、含美元/英鎊計價級別),因底層均為日本資產、
     匯率風險未於基金層級避除。<b>韓元</b>包含韓元計價部位,以及美元 ETF 拆分出的韓國成分(底層為韓股,已設曝險幣別 KRW)。<br>
     <b>手動曝險項目</b>用來補上部位表沒有的資產(外幣存款、不動產、未列入的帳戶等),金額請換算成 USD K;
     它只影響曝險與避險比率,不進總資產與各區配置。「避險名目」填已賣出的遠期/期貨名目本金,正值代表放空該幣別。<br>
     ${(SYNC && SYNC.token) ? '頁面上的輸入會<b>自動同步到所有裝置</b>,並在下一次自動更新時套用到公開網頁 —— 不需要匯出。' : '頁面上的輸入只會存在這台瀏覽器;未啟用同步,要正式生效請「匯出目前組合」把 JSON 回傳給 Claude。'}
   </div>`;

  host.querySelectorAll('.fxtog').forEach(el => el.addEventListener('click', ev => {
    if (ev.target.closest('input, button, select')) return;   // 別攔避險輸入與取消追蹤
    const c = el.dataset.cur;
    FXOPEN.has(c) ? FXOPEN.delete(c) : FXOPEN.add(c);
    saveFxOpen(); renderFx();
  }));

  // change 在 blur 時觸發,若當場重畫 DOM,使用者「輸入完直接點另一列」的那一下
  // 會因為節點被換掉而收不到 click。延到下一個 tick 再重畫。
  host.querySelectorAll('.hedgeI').forEach(inp => inp.addEventListener('change', () => {
    const v = parseFloat(String(inp.value).replace(/,/g, ''));
    const nv = isNaN(v) ? 0 : v;
    if (nv === (+P.hedges[inp.dataset.cur] || 0)) return;   // 沒改就不重畫
    P.hedges[inp.dataset.cur] = nv;
    setTimeout(commitFx, 0);
  }));
  host.querySelectorAll('.fxmF').forEach(el => el.addEventListener('change', () => {
    const m = P.fx_manual[+el.dataset.i]; if (!m) return;
    const k = el.dataset.k;
    m[k] = (k === 'mv') ? (parseFloat(String(el.value).replace(/,/g,'')) || 0) : el.value.trim();
    if (k === 'cur' && !P.fx_track.includes(m.cur)) P.fx_track.push(m.cur);
    commitFx();
  }));
  host.querySelectorAll('.fxmDel').forEach(b => b.addEventListener('click', () => {
    P.fx_manual.splice(+b.dataset.i, 1); commitFx();
  }));
  const add = $('nfAdd');
  if (add) add.addEventListener('click', () => {
    const cur = $('nfCur').value, name = $('nfName').value.trim();
    const mv = parseFloat(String($('nfMv').value).replace(/,/g,''));
    if (!name || isNaN(mv)) { alert('請填項目名稱與金額'); return; }
    P.fx_manual.push({cur, name, mv});
    if (!P.fx_track.includes(cur)) P.fx_track.push(cur);
    commitFx();
  });
  host.querySelectorAll('.trackCur').forEach(b => b.addEventListener('click', () => {
    if (!P.fx_track.includes(b.dataset.cur)) P.fx_track.push(b.dataset.cur);
    commitFx();
  }));
  host.querySelectorAll('.untrack').forEach(b => b.addEventListener('click', () => {
    P.fx_track = P.fx_track.filter(c => c !== b.dataset.cur); commitFx();
  }));
}

// ══ 資產走勢(資料由 Actions 每次執行累積,存於 gh-pages 的加密 history.enc)══
// 第二條線是「指數基準」:每天用當日各區域配置比重 × 該區域大盤指數(換算成 USD)
// 串起來,靜態部位(PE/Activist/海外基金)在兩條線裡都用實際淨值原樣帶過 ——
// 那些淨值一個月才更新一次,逐日比大盤只會做出假的追蹤誤差。欄位由 Actions 端
// 寫入(bench=v[6]、live=v[7]、五個權重 v[8..12]);舊資料沒有這些欄,自動不畫。
const BENCHNAME = [['主題', 'ACWI'], ['中國', '恒生'], ['台股', '加權'],
                   ['日本', '日經'], ['半導體', '費半']];
function renderHistory() {
  // 至少要有 tot 與 dchg 才畫得出來;半截的資料列若放進來,下面的 v[2] 會是
  // undefined,fmt0 直接丟例外,整張走勢卡就停在上一次的畫面且沒有任何提示。
  const H = (window.__HIST__ || []).filter(d =>
    d && Array.isArray(d.v) && d.v.length >= 3 && Number.isFinite(+d.v[0]));
  const card = $('histcard'), host = $('histchart');
  if (!host) return;
  if (H.length < 2) {                      // 資料點不足先不畫,避免一個點的假趨勢
    if (card) card.style.display = 'none';
    return;
  }
  if (card) card.style.display = '';
  const W = 660, HGT = 180, PADL = 56, PADR = 74, PADT = 12, PADB = 22;
  const BV = H.map(d => (d.v.length > 6 && d.v[6] > 0) ? d.v[6] : null);
  // 基準線只有相鄰兩點都在時才連得起來。若只是零星幾點,畫不出線卻還顯示圖例、
  // 落差與端點數字,看起來就像圖表壞了 —— 這種情況整組都不顯示。
  const hasB = BV.some((v, i) => v != null && i > 0 && BV[i - 1] != null);
  const ys = H.map(d => d.v[0]).concat(hasB ? BV.filter(v => v != null) : []);
  const lo = Math.min(...ys), hi = Math.max(...ys), span = (hi - lo) || 1;
  const X = i => PADL + i * (W - PADL - PADR) / (H.length - 1);
  const Y = v => PADT + (1 - (v - lo) / span) * (HGT - PADT - PADB);
  const path = H.map((d, i) => `${i ? 'L' : 'M'}${X(i).toFixed(1)},${Y(d.v[0]).toFixed(1)}`).join('');
  const area = `${path}L${X(H.length-1).toFixed(1)},${HGT-PADB}L${PADL},${HGT-PADB}Z`;
  const first = H[0], last = H[H.length - 1];
  const chg = last.v[0] - first.v[0];

  // 基準線:遇到沒有基準欄的資料點就斷開重畫,不用直線把缺口連起來
  let bpath = '', open = false;
  BV.forEach((v, i) => {
    if (v == null) { open = false; return; }
    bpath += `${open ? 'L' : 'M'}${X(i).toFixed(1)},${Y(v).toFixed(1)}`;
    open = true;
  });
  const bLast = BV[BV.length - 1];
  const gap = (hasB && bLast != null) ? last.v[0] - bLast : null;

  const grid = [0, 0.5, 1].map(f => {
    const v = lo + span * f, y = Y(v);
    return `<line x1="${PADL}" y1="${y.toFixed(1)}" x2="${W-PADR}" y2="${y.toFixed(1)}" stroke="var(--grid)" stroke-width="1"/>` +
           `<text x="${PADL-8}" y="${y.toFixed(1)}" text-anchor="end" dominant-baseline="central" class="lab" style="font-size:11px">${fmt0(v)}</text>`;
  }).join('');
  const dots = H.map((d, i) => {
    const b = hasB ? BV[i] : null;
    const tipTxt = `${esc(d.t)}:總資產 ${fmt0(d.v[0])} · 今日 ${sign0(d.v[2])}` +
                (b != null ? ` · 基準 ${fmt0(b)} · 差 ${sign0(d.v[0] - b)}` : '');
    return `<g class="bar" data-tip="${tipTxt}">` +
      `<rect x="${(X(i)-6).toFixed(1)}" y="${PADT}" width="12" height="${HGT-PADT-PADB}" fill="transparent"/>` +
      (b != null ? `<circle cx="${X(i).toFixed(1)}" cy="${Y(b).toFixed(1)}" r="2" fill="var(--mut)"/>` : '') +
      `<circle cx="${X(i).toFixed(1)}" cy="${Y(d.v[0]).toFixed(1)}" r="${i===H.length-1?4:2.5}" fill="var(--s1)"/></g>`;
  }).join('');
  // 圖例:每一項各自 nowrap,否則窄螢幕會把「指數基準」拆成一字一行
  const line = (c, dash) =>
    `<svg width="20" height="8" viewBox="0 0 20 8" style="width:20px;height:8px;flex:none;display:block"><line x1="0" y1="4" x2="20" y2="4" stroke="${c}" stroke-width="2"${dash ? ' stroke-dasharray="4 3"' : ''}/></svg>`;
  const key = (c, dash, t) =>
    `<span style="display:inline-flex;gap:6px;align-items:center;white-space:nowrap">${line(c, dash)}${t}</span>`;
  const legend = hasB
    ? `<div class="mut" style="font-size:12.5px;display:flex;gap:16px;align-items:center;flex-wrap:wrap">${key('var(--s1)', 0, '總資產')}${key('var(--mut)', 1, '指數基準')}</div>`
    : '';
  // 配置對照:用最後一筆的權重,說明基準線是拿哪些指數、用什麼比重組出來的
  const wParts = (hasB && last.v.length > 12)
    ? BENCHNAME.map(([r, ix], i) => ((last.v[8 + i] || 0) >= 0.005
        ? `${r}→${ix} ${Math.round(last.v[8 + i] * 100)}%` : null)).filter(Boolean)
    : [];
  const wRow = wParts.length
    ? `<div class="note" style="margin-top:6px">基準組成 ${wParts.join(' · ')}</div>` : '';

  host.innerHTML =
    `<div style="display:flex;gap:18px;align-items:baseline;margin-bottom:8px;flex-wrap:wrap">
       <span style="font-size:22px;font-weight:650">${fmt0(last.v[0])}</span>
       <span class="${cls(chg)}">${sign0(chg)} 自 ${esc(first.t)}</span>
       ${gap != null ? `<span class="${cls(gap)}">${gap >= 0 ? '超前' : '落後'}指數基準 ${fmt0(Math.abs(gap))}</span>` : ''}
       <span class="mut" style="font-size:12.5px">${H.length} 個資料點 · 最新 ${esc(last.t)}</span></div>
     <svg viewBox="0 0 ${W} ${HGT}" role="img" aria-label="總資產走勢與指數基準">
       ${grid}
       <path d="${area}" fill="var(--s1)" opacity="0.10"/>
       ${hasB ? `<path d="${bpath}" fill="none" stroke="var(--mut)" stroke-width="1.6" stroke-dasharray="4 3" stroke-linejoin="round"/>` : ''}
       <path d="${path}" fill="none" stroke="var(--s1)" stroke-width="2" stroke-linejoin="round"/>
       ${dots}
       <text x="${(X(H.length-1)+8).toFixed(1)}" y="${Y(last.v[0]).toFixed(1)}" dominant-baseline="central" class="val" fill="var(--s1)">${fmt0(last.v[0])}</text>
       ${hasB && bLast != null ? `<text x="${(X(H.length-1)+8).toFixed(1)}" y="${Y(bLast).toFixed(1)}" dominant-baseline="central" class="val" fill="var(--mut)" style="font-size:11.5px">${fmt0(bLast)}</text>` : ''}
     </svg>
     <div style="margin-top:6px">${legend}</div>
     ${wRow}
     <div class="note" style="margin-top:6px">每個交易日(台北時間週一~週五)18:00 結算一筆 —— 台/日/韓已收盤、美股未開盤;歷史檔以獨立金鑰加密,不隨網站發佈,並補齊長度使檔案大小不隨筆數變動。指數基準以昨日配置比重乘今日各區指數報酬(換算成 USD)逐日串接;大額匯入匯出會同額調整基準,但區域之間的調倉不會。</div>`;
  attachTips();
}

// ══ 編輯器:展開單一部位的可編輯欄位 ══
const COLS = 11;
function allGroupOpts(selReg, selGrp) {
  return P.regions.flatMap(r => r.groups.map(g => {
    const v = `${r.key}||${g.name}`;
    const on = (r.key === selReg && g.name === selGrp) ? ' selected' : '';
    return `<option value="${esc(v)}"${on}>${esc(r.name)} › ${esc(g.name)}</option>`;
  })).join('');
}
function editorRow(p, key, reg, g) {
  const curOpts = ['TWD','JPY','KRW','HKD','CNY','USD','EUR','GBP']
    .map(c => `<option${(p.cur===c)?' selected':''}>${c}</option>`).join('');
  const u = unitsOf(p);
  const isMkt = p.kind === 'live' || !!p.ticker;
  const costLabel = isMkt ? `每股成本(${esc(p.cur || (p.q && p.q.cur) || '')})` : '總成本 USD K';
  const costField = isMkt
    ? `<input class="eF" data-k="cost" data-key="${esc(key)}" value="${esc(p.cost ?? '')}" placeholder="每股">`
    : `<input class="eF" data-k="cost_k" data-key="${esc(key)}" value="${esc(p.cost_k ?? '')}" placeholder="USD K">`;
  return `<tr class="editrow"><td colspan="${COLS}">
    <div class="efgrid">
      <label>名稱<input class="eF" data-k="name" data-key="${esc(key)}" value="${esc(p.name||'')}"></label>
      <label>代號<input class="eF" data-k="ticker" data-key="${esc(key)}" value="${esc(p.ticker||'')}" placeholder="代號:交易所"></label>
      <label>幣別<select class="eF" data-k="cur" data-key="${esc(key)}">${curOpts}</select></label>
      <label>股數<input class="eF" data-k="units_manual" data-key="${esc(key)}" value="${esc(p.units_manual ?? (u!=null?Math.round(u*100)/100:''))}" placeholder="留空=依報表推算"></label>
      <label>${costLabel}${costField}</label>
      <label>曝險幣別<select class="eF" data-k="exp_cur" data-key="${esc(key)}">
        <option value=""${!p.exp_cur ? ' selected' : ''}>(同計價幣別)</option>
        ${['TWD','JPY','KRW','HKD','CNY','USD','EUR','GBP'].map(c=>`<option${p.exp_cur===c?' selected':''}>${c}</option>`).join('')}
      </select></label>
      <label>所屬 basket<select class="eF" data-k="__move" data-key="${esc(key)}">${allGroupOpts(reg.key, g.name)}</select></label>
      <label>備註<input class="eF" data-k="note" data-key="${esc(key)}" value="${esc(p.note||'')}"></label>
      <div class="efbtns">
        <button class="del delPos" data-key="${esc(key)}" title="刪除此部位">✕ 刪除</button>
        <button class="del closeEd" data-key="${esc(key)}">收合</button>
      </div>
    </div>
    <div class="note">股數留空則沿用「報表市值 ÷ 建置價」推算的隱含股數;填入後即以你的股數計算市值與當日損益。
    改代號會讓該部位下次更新才抓到新報價。</div>
  </td></tr>`;
}

function findPos(key) {
  const [rk, gn, i] = key.split('||');
  const r = P.regions.find(x => x.key === rk);
  const g = r && r.groups.find(x => x.name === gn);
  return {r, g, i: +i, p: g && g.positions[+i]};
}

function wireEditing() {
  const dc = $('deriveCost');
  if (dc) dc.addEventListener('click', () => {
    // 有對帳單累積已實現(stmt_real_k)的部位一律跳過。填了 cost_k 會讓 ytdOf
    // 從「報表 pl」切換成對帳單公式,整體 YTD 會被無聲改寫 —— 實測按一下會讓
    // 總 YTD 掉一大截(某檔海外基金由正轉負)。成本要嘛來自對帳單,
    // 要嘛自己填,不該由一顆便利按鈕連帶動到損益口徑。
    let n = 0, skip = 0;
    P.regions.forEach(r => r.groups.forEach(g => g.positions.forEach(p => {
      if (p.derived || costK(p) != null || p.be == null) return;
      if (p.stmt_real_k != null) { skip++; return; }
      const mv = effMv(p);
      const c = mv / (1 + p.be / 100);
      if (isFinite(c) && c > 0) { p.cost_k = Math.round(c * 10) / 10; p.cost_src = 'be'; n++; }
    })));
    alert((n ? `已由 Breakeven 反推 ${n} 個部位的成本(標示為推算,可逐筆覆蓋)。` : '沒有可反推的部位。')
      + (skip ? `\n另有 ${skip} 個部位有對帳單已實現資料,跳過不動 —— 補成本會改變它們的 YTD 口徑。` : ''));
    commit();
  });
  const sf = $('stmtFile');
  if (sf) sf.addEventListener('change', ev => {
    const f = ev.target.files[0]; ev.target.value = '';
    if (f) importStmt(f);
  });
  const pt = $('plToggle');
  if (pt) pt.addEventListener('click', () => {
    HIDEPL = !HIDEPL;
    try { localStorage.setItem(PL_KEY, HIDEPL ? '1' : '0'); } catch (e) {}
    applyHidePL();
  });
  const cl = $('clearLocal');
  if (cl) cl.addEventListener('click', () => { if (confirm('清除本機所有手動修改?將回到自動更新的版本。')) clearLocal(); });
  const t = $('editToggle');
  if (t) t.addEventListener('click', () => { EDIT = !EDIT; OPEN = null; render(); });
  document.querySelectorAll('.editBtn').forEach(b => b.addEventListener('click', () => {
    OPEN = (OPEN === b.dataset.key) ? null : b.dataset.key; render();
  }));
  document.querySelectorAll('.closeEd').forEach(b => b.addEventListener('click', () => { OPEN = null; render(); }));

  document.querySelectorAll('.eF').forEach(el => el.addEventListener('change', () => {
    const {r, g, i, p} = findPos(el.dataset.key);
    if (!p) return;
    const k = el.dataset.k, v = el.value.trim();
    if (k === '__move') {                       // 搬移到其他 basket
      const [rk2, gn2] = v.split('||');
      const r2 = P.regions.find(x => x.key === rk2);
      const g2 = r2 && r2.groups.find(x => x.name === gn2);
      if (g2 && g2 !== g) { g.positions.splice(i, 1); g2.positions.push(p); OPEN = null; }
    } else if (['units_manual','cost','cost_k'].includes(k)) {
      p[k] = v === '' ? null : (isNaN(+v.replace(/,/g,'')) ? null : +v.replace(/,/g,''));
      if (p[k] == null) delete p[k];
      if (k === 'cost' || k === 'cost_k') delete p.cost_src;   // 手動填過就不再標「推算」
    } else {
      if (v === '') delete p[k]; else p[k] = v;
    }
    commit();
  }));

  document.querySelectorAll('.delPos').forEach(b => b.addEventListener('click', () => {
    const {g, i, p} = findPos(b.dataset.key);
    if (!g) return;
    const nm = p.name || '(未命名)';
    if (!confirm(`確定刪除「${nm}」?`)) return;
    // 賣掉之後直接刪掉,會把它今年的損益一起刪掉 —— 年度損益會憑空少一塊,
    // 而且不會出現在「已出清部位追蹤」裡。以前只有匯入對帳單那條路會記錄出清,
    // 兩次對帳單之間賣掉的東西就沒地方記。這裡補上同一套快照。
    const v = ytdOf(p);
    if (!isDup(p) && v != null && Math.abs(v) >= 0.5 &&
        confirm(`要把「${nm}」記為【已出清】嗎?\n\n` +
                `確定 = 以目前價格凍結今年損益 ${sign0(v)} USD K,計入年度損益,` +
                `並列入「已出清部位追蹤」。\n` +
                `取消 = 只從清單移除(例如重複建立或建錯),今年損益不計入。`)) {
      P.closed_ytd = P.closed_ytd || [];
      if (!P.closed_ytd.some(x => x.name === p.name && x.on === todayTaipei()))
        P.closed_ytd.push({name: p.name, usd_k: +v.toFixed(1), on: todayTaipei(),
                           ticker: p.ticker || null,
                           exit: (p.q && p.q.price) || null,
                           cur: (p.q && p.q.cur) || p.cur || null,
                           // 出清當下是最後一次知道股數的時點,不記就再也算不出來
                           units: unitsOf(p) ?? null});
    }
    // 跨區鏡像列要一起清掉,否則半導體那邊會留下一列對不到本尊的孤兒。
    // 只清同代號且標了 dup 的鏡像,不動獨立部位。
    const tk = p.ticker;
    g.positions.splice(i, 1);
    if (tk) P.regions.forEach(r => r.groups.forEach(gg => {
      gg.positions = gg.positions.filter(x => !(x !== p && x.ticker === tk && x.dup));
    }));
    OPEN = null; commit();
  }));
  document.querySelectorAll('.addPos').forEach(b => b.addEventListener('click', () => {
    const r = P.regions.find(x => x.key === b.dataset.reg);
    const g = r && r.groups.find(x => x.name === b.dataset.grp);
    if (!g) return;
    g.positions.push({name: '新部位', kind: 'live', cur: 'USD'});
    OPEN = `${r.key}||${g.name}||${g.positions.length - 1}`;
    commit();
  }));
  document.querySelectorAll('.grpName').forEach(el => el.addEventListener('change', () => {
    const r = P.regions.find(x => x.key === el.dataset.reg);
    const g = r && r.groups.find(x => x.name === el.dataset.grp);
    const nn = el.value.trim();
    if (!g || !nn || nn === g.name) return;
    if (r.groups.some(x => x.name === nn)) { alert('同一區域已有同名 basket'); commit(); return; }
    // 連動合計若指向舊名稱要一併改,避免斷鏈
    P.regions.forEach(rr => rr.groups.forEach(gg => gg.positions.forEach(pp => {
      if (pp.derived && pp.derived.region === r.key && pp.derived.subgrp === g.name) pp.derived.subgrp = nn;
      if (pp.subgrp === g.name) pp.subgrp = nn;
    })));
    g.name = nn; OPEN = null; commit();
  }));
  document.querySelectorAll('.addGrp').forEach(b => b.addEventListener('click', () => {
    const r = P.regions.find(x => x.key === b.dataset.reg);
    if (!r) return;
    let n = 1, nm = '新 basket';
    while (r.groups.some(g => g.name === nm)) nm = `新 basket ${++n}`;
    r.groups.push({name: nm, positions: []});
    commit();
  }));
  document.querySelectorAll('.delGrp').forEach(b => b.addEventListener('click', () => {
    const r = P.regions.find(x => x.key === b.dataset.reg);
    const idx = r ? r.groups.findIndex(g => g.name === b.dataset.grp) : -1;
    if (idx >= 0 && !r.groups[idx].positions.length) { r.groups.splice(idx, 1); commit(); }
  }));
}


// ══ 本機儲存:頁面每 15 分鐘會自動重載,若不保存會把手動輸入清掉 ══
// 只存「使用者改過的結構與欄位」,不存報價(q)——重載後價格用最新的,修改照樣保留。
const LS_KEY = 'portfolio_overlay_v1';
const posKey = p => `${p.ticker || ''}||${p.name || ''}`;

function stripQ(regions) {
  // q 與底線開頭的欄位(_den 等 render 時算出來的暫存值)都不存
  return JSON.parse(JSON.stringify(regions, (k, v) => ((k === 'q' || k[0] === '_') ? undefined : v)));
}
// 本機快取一律加密後再存。
// 原因:GitHub Pages 的 project site 共用 origin(https://<user>.github.io),
// 同帳號底下任何其他 Pages 專案的 JavaScript 都讀得到這個 localStorage。
// 推到 GitHub 的那份本來就是密文,本機這份不加密等於留了一扇後門。
const ENC_PREFIX = 'enc1:';
// bundle 端擁有的欄位:覆寫檔不得覆蓋(伺服器端 merge_overlay.py 有同一份清單)
const CFG_FIELDS = ['stmt_code', 'stmt_cur', 'ytd_base', 'ytd_base_mv', 'ytd_base_cost'];
const BACKFILL_FIELDS = ['stmt_real_k'];

// 這一頁的內容是從哪一版衍生出來的(祖先)。衝突判斷靠它,不是靠時間戳大小 ——
// 「時間戳大的贏、整份取代」會讓離線編輯與另一台已同步成功的修改互相無聲吃掉。
// __localAt 維持原意(本機最後一次保存的時刻,徽章在顯示),兩者不同。
function snapshot(extra) {
  return Object.assign({
    v: 1, at: new Date().toISOString(), base: P.__baseAt || null,
    regions: stripQ(P.regions),
    hedges: P.hedges, fx_track: P.fx_track, fx_manual: P.fx_manual,
    closed_ytd: P.closed_ytd, trims: P.trims, stmt_asof: P.stmt_asof, dropped: [...DROPPED],
  }, extra || {});
}
let _saveSeq = 0;            // 寫入序號:加密是 async,完成順序不保證
let SAVE_FAILED = false;     // 本機寫入失敗(配額不足 / 無痕 / 不支援)
// 回傳 true/false —— 以前這裡是 async 又吞掉例外且沒有回傳值,localStorage 滿了
// 或無痕模式時完全靜默,徽章還寫「僅存本機」,而本機其實也沒存到。
async function saveLocal() {
  const seq = ++_saveSeq;
  const payload = JSON.stringify(snapshot());
  try {
    const val = (SYNC && SYNC.key) ? ENC_PREFIX + await syncEnc(payload) : payload;
    // 加密完才發現有更新的一次寫入排在後面 → 放棄這次,別用舊快照蓋掉新的。
    // 連續編輯(對帳單匯入的迴圈、數字欄位逐字觸發)會同時跑好幾個加密。
    if (seq !== _saveSeq) return true;
    localStorage.setItem(LS_KEY, val);
    P.__localAt = JSON.parse(payload).at;
    if (SAVE_FAILED) { SAVE_FAILED = false; renderAlerts(); }
    return true;
  } catch (e) {
    if (!SAVE_FAILED) { SAVE_FAILED = true; renderAlerts(); }
    return false;
  }
}

// 讀本機快取。舊版存的是明文,這裡照樣讀得懂並在下一次寫入時自動轉成密文。
// 回傳:物件 = 讀到了;null = 沒有副本;false = 有副本但解不開 / 壞掉。
// 這兩者以前都回 null,於是換過金鑰之後舊的 enc1: 副本會被當成「使用者沒有資料」,
// 接著被 migrateClass 的 saveLocal 覆寫掉,原資料永久消失。
let LOCAL_UNREADABLE = false;
async function readLocal() {
  let raw = null;
  LOCAL_UNREADABLE = false;
  try { raw = localStorage.getItem(LS_KEY); } catch (e) { return null; }
  if (!raw) return null;
  try {
    if (raw.startsWith(ENC_PREFIX)) return JSON.parse(await syncDec(raw.slice(ENC_PREFIX.length)));
    // 有金鑰時不再接受明文副本:github.io 的 project pages 共用 origin,同帳號其他頁面
    // 寫得進這個 localStorage —— 一份帶著未來時間戳的明文副本會贏過遠端、再被推上去。
    // 明文格式只存在於 2026-08 之前的舊版,遷移期早已過去。
    if (SYNC && SYNC.key) { try { localStorage.removeItem(LS_KEY); } catch (e) {} return null; }
    const o = JSON.parse(raw);        // 沒有同步金鑰的部署:舊的明文格式照讀
    return o;
  } catch (e) { LOCAL_UNREADABLE = true; return false; }
}
// bundle 內的資料版本時間;比它舊的覆寫檔一律忽略(Claude 重新校準持倉後用)
const DATA_AT = P.data_at ? new Date(P.data_at) : null;
let STALE_HIT = false;                 // 這次載入有覆寫檔因為過期被丟掉
const overlayFresh = o => {
  if (!o || !o.at) return false;
  if (DATA_AT && new Date(o.at) <= DATA_AT) { STALE_HIT = true; return false; }
  return true;
};
// 資料重整期:data_at 還沒到,這段時間的編輯存了也會在下次載入被丟掉,必須明講
const inFreeze = () => !!DATA_AT && new Date() < DATA_AT;
const freezeTxt = () => DATA_AT
  ? DATA_AT.toLocaleString('zh-TW', {timeZone: 'Asia/Taipei', month: '2-digit', day: '2-digit',
                                     hour: '2-digit', minute: '2-digit', hour12: false})
  : '';

function applyOverlay(o) {
  try {
    if (!o || o.v !== 1 || !o.regions) return false;
    if (!overlayFresh(o)) return false;                 // 過期覆寫檔:改用 bundle 最新持倉
    // 先把「這次建置抓到的最新報價」建索引,套回本機保存的結構。
    // stmt_code / stmt_cur 同理:它們是 bundle 端的設定(對帳單代號對應表),
    // 網頁上沒有介面能改,而覆寫檔可能是加這些欄位之前存的 —— 若讓它整份蓋掉,
    // 對帳單匯入就會一筆都對不上。伺服器端 merge_overlay.py 有同樣的保護。
    const qmap = {}, cfg = {};
    P.regions.forEach(r => r.groups.forEach(g => g.positions.forEach(p => {
      const k = posKey(p);
      if (p.q) qmap[k] = p.q;
      if (p.stmt_code || p.ytd_base != null || p.stmt_real_k != null) {
        cfg[k] = {};
        CFG_FIELDS.concat(BACKFILL_FIELDS).forEach(f => { if (p[f] != null) cfg[k][f] = p[f]; });
      }
    })));
    mergeDropped(o.dropped);
    normalizeShape(o);
    o.regions.forEach(r => r.groups.forEach(g => g.positions.forEach(p => {
      const k = posKey(p);
      delete p.q;                                   // 報價只能來自這次建置,覆寫檔裡的一律不信
      if (qmap[k]) p.q = qmap[k];
      const c = cfg[k];
      if (c) Object.keys(c).forEach(f => {
        // 純設定欄位以 bundle 為準;匯入會寫入的欄位只在覆寫檔缺少時補回
        if (BACKFILL_FIELDS.includes(f) && p[f] != null) return;
        p[f] = c[f];
      });
    })));
    P.regions = o.regions;
    if (o.hedges) P.hedges = o.hedges;
    if (o.fx_track) P.fx_track = o.fx_track;
    if (o.fx_manual) P.fx_manual = o.fx_manual;
    if (o.closed_ytd) {
      // 不能整份取代 —— 這正是補登的出清紀錄進了建置端卻在畫面上看不到的原因:
      // 建置端把三筆補進 __P__,但瀏覽器的本機副本(與伺服器上的覆寫檔)裡是舊的那三列,
      // 載入時一蓋,補的那幾筆就消失了,而且下次推送又把舊的寫回去,永遠翻不了身。
      // 與 trims 同一套處理:以覆寫檔為主,建置端有而它沒有的補進來。
      const _ck = c => [c.name || '', c.on || ''].join('|');
      const _cov = cleanClosed(o.closed_ytd), _chave = new Set(_cov.map(_ck));
      P.closed_ytd = _cov.concat((P.closed_ytd || []).filter(c => !_chave.has(_ck(c))));
    }
    P.closed_ytd = cleanClosed(P.closed_ytd);       // 覆寫檔帶來的作廢清單也要套到建置端那份
    if (o.stmt_asof) P.stmt_asof = o.stmt_asof;
    if (o.trims) {
      // 不能整份取代。build 端每輪把 SEED_TRIMS 併進 __P__,但覆寫檔一旦存在
      // 且不含種子(伺服器端套用對帳單就會產生這種覆寫檔),取代之後種子就永遠
      // 看不到了 —— 而且下一次推送會把「沒有種子」的版本寫回去,形成閉環。
      const _k = t => [t.code || '', t.on || '', t.to || '', t.name || ''].join('|');
      const _ov = cleanTrims(o.trims), _have = new Set(_ov.map(_k));
      P.trims = _ov.concat((P.trims || []).filter(t => !_have.has(_k(t))));
    }
    P.__localAt = o.at;
    return true;
  } catch (e) { return false; }
}
// 套用某一版之後,這一頁的祖先就是那一版。本機副本是「從 base 那一版改出來的」,
// 所以套用本機副本時祖先要沿用它自己的 base,不能改成它的 at。
function setBase(o, isLocal) { P.__baseAt = isLocal ? (o && o.base) || null : (o && o.at) || null; }
// ══ 分岔備份與常駐警示 ══════════════════════════════════════════════════
// 兩台裝置各自從同一版改出去時,誰都不是誰的祖先。以前這種情況「時間戳大的贏、
// 整份取代」,輸的那一份連同本機副本一起被抹掉,而且徽章顯示「已同步」。
// 現在:畫面以遠端為準(所有裝置一致),輸的那一份原封不動存起來,常駐紅字提示。
const LS_STASH_KEY = 'portfolio_stash_v1';

async function stashPut(o, why) {
  try {
    const txt = JSON.stringify({at: o.at, base: o.base || null, savedAt: new Date().toISOString(),
                                why: why || '', body: o});
    const val = (SYNC && SYNC.key) ? ENC_PREFIX + await syncEnc(txt) : txt;
    localStorage.setItem(LS_STASH_KEY, val);
    return true;
  } catch (e) { return false; }        // 存不下就只能靠紅字告訴使用者
}
async function stashGet() {
  let raw = null;
  try { raw = localStorage.getItem(LS_STASH_KEY); } catch (e) { return null; }
  if (!raw) return null;
  try {
    return JSON.parse(raw.startsWith(ENC_PREFIX) ? await syncDec(raw.slice(ENC_PREFIX.length)) : raw);
  } catch (e) { return null; }
}
function stashDrop() { try { localStorage.removeItem(LS_STASH_KEY); } catch (e) {} }

let STASH = null;                      // 這次載入時發現的分岔備份(有就常駐提示)
let REMOTE_SNAP = null;                // 這次載入時遠端那一份覆寫檔(原始,未與 bundle 合併)

// 備份要跟「同樣是原始覆寫檔」的那一份比。拿它去比畫面上的 P 會永遠有差:
// P 是 bundle + 覆寫檔合併後的結果(closed_ytd / trims 會多出 bundle 才有的那幾筆),
// 而備份裡只有覆寫檔那一半。v130 的自動清理就是敗在這裡,舊備份怎麼樣都清不掉。
function stashPeer() { return REMOTE_SNAP || {regions: P.regions, hedges: P.hedges,
  fx_track: P.fx_track, fx_manual: P.fx_manual, closed_ytd: P.closed_ytd,
  trims: P.trims, dropped: [...DROPPED], stmt_asof: P.stmt_asof}; }

// 兩份覆寫檔的差異摘要。不做逐欄合併(容易出錯),只告訴使用者差在哪、由他決定。
function stashDiff(mine, theirs) {
  const flat = o => {
    const m = new Map();
    normForSig(o.regions).forEach(r => (r.groups || []).forEach(g => (g.positions || []).forEach(p => {
      m.set(posKey(p), p);
    })));
    return m;
  };
  const a = flat(mine), b = flat(theirs), rows = [];
  const FIELDS = ['name', 'mv', 'cost', 'units_manual', 'be', 'pl', 'ret', 'note', 'geo', 'theme',
                  'cur', 'kind', 'wgt', 'ytd_base', 'prev_override', 'subgrp', 'exp_cur'];
  new Set([...a.keys(), ...b.keys()]).forEach(k => {
    const x = a.get(k), y = b.get(k);
    if (!x) { rows.push([k, '(這份沒有)', '有']); return; }
    if (!y) { rows.push([k, '有', '(目前沒有)']); return; }
    FIELDS.forEach(f => {
      const xv = JSON.stringify(x[f] ?? null), yv = JSON.stringify(y[f] ?? null);
      if (xv !== yv) rows.push([`${x.name || k} · ${f}`, xv, yv]);
    });
  });
  // 清單比「正規化之後的內容」,不是只比筆數 —— 筆數一樣但某一筆被改過的情形會漏掉。
  [['已出清紀錄', 'c', 'closed_ytd'], ['減碼紀錄', 't', 'trims']].forEach(([label, kind, key]) => {
    const [x, y] = [normList(kind, mine[key]), normList(kind, theirs[key])];
    if (JSON.stringify(x) !== JSON.stringify(y))
      rows.push([label, `${(x || []).length} 筆`, `${(y || []).length} 筆(內容有差異)`]);
  });
  return rows;
}

function renderAlerts() {
  const el = $('alerts'); if (!el) return;
  // 差異表是空的就不要掛紅字。使用者點開來看到「其實一樣」只會學到「這條紅字可以不理」,
  // 而下一次真的有東西時他就不會點了 —— 誤報的代價是把整條防線報廢。
  if (STASH && STASH.body) {
    try { if (!stashDiff(STASH.body, stashPeer()).length) { STASH = null; stashDrop(); } }
    catch (e) {}
  }
  let h = '';
  if (SAVE_FAILED) h += `<div class="alert"><b>⚠ 本機副本存不進去</b>
    瀏覽器空間不足、無痕模式,或這個瀏覽器不允許儲存。<b style="display:inline">你現在的修改只活在這個分頁裡,關掉或重新整理就會消失。</b>
    ${(SYNC && SYNC.token) ? '雲端同步仍會嘗試推送 —— 看上方徽章是不是「已同步」。' : ''}
    <div class="mut">處理方式:換一般視窗開、或清掉這個網域的網站資料再重載。</div></div>`;
  if (LOCAL_UNREADABLE) h += `<div class="alert warn"><b>⚠ 本機副本解不開</b>
    這個瀏覽器裡有一份本機副本,但用目前的金鑰解不開(通常是換過 SYNC_KEY)。
    <b style="display:inline">已保留原檔、沒有覆寫它</b>,畫面上顯示的是雲端那一份。
    <div class="mut">確認雲端內容正確後,可按工具列的「↺ 清除修改」把這份無用的舊副本清掉。</div></div>`;
  if (STASH) {
    const when = String(STASH.at || '').slice(5, 16).replace('T', ' ');
    h += `<div class="alert"><b>⚠ 有一份未同步的修改被保留下來</b>
      這台裝置在 ${esc(when)} 的修改,和雲端那一份是從同一版各自改出去的(誰都不是誰的後續),
      所以無法自動決定誰對。<b style="display:inline">畫面上目前是雲端那一份,你的那一份原封不動存著,沒有丟。</b>
      <div><button id="stashView">檢視差異</button><button id="stashApply">整份套用我的那份</button>
           <button id="stashDl">下載備份</button><button id="stashDiscard">確認無誤,丟棄</button></div>
      <div id="stashPanel" hidden></div></div>`;
  }
  el.innerHTML = h;
  if (STASH) {
    $('stashView').onclick = () => {
      const pan = $('stashPanel');
      if (!pan.hidden) { pan.hidden = true; return; }
      const rows = stashDiff(STASH.body, stashPeer());
      pan.innerHTML = rows.length
        ? `<table><tr><td><b>欄位</b></td><td><b>我的那份</b></td><td><b>目前(雲端)</b></td></tr>`
          + rows.slice(0, 200).map(r => `<tr><td>${esc(String(r[0]))}</td><td>${esc(String(r[1]))}</td><td>${esc(String(r[2]))}</td></tr>`).join('')
          + `</table>${rows.length > 200 ? `<div class="mut">還有 ${rows.length - 200} 項未列出</div>` : ''}`
        : '<div class="mut">兩份的持倉欄位其實一樣(差別可能只在報價或時間戳)。可以安心丟棄。</div>';
      pan.hidden = false;
    };
    $('stashApply').onclick = async () => {
      if (!confirm('要用你這台裝置那一份整份取代目前內容嗎?雲端會被覆寫成這一份。')) return;
      const b = STASH.body;
      b.base = null;                       // 這是刻意的人為決定,不再宣稱任何祖先關係
      if (applyOverlay(b)) {
        P.__baseAt = null; STASH = null; stashDrop();
        await saveLocal(); schedulePush(); render(); renderAlerts();
      } else { alert('這份備份套用失敗(可能格式已過期),請改用「下載備份」保留內容。'); }
    };
    $('stashDl').onclick = () => {
      const blob = new Blob([JSON.stringify(STASH, null, 1)], {type: 'application/json'});
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = 'portfolio-unsynced-' + String(STASH.at || '').slice(0, 10) + '.json';
      a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 2000);
    };
    $('stashDiscard').onclick = () => {
      if (!confirm('確定丟棄這份未同步的修改?丟掉之後救不回來。')) return;
      STASH = null; stashDrop(); renderAlerts();
    };
  }
}

function clearLocal() {
  try { localStorage.removeItem(LS_KEY); } catch (e) {}
  stashDrop();
  LEAVING_ON_PURPOSE = true; PENDING_PUSH = false;
  location.reload();
}
// 任何修改都先存再重繪
function commit()   { saveLocal(); schedulePush(); render(); }
function commitFx() { saveLocal(); schedulePush(); renderFx(); }

// ══ 啟動 ══
// 用 setTimeout 延後執行:確保整個檔案(含後面宣告的 const)都完成初始化,
// 不論這段位在檔案何處都不會踩到暫時性死區 —— 先前已因此踩雷三次。

// ══ 跨裝置同步:加密的覆寫檔存在 GitHub repo,任何裝置解開頁面後都能讀寫 ══
// __SYNC__ 由 build script 從 repo secret 注入,只存在於已加密的 HTML 內,
// 公開的 workflow 原始碼看不到。覆寫檔本身再用 AES-GCM 加密,repo 上是亂碼。
const SYNC = window.__SYNC__ || null;
let syncSha = null, syncTimer = null, syncState = 'off';

const b64e = buf => btoa(String.fromCharCode(...new Uint8Array(buf)));
const b64d = s => Uint8Array.from(atob(s), c => c.charCodeAt(0));

async function syncKey() {
  const h = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(SYNC.key));
  return crypto.subtle.importKey('raw', h, 'AES-GCM', false, ['encrypt', 'decrypt']);
}
async function syncEnc(txt) {
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const ct = await crypto.subtle.encrypt({name: 'AES-GCM', iv}, await syncKey(),
                                         new TextEncoder().encode(txt));
  const out = new Uint8Array(12 + ct.byteLength);
  out.set(iv); out.set(new Uint8Array(ct), 12);
  return b64e(out);
}
async function syncDec(blob) {
  const raw = b64d(blob);
  const pt = await crypto.subtle.decrypt({name: 'AES-GCM', iv: raw.slice(0, 12)},
                                         await syncKey(), raw.slice(12));
  return new TextDecoder().decode(pt);
}

const ghUrl = () => `https://api.github.com/repos/${SYNC.repo}/contents/${SYNC.path}`;
const ghRef = () => '?ref=' + encodeURIComponent(SYNC.branch || 'main');
const ghHead = () => ({Authorization: 'Bearer ' + SYNC.token, Accept: 'application/vnd.github+json'});

function syncBadge() {
  return ({off:   '',
           loading:'<span class="badge">☁ 讀取中…</span>',
           saving: '<span class="badge">☁ 儲存中…</span>',
           ok:     '<span class="badge lv">☁ 已同步(所有裝置)</span>',
           err:    (SAVE_FAILED
                     ? '<span class="badge warn">☁ 同步失敗,本機也沒存到</span>'
                     : '<span class="badge warn">☁ 同步失敗,已存本機(會自動重試)</span>')
          })[syncState] || '';
}
function setSync(state) {
  syncState = state;
  const el = $('syncStatus');
  if (el) el.innerHTML = syncBadge();
}

async function pullRemote() {
  if (!SYNC || !SYNC.token) return null;
  setSync('loading');
  try {
    const r = await fetch(ghUrl() + ghRef(), {headers: ghHead()});
    if (r.status === 404) { syncSha = null; setSync('ok'); return null; }   // 還沒有覆寫檔
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const j = await r.json();
    syncSha = j.sha;
    const o = JSON.parse(await syncDec(atob(j.content.replace(/\n/g, ''))));
    setSync('ok');
    return o;
  } catch (e) { setSync('err'); return false; }   // false = 拉取失敗(與 null「沒有覆寫檔」不同)
}

// 上次成功推送的內容簽章:存 SHA-256 雜湊(不可逆,放 localStorage 無資料外洩疑慮),
// 且必須跨重載保存 —— 頁面每 15 分鐘重載,記憶體版的簽章會歸零,去重就失效。
const PUSHSIG_KEY = 'push_sig_v1';
// 比對兩份覆寫檔之前要先正規化,否則衝突偵測會對著「沒有人改過的東西」大叫 ——
// 而誤報比不報更糟:一天跳幾次紅字,三天內使用者就學會忽略它,那條防線等於沒有。
// 兩類雜訊必須排除(2026-09-09 線上實測踩到,兩類同時出現):
//  (a) 鏡像列的 mv / pl / ret / be 是 resolveDerived() 每次重繪即時算出來寫回部位的,
//      會跟著行情變 —— 同一台裝置早上存的和下午存的必然不同;
//  (b) geo / theme 是 migrateClass() 一次性搬進部位的,還沒搬過的舊副本是 null,
//      搬過的是 "US" / "EU" …,兩邊一比就變成一整排假差異。
const DERIVED_CALC = ['mv', 'pl', 'ret', 'be'];
function normForSig(regions) {
  const box = {regions: stripQ(regions || [])};  // 順便深拷貝,不動到原物件
  try { normalizeShape(box); } catch (e) {}      // 型別正規化:'1234' 與 1234 不該算不同
  const rs = box.regions;
  rs.forEach(r => (r.groups || []).forEach(g => (g.positions || []).forEach(p => {
    if (!p || typeof p !== 'object') return;
    if (p.derived) DERIVED_CALC.forEach(f => delete p[f]);
    // 只補空的,使用者真的改過 geo / theme 仍然比得出來
    if (!p.geo) {
      const g2 = GEO_BY_NAME[p.name] || (p.ticker && GEO_BY_TICKER[p.ticker]);
      if (g2) p.geo = g2;
    }
    if (!p.theme && p.ticker && THEME_BY_TICKER[p.ticker]) p.theme = THEME_BY_TICKER[p.ticker];
  })));
  // 部位也一樣:`geo: null` 與沒有 geo 是同一件事,鍵的順序也不該影響
  rs.forEach(r => (r.groups || []).forEach(g => {
    g.positions = (g.positions || []).map(p => (p && typeof p === 'object') ? _prune(p) : p);
  }));
  return rs;
}
// 清單也要正規化再比。`cleanClosed` / `cleanTrims` 會重排鍵的順序、丟掉不認識的欄位,
// 所以「原始覆寫檔」與「畫面上那份(已經清洗+與 bundle 合併過)」即使內容一樣,
// JSON 字串也不同。線上實測:trims 兩邊都是 24 筆、逐筆內容相同,簽章卻對不起來 ——
// 於是分岔備份的自動清理永遠不會觸發,紅字清不掉。順序也一併固定,合併順序才不影響。
const _sortBy = (arr, f) => (Array.isArray(arr) ? arr.slice() : []).sort((a, b) => f(a) < f(b) ? -1 : f(a) > f(b) ? 1 : 0);
// 「有這個鍵但值是 null」與「根本沒有這個鍵」在語意上一樣,JSON 字串卻不同。
// 線上實測(2026-09-10)兩份減碼紀錄唯一的差別就是 `"on": null` vs 沒有 on ——
// 六筆自動記下的減碼沒有日期,覆寫檔存成 null、合併後那份把它省略掉,於是簽章永遠對不起來。
// 空字串不能一起清:`units_manual: ''` 代表「未設定」,unitsOf 靠它判斷。
const _prune = o => {
  const r = {};
  Object.keys(o).sort().forEach(k => { if (o[k] !== null && o[k] !== undefined) r[k] = o[k]; });
  return r;
};
function normList(kind, arr) {
  if (arr == null) return null;
  return kind === 'c'
    ? _sortBy(cleanClosed(arr).map(_prune), x => `${x.name}|${x.on || ''}|${x.usd_k}`)
    : _sortBy(cleanTrims(arr).map(_prune), x => `${x.name}|${x.code || ''}|${x.on || ''}|${x.to || ''}`);
}
async function contentSig(obj) {
  const raw = JSON.stringify({r: normForSig(obj.regions), h: obj.hedges, t: obj.fx_track, m: obj.fx_manual,
    c: normList('c', obj.closed_ytd), tr: normList('t', obj.trims),
    d: (Array.isArray(obj.dropped) ? obj.dropped.slice().sort() : obj.dropped), s: obj.stmt_asof});
  try {
    const d = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(raw));
    return [...new Uint8Array(d)].map(b => b.toString(16).padStart(2, '0')).join('');
  } catch (e) { return null; }                 // 非安全環境拿不到 subtle → 不去重,照常推
}
async function pushRemote() {
  if (!SYNC || !SYNC.token) return;
  setSync('saving');
  try {
    const obj = snapshot();
    // 內容沒變就不推:每次推送都是一筆公開 commit,不去重的話
    // repo 一天多出四十幾筆內容完全相同的 sync 紀錄。
    const sig = await contentSig(obj);
    let prev = null; try { prev = localStorage.getItem(PUSHSIG_KEY); } catch (e) {}
    if (sig && prev && sig === prev) { setSync('ok'); return; }
    // 密文長度會洩漏持倉規模,且會隨每次編輯變動 —— 補到 16KB 級距再加密。
    let payload = JSON.stringify(obj);
    const B = 16384, n = new TextEncoder().encode(payload).length;
    if (n % B) {
      obj._pad = '';
      const base = new TextEncoder().encode(JSON.stringify(obj)).length;
      obj._pad = '0'.repeat((B - base % B) % B);
      payload = JSON.stringify(obj);
    }
    // commit 訊息不帶時間戳:公開 repo 的 commit 列表否則就是一份完整的編輯時間軸。
    const body = {message: 'sync',
                  content: btoa(await syncEnc(payload)), branch: SYNC.branch || 'main'};
    if (syncSha) body.sha = syncSha;
    let r = await fetch(ghUrl(), {method: 'PUT', headers: ghHead(), body: JSON.stringify(body)});
    if (r.status === 409 || r.status === 422) {      // 別台裝置剛寫過
      // 以前這裡直接拿最新 sha 把自己這份再推一次 —— 等於無聲蓋掉另一台裝置較新的修改。
      // 現在先看對方那份:若比本機新,套用對方的、放棄這次推送並明講;否則才重推。
      const g = await fetch(ghUrl() + ghRef(), {headers: ghHead()});
      if (g.ok) {
        const gj = await g.json();
        syncSha = gj.sha;
        let theirs = null;
        try { theirs = JSON.parse(await syncDec(atob(gj.content.replace(/\n/g, '')))); } catch (e) {}
        // 用祖先關係判,不是比時間戳:對方那份若是從我們手上這一版接下去改的
        // (theirs.base === obj.base 之外還要看誰是誰的後續),時間戳誰大都不代表誰對。
        if (theirs && overlayFresh(theirs)) {
          const tsig = await contentSig(theirs);
          if (sig && tsig && sig === tsig) {       // 對方那份就是我們要推的內容(自己的重試)
            syncSha = gj.sha; P.__baseAt = theirs.at;
            try { localStorage.setItem(PUSHSIG_KEY, sig); } catch (e) {}
            pushTries = 0; PENDING_PUSH = false; setSync('ok'); return;
          }
          if (obj.base && theirs.at && obj.base === theirs.at) {
            // 對方那一版正是我們的祖先(sha 變了但內容版本沒往前)→ 帶新 sha 重推即可
          } else {
            // 我們手上有未推送的修改,對方也往前走了 → 真的分岔,兩份都不能丟。
            await stashPut(obj, 'push-409');
            STASH = {at: obj.at, base: obj.base, savedAt: new Date().toISOString(), body: obj};
            applyOverlay(theirs); setBase(theirs); await saveLocal();
            if (tsig) { try { localStorage.setItem(PUSHSIG_KEY, tsig); } catch (e) {} }
            pushTries = 0; PENDING_PUSH = false;
            render(); renderAlerts(); setSync('ok'); return;
          }
        }
        body.sha = syncSha;
        r = await fetch(ghUrl(), {method: 'PUT', headers: ghHead(), body: JSON.stringify(body)});
      }
    }
    if (!r.ok) throw new Error('HTTP ' + r.status);
    syncSha = (await r.json()).content.sha;
    P.__baseAt = obj.at;                        // 推上去成功了:這一版成為新的祖先
    P.__localAt = obj.at;
    if (sig) { try { localStorage.setItem(PUSHSIG_KEY, sig); } catch (e) {} }
    // 本機副本改存成「剛剛推上去的那一份」(連 at / base 一起),兩邊完全對齊。
    // 不這樣做的話,本機副本會帶著一個遠端沒有的存檔時刻,下次載入就會被當成分岔。
    try {
      const mirror = Object.assign({}, obj); delete mirror._pad;
      const txt = JSON.stringify(mirror);
      localStorage.setItem(LS_KEY, (SYNC && SYNC.key) ? ENC_PREFIX + await syncEnc(txt) : txt);
      _saveSeq++;                               // 讓還在飛的舊 saveLocal 放棄寫入
    } catch (e) {}
    pushTries = 0; PENDING_PUSH = false;
    setSync('ok');
  } catch (e) {
    PENDING_PUSH = true;
    setSync('err');
    retryPush();                                // 以前推失敗就永遠停在那裡,不會再試
  }
}

// 兩邊都有覆寫檔時,誰贏。純函式(沒有副作用)—— 這段是整套同步最關鍵的判斷,
// 抽出來才驗得了。回傳 {take:'local'|'remote', stash:bool, rel}。
//
// 為什麼不能比時間戳大小:那會讓離線編輯被無聲銷毀(本機那份連同備份一起被遠端蓋掉,
// 而徽章顯示「已同步」),反方向則會讓一台離線裝置整份覆寫掉另一台已經同步成功的
// 修改,兩邊都沒有訊息,而且不會觸發 409(開機剛拉過 sha,那個 PUT 完全合法)。
// 關鍵的一問不是「誰比較新」,而是**本機這份有沒有還沒成功推上去的修改**。
// 這個答案我們本來就有:PUSHSIG_KEY 存著「上一次推送成功時的內容簽章」,而且跨重載保存。
//   * 本機內容 == 上次推成功的內容 → 沒有未推送的修改 → 遠端一定贏,靜靜換掉即可。
//     (每一次正常推送、每一次伺服器端套用對帳單之後,走的都是這一條 —— 不會誤報。)
//   * 有未推送的修改,而遠端還停在我們的祖先那一版 → 本機贏,推上去。
//   * 有未推送的修改,而遠端也往前走了 → 真的分岔了,誰都不該被丟掉。
//
// 之所以不能只靠 base/at 的祖先鏈:本機副本的 at 是「存檔時刻」,不是版本編號,
// 遠端不會有同一個 at,拿它比祖先會把每一次正常推送都誤判成分岔。
function bootDecide(local, remote, lsig, rsig, pushedSig) {
  if (lsig && rsig && lsig === rsig) return {take: 'remote', stash: false, rel: 'same'};
  if (local.at && remote.at && local.at === remote.at)
    return {take: 'remote', stash: false, rel: 'same'};
  const dirty = !(lsig && pushedSig && lsig === pushedSig);
  if (!dirty) return {take: 'remote', stash: false, rel: 'clean'};
  if (local.base && remote.at && local.base === remote.at)
    return {take: 'local', stash: false, rel: 'ahead'};
  return {take: 'remote', stash: true, rel: 'diverged'};
}

// ── 推送重試:指數退避 + 回到前景 / 恢復連線時再試 ───────────────────────
// 以前只有一個 3 秒 debounce,推失敗就停在那裡等使用者「剛好再編輯一次」;
// 手機在電梯裡編輯完鎖屏,唯一的復原是重新整理,而重新整理正好會走進覆蓋邏輯。
let pushTries = 0, retryTimer = null, PENDING_PUSH = false;
const RETRY_MS = [5000, 15000, 45000, 120000, 300000];
function retryPush() {
  if (!SYNC || !SYNC.token) return;
  clearTimeout(retryTimer);
  const wait = RETRY_MS[Math.min(pushTries++, RETRY_MS.length - 1)];
  retryTimer = setTimeout(() => { if (PENDING_PUSH) pushRemote(); }, wait);
}
function nudgePush() {
  if (!PENDING_PUSH) return;
  clearTimeout(retryTimer); pushTries = 0; pushRemote();
}
addEventListener('online', nudgePush);
addEventListener('visibilitychange', () => { if (!document.hidden) nudgePush(); });
let LEAVING_ON_PURPOSE = false;                  // clearLocal 之類自己觸發的重載不要跳提示
addEventListener('beforeunload', e => {
  if (!PENDING_PUSH || LEAVING_ON_PURPOSE) return;
  e.preventDefault(); e.returnValue = '';       // 還有沒同步出去的修改
});

// 連續編輯只在停手 3 秒後推一次,避免洗版式 commit
function schedulePush() {
  if (!SYNC || !SYNC.token) return;
  PENDING_PUSH = true; pushTries = 0;
  clearTimeout(syncTimer); clearTimeout(retryTimer);
  setSync('saving');
  syncTimer = setTimeout(pushRemote, 3000);
}

// ══ 匯入券商對帳單 ═════════════════════════════════════════════════════════
// 為什麼走這條路:股數與平均成本本來就該是「算出來的」,不是打出來的。
// 對帳單每天都拿得到,而且是權威值 —— 直接吃它,就不必手動維護這兩個欄位,
// 也就沒有「多打一個小數點把成本清空」那類問題。
// 每個部位用 stmt_code 對應(可多對一,同一檔在兩個帳戶);拆分列
// 依 wgt 分配股數;對帳單幣別與報價幣別不同的(對帳單記歐元、報價是美股 ADR)
// 用 stmt_cur 標記並換算。

// ══ 極簡 xlsx 讀取器 ════════════════════════════════════════════════════════
// 為什麼不用 SheetJS:那是 ~1MB,而整個 dashboard 才 131KB(而且長度是補齊過的,
// 塞進去會讓公開網址上的檔案大小跳三個級距)。券商報表就是一張單純的表格,
// 用瀏覽器內建的 DecompressionStream 解 zip + DOMParser 解 XML 就夠了。
// 只支援它實際會遇到的格式:單一工作表、sharedStrings、deflate。
async function xlsxRows(buf) {
  const dv = new DataView(buf), td = new TextDecoder();

  // 走中央目錄(不是逐個 local header,那樣遇到 data descriptor 會算錯長度)
  let eocd = -1;
  for (let i = buf.byteLength - 22; i >= 0 && i > buf.byteLength - 65558; i--) {
    if (dv.getUint32(i, true) === 0x06054b50) { eocd = i; break; }
  }
  if (eocd < 0) throw new Error('不是有效的 xlsx(找不到 zip 目錄)');
  const n = dv.getUint16(eocd + 10, true);
  let p = dv.getUint32(eocd + 16, true);

  const files = {};
  for (let i = 0; i < n; i++) {
    if (dv.getUint32(p, true) !== 0x02014b50) throw new Error('zip 目錄格式不符');
    const method = dv.getUint16(p + 10, true);
    const csize  = dv.getUint32(p + 20, true);
    const nlen   = dv.getUint16(p + 28, true);
    const elen   = dv.getUint16(p + 30, true);
    const clen   = dv.getUint16(p + 32, true);
    const lho    = dv.getUint32(p + 42, true);
    const name   = td.decode(new Uint8Array(buf, p + 46, nlen));
    // local header 的 extra 長度可能與中央目錄不同,一定要重讀
    const lnlen = dv.getUint16(lho + 26, true), lelen = dv.getUint16(lho + 28, true);
    files[name] = {method, off: lho + 30 + lnlen + lelen, csize};
    p += 46 + nlen + elen + clen;
  }

  async function read(name) {
    const f = files[name];
    if (!f) return '';
    const raw = new Uint8Array(buf, f.off, f.csize);
    if (f.method === 0) return td.decode(raw);           // 未壓縮
    if (f.method !== 8) throw new Error('不支援的壓縮方式 ' + f.method);
    const ds = new DecompressionStream('deflate-raw');
    const out = new Response(new Blob([raw]).stream().pipeThrough(ds));
    return await out.text();
  }

  const dp = new DOMParser();
  const shared = [];
  const ssXml = await read('xl/sharedStrings.xml');
  if (ssXml) {
    for (const si of dp.parseFromString(ssXml, 'application/xml').getElementsByTagName('si')) {
      // <si> 可能被拆成多個 <t>(混合格式),要全部串起來
      shared.push([...si.getElementsByTagName('t')].map(t => t.textContent).join(''));
    }
  }

  // 找第一張工作表(名稱不一定是 sheet1.xml)
  const sheetName = Object.keys(files).find(k => /^xl\/worksheets\/sheet\d+\.xml$/.test(k));
  const doc = dp.parseFromString(await read(sheetName), 'application/xml');

  const col = ref => {                       // "BC12" → 欄索引 54
    let c = 0;
    for (const ch of ref) {
      if (ch >= '0' && ch <= '9') break;
      c = c * 26 + (ch.charCodeAt(0) - 64);
    }
    return c - 1;
  };

  const rows = [];
  for (const r of doc.getElementsByTagName('row')) {
    const cells = [];
    for (const c of r.getElementsByTagName('c')) {
      const t = c.getAttribute('t');
      let v;
      if (t === 'inlineStr') {
        v = [...c.getElementsByTagName('t')].map(x => x.textContent).join('');
      } else {
        const vEl = c.getElementsByTagName('v')[0];
        v = vEl ? vEl.textContent : '';
        if (t === 's') v = shared[+v] ?? '';
        else if (v !== '' && t !== 'str') { const num = +v; if (!isNaN(num)) v = num; }
      }
      cells[col(c.getAttribute('r') || 'A1')] = v;
    }
    rows.push(cells);
  }
  return rows;
}

function csvRows(text) {
  const out = [];
  for (const line of text.replace(/^\uFEFF/, '').split(/\r?\n/)) {
    if (!line.trim()) continue;
    const cells = []; let cur = '', q = false;
    for (let i = 0; i < line.length; i++) {
      const ch = line[i];
      if (q) { if (ch === '"' && line[i + 1] === '"') { cur += '"'; i++; }
               else if (ch === '"') q = false; else cur += ch; }
      else if (ch === '"') q = true;
      else if (ch === ',') { cells.push(cur); cur = ''; }
      else cur += ch;
    }
    cells.push(cur);
    out.push(cells.map(c => { const n = +c.replace(/,/g, ''); return c !== '' && !isNaN(n) ? n : c; }));
  }
  return out;
}

const norm = x => String(x ?? '').replace(/\s/g, '');

// 從表格找出表頭列,再依欄名取值 —— 不寫死欄位索引,報表加欄也不會錯位
function parseStmt(rows) {
  let hi = -1, col = {};
  for (let i = 0; i < rows.length && hi < 0; i++) {
    const r = (rows[i] || []).map(norm);
    if (r.some(c => c.includes('股票代碼')) && r.some(c => c.includes('累積成本'))) {
      hi = i;
      r.forEach((c, j) => {
        if (c.includes('幣別')) col.cur = j;
        else if (c.includes('股票代碼')) col.code = j;
        else if (c.includes('股票名稱')) col.name = j;
        else if (c.includes('帳上庫存餘額')) col.sh = j;
        else if (c.includes('累積成本')) col.cost = j;
        else if (c.includes('當日市值')) col.mv = j;
        else if (c.includes('累積已實現')) col.real = j;   // 少了這行,匯入永遠把已實現寫成 0
        else if (c.includes('BreakevenReturn')) col.be = j;
      });
    }
  }
  if (hi < 0) throw new Error('找不到表頭(需要有「股票代碼」與「累積成本」兩欄)');
  const recs = [];
  for (let i = hi + 1; i < rows.length; i++) {
    const r = rows[i] || [];
    const code = String(r[col.code] ?? '').trim();
    const sh = +r[col.sh], cost = +r[col.cost];
    if (!code || isNaN(sh) || isNaN(cost)) continue;
    recs.push({code, name: String(r[col.name] ?? '').trim(),
               cur: String(r[col.cur] ?? '').trim().toUpperCase(),
               shares: sh, cost_k: cost, mv_k: +r[col.mv] || 0,
               real_k: col.real != null ? (+r[col.real] || 0) : null,
               be_rate: (col.be != null && r[col.be] !== '' && !isNaN(+r[col.be])) ? +r[col.be] : null});
  }
  if (!recs.length) throw new Error('表頭找到了,但沒有任何資料列');
  return recs;
}

// 把對帳單比對到目前持倉,產出「要改什麼」的清單(先給人看,不直接寫入)
// Breakeven(%):報表逐帳戶給比率,多帳戶合併時以累積成本加權
function beOf(hits) {
  const v = hits.filter(h => h.be_rate != null && h.cost_k);
  if (!v.length) return null;
  const c = v.reduce((s, h) => s + h.cost_k, 0);
  return c ? +(v.reduce((s, h) => s + h.be_rate * h.cost_k, 0) / c * 100).toFixed(1) : null;
}

function stmtDiff(recs) {
  const by = {};
  recs.forEach(r => { by[r.code] = r; });
  const used = new Set(), changes = [], skipped = [], needFx = [];

  P.regions.forEach(reg => reg.groups.forEach(g => g.positions.forEach((p, pi) => {
    const codes = p.stmt_code;
    if (!codes || !codes.length) return;
    const hits = codes.map(c => by[c]).filter(Boolean);
    hits.forEach(h => used.add(h.code));
    if (!hits.length) {          // 整筆從對帳單消失 → 視同出清
      changes.push({p, label: p.name, gone: true, oldU: unitsOf(p), oldC: p.cost});
      return;
    }
    const shares = hits.reduce((s, h) => s + h.shares, 0);
    const costLocal = hits.reduce((s, h) => s + h.cost_k, 0) * 1000;

    if (p.kind !== 'live') {
      // 靜態 NAV 部位(海外基金)沒有股數概念,對帳單給的是市值 —— 直接換算成 USD K。
      // 每一列各自用自己的幣別換(同一檔基金可能一列瑞郎、一列日圓)。
      const miss = hits.filter(h => !FX[h.cur]).map(h => h.cur);
      if (miss.length) { needFx.push(`${p.name}(缺 ${[...new Set(miss)].join('/')} 匯率)`); return; }
      const mvUsd = hits.reduce((s, h) => s + h.mv_k / FX[h.cur], 0);
      const beS = beOf(hits);
      const beChangedS = beS != null && (p.be == null || Math.abs(beS - p.be) > 0.05);
      if (p.mv != null && Math.abs(mvUsd - p.mv) < Math.max(0.5, Math.abs(p.mv) * 1e-6)
          && !beChangedS) return;
      changes.push({p, label: p.name, static: true, oldMv: p.mv, newMv: mvUsd,
                    beNew: beChangedS ? beS : null, beOld: p.be});
      return;
    }

    const stmtCur = p.stmt_cur || hits[0].cur;
    const qCur = (p.q && p.q.cur) || p.cur || stmtCur;
    const newU = shares * (p.wgt || 1);
    const oldU = unitsOf(p), oldC = p.cost;
    if (!shares) {
      // 對帳單股數為 0 → 已出清。這裡不能再加「oldU 不為 0」的條件:
      // 舊版匯入會把股數設成 0 但留著列,那些列的 oldU 已經是 0,
      // 加了條件就永遠標記不到、再匯入幾次也清不掉。
      changes.push({p, label: p.name, gone: true, oldU, newU: 0, oldC});
      return;
    }
    // 每股成本換算成「報價幣別」計價(costK 就是這樣解讀 p.cost 的)
    let perShare = costLocal / shares;
    if (stmtCur !== qCur && FX[stmtCur] && FX[qCur]) perShare = perShare / FX[stmtCur] * FX[qCur];
    // 容差用相對值:portfolio 內存的成本是四捨五入過的(小數 6 位),
    // 用絕對容差會把純粹的捨入誤差判成「有變動」,整份對帳單每次都跳一堆假差異。
    const near = (a, b) => a != null && b != null && Math.abs(a - b) <= Math.max(1e-6, Math.abs(b) * 1e-6);
    // 報表沒有已實現欄位時 realK 為 null,套用端會跳過、保留既有值
    const realK = hits.some(h => h.real_k == null) ? null
                : hits.reduce((s, h) => s + h.real_k, 0);
    const realChanged = realK != null && p.stmt_real_k != null &&
                        Math.abs(realK - p.stmt_real_k) > Math.max(1, Math.abs(p.stmt_real_k) * 1e-6);
    // Breakeven 對有成本的部位已改為即時計算(beLive),對帳單上的快照只是那一天的價格,
    // 不算「變更」、也不列在預覽 —— 否則每份對帳單都會因為價格不同列出一整排 Breakeven 變動
    // (2026-09-08 曾把一整份預覽的大半算成這種假差異)。套用時仍悄悄更新快照當備援。
    if (near(oldU, newU) && near(oldC, perShare) && !realChanged) return;
    // 累積已實現只增不減;明顯倒退代表帳戶計數器被重置或匯錯檔,要在預覽裡大聲說
    const realBack = realK != null && p.stmt_real_k != null &&
                     realK < p.stmt_real_k - Math.max(1, Math.abs(p.stmt_real_k) * 0.01);
    changes.push({p, label: p.name, oldU, newU, oldC, newC: perShare, cur: qCur,
                  realK, stmtCur, realChanged, realBack,
                  // 減碼紀錄的防呆要用到「這次對帳單上的每股市價」
                  mvK: hits.reduce((s, h) => s + (h.mv_k || 0), 0),
                  beSnap: beOf(hits), beNew: null, beOld: p.be});
  })));

  P.regions.forEach(reg => reg.groups.forEach(g => g.positions.forEach(p => {
    if (!p.stmt_code && !p.derived && !p.dup) skipped.push(p.name);
  })));
  const unmatched = recs.filter(r => !used.has(r.code));
  return {changes, unmatched, skipped, needFx};
}

function stmtDiffHtml(d) {
  const num = (v, dp) => v == null ? '—' : (+v).toLocaleString('en-US',
      {minimumFractionDigits: dp, maximumFractionDigits: dp});
  const seen = new Set();
  const shown = d.changes.filter(c => {          // 跨區重複列示的鏡像列只顯示一次(仍會一起套用)
      const k = (c.p.ticker || c.label) + (c.static ? 'S' : '');
      if (seen.has(k)) return false; seen.add(k); return true;
    });
  const rows = shown.map(c => c.static ? `<tr>
      <td>${esc(c.label)} <span class="badge">NAV</span></td>
      <td class="num" colspan="2">市值 ${num(c.oldMv, 0)} → ${num(c.newMv, 0)} USD K</td>
      <td class="num">—</td><td class="num">—</td></tr>` : `<tr>
      <td>${esc(c.label)}${c.gone ? ' <span class="badge warn">將移除</span>' : ''}</td>
      <td class="num">${num(c.oldU, 0)}</td><td class="num">→ ${num(c.newU, 0)}</td>
      <td class="num">${num(c.oldC, 2)}</td>
      <td class="num">${c.gone ? '—' : '→ ' + num(c.newC, 2)}${c.realChanged
        ? `<div class="sub ${c.realBack ? 'warnnote' : 'mut'}">已實現 ${num(c.p.stmt_real_k, 0)} → ${num(c.realK, 0)}${c.realBack ? ' ⚠倒退' : ''}</div>` : ''}${c.beNew != null
        ? `<div class="sub mut">Breakeven ${c.beOld != null ? num(c.beOld, 1) + '%' : '—'} → ${num(c.beNew, 1)}%</div>` : ''}</td></tr>`).join('');
  const nShown = shown.length;
  return `<div class="modalbg" id="stmtBg"><div class="modal">
    <h2>對帳單匯入預覽</h2>
    ${d.changes.length ? `<div style="overflow:auto;max-height:46vh"><table>
      <thead><tr><th>部位</th><th class="num">目前股數</th><th class="num">對帳單股數</th>
      <th class="num">目前每股成本</th><th class="num">對帳單每股成本</th></tr></thead>
      <tbody>${rows}</tbody></table></div>`
      : '<div class="note">沒有任何差異 —— 目前持倉與這份對帳單一致。</div>'}
    ${d.unmatched.length ? `<div class="note" style="margin-top:10px">
      <b>對帳單有、看板沒對應的 ${d.unmatched.length} 筆</b>(不會被匯入,需要先建立部位並指定代號):<br>
      ${d.unmatched.map(r => esc(r.code + ' ' + r.name)).join('、')}</div>` : ''}
    ${(() => { const g = d.changes.filter(c => c.gone).length, t = d.changes.length;
        return (g >= 3 && g >= t / 3) ? `<div class="note warnnote" style="margin-top:8px">
          ⚠ 這次要移除 <b>${g}</b> 個部位。若對帳單只涵蓋部分帳戶,請先取消再確認檔案是否完整。</div>` : ''; })()}
    ${(() => { const rb = d.changes.filter(c => c.realBack).map(c => c.label);
        return rb.length ? `<div class="note warnnote" style="margin-top:8px">
          ⚠ <b>${esc(rb.join('、'))}</b> 的累積已實現比現有紀錄小 —— 正常情況它只增不減。
          可能是匯錯檔案或帳戶計數器被重置;套用後 YTD 會失真,建議先取消並確認報表來源。</div>` : ''; })()}
    ${d.needFx.length ? `<div class="note warnnote" style="margin-top:6px">
      缺匯率、未套用:${esc(d.needFx.join('、'))}</div>` : ''}
    ${d.skipped.length ? `<div class="note" style="margin-top:6px">
      不在對帳單上、維持原樣的 ${d.skipped.length} 個部位:${esc(d.skipped.slice(0, 6).join('、'))}${d.skipped.length > 6 ? ' …' : ''}</div>` : ''}
    <div class="btnrow" style="margin-top:14px">
      <button id="stmtApply" class="primary"${nShown ? '' : ' disabled'}>套用 ${nShown} 項變更</button>
      <button id="stmtCancel">取消</button></div></div></div>`;
}

async function importStmt(file) {
  const msg = $('msg');
  try {
    msg.textContent = '解析中…';
    const rows = /\.csv$/i.test(file.name)
      ? csvRows(await file.text())
      : await xlsxRows(await file.arrayBuffer());
    const d = stmtDiff(parseStmt(rows));
    msg.textContent = '';
    document.body.insertAdjacentHTML('beforeend', stmtDiffHtml(d));
    const close = () => { const el = $('stmtBg'); if (el) el.remove(); };
    $('stmtCancel').addEventListener('click', close);
    $('stmtBg').addEventListener('click', e => { if (e.target.id === 'stmtBg') close(); });
    const ap = $('stmtApply');
    if (ap) ap.addEventListener('click', () => {
      const drop = new Set();
      // 出清的部位:以「最後看到的價格」凍結其年度損益快照,計入 closed_ytd。
      // 之後 KPI 與走勢圖的 YTD 會把這筆帶著走,口徑與券商年度報表一致
      // (近似:出清成交價與最後收盤價之間的價差無從得知,如實標示為快照值)。
      P.closed_ytd = P.closed_ytd || [];
      d.changes.forEach(c => {
        if (!c.gone || c.p.dup) return;
        const v = ytdOf(c.p);
        // 不再用「算得出且 >= 0.5」當門檻。算不出來就記 0:金額 0 對加總無害,
        // 但那一列會出現在追蹤表上 —— 靜靜跳過會讓部位與它的年度損益一起消失,
        // 而且事後完全看不出來(2026-09-02 兩檔韓股就是這樣)。
        if (!P.closed_ytd.some(x => x.name === c.p.name && x.on === todayTaipei()))
          P.closed_ytd.push({name: c.p.name, usd_k: v != null ? +v.toFixed(1) : 0, on: todayTaipei(),
                             ticker: c.p.ticker || null,
                             exit: (c.p.q && c.p.q.price) || null,
                             cur: (c.p.q && c.p.q.cur) || c.p.cur || null,
                             // 出清當下的股數:之後要拿它換算「沒賣的話今天的損益」,
                             // 這是唯一還知道股數的時點,不記就永遠算不出來了。
                             units: unitsOf(c.p) ?? null});
      });
      // 減碼的部位:股數一被覆蓋就再也回不去,所以在覆蓋「之前」留紀錄。
      // 出場價由「本次新增的已實現損益 ÷ 減碼股數 + 減碼前每股成本」反推。
      P.trims = P.trims || [];
      d.changes.forEach(c => {
        if (c.static || c.gone || c.p.dup || c.p.derived) return;
        const u0 = +c.oldU, u1 = +c.newU, sold = u0 - u1;
        if (!(u0 > 0) || !(sold > u0 * 0.01) || !(c.oldC > 0)) return;
        // 成本/股大幅跳動代表單位重估或轉換,不是減碼(曾有基金 +111%)
        if (Math.abs(c.newC / c.oldC - 1) > 0.25) return;
        // 拆分列的 stmt_real_k 存的是整檔數字,要按 wgt 分攤 ——
        // 不分攤的話五列各記一份完整的已實現,加總會是實際的五倍,反推的出場價也全錯。
        // 而且 oldC 已經換算成「報價幣別」,已實現還在「對帳單幣別」(對帳單記歐元、
        // 報價美元),兩個幣別直接相加會歪掉,要先換過去。
        const w = +c.p.wgt || 1;
        const drS = (c.realK != null && c.p.stmt_real_k != null)
                  ? (c.realK - c.p.stmt_real_k) * w : null;      // 對帳單幣別,已分攤
        const fS = FX[c.stmtCur], fQ = FX[c.cur];
        const dr = (drS != null && fS && fQ) ? drS / fS * fQ : null;   // 報價幣別
        const px = u1 > 0 && c.mvK ? c.mvK * w * 1000 / u1 : 0;  // 對帳單幣別每股市價
        const tooBig = drS != null && px > 0 && Math.abs(drS) > sold * px / 1000 * 1.05;
        const ex = dr != null ? +c.oldC + dr * 1000 / sold : null;
        const on = P.stmt_asof || null, to = todayTaipei();
        if (P.trims.some(x => x.name === c.p.name && x.on === on && x.to === to)) return;
        P.trims.push({name: c.p.name, ticker: c.p.ticker || null,
                      // 幣別一律記報價幣別:出場價與已實現都已換算過去,
                      // 這樣「出場價 vs 現價」與「已實現 ÷ 匯率」用的是同一套。
                      cur: c.cur || c.p.cur || null,
                      code: Array.isArray(c.p.stmt_code) ? c.p.stmt_code[0] : (c.p.stmt_code || null),
                      on, to, u0: +u0.toFixed(4), u1: +u1.toFixed(4),
                      exit: (ex != null && ex > 0 && !tooBig) ? +ex.toFixed(4) : null,
                      est: 1, real_k: (dr != null && !tooBig) ? +dr.toFixed(1) : null,
                      src: 'stmt'});
      });
      d.changes.forEach(c => {
        if (c.static) { c.p.mv = +c.newMv.toFixed(1); stampNav(c.p);
                        if (c.beNew != null) c.p.be = c.beNew; return; }
        if (c.gone) { drop.add(c.p); return; }
        c.p.units_manual = c.newU;
        c.p.cost = c.newC;
        c.p.cost_src = 'stmt';
        if (c.realK != null) c.p.stmt_real_k = c.realK;   // 報表沒有該欄時保留既有值
        if (c.beSnap != null) c.p.be = c.beSnap;           // 快照只當沒成本時的備援,不顯示
        c.p.stmt_cur = c.p.stmt_cur || c.stmtCur;
      });
      // 已出清的直接從表上拿掉。注意只用「對帳單股數歸零」當依據,不是「市值顯示 0」——
      // 報價抓失敗時市值也會是 0,若照畫面上的 0 去刪,一次抓取失敗就會誤刪部位。
      if (drop.size) P.regions.forEach(r => r.groups.forEach(g => {
        g.positions = g.positions.filter(p => !drop.has(p));
      }));
      // 這份對帳單的日期:下一次偵測到減碼時,期間起點就從這裡算
      P.stmt_asof = todayTaipei();
      close();
      commit();
      msg.textContent = '已套用,正在同步到所有裝置…';
      setTimeout(() => { msg.textContent = ''; }, 4000);
    });
  } catch (e) {
    msg.textContent = '匯入失敗:' + e.message;
  }
}

setTimeout(async () => {
  const remote = await pullRemote();          // false = 拉取失敗;null = 遠端還沒有覆寫檔
  const local = await readLocal();            // false = 有副本但解不開;null = 沒有副本
  const r2 = overlayFresh(remote) ? remote : null;
  const l2 = (local && overlayFresh(local)) ? local : null;

  STASH = await stashGet();                   // 之前留下來的分岔備份,重載後也要繼續提示
  REMOTE_SNAP = r2 || null;                   // 備份要跟原始覆寫檔比,不是跟合併後的 P 比

  // 遠端獲勝時的收尾:回寫本機、更新去重簽章(否則「改回上一版」會被當成沒改)
  async function takeRemote(o) {
    applyOverlay(o); setBase(o);
    await saveLocal();
    const sig = await contentSig(o);
    if (sig) { try { localStorage.setItem(PUSHSIG_KEY, sig); } catch (e) {} }
  }

  if (!r2 && !l2) {
    // 兩邊都沒有可用的覆寫檔 —— 用 bundle 原樣
  } else if (!l2) {
    if (r2) await takeRemote(r2);
  } else if (!r2) {
    applyOverlay(l2); setBase(l2, true);
    // 拉取失敗時不推:那會用本機這份舊副本蓋掉遠端(遠端可能有另一台剛存的)
    if (remote !== false && (remote !== null || local)) schedulePush();
  } else {
    let pushedSig = null;
    try { pushedSig = localStorage.getItem(PUSHSIG_KEY); } catch (e) {}
    const decision = bootDecide(l2, r2, await contentSig(l2), await contentSig(r2), pushedSig);
    if (decision.take === 'local') {
      applyOverlay(l2); setBase(l2, true); schedulePush();
    } else {
      if (decision.stash) {
        await stashPut(l2, decision.rel);
        STASH = {at: l2.at, base: l2.base || null, savedAt: new Date().toISOString(), body: l2};
      }
      await takeRemote(r2);
    }
  }
  // 舊的備份若其實與現在的內容一樣(v129 因為沒有正規化而誤存下來的那種),靜靜清掉。
  // 留著只會每次載入都跳一次紅字,而裡面根本沒有任何使用者的修改。
  if (STASH && STASH.body) {
    const [a, b] = [await contentSig(STASH.body), await contentSig(stashPeer())];
    if (a && b && a === b) { STASH = null; stashDrop(); }
  }
  // 一次性:把程式碼裡的分類表搬進部位欄位(見 migrateClass),之後程式碼就不必再帶代號。
  // 拉取失敗時不存本機副本:那會做出一份帶著新時間戳的副本,下次載入贏過遠端再被推上去。
  // 本機副本解不開時也不存:那會把使用者解不開但仍存在的資料覆寫掉。
  if (migrateClass() && remote !== false && !LOCAL_UNREADABLE) { await saveLocal(); schedulePush(); }
  render();
  renderAlerts();
}, 0);
