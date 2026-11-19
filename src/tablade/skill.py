"""產生 AI skill：讓 AI 助理（例如 Microsoft 365 Copilot）撰寫並驗證 recipe（DESIGN.md §17）。

skill 完全從 registry 產生：新增步驟後重新產生就會出現在步驟目錄、schema 與驗證器裡。
版本號＝recipe 版本（``RECIPE_VERSION``）。
"""

from __future__ import annotations

import io
import json
import re
import zipfile
from collections import defaultdict
from pathlib import Path, PurePosixPath
from typing import Any

from .recipe import validator as validator_module
from .recipe.schema import RECIPE_VERSION
from .recipe.spec import build_spec
from .steps import all_steps
from .steps.base import Step

SKILL_NAME = "tablade-recipe"
EMBED_LINE = "EMBEDDED_SPEC: dict[str, Any] | None = None"

# Microsoft 365 Copilot Agent Builder 的限制（Microsoft Learn，2026-11）
LIMITS = {
    "zip_bytes": 50 * 1024 * 1024,
    "file_bytes": 25 * 1024 * 1024,
    "files": 350,
    "depth": 3,
    "instructions_chars": 20_000,
}
ALLOWED_SUFFIXES = {".md", ".json", ".txt", ".yaml", ".yml", ".csv", ".py", ".js", ".sh"}

_EFFECT_TEXT = {
    "keep": "欄位不變",
    "add": "新增{names}列出的欄位",
    "split": "移除{source}指定的欄位（keep_source 為 true 時保留），在原位置插入{names}列出的欄位",
    "replace_data": "所有資料欄換成{names}列出的欄位",
    "rename_data": "資料欄依序改名為{names}列出的欄名（數量必須和目前的資料欄相同）",
    "select": "只留下{names}列出的資料欄，依列出的順序排列；沒列到的資料欄刪掉（欄名不能重複）",
    "rename": "依{names}（{{舊欄名: 新欄名}}）改欄名；改完後欄名不能重複，可以互換",
    "drop": (
        "刪除欄位：by 是 name 時刪除{names}列出的欄位，by 是 position 時刪除{positions}"
        "列出的第幾個資料欄（從 1 起，不算系統欄位）；至少要留下一個資料欄"
    ),
    "unpivot": (
        "沒列在{columns}的資料欄保留不動，後面接{name}與{value}指定的兩個新欄"
        "（新欄名不能和保留的欄重複）"
    ),
    "group": (
        "資料欄變成{by}列出的分組欄，後面接{aggregations}每一項的 name，最後是{count}指定的筆數欄"
        "（空字串代表不加）；其他欄都不保留。新欄名不能重複"
    ),
    "join": (
        "how 是 left / inner 時在後面新增{names}列出的另一張表的欄位（不能和現有欄位重複，也不能是鍵值）；"
        "how 是 anti / semi 時欄位不變"
    ),
    "pivot": (
        "資料欄變成{index}列出的欄，後面接{names}列出的欄（{on}的值變成欄名）；其他欄都不保留。"
        "新欄名不能重複，也不能和{index}列出的欄重複"
    ),
}


def skill_filename(probe: bool = False) -> str:
    return "tablade-probe-skill.zip" if probe else f"tablade-recipe-skill-v{RECIPE_VERSION}.zip"


# ── 步驟目錄 ─────────────────────────────────────────────────────


