# 畫面 i18n（繁體中文＋英文）實作計畫

## 已決定

- **範圍**：畫面上看得到的文字全部都要有英文，分批做、每批各自 commit。
  - 第 1 批是前端自己寫的文字與切換鈕。
  - 之後幾批是 Python 產生的文字：步驟名稱、表單欄位、狀態說明、進度、錯誤與警告。
- **不翻**：
  - Recipe 檔的內容（使用者取的名稱、欄名）與資料本身。
  - AI skill（`skill.py`）、CLI 的輸出、處理紀錄（manifest）。
  - 文件與假資料。
- **預設語言**：跟作業系統語言（`navigator.language` 是 `zh` 開頭就用中文，其他用英文）。
- **不記住選擇**：不寫任何設定檔，每次開啟都用預設語言。
- **切換鈕**：工具列右上「⋯」的左邊，地球圖示＋目前語言（🌐 中文 ▾），點開選「中文」「English」。

## 做法

### 語言放在 Python

- `api.set_language(lang)`：前端啟動時送偵測到的語言，切換時再送一次；回傳新的 `state`（含 `state.lang`）。
- Python 端的文字依這個語言產生（第 2 批以後）；前端也照 `state.lang` 顯示。
- 不加 i18n 套件，兩種語言的文字寫在用到的地方。

### 前端：`tr('中文', 'English')`

- 新增 `frontend/src/i18n.ts`：
  - `Lang = 'zh' | 'en'`、`detectLang()`、`setLang()`、`lang()`
  - `tr(zh, en)`：依目前語言選一個。
- 每個中文字串改成 `tr('載入資料及預覽', 'Load & preview')`，中英文放在一起，好對照也不會漏翻。
- 有變數時兩邊各寫一次，例如 ``tr(`共 ${n} 列`, `${n} rows`)``。
- 英文單複數用小工具 `plural(n, 'row', 'rows')`。
- 切換語言時 App 重新算畫面：沒有用 `memo`，整個畫面都會重畫。
- **不能**在模組最上層用 `tr()` 算好常數（會停在載入時的語言）：要寫成函式，或在元件裡算。
- **檢查**：`tests/test_i18n.py` 掃 `frontend/src`（不含 `dev/`、`mock.ts`、註解），中文只能出現在 `tr()` 的第一個參數。

### Python（第 2 批以後）：`t(zh=…, en=…)`

- 新增 `tablade/i18n.py`：`set_lang()`、`lang()`、`t(zh, en)`，用模組層級的目前語言（桌面 App 只有一個使用者）。預設中文，CLI、AI skill 不受影響。
- **切換語言**：清掉算好的結果，用新的語言重算警告與錯誤；已載入的資料留著，不用重新讀檔。
- **步驟的名稱、分類、表單欄位**（標題、說明、選項）：
  - 英文寫在旁邊：`label_en`、`Field(json_schema_extra={"en": {"title": …, "description": …}})`、`ui_labels_en`。
  - 送給前端時依語言換掉，`catalog` 也跟著換。
- **`describe()`、警告、錯誤、進度、狀態說明**：用 `t()`。
- **`recipe/validator.py`**（只用標準函式庫、不 import tablade）：
  - 自己有一個 `LANG` 與 `_t()`，tablade 切換語言時一起設。
  - AI skill 用的時候維持中文。

## 分批

1. **前端**
   - `i18n.ts`、切換鈕、`api.set_language`（Python 先只記住語言、放進 `state.lang`）
   - 前端所有元件的文字、`tests/test_i18n.py`
2. **Python 的畫面狀態與流程**
   - `session.py`：狀態說明、按鈕不能按的原因、算不出來的原因、錯誤訊息
   - `api.py`
   - `engine/run.py`、`ingest/`：進度文字、讀檔與轉碼錯誤
   - `export/`、`selftest.py`、`dtypes.py`（型別名稱）、`profile.py`（型別分頁）
3. **步驟的名稱與表單**：每個步驟的名稱、分類、參數欄位的標題、說明、選項，`describe()`。
4. **步驟的警告與錯誤、驗證器的訊息**：`warning_checks()`、`StepError`、`EffectError`、`recipe/schema.py`、`recipe/validator.py`。

每一批都要：

- 跑全部檢查（pytest、`check_no_data.py --all`、`check_code_policy.py`、ruff、前端 lint 與 build）。
- 在開發畫面切成英文看過一遍。
- 補測試：切成英文後，那一批負責的欄位沒有中文。

## 用語對照

| 中文 | English |
|---|---|
| Recipe | Recipe |
| 階段 | Stage |
| 步驟 | Step |
| 讀取資料 | Read data |
| 輸出資料 | Output |
| 階段結果 | Stage result |
| 載入資料及預覽 | Load & preview |
| 處理並匯出資料 | Process & export |
| 完整跑一次 | Run in full |
| 抽樣模式／完整模式 | Sample mode／Full mode |
| 預覽結果可能不正確 | Preview may be inaccurate |
| 來源檔 | Source file |
| 合併來源檔 | Combine source files |
| 結合 | Join |
| 另一張表 | Other table |
| 附加 | Append |
| 欄／列 | Column／Row |
| 行（文字檔） | Line |
| 系統欄位 | System columns |
| 警告 | Warnings |
| 型別 | Types |
| 準備檔案 | Preparing files |
| 試跑 | Trial run |
| 轉碼 | Transcoding |
| 暫存資料夾 | Temp folder |
| 主程序 | Main process |
| 自我檢測 | Self-test |
| 處理紀錄 | Processing log |
| 新增步驟 | Add step |
| 前往 | Go to |
| 等待中／處理中／完成／失敗 | Waiting／Processing／Done／Failed |
| 還沒載入資料 | Data not loaded yet |
| 精簡／詳細 | Compact／Detailed |
