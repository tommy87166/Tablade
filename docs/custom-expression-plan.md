# 自訂：用 Polars 的寫法算出新的一欄（實作計畫）

- 狀態：**實作計畫，尚未實作**（2026-11-20）。要決定的事都和維護者確認過了（「已決定」）。
- 範圍：新步驟 `custom_expression`（「欄」分類，畫面上叫「自訂」）、驗證器、參數表單的運算式欄位（語法高亮、插入欄位、語法選擇器）、AI skill 的說明。
- Recipe 1.2 已經隨 `v1.1.0` 發布，所以這一步要把 `RECIPE_VERSION` 升到 1.3（新增步驟，相容，升 minor），再執行 `scripts/update_schema_lock.py`。
- 寫「實測」的地方都是用 Polars 2.1.0、Python 3.13、Node 24 在 macOS 上試作量到的（試作沒有進 repo）。

## 給實作的人

- 先讀 CLAUDE.md（公開 repo 的規則、新增步驟的規則、前端規則）。這份計畫和 CLAUDE.md 不一致的地方，以 CLAUDE.md 為準，並回報。
- 照「實作步驟」的順序做，一步一個 commit；每個 commit 都要過 `pytest`、`check_no_data.py --all`、`check_code_policy.py`，改到前端時再加 lint、build 與前端的測試。
- 「語法表」是這份計畫的核心：**白名單、語法選擇器的目錄、AI skill 的說明、測試都從同一份表來**。要多加或拿掉一項，改表、補「白名單每一項的測試」那張表的一列，其他地方會跟著變。
- 計畫裡寫「實測」的行為不用重新查證；實作時發現和實測不一樣（例如 Polars 的行為不同），先停下來回報，不要自己繞過去。
- 做完後把這份文件最上面的狀態改成「已實作」，並寫下和計畫不同的地方。

## 問題

現有的步驟做不到「兩欄相加、相減」「乘上一個比例」「依條件給值」「把幾欄接成一欄」這類一列一列的計算。DESIGN.md §7.2 的未完成清單本來就有 `custom_expression`（限定用白名單語法組成，不可任意執行程式），這份計畫就是它。

## 已決定（2026-11-20 和維護者確認）

- **讀運算式，不執行程式碼**；寫法用 Polars 的 Python 寫法（不用 SQL）。
- **算出空值時產生警告，可以關**（`warn_null`，預設開著）。
- **只能新增一欄**，不能蓋掉現有的欄（欄位效果是 `add`）；要取代原本的欄時，後面再接「刪除欄」「改欄名」。
- **運算式可以讀系統欄位**（`__line_no`、`__source_file` 等）。
- **名稱**：畫面上叫「自訂」，Recipe 裡的類型是 `custom_expression`。
- **小數是精確小數，相乘時位數相加**。跟著定的細節（相除至少 6 位、`round` 與位數變少時四捨五入、位數上限 18）是照「文字轉數字」現有的做法訂的。
- **表單要有「插入欄位」與「插入語法」**：語法選擇器按下去用對話框（modal）彈開，按類別列出可以用的寫法，每一項附說明，點了就插入。
- **運算式要有語法高亮**。
- **測試**：語法表的每一項都要測到；要檢查語法表的每一項都在語法選擇器裡。

## 原則：讀運算式，不執行程式碼

DESIGN.md §4 的核心原則 1（步驟是資料，不是程式碼；不使用 `eval` / `exec` 執行使用者輸入）不變。

- 使用者輸入的是**一條** Polars 寫法的運算式，例如 `pl.col("金額") - pl.col("折扣")`。它是一段文字，存在 Recipe 裡。
- Tablade 用標準函式庫的 `ast` 把這段文字讀成語法樹，照語法表一個節點一個節點轉成 Polars 的運算式。沒有任何一段使用者的文字會被 Python 執行；不用 `eval`、`exec`、`compile`，`scripts/check_code_policy.py` 不用改。
- 不能直接執行的原因：Recipe 會互相傳，也會由 AI 撰寫。能執行程式碼的話，打開別人給的 Recipe 就等於在自己的電腦上跑對方寫的程式。
- 轉換時不替使用者算任何 Python 的運算：兩個字面值相加也是轉成 `pl.lit` 交給 Polars 算。
- 取屬性只用語法表裡的名字，不用使用者輸入的字串去取（沒有 `__class__` 這類的路）。

比較過、不採用的做法：

| 做法 | 不採用的原因 |
|---|---|
| 直接執行輸入的 Python | 違反核心原則 1 |
| SQL 運算式（`pl.sql_expr("金額 - 折扣")`） | 驗證器只能用標準函式庫，沒辦法解析 SQL；解析時會接受 `SUM(金額)`、`OVER (…)`；`NOW()` 會讓同一份 Recipe 每天結果不同 |
| 靠 Polars 的 `meta.is_row_separable()` 判斷能不能用 | 實測它把「金額 − 金額的平均」判成一列對一列，而且是標示為不穩定的 API |
| 用現成的程式碼編輯器套件（CodeMirror、Monaco）做高亮 | 多一個不小的依賴，整份 HTML 要小於 2 MB；這裡只有一條運算式，自己切詞就夠 |

## 規格

### 參數

| 參數 | 型別 | 說明 |
|---|---|---|
| `name` | 文字，必填 | 新欄位名稱 |
| `expression` | 文字，必填，最多 2,000 個字 | 一條運算式（可以換行，可以有 `#` 註解） |
| `warn_null` | 是／否，預設「是」 | 算出空值時產生警告，可以關掉 |

例子（欄名用假資料的門市銷售）：

| 要做的事 | 運算式 |
|---|---|
| 兩欄相減 | `pl.col("金額") - pl.col("折扣")` |
| 打九折，四捨五入到 2 位 | `(pl.col("金額") * 0.9).round(2)` |
| 空值當成 0 再算 | `pl.col("金額") - pl.col("折扣").fill_null(0)` |
| 依條件給值 | `pl.when(pl.col("數量") >= 10).then(pl.lit("大量")).otherwise(pl.lit("一般"))` |
| 把幾欄接起來 | `pl.concat_str([pl.col("門市代號"), pl.lit("-"), pl.col("單號")])` |
| 取年月 | `pl.col("日期").dt.to_string("%Y%m")` |

讀進來的資料一律是文字（DESIGN.md §7.2.1），數字的計算要先用「文字轉數字」；直接算會報錯（實測：文字 − 1 是 `arithmetic on dtypes str and dyn int is not allowed`），訊息要改寫成中文並提示先轉數字。

### 語法表（白名單＝語法選擇器的目錄）

放在 `recipe/validator.py`，是一個常數（清單，每一項是一個 dict）。只接受表裡有的寫法；其他的一律擋下，訊息寫出是哪一個寫法不能用。

每一項的欄位：

| 欄位 | 說明 |
|---|---|
| `id` | 這個寫法的代號（下表第一欄），整份表不重複 |
| `category` | 語法選擇器的分類（中文；英文在 `en`） |
| `insert` | 語法選擇器插入的文字；`caret` 是插入後游標在這段文字裡的位置（下表用 ▮ 標出來，實際存成數字，▮ 不在 `insert` 裡） |
| `doc` | 說明（中文；英文在 `en`） |
| `example` | 一條完整、可以執行的運算式，用下面「測試用的資料」的欄名（語法選擇器、AI skill 都會顯示；同時是測試） |
| `en` | `{"category": …, "doc": …}`：英文。`tablade/i18n.py` 的 `localize_schema` 遇到 dict 裡有 `"en"` 就會依畫面的語言換上，不用另外寫程式 |
| 檢查用的規則 | 這個寫法接受幾個參數、每個參數是哪一種（見下面）、哪些關鍵字參數可以寫、哪些一定要寫。欄位名稱與形狀由實作決定；這些不送到畫面（產生 `ui_syntax` 時拿掉） |

參數的種類：

| 種類 | 可以寫什麼 |
|---|---|
| 運算式 | 另一條運算式，或數字、`True` / `False`、`None`。**不能直接寫字串**：Polars 在這些位置把字串當成欄名（實測 `then("b")`、`pl.coalesce("a", "b")`、`pl.concat_str(["a", "b"])`、`is_between("a", "c")`、`clip("b", "y")` 都是），直接擋下並提示「文字請寫成 `pl.lit("…")`，欄位請寫成 `pl.col("…")`」。這樣整條運算式裡引用欄位的地方只有 `pl.col` |
| 值 | 運算式，或任何字面值（含字串）。Polars 在這些位置把字串當成文字（實測 `fill_null("b")`、`== "b"`、`+ "b"`） |
| 字串、整數、型別、字面值的清單 | 只能是字面值，不能是運算式；整數有範圍（負數寫成 `-2`，語法樹裡是負號加一個常數，要認得） |

**欄位與值**

