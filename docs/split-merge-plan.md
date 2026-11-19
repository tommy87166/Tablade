# 拆欄：多出來的段併回指定的欄

- 狀態：**計畫，尚未實作**（2026-11-20）。
- 範圍：步驟「依分隔符號拆成多欄」（`split_delimited`）與「拆欄」（`split_column`），兩個共用 `on_extra`。
- Recipe 1.2 還沒發布（`v1.0.1` 是 1.1），所以這次的改動放進 1.2，不再升版號；`recipe-schema.lock.json` 要重新產生。

## 問題

有些系統匯出的 `|` 分隔檔，某個文字欄位（例如摘要）的內容裡本身有 `|`，又沒有用引號包起來。用「依分隔符號拆成多欄」拆的時候，那一筆會多出一段或幾段，**那一欄之後的欄位全部往後錯**。

`on_extra`（段數比欄名多時）現在只有兩種：

| 值 | 做法 | 結果 |
|---|---|---|
| `warn`（預設） | 多的段捨棄，產生警告 | 文字欄之後的欄位錯位，最後幾段被丟掉 |
| `merge` | 多的段併入最後一欄 | 一樣錯位，只是沒丟資料 |

兩種都救不了「多的分隔符號在中間某一欄」的情況。

實際遇到的例子（形狀，數字是舉例）：22 欄的明細，大約 1% 的行在第 13 欄（文字）裡有沒加引號的 `|`，多出 1～3 段。把每一筆錯位的行都試過「多的段併回第 k 欄」，看其他欄的形狀（日期、數字、代碼）是不是和正常的行一樣，最好的一律是併回第 13 欄。

## 做法

`on_extra` 多一個值 `merge_into`，加一個參數 `merge_column`（要併回哪一欄，從這一步自己的欄名裡選）：

| 值 | 畫面上的名稱 |
|---|---|
| `warn` | 產生警告並捨棄多的段（預設，不變） |
| `merge` | 多的段併入最後一欄（不變） |
| `merge_into` | 多的段併回指定的欄 |

段數比欄名多時（`n` 個欄名、拆出 `m` 段，`m > n`，`k` 是 `merge_column` 在欄名裡的位置，從 0 算）：

- 第 0～`k-1` 欄：從左邊照順序取（第 `i` 段）。
- 第 `k` 欄：第 `k` 段到第 `k + (m - n)` 段，用分隔符號接回去（`a|b`）。
- 第 `k+1`～`n-1` 欄：從右邊往回數（第 `i + (m - n)` 段）。

段數剛好或比較少時和現在完全一樣（少的欄是空值）。`merge_column` 是最後一欄時，結果和 `merge` 一樣。

例子（欄名 `日期、門市、摘要、金額`，`merge_column` 是 `摘要`）：

| 原本的一行 | 日期 | 門市 | 摘要 | 金額 |
|---|---|---|---|---|
| `0105\|北區\|一般銷售\|120` | 0105 | 北區 | 一般銷售 | 120 |
| `0105\|北區\|退貨\|重開\|120` | 0105 | 北區 | 退貨\|重開 | 120 |
| `0105\|北區\|A\|B\|C\|120` | 0105 | 北區 | A\|B\|C | 120 |

### 實作（`steps/split.py`）

- `OnExtra = Literal["warn", "merge", "merge_into"]`，`ON_EXTRA_LABELS`、`ON_EXTRA_LABELS_EN` 加一項。
- `SplitDelimited`、`SplitColumn` 各加：

  ```python
  merge_column: str = Field(
      default="",
      title="併回哪一欄",
      json_schema_extra={
          "ui": "own_name",
          "ui_from": "columns",
          "ui_show_if": {"on_extra": "merge_into"},
          **en("Merge into column"),
      },
  )
  ```

  `SplitColumn` 的 `ui_from` 是 `names`。
- `model_validator`：`on_extra` 是 `merge_into` 時 `merge_column` 必填、而且要是欄名之一；不是 `merge_into` 時要是空的。訊息用 `t()`。
- `recipe/validator.py` 的 `step_rules` 補同樣的檢查（AI skill 只有驗證器；`tests/test_review_recipe.py` 會檢查兩邊一致）。
- `apply()` 用 Polars 的清單運算（串流可以用；不用 `.over()`、不呼叫 Python）：

  ```python
  extra = pl.col(count_name) - n                       # 多了幾段（沒有多時 ≤ 0）
  shift = pl.when(extra > 0).then(extra).otherwise(0)
  for i, name in enumerate(names):
      if i < k:   value = parts.list.get(i, null_on_oob=True)
      elif i == k: value = pl.when(extra > 0)
                      .then(parts.list.slice(k, extra + 1).list.join(delimiter))
                      .otherwise(parts.list.get(k, null_on_oob=True))
      else:       value = parts.list.get(pl.lit(i) + shift, null_on_oob=True)
  ```

  `split_column` 有 `trim`：照現在的順序，每一段先去空白再接回去（接回去的值裡面的空白會被去掉；這和 `merge` 現在的行為一樣，實作時確認）。
