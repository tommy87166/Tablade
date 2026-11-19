# 寬鬆解碼（造字與解不開的字換成 �）

- 狀態：**已實作**（2026-11-19）。現在的做法以 DESIGN.md §5.2、§9 為準；和計畫不同的地方：
  - `SourceFile`／`Prepared` 另外多一個 `auto_picked`（`auto`＋寬鬆取樣後選的編碼，警告寫「自動選了…」用）。
  - `utf-8-sig` 的 BOM 改由 `decode_range` 自己拿掉：`utf-8-sig` 回報的錯誤位置不算 BOM 那 3 個位元組，行號會算錯。
  - 前端的「解不開的字」寫在 `SourcePanel.tsx`（讀取資料的設定本來就是手寫的，不是由 schema 產生）。
- 實作後的審查（2026-11-19）：發現寫在 docs/review-findings.md 的 F-077～F-082，還沒修。
- 這是簡化後的版本：原本另外規劃的「cp950＋造字區」編碼不做了，原因見「已決定」與最後的「不做的事」。
- 2026-11-19 對照目前的程式審查過一次，並用原型驗證了做法（見「審查紀錄」）。

## 背景

台灣的系統匯出的 Big5／cp950 文字檔，偶爾會用到**造字區**的字：標準沒有的罕見字（常見是人名、地名），公司用 Windows 的造字程式畫好，放進 Big5 保留給使用者的空號。

- 造字區：`81 40`～`A0 FE`、`C6 A1`～`C8 FE`、`FA 40`～`FE FE`。那個字是什麼、長什麼樣子，只有造字的那台電腦（那家公司的造字檔）知道。
- Python 的 `cp950` 不認造字區，碰到就丟出 `UnicodeDecodeError`。Tablade 現在一律報錯，**一個造字就讓整個檔讀不了**。

改選 `big5hkscs` 不是好辦法。實測把 cp950 解得開的每個 2 位元組字逐一比對：

- **造字會變成別的字**：香港字集把同一個空號拿去放別的字，例如 `8F 46` 讀成「𣅽」，看起來像真的字，最容易被誤當成正確資料。
- **255 個字和 cp950 對應不同**，而且不會報錯：
  - 長得很像的符號，例如 `～`→`∼`、`‧`→`•`、`￥`→`¥`。篩選、取代、結合時會對不到。
  - `C6 A1`～`C7 FC` 整段錯開：cp950 的日文假名、俄文字母、圈號數字，在 big5hkscs 裡是別的字（例如「あ」讀成「⑥」）。
- **7 個字 big5hkscs 解不開**，包括歐元符號 `€`（`A3 E1`）。

另外，有時檔案裡真的有壞掉的位元組，使用者仍希望先讀進來、只要提醒。Python 內建的「換成 �」（`errors="replace"`）不能直接用：2 位元組的字只換掉前半個，後半個會漏出來。

| 原始位元組 | `errors="replace"` 的結果 | 問題 |
|---|---|---|
| `8F 46` | `�F` | 多了一個 `F` |
| `8F 7C` | `�\|` | **多了一個 `\|`**，依分隔符號拆欄時欄位會錯位 |
| `8F A1` | `�、`，後面的字也亂掉 | 吃掉後面的字 |

## 已決定

1. **不做造字區的對照**：
   - 不新增編碼；`auto` 的順序不變（UTF-8 → cp950）。現有的 Recipe 讀出來的結果完全不變。
   - 造字和其他解不開的位元組一樣處理：預設報錯，打開寬鬆解碼就換成 `�`。
   - 代價：不同的造字讀進來都是 `�`，分不出來，之後也換不回正確的字。目前不需要這兩件事；之後需要時再加（見「不做的事」）。
2. **寬鬆解碼只開放給逐行讀取**：只有「逐行讀取」（`text_lines`）與「zip 裡的文字檔」（`zip_lines`）可以用。
   - 預設仍然報錯；打開之後，解不開的字換成 `�`，照樣跑下去。
   - 每個檔產生警告，處理紀錄記下換了幾個字。
   - CSV 不開放：CSV 是交給 Polars 整份解析，換字後的位置錯了很難察覺，影響太大。CSV 要寬鬆，可以改用「逐行讀取＋依分隔符號拆成多欄」。