| id | 插入的文字 | 參數 | 說明（`doc`） |
|---|---|---|---|
| `pl.col` | `pl.col("▮")` | 一個字串：欄名 | 欄位（用「插入欄位」比較快）。只有這個寫法可以引用欄位 |
| `pl.lit` | `pl.lit(▮)` | 一個字面值 | 固定的值。`then`、`otherwise`、`pl.concat_str` 這些地方的文字要用它包起來 |
| `lit.int` | `0` | — | 整數（int64 的範圍內） |
| `lit.decimal` | `0.0` | — | 小數：精確小數，寫幾位就是幾位。不能用科學記號 |
| `lit.str` | `"▮"` | — | 文字，用 `"…"` 或 `'…'` |
| `True` | `True` | — | 是 |
| `False` | `False` | — | 否 |
| `None` | `None` | — | 空值 |

**計算**

| id | 插入的文字 | 參數 | 說明（`doc`） |
|---|---|---|---|
| `+` | ` + ` | 兩邊：值 | 相加；兩邊都是文字時是接起來 |
| `-` | ` - ` | 兩邊：值 | 相減；兩個日期相減之後要接 `.dt.total_days()` |
| `*` | ` * ` | 兩邊：值 | 相乘。小數相乘的位數是兩邊相加 |
| `/` | ` / ` | 兩邊：值 | 相除。結果至少 6 位小數；除以 0 是空值 |
| `//` | ` // ` | 兩邊：值 | 相除取整數；除以 0 是空值 |
| `%` | ` % ` | 兩邊：值 | 相除的餘數；除以 0 是空值 |
| `neg` | `-` | 一個運算式 | 負號 |
| `abs` | `.abs()` | 沒有 | 絕對值 |
| `round` | `.round(2)` | 位數：整數 0～18；`mode=` 可以不寫，`"half_away_from_zero"`（預設）或 `"half_to_even"` | 四捨五入到幾位小數，小數的位數跟著變 |
| `floor` | `.floor()` | 沒有 | 無條件捨去到整數（往小的方向） |
| `ceil` | `.ceil()` | 沒有 | 無條件進位到整數（往大的方向） |
| `clip` | `.clip(▮, )` | 下限、上限：運算式 | 比下限小的變成下限，比上限大的變成上限 |
| `pl.max_horizontal` | `pl.max_horizontal(▮, )` | 兩個以上：運算式 | 同一列幾個值裡最大的（空值不算） |
| `pl.min_horizontal` | `pl.min_horizontal(▮, )` | 兩個以上：運算式 | 同一列幾個值裡最小的（空值不算） |
| `pl.sum_horizontal` | `pl.sum_horizontal(▮, )` | 兩個以上：運算式 | 同一列幾個值加起來（空值當 0） |

**比較與條件**

| id | 插入的文字 | 參數 | 說明（`doc`） |
|---|---|---|---|
| `==` | ` == ` | 兩邊：值 | 等於 |
| `!=` | ` != ` | 兩邊：值 | 不等於 |
| `>` | ` > ` | 兩邊：值 | 大於 |
| `>=` | ` >= ` | 兩邊：值 | 大於或等於 |
| `<` | ` < ` | 兩邊：值 | 小於 |
| `<=` | ` <= ` | 兩邊：值 | 小於或等於 |
| `&` | ` & ` | 兩邊：運算式 | 而且。兩邊的條件各自加括號 |
| `\|` | ` \| ` | 兩邊：運算式 | 或者。兩邊的條件各自加括號 |
| `~` | `~` | 一個運算式 | 相反（不是）。條件要加括號 |
| `is_in` | `.is_in([▮])` | 一個清單：字面值 | 是清單裡的其中一個 |
| `is_between` | `.is_between(▮, )` | 下限、上限：運算式 | 在下限與上限之間（含兩端） |
| `pl.when` | `pl.when(▮).then().otherwise()` | `when(條件)`、`then(值)`、`otherwise(值)`：運算式；`.then()` 後面可以再接 `.when().then()` | 依條件給值：條件成立用 `then` 的值，否則用 `otherwise` 的；沒寫 `otherwise` 是空值 |

**空值**

| id | 插入的文字 | 參數 | 說明（`doc`） |
|---|---|---|---|
| `is_null` | `.is_null()` | 沒有 | 是空值 |
| `is_not_null` | `.is_not_null()` | 沒有 | 不是空值 |
| `fill_null` | `.fill_null(▮)` | 一個值 | 空值換成指定的值 |
| `pl.coalesce` | `pl.coalesce(▮, )` | 兩個以上：運算式 | 由左到右第一個不是空值的 |

**文字**

| id | 插入的文字 | 參數 | 說明（`doc`） |
|---|---|---|---|
| `pl.concat_str` | `pl.concat_str([▮])` | 一個清單：運算式；`separator=` 可以不寫：字串 | 把幾個值接成文字（數字會先變成文字）；有一個是空值結果就是空值 |
| `str.strip_chars` | `.str.strip_chars()` | 沒有，或一個字串（要去掉的字） | 去掉前後的空白 |
| `str.strip_chars_start` | `.str.strip_chars_start()` | 同上 | 去掉前面的空白 |
| `str.strip_chars_end` | `.str.strip_chars_end()` | 同上 | 去掉後面的空白 |
| `str.to_uppercase` | `.str.to_uppercase()` | 沒有 | 英文轉大寫 |
| `str.to_lowercase` | `.str.to_lowercase()` | 沒有 | 英文轉小寫 |
| `str.len_chars` | `.str.len_chars()` | 沒有 | 有幾個字 |
| `str.slice` | `.str.slice(0, ▮)` | 起點：整數（可以是負的，從後面數）；長度：整數 0～100,000，可以不寫 | 從第幾個字開始取幾個字（第一個字是 0） |
| `str.head` | `.str.head(▮)` | 整數 0～100,000 | 前面幾個字 |
| `str.tail` | `.str.tail(▮)` | 整數 0～100,000 | 後面幾個字 |
| `str.zfill` | `.str.zfill(▮)` | 整數 0～1,000 | 前面補 0 到指定的長度 |
| `str.pad_start` | `.str.pad_start(▮, " ")` | 長度：整數 0～1,000；補的字：一個字的字串 | 前面補字到指定的長度 |
| `str.pad_end` | `.str.pad_end(▮, " ")` | 同上 | 後面補字到指定的長度 |
| `str.starts_with` | `.str.starts_with("▮")` | 一個字串 | 開頭是指定的文字 |
| `str.ends_with` | `.str.ends_with("▮")` | 一個字串 | 結尾是指定的文字 |
| `str.contains` | `.str.contains("▮", literal=True)` | 一個字串；`literal=True` 一定要寫 | 包含指定的文字 |
| `str.replace` | `.str.replace("▮", "", literal=True)` | 兩個字串；`literal=True` 一定要寫 | 第一個符合的文字換成另一段 |
| `str.replace_all` | `.str.replace_all("▮", "", literal=True)` | 兩個字串；`literal=True` 一定要寫 | 所有符合的文字換成另一段 |

**日期**

| id | 插入的文字 | 參數 | 說明（`doc`） |
|---|---|---|---|
| `pl.date` | `pl.date(2026, 1, 1)` | 年、月、日：整數的字面值，要是存在的日期 | 固定的日期 |
| `dt.year` | `.dt.year()` | 沒有 | 西元年 |
| `dt.month` | `.dt.month()` | 沒有 | 月（1～12） |
| `dt.day` | `.dt.day()` | 沒有 | 日（1～31） |
| `dt.weekday` | `.dt.weekday()` | 沒有 | 星期幾（星期一是 1，星期日是 7） |
| `dt.to_string` | `.dt.to_string("%Y/%m/%d")` | 一個字串：格式 | 日期轉成文字（`%Y` 年、`%m` 月、`%d` 日） |
| `dt.total_days` | `.dt.total_days()` | 沒有 | 兩個日期相減之後：差幾天 |

**轉型**

| id | 插入的文字 | 參數 | 說明（`doc`） |
|---|---|---|---|
| `cast` | `.cast(▮, strict=False)` | 一個型別；`strict=False` 一定要寫 | 轉成另一種型別；轉不了的是空值 |
| `pl.Int64` | `.cast(pl.Int64, strict=False)` | — | 轉成整數（小數的部分直接捨去） |
| `pl.Float64` | `.cast(pl.Float64, strict=False)` | — | 轉成浮點數 |
| `pl.String` | `.cast(pl.String, strict=False)` | — | 轉成文字 |
| `pl.Decimal` | `.cast(pl.Decimal(38, 2), strict=False)` | `pl.Decimal(38, N)`：N 是整數 0～18 | 轉成 N 位的小數；位數變少時四捨五入 |

- 分類的順序就是上面的順序；英文：欄位與值 Columns and values、計算 Arithmetic、比較與條件 Comparison and conditions、空值 Nulls、文字 Text、日期 Dates、轉型 Type conversion。
- 上表的每一項都實測過：Polars 2.1.0 有這個函式，而且把資料拆成一列一列分別算，結果和整份一起算相同。
- `pl.when` 這一項包含整串 `when(…).then(…)[.when(…).then(…)]….otherwise(…)`；`.then`、`.otherwise` 只能接在 `pl.when` 後面。

幾個要注意的地方：

