# improve.md 的實作計畫

狀態：6 批都已完成。每一項的「為什麼」與已做的決定見 [../improve.md](../improve.md)，這份只寫怎麼做、順序與怎麼驗證。

## 順序

分成 6 批，每批各自一個 commit（照慣例，等說 commit 才 commit）。前一批是後一批的基礎：

| 批 | improve.md | 內容 | 依賴 |
|---|---|---|---|
| 1 | 第 1 項 | 串流匯出的「檢查警告」只跑一次、有進度 | — |
| 2 | 第 3、4 項 | 每個階段都能寫檔、檔名寫在輸出設定、輸出放 `output/`、處理紀錄放 `log/` | — |
| 3 | 第 2、6、7 項 | 工具列兩顆按鈕、不用先載入就能匯出、進度只在彈出視窗 | 2（匯出不再開存檔對話框） |
| 4 | 第 8 項 | 「這一步還算不出來」的顯示 | — |
| 5 | 第 9 項 | 抽樣模式／完整模式，匯出後顯示完整結果 | 2、3 |
| 6 | 第 5 項 | 自我診斷畫面「開啟暫存資料夾」 | — |

`RECIPE_VERSION` 維持 `"1.0"`（還沒發版）；第 2 批改了 schema，要重新產生 schema lock。

---

## 第 1 批：檢查警告只跑一次（`engine/run.py`）

狀態：已完成（實測見 improve.md 第 1 項）。另外把暫存 Parquet 的 row group 改成 8,192 列（`STAGED_ROW_GROUP`）：只照計畫做的話 2 GB 資料記憶體反而從 2.2 GB 升到 3.2 GB，原因是串流讀回暫存檔時每條執行緒一次解開一整個 row group。

**現況**：`_stream_steps` 在 `reshapes`／`combines` 的步驟前（以及有 `on_input` 警告的步驟前），用 `_warnings()` 對還沒跑過的整條流程查詢，每項警告條件跑 2 次（`pl.len()`、`head(limit)`），每次都從原始檔重跑。

**做法**：

1. 新函式 `_materialize(parts, last, folder, sources, progress, label)`：
   - 每個 part 加上 `PART` 欄（跟階段之間的暫存檔一樣），`concat_parts` 後 `sink_parquet(PartitionBy(...))` 寫到 `staged/stage-{k}-seg-{n}/`，用 `Watcher` 回報處理到第幾行（label 例如「階段「X」：執行（串流，第 1 段）」）。
   - 回傳讀回來的 parts：`pl.scan_parquet(...)` 依 `PART` 分開，名稱照原本的 part。暫存欄（`__w_`）、`POSITION` 都保留，後面的步驟才能接著用。
2. `_stream_steps` 改成：遇到需要先算警告的步驟，且 `pending` 裡有警告條件時（沒有警告條件就不寫暫存檔，維持一條流水線），先 `_materialize`，再對讀回來的暫存檔算 `pending` 的警告，後面的步驟從暫存檔接著接。
   - `on_input` 的檢查（結合沒對到的列）也用同一份暫存檔。
   - 寫暫存檔前要知道每一段的總列數才能算進度：第一段用 `sources` 的列數；後面的段落用上一段讀回來的列數（`select(pl.len())` 讀 parquet metadata，很快）。
3. `_warnings()`：數列數和取前幾筆合成一次查詢（`hits.select(...).head(limit)` ＋ `hits.select(pl.len())` 對 parquet 都只讀需要的欄）；有好幾項條件時送「檢查警告 2 / 3」的進度（`done`／`total` 用項數，`unit` 用新的 `"checks"`，前端顯示「第 2 / 3 項」）。
4. eager／sample 模式不變（資料本來就在記憶體裡）。

**驗證**：