3. **2 個位元組的字換成兩個 `�`**，單獨一個解不開的位元組換成一個 `�`。
   - 後半個位元組不會漏出來。
   - 固定位置的報表靠位元組數對齊。`�` 在「依固定位置拆欄」算 1 格，兩個剛好是 2 格，後面的欄位不會偏，那個步驟不用改。
4. **警告只寫總數**：不另外區分是不是落在造字區。
   - 數的是「字」：一個 2 位元組的字算 1 個（畫面上是兩個 `�`）。
5. **讀檔時不丟棄**：要把 `�` 拿掉或換成別的記號，用「取代值」步驟做。這樣 Recipe 與處理紀錄都看得到這個動作。

## 審查紀錄（2026-11-19）

### 用原型驗證過的

原型是一個用 `codecs.register_error` 註冊的處理器，只用標準函式庫。

- **Python 回報的錯誤範圍只有 1 個位元組**：`8F 46`、`8F 7C`、`8F A1` 都是 `start=1 end=2`。所以內建的 `replace` 才會漏出後半個；處理器要自己看下一個位元組，決定一次換掉 1 個還是 2 個。
- **換字的結果**：

  | 原始位元組 | 內建 `replace` | 原型 |
  |---|---|---|
  | `A 8F 46 B` | `A�FB` | `A��B` |
  | `A 8F 7C B` | `A�\|B` | `A��B` |
  | `A 8F A1 B` | `A�、`（吃掉 `B`） | `A��B` |
  | `A 80 B` | `A�B` | `A�B` |
  | `A FF 41 B` | `A�AB` | `A�AB` |
  | `A 8F 0A B`（lead byte 接著換行） | `A�` 換行 `B` | `A�` 換行 `B` |
  | `A 8F`（檔尾只有半個字） | `A�` | `A�` |

- **跨塊沒問題**：把一段含 `8F 46`、`8F 7C`、`80` 的資料在每一個位置切成兩塊，用增量解碼器分兩次解，結果都和整份一次解相同。
- **處理器拿到的位置**：增量解碼器會把上一塊結尾的半個字留著（`getstate()[0]`），下一塊進來才呼叫處理器。這時 `err.object` 是「留著的位元組＋這一塊」，`err.start` 是在它裡面的位置。換算成檔案位置要減掉留著的長度。
- **速度**：約 60 MB 的 cp950 資料，沒有解不開的字時，掛著處理器 0.17 秒、不掛 0.16 秒，沒有差別。每 20 行一個造字（共 61,225 個）時 0.22 秒。
- **其他編碼**：`big5hkscs` 同一套規則可用；UTF-8 的結果和內建 `replace` 相同（差別只在處理器另外數了字數）；`utf-8-sig` 也可以用。
- **取樣判斷編碼**（`auto`＋寬鬆用）：同一段資料分別當 UTF-8、cp950 解，數解不開的字。

  | 資料 | 當 UTF-8 解 | 當 cp950 解 |
  |---|---|---|
  | 有 1 個造字的 cp950 檔 | 26,001 個 | 1 個 |
  | 壞了 1 個字的 UTF-8 檔 | 1 個 | 33,999 個 |

  差距很大，取一小段就分得出來，不用把整個檔再解一遍。
- **固定位置的對齊**（用目前的「依固定位置拆欄」實測）：造字換成兩個 `�` 時金額仍是 `12,280`；換成一個就偏一格，變成 `1` 與 `2,280`。

### 審查後調整的地方

和上一版計畫不同的有這幾項：

