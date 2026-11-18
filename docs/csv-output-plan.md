# 輸出檔可以選 CSV（每個階段各自選）

狀態：已完成。

## 背景

現在每個要寫檔的階段都只能輸出 Parquet。使用者想讓輸出檔（包含中間的階段）也能存成 CSV，例如中間的「品項對照」存 CSV、用 Excel 打開檢查，最後的結果存 Parquet。

改成 CSV 不影響後面階段的引用：
- 後面的階段只讀引擎傳下來的結果（記憶體或暫存資料夾的暫存 Parquet），不讀 output/ 的輸出檔。
- 已經有測試證明這一點：`tests/test_large.py` 的 `test_later_stages_never_read_the_output_files`、`test_preview_never_reads_the_output_files`。

## 已決定

1. **格式在每個階段各自選**：`parquet` 或 `csv`。
2. **CSV 只用 UTF-8 加 BOM**：Excel 雙擊打開中文不會亂碼。cp950 之後真的需要再加。
3. **CSV 輸出不提供完整模式**：匯出 CSV 後，「輸出資料」照舊是抽樣模式。
4. **`output_format` 是必要欄位**：還沒發版，不考慮舊 Recipe。
   - 每個階段都要有，沒寫檔的中間階段也一樣，結構才一致，AI 也比較好寫。
   - `RECIPE_VERSION` 維持 1.0，重新產生 schema lock。
   - Recipe 最外層 `output.format`（現在只能是 parquet）拿掉，免得兩個地方設格式。

## 做法

### 1. Recipe 結構與驗證
- **`src/tablade/recipe/schema.py`**
  - `Stage` 加 `output_format: Literal["parquet", "csv"]`，必填、沒有預設值。標題與說明給 AI skill 看，用中文。
  - `Output` 拿掉 `format`。
- **`src/tablade/recipe/validator.py`**（只用標準函式庫）
  - 補副檔名：`output_name(raw)` 改成 `output_name(raw, fmt)`，依格式補 `.parquet` 或 `.csv`。
  - 填了副檔名、但跟格式不一樣時報錯，例如格式選 csv 卻填「明細.parquet」。
  - 重複檢查改看**主檔名**：`明細.csv` 和 `明細.parquet` 也算重複，因為處理紀錄是 `log/明細.manifest.json`，會撞名。
  - 新的訊息都用驗證器自己的 `t()`。
- 執行 `uv run python -c "from tablade.recipe.spec import write_lock; write_lock()"`。

### 2. 輸出檔的規劃
- **`src/tablade/export/outputs.py`**
  - `default_output_name(recipe_path, fallback, fmt)`：最後一個階段沒填檔名時，用「Recipe 名稱＋那個格式的副檔名」。
  - `plan_outputs` 依每個階段的 `output_format` 算出檔名。
  - `PlannedOutput` 多記 `format`。
  - 「不能寫到輸入檔」的檢查照舊。

### 3. 引擎寫檔（`src/tablade/engine/run.py`）
寫檔集中在兩個地方，依格式分開：

| 執行方式 | Parquet（照舊） | CSV |
|---|---|---|
| 整份在記憶體 | `write_parquet` | `write_csv(include_bom=True, datetime_format=…)` |
| 串流（`_sink_output`） | `sink_parquet` | `sink_csv(include_bom=True, engine="streaming")` |

- **最後一個階段的列數**：現在是讀自己剛寫好的暫存檔來數。改成從 `result` 算，就不用讀 CSV；測試裡「只允許最後一個階段讀自己的暫存檔」的例外也一起拿掉。
- **CSV 內容**：
  - 小數照原樣、日期 `2026-01-05`、日期時間 `2026-01-05 10:00:00`、空值是空白。
  - 要不要保留系統欄位，照現有的 `keep_system_columns`。
- **進度文字**：「寫出 CSV」／`Writing CSV`。
- `OutputReport` 多記 `format`。

### 4. 處理紀錄（`src/tablade/export/manifest.py`）
- `output` 多一個 `format`；SHA-256 照樣算輸出檔本身。

### 5. Session 與 API
- **`src/tablade/session.py`**
  - `_new_stage` 加 `"output_format": "parquet"`。
  - `DEFAULT_OUTPUT` 拿掉 `format`。
  - `set_stage_output(index, file)` 改成 `set_stage_output(index, file, fmt)`。檔名不再自動補 `.parquet`，填了副檔名就照格式改掉（例如格式選 csv、填「明細.csv」，存成「明細」）。
  - 畫面狀態 `stage.output`（`_planned_state`）多 `format`。
