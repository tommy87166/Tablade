# 自訂：用 Polars 的寫法算出新的一欄

- 狀態：**計畫，尚未實作**（2026-11-20）。要決定的事和維護者確認過五項（「已決定」），還剩一項：運算式裡的小數怎麼算（「待決定」）。
- 範圍：新步驟 `custom_expression`（「欄」分類，畫面上叫「自訂」）、驗證器、參數表單的一種新欄位、AI skill 的說明。
- Recipe 1.2 還沒發布（`v1.0.1` 是 1.1），所以放進 1.2，不再升版號；`recipe-schema.lock.json` 要重新產生。
- 下面寫「實測」的地方都是用 Polars 2.1.0、Python 3.13 在 macOS 上試作量到的（試作沒有進 repo）。

## 問題

現有的步驟做不到「兩欄相加、相減」「乘上一個比例」「依條件給值」「把幾欄接成一欄」這類一列一列的計算。DESIGN.md §7.2 的未完成清單本來就有 `custom_expression`（限定用白名單語法組成，不可任意執行程式），這份計畫就是它。

## 原則：讀運算式，不執行程式碼

DESIGN.md §4 的核心原則 1（步驟是資料，不是程式碼；不使用 `eval` / `exec` 執行使用者輸入）不變。

- 使用者輸入的是**一條** Polars 寫法的運算式，例如 `pl.col("金額") - pl.col("折扣")`。它是一段文字，存在 Recipe 裡。
- Tablade 用標準函式庫的 `ast` 把這段文字讀成語法樹，照白名單一個節點一個節點轉成 Polars 的運算式。沒有任何一段使用者的文字會被 Python 執行；不用 `eval`、`exec`、`compile`，`scripts/check_code_policy.py` 不用改。
- 不能直接執行的原因：Recipe 會互相傳，也會由 AI 撰寫。能執行程式碼的話，打開別人給的 Recipe 就等於在自己的電腦上跑對方寫的程式。

比較過、不採用的做法：

| 做法 | 不採用的原因 |
|---|---|
| 直接執行輸入的 Python | 違反核心原則 1 |
| SQL 運算式（`pl.sql_expr("金額 - 折扣")`） | 驗證器只能用標準函式庫，沒辦法解析 SQL，AI skill 查不到錯；解析時會接受 `SUM(金額)`、`OVER (…)`，要另外從運算式序列化的結構去擋（那個格式不保證不變）；`NOW()`、`CURRENT_DATE` 會讓同一份 Recipe 每天結果不同 |
| 靠 Polars 的 `meta.is_row_separable()` 判斷能不能用 | 實測它把「金額 − 金額的平均」判成一列對一列，而且是標示為不穩定的 API |

## 做法

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
| 乘上比例 | `pl.col("金額") * 0.9` |
| 空值當成 0 再算 | `pl.col("金額") - pl.col("折扣").fill_null(0)` |
| 依條件給值 | `pl.when(pl.col("數量") >= 10).then(pl.lit("大量")).otherwise(pl.lit("一般"))` |
| 把幾欄接起來 | `pl.concat_str([pl.col("門市代號"), pl.lit("-"), pl.col("單號")])` |
| 取年月 | `pl.col("日期").dt.to_string("%Y%m")` |

讀進來的資料一律是文字（DESIGN.md §7.2.1），數字的計算要先用「文字轉數字」；直接算會報錯（實測：文字 − 1 是 `arithmetic on dtypes str and dyn int is not allowed`），訊息要改寫成中文並提示先轉數字。

### 允許的寫法（白名單）

只接受下面列的寫法；其他的一律擋下，訊息寫出是哪一個寫法不能用。