1. **`auto`＋寬鬆改成取樣**：上一版是兩種編碼各把整個檔解一遍來數。幾 GB 的檔要多讀一到兩遍；改成只取 4 MB 比較（見「實作方式」第 3 節）。
2. **驗證器的檢查放在 `link_issues`**：上一版寫 `step_rules`，那是步驟的規則。`members` 的檢查在 `recipe/validator.py` 的 `link_issues`（第 1312 行附近），`decode_errors` 放同一個地方。
3. **統計用一個收集物件往下傳**：`_stage_lines`、`_stage_member` 的回傳值不變，自我檢測與既有的測試（共 10 幾處呼叫）不用跟著改。
4. **行號只數 `\n`**：和現在解碼失敗的錯誤訊息同一種算法。只用 `\r` 換行的檔行號會不準（寫進「已知的限制」）。
5. **警告的文字由 Python 產生**：畫面只負責顯示，符合「Python 產生的文字用 `t()`」的規則。
6. **「可能選錯編碼」的門檻改用字數**：我們只記前 10 個行號，沒有「換字的行數」。改成「換掉的字數超過總行數的 1%」。
7. **匯出結果的警告數分開算**：每個有換字的檔算 1 則，另外放在 `input_warnings`，不併進步驟的警告數（那個數字和警告清單是成對的）。

## 實作方式

### 1. 換字的處理器（`ingest/transcode.py`）

`transcode.py` 只用標準函式庫，平行轉碼的工作程序也會載入它，所以換字放這裡。

- **處理器 `tablade-lenient`**：模組載入時用 `codecs.register_error` 註冊，工作程序 import 時也會跟著註冊。
  - **多位元組編碼**（cp950、big5hkscs）：
    - `err.start` 那個位元組是 lead byte（`81`～`FE`），下一個是合法的 trail（`40`～`7E`、`A1`～`FE`）時，回傳 `("��", start + 2)`。
    - 否則回傳 `("�", start + 1)`。
    - 換行 `0A` 不是合法的 trail，所以不會被吃掉，「在換行處切段」的規則照舊成立。
  - **UTF-8**：回傳 `("�", err.end)`，和內建的 `replace` 一樣。
- **計數**：
  - 模組裡放一個 `threading.local()`。`decode_range` 在解每一塊之前，把一個清單掛上去；處理器每被呼叫一次，就把 `err.start` 記進清單。解完這一塊再拿下來。
  - `decode_range` 把每筆換算成「這一段裡第幾行」：
    - 檔案位置＝這一塊的起點 − 解碼器留著的位元組數（解之前的 `decoder.getstate()[0]`）＋ `err.start`。
    - 行號＝這一段到目前為止的 `\n` 數＋這一塊在那個位置之前的 `\n` 數。
  - 每一段只留前 10 筆位置，字數全部都數。
- **`decode_range` 的介面**：
  - 多一個參數 `errors`（`"strict"` 預設，或 `"tablade-lenient"`）。
  - 回傳值從 `Failure` 改成 `(Failure, 統計)`；統計是 `(換掉的字數, 前 10 筆的行號, 這一段共有幾個換行)`，都是可以跨程序傳的基本型別。
- **工作程序的函式**：`transcode_segment`、`transcode_bytes` 的 job 多帶一個 `errors`，回傳值一樣改成 `(Failure, 統計)`。

### 2. 把統計帶回主程序（`ingest/sources.py`）

- **新的小類別 `Replaced`**：`count`（換掉的字數）、`lines`（前 10 個行號）、`newlines`（已經收到的段共有幾個換行）。
- **`_segments`**（一般的檔）、**`_stream_segments`**（zip 裡的檔）：
  - 多兩個參數：`errors`、`replaced`（`Replaced` 或 `None`）。
  - 每收到一段的結果，就把字數加進 `replaced.count`；行號加上 `replaced.newlines` 之後接進 `replaced.lines`（滿 10 個就不再加）；再把這一段的換行數加進 `replaced.newlines`。
  - 段是照檔案順序收的，所以行號是對的；平行轉碼和單一程序的結果一樣。
- **`_stage_lines`**、**`_stage_member`**：多同樣兩個只給關鍵字的參數，往下傳。回傳值不變。
  - 開不了工作程序、改用單核心重來時，`replaced` 要先清空。
