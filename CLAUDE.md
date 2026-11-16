# CLAUDE.md

Tablade：本機執行、Power Query 式的資料清洗工具（Python + Polars 2.x + pywebview；前端 React + TypeScript + Vite + shadcn/ui）。設計見 [DESIGN.md](DESIGN.md)。

## 公開 repo 規則（最高優先）

這個 repo 從第一天就是公開的。

- **永遠不要建立、commit 或要求任何真實資料**；測試資料只能由 `src/tablade/fixtures.py`（`scripts/gen_fixtures.py`）產生。
- 程式碼、註解、文件、commit 訊息、PR 說明都**不得提及使用者的雇主、所屬組織、職業或工作領域**。這個專案一律只描述成通用的資料清洗工具；使用情境用中性說法（例如「權限受管控的公司電腦」），假資料也用通用情境（例如門市銷售）。
- 不要把使用者給的真實檔名、欄位內容、截圖貼進 repo 或 issue。需要重現問題時，先擴充 `fixtures.py` 產生同樣形狀的假資料。
- 真實 recipe 也可能含敏感資訊，只能 commit `examples/` 底下搭配假資料的範例 recipe。

## 程式規則

- 使用 Polars **2.x 的 Python API**。不使用已移除的 API（例如 `melt` 改用 `unpivot`）；不確定時先查官方文件或實際跑一次。
- 新增步驟（DESIGN.md §7.3、§17.5）：
  1. 在 `src/tablade/steps/` 寫一個 `Step` 子類別並加上 `@register`：`type: Literal[...]`、參數欄位（`title`、引用欄位用 `ColumnRef` / `ColumnRefs`、新欄名用 `NewColumns`）、`label`、`category`、docstring、`ai_notes`、`effect`（欄位效果）、至少一個 `examples`（同時是測試）。
  2. 實作 `apply()`（純函式）、`describe()`，需要時加 `warning_checks()` 與建立步驟時用的 `suggest_*()`。
  3. 新模組要加進 `steps/__init__.py`（匯入順序＝顯示順序），並在 `tests/test_steps.py` 補邊界情況的測試。
  4. 結構有變（新增步驟或參數、改預設值）就升 `RECIPE_VERSION`（相容升 minor），再執行 `uv run python scripts/update_schema_lock.py`；只改說明文字不用升。
- `src/tablade/recipe/validator.py` 只能用 Python 標準函式庫、不能 import tablade（它會原封不動放進 AI skill），有測試檢查。
- 步驟 `apply()` 必須是純函式：不讀寫檔案、不 print、不碰 UI。
- 依賴列順序的操作，一律先依 `__source_file, __line_no` 排序。
- 不使用 `eval` / `exec`；不加入任何網路連線程式碼（`scripts/check_code_policy.py` 會檢查 `src/`）。
- 依賴版本固定在 `pyproject.toml` 與 `uv.lock`，不要擅自升級。
- `api.py`（JS bridge）只做參數轉換與錯誤包裝，邏輯放在引擎。
- pywebview 視窗不開 HTTP server：前端 build 成單一 HTML（`frontend/dist/index.html`），以字串載入（見 `app.py`）。不要改成用網址或相對路徑載入；整份 HTML 必須小於 2 MB。

## 前端規則（frontend/，設計見 DESIGN.md §16）

- 狀態放在 Python：前端只送指令、顯示 Python 回傳的畫面狀態，不自己保存 recipe。
- 步驟的參數表單由 pydantic schema 自動產生（DESIGN.md §16.5），不要為個別步驟手寫表單；驗證只在 Python 做。
- `src/bridge.ts` 的 `TabladeApi` 必須與 `api.py` 的公開方法一致（有測試檢查）。
- 不用 `dangerouslySetInnerHTML` / `innerHTML`，不用 `fetch` / `WebSocket` 等網路 API，不用 `eval` / `new Function`。
- UI 元件用 shadcn/ui：`cd frontend && npx shadcn@latest add <元件>`。**不要**把 `shadcn` 裝成依賴；新增元件後確認 `src/index.css` 沒有被改回 `@import "shadcn/tailwind.css"`。
- 不載入外部字型或資源；字型用系統字型。
- CSP 由 `vite.config.ts` 在 build 時注入，不要寫在 `index.html`。
- UI 的新功能：邏輯寫在 `session.py`（有 pytest），`api.py` 只包一層（開對話框、回傳新的 `state`），再到 `bridge.ts` 加型別。
- 用一般瀏覽器開發前端：先 `uv run python scripts/gen_frontend_dev_data.py`，再 `npm --prefix frontend run dev`（`mock.ts` 會用產生的假資料）。

## 常用指令

```bash
uv sync
npm --prefix frontend ci && npm --prefix frontend run build   # 開視窗、跑 test_app.py 前要先 build
npm --prefix frontend run lint
npm --prefix frontend run dev   # 瀏覽器開 http://localhost:5173（假 API）
TABLADE_DEV_URL=http://localhost:5173 uv run tablade   # 真正的視窗＋熱更新
uv run pytest
uv run python scripts/check_no_data.py --all
uv run python scripts/check_code_policy.py
uv run tablade run examples/sales_detail.recipe.json      # 先 uv run python scripts/gen_fixtures.py examples/data
uv run tablade validate recipe.json --run                 # 驗證＋用真實資料試跑
uv run python scripts/update_schema_lock.py              # 升 RECIPE_VERSION 後
uv run ruff check . && uv run ruff format --check .
uv run tablade                  # 開視窗
uv run tablade gui --smoke      # 開視窗、確認 JS bridge 正常後自動關閉
uv run python scripts/build.py --gui-smoke   # 打包 onefile + onedir、自我檢測、壓縮到 dist/release/
uv run --group icons python scripts/make_icons.py   # 改 assets/icon.svg 後重新產生圖示
```

- README 有英文（README.md）與繁體中文（README.zh-TW.md）兩份，內容要同步修改。

- 前端的 Content-Security-Policy 必須保留 `default-src 'none'`（擋掉所有對外連線）。`'unsafe-eval'` 是 pywebview 的 JS bridge 需要的，不要拿掉。
- 前端寫入資料一律用 `textContent`，不用 `innerHTML`（這個頁面可以呼叫 Python）。

每次修改都要跑 `pytest`、`check_no_data.py --all`、`check_code_policy.py`；改到前端時再加 `npm --prefix frontend run lint` 與 `npm --prefix frontend run build`。
