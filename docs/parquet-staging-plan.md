# 轉碼後的暫存檔改存 Parquet（逐行讀取）

- 狀態：**計畫，尚未實作**（2026-11-18）。
- 相關：之後讀 zip 的檔（解出來的內容走同一條路）。

## 背景

暫存資料夾裡最佔空間的是讀檔時的轉碼：cp950 這類檔案會複製一份**沒壓縮的 UTF-8 純文字**（原檔的 1～1.5 倍），而且是快取，**關掉視窗才刪**。要處理的檔案可能很大（之後還要讀 3 GB 的 zip，解開可能 15～30 GB），磁碟空間是主要的顧慮。

原型實測（1 GB cp950 分段報表，同一份假資料）：

- 暫存成 zstd 壓縮的 Parquet：77 MB；純文字：1 GB 以上。讀回來的行數一樣。
- 工作程序維持現在的樣子，不載入 Polars，每個約 33 MB。由主程序把轉好的段寫成 Parquet，每段約 0.03 秒，不會拖慢平行轉碼。
- 如果改成工作程序自己寫 Parquet，每個要約 224 MB，所以不採用。

這次也是之後讀 zip 的基礎：zip 解出來的內容走同一條路。

## 範圍

- **改**：讀取方式是「逐行讀取」（`text_lines`），而且檔案需要轉碼時：
  - cp950、Big5 等其他編碼；
  - UTF-8 但有單獨的 `\r` 換行。
- **不改**：
  - **UTF-8 檔**：照舊直接讀原檔，不寫暫存檔。
  - **CSV**：照舊暫存成 UTF-8 文字，轉碼的程式完全不動（原因見下一節「CSV 為什麼不改」）。另外，分段各自解析 CSV 時，欄數不符、空行的行為和整份讀不一樣（實測：多欄的錯誤訊息不同，少欄的段會直接失敗），要另外處理。CSV 的檔也可以改用「逐行讀取＋依分隔符號拆成多欄」，範例 `sales_detail` 就是這樣讀的。
  - **Excel、Parquet**：不轉碼，不受影響。

## CSV 為什麼不改

CSV 要轉碼時，Polars 最後一定要讀**一整份 UTF-8 的 `.txt`**（`scan_csv`），所以不管轉碼怎麼排程，暫存資料夾最後都會有一整份；只有逐行讀取最後留下的是壓縮過的 Parquet，才省得下空間。

用 2 GB 的 cp950 檔、切成 64 段（每段 32 MB）、8 個工作程序模擬：

| 做法 | 轉碼途中最高 | 轉完留下 |
|---|---|---|
| CSV：現在的做法（全部的段先轉完，再接成 `.txt`，接一段刪一段） | 約 2.03 GB | 2 GB（`.txt`） |
| CSV：套用下面的分段做法（同時最多「工作程序 × 2」段，轉好一段就接進 `.txt`） | 2 GB | 2 GB（`.txt`） |
| 逐行讀取：改存 Parquet（這份計畫） | 約 440 MB（3 個工作程序時約 290 MB） | 約 200 MB |

- 模擬的時間是示意的：一個工作程序轉一段算 1，主程序接一段或寫一段 Parquet 算 0.1。
- Parquet 假設壓縮到 1/10（用假資料實測是 1/13）。
- 轉成 UTF-8 的大小假設不變（中文多的話 CSV 的兩列都會再大一點）。

套用分段做法時，同時在處理的段雖然少了，但已經接好的 `.txt` 一直變大，加起來最後還是一整份。最高和結束都和現在一樣，改了沒有好處，還要多測一條路，所以 **CSV 的轉碼程式（`_transcode`、`_transcode_parallel`、`_transcode_single`）完全不動**。要省 CSV 的空間，只能改成每段各自解析 CSV 再存 Parquet（見「範圍」說的行為差異），或改用「逐行讀取＋依分隔符號拆成多欄」，不在這次的範圍。

