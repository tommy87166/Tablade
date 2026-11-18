# Tablade 審查發現

- 檢查日期：2026-11-18
- git commit：`6bdf5bc`
- 範圍：程式的 bug、畫面漏翻（中英文切換）、文件與程式不一致，以及每種步驟的測試涵蓋度。只記錄問題，沒有修改任何程式。
- 修正：76 個發現都已修正（commit `2e80b79`、`5181505`、`413c289`；每個發現的「狀態」寫是哪一個，做了取捨的另外寫決定）。修正後 `uv run pytest -q` 是 1053 passed，其他檢查照舊全部通過。
- 做法：一位主要審查者看引擎、Session、API 與文件，另外三位各看一部分（前端、步驟模組、驗證器與 AI skill）。其他三位回報的發現，都由主要審查者重新重現或讀程式確認後才列入；沒有重跑的部分，在「確認程度」寫明。
- 環境：macOS（arm64）、Python 3.13、Node.js 24。Windows 專屬的行為沒有實測（見最後一節）。
- 重現用的資料都是腳本當場產生的假資料，或 `src/tablade/fixtures.py` 產生的。
- 每個發現的腳本都可以存成檔案，在 repo 根目錄用 `uv run python 腳本.py` 執行；資料寫在系統的暫存資料夾，不會動到 repo。

## 基本檢查

| 指令 | 結果 |
|---|---|
| `uv sync` | 通過。以 `uv sync --offline` 執行（不連網路）。 |
| `uv run pytest -q` | 576 passed（約 15 秒） |
| `uv run python scripts/check_no_data.py --all` | 通過（結束代碼 0，沒有輸出） |
| `uv run python scripts/check_code_policy.py` | 通過（結束代碼 0，沒有輸出） |
| `uv run python scripts/check_i18n.py` | 通過（結束代碼 0，沒有輸出） |
| `uv run ruff check .` | All checks passed! |
| `uv run ruff format --check .` | 94 files already formatted |
| `npm --prefix frontend ci` | 通過。以 `--offline` 執行（只用本機快取）：added 128 packages，0 vulnerabilities。 |
| `npm --prefix frontend run lint` | 通過（oxlint 結束代碼 0，沒有輸出） |
| `npm --prefix frontend run build` | 通過。`dist/index.html` 683.02 kB（gzip 201.05 kB），小於 2 MB 的上限。 |

基本檢查全部通過；下面的發現都是這些檢查沒有涵蓋到的情況。

類別：bug、漏翻、文件、測試（測試的缺口，放在「步驟的測試涵蓋度」一節）。

## 總表

| 編號 | 嚴重度 | 類別 | 摘要 | 狀態 |
|---|---|---|---|---|
| F-001 | 高 | bug | 輸出檔名和輸入檔只差大小寫（或 Unicode 的組合方式）時，匯出會蓋掉輸入檔 | 已修（2e80b79） |
| F-002 | 高 | bug | 同一個檔經兩個階段處理、附加後再被下一個階段讀：串流會丟資料，stream 與 eager 結果不同 | 已修（2e80b79） |
| F-003 | 高 | bug | 欄名含大括號時，只要有一則警告，載入、預覽與匯出全部失敗 | 已修（2e80b79） |
| F-004 | 高 | bug | 篩選的「不等於／不包含／不符合」遇到空值會把那一列拿掉 | 已修（413c289） |
| F-005 | 高 | bug | 依固定位置拆欄：℃、×、※ 這類字讓後面的欄位錯位，沒有任何警告 | 已修（413c289） |
| F-006 | 中 | bug | 多個輸出檔不是「全部成功才改名」：後面的檔改名失敗時，前面的已經換成新的 | 已修（2e80b79） |
| F-007 | 中 | bug | 檔名或資料夾名稱有 `[`、`?` 時被當成萬用字元，加入後找不到檔 | 已修（2e80b79） |
| F-008 | 中 | bug | 結合的另一張表是樣本時，預覽仍標「完整模式」，還出現實際上沒有的「找不到」警告 | 已修（2e80b79、413c289） |
| F-009 | 中 | bug | `state.issues` 只在開啟 Recipe 時算一次：修正後、切換語言後都不更新 | 已修（2e80b79、413c289） |
| F-010 | 中 | bug | 開啟 Recipe 時，`peek_columns` 把整個 CSV 讀進記憶體 | 已修（2e80b79） |
| F-011 | 中 | bug | 復原階段改名後，已載入的資料對不上，要重新載入 | 已修（2e80b79） |
| F-012 | 中 | bug | 大整數在預覽表格顯示成別的數字；NaN／Infinity 讓表格讀不出資料 | 已修（2e80b79） |
| F-013 | 中 | bug | 樞紐、合併來源檔之後的群組彙總、移除重複（保留最後一筆）不是串流：整份資料放進記憶體 | 已修（413c289） |
| F-014 | 中 | bug | 取消樞紐之後接群組彙總或樞紐：組的順序每次執行都可能不同 | 已修（413c289） |
| F-015 | 中 | bug | 樞紐：「欄依據欄位」是空值或空白的列，金額不在任何一欄，也沒有警告 | 已修（413c289） |
| F-016 | 中 | bug | 結合：這一邊的鍵不存在時，丟出沒有包裝的 Polars 例外 | 已修（413c289） |
| F-017 | 中 | bug | 把第一列作為欄位標題：標題有空白格或重複時，連建立步驟的那個檔都警告「標題不同」 | 已修（413c289） |
| F-018 | 中 | bug | 篩選的數值條件，說明文字用科學記號、只留 6 位有效數字 | 已修（413c289） |
| F-019 | 中 | bug | Recipe 的 `type`／`op`／`reader`／`output_format` 是清單或物件時，驗證器直接丟 `TypeError` | 已修（2e80b79） |
| F-020 | 中 | bug | `tablade run` 沒有接住找不到檔、編碼錯誤等例外；步驟錯誤的 `path` 和 `validate --run` 不一樣 | 已修（2e80b79） |
| F-021 | 中 | bug | 驗證器放行不存在的系統欄位；`__row_type` 只存在於驗證器 | 已修（2e80b79） |
| F-022 | 中 | bug | 欄位檢查只看第一個檔的標題列：多個檔欄位不同時誤報，`tablade run` 無法執行 | 已修（2e80b79） |
| F-023 | 中 | bug | `tablade validate` 不檢查輸出檔的規劃：驗證通過，`run` 才失敗 | 已修（2e80b79） |
| F-024 | 中 | bug | skill 的驗證器通過、Tablade 拒絕：步驟自己的規則沒有進到驗證器 | 已修（2e80b79） |
| F-025 | 中 | bug | 打包後的 App 下載的 AI skill 只有 1 個範例，Release 附的有 4 個 | 已修（2e80b79） |
| F-026 | 中 | bug | 匯出完成後畫面沒有更新：「輸出資料」不會變成完整模式 | 已修（2e80b79、413c289） |
| F-027 | 中 | bug | 沒先載入就匯出：完整模式的資料被「還沒載入資料」擋住，看不到 | 已修（413c289） |
| F-028 | 中 | bug | 判斷「預覽有沒有變」的 key 漏了條件：表格與型別分頁會顯示舊資料 | 已修（2e80b79、413c289） |
| F-029 | 中 | bug | 步驟表單延後送出的修改，會寫到別的步驟，或被較慢的回應蓋掉 | 已修（2e80b79、413c289） |
| F-030 | 中 | bug | 預覽表格沒有丟掉過時的回應；取資料失敗的那一頁永遠是「…」 | 已修（2e80b79、413c289） |
| F-031 | 中 | bug | 警告分頁標題的數字是清單的長度，不是實際的警告數 | 已修（2e80b79、413c289） |
| F-032 | 中 | bug | 完整模式的列數很多時，表格捲不到後面的列 | 已修（413c289） |
| F-033 | 中 | bug | 條件換運算子時沿用舊的值：數字框顯示 100，卻報「應該是數字」 | 已修（413c289） |
| F-034 | 中 | bug | 表單看不到部分驗證錯誤，只有步驟清單上的「錯誤」標記 | 已修（413c289） |
| F-035 | 中 | bug | 不支援的步驟類型：右邊顯示成「讀取資料」的設定 | 已修（413c289） |
| F-036 | 中 | bug | 快捷鍵不看是否正在處理；⌘S 不會先送出輸入框裡的修改 | 已修（413c289） |
| F-037 | 低 | bug | 「檢查並複製錯誤報告」試跑後，平行轉碼的工作程序沒有關掉 | 已修（2e80b79） |
| F-038 | 低 | bug | 任何一個 API 呼叫結束，都會把畫面上的進度清掉 | 已修（2e80b79） |
| F-039 | 低 | bug | `TABLADE_DEV_URL` 的「只接受 localhost」可以用 `localhost:埠@別的主機` 繞過 | 已修（2e80b79） |
| F-040 | 低 | bug | 平行轉碼遇到任何 I/O 錯誤，都被當成「開不了工作程序」，之後整個工作階段只用單核心 | 已修（2e80b79） |
| F-041 | 低 | bug | 正式處理才出現的步驟錯誤，訊息用內部的步驟 id 與類型代碼，也沒有「前往這一步」 | 已修（2e80b79） |
| F-042 | 低 | bug | 錯誤訊息裡的步驟編號從 0 起算 | 已修（2e80b79） |
| F-043 | 低 | bug | 「瀏覽檔案」預設的檔案類型沒有 `.xlsb`、`.ods` | 已修（2e80b79） |
| F-044 | 低 | bug | 拆欄（split_column）的新欄名和來源欄同名時，執行失敗 | 已修（413c289） |
| F-045 | 低 | bug | 條件的正規表示式不在驗證時檢查；條件的欄不存在時是 Polars 的原文 | 已修（413c289） |
| F-046 | 低 | bug | 步驟的幾個小問題 | 已修（413c289；決定：清理文字把格子裡的換行換成一個空白，不直接刪掉） |
| F-047 | 低 | bug | 驗證器漏掉的幾種輸入：版本號、輸出檔名、舊寫法的提示 | 已修（2e80b79） |
| F-048 | 低 | bug | `tablade profile`：整個檔讀進記憶體、遮罩有漏、`--lines` 可以是負數 | 已修（2e80b79） |
| F-049 | 低 | bug | `check_code_policy.py` 擋不住幾種常見的寫法 | 已修（2e80b79） |
| F-050 | 低 | bug | schema lock 裡留著選項的說明文字，改說明會被要求升版本 | 已修（2e80b79） |
| F-051 | 低 | bug | 預覽區與進度視窗的幾個小問題 | 已修（413c289） |
| F-052 | 低 | bug | 表單與面板的零碎問題 | 已修（413c289） |
| F-053 | 低 | bug | 命令列與訊息的小瑕疵 | 已修（2e80b79） |
| F-054 | 低 | 漏翻 | pydantic 的幾種錯誤沒有翻譯，中文畫面顯示英文原文 | 已修（2e80b79） |
| F-055 | 低 | 漏翻 | 英文畫面的狀態列還是用全形的「・」 | 已修（413c289） |
| F-056 | 低 | 漏翻 | 英文用語不一致 | 已修（413c289；決定：「欄位「x」不存在」對 Column "x" does not exist、「欄位不存在：x」對 Column not found: x，兩種句型各自一致；「整數」「數字」是不同的中文，英文的 Integer、number 維持） |
| F-057 | 低 | 漏翻 | 英文的單複數與文法 | 已修（413c289） |
| F-058 | 低 | 漏翻 | 切換語言後沒有跟著換的文字；快捷鍵提示在 Windows 也寫 ⌘ | 已修（2e80b79、413c289） |
| F-059 | 低 | 文件 | DESIGN.md §6 的範例 Recipe 過不了驗證 | 已修（5181505） |
| F-060 | 低 | 文件 | 處理紀錄的說法和實際不符：欄位名稱是英文，步驟說明也跟著畫面的語言 | 已修（5181505；決定：處理紀錄維持現狀，文件照實際寫） |
| F-061 | 低 | 文件 | 兩份 README 的數字與架構說明過時 | 已修（5181505） |
| F-062 | 低 | 文件 | DESIGN.md §12 的檔案結構與實際不符 | 已修（5181505） |
| F-063 | 低 | 文件 | DESIGN.md §14 還留著和串流規則相反的規則 | 已修（5181505） |
| F-064 | 低 | 文件 | DESIGN.md §17 寫了程式沒有的行為（自動升級、`only_row_types`、說明選單、v1.4） | 已修（5181505） |
| F-065 | 低 | 文件 | 「前端不能指定任意檔案路徑」的說法和「手動輸入路徑」的功能不符 | 已修（5181505） |
| F-066 | 低 | 文件 | 兩份 README 不同步；英文 README 的按鈕名稱和畫面不一樣 | 已修（5181505） |
| F-067 | 低 | 文件 | 程式裡過時或放錯位置的說明 | 已修（2e80b79、5181505、413c289） |
| F-068 | 低 | 文件 | CLAUDE.md「新增步驟」的規則，和 registry 實際檢查的不一樣 | 已修（2e80b79） |
| F-069 | 低 | 文件 | DESIGN.md §7、§16.5、§17.5 的步驟說明和程式不符 | 已修（5181505） |
| F-070 | 低 | 文件 | AI skill 的 SKILL.md 有幾處和程式不符 | 已修（2e80b79） |
| F-071 | 低 | 測試 | 四種步驟幾乎沒有串流的測試：依條件複製欄位完全沒在串流跑過 | 已修（413c289） |
| F-072 | 低 | 測試 | 兩種步驟的警告在串流模式沒有測過 | 已修（413c289） |
| F-073 | 低 | 測試 | 有些參數的選項沒有任何測試用過 | 已修（413c289；樞紐的「筆數」沒有資料的格子維持空值，和其他彙總方式一樣） |
| F-074 | 低 | 測試 | 新增步驟的建議值（`suggest()`）有 6 種步驟完全沒測；結合的說明與警告有分支沒測 | 已修（413c289） |
| F-075 | 低 | 測試 | 這次找到的 bug，對應到的都是沒有測試的資料形狀 | 已修（413c289） |
| F-076 | 低 | 測試 | 設了 `TABLADE_NO_UPDATE_CHECK` 的環境，`tests/test_update.py` 有 5 項會失敗 | 已修（2e80b79） |

## 發現

### F-001［高］［bug］輸出檔名和輸入檔只差大小寫（或 Unicode 的組合方式）時，匯出會蓋掉輸入檔

- 位置：src/tablade/export/outputs.py:74、src/tablade/export/outputs.py:88（`plan_outputs` 比對輸入檔與輸出檔的位置）；src/tablade/engine/run.py:590（改名）
- 現象：
  - 「寫出的位置不能是任何一個階段的輸入檔」這項檢查，是拿兩邊 `Path.resolve()` 的結果直接比對。
  - macOS 與 Windows 的檔案系統預設不分大小寫，`resolve()` 不會把大小寫統一。輸入檔是 `output/Data.csv`、輸出檔名填 `data`（CSV）時，檢查通過、匯出按鈕可以按。
  - 匯出後，輸入檔的內容被換成輸出結果，原本的資料就沒了。
  - 讀另一份 Recipe 寫在 `output/` 的檔，是 docs/improve-plan.md 第 2 批提到的用法。
  - 同一個原因的其他情況：
    - 檔名的 Unicode 組合方式不同（磁碟上是 NFD 的 `café.csv`、輸出檔名填 NFC 的 `café`）也一樣被蓋掉（已重現）。
    - 主檔名重複的檢查（src/tablade/recipe/validator.py:872）只做 `casefold()`，NFC 與 NFD 的同名檔不算重複，兩個階段會寫到同一個檔（沒有另外重現）。
    - 處理紀錄的位置（`log/主檔名.manifest.json`）沒有和輸入檔比對（沒有另外重現；拿處理紀錄當輸入檔的情況很少）。
- 證據：在 repo 根目錄執行 `uv run python 腳本.py`（下面每個腳本都一樣：資料在腳本裡產生到暫存資料夾）。
  ```python
  import json
  import tempfile
  from pathlib import Path

  from tablade.session import Session

  work = Path(tempfile.mkdtemp())
  (work / "output").mkdir()
  source = work / "output" / "Data.csv"  # 輸入檔（例如另一份 Recipe 的輸出）
  source.write_text("品名,金額\n筆,12\n紙,34\n", encoding="utf-8")
  recipe = work / "case.recipe.json"
  recipe.write_text(
      json.dumps(
          {
              "tablade_recipe": "1.0",
              "name": "case",
              "stages": [
                  {
                      "name": "主要",
                      "input": {"files": ["output/Data.csv"], "reader": "csv", "encoding": "auto"},
                      "steps": [{"id": "s1", "type": "remove_top_rows", "n": 1}],
                      "output_file": "data",  # 寫到 output/data.csv
                      "output_format": "csv",
                  }
              ],
              "output": {"keep_system_columns": False, "manifest": False},
          },
          ensure_ascii=False,
      ),
      encoding="utf-8",
  )

  print("檔案系統不分大小寫：", (work / "output" / "data.csv").exists())
  session = Session()
  print("開啟時的問題：", session.load_recipe(recipe))
  state = session.state()
  print("輸出設定的錯誤：", state["stages"][0]["output"]["error"])
  print("匯出按鈕：", state["actions"]["export"])
  before = source.read_text(encoding="utf-8")
  session.export()
  print("output/ 裡的檔：", sorted(p.name for p in (work / "output").iterdir()))
  print("輸入檔匯出前：", repr(before))
  print("輸入檔匯出後：", repr(source.read_text(encoding="utf-8-sig")))
  ```
  實際輸出：
  ```text
  檔案系統不分大小寫： True
  開啟時的問題： []
  輸出設定的錯誤： None
  匯出按鈕： {'ok': True, 'reason': None}
  output/ 裡的檔： ['Data.csv']
  輸入檔匯出前： '品名,金額\n筆,12\n紙,34\n'
  輸入檔匯出後： '品名,金額\n紙,34\n'
  ```
  預期：輸出設定顯示「是階段「主要」的輸入檔，不能寫到這裡」，匯出按鈕不能按，輸入檔不變。
- 確認程度：已重現（macOS，不分大小寫的 APFS）。Windows 沒有實測，但 NTFS 預設也不分大小寫。區分大小寫的檔案系統不會發生。
- 建議修法：
  - `plan_outputs` 比對時，輸出檔已經存在就用 `os.path.samefile()` 跟每個輸入檔比；不存在時兩邊先做 `unicodedata.normalize("NFC", …)`、`os.path.normcase()`、`casefold()` 再比。
  - src/tablade/recipe/validator.py:872 的 key 也先 NFC 再 `casefold()`。
  - 加一道保險：`_Writes.commit()` 改名前，確認目標不是這次讀過的任何一個輸入檔。
  - 測試：加一個只差大小寫的案例；先用 `tmp_path` 探測檔案系統是否不分大小寫，分大小寫時 skip。
- 狀態：已修（2e80b79）

### F-002［高］［bug］同一個檔經兩個階段處理、附加後再被下一個階段讀：串流會丟資料，stream 與 eager 結果不同

- 位置：
  - src/tablade/engine/run.py:339-353（`split_parts`：eager 依「連續的同一個來源名稱」切 part）
  - src/tablade/engine/run.py:795-801、src/tablade/engine/run.py:977-983（stream：下一個階段讀的時候把「@階段／」去掉）
  - src/tablade/steps/files.py:85-90（`MergeFiles.combine` 用 `dict(parts)`）
- 現象：階段 A、B 各自讀同一個檔，階段 C 讀 A、B（附加、不合併），階段 D 再讀 C。
  - C 的結果有兩個來源，去掉「@階段／」後名稱都是 `data/f.csv`：part 名稱重複（src/tablade/steps/base.py:11-12 寫「名稱不重複」）。
  - **stream**：D 有兩個同名的 part。「合併來源檔」的 `dict(parts)` 只留下後面那個，再依名稱取兩次：A 的資料不見、B 的資料出現兩次，沒有錯誤也沒有警告。
  - **eager**（資料不大時的匯出，以及畫面的預覽）：`split_parts` 把相鄰、同名的兩段當成一個來源。分檔的步驟（移除前 N 列、向下填滿、把第一列作為欄位標題…）只對接起來的那一份做一次，stream 則各做一次。
  - 結果：同一份 Recipe，資料量小（或預覽）和資料量大（串流）寫出不同的內容。
- 證據：
  ```python
  import tempfile
  from pathlib import Path

  import polars as pl

  from tablade.engine.run import execute
  from tablade.recipe.schema import parse_recipe

  work = Path(tempfile.mkdtemp())
  (work / "data").mkdir()
  (work / "data" / "f.csv").write_text("row1\nrow2\nrow3\n", encoding="utf-8")
  FILES = {"files": ["data/f.csv"], "reader": "text_lines", "encoding": "auto"}


  def stage(name, source, *steps):
      return {"name": name, "input": source, "steps": list(steps), "output_format": "parquet"}


  def mark(step_id, letter):  # 把 row 換成 A 或 B，分得出是哪個階段處理過的
      return {
          "id": step_id,
          "type": "replace_values",
          "columns": ["line"],
          "replacements": [{"find": "row", "replace": letter}],
      }


  def run(title, last_step):
      recipe = parse_recipe(
          {
              "tablade_recipe": "1.0",
              "name": "chain",
              "stages": [
                  stage("A", FILES, mark("a1", "A")),
                  stage("B", FILES, mark("b1", "B")),
                  stage("C", {"reader": "stages", "stages": ["A", "B"]}),  # 附加，不合併
                  stage("D", {"reader": "stages", "stages": ["C"]}, last_step),
              ],
              "output": {"keep_system_columns": True, "manifest": False},
          }
      )
      print(title)
      for mode in ("eager", "stream"):
          target = work / f"{mode}.parquet"
          execute(recipe, work, output=target, mode=mode)
          print(f"  {mode:6}：", pl.read_parquet(target)["line"].to_list())


  run("D 的步驟：合併來源檔", {"id": "d1", "type": "merge_files"})
  run("D 的步驟：移除前 1 列", {"id": "d1", "type": "remove_top_rows", "n": 1})
  ```
  實際輸出：
  ```text
  D 的步驟：合併來源檔
    eager ： ['A1', 'A2', 'A3', 'B1', 'B2', 'B3']
    stream： ['B1', 'B2', 'B3', 'B1', 'B2', 'B3']
  D 的步驟：移除前 1 列
    eager ： ['A2', 'A3', 'B1', 'B2', 'B3']
    stream： ['A2', 'A3', 'B2', 'B3']
  ```
  預期：兩種模式的結果相同；「合併來源檔」是 `A1 A2 A3 B1 B2 B3`。
- 確認程度：已重現（兩種步驟都是）。
- 建議修法：
  - 讓 part 的身分在階段之間保持唯一：eager 也照上游的 part 邊界切（例如讀進來的資料多帶一個 part 編號的暫存欄，跟 stream 的 `PART` 一樣），名稱重複時加上分得出來的字（例如保留上游的「@A／」）。
  - `MergeFiles.combine()` 不要用 `dict(parts)`，直接依索引重排 parts，名稱重複也不會丟資料。
  - 引擎在套用步驟前檢查 part 名稱不重複，重複就報錯，不要默默算錯。
  - 測試：tests/test_large.py 的 stream＝eager 比對加上這個四階段的案例（合併來源檔、移除前 N 列各一）。
- 狀態：已修（2e80b79）

### F-003［高］［bug］欄名含大括號時，只要有一則警告，載入、預覽與匯出全部失敗

- 位置：
  - src/tablade/engine/run.py:447（`check.message.format(value)`）
  - 訊息裡直接放欄名或階段名稱的地方：src/tablade/steps/convert.py:142-143、src/tablade/steps/aggregate.py:481-482、src/tablade/steps/join.py:317-318、src/tablade/steps/split.py:303-304
- 現象：
  - 警告訊息先用 f-string 放進欄名，再用 `str.format()` 填入那一列的值。
  - 欄名（或樞紐依據的欄、結合的階段名稱）含 `{` 或 `}` 時，`format()` 丟出 `KeyError`／`IndexError`／`ValueError`。
  - 這個例外不是 `RunError`，`Session._compute` 沒有接：「載入資料及預覽」失敗，之後 `state()` 每次都丟例外，畫面上每個操作都回錯誤。匯出（eager、stream）也失敗。
  - 那一步至少有一列要警告時才會發生；沒有警告時不會。
- 證據：
  ```python
  import json
  import tempfile
  from pathlib import Path

  from tablade.engine.run import execute
  from tablade.recipe.schema import parse_recipe
  from tablade.session import Session

  work = Path(tempfile.mkdtemp())
  (work / "data").mkdir()
  (work / "data" / "a.csv").write_text("品名,金額{元}\n筆,12\n紙,N/A\n", encoding="utf-8")
  data = {
      "tablade_recipe": "1.0",
      "name": "braces",
      "stages": [
          {
              "name": "主要",
              "input": {"files": ["data/a.csv"], "reader": "csv", "encoding": "auto"},
              "steps": [
                  {"id": "s1", "type": "parse_number", "columns": ["金額{元}"], "kind": "integer"}
              ],
              "output_format": "parquet",
          }
      ],
      "output": {"keep_system_columns": True, "manifest": False},
  }
  for mode in ("eager", "stream"):
      try:
          execute(parse_recipe(data), work, output=work / f"{mode}.parquet", mode=mode)
          print(f"execute（{mode}）→ 成功")
      except Exception as err:
          print(f"execute（{mode}）→ {type(err).__name__}: {err}")

  path = work / "braces.recipe.json"
  path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
  session = Session()
  print("開啟時的問題：", session.load_recipe(path))
  for name in ("load_data", "state"):
      try:
          getattr(session, name)()
          print(f"Session.{name}() → 成功")
      except Exception as err:
          print(f"Session.{name}() → {type(err).__name__}: {err}")
  ```
  實際輸出：
  ```text
  execute（eager）→ KeyError: '元'
  execute（stream）→ KeyError: '元'
  開啟時的問題： []
  Session.load_data() → KeyError: '元'
  Session.state() → KeyError: '元'
  ```
  預期：照常執行，警告訊息裡的欄名是「金額{元}」。
- 確認程度：已重現。
- 建議修法：
  - 不要對含使用者資料的字串用 `str.format()`。把 `WarningCheck.message` 拆成前後兩段（值放中間），或改用不會出現在資料裡的標記再取代。
  - 測試：tests/test_steps.py 對每種有 `{}` 警告的步驟，用含 `{`、`}`、`{0}` 的欄名各跑一次（eager 與 stream）。
- 狀態：已修（2e80b79）

### F-004［高］［bug］篩選的「不等於／不包含／不符合」遇到空值會把那一列拿掉

- 位置：src/tablade/steps/filter.py:65、src/tablade/steps/filter.py:67、src/tablade/steps/filter.py:93（否定的條件）；src/tablade/steps/filter.py:219-222（`fill_null(False)`）。同一組條件也用在「將列進行分類」（src/tablade/steps/report.py:141-145）與「依條件複製欄位」（src/tablade/steps/fill_down.py:96-102）。
- 現象：
  - 否定的條件（不等於、不包含、不符合正規表示式）對空值算出來是空值，接著被 `fill_null(False)` 當成「不符合」。
  - 「保留：備註 不包含『退貨』」會把備註是空值的列一起拿掉；空字串的列則會留下。
  - `csv` 讀進來的空白格是空值，文字行拆欄拆出來的是空字串：同一份資料換一種讀取方式，篩選結果就不一樣。
  - 反過來「排除：備註 包含『退貨』」會留下空值的列。同一件事兩種寫法，結果不同。
  - 步驟的說明與 `ai_notes` 都沒有提到空值怎麼算。
