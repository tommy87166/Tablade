<p align="center">
  <img src="assets/icon.svg" width="76" alt="Tablade logo" />
</p>

<h1 align="center">Tablade</h1>

<p align="center"><b><i>Chop messy files into clean tables</i></b></p>
<p align="center">一步一步把凌亂的 CSV / TXT / Excel 清洗成乾淨的 Parquet —— 全程本機執行，不上傳任何檔案。</p>

<p align="center">
  <img src="https://img.shields.io/badge/version-0.1.0-FD5108" alt="version" />
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-yellow.svg" alt="License: MIT" /></a>
  <img src="https://img.shields.io/badge/Python-3.13-3776AB?logo=python&logoColor=white" alt="Python" />
  <img src="https://img.shields.io/badge/Polars-2.1-CD792C?logo=polars&logoColor=white" alt="Polars" />
  <img src="https://img.shields.io/badge/pywebview-6.2-555555" alt="pywebview" />
  <img src="https://img.shields.io/badge/React-19-61DAFB?logo=react&logoColor=black" alt="React" />
  <img src="https://img.shields.io/badge/TypeScript-7-3178C6?logo=typescript&logoColor=white" alt="TypeScript" />
  <img src="https://img.shields.io/badge/shadcn%2Fui-Radix-000000?logo=shadcnui&logoColor=white" alt="shadcn/ui" />
  <img src="https://img.shields.io/badge/PyInstaller-6.22-1F5FA6" alt="PyInstaller" />
</p>

<p align="center">
  <a href="../../releases/latest"><img src="https://img.shields.io/badge/⬇_下載-Windows_%7C_macOS-FD5108" alt="下載" /></a>
</p>

<p align="center">
  <a href="README.md">English</a> | <b>繁體中文</b>
</p>

---

**Tablade**（*Table + Blade*）是一個桌面資料清洗工具，概念類似 Power Query 的「套用的步驟」：
把清洗過程排成一條**線性步驟清單**——移除雜訊列、拆欄、向下填滿、轉換數字與日期——每一步都能
預覽結果，最後輸出乾淨的 **Parquet**（寫到 Recipe 旁邊的 output 資料夾）。每一列都帶著它來自哪個檔案的哪一行，log 資料夾再附一份處理紀錄（manifest），
記錄用了哪份 Recipe、每個原始檔與輸出檔的 SHA-256、每一步的列數與警告。
步驟存成 **recipe JSON**，下個月拿到新檔案時可以原封不動重跑一次。
**所有檔案都留在你的電腦上**：沒有伺服器、不上傳。唯一的網路連線是啟動時向 GitHub 查最新版號（不送出資料、不下載）。

