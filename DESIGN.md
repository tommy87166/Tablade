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
| 資料不可上雲 | 全部本機運算，不依賴執行時下載任何擴充；唯一的對外連線是啟動時向 GitHub 查最新版號（不送出資料、不下載） |
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
| 前端 | **React 19 + TypeScript + Vite**，UI 元件用 **shadcn/ui**（Radix + Tailwind CSS v4） | build 成單一 HTML 檔，以字串載入，不需要伺服器（見 §16） |
| 預覽表格 | TanStack Virtual（M2 引入） | 列與欄都能虛擬捲動、MIT、約 20 KB |
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
│  frontend/（React + shadcn/ui，build 成單一 HTML）                │
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

- `inputs[].files` 是**檔案清單**：每一項是相對於 recipe 資料夾的路徑或絕對路徑，一律用 `/`，可以含 `*`（glob）。依清單順序讀取，同一項裡依檔名排序，同一個實際的檔只讀一次。
- UI 的「瀏覽檔案」：recipe 資料夾裡（含子資料夾）的檔存相對路徑，其他位置存絕對路徑（用 `is_relative_to` 判斷，Windows 跨磁碟也不會出錯）。程式不會自己產生 `*`；需要時手動輸入路徑加入清單。另存到別的資料夾時，每一項依新位置重新決定相對或絕對。
- 每個來源檔各自掃描成一個 LazyFrame（`scan_parts()`），欄位對齊成同一組：欄位不一致時缺的欄補 null（效果同 `diagonal_relaxed`）。
- 每一列都帶 `__source_file`（recipe 資料夾裡的檔是相對路徑，其他是整理過的絕對路徑）。各檔分開跑步驟，到「合併來源檔」或寫出前才依清單順序接起來（§8）。

---

## 6. Recipe JSON 格式（v1 草案）

```json
{
  "tablade_recipe": "1.0",
  "name": "明細整理範例",
  "inputs": [
    {
      "id": "src",
      "files": ["data/明細_*.csv"],
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
    { "id": "s5", "type": "parse_number", "columns": ["金額"], "kind": "decimal", "scale": 2 },
    { "id": "s6", "type": "parse_date", "columns": ["日期"], "pattern": "slash", "calendar": "roc" }
  ],
  "output": { "format": "parquet", "keep_system_columns": true }
}
```

**規則**

- `tablade_recipe` 是 recipe 格式的版本號（`MAJOR.MINOR`，規則見 §17.6），AI skill 共用同一個版本號。
- 每個步驟都有唯一的 `id`，並可加上 `"enabled": false` 暫時停用。
- recipe **不記輸出位置**：視窗裡按「處理並匯出資料」時才用存檔對話框選位置；命令列 `tablade run` 用 `-o` 指定，預設是 recipe 旁邊同名的 `.parquet`。
- **依資料決定結構的步驟**（提升標題列、拆成幾欄、樞紐），在**建立步驟的當下**先掃一次資料，把欄名和欄數寫死在 recipe 裡。之後套用到新資料時，若出現更多段數或新的欄位，產生**警告**，不默默增減欄位。
- recipe 本身可能含有敏感資訊（例如篩選條件裡的人名或公司名稱），所以**真實 recipe 也不得 commit**，repo 只放範例 recipe。

---

## 7. 步驟目錄

### 7.1 MVP（第一版）

已完成：`remove_top_rows`、`remove_bottom_rows`、`remove_blank_rows`、`promote_headers`、`split_delimited`、`split_column`、`fill_down`、`remove_columns`、`select_columns`、`rename_columns`、`trim_clean`、`replace_values`、`parse_number`、`parse_date`、`filter`、`merge_files`。