| 類別 | 允許的 |
|---|---|
| 整體 | 一條運算式。不能有敘述、指定、變數、函式定義 |
| 名稱 | 只有 `pl` |
| 欄位 | `pl.col("欄名")`：剛好一個字串字面值 |
| 字面值 | 整數（int64 的範圍內）、小數、字串、`True` / `False`、`None`；也可以寫成 `pl.lit(…)` |
| 運算子 | `+ - * / // %`、一元的 `-`、`== != > >= < <=`、`& \| ~` |
| `pl.` 的函式 | `pl.when(條件).then(值)`（可以再接 `.when().then()`）`.otherwise(值)`、`pl.concat_str([…], separator="…")`、`pl.coalesce(…)`、`pl.max_horizontal(…)`、`pl.min_horizontal(…)`、`pl.sum_horizontal(…)` |
| 一般的方法 | `fill_null(值)`、`is_null()`、`is_not_null()`、`is_in([字面值…])`、`is_between(下限, 上限)`、`abs()`、`round(位數)`、`floor()`、`ceil()`、`clip(下限, 上限)`、`cast(型別, strict=False)` |
| `cast` 的型別 | `pl.Int64`、`pl.Float64`、`pl.String`、`pl.Boolean`、`pl.Decimal(38, N)`（N 是 0～10） |
| `.str.` | `strip_chars()`、`strip_chars_start()`、`strip_chars_end()`、`to_uppercase()`、`to_lowercase()`、`len_chars()`、`slice(起點, 長度)`、`head(n)`、`tail(n)`、`zfill(長度)`、`pad_start(長度, 字)`、`pad_end(長度, 字)`、`starts_with(文字)`、`ends_with(文字)`、`contains(文字, literal=True)`、`replace(舊, 新, literal=True)`、`replace_all(舊, 新, literal=True)` |
| `.dt.` | `year()`、`month()`、`day()`、`weekday()`、`to_string(格式)`、`total_days()`（兩個日期相減之後用） |

上表的每一項都實測過：Polars 2.1.0 有這個函式，而且把資料拆成一列一列分別算，結果和整份一起算相同。

幾個要注意的地方：

- **`pl.col` 的欄名不能是 `*`，也不能是 `^` 開頭、`$` 結尾**。實測 `pl.col("*")` 會變成全部的欄、`pl.col("^金.*$")` 會被當成正規表示式。這兩種直接擋下；轉完之後再用 `meta.has_multiple_outputs()` 確認只有一欄。
- **系統欄位可以讀**（`__source_file`、`__line_no` 等；那個位置當時有哪些系統欄位，驗證器本來就會推算）。例如用 `pl.col("__source_file").str.slice(6, 4)` 從來源檔名取出一段。只能讀：新欄位名稱不能是系統欄位的名稱。其他步驟的系統欄位只能用在條件裡，這一步和條件一樣是「讀」。
- **`literal=True` 一定要寫**。Polars 的 `contains`、`replace`、`replace_all` 預設把第一個參數當正規表示式；第一版只做一般文字，要用正規表示式請用現有的「取代文字」「用正規表示式擷取」。寫出來是為了讓這段文字的意思和它在 Polars 裡的意思一樣。
- **`cast` 一定要寫 `strict=False`**。實測嚴格轉型遇到轉不了的值，整次執行會失敗（預覽的樣本裡沒有那個值的話，匯出跑到一半才失敗）；`strict=False` 時轉不了的是空值，會有警告。
- **`cast` 不能轉成日期**：實測 Polars 不支援文字直接轉日期（`casting from string to date is not supported`），而且民國年要用「文字轉日期」。
- **數字的參數只能是字面值，而且檢查範圍**（`round` 的位數 0～10、`zfill` 與 `pad_*` 的長度 0～1,000、`slice`／`head`／`tail` 的整數）。實測 `zfill(-1)` 是執行時才錯，`round(-1)` 是 Python 的 `OverflowError`。
- **`dt.to_string` 的格式**在建立運算式時先用一個固定的日期試一次，錯的格式當場報錯（實測 `%Q` 是執行時才錯）。

擋下的寫法與要顯示的說明：

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
| `pl.read_*`、`pl.scan_*`、`pl.sql*` 與其他 `pl.` 的函式 | 不在白名單 | — |
| `and`、`or`、`not` | 在 Polars 的運算式裡意思不對 | `&`、`\|`、`~`，兩邊加括號 |
| `1 < x < 3`、`x if 條件 else y` | 同上 | `is_between`、`pl.when` |
| `**`、`in`、`is`、`[ ]`、f-string、comprehension、`:=` | 不在白名單 | — |

### 和原本的 Polars 不一樣的三個地方

這三項是 Tablade 在轉換時多做的，要寫進步驟說明、`ai_notes` 與 README。第 1 項還沒決定：選 A 的話，那一項就和 Polars 一樣，只剩兩個地方不一樣。

**1. 運算式裡的小數怎麼算**（還沒決定，見「待決定」）

