# 換行：只認 CRLF（欄位裡的換行不切行）

- 狀態：**計畫，尚未實作**（2026-11-20）。
- 範圍：讀取方式「逐行讀取」（`text_lines`）與「zip 裡的文字檔」（`zip_lines`）。Recipe 多一個設定，`RECIPE_VERSION` 1.1 → 1.2（相容升級）。

## 問題

有些系統匯出的文字檔，每一筆資料都用 CRLF（`\r\n`）結尾，但某個文字欄位（例如摘要）的內容裡有人按了 Enter，多出一個單獨的 LF（`\n`）或 CR（`\r`）。

Tablade 現在的逐行讀取把這三種都當成換行：

- `\n` 與 `\r\n`：Polars 的 `scan_lines` 在 `\n` 切行，行尾的 `\r` 拿掉。
- 單獨的 `\r`：轉碼時改成 `\n`（`transcode.decode_range` 的 `LONE_CR`），UTF-8 的檔有單獨的 `\r` 時也先轉成暫存檔（`sources.prepare` 的 `lone_cr`）。

所以那一筆被切成兩行：後半段變成獨立的一行（常常是 `"` 開頭，因為那個欄位用引號包著），行號也跟著多一號。之後「依分隔符號拆成多欄」時兩行都拆錯。

常見的樣子（數字是舉例）：一個 cp950 的 `|` 分隔檔，十幾萬行全部是 CRLF，只有 1、2 個單獨的 LF，在一個用引號包著的文字欄位裡；`tablade profile` 的換行統計會寫「CRLF 120,000, LF 2」。

## 做法

讀取資料多一個設定「換行」：

| 值 | 畫面上的名稱 | 意思 |
|---|---|---|
| `any`（預設） | 都算換行 | 和現在一樣：`\r\n`、`\n`、單獨的 `\r` 都切行 |
| `crlf` | 只認 CRLF | 只有 `\r\n` 切行；單獨的 `\n`、`\r` 照原樣留在那一行的文字裡 |

- 欄位裡的換行**照原樣留著**（`line` 裡就是 `\n` 或 `\r`），不偷偷換成空白：資料不變，要清掉的話用「清理文字」的「移除看不見的字元」（格子裡的換行會換成一個空白，`steps/text.py` 的 `LINE_BREAK`）。
- 只有 `text_lines`、`zip_lines` 能設；其他讀取方式設了是錯誤（和 `decode_errors` 一樣，pydantic 與驗證器都擋）。CSV 本來就認引號，Excel、Parquet 沒有換行的問題。
- 預設值不寫進 Recipe（和 `decode_errors` 一樣，`session._stage_dict` 拿掉）：沒用到的 Recipe 舊版的 Tablade 還能開。

### 怎麼讓 `scan_lines` 只在 CRLF 切行

`scan_lines` 只會在 `\n` 切（實測：`a\r\n` → `a`；`c1\rc2\r\n` → `c1\rc2`，單獨的 `\r` 本來就留在行裡）。所以 `crlf` 模式只要處理單獨的 `\n`：

1. 轉成 UTF-8 文字時（`transcode.decode_range`），`crlf` 模式：
   - 單獨的 `\r` **不改成** `\n`（現在會改）。
   - 單獨的 `\n`（前面不是 `\r`）換成一個佔位字元 `U+FDD0`（Unicode 的「非字元」，正常的文字不會用到）。跨兩塊的 `\r\n` 已經有 `carry` 處理，照同樣的方式判斷塊開頭的 `\n` 前面是不是 `\r`。
2. 寫成暫存 Parquet（`_write_lines` 的 `scan_lines`）之後，讀回來時把佔位字元換回 `\n`：`_scan_one` 的 `line` 加 `.str.replace_all("﷐", "\n", literal=True)`（一般的運算式，串流引擎可以用）。
3. 原檔就是 UTF-8 的：
   - 沒有單獨的 `\n` 時直接 `scan_lines`，不用轉（單獨的 `\r` 留在行裡，正是要的）。
   - 有單獨的 `\n` 時照現在「有單獨的 `\r`」的做法先轉成暫存檔（`convert("utf-8-sig")`）。`_check_utf8` 要多回報「有沒有單獨的 `\n`」。
   - 原檔裡已經有 `U+FDD0` 的話（幾乎不可能）：`crlf` 模式報錯，說明這個檔不能用「只認 CRLF」。轉碼時順便檢查，不另外讀一遍。