def _type_text(prop: dict[str, Any], defs: dict[str, Any]) -> str:
    if "$ref" in prop:
        return prop["$ref"].rsplit("/", 1)[-1]
    if "enum" in prop:
        labels = prop.get("ui_labels", {})
        return " / ".join(
            f"`{v}`" + (f"（{labels[v]}）" if v in labels else "") for v in prop["enum"]
        )
    kind = prop.get("type")
    if kind == "array":
        items = prop.get("items", {})
        if "discriminator" in items:
            return "條件陣列（見下方）"
        if "$ref" in items:
            at_least = f"，至少 {prop['minItems']} 項" if prop.get("minItems") else ""
            return f"`{items['$ref'].rsplit('/', 1)[-1]}` 物件的陣列（見下方{at_least}）"
        noun = {"string": "文字", "integer": "整數"}.get(items.get("type"), "")
        at_least = f"（至少 {prop['minItems']} 項）" if prop.get("minItems") else ""
        return f"{noun}陣列{at_least}"
    if kind == "object":
        at_least = f"，至少 {prop['minProperties']} 項" if prop.get("minProperties") else ""
        return f"物件（文字 → 文字{at_least}）"
    labels = {"string": "文字", "integer": "整數", "number": "數字", "boolean": "true / false"}
    text = labels.get(kind, str(kind))
    if "minimum" in prop:
        text += f"，≥ {prop['minimum']}"
    if "maximum" in prop:
        text += f"，≤ {prop['maximum']}"
    if "maxLength" in prop:
        text += f"，最多 {prop['maxLength']} 字元"
    return text


def _ui_text(prop: dict[str, Any]) -> str:
    return {
        "column": "引用現有欄位",
        "columns": "引用現有欄位（多個）",
        "column_positions": "第幾個資料欄（多個，從 1 起）",
        "column_order": "引用現有欄位（多個，依順序）",
        "rename_map": "{現有欄名: 新欄名}",
        "new_columns": "新欄名",
        "regex": "正規表示式（Rust regex）",
    }.get(prop.get("ui", ""), "")


def _param_rows(schema: dict[str, Any], defs: dict[str, Any], skip: tuple[str, ...]) -> list[str]:
    required = set(schema.get("required", []))
    if not [n for n in schema.get("properties", {}) if n not in skip]:
        return ["（沒有參數）"]
    rows = ["| 參數 | 說明 | 型別 | 必填 | 預設 |", "|---|---|---|---|---|"]
    for name, prop in schema.get("properties", {}).items():
        if name in skip:
            continue
        note = " ".join(
            x for x in (prop.get("title", ""), _ui_text(prop), prop.get("description", "")) if x
        )
        default = json.dumps(prop["default"], ensure_ascii=False) if "default" in prop else ""
        rows.append(
            f"| `{name}` | {note} | {_type_text(prop, defs)} | {'是' if name in required else ''} | {default} |"
        )
    return rows


def _table(data: dict[str, list[Any]]) -> list[str]:
    columns = list(data)
    n = len(next(iter(data.values()), []))

    def cell(v: Any) -> str:
        return "null" if v is None else str(v).replace("|", "\\|")

    return [
        "| " + " | ".join(columns) + " |",
        "|" + "---|" * len(columns),
        *["| " + " | ".join(cell(data[c][i]) for c in columns) + " |" for i in range(n)],
    ]


def _effect_text(cls: type[Step]) -> str:
    effect = cls.effect
    template = _EFFECT_TEXT[effect["kind"]]
    values = {
        k: (f" `{v[1:]}` 參數" if isinstance(v, str) and v.startswith("$") else v)
        for k, v in effect.items()
    }
    return template.format_map(defaultdict(str, values))