- **`pl.col` 的欄名不能是 `*`，也不能是 `^` 開頭、`$` 結尾**。實測 `pl.col("*")` 會變成全部的欄、`pl.col("^金.*$")` 會被當成正規表示式。這兩種直接擋下；轉完之後再用 `meta.has_multiple_outputs()` 確認只有一欄。
- **系統欄位可以讀**（那個位置當時有哪些系統欄位，驗證器本來就會推算）。只能讀：新欄位名稱不能是系統欄位的名稱。
- **運算式可以完全不引用欄位**（例如 `pl.lit("固定")`）：結果是整欄同一個值。套用時要用 `with_columns`（實測 `select` 一個字面值只有一列）。
- **`literal=True` 一定要寫**。Polars 的 `contains`、`replace`、`replace_all` 預設把第一個參數當正規表示式；第一版只做一般文字。寫出來是為了讓這段文字的意思和它在 Polars 裡的意思一樣。
- **`cast` 一定要寫 `strict=False`**。實測嚴格轉型遇到轉不了的值，整次執行會失敗（預覽的樣本裡沒有那個值的話，匯出跑到一半才失敗）；`strict=False` 時轉不了的是空值，會有警告。
- **`cast` 不能轉成日期、是／否**：實測 Polars 不支援文字直接轉日期（`casting from string to date is not supported`）、文字轉是／否（`casting from Utf8View to Boolean not supported`）。日期用「文字轉日期」；是／否用比較（`pl.col("數量") > 0`）。
- **文字轉數字請用「文字轉數字」**：它會處理千分位、括號負數、四捨五入，轉不了的列有警告。`cast` 只認單純的數字（實測 `"1,234"` 轉成小數是空值）。
- **`pl.date` 只能寫整數的字面值**，而且要是存在的日期（驗證器用 `datetime.date(年, 月, 日)` 檢查）。實測 `pl.date(2026, 2, 30)` 是執行時才錯、整次執行失敗；用欄位組日期的話遇到不存在的日期也會，所以第一版不開放。
- **`dt.to_string` 的格式**在建立運算式時先用一個固定的日期試一次，錯的格式當場報錯（實測 `%Q` 是執行時才錯）。
- 數字參數超出範圍的例子（實測）：`zfill(-1)` 是執行時才錯，`round(-1)` 是 Python 的 `OverflowError`；所以整數參數都在解讀時檢查範圍。

### 擋下的寫法與訊息

| 寫法 | 為什麼不行 | 訊息裡建議改用 |
|---|---|---|
| `.sum()`、`.mean()`、`.min()`、`.max()`、`.count()`、`.n_unique()`、`.first()`、`.last()`、`pl.len()` | 要看整份資料；預覽是樣本，算出來會和匯出的不一樣 | 「群組彙總」，需要的話再「結合」回來 |
| `.over()` | 串流引擎不支援，會把整份資料放進記憶體 | 同上 |
| `.shift()`、`.diff()`、`.cum_sum()`、`.rank()`、`.sort()`、`.forward_fill()`、`fill_null(strategy=…)` | 要看其他列 | 「向下填滿」（只有填滿的情況） |
| `.filter()`、`.head()`、`.unique()`、`.explode()` | 列數會變 | 「篩選」「移除重複」 |
| `.map_elements()`、`.map_batches()`、`lambda` | 會執行 Python | — |
| `.replace_strict()` | 串流引擎不支援 | 「取代文字」或 `pl.when` |
| `.alias()`、`.name.…` | 欄名由 `name` 參數決定 | — |
| `pl.all()`、`pl.exclude()`、`pl.col("a", "b")`、`pl.col(pl.String)` | 一步只算一欄 | — |
| `pl.read_*`、`pl.scan_*`、`pl.sql*` 與其他 `pl.` 的函式 | 不在語法表 | — |
| `and`、`or`、`not` | 在 Polars 的運算式裡意思不對 | `&`、`\|`、`~`，兩邊加括號 |
| `1 < x < 3`、`x if 條件 else y` | 同上 | `is_between`、`pl.when` |
| 在「運算式」的位置直接寫字串（`then("大量")`） | Polars 會把它當成欄名 | `pl.lit("大量")` 或 `pl.col("欄名")` |
| `**`、`in`、`is`、`[ ]`（清單以外的用法）、f-string、comprehension、`:=`、位元組字串（`b"…"`）、科學記號（`1e-3`） | 不在語法表 | — |

不在語法表、也不在上表的寫法，訊息是「不能用 `….xxx`」加上「可以用的寫法見「插入語法」」。

### 和原本的 Polars 不一樣的地方

這幾項是 Tablade 在轉換時多做的，要寫進步驟說明、`ai_notes` 與 README。

**1. 小數是精確小數：相乘時位數相加，位數變少時四捨五入**

DESIGN.md §7.2.1 的「小數」是精確小數（`Decimal(38, N)`），「文字轉數字」預設轉成 2 位；金額、數量在這個工具裡不會是浮點數（浮點數只有群組彙總的平均）。所以這一步算完也維持精確小數。比較過三種做法，實測（金額是小數 2 位，乘 `0.9`）：

| 金額 | 真正的答案 | A：浮點數（Polars 原本的） | B：精確小數，位數照 Polars | C：精確小數，相乘時位數相加 |
|---|---|---|---|---|
| 1.15 | 1.035 | 1.035 | 1.04 | 1.035 |
| 2.35 | 2.115 | 2.115 | 2.12 | 2.115 |
| 0.07 | 0.063 | 0.06300000000000001 | 0.06 | 0.063 |
| 19.99 | 17.991 | 17.991 | 17.99 | 17.991 |
| 33.33 | 29.997 | 29.997 | 30.00 | 29.997 |
| 19.99 的一百萬列，乘完再合計 | 17,991,000 | 17,991,000.0 | **17,990,000.00（少 1,000）** | 17,991,000.000 |
| 結果的型別 | | 浮點數 | 小數 2 位 | 小數 3 位 |

**採用 C**（和維護者確認過）。規則（「整數」是 `Int64`，當成 0 位的小數；有一邊是浮點數時照 Polars，結果是浮點數）：

| 運算 | 規則 | 例子（實測） |
|---|---|---|
| 運算式裡寫的小數 | 從原始的文字讀成精確小數（`ast.get_source_segment` 取原始的文字轉 `Decimal`），寫幾位就是幾位；不經過浮點數。不能用科學記號 | `0.9` 是 1 位、`0.333` 是 3 位 |
| `+`、`-` | 照 Polars：位數取兩邊比較多的，本來就精確 | 2 位 + `0.005` → 3 位 |
| `*` | 兩邊都是小數時，先把兩邊轉成「兩邊的位數相加」的小數再乘：結果完全精確。小數 × 整數照 Polars（本來就精確）；整數 × 整數還是整數 | 2 位 × `0.9` → 3 位；2 位 × 2 位 → 4 位 |
| `/` | 被除數先轉成「兩邊的位數與 6 取最大」的小數再除；整數 ÷ 整數也是（Polars 原本是浮點數） | 1 ÷ 8 → 0.125000；2 ÷ 3 → 0.666667；1.15 ÷ 3 → 0.383333 |
| `//`、`%` | 照 Polars（小數也可以用） | 1.035 `//` 1 → 1.000；1.035 `%` 1 → 0.035 |
| `round(n)` | **四捨五入**，而且小數的結果位數變成 n 位（n 比原本的位數多時不動） | 見下面 |
| `cast(pl.Decimal(38, n), strict=False)` 讓位數變少 | 先四捨五入再轉 | 見下面 |

位數變少時的進位，實測 Polars 預設是「四捨六入五取偶數」；「文字轉數字」是四捨五入（`mode="half_away_from_zero"`），這一步跟它一致：

| 原本（3 位） | Polars 預設的 `round(2)`、`cast` 成 2 位 | 這一步的 `round(2)`、`cast` 成 2 位 |
|---|---|---|
| 1.045 | 1.04 | 1.05 |
| 2.125 | 2.12 | 2.13 |
| 0.005 | 0.00 | 0.01 |
| −1.035 | −1.04 | −1.04 |

- `round(n)` 沒有寫 `mode` 時是四捨五入；要 Polars 預設的進位就寫 `mode="half_to_even"`。小數的 `round(n)` 在 Polars 裡不會改位數（實測 3 位的 1.045 `round(2)` 還是 3 位的 1.050），這一步會接著轉成 n 位。浮點數的 `round(n)` 也是四捨五入（實測 0.125 → 0.13，Polars 預設是 0.12）。
- `cast(pl.Int64, strict=False)` 照 Polars：小數的部分直接捨去（實測 19.99 → 19、−19.99 → −19）。要四捨五入先 `round(0)`。
- 位數的上限是 18（相乘後超過就報錯，請使用者先 `round`）。實測 18 位時整數部分可以到 20 位。
- 做法：轉換 `*`、`/`、`round`、`cast` 的節點時，用 schema 查兩邊的型別（`lf.select(子運算式).collect_schema()`，不用跑資料），是整數或小數才照上面轉。