DESIGN.md §7.2.1 的「小數」是精確小數（`Decimal(38, N)`）。運算式裡寫 `pl.col("金額") * 0.9` 時有三種做法，實測（金額是小數 2 位）：

| 金額 | 真正的答案 | A：浮點數（Polars 原本的） | B：精確小數，位數照 Polars | C：精確小數，相乘時位數相加 |
|---|---|---|---|---|
| 1.15 | 1.035 | 1.035 | 1.04 | 1.035 |
| 2.35 | 2.115 | 2.115 | 2.12 | 2.115 |
| 0.07 | 0.063 | 0.06300000000000001 | 0.06 | 0.063 |
| 19.99 | 17.991 | 17.991 | 17.99 | 17.991 |
| 33.33 | 29.997 | 29.997 | 30.00 | 29.997 |
| 19.99 的一百萬列，乘完再合計 | 17,991,000 | 17,991,000.0 | **17,990,000.00（少 1,000）** | 17,991,000.000 |
| 結果的型別 | | 浮點數 | 小數 2 位 | 小數 3 位 |

- **A：照 Polars，`0.9` 是浮點數**。整欄變成浮點數。每一列差不到十億分之一，但寫成 CSV 會出現 `0.06300000000000001` 這種數字，型別分頁顯示「浮點數」。實作最少，寫的和 Polars 的意思完全一樣。
- **B：`0.9` 讀成精確小數，其他照 Polars**。Polars 的規則是結果的小數位數取兩邊比較多的那個，多的位數四捨六入五取偶數：每一列都先被進位到 2 位，合計就跟著錯（上表少 1,000）。**不採用。**
- **C：`0.9` 讀成精確小數，相乘前先把兩邊轉成「兩邊的位數相加」的小數**（金額 2 位 × `0.9` 1 位 → 3 位）。相乘的結果完全精確（實測 × 0.9、× 0.333 每一列都和真正的答案相同，合計也是）。相除沒有「精確的位數」，要另外定一個規則，例如結果至少 6 位（實測 1.15 ÷ 3：照 Polars 是 0.38，先加到 6 位是 0.383333）。位數相加的上限定 10 位，超過就報錯請使用者先 `round`。
  - 實作比 A 多：轉換每一個乘、除的節點時，要先用 schema 查兩邊各是幾位（`lf.select(子運算式).collect_schema()`，不用跑資料）。
  - 有一邊是浮點數時結果就是浮點數（實測），三種做法都一樣。
  - 運算式裡的小數從原始的文字讀成 `Decimal`（`ast.get_source_segment`），不經過浮點數。

**2. 除以 0 是空值**

實測 Polars 原本的結果依型別不同，其中小數除以 0 會讓整次執行失敗：

| 運算 | Polars 原本 | 計畫 |
|---|---|---|
| 小數 ÷ 0 | 整次執行失敗（`division by zero Decimal`） | 空值 |
| 整數 `/` 0 | 無限大（`inf`） | 空值 |
| 整數 `//` 0、`%` 0 | 空值 | 空值 |

做法：`/`、`//`、`%` 的除數包成 `pl.when(除數 != 0).then(除數)`（除數是 0 時變成空值，任何數除以空值是空值）。實測小數、整數、浮點數互相除的七種組合都對。不能寫成「`pl.when(除數 == 0).then(None).otherwise(左 / 右)`」：兩邊都會算，一樣會失敗。

**3. 結果的型別只會是 DESIGN.md §7.2.1 的那幾種**

| 算出來的型別（實測的例子） | 處理 |
|---|---|
| `UInt32`（`str.len_chars()`）、`Int32`（`dt.year()`）、`Int8`（`dt.month()`、`dt.weekday()`） | 轉成 `Int64`（整數） |
| `Float32` | 轉成 `Float64`（浮點數） |
| `Boolean`（比較、`is_null()`） | 維持。型別分頁已經認得「是／否」，這會是第一個產生它的步驟 |
| `Duration`（兩個日期相減）與其他型別 | 報錯，提示改用 `.dt.total_days()` |

### 錯誤什麼時候報