- **CSV 走的那條路**（`_transcode`、`_transcode_single`、`_transcode_parallel`）：一律用 `strict`，只配合新的回傳值多拆一層，行為不變。

### 3. 準備檔案（`ingest/sources.py`）

- **`Prepared`、`SourceFile`** 各加兩個有預設值的欄位，放在最後面：
  - `replaced: int = 0`：換掉的字數。
  - `replaced_lines: tuple[int, ...] = ()`：前 10 個行號。
- **`prepare(path, encoding, progress, lines, errors="strict")`**：
  - 快取的 key 加上 `errors`。
  - `convert(candidate, errors)` 內部建立一個 `Replaced` 傳給 `_stage_lines`，把結果放進 `Prepared`。
  - 各種情況：

    | 編碼設定 | 嚴格（現在的行為） | 寬鬆 |
    |---|---|---|
    | `utf-8`／`utf-8-sig`，檔案是合法的 UTF-8 | 直接讀原檔 | 同左，沒有換字 |
    | `utf-8`／`utf-8-sig`，有壞位元組 | 報錯 | 用 UTF-8 寬鬆轉成暫存的 Parquet |
    | `cp950`／`big5hkscs` | 轉碼，解不開就報錯 | 用同一個編碼寬鬆轉 |
    | `auto` | UTF-8 → cp950，都失敗就報錯 | 見下面 |

- **`auto`＋寬鬆**：
  1. 先照現在的流程走：`_check_utf8` 檢查 UTF-8，不行再用 cp950 嚴格轉。有一個成功就用它，沒有換字，速度也和現在一樣。
  2. 兩種都失敗時取樣：
     - `_check_utf8` 多回傳「第一個不合 UTF-8 的位置」。
     - 從那個位置所在的那一行開頭起，讀最多 4 MB，切在行尾。
     - 這一段分別當 UTF-8、cp950 用寬鬆模式解（只在記憶體裡解，不寫暫存），數換掉的字數。
  3. 選字數少的那個；一樣多時照 `auto` 的順序選 UTF-8。用它寬鬆轉整個檔。
  4. 警告多寫一句：選了哪個編碼。
  - 有造字的 cp950 檔在這條路上會多花一些時間：第 1 步的 cp950 嚴格轉碼做到第一個造字才失敗，那一段是白做的。不想多等可以把編碼明確選成 `cp950`。
- **`prepare_member(member, name, encoding, progress, errors="strict")`**（zip 裡的檔）：
  - 快取的 key 加上 `errors`；`stage(candidate, errors)` 同樣傳 `Replaced`。
  - `auto`＋寬鬆的取樣：zip 裡的檔不能跳著讀。UTF-8 嚴格轉失敗時，失敗的那一段（最多 4 MB）還在記憶體裡，把它掛在 `DecodeError` 上帶出來當樣本，不用重新解壓。
- **`scan_parts(…, decode_errors="error")`**、**`_scan_zip_parts(…)`**：
  - 多一個參數，`"replace"` 時把 `errors="tablade-lenient"` 傳給 `prepare`／`prepare_member`。
  - 只有 `reader` 是 `text_lines`、`zip_lines` 時才傳；其他讀取方式忽略。
  - 建立 `SourceFile` 的兩個地方（第 1109、1330 行）帶上 `replaced`、`replaced_lines`。
- **嚴格模式的錯誤訊息**：`prepare`（`lines=True` 時）與 `prepare_member` 丟出 `DecodeError` 時，在原本的訊息後面加一句提示「可以把『解不開的字』改成『換成 �』」。CSV 不加。
- **警告的文字**：新函式 `decode_warning(file: SourceFile) -> str | None`，沒有換字時回傳 `None`。
  - 「有 3 個字解不開，換成 �（第 12、80、81 行）」；超過 10 個時行號後面加「…」。
  - 換掉的字數超過總行數的 1% 時，後面加「，可能選錯編碼」。
  - `auto` 取樣選出編碼時，後面加「（自動選了 cp950）」。

