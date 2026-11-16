# Tablade（暫定名稱）— 設計文件

> Table + Blade：資料整理的瑞士刀。
> 取代 Alteryx 的開源、本機執行、Power Query 式「一步一步」資料整理工具，最終輸出 Parquet。

---

## 1. 背景與目標

資料清洗時常會遇到大量、分成多個檔案、格式不乾淨的資料（CSV、TXT 報表、Excel）。常見做法是先手動合併成一個檔，再用 SQLite Browser 等工具篩選，流程繁瑣且不可重現。

**目標**

- 像 Power Query 一樣，以**線性步驟清單**（Applied Steps）整理資料，每一步可預覽、修改、停用、刪除。
- 可處理**多檔合併**、**爛格式檔案**（標題雜訊、空行、分段報表、Big5 編碼）。
- 最終輸出 **Parquet**，並附上記錄資料來源與處理過程的 **manifest**。
- 步驟存成 **recipe JSON**，同樣的 recipe 套用到新資料可重現結果。
- 在 **Mac 與 Windows** 上以**單一執行檔**執行，免安裝 Python。

**非目標（至少第一版不做）**

- 節點式畫布（Alteryx / KNIME 那種 DAG）
- 排程、伺服器模式、多人協作
- 連接資料庫或雲端資料來源
- 視覺化圖表

---

## 2. 限制條件

| 限制 | 對設計的影響 |
|---|---|
| 公司配發的電腦權限受管控、不可自行安裝套件 | 必須打包成單一執行檔（或解壓即用的資料夾） |
| 資料不可上雲 | 全部本機運算，不開對外網路連線，不依賴執行時下載任何擴充 |
| 處理的資料可能含敏感資訊 | repo 內**永遠不得出現真實資料**（見 §11） |
| repo 從第一天就公開 | 程式碼、文件、commit 訊息、issue 都不得含真實資料或可識別的組織資訊（見 §11） |
| 目標電腦可執行 Electron 程式 | 未簽章程式至少不會全面被擋，但單一執行檔需實測（見 §10） |
| 筆電記憶體約 16GB | 預覽只跑前 N 列；大量 join / group-by 時要注意記憶體 |

---

## 3. 技術選型

| 層 | 選擇 | 理由 |
|---|---|---|
| 語言 | Python 3.12+（開發與打包用 3.13） | 開發快、AI coding 友善 |
| 資料引擎 | **Polars 2.x**（Python API，目前固定 2.1） | Python API 有正式的版本與棄用政策；Lazy API 適合把步驟串成查詢計畫 |
| 視窗 / UI 殼 | **pywebview 6.x** | 單一行程；JS 直接呼叫 Python（`js_api`），不開 port、不起 HTTP 服務；Windows 用 WebView2、Mac 用 WKWebView |
| 前端 | 靜態 HTML / JS（先不用框架，或用 Vite 打包後的靜態檔） | 前端只是 Polars API 的外殼，不需要伺服器 |
| Excel | `polars.read_excel`（calamine 引擎，需 `fastexcel`） | 快、純讀取 |
| 編碼 | Python 內建 codecs（`utf-8-sig`、`cp950`） | Polars `read_csv` 只支援 UTF-8 |
| Schema 驗證 | pydantic v2 | recipe JSON 驗證與型別 |
| 打包 | PyInstaller 6.x（`--onefile`，備案 `--onedir`） | 跨平台、成熟 |
| 套件管理 | uv（`uv.lock` 鎖定所有版本） | 開發、CI、打包用同一份鎖定檔 |
| CI | GitHub Actions（Windows / macOS matrix） | 自動產出可下載的執行檔 |
| 授權 | MIT | 依賴套件：Polars MIT、pywebview BSD、fastexcel MIT、PyInstaller GPL + bootloader exception（散布打包後的程式沒有問題） |

**為什麼不選 Tauri / Electron**：Python 本身就要整包打包進去，Tauri 外殼小的優勢沒有意義，還要另外處理兩支程式之間的溝通；Electron 會多出 100MB 以上的 Chromium。

**保留彈性**：Polars 2.x 的 join / group-by 目前不能寫到硬碟。若遇到記憶體不足，可把那幾種步驟改交給 DuckDB 執行（兩者透過 Arrow 交換資料，幾乎沒有成本）。第一版不引入。

---

## 4. 整體架構