def steps_markdown(spec: dict[str, Any] | None = None) -> str:
    spec = spec or build_spec()
    defs = spec["schema"]["$defs"]
    out = [
        f"# Tablade 步驟目錄（recipe 版本 {RECIPE_VERSION}）",
        "",
        "recipe 只能使用這裡列出的步驟類型。每個步驟都要有 `id`（例如 s1、s2，不能重複）與 `type`，"
        '可加 `"enabled": false` 暫時停用；其餘欄位就是參數，省略的參數用預設值。',
        "",
    ]
    for cls in all_steps():
        schema = defs[cls.__name__]
        out += [
            f"## `{cls.type_name()}` — {cls.label}",
            "",
            cls.summary(),
            "",
            f"- 分類：{cls.category}",
            f"- 欄位效果：{_effect_text(cls)}",
            f"- 注意：{cls.ai_notes}" if cls.ai_notes else "",
            "",
            *_param_rows(schema, defs, ("id", "type", "enabled")),
            "",
        ]
        if cls.type_name() == "filter":
            out += ["條件（`conditions` 的每一項）依 `op` 分成四種：", ""]
            for name in ("TextCondition", "RegexCondition", "EmptyCondition", "NumberCondition"):
                out += [f"**{name}**", "", *_param_rows(defs[name], defs, ()), ""]
        for param, prop in schema.get("properties", {}).items():  # 物件陣列：列出每一項的欄位
            ref = prop.get("items", {}).get("$ref") if prop.get("type") == "array" else None
            if ref and "discriminator" not in prop["items"]:
                name = ref.rsplit("/", 1)[-1]
                out += [
                    f"`{param}` 的每一項（**{name}**）：",
                    "",
                    *_param_rows(defs[name], defs, ()),
                    "",
                ]
        for example in cls.examples:
            step = {"id": "s1", "type": cls.type_name(), **example.params}
            out += [
                f"### 範例：{example.title}",
                "",
                "```json",
                json.dumps(step, ensure_ascii=False),
                "```",
                "",
                *[
                    line
                    for name, data in (example.tables or {}).items()
                    for line in (f"另一張表（階段「{name}」的結果）：", "", *_table(data), "")
                ],
                "輸入：",
                "",
                *_table(example.input),
                "",
                "輸出：",
                "",
                *_table(example.output),
                "",
            ]
    return "\n".join(line for line in out if line is not None) + "\n"


# ── SKILL.md ────────────────────────────────────────────────────


BUTTONS = {
    "open": "開啟 Recipe",
    "load": "載入資料及預覽",
    "export": "處理並匯出資料",
    "menu": "Copilot",
    "report": "檢查並複製錯誤報告",
}
"""SKILL.md 提到的畫面上的按鈕與選單（tests/test_review_recipe.py 確認前端真的有這些字）。"""


def _quick_row(cls: type[Step]) -> str:
    """步驟速查表的一列：說明已經用名稱開頭時（「移除重複：…」）不再加一次名稱。"""
    summary = cls.summary()
    text = summary if summary.startswith(cls.label) else f"{cls.label}：{summary}"
    return f"| `{cls.type_name()}` | {text} |"


def _condition_steps(spec: dict[str, Any]) -> list[str]:
    """參數裡有條件（可以引用系統欄位）的步驟類型。"""
    defs = spec["schema"]["$defs"]
    targets = {f"#/$defs/{name}" for name in spec["system_column_defs"]}

    def uses(name: str, seen: set[str]) -> bool:
        if name in seen:
            return False
        seen.add(name)
        text = json.dumps(defs[name])
        if any(target in text for target in targets):
            return True
        refs = {r.rsplit("/", 1)[-1] for r in re.findall(r'"#/\$defs/([^"]+)"', text)}
        return any(uses(r, seen) for r in refs if r in defs)

    return [cls.type_name() for cls in all_steps() if uses(cls.__name__, set())]


