# 用 Rust 模組解 zip 裡的 Deflate64（自我檢測顯示）

- 狀態：**已實作**（2026-11-19）。模組的說明在 native/README.md，現在的做法以 DESIGN.md §5.4 第 4 項、§10 為準。

## 背景

讀取方式「zip 裡的文字檔」（`zip_lines`，docs/zip-lines-plan.md）已經做好，但 Deflate64 壓縮的檔目前標成不支援。Windows 檔案總管壓縮大檔時可能用 Deflate64。

候選方案的實測結果（1 GB 的 cp950 分段報表，Deflate64 壓縮，重跑 3 輪）：

| 方案 | 速度 | 記憶體 | 結論 |
|---|---|---|---|
| inflate64（Python 套件，LGPL） | 約 354 MB/s | 隨讀過的壓縮資料一路變大（400 MB → 443 MB） | 不用 |
| stream-inflate | 約 3 MB/s | — | 太慢，不用 |
| **Rust 的 `deflate64` crate，自己用 PyO3 編的模組** | **約 440 MB/s** | **固定約 30 MB** | **採用** |

`deflate64` 0.1.12 的特點：
- MIT 授權，照 .NET 的實作改寫，README 寫明是為了解 Windows 11 壓出來的 Deflate64 zip。
- 整個 crate `#![forbid(unsafe_code)]`，沒有其他依賴。

這次把原型正式放進專案，並在自我檢測顯示它能不能用。

## 已決定

- **只用 Rust 做「解 Deflate64」這一步**。zip 的目錄、位置、CRC、切段、轉碼、寫 Parquet 照舊是 Python。
  轉碼不搬到 Rust：Rust 常用的 Big5 和 Python 的 cp950 有些字對應不一樣。
- **開發時可以沒有 Rust**：
  - 模組放在 uv 的 dependency group `native`，`uv sync` 預設不編。
  - 沒有模組時 Deflate64 照現在的方式標成不支援，其他功能不受影響。
  - 測試裡需要模組的會略過。
- **發布的 App 一定要有**：
  - `scripts/build.py` 檢查模組 import 得到，沒有就停下。
  - 打包後的自我檢測實際解一次 Deflate64。
  - CI 的建置與測試都會裝 Rust、編模組。
- **假資料不用另外的壓縮套件**：zlib 用「只用 Huffman、不做重複比對」（`Z_HUFFMAN_ONLY`）壓出來的，就是合法的 Deflate64。已實測：88 萬 bytes 的 cp950 文字，每次餵 997 bytes 的奇怪切法，Rust 模組解出來完全一樣；一般的 Deflate 當 Deflate64 解則不對。

## 做法

### 1. Rust 模組（新資料夾 `native/`）

- **`native/Cargo.toml`**：
  - crate 名稱 `tablade_native`，`crate-type = ["cdylib"]`。
  - 依賴固定精確版本：`pyo3 = { version = "=0.30.1", features = ["extension-module", "abi3-py312"] }`、`deflate64 = "=0.1.12"`。
  - `[profile.release]` 開 `lto`、`codegen-units = 1`。
  - `Cargo.lock` 進版控，建置一律 `--locked`。
- **`native/pyproject.toml`**：
  - `build-backend = "maturin"`，`requires = ["maturin==1.9.6"]`。
  - 套件名稱 `tablade-native`，版本寫死（0.1.0），uv 不用編就讀得到。
- **`native/src/lib.rs`**（沿用原型，約 60 行）：
  - `Inflater` 類別：`inflate(data: bytes) -> bytes` 餵一段壓縮過的位元組、回傳這次解得出來的；`finished` 屬性。
  - 解壓時放開 Python 的全域鎖。
  - 壞掉的資料丟出 `ValueError("invalid deflate64 data")`。
  - 常數 `__version__` 和 `DEFLATE64_VERSION`（"0.1.12"，有測試核對 `Cargo.lock`）。
- **`native/README.md`**：模組在做什麼、怎麼編、為什麼不用 inflate64（附上面的實測數字）。
- **授權**：
  - `native/LICENSES/` 放 deflate64（MIT，© .NET Foundation and Contributors）與 PyO3（MIT）的授權條文。
  - 打包時放進 App（`tablade.spec` 的 datas）。

### 2. 專案設定