| 類型 | 說明 | Polars 實作方向 |
|---|---|---|
| `remove_top_rows` | 移除前 N 列 | 每個來源檔各自 `slice(n)` |
| `remove_bottom_rows` | 移除最後 N 列 | 每個來源檔先數列數，再 `head(列數 − n)` |
| `remove_blank_rows` | 移除空白列 | 依全部欄位都是 null 或空字串篩選 |
| `promote_headers` | 指定某一列當欄名 | 建立步驟時讀取該列內容，寫死在 recipe；執行時每個來源檔只讀開頭一列比對欄名 |
| `split_delimited` | 把 `line` 依分隔符號拆成多欄（處理引號） | 用 Python `csv` 解析，或 `str.split` 加上引號處理 |
| `split_column` | 某一欄依分隔符號再拆 | `str.split_exact(sep, n)` 後 `unnest` |
| `fill_down` | 向下填滿 | 每個來源檔各自 `forward_fill()`（各檔分開跑，不會填到別的檔） |
| `filter` | 條件篩選（等於、包含、開頭是、正規表示式、空值、數值比較） | `filter(expr)`，條件用結構化參數組成，不接受任意字串運算式 |
| `merge_files` | 依指定順序把所有來源檔接成一份；`__source_file` 改成同一個名稱、`__line_no` 從 1 重編，之後「前 N 列」等步驟改對整份計算 | 合併型步驟（`combine()`）：照指定順序把各檔的流水線 `concat` 起來，每個檔只讀一次 |
| `remove_columns` | 刪除欄位：依名稱或依位置（第幾個資料欄），可以多選 | 欄位效果 `drop` 算出剩下的欄，`select`；欄不存在、位置超出範圍、刪光所有資料欄都是錯誤 |
| `select_columns` | 保留和排序欄位：只留下列出的欄，依清單順序排列 | 欄位效果 `select`，`select`；欄不存在、重複都是錯誤 |
| `rename_columns` | 改欄名：`{舊名: 新名}`，只列要改的欄 | 欄位效果 `rename`，`rename`；改完後不能重複（可以互換）、新名不能以 `__` 開頭 |
| `trim_clean` | 清理文字：去掉前後空白（含全形空白）、全形轉半形（預設只轉英數字與全形空白，中文標點不動；可選全部轉 NFKC）、移除看不見的字元（零寬空白、BOM、Tab 以外的控制字元）、合併中間的空白 | 全用原生運算：`str.replace_all(r"[\p{Cf}[\p{Cc}--\t]]")`、`str.replace_many`（英數字對照表）或 `str.normalize("NFKC")`、`str.strip_chars`。不用 `map_batches`（會違反串流規則） |
| `replace_values` | 取代值：一個步驟可以列很多組「找 → 換成」，依序取代；部分相符、整格相符或正規表示式（換成可以用 `$1`） | `str.replace_all(literal=…)`；整格相符用 `when(col == find)`；正規表示式在驗證時就編譯一次 |
| `parse_number` | 文字轉數字：千分位、括號負數 `(1,000)`、尾端負號 `1,000-`；精確小數（`Decimal`，指定位數、四捨五入）或整數 | 正規化字串後 `cast(Decimal(38, 18), strict=False)`，再 `round(mode="half_away_from_zero")`（直接 cast 是銀行家捨入）；原本有值、轉完是空值的列產生附行號的警告 |
| `parse_date` | 文字轉日期。**不自動判斷**：一定要先選樣式，再選民國年或西元年。樣式各是一個獨立的正規表示式（年/月/日、年-月-日、年.月.日、年月日、連寫），也可以自訂；正規表示式要剛好三個群組，依序是年、月、日 | `str.extract` 取出三個群組；民國年 1～3 位數、加 1911，西元年要 4 位數，位數不對算失敗（不會變成西元 115 年）；`str.to_date(strict=False)`（不能用 `pl.date()`：不存在的日期會讓整個執行失敗）；失敗的列產生附行號的警告 |
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
6. 欄位效果宣告：這一步對欄位做了什麼（見 §17.5），驗證器不用資料就能檢查欄位引用
7. 範例：輸入假資料、參數、預期輸出；同時是單元測試，也會寫進 AI skill 的步驟目錄
8. 給 AI 的說明：適用情境與常見誤用

---

## 8. 預覽與執行