| 錯誤 | 什麼時候 | 在哪裡 |
|---|---|---|
| 語法錯、用了白名單以外的寫法、參數超出範圍、太長 | 寫 Recipe 時 | 驗證器（AI skill 也有）與 pydantic |
| 引用的欄不存在；新欄位名稱和現有的欄或系統欄位重複 | 寫 Recipe 時 | 驗證器的欄位檢查（給相近的欄名） |
| 型別不對（文字做加減）、結果的型別不支援、`to_string` 的格式錯 | 套用步驟時（不用跑資料） | `apply()` 裡看 schema，包成 `StepError` |
| 轉型失敗、除以 0 | 執行時 | 不會失敗：結果是空值，產生警告 |

執行時才會讓整次失敗的情況，實測到的（嚴格轉型、小數除以 0、錯的格式、負的長度）上面都處理了。

### 防止很大的輸入

- 上限：2,000 個字；語法樹最多 500 個節點、最深 50 層；整數字面值要在 int64 的範圍內。
- 實測 `ast.parse` 的反應：300 層括號是 `SyntaxError`、20,000 個 `+` 是 `RecursionError`、有空字元是 `SyntaxError`。三種都接住，回報「運算式太長或太複雜」。先檢查長度再解析。
- 轉換時不替使用者算任何 Python 的運算：兩個字面值相加也是轉成 `pl.lit` 交給 Polars 算（不會有 `"a" * 1000000000` 在 Python 裡展開的情況）。
- 取屬性只用白名單表裡的名字，不用使用者輸入的字串去取（沒有 `__class__` 這類的路）。

### 警告

`warn_null` 開著時：結果是空值（或無限大、NaN），而運算式用到的欄在那一列都有值，就產生一則警告「這一列算出來是空值」，點警告可以跳到那一列。會抓到轉型失敗、除以 0。`pl.when` 沒有寫 `.otherwise()` 的列也會是空值；那是故意的話把 `warn_null` 關掉。

## 實作

### 驗證器（`recipe/validator.py`）

白名單與語法檢查放在驗證器（只能用標準函式庫；`ast` 是標準函式庫），Tablade 的步驟引用它，兩邊不會不一樣。`steps/registry.py` 已經是從驗證器引用 `EFFECT_KINDS`。

- 白名單的表（常數）：運算子、`pl.` 的函式、一般的方法、`.str.` 與 `.dt.` 的方法、型別；每個方法記它接受幾個參數、哪些關鍵字、哪些參數只能是字面值與範圍、哪些關鍵字一定要寫（`literal=True`、`strict=False`）。
- `ExpressionScan(text)`（做法比照現有的 `RegexScan`）：檢查長度 → `ast.parse(text, mode="eval")` → 走一遍語法樹，得到 `error`（第一個問題，中英文用驗證器自己的 `t()`）與 `columns`（引用到的欄名，依出現的順序）。
- `step_rules`：`custom_expression` 的 `expression` 有 `error` 時回報在 `…steps[i].expression`。
- `_semantic` 的欄位引用（現在只認 `ui: column` 等標記）：加上 `ExpressionScan.columns`，不存在的欄給相近的欄名；這裡的引用可以是系統欄位（和條件裡的引用一樣，`column_refs` 回傳的第三個值是 True）。
- AI skill 的沙箱是哪一版 Python 還不知道（DESIGN.md §15），所以只用 `ast.Constant` 這類 3.8 就有的東西，不用 `match`。

### 步驟（新檔 `steps/custom.py`）

```python
@register
class CustomExpression(Step):
    """自訂：用 Polars 的寫法寫一條運算式，算出新的一欄（一列一列算；不能合計、不能看其他列）。"""

    type: Literal["custom_expression"] = "custom_expression"
    name: NonEmpty = Field(title="新欄位名稱", json_schema_extra=en("New column name"))
    expression: str = Field(
        title="運算式",
        min_length=1,
        max_length=2000,
        json_schema_extra={"ui": "expression", **en("Expression")},
    )
    warn_null: bool = Field(
        default=True, title="算出空值時警告", json_schema_extra=en("Warn on null results")
    )

    label = "自訂"
    label_en = "Custom"
    category = "欄"
    effect = {"kind": "add", "names": "$name"}
```