- **`pyproject.toml`**：
  - `[tool.uv.workspace] members = ["native"]`、`[tool.uv.sources] tablade-native = { workspace = true }`。
  - `[dependency-groups] native = ["tablade-native"]`。
  - 重新產生 `uv.lock`。
- **`.gitignore`**：加 `native/target/`。
- **`scripts/check_no_data.py`**：確認 `.rs`、`Cargo.lock`、`Cargo.toml` 不會被當成資料檔擋下（應該不會，實作時確認）。

### 3. Python 端改用這個模組（`src/tablade/ingest/zip_lines.py`）

- `deflate64_available()` 改成 import `tablade_native`。
- `_Deflate64Reader` 改用 `tablade_native.Inflater()`，其他照舊：
  - 從目錄記的位置讀出壓縮過的原始位元組。
  - 讀完核對 CRC 與大小。
  - 解壓失敗（`ValueError`）換成「zip 可能壞了」。
- 掃描時「需要 Deflate64 的套件」的訊息改成「這個版本沒有 Deflate64 的模組」。
  開發環境沒有編模組時才會看到；發布的 App 一定有。
- 程式碼、測試、文件裡提到 inflate64 的地方都拿掉。

### 4. 自我檢測顯示（`src/tablade/selftest.py`、前端）

- **新增一項檢查「解 Deflate64（Rust 模組）」**，放在「多核心轉碼」後面，總數從 8 項變 9 項：
  1. 確認：「Windows 檔案總管壓縮大檔時用的 Deflate64 格式，zip 裡的這種檔讀得出來」。
  2. 用 `fixtures.write_deflate64_zip()` 在暫存資料夾產生一個 Deflate64 的 zip。內容是門市銷售的假資料，cp950，有資料夾，大小足以切成好幾段。
  3. 用 `sources.scan_parts(..., "zip_lines", ...)` 讀出來，和原本的行、行號、行數比對，確認 CRC 也對。
  4. 成功時的說明：「用 Rust 模組（tablade_native 0.1.0、deflate64 0.1.12）解開 Deflate64 的 zip，N 行與原檔一致」。
- **沒有模組時**：
  - 打包好的 App（`sys.frozen`）：這一項**失敗**，說明「這個版本沒有 Deflate64 的模組」。
  - 原始碼執行：這一項**通過但寫明略過**：「開發環境沒有編 Rust 模組（uv sync --group native），略過；打包好的 App 一定有」。
- **版本資訊**：
  - `versions()` 多一欄 `tablade_native`，例如 `0.1.0（deflate64 0.1.12）`，沒有模組時寫「沒有」。
  - `frontend/src/components/SelftestDialog.tsx` 的 `versions()` 加名稱與說明：「Rust 模組」「解 zip 裡的 Deflate64」。
- **文字**：中英文都用 `t()`／`tr()`。

### 5. 假資料（`src/tablade/fixtures.py`）

- **新增 `write_deflate64_zip(path, seed)`**：
  1. 用 zlib 的 `Z_HUFFMAN_ONLY` 壓縮假資料。
  2. 用 `zipfile` 先寫成不壓縮的項目。
  3. 改標頭：壓縮方式（9）、CRC、解開後大小（本機標頭與中央目錄都改）。檔案時間固定。
- **測試**（`tests/test_zip_lines.py` 現在的 Deflate64 測試）改用它，不再依賴 inflate64。

### 6. 打包與 CI

- **`scripts/build.py`**：開始打包前先確認 `import tablade_native` 成功，失敗就停下，訊息寫「先執行 uv sync --group native（需要 Rust）」。
- **`tablade.spec`**：
  - `hiddenimports` 加 `tablade_native`。zip_lines 是在函式裡才 import，保險起見明寫。
  - datas 加 `native/LICENSES/`。
- **`.github/workflows/build.yml`、`test.yml`**：
  - 在 `uv sync` 之前裝 Rust：GitHub 的 runner 內建 rustup，用 `rustup toolchain install 1.100.0 --profile minimal` 固定版本，並加上 cargo 快取。
  - `uv sync --frozen --group native`。
  - 測試的三個作業系統（Ubuntu、Windows、macOS）都會實際跑 Deflate64 的測試。
- **`scripts/check_release.py`**：不用改。版號與 tag 的檢查照舊，模組的版本跟著專案走。

### 7. 程式規則的把關（`scripts/check_code_policy.py`）