- **讀檔**（`ingest/sources.py`）：不把整個檔案讀進記憶體。以 1 MB 分塊讀取，同時算 SHA-256、檢查是否為 UTF-8；UTF-8 檔直接交給 Polars 的 `scan_lines` / `scan_csv`（lazy、可串流），cp950 等其他編碼（或有單獨 `\r` 換行）的檔案先分塊轉成 UTF-8 暫存檔再掃描。分塊不用更大：16 MB 的區塊解碼後釋放的記憶體，macOS 的配置器會留著不還，每個程序會長到幾百 MB 到 1.5 GB，1 MB 時固定在幾十 MB、速度一樣。暫存檔依「路徑＋大小＋修改時間＋編碼」快取，**關閉視窗時刪除**（macOS 關視窗時不一定會跑 Python 的結束程序，所以不能只靠 atexit）。暫存資料夾裡有一個鎖檔，程式開著時一直鎖住；啟動時把沒有人鎖著的 `tablade-stage-*` 資料夾（上次當掉或被強制結束時留下的轉碼副本）清掉，另一個開著的 Tablade 正在用的不會動。**需要約原檔 1.5 倍的暫存空間。**
- **平行轉碼**（`ingest/transcode.py`，只用標準函式庫）：64 MB 以上要轉碼的檔，在換行後面切成約 32 MB 的段，交給多個工作程序（`ProcessPoolExecutor`、spawn；CPU 核心數減 1，最多 8 個）同時轉，再依序接起來。cp950、Big5-HKSCS、UTF-8 的多位元組字元裡都不會出現 `0x0A`，`\n` 後面一定是一個字元的開頭，所以接起來的結果和單核心完全相同；`\r\n` 也不會被切開。SHA-256 一定要從頭讀到尾，由主程序另一條執行緒同時算。解碼錯誤依檔案順序回報第一個（和單核心相同的訊息）。開不了工作程序（受管控的電腦、`__main__` 不是檔案等）就改用單核心，之後不再嘗試。打包後的 App 靠 `__main__.py` 的 `multiprocessing.freeze_support()` 讓工作程序從同一個執行檔啟動；自我檢測有一項「多核心轉碼」確認這件事：不管核心數多少都實際開 2 個工作程序轉碼一次（CI 的 Windows 只有 2 個核心，平常會用單核心，不這樣做就測不到），訊息裡另外寫這台電腦的大檔平常用幾個工作程序。
- **預覽**：輸入不超過 `SAMPLE_THRESHOLD`（20 萬行）時整份讀進記憶體，每一步的列數是精確值；超過時只用**每個來源檔的前 10 萬行與最後 1,000 行**當樣本（一次串流掃描取得），步驟的列數標示為樣本的。結尾的頁尾、合計也在樣本裡，「移除後 N 列」的預覽仍然正確。
- **快取**：以「輸入檔指紋＋前 k 步的 recipe 雜湊」當 key，快取每一步的預覽結果。修改第 k 步時，只需重算第 k 步之後的步驟。
- **列順序**：讀檔時就依 `__source_file, __line_no` 排好，filter / with_columns 會保持順序，所以**步驟不排序**（排序在串流下會把整份資料放進記憶體）。Polars 2.x 的串流引擎在 join / group_by / unpivot 之後不保證順序，M5 加入這些步驟時要在步驟內恢復順序。
- **各檔分開處理**：資料在引擎裡是一串 part（每個來源檔一個 LazyFrame），每個檔各自一條流水線，到「合併來源檔」（`combines = True` 的步驟，`combine(parts)`）或寫出前才依序接起來。`per_file = True` 的步驟 `apply()` 一次只收到一個 part，不需要把每個檔的資訊套回每一列；需要整檔資訊（列數、第一列）時對這個 part 用 `collect_small()`。預覽（資料在記憶體裡）依 `__source_file` 把資料切成 parts，一列一列的步驟直接套在整張表上。以後要看整份資料的步驟（group_by、去重、排序、join）要做成合併型步驟。
- **串流規則**（新增步驟時必須遵守）：不用 window 函式（`.over()`）與 `replace_strict`，串流引擎不支援，會退回整份放進記憶體。`split_delimited` 用 Polars 原生正規表示式拆欄，不呼叫 Python。
- **完整執行**（`engine/run.py`）：小資料整份讀進記憶體、每步 `collect()`（`eager`）；大資料整條 lazy 查詢用串流引擎寫成暫存 Parquet，再寫出最後結果（`stream`）。試跑（`validate --run`、錯誤報告）在大資料時用樣本（`sample`）。
  - 警告：步驟用 `warning_checks()` 宣告「輸出上哪些列要警告」，可以引用自己留下的 `__w_<步驟id>_*` 暫存欄位。eager / sample 模式每一步收集一次；stream 模式暫存欄位一路保留到最後才收集，所以**只算最後還留著的列**。
  - stream 模式不提供每一步的列數（要多跑好幾遍資料）；每一步的列數看預覽或試跑。
  - **進度**（`engine/progress.py`）：Polars 的串流引擎執行中不會告訴 Python 做到哪裡。所以讀檔時每一列多一個暫存的位置欄（在所有輸入裡是第幾行），暫存結果用 `pl.PartitionBy(max_rows_per_file=…)` 分成大約 100 個檔寫（每 1% 一個），另一條執行緒每 0.2 秒看哪些檔寫完了、讀出位置欄，每個來源檔取看過的最大位置加起來，就是處理到第幾行（合併來源檔改了順序也算得對）。資料流裡完全沒有 Python：執行緒慢了只會讓進度更新得少一點。不用 `sink_batches` 把資料交回 Python：實測 Python 每批只要慢 1 毫秒，整個執行就慢一倍、記憶體從 1.4 GB 變成 5 GB 以上。實測（2 GB、3,678 萬列）：分檔寫與寫成一個檔一樣快（5.6 秒）、記憶體相同，約 85 次更新、間隔最多 0.3 秒，輸出逐列相同；位置欄約多 2% 的時間。進度列另外顯示這次動作已經過的時間；「寫出 Parquet」這一段（約占 15%）沒有百分比。
  - 實測（macOS arm64）：5 GB UTF-8 文字檔（8,560 萬行）開啟並產生預覽 20 秒、峰值記憶體 1.9 GB；串流匯出 62 秒、峰值 2.5 GB（各檔分開處理之前測的）。各檔分開處理後（範例 Recipe、串流執行）：2 個 1.5 GB 的檔 29 秒、峰值 2.4 GB（之前 51 秒、3.2 GB）；200 個 10 MB 的檔 23 秒（之前 57 秒）；1000 個 100 KB 的檔 15 秒（之前 106 秒）；19 個 10 MB 的檔合併並改順序 2 秒（之前 31 秒）。很多個檔時峰值記憶體略高（200 個檔 1.3 GB，之前 1.1 GB），約 100 個檔以上就持平。2 GB cp950 檔轉碼：單核心 9 秒（約 230 MB/秒），平行轉碼（10 核心、8 個工作程序）3 秒；記憶體主程序約 70 MB、每個工作程序約 70 MB，不隨檔案大小成長（改成 1 MB 分塊之前：單核心 1.5 GB、平行時每個工作程序 800 MB）。改寫前 1 GB 檔就要 10 GB 記憶體。
- **錯誤與警告**：轉型失敗、段數超出預期、新出現的欄位等等，集中顯示在 UI 的警告區，每一則都附上來源檔名和行號。

---

## 9. 輸出與可追溯紀錄

輸出 `xxx.parquet` 時，在旁邊寫一份 `xxx.manifest.json`（處理紀錄，`export/manifest.py`）。「輸出資料」設定可以關掉（Recipe 的 `output.manifest`，預設開）；只試跑、不寫出檔案時不寫。