### 切段與行號

現在好幾個地方用「`\n` 與單獨的 `\r`」算換行，`crlf` 模式都要改成只算 `\r\n`：

| 地方 | 現在 | `crlf` 模式 |
|---|---|---|
| `transcode.LineCounter`、`line_breaks` | `\n` 與單獨的 `\r` | 只算 `\r\n`（塊結尾的 `\r` 一樣要看下一塊開頭） |
| `transcode.segment_bounds` / `_after_newline`（平行轉碼的切點） | `\n` 後面 | `\r\n` 後面：在單獨的 `\n` 後面切，會把一筆切成兩段 |
| `sources._cut`（zip 裡的檔的切點） | 最後一個 `\n` 後面，沒有時最後一個單獨的 `\r` 後面 | 最後一個 `\r\n` 後面 |
| `sources._check_utf8`、`_line_of`（解碼錯誤的行號） | `LineCounter` | 同上 |
| 寬鬆解碼的 `replaced_lines` | `LineCounter` | 同上 |
| `__line_no` | Parquet／`scan_lines` 的列號 | 不用改：一筆一列，列號就是第幾筆 |

- 函式多一個參數（例如 `breaks: Literal["any", "crlf"] = "any"`），一路從 `scan_parts` 傳下去：`prepare`、`prepare_member`、`_stage_lines`、`_segments`、`_stream_segments`、`_stage_member`、工作程序的 job（`transcode_segment`、`transcode_bytes` 的 tuple 多一項）。
- 暫存的快取 key 加上這個設定（`prepare`、`prepare_member` 現在已經有 `lines`、`errors`）。
- 一筆都找不到 `\r\n`（例如整個檔只用 `\n`）卻選了 `crlf`：整個檔會變成一行。轉碼時數得到，**報錯**：「這個檔沒有用 CRLF 換行（只有 LF），不能選「只認 CRLF」」。`\r\n` 比單獨的 `\n` 少很多時也報錯（門檻：單獨的 `\n` 超過 `\r\n` 的一半），避免讀成幾筆超長的行。

### 警告：欄位裡留下了幾個換行

和寬鬆解碼的警告一樣（`decode_warning`），`crlf` 模式時每個檔記下：

- 留在行裡的換行有幾個、前 10 個在第幾筆（行號用 `crlf` 的算法）。
- 畫面：讀取資料下面的黃色警告、下方的警告分頁、步驟清單的「讀取資料」（和 `decode_warnings` 放在一起，改名成 `input_warnings`）。
  - 中文：「有 3 筆的欄位裡有換行，照原樣留在同一行（第 1,203、5,871、9,012 行）」
  - 英文照 `docs/i18n-plan.md` 的用語。
- 處理紀錄（manifest）的 `inputs[]` 多 `inner_breaks`（只有大於 0 時才寫）。
- `SourceFile`、`Prepared` 多 `inner_breaks: int = 0`、`inner_break_lines: tuple = ()`。

### 「都算換行」時的提示

很多人不知道有這個選項。`any` 模式下，如果一個檔的 `\r\n` 佔了 99% 以上，又有幾個單獨的 `\n` 或 `\r`，載入後顯示一個提示（不是警告）：

- 「這個檔用 CRLF 換行，但有 3 個單獨的 LF：可能是欄位裡的換行，被切成了兩行。可以把「換行」改成「只認 CRLF」（Recipe：input 的 `"line_breaks": "crlf"`）。」
- 數字從現有的檢查順便算：UTF-8 檔的 `_check_utf8`，要轉碼的檔在轉碼時。不另外讀一遍。
- `tablade profile` 的換行統計後面也加同樣的一句（給 AI skill 看）。

## Recipe 與畫面