def skill_markdown() -> str:
    spec = build_spec()
    quick = ["| type | 用途 |", "|---|---|"] + [_quick_row(cls) for cls in all_steps()]
    with_conditions = "、".join(f"`{name}`" for name in _condition_steps(spec))
    b = BUTTONS
    return f"""---
name: {SKILL_NAME}
description: 撰寫與驗證 Tablade recipe（資料清洗步驟的 JSON，recipe 版本 {RECIPE_VERSION}）。使用者要把 CSV、TXT 報表等凌亂的資料整理成表格，或要撰寫、修改、修正 Tablade recipe 時使用。
---

# Tablade recipe（版本 {RECIPE_VERSION}）

Tablade 是本機執行的資料清洗工具。使用者描述要怎麼整理資料，你寫出 recipe（JSON），
使用者再用 Tablade 開啟 recipe，套用到真實資料並輸出 Parquet 或 CSV。

## 工作流程

1. **看資料。** 讀使用者上傳的檔案（或 `tablade profile` 產生的摘要），找出：
   - 編碼（台灣的 Windows 匯出檔多半是 cp950；`encoding` 用 `"auto"` 會自動偵測）
   - 開頭的標題雜訊有幾行、結尾的合計或頁尾有幾行、中間有沒有空行
   - 分隔符號、有沒有引號包住的欄位（例如 `"1,280"`）
   - 有沒有只在群組第一列才寫值的欄位（要向下填滿）、要再拆開的欄位、要排除的列
2. **選讀取方式。** 凌亂的檔案用 `"reader": "text_lines"`（每一行原封不動讀成 `line` 欄）；
   只有乾淨、第一行就是標題的 CSV 才用 `"csv"`。Excel 用 `"reader": "excel"`：每一列都是資料（不當標題），
   欄名是 A、B、C…，`"sheet"` 寫工作表名稱（空白是第一個工作表，`"*"` 是每個檔的全部工作表：
   每個工作表各自處理，系統欄位 `__sheet` 記工作表名稱）。Parquet 檔用 `"parquet"`（欄位與型別照檔案）。
   資料放在 zip 裡的文字檔用 `"reader": "zip_lines"`（和 `text_lines` 一樣逐行讀，不用先解壓縮）：
   `files` 是 zip 的清單（例如 `"data/2026*.zip"`），`"members"` 必填，寫 zip 裡要讀的檔的相對路徑
   （例如 `"2026*/HEADER*.TXT"`；比對完整路徑、不分大小寫，`*` 不跨過 `/`，`**` 是任何層數的資料夾）。
   zip 裡每個符合的檔是一個來源，`__source_file` 是「zip 的路徑/zip 裡的路徑」。只支援文字檔，CSV、Excel 要先解壓縮；
   有密碼的 zip 不支援。zip 裡有好幾種檔時，一種檔一個階段（`members` 各寫各的）。
   檔案裡有用這個編碼解不開的字（例如 Big5 的造字）時，預設是報錯、整個檔讀不了。只有 `text_lines`、`zip_lines`
   可以加 `"decode_errors": "replace"`：解不開的字換成 `�`（2 位元組的字換成兩個 `�`，固定位置不會偏）照樣讀完，
   每個有換字的檔產生警告。使用者說資料有造字、或驗證時出現解不開的錯誤才加；要把 `�` 拿掉或換成別的記號，
   用 `replace_values` 步驟（不要在讀檔時丟掉）。不同的造字讀進來都是 `�`，分不出來，不要拿那種欄位當鍵。
   每一筆都用 CRLF 結尾、某個文字欄位裡有換行時（profile 的換行統計幾乎都是 CRLF、只有幾個 LF 或 CR，
   會出現「提示」；或有些行看起來是上一行的後半段，例如以 `"` 開頭），`text_lines`、`zip_lines` 加
   `"line_breaks": "crlf"`：只在 `\r\n` 切行，欄位裡單獨的 `\n`、`\r` 照原樣留在那一行（每個有的檔產生警告）。
   整個檔只用 LF 換行時不能加（會報錯）。要把欄位裡的換行換成空白，用 `trim_clean` 的「移除看不見的字元」。
   好幾份格式不同的資料要各自整理再接起來或對照時，分成好幾個階段（見下面「階段」）。
3. **依序寫步驟。** 只能用下面列出的步驟類型，參數細節見 `references/steps.md`。
   決定欄位結構的步驟要把欄名寫死：`split_delimited` 的 `columns`、`promote_headers` 的 `names`。
4. **驗證。** 執行 `python scripts/validate_recipe.py <recipe 檔> --json`
   （第一個階段的 `reader` 是 `csv` 時加上 `--data <資料檔>`，會用資料的標題列檢查欄位）。
   `severity` 是 `warning` 的只是提醒（例如沒有被用到的階段），不影響執行。
   有問題就依 `path`、`error`、`hint` 修正，直到 `"ok": true`。
5. **交給使用者。** 輸出檔名為 `<名稱>.recipe.json` 的 recipe，並提醒使用者：
   把 recipe 放在資料檔旁邊（或依各階段 `input.files` 的相對路徑），用 Tablade「{b["open"]}」後按「{b["load"]}」。
   Tablade 會用真實資料試跑；有問題時在「{b["menu"]}」選單按「{b["report"]}」，把報告貼回來，你再依報告修正。

## recipe 格式

```json
{{
  "tablade_recipe": "{RECIPE_VERSION}",
  "name": "名稱",
  "stages": [
    {{
      "name": "預設階段",
      "input": {{ "files": ["明細_*.csv"], "reader": "text_lines", "encoding": "auto" }},
      "steps": [
        {{ "id": "s1", "type": "remove_top_rows", "n": 2 }}
      ],
      "output_format": "parquet"
    }}
  ],
  "output": {{ "keep_system_columns": true }}
}}
```

- `tablade_recipe` 一定要是 `"{RECIPE_VERSION}"`。
- `files` 是檔案清單：每一項是**相對於 recipe 檔所在資料夾**的路徑（或絕對路徑），可用 `*` 一次讀多個檔案；依清單順序讀取。
- recipe 不記輸出資料夾（`output` 裡沒有 `path`）：使用者在 Tablade 按「{b["export"]}」時，一律寫到 recipe 檔所在資料夾底下的 `output/`，
  檔名是每個階段的 `output_file`（見下面「階段」）。
- 只有一份資料時就一個階段，名稱用 `"預設階段"`。舊的寫法（最外層的 `inputs` / `steps`）已經不支援。
- 每一列都帶系統欄位 `__source_file`（來源檔）與 `__line_no`（原始行號）。系統欄位**只能在條件裡引用**
  （{with_conditions} 的 `conditions`），其他參數只能用資料欄位。
  `__sheet` 只有讀 Excel 全部工作表（`"sheet": "*"`）時才有，`__stage` 只有讀前面階段的輸出時才有，
  `__orig_*` 要在 `merge_files` 之後才有。
- 「前 N 列」「後 N 列」「向下填滿」「把第一列作為欄位標題」「移除重複」「群組彙總」「樞紐」都是**每個來源檔各自計算**；
  要整份一起算就先用 `merge_files`（合併來源檔），之後 `__source_file` 是合併的名稱、`__line_no` 重新編號，
  原本的位置留在 `__orig_source_file`、`__orig_line_no`。

## 階段

- `stages` 依序執行。每個階段：`name`（不能重複，也是引用名稱）、`input`（讀什麼）、`steps`、`output_format`（必填）。
- `output_format`（必填，每個階段都要寫，沒寫檔的中間階段也一樣）：`"parquet"`（保留型別，給其他程式用）或 `"csv"`
  （UTF-8 加 BOM，Excel 可以直接打開；日期是 2026-01-05、空值是空白）。後面的階段讀的是 Tablade 自己暫存的結果，跟輸出格式無關。
- 每個階段可以有 `output_file`（只填檔名，例如 `"明細"`，副檔名照 `output_format` 補上），寫到 Recipe 所在資料夾底下的 `output/`，處理紀錄寫到並排的 `log/`。
  最後一個階段一定寫檔，沒填 `output_file` 時用 Recipe 的名稱；中間的階段有填才寫，沒填就只給後面的階段用（暫存）。
  檔名不能有路徑字元；主檔名在同一個 recipe 裡不能重複（`明細.csv` 和 `明細.parquet` 也算重複，處理紀錄都是 `log/明細.manifest.json`）。
- 讀前面階段的結果：`"input": {{ "reader": "stages", "stages": ["階段A", "階段B"] }}`（第一個階段不能這樣讀）。
  每個來源檔各自是一個來源，名稱是「@階段／檔名」，系統欄位 `__stage` 記階段；
  **附加**（上下接）就是讀好幾個階段，再用 `merge_files` 接成一份（同名欄的型別要一樣，缺的欄補空值）。
- **結合**（左右接、查表）用 `join` 步驟：`right` 寫前面某個階段的名稱，那個階段的結果就是另一張表。
  另一張表的鍵值不能重複（要先在那個階段 `dedupe`）；沒對到的列預設產生警告。
- 只能引用**前面**的階段；步驟 `id` 在整份 recipe 裡不能重複。
- 拆出來的值一律是文字；數值條件（`gt`、`le`…）比較前會去掉千分位逗號。
- 正規表示式是 Rust regex 語法：不支援 lookaround 與反向參照。`validate_recipe.py` 只檢查括號是否成對、群組數，
  以及這兩種寫法；其他語法錯誤以 Tablade 開啟 recipe（或 `tablade validate`）的結果為準。

## 步驟

{chr(10).join(quick)}

## 常見的清洗順序（凌亂的匯出檔）

1. `remove_top_rows` 去掉標題雜訊 → 2. `remove_bottom_rows` 去掉頁尾 → 3. `remove_blank_rows`
→ 4. `split_delimited` 拆成暫時的欄（欄1、欄2…）→ 5. `promote_headers` 用標題列命名
→ 6. `fill_down` 補齊群組欄位 → 7. `split_column` 拆開複合欄位 → 8. `filter` 排除不要的列

完整的範例 recipe 在 `references/examples/`。
"""