- **完整模式**：
  - `_full_output()`：匯出檔是 CSV 時回傳 None，維持抽樣模式。
  - `export()` 記 `_exports` 時一起記格式。
  - 匯出結果的 `outputs[]` 多 `format`。
- **`src/tablade/api.py`、`frontend/src/bridge.ts`**：`set_stage_output(index, file, format)`；`StageState.output`、匯出結果的型別也加 `format`。

### 6. 畫面（`frontend/src/components/SourcePanel.tsx` 的 `OutputFileField`、`App.tsx`）
- **格式選單**：檔名欄後面原本固定的「.parquet」，換成 shadcn 的選單「.parquet ▾ / .csv」，選了馬上存，跟改檔名一樣走 `onStageOutput`。
- **輸出設定的說明**：CSV 時加一句「CSV 用 UTF-8（含 BOM），Excel 可以直接打開；匯出後不提供完整模式」。
- **抽樣模式的滑鼠說明**：選的階段輸出 CSV 時，不寫「匯出後「輸出資料」會改成讀匯出檔」。
- **匯出摘要**：CSV 超過 1,048,576 列時，加一句提醒 Excel 打開會被截斷。
- 所有新文字都用 `tr('中文', 'English')`（`check_i18n.py` 會檢查）。
- **開發用的假資料**：`mock.ts` 的 `set_stage_output` 跟著改。

### 7. 範例、AI skill、CLI
- **範例 Recipe**：`examples/*.recipe.json` 每個階段加 `output_format`，`output` 拿掉 `format`。`sales_combined` 的中間階段「品項對照」改成輸出 CSV，當作範例。
- **AI skill**：`src/tablade/skill.py` 的 Recipe 說明補上 `output_format`（必填、parquet／csv、CSV 是 UTF-8 加 BOM）。
- **CLI**：`tablade run` 照 Recipe 的格式寫，不用另外改；`convert` 指令不動。

### 8. 測試要跟著改的地方
- **手寫的 Recipe**：`tests/` 裡手寫的 Recipe 大約有 140 處，例如 `test_recipe.py`、`test_session.py`、`test_large.py`，每個階段都要加 `output_format`。
  - 用 Session 建立、或讀 `examples/` 的不用改。
  - 驗證錯誤的測試要確認錯誤沒有變多。
- **新增測試**：
  - **CSV 內容：** BOM、小數、日期、空值、系統欄位開關；整份在記憶體和串流寫出的 CSV 完全一樣。
  - **不讀輸出檔：** `test_later_stages_never_read_the_output_files` 加上 CSV 的情況。
  - **驗證：** 少了 `output_format` 報錯（中英文）；副檔名跟格式不合報錯；`明細.csv` 和 `明細.parquet` 算重複。
  - **最後一個階段沒填檔名：** 依格式補副檔名。
  - **處理紀錄：** 有 `format`，SHA-256 是 CSV 檔的。
  - **完整模式：** CSV 輸出匯出後還是抽樣模式，Parquet 照舊變完整模式。
  - **英文訊息：** 新的訊息有英文（`tests/test_i18n*.py`）。

### 9. 文件
- `DESIGN.md` §9（輸出）、§16（輸出設定畫面）。
- 兩份 README 的已知限制或功能說明。
- 把這份計畫存成 `docs/csv-output-plan.md`。

## 驗證
- 全部檢查：
  - `uv run pytest`、`uv run python scripts/check_no_data.py --all`、`uv run python scripts/check_code_policy.py`、`uv run python scripts/check_i18n.py`
  - `uv run ruff check . && uv run ruff format --check .`
  - `npm --prefix frontend run lint`、`npm --prefix frontend run build`
- 範例跑一次：`uv run python scripts/gen_fixtures.py examples/data`，再 `uv run tablade run examples/sales_combined.recipe.json`。確認：
  - `output/品項對照.csv` 開頭是 BOM。
  - 用 Polars 讀回來的內容跟 Parquet 版一致（型別除外）。
- 開發畫面（中文、英文各看一次）：
  - 輸出設定的格式選單、CSV 的說明。
  - 匯出 CSV 後還是抽樣模式。
- 真的視窗：`uv run tablade gui --smoke`。
- 完成後回報，等你說 commit 才 commit。
- 開始前先處理：上一輪補的 3 個測試和開發畫面自我檢測的修正還沒 commit，要你說了才 commit。
