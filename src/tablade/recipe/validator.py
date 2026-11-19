"""Tablade recipe 驗證器（DESIGN.md §17.4）。

**只用 Python 標準函式庫、不 import 任何 tablade 模組**：同一個檔案同時給 Tablade 使用，
也會原封不動放進 AI skill（Copilot 的沙箱不能安裝套件）。步驟規格（spec）由 Tablade 從
registry 產生後傳進來；放進 skill 時會嵌入在 ``EMBEDDED_SPEC``。

檢查分兩層：
1. 結構：版本號、JSON Schema（步驟類型、參數型別、必填、列舉…）
2. 語意：步驟 id 不重複、欄位引用存在、新欄名不衝突、欄位數相符

每個問題是 ``{"path", "error", "hint"?, "severity"}``，``path`` 例如 ``stages[0].steps[3].column``。
``severity`` 是 ``error``（不能執行）或 ``warning``（提醒，例如沒有被用到的階段）。

命令列（給 AI skill 使用）::

    python validate_recipe.py recipe.json [--data 資料檔.csv] [--json]
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import unicodedata
from difflib import get_close_matches
from pathlib import Path
from typing import Any

EMBEDDED_SPEC: dict[str, Any] | None = None
"""放進 AI skill 時，build 會把步驟規格嵌在這裡。"""

LANG = "zh"
"""訊息的語言（zh、en）：Tablade 切換畫面的語言時一起設（tablade/i18n.py 的 set_lang）；
AI skill 與命令列一律中文。這個檔不能 import tablade，所以自己有一個 t()。"""


def t(zh: str, en: str) -> str:
    """依 ``LANG`` 選一個（和 tablade/i18n.py 的 t 一樣）。"""
    return en if LANG == "en" else zh


def _plural(n: int, one: str, many: str) -> str:
    """英文的單複數（和 tablade/i18n.py 的 plural 一樣）：``_plural(3, "item", "items")`` → ``"3 items"``。"""
    return f"{n:,} {one if n == 1 else many}"


SYSTEM_COLUMNS = (
    "__source_file",
    "__line_no",
    "__sheet",
    "__stage",
    "__orig_source_file",
    "__orig_line_no",
    "__orig_sheet",
    "__orig_stage",
)
"""所有的系統欄位。每個階段實際有哪些，依讀取方式與步驟而定（``_semantic``）：
一律有 __source_file、__line_no；讀 Excel 全部工作表（sheet 是 ``*``）多 __sheet；
讀前面階段的輸出多 __stage（也帶著那些階段的系統欄位）；合併來源檔（spec 裡 ``combines`` 的步驟）
之後多 __orig_*（合併前有的才有）。系統欄位只能在條件裡引用（spec 的 ``system_column_defs``）。"""
BASE_SYSTEM = ("__source_file", "__line_no")
"""每個階段一定有的系統欄位。"""
ORIGINAL_COLUMNS = {
    "__source_file": "__orig_source_file",
    "__line_no": "__orig_line_no",
    "__sheet": "__orig_sheet",
    "__stage": "__orig_stage",
}
"""合併來源檔時，原本的來源檔、行號、工作表、階段存到這些系統欄位（tablade/steps/base.py 的 ORIGINAL）。"""
VERSION_RE = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")
"""版本號「主版本.次版本」：只能是半形數字、不能有多的 0（"1.00"、"01.0" 都不行）；用 fullmatch。"""
EFFECT_KINDS = (
    "keep",
    "add",
    "split",
    "replace_data",
    "rename_data",
    "drop",
    "select",
    "rename",
    "unpivot",
    "group",
    "pivot",
    "join",
)

Issue = dict[str, Any]


# ── 欄位效果（DESIGN.md §17.5）────────────────────────────────────


class EffectError(ValueError):
    """套用欄位效果時發現的問題（例如新欄名重複）。"""


def resolve_effect(template: dict[str, Any], step: dict[str, Any]) -> dict[str, Any]:
    """把樣板裡的 ``$參數名`` 換成這一步的參數值。"""

    def value(v: Any) -> Any:
        if isinstance(v, str) and v.startswith("$"):
            return step.get(v[1:])
        return v

    return {k: value(v) for k, v in template.items()}


def _check_new_names(names: list[str], existing: list[str]) -> None:
    seen: set[str] = set()
    for name in names:
        if name.startswith("__"):
            raise EffectError(
                t(
                    f"欄名「{name}」不能以 __ 開頭（保留給系統欄位）",
                    f'Column name "{name}" cannot start with __ (reserved for system columns)',
                )
            )
        if name in seen:
            raise EffectError(
                t(f"新欄名「{name}」重複", f'New column name "{name}" is listed more than once')
            )
        if name in existing:
            raise EffectError(
                t(f"新欄名「{name}」和現有欄位重複", f'New column name "{name}" already exists')
            )
        seen.add(name)


def apply_effect(
    columns: list[str] | None,
    effect: dict[str, Any],
    stages: dict[str, list[str] | None] | None = None,
) -> list[str] | None:
    """依欄位效果推算下一步的資料欄位；``None`` 表示目前不知道有哪些欄位。

    ``stages`` 是前面每個階段輸出的資料欄位（結合檢查右表的欄位用；不知道時不檢查）。
    """
    kind = effect["kind"]
    if kind == "keep":
        return columns
    if kind == "add":
        names = effect["names"]
        names = [names] if isinstance(names, str) else list(names)  # 新增一欄時可以只寫一個名稱
        if columns is None:
            return None
        _check_new_names(names, columns)
        return columns + names
    if kind == "split":
        names = list(effect["names"])
        source = effect["source"]
        keep_source = bool(effect.get("keep_source"))
        if columns is None:
            return None
        if source not in columns:
            raise EffectError(t(f"欄位「{source}」不存在", f'Column "{source}" does not exist'))
        idx = columns.index(source)
        rest = [c for c in columns if c != source or keep_source]
        _check_new_names(names, rest)
        before, after = columns[:idx], columns[idx + 1 :]
        return before + ([source] if keep_source else []) + names + after
    if kind == "replace_data":
        names = list(effect["names"])
        _check_new_names(names, [])
        return names
    if kind == "rename_data":
        names = list(effect["names"])
        _check_new_names(names, [])
        if columns is not None and len(columns) != len(names):
            raise EffectError(
                t(
                    f"目前有 {len(columns)} 個資料欄，但列了 {len(names)} 個欄名",
                    f"The data has {_plural(len(columns), 'column', 'columns')}, "
                    f"but {_plural(len(names), 'name is', 'names are')} listed",
                )
            )
        return names
    if kind == "drop":
        return _drop(columns, effect)
    if kind == "select":
        return _select(columns, list(effect.get("names") or []))
    if kind == "rename":
        return _rename(columns, dict(effect.get("names") or {}))
    if kind == "unpivot":
        return _unpivot(columns, effect)
    if kind == "group":
        return _group(columns, effect)
    if kind == "pivot":
        return _pivot(columns, effect)
    if kind == "join":
        return _join(columns, effect, stages or {})
    raise EffectError(t(f"未知的欄位效果：{kind}", f"Unknown column effect: {kind}"))


def _missing(columns: list[str], names: list[str]) -> None:
    missing = [n for n in names if n not in columns]
    if missing:
        raise EffectError(
            t(
                f"欄位不存在：{'、'.join(missing)}",
                f"{'Column' if len(missing) == 1 else 'Columns'} not found: {', '.join(missing)}",
            )
        )


def _select(columns: list[str] | None, names: list[str]) -> list[str] | None:
    """保留和排序欄位：依清單順序只留下這些欄。"""
    if not names:
        raise EffectError(t("要選至少一個要保留的欄位", "Choose at least one column to keep"))
    repeated = sorted({n for n in names if names.count(n) > 1})
    if repeated:
        raise EffectError(
            t(
                f"欄位重複：{'、'.join(repeated)}",
                f"Listed more than once: {', '.join(repeated)}",
            )
        )
    if columns is None:
        return None
    _missing(columns, names)
    return names


def _rename(columns: list[str] | None, names: dict[str, str]) -> list[str] | None:
    """改欄名：{舊名: 新名}；改完後不能重複（可以互換），新名不能以 __ 開頭。"""
    if not names:
        raise EffectError(t("要填至少一個新欄名", "Enter at least one new column name"))
    for new in names.values():
        if new.startswith("__"):
            raise EffectError(
                t(
                    f"欄名「{new}」不能以 __ 開頭（保留給系統欄位）",
                    f'Column name "{new}" cannot start with __ (reserved for system columns)',
                )
            )
    if columns is None:
        return None
    _missing(columns, list(names))
    result = [names.get(c, c) for c in columns]
    repeated = sorted({n for n in result if result.count(n) > 1})
    if repeated:
        raise EffectError(
            t(
                f"改完後欄名重複：{'、'.join(repeated)}",
                f"Duplicate column {'name' if len(repeated) == 1 else 'names'} after renaming: "
                f"{', '.join(repeated)}",
            )
        )
    return result


def _unique(names: list[str]) -> list[str]:
    return list(dict.fromkeys(names))


def _unpivot(columns: list[str] | None, effect: dict[str, Any]) -> list[str] | None:
    """取消樞紐：沒攤開的欄保留，後面接「名稱欄、值欄」。"""
    picked = _unique(list(effect.get("columns") or []))
    if not picked:
        raise EffectError(t("要選至少一個要攤開的欄位", "Choose at least one column to unpivot"))
    if columns is None:
        return None
    _missing(columns, picked)
    kept = [c for c in columns if c not in picked]
    _check_new_names([effect["name"], effect["value"]], kept)
    return kept + [effect["name"], effect["value"]]


def _group(columns: list[str] | None, effect: dict[str, Any]) -> list[str] | None:
    """群組彙總：分組欄，後面接每個彙總的新欄名與筆數欄。分組後的欄位不看之前有哪些欄。"""
    by = _unique(list(effect.get("by") or []))
    aggregations = list(effect.get("aggregations") or [])
    names = [a["name"] for a in aggregations] + ([effect["count"]] if effect.get("count") else [])
    if not names:
        raise EffectError(
            t(
                "要有至少一項彙總，或另外加一欄筆數",
                "Add at least one aggregation, or a row count column",
            )
        )
    _check_new_names(names, by)
    if columns is not None:
        _missing(columns, _unique(by + [a["column"] for a in aggregations]))
    return by + names


def _pivot(columns: list[str] | None, effect: dict[str, Any]) -> list[str] | None:
    """樞紐：樞紐表格列依據欄位，後面接要變成欄的值。"""
    index = _unique(list(effect.get("index") or []))
    names = list(effect.get("names") or [])
    if not names:
        raise EffectError(
            t("要列出至少一個要變成欄的值", "List at least one value to turn into a column")
        )
    for role, name in ((t("欄", "Column"), effect["on"]), (t("值", "Value"), effect["values"])):
        if name in index:
            raise EffectError(
                t(
                    f"「{name}」已經是樞紐表格列依據欄位，不能再當樞紐表格{role}依據欄位",
                    f'"{name}" is already one of the Row fields; it cannot also be the {role} field',
                )
            )
    _check_new_names(names, index)
    if columns is not None:
        _missing(columns, _unique(index + [effect["on"], effect["values"]]))
    return index + names


def _join(
    columns: list[str] | None, effect: dict[str, Any], stages: dict[str, list[str] | None]
) -> list[str] | None:
    """結合：左連接、內連接時在後面接上從右表帶進來的欄；只留沒對到／有對到的欄位不變。"""
    pairs = [p for p in effect.get("on") or [] if isinstance(p, dict)]
    if not pairs:
        raise EffectError(t("要設定至少一組鍵", "Set at least one key"))
    keep_only = effect.get("how") in ("anti", "semi")
    names = [] if keep_only else list(effect.get("names") or [])
    right_keys = [p.get("right") for p in pairs]
    keyed = [n for n in names if n in right_keys]
    if keyed:
        raise EffectError(
            t(
                f"「{'、'.join(keyed)}」是另一張表的鍵，不用帶進來",
                f"No need to bring in the other table's keys: {', '.join(keyed)}",
            )
        )
    right = stages.get(effect.get("stage"))
    if right is not None:
        missing = [n for n in _unique(right_keys + names) if n not in right]
        if missing:
            raise EffectError(
                t(
                    f"階段「{effect.get('stage')}」沒有欄位：{'、'.join(missing)}"
                    f"（它的欄位：{'、'.join(right)}）",
                    f'Stage "{effect.get("stage")}" has no '
                    f"{'column' if len(missing) == 1 else 'columns'} {', '.join(missing)} "
                    f"(its columns: {', '.join(right)})",
                )
            )
    if columns is None:
        return None
    _missing(columns, _unique([p.get("left") for p in pairs]))
    if keep_only:
        return columns
    _check_new_names(names, columns)
    return columns + names


def _drop(columns: list[str] | None, effect: dict[str, Any]) -> list[str] | None:
    """刪除欄：依名稱（names）或依位置（positions，第幾個資料欄，從 1 起）。"""
    by_position = effect.get("by") == "position"
    picked = list(effect.get("positions") or []) if by_position else list(effect.get("names") or [])
    if not picked:
        raise EffectError(t("要選至少一個要刪除的欄位", "Choose at least one column to remove"))
    if columns is None:
        return None
    if by_position:
        bad = [p for p in picked if not isinstance(p, int) or not 1 <= p <= len(columns)]
        if bad:
            shown = t("、", ", ").join(str(p) for p in bad)
            raise EffectError(
                t(
                    f"目前只有 {len(columns)} 個資料欄，沒有第 {shown} 欄",
                    f"The data has only {_plural(len(columns), 'column', 'columns')}; "
                    f"there is no column {shown}",
                )
            )
        drop = {columns[p - 1] for p in picked}
    else:
        missing = [n for n in picked if n not in columns]
        if missing:
            raise EffectError(
                t(
                    f"欄位不存在：{'、'.join(missing)}",
                    f"{'Column' if len(missing) == 1 else 'Columns'} not found: "
                    f"{', '.join(missing)}",
                )
            )
        drop = set(picked)
    rest = [c for c in columns if c not in drop]
    if not rest:
        raise EffectError(
            t(
                "不能刪除所有資料欄，至少要留下一個",
                "Cannot remove every data column; keep at least one",
            )
        )
    return rest


# ── JSON Schema（pydantic 產生的子集）───────────────────────────────


def _fmt(path: list[str | int]) -> str:
    out = ""
    for part in path:
        out += f"[{part}]" if isinstance(part, int) else (f".{part}" if out else part)
    return out or "(Recipe)"


def _issue(
    path: list[str | int], error: str, hint: str | None = None, severity: str = "error"
) -> Issue:
    item: Issue = {"path": _fmt(path), "error": error, "severity": severity}
    if hint:
        item["hint"] = hint
    return item


def _close(word: Any, options: list[str]) -> str | None:
    if not isinstance(word, str):
        return None
    match = get_close_matches(word, options, n=1, cutoff=0.5)
    return t(f"是不是「{match[0]}」？", f'Did you mean "{match[0]}"?') if match else None


_JSON_TYPES = {
    "string": str,
    "array": list,
    "object": dict,
    "boolean": bool,
    "null": type(None),
}


def _is_type(value: Any, name: str) -> bool:
    if name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if name == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return isinstance(value, _JSON_TYPES[name])


_TYPE_LABELS = {
    "string": "文字",
    "integer": "整數",
    "number": "數字",
    "boolean": "true / false",
    "array": "陣列",
    "object": "物件",
    "null": "null",
}
_TYPE_LABELS_EN = {
    "string": "text",
    "integer": "an integer",
    "number": "a number",
    "boolean": "true / false",
    "array": "a list",
    "object": "an object",
    "null": "null",
}
"""``_TYPE_LABELS`` 的英文（模組最上層不能用 t() 算好，用到時才依 ``LANG`` 選）。"""


def _rust_pattern(pattern: str) -> str:
    """JSON Schema 的 ``pattern`` 照 Rust（pydantic）的意思換成 Python 的寫法：結尾的 ``$`` 是字串結尾。

    Python 的 ``$`` 也會比對到「結尾的換行之前」，``"s1\\n"`` 會通過 ``^[A-Za-z0-9_-]+$``，pydantic 不會。"""
    if pattern.endswith("$") and not pattern.endswith("\\$"):
        return pattern[:-1] + r"\Z"
    return pattern


class _Schema:
    def __init__(self, root: dict[str, Any], system_defs: list[str] | None = None) -> None:
        self.defs = root.get("$defs", {})
        self.root = root
        self.system_defs = set(system_defs or [])
        """這些物件裡的欄位引用可以是系統欄位（篩選等步驟的條件）。"""

    def resolve(self, schema: dict[str, Any]) -> dict[str, Any]:
        while "$ref" in schema:
            schema = self.defs[schema["$ref"].rsplit("/", 1)[-1]]
        return schema

    def check(self, value: Any, schema: dict[str, Any], path: list[str | int]) -> list[Issue]:
        schema = self.resolve(schema)
        issues: list[Issue] = []
        for sub in schema.get("allOf", []):
            issues += self.check(value, sub, path)

        if "anyOf" in schema:
            results = [self.check(value, sub, path) for sub in schema["anyOf"]]
            if not any(not r for r in results):
                issues += min(results, key=len)
            return issues

        if "oneOf" in schema:
            disc = schema.get("discriminator")
            if disc and isinstance(value, dict):
                prop, mapping = disc["propertyName"], disc["mapping"]
                tag = value.get(prop)
                if tag is None:
                    return issues + [_issue(path + [prop], t(f"缺少 {prop}", f"Missing {prop}"))]
                if not isinstance(tag, str):  # 例如 ["filter"]：清單、物件不能拿來查表
                    return issues + [
                        _issue(
                            path + [prop],
                            t("型別錯誤：應該是文字", "Wrong type: should be text"),
                            _close(tag[0], list(mapping))
                            if isinstance(tag, list) and len(tag) == 1
                            else None,
                        )
                    ]
                if tag not in mapping:
                    hint = _close(tag, list(mapping))
                    allowed = t("、", ", ").join(mapping)
                    return issues + [
                        _issue(
                            path + [prop],
                            t(
                                f"不支援的 {prop}：{tag}（可用：{allowed}）",
                                f"Unsupported {prop}: {tag} (allowed: {allowed})",
                            ),
                            hint,
                        )
                    ]
                return issues + self.check(value, {"$ref": mapping[tag]}, path)
            results = [self.check(value, sub, path) for sub in schema["oneOf"]]
            passed = sum(1 for r in results if not r)
            if passed != 1:
                issues += (
                    min(results, key=len)
                    if passed == 0
                    else [_issue(path, t("格式不明確", "Ambiguous format"))]
                )
            return issues

        if "const" in schema and value != schema["const"]:
            const = json.dumps(schema["const"], ensure_ascii=False)
            return issues + [_issue(path, t(f"必須是 {const}", f"Must be {const}"))]
        if "enum" in schema and value not in schema["enum"]:
            allowed = t("、", ", ").join(json.dumps(v, ensure_ascii=False) for v in schema["enum"])
            return issues + [
                _issue(
                    path,
                    t(
                        f"不支援的值：{value!r}（可用：{allowed}）",
                        f"Unsupported value: {value!r} (allowed: {allowed})",
                    ),
                    _close(value, [str(v) for v in schema["enum"]]),
                )
            ]

        types = schema.get("type")
        if types:
            types = [types] if isinstance(types, str) else types
            if not any(_is_type(value, name) for name in types):
                expected = t(" 或 ", " or ").join(
                    t(_TYPE_LABELS.get(name, name), _TYPE_LABELS_EN.get(name, name))
                    for name in types
                )
                return issues + [
                    _issue(
                        path, t(f"型別錯誤：應該是{expected}", f"Wrong type: should be {expected}")
                    )
                ]

        if isinstance(value, str):
            if len(value) < schema.get("minLength", 0):
                issues.append(
                    _issue(
                        path,
                        t("不能是空字串", "Cannot be empty")
                        if schema["minLength"] == 1
                        else t(
                            f"至少 {schema['minLength']} 個字元",
                            f"At least {_plural(schema['minLength'], 'character', 'characters')}",
                        ),
                    )
                )
            if "maxLength" in schema and len(value) > schema["maxLength"]:
                issues.append(
                    _issue(
                        path,
                        t(
                            f"最多 {schema['maxLength']} 個字元",
                            f"At most {_plural(schema['maxLength'], 'character', 'characters')}",
                        ),
                    )
                )
            if "pattern" in schema and not re.search(_rust_pattern(schema["pattern"]), value):
                issues.append(
                    _issue(
                        path,
                        t(
                            f"格式不符（{schema['pattern']}）",
                            f"Invalid format ({schema['pattern']})",
                        ),
                    )
                )
        if _is_type(value, "number"):
            if "minimum" in schema and value < schema["minimum"]:
                issues.append(
                    _issue(
                        path,
                        t(
                            f"不能小於 {schema['minimum']}",
                            f"Cannot be less than {schema['minimum']}",
                        ),
                    )
                )
            if "maximum" in schema and value > schema["maximum"]:
                issues.append(
                    _issue(
                        path,
                        t(
                            f"不能大於 {schema['maximum']}",
                            f"Cannot be greater than {schema['maximum']}",
                        ),
                    )
                )
            if "exclusiveMinimum" in schema and value <= schema["exclusiveMinimum"]:
                issues.append(
                    _issue(
                        path,
                        t(
                            f"必須大於 {schema['exclusiveMinimum']}",
                            f"Must be greater than {schema['exclusiveMinimum']}",
                        ),
                    )
                )
        if isinstance(value, list):
            if len(value) < schema.get("minItems", 0):
                issues.append(
                    _issue(
                        path,
                        t(
                            f"至少要有 {schema['minItems']} 項",
                            f"Needs at least {_plural(schema['minItems'], 'item', 'items')}",
                        ),
                    )
                )
            if "maxItems" in schema and len(value) > schema["maxItems"]:
                issues.append(
                    _issue(
                        path,
                        t(
                            f"最多 {schema['maxItems']} 項",
                            f"At most {_plural(schema['maxItems'], 'item', 'items')}",
                        ),
                    )
                )
            if "items" in schema:
                for i, item in enumerate(value):
                    issues += self.check(item, schema["items"], path + [i])
        if isinstance(value, dict):
            props = schema.get("properties", {})
            extra = schema.get("additionalProperties")
            if len(value) < schema.get("minProperties", 0):
                issues.append(
                    _issue(
                        path,
                        t(
                            f"至少要有 {schema['minProperties']} 項",
                            f"Needs at least {_plural(schema['minProperties'], 'item', 'items')}",
                        ),
                    )
                )
            for name in schema.get("required", []):
                if name not in value:
                    issues.append(_issue(path + [name], t("必填，請填寫", "Required")))
            for name, item in value.items():
                if len(name) < schema.get("propertyNames", {}).get("minLength", 0):
                    issues.append(
                        _issue(path + [name], t("名稱不能是空字串", "Name cannot be empty"))
                    )
                if name in props:
                    issues += self.check(item, props[name], path + [name])
                elif extra is False:
                    issues.append(
                        _issue(
                            path + [name],
                            t("不認得的欄位", "Unknown field"),
                            _close(name, list(props)),
                        )
                    )
                elif isinstance(extra, dict):  # 例如改欄名的 {舊名: 新名}
                    issues += self.check(item, extra, path + [name])
        return issues

    def column_refs(
        self, value: Any, schema: dict[str, Any], path: list[str | int], system: bool = False
    ) -> list[tuple[list[str | int], str, bool]]:
        """找出參數裡標記為 ``ui: column`` / ``ui: columns`` 的欄位引用：（路徑, 欄名, 可不可以是系統欄位）。

        只有 ``system_defs`` 列的物件（條件）裡的引用可以是系統欄位；其他參數只能是資料欄位。
        """
        if "$ref" in schema:
            system = system or schema["$ref"].rsplit("/", 1)[-1] in self.system_defs
        schema = self.resolve(schema)
        ui = schema.get("ui")
        if ui == "column" and isinstance(value, str):
            return [(path, value, system)]
        if ui in ("columns", "column_order") and isinstance(value, list):
            return [(path + [i], v, system) for i, v in enumerate(value) if isinstance(v, str)]
        if ui == "rename_map" and isinstance(value, dict):
            return [(path + [k], k, system) for k in value if isinstance(k, str)]
        refs: list[tuple[list[str | int], str, bool]] = []
        disc = schema.get("discriminator")
        if disc and isinstance(value, dict):
            tag = value.get(disc["propertyName"])
            if isinstance(tag, str) and tag in disc["mapping"]:
                return self.column_refs(value, {"$ref": disc["mapping"][tag]}, path, system)
        for sub in schema.get("anyOf", []) + schema.get("allOf", []):
            refs += self.column_refs(value, sub, path, system)
        if isinstance(value, list) and "items" in schema:
            for i, item in enumerate(value):
                refs += self.column_refs(item, schema["items"], path + [i], system)
        if isinstance(value, dict):
            for name, sub in schema.get("properties", {}).items():
                if name in value:
                    refs += self.column_refs(value[name], sub, path + [name], system)
        return refs

    def regex_fields(
        self,
        value: Any,
        schema: dict[str, Any],
        path: list[str | int],
        owner: dict[str, Any] | None = None,
    ) -> list[tuple[list[str | int], str]]:
        """找出參數裡標記為 ``ui: regex`` 的正規表示式。

        ``ui_show_if``（看同一個物件裡的其他參數）不成立的不算：那時不會用到，例如日期樣式不是自訂時的 regex。
        """
        schema = self.resolve(schema)
        if schema.get("ui") == "regex":
            context = owner or {}
            shown = all(
                (context.get(k) in v) if isinstance(v, list) else context.get(k) == v
                for k, v in (schema.get("ui_show_if") or {}).items()
            )
            return [(path, value)] if shown and isinstance(value, str) else []
        found: list[tuple[list[str | int], str]] = []
        disc = schema.get("discriminator")
        if disc and isinstance(value, dict):
            tag = value.get(disc["propertyName"])
            if isinstance(tag, str) and tag in disc["mapping"]:
                return self.regex_fields(value, {"$ref": disc["mapping"][tag]}, path, owner)
        for sub in schema.get("anyOf", []) + schema.get("allOf", []):
            found += self.regex_fields(value, sub, path, owner)
        if isinstance(value, list) and "items" in schema:
            for i, item in enumerate(value):
                found += self.regex_fields(item, schema["items"], path + [i], owner)
        if isinstance(value, dict):
            for name, sub in schema.get("properties", {}).items():
                if name in value:
                    found += self.regex_fields(value[name], sub, path + [name], value)
        return found


# ── 正規表示式與步驟自己的規則 ────────────────────────────────────


class RegexScan:
    """不編譯、只掃一遍正規表示式：群組數，以及 Tablade 一定不接受的寫法。

    Tablade 用的是 Rust 的正規表示式（Polars），Python 的 ``re`` 語法不一樣（例如 Rust 的 ``\\p{Han}``、
    ``(?<名稱>…)`` Python 不認得），不能用 ``re.compile`` 判斷對錯。這裡只抓兩邊確定一樣的：
    環顧（look-around）、反向參照、括號不成對；其他語法錯誤以 Tablade 試跑的結果為準。
    """

    def __init__(self, pattern: str) -> None:
        self.groups = 0
        self.error: str | None = None
        self._scan(pattern)

    def _scan(self, p: str) -> None:
        depth, klass, i, n = 0, 0, 0, len(p)
        while i < n:
            c = p[i]
            if c == "\\":
                if p[i + 1 : i + 2].isdigit():  # Rust 在 [ ] 裡也不接受
                    self.error = t(
                        f"Tablade 的正規表示式不支援反向參照（{p[i : i + 2]}）",
                        f"Tablade's regular expressions don't support backreferences ({p[i : i + 2]})",
                    )
                    return
                i += 2
                continue
            if klass:
                if p.startswith("[:", i):  # [[:alpha:]] 這種 POSIX 類別
                    end = p.find(":]", i + 2)
                    i = end + 2 if end >= 0 else i + 1
                    continue
                if c == "[":
                    klass += 1
                    i = self._class_start(p, i + 1)
                    continue
                if c == "]":
                    klass -= 1
                i += 1
                continue
            if c == "[":
                klass = 1
                i = self._class_start(p, i + 1)
                continue
            if c == "(":
                depth += 1
                if p.startswith(("(?=", "(?!", "(?<=", "(?<!"), i):
                    self.error = t(
                        "Tablade 的正規表示式不支援環顧（look-ahead／look-behind，例如 (?=…)、(?<!…)）",
                        "Tablade's regular expressions don't support look-around "
                        "(look-ahead / look-behind such as (?=…) or (?<!…))",
                    )
                    return
                if not p.startswith("(?", i) or p.startswith(("(?P<", "(?<"), i):
                    self.groups += 1  # 一般的群組、具名群組；(?:…)、(?i) 這些不算
            elif c == ")":
                depth -= 1
                if depth < 0:
                    self.error = t("括號不成對：多了一個 )", "Unbalanced parentheses: an extra )")
                    return
            i += 1
        if klass:
            self.error = t("[ ] 不成對：少了 ]", "Unbalanced brackets: a ] is missing")
        elif depth:
            self.error = t("括號不成對：少了 )", "Unbalanced parentheses: a ) is missing")

    @staticmethod
    def _class_start(p: str, i: int) -> int:
        """[ 後面的位置：開頭的 ^ 與緊接著的 ]（字面上的 ]）略過。"""
        if p.startswith("^", i):
            i += 1
        return i + 1 if p.startswith("]", i) else i


def step_rules(
    step: dict[str, Any], step_schema: dict[str, Any], schema: _Schema, path: list[str | int]
) -> list[Issue]:
    """步驟自己的規則（pydantic 的 field_validator；tests/test_review_recipe.py 確認兩邊一致）。

    結構正確的步驟才檢查。正規表示式只檢查 ``RegexScan`` 做得到的部分。
    """
    issues: list[Issue] = []
    kind = step.get("type")
    regexes = schema.regex_fields(step, step_schema, path, step)
    if kind == "replace_values" and step.get("match") == "regex":
        regexes += [
            (path + ["replacements", k, "find"], item.get("find"))
            for k, item in enumerate(step.get("replacements") or [])
            if isinstance(item, dict) and isinstance(item.get("find"), str)
        ]
    scans: dict[str, RegexScan] = {}
    for where, pattern in regexes:
        scans[_fmt(where)] = scan = RegexScan(pattern)
        if scan.error:
            issues.append(
                _issue(
                    where,
                    scan.error,
                    t(
                        "Tablade 用 Rust 的正規表示式語法；其他語法錯誤要用 Tablade 開啟才查得到",
                        "Tablade uses Rust regular expression syntax; other syntax errors show "
                        "up when the Recipe is opened in Tablade",
                    ),
                )
            )
    if kind == "extract_regex":
        scan = scans.get(_fmt(path + ["pattern"]))
        names = step.get("names")
        if scan and not scan.error and isinstance(names, list):
            if scan.groups == 0:
                issues.append(
                    _issue(
                        path + ["names"],
                        t(
                            "正規表示式沒有括號群組：要擷取的部分請用 ( ) 括起來",
                            "The regular expression has no groups: put the part to extract in ( )",
                        ),
                    )
                )
            elif scan.groups != len(names):
                issues.append(
                    _issue(
                        path + ["names"],
                        t(
                            f"正規表示式有 {scan.groups} 個群組，但列了 {len(names)} 個新欄名",
                            f"The regular expression has {_plural(scan.groups, 'group', 'groups')}, "
                            f"but {_plural(len(names), 'new column name is', 'new column names are')} "
                            "given",
                        ),
                    )
                )
    elif kind == "split_fixed_width":
        starts = step.get("starts")
        if isinstance(starts, list) and all(_is_type(p, "integer") for p in starts):
            if any(p < 1 for p in starts):
                issues.append(
                    _issue(path + ["starts"], t("開始位置從 1 起算", "Start positions begin at 1"))
                )
            elif any(starts[k] <= starts[k - 1] for k in range(1, len(starts))):
                issues.append(
                    _issue(
                        path + ["starts"],
                        t("開始位置要由小到大", "Start positions must be in increasing order"),
                    )
                )
            names = step.get("names")
            if isinstance(names, list) and len(names) != len(starts):
                issues.append(
                    _issue(
                        path + ["names"],
                        t(
                            f"有 {len(starts)} 個開始位置，但列了 {len(names)} 個欄名",
                            f"{_plural(len(starts), 'start position', 'start positions')}, "
                            f"but {_plural(len(names), 'column name', 'column names')} given",
                        ),
                    )
                )
    elif kind == "parse_date" and step.get("pattern") == "custom":
        regex = step.get("regex", "")
        scan = scans.get(_fmt(path + ["regex"]))
        if isinstance(regex, str) and not regex.strip():
            issues.append(
                _issue(
                    path + ["regex"],
                    t(
                        "選「自訂正規表示式」時要輸入正規表示式",
                        'Enter a regular expression when "Custom regular expression" is selected',
                    ),
                )
            )
        elif scan and not scan.error and scan.groups != 3:
            issues.append(
                _issue(
                    path + ["regex"],
                    t(
                        f"要剛好三個群組（年、月、日），這個正規表示式有 {scan.groups} 個",
                        "Needs exactly three groups (year, month, day); this regular expression "
                        f"has {scan.groups}",
                    ),
                )
            )
    if kind in ("split_delimited", "split_column"):
        issues += _merge_column_rules(
            step, path, "columns" if kind == "split_delimited" else "names"
        )
    return issues


def _merge_column_rules(step: dict[str, Any], path: list[str | int], names_key: str) -> list[Issue]:
    """拆欄的 ``merge_column``（和 ``steps/split.py`` 的 ``check_merge_column`` 一樣）。"""
    value, names = step.get("merge_column", ""), step.get(names_key)
    where = path + ["merge_column"]
    if not isinstance(value, str):
        return []
    if step.get("on_extra") == "merge_into":
        if not value:
            return [
                _issue(
                    where,
                    t("要選多的段併回哪一欄", "Choose the column to merge the extra parts into"),
                )
            ]
        if isinstance(names, list) and value not in names:
            return [
                _issue(
                    where,
                    t(
                        f"「{value}」不是拆出來的欄名",
                        f'"{value}" is not one of the new column names',
                    ),
                )
            ]
    elif value:
        return [
            _issue(
                where,
                t(
                    "只有「段數比欄名多時」選「多的段併回指定的欄」才能填 merge_column",
                    "merge_column can be set only when extra parts are merged into a chosen column",
                ),
            )
        ]
    return []


# ── 驗證 ─────────────────────────────────────────────────────────


def check_version(value: Any, current: str) -> Issue | None:
    path = ["tablade_recipe"]
    if not isinstance(value, str) or not VERSION_RE.fullmatch(value):
        return _issue(
            path,
            t(
                '版本號格式應該是 "主版本.次版本"，例如 "1.0"',
                'The version should be "major.minor", for example "1.0"',
            ),
            t(f'目前的版本是 "{current}"', f'The current version is "{current}"'),
        )
    major, minor = (int(x) for x in VERSION_RE.fullmatch(value).groups())
    cur_major, cur_minor = (int(x) for x in VERSION_RE.fullmatch(current).groups())
    if major != cur_major:
        return _issue(
            path,
            t(
                f"不相容的 Recipe 版本 {value}（這裡支援 {cur_major}.x）",
                f"Incompatible Recipe version {value} (this version supports {cur_major}.x)",
            ),
            t(
                f"請改用 Recipe 版本 {current} 的 skill 或 Tablade",
                f"Use the skill or Tablade for Recipe version {current}",
            ),
        )
    if minor > cur_minor:
        return _issue(
            path,
            t(
                f"Recipe 版本 {value} 比這裡支援的 {current} 新",
                f"Recipe version {value} is newer than the supported version {current}",
            ),
            t("請更新 Tablade 或 skill", "Update Tablade or the skill"),
        )
    return None


def validate(
    recipe: Any,
    spec: dict[str, Any],
    input_columns: dict[str, list[str]] | None = None,
) -> list[Issue]:
    """驗證 recipe，回傳問題清單（空清單代表通過）。

    ``input_columns`` 可提供各 input 的欄位（例如從 CSV 標題列讀到的），用來檢查欄位引用。
    沒預期到的寫法讓檢查本身出錯時，也回報成一個問題（AI 一定拿得到 ``path`` 與 ``error``）。
    """
    try:
        return _validate(recipe, spec, input_columns)
    except Exception as err:  # 驗證器不能自己當掉，要回報成問題
        return [
            _issue(
                [],
                t(
                    f"驗證器無法檢查這份 Recipe（{type(err).__name__}: {err}）",
                    f"The validator could not check this Recipe ({type(err).__name__}: {err})",
                ),
                t(
                    "請確認每個欄位的型別（文字、數字、清單、物件）和步驟目錄寫的一樣",
                    "Check that every field has the type the step catalog lists "
                    "(text, number, list, object)",
                ),
            )
        ]


def _validate(
    recipe: Any,
    spec: dict[str, Any],
    input_columns: dict[str, list[str]] | None = None,
) -> list[Issue]:
    if not isinstance(recipe, dict):
        return [_issue([], t("Recipe 必須是 JSON 物件", "The Recipe must be a JSON object"))]
    issues: list[Issue] = []
    version_issue = check_version(recipe.get("tablade_recipe"), spec["recipe_version"])
    if version_issue:
        issues.append(version_issue)

    if "stages" not in recipe and any(k in recipe for k in ("inputs", "steps")):
        issues.append(
            _issue(
                [],
                t(
                    "這是舊的 Recipe 寫法：最外層的 inputs / steps 要改成 stages",
                    "This Recipe uses the old format: change the top-level inputs / steps to stages",
                ),
                t(
                    'stages 的每一項是一個階段：{"name": "預設階段", "input": {原本 inputs[0]，去掉 id}, '
                    '"steps": [原本的 steps], "output_format": "parquet"}（output_format 必填，'
                    '也可以是 "csv"）；output 留在最外層',
                    'Each item in stages is a stage: {"name": "Stage 1", '
                    '"input": {the old inputs[0] without id}, "steps": [the old steps], '
                    '"output_format": "parquet"} (output_format is required; it can also be '
                    '"csv"); output stays at the top level',
                ),
            )
        )
        return issues
    schema = _Schema(spec["schema"], spec.get("system_column_defs"))
    structural = [
        i for i in schema.check(recipe, spec["schema"], []) if i["path"] != "tablade_recipe"
    ]
    issues += structural
    issues += link_issues(recipe, stage_ref_fields(spec))
    issues += _semantic(recipe, spec, schema, structural, input_columns or {})
    return issues


def stage_ref_fields(spec: dict[str, Any]) -> dict[str, list[str]]:
    """每種步驟引用其他階段的參數（schema 標了 ``ui: stage``）。"""
    defs = spec["schema"].get("$defs", {})
    out = {}
    for type_name, info in spec["steps"].items():
        props = defs.get(info["schema_ref"], {}).get("properties", {})
        names = [n for n, p in props.items() if p.get("ui") == "stage"]
        if names:
            out[type_name] = names
    return out


def _stage_hint(ref: str, earlier: list[str], later: list[str]) -> str | None:
    if ref in later:
        return t(
            f"「{ref}」在後面：只能讀前面的階段，請調整階段的順序",
            f'"{ref}" comes later: only earlier stages can be read, so reorder the stages',
        )
    if not earlier:
        return t("前面沒有階段", "There are no earlier stages")
    close = _close(ref, earlier)
    return t(
        (close or "") + f"前面的階段：{'、'.join(earlier)}",
        (f"{close} " if close else "") + f"Earlier stages: {', '.join(earlier)}",
    )


BAD_FILENAME_CHARS = '/\\:*?"<>|'
"""輸出檔名不能有的字元（Windows 不允許，也不能指到別的資料夾）；控制字元（含 \\x7f）也不行。"""
RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
"""Windows 的保留名稱（不分大小寫；CON.parquet 也算：看第一個「.」之前、去掉結尾空白）。"""
MAX_STEM_BYTES = 200
"""主檔名最多幾個位元組（UTF-8）。檔名上限一般是 255 個位元組，處理紀錄的暫存檔
（.主檔名.manifest.json.xxxxxxxx.tmp）會多 28 個，再留一點餘裕。"""


OUTPUT_EXTENSIONS = {"parquet": ".parquet", "csv": ".csv"}
"""輸出格式（每個階段的 output_format）→ 副檔名。"""


def output_name(raw: str, fmt: str = "parquet") -> str:
    """輸出檔名補上那個格式的副檔名（已經有就不補）。"""
    name = raw.strip()
    ext = OUTPUT_EXTENSIONS.get(fmt, ".parquet")
    return name if name.lower().endswith(ext) else f"{name}{ext}"


def output_stem(name: str) -> str:
    """去掉輸出格式的副檔名（處理紀錄用主檔名：明細.csv、明細.parquet 都是 log/明細.manifest.json）。"""
    for ext in OUTPUT_EXTENSIONS.values():
        if name.lower().endswith(ext):
            return name[: -len(ext)]
    return name


def output_issues(recipe: dict[str, Any], default: str | None = None) -> list[Issue]:
    """每個階段的輸出檔名：不能有路徑字元、不能「.」開頭，副檔名要和輸出格式一致，
    主檔名在同一個 Recipe 裡不能重複（不分大小寫；明細.csv 和 明細.parquet 也算重複）。

    最後一個階段沒填時用 ``default``（Recipe 的名稱，不含副檔名；不知道時不檢查它）。
    """
    stages = recipe.get("stages")
    if not isinstance(stages, list):
        return []
    issues: list[Issue] = []
    seen: dict[str, int] = {}
    for si, stage in enumerate(stages):
        if not isinstance(stage, dict):
            continue
        raw = stage.get("output_file", "")
        if not isinstance(raw, str):
            continue  # 型別錯誤由 schema 報
        if not raw.strip():
            if si != len(stages) - 1 or not default:
                continue
            raw = default
        fmt = stage.get("output_format")
        # 沒填或填錯（也可能是清單）由 schema 報
        fmt = fmt if isinstance(fmt, str) and fmt in OUTPUT_EXTENSIONS else "parquet"
        path = ["stages", si, "output_file"]
        other_ext = [
            e for f, e in OUTPUT_EXTENSIONS.items() if f != fmt and raw.strip().lower().endswith(e)
        ]
        if other_ext:
            issues.append(
                _issue(
                    path,
                    t(
                        f"輸出檔名「{raw.strip()}」的副檔名和輸出格式（{fmt}）不一樣",
                        f'Output file name "{raw.strip()}" does not match the output format ({fmt})',
                    ),
                    t(
                        f"只填檔名，副檔名會照格式補上（{OUTPUT_EXTENSIONS[fmt]}）",
                        f"Enter the name only; the extension follows the format ({OUTPUT_EXTENSIONS[fmt]})",
                    ),
                )
            )
            continue
        name = output_name(raw, fmt)
        if (
            any(c in BAD_FILENAME_CHARS or ord(c) < 32 or ord(c) == 127 for c in name)
            or name.startswith(".")
            or not output_stem(name).strip()
        ):
            issues.append(
                _issue(
                    path,
                    t(
                        f'輸出檔名「{raw.strip()}」不能有 / \\ : * ? " < > |，也不能用「.」開頭',
                        f'Output file name "{raw.strip()}" cannot contain / \\ : * ? " < > | '
                        'or start with "."',
                    ),
                    t(
                        "控制字元（例如換行、\\x7f）也不行",
                        "Control characters (such as line breaks or \\x7f) are not allowed either",
                    )
                    if any(ord(c) < 32 or ord(c) == 127 for c in name)
                    else None,
                )
            )
            continue
        if name.split(".")[0].rstrip(" ").upper() in RESERVED_NAMES:
            issues.append(
                _issue(
                    path,
                    t(
                        f"輸出檔名「{raw.strip()}」是 Windows 的保留名稱，不能當檔名",
                        f'Output file name "{raw.strip()}" is a reserved name on Windows',
                    ),
                    t(
                        "CON、PRN、AUX、NUL、COM1～COM9、LPT1～LPT9（不分大小寫）都不行",
                        "CON, PRN, AUX, NUL, COM1–COM9 and LPT1–LPT9 (any case) are not allowed",
                    ),
                )
            )
            continue
        size = len(output_stem(name).encode("utf-8"))
        if size > MAX_STEM_BYTES:
            issues.append(
                _issue(
                    path,
                    t(
                        f"輸出檔名「{raw.strip()[:20]}…」太長（{size} 個位元組）",
                        f'Output file name "{raw.strip()[:20]}…" is too long ({size} bytes)',
                    ),
                    t(
                        f"主檔名最多 {MAX_STEM_BYTES} 個位元組（UTF-8：中文字一個字 3 個位元組，"
                        f"大約 {MAX_STEM_BYTES // 3} 個字）",
                        f"The name can have at most {MAX_STEM_BYTES} bytes (UTF-8: a Chinese "
                        f"character takes 3 bytes, so about {MAX_STEM_BYTES // 3} characters)",
                    ),
                )
            )
            continue
        # 主檔名不能重複（處理紀錄是 log/主檔名.manifest.json）：不分大小寫，NFC／NFD 寫法不同的同一個字也算重複
        key = unicodedata.normalize("NFC", output_stem(name)).casefold()
        if key in seen:
            other = stages[seen[key]].get("name") if isinstance(stages[seen[key]], dict) else None
            where = (
                t(f"階段「{other}」", f'stage "{other}"')
                if isinstance(other, str)
                else f"stages[{seen[key]}]"
            )
            issues.append(
                _issue(
                    path,
                    t(
                        f"輸出檔名「{name}」和{where}重複",
                        f'Output file name "{name}" is the same as {where}',
                    ),
                )
            )
        seen.setdefault(key, si)
    return issues


def link_issues(recipe: dict[str, Any], ref_fields: dict[str, list[str]]) -> list[Issue]:
    """階段之間的關係：名稱不重複、只能讀前面的階段、步驟 id 整份 Recipe 不重複、輸出檔名。

    中間的階段沒有被後面的階段用到、也不寫檔時給警告（``severity: warning``）。
    """
    stages = recipe.get("stages")
    if not isinstance(stages, list):
        return []
    names = [s.get("name") if isinstance(s, dict) else None for s in stages]
    issues: list[Issue] = []
    first_index: dict[str, int] = {}
    step_ids: dict[str, str] = {}
    used: set[str] = set()
    for si, stage in enumerate(stages):
        if not isinstance(stage, dict):
            continue
        name = stage.get("name")
        earlier = list(dict.fromkeys(n for n in names[:si] if isinstance(n, str)))
        later = [n for n in names[si + 1 :] if isinstance(n, str)]
        if isinstance(name, str) and name in first_index:
            issues.append(
                _issue(
                    ["stages", si, "name"],
                    t(
                        f"階段名稱「{name}」和 stages[{first_index[name]}] 重複",
                        f'Stage name "{name}" is the same as stages[{first_index[name]}]',
                    ),
                )
            )
        source = stage.get("input") if isinstance(stage.get("input"), dict) else {}
        listed = source.get("stages") if isinstance(source.get("stages"), list) else []
        if source.get("reader") == "stages":
            if si == 0:
                issues.append(
                    _issue(
                        ["stages", 0, "input", "reader"],
                        t(
                            "第一個階段只能讀實際檔（前面沒有階段）",
                            "The first stage can only read files (there are no earlier stages)",
                        ),
                    )
                )
            elif not listed:
                issues.append(
                    _issue(
                        ["stages", si, "input", "stages"],
                        t("要選至少一個前面的階段", "Choose at least one earlier stage"),
                    )
                )
            for k, ref in enumerate(listed):
                if not isinstance(ref, str):
                    continue
                used.add(ref)
                if si and ref not in earlier:
                    issues.append(
                        _issue(
                            ["stages", si, "input", "stages", k],
                            t(f"「{ref}」不是前面的階段", f'"{ref}" is not an earlier stage'),
                            _stage_hint(ref, earlier, later),
                        )
                    )
                elif listed.index(ref) != k:
                    issues.append(
                        _issue(
                            ["stages", si, "input", "stages", k],
                            t(f"「{ref}」重複", f'"{ref}" is listed more than once'),
                        )
                    )
            if source.get("files"):
                issues.append(
                    _issue(
                        ["stages", si, "input", "files"],
                        t(
                            "讀前面階段的輸出時不能另外列檔案",
                            "Files cannot be listed when reading the output of earlier stages",
                        ),
                    )
                )
        members = source.get("members")
        if source.get("reader") == "zip_lines" and not (
            isinstance(members, str) and members.strip()
        ):
            issues.append(
                _issue(
                    ["stages", si, "input", "members"],
                    t(
                        "讀取方式是「zip 裡的文字檔」時要填 members（zip 裡要讀的檔，例如 2026*/HEADER*.TXT）",
                        'With the "zip_lines" reader, fill in members (the files to read in the zip, '
                        "for example 2026*/HEADER*.TXT)",
                    ),
                )
            )
        elif source.get("reader") != "zip_lines" and members:
            issues.append(
                _issue(
                    ["stages", si, "input", "members"],
                    t(
                        "只有讀取方式是「zip 裡的文字檔」（zip_lines）時才能填 members",
                        'members can be set only when the reader is "zip_lines"',
                    ),
                )
            )
        if source.get("decode_errors") == "replace" and source.get("reader", "text_lines") not in (
            "text_lines",
            "zip_lines",
        ):
            issues.append(
                _issue(
                    ["stages", si, "input", "decode_errors"],
                    t(
                        "只有讀取方式是逐行讀取（text_lines）或 zip 裡的文字檔（zip_lines）時，"
                        "decode_errors 才能設成 replace（換成 �）",
                        'decode_errors can be "replace" (replace with �) only when the reader is '
                        "text_lines or zip_lines",
                    ),
                )
            )
        if source.get("line_breaks") == "crlf" and source.get("reader", "text_lines") not in (
            "text_lines",
            "zip_lines",
        ):
            issues.append(
                _issue(
                    ["stages", si, "input", "line_breaks"],
                    t(
                        "只有讀取方式是逐行讀取（text_lines）或 zip 裡的文字檔（zip_lines）時，"
                        "line_breaks 才能設成 crlf（只認 CRLF）",
                        'line_breaks can be "crlf" (CRLF only) only when the reader is '
                        "text_lines or zip_lines",
                    ),
                )
            )
        if source.get("reader") != "stages" and listed:
            issues.append(
                _issue(
                    ["stages", si, "input", "stages"],
                    t(
                        "只有讀取方式是「前面階段的輸出」（stages）時才能列階段",
                        'Stages can be listed only when the reader is "Output of earlier stages" '
                        "(stages)",
                    ),
                )
            )
        steps = stage.get("steps") if isinstance(stage.get("steps"), list) else []
        for i, step in enumerate(steps):
            if not isinstance(step, dict):
                continue
            path: list[str | int] = ["stages", si, "steps", i]
            sid = step.get("id")
            if isinstance(sid, str):
                if sid in step_ids:
                    issues.append(
                        _issue(
                            path + ["id"],
                            t(
                                f"步驟 id「{sid}」和 {step_ids[sid]} 重複",
                                f'Step id "{sid}" is the same as {step_ids[sid]}',
                            ),
                        )
                    )
                step_ids.setdefault(sid, _fmt(path))
            kind = step.get("type")
            for field in ref_fields.get(kind, []) if isinstance(kind, str) else []:
                ref = step.get(field)
                if not isinstance(ref, str):
                    continue
                if step.get("enabled", True) is not False:
                    used.add(ref)
                if ref not in earlier:
                    issues.append(
                        _issue(
                            path + [field],
                            t(f"「{ref}」不是前面的階段", f'"{ref}" is not an earlier stage'),
                            _stage_hint(ref, earlier, later),
                        )
                    )
        if isinstance(name, str):
            first_index.setdefault(name, si)
    for si, name in enumerate(names[:-1]):
        stage = stages[si] if isinstance(stages[si], dict) else {}
        writes = isinstance(stage.get("output_file"), str) and stage["output_file"].strip()
        if isinstance(name, str) and name not in used and not writes:
            issues.append(
                _issue(
                    ["stages", si],
                    t(
                        f"階段「{name}」的結果沒有被後面的階段用到",
                        f'The result of stage "{name}" is not used by any later stage',
                    ),
                    t(
                        "也沒有填輸出檔名，結果不會留下來；用不到可以刪掉這個階段",
                        "It has no output file name either, so the result will not be kept; "
                        "delete this stage if you don't need it",
                    ),
                    severity="warning",
                )
            )
    return issues + output_issues(recipe)


def _semantic(
    recipe: dict[str, Any],
    spec: dict[str, Any],
    schema: _Schema,
    structural: list[Issue],
    input_columns: dict[str, list[str]],
) -> list[Issue]:
    """每個階段依序推算欄位，檢查欄位引用與欄位效果。``input_columns`` 依階段名稱提供讀到的欄位。"""
    issues: list[Issue] = []
    stages = recipe.get("stages")
    if not isinstance(stages, list):
        return issues
    broken = set()
    for item in structural:
        match = re.match(r"^stages\[(\d+)\]\.steps\[(\d+)\]", item["path"])
        if match:
            broken.add((int(match[1]), int(match[2])))
    step_schema = schema.root["$defs"]
    outputs: dict[str, list[str] | None] = {}
    systems: dict[str, list[str] | None] = {}
    for si, stage in enumerate(stages):
        if not isinstance(stage, dict):
            continue
        name = stage.get("name")
        source = stage.get("input") if isinstance(stage.get("input"), dict) else {}
        reader = source.get("reader", "text_lines")
        reader = reader if isinstance(reader, str) else None  # 型別錯誤由 schema 報
        if reader == "stages":
            listed = source.get("stages") if isinstance(source.get("stages"), list) else []
            columns = _union([outputs.get(n) if isinstance(n, str) else None for n in listed])
            # 前面階段的系統欄位都帶過來，再加上 __stage
            from_stages = [systems.get(n, list(BASE_SYSTEM)) for n in listed if isinstance(n, str)]
            system = _union([*from_stages, ["__stage"]])
        else:
            known = input_columns.get(name) if isinstance(name, str) else None
            columns = known or spec.get("readers", {}).get(reader, {}).get("columns")
            all_sheets = reader == "excel" and source.get("sheet") == "*"
            system = list(BASE_SYSTEM) + (["__sheet"] if all_sheets else [])
        columns = list(columns) if columns is not None else None
        steps = stage.get("steps") if isinstance(stage.get("steps"), list) else []
        for i, raw_step in enumerate(steps):
            if not isinstance(raw_step, dict) or raw_step.get("enabled", True) is False:
                continue
            kind = raw_step.get("type")
            if (si, i) in broken or not isinstance(kind, str) or kind not in spec["steps"]:
                columns = system = None  # 這一步有結構問題，之後的欄位無法推算
                continue
            info = spec["steps"][kind]
            ref_schema = step_schema[info["schema_ref"]]
            step = _with_defaults(raw_step, ref_schema)
            path: list[str | int] = ["stages", si, "steps", i]
            before = len(issues)
            issues += step_rules(step, ref_schema, schema, path)
            for ref_path, ref, in_condition in schema.column_refs(step, ref_schema, path):
                if ref.startswith("__"):
                    issues += _system_ref_issues(ref_path, ref, in_condition, system)
                elif columns is not None and ref not in columns:
                    hint = _close(ref, columns) or ""
                    issues.append(
                        _issue(
                            ref_path,
                            t(
                                f"欄位「{ref}」在這一步之前不存在",
                                f'Column "{ref}" does not exist before this step',
                            ),
                            t(
                                f"目前的欄位：{'、'.join(columns)}。{hint}".rstrip("。"),
                                f"Current columns: {', '.join(columns)}"
                                + (f". {hint}" if hint else ""),
                            ),
                        )
                    )
            try:
                columns = apply_effect(columns, resolve_effect(info["effect"], step), outputs)
            except EffectError as err:
                if len(issues) == before:  # 欄位引用已經報過錯時，不要再報一次同樣的問題
                    issues.append(_issue(path, str(err)))
                columns = None
            if info.get("combines") and system is not None:  # 合併來源檔：原本的位置留在 __orig_*
                system = system + [
                    orig
                    for col, orig in ORIGINAL_COLUMNS.items()
                    if col in system and orig not in system
                ]
        if isinstance(name, str):
            outputs.setdefault(name, columns)
            systems.setdefault(name, system)
    return issues


def _system_ref_issues(
    path: list[str | int], ref: str, in_condition: bool, system: list[str] | None
) -> list[Issue]:
    """引用系統欄位的檢查：有沒有這個系統欄位、能不能用在這個參數、這一步之前有沒有。

    ``system`` 是這一步之前有的系統欄位（``None``：前面有結構問題，不知道）。
    """
    if ref not in SYSTEM_COLUMNS:
        return [
            _issue(
                path,
                t(f"沒有系統欄位「{ref}」", f'No system column "{ref}"'),
                t(
                    f"系統欄位有：{'、'.join(SYSTEM_COLUMNS)}",
                    f"System columns: {', '.join(SYSTEM_COLUMNS)}",
                ),
            )
        ]
    if not in_condition:
        return [
            _issue(
                path,
                t(
                    f"系統欄位「{ref}」只能用在條件裡（例如篩選的 conditions）",
                    f'System column "{ref}" can only be used in conditions '
                    "(for example the conditions of a filter)",
                ),
                t("其他參數只能引用資料欄位", "Other parameters can only refer to data columns"),
            )
        ]
    if system is None or ref in system:
        return []
    if ref == "__sheet":
        why = t(
            '只有讀 Excel 全部工作表（"sheet": "*"）的階段才有',
            'Only stages that read every Excel worksheet ("sheet": "*") have it',
        )
    elif ref == "__stage":
        why = t(
            '只有讀前面階段的輸出（"reader": "stages"）的階段才有',
            'Only stages that read the output of earlier stages ("reader": "stages") have it',
        )
    else:
        base = next(col for col, orig in ORIGINAL_COLUMNS.items() if orig == ref)
        why = t(
            f"合併來源檔（merge_files）之後才有，而且合併前要有 {base}",
            f"It exists only after combining source files (merge_files), and only if {base} "
            "existed before",
        )
    return [
        _issue(
            path,
            t(
                f"這一步之前沒有系統欄位「{ref}」",
                f'System column "{ref}" does not exist before this step',
            ),
            why
            + t(
                f"。目前的系統欄位：{'、'.join(system)}",
                f". Current system columns: {', '.join(system)}",
            ),
        )
    ]


def _union(lists: list[list[str] | None]) -> list[str] | None:
    """讀好幾個階段時的欄位：依第一次出現的順序合在一起；有一個不知道就是不知道。"""
    if not lists or any(c is None for c in lists):
        return None
    out: list[str] = []
    for columns in lists:
        out += [c for c in columns if c not in out]  # type: ignore[union-attr]
    return out


def _with_defaults(step: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    """補上省略的參數預設值（例如 split_delimited 的 column 預設是 line）。"""
    merged = dict(step)
    for name, prop in schema.get("properties", {}).items():
        if name not in merged and "default" in prop:
            merged[name] = prop["default"]
    return merged


# ── 命令列（AI skill 用）────────────────────────────────────────────


def csv_header(path: Path) -> list[str]:
    """讀 CSV 第一列當欄名（自動偵測 utf-8 / cp950）。"""
    raw = path.read_bytes()[:65536]
    for encoding in ("utf-8-sig", "cp950"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = raw.decode("utf-8", errors="replace")
    first = text.splitlines()[0] if text else ""
    return next(csv.reader([first]), [])


def _utf8_output() -> None:
    """一律用 UTF-8 輸出（Windows 的管線預設是 cp1252，印中文會失敗；AI 讀的也是 UTF-8）。"""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def main(argv: list[str] | None = None, spec: dict[str, Any] | None = None) -> int:
    _utf8_output()
    parser = argparse.ArgumentParser(
        description="驗證 Tablade Recipe"  # i18n-ignore：命令列給 AI skill 用，維持中文
    )
    parser.add_argument("recipe", type=Path)
    parser.add_argument(
        "--data",
        type=Path,
        help="第一個階段讀 csv 時，用這個檔案的標題列檢查欄位引用",  # i18n-ignore：命令列給 AI skill 用，維持中文
    )
    parser.add_argument(
        "--spec",
        type=Path,
        help="步驟規格 JSON（預設用內嵌的）",  # i18n-ignore：命令列給 AI skill 用，維持中文
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="以 JSON 輸出",  # i18n-ignore：命令列給 AI skill 用，維持中文
    )
    args = parser.parse_args(argv)

    if args.spec:
        spec = json.loads(args.spec.read_text(encoding="utf-8"))
    spec = spec or EMBEDDED_SPEC
    if spec is None:
        parser.error(
            "沒有步驟規格：請用 --spec 指定，或使用 skill 內附的 validate_recipe.py"  # i18n-ignore：命令列給 AI skill 用，維持中文
        )

    try:
        recipe = json.loads(args.recipe.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as err:
        issues = [
            _issue(
                [],
                f"不是合法的 JSON：第 {err.lineno} 行第 {err.colno} 欄，{err.msg}",  # i18n-ignore：命令列給 AI skill 用，維持中文
            )
        ]
    else:
        columns = None
        stages = recipe.get("stages") if isinstance(recipe, dict) else None
        if args.data and isinstance(stages, list) and stages and isinstance(stages[0], dict):
            first = stages[0]
            source = first.get("input") if isinstance(first.get("input"), dict) else {}
            if source.get("reader") == "csv" and isinstance(first.get("name"), str):
                columns = {first["name"]: csv_header(args.data)}
        issues = validate(recipe, spec, columns)

    errors = [i for i in issues if i.get("severity", "error") == "error"]
    if args.json:
        print(json.dumps({"ok": not errors, "issues": issues}, ensure_ascii=False, indent=2))
    else:
        if errors:
            print(
                f"Recipe 有 {len(errors)} 個問題："  # i18n-ignore：命令列給 AI skill 用，維持中文
            )
        else:
            print(
                f"Recipe 通過驗證（版本 {spec['recipe_version']}）"  # i18n-ignore：命令列給 AI skill 用，維持中文
            )
        for item in issues:
            hint = (
                f"（{item['hint']}）"  # i18n-ignore：命令列給 AI skill 用，維持中文
                if item.get("hint")
                else ""
            )
            mark = (
                "提醒："  # i18n-ignore：命令列給 AI skill 用，維持中文
                if item.get("severity") == "warning"
                else ""
            )
            print(f"  {item['path']}: {mark}{item['error']}{hint}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
