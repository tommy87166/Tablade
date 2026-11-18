# tablade_native：解 zip 裡的 Deflate64

Tablade 的 Rust 模組，只做一件事：把 Deflate64 壓縮過的位元組解開。Windows 檔案總管壓縮大檔時可能用 Deflate64，Python 內建的 `zipfile` 解不開。

zip 的目錄、檔案位置、CRC、切段、轉碼、寫暫存都在 Python（`src/tablade/ingest/zip_lines.py`、`sources.py`），這個模組只負責解壓。它不讀寫檔、不連網路、不呼叫外部程式，`scripts/check_code_policy.py` 會檢查。

## 內容

- 包的是 [`deflate64`](https://github.com/anatawa12/deflate64-rs) 0.1.12：照 .NET 的實作改寫，純 Rust、禁止 unsafe，沒有其他依賴。
- `src/lib.rs` 提供 `Inflater`：
  - `inflate(bytes) -> bytes`：餵一段壓縮過的位元組，回傳這次解得出來的。解壓時放開 Python 的全域鎖。
  - `finished` 屬性：讀到串流結尾了沒有。
  - 資料壞了丟出 `ValueError`。
- 模組另外有 `__version__` 與 `DEFLATE64_VERSION` 兩個常數，自我檢測與版本資訊會顯示。
- 用 [PyO3](https://pyo3.rs/) 0.30.1 與 [maturin](https://www.maturin.rs/) 1.9.6 編成 abi3 的 Python 擴充模組（Python 3.12 以上通用）。

## 怎麼編

要先裝 Rust（[rustup](https://rustup.rs/)，CI 用 1.100.0）。在 repo 根目錄：

```bash
uv sync --group native                                       # 編好並裝進 .venv
cargo test --manifest-path native/Cargo.toml --locked        # Rust 本身的測試
```

- 開發時可以不裝 Rust：沒有這個模組時，Deflate64 的檔在掃描時標成不支援，需要模組的測試會略過，其他都照常。
- 發布的 App 一定要有：`scripts/build.py` 找不到模組就停下，打包好的 App 自我檢測會實際解一次 Deflate64。

## 依賴的規則

- 依賴固定精確版本（`=x.y.z`），`Cargo.lock` 進版控，建置用 `--locked`，不要擅自升級。
- 只能用 `pyo3`、`deflate64`；不能有 `build.rs`、`build-dependencies`、`dev-dependencies`（`check_code_policy.py` 會擋）。
- 用到的 crate 的授權條文放在 `LICENSES/`，打包時一起放進 App（`licenses/`）。

## 為什麼不用 Python 的套件

同一個 1 GB 的 cp950 分段報表，Deflate64 壓縮，macOS arm64，重跑 3 輪：

| | 解壓速度 | 記憶體 |
|---|---|---|
| **這個模組（deflate64 crate）** | 約 440 MB/s | 固定約 30 MB |
| inflate64 1.0.4（LGPL-2.1+） | 約 354 MB/s | 隨讀過的壓縮資料一路變大（讀 400 MB 時 443 MB） |
| stream-inflate 0.0.43 | 約 3 MB/s | — |
| zipfile-deflate64 | 沒有 Python 3.13 的安裝檔，只支援到 3.10 | — |

詳細的紀錄在 improve.md 第 11 項。