新增檢查 `native/`：
- **依賴**：`Cargo.toml` 的依賴只能是 `pyo3`、`deflate64`，而且要固定精確版本（`=x.y.z`）。
- **Rust 原始碼**：`.rs` 不能用 `std::net`、`std::process`、`unsafe`（模組自己也加 `#![forbid(unsafe_code)]`）。
- **說明**：和現在的 Python 檢查一樣，這是把關的工具、不是沙箱。
- **測試**：`tests/` 裡補一個檢查這幾條的測試，和現有的程式規則測試放一起。

### 8. 文件

- **DESIGN.md**：
  - §5.4 第 4 項的 Deflate64：改成 Rust 模組，附實測數字與為什麼不用 inflate64。
  - §10 打包：Rust 模組、`uv sync --group native`。
  - §11：Rust 的部分也受程式規則檢查。
- **README 兩份**：
  - 開發環境：Rust 不是必要的，要讀 Deflate64 才需要。
  - 建置：發布的 App 需要 Rust。
  - 第三方授權。
- **CLAUDE.md**：
  - 常用指令加 `uv sync --group native`、`cargo test --manifest-path native/Cargo.toml --locked`。
  - 規則加「Rust 的依賴固定在 native/Cargo.toml 與 Cargo.lock，不要擅自升級」。
- **docs/zip-lines-plan.md**：狀態改成 Deflate64 已支援。
- **improve.md 第 11 項**：補 Deflate64 的實測，並寫下用真正 Windows 壓出來的檔驗證的結果（見驗證第 3 步）。

## 測試

- **`tests/test_zip_lines.py`**：
  - **Deflate64 讀得對**：用 `write_deflate64_zip` 產生的 zip，讀出來的行、行號、行數和原檔一樣；切很多段、走平行，結果也一樣。
  - **餵法不影響結果**：一次餵、分很多次奇怪的大小餵，解出來都一樣。
  - **壞掉的資料**：改壞壓縮內容是「zip 可能壞了」，CRC 不符也是。
  - **沒有模組時**：用 monkeypatch 讓 import 失敗，掃描標成不支援。
- **`tests/test_selftest*`**：
  - 檢查數從 8 改 9。
  - 有模組時「解 Deflate64」通過、說明有版本。
  - 原始碼執行沒有模組時是「略過」，打包環境沒有模組時是失敗（用 monkeypatch 模擬 `sys.frozen`）。
- **模組版本**：`tablade_native.DEFLATE64_VERSION` 和 `native/Cargo.lock` 記的一樣。
- **程式規則**：在暫存的資料夾放違規的 Cargo.toml 與 .rs，`check_code_policy` 要抓到。
- **Rust 本身**：`cargo test --locked`，在 `native/src/lib.rs` 放一個小測試，用一段寫死的 Huffman-only 位元組解回原文。

## 驗證

1. **全部檢查**：
   - `uv sync --group native`
   - `uv run pytest -q`（Deflate64 的測試要實際跑，不是略過）
   - `cargo test --manifest-path native/Cargo.toml --locked`
   - `check_no_data.py --all`、`check_code_policy.py`、`check_i18n.py`、ruff
   - 前端 lint 與 build
   - `uv run tablade gui --smoke`
2. **自我檢測**：
   - `uv run tablade selftest` 顯示 9 項，「解 Deflate64（Rust 模組）」通過，版本欄有 tablade_native。
   - 開發畫面的自我檢測對話框中英文各看一次。
3. **真正 Windows 壓出來的 Deflate64**：
   - 從 deflate64-rs 的 GitHub 下載它用 Windows 11 做的測試檔（公開 repo 的測試資料），只放 scratchpad、不進版控。
   - 確認讀得出來、CRC 正確。下載前先問你。
4. **實測**：同一個 1 GB 的 Deflate64 zip，用 `zip_lines` 讀，量準備檔案的時間、暫存空間、記憶體，寫進 improve.md。
5. **打包**：`uv run python scripts/build.py --gui-smoke`，打包好的 App 自我檢測 9 項全過。
6. **CI**：push 後 test 與 build 兩個 workflow 在 Windows、macOS 都要過，Windows 打包後的自我檢測也要過。只有這一步驗證得到 Windows 上編得過。
7. **完成後回報，等你說了才 commit。**