```
┌──────────────────────── pywebview 視窗 ────────────────────────┐
│  frontend/（HTML/JS）                                           │
│   步驟清單 │ 資料預覽表格 │ 步驟參數表單 │ 警告 / 列數            │
│        │  window.pywebview.api.xxx()（Promise）                  │
└────────┼───────────────────────────────────────────────────────┘
         ▼
   api.py（JS bridge：只做參數轉換與錯誤包裝）
         ▼
   recipe/（schema 驗證）──► engine/compiler.py
                                 │  步驟（資料）→ Polars LazyFrame 操作
         ┌───────────────────────┼───────────────────────┐
         ▼                       ▼                       ▼
   ingest/                 steps/（每種步驟一個模組）    export/
   編碼轉換、逐行讀取、       純函式：                   Parquet + manifest
   CSV / Excel、加行號        LazyFrame → LazyFrame

   cli.py：`tablade run recipe.json`（不開 UI，批次執行，也方便測試）
```

**核心原則**

1. **步驟是資料，不是程式碼**：recipe 只記「步驟類型＋參數」，由 compiler 對應成 Polars 操作。**不使用 `eval` / `exec` 執行使用者輸入。**
2. **每個步驟都是純函式**：`apply(lf: pl.LazyFrame, params) -> pl.LazyFrame`，不讀寫檔案、不碰 UI。
3. **UI 與引擎分離**：引擎可以只靠 CLI 完整運作；UI 只是呼叫引擎。
4. **可追溯**：每一筆輸出都能追回來源檔名與原始實體行號。

---

## 5. 讀檔層（Ingest）

### 5.1 讀取模式

| 模式 | 用途 | 產出欄位 |
|---|---|---|
| `text_lines` | 爛格式 CSV / TXT 報表：每一個實體行原封不動讀進來 | `__source_file`, `__line_no`, `line` |
| `csv` | 乾淨的 CSV，交給 Polars 解析 | `__source_file`, `__line_no`, 各欄 |
| `excel` | xlsx / xls，指定工作表 | `__source_file`, `__sheet`, `__line_no`, 各欄 |

- `text_lines` 是爛檔的預設模式。**不要讓解析器自動略過空行或猜測格式**，否則行號會跟原始檔對不起來。
- `__line_no` 從 1 起算，對應原始檔的實體行號（Excel 則對應工作表列號）。
- 以 `__` 開頭的欄位是系統欄位，UI 預設隱藏但可顯示，匯出時可選擇是否保留。

### 5.2 編碼

- `encoding: "auto"`：依序嘗試 `utf-8-sig` → `cp950`；都失敗就報錯，並列出第一個無法解碼的位元組位置。
- 台灣 Windows 產出的「Big5」檔案實際上大多是 **cp950**（Big5 的超集），預設用 `cp950`，可手動指定 `big5hkscs`。
- 轉碼在 Python 層完成，之後一律以 UTF-8 交給 Polars。

### 5.3 多檔

- `inputs[].pattern` 支援 glob（例如 `明細_*.csv`）。
- 多檔用 `pl.concat(how="diagonal_relaxed")` 合併：欄位不一致時自動補 null。
- 每一列都帶 `__source_file`。

---

## 6. Recipe JSON 格式（v1 草案）

```json
{
  "tablade_recipe": 1,
  "name": "明細整理範例",
  "inputs": [
    {
      "id": "src",
      "pattern": "data/明細_*.csv",
      "reader": "text_lines",
      "encoding": "auto"
    }
  ],
  "steps": [
    { "id": "s1", "type": "remove_top_rows", "n": 4 },
    { "id": "s2", "type": "split_delimited", "delimiter": ",", "quote": "\"",
      "columns": ["日期", "門市", "備註", "金額"] },
    { "id": "s3", "type": "fill_down", "columns": ["門市"] },
    { "id": "s4", "type": "split_column", "column": "備註", "delimiter": "|",
      "names": ["備註_1", "備註_2", "備註_3"], "on_extra": "warn" },
    { "id": "s5", "type": "parse_number", "column": "金額",
      "negative": "parentheses", "on_error": "null_and_report" },
    { "id": "s6", "type": "parse_date", "column": "日期", "format": "roc" }
  ],
  "output": { "format": "parquet", "path": "output/明細.parquet", "keep_system_columns": true }
}
```

**規則**

- 每個步驟都有唯一的 `id`，並可加上 `"enabled": false` 暫時停用。
- **依資料決定結構的步驟**（提升標題列、拆成幾欄、樞紐），在**建立步驟的當下**先掃一次資料，把欄名和欄數寫死在 recipe 裡。之後套用到新資料時，若出現更多段數或新的欄位，產生**警告**，不默默增減欄位。
- recipe 本身可能含有敏感資訊（例如篩選條件裡的人名或公司名稱），所以**真實 recipe 也不得 commit**，repo 只放範例 recipe。

