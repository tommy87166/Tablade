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
預覽結果，最後輸出乾淨的 **Parquet**，並附上一份 manifest，記錄每一列資料來自哪個檔案的哪一行。
步驟存成 **recipe JSON**，下個月拿到新檔案時可以原封不動重跑一次。
**所有檔案都留在你的電腦上**：沒有伺服器、不上傳，程式裡也沒有任何網路連線。

> [!IMPORTANT]
> **本 repo 不含、也不得含任何真實資料。**
> 測試資料一律由 `scripts/gen_fixtures.py` 產生；pre-commit hook 會擋下資料檔與疑似個資。
> 詳見 [不讓真實資料進入 repo](#️-不讓真實資料進入-repo)。

> **目前進度：M0 環境驗證。** 視窗、Python ↔ JS bridge、CSV → Parquet、單一執行檔打包都已可用；
> 步驟引擎在 M1 加入。見 [路線圖](#️-路線圖)。

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
| `excel` | XLSX / XLS，指定工作表 | `__source_file`、`__sheet`、`__line_no`、各欄 | M4 |

- `__` 開頭的是系統欄位：UI 預設隱藏，匯出時可選擇是否保留。
- 多檔（glob）以 `diagonal_relaxed` 合併：欄位不一致時自動補 null。
- 依賴列順序的操作（向下填滿、移除前後 N 列）一律先依 `__source_file, __line_no` 排序，
  不依賴實際執行順序。

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

- **沒有網路程式碼。** `scripts/check_code_policy.py` 會解析 `src/` 底下每個檔案，禁止 import
  `socket`、`http`、`urllib`、`requests`、`httpx`、`aiohttp`、`ssl` 等模組，也禁止
  `eval` / `exec` / `compile` / `__import__`。pre-commit 與 CI 都會跑。
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
| Windows | `tablade-windows-x64-onedir.zip` | 解壓後執行 `tablade\tablade.exe`——onefile 被擋或啟動太慢時的備案 |
| macOS | `tablade-macos-arm64-app.zip` | `Tablade.app`；第一次開啟請按右鍵 →「打開」 |
| macOS | `tablade-macos-arm64-onefile.zip` | 命令列單一執行檔 |

- `scripts/build.py` 會打包兩種版本、各跑一次內建的自我檢測，再壓成 zip；CI
  （`.github/workflows/build.yml`）在 Windows 與 macOS 上跑同一支腳本，推 `v*` tag 時把 zip
  附到 Release。
- 執行檔**未簽章**。macOS 也可以執行 `xattr -dr com.apple.quarantine Tablade.app`。
- Windows 版是視窗程式，命令列輸出不會顯示；請用 `tablade.exe selftest --out result.json`，
  再看 JSON 的內容。

---

## 📊 測試結果

| 項目 | 結果 |
|---|---|
| 單元測試（`pytest`） | 66 項通過 |
| 打包後的自我檢測（產生 cp950 CSV → Parquet → 讀回比對 → Lazy 查詢 → Excel 引擎） | ✅ onefile 與 `.app`（macOS arm64） |
| 視窗 smoke test（開視窗、透過 JS bridge 跑自我檢測、關閉） | ✅ onefile 與 `.app`（macOS arm64） |

| 版本（macOS arm64） | 大小 | 啟動時間 |
|---|---|---|
| onefile | 65 MB | 約 4 秒（每次啟動都要解壓到暫存資料夾） |
| onedir `.app` | 解壓後 191 MB / 壓縮 62 MB（Polars runtime 佔 148 MB） | 約 1.5 秒 |

Windows 的數據會在 CI 版本於權限受管控的電腦上驗證後補上。

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
npm --prefix frontend run dev                        # Vite dev server（一般瀏覽器會用假 API）
TABLADE_DEV_URL=http://localhost:5173 uv run tablade # 在真正的視窗裡熱更新
uv run python scripts/gen_fixtures.py tmp/fixtures   # 產生假資料
uv run python scripts/build.py --gui-smoke           # 打包 onefile + onedir、自我檢測、壓縮到 dist/release/
uv run --group icons python scripts/make_icons.py    # 從 assets/icon.svg 重新產生 icon.ico / icon.icns
```

命令列：

```bash
tablade                              # 開啟視窗
tablade selftest [--out result.json] # 用假資料自我檢測（結束代碼 0 表示通過）
tablade gui --smoke                  # 開視窗、自我檢測後關閉
tablade convert in.csv out.parquet [--encoding auto|utf-8|cp950|big5hkscs]
tablade version
```

若有不能出現在 repo 的字詞（人名、公司名稱、專案代號等），寫進 repo 根目錄的 `.tablade-denylist`
（一行一個，已在 `.gitignore`），pre-commit 會擋下含有這些字詞的檔案。

---

## 🗂️ 程式架構

```
src/tablade/
├─ app.py             pywebview 進入點；把 frontend/dist/index.html 讀成字串載入
├─ api.py             JS bridge：只做參數轉換與錯誤包裝
├─ cli.py             命令列（gui / selftest / convert / version）
├─ selftest.py        用假資料檢測打包後的環境
├─ fixtures.py        決定性的假資料（測試、自我檢測、範例共用）
├─ ingest/            編碼偵測、CSV 讀取
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
- **依資料決定結構的步驟**（提升標題列、拆成 N 欄、樞紐）在建立步驟時先掃一次資料，把欄名寫死在 recipe。
- 完整設計與理由：[`DESIGN.md`](DESIGN.md)。

---

## 🗺️ 路線圖

| 階段 | 內容 | 完成標準 |
|---|---|---|
| **M0 環境驗證** | 最小 pywebview + Polars 程式，onefile 與 onedir 打包 | 在權限受管控的 Windows 筆電上能開出視窗，並把 CSV 轉成 Parquet |
| M1 引擎 | 有版本號的 recipe schema、步驟 registry、`text_lines` / `csv` 讀取、compiler、CLI（`run` / `validate` / `schema`）、前 8 種步驟 | `tablade run examples/sales_report.recipe.json` 產出正確結果 |
| M1.5 AI skill | 沙箱實測、`tablade profile`、skill 產生器、單一檔案驗證器 | Copilot 載入 skill 後能寫出通過驗證的 recipe |
| M2 UI | 步驟清單（新增、排序、停用、刪除）、預覽表格、參數表單、匯入 recipe 與錯誤報告、下載 skill | 不寫 JSON 也能完成範例清洗流程 |
| M3 在地格式與追溯 | `parse_number`、`parse_date`（民國）、`trim_clean`、manifest | 輸出附 manifest，警告附行號 |
| M4 多檔與 Excel | glob、diagonal concat、Excel 讀取、`classify_rows`、`extract_regex` | 分段報表的範例可以完整攤平 |
| M5 進階步驟 | join、group_by、unpivot、pivot、dedupe、匯出成 Python 腳本 | — |

---

## ⚠️ 已知限制

- **M0 只能把單一 CSV 轉成 Parquet。** 系統欄位（`__source_file`、`__line_no`）、recipe 與步驟在 M1 加入。
- **M0 會把整個檔案讀進記憶體**（先轉碼再解析）。一般大小的檔案在 16 GB 筆電上沒問題；串流處理隨引擎一起加入。
- **onefile 啟動較慢**：每次啟動都要先解壓縮；部分防毒軟體或 AppLocker 會擋暫存資料夾裡的程式，這時請改用 onedir 版。
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