- 證據：
  ```python
  import polars as pl

  from tablade.engine.run import apply_step
  from tablade.steps import get

  frame = pl.DataFrame(
      {
          "__source_file": ["a.csv"] * 4,
          "__line_no": [2, 3, 4, 5],
          "品名": ["原子筆", "折疊傘", "滑鼠", "筆記本"],
          "備註": [None, "退貨", "一般", ""],  # csv 的空白格讀進來是空值；拆欄拆出來的是空字串
      }
  )


  def run(op, keep):
      step = get("filter").model_validate(
          {
              "id": "s1",
              "type": "filter",
              "conditions": [{"column": "備註", "op": op, "value": "退貨"}],
              "keep": keep,
          }
      )
      out, _, _ = apply_step(step, frame)
      print(f"{step.describe()} → {out['品名'].to_list()}")


  run("not_contains", "matching")
  run("not_equals", "matching")
  run("contains", "non_matching")
  ```
  實際輸出：
  ```text
  保留：備註 不包含「退貨」 → ['滑鼠', '筆記本']
  保留：備註 不等於「退貨」 → ['滑鼠', '筆記本']
  排除：備註 包含「退貨」 → ['原子筆', '滑鼠', '筆記本']
  ```
  預期：前兩行也留下「原子筆」（備註是空值，當然不包含「退貨」）。
- 確認程度：已重現（eager；另一位審查者用 `execute()` 確認 stream 也一樣）。這可能是沿用資料庫的三值邏輯而不是寫錯，但對「保留不包含某個字的列」這種用法來說，資料會默默少列，所以列為高；請先確認要的是哪一種行為。
- 建議修法：
  - 否定的條件改成「先算肯定的條件、空值當成不符合，再取反」：`~(…).fill_null(False)`；或文字條件比對前先 `fill_null("")`。
  - `ai_notes` 與表單的說明寫清楚空值怎麼算。
  - 測試：tests/test_steps.py 對每個否定的運算子加上空值與空字串的列；`keep` 兩種都測。
- 狀態：已修（413c289）

### F-005［高］［bug］依固定位置拆欄：℃、×、※ 這類字讓後面的欄位錯位，沒有任何警告

- 位置：src/tablade/steps/report.py:273-277（`WIDE`：哪些字算 2 格）、src/tablade/steps/report.py:420；說明在 src/tablade/steps/report.py:316、src/tablade/steps/report.py:359-361、DESIGN.md:218（「和 Big5 報表一個字兩個位元組相同」）
- 現象：
  - 列印格式的報表是照 Big5（cp950）的位元組數對齊的。程式把中日韓文字與全形符號算 2 格，其他算 1 格。
  - cp950 裡還有一批佔 2 個位元組、但不在這個範圍裡的字：`℃ × ± ° § ※ ─ │ ① Ⅰ α β ★ ○ → √` 等（希臘字母、框線、圈號、單位符號；另一位審查者逐字比對，共 312 個）。
  - 這些字出現在哪一欄，那一列後面的欄位就全部往左偏 1 格：金額 `12,280` 被切成 `1` 與 `2,280`，沒有錯誤也沒有警告。
- 證據：
  ```python
  import polars as pl

  from tablade.engine.run import apply_step
  from tablade.steps import get


  def pad(text, width):  # 列印格式的報表：依 cp950 的位元組數補空白對齊
      return text + " " * (width - len(text.encode("cp950")))


  names = ["保溫瓶", "20℃保溫瓶", "3×4收納盒", "※特價雨傘"]
  lines = [pad(f"A{i:03d}", 5) + pad(name, 12) + "12,280" for i, name in enumerate(names, start=1)]
  frame = pl.DataFrame({"__source_file": ["a.txt"] * 4, "__line_no": [1, 2, 3, 4], "line": lines})
  step = get("split_fixed_width").model_validate(
      {
          "id": "s1",
          "type": "split_fixed_width",
          "starts": [1, 6, 18],
          "names": ["品號", "品名", "金額"],
      }
  )
  out, _, warnings = apply_step(step, frame)
  for line, row in zip(lines, out.select("品號", "品名", "金額").iter_rows(), strict=True):
      print(f"{len(line.encode('cp950'))} 位元組 {line!r} → {row}")
  print("警告：", warnings)
  ```
  實際輸出：
  ```text
  23 位元組 'A001 保溫瓶      12,280' → ('A001', '保溫瓶', '12,280')
  23 位元組 'A002 20℃保溫瓶  12,280' → ('A002', '20℃保溫瓶  1', '2,280')
  23 位元組 'A003 3×4收納盒  12,280' → ('A003', '3×4收納盒  1', '2,280')
  23 位元組 'A004 ※特價雨傘  12,280' → ('A004', '※特價雨傘  1', '2,280')
  警告： []
  ```
  預期：四列的金額都是 `12,280`。
- 確認程度：已重現（3 個字）。「共 312 個」是另一位審查者的統計，我沒有重算。
- 建議修法：
  - 「算 2 格」的定義改成「編成 cp950 是 2 個位元組」（或加一個「依 Big5 位元組計算」的選項，預設開）。
  - 建立步驟時猜位置用的 `display_width()`（src/tablade/steps/report.py:282）改成和執行時同一套算法。
  - 測試：tests/test_steps.py 的 `test_split_fixed_width_counts_wide_characters` 加上這幾類字。
- 狀態：已修（413c289）

### F-006［中］［bug］多個輸出檔不是「全部成功才改名」：後面的檔改名失敗時，前面的已經換成新的

- 位置：src/tablade/engine/run.py:587-598（`_Writes.commit`）；src/tablade/engine/run.py:668、src/tablade/engine/run.py:677（全部改名後才寫處理紀錄）
- 現象：
  - `_Writes` 的說明寫「整個執行成功才一起改名…不會有的換成新的、有的還是舊的」，但 `commit()` 是一個一個 `os.replace`。
  - 後面的檔改名失敗時（Windows 上輸出的 CSV 還開在 Excel 裡就會這樣），前面的檔已經換成新的。
  - 處理紀錄要等全部改名成功才寫，所以結果是：新的輸出檔配舊的處理紀錄（SHA-256 對不上），畫面只顯示匯出失敗。
- 證據：
  ```python
  import hashlib
  import json
  import os
  import tempfile
  from pathlib import Path

  from tablade.engine import run
  from tablade.export.outputs import log_dir, resolve_outputs
  from tablade.recipe.schema import parse_recipe

  work = Path(tempfile.mkdtemp())
  (work / "data").mkdir()
  source = work / "data" / "a.csv"
  RECIPE = {
      "tablade_recipe": "1.0",
      "name": "two",
      "stages": [
          {
              "name": "對照",
              "input": {"files": ["data/a.csv"], "reader": "csv", "encoding": "auto"},
              "steps": [],
              "output_file": "對照",
              "output_format": "csv",
          },
          {
              "name": "結果",
              "input": {"reader": "stages", "stages": ["對照"]},
              "steps": [],
              "output_file": "結果",
              "output_format": "parquet",
          },
      ],
      "output": {"keep_system_columns": False, "manifest": True},
  }


  def export():
      recipe = parse_recipe(RECIPE)
      run.execute(recipe, work, outputs=resolve_outputs(recipe, work, "two"), log_dir=log_dir(work))


  def show(title):
      print(title)
      for name in ("對照.csv", "結果.parquet"):
          out = work / "output" / name
          actual = hashlib.sha256(out.read_bytes()).hexdigest()
          manifest = json.loads((work / "log" / f"{out.stem}.manifest.json").read_text("utf-8"))
          recorded = manifest["output"]["sha256"]
          print(
              f"  {name}：檔案 {actual[:8]}，處理紀錄記的 {recorded[:8]}，一致：{actual == recorded}"
          )


  source.write_text("品名,數量\n筆,1\n", encoding="utf-8")
  export()
  show("第 1 次匯出（成功）")

  source.write_text("品名,數量\n筆,1\n紙,2\n", encoding="utf-8")  # 資料變了，再匯出一次
  real_replace, calls = os.replace, []


  def second_fails(tmp, target):  # 模擬第 2 個檔被別的程式開著（Windows 上是 PermissionError）
      calls.append(Path(target).name)
      if len(calls) == 2:
          raise PermissionError(13, "Permission denied")
      return real_replace(tmp, target)


  run.os.replace = second_fails
  try:
      export()
  except RuntimeError as err:
      print("第 2 次匯出 →", err)
  finally:
      run.os.replace = real_replace
  show("第 2 次匯出失敗之後")
  ```
  實際輸出：
  ```text
  第 1 次匯出（成功）
    對照.csv：檔案 02300108，處理紀錄記的 02300108，一致：True
    結果.parquet：檔案 5d969c91，處理紀錄記的 5d969c91，一致：True
  第 2 次匯出 → 「結果.parquet」寫不進去：可能被別的程式開著，關掉後再試一次
  第 2 次匯出失敗之後
    對照.csv：檔案 ae822580，處理紀錄記的 02300108，一致：False
    結果.parquet：檔案 5d969c91，處理紀錄記的 5d969c91，一致：True
  ```
  預期：第 2 次匯出失敗後，兩個檔與處理紀錄都維持第 1 次的內容。
- 確認程度：已重現（用 monkeypatch 讓第二次 `os.replace` 丟 `PermissionError`，模擬檔案被別的程式開著；真的被 Excel 鎖住要在 Windows 上才有，沒有實測）。
- 建議修法：
  - 改名前先確認每個目標都寫得進去（已存在的目標先試著用寫入模式開啟），有一個不行就全部不改名。
  - 改名途中失敗時把已經改名的還原（先把舊檔改名成備份，全部成功才刪備份）。
  - 測試：第二個檔改名失敗時，第一個檔與它的處理紀錄維持原樣。
- 狀態：已修（2e80b79）

### F-007［中］［bug］檔名或資料夾名稱有 `[`、`?` 時被當成萬用字元，加入後找不到檔

- 位置：src/tablade/ingest/sources.py:202-211（`is_glob`、`expand`）；src/tablade/session.py:662-671（`add_files`）
- 現象：
  - `is_glob()` 只要路徑裡有 `*`、`?`、`[` 就當成 glob。`expand()` 的說明寫「有 * 才當成 glob」，DESIGN.md §5.3 也只提 `*`。
  - 「瀏覽檔案」選到檔名或上層資料夾名稱有 `[` 的檔（例如 `明細[1].csv`、`匯出 [2026]/`）時，`add_files` 原樣存進清單，之後被當成 glob 比對，找不到檔。
  - 畫面上清單顯示找不到、兩顆按鈕都不能按，只能改檔名。macOS 的檔名可以有 `?`，也一樣。
- 證據：
  ```python
  import tempfile
  from pathlib import Path

  from tablade.session import Session

  work = Path(tempfile.mkdtemp())
  (work / "data").mkdir()
  (work / "匯出 [2026]").mkdir()
  first = work / "data" / "明細[1].csv"
  second = work / "匯出 [2026]" / "明細.csv"
  for path in (first, second):
      path.write_text("a,b\n1,2\n", encoding="utf-8")

  session = Session()
  session.new(work / "t.recipe.json")
  session.set_input({"reader": "csv"})
  session.add_files([first, second])  # 「瀏覽檔案」選了這兩個檔（api.add_files 做的事）
  state = session.state()
  print("檔案清單：", state["input"]["entries"])
  print("載入按鈕：", state["actions"]["load"])
  ```
  實際輸出：
  ```text
  檔案清單： [{'path': 'data/明細[1].csv', 'found': 0}, {'path': '匯出 [2026]/明細.csv', 'found': 0}]
  載入按鈕： {'ok': False, 'reason': '找不到：data/明細[1].csv、匯出 [2026]/明細.csv，請先從檔案清單移除'}
  ```
  預期：兩個檔都找得到（`found` 是 1），可以載入。
- 確認程度：已重現。
- 建議修法：
  - `add_files`（瀏覽選到的一定是實際的檔）存進清單前用 `glob.escape()`；或 `expand()` 先看這個路徑本身是不是檔案，是就直接用，不是才當 glob。
  - `is_glob` 的判斷和文件寫的一致。
  - 測試：tests/test_session.py 加上檔名與資料夾名稱含 `[`、`]` 的案例。
- 狀態：已修（2e80b79）

### F-008［中］［bug］結合的另一張表是樣本時，預覽仍標「完整模式」，還出現實際上沒有的「找不到」警告

- 位置：src/tablade/session.py:1128-1171（`_compute` 回傳的 `sampled` 只看這個階段自己的輸入）；src/tablade/session.py:1423、src/tablade/session.py:1434（`preview.mode`、`sampled_steps`）；src/tablade/session.py:1269（`stages[].sampled`）
- 現象：
  - 階段自己的資料不大，但結合的另一張表是大檔的樣本時，這個階段的預覽標「完整模式」，`sampled_steps` 是空的。
  - 另一張表其實只有每個檔的前 10 萬行＋最後 1,000 行，中間的鍵都對不到：預覽裡帶進來的欄是空值，並出現「在 @階段 找不到」的警告。
  - 匯出（用全部資料）時這些警告並不存在。使用者會以為對照表缺資料。
  - 結合的表單會標示另一張表是樣本（DESIGN.md §16.6），但資料 pane 的模式標籤說的是相反的事。
- 證據：
  ```python
  import json
  import tempfile
  from pathlib import Path

  import tablade.engine.run as run
  import tablade.session as session_module
  from tablade.session import Session

  # 把「大檔」的門檻調小，不用真的產生 20 萬行
  for module in (run, session_module):
      module.SAMPLE_THRESHOLD, module.SAMPLE_HEAD, module.SAMPLE_TAIL = 50, 10, 2

  work = Path(tempfile.mkdtemp())
  (work / "data").mkdir()
  # 右表 100 列（超過門檻：樣本只有前 10 列＋最後 2 列）；左表 3 列，在完整的右表裡都對得到
  (work / "data" / "品項.csv").write_text(
      "品號,類別\n" + "".join(f"P{i:03d},類別{i % 3}\n" for i in range(1, 101)), encoding="utf-8"
  )
  (work / "data" / "明細.csv").write_text("品號,數量\nP005,1\nP050,2\nP099,3\n", encoding="utf-8")


  def stage(name, file, *steps):
      source = {"files": [f"data/{file}"], "reader": "csv", "encoding": "auto"}
      return {"name": name, "input": source, "steps": list(steps), "output_format": "parquet"}


  join = {
      "id": "s1",
      "type": "join",
      "right": "品項對照",
      "on": [{"left": "品號", "right": "品號"}],
      "columns": ["類別"],
  }
  path = work / "join.recipe.json"
  path.write_text(
      json.dumps(
          {
              "tablade_recipe": "1.0",
              "name": "join",
              "stages": [stage("品項對照", "品項.csv"), stage("明細", "明細.csv", join)],
              "output": {"keep_system_columns": False, "manifest": False},
          },
          ensure_ascii=False,
      ),
      encoding="utf-8",
  )
  session = Session()
  session.load_recipe(path)
  session.load_data()
  state = session.state()  # 目前的階段是最後一個（明細）
  for item in state["stages"]:
      print(f"階段「{item['name']}」：rows={item['rows']}，sampled={item['sampled']}")
  preview = state["preview"]
  print("明細的預覽：mode =", preview["mode"], "，sampled_steps =", preview["sampled_steps"])
  print("預覽的資料：", session.rows(0, 0, 10)["rows"])
  print("預覽的警告：", [w["message"] for w in preview["warnings"]])
  result = session.export()
  print("匯出（全部資料）：rows =", result["rows"], "，warnings =", result["warnings"])
  ```
  實際輸出：
  ```text
  階段「品項對照」：rows=12，sampled=True
  階段「明細」：rows=3，sampled=False
  明細的預覽：mode = full ，sampled_steps = []
  預覽的資料： [['data/明細.csv', 2, 'P005', '1', '類別2'], ['data/明細.csv', 3, 'P050', '2', None], ['data/明細.csv', 4, 'P099', '3', '類別0']]
  預覽的警告： ['「品號」的「P050」在 @品項對照 找不到']
  匯出（全部資料）：rows = 3 ，warnings = 0
  ```
  預期：「明細」的預覽是抽樣模式（`mode` 是 `sample`），並說明是結合的另一張表用了樣本。
- 確認程度：已重現（把 `SAMPLE_THRESHOLD` 調小模擬大檔，和 tests/test_session.py 的做法一樣）。
- 建議修法：
  - `_compute`／`_stage_result` 的 `sampled`，把「啟用的步驟引用到的階段結果是樣本（不是完整跑一次的結果）」也算進去。
  - `sampled_steps`（或另一個欄位）列出「第 N 步「結合」的另一張表是樣本」。
  - 測試：左表小、右表超過門檻時是抽樣模式；右表「完整跑一次」之後回到完整模式。
- 狀態：已修（2e80b79、413c289）

### F-009［中］［bug］`state.issues` 只在開啟 Recipe 時算一次：修正後、切換語言後都不更新

- 位置：
  - src/tablade/session.py:505（只有 `load_recipe` 會設定 `self.issues`）、src/tablade/session.py:1499
  - src/tablade/session.py:423-441（`set_language` 只清 `_computed`）
  - frontend/src/App.tsx:396-400（`showReport` 用 `state.issues`）、frontend/src/components/ReportDialog.tsx:37-64
- 現象：
  - 「Copilot → 檢查並複製錯誤報告」的對話框，上半部列的是 `state.issues`，也就是開啟 Recipe 當下的驗證結果。
  - 在畫面上把問題修好之後，標題還是「Recipe 有 1 個問題」、清單還是舊的問題；下面的報告文字是當場重算的，寫「沒有發現問題」，兩邊矛盾。
  - 反過來，開啟之後才出現的問題不會出現在清單。
  - 切換語言後，這份清單還是開啟當時的語言。
- 證據：
  ```python
  import json
  import tempfile
  from pathlib import Path

  from tablade.session import Session

  work = Path(tempfile.mkdtemp())
  (work / "data").mkdir()
  (work / "data" / "a.csv").write_text("品名,金額\n筆,12\n", encoding="utf-8")
  path = work / "issues.recipe.json"
  path.write_text(
      json.dumps(
          {
              "tablade_recipe": "1.0",
              "name": "issues",
              "stages": [
                  {
                      "name": "主要",
                      "input": {"files": ["data/a.csv"], "reader": "csv", "encoding": "auto"},
                      "steps": [
                          {
                              "id": "s1",
                              "type": "parse_number",
                              "columns": ["金額錯字"],
                              "kind": "integer",
                          }
                      ],
                      "output_format": "parquet",
                  }
              ],
              "output": {"keep_system_columns": True, "manifest": False},
          },
          ensure_ascii=False,
      ),
      encoding="utf-8",
  )


  def issues(state):
      return [(i["path"], i["error"]) for i in state["issues"]]


  session = Session()
  session.load_recipe(path)
  print("開啟後 state.issues：", issues(session.state()))
  session.set_language("en")
  print("切成英文後 state.issues：", issues(session.state()))
  session.update_step(0, {"columns": ["金額"], "kind": "integer"})  # 在畫面上把欄名改對
  session.load_data()
  state = session.state()
  print("修正後 steps[0]：", state["steps"][0]["status"], state["steps"][0]["errors"])
  print("修正後 state.issues：", issues(state))
  print("修正後 error_report()：", session.error_report().splitlines()[3])
  ```
  實際輸出：
  ```text
  開啟後 state.issues： [('stages[0].steps[0].columns[0]', '欄位「金額錯字」在這一步之前不存在')]
  切成英文後 state.issues： [('stages[0].steps[0].columns[0]', '欄位「金額錯字」在這一步之前不存在')]
  修正後 steps[0]： ok []
  修正後 state.issues： [('stages[0].steps[0].columns[0]', '欄位「金額錯字」在這一步之前不存在')]
  修正後 error_report()： No issues found.
  ```
  預期：切成英文後訊息是英文；修正後 `state.issues` 是空的。
- 確認程度：`state()` 的部分已重現；對話框怎麼用這個欄位是讀前端程式確認的，沒有在真的視窗點過。
- 建議修法：
  - `error_report()` 一起回傳當場算出的問題，對話框用它；`state.issues` 改成每次重算（`validate()` 不讀資料，很快）或拿掉。
  - 測試：tests/test_session.py（修正後 issues 變空）；tests/test_i18n_session.py（開啟有問題的 Recipe 後切成英文，整份 `state()` 沒有中文訊息）。
- 狀態：已修（2e80b79、413c289）

### F-010［中］［bug］開啟 Recipe 時，`peek_columns` 把整個 CSV 讀進記憶體

- 位置：src/tablade/ingest/sources.py:842；呼叫的地方：src/tablade/engine/check.py:37-65（`_peek_stages`），由 src/tablade/session.py:466（開啟 Recipe）、`tablade validate`、錯誤報告用到
- 現象：
  - `peek_columns` 的說明是「不讀整個檔案」，但讀取方式是 CSV 時，先 `read_bytes()` 讀整個檔，再切前 64 KB。
  - 幾 GB 的 CSV 只是「開啟 Recipe」就要等它讀完，並佔用和檔案一樣大的記憶體，違反 DESIGN.md §8「不把整個檔案讀進記憶體」。
- 證據：
  ```python
  import json
  import tempfile
  import tracemalloc
  from pathlib import Path

  from tablade.session import Session

  work = Path(tempfile.mkdtemp())
  (work / "data").mkdir()
  big = work / "data" / "big.csv"
  line = "2026-01-05,北區門市,P001,備註備註備註備註,1234.50\n".encode()
  with big.open("wb") as f:  # 約 40 MB 的 UTF-8 CSV
      f.write("日期,門市,品號,備註,金額\n".encode())
      for _ in range(40):
          f.write(line * (1_000_000 // len(line)))
  path = work / "peek.recipe.json"
  path.write_text(
      json.dumps(
          {
              "tablade_recipe": "1.0",
              "name": "peek",
              "stages": [
                  {
                      "name": "主要",
                      "input": {"files": ["data/big.csv"], "reader": "csv", "encoding": "auto"},
                      "steps": [],
                      "output_format": "parquet",
                  }
              ],
              "output": {"keep_system_columns": True, "manifest": False},
          },
          ensure_ascii=False,
      ),
      encoding="utf-8",
  )
  print(f"檔案大小：{big.stat().st_size / 1e6:.1f} MB")
  tracemalloc.start()
  Session().load_recipe(path)  # 只是開啟 Recipe，還沒按「載入資料及預覽」
  peak = tracemalloc.get_traced_memory()[1]
  print(f"開啟 Recipe 時 Python 配置的記憶體峰值：{peak / 1e6:.1f} MB")
  ```
  實際輸出：
  ```text
  檔案大小：40.0 MB
  開啟 Recipe 時 Python 配置的記憶體峰值：40.1 MB
  ```
  預期：峰值遠小於檔案大小（只讀開頭 64 KB）。
- 確認程度：已重現（40 MB 的假檔；沒有用幾 GB 的檔實測）。
- 建議修法：
  - 改成 `with first.open("rb") as f: head = f.read(1 << 16)`。
  - 測試：monkeypatch `Path.read_bytes` 讓它丟例外，確認 `peek_columns` 不會呼叫它。
- 狀態：已修（2e80b79）

### F-011［中］［bug］復原階段改名後，已載入的資料對不上，要重新載入

- 位置：src/tablade/session.py:583-588（`rename_stage` 把快取搬到新名稱）；src/tablade/session.py:364-388（`_restore`、`undo`、`redo` 沒有處理快取）
- 現象：
  - 已載入的資料、算好的每一步、「完整跑一次」的結果，都用階段名稱當 key。
  - 改名時會搬到新名稱，但「復原」把名稱改回去時沒有搬回來：那個階段變成「還沒載入資料」，要再按一次「載入資料及預覽」（大檔要重新掃描、取樣）。
  - 新名稱底下的資料還留在記憶體裡，沒有釋放。「重做」同理。
  - 刪除階段後，它的快取也一直留著，直到關閉 Recipe。
- 證據：
  ```python
  import json
  import tempfile
  from pathlib import Path

  from tablade.session import Session

  work = Path(tempfile.mkdtemp())
  (work / "data").mkdir()
  (work / "data" / "a.csv").write_text("a,b\n1,2\n", encoding="utf-8")
  path = work / "undo.recipe.json"
  path.write_text(
      json.dumps(
          {
              "tablade_recipe": "1.0",
              "name": "undo",
              "stages": [
                  {
                      "name": "原名",
                      "input": {"files": ["data/a.csv"], "reader": "csv", "encoding": "auto"},
                      "steps": [],
                      "output_format": "parquet",
                  }
              ],
              "output": {"keep_system_columns": True, "manifest": False},
          },
          ensure_ascii=False,
      ),
      encoding="utf-8",
  )


  def show(title):
      state = session.state()
      stage = state["stages"][0]
      print(
          f"{title}：階段「{stage['name']}」，{stage['status_text']}；記憶體裡的資料：{list(session._sources)}"
      )


  session = Session()
  session.load_recipe(path)
  session.load_data()
  show("載入後")
  session.rename_stage(0, "新名")
  show("改名後")
  session.undo()
  show("復原後")
  ```
  實際輸出：
  ```text
  載入後：階段「原名」，已算出結果：1 列；記憶體裡的資料：['原名']
  改名後：階段「新名」，已算出結果：1 列；記憶體裡的資料：['新名']
  復原後：階段「原名」，還沒載入資料；記憶體裡的資料：['新名']
  ```
  預期：復原後仍是「已算出結果：1 列」。
- 確認程度：已重現（刪除階段後快取留著是讀程式推論的：`delete_stage` 沒有清任何快取）。
- 建議修法：
  - 快取改用不會變的階段代號當 key（階段的 dict 裡放一個不寫進 Recipe 的 id，和 `selected` 一樣在 `_stage_dict` 不輸出）。
  - 或在 `_restore` 之後依名稱把快取搬回去，並清掉已經不存在的階段的快取。
  - 測試：載入 → 改名 → 復原 → 仍是已載入；再重做 → 仍是已載入。
- 狀態：已修（2e80b79）

### F-012［中］［bug］大整數在預覽表格顯示成別的數字；NaN／Infinity 讓表格讀不出資料

- 位置：src/tablade/session.py:243-246（`_jsonable`）；用到的地方：src/tablade/session.py:1548、src/tablade/session.py:1560（`rows`）、src/tablade/session.py:288-290（型別分頁的最小、最大、範例值）
- 現象：整數與浮點數原樣交給 pywebview；pywebview 用 `json.dumps` 轉成文字，JS 再 `JSON.parse`。
  - 超過 2^53 的整數（17 位數以上的單號用「文字轉數字」轉成整數，或 Parquet 檔的整數欄）在 JS 會失去精確度：`1234567890123456789` 在表格裡顯示成 `1234567890123456800`。匯出的檔是對的，只有畫面顯示錯。
  - 浮點欄有 NaN 或 Infinity（Parquet 輸入檔）時，`json.dumps` 寫出 `NaN`／`Infinity`，不是合法的 JSON。JS 的 `JSON.parse` 會丟例外，那次取資料的呼叫不會完成。
- 證據：
  ```python
  import json
  import tempfile
  from pathlib import Path

  import polars as pl

  from tablade.session import Session

  work = Path(tempfile.mkdtemp())
  (work / "data").mkdir()
  pl.DataFrame(
      {"單號": [1234567890123456789, 9007199254740993], "比率": [float("nan"), float("inf")]}
  ).write_parquet(work / "data" / "a.parquet")
  path = work / "numbers.recipe.json"
  path.write_text(
      json.dumps(
          {
              "tablade_recipe": "1.0",
              "name": "numbers",
              "stages": [
                  {
                      "name": "主要",
                      "input": {"files": ["data/a.parquet"], "reader": "parquet"},
                      "steps": [],
                      "output_format": "parquet",
                  }
              ],
              "output": {"keep_system_columns": False, "manifest": False},
          },
          ensure_ascii=False,
      ),
      encoding="utf-8",
  )
  session = Session()
  session.load_recipe(path)
  session.load_data()
  rows = [row[2:] for row in session.rows(-1, 0, 10)["rows"]]  # 去掉 __source_file、__line_no
  print("Session.rows() 的資料欄：", rows)
  print("pywebview 送給 JS 的 JSON：", json.dumps(rows))
  ```
  實際輸出：
  ```text
  Session.rows() 的資料欄： [[1234567890123456789, nan], [9007199254740993, inf]]
  pywebview 送給 JS 的 JSON： [[1234567890123456789, NaN], [9007199254740993, Infinity]]
  ```
  JS 那一邊（node，和瀏覽器同一套 `JSON.parse`）：
  ```bash
  node -e 'console.log(JSON.parse("[1234567890123456789, 9007199254740993]")); try { JSON.parse("[NaN]") } catch (e) { console.log(e.name + ": " + e.message) }'
  ```
  ```text
  [ 1234567890123456800, 9007199254740992 ]
  SyntaxError: Unexpected token 'N', "[NaN]" is not valid JSON
  ```