# ── 打包 ────────────────────────────────────────────────────────


def _validator_source() -> str:
    """驗證器的原始碼。打包後的 App 只有編譯過的模組，原始碼另外以資料檔放在 skill/ 底下。"""
    path = Path(validator_module.__file__)
    if path.is_file():
        return path.read_text(encoding="utf-8")
    from .app import resource_dir

    return (resource_dir() / "skill" / "validator.py").read_text(encoding="utf-8")


def validator_script(spec: dict[str, Any] | None = None) -> str:
    """驗證器原始碼，嵌入步驟規格（只改一行，其餘與 Tablade 用的完全相同）。"""
    source = _validator_source()
    if source.count(EMBED_LINE) != 1:
        raise RuntimeError("validator.py 找不到 EMBEDDED_SPEC 那一行")
    text = json.dumps(spec or build_spec(), ensure_ascii=False, indent=1)
    if "'''" in text:
        raise RuntimeError("步驟規格裡不能有三個連續的單引號")
    return source.replace(
        EMBED_LINE, f"EMBEDDED_SPEC: dict[str, Any] | None = json.loads(r'''{text}''')", 1
    )


def _example_recipes() -> dict[str, str]:
    from .app import resource_dir

    folder = resource_dir() / "examples"
    return {
        f"references/examples/{p.name}": p.read_text(encoding="utf-8")
        for p in sorted(folder.glob("*.recipe.json"))
    }