- `tests/test_large.py`：
  - 既有的串流＝整份讀進記憶體測試都要過（結果、警告、行號、`__orig_*`）。
  - 新增：拆欄＋把第一列作為欄位標題（有警告）→ 合併來源檔 → 群組彙總（中間再一個有警告的步驟），兩種模式的結果與警告一樣。
  - 新增：結合沒對到的列的警告，兩種模式一樣。
  - 新增：用 monkeypatch 數 `_warnings` 裡實際 `collect` 的次數，確認不會再對原始檔重跑。
- 實測（scratchpad 的 `probe_warn.py`，2 個約 150 MB 的 cp950 假檔）：記下改前改後的時間，寫回 improve.md 第 1 項；再用幾 GB 的假資料量一次記憶體（應該跟改前差不多）。

---

## 第 2 批：每個階段都能寫檔、檔名在輸出設定、輸出放 `output/`、處理紀錄放 `log/`

狀態：已完成。跟計畫不同的地方：`execute()` 用 `outputs`（階段 → 位置）與 `log_dir`，另外保留 `output=`（只寫最後一個階段，測試與「完整跑一次」用）；檔名檢查在 `export/outputs.py` 的 `plan_outputs`（畫面與匯出共用）；要寫的檔先寫成暫存檔，整個執行成功才一起改名。

### Recipe 格式（`recipe/schema.py`、`recipe/validator.py`）

- `Stage` 新增 `output_file: str = ""`（title「輸出檔名」，description「只填檔名，存到 Recipe 所在資料夾底下的 output 資料夾」）。
  - 中間階段：有填就寫檔，空白就不寫（跟現在一樣是暫存的）。沒有開關，也不自動帶檔名。
  - 最後一個階段一定寫檔：空白時用預設檔名＝Recipe 名稱（`明細.recipe.json` → `output/明細.parquet`），舊的 Recipe 不用改。
  - 沒有 `.parquet` 結尾時自動補上（session 存的時候補；validator 只檢查補完的結果）。
- 頂層 `output`（格式、保留系統欄位、處理紀錄）維持，套用到每個寫出的檔。
- `validator.link_issues` 加檢查（只用標準函式庫）：
  - 檔名不能有路徑字元（`/ \ : * ? " < > |`）、不能是 `.` 開頭、不能是空白加副檔名。
  - 同一個 Recipe 裡（補完 `.parquet` 後，不分大小寫）不能重複；最後一個階段的預設檔名也算進去。
  - 寫出的位置（`output/檔名`）不能是任何一個階段的輸入檔（例如另一份 Recipe 讀 `output/` 裡的檔時，避免覆蓋輸入）；Recipe 本身不會在 `output/` 裡，不用另外檢查。
- `uv run python -c "from tablade.recipe.spec import write_lock; write_lock()"` 重新產生 lock。

### 引擎（`engine/run.py`、`export/manifest.py`）

- `execute(recipe, base_dir, *, out_dir=None, ...)`：取代現在的 `output`。寫出的位置是 `out_dir / "output" / 檔名`，處理紀錄是 `out_dir / "log" / 檔名.manifest.json`（`out_dir` 預設是 Recipe 所在的資料夾，CLI 可以改）；兩個資料夾不存在就建立。
- 每個要寫檔的階段：
  - eager：階段算完就寫（`write_parquet` 到暫存檔再 `replace`，跟現在一樣不會留下寫一半的檔）。
  - stream：中間階段本來就 sink 到 `staged/stage-{k}/`；要寫檔時再從那裡 `select(要保留的欄).sink_parquet(暫存檔)` → `replace`。最後一個階段照現在。
  - 已經存在就直接覆蓋。檔案被別的程式鎖住（Windows）時報「「結果.parquet」寫不進去：可能被別的程式開著」。
