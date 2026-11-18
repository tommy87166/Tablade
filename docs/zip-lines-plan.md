# 讀 zip 裡的文字檔（逐行讀取）

- 狀態：**已實作**（2026-11-18），Deflate64 的套件還沒加（程式已經支援，裝了就能讀；加依賴要先確認）。現在的做法以 DESIGN.md §5.4 第 4 項為準。
- 相關：轉碼後的暫存改存 Parquet（docs/parquet-staging-plan.md、DESIGN.md §5.4），這次直接沿用那條流程。

## 背景

要處理的資料有時是很多個 zip：每個月一個，一個好幾 GB，解開可能 15～30 GB。每個 zip 裡有好幾種文字檔，放在資料夾裡，例如：

```
2026-01.zip
├── 202601/HEADER_001.TXT
├── 202601/HEADER_002.TXT
├── 202601/ITEM_001.TXT
└── 說明.pdf
```

要能直接讀 zip 裡指定的檔，不用先全部解壓縮，也不能把暫存資料夾所在的磁碟塞滿。這是比較少見的大檔情況，所以只做一種新的讀取方式，其他讀取方式不支援 zip。

## 已決定

1. **新的讀取方式「zip 裡的文字檔（逐行讀取）」**（`reader: "zip_lines"`），只做這一種。CSV、Excel、Parquet 不支援 zip；需要時先解壓縮。
2. **兩欄設定**：
   - `files`：zip 的清單，和現在的檔案清單一樣，可以有好幾項、可以用萬用字元，例如 `data/2026*.zip`。
   - `members`：zip 裡要讀的檔，是 **zip 裡的相對路徑**，例如 `2026*/HEADER*.TXT`。
   - zip 檔名和 zip 裡的資料夾都可以用萬用字元。
3. **`members` 比對完整路徑、不分大小寫**：
   - `*`：一層裡的任何字，不跨過 `/`。
   - `**`：任何層數的資料夾，也可以是 0 層。
   - `?`、`[…]`：和檔案清單一樣。
   - `HEADER*.TXT` 只找到最上層；要包含所有資料夾，寫 `**/HEADER*.TXT`。
4. **載入前先掃描**：填好設定就列出每個 zip 裡符合的檔、大小、壓縮方式，以及暫存大約要多少空間。只讀 zip 的目錄，不解壓。
5. **編碼照現在的選項**：自動、UTF-8、cp950、Big5-HKSCS，zip 裡每個檔各自判斷。
   - 「不轉碼」（UTF-8）只是不換編碼。zip 裡的檔不能直接交給 Polars 讀，所以一樣一邊解壓一邊切段，寫成 Parquet 暫存。
6. **處理紀錄**：zip 裡每個檔記解壓後的 SHA-256，**也記 zip 本身的 SHA-256**。
7. **支援 Deflate64**：Windows 檔案總管壓大檔時可能用這種壓縮，要另外加一個解壓的套件。
   - 先查版本、授權、Windows／macOS 的安裝檔，**回報、同意後才加進依賴**，加完實測速度。
   - 一般的 Deflate、不壓縮的 zip 不經過這個套件。
8. **某個 zip 裡沒有符合的檔：略過並提醒**。掃描清單標黃色，處理紀錄也記下來；其他 zip 照樣讀。
9. **不支援，訊息要寫清楚**：
   - 有密碼的 zip：程式不碰密碼，請先解壓縮。
   - zip 裡的 zip、7z、rar。
   - 套件裝不起來時的 Deflate64。

## Recipe 的格式

```json
"input": {
  "reader": "zip_lines",
  "files": ["data/2026*.zip"],
  "members": "2026*/HEADER*.TXT",
  "encoding": "auto"
}
```

- **`members` 的規則**：`reader` 是 `zip_lines` 時必填、不能是空白，其他讀取方式不能有。驗證器（`recipe/validator.py`，只用標準函式庫）也要檢查。
- **一種檔一個階段**：好幾種檔各自一個階段，之後用「結合」或「合併來源檔」接起來，沿用現在的多階段：

  | 階段 | files | members |
  |---|---|---|
  | 主檔 | `data/2026*.zip` | `2026*/HEADER*.TXT` |
  | 明細 | `data/2026*.zip` | `2026*/ITEM*.TXT` |

- **版本**：還沒發版，`RECIPE_VERSION` 維持 1.0，重新產生 schema lock。AI skill 的說明補上這種讀取方式。

## 掃描（載入之前）