```json
{
  "tablade_manifest": "1.0",
  "tool": { "name": "tablade", "version": "0.1.0", "polars": "2.1.0" },
  "run_at": "2026-11-17T10:00:00+08:00",
  "mode": "eager",
  "recipe": { "name": "門市銷售明細整理", "sha256": "…", "content": { "…完整的 Recipe…": "" } },
  "inputs": [
    { "file": "data/明細_01.csv", "bytes": 123456, "sha256": "…", "encoding": "cp950", "rows": 120345 }
  ],
  "steps": [
    { "id": "s1", "type": "remove_top_rows", "description": "移除每個檔案的前 2 列", "enabled": true, "rows": 120341, "warning_count": 0 }
  ],
  "warnings": { "count": 1, "items": [ { "step": "s5", "source_file": "data/明細_01.csv", "line_no": 88, "message": "「金額」的「N/A」不是數字，已改成空值" } ] },
  "output": { "file": "明細.parquet", "rows": 120300, "columns": ["日期", "…"], "bytes": 456789, "sha256": "…" }
}
```

- `recipe.sha256` 是 `recipe.content` 以排序過的緊湊 JSON 算出來的；完整的 Recipe 一起放進去，日後可以重跑。
- `inputs[].sha256` 讀檔時就算好了（和檢查編碼同一次讀取）；`output.sha256` 寫完後再讀一次輸出檔算。
- 串流執行沒有每一步的列數（`rows` 是 `null`）；警告沿用執行報告（每項檢查最多 100 則），`count` 是總數。
- 先寫暫存檔再改名，不會留下半個檔案。自我檢測的「完整執行範例 Recipe」也檢查 manifest 的 SHA-256。
- 輸入檔的 SHA-256 加上 recipe 雜湊，可以證明「這份輸出是用這份 recipe，從這些原始檔產生的」。
- 每一步的列數變化可以直接拿來核對資料有沒有漏掉或重複。
- 另外提供「匯出成 Python 腳本」，把 recipe 轉成獨立可執行的 Polars 程式碼，方便檢查或重跑（第二階段）。

---

## 10. 打包與發布

- **PyInstaller `--onefile`** 為主；**`--onedir`**（壓成 zip，解壓即用）為備案。
  - 單一執行檔每次啟動會先解壓縮到暫存資料夾（Windows 的 `%TEMP%\_MEI…`），可能比較慢、可能被防毒軟體誤判，也可能被 AppLocker 擋。
  - 實測參考：Linux 上 Python 3.13 + Polars 2.1 的最小 onefile 約 64MB，啟動約 0.9 秒。加上 pywebview、fastexcel 和前端後，預估 70 到 100MB。
- 前端 build 好的 `frontend/dist/index.html` 以 `datas` 打包；程式內用 `sys._MEIPASS` 取得路徑。
- Windows：pywebview 使用 Edge WebView2（Win10/11 通常已內建）。
- macOS：未簽章，第一次開啟要按右鍵 →「打開」，或執行 `xattr -dr com.apple.quarantine`。PyInstaller 不支援 macOS 的 onefile `.app`，所以 macOS 只發布 `Tablade.app`（onedir 包成 App）。
- **發布的產物**：Windows 只發 onefile（`tablade.exe`），macOS 只發 `Tablade.app`，另外附 AI skill 的 zip。Windows 的 onedir 不發布，onefile 被擋時用 `scripts/build.py onedir` 另外打包。
- **GitHub Actions**：在 `windows-latest` 和 `macos-latest` 上跑 `scripts/build.py --gui-smoke`（打包、自我檢測、視窗 smoke test、壓縮，本機與 CI 共用），上傳 artifacts。推 `v*` tag 時：
  1. `scripts/check_release.py` 檢查 tag 和 `src/tablade/__init__.py` 的 `__version__` 一致，不一致就停止發版（否則新版的 App 會以為自己是舊版，一直顯示「有新版本」）。
  2. 建立 Release，附上兩個平台的 zip 與 `tablade skill` 產生的 AI skill zip。
- **發版流程**：改 `__version__`（例如 `0.2.0`）→ commit、push → `git tag v0.2.0 && git push origin v0.2.0`。
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
4. **程式不得有其他對外連線**（不做遙測、不自動更新、不下載擴充）。唯一例外是 `src/tablade/update.py`：啟動時對 GitHub API 發一個 GET，只讀最新 release 的版號；有新版時在起始頁顯示下載網址，不下載、不自動更新，不送出任何使用者資料；設定 `TABLADE_NO_UPDATE_CHECK=1` 就完全不連線。CI 加一道檢查（`scripts/check_code_policy.py`），除了這個檔案以外禁止 import `requests`、`urllib.request`、`httpx` 等網路套件，也禁止 `eval` / `exec`。
5. README 明確寫出：「本 repo 不含、也不得含任何真實資料」。
6. issue、PR、commit 訊息同樣不得貼上真實資料或真實檔名；回報問題時用 `gen_fixtures.py` 產生的假資料重現。

---

## 12. Repo 結構