- `RunReport` 新增 `outputs: list[OutputReport]`（階段、路徑、列數、欄位、處理紀錄位置）；`rows`、`columns`、`output` 維持是最後一個階段的（相容現在的呼叫端）。
- 處理紀錄：
  - `manifest_path(out_dir, output)` → `out_dir / "log" / f"{output.stem}.manifest.json"`（`output/` 與 `log/` 是 Recipe 資料夾底下並排的兩個資料夾）。
  - 每個輸出檔一份，只記跟它有關的：這個階段和它往上引用到的階段（遞迴）的輸入檔、步驟、警告，以及這個輸出檔的 SHA-256。另外記 `stage`（寫出這個檔的階段）。
  - `MANIFEST_VERSION` 維持 `"1.0"`（還沒發版），內容多 `stage` 欄位。

### 畫面與 API（`session.py`、`api.py`、`SourcePanel.tsx`）

- `session.set_stage_output(index, file)`：改檔名（補 `.parquet`、驗證錯誤一樣在 state 的 issues 裡）。
- `session.export()` 不再收 `target`：寫到 Recipe 所在資料夾底下的 `output/`；回傳每個輸出檔。`export_suggestion()` 拿掉。
- `api.export_parquet()` 不開存檔對話框（還沒存過的新 Recipe 不會發生：新增 Recipe 時就選了位置）。
- `OutputPanel`：
  - 每個階段都顯示「輸出檔名」欄（只填檔名，後面固定顯示「.parquet」）：中間階段的提示是「填了才另外存成檔案」；最後一個階段的 placeholder 是預設檔名（Recipe 名稱）。
  - 檔名欄下面顯示寫出的位置（灰字，例如「output/明細.parquet」）與「已存在，匯出時會覆蓋」的提示。
  - 中間階段的說明改成「後面的階段用 @名稱 讀這個階段的結果；填了輸出檔名也會另外存成檔案」。
  - 處理紀錄開關的說明改成「在 log 資料夾寫一份 .manifest.json…」。
- 階段面板：有寫檔的階段在名稱旁加一個小的檔案圖示。

### CLI、skill、範例

- `tablade run recipe.json [--out-dir 資料夾]`：`-o/--output` 拿掉（輸出檔名寫在 Recipe 裡）；`output/`、`log/` 建在 `--out-dir` 底下（預設是 Recipe 所在的資料夾）；印出每個輸出檔與處理紀錄。
- `skill.py`：說明 `output_file`、處理紀錄在 `log/`。
- `examples/`：`sales_combined` 的「品項對照」填上輸出檔名當範例；其他維持預設檔名。`.gitignore` 已經有 `output/`，再加 `log/`（跑範例時會產生在 `examples/` 底下）。
- `selftest.py`：處理紀錄改到 `log/` 找。

**驗證**：

- `test_recipe.py`：檔名的各種錯誤（路徑字元、重複、寫出位置是輸入檔）、最後一個階段空白用預設檔名、中間階段空白不寫檔、補 `.parquet`。
- `test_large.py`：中間階段寫檔，stream＝eager（檔案內容一樣），後面的階段照樣讀得到；覆蓋已存在的檔。
- `test_manifest.py`：輸出在 `output/`、處理紀錄在 `log/`；每份只記自己上游的輸入與步驟。
- `test_session.py`：`export()` 寫到 Recipe 資料夾的 `output/`；改檔名的驗證錯誤。
- `test_app.py`：`API_METHODS` 與 `bridge.ts` 一致。
- 畫面：dev 預覽看輸出設定（中間階段、最後階段、檔名錯誤）。

---

## 第 3 批：工具列兩顆按鈕、不用先載入就能匯出、進度只在彈出視窗

狀態：已完成。跟計畫不同的地方：只有一個階段時也列出階段（那一列就是正式處理）；試跑完成那一列寫「沒有錯誤」；進度狀態推到 `window.state.run`（`stages`＋`phases`）。

### 按鈕（`App.tsx`、`SourcePanel.tsx`、`session.py`）