沒辦法在執行前擋的情況（實測）：相乘的結果超過 38 位數（整數部分加小數位數）時，Polars 讓整次執行失敗（`overflow in decimal multiplication`）；轉成位數比較多的小數時放不下，結果是空值、會有警告。實際的金額不會這麼大（要 20 位以上的整數）。

**2. 除以 0 是空值**

實測 Polars 原本的結果依型別不同，其中小數除以 0 會讓整次執行失敗：

| 運算 | Polars 原本 | 這一步 |
|---|---|---|
| 小數 ÷ 0 | 整次執行失敗（`division by zero Decimal`） | 空值 |
| 整數 `/` 0 | 無限大（`inf`） | 空值 |
| 整數 `//` 0、`%` 0 | 空值 | 空值 |

做法：`/`、`//`、`%` 的除數包成 `pl.when(除數 != 0).then(除數)`（除數是 0 時變成空值，任何數除以空值是空值）。實測小數、整數、浮點數互相除的七種組合都對。不能寫成「`pl.when(除數 == 0).then(None).otherwise(左 / 右)`」：兩邊都會算，一樣會失敗。

**3. 結果的型別只會是 DESIGN.md §7.2.1 的那幾種**

| 算出來的型別（實測的例子） | 處理 |
|---|---|
| `UInt32`（`str.len_chars()`）、`Int32`（`dt.year()`）、`Int8`（`dt.month()`、`dt.day()`、`dt.weekday()`） | 轉成 `Int64`（整數） |
| `Float32` | 轉成 `Float64`（浮點數） |
| `Boolean`（比較、`is_null()`） | 維持。型別分頁已經認得「是／否」，這會是第一個產生它的步驟 |
| `Duration`（兩個日期相減）與其他型別 | 報錯，提示改用 `.dt.total_days()` |

### 錯誤與警告

| 錯誤 | 什麼時候 | 在哪裡 |
|---|---|---|
| 語法錯、用了語法表以外的寫法、參數的種類或範圍不對、太長 | 寫 Recipe 時 | 驗證器（AI skill 也有）與 pydantic，同一份檢查 |
| 引用的欄不存在；新欄位名稱和現有的欄或系統欄位重複 | 寫 Recipe 時 | 驗證器的欄位檢查（給相近的欄名） |
| 型別不對（文字做加減）、結果的型別不支援、小數的位數超過 18、`to_string` 的格式錯 | 套用步驟時（不用跑資料） | `apply()` 裡看 schema，包成 `StepError`（中英文） |
| 轉型失敗、除以 0 | 執行時 | 不會失敗：結果是空值，產生警告 |

警告（`warn_null` 開著時）：結果是空值（或無限大、NaN），而運算式用到的欄在那一列都有值，就產生一則警告「這一列算出來是空值」，點警告可以跳到那一列。`pl.when` 沒有寫 `.otherwise()` 的列也會是空值；那是故意的話把 `warn_null` 關掉。運算式沒有引用任何欄時不檢查。

### 防止很大的輸入

- 上限：2,000 個字；語法樹最多 500 個節點、最深 50 層；整數字面值要在 int64 的範圍內。
- 實測 `ast.parse` 的反應：300 層括號是 `SyntaxError`、20,000 個 `+` 是 `RecursionError`、有空字元是 `SyntaxError`。三種都接住，回報「運算式太長或太複雜」。先檢查長度再解析；走語法樹時自己數深度，不靠 Python 的遞迴上限。

## 畫面

參數表單由 schema 自動產生，不為這個步驟手寫表單。新增一種欄位類型 `"ui": "expression"`，它需要的資料都放在 schema 裡：

```python
expression: str = Field(
    title="運算式",
    min_length=1,
    max_length=2000,
    json_schema_extra={"ui": "expression", "ui_syntax": syntax_catalog(), **en("Expression")},
)
```

`syntax_catalog()` 從驗證器的語法表產生給畫面的那幾個欄位（`id`、`category`、`insert`、`caret`、`doc`、`example`、`en`）。畫面上的函式名稱、分類、說明**全部從這裡來**，前端的程式裡不寫死任何一個函式的名字（有測試檢查）。

欄位由上到下：

1. 標題「運算式」。
2. 一列按鈕：**「插入欄位」**（下拉選單）、**「插入語法…」**（開對話框）。
3. 有語法高亮的編輯框。
4. Python 回傳的錯誤（現有的 `FieldErrors`）。
5. 一行說明與一個例子。

### 語法高亮

- 編輯框還是原生的 `<textarea>`（中文輸入法、複製貼上、復原都照原本的）。做法是兩層疊在一起：下面一層 `<pre aria-hidden>` 把切好的每一段畫成有顏色的 `<span>`（React 的元素，不用 `innerHTML`）；上面一層是 `textarea`，文字透明、游標看得見（`color: transparent; caret-color: …`），背景透明。
- 兩層的字型（等寬）、字級、行高、內距、邊框寬度、換行方式（`white-space: pre-wrap; overflow-wrap: break-word`）要完全一樣，不然游標和字會對不上。文字最後是換行時，`<pre>` 要多補一個字元才會一樣高。
- 編輯框的高度跟著內容長高（最少 3 行、最多約 12 行）；超過時出現捲軸，`textarea` 捲動時 `<pre>` 跟著捲。
- 關掉拼字檢查、自動大寫、自動修正（`spellCheck={false}` 等）。
- 切詞是純函式（新檔 `frontend/src/lib/expression.ts`，沒有任何 import，測試可以直接跑）：

  | 種類 | 是什麼 | 顏色 |
  |---|---|---|
  | `function` | 語法表裡的函式、方法、型別（`pl.` 後面的、`.` 後面的、`.str.` / `.dt.` 後面的） | 主色 |
  | `unknown` | 不在語法表裡的名稱（例如 `.sum`、`pl.read_csv`、`os`） | 錯誤的顏色＋底線 |
  | `namespace` | `pl` | 和 `function` 同色 |
  | `column` | `pl.col(` 後面的字串 | 欄位的顏色（和一般的字串不同） |
  | `error` | `pl.col("…")` 的欄不在這一步的輸入裡；沒有關的引號；認不得的字元（例如全形的括號） | 錯誤的顏色＋底線 |
  | `string`、`number`、`keyword`（`True`、`False`、`None`）、`comment` | | 各一種顏色；註解用次要的文字色 |
  | `operator`、`punct`、`space` | | 一般的文字色 |

  - 每一段的文字接回去要和原本的文字完全一樣（一個字都不能少）。
  - 語法表的名稱由參數傳進來（從 `ui_syntax` 的 `id` 整理出來：`pl.xxx`、`str.xxx`、`dt.xxx`、其他的方法），切詞的程式裡不寫死。
  - 顏色用 `src/index.css` 現有的變數（`--primary`、`--destructive`、`--muted-foreground`…），不夠的在 `index.css` 加，淺色與深色（`.dark`）都要定義。
- 高亮只是幫忙看：對不對以 Python 的驗證為準（草稿停止輸入 350 毫秒後送出，照現在的流程）。高亮標成 `unknown` 的，Python 也一定會擋；反過來不一定（例如參數個數不對，高亮看不出來）。
- 試作（實測）：切詞函式約 70 行；`node --test` 可以直接跑 TypeScript 的測試檔，不用裝任何套件（Node 24；CI 也是 Node 24）。

### 插入欄位

- 下拉選單列出這一步輸入的欄：資料欄在前，系統欄位在後（分兩組）。
- 選一欄就把 `pl.col("欄名")` 插到編輯框裡游標的位置（有選取文字時取代選取的部分）；編輯框沒有被點過時接在最後面。插入後焦點回到編輯框，游標在插入的文字後面。
- 欄名裡的 `\` 與 `"` 要加反斜線（`quote()`，在 `expression.ts`）。
- 系統欄位的清單：步驟的狀態多一個 `input_system_columns`（`session.py` 組 `steps` 的地方，和 `input_columns` 放一起；`bridge.ts` 的 `StepState` 加型別；`scripts/gen_frontend_dev_data.py` 產生的假資料會跟著有）。表單把它經 `FieldProps` 傳給這個欄位。

### 語法選擇器

- 按「插入語法…」開對話框（`components/ui/dialog.tsx`，現有的），標題「插入語法」。
- 左邊是分類（照語法表的順序，共 7 類），右邊是那一類的項目；上面有搜尋框，輸入文字時跨分類找（比對 `insert`、`doc`、`example`，不分大小寫）。
- 每一項顯示三樣：寫法（`insert`，等寬字、有高亮）、說明（`doc`）、例子（`example`，等寬字、次要的文字色）。
- 點一項（或鍵盤選到後按 Enter）就插入、關掉對話框、焦點回到編輯框。插入的位置是開對話框之前編輯框裡游標的位置（開之前先記下來，因為焦點會跑到對話框）；游標放在 `caret` 指的位置（例如 `.fill_null(▮)` 的括號裡）。
- 按 Esc 或點外面關掉，不插入。
- 全部的文字用 `tr('中文', 'English')`；語法表的文字由 Python 依語言換好（`localize_schema`），前端直接顯示。
- 新元件放在 `frontend/src/components/form/`（例如 `ExpressionField.tsx`、`SyntaxPicker.tsx`），`fields.tsx` 的 `control()` 加一個 `case 'expression'`，`lib/schema.ts` 的 `FieldKind`、`fieldKind()` 與 `bridge.ts` 的 `JsonSchema`（`ui` 的清單、`ui_syntax` 的型別）跟著加。