```
tablade/
├── README.md                # 英文；README.zh-TW.md 為繁體中文
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
│   │   ├── schema.py          # pydantic models、RECIPE_VERSION、步驟的 discriminated union
│   │   ├── validator.py       # 只用標準函式庫的驗證器（Tablade 與 AI skill 共用）
│   │   ├── spec.py            # 從 registry 產生步驟規格；schema lock
│   │   └── recipe-schema.lock.json
│   ├── ingest/
│   │   ├── encoding.py
│   │   ├── text_lines.py
│   │   ├── csv_reader.py
│   │   ├── sources.py         # glob、轉碼、讀取、多檔合併
│   │   ├── transcode.py       # 轉碼核心（只用標準函式庫，平行轉碼的工作程序也用）
│   │   └── excel_reader.py    # M4
│   ├── engine/
│   │   ├── run.py             # 依序套用步驟、每步列數、附行號的警告、寫出 Parquet
│   │   ├── progress.py        # 串流執行的進度：位置欄、分檔寫暫存、看寫完幾個檔
│   │   ├── check.py           # 驗證器 → pydantic → 選擇性試跑
│   │   └── preview.py         # M2：head + 快取
│   ├── steps/                 # 每類步驟一個模組，以 @register 登錄；steps/__init__.py 的匯入順序就是顯示順序
│   │   ├── base.py            # Step 基底類別、Example、WarningCheck、欄位型別
│   │   ├── registry.py
│   │   ├── remove_rows.py     # remove_top_rows、remove_bottom_rows、remove_blank_rows
│   │   ├── split.py           # split_delimited、split_column
│   │   ├── headers.py         # promote_headers
│   │   ├── fill_down.py
│   │   ├── filter.py
│   │   ├── classify.py        # M4
│   │   ├── parse_tw.py        # M3：民國日期、括號負數、全形半形
│   │   └── ...
│   └── export/
│       ├── parquet.py
│       └── manifest.py
├── frontend/                  # Vite + React + TypeScript + shadcn/ui（見 §16）
│   ├── package.json / package-lock.json
│   ├── vite.config.ts         # 單一檔案輸出、build 時注入 CSP
│   ├── components.json        # shadcn/ui 設定
│   └── src/
│       ├── main.tsx / App.tsx
│       ├── bridge.ts          # 與 api.py 對應的型別與取得方式
│       ├── mock.ts            # 開發用的假 API（只在 dev 載入）
│       ├── components/ui/     # shadcn/ui 元件（npx shadcn@latest add 產生）
│       └── styles/shadcn-tailwind.css
├── assets/
│   └── icon.svg               # 圖示原始檔（置中表格＋🔪）；icon.ico / icon.icns / icon.png 由 make_icons.py 產生
├── examples/
│   ├── sales_detail.recipe.json  # M1：凌亂的匯出檔（用到 8 種步驟）
│   └── sales_report.recipe.json  # M4：分段報表
├── scripts/
│   ├── gen_fixtures.py
│   ├── check_no_data.py
│   ├── check_code_policy.py
│   ├── build.py               # 打包 + 自我檢測 + 壓縮（本機與 CI 共用）
│   ├── update_schema_lock.py  # 升 RECIPE_VERSION 後更新 schema lock
│   └── make_icons.py          # assets/icon.svg → ico / icns / png
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
| M1 引擎 | recipe schema（版本 `MAJOR.MINOR`、schema lock）、registry（含 §7.3 的 8 件事）、ingest（text_lines / csv）、compiler、CLI（`run` / `validate` / `schema`）、MVP 中的 8 種步驟 | `tablade run examples/sales_detail.recipe.json` 產出正確結果，測試全過（✅ 完成） |
| M1.5 AI skill | probe skill 實測沙箱、`tablade profile`、skill 產生器、單一檔案驗證器（§17） | Copilot 載入 skill 後，能依描述寫出通過驗證的範例 recipe（程式已完成，等 Copilot 實測） |
| M2 UI | 步驟清單（新增、排序、停用、刪除）、預覽表格、參數表單、匯入 recipe（版本檢查、錯誤報告）、下載 AI skill | 不寫 JSON，也能在 UI 完成範例整理流程（✅ 完成） |
| M3 台灣格式與留痕 | parse_number、parse_date（民國）、trim_clean、replace_values、remove_columns、select_columns、rename_columns、型別分頁、manifest | 輸出附 manifest，警告附行號（✅ 完成） |
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
- 不使用 `eval` / `exec`；除了 `update.py` 查版號以外，不加入任何網路連線程式碼。
- 依賴版本固定在 `pyproject.toml`，不要擅自升級。
- 每次修改都要跑 `pytest` 和 `scripts/check_no_data.py`。

---

## 15. 待決事項

- [ ] 正式名稱：Tablade（首推）／Helvetab／Bladeframe；中文名可以叫「瑞刃」
- [x] 前端框架與預覽表格元件：React + Vite + TypeScript + shadcn/ui，表格用 TanStack Virtual（見 §16）
- [ ] 第一版的資料量目標（單檔最大幾 GB／幾千萬列），以決定是否需要提早引入 DuckDB
- [ ] recipe 預設存放位置（跟資料放在一起，或放在使用者指定的資料夾）
- [ ] 是否需要「每一步顯示等效的 Polars 程式碼」給檢查的人看
- [ ] Copilot 沙箱的 Python 版本、skill 的 script 能否讀取聊天中上傳的檔案（用 probe skill 實測，見 §17.3）

---

## 16. 前端架構

### 16.1 硬限制

| 限制 | 影響 |
|---|---|
| 不開 HTTP server，HTML 以字串交給 WebView2（`NavigateToString`） | **整份 HTML 必須小於 2 MB**；`scripts/build.py` 與測試都會檢查 |
| Chromium 在 `file://` 下會擋 ES module script | 不能直接用 `file://` 載入 Vite 的多檔產物，必須合併成單一 HTML（`vite-plugin-singlefile`） |
| pywebview 的 JS bridge 內部用到 `eval` | CSP 必須保留 `'unsafe-eval'` |
| 完全離線 | 不載入外部字型（用系統字型，含繁中）、圖示以 data URI 內嵌 |

