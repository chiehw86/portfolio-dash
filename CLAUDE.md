# portfolio-dash — 給 Claude 的操作說明

投資組合 dashboard。GitHub Actions 一天跑 22 輪:抓價 → 併入網頁端的修改 → 建置 →
StatiCrypt 加密 → 發佈到 `gh-pages`。**這個 repo 是公開的。**

## 檔案分工(不要搞混)

| 路徑 | 是什麼 |
|---|---|
| `src/` | **唯一可編輯的原始碼。改程式改這裡。** |
| `.github/workflows/build.yml` | 由 `src/` 打包產生(base64+gzip 內嵌)。**實際執行的是這一份** |
| `scripts/` | 建置在執行時把「這一輪真的跑了什麼」寫回來的鏡像。**只給人 diff 用,改它沒有作用** |
| `tools/` | 驗證與出版工具 |
| `tests/` | 情境測試 |
| `data/*.enc`、`overlay.enc` | 加密資料,由建置與網頁端讀寫。**不要手動改** |

**為什麼執行的那份要留在 workflow 檔裡:** 改 `.github/workflows/` 需要 token 的 `workflows` 權限,
改一般檔案只要 `contents` 權限,而解密後的頁面裡嵌著一顆**只有 contents 讀寫**的同步 token。
一旦建置改成去跑 `scripts/` 或 `src/`,「拿到觀看密碼」就等於「可以改一段會拿到全部 secret 的程式碼」。
這是刻意的取捨,**不要為了交付方便把它換掉**。

## 出版流程

```bash
# 1. 改 src/ 裡的檔,並把 src/build_dashboard_v3.py 的 BUILD_TAG 遞增
# 2. 跑完十關(任何一關紅就不要出版)
VIEW_PASSWORD=… SYNC_KEY=… bash tools/preship.sh vNNN
#    yml 本體也改過時要指定改好的那份:bash tools/preship.sh vNNN 改好的.yml
# 3. 把產生的 build-vNNN.yml 覆蓋到 .github/workflows/build.yml,開 PR
# 4. 合併後驗證
VIEW_PASSWORD=… python3 tools/live.py build
VIEW_PASSWORD=… python3 tools/live.py check tools/claims.json vNNN
VIEW_PASSWORD=… node tools/stash_live.js     # 動到同步 / 本機副本時必跑
```

十關是:語法 / 版本號 / 情境測試 / **拉線上真實狀態** / 用真實資料建置 /
**replica 數字與線上一致** / 前端測試 / **分岔備份不誤報** / 洩漏檢查 / 打包 + 500 KB。

## 驗證鐵則

1. **任何關於「畫面上的數字」的宣稱,必須對線上那份頁面求證。**
   本機只有 bundle,**沒有覆寫檔** —— `trims`、`closed_ytd`、所有手動修改都不在裡面,
   拿它算出來的結論會是錯的。`tools/replica.py` 會把線上真實狀態拉下來組成等價的建置輸入。
2. **「修好了」不能只跑本機測試就說。** 要嘛對線上驗,要嘛明講「本機通過,線上待驗」。
3. **測試要能證明自己抓得到 bug** —— 每條修正要有「拿舊版跑會 FAIL」的對照。
   種不進去的狀態(例如分岔備份)要**真的種進 localStorage 再重載**,不能只呼叫純函式。
4. **推論要標成推論。** 沒量過的成因寫「推測」,不要寫成「原因是」。

2026-09-09/10 連續四個版本都是因為違反第 1、2 條而修不對。

## 鐵則:公開面不得出現持倉資訊

任何腳本、註解、頁尾文字都**不得出現持倉名稱、代號、券商代碼、股數、金額、檔數**。
建置有 `leak_check.py` 擋(命中就中止、不發佈),每日排程另外掃一次。
交付前先自己掃過改動的檔案。

## 版本編號

頁尾的 `建置 vNNN · xxxxxx`,後六碼是 `v3.js + v3.css + build_dashboard_v3.py + fetch_action.py`
串接後的 md5 前六碼 —— **版本號忘了改也會變**。判斷「有沒有上線」一律看它,不要比檔案大小。
`BUILD_TAG` 要接續遞增(曾經發生過兩份不同內容都叫 v101)。

## 硬限制

- **`.github/workflows/build.yml` 超過 500 KB(512,000 bytes),GitHub 的 run 永遠不會啟動,
  而且不報錯、只停在 Queued。** 2026-09-08 因此凍結 24 小時。`tools/repack.py` 有 assert 擋著。
- `PORTFOLIO_BUNDLE` secret 的上限是 48 KB,目前約 24 KB 且在長大 —— 形狀跟 500 KB 那次一樣。

## 金鑰

`VIEW_PASSWORD` / `SYNC_KEY` / `HISTORY_KEY` **只從環境變數進來,絕不寫進任何檔案**。
值放在 claude.ai 專案文件裡(不在這個 repo)。

## 更詳細的文件(在 claude.ai 專案裡,不在 repo)

`publish-status.md`(每一版改了什麼)、`verify-rules.md`(驗證鐵則與沙盒限制)、
`release-process.md`(出版節奏)、`system-review-2026-09-09.md`(全系統複查)、
`security-review-2026-09-08.md`、`dashboard-access.md`(網址與金鑰)。