## 實作步驟

一步一個 commit。

1. **語法表與檢查（`recipe/validator.py`）**
   - 語法表的常數（上面那張表的 69 項，含 `doc`、`example` 與英文）。
   - `ExpressionScan(text)`（做法比照現有的 `RegexScan`）：檢查長度 → `ast.parse(text, mode="eval")` → 走一遍語法樹，得到 `error`（第一個問題，中英文用驗證器自己的 `t()`）與 `columns`（引用到的欄名，依出現的順序）。
   - `step_rules`：`custom_expression` 的 `expression` 有 `error` 時回報在 `…steps[i].expression`。
   - `_semantic` 的欄位引用（現在只認 `ui: column` 等標記）：加上 `ExpressionScan.columns`，不存在的欄給相近的欄名；這裡的引用可以是系統欄位（和條件裡的引用一樣）。
   - 驗證器只能用標準函式庫、不能 import tablade（`ast`、`datetime`、`decimal` 都是標準函式庫）。AI skill 的沙箱是哪一版 Python 還不知道（DESIGN.md §15），所以只用 `ast.Constant` 這類 3.8 就有的東西，不用 `match`。
   - 測試：「測試」的第 3、4、6 節。
2. **步驟（新檔 `steps/custom.py`）**
   - `CustomExpression`：`type`、`name`（`NonEmpty`）、`expression`、`warn_null`；`label = "自訂"`、`label_en = "Custom"`、`category = "欄"`、`effect = {"kind": "add", "names": "$name"}`、`per_file = False`、docstring 與 `doc_en`。
   - `field_validator("expression")`：呼叫 `ExpressionScan`，有 `error` 就丟出同樣的訊息（`tests/test_review_recipe.py` 檢查兩邊一致）。
   - `build(tree, lf) -> pl.Expr`：照語法樹轉成 Polars 的運算式，用驗證器的語法表決定能呼叫什麼；小數、除以 0、型別收斂照「和原本的 Polars 不一樣的地方」。
   - `apply()`：純函式。確認引用的欄存在（現有的 `missing_columns`）→ `build` → 看型別（Polars 的錯誤包成 `StepError`）→ `lf.with_columns(expr.alias(self.name)).select(self.output_order(lf))`。不排序、不用 `.over()`、不呼叫 Python。
   - `describe()`：「新增「毛利」＝ `pl.col("金額") - pl.col("成本")`」；運算式超過 60 個字就截斷加「…」。
   - `suggest()`：從資料格右鍵某一欄新增時，運算式帶入 `pl.col("那一欄")`。
   - `warning_checks()`：「錯誤與警告」那一節。
   - `ai_notes`（維持中文）：只能一列一列算、要合計用「群組彙總」；數字先用「文字轉數字」；欄位只能用 `pl.col`、文字在 `then` 等位置要用 `pl.lit`；和 Polars 不一樣的地方（小數是精確小數、`round` 是四捨五入、除以 0 是空值）；`literal=True`、`strict=False` 一定要寫。`examples` 至少三個（相減、依條件給值、接文字）。
   - `syntax_catalog()` 與 schema 的 `ui_syntax`。
   - 加進 `steps/__init__.py`，放在「欄」分類的最後（匯入順序＝顯示順序）。
   - `RECIPE_VERSION` 升到 1.3，執行 `scripts/update_schema_lock.py`。之後改語法表（含說明文字）都要重跑一次；多一項或少一項寫法算結構有變，要升版號。
   - 測試：「測試」的第 1、2、5、7 節。
3. **AI skill 與文件**
   - `skill.py`：步驟目錄會自動帶 `ai_notes` 與 `examples`。另外從語法表產生一段「自訂的運算式可以用的寫法」（每一項的寫法、參數、說明、例子），放在 `references/steps.md`（不放 `SKILL.md`，它有字數上限）。
   - DESIGN.md：§4 核心原則 1 補一句（運算式是解讀、不是執行）；§7.2 把 `custom_expression` 從未完成移到已完成；§7.2.1 的型別表（「是／否」「浮點數」「小數」多一個來源）；§16.5 的欄位類型；§17.4 驗證器。
   - README 兩份（英文、繁體中文，內容同步）：步驟清單與可用的寫法。
   - `docs/i18n-plan.md` 用語表：「自訂／Custom」「運算式／Expression」「插入欄位／Insert column」「插入語法／Insert syntax」與七個分類。
   - CLAUDE.md：補「語法表要加一項時的規則」（下面）與前端測試的指令。
4. **前端：切詞與插入（`frontend/src/lib/expression.ts`）**
   - `tokenize(text, vocabulary, columns)`、`vocabulary(ui_syntax)`、`quote(name)`、`insertAt(text, start, end, snippet, caret)`（回傳新的文字與游標位置）。純函式、沒有 import。
   - 測試的設定和 docs/cell-view-plan.md 共用：先做的那一邊加好，後做的只加測試檔。
   - 測試檔放在 `frontend/tests/`（在 `src` 外面，`tsc -b` 不會編到它）；`package.json` 加 `"test": "node --test tests/"`（不加依賴）；`.github/workflows/test.yml` 的前端那一步加 `npm test`。
   - 測試：「測試」的第 8 節。
5. **前端：運算式欄位**
   - `input_system_columns`（`session.py`、`bridge.ts`）。
   - `ExpressionField`（高亮的編輯框、插入欄位）、`SyntaxPicker`（對話框），接進 `fields.tsx`、`lib/schema.ts`、`bridge.ts`。
   - 測試：「測試」的第 9、10 節（含用瀏覽器實際操作）。
6. **收尾**：全部的檢查再跑一次；在真正的視窗（`uv run tablade`）用假資料走一遍；把這份文件的狀態改成已實作。

### 之後要在語法表加一項時

每加一項都要符合，並由測試檢查：

1. 一列對一列：把資料拆成一列一列分別算，結果和整份一起算相同。
2. 串流引擎支援（不會退回整份放進記憶體）。
3. 不讀寫檔、不連網路、不執行 Python。
4. 會在執行時失敗的參數（負的長度、嚴格轉型）要在解讀時擋掉或改成不會失敗的寫法。
5. 結果的型別在 DESIGN.md §7.2.1 的範圍內，或在收斂型別那裡處理。
6. 有 `category`、`insert`、`doc`、`example` 與英文；「白名單每一項的測試」多一列。

## 測試

Python 的測試放在新檔 `tests/test_custom_expression.py`（步驟的邊界情況照慣例也在 `tests/test_steps.py` 補）。

### 測試用的資料

四列，欄名用門市銷售；加進 `src/tablade/fixtures.py`（例如 `expression_rows()`），不要在測試裡另外寫一份。語法表每一項的 `example` 也用這些欄名。

| 門市（文字） | 代號（文字） | 數量（整數） | 單價（小數 2 位） | 折扣（小數 2 位） | 日期 | 評分（浮點數） | `__line_no` |
|---|---|---|---|---|---|---|---|
| 北區門市 | a1 | 3 | 12.50 | 0.00 | 2026-01-05 | 0.125 | 2 |
| `" 南區 "`（前後各一個空白） | B22 | 10 | 120.00 | 10.05 | 2026-02-28 | 2.5 | 3 |
| 東區 | c | 0 | 19.99 | 0.55 | 2026-12-31 | −2.5 | 4 |
| 空 | 空 | 空 | 空 | 空 | 空 | 空 | 5 |

### 1. 白名單每一項的測試

下表的每一列是一個測試（`pytest.mark.parametrize`）：把運算式交給步驟，結果的值與型別要和表裡的一樣。表裡的預期結果是照這份計畫的規則、用 Polars 2.1.0 實際算出來的（81 列，涵蓋語法表全部 69 項）。

每一列另外檢查兩件事：

- **一列對一列**：把資料拆成一列一列分別算，結果和整份算相同。
- **串流＝eager**：用串流引擎與一般引擎各算一次，結果相同。

**運算子**