## 做法

### 1. 轉碼的分段改成「一段一段交出來」（`src/tablade/ingest/sources.py`）

新增一個產生器 `_segments(path, encoding, folder, progress, segment, pool)`：

- **切段**：沿用 `transcode.segment_bounds` 在換行後面切段。
- **轉碼**：每段交給工作程序的 `transcode.transcode_segment`（不改，只用標準函式庫）。
- **順序**：依檔案順序 `yield` 每段轉好的 UTF-8 文字檔。
- **在路上的段數有上限**：最多「工作程序數 × 2」段同時在處理或等著被取走。用完一段才送下一段，所以轉碼途中的暫存文字最多約 8 × 2 × 32 MB = 512 MB，不再整份都在。
- **錯誤**：遇到第一個解碼錯誤，照現在的方式丟出 `_decode_error`，訊息一樣、行號一樣。
- **清理**：成功、失敗都刪掉還沒取走的段。
- **單核心**：沒有工作程序時，同一個產生器在主程序依序轉（`pool=None`）。
- **SHA-256**：沿用另開一條執行緒從頭讀原檔算。
- **進度**：照舊是「轉碼」，依位元組回報。

這個產生器只給逐行讀取用（第 2 節）。CSV 用的 `_transcode`、`_transcode_parallel`、`_transcode_single` 不動（見「CSV 為什麼不改」）；開不了工作程序時改用單核心（`PoolUnavailable`）的判斷兩邊一樣。

### 2. 逐行讀取：每段寫成一個 Parquet

新增 `_stage_lines(path, encoding, folder, progress) -> (sha256, files, lines)`：

- **寫法**：每取到一段文字，就在主程序用 `pl.scan_lines(段, name="line").sink_parquet(folder/f"{i:06d}.parquet", compression="zstd", row_group_size=…)` 寫出，接著刪掉那段文字。
- **row group**：在 `ingest` 裡定義自己的常數，不 import engine。
- **換行**：單獨的 `\r` 已經在轉碼時改成 `\n`，`\r\n` 由 `scan_lines` 處理，和現在一樣。
- **BOM**：
  - 第一段用 `utf-8-sig` 解碼時，BOM 在轉碼時就拿掉了（和現在一樣）。
  - cp950 沒有 BOM。
  - 原檔是 UTF-8 不轉碼時，照舊在掃描時去掉第 1 行的 BOM。
- **行數**：加總每個 Parquet 的列數（讀 metadata），不用再把暫存檔讀一遍（取代 `_count_lines`）。
- **空檔**：大小是 0 的檔照舊不寫暫存。

### 3. `Prepared` 與快取

- **`Prepared` 多一個欄位** `parquet: tuple[Path, ...] = ()`：
  - 有值時，掃描這些 Parquet；
  - 沒有值時，照舊掃 `effective` 的文字檔。
- **`prepare()` 多一個參數** `lines: bool = False`：
  - `scan_parts` 傳 `lines=(reader == "text_lines")`；
  - 預設 `False` 的行為和現在一樣，CSV 與現有呼叫不用改。
- **快取的 key 加上這個旗標**：同一個檔用逐行讀取與 CSV 讀，各自有自己的暫存。
- **暫存的位置**：Parquet 放在暫存資料夾裡的 `<hash>/` 子資料夾。
  - `clear_staging()` 用 `rmtree`，`_folder_size()` 用 `os.walk`，本來就處理得了子資料夾。
  - 啟動時清掉沒人用的暫存資料夾的邏輯不受影響。

### 4. 掃描（`_scan_one`）

`text_lines` 而且有 `parquet` 時，改成：

```python
pl.scan_parquet(list(prepared.parquet), row_index_name=LINE_NO, row_index_offset=1)
```

之後的 `select`（`__source_file`、`__line_no` 轉 Int64、`line`）照舊。其他地方都只看 part 的 LazyFrame，所以不用改：