### 16.2 技術選型

- **React 19 + TypeScript + Vite**：元件化、有型別、AI 輔助支援最好；`npm run build` 先跑 `tsc -b` 型別檢查。
- **shadcn/ui**（Radix 為底層、Nova 主題、Tailwind CSS v4、lucide 圖示）：元件原始碼直接放在 `src/components/ui/`，需要時用 `npx shadcn@latest add <元件>` 加入。
  - 不把 shadcn CLI 裝成依賴（它會帶進數百個套件和安全性警告）。它唯一需要的 `tailwind.css` 已複製到 `src/styles/shadcn-tailwind.css`（MIT）。
  - 主色：圖示用 `#fd5108`；按鈕底色用深一階的 `#d03f00`（白字對比 4.7:1），深色模式用 `#ff7a3d`。
- **lint**：oxlint，開啟 `react/no-danger`（禁止 `dangerouslySetInnerHTML`）。
- **真正會出貨的套件**只有 react、react-dom、radix-ui、lucide-react、class-variance-authority、cn；其餘都是 build 工具。

### 16.3 載入方式

- 正式版：`npm run build` → `frontend/dist/index.html`（JS、CSS、圖示全部內嵌，目前約 300 KB）→ PyInstaller 打包 → `app.py` 讀成字串交給 `create_window(html=...)`。
- CSP 只在 build 時注入（`vite.config.ts`），dev server 才能用 HMR。
- 開發：`npm --prefix frontend run dev` 啟動 Vite，再用 `TABLADE_DEV_URL=http://localhost:5173 uv run tablade` 讓視窗載入 dev server（只接受 localhost）。也可以直接用一般瀏覽器打開 dev server，`bridge.ts` 會在找不到 pywebview 時改用 `mock.ts` 的假 API。

### 16.4 狀態與溝通（M1 起）

- **狀態放在 Python**：recipe、目前選到的步驟、預覽快取、警告、undo / redo 都由 Python 的 session 管理。前端呼叫指令（`add_step`、`update_step`、`move_step`、`toggle_step`、`delete_step`、`select_step`），每次拿回一份新的畫面狀態：步驟清單（含 `describe()`、列數變化、警告數）與目前步驟的參數。
- **最新的結果才算數**：pywebview 每次 JS 呼叫都在新的 thread 執行。每個預覽請求帶流水號，Python 只回傳最新一次的結果，前端也丟掉過時的回應。
- **表格分頁取資料**：`get_rows(step_id, offset, limit)` 只回傳畫面看得到的那一段，用欄式陣列（`{columns, data: [[...], ...]}`）傳輸。
- **長時間工作**：完整匯出、掃描大檔在 Python 背景執行，進度寫進 pywebview 的 `window.state`，前端監聽 `pywebview.state` 的 `change` 事件。
- **檔案只用代號**：開檔對話框由 Python 開啟，前端只拿到檔案代號，不能要求 Python 讀任意路徑。
- **M2 實作**（`src/tablade/session.py`）：
  - 步驟以原始 dict 保存，參數還不合法的步驟也能留在清單裡；計算到第一個錯誤的步驟為止，預覽顯示它之前最後一個算得出來的結果（`preview.step` ≠ `preview.requested`）。
  - 每一步的結果依「input ＋ 到這一步為止的步驟」的雜湊快取；改第 k 步時，前面的步驟直接沿用。
  - 新增步驟時，各步驟的 `suggest(frame, context)` 依目前的資料給建議參數：判斷分隔符號（會處理引號）、從第一列抓欄名、帶入右鍵點選的欄位或值。
  - 存檔時去掉跟預設值相同的參數；另存到別的資料夾時，檔案清單依新位置重新決定相對或絕對路徑。
  - **開檔流程**：程式一開沒有 recipe，只能「新增 Recipe」（先跳存檔視窗命名，立刻寫出空的 recipe 檔）或「開啟 Recipe」。在「讀取資料」用「瀏覽檔案」或手動輸入路徑建立檔案清單；清單每一項都找得到檔時，才能按「讀取資料」設定裡的「載入資料」（載入後隱藏，設定變了才再出現）。資料**不會自動讀**：改了檔案清單、讀取方式、編碼，或檔案在磁碟上被改過，預覽就清空，要再按一次「載入資料」。工具列的主要按鈕是「▶ 處理並匯出資料」。
  - 點警告時用 `find_row` 找到那一列在預覽中的位置並捲過去。
- **前端開發**：`scripts/gen_frontend_dev_data.py` 用假資料跑一次範例 recipe，產生 `frontend/src/dev/sample.json`（不進版控）；在一般瀏覽器開 dev server 時，`mock.ts` 用它模擬 API。