| # | 測試的重點 | 運算式 | 預期結果（四列） | 型別 |
|---|---|---|---|---|
| 1 | `+` | `pl.col("單價") + pl.col("折扣")` | 12.50、130.05、20.54、空 | 小數 2 位 |
| 2 | `+`（文字相接） | `pl.col("代號") + "-x"` | a1-x、B22-x、c-x、空 | 文字 |
| 3 | `-` | `pl.col("單價") - pl.col("折扣")` | 12.50、109.95、19.44、空 | 小數 2 位 |
| 4 | `*`（小數 × 整數） | `pl.col("單價") * pl.col("數量")` | 37.50、1200.00、0.00、空 | 小數 2 位 |
| 5 | `*`（小數 × 寫的小數） | `pl.col("單價") * 0.9` | 11.250、108.000、17.991、空 | 小數 3 位 |
| 6 | `*`（小數 × 小數） | `pl.col("單價") * pl.col("折扣")` | 0.0000、1206.0000、10.9945、空 | 小數 4 位 |
| 7 | `*`（有一邊是浮點數） | `pl.col("評分") * 2` | 0.25、5.0、-5.0、空 | 浮點數 |
| 8 | `/`（小數 ÷ 整數，除數有 0） | `pl.col("單價") / pl.col("數量")` | 4.166667、12.000000、空、空 | 小數 6 位 |
| 9 | `/`（整數 ÷ 整數） | `pl.col("數量") / 4` | 0.750000、2.500000、0.000000、空 | 小數 6 位 |
| 10 | `//` | `pl.col("數量") // 4` | 0、2、0、空 | 整數 |
| 11 | `%` | `pl.col("數量") % 4` | 3、2、0、空 | 整數 |
| 12 | `%`（除數有 0） | `10 % pl.col("數量")` | 1、0、空、空 | 整數 |
| 13 | 一元 `-` | `-pl.col("折扣")` | 0.00、-10.05、-0.55、空 | 小數 2 位 |
| 14 | `==` | `pl.col("數量") == 3` | 是、否、否、空 | 是／否 |
| 15 | `!=` | `pl.col("數量") != 3` | 否、是、是、空 | 是／否 |
| 16 | `>` | `pl.col("數量") > 3` | 否、是、否、空 | 是／否 |
| 17 | `>=` | `pl.col("數量") >= 3` | 是、是、否、空 | 是／否 |
| 18 | `<` | `pl.col("數量") < 3` | 否、否、是、空 | 是／否 |
| 19 | `<=` | `pl.col("數量") <= 3` | 是、否、是、空 | 是／否 |
| 20 | `&` | `(pl.col("數量") > 1) & (pl.col("單價") > 15)` | 否、是、否、空 | 是／否 |
| 21 | `\|` | `(pl.col("數量") > 5) \| pl.col("門市").is_null()` | 否、是、否、是 | 是／否 |
| 22 | `~` | `~(pl.col("數量") > 1)` | 否、否、是、空 | 是／否 |

**欄位與字面值**

| # | 測試的重點 | 運算式 | 預期結果（四列） | 型別 |
|---|---|---|---|---|
| 23 | `pl.col` | `pl.col("數量")` | 3、10、0、空 | 整數 |
| 24 | `pl.col`（系統欄位） | `pl.col("__line_no") * 10` | 20、30、40、50 | 整數 |
| 25 | `pl.lit`（文字） | `pl.lit("固定")` | 固定、固定、固定、固定 | 文字 |
| 26 | `pl.lit`（小數） | `pl.lit(1.5)` | 1.5、1.5、1.5、1.5 | 小數 1 位 |
| 27 | 整數 | `pl.col("數量") + 1` | 4、11、1、空 | 整數 |
| 28 | `False` | `pl.col("門市").is_null() == False` | 是、是、是、否 | 是／否 |
| 29 | `True` | `pl.lit(True)` | 是、是、是、是 | 是／否 |
| 30 | `None` | `pl.lit(None)` | 空、空、空、空 | 空 |
| 31 | `pl.date` | `pl.col("日期") >= pl.date(2026, 2, 1)` | 否、是、是、空 | 是／否 |

**pl. 的函式**

| # | 測試的重點 | 運算式 | 預期結果（四列） | 型別 |
|---|---|---|---|---|
| 32 | `pl.when` … `.then` … `.otherwise` | `pl.when(pl.col("數量") >= 10).then(pl.lit("大量")).when(pl.col("數量") >= 1).then(pl.lit("一般")).otherwise(pl.lit("無"))` | 一般、大量、無、無 | 文字 |
| 33 | `pl.when`（沒有 `otherwise`） | `pl.when(pl.col("數量") >= 10).then(pl.col("單價"))` | 空、120.00、空、空 | 小數 2 位 |
| 34 | `pl.concat_str` | `pl.concat_str([pl.col("代號"), pl.lit("-"), pl.col("數量")])` | a1-3、B22-10、c-0、空 | 文字 |
| 35 | `pl.concat_str`（`separator`） | `pl.concat_str([pl.col("代號"), pl.col("數量")], separator="/")` | a1/3、B22/10、c/0、空 | 文字 |
| 36 | `pl.coalesce` | `pl.coalesce(pl.col("折扣"), 0)` | 0.00、10.05、0.55、0.00 | 小數 2 位 |
| 37 | `pl.max_horizontal` | `pl.max_horizontal(pl.col("單價"), 20)` | 20.00、120.00、20.00、20.00 | 小數 2 位 |
| 38 | `pl.min_horizontal` | `pl.min_horizontal(pl.col("單價"), 20)` | 12.50、20.00、19.99、20.00 | 小數 2 位 |
| 39 | `pl.sum_horizontal` | `pl.sum_horizontal(pl.col("單價"), pl.col("折扣"))` | 12.50、130.05、20.54、0.00 | 小數 2 位 |

**一般的方法**

| # | 測試的重點 | 運算式 | 預期結果（四列） | 型別 |
|---|---|---|---|---|
| 40 | `fill_null` | `pl.col("數量").fill_null(0)` | 3、10、0、0 | 整數 |
| 41 | `is_null` | `pl.col("門市").is_null()` | 否、否、否、是 | 是／否 |
| 42 | `is_not_null` | `pl.col("門市").is_not_null()` | 是、是、是、否 | 是／否 |
| 43 | `is_in` | `pl.col("代號").is_in(["a1", "c"])` | 是、否、是、空 | 是／否 |
| 44 | `is_between` | `pl.col("數量").is_between(1, 5)` | 是、否、否、空 | 是／否 |
| 45 | `abs` | `(pl.col("折扣") - 1).abs()` | 1.00、9.05、0.45、空 | 小數 2 位 |
| 46 | `round`（小數：四捨五入、位數變成 2） | `(pl.col("單價") * 0.333).round(2)` | 4.16、39.96、6.66、空 | 小數 2 位 |
| 47 | `round`（浮點數：四捨五入） | `pl.col("評分").round(2)` | 0.13、2.5、-2.5、空 | 浮點數 |
| 48 | `round`（`mode="half_to_even"`） | `pl.col("評分").round(2, mode="half_to_even")` | 0.12、2.5、-2.5、空 | 浮點數 |
| 49 | `floor` | `pl.col("單價").floor()` | 12.00、120.00、19.00、空 | 小數 2 位 |
| 50 | `ceil` | `pl.col("單價").ceil()` | 13.00、120.00、20.00、空 | 小數 2 位 |
| 51 | `clip` | `pl.col("數量").clip(1, 5)` | 3、5、1、空 | 整數 |
| 52 | `cast`（`pl.Int64`：無條件捨去） | `pl.col("單價").cast(pl.Int64, strict=False)` | 12、120、19、空 | 整數 |
| 53 | `cast`（`pl.Int64`：轉不了的是空值） | `pl.col("代號").cast(pl.Int64, strict=False)` | 空、空、空、空 | 整數 |
| 54 | `cast`（`pl.Float64`） | `pl.col("數量").cast(pl.Float64, strict=False)` | 3.0、10.0、0.0、空 | 浮點數 |
| 55 | `cast`（`pl.String`） | `pl.col("單價").cast(pl.String, strict=False)` | 12.50、120.00、19.99、空 | 文字 |
| 56 | `cast`（`pl.Decimal(38, 1)`：四捨五入） | `pl.col("單價").cast(pl.Decimal(38, 1), strict=False)` | 12.5、120.0、20.0、空 | 小數 1 位 |
| 57 | `cast`（`pl.Decimal(38, 4)`：位數變多） | `pl.col("數量").cast(pl.Decimal(38, 4), strict=False)` | 3.0000、10.0000、0.0000、空 | 小數 4 位 |

**`.str.`**

| # | 測試的重點 | 運算式 | 預期結果（四列） | 型別 |
|---|---|---|---|---|
| 58 | `strip_chars` | `pl.col("門市").str.strip_chars()` | 北區門市、南區、東區、空 | 文字 |
| 59 | `strip_chars_start` | `pl.col("門市").str.strip_chars_start()` | 北區門市、`"南區 "`、東區、空 | 文字 |
| 60 | `strip_chars_end` | `pl.col("門市").str.strip_chars_end()` | 北區門市、`" 南區"`、東區、空 | 文字 |
| 61 | `to_uppercase` | `pl.col("代號").str.to_uppercase()` | A1、B22、C、空 | 文字 |
| 62 | `to_lowercase` | `pl.col("代號").str.to_lowercase()` | a1、b22、c、空 | 文字 |
| 63 | `len_chars` | `pl.col("門市").str.len_chars()` | 4、4、2、空 | 整數 |
| 64 | `slice` | `pl.col("門市").str.slice(0, 2)` | 北區、`" 南"`、東區、空 | 文字 |
| 65 | `slice`（只寫起點） | `pl.col("門市").str.slice(2)` | 門市、`"區 "`、`""`、空 | 文字 |
| 66 | `head` | `pl.col("代號").str.head(1)` | a、B、c、空 | 文字 |
| 67 | `tail` | `pl.col("代號").str.tail(1)` | 1、2、c、空 | 文字 |
| 68 | `zfill` | `pl.col("代號").str.zfill(4)` | 00a1、0B22、000c、空 | 文字 |
| 69 | `pad_start` | `pl.col("代號").str.pad_start(4, "*")` | **a1、*B22、***c、空 | 文字 |
| 70 | `pad_end` | `pl.col("代號").str.pad_end(4, "*")` | a1**、B22*、c***、空 | 文字 |
| 71 | `starts_with` | `pl.col("門市").str.starts_with("北")` | 是、否、否、空 | 是／否 |
| 72 | `ends_with` | `pl.col("門市").str.ends_with("門市")` | 是、否、否、空 | 是／否 |
| 73 | `contains` | `pl.col("門市").str.contains("區", literal=True)` | 是、是、是、空 | 是／否 |
| 74 | `replace` | `pl.col("門市").str.replace(" ", "", literal=True)` | 北區門市、`"南區 "`、東區、空 | 文字 |
| 75 | `replace_all` | `pl.col("門市").str.replace_all(" ", "", literal=True)` | 北區門市、南區、東區、空 | 文字 |