- 確認程度：
  - Python 這一段已重現；整數失去精確度用 node 確認。
  - NaN 讓呼叫沒有回應是讀 pywebview 的程式推論的（它的 `js/api.js` 直接 `JSON.parse(value)`），沒有在真的視窗試。
- 建議修法：
  - `_jsonable` 把絕對值超過 `2**53 - 1` 的整數轉成字串；非有限的浮點數轉成字串或 None。型別分頁的最小、最大值同樣處理。
  - 測試：`json.dumps(rows, allow_nan=False)` 不會失敗；大整數是字串。
- 狀態：已修（2e80b79）

### F-013［中］［bug］樞紐、合併來源檔之後的群組彙總、移除重複（保留最後一筆）不是串流：整份資料放進記憶體

- 位置：
  - src/tablade/steps/aggregate.py:472（樞紐記「沒轉成欄的值」：`unique(maintain_order=True).str.join(…)`）
  - src/tablade/steps/aggregate.py:135（有 `__orig_*` 欄位時，也就是合併來源檔之後：`arg_min()` 取每組第一筆）
  - src/tablade/steps/remove_rows.py:203（`unique(keep="last", maintain_order=True)`）
  - 說明：src/tablade/steps/aggregate.py:7（「全部用 Polars 原生運算，可以串流」）、DESIGN.md §7.2、§8、CLAUDE.md 的串流規則
- 現象：這三種情況，Polars 的串流執行計畫裡出現 `in-memory-map`，代表那一段退回整份放進記憶體。記憶體用量跟著資料量成長，幾 GB 的檔可能記憶體不足。
- 證據（執行計畫，加上 1,000 萬列假資料的峰值記憶體；每種情況各開一個程序量）：
  ```python
  """哪些步驟在串流引擎裡退回「整份放進記憶體」。

  用法：python stream_fallback.py           列出每種情況的串流執行計畫有沒有 in-memory 的節點
        python stream_fallback.py mem 名稱   用 1,000 萬列的假資料實際跑一次，印出峰值記憶體（每種情況各開一個程序）
  """

  import re
  import resource
  import subprocess
  import sys
  import tempfile
  from pathlib import Path

  import polars as pl

  from tablade.steps import get


  def step(name, **params):
      return get(name).model_validate({"id": "s", "type": name, **params})


  SUM = [{"column": "金額", "op": "sum", "name": "合計"}]
  CASES = {
      "群組彙總（合併來源檔之前）": lambda lf: step("group_by", by=["門市"], aggregations=SUM).apply(
          lf
      ),
      "合併來源檔 → 群組彙總": lambda lf: step("group_by", by=["門市"], aggregations=SUM).apply(
          step("merge_files").apply(lf)
      ),
      "樞紐": lambda lf: step(
          "pivot", index=["門市"], on="月份", values="金額", columns=["1月", "2月"]
      ).apply(lf),
      "移除重複（保留第一筆）": lambda lf: step("dedupe", columns=["門市", "月份"]).apply(lf),
      "移除重複（保留最後一筆）": lambda lf: step(
          "dedupe", columns=["門市", "月份"], keep="last"
      ).apply(lf),
  }


  def data(path, rows):
      pl.select(
          pl.lit("a.csv").alias("__source_file"),
          pl.int_range(1, rows + 1, dtype=pl.Int64).alias("__line_no"),
          (pl.lit("門市") + (pl.int_range(0, rows) % 5000).cast(pl.String)).alias("門市"),
          (((pl.int_range(0, rows) // 5000) % 3 + 1).cast(pl.String) + pl.lit("月")).alias("月份"),
          (pl.int_range(0, rows) % 977).alias("金額"),
      ).write_parquet(path)
      return pl.scan_parquet(path)


  if len(sys.argv) == 1:
      lf = data(Path(tempfile.mkdtemp()) / "small.parquet", 100)
      for name, build in CASES.items():
          graph = build(lf).show_graph(
              engine="streaming", plan_stage="physical", raw_output=True, show=False
          )
          nodes = re.findall(r'label="([a-z][a-z\-\[\]]*)', graph.split("fallback>", 1)[1])
          mark = "退回 in-memory" if "in-memory-map" in nodes else "可以串流"
          print(f"{name}：{mark}  {nodes}")
      for name in CASES:
          out = subprocess.run(
              [sys.executable, __file__, "mem", name], capture_output=True, text=True
          )
          print(out.stdout.strip() or out.stderr.strip()[-200:])
  else:
      name = sys.argv[2]
      lf = data(Path(tempfile.mkdtemp()) / "big.parquet", 10_000_000)
      target = Path(tempfile.mkdtemp()) / "out.parquet"
      CASES[name](lf).sink_parquet(target, engine="streaming")
      rows = pl.scan_parquet(target).select(pl.len()).collect().item()
      peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1 << 20)  # macOS 的單位是 bytes
      print(f"{name}：輸出 {rows:,} 列，峰值記憶體 {peak:,.0f} MB")
  ```
  實際輸出：
  ```text
  群組彙總（合併來源檔之前）：可以串流  ['select', 'sort', 'select', 'group-by', 'select', 'multi-scan[parquet]']
  合併來源檔 → 群組彙總：退回 in-memory  ['select', 'sort', 'in-memory-map', 'select', 'with-columns', 'with-row-index', 'select', 'with-columns', 'multi-scan[parquet]']
  樞紐：退回 in-memory  ['select', 'sort', 'in-memory-map', 'multi-scan[parquet]']
  移除重複（保留第一筆）：可以串流  ['filter', 'is-first-distinct', 'multi-scan[parquet]']
  移除重複（保留最後一筆）：退回 in-memory  ['in-memory-map', 'multi-scan[parquet]']
  群組彙總（合併來源檔之前）：輸出 5,000 列，峰值記憶體 817 MB
  合併來源檔 → 群組彙總：輸出 5,000 列，峰值記憶體 1,534 MB
  樞紐：輸出 5,000 列，峰值記憶體 1,663 MB
  移除重複（保留第一筆）：輸出 15,000 列，峰值記憶體 823 MB
  移除重複（保留最後一筆）：輸出 15,000 列，峰值記憶體 1,909 MB
  ```
  預期：五種情況都可以串流，峰值記憶體相近。
- 確認程度：已重現（執行計畫＋1,000 萬列）。另一位審查者用 4,000 萬列量到的差距更大（群組彙總 0.9 GB，合併後 5.9 GB，樞紐 6.6 GB，保留最後一筆 7.6 GB），我沒有重跑那個規模。
- 建議修法：
  - `__orig_*` 改用 `first()`（每個來源已依行號排好，和 `__source_file` 的做法一樣）。
  - 樞紐的警告改成可以串流的彙總，例如「沒轉成欄的筆數」加上其中一個值。
  - 「保留最後一筆」換做法，或在說明與文件標明不能串流。
  - 測試：tests/test_large.py 加一項，檢查每種步驟的串流執行計畫沒有 `in-memory-map`。
- 狀態：已修（413c289）

### F-014［中］［bug］取消樞紐之後接群組彙總或樞紐：組的順序每次執行都可能不同

- 位置：src/tablade/steps/aggregate.py:149（`.sort(LINE_NO)`）；說明在 src/tablade/steps/aggregate.py:5、DESIGN.md §7.2、§8（「每組第一次出現的順序」）
- 現象：
  - 群組彙總的結果依「每組第一筆的行號」排序。取消樞紐之後，同一個行號有好幾列，好幾組的第一筆行號相同，排序分不出先後，`group_by` 本身又不保證順序。
  - 同一份資料重跑，列的順序會變；eager 與 stream 的順序也可能不同。數字是對的，但輸出不可重現。
  - 讀前面階段（做過取消樞紐）的結果時也一樣。
- 證據：
  ```python
  import polars as pl

  from tablade.engine.run import apply_step
  from tablade.steps import get

  months = [f"{m}月" for m in range(1, 13)]
  frame = pl.DataFrame(
      {
          "__source_file": ["a.csv"] * 3,
          "__line_no": [2, 3, 4],
          "門市": ["北", "中", "南"],
          **{m: [1, 2, 3] for m in months},
      }
  )
  unpivot = get("unpivot").model_validate(
      {
          "id": "s1",
          "type": "unpivot",
          "columns": months,
          "name_column": "月份",
          "value_column": "金額",
      }
  )
  group = get("group_by").model_validate(
      {
          "id": "s2",
          "type": "group_by",
          "by": ["月份"],
          "aggregations": [{"column": "金額", "op": "sum", "name": "合計"}],
      }
  )
  long, _, _ = apply_step(unpivot, frame)
  orders = set()
  for _ in range(20):  # 同一份資料跑 20 次
      out, _, _ = apply_step(group, long)
      orders.add(tuple(out["月份"].to_list()))
  print(f"20 次得到 {len(orders)} 種順序")
  for order in sorted(orders)[:3]:
      print(" ", list(order))
  ```
  實際輸出（每次執行的種數可能不同）：
  ```text
  20 次得到 3 種順序
    ['1月', '2月', '3月', '4月', '5月', '6月', '7月', '8月', '9月', '10月', '11月', '12月']
    ['1月', '2月', '3月', '4月', '9月', '10月', '11月', '12月', '5月', '6月', '7月', '8月']
    ['5月', '6月', '7月', '8月', '1月', '2月', '3月', '4月', '9月', '10月', '11月', '12月']
  ```
  預期：只有一種順序（1月、2月…12月）。
- 確認程度：已重現。
- 建議修法：分組前先 `with_row_index`，彙總時取最小值、用它排序後丟掉；tests/test_large.py 補一個「取消樞紐 → 群組彙總」的 stream＝eager 比對。
- 狀態：已修（413c289）

### F-015［中］［bug］樞紐：「欄依據欄位」是空值或空白的列，金額不在任何一欄，也沒有警告

- 位置：src/tablade/steps/aggregate.py:471-472、src/tablade/steps/aggregate.py:475-486；src/tablade/steps/base.py:302-305（`data_values` 不列空白，所以空白也沒辦法變成一欄）
- 現象：資料裡多出來的值會警告「這一組有沒轉成欄的…」，但依據欄是空值或空字串的列被排除在這項檢查之外：那些列的金額不會出現在結果的任何一欄，也沒有警告。
- 證據：「南」那一組有 1,000 與 2,000 兩筆，月份分別是空值與空字串。
  ```python
  import polars as pl

  from tablade.engine.run import apply_step
  from tablade.steps import get

  frame = pl.DataFrame(
      {
          "__source_file": ["a.csv"] * 5,
          "__line_no": [2, 3, 4, 5, 6],
          "門市": ["北", "北", "北", "南", "南"],
          "月份": ["1月", "2月", "3月", None, ""],
          "金額": [100, 200, 300, 1000, 2000],
      }
  )
  step = get("pivot").model_validate(
      {
          "id": "s1",
          "type": "pivot",
          "index": ["門市"],
          "on": "月份",
          "values": "金額",
          "columns": ["1月", "2月"],
      }
  )
  out, total, warnings = apply_step(step, frame)
  print(out.select("門市", "1月", "2月").to_dicts())
  print("警告：", [(w.line_no, w.message) for w in warnings])
  ```
  實際輸出：
  ```text
  [{'門市': '北', '1月': 100, '2月': 200}, {'門市': '南', '1月': None, '2月': None}]
  警告： [(2, '這一組有沒轉成欄的「月份」：3月')]
  ```
  預期：「南」那一組也有警告，說明有沒轉成欄的空白。
- 確認程度：已重現。
- 建議修法：空值與空白也算「沒轉成欄的值」，訊息裡寫成「（空白）」；tests/test_steps.py 的 `test_pivot_empty_cells_ops_and_new_values` 補這個情況。
- 狀態：已修（413c289）

### F-016［中］［bug］結合：這一邊的鍵不存在時，丟出沒有包裝的 Polars 例外

- 位置：src/tablade/steps/join.py:295-323（`warning_checks` 只處理另一張表缺欄）；src/tablade/engine/run.py:477（對輸入檢查的警告在 `try` 外面）、src/tablade/engine/run.py:431-444
- 現象：
  - `warn_unmatched`（預設開）時，引擎在套用步驟之前先對輸入算「沒對到的列」。這一邊的鍵不存在時，這一段直接丟出 `polars.exceptions.ColumnNotFoundError`。
  - 它不是 `RunError`：命令列是 traceback；畫面上 `Session._compute` 沒有接，`state()` 之後每次都失敗（和「欄名含大括號」那一項同一種後果）。
  - 關掉 `warn_unmatched` 時是正常的「欄位不存在：…」。
- 證據：
  ```python
  import polars as pl

  from tablade.engine.run import apply_step
  from tablade.steps import get

  left = pl.DataFrame({"__source_file": ["a.csv"], "__line_no": [2], "品號": ["P1"], "金額": ["1"]})
  right = pl.DataFrame(
      {"__source_file": ["b.csv"], "__line_no": [2], "代號": ["P1"], "類別": ["文具"]}
  )
  for warn in (True, False):
      step = get("join").model_validate(
          {
              "id": "j",
              "type": "join",
              "right": "對照",
              "on": [{"left": "不存在", "right": "代號"}],
              "columns": ["類別"],
              "warn_unmatched": warn,
          }
      )
      try:
          apply_step(step, left, tables={"對照": right})
      except Exception as err:
          print(f"warn_unmatched={warn} → {type(err).__name__}: {str(err).splitlines()[0][:90]}")
  ```
  實際輸出：
  ```text
  warn_unmatched=True → ColumnNotFoundError: unable to find column "不存在"; valid columns: ["__source_file", "__line_no", "品號", "金額"]
  warn_unmatched=False → RunError: 步驟 j（join）失敗：欄位不存在：不存在
  ```
  預期：兩種情況都是 `RunError`「欄位不存在：不存在」。
- 確認程度：已重現（`apply_step`）。畫面卡住是依 `Session._compute` 只接 `RunError` 推論的。
- 建議修法：`collect_warnings` 的查詢也包成 `RunError`（`apply_step` 與 `_stream_steps` 都受益）；`Join.warning_checks()` 這一邊缺欄時回傳空清單，讓 `apply()` 報錯。測試：tests/test_steps.py 的結合錯誤案例加上 `warn_unmatched` 開著的版本。
- 狀態：已修（413c289）

### F-017［中］［bug］把第一列作為欄位標題：標題有空白格或重複時，連建立步驟的那個檔都警告「標題不同」

- 位置：src/tablade/steps/headers.py:99-103（拿原始的第一列和 `names` 比）；src/tablade/steps/headers.py:71-84（`suggest_names` 會把空白補成「欄N」、重複的加 `_2`、去掉開頭的 `__`）
- 現象：建立步驟時欄名經過整理，執行時卻拿沒整理過的第一列來比。標題列只要有空白格、重複的欄名或 `__` 開頭的欄名，每個檔都會產生「這個檔案的標題列和 recipe 記錄的欄名不同」的警告，真的標題不同的檔反而分不出來。
- 證據：
  ```python
  import polars as pl

  from tablade.engine.run import apply_step
  from tablade.steps import get

  frame = pl.DataFrame(
      {
          "__source_file": ["a.csv"] * 2,
          "__line_no": [1, 2],
          "欄1": ["日期", "115/01/05"],
          "欄2": ["", "北區門市"],
          "欄3": ["金額", "100"],
          "欄4": ["金額", "200"],
      }
  )
  cls = get("promote_headers")
  params = cls.suggest(frame, {})  # 新增步驟時，從第一列產生的欄名
  print("建議的欄名：", params)
  step = cls.model_validate({"id": "s1", "type": "promote_headers", **params})
  _, total, warnings = apply_step(step, frame)  # 套用在同一個檔
  print("警告數：", total, [w.message for w in warnings])
  ```
  實際輸出：
  ```text
  建議的欄名： {'names': ['日期', '欄2', '金額', '金額_2']}
  警告數： 1 ['這個檔案的標題列和 recipe 記錄的欄名不同，仍照 recipe 命名']
  ```
  預期：警告數是 0。
- 確認程度：已重現。
- 建議修法：比對 `suggest_names(第一列) != names`；tests/test_steps.py 補上標題有空白、重複的案例。
- 狀態：已修（413c289）

### F-018［中］［bug］篩選的數值條件，說明文字用科學記號、只留 6 位有效數字

- 位置：src/tablade/steps/filter.py:143（`{self.value:g}`）；「依條件複製欄位」「將列進行分類」的說明也用到
- 現象：一百萬以上的門檻在步驟清單與處理紀錄裡顯示成 `1e+06`；`1234567` 顯示成 `1.23457e+06`，`100000.5` 顯示成 `100000`。實際篩選用的是正確的值，但畫面與處理紀錄寫的門檻和實際的不一樣。
- 證據：
  ```python
  from tablade.steps import get

  for value in (999999, 1000000, 1234567, 100000.5):
      step = get("filter").model_validate(
          {
              "id": "s1",
              "type": "filter",
              "conditions": [{"column": "金額", "op": "gt", "value": value}],
          }
      )
      print(f"value={value!r} → {step.describe()}")
  ```
  實際輸出：
  ```text
  value=999999 → 保留：金額 > 999999
  value=1000000 → 保留：金額 > 1e+06
  value=1234567 → 保留：金額 > 1.23457e+06
  value=100000.5 → 保留：金額 > 100000
  ```
- 確認程度：已重現。
- 建議修法：整數值印成整數（可以加千分位），其他用 `repr()`；tests/test_steps.py 補上大數字的 `describe()`。
- 狀態：已修（413c289）

### F-019［中］［bug］Recipe 的 `type`／`op`／`reader`／`output_format` 是清單或物件時，驗證器直接丟 `TypeError`

- 位置：src/tablade/recipe/validator.py:471、src/tablade/recipe/validator.py:835（同一種寫法還有 `reader`）；src/tablade/export/outputs.py:85
- 現象：
  - 驗證器把這些值直接拿去查表，遇到清單或物件就是 `TypeError: unhashable type`。
  - `tablade validate --json` 與 skill 裡的 `validate_recipe.py` 沒有任何 JSON 輸出，AI 拿不到 `path` 與 `error`；畫面開啟這種 Recipe 時只顯示 `TypeError: unhashable type: 'list'`。
  - pydantic 對同樣的輸入會正常回報錯誤。AI 寫 Recipe 時把字串寫成清單並不少見。
- 證據：
  ```python
  import copy

  from tablade.recipe.schema import RecipeError, parse_recipe
  from tablade.recipe.spec import build_spec
  from tablade.recipe.validator import validate

  BASE = {
      "tablade_recipe": "1.0",
      "name": "x",
      "stages": [
          {
              "name": "主要",
              "input": {"files": ["a.csv"], "reader": "csv", "encoding": "auto"},
              "steps": [
                  {"id": "s1", "type": "remove_top_rows", "n": 2},
                  {
                      "id": "s2",
                      "type": "filter",
                      "conditions": [{"column": "a", "op": "equals", "value": "x"}],
                  },
              ],
              "output_file": "out",
              "output_format": "csv",
          }
      ],
      "output": {"keep_system_columns": True, "manifest": True},
  }


  def change(path, value):
      data = copy.deepcopy(BASE)
      node = data
      for key in path[:-1]:
          node = node[key]
      node[path[-1]] = value
      return data


  cases = {
      "步驟的 type 是清單": change(["stages", 0, "steps", 0, "type"], ["remove_top_rows"]),
      "條件的 op 是清單": change(["stages", 0, "steps", 1, "conditions", 0, "op"], ["equals"]),
      "output_format 是清單": change(["stages", 0, "output_format"], ["csv"]),
      "reader 是清單": change(["stages", 0, "input", "reader"], ["csv"]),
  }
  for title, data in cases.items():
      try:
          issues = validate(data, build_spec(), {})
          result = f"回報 {len(issues)} 個問題：{issues[0]['path']} {issues[0]['error']}"
      except Exception as err:
          result = f"{type(err).__name__}: {err}"
      try:
          parse_recipe(data)
          strict = "通過"
      except RecipeError as err:
          strict = f"{err.issues[0]['path']} {err.issues[0]['error']}"
      print(f"{title}\n  驗證器 → {result}\n  pydantic → {strict}")
  ```
  實際輸出：
  ```text
  步驟的 type 是清單
    驗證器 → TypeError: unhashable type: 'list'
    pydantic → stages[0].steps[0] Input tag '['remove_top_rows']' found using 'type' does not match any of the expected tags: 'remove_top_rows', 'remove_bottom_rows', 'remove_blank_rows', 'dedupe', 'filter', 'split_delimited', 'split_column', 'classify_rows', 'extract_regex', 'split_fixed_width', 'promote_headers', 'conditional_copy', 'fill_down', 'remove_columns', 'select_columns', 'rename_columns', 'trim_clean', 'replace_values', 'parse_number', 'parse_date', 'unpivot', 'group_by', 'pivot', 'merge_files', 'join'
  條件的 op 是清單
    驗證器 → TypeError: unhashable type: 'list'
    pydantic → stages[0].steps[1].conditions[0] Input tag '['equals']' found using 'op' does not match any of the expected tags: 'equals', 'not_equals', 'contains', 'not_contains', 'starts_with', 'ends_with', 'regex', 'not_regex', 'is_empty', 'is_not_empty', 'gt', 'ge', 'lt', 'le', 'num_equals'
  output_format 是清單
    驗證器 → TypeError: unhashable type: 'list'
    pydantic → stages[0].output_format 不支援的值（可用：'parquet' or 'csv'）
  reader 是清單
    驗證器 → TypeError: unhashable type: 'list'
    pydantic → stages[0].input.reader 不支援的值（可用：'text_lines', 'csv', 'excel', 'parquet' or 'stages'）
  ```
  預期：驗證器回報一個問題，指出哪個欄位的型別不對。
- 確認程度：已重現。
- 建議修法：查表前先確認是字串，不是就回報「應該是文字」；`validate()` 最外層再包一層，把沒預期的例外轉成一筆問題。tests/test_recipe.py 的「驗證器與 pydantic 結論相同」那組測試加上這些輸入。
- 狀態：已修（2e80b79）

### F-020［中］［bug］`tablade run` 沒有接住找不到檔、編碼錯誤等例外；步驟錯誤的 `path` 和 `validate --run` 不一樣

- 位置：src/tablade/cli.py:81-91；src/tablade/engine/check.py:59-62（找不到檔在沒有試跑時只是「提醒」）
- 現象：
  - `run` 只接 `OutputError` 與 `RunError`。找不到輸入檔時，驗證階段只給提醒，接著引擎丟出 `InputError`，沒有人接：印出 traceback，`--json` 沒有任何輸出。編碼錯誤（`DecodeError`）、寫不進去（`OSError`）同理。
  - 同一個步驟錯誤，`run --json` 回報的 `path` 是 `steps`（舊寫法才有的路徑），`validate --run --json` 是 `stages[0].steps[0]`。
- 證據：
  ```python
  import contextlib
  import io
  import json
  import tempfile
  from pathlib import Path

  from tablade.cli import main

  work = Path(tempfile.mkdtemp())
  (work / "a.csv").write_text("品號,金額\nP1,1\n", encoding="utf-8")


  def recipe(name, files, steps=()):
      path = work / f"{name}.recipe.json"
      path.write_text(
          json.dumps(
              {
                  "tablade_recipe": "1.0",
                  "name": name,
                  "stages": [
                      {
                          "name": "主要",
                          "input": {"files": files, "reader": "csv", "encoding": "auto"},
                          "steps": list(steps),
                          "output_format": "parquet",
                      }
                  ],
                  "output": {"keep_system_columns": True, "manifest": False},
              },
              ensure_ascii=False,
          ),
          encoding="utf-8",
      )
      return str(path)


  def call(*args):
      out = io.StringIO()
      try:
          with contextlib.redirect_stdout(out):
              code = main(list(args))
          issues = json.loads(out.getvalue()).get("issues", [])
          print(
              " ".join(args[:-1]),
              f"→ 結束代碼 {code}，issues：",
              [(i["path"], i["error"][:40]) for i in issues],
          )
      except Exception as err:
          print(
              " ".join(args[:-1]),
              f"→ 沒有接住的例外 {type(err).__name__}: {str(err)[:40]}；stdout {len(out.getvalue())} 個字",
          )


  missing = recipe("missing", ["沒有這個檔.csv"])
  call("validate", "--run", "--json", missing)
  call("run", "--json", missing)
  # 步驟在資料上才會出錯：驗證器放行系統欄位的引用（見另一項），執行時找不到欄位
  broken = recipe("broken", ["a.csv"], [{"id": "s1", "type": "fill_down", "columns": ["__sheet"]}])
  call("validate", "--run", "--json", broken)
  call("run", "--json", broken)
  ```
  實際輸出：
  ```text
  validate --run --json → 結束代碼 1，issues： [('stages[0].input.files', '找不到檔案「沒有這個檔.csv」（相對於 <暫存資料夾>')]
  run --json → 沒有接住的例外 InputError: 找不到檔案「沒有這個檔.csv」（相對於 <暫存資料夾>；stdout 0 個字
  validate --run --json → 結束代碼 1，issues： [('stages[0].steps[0]', '步驟 s1（fill_down）失敗：欄位不存在：__sheet')]
  run --json → 結束代碼 1，issues： [('steps', '步驟 s1（fill_down）失敗：欄位不存在：__sheet')]
  ```
  預期：四個指令都回報 issues，`path` 的寫法一致。
- 確認程度：已重現（在同一個程序裡呼叫 `tablade.cli.main`）。
- 建議修法：`run` 也接 `InputError`、`DecodeError`、`OSError`，轉成問題；`RunError` 的 `path` 用 `check._step_path()`。tests/test_export_and_cli.py 補上找不到檔的 `run --json`。
- 狀態：已修（2e80b79）

### F-021［中］［bug］驗證器放行不存在的系統欄位；`__row_type` 只存在於驗證器

- 位置：src/tablade/recipe/validator.py:48-58（`SYSTEM_COLUMNS`，`__row_type` 在第 57 行）、src/tablade/recipe/validator.py:1084-1095
- 現象：
  - `__` 開頭的引用只檢查「在不在 `SYSTEM_COLUMNS` 清單裡」，不看這個階段實際有沒有那一欄：`__sheet` 只有讀 Excel 全部工作表時才有，`__stage` 只有讀前面階段時才有，`__orig_*` 要合併來源檔之後才有。
  - `__row_type` 在 `src/` 其他地方完全沒出現（早期設計留下的），但驗證器當成合法的欄位。
  - 欄位效果是「不變」的步驟（向下填滿、清理文字、文字轉數字…）引用 `__source_file`、`__line_no` 也通過驗證，引擎卻回報欄位不存在。
  - 結果：驗證通過、執行才失敗，而且失敗訊息是 Polars 的原文。
- 證據：
  ```python
  import tempfile
  from pathlib import Path

  from tablade.engine.check import check_recipe
  from tablade.engine.run import RunError, execute

  work = Path(tempfile.mkdtemp())
  (work / "a.csv").write_text("品號,金額\nP1,1\n", encoding="utf-8")


  def recipe(step):
      return {
          "tablade_recipe": "1.0",
          "name": "x",
          "stages": [
              {
                  "name": "主要",
                  "input": {"files": ["a.csv"], "reader": "csv", "encoding": "auto"},
                  "steps": [step],
                  "output_format": "parquet",
              }
          ],
          "output": {"keep_system_columns": True, "manifest": False},
      }


  steps = {
      "篩選 __sheet（讀的是 CSV，沒有這一欄）": {
          "id": "s1",
          "type": "filter",
          "conditions": [{"column": "__sheet", "op": "is_not_empty"}],
      },
      "篩選 __row_type（程式裡沒有任何地方產生這一欄）": {
          "id": "s1",
          "type": "filter",
          "conditions": [{"column": "__row_type", "op": "is_not_empty"}],
      },
      "向下填滿 __source_file": {"id": "s1", "type": "fill_down", "columns": ["__source_file"]},
      "篩選 不存在的一般欄（對照）": {
          "id": "s1",
          "type": "filter",
          "conditions": [{"column": "備註", "op": "is_not_empty"}],
      },
  }
  for title, step in steps.items():
      result = check_recipe(recipe(step), work)
      verdict = "通過" if result.ok else result.issues[0]["error"]
      if result.recipe is None:
          print(f"{title}\n  驗證 → {verdict}")
          continue
      try:
          execute(result.recipe, work, mode="eager")
          ran = "成功"
      except RunError as err:
          ran = err.message.splitlines()[0][:70]
      print(f"{title}\n  驗證 → {verdict}\n  執行 → {ran}")
  ```
  實際輸出：
  ```text
  篩選 __sheet（讀的是 CSV，沒有這一欄）
    驗證 → 通過
    執行 → ColumnNotFoundError: unable to find column "__sheet"; valid columns: [
  篩選 __row_type（程式裡沒有任何地方產生這一欄）
    驗證 → 通過
    執行 → ColumnNotFoundError: unable to find column "__row_type"; valid columns
  向下填滿 __source_file
    驗證 → 通過
    執行 → 欄位不存在：__source_file
  篩選 不存在的一般欄（對照）
    驗證 → 欄位「備註」在這一步之前不存在
  ```
  預期：前三種在驗證時就報錯，和最後一種一樣。
