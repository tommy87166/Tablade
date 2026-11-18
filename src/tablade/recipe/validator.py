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
from difflib import get_close_matches
from pathlib import Path
from typing import Any

EMBEDDED_SPEC: dict[str, Any] | None = None
"""放進 AI skill 時，build 會把步驟規格嵌在這裡。"""

SYSTEM_COLUMNS = (
    "__source_file",
    "__line_no",
    "__sheet",
    "__stage",
    "__orig_source_file",
    "__orig_line_no",
    "__orig_sheet",
    "__orig_stage",
    "__row_type",
)
VERSION_RE = re.compile(r"^(\d+)\.(\d+)$")
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
            raise EffectError(f"欄名「{name}」不能以 __ 開頭（保留給系統欄位）")
        if name in seen:
            raise EffectError(f"新欄名「{name}」重複")
        if name in existing:
            raise EffectError(f"新欄名「{name}」和現有欄位重複")
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
            raise EffectError(f"欄位「{source}」不存在")
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
            raise EffectError(f"目前有 {len(columns)} 個資料欄，但列了 {len(names)} 個欄名")
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
    raise EffectError(f"未知的欄位效果：{kind}")


def _missing(columns: list[str], names: list[str]) -> None:
    missing = [n for n in names if n not in columns]
    if missing:
        raise EffectError(f"欄位不存在：{'、'.join(missing)}")


def _select(columns: list[str] | None, names: list[str]) -> list[str] | None:
    """保留和排序欄位：依清單順序只留下這些欄。"""
    if not names:
        raise EffectError("要選至少一個要保留的欄位")
    repeated = sorted({n for n in names if names.count(n) > 1})
    if repeated:
        raise EffectError(f"欄位重複：{'、'.join(repeated)}")
    if columns is None:
        return None
    _missing(columns, names)
    return names


def _rename(columns: list[str] | None, names: dict[str, str]) -> list[str] | None:
    """改欄名：{舊名: 新名}；改完後不能重複（可以互換），新名不能以 __ 開頭。"""
    if not names:
        raise EffectError("要填至少一個新欄名")
    for new in names.values():
        if new.startswith("__"):
            raise EffectError(f"欄名「{new}」不能以 __ 開頭（保留給系統欄位）")
    if columns is None:
        return None
    _missing(columns, list(names))
    result = [names.get(c, c) for c in columns]
    repeated = sorted({n for n in result if result.count(n) > 1})
    if repeated:
        raise EffectError(f"改完後欄名重複：{'、'.join(repeated)}")
    return result


def _unique(names: list[str]) -> list[str]:
    return list(dict.fromkeys(names))


def _unpivot(columns: list[str] | None, effect: dict[str, Any]) -> list[str] | None:
    """取消樞紐：沒攤開的欄保留，後面接「名稱欄、值欄」。"""
    picked = _unique(list(effect.get("columns") or []))
    if not picked:
        raise EffectError("要選至少一個要攤開的欄位")
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
        raise EffectError("要有至少一項彙總，或另外加一欄筆數")
    _check_new_names(names, by)
    if columns is not None:
        _missing(columns, _unique(by + [a["column"] for a in aggregations]))
    return by + names