### 4. Recipe 與驗證

- **`recipe/schema.py` 的 `Input`**：
  - 加 `decode_errors: Literal["error", "replace"] = "error"`，標題「解不開的字」，選項文字「報錯」「換成 �」。
  - 加一個 `model_validator`：`"replace"` 只有 `reader` 是 `text_lines`、`zip_lines` 時才能用（寫法照旁邊的 `_members_only_for_zip`）。
  - `RECIPE_VERSION` 從 `"1.0"` 升到 `"1.1"`（相容：舊的 recipe 照樣讀），執行 `uv run python scripts/update_schema_lock.py`。
- **`recipe/validator.py` 的 `link_issues`**：在 `members` 的檢查旁邊加同樣的規則，訊息和 pydantic 的一致。
- **`session.py`**：
  - `DEFAULT_INPUT` 加 `"decode_errors": "error"`。
  - `_stage_dict`：讀取方式不是逐行讀取或 zip，或值是 `"error"` 時不寫進 Recipe（和 `members` 一樣，Recipe 比較好讀）。
  - `set_input` 接受的欄位加 `decode_errors`。
  - `_source_key` 的讀取設定加 `decode_errors`：改了之後這個階段要重新載入。
  - `_load_stage` 呼叫 `scan_parts` 時傳 `decode_errors`。
- **`engine/run.py` 的 `scan_stages`**：呼叫 `scan_parts` 時傳 `source.decode_errors`。

### 5. 警告、處理紀錄與畫面

- **處理紀錄**（`export/manifest.py`）：`inputs[]` 每個檔有換字時，加 `decode: {"replaced": 3, "replaced_lines": [12, 80, 81]}`。
- **執行報告**（`engine/run.py`、`engine/check.py`）：
  - `RunReport` 加 `input_warnings`：有換字的檔數。
  - `format_report` 的輸入檔那一行，後面接 `decode_warning()` 的文字（命令列與錯誤報告都看得到）。
- **畫面狀態**（`session.py`）：
  - `state.input.sources` 本來就是把 `SourceFile` 整個送出去，新欄位會自動帶上。
  - 另外加 `state.input.decode_warnings`：這個階段每個有換字的檔一則（檔名＋`decode_warning()` 的文字）。
  - `export()` 的結果加 `input_warnings`。
- **前端**：
  - `SourcePanel.tsx`：編碼的下面加「解不開的字」的選項（報錯／換成 �），只有逐行讀取與 zip 裡的文字檔顯示。
  - `SourcePanel.tsx`：`decode_warnings` 用黃色列出來。
  - `App.tsx`：匯出摘要在警告數後面加「N 個檔有解不開的字」。
  - `bridge.ts`：`SourceFile`（第 194 行起）加 `replaced`、`replaced_lines`；`ViewState['input']` 加 `decode_errors`、`decode_warnings`；匯出結果的型別加 `input_warnings`。
  - `mock.ts`、`scripts/gen_frontend_dev_data.py`：假資料跟著加。
- **中英文**：都用 `t()`／`tr()`。`docs/i18n-plan.md` 的對照表加：解不開的字／Undecodable characters、報錯／Report an error、換成 �／Replace with �。

### 6. 文件與 AI skill

- `skill.py`（SKILL.md 講讀取設定的那一段，第 281～293 行附近）：
  - `decode_errors` 只有逐行讀取與 zip 裡的文字檔能用，預設是報錯。
  - 換成 `�` 之後，可以用「取代值」步驟拿掉或換成別的記號。
- DESIGN.md §5.2（編碼）補上寬鬆解碼；§9 的處理紀錄範例加 `decode`。
- 兩份 README 的功能說明與已知限制同步修改。

## 會碰到的內容