- 工具列：［載入資料及預覽］［處理並匯出資料］並排。
  - 「載入資料及預覽」＝現在的 `load_data()`（依序載入全部階段、讀樣本、算預覽）。可以按的條件：有任何讀實際檔的階段有檔案、清單上的檔都找得到，而且至少有一個階段還沒載入或有變更（全部都載入過就變灰，提示「已載入；檔案或設定變更後可以重新載入」）。
  - 「處理並匯出資料」可以按的條件：Recipe 已開啟、每個讀實際檔的階段都有檔案且都找得到、沒有驗證錯誤（`validate`，不讀資料）。**不用先載入**。
  - 不能按時，滑鼠移上去寫出原因與是哪幾個階段（state 新增 `can_export: {ok, reason}`、`can_load: {ok, reason}`，原因在 Python 算）。
- 「讀取資料」裡拿掉載入按鈕，只留檔案清單與狀態（「已載入」「還沒載入」「檔案或設定已變更」）。
- `session.require_loaded()` 拿掉；`export()` 不檢查有沒有載入。

### 匯出前先試跑（`engine/run.py`、`session.py`）

- `execute()` 的 sample 模式新增 `head`／`tail` 參數（預設還是 `SAMPLE_HEAD`／`SAMPLE_TAIL`）。
- `session.export()`：
  1. 讀檔準備（轉碼、數行數；有快取就跳過），進度顯示在彈出視窗的「準備」那一列。
  2. 試跑：`execute(..., write_output=False, mode="sample", head=TRIAL_HEAD, tail=0)`（`TRIAL_HEAD = 2_000`）。試跑不改動階段清單的狀態，而是更新「試跑」那一列（見下面的進度視窗）。有錯就停：彈出視窗顯示「第 2 個階段「文字明細」第 4 步：…」，並有「前往這一步」按鈕（切到那個階段、選那一步）。
  3. 正式跑（送階段狀態事件）。
- 已經按過「載入資料及預覽」、而且步驟都算得出來時，試跑可以跳過（預覽已經檢查過了）。

### 進度只在彈出視窗（`RunDialog.tsx`、`App.tsx`、`StagesBar.tsx`、`ui/dialog.tsx`）

- 所有會推送進度的動作（載入資料及預覽、處理並匯出資料、完整跑一次、重新從資料讀取）都開 `RunDialog`：
  - 最上面一列是目前在做什麼（「轉碼 明細_01.csv 120 / 300 MB（40%）」「試跑」「寫出 Parquet」），沒有階段在跑的時候也看得到。
  - 匯出時，階段清單上面多兩列「準備檔案」（轉碼、數行數）與「試跑（每個檔前 2,000 行）」，各自轉圈／打勾／紅叉；試跑時寫出「第 2 / 4 個階段：文字明細」；略過試跑時寫「已用預覽檢查過，略過」並打勾。事件格式：`StageTracker` 的 `stages` 之外多一個 `phases: [{name, status, detail}]`。
  - 「準備檔案」顯示已處理／總檔案數與目前的檔：「準備檔案　2 / 5 個檔：明細_02.csv 轉碼 120 / 300 MB（40%）」。總檔案數在開始前先展開每個階段的檔案清單（`resolve`，只看檔名不讀檔）算好；每準備好一個檔（`prepare`、Excel、Parquet）就更新一次（`phases` 的 `done`／`total`），目前那個檔的轉碼進度沿用現在的進度事件。
  - 「載入資料及預覽」沒有「準備檔案」這一列（讀檔跟著每個階段做），改在處理中的階段那一列顯示「讀檔 2 / 3 個檔：明細_02.csv 轉碼 …」，讀完再顯示「計算預覽」。
  - 下面是每個階段的狀態；只有一個階段、或這個動作不分階段時不顯示階段清單。
  - 匯出完留著當摘要（每個輸出檔的位置、列數、警告數），按「關閉」才收起來；其他動作成功時自動關掉。