---

## 7. 步驟目錄

### 7.1 MVP（第一版）

| 類型 | 說明 | Polars 實作方向 |
|---|---|---|
| `remove_top_rows` | 移除前 N 列 | 依 `__line_no` 篩選 |
| `remove_bottom_rows` | 移除最後 N 列 | 依 `__line_no` 篩選（每個來源檔各自計算） |
| `remove_blank_rows` | 移除空白列 | 依全部欄位都是 null 或空字串篩選 |
| `promote_headers` | 指定某一列當欄名 | 建立步驟時讀取該列內容，寫死在 recipe |
| `split_delimited` | 把 `line` 依分隔符號拆成多欄（處理引號） | 用 Python `csv` 解析，或 `str.split` 加上引號處理 |
| `split_column` | 某一欄依分隔符號再拆 | `str.split_exact(sep, n)` 後 `unnest` |
| `fill_down` | 向下填滿 | 先依 `__line_no` 排序，再 `fill_null(strategy="forward")` |
| `filter` | 條件篩選（等於、包含、開頭是、正規表示式、空值、數值比較） | `filter(expr)`，條件用結構化參數組成，不接受任意字串運算式 |
| `select_columns` | 保留、刪除、重新排序欄位 | `select` |
| `rename_columns` | 重新命名欄位 | `rename` |
| `trim_clean` | 修剪空白、全形轉半形（NFKC）、移除不可見字元 | `str.strip_chars`、`map_batches` 加 `unicodedata` |
| `replace_values` | 取代值（一般文字或正規表示式） | `str.replace_all` |
| `parse_number` | 文字轉數字：千分位、括號負數 `(1,000)`、尾端負號 `1,000-` | 正規化字串後再轉型；失敗的列記錄行號 |
| `parse_date` | 文字轉日期：西元、民國 `115/01/05`、`1150105` | 民國年加 1911；失敗的列記錄行號 |
| `classify_rows` | 依規則替每一列貼標籤（開頭是、包含、正規表示式） | `when/then` 產生 `__row_type` |
| `extract_regex` | 用正規表示式從欄位擷取一或多個群組 | `str.extract_groups` |

**`classify_rows` 是處理分段報表的關鍵**：先分類（段落標題、欄位標題、明細、小計）→ 從段落標題擷取分組名稱（例如門市）→ `fill_down` → 依類型拆欄位 → 篩掉標題和小計列。後續步驟可以指定 `only_row_types: [...]`，只套用在某些類型的列上。

### 7.2 第二階段

`join`（合併查詢，`maintain_order="left"`）、`append`（附加查詢）、`group_by`、`unpivot`、`pivot`、`dedupe`、`sort`、`conditional_column`、`add_index`、`custom_expression`（限定用白名單語法組成，不可任意執行程式）。

### 7.3 每種步驟都要提供

1. pydantic 參數 model
2. `apply()` 純函式
3. `describe()`：給 UI 顯示的一句話說明，例如「移除前 4 列」
4. `infer_schema()`（如果需要）：建立步驟時掃描資料，決定欄名與欄數
5. 單元測試（假資料）

---

## 8. 預覽與執行

- **預覽**：選取第 k 步時，只執行第 1 到 k 步，最後 `.head(1000).collect()`。
- **快取**：以「輸入檔指紋＋前 k 步的 recipe 雜湊」當 key，快取每一步的預覽結果。修改第 k 步時，只需重算第 k 步之後的步驟。
- **列順序**：Polars 2.x 預設的串流引擎在 join / group_by / unpivot 之後**不保證列順序**。凡是依賴順序的步驟（填滿、移除前後 N 列），一律先依 `__source_file, __line_no` 排序，不依賴實際執行時的順序。
- **完整執行**：匯出時才跑全部資料，盡量用 `sink_parquet` 串流寫出。
- **錯誤與警告**：轉型失敗、段數超出預期、新出現的欄位等等，集中顯示在 UI 的警告區，每一則都附上來源檔名和行號。

---

## 9. 輸出與可追溯紀錄

輸出 `xxx.parquet`，並在旁邊寫一份 `xxx.manifest.json`：