- `field_validator("expression")`：呼叫 `ExpressionScan`，有 `error` 就丟出同樣的訊息（`tests/test_review_recipe.py` 檢查兩邊一致）。
- `build(tree) -> pl.Expr`：照語法樹轉成 Polars 的運算式，用驗證器的表決定能呼叫什麼。運算式裡的小數依「待決定」選的做法處理；除數包起來；最後依 schema 收斂型別。
- `apply()`：純函式。確認引用的欄存在（現有的 `missing_columns`）→ `build` → 用 `lf.select(expr).collect_schema()` 看型別（Polars 的錯誤包成 `StepError`，中英文訊息）→ `lf.with_columns(expr.alias(self.name)).select(self.output_order(lf))`。
- `per_file = False`（一列一列處理，與來源檔怎麼分檔無關）。不排序、不用 `.over()`、不呼叫 Python，符合串流規則。
- `describe()`：「新增「毛利」＝ `pl.col("金額") - pl.col("成本")`」；運算式超過 60 個字就截斷加「…」。
- `suggest()`：從資料格右鍵某一欄新增時，運算式帶入 `pl.col("那一欄")`。
- `warning_checks()`：上面「警告」那一項。
- `ai_notes`（維持中文）：只能一列一列算、要合計用「群組彙總」；數字先用「文字轉數字」；三個和 Polars 不一樣的地方；`literal=True`、`strict=False` 一定要寫。`examples` 至少三個（相減、依條件給值、接文字），同時是測試。
- 加進 `steps/__init__.py`，放在「欄」分類的最後（匯入順序＝顯示順序）。

### 表單（前端）

參數表單由 schema 自動產生，不為這個步驟手寫。需要一種新的欄位類型：

- `"ui": "expression"`：多行的文字框（`components/ui/textarea.tsx` 已經有），等寬字、關掉拼字檢查與自動大寫。加在 `frontend/src/lib/schema.ts` 的類型清單與 `components/form/fields.tsx`。
- 錯誤訊息照現在的方式顯示在欄位下面（驗證只在 Python 做）。
- 欄位下面的說明放兩個例子；完整的可用寫法放在步驟說明與 README。
- **「插入欄位」**：文字框上面一個下拉選單，列出這一步輸入的欄（資料欄在前，系統欄位在後；畫面的狀態裡已經有每一欄的名稱與是不是系統欄位，表單要把系統欄位也傳給這個欄位）。選一欄就在游標的位置插入 `pl.col("欄名")`，沒有游標時接在最後面。欄名裡的 `\` 與 `"` 要加反斜線。只是幫忙打字：插入的文字和手打的一樣，照樣由 Python 驗證。

### AI skill 與文件

- `skill.py`：步驟目錄會自動帶 `ai_notes` 與 `examples`。另外從驗證器的白名單表產生一段「自訂的運算式可以用的寫法」，放在 references 的步驟說明裡（不放 `SKILL.md`，它有字數上限）；表是產生的，不會和程式不一樣。
- DESIGN.md：§4 核心原則 1 補一句（運算式是解讀、不是執行）；§7.2 把 `custom_expression` 從未完成移到已完成；§7.2.1 的型別表（「是／否」「浮點數」的來源）；§17.4 驗證器。
- README 兩份（英文、繁體中文）：步驟清單與可用的寫法。
- `docs/i18n-plan.md` 用語表：「自訂／Custom」「運算式／Expression」。
- CLAUDE.md：補一條「白名單要加函式時的規則」（見下面）。

### 之後要在白名單加函式時

每加一個都要符合，並由測試檢查：

1. 一列對一列：把資料拆成一列一列分別算，結果和整份一起算相同。
2. 串流引擎支援（不會退回整份放進記憶體）。
3. 不讀寫檔、不連網路、不執行 Python。
4. 會在執行時失敗的參數（負的長度、嚴格轉型）要在解讀時擋掉或改成不會失敗的寫法。
5. 結果的型別在 DESIGN.md §7.2.1 的範圍內，或在收斂型別那裡處理。

## 測試

新檔 `tests/test_custom_expression.py`（步驟的邊界情況照慣例也在 `tests/test_steps.py` 補）：