- `warning_checks()`：`merge_into` 跟 `merge` 一樣不產生「多的段已捨棄」的警告（沒有捨棄）。另外考慮加一則「這一列多 N 段，已併回『摘要』」的提示性警告，讓使用者知道哪幾列被併過（見「待決定」）。
- `describe()`：「把「line」依「|」拆成 22 欄（…）；多的段併回「摘要」」。
- `effect` 不變（欄位還是那幾個）。
- `suggest()`：不自動選 `merge_into`（猜不準是哪一欄）。
- `ai_notes`：什麼時候用 `merge_into`（某個文字欄裡有沒加引號的分隔符號、其他欄都很規矩時）；`examples` 加一個 `merge_into` 的例子（同時是測試）。

### 表單（前端）

參數表單由 schema 自動產生（DESIGN.md §16.5），不為這個步驟手寫。需要一種新的欄位類型：

- `"ui": "own_name"`、`"ui_from": "columns"`：下拉選單，選項是同一個表單裡 `ui_from` 那個參數（這一步自己的欄名）目前的值。欄名改了，選項跟著變；選的值不在裡面時顯示紅字（驗證在 Python 做，前端只顯示）。
- 放在 `frontend/src/components/form/fields.tsx`，和其他 `ui` 類型一樣。
- `ui_show_if` 已經有了：只有「段數比欄名多時」選「併回指定的欄」才顯示。

### 文件

- DESIGN.md：步驟一覽的 `split_delimited`、`split_column`。
- `src/tablade/skill.py`、`references/steps.md`（由 schema 產生）：AI skill 的說明。
- README 兩份：步驟清單裡這兩步的說明（有提到 `on_extra` 的話）。
- `docs/i18n-plan.md` 用語表：「併回哪一欄／Merge into column」、「多的段併回指定的欄／Merge extra parts into a chosen column」。

## 假資料（`fixtures.py`）

- `extra_delimiter_rows(seed=0, rows=200, every=20)`：`|` 分隔、6 欄（日期、門市、品號、品名、備註、金額），每 `every` 筆的備註裡有 1～2 個沒加引號的 `|`。回傳（每一行, 每一行正確拆開的 6 個值）。

## 測試

- `tests/test_steps.py`：
  - `merge_into` 在第一欄、中間、最後一欄；多 1 段、多 2 段、多很多段；段數剛好、比較少（和 `warn` 一樣）；空行。
  - 最後一欄時和 `merge` 結果一樣。
  - 有引號的欄位（`split_delimited`）：引號裡的分隔符號照舊不拆，多的段只算引號外的。
  - `split_column` 的 `trim`。
  - 參數驗證：`merge_into` 沒填 `merge_column`、填了不在欄名裡的、不是 `merge_into` 卻填了：pydantic 與 `validator.py` 都報錯、訊息一致。
- 用 `extra_delimiter_rows` 跑一次：每一行拆出來都和正確的值一樣。
- `tests/test_large.py`：串流＝eager。
- 表單：`fields.tsx` 的新類型由前端 lint／build 檢查型別；`tests/test_i18n.py` 檢查英文寫齊。

## 待決定

1. **兩個步驟都加，還是只加 `split_delimited`**：計畫是兩個都加（共用 `OnExtra`，只加一個的話選單會不一樣）。
2. **要不要對被併過的列產生警告**：`merge` 現在不產生警告。`merge_into` 改了資料的切法，建議產生一則「這一列多 N 段，已併回『摘要』」（點警告可以跳到那一列），讓使用者檢查有沒有併錯；也可以照 `merge` 不產生。
3. **`merge_column` 用欄名還是位置**：計畫用欄名（改欄名時會跟著驗證；位置在插入欄名時容易對錯）。

## 不做的事

- 不自動判斷要併回哪一欄（像這次用「其他欄的形狀」比對）：猜錯會默默把資料放錯欄；讓使用者自己選。
- 不處理「好幾個欄位裡都有多的分隔符號」：那樣沒有唯一的切法，只能回到來源修正。