> [!IMPORTANT]
> **本 repo 不含、也不得含任何真實資料。**
> 測試資料一律由 `scripts/gen_fixtures.py` 產生；pre-commit hook 會擋下資料檔與疑似個資。
> 詳見 [不讓真實資料進入 repo](#️-不讓真實資料進入-repo)。

> **目前進度：M5，多階段 Recipe、結合與附加查詢完成。** 可以在視窗裡一步一步建立 recipe（套用的步驟、即時預覽、依步驟 schema 自動產生的表單），
> 也可以用產生的 skill 讓 Microsoft 365 Copilot 撰寫，或從命令列執行。Recipe 由依序執行的階段組成（每個階段讀實際檔或前面階段的結果），
> 格式不同的資料可以各自整理，再上下接起來（附加）或左右對照（結合）。共 25 種步驟：除了移除雜訊列、拆欄（分隔符號或固定位置）、
> 依條件複製欄位（例如段落標題）、向下填滿、篩選、合併來源檔，還有將列進行分類與正規表示式擷取（攤平分段的列印格式報表）、讀 Excel 工作表，以及清理文字（全形轉半形、看不見的字元）、取代文字、文字轉數字與日期（轉不了的列附行號警告）、
> 刪除、保留和排序、改欄名，移除重複、取消樞紐、群組彙總、樞紐（合併來源檔之前每個來源檔各自處理），以及到前面階段的結果查表（結合）；下方面板的「型別」分頁看得到每一欄的型別，匯出時附上處理紀錄（manifest）。見 [路線圖](#️-路線圖)。

---

## 💡 開發原因

實際拿到的資料很少是乾淨的：**拆成幾十個檔案**、**列印格式的 TXT 報表**（有標題雜訊、空行、小計），
或是多數工具讀不了的 **Big5 / cp950** 編碼。常見的做法是先手動合併，再用資料庫瀏覽器篩選——
又慢，而且沒辦法重現。

這個工具要滿足五件事：

- **爛檔也能一步一步處理。** 每個實體行原封不動讀進來（保留原始行號），再用一個個可預覽的小步驟
  清洗，而不是寫一大段腳本。
- **可重現。** 步驟是資料、不是程式碼。存成 recipe 後套用到新檔案，結果一樣；新資料的欄數或段數
  比預期多時會產生**警告**，不會默默改變結果。
- **資料不離開電腦。** 全部本機運算，並且**在程式碼層級杜絕對外連線**（見
  [🔒 安全性：不對外連線](#-安全性不對外連線)）。
- **能在權限受管控的電腦上執行。** 不用安裝 Python、不需要系統管理員權限：Windows 與 macOS
  都提供單一執行檔（或解壓即用的資料夾）。
- **讓 AI 助理寫 recipe。** Tablade 會從步驟 registry 自動產生 Agent Skills 套件（`SKILL.md`、步驟目錄、
  schema 與驗證器），Microsoft 365 Copilot 等 AI 助理就能把口語描述轉成 recipe 並自行驗證。
  skill 與 recipe 格式共用同一個版本號（見 [`DESIGN.md` §17](DESIGN.md)）。

---

## 🔧 運作方式

| 層 | 技術 | 角色 |
|---|---|---|
| 引擎 | [Polars](https://pola.rs/) 2.x（Python API） | 每個步驟編譯成 LazyFrame 操作；預覽只跑 `head(1000)`，匯出時串流寫成 Parquet。 |
| 視窗 | [pywebview](https://pywebview.flowrl.com/) 6 | 原生視窗（Windows 用 WebView2、macOS 用 WKWebView）。JS 直接呼叫 Python——**不起 HTTP 服務、不開 port**。 |
| 前端 | React 19 + TypeScript + Vite，[shadcn/ui](https://ui.shadcn.com/)（Radix + Tailwind CSS v4） | build 成**單一、自給自足的 HTML 檔**（約 300 KB），由 Python 以字串載入。 |
| 編碼 | Python codecs | 自動偵測 `utf-8-sig` → `cp950`；失敗時指出第一個無法解碼的位元組與行號。 |
| Excel | [fastexcel](https://github.com/ToucanToco/fastexcel)（calamine） | 快速、唯讀的 XLSX / XLS 讀取。 |
| Recipe | pydantic v2 | 驗證 recipe JSON。 |
| 打包 | PyInstaller 6 + uv | 同一份鎖定檔產出 `onefile` 與 `onedir`。 |

### 讀檔模式

| 模式 | 用途 | 產出欄位 | 狀態 |
|---|---|---|---|
| `csv` | 乾淨的 CSV | 所有欄位（一律文字） | ✅ M0（系統欄位在 M1） |
| `text_lines` | 爛格式 CSV / TXT 報表：每個實體行原封不動 | `__source_file`、`__line_no`、`line` | M1 |
| `excel` | XLSX / XLSM / XLS / ODS，指定工作表，或 `*` 讀每個檔的全部工作表：每一列都是資料（不當標題）、每一格都讀成文字 | `__source_file`、`__line_no`（Excel 的列號）、`A`、`B`、`C`…；`*` 時多一個 `__sheet`（每個工作表各自處理） | ✅ M4 |
| `parquet` | Parquet 檔：欄位與型別照檔案；檔案自己帶的 `__` 開頭欄位改名成「原始…」的一般欄保留 | `__source_file`（檔名）、`__line_no`（第幾列）、各欄 | ✅ M5 |
| `stages` | 前面階段的結果：每個來源各自是一個來源（名稱是「@階段／檔名」）；要接成一份（附加）就用「合併來源檔」 | `__stage`、上游的系統欄位（警告照樣指回原始檔的行號）、各欄 | ✅ M5 |

- `__` 開頭的是系統欄位：UI 預設隱藏，匯出時可選擇是否保留。
- Recipe 記一份**檔案清單**：Recipe 資料夾裡的檔存相對路徑，其他存絕對路徑，也可以手動輸入含 `*` 的路徑。清單裡的檔都找得到時，按工具列的「載入資料及預覽」才讀檔、算出每一步的預覽；只要匯出的話，可以直接按「處理並匯出資料」（不用先載入，會先用每個檔的前 2,000 行試跑，有錯就停下並指出是哪一步）。
- 多檔時每個檔各自一條流水線，到「合併來源檔」或寫出前才依清單順序接起來；欄位不一致時自動補 null。
- 每個檔讀進來時就依 `__line_no` 排好，每個步驟都保持這個順序，所以步驟不排序；
  依檔案計算的步驟（向下填滿、移除前後 N 列）不用 window 函式，幾 GB 的檔案記憶體用量也固定。

### 資料流

```
pywebview 視窗 — frontend/（React + shadcn/ui，單一 HTML）
      │  window.pywebview.api.xxx()（Promise，同一個行程，不走 HTTP）
      ▼
api.py（只做參數轉換與錯誤包裝）
      ▼
recipe/（pydantic）──► engine/compiler.py   步驟（資料）→ Polars LazyFrame
      ┌──────────────────────┼──────────────────────┐
      ▼                      ▼                      ▼
ingest/                steps/                  export/
編碼、逐行讀取、         純函式：                 Parquet + manifest
CSV / Excel、行號        LazyFrame → LazyFrame

cli.py — `tablade run recipe.json` 不開 UI，跑同一套引擎
```

### 🔒 安全性：不對外連線

- **只有一個網路連線，只查版號。** 啟動時 `src/tablade/update.py` 對 GitHub API 發一個 GET，
  讀最新 release 的版號；有新版時起始頁會顯示下載網址。不送出任何你或資料的資訊、不下載、
  不自動更新。設定 `TABLADE_NO_UPDATE_CHECK=1` 就完全不連線。
- **沒有其他網路程式碼。** `scripts/check_code_policy.py` 會解析 `src/` 底下每個檔案，禁止 import
  `socket`、`http`、`urllib`、`requests`、`httpx`、`aiohttp`、`ssl` 等模組（只有 `update.py`
  可以用 `urllib.request`），也禁止 `eval` / `exec` / `compile` / `__import__`。pre-commit 與 CI 都會跑。
- **沒有本機伺服器。** 頁面以 HTML 字串載入，pywebview 不會啟動內建的 HTTP server，也不開 port。
- **Content-Security-Policy `default-src 'none'`**（build 時注入）。頁面無法連線、嵌入或載入任何外部資源。
  `'unsafe-eval'` 只是因為 pywebview 的 JS bridge 需要。
- **資料不會被當成 HTML 顯示。** 儲存格內容一律用 `textContent` 寫入，含有標記的資料不會變成
  可以呼叫 Python 的程式。
- **已知的殘餘風險：** 規則檢查只涵蓋 Tablade 自己的程式碼，不含第三方套件。依賴版本鎖定在
  `uv.lock`，並維持在少數、知名的套件。

### 🛡️ 不讓真實資料進入 repo

- `.gitignore` 排除所有資料格式（CSV、TXT、Excel、Parquet、資料庫、PDF、壓縮檔…）。
- pre-commit hook `scripts/check_no_data.py` 會擋下：資料檔副檔名、超過 1 MiB 的檔案、通過檢查碼
  驗證的身分證字號、出現在關鍵字旁的 8 碼統一編號，以及本機私有、不進版控的 `.tablade-denylist`
  裡列出的字詞。命中的值在輸出時會遮蔽，避免透過 CI log 二次外洩。
- 真實 recipe 也可能含敏感的篩選條件，所以只能 commit `examples/` 底下搭配假資料的範例。

### 📦 打包與發布

| 平台 | 檔案 | 說明 |
|---|---|---|
| Windows | `tablade-windows-x64-onefile.zip` | 單一 `tablade.exe` |
| macOS | `tablade-macos-arm64-app.zip` | `Tablade.app`（onedir 包成 App；PyInstaller 不支援 onefile 的 `.app`）；第一次開啟請按右鍵 →「打開」 |
| AI skill | `tablade-recipe-skill-v<recipe 版本>.zip` | 給 Microsoft 365 Copilot 等 AI 助理用 |

- `scripts/build.py` 打包這個平台要發布的版本（Windows：onefile；macOS：`Tablade.app`），跑一次內建的
  自我檢測，再壓成 zip。`scripts/build.py onedir` 仍然可以另外打包 Windows 的 onedir 資料夾，
  onefile 被擋時用。
- CI（`.github/workflows/build.yml`）在 Windows 與 macOS 上跑同一支腳本。推 `v*` tag 時，先檢查 tag
  和 `src/tablade/__init__.py` 的 `__version__` 一致（`scripts/check_release.py`），不一致就停止發版；
  再把 zip 和 AI skill 附到 Release。
- 發版：改 `__version__`（例如 `0.2.0`）、commit 並 push，再 `git tag v0.2.0 && git push origin v0.2.0`。
- 執行檔**未簽章**。macOS 也可以執行 `xattr -dr com.apple.quarantine Tablade.app`。
- Windows 版是視窗程式，命令列輸出不會顯示；請用 `tablade.exe selftest --out result.json`，
  再看 JSON 的內容。

---

## 🤖 讓 Microsoft 365 Copilot 寫 recipe

Tablade 會從步驟 registry 產生 [Agent Skills](https://learn.microsoft.com/en-us/microsoft-365/copilot/extensibility/agent-builder-add-skills)
套件：`SKILL.md`、附範例的步驟目錄、recipe JSON Schema，以及只需要 Python 標準函式庫的單一檔案驗證器
（Copilot 的沙箱不能安裝套件）。skill 的版本就是 recipe 格式的版本，所以 skill 寫出的 recipe 一定能用這版 Tablade 開啟。

1. **取得 skill。** 在 Tablade 選 **Copilot → 下載 Copilot skill**，或執行 `tablade skill`。
2. **加到 agent。** 在 Agent Builder：*Configure → Skills → Add*，上傳整個 zip。
   （想先確認沙箱能做什麼，可以上傳 `tablade skill --probe` 產生的 probe，再說「執行 Tablade probe」。）
3. **請 Copilot 寫。** 上傳資料（或 `tablade profile 資料.csv` 產生的摘要），描述要怎麼整理。
   Copilot 會寫出 recipe，並用 `scripts/validate_recipe.py` 自我檢查。
4. **用 Tablade 開啟。** 把 recipe 放在資料旁邊，選 **開啟 recipe**。Tablade 會驗證並用真實資料試跑每一步；
   有問題時選 **Copilot → 檢查並複製錯誤報告**，把報告貼回給 Copilot。

---

## 📊 測試結果

| 項目 | 結果 |
|---|---|
| 單元測試（`pytest`） | 244 項通過（每種步驟文件裡的範例都當成測試跑；UI 背後的 session 照使用者的操作順序一步步測） |
| 範例 recipe（`examples/sales_detail.recipe.json`，2 個 cp950 檔、94 行 → 70 列） | ✅ 輸出與預期完全一致；每一列的 `__line_no` 都指回原始檔的那一行 |
| 打包後的自我檢測（產生 cp950 CSV → Parquet → 讀回比對 → Lazy 查詢 → 讀 Excel → 多核心轉碼 → 完整執行範例 recipe：轉碼、各檔分開處理、合併來源檔、串流匯出） | ✅ Windows x64（CI，2 個核心：仍實際開 2 個工作程序測試）與 macOS arm64 |
| 視窗 smoke test（開視窗、自我檢測、透過 JS bridge 載入範例、等表格畫出 70 列、關閉） | ✅ macOS arm64 的 onefile、onedir；Windows 由 CI 跑 |
| 打包後的 App 產生 AI skill（`tablade skill`） | ✅ onefile、onedir |

| 版本（macOS arm64） | 大小 | 啟動時間 |
|---|---|---|
| onefile | 65 MB | 約 4 秒（每次啟動都要解壓到暫存資料夾） |
| onedir `.app` | 解壓後 191 MB / 壓縮 62 MB（Polars runtime 佔 148 MB） | 約 1.5 秒 |

| 大檔（macOS arm64，假資料） | 時間 | 峰值記憶體 |
|---|---|---|
| 5 GB UTF-8 文字檔、8,560 萬行：開啟並產生預覽（取樣 101,000 行） | 20 秒 | 1.9 GB |
| 同上：串流匯出全部 | 62 秒 | 2.5 GB |
| 2 GB cp950 文字檔：轉碼（多核心，8 個工作程序） | 3 秒（單核心 9 秒） | 每個程序約 70 MB，不隨檔案成長 |
| 2 個 1.5 GB UTF-8 檔：範例 Recipe 串流執行 | 29 秒 | 2.4 GB |
| 200 個 10 MB 的檔：同上 | 23 秒 | 1.3 GB |
| 1000 個 100 KB 的檔：同上 | 15 秒 | 0.7 GB |
| 2 個 2 GB cp950 檔：範例 Recipe 最後加「合併來源檔」，串流執行 | 40 秒（改進前 119 秒） | 1.3 GB（改進前 2.9 GB） |
| 改寫前：1 GB cp950 | 96 秒 | 10.2 GB |

Windows（CI）：onefile 壓縮後 68.7 MB。已在權限受管控的公司電腦上確認 onefile 版可以開啟（M0 完成）；啟動時間還沒量。

---

## 🛠️ 開發

需要 [uv](https://docs.astral.sh/uv/) 與 Node.js 24 以上。

```bash
uv sync                                              # 依 uv.lock 建立虛擬環境
npm --prefix frontend ci                             # 安裝前端依賴
npm --prefix frontend run build                      # build 出 frontend/dist/index.html（開視窗前必做）
uv run pre-commit install                            # 安裝資料防護 hook（必做）
uv run pytest                                        # 跑測試
uv run tablade                                       # 從原始碼開啟視窗
uv run python scripts/gen_frontend_dev_data.py       # 產生瀏覽器假 API 用的資料（不進版控）
npm --prefix frontend run dev                        # Vite dev server（一般瀏覽器會用假 API）
TABLADE_DEV_URL=http://localhost:5173 uv run tablade # 在真正的視窗裡熱更新
uv run python scripts/gen_fixtures.py tmp/fixtures   # 產生假資料
uv run python scripts/build.py --gui-smoke           # 打包要發布的版本（Windows：onefile，macOS：.app）、自我檢測、壓縮到 dist/release/
uv run --group icons python scripts/make_icons.py    # 從 assets/icon.svg 重新產生 icon.ico / icon.icns
```

命令列：

```bash
tablade                              # 開啟視窗
tablade selftest [--out result.json] # 用假資料自我檢測（結束代碼 0 表示通過）
tablade gui --smoke                  # 開視窗、自我檢測後關閉
tablade convert in.csv out.parquet [--encoding auto|utf-8|cp950|big5hkscs]
tablade run recipe.json [--out-dir 資料夾] [--json]  # 執行 recipe：輸出寫到 output/、處理紀錄寫到 log/（預設在 recipe 所在的資料夾），列出每一步的列數
tablade validate recipe.json [--run] [--json]    # 驗證 recipe（--run：用真實資料試跑）
tablade schema [--out spec.json]                 # 步驟目錄＋recipe JSON Schema
tablade skill [--out x.zip] [--probe]            # 給 Microsoft 365 Copilot 的 AI skill（或沙箱 probe）
tablade profile 資料.csv [--lines 30] [--mask]   # 給 AI 助理看的資料檔摘要
tablade version
```

跑一次範例：

```bash
uv run python scripts/gen_fixtures.py examples/data
uv run tablade run examples/sales_detail.recipe.json
uv run tablade run examples/sales_report.recipe.json   # 分段的列印格式報表 → 每一列明細
uv run tablade run examples/sales_excel.recipe.json    # Excel 工作表：去掉上面的標題列，轉成日期與數字
uv run tablade run examples/sales_combined.recipe.json # 4 個階段：文字匯出檔與 Excel 附加起來，再對照品項表
```

若有不能出現在 repo 的字詞（人名、公司名稱、專案代號等），寫進 repo 根目錄的 `.tablade-denylist`
（一行一個，已在 `.gitignore`），pre-commit 會擋下含有這些字詞的檔案。

---

## 🗂️ 程式架構

```
src/tablade/
├─ app.py             pywebview 進入點；把 frontend/dist/index.html 讀成字串載入
├─ api.py             JS bridge：只做參數轉換與錯誤包裝
├─ session.py         編輯中的 recipe：狀態、每一步的快取、undo / redo（UI 只跟它溝通）
├─ skill.py           從 registry 產生 AI skill（SKILL.md、步驟目錄、schema、驗證器）
├─ profile.py         給 AI 助理看的資料檔摘要
├─ cli.py             命令列（gui / run / validate / schema / skill / profile / selftest / convert / version）
├─ selftest.py        用假資料檢測打包後的環境
├─ fixtures.py        決定性的假資料（測試、自我檢測、範例共用）
├─ recipe/            pydantic model、版本號、只用標準函式庫的驗證器、spec 與 schema lock
├─ steps/             每類步驟一個模組，登錄到 registry.py
├─ engine/            執行 recipe（每步列數、附行號的警告）；check = 驗證＋試跑
├─ ingest/            編碼偵測、text_lines / csv 讀取、glob 與多檔合併
└─ export/            原子寫入的 Parquet 輸出
frontend/             Vite + React + TypeScript + shadcn/ui；bridge.ts 與 api.py 對應
assets/               icon.svg（原始檔）、icon.ico、icon.icns、icon.png
scripts/              build、make_icons、gen_fixtures、check_no_data、check_code_policy
tests/                pytest
tablade.spec          PyInstaller 設定（以 TABLADE_BUILD 切換 onefile / onedir）
```

- **步驟是資料，不是程式碼。** recipe 只記步驟類型與參數，由 compiler 對應成 Polars 操作，
  絕不執行使用者輸入。
- **每個步驟都是純函式** `apply(lf, params) -> LazyFrame`：不讀寫檔案、不碰 UI。
- **依資料決定結構的步驟**（把第一列作為欄位標題、拆成 N 欄、樞紐）在建立步驟時先掃一次資料，把欄名寫死在 recipe。
- 完整設計與理由：[`DESIGN.md`](DESIGN.md)。

---

## 🗺️ 路線圖

| 階段 | 內容 | 完成標準 |
|---|---|---|
| **M0 環境驗證** | 最小 pywebview + Polars 程式，onefile 與 onedir 打包 | 在權限受管控的 Windows 筆電上能開出視窗，並把 CSV 轉成 Parquet ✅ |
| M1 引擎 | 有版本號的 recipe schema、步驟 registry、`text_lines` / `csv` 讀取、compiler、CLI（`run` / `validate` / `schema`）、前 8 種步驟 | `tablade run examples/sales_detail.recipe.json` 產出正確結果 ✅ |
| M1.5 AI skill | 沙箱實測、`tablade profile`、skill 產生器、單一檔案驗證器 | Copilot 載入 skill 後能寫出通過驗證的 recipe（已完成，等 Copilot 實測） |
| M2 UI | 步驟清單（新增、排序、停用、刪除）、預覽表格、參數表單、匯入 recipe 與錯誤報告、下載 skill | 不寫 JSON 也能完成範例清洗流程 ✅ |
| M3 在地格式與追溯 | `parse_number`、`parse_date`（民國）、`trim_clean`、`replace_values`、`remove_columns`、`select_columns`、`rename_columns`、型別分頁、manifest | 輸出附 manifest，警告附行號 ✅ |
| M4 多檔與 Excel | glob、diagonal concat、Excel 讀取、`classify_rows`、`extract_regex`、`split_fixed_width` | 分段報表的範例可以完整攤平 ✅ |
| M5 進階步驟 | `dedupe`、`unpivot`、`group_by`、`pivot`、多階段 Recipe、`parquet` 讀取、`join`、附加查詢（讀好幾個階段＋合併來源檔） | 範例 `sales_combined`：文字匯出檔與 Excel 各自整理後附加，再到品項對照表查類別 ✅ |

---

## ⚠️ 已知限制

- **Copilot skill 還沒在 Agent Builder 實測**：skill 的 script 能不能讀到對話中上傳的檔案、沙箱的 Python 版本，要用 probe 確認。
- **結合的另一張表整份放進記憶體**（這一張表串流）。另一張表的鍵值重複時直接報錯，不會默默多出列。大檔時預覽的另一張表是樣本，
  結合的設定畫面可以「完整跑一次」那個階段。
- **大檔的預覽是樣本。** 超過 20 萬行時，預覽只用每個檔案的前 10 萬行與最後 1,000 行，UI 上每一步的列數是樣本的；
  資料 pane 會標「抽樣模式」，前面有移除重複、群組彙總、樞紐這類要看整個檔的步驟時另外標紅色的「警告：預覽結果可能不正確」（滑鼠移上去列出是哪幾步）；匯出時會串流處理全部資料，進度視窗顯示處理到第幾行與百分比，匯出後「輸出資料」改成讀匯出檔，標「完整模式」。串流匯出只回報最後的列數，警告也只算最後還留著的列。群組彙總、樞紐的預覽也是用樣本算的（樞紐要變成欄的值從預覽的資料讀出，匯出時資料裡多出來的值會產生警告）。
- **非 UTF-8 的檔案會先轉成 UTF-8 暫存檔**（cp950 單核心約每秒 230 MB；64 MB 以上的檔用多個程序同時轉，最多 8 個，結果和單核心相同；需要約原檔 1.5 倍的暫存空間），關閉視窗時刪除；當掉或被強制結束時留下的副本，下次啟動 Tablade 時清掉。
- **`csv` 模式的行號假設沒有跨行欄位**；有跨行欄位的檔案請用 `text_lines`。
- **onefile 啟動較慢**：每次啟動都要先解壓縮；部分防毒軟體或 AppLocker 會擋暫存資料夾裡的程式。Windows 只發布 onefile，遇到這種情況請用 `uv run python scripts/build.py onedir` 另外打包 onedir 版。
- **執行檔未簽章**：macOS 會出現 Gatekeeper、Windows 會出現 SmartScreen 提示。
- **macOS 版只支援 Apple Silicon（arm64）。**
- Polars 2.x 的 join / group-by 還不能寫到硬碟；若記憶體不夠，之後可能把這幾種步驟交給 DuckDB。

---

## 📄 免責聲明

本軟體依「現狀」提供，不附任何明示或默示的保證。結果（資料正確性、匯出、相容性等）需由使用者自行驗證。
**使用風險由使用者自行承擔。**

---

<p align="center">
  <sub>Tablade v0.1.0 · <a href="LICENSE">MIT License</a></sub>
</p>