- 確認程度：已重現。
- 建議修法：驗證器依階段算出實際有的系統欄位（一律有 `__source_file`、`__line_no`；Excel `*` 加 `__sheet`；讀前面階段加 `__stage`；合併來源檔之後加 `__orig_*`）；拿掉 `__row_type`（要更新 schema lock）；系統欄位只能用在條件時，SKILL.md 寫明。
- 狀態：已修（2e80b79）

### F-022［中］［bug］欄位檢查只看第一個檔的標題列：多個檔欄位不同時誤報，`tablade run` 無法執行

- 位置：src/tablade/ingest/sources.py:823（`peek_columns` 只取第一個檔）；src/tablade/engine/check.py:63-64
- 現象：
  - 引擎對多個檔會對齊欄位、缺的補空值（DESIGN.md §5.3）。但驗證只拿第一個檔的欄位，引用「只在後面的檔才有」的欄就報「欄位不存在」。
  - `tablade run` 要驗證通過才執行，所以整個被擋下；把檔案順序對調就通過。畫面開啟 Recipe 時「有問題仍會載入」，所以畫面只是多一則誤報。
- 證據：
  ```python
  import tempfile
  from pathlib import Path

  from tablade.engine.check import check_recipe
  from tablade.engine.run import execute
  from tablade.recipe.schema import parse_recipe

  work = Path(tempfile.mkdtemp())
  (work / "a.csv").write_text("品號,金額\nP1,1\n", encoding="utf-8")
  (work / "b.csv").write_text("品號,金額,備註\nP2,2,急件\nP3,3,\n", encoding="utf-8")
  data = {
      "tablade_recipe": "1.0",
      "name": "x",
      "stages": [
          {
              "name": "主要",
              "input": {"files": ["a.csv", "b.csv"], "reader": "csv", "encoding": "auto"},
              "steps": [{"id": "s1", "type": "fill_down", "columns": ["備註"]}],
              "output_format": "parquet",
          }
      ],
      "output": {"keep_system_columns": False, "manifest": False},
  }
  result = check_recipe(data, work)  # tablade validate／tablade run 都先做這一步
  print("驗證 →", [(i["severity"], i["error"], i.get("hint")) for i in result.issues])
  frame, _ = execute(parse_recipe(data), work, mode="eager")  # 直接交給引擎
  print("引擎 →", frame.columns, frame.rows())
  ```
  實際輸出：
  ```text
  驗證 → [('error', '欄位「備註」在這一步之前不存在', '目前的欄位：品號、金額')]
  引擎 → ['品號', '金額', '備註'] [('P1', '1', None), ('P2', '2', '急件'), ('P3', '3', '急件')]
  ```
  預期：驗證通過。
- 確認程度：已重現。
- 建議修法：`peek_columns` 看每個檔、取聯集（檔案很多時可以設上限，超過就不檢查欄位引用）；tests/test_recipe.py 補上兩個欄位不同的檔。
- 狀態：已修（2e80b79）

### F-023［中］［bug］`tablade validate` 不檢查輸出檔的規劃：驗證通過，`run` 才失敗

- 位置：src/tablade/engine/check.py:76-102（`check_recipe`、`check_recipe_file`）；規則在 src/tablade/export/outputs.py:53-95（`plan_outputs`）
- 現象：
  - 「最後一個階段沒填檔名、用 Recipe 名稱」造成的撞名，以及「寫出的位置是輸入檔」，只在 `plan_outputs` 檢查。`check_recipe_file` 明明知道 Recipe 的路徑，卻沒有做這一步。
  - AI 用 `tablade validate` 自我檢查得到「通過」，使用者 `tablade run` 才看到錯誤。（畫面上的「輸出設定」與匯出按鈕會顯示這些錯誤，不受影響。）
- 證據：
  ```python
  import contextlib
  import io
  import json
  import tempfile
  from pathlib import Path

  from tablade.cli import main

  work = Path(tempfile.mkdtemp())
  (work / "a.csv").write_text("品號,金額\nP1,1\n", encoding="utf-8")
  source = {"files": ["a.csv"], "reader": "csv", "encoding": "auto"}
  path = work / "合計.recipe.json"
  path.write_text(
      json.dumps(
          {
              "tablade_recipe": "1.0",
              "name": "合計",
              "stages": [
                  # 中間的階段填了「合計」，最後一個階段沒填：預設也是 Recipe 的名稱「合計」
                  {
                      "name": "對照",
                      "input": source,
                      "steps": [],
                      "output_file": "合計",
                      "output_format": "parquet",
                  },
                  {
                      "name": "主要",
                      "input": {"reader": "stages", "stages": ["對照"]},
                      "steps": [],
                      "output_format": "parquet",
                  },
              ],
              "output": {"keep_system_columns": True, "manifest": False},
          },
          ensure_ascii=False,
      ),
      encoding="utf-8",
  )
  for command in (["validate"], ["validate", "--run"], ["run"]):
      out = io.StringIO()
      with contextlib.redirect_stdout(out):
          code = main([*command, "--json", str(path)])
      issues = [i["error"] for i in json.loads(out.getvalue()).get("issues", [])]
      print("tablade", " ".join(command), f"→ 結束代碼 {code}", issues)
  ```
  實際輸出：
  ```text
  tablade validate → 結束代碼 0 []
  tablade validate --run → 結束代碼 0 []
  tablade run → 結束代碼 1 ['階段「主要」：輸出檔名「合計.parquet」和階段「對照」重複']
  ```
  預期：`validate` 就回報這個問題。
- 確認程度：已重現。
- 建議修法：`check_recipe_file` 多跑一次 `plan_outputs(data, base, default_output_name(path))`，把錯誤併進問題清單；tests/test_export_and_cli.py 補這個案例。
- 狀態：已修（2e80b79）

### F-024［中］［bug］skill 的驗證器通過、Tablade 拒絕：步驟自己的規則沒有進到驗證器

- 位置：規則在 src/tablade/steps/report.py:226-240、src/tablade/steps/report.py:379-393、src/tablade/steps/convert.py:316-330、src/tablade/steps/text.py:246-252（pydantic 的 validator）；src/tablade/recipe/validator.py 沒有對應的檢查；DESIGN.md §17.4 寫「與 pydantic 一致」
- 現象：下面這些只有 Tablade（pydantic）會擋。AI 用 skill 裡的驗證器自我檢查會得到通過，使用者開啟時才報錯：
  - 正規表示式的語法（Tablade 用 Rust 的語法，不支援 look-ahead 等）、群組數和新欄名的數量不同。
  - 依固定位置拆欄的開始位置沒有由小到大。
  - 字串結尾多一個換行的版本號、階段名稱、步驟 id：驗證器用 Python 的 `$`（會比對到結尾換行之前），pydantic 不會。
- 證據：
  ```python
  from tablade.recipe.schema import RecipeError, parse_recipe
  from tablade.recipe.spec import build_spec
  from tablade.recipe.validator import validate


  def recipe(step):
      return {
          "tablade_recipe": "1.0",
          "name": "x",
          "stages": [
              {
                  "name": "主要",
                  "input": {"files": ["a.txt"], "reader": "text_lines", "encoding": "auto"},
                  "steps": [step],
                  "output_format": "parquet",
              }
          ],
          "output": {"keep_system_columns": True, "manifest": False},
      }


  steps = {
      "extract_regex：用了 look-ahead": {
          "id": "s1",
          "type": "extract_regex",
          "column": "line",
          "pattern": "(?=a)(.)",
          "names": ["x"],
      },
      "extract_regex：2 個群組、1 個欄名": {
          "id": "s1",
          "type": "extract_regex",
          "column": "line",
          "pattern": "(a)(b)",
          "names": ["x"],
      },
      "split_fixed_width：位置沒有由小到大": {
          "id": "s1",
          "type": "split_fixed_width",
          "starts": [5, 1],
          "names": ["a", "b"],
      },
      "步驟 id 結尾有換行": {"id": "s1\n", "type": "remove_top_rows", "n": 1},
  }
  for title, step in steps.items():
      issues = [i for i in validate(recipe(step), build_spec(), {}) if i["severity"] == "error"]
      try:
          parse_recipe(recipe(step))
          strict = "通過"
      except RecipeError as err:
          strict = err.issues[0]["error"][:60]
      print(
          f"{title}\n  skill 的驗證器 → {'通過' if not issues else issues[0]['error']}\n  Tablade（pydantic）→ {strict}"
      )
  ```
  實際輸出：
  ```text
  extract_regex：用了 look-ahead
    skill 的驗證器 → 通過
    Tablade（pydantic）→ 正規表示式有誤：regex error: regex parse error:
      (?=a)(.)
      ^^^
  extract_regex：2 個群組、1 個欄名
    skill 的驗證器 → 通過
    Tablade（pydantic）→ 正規表示式有 2 個群組，但列了 1 個新欄名
  split_fixed_width：位置沒有由小到大
    skill 的驗證器 → 通過
    Tablade（pydantic）→ 開始位置要由小到大
  步驟 id 結尾有換行
    skill 的驗證器 → 通過
    Tablade（pydantic）→ 格式不符
  ```
- 確認程度：已重現（直接呼叫 `validate()`，和 skill 裡的是同一份程式）。
- 建議修法：
  - 標準函式庫做得到的先補上：開始位置遞增、用 Python `re` 算群組數、看到 `(?=`／`(?!`／`(?<` 就報錯；`pattern` 檢查改用 `re.fullmatch`。
  - 做不到的（Rust 的正規表示式語法）在 SKILL.md 寫明「以 Tablade 的試跑為準」，DESIGN.md §17.4 改掉「結論相同」的說法。
- 狀態：已修（2e80b79）

### F-025［中］［bug］打包後的 App 下載的 AI skill 只有 1 個範例，Release 附的有 4 個

- 位置：tablade.spec:27（`datas` 只帶 `examples/sales_detail.recipe.json`）；src/tablade/skill.py:349-356；scripts/build.py:121-135（檢查 skill 時不看範例數）
- 現象：App 裡「下載 Copilot skill」產生的 zip 少了 `sales_combined`、`sales_excel`、`sales_report` 三個範例。其中 `sales_combined` 是唯一示範多階段、結合與 CSV 輸出的。CI 的 Release 是用原始碼產生的，所以有 4 個：同一個版本號的 skill 有兩種內容。
- 證據：
  ```bash
  grep -n "examples" tablade.spec; ls examples/*.recipe.json | wc -l
  ```
  ```text
  27:        ("examples/sales_detail.recipe.json", "examples"),
         4
  ```
- 確認程度：只靠讀程式推論（沒有重新打包）。另一位審查者用 repo 裡現有的打包產物（比這個 commit 舊）實際產生過，只有 1 個範例。
- 建議修法：`datas` 改成整個 `examples/*.recipe.json`；`scripts/build.py` 的 skill 檢查加上範例數。
- 狀態：已修（2e80b79）

### F-026［中］［bug］匯出完成後畫面沒有更新：「輸出資料」不會變成完整模式

- 位置：src/tablade/api.py:435-447（`export_parquet` 的回傳沒有 `state`）；frontend/src/App.tsx:169-194（`call` 只有回應帶 `state` 時才更新畫面）、frontend/src/App.tsx:311-349
- 現象：
  - 其他會改變狀態的 API 都一起回傳新的 `state`，只有 `export_parquet` 沒有；前端也沒有任何地方另外呼叫 `api.state()`。
  - 停在「輸出資料」按「處理並匯出資料」，關掉摘要之後畫面還是匯出前的樣子：仍是抽樣模式的樣本，輸出設定的「已經存在，匯出時會覆蓋」也不出現。要再做一個會回傳 `state` 的動作（例如點別的步驟再點回來）才會變成完整模式。
  - 抽樣模式的滑鼠說明寫的是「匯出後『輸出資料』會改成讀匯出檔」。
- 證據（Python 這一段）：
  ```python
  import shutil
  import tempfile
  from pathlib import Path

  from tablade.api import Api
  from tablade.fixtures import write_all

  work = Path(tempfile.mkdtemp())
  write_all(work / "data")
  shutil.copy("examples/sales_detail.recipe.json", work)
  api = Api()
  api._session.load_recipe(work / "sales_detail.recipe.json")
  api.select_step(8)  # 選「輸出資料」；沒有按「載入資料及預覽」


  def show(title, state):
      preview, stage = state["preview"], state["stages"][0]
      print(
          f"{title}：input.loaded={state['input']['loaded']}，preview.mode={preview['mode']}，"
          f"preview.rows={preview['rows']}，匯出檔已存在={stage['output']['exists']}"
      )


  show("匯出前的 state", api.state()["state"])
  result = api.export_parquet()
  print("export_parquet 回傳的欄位：", sorted(result))
  show("匯出後的 state（前端要另外呼叫才拿得到）", api.state()["state"])
  print("rows(8, 0, 2)：", api.rows(8, 0, 2)["rows"][0][:4])
  ```
  實際輸出：
  ```text
  匯出前的 state：input.loaded=False，preview.mode=None，preview.rows=None，匯出檔已存在=False
  export_parquet 回傳的欄位： ['cancelled', 'columns', 'manifest', 'mode', 'ok', 'outputs', 'path', 'rows', 'warnings']
  匯出後的 state（前端要另外呼叫才拿得到）：input.loaded=False，preview.mode=full，preview.rows=70，匯出檔已存在=True
  rows(8, 0, 2)： ['data/明細_01.csv', 4, '115/01/14', '北區門市']
  ```
  前端：`grep -n "api.state()" frontend/src/App.tsx frontend/src/components/*.tsx` 沒有結果。
- 確認程度：Python 這一段已重現；前端是讀程式確認的，沒有在真的視窗操作。
- 建議修法：`export_parquet` 一起回傳 `state`（試跑出錯時也是），tests/test_app.py 的 `test_mutations_return_new_state` 加上匯出。
- 狀態：已修（2e80b79、413c289）

### F-027［中］［bug］沒先載入就匯出：完整模式的資料被「還沒載入資料」擋住，看不到

- 位置：frontend/src/App.tsx:940-948（標題旁的標籤）、frontend/src/App.tsx:974（`input.loaded` 才畫表格）；src/tablade/session.py:1436-1453、src/tablade/session.py:1483
- 現象：
  - 「處理並匯出資料」不用先載入。匯出後 Python 的 `state` 是 `input.loaded=False`、`preview.mode='full'`、`preview.rows=70`，`rows()` 也讀得到匯出檔（見上一項的輸出）。
  - 但前端畫表格的條件是 `input.loaded`：資料區仍是「按『載入資料及預覽』…」的提示，標題旁同時出現「70 列 × 8 欄」「完整模式」和灰色的「還沒載入資料」。
  - 下方的警告分頁、型別分頁不看這個條件，反而讀得到匯出檔。
- 證據：上一項的輸出（`input.loaded=False，preview.mode=full`）加上前端的分支：
  ```bash
  sed -n '942p;974p' frontend/src/App.tsx
  ```
  ```text
                      ) : !input.loaded ? (
                      ) : input.loaded ? (
  ```
- 確認程度：`state` 已重現；畫面是讀程式推論的（還要先修上一項，畫面才會拿到這份 `state`）。
- 建議修法：兩處的條件改成 `state.preview.full || input.loaded`。
- 狀態：已修（413c289）

### F-028［中］［bug］判斷「預覽有沒有變」的 key 漏了條件：表格與型別分頁會顯示舊資料

- 位置：frontend/src/App.tsx:449-463（`previewKey`）；frontend/src/components/PreviewGrid.tsx:124-129、frontend/src/components/TypesPane.tsx:46-60
- 現象：key 只有步驟位置、檔案清單、是否載入、讀取方式、編碼、到這一步為止的參數、列數、模式、匯出時間。下面這些情況 key 不變，表格不會重新取資料：
  - **結合的表單按「完整跑一次」**：左連接的列數不變，表格仍是用樣本的另一張表算出來的空值，警告卻已經消失。這正是這顆按鈕要解決的事。
  - **切換階段**：兩個都讀前面階段、都停在「讀取資料」、列數相同的階段，切過去還是上一個階段的資料。
  - **同一個階段把讀的階段從 A 換成 B**（列數相同）。
  - **切換語言**：型別分頁的「文字／整數／小數」停在舊的語言（frontend/src/components/TypesPane.tsx:21 的註解寫會跟著換）。
- 證據：
  ```bash
  sed -n '449,463p' frontend/src/App.tsx
  ```
  ```text
    const previewKey = useMemo(() => {
      if (!state) return ''
      const upto = state.steps.slice(0, state.preview.step + 1).map((s) => s.params)
      return JSON.stringify([
        state.preview.step,
        state.input.files,
        state.input.loaded,
        state.input.reader,
        state.input.encoding,
        upto,
        state.preview.rows,
        state.preview.mode,
        state.preview.full?.time,
      ])
    }, [state])
  ```
  裡面沒有 `state.current`、`state.input.stages`、另一張表是不是完整的結果、`state.lang`。
- 確認程度：只靠讀程式推論（另一位審查者在 Python 這一段確認過：完整跑一次前後，照這個算法算出來的 key 相同，但資料不同）。
- 建議修法：不要在前端挑欄位。Python 在 `state.preview` 給一個預覽內容的指紋（每一步的 `_Computed.key` 已經有，再加上階段與語言），前端只比它。
- 狀態：已修（2e80b79、413c289）

### F-029［中］［bug］步驟表單延後送出的修改，會寫到別的步驟，或被較慢的回應蓋掉

- 位置：frontend/src/components/form/StepForm.tsx:31-47、frontend/src/components/form/StepForm.tsx:52-66；frontend/src/App.tsx:1105-1109（`api.update_step(selected.index, values)`）；src/tablade/session.py:807-820（`update_step` 用位置找步驟）
- 現象：表單的修改等 350 毫秒才送出，元件卸載時補送；送的時候用的是步驟的「位置」。
  - 改了欄位後 350 毫秒內刪掉這一步（或它前面的步驟）、拖曳移動、切換階段：補送的修改落在遞補到那個位置的另一個步驟上。同類型的步驟不會有任何錯誤，參數就被換掉了。
  - 350 毫秒內按復原：復原馬上被補送的修改蓋回去。
  - Python 重算比較慢時（大的樣本要一兩秒）：打「a」送出、繼續打成「ab」，第一個回應回來時輸入框被改回「a」（`pending` 只涵蓋等待送出的那 350 毫秒，不涵蓋請求在路上的時間）。
- 證據：
  ```bash
  sed -n '1105,1109p' frontend/src/App.tsx; sed -n '41,47p' frontend/src/components/form/StepForm.tsx
  ```
  ```text
                    <StepForm
                      key={selected.id}
                      step={selected}
                      item={catalogItem}
                      onChange={(values) => void call(tr('更新步驟', 'Update step'), () => api.update_step(selected.index, values))}
    useEffect(
      () => () => {
        window.clearTimeout(timer.current)
        if (pending.current) latest.current.onChange(latest.current.draft)
      },
      [],
    )
  ```
- 確認程度：只靠讀程式推論（要在 350 毫秒內操作，沒有在真的視窗試）。另一位審查者在 Python 這一段確認過：`delete_step(0)` 之後再 `update_step(0, 草稿)`，草稿寫進了原本的第 2 步。
- 建議修法：`update_step` 多帶步驟 id，Python 核對不符就拒絕；前端在送其他指令（刪除、移動、切換、復原、存檔）之前先把草稿送出；回應回來時，參數和最後送出的草稿不同就不覆蓋輸入框。
- 狀態：已修（2e80b79、413c289）

### F-030［中］［bug］預覽表格沒有丟掉過時的回應；取資料失敗的那一頁永遠是「…」

- 位置：frontend/src/components/PreviewGrid.tsx:124-142；src/tablade/api.py:228-230（`rows` 沒有階段、沒有流水號）；DESIGN.md:540（「每個預覽請求帶流水號…前端也丟掉過時的回應」）
- 現象：
  - DESIGN.md §16.4 寫每個預覽請求帶流水號、丟掉過時的回應，程式沒有這個機制。
  - 換步驟或階段時表格會清掉快取，但還在路上的 `rows()` 回應回來後照樣寫進新的快取：那一頁（200 列）顯示的是上一個步驟或階段的資料，而且之後不會重取。
  - `rows()` 失敗時（`!res.ok`）直接 return：那一頁留在 `loading` 裡不會重試，格子一直是「…」，沒有錯誤訊息（「大整數／NaN」那一項就會走到這裡）。
- 證據：
  ```bash
  sed -n '131,142p' frontend/src/components/PreviewGrid.tsx
  ```
  ```text
    const load = useCallback(
      async (page: number) => {
        if (loading.current.has(page)) return
        loading.current.add(page)
        const res = await api.rows(step, page * PAGE, PAGE)
        if (!res.ok) return
        setColumns(res.columns)
        setPages((prev) => new Map(prev).set(page, res.rows))
        if (page === 0) onLoaded?.(res.total)
      },
      [api, step, onLoaded],
    )
  ```
- 確認程度：只靠讀程式推論（要剛好在回應回來之前切換，機率不高；沒有在真的視窗試）。
- 建議修法：重置時遞增一個世代編號，`load` 回來時編號不同就丟掉；失敗時從 `loading` 移除並顯示錯誤。DESIGN.md §16.4 照實際的做法寫。
- 狀態：已修（2e80b79、413c289）

### F-031［中］［bug］警告分頁標題的數字是清單的長度，不是實際的警告數

- 位置：frontend/src/App.tsx:1037-1038；src/tablade/session.py:1406-1410；src/tablade/engine/run.py:64（每項檢查最多留 100 則）
- 現象：某一步有 350 則警告時，步驟清單的標記寫 350，下面的分頁卻寫「警告（100）」，清單也沒有說明只列了前 100 則。使用者會以為只有 100 列有問題。
- 證據：
  ```python
  import json
  import tempfile
  from pathlib import Path

  from tablade.session import Session

  work = Path(tempfile.mkdtemp())
  (work / "a.csv").write_text("金額\n" + "N/A\n" * 350, encoding="utf-8")
  path = work / "w.recipe.json"
  path.write_text(
      json.dumps(
          {
              "tablade_recipe": "1.0",
              "name": "w",
              "stages": [
                  {
                      "name": "主要",
                      "input": {"files": ["a.csv"], "reader": "csv", "encoding": "auto"},
                      "steps": [
                          {"id": "s1", "type": "parse_number", "columns": ["金額"], "kind": "integer"}
                      ],
                      "output_format": "parquet",
                  }
              ],
              "output": {"keep_system_columns": True, "manifest": False},
          },
          ensure_ascii=False,
      ),
      encoding="utf-8",
  )
  session = Session()
  session.load_recipe(path)
  session.load_data()
  state = session.state()
  print("steps[0].warning_count：", state["steps"][0]["warning_count"])
  print("preview.warnings 的長度（警告分頁標題用的數字）：", len(state["preview"]["warnings"]))
  ```
  實際輸出：
  ```text
  steps[0].warning_count： 350
  preview.warnings 的長度（警告分頁標題用的數字）： 100
  ```
- 確認程度：數字已重現；分頁怎麼顯示是讀程式確認的（`warnings.length`）。
- 建議修法：分頁標題用那一步的 `warning_count`，清單最後寫「只列出前 N 則」。
- 狀態：已修（2e80b79、413c289）

### F-032［中］［bug］完整模式的列數很多時，表格捲不到後面的列

- 位置：frontend/src/components/PreviewGrid.tsx:20（每列 28px）、frontend/src/components/PreviewGrid.tsx:146-151、frontend/src/components/PreviewGrid.tsx:247
- 現象：虛擬捲動把整個表格的高度設成「列數 × 28px」。瀏覽器對單一元素的高度有上限（Chromium、WebKit 約 3,355 萬 px），大約 120 萬列以後就捲不到，點警告跳到那一列也到不了。完整模式的列數是匯出檔的列數，幾百萬列很常見。
- 證據：`28 × 1,198,373 ≈ 33,554,444 px`，超過上限。
- 確認程度：只靠讀程式推論（依瀏覽器已知的上限；沒有用幾百萬列的匯出檔在真的視窗試）。驗證方法：匯出 500 萬列，把捲軸拉到底，看最後一列的行號。
- 建議修法：超過上限時把虛擬高度等比縮放，或改成分段跳頁。
- 狀態：已修（413c289）

### F-033［中］［bug］條件換運算子時沿用舊的值：數字框顯示 100，卻報「應該是數字」

- 位置：frontend/src/components/form/fields.tsx:668-676（`changeOp`）；src/tablade/recipe/schema.py:200-236
- 現象：新增條件（預設「等於」）、填 100、再把運算子改成「≥」：值還是文字 `"100"`。數字框顯示 100，下面卻出現 `Input should be a valid number`（中文畫面也是這句英文，見「pydantic 的幾種錯誤沒有翻譯」那一項），要刪掉重打才會好。從表格右鍵「只保留等於這個值」再改運算子也一樣。
- 證據：
  ```bash
  uv run python -c "
  from pydantic import ValidationError
  from tablade.recipe.schema import pydantic_issues
  from tablade.steps import get
  try:
      get('filter').model_validate({'id': 's', 'type': 'filter', 'conditions': [{'column': 'a', 'op': 'ge', 'value': '100'}]})
  except ValidationError as err:
      print(pydantic_issues(err))"
  ```
  ```text
  [{'path': 'conditions[0].value', 'error': 'Input should be a valid number', 'severity': 'error'}]
  ```
- 確認程度：Python 這一段已重現；表單換運算子時保留舊值是讀程式確認的。
- 建議修法：`changeOp` 依新欄位的型別轉換舊值，轉不了就清掉。
- 狀態：已修（413c289）

### F-034［中］［bug］表單看不到部分驗證錯誤，只有步驟清單上的「錯誤」標記

- 位置：frontend/src/components/form/fields.tsx:847、frontend/src/components/form/fields.tsx:658-782、frontend/src/components/form/fields.tsx:576-600；frontend/src/components/form/StepForm.tsx:69-70
- 現象：下面這些錯誤，右邊的表單上完全沒有顯示，使用者只看到步驟被標成錯誤：
  - 篩選、依條件複製欄位把條件全部刪光（`conditions`：至少要有 1 項）。
  - 新欄名的清單按「新增」後留著空白的一項（`names[1]`：不能是空白）。
  - 手寫或 AI 寫的 Recipe 才會有的路徑（例如 `positions[0]`、`rules[0]` 底下多的欄位）。
- 證據：
  ```bash
  uv run python -c "
  from pydantic import ValidationError
  from tablade.recipe.schema import pydantic_issues
  from tablade.steps import get
  for name, params in (('filter', {'conditions': []}), ('split_column', {'column': 'a', 'delimiter': '|', 'names': ['x', '']})):
      try:
          get(name).model_validate({'id': 's', 'type': name, **params})
      except ValidationError as err:
          print(name, pydantic_issues(err))"
  ```
  ```text
  filter [{'path': 'conditions', 'error': '至少要有 1 項', 'severity': 'error'}]
  split_column [{'path': 'names[1]', 'error': '不能是空白', 'severity': 'error'}]
  ```
