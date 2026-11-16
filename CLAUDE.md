# CLAUDE.md

Tablade：本機執行、Power Query 式的資料清洗工具（Python + Polars 2.x + pywebview）。設計見 [DESIGN.md](DESIGN.md)。

## 公開 repo 規則（最高優先）

這個 repo 從第一天就是公開的。

- **永遠不要建立、commit 或要求任何真實資料**；測試資料只能由 `src/tablade/fixtures.py`（`scripts/gen_fixtures.py`）產生。
- 程式碼、註解、文件、commit 訊息、PR 說明都**不得提及使用者的雇主、所屬組織、職業或工作領域**。這個專案一律只描述成通用的資料清洗工具；使用情境用中性說法（例如「權限受管控的公司電腦」），假資料也用通用情境（例如門市銷售）。
- 不要把使用者給的真實檔名、欄位內容、截圖貼進 repo 或 issue。需要重現問題時，先擴充 `fixtures.py` 產生同樣形狀的假資料。
- 真實 recipe 也可能含敏感資訊，只能 commit `examples/` 底下搭配假資料的範例 recipe。

## 程式規則

- 使用 Polars **2.x 的 Python API**。不使用已移除的 API（例如 `melt` 改用 `unpivot`）；不確定時先查官方文件或實際跑一次。
- 新增步驟時，必須同時完成：pydantic 參數 model、`apply()`、`describe()`、`infer_schema()`（視需要）、單元測試（假資料）。
- 步驟 `apply()` 必須是純函式：不讀寫檔案、不 print、不碰 UI。
- 依賴列順序的操作，一律先依 `__source_file, __line_no` 排序。
- 不使用 `eval` / `exec`；不加入任何網路連線程式碼（`scripts/check_code_policy.py` 會檢查 `src/`）。
- 依賴版本固定在 `pyproject.toml` 與 `uv.lock`，不要擅自升級。
- `api.py`（JS bridge）只做參數轉換與錯誤包裝，邏輯放在引擎。
- pywebview 視窗不開 HTTP server：HTML 以字串載入（見 `app.py`），不要改成用網址或相對路徑載入。

## 常用指令

```bash
uv sync
uv run pytest
uv run python scripts/check_no_data.py --all
uv run python scripts/check_code_policy.py
uv run ruff check . && uv run ruff format --check .
uv run tablade                  # 開視窗
uv run tablade gui --smoke      # 開視窗、確認 JS bridge 正常後自動關閉
uv run python scripts/build.py --gui-smoke   # 打包 onefile + onedir、自我檢測、壓縮到 dist/release/
uv run --group icons python scripts/make_icons.py   # 改 assets/icon.svg 後重新產生圖示
```

- README 有英文（README.md）與繁體中文（README.zh-TW.md）兩份，內容要同步修改。

- 前端的 Content-Security-Policy 必須保留 `default-src 'none'`（擋掉所有對外連線）。`'unsafe-eval'` 是 pywebview 的 JS bridge 需要的，不要拿掉。
- 前端寫入資料一律用 `textContent`，不用 `innerHTML`（這個頁面可以呼叫 Python）。

每次修改都要跑 `pytest`、`check_no_data.py --all`、`check_code_policy.py`。
