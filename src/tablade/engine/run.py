"""執行 recipe（DESIGN.md §8）。

依資料量選擇做法：
- ``eager``：資料不大時整份讀進記憶體，每一步 ``collect()`` 一次，每步的列數、警告、行號都很便宜。
- ``sample``：資料很大時的試跑與預覽，只用每個來源檔的前幾行與最後幾行（``SAMPLE_HEAD`` / ``SAMPLE_TAIL``）。
- ``stream``：資料很大時的完整執行。整條 lazy 查詢用串流引擎寫成暫存 Parquet（記憶體用量固定），
  再從暫存檔取出警告、寫出最後的結果。每一步的列數只在 eager / sample 模式提供。

步驟本身都是 LazyFrame → LazyFrame，三種模式共用。
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

import polars as pl

from tablade.export.parquet import write_parquet
from tablade.ingest.sources import Progress, SourceFile, sample, scan, staging_dir
from tablade.recipe.schema import Recipe
from tablade.recipe.validator import EffectError
from tablade.steps.base import LINE_NO, SOURCE_FILE, SYSTEM_PREFIX, TEMP_PREFIX, Step, StepError

MAX_WARNINGS_PER_CHECK = 100
SAMPLE_THRESHOLD = 200_000
"""輸入超過這麼多行時，預覽與試跑改用樣本，完整執行改用串流。"""
SAMPLE_HEAD = 100_000
SAMPLE_TAIL = 1_000

Mode = Literal["auto", "eager", "sample", "stream"]


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
    mode: str = "eager"
    sample_rows: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def warning_count(self) -> int:
        return sum(s.warning_count for s in self.steps)


class RunError(RuntimeError):
    def __init__(self, step: Step, message: str) -> None:
        self.step_id = step.id
        super().__init__(f"步驟 {step.id}（{step.type_name()}）失敗：{message}")


def _data_columns(names: list[str]) -> list[str]:
    return [c for c in names if not c.startswith(SYSTEM_PREFIX)]


def _apply(step: Step, lf: pl.LazyFrame) -> pl.LazyFrame:
    try:
        return step.apply(lf)
    except (StepError, EffectError) as err:
        raise RunError(step, str(err)) from None
    except pl.exceptions.PolarsError as err:
        raise RunError(step, f"{type(err).__name__}: {err}") from None


def _warnings(
    step: Step, frame: pl.DataFrame | pl.LazyFrame, limit: int
) -> tuple[int, list[RowWarning]]:
    total, found = 0, []
    lazy = frame.lazy()
    names = lazy.collect_schema().names()
    for check in step.warning_checks():
        detail = check.detail if check.detail is not None else pl.lit(None)
        hits = lazy.filter(check.condition.fill_null(False))
        total += hits.select(pl.len()).collect(engine="streaming").item()
        rows = (
            hits.select(
                pl.col(SOURCE_FILE) if SOURCE_FILE in names else pl.lit(None).alias(SOURCE_FILE),
                pl.col(LINE_NO) if LINE_NO in names else pl.lit(None).alias(LINE_NO),
                detail.alias("__detail"),
            )
            .head(limit)
            .collect(engine="streaming")
        )
        for source_file, line_no, value in rows.iter_rows():
            message = check.message.format(value) if "{}" in check.message else check.message
            found.append(RowWarning(step.id, message, source_file, line_no))
    return total, found


def apply_step(
    step: Step, frame: pl.DataFrame, limit: int = MAX_WARNINGS_PER_CHECK
) -> tuple[pl.DataFrame, int, list[RowWarning]]:
    """套用一個步驟（eager），回傳（移除暫存欄位後的結果、警告總數、前幾則警告）。"""
    try:
        out = _apply(step, frame.lazy()).collect(engine="streaming")
    except pl.exceptions.PolarsError as err:
        raise RunError(step, f"{type(err).__name__}: {err}") from None
    total, warnings = _warnings(step, out, limit)
    temp = [c for c in out.columns if c.startswith(TEMP_PREFIX)]
    return out.drop(temp), total, warnings


def choose_mode(files: list[SourceFile], write_output: bool, mode: Mode) -> Mode:
    if mode != "auto":
        return mode
    if sum(f.rows for f in files) <= SAMPLE_THRESHOLD:
        return "eager"
    return "stream" if write_output else "sample"


def execute(
    recipe: Recipe,
    base_dir: Path,
    *,
    write_output: bool = True,
    output: Path | None = None,
    mode: Mode = "auto",
    limit: int = MAX_WARNINGS_PER_CHECK,
    progress: Progress = None,
) -> tuple[pl.DataFrame | None, RunReport]:
    """執行 recipe。``stream`` 模式不回傳 DataFrame（結果直接寫到檔案）。"""
    source = recipe.inputs[0]
    lf, files = scan(source.pattern, source.reader, source.encoding, base_dir, progress)
    input_rows = sum(f.rows for f in files)
    mode = choose_mode(files, write_output, mode)
    if write_output and output is None:
        raise ValueError("寫出檔案時要指定 output（recipe 不記輸出位置）")
    if mode == "stream":
        if output is None:
            raise ValueError("stream 模式一定會寫出檔案")
        return None, _execute_stream(recipe, lf, files, input_rows, output, limit, progress)

    frame = (
        lf.collect(engine="streaming")
        if mode == "eager"
        else sample(lf, files, source.reader, SAMPLE_HEAD, SAMPLE_TAIL)
    )
    sample_rows = frame.height if mode == "sample" else None
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
                _data_columns(frame.columns),
                total,
                warnings,
            )
        )
    if not recipe.output.keep_system_columns:
        frame = frame.select(_data_columns(frame.columns))
    written = None
    if output is not None and write_output:
        write_parquet(frame, output)
        written = output.as_posix()
    report = RunReport(
        files,
        input_rows,
        reports,
        frame.height,
        _data_columns(frame.columns),
        written,
        mode,
        sample_rows,
    )
    return frame, report


def _execute_stream(
    recipe: Recipe,
    lf: pl.LazyFrame,
    files: list[SourceFile],
    input_rows: int,
    target: Path,
    limit: int,
    progress: Progress,
) -> RunReport:
    def report(stage: str) -> None:
        if progress is not None:
            progress({"stage": stage, "file": "", "done": 0, "total": 0})

    enabled = recipe.enabled_steps()
    for step in enabled:
        report(f"準備 {step.id}")
        lf = _apply(step, lf)  # 暫存欄位（__w_）一路保留到最後，才能取出警告

    staged = staging_dir() / f"run-{uuid.uuid4().hex}.parquet"
    try:
        report("執行（串流）")
        try:
            lf.sink_parquet(staged, engine="streaming")
        except pl.exceptions.PolarsError as err:
            raise RuntimeError(f"執行失敗：{type(err).__name__}: {err}") from None
        result = pl.scan_parquet(staged)
        reports = []
        for step in recipe.steps:
            total, warnings = _warnings(step, result, limit) if step.enabled else (0, [])
            reports.append(
                StepReport(
                    step.id,
                    step.type_name(),
                    step.describe(),
                    step.enabled,
                    None,
                    None,
                    total,
                    warnings,
                )
            )
        names = result.collect_schema().names()
        keep = [c for c in names if not c.startswith(TEMP_PREFIX)]
        if not recipe.output.keep_system_columns:
            keep = _data_columns(keep)
        report("寫出 Parquet")
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(f".{target.name}.{uuid.uuid4().hex[:8]}.tmp")
        try:
            result.select(keep).sink_parquet(tmp, engine="streaming")
            tmp.replace(target)
        finally:
            tmp.unlink(missing_ok=True)
        rows = pl.scan_parquet(target).select(pl.len()).collect().item()
    finally:
        staged.unlink(missing_ok=True)
    columns = _data_columns(keep)
    return RunReport(files, input_rows, reports, rows, columns, target.as_posix(), "stream")