### 16.5 參數表單從 pydantic schema 自動產生

每種步驟的參數 model（§7.3）就是表單的唯一來源，前端沒有任何步驟專屬的表單程式碼。

1. Python 用 `Model.model_json_schema()` 產生 JSON Schema，連同目前的參數值與「上一步的輸出欄位」一起交給前端。
2. 前端依 schema 的型別挑元件：

| JSON Schema | 元件 |
|---|---|
| `type: string` | Input |
| `type: integer` / `number` | 數字 Input |
| `type: boolean` | Switch |
| `enum`（來自 `Literal[...]`） | Select；選項文字用 `ui_labels`，選到的選項有 `ui_option_hints` 時顯示在下面（例如日期樣式的正規表示式） |
| `type: array` + `items: string` | 可增減的標籤輸入 |
| `ui: "column"` / `ui: "columns"` | 欄位選擇器（單選 / 多選），選項是上一步的輸出欄位 |
| `ui: "column_positions"` | 依位置勾選欄位（顯示「2 門市」，存的是第幾欄） |
| `ui: "column_order"` | 保留和排序：上面是保留的欄（上下移、移除），下面是沒保留的欄（勾選加到最後） |
| `ui: "rename_map"` | 改欄名：每個欄位一列，旁邊填新名稱（空白代表不改），存成 `{舊名: 新名}` |
| `type: array` + `items` 是物件的 `$ref` | 每一項一個框，依子 schema 畫欄位，可以新增、移除（例如取代值的「找 → 換成」） |
| `ui: "regex"` | 正規表示式輸入框＋即時測試 |
| `oneOf` + `discriminator` | 先選類型，再顯示對應的子表單（例如篩選條件「等於 / 包含 / 數值比較」） |

3. 欄位標題用 `Field(title=...)`、說明用 `Field(description=...)`、預設值用 Python 的預設值；`ui`、`ui_labels` 等提示放在 `json_schema_extra`。`ui_show_if: {"by": "position"}` 表示其他參數是這個值時才顯示這個欄位（例如刪除欄的名稱／位置、只有小數才有的小數位數）；因為修改而隱藏的欄位回到預設值。
4. **驗證只在 Python 做**：前端送出原始值，pydantic 驗證；錯誤的 `loc`（例如 `["delimiter"]`）對應回表單欄位顯示。前端不重複寫驗證規則。
5. 驗證通過後，Python 重算預覽並回傳 `describe()` 的一句話說明，步驟清單即時更新。

新增一種步驟只要寫 Python（參數 model、`apply()`、`describe()`、測試），表單自動出現。

### 16.6 畫面配置（M2）

- 左側：輸入檔案與步驟清單（拖曳排序、停用、刪除、每步的列數變化）。
- 中間：預覽表格。欄標題顯示型別與空值比例；在欄標題按右鍵可直接建立步驟（拆欄、轉數字…）。
- 右側：目前步驟的參數表單（§16.5）。
- 下方：兩個分頁。
  - 「警告」：每則附來源檔名與行號，點一下跳到該列。
  - 「型別」：目前預覽那一步每一欄的位置（和刪除欄的位置相同）、型別（中文＋Polars 型別）、空值、空白字串、最小～最大（數字與日期）、範例值。由 `session.column_info()` 算，只有打開這個分頁時才算；大檔時是預覽樣本的統計。

### 16.7 檢查

- `tests/test_app.py`：前端產物是單一檔案且小於 2 MB、CSP 是 `default-src 'none'`、`bridge.ts` 的方法與 `api.py` 一致、前端原始碼沒有 `innerHTML` / `dangerouslySetInnerHTML` / `fetch` / `WebSocket` / `eval` 等。
- CI 在 Windows / macOS / Linux 都先 `npm ci`、`npm run lint`、`npm run build`，再跑 pytest 與打包。

---

## 17. AI skill：讓 AI 助理撰寫 recipe

### 17.1 目標與流程

使用者在 AI 助理裡描述要怎麼整理資料，AI 讀資料、寫出 recipe，並用 skill 內的驗證器自我檢查；使用者再把 recipe 匯入 Tablade，由 Tablade 做最終驗證與試跑。

- 目標環境：**Microsoft 365 Copilot 的 Agent Builder**。skill 採開放的 Agent Skills 格式（`SKILL.md`），GitHub Copilot、Claude 等也能使用同一個套件。
- 資料：AI 服務若已獲准處理這些資料，可以直接讀取原始檔。大檔或 Big5 檔建議先用 `tablade profile` 產生精簡摘要（UTF-8：偵測到的編碼、行數、前 N 行原文、欄位與簡單統計）再上傳；`--mask` 可把數字與文字遮罩成 `9` / `a` / `〇`。

```
使用者描述 ＋ 上傳資料（或 tablade profile 摘要）
        ▼
Copilot（載入 Tablade skill）── 寫 recipe ──► scripts/validate_recipe.py（在沙箱內自我檢查）
        ▼ 使用者下載 recipe
Tablade 匯入 → 完整驗證 ＋ 用真實資料試跑 → 有問題就「複製錯誤報告」，貼回 Copilot 修正
```

### 17.2 skill 內容（build 時從 registry 自動產生）

