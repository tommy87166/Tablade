"""檢查 recipe 檔：驗證器（結構＋語意）→ pydantic → 選擇性用真實資料試跑（DESIGN.md §17.4）。

CLI 的 ``tablade validate`` / ``tablade run`` 與之後 UI 的「匯入 recipe」共用這裡。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tablade.ingest.encoding import DecodeError
from tablade.ingest.sources import InputError, peek_columns
from tablade.recipe.schema import Recipe, RecipeError, load_json, parse_recipe
from tablade.recipe.spec import build_spec
from tablade.recipe.validator import validate

from .run import RunError, RunReport, execute


@dataclass
class CheckResult:
    issues: list[dict[str, Any]]
    recipe: Recipe | None = None
    report: RunReport | None = None

    @property
    def ok(self) -> bool:
        return not any(i["severity"] == "error" for i in self.issues)


def _issue(path: str, error: str, severity: str = "error") -> dict[str, Any]:
    return {"path": path, "error": error, "severity": severity}


def check_recipe(data: dict[str, Any], base_dir: Path, *, dry_run: bool = False) -> CheckResult:
    extra: list[dict[str, Any]] = []
    input_columns = None
    inputs = data.get("inputs")
    if (
        isinstance(inputs, list)
        and inputs
        and isinstance(inputs[0], dict)
        and isinstance(inputs[0].get("pattern"), str)
    ):
        first = inputs[0]
        try:
            columns = peek_columns(
                first["pattern"],
                first.get("reader", "text_lines"),
                first.get("encoding", "auto"),
                base_dir,
            )
            input_columns = {first.get("id", "src"): columns}
        except (InputError, DecodeError, LookupError) as err:
            extra.append(_issue("inputs[0].pattern", str(err), "error" if dry_run else "warning"))

    issues = validate(data, build_spec(), input_columns) + extra
    result = CheckResult(issues)
    if not result.ok:
        return result
    try:
        result.recipe = parse_recipe(data)
    except RecipeError as err:
        result.issues += err.issues
        return result
    if dry_run:
        try:
            _, result.report = execute(result.recipe, base_dir, write_output=False)
        except RunError as err:
            index = next(i for i, s in enumerate(result.recipe.steps) if s.id == err.step_id)
            result.issues.append(_issue(f"steps[{index}]", str(err)))
        except (InputError, DecodeError) as err:
            result.issues.append(_issue("inputs[0]", str(err)))
    return result


def check_recipe_file(path: Path, *, dry_run: bool = False) -> CheckResult:
    try:
        data = load_json(path)
    except RecipeError as err:
        return CheckResult(err.issues)
    return check_recipe(data, path.parent, dry_run=dry_run)


def format_issues(issues: list[dict[str, Any]]) -> str:
    lines = []
    for item in issues:
        mark = "錯誤" if item["severity"] == "error" else "提醒"
        hint = f"（{item['hint']}）" if item.get("hint") else ""
        lines.append(f"  [{mark}] {item['path']}: {item['error']}{hint}")
    return "\n".join(lines)


def format_report(report: RunReport, max_warnings: int = 10) -> str:
    lines = ["輸入："]
    for f in report.inputs:
        lines.append(f"  {f.path}（{f.encoding}，{f.bytes:,} bytes，{f.rows:,} 列）")
    lines.append(f"步驟（輸入共 {report.input_rows:,} 列）：")
    previous = report.input_rows
    for s in report.steps:
        if not s.enabled:
            lines.append(f"  {s.id:<6} [停用] {s.description}")
            continue
        delta = s.rows - previous if s.rows is not None else 0
        change = f"{delta:+,}" if delta else "±0"
        warn = f"，警告 {s.warning_count:,}" if s.warning_count else ""
        lines.append(f"  {s.id:<6} {s.rows:>8,} 列（{change}{warn}）  {s.description}")
        previous = s.rows if s.rows is not None else previous
    warnings = [w for s in report.steps for w in s.warnings]
    if warnings:
        lines.append(
            f"警告（共 {report.warning_count:,} 則，列出前 {min(len(warnings), max_warnings)} 則）："
        )
        for w in warnings[:max_warnings]:
            lines.append(f"  {w.step} {w.source_file}:{w.line_no}  {w.message}")
    lines.append(
        f"結果：{report.rows:,} 列 × {len(report.columns)} 欄（{'、'.join(report.columns)}）"
    )
    if report.output:
        lines.append(f"輸出：{report.output}")
    return "\n".join(lines)