- 確認程度：錯誤的路徑已重現；表單不畫這些路徑是另一位審查者用實際的 `lib/schema.ts` 對 92 組參數比對出來的，我只讀了相關的程式，沒有在畫面上確認。
- 建議修法：每個欄位一律顯示「路徑落在它底下、還沒被子元件顯示過」的錯誤。
- 狀態：已修（413c289）

### F-035［中］［bug］不支援的步驟類型：右邊顯示成「讀取資料」的設定

- 位置：frontend/src/App.tsx:445-446、frontend/src/App.tsx:1104-1139；src/tablade/session.py:1190-1199
- 現象：AI 把 `type` 寫錯的步驟照樣會載入（狀態是錯誤）。選到它時前端找不到對應的步驟目錄，落到最後的分支，右邊畫的是「讀取資料」的面板：沒有錯誤說明，使用者還可能誤改讀取設定。
- 證據：
  ```bash
  sed -n '1104p;1130,1131p' frontend/src/App.tsx
  ```
  ```text
                  {selected && catalogItem ? (
                  ) : (
                    <InputPanel
  ```
- 確認程度：只靠讀程式推論；`state` 裡這種步驟的內容（`status='error'`、`errors=[不支援的步驟類型…]`）已用 Python 確認。
- 建議修法：`selected && !catalogItem` 時顯示這一步的錯誤與刪除按鈕。
- 狀態：已修（413c289）

### F-036［中］［bug］快捷鍵不看是否正在處理；⌘S 不會先送出輸入框裡的修改

- 位置：frontend/src/App.tsx:423-443；frontend/src/components/SourcePanel.tsx:334-354、frontend/src/components/RecipeTitle.tsx:36-41
- 現象：
  - 處理中或任何對話框開著時，⌘Z 照樣送出復原（Python 排在鎖後面，跑完就套用），⌘O 照樣開檔案對話框。按鈕都會看 `busy`，快捷鍵沒有。
  - 游標還在「輸出檔名」或 Recipe 名稱時按 ⌘S：這兩個欄位離開才送出，存檔的內容不含這次修改。
- 證據：
  ```bash
  sed -n '426,440p' frontend/src/App.tsx | grep -c busy
  ```
  ```text
  0
  ```
- 確認程度：只靠讀程式推論。
- 建議修法：handler 開頭檢查 `busy` 與有沒有開著的對話框；存檔前先讓目前的輸入框失去焦點、送出表單的草稿。
- 狀態：已修（413c289）

### F-037［低］［bug］「檢查並複製錯誤報告」試跑後，平行轉碼的工作程序沒有關掉

- 位置：src/tablade/session.py:1897-1931（`error_report`）；src/tablade/engine/check.py:87-93（試跑）
- 現象：
  - DESIGN.md §8 寫工作程序在「載入資料及預覽」「處理並匯出資料」「完整跑一次」跑完就關掉（`sources.release_workers`）。
  - `error_report()` 也會試跑（`check_recipe(..., dry_run=True)`）。還沒載入、檔案又大到要平行轉碼時，工作程序被開起來，之後沒有人關：8 個工作程序（DESIGN.md 寫合計約 260 MB）一直留到下次載入、匯出或關閉視窗。
- 證據：
  ```python
  import json
  import multiprocessing
  import tempfile
  from pathlib import Path


  def main():
      from tablade.ingest import sources
      from tablade.session import Session

      # 把平行轉碼的門檻（64 MB）調小，不用真的產生大檔
      sources.PARALLEL_MIN, sources.SEGMENT = 1 << 16, 1 << 15
      work = Path(tempfile.mkdtemp())
      (work / "data").mkdir()
      (work / "data" / "cp950.csv").write_bytes(
          ("門市,金額\n" + "北區門市,100\n" * 20_000).encode("cp950")
      )
      path = work / "pool.recipe.json"
      path.write_text(
          json.dumps(
              {
                  "tablade_recipe": "1.0",
                  "name": "pool",
                  "stages": [
                      {
                          "name": "主要",
                          "input": {"files": ["data/cp950.csv"], "reader": "csv", "encoding": "auto"},
                          "steps": [],
                          "output_format": "parquet",
                      }
                  ],
                  "output": {"keep_system_columns": True, "manifest": False},
              },
              ensure_ascii=False,
          ),
          encoding="utf-8",
      )
      session = Session()
      session.load_recipe(path)
      print("這台電腦平行轉碼用幾個工作程序：", sources._workers())
      session.error_report()  # 選單的「檢查並複製錯誤報告」（還沒載入資料）
      print("error_report() 之後還開著的工作程序：", len(multiprocessing.active_children()))
      session.load_data()
      print("load_data() 之後還開著的工作程序：", len(multiprocessing.active_children()))


  if __name__ == "__main__":  # 工作程序用 spawn 啟動，會重新載入這個檔
      main()
  ```
  實際輸出：
  ```text
  這台電腦平行轉碼用幾個工作程序： 8
  error_report() 之後還開著的工作程序： 8
  load_data() 之後還開著的工作程序： 0
  ```
  預期：`error_report()` 之後是 0。
- 確認程度：已重現（把 64 MB 的門檻調小；沒有量實際佔用的記憶體）。
- 建議修法：`error_report()` 加上 `try … finally: release_workers()`；tests/test_session.py 仿照載入、匯出的測試補一個。
- 狀態：已修（2e80b79）

### F-038［低］［bug］任何一個 API 呼叫結束，都會把畫面上的進度清掉

- 位置：src/tablade/api.py:65-66（`_wrap` 的 `finally: self._push_progress(None)`）
- 現象：
  - 每個 API 方法結束時都推一次 `progress = None`，不管它自己有沒有送過進度。
  - 匯出、載入進行中（另一條執行緒）時，只要有一個不用等 Session 鎖的呼叫結束，進度視窗最上面那一列就被清成「處理中…」，等下一次進度事件才恢復。
  - 目前會遇到的是啟動時背景執行的 `check_update`（最多等 3 秒才回來）：啟動後馬上按載入或匯出時會閃一下。影響很小，但之後只要多一個背景呼叫就會更常發生。
- 證據：
  ```python
  import os

  from tablade.api import Api


  class FakeState:
      def __setattr__(self, name, value):
          print(f"  window.state.{name} = {value!r}")


  class FakeWindow:
      state = FakeState()


  os.environ["TABLADE_NO_UPDATE_CHECK"] = "1"  # 不連線
  api = Api()
  api._window = FakeWindow()
  print("匯出進行中（另一條執行緒）推了一次進度：")
  api._push_progress({"stage": "執行（串流）", "file": "", "done": 10, "total": 100})
  print("這時另一個不用等 Session 鎖的 API 呼叫結束（例如啟動時的 check_update）：")
  api.check_update()
  ```
  實際輸出：
  ```text
  匯出進行中（另一條執行緒）推了一次進度：
    window.state.progress = {'stage': '執行（串流）', 'file': '', 'done': 10, 'total': 100}
  這時另一個不用等 Session 鎖的 API 呼叫結束（例如啟動時的 check_update）：
    window.state.progress = None
  ```
  預期：`check_update` 結束時不動進度。
- 確認程度：Python 這一段已重現（用假的視窗物件）；畫面上會不會看到閃動，沒有在真的視窗確認。
- 建議修法：只有會送進度的方法（`load_data`、`export_parquet`、`full_run`）結束時才清；或記下目前有幾個長時間工作在跑，歸零才清。
- 狀態：已修（2e80b79）

### F-039［低］［bug］`TABLADE_DEV_URL` 的「只接受 localhost」可以用 `localhost:埠@別的主機` 繞過

- 位置：src/tablade/app.py:22、src/tablade/app.py:48
- 現象：檢查是 `url.startswith(("http://localhost:", "http://127.0.0.1:"))`。`http://localhost:5173@example.com/` 開頭符合，但實際連到的主機是 `example.com`（`localhost:5173` 變成帳號密碼的部分）。視窗會載入那個網址的頁面，而頁面可以呼叫 Python 的 API。
- 影響有限：這是開發用的環境變數，能設環境變數的人本來就能做更多事；但程式與 tests/test_app.py 的 `test_dev_url_only_accepts_localhost` 宣稱「只接受 localhost」。
- 證據：
  ```python
  import os

  from tablade.app import dev_url

  for url in (
      "http://localhost:5173",
      "http://localhost:5173@example.com/",
      "http://127.0.0.1:1@example.com/",
      "http://example.com/",
  ):
      os.environ["TABLADE_DEV_URL"] = url
      try:
          print(f"{url} → 接受（{dev_url()}）")
      except ValueError:
          print(f"{url} → 拒絕")
  ```
  實際輸出：
  ```text
  http://localhost:5173 → 接受（http://localhost:5173）
  http://localhost:5173@example.com/ → 接受（http://localhost:5173@example.com/）
  http://127.0.0.1:1@example.com/ → 接受（http://127.0.0.1:1@example.com/）
  http://example.com/ → 拒絕
  ```
  預期：第 2、3 個網址被拒絕。
- 確認程度：已重現（只確認 `dev_url()` 接受；沒有真的開視窗連過去）。
- 建議修法：用 `urllib.parse.urlsplit()` 解析（只是字串解析，不連線），要求 `scheme == "http"`、`hostname in ("localhost", "127.0.0.1")`、沒有 `username`；測試補上這兩個網址。
- 狀態：已修（2e80b79）

### F-040［低］［bug］平行轉碼遇到任何 I/O 錯誤，都被當成「開不了工作程序」，之後整個工作階段只用單核心

- 位置：src/tablade/ingest/sources.py:476-485（`_transcode`）
- 現象：
  - `except (OSError, RuntimeError, BrokenProcessPool)` 的本意是「這台電腦不讓開新程序」，但暫存碟空間不足、暫存檔被別的程式鎖住等一般的 `OSError` 也會進來。
  - 進來之後 `_pool_failed = True`，這次開著的期間所有大檔都改用單核心轉碼（DESIGN.md §8 的實測：2 GB 的 cp950 檔 3 秒變 9 秒），沒有任何提示。
- 證據（用 monkeypatch 模擬平行轉碼中途的 I/O 錯誤）：
  ```python
  import tempfile
  from pathlib import Path

  from tablade.ingest import sources

  work = Path(tempfile.mkdtemp())
  source = work / "a.csv"
  source.write_bytes(("門市,金額\n" + "北區門市,100\n" * 20_000).encode("cp950"))
  sources.PARALLEL_MIN = 1 << 16  # 把平行轉碼的門檻調小
  print("之前：平行轉碼用幾個工作程序 =", sources._workers())


  def io_error(*args, **kwargs):  # 模擬平行轉碼中途的 I/O 錯誤（跟開不開得了工作程序無關）
      raise OSError(28, "No space left on device")


  sources._transcode_parallel = io_error
  sources._transcode(source, "cp950", work / "out.txt", None)
  print(
      "之後：_pool_failed =", sources._pool_failed, "，平行轉碼用幾個工作程序 =", sources._workers()
  )
  ```
  實際輸出：
  ```text
  之前：平行轉碼用幾個工作程序 = 8
  之後：_pool_failed = True ，平行轉碼用幾個工作程序 = 0
  ```
- 確認程度：邏輯已用模擬重現；真實的磁碟滿或檔案被鎖住沒有實測。
- 建議修法：只有「開工作程序」那一段（`_get_pool()`、`submit`）的失敗才設 `_pool_failed`；讀寫檔的 `OSError` 照原樣往外丟（單核心重試一次也可以，但不要記成永久失敗）。
- 狀態：已修（2e80b79）

### F-041［低］［bug］正式處理才出現的步驟錯誤，訊息用內部的步驟 id 與類型代碼，也沒有「前往這一步」

- 位置：src/tablade/session.py:1826-1835（`export`：正式執行的 `RunError` 沒有像 `_trial` 那樣轉成畫面上的說法）；src/tablade/engine/run.py:157-168（`RunError` 的訊息）；src/tablade/api.py:441-447
- 現象：
  - 試跑出錯時，訊息是「階段「X」第 3 步「結合」：…」，並可以「前往這一步」（`trial_error`）。
  - 試跑只看每個檔的前 2,000 行。只有全部資料才會出現的錯誤（例如另一張表後面才出現重複的鍵），在正式處理時才丟出 `RunError`：畫面上的訊息是「步驟 s1（join）失敗：…」，用的是 Recipe 裡的步驟 id 與類型代碼（畫面上看不到這兩樣），也沒有「前往這一步」。英文畫面是 `Step s1 (join) failed: …`。
- 證據：
  ```python
  import json
  import tempfile
  from pathlib import Path

  from tablade.api import Api

  work = Path(tempfile.mkdtemp())
  (work / "data").mkdir()
  # 另一張表 3,000 列，重複的鍵在第 2,500 列：試跑只看每個檔的前 2,000 行，看不到
  keys = [f"P{i:04d}" for i in range(1, 3001)]
  keys[2499] = "P0001"
  (work / "data" / "品項.csv").write_text(
      "品號,類別\n" + "".join(f"{k},文具\n" for k in keys), encoding="utf-8"
  )
  (work / "data" / "明細.csv").write_text("品號,數量\nP0001,1\n", encoding="utf-8")


  def stage(name, file, *steps):
      source = {"files": [f"data/{file}"], "reader": "csv", "encoding": "auto"}
      return {"name": name, "input": source, "steps": list(steps), "output_format": "parquet"}


  join = {
      "id": "s1",
      "type": "join",
      "right": "品項對照",
      "on": [{"left": "品號", "right": "品號"}],
      "columns": ["類別"],
  }
  path = work / "late.recipe.json"
  path.write_text(
      json.dumps(
          {
              "tablade_recipe": "1.0",
              "name": "late",
              "stages": [stage("品項對照", "品項.csv"), stage("明細", "明細.csv", join)],
              "output": {"keep_system_columns": False, "manifest": False},
          },
          ensure_ascii=False,
      ),
      encoding="utf-8",
  )
  api = Api()
  api._session.load_recipe(path)
  result = api.export_parquet()  # 沒有先載入：先試跑，再正式處理
  print({k: v for k, v in result.items() if k != "state"})
  ```
  實際輸出：
  ```text
  {'ok': False, 'error': 'RunError: 步驟 s1（join）失敗：@品項對照 的鍵「品號」重複：「P0001」（data/品項.csv 第 2、2501 行）。請先在階段「品項對照」移除重複或修正資料'}
  ```
  預期：和試跑出錯一樣指出第幾個階段第幾步（步驟名稱用畫面的語言），並可以前往。
- 確認程度：已重現（Python）；進度視窗實際怎麼顯示這段文字沒有在真的視窗確認。
- 建議修法：`export()` 接住正式執行的 `RunError`，用 `_step_place(err.step_id)` 組訊息，回傳和 `trial_error` 同樣的結構（可以共用一個欄位，例如 `step_error`）。
- 狀態：已修（2e80b79）

### F-042［低］［bug］錯誤訊息裡的步驟編號從 0 起算

- 位置：src/tablade/session.py:936（`_check_index`）
- 現象：階段、檔案清單的訊息都是從 1 起算（`index + 1`），只有步驟直接用 `index`：第 10 步不存在時寫「沒有第 9 步」。
- 證據：
  ```python
  from tablade.session import Session, SessionError

  session = Session()
  for call in (
      lambda: session.delete_stage(9),
      lambda: session.remove_file(9),
      lambda: session.move_step(9, 0),
  ):
      try:
          call()
      except SessionError as err:
          print(err)
  ```
  實際輸出：
  ```text
  沒有第 10 個階段
  檔案清單沒有第 10 項
  沒有第 9 步
  ```
- 確認程度：已重現。正常操作畫面不會送出不存在的步驟，只有畫面與 Python 不同步時才看得到。
- 建議修法：改成 `index + 1`（中英文都是）。
- 狀態：已修（2e80b79）

### F-043［低］［bug］「瀏覽檔案」預設的檔案類型沒有 `.xlsb`、`.ods`

- 位置：src/tablade/api.py:34（`DATA_PATTERNS`）；對照 src/tablade/ingest/excel.py:22（`EXCEL_SUFFIXES`）、src/tablade/api.py:41
- 現象：Excel 讀取支援 `.xlsx`、`.xlsm`、`.xlsb`、`.xls`、`.ods`，對話框第二個類型「Excel」也列了這五種，但預設的「資料檔」只有 `*.xlsx;*.xlsm;*.xls`：`.xlsb`、`.ods` 的檔在預設的清單裡看不到，要自己切換類型。
- 證據：
  ```bash
  grep -n "DATA_PATTERNS = \|Excel (" src/tablade/api.py; grep -n "EXCEL_SUFFIXES = " src/tablade/ingest/excel.py
  ```
  ```text
  34:DATA_PATTERNS = "*.csv;*.txt;*.tsv;*.prn;*.dat;*.xlsx;*.xlsm;*.xls;*.parquet"
  41:        "Excel (*.xlsx;*.xlsm;*.xlsb;*.xls;*.ods)",
  22:EXCEL_SUFFIXES = (".xlsx", ".xlsm", ".xlsb", ".xls", ".ods")
  ```
- 確認程度：只靠讀程式推論（檔案對話框要真的視窗才看得到）。
- 建議修法：`DATA_PATTERNS` 由 `EXCEL_SUFFIXES` 組出來，兩邊不會再不一致。
- 狀態：已修（2e80b79）

### F-044［低］［bug］拆欄（split_column）的新欄名和來源欄同名時，執行失敗

- 位置：src/tablade/steps/split.py:286-291；欄位效果（驗證器）認為可以
- 現象：不保留來源欄、新欄名裡有一個和來源欄同名（例如把「備註」拆成「備註」「付款」）時，欄位效果推算得出結果、驗證通過，執行時卻是 Polars 的 `DuplicateError`。「依分隔符號拆欄」「依固定位置拆欄」「用正規表示式擷取」同樣的寫法都可以執行。
- 證據：
  ```python
  import polars as pl

  from tablade.engine.run import RunError, apply_step
  from tablade.recipe.validator import apply_effect, resolve_effect
  from tablade.steps import get

  frame = pl.DataFrame({"__source_file": ["a.csv"], "__line_no": [2], "備註": ["門市|現金"]})
  params = {
      "id": "s1",
      "type": "split_column",
      "column": "備註",
      "delimiter": "|",
      "names": ["備註", "付款"],
  }
  step = get("split_column").model_validate(params)
  print(
      "欄位效果推算的結果：",
      apply_effect(["備註"], resolve_effect(step.effect, step.model_dump(mode="json"))),
  )
  try:
      apply_step(step, frame)
  except RunError as err:
      print("apply →", str(err)[:120])
  ```
  實際輸出：
  ```text
  欄位效果推算的結果： ['備註', '付款']
  apply → 步驟 s1（split_column）失敗：DuplicateError: could not create a new DataFrame: column with name '備註' has more than one occurren
  ```
- 確認程度：已重現。
- 建議修法：展開前先拿掉來源欄（或改成一欄一個 `struct.field()`）；tests/test_steps.py 補這個案例。
- 狀態：已修（413c289）

### F-045［低］［bug］條件的正規表示式不在驗證時檢查；條件的欄不存在時是 Polars 的原文

- 位置：src/tablade/steps/filter.py:80-93（`RegexCondition`）、src/tablade/steps/filter.py:219-222；同樣的條件用在 src/tablade/steps/report.py:141-145、src/tablade/steps/fill_down.py:96-102
- 現象：
  - 取代文字、用正規表示式擷取、文字轉日期都在驗證時就編譯正規表示式，篩選（以及分類、依條件複製欄位）的條件沒有：寫錯的正規表示式通過驗證，執行時才失敗。
  - 條件引用的欄不存在時，其他步驟回報「欄位不存在：…」，這三個步驟是 Polars 的 `ColumnNotFoundError` 原文，後面接一大段執行計畫（中文畫面也是英文）。
- 證據：
  ```python
  import polars as pl
  from pydantic import ValidationError

  from tablade.engine.run import RunError, apply_step
  from tablade.steps import get

  frame = pl.DataFrame({"__source_file": ["a.csv"], "__line_no": [2], "品名": ["筆"]})
  cases = {
      "篩選：正規表示式少了右括號": (
          "filter",
          {"conditions": [{"column": "品名", "op": "regex", "pattern": "("}]},
      ),
      "取代文字：同樣的正規表示式": (
          "replace_values",
          {"columns": ["品名"], "match": "regex", "replacements": [{"find": "(", "replace": ""}]},
      ),
      "篩選：條件的欄不存在": (
          "filter",
          {"conditions": [{"column": "zz", "op": "equals", "value": "x"}]},
      ),
      "向下填滿：欄不存在": ("fill_down", {"columns": ["zz"]}),
  }
  for title, (name, params) in cases.items():
      try:
          step = get(name).model_validate({"id": "s1", "type": name, **params})
      except ValidationError as err:
          print(f"{title} → 驗證時就擋下：{err.errors()[0]['msg'][:50]}")
          continue
      try:
          apply_step(step, frame)
      except RunError as err:
          print(f"{title} → 執行時才失敗：{err.message.splitlines()[0][:95]}")
  ```
  實際輸出：
  ```text
  篩選：正規表示式少了右括號 → 執行時才失敗：ComputeError: regex error: regex parse error:
  取代文字：同樣的正規表示式 → 驗證時就擋下：Value error, 正規表示式有誤：regex error: regex parse erro
  篩選：條件的欄不存在 → 執行時才失敗：ColumnNotFoundError: unable to find column "zz"; valid columns: ["__source_file", "__line_no",
  向下填滿：欄不存在 → 執行時才失敗：欄位不存在：zz
  ```
- 確認程度：已重現。
- 建議修法：`RegexCondition` 加上和取代文字共用的檢查；三個步驟的 `apply()` 開頭先檢查條件的欄。
- 狀態：已修（413c289）

### F-046［低］［bug］步驟的幾個小問題

- 位置與現象：
  - **向下填滿**（src/tablade/steps/fill_down.py:163-168）：同一欄列兩次時是 Polars 的 `ComputeError`，其他步驟會先去掉重複。
  - **清理文字**（src/tablade/steps/text.py:32、src/tablade/steps/text.py:150）：「看不見的字元」包含換行，格子裡的換行被直接刪掉、前後的字黏在一起；說明只寫「控制字元（Tab 保留）」。
  - **取代文字**的正規表示式模式：換成 `$1x` 時，`1x` 被當成群組名稱，結果是空字串；選項的說明只寫「可以用 $1」（要寫成 `${1}x`）。
  - **群組彙總**（src/tablade/steps/aggregate.py:122）：「不同值個數」把空值算成一個值，「有值的筆數」不算空值，兩個數字對不起來。
  - **文字轉日期**（src/tablade/steps/convert.py:344-354）：西元年可以轉出 `0000-01-01`。Python 的日期沒有 0 年，預覽表格取這一列時丟出 `ValueError`（src/tablade/session.py:1548、src/tablade/session.py:1560 用 `iter_rows()`）。
  - **新增步驟的建議值**：移除重複、取消樞紐、群組彙總、改欄名在系統欄位上新增時，建議的參數就是那個系統欄位，一套用就是「欄位不存在」（src/tablade/steps/remove_rows.py:190、src/tablade/steps/aggregate.py:214、src/tablade/steps/columns.py:222）。
  - **結合**（src/tablade/steps/join.py:230-238）：另一張表的鍵重複時，錯誤訊息把不同檔的行號都寫在第一個檔底下。
- 證據：
  ```python
  import polars as pl

  from tablade.engine.run import RunError, apply_step
  from tablade.steps import get


  def frame(data, files=None):
      n = len(next(iter(data.values())))
      files = files or ["a.csv"] * n
      lines = [files[:i].count(f) + 2 for i, f in enumerate(files)]
      return pl.DataFrame({"__source_file": files, "__line_no": lines, **data})


  def run(title, name, df, tables=None, show=None, **params):
      step = get(name).model_validate({"id": "s1", "type": name, **params})
      try:
          out, _, _ = apply_step(step, df, tables=tables)
          print(f"{title} → {show(out) if show else out.rows()}")
      except (RunError, Exception) as err:
          print(f"{title} → {type(err).__name__}: {str(err).splitlines()[0][:110]}")


  text = frame({"門市": ["北", None], "備註": ["a\nb", "A-1"]})
  run("向下填滿：同一欄列兩次", "fill_down", text, columns=["門市", "門市"])
  run(
      "清理文字：格子裡的換行",
      "trim_clean",
      text,
      columns=["備註"],
      show=lambda o: o["備註"].to_list(),
  )
  run(
      "取代文字：換成 $1x",
      "replace_values",
      text,
      columns=["備註"],
      match="regex",
      replacements=[{"find": r"^(\w)-(\d)$", "replace": "$1x"}],
      show=lambda o: o["備註"].to_list(),
  )
  run(
      "群組彙總：有值的筆數、不同值個數（值是 x 與空值）",
      "group_by",
      frame({"門市": ["北", "北"], "類別": ["x", None]}),
      by=["門市"],
      aggregations=[
          {"column": "類別", "op": "count", "name": "有值的筆數"},
          {"column": "類別", "op": "n_unique", "name": "不同值個數"},
      ],
      count_as="",
      show=lambda o: o.select("有值的筆數", "不同值個數").to_dicts(),
  )
  dates = frame({"日期": ["0000-01-01"]})
  step = get("parse_date").model_validate(
      {"id": "s1", "type": "parse_date", "columns": ["日期"], "pattern": "dash", "calendar": "ad"}
  )
  out, total, _ = apply_step(step, dates)
  print("文字轉日期：0000-01-01（西元年）→ 型別", out["日期"].dtype, "，警告數", total)
  try:
      list(out.iter_rows())
  except ValueError as err:
      print("  取出這一列（預覽表格就是這樣取）→ ValueError:", err)
  print(
      "移除重複：點在系統欄位上新增步驟 →", get("dedupe").suggest(text, {"column": "__source_file"})
  )
  right = frame(
      {"代號": ["P1", "P2", "P3", "P1"], "類別": ["甲", "乙", "丙", "丁"]},
      ["lk/a.csv", "lk/a.csv", "lk/b.csv", "lk/b.csv"],
  )
  run(
      "結合：另一張表的 P1 在 lk/a.csv 第 2 行與 lk/b.csv 第 3 行",
      "join",
      frame({"品號": ["P1"]}),
      tables={"對照": right},
      right="對照",
      on=[{"left": "品號", "right": "代號"}],
      columns=["類別"],
  )
  ```
  實際輸出：
  ```text
  向下填滿：同一欄列兩次 → RunError: 步驟 s1（fill_down）失敗：ComputeError: the name '門市' passed to `LazyFrame.with_columns` is duplicate
  清理文字：格子裡的換行 → ['ab', 'A-1']
  取代文字：換成 $1x → ['a\nb', '']
  群組彙總：有值的筆數、不同值個數（值是 x 與空值） → [{'有值的筆數': 1, '不同值個數': 2}]
  文字轉日期：0000-01-01（西元年）→ 型別 Date ，警告數 0
    取出這一列（預覽表格就是這樣取）→ ValueError: year 0 is out of range
  移除重複：點在系統欄位上新增步驟 → {'columns': ['__source_file']}
  結合：另一張表的 P1 在 lk/a.csv 第 2 行與 lk/b.csv 第 3 行 → RunError: 步驟 s1（join）失敗：@對照 的鍵「代號」重複：「P1」（lk/a.csv 第 2、3 行）。請先在階段「對照」移除重複或修正資料
  ```
- 確認程度：已重現。另一位審查者另外回報、我沒有重現的：結合在串流時每個來源檔各重算一次另一張表的重複檢查（200 個來源檔時多花約 3 秒）；數值條件把 `NaN`、`inf` 當成數字。
- 建議修法：逐項修正，各補一個 tests/test_steps.py 的案例。日期的部分，年份是 0 時當成轉換失敗（產生警告）。
- 狀態：已修（413c289；決定：清理文字把格子裡的換行換成一個空白，不直接刪掉）

### F-047［低］［bug］驗證器漏掉的幾種輸入：版本號、輸出檔名、舊寫法的提示