- 背景不模糊：`DialogContent` 新增 `overlayClassName`，`RunDialog` 傳入不含 `backdrop-blur` 的樣式（背景仍然稍微變暗、不能點，因為 Python 正在忙）。其他對話框不變。
- 拿掉 `App.tsx` 的 `ProgressBar`（原畫面上方的進度列）。
- 拿掉 `StagesBar` 的 `runs`（階段面板上的轉圈／打勾）；`StageRunIcon` 只留在 `RunDialog`。

**驗證**：

- `test_session.py`：
  - 開 Recipe 後不載入直接 `export()` 寫得出檔，結果跟先載入再匯出一樣。
  - 某一步在真實資料上會出錯時，試跑就停下，不會跑正式的（monkeypatch 正式執行，確認沒被呼叫），錯誤指到正確的階段與步驟。
  - `can_export`／`can_load` 的各種原因。
- `test_large.py`：sample 模式的 `head`／`tail` 參數。
- 畫面：dev 預覽確認背景不模糊、上方沒有進度列、階段面板沒有轉圈；mock 的 `fakeExport` 加上「準備」「試跑」兩段。
- 打包後實際開 App 用大一點的假資料跑一次（打包前先確認 App 沒有開著）。

---

## 第 4 批：「這一步還算不出來」的顯示

狀態：已完成（dev 資料：`uv run python scripts/gen_frontend_dev_data.py --broken`）。

- `session.state()`：
  - 每一步新增 `blocked_by`：狀態是 `pending` 的步驟，記前面第一個出錯的步驟（index）；是因為引用的階段還沒有結果時記那個階段名稱（`blocked_stage`）。
  - `preview` 新增 `blocked_by`（選的那一步算不出來的原因，同上）。
- `App.tsx` 資料 pane 標題：
  - 還沒載入資料：灰色中性標籤「還沒載入資料」（不顯示紅色標籤）。
  - 選的那一步算不出來：標題寫顯示的那一步「第 10 步的結果・70 列 × 8 欄」（顯示讀取的資料時寫「讀取的資料・…」），旁邊紅色標籤「第 19 步算不出來：第 11 步有錯」（或「：@品項對照 還沒有結果」）。
- `StepsPanel.tsx`：`pending` 的步驟在精簡、詳細兩種模式都淡化（`opacity-60`），滑鼠移上去提示「前面第 11 步有錯，這一步算不出來」；只有真的出錯的那一步是紅色。詳細模式原本的「（前面的步驟有錯，尚未計算）」改成同一句。
- mock：`?unloaded` 已經跟 Python 一致（`preview.step = -1`）；dev 資料加一個 `--broken` 選項（讓一步出錯），方便看這些畫面。

**驗證**：`test_session.py` 的 `blocked_by`（出錯的步驟、引用的階段沒有結果、沒載入）；dev 預覽三種情況截圖確認。

---

## 第 5 批：抽樣模式／完整模式

狀態：已完成。要看整個來源檔的步驟用新的 `Step.whole_source`（移除重複、群組彙總、樞紐）；完整模式時預覽的 `step` 是步驟數（「輸出資料」），`rows`／`column_info`／`find_row` 依它讀匯出檔。

### 記住匯出結果（`session.py`）

- 匯出成功後，每個寫出的檔記一筆 `self._full[階段名稱] = {path, rows, columns, time, fingerprint}`。
  - `fingerprint`：這個階段和它往上引用到的階段的設定（讀取設定＋步驟＋`output`）加上 `_source_key`（檔案位置、大小、修改時間）的 digest。
- `state()` 每次比對 fingerprint，不一樣就丟掉那一筆（回到抽樣模式）。只記在這次 App 開著的期間。

### 顯示（`session.py` 的 `state()`、`rows()`、`column_info()`、`find_row()`）

- `preview.mode`：
  - `"full"`：資料本來就全部讀進來（`sampled` 是 false），或選的是「輸出資料」而且這個階段有符合的完整結果。
  - `"sample"`：其他情況。