def skill_files() -> dict[str, str]:
    spec = build_spec()
    files = {
        "SKILL.md": skill_markdown(),
        "references/steps.md": steps_markdown(spec),
        "references/recipe.schema.json": json.dumps(spec["schema"], ensure_ascii=False, indent=2)
        + "\n",
        "scripts/validate_recipe.py": validator_script(spec),
        **_example_recipes(),
    }
    return files


PROBE_SKILL = """---
name: tablade-probe
description: 檢查這個 AI 環境能不能執行 Tablade skill 的 script。使用者說「執行 Tablade probe」時使用。
---

# Tablade probe

使用者說「執行 Tablade probe」時：

1. 執行 `python scripts/probe.py`；如果使用者在對話中上傳了檔案，把檔案路徑當參數傳進去。
2. 把 script 的輸出**原封不動**貼給使用者，不要摘要或省略。
"""

PROBE_SCRIPT = '''"""Tablade probe：回報執行環境（不讀取檔案內容，只列檔名、大小與編碼推測）。"""

import importlib.util
import os
import platform
import sys

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

print("== Tablade probe ==")
print("python:", sys.version.replace("\\n", " "))
print("platform:", platform.platform())
print("executable:", sys.executable)
print("cwd:", os.getcwd())
print("script:", os.path.abspath(__file__))

print("\\n== 套件 ==")
for name in ("pydantic", "pandas", "polars", "pyarrow", "numpy", "openpyxl", "chardet", "jsonschema"):
    print(f"{name}: {'有' if importlib.util.find_spec(name) else '沒有'}")

print("\\n== 看得到的檔案（只列名稱與大小）==")
seen = set()
for folder in [os.getcwd(), os.path.dirname(os.path.abspath(__file__)), "/mnt/data", "/mnt/user-data", "/tmp", os.path.expanduser("~")]:
    if folder in seen or not os.path.isdir(folder):
        continue
    seen.add(folder)
    try:
        names = sorted(os.listdir(folder))[:50]
    except OSError as err:
        print(folder, "無法列出：", err)
        continue
    print(folder)
    for name in names:
        path = os.path.join(folder, name)
        size = os.path.getsize(path) if os.path.isfile(path) else "資料夾"
        print("   ", name, size)

print("\\n== 參數指定的檔案 ==")
for arg in sys.argv[1:]:
    if not os.path.isfile(arg):
        print(arg, "找不到")
        continue
    raw = open(arg, "rb").read()
    guess = "無法判斷"
    for enc in ("utf-8-sig", "cp950"):
        try:
            text = raw.decode(enc)
            guess = enc
            break
        except UnicodeDecodeError:
            continue
    lines = raw.count(b"\\n") + (0 if raw.endswith(b"\\n") else 1)
    print(arg, f"{len(raw):,} bytes", "編碼:", guess, "行數:", lines)
print("\\n== 結束 ==")
'''


