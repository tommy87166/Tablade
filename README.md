# Tablade

> Table + Blade：資料整理的瑞士刀。（暫定名稱）

開源、完全在本機執行、Power Query 式「一步一步」的資料清洗工具。把多檔、爛格式的 CSV / TXT 報表 / Excel 整理成乾淨的 **Parquet**，並附上記錄資料來源與處理過程的 manifest。

> [!IMPORTANT]
> **本 repo 不含、也不得含任何真實資料。**
> 測試資料一律由 `scripts/gen_fixtures.py` 產生；pre-commit hook 會擋下資料檔與疑似個資。詳見 [DESIGN.md §11](DESIGN.md#11-真實資料防護最高優先)。

## 特色（規劃中）

- **線性步驟清單**：每一步可預覽、修改、停用、刪除；步驟存成 recipe JSON，可重現
- **爛檔友善**：逐行讀取並保留原始行號、自動處理 Big5／cp950 編碼、分段報表攤平
- **台灣格式**：民國日期、括號負數、全形半形
- **可追溯**：每一筆輸出都能追回來源檔名與原始行號；manifest 記錄輸入檔雜湊與每一步的列數
- **完全離線**：不連網、不遙測、不自動更新
- **單一執行檔**：Windows / macOS 免安裝 Python

## 目前進度

**M0 環境驗證**：最小 pywebview + Polars 程式，確認單一執行檔能在權限受管控的電腦上執行。完整路線圖見 [DESIGN.md §13](DESIGN.md#13-里程碑)。

## 使用（M0）

從 [Actions](../../actions/workflows/build.yml) 或 Releases 下載對應平台的執行檔：

| 平台 | 檔案 | 說明 |
|---|---|---|
| Windows | `tablade-windows-x64-onefile.zip` | 單一 `tablade.exe` |
| Windows | `tablade-windows-x64-onedir.zip` | 解壓後執行 `tablade\tablade.exe`（onefile 被擋或啟動太慢時的備案） |
| macOS | `tablade-macos-arm64-app.zip` | `Tablade.app`，第一次開啟請按右鍵 →「打開」 |
| macOS | `tablade-macos-arm64-onefile.zip` | 命令列單一執行檔 |

開啟後可以：

1. 按「自我檢測」：用程式產生的假資料跑一次 CSV → Parquet，確認環境沒問題
2. 按「開啟 CSV」選一個檔案預覽，再按「匯出 Parquet」

命令列：

```bash
tablade                        # 開啟視窗
tablade selftest               # 自我檢測（結束代碼 0 表示通過）
tablade gui --smoke            # 開視窗、自動自我檢測後關閉
tablade convert in.csv out.parquet
tablade version
```

Windows 版是視窗程式，命令列子命令不會顯示輸出；自我檢測請用 `tablade.exe selftest --out result.json`，再打開 `result.json` 看結果。

## 開發

需要 [uv](https://docs.astral.sh/uv/)。

```bash
uv sync                                   # 建立虛擬環境並安裝鎖定版本
uv run pre-commit install                 # 安裝資料防護 hook（必做）
uv run pytest                             # 跑測試
uv run tablade                            # 從原始碼開啟視窗
uv run python scripts/gen_fixtures.py tmp/fixtures   # 產生假資料
uv run python scripts/build.py --gui-smoke           # 打包 onefile + onedir、自我檢測、壓縮到 dist/release/
```

若有不能出現在 repo 的字詞（人名、公司名稱、專案代號等），寫進 repo 根目錄的 `.tablade-denylist`（一行一個，已在 `.gitignore`），pre-commit 會擋下含有這些字詞的檔案。

## 授權

[MIT](LICENSE)