1. **找出 zip**：用 `files` 找出所有 zip（沿用 `sources.resolve`）。不是 zip 的檔要報錯。
2. **讀每個 zip 的目錄**（`zipfile.ZipFile.infolist()`，只讀 zip 尾端的目錄），整理每個項目的路徑：
   - `\` 換成 `/`，拿掉開頭的 `./`，資料夾項目（以 `/` 結尾）不算。
   - **檔名的編碼**：有 UTF-8 標記的照用；沒有的，用 zip 原本的位元組依序試 UTF-8 → cp950，都不行才用 cp437（zip 的預設）。台灣的 Windows 壓縮程式通常是 cp950，Mac 常是沒標記的 UTF-8。
3. **比對**：用 `members` 比對整理過的路徑（上面第 3 點的規則）。
4. **每個符合的檔記下**：解壓後大小、壓縮後大小、CRC、壓縮方式，以及有沒有密碼（加密旗標）。
5. **畫面上列出來**：
   ```
   data/2026-01.zip   202601/HEADER_001.TXT   1.2 GB   Deflate
                      202601/HEADER_002.TXT   0.8 GB   Deflate64
   data/2026-02.zip   （沒有符合的檔）          ← 黃色提醒
   共 12 個 zip、23 個檔，解開後約 26.4 GB；暫存大約需要 3 GB，磁碟剩 70 GB
   ```
6. **先擋下的情況**：「載入資料及預覽」「處理並匯出資料」不能按，原因寫在按鈕的提示與讀取資料的面板。
   - 有密碼的檔。
   - 不支援的壓縮方式（Deflate64 的套件沒裝時也算）。
   - 所有 zip 都沒有符合的檔。
   - 磁碟空間不夠。
7. **暫存空間的估計**：
   - 讀的時候：已經寫好的 Parquet，加上最多「工作程序數 × 2」段（每段 32 MB）的文字。
   - 寫好的 Parquet 先用「解開後大小 × 1/5」估，比實測的 1/10 保守。
   - 跟暫存資料夾所在磁碟剩下的空間（`shutil.disk_usage`）比。

## 每個檔用哪種方式解壓

zip 的目錄裡，**每個檔各自記著自己的壓縮方式**（一個數字），還有一個旗標記有沒有加密。Python 讀目錄時，每個檔是一個 `ZipInfo`：`compress_type` 是壓縮方式，`flag_bits & 1` 是有沒有密碼。程式不用猜，掃描時就照這兩欄決定：

| 數字 | 壓縮方式 | 怎麼解 |
|---|---|---|
| 0 | 不壓縮（Stored） | 內建的 `zipfile` |
| 8 | Deflate（最常見） | 內建的 `zipfile`（底層是 zlib） |
| 9 | Deflate64 | 從目錄記的位置讀出壓縮過的原始位元組，交給 Deflate64 的套件 |
| 12 | bzip2 | 內建的 `zipfile` |
| 14 | LZMA | 內建的 `zipfile` |
| 其他（例如 93 Zstandard、99 AES 加密） | — | 不支援：掃描時標出來，不能載入或匯出 |

- **有加密旗標的檔**：一律不支援（請先解壓縮）。
- **同一個 zip 裡可能混著用**：例如 Windows 檔案總管壓縮時，小檔用 Deflate、很大的檔改用 Deflate64。所以是一個檔一個檔判斷，掃描清單也每個檔各自列出壓縮方式。
- **Deflate64 的套件**：沒有裝、或裝了載入失敗時，Deflate64 的檔在掃描時就標成「需要 Deflate64 的套件」。

## 讀取的流程

zip 裡每個符合的檔是一個來源，依 zip 清單的順序、同一個 zip 裡依完整路徑排。

1. **zip 本身的 SHA-256**：每個 zip 從頭讀一遍壓縮過的檔來算，同一個 zip 只算一次，和 zip 的路徑、大小、修改時間一起快取。另開一條執行緒跟著解壓同時做。
2. **編碼：試 UTF-8 的同時就寫暫存**（`auto` 時）。順序和現在一樣是先試 UTF-8 再試 cp950，但不先另外檢查一遍：
   - 直接用 UTF-8 進行下面第 3 步：解壓、切段、寫 Parquet，同時算這個檔的 SHA-256。
   - **UTF-8 的檔：只解壓一遍**就讀完、存好了。
   - **cp950 的檔**：工作程序遇到第一個不合 UTF-8 的位元組就失敗。這時停下，丟掉已經寫的暫存，用 cp950 從頭再解壓一遍。cp950 的檔通常前幾行就有中文，浪費的只有開頭一點點；最壞的情況（很後面才出現第一個中文字）差不多是讀兩遍。
   - 指定了編碼（UTF-8、cp950、Big5-HKSCS）時只解壓一遍。指定 UTF-8 卻遇到不合的位元組就報錯，不改試其他編碼。

   | 編碼設定 | zip 裡的檔實際是 | 解壓幾遍 | 工作程序對每一段做什麼 |
   |---|---|---|---|
   | 自動 | UTF-8 | 1 遍 | 檢查是合法的 UTF-8、單獨的 `\r` 改成 `\n`、第一段拿掉 BOM（不換編碼） |
   | 自動 | cp950 | 第 1 遍很快中斷，再 1 遍 | 第 2 遍用 cp950 轉成 UTF-8 |
   | 指定 UTF-8 | UTF-8 | 1 遍 | 同第一列；不合 UTF-8 就報錯 |
   | 指定 cp950／Big5-HKSCS | cp950／Big5 | 1 遍 | 用指定的編碼轉成 UTF-8 |

3. **寫成 Parquet 暫存**（沿用 `_stage_lines` 的後半段）：
   - 主程序**依序解壓**，在換行後面切段（約 32 MB）。cp950、Big5-HKSCS、UTF-8 的多位元組字元裡都沒有 `0x0A`。
   - 把那一段的位元組交給工作程序，轉成 UTF-8 文字小檔。
     - 新增 `transcode.transcode_bytes`，只用標準函式庫。
     - UTF-8 的檔不換編碼，但一樣檢查是不是合法的 UTF-8、把單獨的 `\r` 改成 `\n`。
   - 主程序照順序把每段寫成 zstd 壓縮的 Parquet，寫完就刪掉那段文字（沿用 `_write_lines`）。
   - 同時在路上的段最多「工作程序數 × 2」個。
   - 比較小的檔，或開不了工作程序時，主程序自己依序轉。
4. **讀完後檢查 CRC**：和 zip 目錄記的一樣，否則報錯（zip 壞了）。
5. **掃描**：和 DESIGN.md §5.4 一樣是 `scan_parquet(這些段)`，照順序編行號。之後的步驟、預覽、串流執行都不用改。
6. **Deflate64**：`zipfile` 不能開這種項目。
   - 自己從目錄記的位置讀壓縮過的原始位元組，交給解壓套件，一邊解一邊接上面第 3 步。
   - CRC 自己用 `zlib.crc32` 算。

原型實測（1 GB cp950、一般的 Deflate）：主程序解壓、8 個工作程序轉碼，約 1 GB/s，結果和整份轉碼完全一樣。Deflate64 的解壓速度要等套件加進來後實測；只有一個程序在解壓，可能會變成瓶頸（30 GB 的檔可能從約 30 秒變成 1～1.5 分鐘）。

## 名稱、快取與處理紀錄

- **來源的名稱**：`__source_file` 是 `zip 的路徑/zip 裡的路徑`，例如 `data/2026-01.zip/202601/HEADER_001.TXT`。行號是那個檔自己的行號；警告、點警告跳到那一列都照常。
- **快取的 key**：zip 的路徑＋大小＋修改時間、zip 裡的路徑＋CRC＋壓縮後大小、編碼。
- **處理紀錄的 `inputs[]`**：
  - 每一項多一個 `archive`：`{path, bytes, sha256}`，記 zip 本身。
  - `path` 是上面的來源名稱，`bytes` 是解壓後大小，`sha256` 是解壓後的內容。
  - 沒有符合檔的 zip 也記下來（`archives_without_matches`），DESIGN.md §9 補說明。
- **「輸出檔不能寫到輸入檔」的檢查**：比對 zip 本身。

## 畫面

- **讀取方式的選單**：多一個「zip 裡的文字檔（逐行讀取）」。
  - 選了之後，「瀏覽檔案…」只選 `.zip`。
  - 檔案清單下面多一欄「zip 裡的檔」（`members`），提示寫法（例如 `2026*/HEADER*.TXT`，`**` 代表任何層數的資料夾）。
- **掃描結果**：顯示在讀取資料的面板（上面「掃描」第 5 步），改了 zip 清單或 `members` 馬上重新掃描。掃描很快，只讀目錄。
- **畫面上的文字**：都用 `tr('中文', 'English')`；Python 的訊息用 `t()`。

## 要改的地方

- **新模組 `src/tablade/ingest/zip_lines.py`**（只用標準函式庫）：
  - 讀目錄、整理路徑與檔名編碼、`members` 的比對（含 `**`、不分大小寫）。
  - 依序讀出一個檔的內容（Deflate 用 `zipfile`，Deflate64 用套件）。
  - 估暫存空間。
- **`src/tablade/ingest/transcode.py`**：新增 `transcode_bytes(job)`，把一段位元組轉成 UTF-8 文字小檔，給工作程序用。
- **`src/tablade/ingest/sources.py`**：
  - `scan_parts` 支援 `zip_lines`，`peek_columns` 回傳 `["line"]`。
  - 準備 zip 裡的檔（判斷編碼、寫 Parquet 暫存、快取）。
  - 讓 `_segments` 的段也可以來自「依序讀出的位元組」，不只是「原檔的位置」。
- **Recipe 的結構與驗證**：
  - `src/tablade/recipe/schema.py`：`reader` 加 `zip_lines`、`members` 欄位。
  - `src/tablade/recipe/validator.py`：`members` 的規則。
  - 重新產生 lock。
- **輸出與處理紀錄**：`src/tablade/export/manifest.py` 記 `archive`、沒有符合檔的 zip；`src/tablade/export/outputs.py` 的輸入檔比對。
- **畫面**：
  - `src/tablade/session.py`：讀取設定的 `members`、掃描結果放進畫面狀態、按鈕能不能按的原因。
  - `src/tablade/api.py`、`frontend/src/bridge.ts`、`frontend/src/components/SourcePanel.tsx`、`frontend/src/mock.ts`。
- **AI skill**：`src/tablade/skill.py` 的說明。
- **測試資料**：`src/tablade/fixtures.py` 加上產生 zip 的假資料：
  - 每個月一個 zip，裡面有資料夾和兩種檔，內容用門市銷售的假資料。
  - cp950 的檔名沒標 UTF-8，以及 UTF-8 檔名。
  - 有一個 zip 沒有符合的檔。
- **依賴（同意後才加）**：Deflate64 的解壓套件，固定版本，寫進 `pyproject.toml` 與 `uv.lock`，確認 PyInstaller 打包得進去。

## 測試

- **比對**：完整路徑、`*` 不跨 `/`、`**` 任何層數（含 0 層）、`?`、`[…]`、不分大小寫、`\` 與 `./` 的整理、資料夾項目不算。
- **檔名編碼**：UTF-8 標記、沒標記的 UTF-8、cp950，掃描與 `__source_file` 都是正確的中文。
- **讀出來和解開後的檔一樣**：zip 裡的 cp950 與 UTF-8 檔用 `zip_lines` 讀，和解開後用 `text_lines` 讀，行、行號、行數、警告完全一樣（串流＝整份）。要涵蓋：空行、最後一行沒換行、`\r\n`、單獨的 `\r`、BOM。
- **自動判斷編碼**：UTF-8 的檔只解壓一遍（數解壓的次數）；cp950 的檔改用 cp950 重讀，結果正確、第一遍寫的暫存都清掉；很後面才出現第一個中文字的 cp950 檔也讀得對。
- **平行＝單核心**：在路上的段不超過「工作程序數 × 2」，暫存資料夾裡只剩 Parquet。
- **處理紀錄**：有 zip 本身的 SHA-256、每個檔解壓後的 SHA-256，以及沒有符合檔的 zip。
- **擋下與訊息**：
  - 有密碼的 zip：標準函式庫寫不出來，在測試裡改 zip 標頭的加密旗標。
  - 不支援的壓縮方式：在測試裡改壓縮方式的欄位。
  - CRC 不符。
  - 不是 zip 的檔。
  - 空間不夠：替換 `shutil.disk_usage`。
  - 以上都有中英文。
- **Deflate64**：套件加進來後，用套件產生的 Deflate64 檔（或事先做好、只放假資料的小檔）測試讀得對。
- **驗證器與 schema**：`members` 必填、空白、其他讀取方式帶了 `members`。

## 驗證

1. **全部檢查**：
   - `uv run pytest -q`
   - `uv run python scripts/check_no_data.py --all`
   - `uv run python scripts/check_code_policy.py`
   - `uv run python scripts/check_i18n.py`
   - `uv run ruff check . && uv run ruff format --check .`
   - `npm --prefix frontend run lint && npm --prefix frontend run build`
   - `uv run tablade gui --smoke`
2. **實測**：12 個假資料的 zip（每個約 1 GB 解開後，一般的 Deflate；另做一個 Deflate64 的），量這些，寫進 improve.md：
   - 掃描時間
   - 準備檔案的時間
   - 暫存資料夾的最高與轉完的大小
   - 記憶體
   - 串流匯出的時間
3. **開發畫面**：中文、英文各看一次掃描清單、黃色提醒、擋下的原因。
4. **打包**：`uv run python scripts/build.py --gui-smoke`。
5. **完成後回報，等你說了才 commit。**

## 不做

- CSV、Excel、Parquet 讀 zip。
- 有密碼的 zip、zip 裡的 zip、7z、rar。
- 自動找出 zip 裡有哪些種類的檔（`members` 要自己寫；掃描清單會列出找到的檔，寫錯馬上看得出來）。
