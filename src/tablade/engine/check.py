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

from .run import SAMPLE_HEAD, SAMPLE_TAIL, RowWarning, RunError, RunReport, execute


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


def _peek_stages(
    data: dict[str, Any], base_dir: Path, dry_run: bool
) -> tuple[dict[str, list[str]], list[dict[str, Any]]]:
    """每個讀實際檔的階段，先看第一個檔有哪些欄位（不讀整個檔，給驗證器檢查欄位引用）。"""
    columns: dict[str, list[str]] = {}
    extra: list[dict[str, Any]] = []
    stages = data.get("stages")
    for si, stage in enumerate(stages if isinstance(stages, list) else []):
        source = stage.get("input") if isinstance(stage, dict) else None
        if not isinstance(source, dict) or source.get("reader") == "stages":
            continue
        files = source.get("files")
        if not (isinstance(files, list) and files and all(isinstance(f, str) and f for f in files)):
            continue
        try:
            found = peek_columns(
                files,
                source.get("reader", "text_lines"),
                source.get("encoding", "auto"),
                base_dir,
                source.get("sheet", "") if isinstance(source.get("sheet"), str) else "",
            )
        except (InputError, DecodeError, LookupError) as err:
            path = f"stages[{si}].input.files"
            extra.append(_issue(path, str(err), "error" if dry_run else "warning"))
            continue
        if isinstance(stage.get("name"), str) and found is not None:
            columns[stage["name"]] = found
    return columns, extra


def _step_path(recipe: Recipe, step_id: str) -> str:
    for si, stage in enumerate(recipe.stages):
        for i, step in enumerate(stage.steps):
            if step.id == step_id:
                return f"stages[{si}].steps[{i}]"
    return "stages"


def check_recipe(data: dict[str, Any], base_dir: Path, *, dry_run: bool = False) -> CheckResult:
    input_columns, extra = _peek_stages(data, base_dir, dry_run)
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
            result.issues.append(_issue(_step_path(result.recipe, err.step_id), str(err)))
        except (InputError, DecodeError) as err:
            result.issues.append(_issue("stages", str(err)))
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


def warning_place(w: RowWarning) -> str:
    """警告指到的位置：「@階段／檔名［工作表］:行號（原始：檔名:行號）」。"""
    where = f"{w.source_file}［{w.sheet}］" if w.sheet else f"{w.source_file}"
    if w.stage:
        where = f"@{w.stage}／{where}"
    where += f":{w.line_no}"
    if w.orig_source_file is not None or w.orig_line_no is not None:
        original = f"{w.orig_source_file}［{w.orig_sheet}］" if w.orig_sheet else w.orig_source_file
        if w.orig_stage:
            original = f"@{w.orig_stage}／{original}"
        where += f"（原始：{original}:{w.orig_line_no}）"
    return where


def format_report(report: RunReport, max_warnings: int = 10) -> str:
    multi = len(report.stages) > 1
    lines = ["輸入："]
    for f in report.inputs:
        stage = f"［{f.stage}］" if multi and f.stage else ""
        lines.append(f"  {stage}{f.name}（{f.encoding}，{f.bytes:,} bytes，{f.rows:,} 行）")
    previous = report.input_rows
    if report.mode == "sample":
        previous = report.sample_rows or 0
        lines.append(
            f"大檔：試跑只用每個檔案的前 {SAMPLE_HEAD:,} 行與最後 {SAMPLE_TAIL:,} 行"
            f"（共 {previous:,} 行），下面的列數是樣本的；匯出時會處理全部資料。"
        )
    if report.mode == "stream":
        lines.append(
            f"步驟（輸入共 {report.input_rows:,} 行，串流執行；每一步的列數只在試跑時提供）："
        )
    else:
        lines.append(f"步驟（輸入共 {previous:,} 行）：")
    stage = None
    for s in report.steps:
        if multi and s.stage != stage:
            stage = s.stage
            lines.append(f" 階段「{stage}」")
            previous = None
        warn = f"，警告 {s.warning_count:,}" if s.warning_count else ""
        if not s.enabled:
            lines.append(f"  {s.id:<6} [停用] {s.description}")
        elif s.rows is None:
            lines.append(f"  {s.id:<6} {s.description}{f'（{warn[1:]}）' if warn else ''}")
        elif previous is None:
            lines.append(
                f"  {s.id:<6} {s.rows:>8,} 列{f'（{warn[1:]}）' if warn else ''}  {s.description}"
            )
            previous = s.rows
        else:
            delta = s.rows - previous
            change = f"{delta:+,}" if delta else "±0"
            lines.append(f"  {s.id:<6} {s.rows:>8,} 列（{change}{warn}）  {s.description}")
            previous = s.rows
    warnings = [w for s in report.steps for w in s.warnings]
    if warnings:
        lines.append(
            f"警告（共 {report.warning_count:,} 則，列出前 {min(len(warnings), max_warnings)} 則）："
        )
        for w in warnings[:max_warnings]:
            lines.append(f"  {w.step} {warning_place(w)}  {w.message}")
    lines.append(
        f"結果：{report.rows:,} 列 × {len(report.columns)} 欄（{'、'.join(report.columns)}）"
    )
    if report.output:
        lines.append(f"輸出：{report.output}")
    if report.manifest:
        lines.append(f"處理紀錄：{report.manifest}")
    return "\n".join(lines)
