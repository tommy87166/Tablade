"""Tablade recipe 驗證器（DESIGN.md §17.4）。

**只用 Python 標準函式庫、不 import 任何 tablade 模組**：同一個檔案同時給 Tablade 使用，
也會原封不動放進 AI skill（Copilot 的沙箱不能安裝套件）。步驟規格（spec）由 Tablade 從
registry 產生後傳進來；放進 skill 時會嵌入在 ``EMBEDDED_SPEC``。

檢查分兩層：
1. 結構：版本號、JSON Schema（步驟類型、參數型別、必填、列舉…）
2. 語意：步驟 id 不重複、欄位引用存在、新欄名不衝突、欄位數相符

每個問題是 ``{"path", "error", "hint"?, "severity"}``，``path`` 例如 ``steps[3].column``。

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

SYSTEM_COLUMNS = ("__source_file", "__line_no", "__sheet", "__row_type")
VERSION_RE = re.compile(r"^(\d+)\.(\d+)$")
EFFECT_KINDS = ("keep", "add", "split", "replace_data", "rename_data")

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


def apply_effect(columns: list[str] | None, effect: dict[str, Any]) -> list[str] | None:
    """依欄位效果推算下一步的資料欄位；``None`` 表示目前不知道有哪些欄位。"""
    kind = effect["kind"]
    if kind == "keep":
        return columns
    if kind == "add":
        names = list(effect["names"])
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
    raise EffectError(f"未知的欄位效果：{kind}")


# ── JSON Schema（pydantic 產生的子集）───────────────────────────────


def _fmt(path: list[str | int]) -> str:
    out = ""
    for part in path:
        out += f"[{part}]" if isinstance(part, int) else (f".{part}" if out else part)
    return out or "(recipe)"


def _issue(path: list[str | int], error: str, hint: str | None = None) -> Issue:
    item: Issue = {"path": _fmt(path), "error": error, "severity": "error"}
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
            for name in schema.get("required", []):
                if name not in value:
                    issues.append(_issue(path + [name], "缺少必填欄位"))
            for name, item in value.items():
                if name in props:
                    issues += self.check(item, props[name], path + [name])
                elif schema.get("additionalProperties") is False:
                    issues.append(_issue(path + [name], "不認得的欄位", _close(name, list(props))))
        return issues

    def column_refs(
        self, value: Any, schema: dict[str, Any], path: list[str | int]
    ) -> list[tuple[list[str | int], str]]:
        """找出參數裡標記為 ``ui: column`` / ``ui: columns`` 的欄位引用。"""
        schema = self.resolve(schema)
        ui = schema.get("ui")
        if ui == "column" and isinstance(value, str):
            return [(path, value)]
        if ui == "columns" and isinstance(value, list):
            return [(path + [i], v) for i, v in enumerate(value) if isinstance(v, str)]
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
            f"不相容的 recipe 版本 {value}（這裡支援 {cur_major}.x）",
            f"請改用 recipe 版本 {current} 的 skill 或 Tablade",
        )
    if minor > cur_minor:
        return _issue(
            path, f"recipe 版本 {value} 比這裡支援的 {current} 新", "請更新 Tablade 或 skill"
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
        return [_issue([], "recipe 必須是 JSON 物件")]
    issues: list[Issue] = []
    version_issue = check_version(recipe.get("tablade_recipe"), spec["recipe_version"])
    if version_issue:
        issues.append(version_issue)

    schema = _Schema(spec["schema"])
    structural = [
        i for i in schema.check(recipe, spec["schema"], []) if i["path"] != "tablade_recipe"
    ]
    issues += structural
    issues += _semantic(recipe, spec, schema, structural, input_columns or {})
    return issues


def _semantic(
    recipe: dict[str, Any],
    spec: dict[str, Any],
    schema: _Schema,
    structural: list[Issue],
    input_columns: dict[str, list[str]],
) -> list[Issue]:
    issues: list[Issue] = []
    steps = recipe.get("steps")
    inputs = recipe.get("inputs")
    if not isinstance(steps, list) or not isinstance(inputs, list) or not inputs:
        return issues

    broken = {i["path"].split("]")[0] + "]" for i in structural if i["path"].startswith("steps[")}

    seen: dict[str, int] = {}
    for i, step in enumerate(steps):
        sid = step.get("id") if isinstance(step, dict) else None
        if isinstance(sid, str):
            if sid in seen:
                issues.append(
                    _issue(["steps", i, "id"], f"步驟 id「{sid}」和 steps[{seen[sid]}] 重複")
                )
            seen.setdefault(sid, i)

    first = inputs[0] if isinstance(inputs[0], dict) else {}
    reader = first.get("reader", "text_lines")
    columns = input_columns.get(first.get("id", "src")) or spec.get("readers", {}).get(
        reader, {}
    ).get("columns")
    columns = list(columns) if columns is not None else None

    step_schema = schema.root["$defs"]
    for i, raw_step in enumerate(steps):
        if not isinstance(raw_step, dict) or raw_step.get("enabled", True) is False:
            continue
        if f"steps[{i}]" in broken or raw_step.get("type") not in spec["steps"]:
            columns = None  # 這一步有結構問題，之後的欄位無法推算
            continue
        info = spec["steps"][raw_step["type"]]
        ref_schema = step_schema[info["schema_ref"]]
        step = _with_defaults(raw_step, ref_schema)
        if columns is not None:
            for path, name in schema.column_refs(step, ref_schema, ["steps", i]):
                if name.startswith("__"):
                    if name not in SYSTEM_COLUMNS:
                        issues.append(
                            _issue(
                                path,
                                f"沒有系統欄位「{name}」",
                                f"系統欄位有：{'、'.join(SYSTEM_COLUMNS)}",
                            )
                        )
                elif name not in columns:
                    hint = _close(name, columns) or ""
                    issues.append(
                        _issue(
                            path,
                            f"欄位「{name}」在這一步之前不存在",
                            f"目前的欄位：{'、'.join(columns)}。{hint}".rstrip("。"),
                        )
                    )
        try:
            columns = apply_effect(columns, resolve_effect(info["effect"], step))
        except EffectError as err:
            issues.append(_issue(["steps", i], str(err)))
            columns = None
    return issues


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


def main(argv: list[str] | None = None, spec: dict[str, Any] | None = None) -> int:
    parser = argparse.ArgumentParser(description="驗證 Tablade recipe")
    parser.add_argument("recipe", type=Path)
    parser.add_argument("--data", type=Path, help="input 為 csv 時，用這個檔案的標題列檢查欄位引用")
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
        if args.data and isinstance(recipe, dict) and recipe.get("inputs"):
            first = recipe["inputs"][0]
            if isinstance(first, dict) and first.get("reader") == "csv":
                columns = {first.get("id", "src"): csv_header(args.data)}
        issues = validate(recipe, spec, columns)

    if args.json:
        print(json.dumps({"ok": not issues, "issues": issues}, ensure_ascii=False, indent=2))
    elif issues:
        print(f"recipe 有 {len(issues)} 個問題：")
        for item in issues:
            hint = f"（{item['hint']}）" if item.get("hint") else ""
            print(f"  {item['path']}: {item['error']}{hint}")
    else:
        print(f"recipe 通過驗證（版本 {spec['recipe_version']}）")
    return 1 if issues else 0


if __name__ == "__main__":
    sys.exit(main())