```json
{
  "tool": "tablade", "tool_version": "0.1.0", "polars_version": "2.1.x",
  "run_at": "2026-11-16T18:00:00+08:00",
  "recipe_sha256": "…",
  "inputs": [
    { "file": "明細_01.csv", "bytes": 123456, "sha256": "…", "encoding_detected": "cp950" }
  ],
  "row_counts": { "input": 120345, "s1": 120341, "s5": 120300, "output": 120300 },
  "warnings": [ { "step": "s5", "line_no": 88, "source_file": "明細_01.csv", "message": "無法轉為數字：'N/A'" } ],
  "output": { "file": "明細.parquet", "rows": 120300, "sha256": "…" }
}
```

- 輸入檔的 SHA-256 加上 recipe 雜湊，可以證明「這份輸出是用這份 recipe，從這些原始檔產生的」。
- 每一步的列數變化可以直接拿來核對資料有沒有漏掉或重複。
- 另外提供「匯出成 Python 腳本」，把 recipe 轉成獨立可執行的 Polars 程式碼，方便檢查或重跑（第二階段）。

---

## 10. 打包與發布

- **PyInstaller `--onefile`** 為主；**`--onedir`**（壓成 zip，解壓即用）為備案。
  - 單一執行檔每次啟動會先解壓縮到暫存資料夾（Windows 的 `%TEMP%\_MEI…`），可能比較慢、可能被防毒軟體誤判，也可能被 AppLocker 擋。
  - 實測參考：Linux 上 Python 3.13 + Polars 2.1 的最小 onefile 約 64MB，啟動約 0.9 秒。加上 pywebview、fastexcel 和前端後，預估 70 到 100MB。
- 前端靜態檔以 `--add-data` 打包；程式內用 `sys._MEIPASS` 取得路徑。
- Windows：pywebview 使用 Edge WebView2（Win10/11 通常已內建）。
- macOS：未簽章，第一次開啟要按右鍵 →「打開」，或執行 `xattr -dr com.apple.quarantine`。PyInstaller 不支援 macOS 的 onefile `.app`，所以 macOS 發布 onedir 的 `Tablade.app` 加上 onefile 的命令列執行檔。
- **GitHub Actions**：在 `windows-latest` 和 `macos-latest` 上建置 onefile 與 onedir，上傳 artifacts；推 tag 時建立 Release。打包、自我檢測、壓縮都寫在 `scripts/build.py`，本機與 CI 共用。
- **M0 本機實測（macOS arm64）**：onefile 65MB、每次啟動約 4 秒（要先解壓縮）；onedir 191MB（其中 Polars runtime 佔 148MB）、壓縮後 62MB、啟動約 1.5 秒。

---

## 11. 真實資料防護（最高優先）

本 repo **從第一天就是公開的**，所以以下規則沒有例外。

1. **`.gitignore`** 預設排除所有資料檔：
   ```gitignore
   *.csv
   *.tsv
   *.txt
   *.xlsx
   *.xls
   *.xlsm
   *.parquet
   *.db
   *.sqlite
   *.duckdb
   *.manifest.json
   recipes/
   data/
   output/
   # 例外：只允許範例 recipe 與文件
   !examples/**/*.recipe.json
   !docs/**/*.txt
   !requirements*.txt
   ```
2. **pre-commit hook**（`scripts/check_no_data.py`）：擋下以下情況：
   - 副檔名屬於資料檔的檔案（白名單路徑除外）
   - 超過 1MB 的檔案
   - 疑似身分證字號（`[A-Z][12]\d{8}` 且通過檢查碼驗證）、疑似統一編號（8 碼數字並出現在「統編」附近）的內容
   - 本機私有封鎖清單 `.tablade-denylist`（不進版控）裡列出的字詞，例如人名、公司名稱、專案代號
3. **測試資料一律由程式產生**：`scripts/gen_fixtures.py` 在測試時產生假資料，產生出來的檔案不 commit。
4. **程式不得有任何對外連線**（不做遙測、不自動更新、不下載擴充）。CI 加一道檢查（`scripts/check_code_policy.py`），禁止 import `requests`、`urllib.request`、`httpx` 等網路套件，也禁止 `eval` / `exec`。
5. README 明確寫出：「本 repo 不含、也不得含任何真實資料」。
6. issue、PR、commit 訊息同樣不得貼上真實資料或真實檔名；回報問題時用 `gen_fixtures.py` 產生的假資料重現。

---

## 12. Repo 結構