| 檔案 | 要改的地方 |
|---|---|
| `src/tablade/ingest/transcode.py` | 新的處理器與計數；`decode_range`、`transcode_segment`、`transcode_bytes` 的參數與回傳值 |
| `src/tablade/ingest/sources.py` | `Prepared`、`SourceFile`、新的 `Replaced`；`_check_utf8`（多回傳位置）；`_transcode_single`、`_transcode_parallel`（配合回傳值）；`_segments`、`_stream_segments`、`_stage_lines`、`_stage_member`；`prepare`、`prepare_member`、`_scan_zip_parts`、`scan_parts`；新的 `decode_warning` |
| `src/tablade/recipe/schema.py` | `Input.decode_errors` 與它的檢查；`RECIPE_VERSION` |
| `src/tablade/recipe/validator.py` | `link_issues` 加一條規則 |
| `src/tablade/recipe/recipe-schema.lock.json` | 重新產生 |
| `src/tablade/session.py` | `DEFAULT_INPUT`、`_stage_dict`、`set_input`、`_source_key`、`_load_stage`、`state()`（`decode_warnings`）、`export()`（`input_warnings`） |
| `src/tablade/engine/run.py` | `scan_stages` 傳參數；`RunReport.input_warnings` |
| `src/tablade/engine/check.py` | `format_report` 的輸入檔那一行 |
| `src/tablade/export/manifest.py` | `inputs[].decode` |
| `src/tablade/skill.py` | SKILL.md 的說明 |
| `src/tablade/fixtures.py` | 含造字位元組與壞位元組的假資料 |
| `frontend/src/components/SourcePanel.tsx` | 新的選項、黃色的警告清單 |
| `frontend/src/App.tsx` | 匯出摘要 |
| `frontend/src/bridge.ts`、`frontend/src/mock.ts` | 型別與假 API |
| `scripts/gen_frontend_dev_data.py` | 開發用的假資料 |
| `tests/` | 見「測試」 |
| `DESIGN.md`、`README.md`、`README.zh-TW.md`、`docs/i18n-plan.md` | 見上面第 5、6 節 |

### 不用改的地方

因為不新增編碼、`auto` 在嚴格模式下不變，下面這些都不用動：

- `ingest/encoding.py`（`SUPPORTED`、`AUTO_ORDER`），以及走這條路的 `tablade convert`、自我檢測的「讀取 CSV」。
- `selftest.py` 與既有測試裡對 `_stage_lines`、`_stage_member`、`_transcode_single`、`_transcode_parallel` 的呼叫（回傳值不變）。
- `profile.py`、驗證器看檔頭的部分。
- 步驟：`�` 是一般的字元，篩選、取代、結合照常比對；「依固定位置拆欄」因為 2 個位元組換成兩個 `�`，不用改。
- CSV、Excel、Parquet、讀前面階段：完全不受影響。

## 已知的限制

- **不同的造字分不出來**：都是 `��`。拿含造字的欄位當鍵做結合、移除重複、群組彙總時，不同的值會被當成同一個。
- **換不回來**：之後就算知道那個字是什麼，也沒辦法從 `�` 還原。
- **CSV 讀不了有造字的檔**：要改用「逐行讀取＋依分隔符號拆成多欄」。
- **UTF-8 檔的固定位置報表**：壞掉的位元組換成 `�` 之後，原本佔幾格無從得知，後面的欄位可能偏移。Big5 的檔沒有這個問題。
- **只用 `\r` 換行的檔**：警告裡的行號不準（只數 `\n`），和現在解碼失敗的錯誤訊息一樣。
- **本來就是別種編碼的檔**（例如簡體的 GBK）：現在的 `auto` 就會把它當 cp950 讀成亂碼而不報錯（原型實測：解不開的字是 0 個）。這次不處理。

## 測試

- **處理器**（新的 `tests/test_lenient.py`）：
  - 「審查紀錄」那張表的每一列。
  - 每一個切點的增量解碼結果都和整份解碼相同。
  - 3 個 2 位元組的字，字數是 3（不是 6）。
  - 行號：解不開的字在第一行、最後一行、剛好在塊的邊界上。
- **換完之後欄位不亂**：
  - 依分隔符號拆出來的欄數和原本一樣（`8F 7C` 不會多出一個 `|`）。
  - 對齊的固定位置報表裡有造字時，「依固定位置拆欄」的欄位不錯位。