- `recipe/schema.py`：`Input.line_breaks: Literal["any", "crlf"] = Field(default="any", title="換行", json_schema_extra={"ui_labels": {"any": "都算換行", "crlf": "只認 CRLF"}, **en("Line breaks", ui_labels=…)})`；`_members_only_for_zip` 旁邊加「只有逐行讀取、zip 裡的文字檔能設」的檢查。
- `recipe/validator.py`：`link_issues` 加同樣的規則（路徑 `["stages", si, "input", "line_breaks"]`，訊息和 pydantic 一樣）。
- `RECIPE_VERSION = "1.2"`，`uv run python scripts/update_schema_lock.py`。AI skill 的檔名跟著變成 v1.2。
- `session.py`：`DEFAULT_INPUT["line_breaks"] = "any"`；`_stage_dict` 是預設值或不是逐行讀取時拿掉；`set_input` 接受；`_source_key` 的 `reading` 加上它（改了要重新載入）；`scan_parts(line_breaks=…)`。
- `engine/run.py` 的 `scan_stages`、`check.py` 的報告（每個檔的警告）。
- 前端：`SourcePanel.tsx` 在「解不開的字」旁邊加「換行」的選單（只有 `text_lines`、`zip_lines` 顯示），說明文字：「欄位裡有換行、一筆被拆成兩行時，選「只認 CRLF」」；`bridge.ts` 的型別；提示與警告的顯示。
- `skill.py` 的 SKILL.md 加一段：什麼時候用 `"line_breaks": "crlf"`（profile 的換行統計是 CRLF 為主、又有少數 LF 或 CR 時）。
- 文件：DESIGN.md §5（讀取方式與編碼）、§9（manifest）；README 兩份的讀取方式那一段與限制；`docs/i18n-plan.md` 的用語表（換行／Line breaks、都算換行／Any line break、只認 CRLF／CRLF only）。

## 假資料（`fixtures.py`）

- `inner_breaks_report(seed=0, rows=200)`：`|` 分隔、CRLF 換行的明細（門市銷售），其中幾筆的「備註」欄用引號包著、內容有單獨的 `\n`，一筆有單獨的 `\r`。回傳（位元組, 每一筆的內容, 有換行的筆是第幾筆）。
- `write_inner_breaks(path, encoding)`：cp950 與 UTF-8 各一個。
- `write_inner_breaks_zip(path)`：放進 zip（`zip_lines`）。
- `only_lf_lines()`：整個檔只用 `\n`（測「選了 crlf 卻沒有 CRLF」的錯誤）。

## 測試（新的 `tests/test_line_breaks.py`）

- 每種來源（cp950 轉碼、UTF-8 直接讀、UTF-8 有單獨 `\n` 要轉、zip 裡的檔）：
  - `any`：和現在一樣（有換行的那幾筆被切開，行數比筆數多）。
  - `crlf`：行數＝筆數，有換行的那幾筆完整，`line` 裡的 `\n`、`\r` 照原樣；行號＝第幾筆。
- 平行轉碼與 zip 的切段：段的大小調很小（每段幾百 bytes），單獨的 `\n` 剛好在段的邊界附近，結果和單核心一樣；`\r\n` 跨兩塊也一樣。
- 寬鬆解碼＋`crlf`：`replaced_lines` 的行號用 `crlf` 的算法。
- 只有 LF 的檔選 `crlf`：報錯，訊息有「只認 CRLF」。
- 原檔有 `U+FDD0`：`crlf` 報錯。
- 警告：數量、前 10 筆的行號、門檻、中英文；manifest 的 `inner_breaks`。
- `any` 模式的提示：CRLF 為主＋少數 LF 時出現，全部 CRLF 或全部 LF 時不出現；`profile` 的那一句。
- Recipe：其他讀取方式設 `crlf` 時 pydantic 與驗證器都報錯；預設值不寫進 Recipe；舊的 1.1 Recipe 照樣能開。
- 串流＝eager（`tests/test_large.py` 的寫法）：`crlf` 時匯出的結果和預覽一樣。
- 「依分隔符號拆成多欄」接在 `crlf` 後面：有換行的那幾筆拆出來的欄數正確（引號裡的 `\n` 留在欄位裡）。

## 不做的事

- 不做「依引號判斷換行」（引號裡的換行不算）：那就是 CSV 的讀法，要 CSV 用「CSV」讀取方式。逐行讀取的檔常常不是規矩的 CSV（固定寬度、頁首頁尾、沒成對的引號），用引號判斷反而會把很多行黏在一起。
- 不自動切換成 `crlf`：只顯示提示，讓使用者自己選（換行的判斷會改變行號，不能默默改）。
- 「欄位裡有沒加引號的分隔符號」（例如文字欄裡有 `|`，拆欄時欄位往後移）是另一個問題，這次不處理。之後可以考慮在「依分隔符號拆成多欄」加「段數比欄名多時，多的段併回指定的欄」。