- 樣本（`sample`）
- 進度的位置欄（`engine/progress.with_positions`，只看 `LINE_NO` 與 `rows`）
- 串流執行

### 5. 自我檢測（`src/tablade/selftest.py`）

「多核心轉碼」照舊比較單核心與平行轉出的文字，確認打包後開得了工作程序。另外加一個斷言：用逐行讀取的暫存方式準備同一個檔，讀回的行數、內容和文字版一樣，確認打包後的 App 寫得了 Parquet 暫存。

### 6. 文件

- **DESIGN.md §8**：
  - 「讀檔」：逐行讀取的暫存是 Parquet，空間大約和 zip 壓縮後差不多，轉碼途中最多多出「工作程序數 × 2」段的文字；CSV 照舊約原檔 1.5 倍（寫明為什麼不改）。
  - 「平行轉碼」：逐行讀取時段依序交出、在路上的段數有上限；CSV 照舊全部轉完再接起來。
  - 順便修正「列順序」那段的過時說法：群組彙總、樞紐現在照每組第一筆的位置排序；移除重複保留最後一筆的做法（上一輪 §7 已改，§8 漏了）。
- **模組說明**：`ingest/sources.py`、`ingest/transcode.py` 開頭的說明跟著改。
- **improve.md**：新增一項，記這次的原因與實測數字（暫存大小、時間、記憶體）。

## 測試

- **新增，`tests/test_large.py`**：
  - **讀出來和 UTF-8 版一樣**：cp950 的分段報表用逐行讀取，讀出來的 `line`、`__line_no` 和同內容的 UTF-8 檔（不轉碼、直接讀）完全一樣。要涵蓋：空行、最後一行沒有換行、`\r\n`、單獨的 `\r`、`utf-8-sig` 加上單獨的 `\r`。
  - **平行＝單核心**：切成很小的段強迫走平行，結果和單核心一樣。
  - **暫存資料夾裡只有 Parquet**：沒有 `.txt`，也沒有留下的段，而且總大小比原檔小很多。
  - **在路上的段數有上限**：監看暫存資料夾，同時存在的文字段不超過工作程序數 × 2。
  - **解碼錯誤**：平行與單核心的訊息一樣、行號一樣，失敗後不留任何暫存檔。
  - **快取分開**：同一個檔先用逐行讀取、再用 CSV 讀，兩種都正確。
  - **開不了工作程序**：改用單核心，結果一樣。
- **既有測試照舊要過**：
  - 範例的明細是 cp950、逐行讀取，所以串流＝整份的比對（`run_both`、分段報表、難搞的資料）都會走新的暫存。
  - CSV 的轉碼測試（`both_ways` 等）照舊比文字。
  - 預覽樣本、`test_prepare_is_cached`、自我檢測的測試。
- `tests/test_review_engine.py`、`tests/test_large.py` 裡檢查 `*.part*` 有沒有留下來的，改成檢查暫存的段。

## 驗證

1. **全部檢查**：
   - `uv run pytest -q`
   - `uv run python scripts/check_no_data.py --all`
   - `uv run python scripts/check_code_policy.py`
   - `uv run python scripts/check_i18n.py`
   - `uv run ruff check . && uv run ruff format --check .`
   - `uv run tablade gui --smoke`
2. **實測**：2 個約 1 GB 的 cp950 分段報表（`fixtures.write_report`），改之前與改之後各跑一次：
   - 「準備檔案」的時間；
   - 暫存資料夾的大小（轉碼途中的最大值與之後）；
   - 主程序與工作程序的記憶體；
   - 串流匯出的時間；
   - 1,000 個左右 Parquet 檔時，建立查詢要多久。

   數字寫進 improve.md。
3. **打包**：`uv run python scripts/build.py --gui-smoke`，自我檢測要通過。
4. **完成後回報，等你說了才 commit。**
