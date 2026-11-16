"""執行 recipe。

M1 的做法：每一步都 ``collect()`` 一次，換來每一步的列數、警告與行號都很便宜就能取得；
步驟本身仍是 LazyFrame → LazyFrame 的純函式，之後改成整條 lazy 查詢＋``sink_parquet`` 不必動步驟。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import polars as pl

from tablade.export.parquet import write_parquet
from tablade.ingest.sources import SourceFile, load
from tablade.recipe.schema import Recipe
from tablade.steps.base import LINE_NO, SOURCE_FILE, SYSTEM_PREFIX, TEMP_PREFIX, Step, StepError

MAX_WARNINGS_PER_CHECK = 100


@dataclass
class RowWarning:
    step: str
    message: str
    source_file: str | None
    line_no: int | None


@dataclass
class StepReport:
    id: str
    type: str
    description: str
    enabled: bool
    rows: int | None
    columns: list[str] | None
    warning_count: int = 0
    warnings: list[RowWarning] = field(default_factory=list)


@dataclass
class RunReport:
    inputs: list[SourceFile]
    input_rows: int
    steps: list[StepReport]
    rows: int
    columns: list[str]
    output: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def warning_count(self) -> int:
        return sum(s.warning_count for s in self.steps)


class RunError(RuntimeError):
    def __init__(self, step: Step, message: str) -> None:
        self.step_id = step.id
        super().__init__(f"步驟 {step.id}（{step.type_name()}）失敗：{message}")


def _data_columns(frame: pl.DataFrame) -> list[str]:
    return [c for c in frame.columns if not c.startswith(SYSTEM_PREFIX)]


def _collect_warnings(step: Step, frame: pl.DataFrame, limit: int) -> tuple[int, list[RowWarning]]:
    total, found = 0, []
    for check in step.warning_checks():
        detail = check.detail if check.detail is not None else pl.lit(None)
        hits = frame.filter(check.condition.fill_null(False)).select(
            pl.col(SOURCE_FILE)
            if SOURCE_FILE in frame.columns
            else pl.lit(None).alias(SOURCE_FILE),
            pl.col(LINE_NO) if LINE_NO in frame.columns else pl.lit(None).alias(LINE_NO),
            detail.alias("__detail"),
        )
        total += hits.height
        for source_file, line_no, value in hits.head(limit).iter_rows():
            message = check.message.format(value) if "{}" in check.message else check.message
            found.append(RowWarning(step.id, message, source_file, line_no))
    return total, found


def apply_step(
    step: Step, frame: pl.DataFrame, limit: int = MAX_WARNINGS_PER_CHECK
) -> tuple[pl.DataFrame, int, list[RowWarning]]:
    """套用一個步驟，回傳（移除暫存欄位後的結果、警告總數、前幾則警告）。"""
    try:
        out = step.apply(frame.lazy()).collect()
    except StepError as err:
        raise RunError(step, str(err)) from None
    except pl.exceptions.PolarsError as err:
        raise RunError(step, f"{type(err).__name__}: {err}") from None
    total, warnings = _collect_warnings(step, out, limit)
    temp = [c for c in out.columns if c.startswith(TEMP_PREFIX)]
    return out.drop(temp), total, warnings


def execute(
    recipe: Recipe,
    base_dir: Path,
    *,
    write_output: bool = True,
    limit: int = MAX_WARNINGS_PER_CHECK,
) -> tuple[pl.DataFrame, RunReport]:
    source = recipe.inputs[0]
    frame, files = load(source.pattern, source.reader, source.encoding, base_dir)
    input_rows = frame.height

    reports = []
    for step in recipe.steps:
        if not step.enabled:
            reports.append(
                StepReport(step.id, step.type_name(), step.describe(), False, None, None)
            )
            continue
        frame, total, warnings = apply_step(step, frame, limit)
        reports.append(
            StepReport(
                step.id,
                step.type_name(),
                step.describe(),
                True,
                frame.height,
                _data_columns(frame),
                total,
                warnings,
            )
        )

    if not recipe.output.keep_system_columns:
        frame = frame.select(_data_columns(frame))

    output = None
    if write_output:
        target = base_dir / recipe.output.path
        write_parquet(frame, target)
        output = target.as_posix()

    report = RunReport(files, input_rows, reports, frame.height, _data_columns(frame), output)
    return frame, report