**`.dt.`**

| # | 測試的重點 | 運算式 | 預期結果（四列） | 型別 |
|---|---|---|---|---|
| 76 | `year` | `pl.col("日期").dt.year()` | 2026、2026、2026、空 | 整數 |
| 77 | `month` | `pl.col("日期").dt.month()` | 1、2、12、空 | 整數 |
| 78 | `day` | `pl.col("日期").dt.day()` | 5、28、31、空 | 整數 |
| 79 | `weekday`（星期一是 1） | `pl.col("日期").dt.weekday()` | 1、6、4、空 | 整數 |
| 80 | `to_string` | `pl.col("日期").dt.to_string("%Y/%m")` | 2026/01、2026/02、2026/12、空 | 文字 |
| 81 | `total_days` | `(pl.col("日期") - pl.date(2026, 1, 1)).dt.total_days()` | 4、58、364、空 | 整數 |

- 「是／否」那幾欄的「是」「否」是 `True`、`False`，「空」是空值；有引號的是含空白或空字串的文字。
- 第 37、38 列：`max_horizontal`、`min_horizontal` 不算空值，所以最後一列是 20.00；第 39 列 `sum_horizontal` 把空值當 0。這是 Polars 的行為，說明裡有寫。
- 第 53 列（文字轉整數全部失敗）要另外檢查：`warn_null` 開著時前三列各有一則警告，第四列（本來就是空值）沒有。

**每一項都要測到**（另一個測試，不是靠人看）：測試檔裡這張表的每一列標明它測的是語法表的哪些 `id`；有一個測試把語法表的 `id` 全部列出來，任何一個沒有被標到就失敗。之後在語法表加一項而沒有補測試，這個測試會失敗。

### 2. 語法選擇器的完整性

- 語法表的每一項都有 `category`（在七個分類裡）、`insert`、`doc`、`example`、英文的 `category` 與 `doc`；都不是空的；`caret` 在 0 與 `insert` 的長度之間。
- `id` 不重複；`insert` 不重複。
- **schema 裡 `expression` 的 `ui_syntax` 的 `id`，和語法表的 `id` 完全一樣（一項不多、一項不少），順序也一樣**。這就是「白名單的都有在語法選擇器裡」。
- 每一項的 `example`：通過 `ExpressionScan`、可以用測試用的資料算出來不出錯，而且真的用到那一項（解讀時記下用到的 `id`，那一項的 `example` 用到的 `id` 要包含它自己）。
- 每一項的 `insert` 裡出現的名稱（`pl.` 後面的、`.` 後面的）都是語法表裡的，而且包含那一項自己的名稱：避免選擇器插入的是不能用的寫法。`example` 可以直接用下面測試表裡的運算式。
- 畫面的語言切成英文時，`localize_schema` 之後每一項的 `category` 與 `doc` 是英文、沒有 `en` 這個鍵；`strip_en` 之後（schema lock、AI skill）是中文。
- 反過來的方向：`build()` 能呼叫的 Polars 函式只來自語法表。測試：把語法表的某一項暫時拿掉，原本用到它的運算式就被擋下（挑三項：一個 `pl.` 的函式、一個方法、一個 `.str.` 的方法）。

### 3. 擋下的寫法

每一項都要被擋下，訊息有寫出是哪一個寫法，而且驗證器與 pydantic 的訊息相同：

- 「擋下的寫法與訊息」那張表的每一個寫法（表裡用頓號分開的每一個都算一項）；有建議的，訊息裡要有那個建議。
- 執行程式的嘗試：`__import__("os").system("echo hi")`、`open("x").read()`、`pl.col("a").__class__`、`pl.col("a").__class__.__mro__`、`getattr(pl, "read_csv")("x")`、`pl.read_csv("x")`、`pl.scan_csv("x")`、`pl.SQLContext()`、`eval("1")`、`exec("1")`、`(lambda: 1)()`、`[x for x in range(3)]`、`f"{1}"`、`(a := 1)`、`import os`、兩行各一條運算式。
- 名稱：`polars.col("a")`、`col("a")`、`x`（`pl` 以外的名稱）。
- `pl.col`：`pl.col("*")`、`pl.col("^金.*$")`、`pl.col("a", "b")`、`pl.col(["a"])`、`pl.col(pl.String)`、`pl.col(1)`、`pl.col()`。
- 參數：個數不對（`.round()`、`.round(1, 2, 3)`）；種類不對（`.round("2")`、`.str.head(pl.col("a"))`、`.cast("Int64", strict=False)`）；超出範圍（`.round(19)`、`.round(-1)`、`.str.zfill(-1)`、`.str.zfill(1001)`、`pl.Decimal(38, 19)`、`pl.Decimal(10, 2)`）；不認得的關鍵字（`.round(2, foo=1)`）；少了一定要寫的（`.cast(pl.Int64)`、`.cast(pl.Int64, strict=True)`、`.str.contains("a")`、`.str.contains("a", literal=False)`）；`mode` 不是那兩個值。
- 在「運算式」的位置直接寫字串：`pl.when(…).then("大量")`、`.otherwise("一般")`、`pl.coalesce("a", "b")`、`pl.concat_str(["a", "b"])`、`.is_between("a", "c")`、`.clip("b", "y")`、`pl.max_horizontal("a", "b")`；訊息提示 `pl.lit` 與 `pl.col`。
- `pl.date`：`pl.date(2026, 2, 30)`、`pl.date(2026, 13, 1)`、`pl.date(pl.col("年"), 1, 1)`、`pl.date(2026, 1)`。
- 字面值：超過 int64 的整數、`1e-3`、`b"x"`、`1j`、`...`。
- 打錯字的：全形的括號與引號（`pl.col（“a”）`）、少一個括號、空的運算式、只有空白、只有註解。
- 很大的輸入：超過 2,000 個字、300 層括號、很長的一串 `-`、很長的一串 `+`、有空字元、超過 500 個節點、超過 50 層：都是一般的錯誤訊息，不會有沒接住的例外（含 `RecursionError`、`MemoryError`）。

### 4. 可以用、容易誤擋的寫法

- 換行與註解：運算式分成好幾行（用括號包起來）、行尾有 `#` 註解。
- 字串：單引號、雙引號、三引號、字串裡有引號與反斜線、中文、表情符號；欄名有空白、有 `.`、有引號。
- 負數的字面值：`-1`、`.str.slice(-2)`、`.clip(-5, 5)`。
- 括號多包幾層、運算子的優先順序（`a + b * c` 與 `(a + b) * c` 結果不同；`~a & b` 與 `~(a & b)`）。
- 巢狀：`pl.when` 裡面再一個 `pl.when`；`fill_null` 的參數是另一條運算式；`pl.concat_str` 的清單裡有運算式。
- 不引用任何欄的運算式（`pl.lit("固定")`、`1 + 1`）。

### 5. 和 Polars 不一樣的地方

- **精確小數**：三種做法那張表的 C 欄每一列（含一百萬列的合計）；規則表的每一列（`+ - * / // %`；小數與整數、小數與小數、整數與整數、有一邊是浮點數）；`round` 與 `cast` 位數變少的四個例子、`mode="half_to_even"`；`round(n)` 的 n 比原本的位數多時不動；位數相加超過 18 報錯；轉成位數多的小數放不下時是空值、有警告。
- **除以 0**：小數、整數、浮點數各自的 `/`、`//`、`%`；除數是欄、是字面值 `0`、是算出來的 0（`pl.col("數量") - pl.col("數量")`）；結果是空值、有警告、整次執行不會失敗（串流引擎也是）。
- **型別收斂**：`len_chars`、`dt.year`、`dt.month`、`dt.day`、`dt.weekday` 是整數；兩個日期相減報錯並提示 `total_days`；比較的結果是「是／否」。
- **和 Polars 相同的部分**：語法表裡沒有被上面三項改到的寫法，轉出來的運算式和直接用 Polars 寫的相同（`meta.eq`）。