- 位置：src/tablade/recipe/validator.py:59、src/tablade/recipe/validator.py:684-694（版本號）；src/tablade/recipe/validator.py:813 起的 `output_issues`（輸出檔名）；src/tablade/recipe/validator.py:740 起（舊寫法的提示）
- 現象：
  - 版本號 `"1.00"`、`"01.0"`、全形的 `"１.０"` 都通過（SKILL.md 寫「一定要是 "1.0"」）。
  - 輸出檔名 `CON`（Windows 的保留名稱）、含 `\x7f`、300 個字都通過。後兩種在執行時才失敗；`CON` 在 Windows 上的後果沒有實測。
  - 舊寫法（最外層的 `inputs`／`steps`）的提示教人怎麼改成 `stages`，但沒有提到每個階段必填的 `output_format`，照著改完還是不通過。
- 證據：
  ```python
  from tablade.recipe.spec import build_spec
  from tablade.recipe.validator import validate


  def recipe(version="1.0", **stage):
      one = {
          "name": "主要",
          "input": {"files": ["a.csv"], "reader": "csv"},
          "steps": [],
          "output_format": "parquet",
      }
      return {"tablade_recipe": version, "name": "x", "stages": [{**one, **stage}], "output": {}}


  def errors(data):
      return [
          i["error"] for i in validate(data, build_spec(), {}) if i["severity"] == "error"
      ] or "通過"


  for version in ("1.00", "01.0", "１.０"):
      print(f"版本號 {version!r} →", errors(recipe(version)))
  for name in ("CON", "a\x7fb", "長" * 300):
      print(f"輸出檔名 {name[:4]!r}（{len(name)} 個字）→", errors(recipe(output_file=name)))
  legacy = validate(
      {"tablade_recipe": "1.0", "inputs": [{"files": ["a.csv"]}], "steps": []}, build_spec(), {}
  )
  print("舊寫法的提示：", legacy[0]["hint"])
  ```
  實際輸出：
  ```text
  版本號 '1.00' → 通過
  版本號 '01.0' → 通過
  版本號 '１.０' → 通過
  輸出檔名 'CON'（3 個字）→ 通過
  輸出檔名 'a\x7fb'（3 個字）→ 通過
  輸出檔名 '長長長長'（300 個字）→ 通過
  舊寫法的提示： stages 的每一項是一個階段：{"name": "預設階段", "input": {原本 inputs[0]，去掉 id}, "steps": [原本的 steps]}；output 留在最外層
  ```
- 確認程度：已重現（都是「通過驗證」這一段；執行時的後果只有另一位審查者試過 300 個字的檔名：`OSError: File name too long`）。
- 建議修法：版本號用 `re.fullmatch(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", …)`；輸出檔名加上 Windows 保留名稱、`\x7f`、長度的檢查；提示補上 `"output_format": "parquet"`。
- 狀態：已修（2e80b79）

### F-048［低］［bug］`tablade profile`：整個檔讀進記憶體、遮罩有漏、`--lines` 可以是負數

- 位置：src/tablade/profile.py:36-42（`path.read_bytes()` 後整份解碼、切行）、src/tablade/profile.py:21-32（`mask_text`）；src/tablade/cli.py:60
- 現象：
  - DESIGN.md §17.1 建議「大檔或 Big5 檔先用 `tablade profile` 產生摘要」，但它把整個檔讀進記憶體再整份解碼：記憶體約是檔案大小的 6 倍，幾 GB 的檔會用掉十幾 GB。
  - `--mask` 只處理「數字」與「字母」：`Ⅳ`、`㈱`、`½`、`㊣`、`㎡` 這類字原樣留著，檔名（`## 檔名`）也不遮。遮罩的目的是不讓內容外流，漏掉的字與檔名可能正是認得出來源的部分。
  - `--lines -1` 印出「前 -1 行」，然後把除了最後一行以外的整個檔印出來。
- 證據：
  ```python
  import contextlib
  import io
  import tempfile
  import tracemalloc
  from pathlib import Path

  from tablade.cli import main
  from tablade.profile import mask_text, profile_file

  work = Path(tempfile.mkdtemp())
  print(
      "遮罩：",
      repr("２０２６／０１／０６,Ⅳ號店㈱,½ ㊣ ㎡ x²"),
      "→",
      repr(mask_text("２０２６／０１／０６,Ⅳ號店㈱,½ ㊣ ㎡ x²")),
  )

  small = work / "明細_北區門市.csv"
  small.write_text("".join(f"第{i}行,{i}\n" for i in range(1, 58)), encoding="utf-8")
  print("遮罩後的第一行（檔名）：", profile_file(small, mask=True).splitlines()[0])
  out = io.StringIO()
  with contextlib.redirect_stdout(out):
      main(["profile", str(small), "--lines", "-1"])
  lines = out.getvalue().splitlines()
  start = next(i for i, line in enumerate(lines) if line.startswith("前 -1 行"))
  fence = [i for i, line in enumerate(lines) if line.startswith("```") and i > start][:2]
  print("--lines -1：", lines[start], f"；下面列了 {fence[1] - fence[0] - 1} 行（檔案共 57 行）")

  big = work / "big.csv"
  with big.open("wb") as f:
      f.write(("北區門市,保溫瓶,１２３\n" * 1_000_000).encode("cp950"))
  tracemalloc.start()
  profile_file(big)
  peak = tracemalloc.get_traced_memory()[1]
  print(f"{big.stat().st_size / 1e6:.0f} MB 的 cp950 檔：Python 配置的記憶體峰值 {peak / 1e6:.0f} MB")
  ```
  實際輸出：
  ```text
  遮罩： '２０２６／０１／０６,Ⅳ號店㈱,½ ㊣ ㎡ x²' → '9999／99／99,Ⅳ〇〇㈱,½ ㊣ ㎡ a9'
  遮罩後的第一行（檔名）： ## 明細_北區門市.csv
  --lines -1： 前 -1 行（行號：內容）： ；下面列了 56 行（檔案共 57 行）
  23 MB 的 cp950 檔：Python 配置的記憶體峰值 139 MB
  ```
- 確認程度：已重現。
- 建議修法：只讀開頭與結尾各一段（編碼偵測沿用 `ingest` 的分塊做法）；遮罩改成「不是空白、不是 ASCII 標點的都遮」，`--mask` 時檔名也遮；`--lines` 不能小於 0。
- 狀態：已修（2e80b79）

### F-049［低］［bug］`check_code_policy.py` 擋不住幾種常見的寫法

- 位置：scripts/check_code_policy.py:42-54、scripts/check_code_policy.py:66-77
- 現象：這支腳本是「程式碼層級杜絕對外連線」的把關（README「安全性」一節）。它只看 `import` 的模組名稱、直接呼叫的 `eval`／`exec`，以及 `importlib.import_module(...)` 這一種寫法。下面這些都會通過：
  - `from importlib import import_module` 之後呼叫、`builtins.eval(...)`、把 `eval` 指定給別的名稱。
  - `subprocess` 呼叫外部程式連線、`pl.read_csv("https://…")`（Polars 自己會連線）。
  - 例外清單用 `path.endswith("tablade/update.py")` 判斷，任何資料夾底下同名的檔都被當成例外。
- 目前的 `src/` 沒有這些寫法（見「檢查過」一節）；這一項說的是把關的工具有洞，不是現在有違規。
- 證據：
  ```python
  import sys

  sys.path.insert(0, "scripts")
  from check_code_policy import check_source  # noqa: E402

  samples = {
      "import socket（對照）": ("src/tablade/x.py", "import socket\n"),
      "from importlib import import_module": (
          "src/tablade/x.py",
          "from importlib import import_module\nimport_module('socket')\n",
      ),
      "builtins.eval": ("src/tablade/x.py", "import builtins\nbuiltins.eval('1+1')\n"),
      "把 eval 指定給別的名稱": ("src/tablade/x.py", "e = eval\ne('1+1')\n"),
      "subprocess 呼叫 curl": (
          "src/tablade/x.py",
          "import subprocess\nsubprocess.run(['curl', 'https://example.com'])\n",
      ),
      "Polars 直接讀網址": (
          "src/tablade/x.py",
          "import polars as pl\npl.read_csv('https://example.com/a.csv')\n",
      ),
      "別的資料夾裡也叫 tablade/update.py": (
          "src/tablade/vendor/tablade/update.py",
          "import urllib.request\n",
      ),
  }
  for title, (path, source) in samples.items():
      found = check_source(path, source)
      print(f"{title} → {'擋下' if found else '通過'}")
  ```
  實際輸出：
  ```text
  import socket（對照） → 擋下
  from importlib import import_module → 通過
  builtins.eval → 通過
  把 eval 指定給別的名稱 → 通過
  subprocess 呼叫 curl → 通過
  Polars 直接讀網址 → 通過
  別的資料夾裡也叫 tablade/update.py → 通過
  ```
- 確認程度：已重現。
- 建議修法：例外改成比對完整的相對路徑；`eval`／`exec`／`import_module` 改看名稱（含別名與屬性呼叫）；`subprocess`、`os.system` 用白名單（目前只有 src/tablade/desktop.py）；檢查 `pl.read_*`／`pl.scan_*` 的字串參數有沒有 `://`。README「已知的殘餘風險」補上這一項。
- 狀態：已修（2e80b79）

### F-050［低］［bug］schema lock 裡留著選項的說明文字，改說明會被要求升版本

- 位置：src/tablade/recipe/spec.py:16（`_HUMAN_KEYS` 沒有 `ui_option_hints`）；src/tablade/recipe/recipe-schema.lock.json
- 現象：lock 只該記結構，標題、說明、選項文字都會先拿掉。但 `ui_option_hints`（選項下面的說明，例如日期樣式的正規表示式、結合方式的說明）沒有列在要拿掉的清單裡：只改這些說明文字，lock 的測試就失敗、被要求升 `RECIPE_VERSION`，和 CLAUDE.md「只改說明文字不用升」不符。
- 證據：
  ```bash
  grep -n "_HUMAN_KEYS = " src/tablade/recipe/spec.py; grep -c "ui_option_hints" src/tablade/recipe/recipe-schema.lock.json
  ```
  ```text
  16:_HUMAN_KEYS = {"title", "description", "ui_labels", "label", "category", "examples", "ai_notes"}
  2
  ```
- 確認程度：只靠讀程式推論（沒有實際改說明文字跑測試）。
- 建議修法：`_HUMAN_KEYS` 加上 `ui_option_hints`，重新產生 lock。
- 狀態：已修（2e80b79）

### F-051［低］［bug］預覽區與進度視窗的幾個小問題

- 位置與現象（都是另一位審查者回報；`state` 的內容他用 Python 確認過，畫面的部分我只讀了程式）：
  - **選「輸出資料」而最後一步有錯**（frontend/src/App.tsx:645-669）：寫「這一步有錯」，沒有「前往第 N 步」的按鈕。選到出錯步驟後面被停用的步驟時也沒有按鈕（src/tablade/session.py:1378：`blocked_by` 只給 `pending` 的步驟）。
  - **載入後有步驟出錯**（frontend/src/App.tsx:383-392、frontend/src/components/RunDialog.tsx:105-107）：進度視窗留著，寫「處理失敗」，但沒有說是哪一步、也沒有「前往」；狀態列同時寫「已載入資料」。
  - **結果是 0 列時**（frontend/src/components/PreviewGrid.tsx:154-162）：標題寫「0 列 × 3 欄」，表格卻只有「行號」一欄（欄名只從 `rows()` 的回應來，0 列時不會呼叫）。
  - **讀前面階段、但還沒選階段**（frontend/src/App.tsx:604-621）：資料區寫「按『載入資料及預覽』…」，按鈕卻是灰的；真正的原因在 `input.error`（「要選至少一個前面的階段」），資料區沒有顯示。
  - **忙碌狀態是布林值**（frontend/src/App.tsx:169-194）：兩個呼叫重疊時，先回來的那個把 `busy` 設回 false，進度視窗就顯示「完成」、可以關，另一個其實還在跑。
- 確認程度：只靠讀程式推論。
- 建議修法：逐項修正；`busy` 改成計數。
- 狀態：已修（413c289）

### F-052［低］［bug］表單與面板的零碎問題

- 位置與現象（同上，另一位審查者回報，我只讀了程式）：
  - **輸出檔名帶另一種格式的副檔名**（frontend/src/components/SourcePanel.tsx:326-336）：格式是 Parquet 時填 `a.csv`，Python 存成 `a.csv` 並報副檔名不合；但輸入框兩種副檔名都會去掉，顯示成 `a`，再打一次 `a` 因為和顯示的一樣而不會送出。
  - **依位置選欄的文字輸入**（frontend/src/components/form/fields.tsx:334-348）：每打一個字就重新解析，`2,` 馬上變回 `2`，打不了逗號（只在沒有欄位清單時出現）。
  - **多選欄位**（frontend/src/components/form/fields.tsx:197-198）：勾任何一欄時，已選但不存在的欄名整批被丟掉，順序也被重排成資料表的順序（取消樞紐、群組依據的順序是有意義的）。
  - **React 的 key**（frontend/src/components/StepsPanel.tsx:230）：`step.id + step.index` 是字串相加，`s1`＋12 和 `s11`＋2 都是 `s112`。
  - **已載入的檔案大小沒有 GB**（frontend/src/components/SourcePanel.tsx:32-36）：5 GB 顯示成「5120.0 MB」。
  - **切換語言失敗時**（frontend/src/App.tsx:364-371）：前端已經先換了語言，會和 Python 的語言不一致。
  - **型別與 Python 的小差異**（frontend/src/bridge.ts）：`StepState.id`／`type`／`label` 實際可能是 null；`SourceFile` 少了 `stage`；`Progress.unit` 的 `'files'` 沒有人送。
- 確認程度：只靠讀程式推論。
- 建議修法：逐項修正。
- 狀態：已修（413c289）

### F-053［低］［bug］命令列與訊息的小瑕疵

- 位置與現象：
  - src/tablade/cli.py:32：`run` 的說明還是「執行 Recipe，輸出 Parquet」，現在也輸出 CSV。
  - src/tablade/engine/check.py:59：讀取設定的 `encoding` 寫錯時，Python 的 `LookupError` 原樣顯示（`unknown encoding: nope`）。
  - 輸入檔的「行數」：CSV 顯示的是資料列數（7 行的檔顯示 6 行），和文字行、DESIGN.md §9 的 `rows` 說法混用。
  - 處理紀錄的 `output.columns` 不含系統欄位，即使輸出檔裡有 `__source_file`、`__line_no`；DESIGN.md §9 沒有說明。
  - Recipe 的路徑不存在或是資料夾時，`tablade validate` 是 traceback（`FileNotFoundError`／`IsADirectoryError`）。
- 確認程度：只靠讀程式推論（另一位審查者實際執行過；我只核對了程式的位置）。
- 建議修法：逐項修正。
- 狀態：已修（2e80b79）

### F-054［低］［漏翻］pydantic 的幾種錯誤沒有翻譯，中文畫面顯示英文原文

- 位置：src/tablade/recipe/schema.py:200-236（`_message`：沒列到的錯誤類型照 pydantic 原樣）
- 現象：
  - `_message` 沒有列到的錯誤類型，照 pydantic 的英文原樣顯示（英文畫面則是 pydantic 自己的說法，和其他訊息的語氣不一樣）：`less_than_equal`、`float_type`、`union_tag_not_found`、`union_tag_invalid`、`model_type`、`model_attributes_type`。
  - 表單上最容易遇到的是前兩種：小數位數填超過上限、數值條件的值是文字（見「條件換運算子時沿用舊的值」那一項）。
  - `literal_error` 有中文，但選項之間夾著英文的 `or`。
- 證據（預設語言是中文）：
  ```python
  from pydantic import ValidationError

  from tablade.recipe.schema import pydantic_issues
  from tablade.steps import get

  cases = {
      "篩選的條件沒有 op": ("filter", {"conditions": [{"column": "金額", "value": "x"}]}),
      "篩選的 op 是空字串": ("filter", {"conditions": [{"column": "金額", "op": "", "value": "x"}]}),
      "篩選的條件不是物件": ("filter", {"conditions": ["金額"]}),
      "篩選「大於」的值是文字": (
          "filter",
          {"conditions": [{"column": "金額", "op": "gt", "value": "abc"}]},
      ),
      "文字轉數字的小數位數填 10": (
          "parse_number",
          {"columns": ["金額"], "kind": "decimal", "scale": 10},
      ),
      "文字轉數字的 kind 填錯": ("parse_number", {"columns": ["金額"], "kind": "money"}),
  }
  for title, (name, params) in cases.items():
      try:
          get(name).model_validate({"id": "s1", "type": name, **params})
      except ValidationError as err:
          for issue in pydantic_issues(err):  # 預設語言是中文
              print(f"{title}：{issue['path']} → {issue['error'][:95]}")
  ```
  實際輸出：
  ```text
  篩選的條件沒有 op：conditions[0] → Unable to extract tag using discriminator 'op'
  篩選的 op 是空字串：conditions[0] → Input tag '' found using 'op' does not match any of the expected tags: 'equals', 'not_equals',
  篩選的條件不是物件：conditions[0] → Input should be a valid dictionary or object to extract fields from
  篩選「大於」的值是文字：conditions[0].value → Input should be a valid number
  文字轉數字的小數位數填 10：scale → Input should be less than or equal to 9
  文字轉數字的 kind 填錯：kind → 不支援的值（可用：'decimal' or 'integer'）
  ```
- 確認程度：已重現（Python）。哪幾種會從表單送出，沒有在畫面上逐一確認；手寫或 AI 寫的 Recipe 在畫面上編輯時都會遇到。
- 建議修法：`_message` 補上這 6 種（例如「不能大於 9」「應該是數字」「請選條件的類型」「不支援的類型（可用：…）」「應該是物件」）；`literal_error` 的選項自己用「、」重組。tests/test_i18n_recipe.py 補中英文各一。
- 狀態：已修（2e80b79）

### F-055［低］［漏翻］英文畫面的狀態列還是用全形的「・」

- 位置：frontend/src/App.tsx:481
- 現象：狀態列右邊的 `Tablade {版本}・Recipe {版本}` 直接寫在 JSX 裡，沒有經過 `tr()`。英文畫面其他地方都用半形的「 · 」（例如 frontend/src/App.tsx:627），只有這裡留著全形的「・」。`scripts/check_i18n.py` 不把「・」當成中文，所以檢查不到。
- 證據：開發畫面（`npm --prefix frontend run dev`）切成 English 後，在瀏覽器的主控台執行 `document.body.innerText`，最後幾行是：
  ```text
  Switched to English.
  385 MB
  300 MB
  Tablade 0.1.0・Recipe 1.0
  ```
- 確認程度：已重現（開發畫面、假資料）。
- 建議修法：改成 `tr('・', ' · ')`；`check_i18n.py` 的中文字元範圍加上「・」（U+30FB）。
- 狀態：已修（413c289）

### F-056［低］［漏翻］英文用語不一致

- 位置與現象：同一件事在不同地方的英文說法不同（對照表見 docs/i18n-plan.md）。
  - **Recipe 的大小寫**：對照表與 Python 都是大寫的 `Recipe`；前端有 11 處是小寫（例如 frontend/src/App.tsx:239 的 `Create or open a recipe`、frontend/src/App.tsx:254 的 `New recipe`、frontend/src/App.tsx:265 的 `Open recipe`），同一個畫面上會同時看到 `Open recipe` 與 `No Recipe open`（src/tablade/session.py:1663）。
  - **欄位不存在**：`Column "x" does not exist`（src/tablade/session.py:829、src/tablade/steps/split.py:277、src/tablade/steps/report.py:258）與 `Column not found: x`（src/tablade/steps/fill_down.py:98、src/tablade/steps/remove_rows.py:198 等）兩種。
  - **按鈕名稱的引號**：前端大多用彎引號 `“Load & preview”`，frontend/src/components/SourcePanel.tsx 與 frontend/src/components/form/fields.tsx:274 用直引號。
  - **讀取方式**：`Read as`（frontend/src/components/SourcePanel.tsx:112、src/tablade/session.py:1685）、`read mode`（frontend/src/App.tsx:616）、`read settings`（frontend/src/components/SourcePanel.tsx:144）。
  - **正規表示式**：`Regular expression`（src/tablade/steps/report.py:183、src/tablade/steps/convert.py:268）、`Regex`（src/tablade/steps/filter.py:81）、`Pattern`（src/tablade/steps/filter.py:88）。
  - **另一張表**：對照表是「另一張表／Other table」；src/tablade/recipe/validator.py:310 的中文寫「右表」。
  - **同一個錯誤兩層的說法不同**：驗證器 `Missing required field`、pydantic 那一層 `Required`；`Pick at least one column to remove`（src/tablade/recipe/validator.py:341）與 `Choose at least one column to remove`（src/tablade/steps/columns.py:98）。
  - **型別名稱兩套**：`Integer`、`Decimal (2 places)`（src/tablade/dtypes.py）與 `number`、`date`、`text`（src/tablade/steps/aggregate.py:69-74）。
  - **警告指到的位置**：英文用半形的 `@階段/檔名`（src/tablade/engine/check.py:116），資料裡的來源名稱是全形的 `@階段／檔名`。
- 證據：
  ```bash
  grep -c "', '[^']*\brecipe\b" frontend/src/App.tsx
  grep -rhno 'Column "[^"]*" does not exist\|Column not found' src/tablade | awk -F: '{print $2}' | sed 's/"{[^}]*}"/"…"/' | sort | uniq -c
  ```
  ```text
  9
     6 Column "…" does not exist
     1 Column not found
  ```
  （第二個指令只數得到字面相同的；`Column not found: …` 多數寫在共用的函式裡，位置見上面。）
- 確認程度：已重現（讀程式逐處確認；清單由三位審查者各自整理後合併）。
- 建議修法：照對照表統一；`Column not found` 與 `Recipe` 的大小寫可以加進 `scripts/check_i18n.py` 當成固定用語檢查。
- 狀態：已修（413c289；決定：「欄位「x」不存在」對 Column "x" does not exist、「欄位不存在：x」對 Column not found: x，兩種句型各自一致；「整數」「數字」是不同的中文，英文的 Integer、number 維持）

### F-057［低］［漏翻］英文的單複數與文法

- 位置與現象：
  - frontend/src/App.tsx:342：兩個以上的檔還是 `a.csv, b.csv has more than…`。
  - frontend/src/App.tsx:871、frontend/src/App.tsx:896、frontend/src/App.tsx:900：`The data has ${n} lines` 沒有用 `plural()`，1 行時是 `1 lines`。
  - src/tablade/steps/report.py:435：只有一個位置時是 `at positions 1`。
  - src/tablade/steps/join.py:259：多個鍵時是 `Key "a, b" has duplicates`。
  - src/tablade/steps/files.py:124：整個清單包在一對引號裡，`in the order "a.csv, b.csv"`。
  - src/tablade/steps/fill_down.py:50：選單上的步驟名稱 `Copy column if` 像沒講完。
  - 步驟說明（`doc_en`）的語氣混用：多數是祈使句（Remove…／Keep…），src/tablade/steps/files.py:55、src/tablade/steps/join.py:124、src/tablade/steps/aggregate.py:182 等是第三人稱（Appends…／turns…）。
  - src/tablade/recipe/schema.py:231：中文訊息裡夾英文的 `or`：「不支援的值（可用：'decimal' or 'integer'）」（見「pydantic 的幾種錯誤沒有翻譯」那一項的輸出）。
- 證據：
  ```bash
  sed -n '342p' frontend/src/App.tsx | grep -o "has more than[^\`]*"; grep -c -F 'toLocaleString()} lines' frontend/src/App.tsx
  ```
  ```text
  has more than 1,048,576 rows and will be cut off when opened in Excel
  3
  ```
- 確認程度：已重現（讀程式）。
- 建議修法：逐處修正；有數字的句子一律用 `plural()`。
- 狀態：已修（413c289）

### F-058［低］［漏翻］切換語言後沒有跟著換的文字；快捷鍵提示在 Windows 也寫 ⌘

- 位置與現象：
  - **起始頁的更新檢查**（frontend/src/components/UpdateNotice.tsx:28、src/tablade/update.py:42-65）：連不上或關閉檢查時，說明文字是 Python 在啟動當下用當時的語言產生的，存在前端的狀態裡。之後切成英文會看到 `Current version v0.1.0 · 無法連線到 GitHub，略過檢查更新`。
  - **型別分頁**：見「判斷預覽有沒有變的 key」那一項。
  - **問題清單**：見「`state.issues` 只在開啟 Recipe 時算一次」那一項。
  - **快捷鍵提示**（frontend/src/App.tsx:712、frontend/src/App.tsx:716、frontend/src/App.tsx:723、frontend/src/App.tsx:726）：按鍵處理接受 Ctrl，但提示固定寫 Mac 的 `⌘`、`⇧⌘`，中英文都一樣；Windows 使用者看不懂。
- 證據：
  ```bash
  sed -n '28p' frontend/src/components/UpdateNotice.tsx; grep -c "⌘" frontend/src/App.tsx
  ```
  ```text
          {tr(`目前版本 v${current}・${update.error ?? '已是最新版'}`, `Current version v${current} · ${update.error ?? 'Up to date'}`)}
  5
  ```
- 確認程度：只靠讀程式推論（`update.error` 是 Python 的 `t()` 產生的字串，前端收到後不會再換；沒有在真的視窗切換確認）。
- 建議修法：`check_update` 回傳錯誤的代碼，文字由前端用 `tr()` 產生；快捷鍵提示依 `ready()` 回傳的 `platform` 顯示 Ctrl 或 ⌘。
- 狀態：已修（2e80b79、413c289）

### F-059［低］［文件］DESIGN.md §6 的範例 Recipe 過不了驗證

- 位置：DESIGN.md:136-165（範例）、DESIGN.md:181（「格式、保留系統欄位、處理紀錄的設定在最外層的 `output`」）
- 現象：
  - §6 是 Recipe 格式的正式說明，但範例還是改成「每個階段各自選輸出格式」之前的寫法：兩個階段都沒有必填的 `output_format`，最外層的 `output` 還有已經拿掉的 `format`。
  - 同一節下面的規則已經寫了 `output_format` 必填，和範例互相矛盾；第 181 行也還說格式在最外層的 `output`。
- 證據：
  ```python
  import json
  import re
  from pathlib import Path

  from tablade.recipe.spec import build_spec
  from tablade.recipe.validator import validate

  text = Path("DESIGN.md").read_text(encoding="utf-8")
  block = re.search(r"## 6\. Recipe JSON 格式.*?```json\n(.*?)```", text, re.S).group(1)
  for issue in validate(json.loads(block), build_spec(), {}):
      print(issue["severity"], issue["path"], issue["error"])
  ```
  實際輸出：
  ```text
  error stages[0].output_format 缺少必填欄位
  error stages[1].output_format 缺少必填欄位
  error output.format 不認得的欄位
  ```
- 確認程度：已重現。
- 建議修法：範例的每個階段加上 `"output_format"`（可以照 examples/sales_combined.recipe.json 讓一個階段用 csv），`output` 拿掉 `format`；第 181 行拿掉「格式」。可以加一個測試：把 DESIGN.md §6 的 JSON 區塊抓出來跑 `validate()`，避免再過時。
- 狀態：已修（5181505）

### F-060［低］［文件］處理紀錄的說法和實際不符：欄位名稱是英文，步驟說明也跟著畫面的語言

- 位置：DESIGN.md:640、docs/i18n-plan.md:10、docs/i18n-plan.md:47；程式：src/tablade/export/manifest.py:94-157（`build_manifest`）、src/tablade/engine/run.py:993-1003（`step.describe()`）
- 現象：
  - 文件寫「處理紀錄的欄位名稱是中文，裡面的警告是匯出當時畫面的語言」，i18n-plan 的「不翻」清單也列了處理紀錄。
  - 實際上處理紀錄的欄位名稱全部是英文（`tablade_manifest`、`tool`、`run_at`、`inputs`、`steps`…），沒有中文的欄位名稱。
  - 跟著畫面語言變的不只警告：每一步的 `description`（`describe()` 的結果）也是匯出當時的語言。同一份 Recipe 在中文、英文畫面匯出的處理紀錄，內容不一樣。