```
tablade/
├── README.md
├── DESIGN.md                  # 本文件
├── CLAUDE.md                  # 給 AI coding 的規則（見 §14）
├── LICENSE                    # MIT
├── pyproject.toml             # 固定版本：polars==2.1.*、pywebview、fastexcel、pydantic
├── uv.lock
├── .gitignore
├── .pre-commit-config.yaml
├── src/tablade/
│   ├── __init__.py
│   ├── app.py                 # pywebview 進入點
│   ├── api.py                 # JS bridge
│   ├── cli.py                 # tablade run recipe.json
│   ├── fixtures.py            # 假資料產生器（測試、selftest 共用）
│   ├── recipe/
│   │   ├── schema.py          # pydantic models
│   │   └── io.py              # 讀寫 recipe、計算雜湊
│   ├── ingest/
│   │   ├── encoding.py
│   │   ├── text_lines.py
│   │   ├── csv_reader.py
│   │   └── excel_reader.py
│   ├── engine/
│   │   ├── compiler.py        # recipe → LazyFrame
│   │   ├── preview.py         # head + 快取
│   │   └── warnings.py
│   ├── steps/                 # 每種步驟一個檔案，註冊到 registry
│   │   ├── registry.py
│   │   ├── remove_rows.py
│   │   ├── split.py
│   │   ├── fill_down.py
│   │   ├── classify.py
│   │   ├── parse_tw.py        # 民國日期、括號負數、全形半形
│   │   └── ...
│   └── export/
│       ├── parquet.py
│       └── manifest.py
├── frontend/
│   ├── index.html
│   ├── app.js
│   └── style.css
├── examples/
│   └── sales_report.recipe.json  # 搭配 gen_fixtures 產生的假資料
├── scripts/
│   ├── gen_fixtures.py
│   ├── check_no_data.py
│   ├── check_code_policy.py
│   └── build.py               # 打包 + 自我檢測 + 壓縮（本機與 CI 共用）
├── tests/
│   ├── test_ingest.py
│   ├── test_steps_*.py
│   └── test_golden.py         # recipe + 假資料 → 預期輸出
├── tablade.spec               # PyInstaller 設定
└── .github/workflows/
    ├── test.yml
    └── build.yml
```

---

## 13. 里程碑

| 階段 | 內容 | 完成標準 |
|---|---|---|
| **M0 環境驗證** | 最小 pywebview + Polars 程式，用 onefile 和 onedir 各打包一次 | **在目標 Windows 筆電（權限受管控的公司電腦）上能開出視窗，並讀一個 CSV 寫出 Parquet** |
| M1 引擎 | recipe schema、ingest（text_lines / csv）、compiler、CLI、MVP 中的 8 種步驟 | `tablade run examples/sales_report.recipe.json` 產出正確結果，測試全過 |
| M2 UI | 步驟清單（新增、排序、停用、刪除）、預覽表格、參數表單 | 不寫 JSON，也能在 UI 完成範例整理流程 |
| M3 台灣格式與留痕 | parse_number、parse_date（民國）、trim_clean、manifest | 輸出附 manifest，警告附行號 |
| M4 多檔與 Excel | glob、diagonal concat、excel reader、classify_rows、extract_regex | 分段報表的範例可以完整攤平 |
| M5 進階步驟 | join、group_by、unpivot、pivot、dedupe、匯出 Python 腳本 | — |

**M0 要最先做**：它決定單一執行檔這條路走不走得通。

---

## 14. 給 AI coding 的規則（放進 CLAUDE.md）

- **永遠不要建立、commit 或要求任何真實資料**；測試資料只能由 `scripts/gen_fixtures.py` 產生。
- 使用 Polars **2.x 的 Python API**。不使用已移除的 API（例如 `melt` 改用 `unpivot`）；不確定時先查官方文件。
- 新增步驟時，必須同時完成 §7.3 的五件事（參數 model、apply、describe、infer_schema 視需要、測試）。
- 步驟 `apply()` 必須是純函式：不讀寫檔案、不 print、不碰 UI。
- 依賴列順序的操作，一律先依 `__source_file, __line_no` 排序。
- 不使用 `eval` / `exec`；不加入任何網路連線程式碼。
- 依賴版本固定在 `pyproject.toml`，不要擅自升級。
- 每次修改都要跑 `pytest` 和 `scripts/check_no_data.py`。

---

## 15. 待決事項

- [ ] 正式名稱：Tablade（首推）／Helvetab／Bladeframe；中文名可以叫「瑞刃」
- [ ] 前端要用純 JS，還是 Vite + 框架？預覽表格要用哪個虛擬捲動元件？（要能離線打包、授權寬鬆）
- [ ] 第一版的資料量目標（單檔最大幾 GB／幾千萬列），以決定是否需要提早引入 DuckDB
- [ ] recipe 預設存放位置（跟資料放在一起，或放在使用者指定的資料夾）
- [ ] 是否需要「每一步顯示等效的 Polars 程式碼」給檢查的人看