- **白名單的每一項**：表裡的每個運算子、函式、方法各跑一次；結果和直接用 Polars 寫的相同（用 `meta.eq` 比對運算式，三個不一樣的地方除外）。
- **一列對一列**：白名單的每一項，把資料拆成一列一列算，結果和整份算相同。Polars 升版時某個函式行為變了，這裡會抓到。
- **擋下的寫法**：上面那張表的每一項、`__import__("os")`、`open("x")`、`pl.col("a").__class__`、`pl.read_csv("x")`、`lambda`、comprehension、f-string、`:=`、兩行、敘述（`import os`）、全形的括號與引號；訊息有寫出是哪一個寫法。
- **`pl.col`**：`*`、`^…$`、兩個欄名都擋下；系統欄位可以讀（那個位置沒有的系統欄位，例如沒有讀全部工作表時的 `__sheet`，回報欄不存在）；新欄位名稱是系統欄位的名稱時擋下。
- **很大的輸入**：超過 2,000 個字、很深的括號、很長的 `-` 與 `+`、空字元、超過 int64 的整數：都是一般的錯誤訊息，不會有沒接住的例外。
- **運算式裡的小數**：依「待決定」選的做法，上面那張表的每一列（含一百萬列的合計）。
- **除以 0**：小數、整數、浮點數各自的 `/`、`//`、`%`；結果是空值、有警告、整次執行不會失敗。
- **型別收斂**：`len_chars`、`dt.year`、`dt.month` 是整數；兩個日期相減報錯並提示 `total_days`。
- **錯誤訊息**：欄不存在（有相近的欄名）、文字做加減（提示先轉數字）、`to_string` 的格式錯。
- **警告**：轉型失敗、除以 0 有警告；`warn_null` 關掉就沒有；用到的欄本來就是空值時不警告。
- **驗證器與 pydantic 一致**：`tests/test_review_recipe.py` 的 `field_validator` 對照；同一批錯的運算式兩邊的訊息相同。
- **驗證器只用標準函式庫**：現有的測試（加了 `import ast` 之後還是過）。
- `tests/test_large.py`：串流＝eager。
- 匯出：結果是「是／否」與浮點數的欄，寫成 CSV 與 Parquet 再讀回來是對的。
- `tests/test_i18n.py`：英文寫齊。前端 lint 與 build；整份 HTML 還是小於 2 MB。
- 「插入欄位」：用瀏覽器的開發模式（假 API）實際操作一次：插在游標的位置、欄名有引號與反斜線、欄名是中文；插入後 Python 驗證通過。
- 假資料：例子用得到數字欄與日期欄，`fixtures.py` 現有的門市銷售資料加上「文字轉數字」「文字轉日期」就夠；不夠的話擴充 `fixtures.py`。

## 效能（實測）

解讀完就是一條普通的 Polars 運算式，執行時和內建步驟走同一條路。500 萬列、串流引擎、取 5 次最快：

| 運算 | 直接用 Polars 寫 | 這一步解讀出來的 |
|---|---|---|
| 兩欄相減 | 2 ms | 2 ms |
| 乘小數（當時還沒改成精確小數） | 2 ms | 2 ms |
| 依條件給值 | 6 ms | 6 ms |
| 文字轉大寫 | 15 ms | 16 ms |

- 四項解讀出來的運算式都和直接寫的相同（`meta.eq`）。
- 解讀本身每次不到 0.2 毫秒，和資料量無關。
- 精確小數的乘法比浮點數慢（同一份資料：SQL 寫法的小數字面值 6 ms 對 2 ms）。「待決定」選 C 的話，實作後要重量這一項。

## 已決定（2026-11-20 和維護者確認）

- **算出空值時產生警告，可以關**（`warn_null`，預設開著）。
- **只能新增一欄**，不能蓋掉現有的欄（欄位效果是 `add`）；要取代原本的欄時，後面再接「刪除欄」「改欄名」。
- **表單要有「插入欄位」**。
- **運算式可以讀系統欄位**（`__line_no`、`__source_file` 等）。
- **名稱**：畫面上叫「自訂」，Recipe 裡的類型是 `custom_expression`（DESIGN.md 原本留的名字）。

## 待決定

1. **運算式裡的小數怎麼算**：A（照 Polars，浮點數）或 C（精確小數，相乘時位數相加；相除至少 6 位）。比較見「和原本的 Polars 不一樣的三個地方」第 1 項。B 不採用。

## 不做的事

- 不執行使用者輸入的程式碼，也不提供「進階模式」放寬。
- 不做多行的程式、變數、迴圈、自己定義函式：只有一條運算式。
- 不做合計、排名、累計、上一列這類要看其他列的計算：用「群組彙總」「結合」「向下填滿」。
- 第一版不做正規表示式：用現有的「取代文字」「用正規表示式擷取」。
- 一步只算一欄：要算兩欄就加兩個步驟。
- 不做 SQL 的寫法。
