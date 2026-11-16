"""產生 AI skill：讓 AI 助理（例如 Microsoft 365 Copilot）撰寫並驗證 recipe（DESIGN.md §17）。

skill 完全從 registry 產生：新增步驟後重新產生就會出現在步驟目錄、schema 與驗證器裡。
版本號＝recipe 版本（``RECIPE_VERSION``）。
"""

from __future__ import annotations

import io
import json
import zipfile
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
        return (
            f"文字陣列（至少 {prop.get('minItems', 0)} 項）"
            if items.get("type") == "string"
            else "陣列"
        )
    labels = {"string": "文字", "integer": "整數", "number": "數字", "boolean": "true / false"}
    text = labels.get(kind, str(kind))
    if "minimum" in prop:
        text += f"，≥ {prop['minimum']}"
    if "maxLength" in prop:
        text += f"，最多 {prop['maxLength']} 字元"
    return text


def _ui_text(prop: dict[str, Any]) -> str:
    return {
        "column": "引用現有欄位",
        "columns": "引用現有欄位（多個）",
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
    return template.format(**{k: values.get(k, "") for k in ("names", "source")})


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
        for example in cls.examples:
            step = {"id": "s1", "type": cls.type_name(), **example.params}
            out += [
                f"### 範例：{example.title}",
                "",
                "```json",
                json.dumps(step, ensure_ascii=False),
                "```",
                "",
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


def skill_markdown() -> str:
    quick = ["| type | 用途 |", "|---|---|"] + [
        f"| `{cls.type_name()}` | {cls.label}：{cls.summary()} |" for cls in all_steps()
    ]
    return f"""---
name: {SKILL_NAME}
description: 撰寫與驗證 Tablade recipe（資料清洗步驟的 JSON，recipe 版本 {RECIPE_VERSION}）。使用者要把 CSV、TXT 報表等凌亂的資料整理成表格，或要撰寫、修改、修正 Tablade recipe 時使用。
---

# Tablade recipe（版本 {RECIPE_VERSION}）

Tablade 是本機執行的資料清洗工具。使用者描述要怎麼整理資料，你寫出 recipe（JSON），
使用者再用 Tablade 開啟 recipe，套用到真實資料並輸出 Parquet。

## 工作流程

1. **看資料。** 讀使用者上傳的檔案（或 `tablade profile` 產生的摘要），找出：
   - 編碼（台灣的 Windows 匯出檔多半是 cp950；`encoding` 用 `"auto"` 會自動偵測）
   - 開頭的標題雜訊有幾行、結尾的合計或頁尾有幾行、中間有沒有空行
   - 分隔符號、有沒有引號包住的欄位（例如 `"1,280"`）
   - 有沒有只在群組第一列才寫值的欄位（要向下填滿）、要再拆開的欄位、要排除的列
2. **選讀取方式。** 凌亂的檔案用 `"reader": "text_lines"`（每一行原封不動讀成 `line` 欄）；
   只有乾淨、第一行就是標題的 CSV 才用 `"csv"`。
3. **依序寫步驟。** 只能用下面列出的步驟類型，參數細節見 `references/steps.md`。
   決定欄位結構的步驟要把欄名寫死：`split_delimited` 的 `columns`、`promote_headers` 的 `names`。
4. **驗證。** 執行 `python scripts/validate_recipe.py <recipe 檔> --json`
   （`reader` 是 `csv` 時加上 `--data <資料檔>`，會用資料的標題列檢查欄位）。
   有問題就依 `path`、`error`、`hint` 修正，直到 `"ok": true`。
5. **交給使用者。** 輸出檔名為 `<名稱>.recipe.json` 的 recipe，並提醒使用者：
   把 recipe 放在資料檔旁邊（或依 `inputs[0].pattern` 的相對路徑），用 Tablade「開啟 recipe」。
   Tablade 會用真實資料試跑；有問題時按「複製錯誤報告」貼回來，你再依報告修正。

## recipe 格式

```json
{{
  "tablade_recipe": "{RECIPE_VERSION}",
  "name": "名稱",
  "inputs": [{{ "id": "src", "pattern": "明細_*.csv", "reader": "text_lines", "encoding": "auto" }}],
  "steps": [
    {{ "id": "s1", "type": "remove_top_rows", "n": 2 }}
  ],
  "output": {{ "format": "parquet", "keep_system_columns": true }}
}}
```

- `tablade_recipe` 一定要是 `"{RECIPE_VERSION}"`。
- `pattern` 是**相對於 recipe 檔所在的資料夾**，可用 glob 一次讀多個檔案。
- recipe 不記輸出位置：使用者在 Tablade 按「執行」時才選存檔位置（`output` 裡沒有 `path`）。
- 目前一個 recipe 只能有一個 input。
- 每一列都帶系統欄位 `__source_file`（來源檔）與 `__line_no`（原始行號），可以在條件裡引用。
- 「前 N 列」「後 N 列」「向下填滿」「提升標題列」都是**每個來源檔各自計算**。
- 拆出來的值一律是文字；數值條件（`gt`、`le`…）比較前會去掉千分位逗號。
- 正規表示式是 Rust regex 語法：不支援 lookaround 與反向參照。

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