### 6. 驗證器

- 驗證器與 pydantic 一致：`tests/test_review_recipe.py` 的 `field_validator` 對照；第 3 節的每一條錯的運算式，兩邊的訊息相同。
- 欄位引用：引用不存在的欄，回報在 `…steps[i].expression`，有相近的欄名；引用前一步新增的欄可以；引用後面才新增的欄不行；系統欄位可以讀，那個位置沒有的系統欄位（沒有讀全部工作表時的 `__sheet`）回報欄不存在。
- 欄位效果：這一步之後多一欄；新欄位名稱和現有的欄、系統欄位重複時回報。
- 驗證器只用標準函式庫（現有的測試，加了 `import ast` 之後還是過）；`tablade validate` 與 AI skill 裡的驗證器對同一份 Recipe 的結果相同。
- 訊息的中英文：畫面的語言切成英文時，第 3 節挑十條，訊息是英文。

### 7. 步驟的其他行為

- 錯誤訊息：欄不存在；文字做加減（提示先用「文字轉數字」）；`to_string` 的格式錯；結果的型別不支援。都是 `StepError`，不是 Polars 的英文原文與一大段執行計畫。
- 警告：轉型失敗、除以 0 有警告；`warn_null` 關掉就沒有；用到的欄本來就是空值時不警告；`pl.when` 沒有 `otherwise` 的列有警告；運算式沒有引用任何欄時沒有警告；點警告可以跳到那一列（行號對）。
- `describe()`：中英文；很長的運算式會截斷。`suggest()`：右鍵的欄帶進運算式，欄名有引號時有加反斜線。
- 分檔無關：兩個來源檔分開算再接起來，和合在一起算相同（`per_file = False` 的現有測試）。
- 新增的欄放在資料欄的最後、系統欄位的位置不變（`output_order`）。
- `tests/test_large.py`：串流＝eager（用現有的假資料加上「文字轉數字」再接這一步）。
- 匯出：結果是「是／否」、浮點數、小數 3 位以上的欄，寫成 CSV 與 Parquet 再讀回來是對的。
- Recipe：1.2 的 Recipe 照樣能開；有這一步的 Recipe 版本是 1.3；`recipe-schema.lock.json` 和 schema 一致（現有的測試）。
- `tests/test_i18n.py`：英文寫齊（`label_en`、`doc_en`、每個欄位的 `en()`、訊息的 `t()`）。
- AI skill：`references/steps.md` 裡有語法表的每一個 `id` 的寫法；`check_limits` 還是過。
- `scripts/check_code_policy.py` 還是過（沒有 `eval`、`exec`、`compile`）。

### 8. 前端：切詞與插入（`node --test`）

`frontend/tests/expression.test.ts`，用 `node:test` 與 `node:assert`：

- **接回去一樣**：下面每一條文字切完再接起來，和原本的文字完全相同：一般的運算式、多行與註解、沒有關的引號、全形的符號、空字串、只有空白、有表情符號的字串。
- **種類**：`pl.col("金額") - 0.9` 的每一段種類正確（`namespace`、`function`、`column`、`operator`、`number`）。
- **不在語法表的名稱是 `unknown`**：`.sum`、`pl.read_csv`、`os`；`.str.` 後面不是 `.str.` 的方法；`.dt.` 後面不是 `.dt.` 的方法；`.then` 這類只會接在 `pl.when` 後面的名稱照一般的方法算（高亮不檢查前後文）。
- **欄**：`pl.col("金額")` 的欄在清單裡是 `column`，不在是 `error`；欄的清單是 `null`（還算不出來）時都是 `column`；欄名有 `\"` 時比對對。
- **字串**：單引號、雙引號、三引號、有反斜線；沒有關的引號是 `error`，而且只到那一行的最後。
- **關鍵字參數**（`strict=`、`literal=`、`separator=`、`mode=`）的名稱不算 `unknown`。
- **`vocabulary()`**：從一小份 `ui_syntax` 整理出 `pl.`、`.str.`、`.dt.`、其他方法四組；運算子與字面值那幾項不會變成名稱。
- **`quote()`**：一般的欄名、中文、有 `"`、有 `\`、兩種都有。
- **`insertAt()`**：插在中間、開頭、最後；取代選取的文字；`caret` 指的位置換算成整段文字裡的位置；文字是空的。

另外一個 Python 的測試：`frontend/src/lib/expression.ts` 與兩個新元件的原始碼裡**沒有**語法表裡任何一個函式名稱的字串（例如 `fill_null`、`strip_chars`），確認名稱都是從 schema 來的；`True`、`False`、`None`、`pl`、`col`、`str`、`dt` 這幾個切詞要認得的除外，測試裡把例外列清楚。

### 9. 前端：其他檢查

- `npm --prefix frontend run lint`、`npm --prefix frontend run build`、`npm --prefix frontend test` 都過；整份 HTML 還是小於 2 MB（改之前約 0.7 MB）。
- `tests/test_app.py`：`bridge.ts` 與 `api.py` 一致（這次沒有新的 API 方法）；前端原始碼沒有 `innerHTML`、`dangerouslySetInnerHTML`、`fetch`、`eval`。
- `tests/test_i18n.py`：新元件的文字都是 `tr('中文', 'English')`，沒有在模組最上層用 `tr()`。
- `tests/test_session.py`：`state()` 的每一步有 `input_system_columns`；讀全部工作表時含 `__sheet`；前一步算不出來時是 `None`。

### 10. 畫面實際操作

用瀏覽器的開發模式（先 `uv run python scripts/gen_frontend_dev_data.py`，再 `npm --prefix frontend run dev`；假 API 不會驗證）與真正的視窗（`TABLADE_DEV_URL=http://localhost:5173 uv run tablade`，有 Python 的驗證）各走一遍，淺色與深色都看：

- 新增「自訂」步驟，表單出現運算式的編輯框、兩個按鈕。
- **高亮**：打 `pl.col("金額") - pl.col("折扣")`，顏色對；打 `.sum()` 變成錯誤的顏色；欄名打錯變成錯誤的顏色；打中文的欄名（用輸入法，選字的過程中畫面正常）；游標、選取的範圍和字對得上，打到自動換行、按 Enter 換行、最後一行是空行時也對得上；內容超過最大高度時捲動，兩層一起動；貼上一大段文字。
- **插入欄位**：清單有資料欄與系統欄位；插在游標的位置；選取一段文字後插入會取代它；沒點過編輯框時接在最後面；欄名有引號時插入的文字是對的。
- **插入語法**：對話框有七個分類；每一項有寫法、說明、例子；搜尋找得到（打「空」找到空值那幾項、打 `round` 找到 `round`）；點一項後插在原本游標的位置，游標在括號裡；Esc 關掉不插入；只用鍵盤也能選與插入。**逐類點過，確認語法表的 69 項都在**。
- 插入後 350 毫秒，預覽多出新的一欄；錯的運算式在欄位下面顯示 Python 的錯誤訊息。
- 畫面的語言切成英文：按鈕、對話框的標題、分類、說明都是英文。
- 視窗縮窄時表單與對話框不會跑版。
- 用假資料（`scripts/gen_fixtures.py`）實際做一次：文字轉數字 → 自訂（金額 × 0.9 再 `round(2)`）→ 匯出，匯出的數字和預覽相同。

### 11. 既有的檢查

`uv run pytest`、`scripts/check_no_data.py --all`、`scripts/check_code_policy.py`、`ruff check`、`ruff format --check` 全部通過；CI 的三個作業系統都過。

## 效能（實測）

解讀完就是一條普通的 Polars 運算式，執行時和內建步驟走同一條路。500 萬列、串流引擎、取 5 次最快：

| 運算 | 直接用 Polars 寫 | 試作解讀出來的 |
|---|---|---|
| 兩欄相減 | 2 ms | 2 ms |
| 乘小數（浮點數；當時還沒改成精確小數） | 2 ms | 2 ms |
| 依條件給值 | 6 ms | 6 ms |
| 文字轉大寫 | 15 ms | 16 ms |

- 四項解讀出來的運算式都和直接寫的相同（`meta.eq`）。解讀本身每次不到 0.2 毫秒，和資料量無關。
- 精確小數的乘法比浮點數慢（同一份資料 6 ms 對 2 ms），而且相乘前多一次轉型。實作後用同樣的方式重量一次，把數字補在這裡。

## 不做的事

- 不執行使用者輸入的程式碼，也不提供「進階模式」放寬。
- 不做多行的程式、變數、迴圈、自己定義函式：只有一條運算式。
- 不做合計、排名、累計、上一列這類要看其他列的計算：用「群組彙總」「結合」「向下填滿」。
- 第一版不做正規表示式：用現有的「取代文字」「用正規表示式擷取」。
- 一步只算一欄：要算兩欄就加兩個步驟。
- 不能蓋掉現有的欄。
- 不做 SQL 的寫法。
- 不做自動完成（打字時跳出建議）、不在編輯框裡標出錯誤的位置：錯誤訊息會寫出是哪一個寫法。
- 不加前端的依賴（程式碼編輯器、測試框架）。