- **跨塊、跨段**：解不開的字剛好在 1 MB 塊、32 MB 段、4 MB zip 段的邊界上；平行轉碼和單一程序的結果、字數、行號都一樣（照 `tests/test_large.py`、`tests/test_zip_lines.py` 現有的比法，把段調小）。
- **嚴格模式不變**：
  - 有造字的 cp950 檔照樣報錯，錯誤訊息多一句提示；CSV 的錯誤訊息沒有那一句。
  - 沒有造字的檔，結果和處理紀錄都和以前一樣。
- **`auto`＋寬鬆**：
  - 沒有解不開的字時，結果和嚴格模式完全相同，沒有警告。
  - 只壞幾個位元組的 UTF-8 檔選到 UTF-8；有造字或只壞幾個位元組的 cp950 檔選到 cp950。
  - zip 裡的檔同樣的兩種情況。
- **recipe**（`tests/test_recipe.py`、`tests/test_review_recipe.py`）：
  - CSV、Excel、Parquet、讀前面階段設 `decode_errors: "replace"`，被 pydantic 與驗證器擋下，兩邊訊息一致。
  - 舊的 1.0 recipe 照樣讀；沒用到寬鬆時，存出來的 Recipe 沒有 `decode_errors`。
- **Session**（`tests/test_session.py`）：改了「解不開的字」之後要重新載入；`state.input.decode_warnings` 與 `export()` 的 `input_warnings`。
- **處理紀錄**（`tests/test_manifest.py`）：有換字的檔有 `decode`，沒有的檔沒有這個欄位。
- **stream ＝ eager**（`tests/test_large.py`）：有換字的檔，兩種執行方式的結果相同。
- **i18n**（`tests/test_i18n*.py`）：新的文字都有英文；英文畫面下警告沒有中文。
- **前端與 API**（`tests/test_app.py`）：`bridge.ts` 與 `api.py` 一致（這次沒有新的 API 方法，只有欄位）。
- **假資料**：`fixtures.py` 產生含造字位元組與壞位元組的門市銷售假資料（文字檔、固定位置的報表、zip），不用真實檔案。

## 驗證

1. `uv run pytest`、`check_no_data.py --all`、`check_code_policy.py`、ruff、前端 lint 與 build。
2. 用假資料在畫面上各試一次：寬鬆讀到造字與壞字元、CSV 看不到寬鬆的選項；中英文各看一次。
3. 拿含造字的真實檔案（只在本機，不進 repo）實際載入，確認打開寬鬆解碼後不再報錯、警告的行號正確、固定位置的欄位沒有偏。
4. 用幾 GB 的假資料量一次：寬鬆模式的時間與記憶體和嚴格模式差不多。
5. 完成後回報，等你說了才 commit。

## 不做的事（之後需要再加）

- **cp950＋造字區**（把造字轉成 Unicode 的私用字元）：
  - 好處：不同的造字分得出來，之後可以用「取代值」換回正確的字；CSV 也讀得了有造字的檔。
  - 這次不做的原因，是它連帶要處理的事很多：
    - 5,968 個碼的對照表，而且要在 Windows 上核對和 Windows 本身的轉法一致。
    - 新的編碼 `cp950-eudc`，`auto` 的順序要改，所有 Recipe 都受影響。
    - 私用字元沒有固定的字形：macOS 多半顯示成方框，少數碼位會顯示成不相干的字；Tablade 的視窗（WebView2）會不會套用系統的造字檔還沒確認。
    - 「依固定位置拆欄」要改：私用字元要算 2 格；內部的佔位字元 `U+E000` 剛好是造字 `FA 40` 對到的字元，要換掉。
  - 之後要加是相容的新增（新的編碼選項），不會影響這次做的寬鬆解碼。
- **警告區分造字與其他解不開的位元組**（多寫一句「其中幾個在造字區」）。
- **CSV 的寬鬆解碼**。