- 證據（畫面是英文時匯出範例 Recipe）：
  ```python
  import json
  import shutil
  import tempfile
  from pathlib import Path

  from tablade.fixtures import write_all
  from tablade.session import Session

  work = Path(tempfile.mkdtemp())
  write_all(work / "data")
  shutil.copy("examples/sales_detail.recipe.json", work)
  session = Session()
  session.set_language("en")  # 畫面是英文
  session.load_recipe(work / "sales_detail.recipe.json")
  manifest = json.loads(Path(session.export()["manifest"]).read_text(encoding="utf-8"))
  print("最外層的欄位：", list(manifest))
  print("inputs[0] 的欄位：", list(manifest["inputs"][0]))
  print("steps[0]：", manifest["steps"][0])
  ```
  實際輸出：
  ```text
  最外層的欄位： ['tablade_manifest', 'tool', 'run_at', 'mode', 'recipe', 'inputs', 'steps', 'warnings', 'output']
  inputs[0] 的欄位： ['file', 'bytes', 'sha256', 'encoding', 'rows']
  steps[0]： {'id': 's1', 'type': 'remove_top_rows', 'description': 'Remove the top 2 rows of each file', 'enabled': True, 'rows': 90, 'warning_count': 0}
  ```
- 確認程度：已重現。
- 建議修法：先決定要哪一種，再讓文件和程式一致。
  - 處理紀錄要跟語言無關（日後比對比較方便）：寫處理紀錄時用 `i18n.using("zh")` 產生步驟說明與警告。
  - 或維持現狀：文件改成「欄位名稱固定是英文；步驟說明與警告是匯出當時畫面的語言」，i18n-plan 的「不翻」清單拿掉處理紀錄。
- 狀態：已修（5181505；決定：處理紀錄維持現狀，文件照實際寫）

### F-061［低］［文件］兩份 README 的數字與架構說明過時

- 位置：README.md:89、README.md:91、README.md:127、README.md:211；README.zh-TW.md:78、README.zh-TW.md:80、README.zh-TW.md:110、README.zh-TW.md:185
- 現象：
  - 測試數寫 244 項，實際是 576 項。
  - 前端產物寫約 300 KB，實際是 683 KB（gzip 後 201 KB）。DESIGN.md:533 也寫約 300 KB。
  - 引擎那一列寫「預覽只跑 `head(1000)`」：程式沒有這個做法。預覽是 20 萬行以內整份讀進來，超過時用每個檔的前 10 萬行＋最後 1,000 行（同一份 README 的「已知限制」寫的才是對的）。
  - 資料流的圖寫 `engine/compiler.py`：沒有這個檔（引擎在 `engine/run.py`）。DESIGN.md:76 也一樣。
  - 命令列的說明（README.md:262、README.md:267；README.zh-TW.md:236、README.zh-TW.md:241）：`convert --encoding` 少列 `utf-8-sig`，`profile` 少列 `--out`（對照 src/tablade/cli.py:30、src/tablade/cli.py:62）。
- 證據：
  ```bash
  uv run pytest -q | tail -1
  npm --prefix frontend run build | grep "dist/index.html"
  grep -rn "head(1000)\|head(1_000)" src | wc -l
  ls src/tablade/engine | grep -v __pycache__ | tr '\n' ' '
  ```
  ```text
  576 passed in 15.29s
  dist/index.html  683.02 kB │ gzip: 201.05 kB
  0
  __init__.py check.py progress.py run.py
  ```
- 確認程度：已重現。
- 建議修法：更新這四處（兩份 README 都要）。測試數與檔案大小會一直變，可以改寫成不帶數字的說法，或在發版前的檢查清單加一項。
- 狀態：已修（5181505）

### F-062［低］［文件］DESIGN.md §12 的檔案結構與實際不符

- 位置：DESIGN.md:385-467
- 現象：
  - 列了不存在的檔：`engine/preview.py`（DESIGN.md:420）、`steps/parse_tw.py`（DESIGN.md:430）、`tests/test_steps_*.py`（DESIGN.md:461，實際是 `tests/test_steps.py`）。
  - 沒列到已經有的檔：`session.py`、`skill.py`、`profile.py`、`i18n.py`、`usage.py`、`update.py`、`desktop.py`、`selftest.py`、`dtypes.py`；`examples/sales_combined.recipe.json`；`scripts/check_i18n.py`、`scripts/check_release.py`、`scripts/gen_frontend_dev_data.py`。
  - `preview.py`、`parse_tw.py` 旁邊還標著 M2、M3 的計畫；`engine/run.py` 的說明只寫「寫出 Parquet」，現在也寫 CSV。
- 證據：
  ```bash
  ls src/tablade/engine src/tablade/steps | grep -v __pycache__ | tr '\n' ' '
  ```
  ```text
  src/tablade/engine: __init__.py check.py progress.py run.py  src/tablade/steps: __init__.py aggregate.py base.py columns.py convert.py files.py fill_down.py filter.py headers.py join.py registry.py remove_rows.py report.py split.py text.py
  ```
- 確認程度：已重現（對照 `git ls-files`）。
- 建議修法：照 `git ls-files` 重寫這一節，或縮成只列資料夾與各自的用途（細到檔案的清單很容易過時；README 的「程式架構」已經有一份）。
- 狀態：已修（5181505）

### F-063［低］［文件］DESIGN.md §14 還留著和串流規則相反的規則

- 位置：DESIGN.md:487-496；對照 CLAUDE.md 的「串流規則」、DESIGN.md §8
- 現象：
  - DESIGN.md:493「依賴列順序的操作，一律先依 `__source_file, __line_no` 排序」，和 §8、CLAUDE.md 的「步驟不要 sort」相反（排序在串流下會把整份資料放進記憶體）。
  - DESIGN.md:491「必須同時完成 §7.3 的五件事」：§7.3 現在列的是 8 件，CLAUDE.md 另外還要求 `label_en`、`doc_en`、`en()`。
  - DESIGN.md:496 的檢查只列 `pytest` 和 `check_no_data.py`，CLAUDE.md 還有 `check_code_policy.py`、前端的 lint 與 build。
  - 這一節的標題是「給 AI coding 的規則（放進 CLAUDE.md）」，照著它做會違反現在的規則。
- 證據：
  ```bash
  sed -n '491p;493p' DESIGN.md
  ```
  ```text
  - 新增步驟時，必須同時完成 §7.3 的五件事（參數 model、apply、describe、infer_schema 視需要、測試）。
  - 依賴列順序的操作，一律先依 `__source_file, __line_no` 排序。
  ```
- 確認程度：已重現（讀文件對照）。
- 建議修法：§14 改成一句「規則以 CLAUDE.md 為準」並連過去，不要留兩份。
- 狀態：已修（5181505）

### F-064［低］［文件］DESIGN.md §17 寫了程式沒有的行為（自動升級、`only_row_types`、說明選單、v1.4）

- 位置：DESIGN.md:663、DESIGN.md:697、DESIGN.md:699、DESIGN.md:724-727、DESIGN.md:736
- 現象：
  - DESIGN.md:727「版本比較舊 → 自動升級」、DESIGN.md:724「major：附自動升級舊 recipe 的程式」：程式沒有升級舊 Recipe 的功能，不同 major（例如 0.9）直接報「不相容」。
  - DESIGN.md:697 驗證器的語意檢查列了「`only_row_types` 引用的類型有定義」：§7.1 已經寫 `only_row_types` 先不做，程式裡也沒有這個參數。
  - DESIGN.md:699 錯誤路徑的例子是 `steps[3].params.column`：實際的格式是 `stages[1].steps[0].columns[0]`（有階段、沒有 `params`）。
  - DESIGN.md:736「前端的說明選單提供下載 Copilot skill（v1.4）」：畫面上沒有「說明」選單，是工具列的「Copilot」選單與起始頁的「⋯」。
  - DESIGN.md:663、DESIGN.md:736 的 `v1.4`、DESIGN.md:725 的「recipe 1.4」：現在是 1.0（`tablade-recipe-skill-v1.0.zip`），看起來像是目前的版本。
- 證據：
  ```bash
  uv run python -c "
  from pathlib import Path
  from tablade.engine.check import check_recipe
  from tablade.skill import skill_filename
  stage = {'name': 'x', 'input': {'files': ['a.csv'], 'reader': 'csv'}, 'steps': [], 'output_format': 'parquet'}
  data = {'tablade_recipe': '0.9', 'name': 'v', 'stages': [stage], 'output': {}}
  print([i['error'] for i in check_recipe(data, Path('.')).issues if i['path'] == 'tablade_recipe'])
  print(skill_filename())"
  grep -rn "only_row_types" src | wc -l
  ```
  ```text
  ['不相容的 Recipe 版本 0.9（這裡支援 1.x）']
  tablade-recipe-skill-v1.0.zip
  0
  ```
- 確認程度：已重現。
- 建議修法：自動升級改寫成「之後有 major 變更時才做」；拿掉 `only_row_types`；路徑的例子換成實際的格式；選單的說法照畫面；版本號的例子標明是舉例，或直接寫 1.0。
- 狀態：已修（5181505）

### F-065［低］［文件］「前端不能指定任意檔案路徑」的說法和「手動輸入路徑」的功能不符

- 位置：DESIGN.md:541、DESIGN.md:543；src/tablade/api.py:6（模組說明）；功能本身：src/tablade/api.py:312-315、src/tablade/session.py:673-683（`add_file_entry`）
- 現象：
  - DESIGN.md:543「檔案只用代號：前端只拿到檔案代號，不能要求 Python 讀任意路徑」，api.py 的說明也寫「前端拿不到也不能指定任意檔案路徑」。
  - 實際上 `add_file_entry(text)` 接受前端送來的任何路徑（絕對路徑、含 `*` 的也可以）加進檔案清單，之後照樣讀；`state` 裡也直接給完整路徑（`recipe.path`、`base_dir`、檔案清單），沒有「代號」。
  - 手動輸入路徑是 DESIGN.md §5.3 寫明的功能，所以是這兩句說明過時；但它們讀起來像安全上的保證，容易讓人誤以為有這一層防護。
  - 同一段的 DESIGN.md:541 寫 `get_rows(step_id, offset, limit)`：實際的 API 是 `rows(step, offset, limit)`，用的是步驟的位置，不是 id。
- 證據：
  ```bash
  grep -n "def add_file_entry" -A3 src/tablade/api.py; grep -c "get_rows" src/tablade/api.py frontend/src/bridge.ts
  ```
  ```text
  313:    def add_file_entry(self, text: str) -> dict[str, Any]:
  314-        self._session.add_file_entry(str(text))
  315-        return self._state()
  316-
  src/tablade/api.py:0
  frontend/src/bridge.ts:0
  ```
- 確認程度：只靠讀程式推論（功能照設計運作，不一致的是說明）。
- 建議修法：兩處改成實際的情況，例如「開檔、存檔的對話框由 Python 開；檔案清單也可以手動輸入路徑（§5.3）。頁面不載入任何外部內容（CSP），所以能呼叫 API 的只有 Tablade 自己的前端」。`get_rows` 改成 `rows(step, offset, limit)`。
- 狀態：已修（5181505）

### F-066［低］［文件］兩份 README 不同步；英文 README 的按鈕名稱和畫面不一樣

- 位置：README.md:105、README.md:110-113、README.md:213、README.md:230、README.md:326、README.md:339-342；README.zh-TW.md:255-256
- 現象：
  - 中文 README 的「開發」一節最後有一段說明 `.tablade-denylist` 怎麼用（README.zh-TW.md:255-256），英文 README 沒有對應的段落。
  - 英文 README 寫的按鈕與用語，和英文畫面、docs/i18n-plan.md 的對照表不一樣，照著 README 在畫面上找不到：

    | README.md | 畫面與對照表 |
    |---|---|
    | "Load data & preview" | Load & preview |
    | "Process and export" | Process & export |
    | "merge source files" | Combine source files |
    | "sample mode"／"full mode" | Sample mode／Full mode |

- 證據：
  ```bash
  grep -n -o '"Load data & preview"\|"Process and export"\|merge source files' README.md
  grep -n -o "tr('載入資料及預覽', 'Load & preview')\|tr('處理並匯出資料', 'Process & export')" frontend/src/App.tsx | head -2
  grep -n 'label_en = "Combine' src/tablade/steps/files.py
  ```
  ```text
  105:merge source files
  110:"Load data & preview"
  111:"Process and export"
  113:merge source files
  213:merge source files
  230:merge source files
  326:merge source files
  296:tr('載入資料及預覽', 'Load & preview')
  313:tr('處理並匯出資料', 'Process & export')
  53:    label_en = "Combine source files"
  ```
- 確認程度：已重現（讀文件對照畫面的字串）。
- 建議修法：英文 README 補上 denylist 那一段；按鈕與步驟名稱照畫面的英文。
- 狀態：已修（5181505）

### F-067［低］［文件］程式裡過時或放錯位置的說明

- 位置與現象：
  - src/tablade/ingest/sources.py:4：模組說明寫「以 16 MB 為單位分塊讀取」，實際是 1 MB（src/tablade/ingest/transcode.py:17 的 `CHUNK = 1 << 20`，旁邊的說明還解釋了為什麼不用 16 MB）。
  - src/tablade/engine/run.py:69-74：`PART` 的說明（「串流執行時，中間階段的暫存結果裡每一列屬於第幾個 part…」）接在 `STAGED_ROW_GROUP` 的說明後面，變成 `STAGED_ROW_GROUP` 有兩段說明、`PART` 沒有。
  - src/tablade/engine/run.py:71-73 寫 row group 改小後「約 0.8 GB」，DESIGN.md §8 同一件事寫「1.4 GB」（improve.md 第 1 項的表：0.8 GB 是 2 × 150 MB 的資料，1.4 GB 是 2 × 1 GB）。
  - src/tablade/ingest/sources.py:207：`expand` 的說明寫「有 * 才當成 glob」，實際上 `?`、`[` 也算（見「檔名或資料夾名稱有 `[`、`?`」那一項）。
  - src/tablade/steps/__init__.py:7、src/tablade/steps/__init__.py:12：`remove_rows` 的註解少了「移除重複」，`fill_down` 少了「依條件複製欄位」；src/tablade/steps/columns.py:1 的模組說明只寫「刪除欄。」，裡面有三種步驟。
  - src/tablade/steps/aggregate.py:7「全部用 Polars 原生運算，可以串流」、src/tablade/steps/convert.py:5「所以壞資料不會悄悄消失」：和實際的行為不符（見「樞紐…不是串流」那一項，以及「檢查過」一節的「警告數的已知差異」：串流時被後面的步驟拿掉的列不會警告）。
  - src/tablade/steps/base.py:94-95：`WarningCheck.message`「可含一個 `{}`」，沒有提欄名含大括號會出錯（見那一項）。
  - src/tablade/dtypes.py:1、src/tablade/dtypes.py:11：說明還寫「型別的中文名稱」，現在中英文都有。
  - frontend/src/components/PreviewGrid.tsx:3 寫欄標題有右鍵選單，實際只有儲存格有（DESIGN.md §16.6 也寫欄標題沒有）；frontend/src/bridge.ts:309 寫 `window.state.stages`，實際是 `window.state.run`。
  - improve.md:3 寫「做完就從這裡移掉」，但 9 項都標了「已完成」還留著；docs/improve-plan.md:230 也寫「做完的項目從 improve.md 移掉」。
  - docs/i18n-plan.md:36、docs/i18n-plan.md:41、docs/i18n-plan.md:49 寫的是計畫當時的寫法（`t(zh=…, en=…)`、`ui_labels_en`、驗證器的 `_t()`），實際是 `t("中文", "English")`、`en(…, ui_labels=…)`、驗證器的 `t()`。計畫已標「已完成」，照它寫會寫錯。
- 證據：
  ```bash
  grep -n -o "以 16 MB 為單位分塊讀取" src/tablade/ingest/sources.py
  grep -n "^CHUNK = " src/tablade/ingest/transcode.py
  sed -n '69,74p' src/tablade/engine/run.py
  ```
  ```text
  4:以 16 MB 為單位分塊讀取
  17:CHUNK = 1 << 20
  PART = f"{TEMP_PREFIX}part"
  STAGED_ROW_GROUP = 8_192
  """暫存 Parquet 每個 row group 最多幾列。串流讀回來時每條執行緒一次解開一個 row group：
  預設的 row group 很大（一個檔一個），範例資料 530 萬列讀回來接成一份要 3.2 GB；
  改成 8,192 列約 0.8 GB，速度差不多（實測記錄在 improve.md 第 1 項）。"""
  """串流執行時，中間階段的暫存結果裡每一列屬於第幾個 part（下一個階段讀回來時依它分開）。"""
  ```
- 確認程度：已重現（讀程式對照）。
- 建議修法：逐項更正；improve.md 的項目移掉或把開頭那句改成「已完成的留作紀錄」。
- 狀態：已修（2e80b79、5181505、413c289）

### F-068［低］［文件］CLAUDE.md「新增步驟」的規則，和 registry 實際檢查的不一樣

- 位置：CLAUDE.md「程式規則」的「新增步驟」；src/tablade/steps/registry.py:12-29；src/tablade/steps/base.py:71（`CATEGORY_EN`）
- 現象：
  - **規則有寫、註冊時不檢查**：`ai_notes`（只有 tests/test_steps.py:136 檢查）；`effect`（沒宣告就用預設的「不變」，不會被擋；25 種步驟有 11 種用預設）；欄位的 `title` 與 `en()`（只有 tests/test_i18n.py 檢查）。
  - **註冊時會檢查、規則沒寫**：`scope_note` 要配 `scope_note_en`（CLAUDE.md 完全沒提 `scope_note`）；步驟類型不能重複。
  - **兩邊都沒管**：`category` 是不是 `CATEGORY_EN` 裡的四個（列、欄、彙總、合併）。新增一個分類時，英文畫面的分頁會直接顯示中文（src/tablade/steps/base.py:176 找不到就用原字）。
  - CLAUDE.md 寫「`ai_notes` 與 `examples` 維持中文（`tests/test_i18n.py` 會檢查）」：測試只檢查有沒有英文，沒有檢查這兩項是中文。
  - CLAUDE.md 的串流規則與 src/tablade/steps/base.py:16-18 寫「要接多個輸入的步驟（join、附加查詢）才做成 `combines = True`」，但結合實際上是 `combines = False`、用 `bind()`／`table()` 取另一張表（DESIGN.md §8 與 CLAUDE.md 第 5 點是這樣寫的）：同一份文件前後矛盾。
- 證據：
  ```bash
  sed -n '12,27p' src/tablade/steps/registry.py | grep -o "if [^:]*:" ; grep -c "scope_note" CLAUDE.md
  ```
  ```text
  if name in _STEPS:
  if not cls.label or not cls.category:
  if not cls.__doc__:
  if not cls.label_en or not cls.doc_en:
  if cls.scope_note and not cls.scope_note_en:
  if not cls.examples:
  if cls.effect.get("kind") not in EFFECT_KINDS:
  0
  ```
- 確認程度：已重現（讀程式對照）。
- 建議修法：`register()` 補上 `ai_notes`、`category in CATEGORY_EN` 的檢查；CLAUDE.md 補上 `scope_note`／`scope_note_en`，並把「join 做成 `combines`」那句改成實際的做法。
- 狀態：已修（2e80b79）

### F-069［低］［文件］DESIGN.md §7、§16.5、§17.5 的步驟說明和程式不符

- 位置與現象：
  - DESIGN.md:718「Tablade 的 `output_columns()`」、DESIGN.md:269 的 `infer_schema()`：程式裡沒有這兩個名稱，實際是 `Step.output_order()`／`reshape_order()` 與 `suggest()`。
  - DESIGN.md:711 把 `split_delimited`、`promote_headers` 列成「整組換掉」：實際的欄位效果是 `split` 與 `rename_data`；`replace_data` 沒有任何步驟在用。
  - DESIGN.md:203：`split_delimited`「用 Python `csv` 解析，或 `str.split`」：實際是 Polars 原生的正規表示式（§8 寫的才對）。
  - DESIGN.md:202：`promote_headers`「指定某一列當欄名」：實際只能是目前的第一列。
  - DESIGN.md:201：`remove_blank_rows`「null 或空字串」：實際上只有空白字元的也算。
  - DESIGN.md:195：「已完成」的清單有 17 個，少了同一張表裡的 `classify_rows`、`extract_regex`、`split_fixed_width`。
  - DESIGN.md:575：`ui: "regex"`「正規表示式輸入框＋即時測試」：畫面上只是文字框，沒有即時測試。
  - DESIGN.md §16.5 的表單對照表沒有列已經在用的 `ui: "new_columns"`、`"int_list"`、`"source_files"`、`"option_cards"`。
  - DESIGN.md:127：還寫 `inputs[].files`（現在是每個階段的 `input.files`）。
  - DESIGN.md:352-377：§11 的 `.gitignore` 內容比實際少（沒有 `*.recipe.json`、`log/`、`tmp/` 與多種副檔名）。
- 證據：
  ```bash
  grep -rn "def output_columns\|def infer_schema" src | wc -l; grep -c '"kind": "replace_data"' src/tablade/steps/*.py | grep -vc ":0"
  ```
  ```text
         0
  0
  ```
- 確認程度：已重現（讀文件對照程式；清單由另一位審查者整理，我核對了前七項）。
- 建議修法：逐項更正。
- 狀態：已修（5181505）

### F-070［低］［文件］AI skill 的 SKILL.md 有幾處和程式不符

- 位置：src/tablade/skill.py:285、src/tablade/skill.py:239、src/tablade/skill.py:261、src/tablade/skill.py:230-232
- 現象：SKILL.md 是給 AI 看的說明，AI 會照著跟使用者說。
  - 第 285 行：「recipe 不記輸出位置：使用者在 Tablade 按『處理並匯出資料』時才選存檔位置」。實際上匯出不會開存檔對話框，一律寫到 Recipe 旁邊的 `output/`，檔名在每個階段的 `output_file`。同一份 SKILL.md 的「階段」段落寫的是對的，兩段互相矛盾。
  - 第 239 行：「套用到真實資料並輸出 Parquet」：現在也可以輸出 CSV。
  - 第 261 行：按鈕寫「複製錯誤報告」：畫面上是「Copilot」選單裡的「檢查並複製錯誤報告」。
  - 步驟速查表有 14 種步驟的名稱重複，例如「移除重複：移除重複：依指定的欄…」（說明本身就以名稱開頭，前面又加了一次名稱）。
- 證據：
  ```bash
  uv run python -c "
  from tablade.skill import skill_markdown
  rows = [l.split('|')[2].strip() for l in skill_markdown().splitlines() if l.startswith('| \`')]
  dup = [r for r in rows if r.split('：')[1].startswith(r.split('：')[0])]
  print(len(rows), len(dup)); print(dup[0][:40])"
  sed -n '285p' src/tablade/skill.py
  ```
  ```text
  25 14
  移除重複：移除重複：依指定的欄判斷重複的列，只留第一筆（或最後一筆）。每個來源檔
  - recipe 不記輸出位置：使用者在 Tablade 按「處理並匯出資料」時才選存檔位置（`output` 裡沒有 `path`）。
  ```
- 確認程度：已重現。
- 建議修法：改寫這三行；速查表不要再加一次名稱。tests/test_skill.py 可以加一項：SKILL.md 提到的按鈕名稱要能在前端的字串裡找到。
- 狀態：已修（2e80b79）

## 步驟的測試涵蓋度

### 怎麼量的

- repo 沒有涵蓋度工具（沒有 `coverage`、`pytest-cov`，這次也不能安裝套件），所以用 Python 內建的 `sys.monitoring` 寫了一個 pytest 外掛來量。外掛只放在暫存資料夾，原始碼附在這一節最後。
- 跑的是整套測試（`uv run pytest`，576 項），量三件事：
  1. **行涵蓋**：步驟類別裡每個函式的每一行，整個測試過程有沒有執行過。
  2. **怎麼被用到**：每種步驟的 `apply()`／`combine()` 在哪些測試被呼叫、是不是在串流模式（stream）跑過、有沒有真的產生過警告、有沒有丟過錯誤，`suggest()` 有沒有被呼叫。
  3. **參數的選項**：schema 裡有固定選項的參數（`Literal`、布林），每個值有沒有在某一次 `apply()` 用過。
- 這些數字的限制：
  - 「跑過」不等於「結果有被檢查」。
  - 很多選項寫在同一行的對照表裡（例如篩選的運算子），行涵蓋看不出哪個選項沒測，所以另外量第 3 項。
  - 「在串流跑過」包含 tests/test_golden.py 那種「各自和預期結果比」的測試，不一定是同一個測試裡 stream 和 eager 互相比。

### 每種步驟的數字

| 步驟 | 名稱 | 範例 | 用到它的測試 | 其中 tests/test_steps.py | 在串流跑過的測試 | 產生過警告的測試（eager／stream） | 丟過錯誤的測試 | `suggest()` 的測試 | 函式裡沒跑到的行 |
|---|---|---|---|---|---|---|---|---|---|
| `remove_top_rows` | 移除前 N 列 | 1 | 98 | 4 | 41 | （沒有警告） | 0 | 13 | — |
| `remove_bottom_rows` | 移除後 N 列 | 1 | 80 | 2 | 35 | （沒有警告） | 0 | 5 | — |
| `remove_blank_rows` | 移除空白列 | 1 | 79 | 4 | 34 | （沒有警告） | 0 | （沒有） | `apply` 1 行 |
| `dedupe` | 移除重複 | 1 | 6 | 2 | 3 | （沒有警告） | 2 | 2 | `apply` 1 行 |
| `filter` | 篩選 | 2 | 94 | 19 | 35 | （沒有警告） | 0 | 2 | `suggest` 2 行 |
| `split_delimited` | 依分隔符號拆成多欄 | 1 | 92 | 17 | 35 | 3／0 | 0 | 1 | `suggest` 1 行 |
| `split_column` | 拆欄 | 1 | 54 | 4 | 27 | 3／1 | 3 | 2 | `suggest` 1 行 |
| `classify_rows` | 將列進行分類 | 1 | 5 | 3 | 1 | （沒有警告） | 1 | 0 | `suggest` 6 行 |
| `extract_regex` | 用正規表示式擷取 | 1 | 6 | 3 | 1 | （沒有警告） | 2 | 0 | `suggest` 2 行 |
| `split_fixed_width` | 依固定位置拆欄 | 1 | 7 | 4 | 1 | （沒有警告） | 1 | 1 | `suggest` 1 行 |
| `promote_headers` | 把第一列作為欄位標題 | 1 | 88 | 3 | 40 | 5／3 | 2 | 1 | `suggest` 1 行 |
| `conditional_copy` | 依條件複製欄位 | 1 | 6 | 4 | 0 | （沒有警告） | 2 | 0 | `suggest` 9 行 |
| `fill_down` | 向下填滿 | 1 | 86 | 5 | 37 | （沒有警告） | 2 | 1 | — |
| `remove_columns` | 刪除欄 | 2 | 12 | 8 | 1 | （沒有警告） | 5 | 3 | — |
| `select_columns` | 保留和排序欄位 | 1 | 33 | 3 | 10 | （沒有警告） | 1 | 0 | `suggest` 2 行 |
| `rename_columns` | 改欄名 | 1 | 4 | 3 | 1 | （沒有警告） | 1 | 0 | `suggest` 2 行 |
| `trim_clean` | 清理文字 | 1 | 42 | 11 | 9 | （沒有警告） | 4 | 1 | `suggest` 2 行 |
| `replace_values` | 取代文字 | 2 | 10 | 6 | 1 | （沒有警告） | 2 | 0 | `suggest` 6 行 |
| `parse_number` | 文字轉數字 | 1 | 58 | 4 | 21 | 15／6 | 9 | 9 | — |
| `parse_date` | 文字轉日期 | 2 | 68 | 25 | 15 | 18／0 | 1 | 1 | — |
| `unpivot` | 取消樞紐 | 1 | 5 | 3 | 1 | （沒有警告） | 2 | 1 | — |
| `group_by` | 群組彙總 | 1 | 9 | 4 | 3 | （沒有警告） | 2 | 2 | — |
| `pivot` | 樞紐 | 1 | 8 | 3 | 1 | 3／1 | 3 | 4 | `suggest` 1 行 |
| `merge_files` | 合併來源檔 | 1 | 48 | 4 | 21 | （沒有警告） | 0 | 2 | `apply` 1 行 |
| `join` | 結合 | 1 | 32 | 5 | 7 | 26／7 | 4 | 1 | `describe` 4 行、`suggest` 3 行、`warning_checks` 1 行 |