```
tablade-recipe-skill-v1.4.zip
├── SKILL.md                     # frontmatter：name、description（含版本）；工作流程與規則
├── references/
│   ├── steps.md                 # 步驟目錄：用途、參數表、範例（自動產生）
│   ├── recipe.schema.json       # 完整 recipe JSON Schema（由 pydantic 產生）
│   └── examples/*.recipe.json   # 範例 recipe（搭配假資料）
└── scripts/
    └── validate_recipe.py       # 驗證器：單一檔案、只用 Python 標準函式庫
```

新增步驟放進 registry，下次 build 就會自動出現在 `steps.md`、schema 與驗證器裡，不用手改。

### 17.3 Agent Builder 的限制（Microsoft Learn，2026-11）

| 項目 | 上限 |
|---|---|
| 每個 agent 的 skill 數 | 8 |
| zip 大小 | 50 MB |
| 單一檔案 | 25 MB |
| 檔案數 | 350 |
| 目錄深度 | 3 |
| SKILL.md 指示 | 20,000 字元 |
| 允許的檔案 | 資源：`.md` `.json` `.csv` `.txt` `.yaml` 等；script：`.py` `.js` `.sh` 等 |
| 執行 script 的沙箱 | 沒有網路、不能安裝套件；預裝套件清單沒有公開 |

- 所以驗證器**只用 Python 標準函式庫**；build 與測試會檢查以上限制（字數、檔案數、深度、副檔名）。
- 文件沒有寫、需要實測：沙箱的 Python 版本、script 能不能讀取聊天中上傳的檔案。M1.5 先做一個 probe skill（只回報 Python 版本、可用套件、看得到哪些檔案），上傳到 Agent Builder 實測。
- 更新方式文件沒有寫：新版本要在 Agent Builder 移除舊 skill、再上傳新 zip。

### 17.4 驗證器

- **一份程式碼，兩個地方用**：`src/tablade/recipe/validator.py`（只用標準函式庫）同時給 Tablade（`tablade validate`、匯入 recipe）與 skill 使用；build 時把它和從 registry 產生的步驟規格（JSON）合併成單一檔案。
- **三層檢查**：
  1. 結構：版本號、步驟類型是否存在、參數型別 / 必填 / 列舉（依內嵌的 JSON Schema）
  2. 語意：步驟 id 不重複、欄位引用存在（依各步驟的欄位效果一步步推算）、需要寫死欄名的步驟有寫、`only_row_types` 引用的類型有定義
  3. 試跑（只在 Tablade）：用真實資料跑一次，回報每一步的列數、欄名、警告與行號
- **給 AI 看的輸出**：JSON，每個錯誤含 `path`（例如 `steps[3].params.column`）、`error`、`hint`（例如相近的欄位名稱）。
- **與 pydantic 一致**：測試用一組正確與錯誤的 recipe，確認 pydantic 與驗證器的結論相同。

### 17.5 欄位效果宣告

每種步驟用少數幾種標準形式宣告自己對欄位的影響，例如：

| 形式 | 例子 |
|---|---|
| 不變 | `remove_top_rows`、`filter`、`fill_down` |
| 新增 | `classify_rows` 新增 `__row_type` |
| 拆分 | `split_column`：移除 `column`，新增 `names` |
| 整組換掉 | `split_delimited`、`promote_headers`：換成參數裡寫死的欄名 |
| 刪除 / 選取 / 改名 | `remove_columns`（`drop`）、`select_columns`（`select`）、`rename_columns`（`rename`） |

Tablade 的 `output_columns()` 與驗證器都從這份宣告推算，所以不需要資料就能檢查「第 5 步引用的欄位在那之前存在嗎」。

### 17.6 版本號

- recipe：`"tablade_recipe": "MAJOR.MINOR"`。
  - minor：相容的變更（新增步驟、新增選用參數）
  - major：不相容的變更，附自動升級舊 recipe 的程式
- **skill 版本 = recipe 版本**（zip 檔名、SKILL.md、驗證器常數都標示）。App 版本另外計算，例如 Tablade 0.3.0 支援 recipe 1.4。
- **由 CI 把關**：repo 存一份 `recipe-schema.lock.json`（完整 schema 快照），測試重新產生後比對；步驟定義變了但版本沒升，CI 就失敗並提示該升 minor 還是 major。
- Tablade 開啟 recipe：版本比 App 新 → 提示更新 Tablade；比較舊 → 自動升級。
- 驗證器遇到不同 major 或比自己新的 minor → 提示改用對應版本的 skill。

### 17.7 下載與命令列

- `tablade skill [--out x.zip]`：產生 skill；`tablade skill --probe`：產生 probe skill（只回報 Python 版本、可用套件、看得到的檔名與大小，不讀檔案內容）。
- `tablade profile 檔案… [--lines N] [--mask]`：給 AI 助理看的資料檔摘要。
- 打包後的 App 只有編譯過的模組，所以 `validator.py` 原始碼另外以資料檔打包（`tablade.spec`）；`scripts/build.py` 會用打包後的 App 實際產生一次 skill 確認。

- 前端的說明選單提供「下載 Copilot skill（v1.4）」：Python 依目前的 registry 當場產生 zip，再開存檔對話框，所以下載到的 skill 一定與 App 版本一致。
- CI 也把 skill zip 附到 Release。