def _pivot(columns: list[str] | None, effect: dict[str, Any]) -> list[str] | None:
    """樞紐：樞紐表格列依據欄位，後面接要變成欄的值。"""
    index = _unique(list(effect.get("index") or []))
    names = list(effect.get("names") or [])
    if not names:
        raise EffectError("要列出至少一個要變成欄的值")
    for role, name in (("欄", effect["on"]), ("值", effect["values"])):
        if name in index:
            raise EffectError(f"「{name}」已經是樞紐表格列依據欄位，不能再當樞紐表格{role}依據欄位")
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
        raise EffectError("要設定至少一組鍵")
    keep_only = effect.get("how") in ("anti", "semi")
    names = [] if keep_only else list(effect.get("names") or [])
    right_keys = [p.get("right") for p in pairs]
    keyed = [n for n in names if n in right_keys]
    if keyed:
        raise EffectError(f"「{'、'.join(keyed)}」是右表的鍵，不用帶進來")
    right = stages.get(effect.get("stage"))
    if right is not None:
        missing = [n for n in _unique(right_keys + names) if n not in right]
        if missing:
            raise EffectError(
                f"階段「{effect.get('stage')}」沒有欄位：{'、'.join(missing)}"
                f"（它的欄位：{'、'.join(right)}）"
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
        raise EffectError("要選至少一個要刪除的欄位")
    if columns is None:
        return None
    if by_position:
        bad = [p for p in picked if not isinstance(p, int) or not 1 <= p <= len(columns)]
        if bad:
            shown = "、".join(str(p) for p in bad)
            raise EffectError(f"目前只有 {len(columns)} 個資料欄，沒有第 {shown} 欄")
        drop = {columns[p - 1] for p in picked}
    else:
        missing = [n for n in picked if n not in columns]
        if missing:
            raise EffectError(f"欄位不存在：{'、'.join(missing)}")
        drop = set(picked)
    rest = [c for c in columns if c not in drop]
    if not rest:
        raise EffectError("不能刪除所有資料欄，至少要留下一個")
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
    return f"是不是「{match[0]}」？" if match else None


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


class _Schema:
    def __init__(self, root: dict[str, Any]) -> None:
        self.defs = root.get("$defs", {})
        self.root = root

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
                    return issues + [_issue(path + [prop], f"缺少 {prop}")]
                if tag not in mapping:
                    hint = _close(tag, list(mapping))
                    allowed = "、".join(mapping)
                    return issues + [
                        _issue(path + [prop], f"不支援的 {prop}：{tag}（可用：{allowed}）", hint)
                    ]
                return issues + self.check(value, {"$ref": mapping[tag]}, path)
            results = [self.check(value, sub, path) for sub in schema["oneOf"]]
            passed = sum(1 for r in results if not r)
            if passed != 1:
                issues += min(results, key=len) if passed == 0 else [_issue(path, "格式不明確")]
            return issues

        if "const" in schema and value != schema["const"]:
            return issues + [
                _issue(path, f"必須是 {json.dumps(schema['const'], ensure_ascii=False)}")
            ]
        if "enum" in schema and value not in schema["enum"]:
            allowed = "、".join(json.dumps(v, ensure_ascii=False) for v in schema["enum"])
            return issues + [
                _issue(
                    path,
                    f"不支援的值：{value!r}（可用：{allowed}）",
                    _close(value, [str(v) for v in schema["enum"]]),
                )
            ]

        types = schema.get("type")
        if types:
            types = [types] if isinstance(types, str) else types
            if not any(_is_type(value, t) for t in types):
                expected = " 或 ".join(_TYPE_LABELS.get(t, t) for t in types)
                return issues + [_issue(path, f"型別錯誤：應該是{expected}")]

        if isinstance(value, str):
            if len(value) < schema.get("minLength", 0):
                issues.append(
                    _issue(
                        path,
                        "不能是空字串"
                        if schema["minLength"] == 1
                        else f"至少 {schema['minLength']} 個字元",
                    )
                )
            if "maxLength" in schema and len(value) > schema["maxLength"]:
                issues.append(_issue(path, f"最多 {schema['maxLength']} 個字元"))
            if "pattern" in schema and not re.search(schema["pattern"], value):
                issues.append(_issue(path, f"格式不符（{schema['pattern']}）"))
        if _is_type(value, "number"):
            if "minimum" in schema and value < schema["minimum"]:
                issues.append(_issue(path, f"不能小於 {schema['minimum']}"))
            if "maximum" in schema and value > schema["maximum"]:
                issues.append(_issue(path, f"不能大於 {schema['maximum']}"))
            if "exclusiveMinimum" in schema and value <= schema["exclusiveMinimum"]:
                issues.append(_issue(path, f"必須大於 {schema['exclusiveMinimum']}"))
        if isinstance(value, list):
            if len(value) < schema.get("minItems", 0):
                issues.append(_issue(path, f"至少要有 {schema['minItems']} 項"))
            if "maxItems" in schema and len(value) > schema["maxItems"]:
                issues.append(_issue(path, f"最多 {schema['maxItems']} 項"))
            if "items" in schema:
                for i, item in enumerate(value):
                    issues += self.check(item, schema["items"], path + [i])
        if isinstance(value, dict):
            props = schema.get("properties", {})
            extra = schema.get("additionalProperties")
            if len(value) < schema.get("minProperties", 0):
                issues.append(_issue(path, f"至少要有 {schema['minProperties']} 項"))
            for name in schema.get("required", []):
                if name not in value:
                    issues.append(_issue(path + [name], "缺少必填欄位"))
            for name, item in value.items():
                if len(name) < schema.get("propertyNames", {}).get("minLength", 0):
                    issues.append(_issue(path + [name], "名稱不能是空字串"))
                if name in props:
                    issues += self.check(item, props[name], path + [name])
                elif extra is False:
                    issues.append(_issue(path + [name], "不認得的欄位", _close(name, list(props))))
                elif isinstance(extra, dict):  # 例如改欄名的 {舊名: 新名}
                    issues += self.check(item, extra, path + [name])
        return issues

    def column_refs(
        self, value: Any, schema: dict[str, Any], path: list[str | int]
    ) -> list[tuple[list[str | int], str]]:
        """找出參數裡標記為 ``ui: column`` / ``ui: columns`` 的欄位引用。"""
        schema = self.resolve(schema)
        ui = schema.get("ui")
        if ui == "column" and isinstance(value, str):
            return [(path, value)]
        if ui in ("columns", "column_order") and isinstance(value, list):
            return [(path + [i], v) for i, v in enumerate(value) if isinstance(v, str)]
        if ui == "rename_map" and isinstance(value, dict):
            return [(path + [k], k) for k in value if isinstance(k, str)]
        refs: list[tuple[list[str | int], str]] = []
        disc = schema.get("discriminator")
        if disc and isinstance(value, dict) and value.get(disc["propertyName"]) in disc["mapping"]:
            ref = disc["mapping"][value[disc["propertyName"]]]
            return self.column_refs(value, {"$ref": ref}, path)
        for sub in schema.get("anyOf", []) + schema.get("allOf", []):
            refs += self.column_refs(value, sub, path)
        if isinstance(value, list) and "items" in schema:
            for i, item in enumerate(value):
                refs += self.column_refs(item, schema["items"], path + [i])
        if isinstance(value, dict):
            for name, sub in schema.get("properties", {}).items():
                if name in value:
                    refs += self.column_refs(value[name], sub, path + [name])
        return refs


# ── 驗證 ─────────────────────────────────────────────────────────


def check_version(value: Any, current: str) -> Issue | None:
    path = ["tablade_recipe"]
    if not isinstance(value, str) or not VERSION_RE.match(value):
        return _issue(
            path, '版本號格式應該是 "主版本.次版本"，例如 "1.0"', f'目前的版本是 "{current}"'
        )
    major, minor = (int(x) for x in VERSION_RE.match(value).groups())
    cur_major, cur_minor = (int(x) for x in VERSION_RE.match(current).groups())
    if major != cur_major:
        return _issue(
            path,
            f"不相容的 Recipe 版本 {value}（這裡支援 {cur_major}.x）",
            f"請改用 Recipe 版本 {current} 的 skill 或 Tablade",
        )
    if minor > cur_minor:
        return _issue(
            path, f"Recipe 版本 {value} 比這裡支援的 {current} 新", "請更新 Tablade 或 skill"
        )
    return None


def validate(
    recipe: Any,
    spec: dict[str, Any],
    input_columns: dict[str, list[str]] | None = None,
) -> list[Issue]:
    """驗證 recipe，回傳問題清單（空清單代表通過）。

    ``input_columns`` 可提供各 input 的欄位（例如從 CSV 標題列讀到的），用來檢查欄位引用。
    """
    if not isinstance(recipe, dict):
        return [_issue([], "Recipe 必須是 JSON 物件")]
    issues: list[Issue] = []
    version_issue = check_version(recipe.get("tablade_recipe"), spec["recipe_version"])
    if version_issue:
        issues.append(version_issue)

    if "stages" not in recipe and any(k in recipe for k in ("inputs", "steps")):
        issues.append(
            _issue(
                [],
                "這是舊的 Recipe 寫法：最外層的 inputs / steps 要改成 stages",
                'stages 的每一項是一個階段：{"name": "預設階段", "input": {原本 inputs[0]，去掉 id}, '
                '"steps": [原本的 steps]}；output 留在最外層',
            )
        )
        return issues
    schema = _Schema(spec["schema"])
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
        return f"「{ref}」在後面：只能讀前面的階段，請調整階段的順序"
    if not earlier:
        return "前面沒有階段"
    return (_close(ref, earlier) or "") + f"前面的階段：{'、'.join(earlier)}"


BAD_FILENAME_CHARS = '/\\:*?"<>|'
"""輸出檔名不能有的字元（Windows 不允許，也不能指到別的資料夾）。"""


def output_name(raw: str) -> str:
    """輸出檔名補上 .parquet（已經有就不補）。"""
    name = raw.strip()
    return name if name.lower().endswith(".parquet") else f"{name}.parquet"


def output_issues(recipe: dict[str, Any], default: str | None = None) -> list[Issue]:
    """每個階段的輸出檔名：不能有路徑字元、不能「.」開頭，同一個 Recipe 裡不能重複（不分大小寫）。

    最後一個階段沒填時用 ``default``（Recipe 的名稱；不知道時不檢查它）。
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
        name = output_name(raw)
        path = ["stages", si, "output_file"]
        if (
            any(c in BAD_FILENAME_CHARS or ord(c) < 32 for c in name)
            or name.startswith(".")
            or name.lower() == ".parquet"
        ):
            issues.append(
                _issue(
                    path, f'輸出檔名「{raw.strip()}」不能有 / \\ : * ? " < > |，也不能用「.」開頭'
                )
            )
            continue
        key = name.casefold()
        if key in seen:
            issues.append(_issue(path, f"輸出檔名「{name}」和 stages[{seen[key]}] 重複"))
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
                    ["stages", si, "name"], f"階段名稱「{name}」和 stages[{first_index[name]}] 重複"
                )
            )
        source = stage.get("input") if isinstance(stage.get("input"), dict) else {}
        listed = source.get("stages") if isinstance(source.get("stages"), list) else []
        if source.get("reader") == "stages":
            if si == 0:
                issues.append(
                    _issue(
                        ["stages", 0, "input", "reader"], "第一個階段只能讀實際檔（前面沒有階段）"
                    )
                )
            elif not listed:
                issues.append(_issue(["stages", si, "input", "stages"], "要選至少一個前面的階段"))
            for k, ref in enumerate(listed):
                if not isinstance(ref, str):
                    continue
                used.add(ref)
                if si and ref not in earlier:
                    issues.append(
                        _issue(
                            ["stages", si, "input", "stages", k],
                            f"「{ref}」不是前面的階段",
                            _stage_hint(ref, earlier, later),
                        )
                    )
                elif listed.index(ref) != k:
                    issues.append(_issue(["stages", si, "input", "stages", k], f"「{ref}」重複"))
            if source.get("files"):
                issues.append(
                    _issue(["stages", si, "input", "files"], "讀前面階段的輸出時不能另外列檔案")
                )
        elif listed:
            issues.append(
                _issue(
                    ["stages", si, "input", "stages"],
                    "只有讀取方式是「前面階段的輸出」（stages）時才能列階段",
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
                    issues.append(_issue(path + ["id"], f"步驟 id「{sid}」和 {step_ids[sid]} 重複"))
                step_ids.setdefault(sid, _fmt(path))
            for field in ref_fields.get(step.get("type"), []):
                ref = step.get(field)
                if not isinstance(ref, str):
                    continue
                if step.get("enabled", True) is not False:
                    used.add(ref)
                if ref not in earlier:
                    issues.append(
                        _issue(
                            path + [field],
                            f"「{ref}」不是前面的階段",
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
                    f"階段「{name}」的結果沒有被後面的階段用到",
                    "也沒有填輸出檔名，結果不會留下來；用不到可以刪掉這個階段",
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
    for si, stage in enumerate(stages):
        if not isinstance(stage, dict):
            continue
        name = stage.get("name")
        source = stage.get("input") if isinstance(stage.get("input"), dict) else {}
        reader = source.get("reader", "text_lines")
        if reader == "stages":
            listed = source.get("stages") if isinstance(source.get("stages"), list) else []
            columns = _union([outputs.get(n) if isinstance(n, str) else None for n in listed])
        else:
            known = input_columns.get(name) if isinstance(name, str) else None
            columns = known or spec.get("readers", {}).get(reader, {}).get("columns")
        columns = list(columns) if columns is not None else None
        steps = stage.get("steps") if isinstance(stage.get("steps"), list) else []
        for i, raw_step in enumerate(steps):
            if not isinstance(raw_step, dict) or raw_step.get("enabled", True) is False:
                continue
            if (si, i) in broken or raw_step.get("type") not in spec["steps"]:
                columns = None  # 這一步有結構問題，之後的欄位無法推算
                continue
            info = spec["steps"][raw_step["type"]]
            ref_schema = step_schema[info["schema_ref"]]
            step = _with_defaults(raw_step, ref_schema)
            path: list[str | int] = ["stages", si, "steps", i]
            before = len(issues)
            if columns is not None:
                for ref_path, ref in schema.column_refs(step, ref_schema, path):
                    if ref.startswith("__"):
                        if ref not in SYSTEM_COLUMNS:
                            issues.append(
                                _issue(
                                    ref_path,
                                    f"沒有系統欄位「{ref}」",
                                    f"系統欄位有：{'、'.join(SYSTEM_COLUMNS)}",
                                )
                            )
                    elif ref not in columns:
                        hint = _close(ref, columns) or ""
                        issues.append(
                            _issue(
                                ref_path,
                                f"欄位「{ref}」在這一步之前不存在",
                                f"目前的欄位：{'、'.join(columns)}。{hint}".rstrip("。"),
                            )
                        )
            try:
                columns = apply_effect(columns, resolve_effect(info["effect"], step), outputs)
            except EffectError as err:
                if len(issues) == before:  # 欄位引用已經報過錯時，不要再報一次同樣的問題
                    issues.append(_issue(path, str(err)))
                columns = None
        if isinstance(name, str):
            outputs.setdefault(name, columns)
    return issues


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
    parser = argparse.ArgumentParser(description="驗證 Tablade Recipe")
    parser.add_argument("recipe", type=Path)
    parser.add_argument(
        "--data", type=Path, help="第一個階段讀 csv 時，用這個檔案的標題列檢查欄位引用"
    )
    parser.add_argument("--spec", type=Path, help="步驟規格 JSON（預設用內嵌的）")
    parser.add_argument("--json", action="store_true", help="以 JSON 輸出")
    args = parser.parse_args(argv)

    if args.spec:
        spec = json.loads(args.spec.read_text(encoding="utf-8"))
    spec = spec or EMBEDDED_SPEC
    if spec is None:
        parser.error("沒有步驟規格：請用 --spec 指定，或使用 skill 內附的 validate_recipe.py")

    try:
        recipe = json.loads(args.recipe.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as err:
        issues = [_issue([], f"不是合法的 JSON：第 {err.lineno} 行第 {err.colno} 欄，{err.msg}")]
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
            print(f"Recipe 有 {len(errors)} 個問題：")
        else:
            print(f"Recipe 通過驗證（版本 {spec['recipe_version']}）")
        for item in issues:
            hint = f"（{item['hint']}）" if item.get("hint") else ""
            mark = "提醒：" if item.get("severity") == "warning" else ""
            print(f"  {item['path']}: {mark}{item['error']}{hint}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
