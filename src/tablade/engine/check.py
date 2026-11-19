"""檢查 recipe 檔：驗證器（結構＋語意）→ pydantic → 選擇性用真實資料試跑（DESIGN.md §17.4）。

CLI 的 ``tablade validate`` / ``tablade run`` 與之後 UI 的「匯入 recipe」共用這裡。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tablade.export.outputs import default_output_name, plan_outputs
from tablade.i18n import plural, t
from tablade.ingest.encoding import SUPPORTED, DecodeError
from tablade.ingest.sources import InputError, decode_warning, peek_columns, release_workers
from tablade.recipe.schema import Recipe, RecipeError, load_json, parse_recipe
from tablade.recipe.spec import build_spec
from tablade.recipe.validator import validate

from .run import SAMPLE_MB_ENV, RowWarning, RunError, RunReport, execute


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
    """每個讀實際檔的階段有哪些欄位（每個檔只讀標題列、取聯集，給驗證器檢查欄位引用）。"""
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
        encoding = source.get("encoding", "auto")
        if encoding not in SUPPORTED:
            continue  # 編碼寫錯由驗證器報（不要再多一則 Python 的 LookupError 原文）
        try:
            found = peek_columns(
                files,
                source.get("reader", "text_lines"),
                encoding,
                base_dir,
                source.get("sheet", "") if isinstance(source.get("sheet"), str) else "",
                members=source.get("members", "") if isinstance(source.get("members"), str) else "",
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
            _, result.report = execute(result.recipe, base_dir)
        except RunError as err:
            result.issues.append(_issue(_step_path(result.recipe, err.step_id), str(err)))
        except (InputError, DecodeError) as err:
            result.issues.append(_issue("stages", str(err)))
        finally:
            release_workers()  # 試跑用到的平行轉碼工作程序：跑完就關掉，不用時不佔記憶體
    return result


def output_plan_issues(
    data: dict[str, Any], base_dir: Path, default: str, out_dir: Path | None = None
) -> list[dict[str, Any]]:
    """輸出檔的規劃（``plan_outputs``）有問題的階段：檔名重複（含最後一個階段沒填、用 Recipe 名稱時）、
    寫出的位置是輸入檔。"""
    issues = []
    for k, planned in enumerate(plan_outputs(data, base_dir, default, out_dir)):
        if planned is not None and planned.error:
            issues.append(_issue(f"stages[{k}].output_file", planned.error))
    return issues


def check_recipe_file(
    path: Path, *, dry_run: bool = False, out_dir: Path | None = None
) -> CheckResult:
    """檢查 Recipe 檔。知道 Recipe 的位置，所以也檢查輸出檔的規劃（``tablade run`` 會寫到哪裡）；
    ``out_dir`` 是 ``tablade run --out-dir``（預設是 Recipe 所在的資料夾）。"""
    try:
        data = load_json(path)
    except RecipeError as err:
        return CheckResult(err.issues)
    except OSError as err:  # 找不到、是資料夾、沒有權限
        if isinstance(err, FileNotFoundError):
            reason = t("找不到這個檔", "file not found")
        elif isinstance(err, IsADirectoryError) or path.is_dir():  # Windows 是 PermissionError
            reason = t("這是資料夾，不是檔案", "this is a folder, not a file")
        else:
            reason = err.strerror or str(err)
        return CheckResult(
            [
                _issue(
                    "(Recipe)",
                    t(
                        f"讀不到 Recipe「{path}」：{reason}",
                        f'Can\'t read Recipe "{path}": {reason}',
                    ),
                )
            ]
        )
    result = check_recipe(data, path.parent, dry_run=dry_run)
    seen = {(i["path"], i["error"]) for i in result.issues}
    extra = [
        i
        for i in output_plan_issues(data, path.parent, default_output_name(path), out_dir)
        if (i["path"], i["error"]) not in seen
    ]
    result.issues += extra
    return result


def format_issues(issues: list[dict[str, Any]]) -> str:
    lines = []
    for item in issues:
        mark = t("錯誤", "Error") if item["severity"] == "error" else t("提醒", "Warning")
        hint = t(f"（{item['hint']}）", f" ({item['hint']})") if item.get("hint") else ""
        lines.append(f"  [{mark}] {item['path']}: {item['error']}{hint}")
    return "\n".join(lines)


def warning_place(w: RowWarning) -> str:
    """警告指到的位置：「@階段／檔名［工作表］:行號（原始：檔名:行號）」。"""
    slash = "／"  # i18n-ignore：來源名稱的寫法（和資料裡的 __source_file 一樣，英文畫面也是）
    where = (
        t(f"{w.source_file}［{w.sheet}］", f"{w.source_file} [{w.sheet}]")
        if w.sheet
        else f"{w.source_file}"
    )
    if w.stage:
        where = f"@{w.stage}{slash}{where}"
    where += f":{w.line_no}"
    if w.orig_source_file is not None or w.orig_line_no is not None:
        original = (
            t(f"{w.orig_source_file}［{w.orig_sheet}］", f"{w.orig_source_file} [{w.orig_sheet}]")
            if w.orig_sheet
            else w.orig_source_file
        )
        if w.orig_stage:
            original = f"@{w.orig_stage}{slash}{original}"
        where += t(
            f"（原始：{original}:{w.orig_line_no}）", f" (original: {original}:{w.orig_line_no})"
        )
    return where


def format_report(report: RunReport, max_warnings: int = 10) -> str:
    def rows(n: int) -> str:
        return t(f"{n:>8,} 列", f"{plural(n, 'row', 'rows'):>13}")

    multi = len(report.stages) > 1
    lines = [t("輸入：", "Inputs:")]
    for f in report.inputs:
        stage = t(f"［{f.stage}］", f"[{f.stage}] ") if multi and f.stage else ""
        lines.append(
            t(
                f"  {stage}{f.name}（{f.encoding}，{f.bytes:,} bytes，{f.rows:,} 行）",
                f"  {stage}{f.name} ({f.encoding}, {plural(f.bytes, 'byte', 'bytes')}, "
                f"{plural(f.rows, 'line', 'lines')})",
            )
        )
        if warning := decode_warning(f):
            lines.append(t(f"    警告：{warning}", f"    Warning: {warning}"))
    previous = report.input_rows
    if report.mode == "sample":
        previous = report.sample_rows or 0
        lines.append(
            t(
                f"大檔：試跑只用每個檔案的開頭與結尾（共 {previous:,} 行；樣本大小依這台電腦的記憶體決定，"
                f"要固定可設環境變數 {SAMPLE_MB_ENV}），下面的列數是樣本的；匯出時會處理全部資料。",
                "Large files: the trial run uses only the beginning and end of each file "
                f"({plural(previous, 'line', 'lines')} in total; the sample size depends on this "
                f"computer's memory, set the environment variable {SAMPLE_MB_ENV} to fix it), so the "
                "row counts below are from the sample; export processes all data.",
            )
        )
    if report.mode == "stream":
        lines.append(
            t(
                f"步驟（輸入共 {report.input_rows:,} 行，串流執行；每一步的列數只在試跑時提供）：",
                f"Steps ({plural(report.input_rows, 'input line', 'input lines')}, streamed; "
                "per-step row counts are only shown in trial runs):",
            )
        )
    else:
        lines.append(
            t(
                f"步驟（輸入共 {previous:,} 行）：",
                f"Steps ({plural(previous, 'input line', 'input lines')}):",
            )
        )
    stage = None
    for s in report.steps:
        if multi and s.stage != stage:
            stage = s.stage
            lines.append(t(f" 階段「{stage}」", f' Stage "{stage}"'))
            previous = None
        warns = (
            t(f"警告 {s.warning_count:,}", plural(s.warning_count, "warning", "warnings"))
            if s.warning_count
            else ""
        )
        paren = t(f"（{warns}）", f" ({warns})") if warns else ""
        if not s.enabled:
            lines.append(
                t(f"  {s.id:<6} [停用] {s.description}", f"  {s.id:<6} [disabled] {s.description}")
            )
        elif s.rows is None:
            lines.append(f"  {s.id:<6} {s.description}{paren}")
        elif previous is None:
            lines.append(f"  {s.id:<6} {rows(s.rows)}{paren}  {s.description}")
            previous = s.rows
        else:
            delta = s.rows - previous
            change = f"{delta:+,}" if delta else "±0"
            if warns:
                change += t("，", ", ") + warns
            paren = t(f"（{change}）", f" ({change})")
            lines.append(f"  {s.id:<6} {rows(s.rows)}{paren}  {s.description}")
            previous = s.rows
    warnings = [w for s in report.steps for w in s.warnings]
    if warnings:
        shown = min(len(warnings), max_warnings)
        lines.append(
            t(
                f"警告（共 {report.warning_count:,} 則，列出前 {shown} 則）：",
                f"Warnings ({report.warning_count:,} in total, showing the first {shown}):",
            )
        )
        for w in warnings[:max_warnings]:
            lines.append(f"  {w.step} {warning_place(w)}  {w.message}")
    columns = t("、", ", ").join(report.columns)
    lines.append(
        t(
            f"結果：{report.rows:,} 列 × {len(report.columns)} 欄（{columns}）",
            f"Result: {plural(report.rows, 'row', 'rows')} × "
            f"{plural(len(report.columns), 'column', 'columns')} ({columns})",
        )
    )
    middle = [o for o in report.outputs if o.path != report.output]
    for o in middle:
        lines.append(
            t(
                f"輸出（階段「{o.stage}」）：{o.path}（{o.rows:,} 列）",
                f'Output (stage "{o.stage}"): {o.path} ({plural(o.rows, "row", "rows")})',
            )
        )
        if o.manifest:
            lines.append(t(f"處理紀錄：{o.manifest}", f"Processing log: {o.manifest}"))
    if report.output:
        lines.append(t(f"輸出：{report.output}", f"Output: {report.output}"))
    if report.manifest:
        lines.append(t(f"處理紀錄：{report.manifest}", f"Processing log: {report.manifest}"))
    return "\n".join(lines)