- 「用到它的測試」是整套測試裡呼叫過這個步驟的測試數。前 8 個範例 Recipe 用到的步驟數字很高，是因為 tests/test_large.py、tests/test_session.py 幾乎每個測試都拿範例 Recipe 來跑。
- 每種步驟的 `describe()` 在中文、英文都至少被呼叫過一次（表格省略）。
- 行涵蓋整體很高：25 種步驟的函式共 849 行，沒跑到 49 行，其中 39 行在 `suggest()`。所以**缺口不在「哪幾行沒跑到」，而在下面幾項：哪些組合、哪些資料形狀沒有測**。

### 其他模組（附帶）

同一次量測，其他模組的函式行涵蓋：`api.py` 52%、`app.py` 42%、`usage.py` 40%、`desktop.py` 17%、`engine/check.py` 70%、`recipe/validator.py` 80%、`ingest/transcode.py` 85%；其餘都在 88% 以上（`engine/run.py` 95%、`session.py` 94%、`ingest/sources.py` 94%）。`api.py`、`app.py`、`desktop.py` 低是因為要真的視窗；`usage.py` 低是因為 Windows、Linux 的分支在 macOS 跑不到。

### F-071［低］［測試］四種步驟幾乎沒有串流的測試：依條件複製欄位完全沒在串流跑過

- 位置：tests/test_large.py（stream＝eager 的比對）、tests/test_golden.py；步驟在 src/tablade/steps/fill_down.py（`conditional_copy`）、src/tablade/steps/report.py（`classify_rows`、`extract_regex`、`split_fixed_width`）
- 現象：
  - `conditional_copy`：整套測試沒有任何一次在串流模式執行。
  - `classify_rows`、`extract_regex`、`split_fixed_width`：串流只跑過 1 次，就是 tests/test_golden.py 用分段報表的範例和預期結果比的那一個（單一個檔、每個步驟一組參數）。tests/test_large.py 的 stream＝eager 比對完全沒有用到它們。
  - `remove_columns`、`rename_columns`、`replace_values`、`unpivot`、`pivot` 也都只有 1 個串流的測試。
  - 這幾個步驟正是「攤平分段報表」的主要流程（DESIGN.md §7.1），大檔時走的就是串流。
- 證據：上表「在串流跑過的測試」那一欄（`conditional_copy` 是 0）。
- 確認程度：已重現（量測）。
- 建議修法：tests/test_large.py 加一個用分段報表的 stream＝eager 比對（兩個以上的檔、含 `conditional_copy`＋`fill_down`），並把上面只有 1 個串流測試的步驟補進現有的比對裡。
- 狀態：已修（413c289）

### F-072［低］［測試］兩種步驟的警告在串流模式沒有測過

- 位置：tests/test_large.py；src/tablade/steps/split.py:189-200（`split_delimited` 段數超出的警告）、src/tablade/steps/convert.py:137-149（`parse_date` 轉不了的警告）
- 現象：
  - `split_delimited`：有 3 個測試在 eager 產生過「拆出 N 段，超過…」的警告，串流 0 個。
  - `parse_date`：有 18 個測試在 eager 產生過「不是日期」的警告，串流 0 個。（`parse_number` 用同一段程式，串流有 6 個測試產生過警告，所以風險較小。）
  - 串流模式的警告是另一條路（暫存欄一路帶到最後才取），而且「只算最後還留著的列」，和 eager 的算法不同，最需要測試。
- 證據：上表「產生過警告的測試」那一欄。
- 確認程度：已重現（量測）。
- 建議修法：tests/test_large.py 的比對資料裡放進段數超出的列與轉不了的日期，確認兩種模式的警告數與行號一樣。
- 狀態：已修（413c289）

### F-073［低］［測試］有些參數的選項沒有任何測試用過

- 位置：tests/test_steps.py；選項定義在 src/tablade/steps/filter.py:126-140、src/tablade/steps/aggregate.py:115-125、src/tablade/steps/split.py（`keep_source`）
- 現象：下面這些選項，整套測試沒有任何一次 `apply()` 用過（選項寫在同一行的對照表裡，行涵蓋是 100%，看不出來）：
  - 篩選的數值條件「≥」（`ge`）、「<」（`lt`）。
  - 群組彙總的「最小」（`min`）；樞紐的「最大」「平均」「最小」。
  - 拆欄（`split_column`）的「保留來源欄」。
  - 「將列進行分類」的條件只用過 5 種運算子，「依條件複製欄位」只用過 3 種，兩個都沒測過「不分大小寫」。它們和篩選共用同一組條件的程式，所以運算子本身在篩選測過；沒測到的是「這兩個步驟帶著這些條件」的組合。
- 證據（量測的輸出：步驟、參數、沒用過的值、用過的值）：
  ```text
  filter | conditions[].op | 沒用過："ge", "lt" | 用過："contains", "ends_with", "equals", "gt", "is_empty", "is_not_empty", "le", "not_contains", "not_equals", "not_regex", "num_equals", "regex", "starts_with"
  split_column | keep_source | 沒用過：true | 用過：false
  classify_rows | rules[].conditions[].ignore_case | 沒用過：true | 用過：false
  classify_rows | rules[].conditions[].op | 沒用過："ends_with", "ge", "gt", "is_not_empty", "le", "lt", "not_contains", "not_equals", "not_regex", "num_equals" | 用過："contains", "equals", "is_empty", "regex", "starts_with"
  conditional_copy | conditions[].ignore_case | 沒用過：true | 用過：false
  conditional_copy | conditions[].op | 沒用過："contains", "ends_with", "ge", "gt", "is_not_empty", "le", "lt", "not_contains", "not_equals", "not_regex", "num_equals", "regex" | 用過："equals", "is_empty", "starts_with"
  group_by | aggregations[].op | 沒用過："min" | 用過："count", "first", "last", "max", "mean", "n_unique", "sum"
  pivot | op | 沒用過："max", "mean", "min" | 用過："count", "first", "last", "sum"
  ```
- 確認程度：已重現（量測）。
- 建議修法：tests/test_steps.py 用參數化的測試把每個運算子、每種彙總方式各跑一次並檢查結果；`keep_source=true` 補一個。之後可以把「每個固定選項至少被一個測試用到」做成一項測試（用這次的外掛的做法，或在 `Example` 裡補齊）。
- 狀態：已修（413c289；樞紐的「筆數」沒有資料的格子維持空值，和其他彙總方式一樣）

### F-074［低］［測試］新增步驟的建議值（`suggest()`）有 6 種步驟完全沒測；結合的說明與警告有分支沒測

- 位置：tests/test_session.py、tests/test_steps.py；沒跑到的行：
  - `suggest()` 完全沒被呼叫：`classify_rows`（src/tablade/steps/report.py:132-137）、`extract_regex`（src/tablade/steps/report.py:252-253）、`conditional_copy`（src/tablade/steps/fill_down.py:85-94）、`select_columns`（src/tablade/steps/columns.py:170-171）、`rename_columns`（src/tablade/steps/columns.py:221-222）、`replace_values`（src/tablade/steps/text.py:256-261）。
  - `suggest()` 有分支沒跑到：`filter`（src/tablade/steps/filter.py:212、src/tablade/steps/filter.py:216：沒有欄可選、點在空白的值上）、`trim_clean`（src/tablade/steps/text.py:144-145）、`join`（src/tablade/steps/join.py:161-169）、`promote_headers`（src/tablade/steps/headers.py:67：沒有資料時）。
  - 結合的 `describe()`：「只留有對到的」（src/tablade/steps/join.py:336-338）與「沒有要帶的欄」（src/tablade/steps/join.py:346）沒跑到；`warning_checks()` 在另一張表缺鍵時的分支（src/tablade/steps/join.py:303）沒跑到。
- 現象：`suggest()` 決定使用者新增步驟時表單預先填什麼。這次找到的「在系統欄位上新增時，建議的參數一套用就出錯」（見「步驟的幾個小問題」）就在這些沒測的地方。
- 證據：上表最後一欄。
- 確認程度：已重現（量測）。
- 建議修法：tests/test_session.py 加一個參數化的測試：對每種步驟，在有資料的階段用 `add_step()` 新增（點在一般欄、系統欄、有值、空白各一次），檢查建議出來的參數套用後不是「欄位不存在」這類錯誤。
- 狀態：已修（413c289）

### F-075［低］［測試］這次找到的 bug，對應到的都是沒有測試的資料形狀

- 位置：tests/test_steps.py、tests/test_large.py
- 現象：行涵蓋很高，但這次的 bug 幾乎都出在「測試資料太乾淨」的地方。把它們列在一起，補測試時可以當成清單：

  | 沒測到的情況 | 對應的發現 |
  |---|---|
  | 條件比對的欄有空值（否定的運算子） | 篩選的「不等於／不包含」遇到空值 |
  | 固定位置的報表裡有 ℃、×、※ 這類字 | 依固定位置拆欄錯位 |
  | 取消樞紐之後再彙總（同一個行號有好幾列） | 組的順序不固定 |
  | 樞紐依據的欄有空值或空白 | 那些列默默不見 |
  | 結合時這一邊的鍵不存在，而且 `warn_unmatched` 開著 | 沒包裝的 Polars 例外 |
  | 標題列有空白格、重複的欄名 | 每個檔都警告「標題不同」 |
  | 欄名含 `{`、`}` | 一有警告就全部失敗 |
  | 新欄名和來源欄同名 | 拆欄執行失敗 |
  | 一百萬以上的門檻 | 說明文字變成科學記號 |
  | 多階段：同一個檔經兩個階段後附加，再被下一個階段讀 | 串流會丟資料 |
  | 串流的執行計畫有沒有退回 in-memory | 樞紐等三種情況不是串流 |
  | 檔名有 `[`、大小寫不同的輸出檔名、右表是樣本 | 見各項 |

- 確認程度：已重現（每一項都在對應的發現裡）。
- 建議修法：tests/test_steps.py 加一組共用的「難搞資料」（空值、空字串、只有空白、重複的值、全形與 cp950 的符號、含括號的欄名），讓每種步驟的參數化測試都跑一遍；tests/test_large.py 的 stream＝eager 比對也用同一組資料。
- 狀態：已修（413c289）

### F-076［低］［測試］設了 `TABLADE_NO_UPDATE_CHECK` 的環境，`tests/test_update.py` 有 5 項會失敗

- 位置：tests/test_update.py（只有第 72 行的測試自己設這個環境變數，其他測試沒有先清掉）
- 現象：這個環境變數是給「網路受管控的電腦」關掉版本檢查用的。在這種電腦上開發（環境變數設在系統裡）時，整套測試會有 5 項失敗，看起來像程式壞了。
- 證據：
  ```bash
  uv run pytest -q tests/test_update.py | tail -1
  TABLADE_NO_UPDATE_CHECK=1 uv run pytest -q tests/test_update.py | tail -1
  ```
  ```text
  8 passed in 0.11s
  5 failed, 3 passed in 0.13s
  ```
- 確認程度：已重現。
- 建議修法：tests/test_update.py 加一個 autouse 的 fixture，先 `monkeypatch.delenv("TABLADE_NO_UPDATE_CHECK", raising=False)`。
- 狀態：已修（2e80b79）

### 量測用的外掛

<details>
<summary>tablade_cov.py（放在 repo 外的任何資料夾）</summary>

```python
"""pytest plugin：不裝任何套件，量每種步驟的測試涵蓋度（只用標準函式庫的 sys.monitoring，Python 3.12+）。

用法（在 repo 根目錄）：
    PYTHONPATH=<這個檔所在的資料夾> TABLADE_COV_OUT=<結果.json> uv run pytest -q -p tablade_cov

量兩件事：
1. 行涵蓋度：src/tablade 底下每個函式的每一行，在整個測試過程有沒有執行過。
2. 步驟的使用情況：每種步驟的 apply()／combine() 在哪些測試被呼叫、用了哪些參數值、
   是不是在串流模式跑過、有沒有產生過警告、有沒有丟過錯誤，describe()／suggest() 有沒有被呼叫。
"""

from __future__ import annotations

import collections
import functools
import json
import os
import sys
from pathlib import Path

OUT = Path(os.environ["TABLADE_COV_OUT"])
ROOT = Path.cwd().resolve()
TARGET = str(ROOT / "src" / "tablade") + os.sep

hits: dict[str, set[int]] = collections.defaultdict(set)
TOOL = sys.monitoring.COVERAGE_ID


def _on_line(code, line):
    if code.co_filename.startswith(TARGET):
        hits[code.co_filename].add(line)
    return sys.monitoring.DISABLE  # 每個位置只需要知道有沒有跑過


sys.monitoring.use_tool_id(TOOL, "tablade-review")
sys.monitoring.register_callback(TOOL, sys.monitoring.events.LINE, _on_line)
sys.monitoring.set_events(TOOL, sys.monitoring.events.LINE)

current = {"test": ""}
usage: dict[str, dict] = collections.defaultdict(
    lambda: {
        "tests": collections.defaultdict(set),  # 測試檔 → 測試
        "modes": collections.defaultdict(set),  # eager／stream → 測試
        "both_modes": set(),  # 同一個測試裡兩種模式都跑過
        "values": collections.defaultdict(set),  # 參數路徑 → 用過的值
        "warned": collections.defaultdict(set),  # eager／stream → 測試（真的產生警告）
        "errors": set(),  # 丟過錯誤的測試（錯誤類別|測試）
        "describe": collections.defaultdict(set),  # 語言 → 測試
        "suggest": set(),
    }
)
per_test_modes: dict[str, dict[str, set[str]]] = collections.defaultdict(
    lambda: collections.defaultdict(set)
)


def _mode() -> str:
    """呼叫的地方是串流（_stream_steps）還是整份在記憶體（apply_step）。"""
    frame = sys._getframe(2)
    for _ in range(25):
        if frame is None:
            break
        name = frame.f_code.co_name
        if name in ("_stream_steps", "_execute_stream"):
            return "stream"
        if name == "apply_step":
            return "eager"
        frame = frame.f_back
    return "direct"


def _walk(value, path=""):
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _walk(item, f"{path}.{key}" if path else key)
    elif isinstance(value, list):
        if not value:
            yield path, "[]"
        for item in value:
            yield from _walk(item, f"{path}[]")
    elif isinstance(value, (str, bool, int)) or value is None:
        yield path, value


def _record(step, mode: str) -> None:
    name = step.type_name()
    test = current["test"]
    item = usage[name]
    item["tests"][test.split("::")[0]].add(test)
    item["modes"][mode].add(test)
    per_test_modes[test][name].add(mode)
    for path, value in _walk(step.model_dump(mode="json", exclude={"id", "type"})):
        item["values"][path].add(json.dumps(value, ensure_ascii=False))


def _wrap_steps() -> None:
    from tablade import i18n
    from tablade.engine import run
    from tablade.steps import all_steps
    from tablade.steps.base import Step

    def inherited(cls, method):
        """這個類別用的那個方法（自己定義的，或從共用的父類別繼承的；Step 基底的不算）。"""
        for klass in cls.__mro__:
            if klass is Step:
                return None
            if method in klass.__dict__:
                return klass.__dict__[method]
        return None

    def wrap_call(cls, method):
        original = inherited(cls, method)
        if original is None:
            return

        @functools.wraps(original)
        def wrapper(self, *args, **kwargs):
            _record(self, _mode())
            try:
                return original(self, *args, **kwargs)
            except Exception as err:  # 步驟自己的錯誤、欄位效果的錯誤、Polars 的錯誤…
                usage[self.type_name()]["errors"].add(f"{type(err).__name__}|{current['test']}")
                raise

        setattr(cls, method, wrapper)

    def wrap_describe(cls):
        original = inherited(cls, "describe")
        if original is None:
            return

        @functools.wraps(original)
        def wrapper(self, *args, **kwargs):
            usage[self.type_name()]["describe"][i18n.lang()].add(current["test"])
            return original(self, *args, **kwargs)

        cls.describe = wrapper

    def wrap_suggest(cls):
        original = inherited(cls, "suggest")
        if original is None:
            return
        inner = original.__func__

        @functools.wraps(inner)
        def wrapper(klass, *args, **kwargs):
            usage[klass.type_name()]["suggest"].add(current["test"])
            return inner(klass, *args, **kwargs)

        cls.suggest = classmethod(wrapper)

    for cls in all_steps():
        wrap_call(cls, "apply")
        wrap_call(cls, "combine")
        wrap_describe(cls)
        wrap_suggest(cls)

    original_collect = run.collect_warnings

    @functools.wraps(original_collect)
    def collect(items, frame, limit, progress=None):
        found = original_collect(items, frame, limit, progress)
        mode = (
            _mode() if _mode() != "direct" else ("stream" if hasattr(frame, "collect") else "eager")
        )
        for key, step, _ in items:
            if found.get(key, (0, []))[0]:
                usage[step.type_name()]["warned"][mode].add(current["test"])
        return found

    run.collect_warnings = collect


def pytest_sessionstart(session):
    _wrap_steps()


def pytest_runtest_setup(item):
    current["test"] = item.nodeid


def _functions(path: Path) -> dict[str, set[int]]:
    """每個函式（qualname）有哪些可執行的行。"""
    out: dict[str, set[int]] = {}

    def visit(code) -> None:
        for const in code.co_consts:
            if not hasattr(const, "co_code"):
                continue
            is_class_body = const.co_flags & 0x2 == 0
            if not is_class_body:
                lines = {line for _, _, line in const.co_lines() if line}
                out.setdefault(const.co_qualname.split(".<locals>")[0], set()).update(lines)
            visit(const)

    visit(compile(path.read_text(encoding="utf-8"), str(path), "exec"))
    return out


def pytest_sessionfinish(session, exitstatus):
    sys.monitoring.set_events(TOOL, 0)
    files = {}
    for path in sorted((ROOT / "src" / "tablade").rglob("*.py")):
        seen = hits.get(str(path), set())
        functions = {}
        for qualname, lines in sorted(_functions(path).items()):
            missing = sorted(lines - seen)
            functions[qualname] = {"lines": len(lines), "missing": missing}
        files[path.relative_to(ROOT).as_posix()] = functions

    both = collections.defaultdict(set)
    for test, steps in per_test_modes.items():
        for name, modes in steps.items():
            if {"eager", "stream"} <= modes:
                both[name].add(test)

    def tidy(item, name):
        return {
            "tests": {k: sorted(v) for k, v in item["tests"].items()},
            "modes": {k: len(v) for k, v in item["modes"].items()},
            "both_modes": sorted(both.get(name, ())),
            "values": {k: sorted(v) for k, v in sorted(item["values"].items())},
            "warned": {k: sorted(v) for k, v in item["warned"].items()},
            "errors": sorted(item["errors"]),
            "describe": {k: len(v) for k, v in item["describe"].items()},
            "suggest": sorted(item["suggest"]),
        }

    OUT.write_text(
        json.dumps(
            {"files": files, "steps": {name: tidy(item, name) for name, item in usage.items()}},
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
```

</details>

執行方式（在 repo 根目錄；`<資料夾>` 是放這個檔的地方）：

```bash
PYTHONPATH=<資料夾> TABLADE_COV_OUT=<資料夾>/result.json uv run pytest -q -p tablade_cov
```

結果是一個 JSON：`files` 是每個函式沒跑到的行，`steps` 是每種步驟被哪些測試用到、用過哪些參數值。上面的表格是從這個 JSON 整理出來的。

## 檢查過、沒有發現問題的部分

### 引擎與串流

- **串流規則**：在 `src/tablade/steps/`、`src/tablade/engine/` 用 grep 找 `.over(`、`replace_strict`、`map_batches`、`map_elements`，沒有。`sort` 只有 src/tablade/steps/aggregate.py:149（彙總後的少量結果依 `__line_no` 排）。
- **stream＝eager**：tests/test_large.py 的比對都通過。另外把 4 個範例 Recipe 各用兩種模式跑一次，輸出逐列相同、警告數相同（sales_combined 兩邊都是 18 則）。不一樣的情況見上面「同一個檔經兩個階段處理」那一項。
- **警告數的已知差異**：stream 只算最後還留著的列（DESIGN.md §8、README「已知限制」都有寫）。實測：「文字轉數字」有 2 列轉不了，後面的「篩選」拿掉其中 1 列，eager 回報 2 則、stream 回報 1 則。符合文件，所以沒有列成發現；但同一份資料在預覽與匯出看到的警告數會不同，修「警告要一致」時要一起考慮。
- **多階段**：
  - 後面的階段讀的是引擎傳下來的結果，不讀 `output/`（讀程式確認；`test_later_stages_never_read_the_output_files`、`test_preview_never_reads_the_output_files` 通過）。
  - `Session.full_run` 只送 `_upstream()` 算出的階段給引擎，輸出格式固定用 Parquet，不寫處理紀錄。
- **輸出**：範例 `sales_combined` 匯出後，`output/品項對照.csv` 開頭是 BOM（`ef bb bf`），每個輸出檔在 `log/` 各有一份處理紀錄。
- **工作程序**：`load_data`、`export`、`full_run` 都在 `finally` 呼叫 `release_workers()`；實測 `load_data()` 之後工作程序是 0 個。
- **進度與用量**：
  - `StageTracker` 的 `prepare`／`trial` 兩個 phase：先試跑、略過試跑、準備檔案失敗三條路徑的狀態轉換（讀程式）。
  - 一般進度每 0.2 秒最多推一次，階段狀態不限頻率；`_push_usage` 不拿 Session 的鎖，`staging_size()` 不會為了算大小去建資料夾（讀程式）。
- **預覽**：`preview.blocked` 的三種情況（還沒載入、這一步有錯、被前面的步驟或階段擋住）、`sampled_steps`、匯出 CSV 後維持抽樣模式（讀程式；tests/test_session.py、tests/test_csv_output.py 通過）。

### 安全規則

- `src/` 裡只有 src/tablade/update.py 用到網路模組（grep `socket`、`ssl`、`http`、`urllib`、`requests`、`httpx`、`webbrowser`）。src/tablade/desktop.py 用 `subprocess` 開本機資料夾，是文件寫明的用途。沒有 `eval`／`exec`／`__import__`。
- src/tablade/recipe/validator.py 只 import 標準函式庫（`argparse`、`csv`、`json`、`re`、`sys`、`difflib`、`pathlib`、`typing`），沒有 import tablade。
- build 出來的 `frontend/dist/index.html` 的 CSP 是 `default-src 'none'; script-src 'unsafe-inline' 'unsafe-eval'; style-src 'unsafe-inline'; img-src data:`；frontend/index.html 沒有載入外部資源。
- 前端原始碼（不含 `components/ui/`）沒有 `innerHTML`、`dangerouslySetInnerHTML`、`fetch(`、`WebSocket`、`XMLHttpRequest`、`EventSource`、`new Function`、`eval(`、`localStorage`。

### 中英文切換

- **Python 的畫面狀態**：4 個範例 Recipe 各自用中文開啟並載入，再 `Session.set_language("en")`，逐一檢查每個階段的 `state()`、`catalog()`、匯出時的進度事件、`export()` 的結果、匯出後的 `state()`（完整模式）、`column_info()`、`error_report()`。剩下的中文都是資料：欄名、階段名稱、檔名、參數值，以及參數預設值（「類型」「其他」「名稱」「值」「筆數」「合併」）。
- **錯誤狀態**：故意做出有問題的 Recipe（欄位不存在、清單是空的、不支援的步驟類型、輸出檔名有路徑字元、舊寫法、版本不合、JSON 壞掉、少了 `output_format`），英文畫面下的問題清單、步驟錯誤、按鈕不能按的原因、API 的錯誤字串都是英文。例外見「`state.issues` 只在開啟 Recipe 時算一次」那一項。
- **下載的 AI skill**：畫面是中文、英文時各下載一次（`api.download_skill`，檔案對話框用假的），zip 裡 8 個檔逐檔相同；下載後畫面的語言沒有被改掉。
- **開發畫面**（`npm --prefix frontend run dev`、假資料）切成 English：頁面上的文字與 `title`／`aria-label`／`placeholder` 裡，剩下的中文都是資料；`<html lang>` 會跟著換成 `en`。例外見「英文畫面的狀態列」那一項。

### 文件

- README 寫的「25 種步驟」和 registry 一致。
- CLAUDE.md「常用指令」提到的腳本與範例都存在。

### 其他三位審查者檢查過、主要審查者沒有重跑的部分

- **步驟的 stream＝eager**：空的來源檔、只有標題的檔、全部被濾掉、多個檔欄位不一致、Excel 全部工作表（含空的工作表）、合併來源檔的順序與再合併、35 萬列的文字檔跑 18 種逐列的步驟、20 萬列跑群組彙總／樞紐／移除重複／取消樞紐／四種結合。除了上面列出的發現，輸出逐列相同、警告相同、沒有暫存欄漏出。
- **個別步驟的邊界情況**：拆欄的引號與段數、固定位置超出行尾、文字轉數字的千分位／括號負數／尾端負號／四捨五入、文字轉日期的民國年位數與不存在的日期、取代文字的三種模式、刪除／保留／改欄名、移除重複（空字串與空值不同）、結合的鍵型別與空值鍵。每種步驟的欄位效果推算出來的欄位，和 `apply()` 實際輸出的一致（「拆欄的新欄名和來源欄同名」除外）。
- **步驟的英文**：英文的步驟目錄逐欄檢查（標題、說明、選項文字都有英文，沒有全形標點）；90 組參數的 `describe()`、95 則故意造出來的錯誤與警告，英文畫面下都沒有中文。沒有在模組最上層用 `t()` 算好的常數；12 處 `i18n-ignore` 都是資料。
- **驗證器與 pydantic**：89 個手寫的 Recipe 加上約 13,000 個自動變形的 Recipe，除了上面列出的發現，兩邊結論一致。schema lock 和目前程式產生的內容相同（英文模式下也是）。
- **範例與 AI skill**：4 個範例的 `validate`、`validate --run`、`run` 都成功；skill 裡的 `validate_recipe.py` 單獨執行的結論相同；SKILL.md 約 5,200 字元、8 個檔，符合字數、檔案數、目錄深度的限制；處理紀錄裡的 SHA-256 重新計算後都相符。
- **前端**：`bridge.ts` 的 42 個方法和 `api.py` 逐一對過（參數個數、順序、回傳欄位）；`mock.ts` 和 `TabladeApi` 一致；沒有在模組最上層用 `tr()` 算的常數；前端沒有拿 Python 產生的中文做比對（進度文字的正規表示式中英文兩種前綴都對得上）。
- **CI 與腳本**：`test.yml` 會跑前端 lint 與 build、`check_no_data`、`check_code_policy`、ruff、pytest（`check_i18n` 由 tests/test_i18n.py 帶到）；`check_release.py` 對正確與錯誤的 tag 反應正確。

## 沒辦法驗證的部分

- **Windows 專屬的行為**：輸出檔被 Excel 開著時的 `PermissionError`、`msvcrt` 的鎖檔、`os.startfile`、WebView2、`GetProcessMemoryInfo`。
- **打包後的 App**：PyInstaller 的 onefile／`.app`、`multiprocessing.freeze_support()`、`scripts/build.py`。
- **真的 pywebview 視窗**：JS bridge 的實際往返、檔案對話框、`window.state` 的推送、`tablade gui --smoke`（沒有執行）。前端只在瀏覽器的開發畫面（假 API）看過。
- **幾 GB 的大檔**：DESIGN.md §8 與 README 的時間、記憶體數字沒有重新量；「大檔」的情況都是把門檻調小來模擬。
- **網路**：src/tablade/update.py 的實際連線（這次檢查不連網路）、GitHub Actions 實際執行的結果。
- **Agent Builder**：把 skill 上傳到 Microsoft 365 Copilot 實測。
- **前端要操作才看得到的行為**：有好幾項前端的發現（標成「只靠讀程式推論」的）需要在很短的時間內連續操作，或需要幾百萬列的資料，沒有在畫面上重現。