- `preview.full`：完整結果的列數、匯出時間、檔案位置（`"full"` 而且是讀匯出檔時才有）。
- 選「輸出資料」而且是讀匯出檔時：
  - `rows()`：`pl.scan_parquet(path).slice(offset, limit).collect()`，`total` 用記下的列數。不整份讀進記憶體。
  - `column_info()`：一次串流查詢算每一欄的空值、空白字串、最小～最大（`column_summary` 改成可以吃 LazyFrame），範例值用前 `EXAMPLE_ROWS` 列。
  - `find_row()`：對 parquet 用 `__source_file`／`__line_no` 篩選（沒保留系統欄位時不提供「找原始行」）。
  - 警告分頁：顯示匯出時這個階段每一步的警告（`RunReport` 裡的，最多 200 筆），標題寫「匯出時共 N 則警告」。
  - 其他步驟維持樣本的預覽。

### 標示（`App.tsx`）

- 資料 pane 標題列的模式標籤取代現在的「預覽樣本」：
  - 抽樣模式（黃）：「抽樣模式：只用每個檔的前 10 萬行＋最後 1,000 行，列數、加總、彙總結果可能不正確。按「處理並匯出資料」看完整結果。」選到 `reshapes` 的步驟（群組彙總、樞紐、移除重複…）時，多一行醒目的「這一步的結果是用樣本算的」。
  - 完整模式（綠）：「完整模式：已處理全部 5,963,584 行（11/18 14:32 匯出）」；資料本來就全部讀進來時寫「完整模式：資料共 N 行，全部讀進來」。
- 型別分頁的「樣本」提示也跟著模式走。

**驗證**：

- `test_session.py`：
  - 大檔（用 monkeypatch 把 `SAMPLE_THRESHOLD` 調小）：匯出前是抽樣模式、群組彙總的合計跟完整的不一樣；匯出後「輸出資料」是完整模式，合計跟匯出檔一樣。
  - 改了步驟或檔案後回到抽樣模式。
  - `rows()` 的分頁、`column_info()` 的數字跟整份讀進記憶體算的一樣。
- 畫面：dev 預覽兩種標籤；打包後用幾 GB 的假資料確認捲動輸出資料時記憶體不會整份讀進來。

---

## 第 6 批：自我診斷畫面「開啟暫存資料夾」

狀態：已完成。打開資料夾的程式在 `desktop.py`；API 是 `staging_info()`、`open_staging()`。

- `api.open_staging()`：用 `staging_dir()` 取得位置（還沒建立就建立），macOS 用 `subprocess.run(["open", path])`、Windows 用 `os.startfile(path)` 打開；回傳位置與目前大小。只開本機資料夾，沒有網路（`check_code_policy.py` 沒有禁止 `subprocess`）。
- `SelftestDialog.tsx`：最下面加「暫存資料夾」一列：位置、目前大小、［開啟暫存資料夾］按鈕；說明「轉碼後的檔與處理中的暫存檔放在這裡，關掉 App 時會刪除；上次沒正常關掉留下的，下次開啟時清掉」。
- `bridge.ts`、mock、`test_app.py` 的 `API_METHODS` 同步。

**驗證**：`test_api`（monkeypatch 打開資料夾的函式，確認傳入的位置）；dev 預覽看按鈕。

---

## 每一批都要做

- `uv run pytest`、`scripts/check_no_data.py --all`、`scripts/check_code_policy.py`、`ruff check`、`ruff format --check`；有改前端時再加 `npm --prefix frontend run lint` 與 `npm --prefix frontend run build`。
- 文件同步：DESIGN.md（§8 串流、§9 處理紀錄、§16 畫面）、兩份 README、AI skill（`skill.py`）、CLAUDE.md 的常用指令（第 2 批改了 `tablade run` 的參數）。
- 做完的項目從 improve.md 移掉。