def probe_files() -> dict[str, str]:
    return {"SKILL.md": PROBE_SKILL, "scripts/probe.py": PROBE_SCRIPT}


def check_limits(files: dict[str, str]) -> list[str]:
    """檢查 Agent Builder 的限制，回傳違反的項目。"""
    problems = []
    if len(files) > LIMITS["files"]:
        problems.append(f"檔案數 {len(files)} 超過 {LIMITS['files']}")
    for name, text in files.items():
        path = PurePosixPath(name)
        if len(path.parts) > LIMITS["depth"]:
            problems.append(f"{name} 的目錄深度超過 {LIMITS['depth']}")
        if path.suffix not in ALLOWED_SUFFIXES:
            problems.append(f"{name} 的副檔名不在允許清單")
        if len(text.encode("utf-8")) > LIMITS["file_bytes"]:
            problems.append(f"{name} 超過 25 MB")
    body = files["SKILL.md"].split("---", 2)[-1]
    if len(body) >= LIMITS["instructions_chars"]:
        problems.append(
            f"SKILL.md 指示有 {len(body):,} 字元，超過 {LIMITS['instructions_chars']:,}"
        )
    return problems


def build_zip(probe: bool = False) -> bytes:
    files = probe_files() if probe else skill_files()
    problems = check_limits(files)
    if problems:
        raise RuntimeError("skill 不符合 Agent Builder 的限制：" + "；".join(problems))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, text in files.items():
            zf.writestr(name, text)
    data = buf.getvalue()
    if len(data) > LIMITS["zip_bytes"]:
        raise RuntimeError("skill zip 超過 50 MB")
    return data


def write_zip(path: Path, probe: bool = False) -> Path:
    path.write_bytes(build_zip(probe))
    return path
