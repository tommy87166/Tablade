"""執行 recipe（DESIGN.md §8）。

依資料量選擇做法：
- ``eager``：資料不大時整份讀進記憶體，每一步 ``collect()`` 一次，每步的列數、警告、行號都很便宜。
- ``sample``：資料很大時的試跑與預覽，只用每個來源檔的前幾行與最後幾行（``SAMPLE_HEAD`` / ``SAMPLE_TAIL``）。
- ``stream``：資料很大時的完整執行。整條 lazy 查詢用串流引擎寫成暫存 Parquet（記憶體用量固定），
  再從暫存檔取出警告、寫出最後的結果。每一步的列數只在 eager / sample 模式提供。
  暫存結果分成很多個檔寫，另一條執行緒看寫完了幾個來回報進度（``engine/progress.py``）。

三種模式都「各檔分開處理」：資料是一串 part（每個來源檔一個），分檔的步驟一次只套在一個 part 上，
到「合併來源檔」（``combines``）或寫出前才接起來（DESIGN.md §8）。
"""

from __future__ import annotations

import shutil
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

import polars as pl

from tablade.engine.progress import Watcher, rows_per_file, with_positions
from tablade.export.manifest import write_manifest
from tablade.export.parquet import write_parquet
from tablade.ingest.sources import (
    Part,
    PartsMismatch,
    Progress,
    SourceFile,
    concat,
    sample,
    scan_parts,
    staging_dir,
)
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
    manifest: str | None = None
    """處理紀錄（xxx.manifest.json）的位置；沒有寫出時是 None。"""

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


def _apply(step: Step, lf: pl.LazyFrame, source: str = "") -> pl.LazyFrame:
    where = f"（來源檔 {source}）" if source else ""
    try:
        return step.apply(lf)
    except (StepError, EffectError) as err:
        raise RunError(step, f"{err}{where}") from None
    except pl.exceptions.PolarsError as err:
        raise RunError(step, f"{type(err).__name__}: {err}{where}") from None


def split_parts(frame: pl.DataFrame) -> list[Part]:
    """把讀進記憶體的資料依 ``__source_file`` 切成 parts（每段連續的同一個來源檔；不複製資料）。"""
    if frame.height == 0 or SOURCE_FILE not in frame.columns:
        return [("", frame.lazy())]
    parts, offset = [], 0
    for length, name in frame.get_column(SOURCE_FILE).rle().struct.unnest().iter_rows():
        parts.append((name, frame.slice(offset, length).lazy()))
        offset += length
    return parts


def concat_parts(parts: list[Part], step: Step | None = None) -> pl.LazyFrame:
    """把 parts 接成一個 LazyFrame；欄位不一樣時指出是哪一步。"""
    try:
        return concat(parts)
    except PartsMismatch as err:
        if step is None:
            raise
        raise RunError(step, str(err)) from None


def apply_parts(step: Step, parts: list[Part], progress: Progress = None) -> list[Part]:
    """套用一個步驟：合併型步驟接起所有 part，其他步驟每個 part 各自套用。"""
    if step.combines:
        try:
            return step.combine(parts)
        except (StepError, EffectError, PartsMismatch) as err:
            raise RunError(step, str(err)) from None
        except pl.exceptions.PolarsError as err:
            raise RunError(step, f"{type(err).__name__}: {err}") from None
    out = []
    for i, (name, lf) in enumerate(parts):
        if progress is not None and len(parts) > 1:
            progress(
                {
                    "stage": f"準備 {step.id}",
                    "file": name,
                    "done": i,
                    "total": len(parts),
                    "unit": "files",
                }
            )
        out.append((name, _apply(step, lf, name if len(parts) > 1 else "")))
    return out


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
    """套用一個步驟（eager），回傳（移除暫存欄位後的結果、警告總數、前幾則警告）。

    分檔與合併型步驟依來源檔切開再套用；一列一列的步驟直接套在整張表上（結果一樣，比較快）。
    """
    try:
        if step.per_file or step.combines:
            lf = concat_parts(apply_parts(step, split_parts(frame)), step)
        else:
            lf = _apply(step, frame.lazy())
        out = lf.collect(engine="streaming")
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
    parts, files = scan_parts(source.files, source.reader, source.encoding, base_dir, progress)
    input_rows = sum(f.rows for f in files)
    mode = choose_mode(files, write_output, mode)
    if write_output and output is None:
        raise ValueError("寫出檔案時要指定 output（recipe 不記輸出位置）")
    if mode == "stream":
        if output is None:
            raise ValueError("stream 模式一定會寫出檔案")
        # 每一列帶著在所有輸入裡的位置，執行中才能回報進度（寫出前拿掉）
        parts = with_positions(parts, files, source.reader)
        report = _execute_stream(recipe, parts, files, input_rows, output, limit, progress)
        return None, _with_manifest(recipe, report, output)

    frame = (
        concat(parts).collect(engine="streaming")
        if mode == "eager"
        else sample(parts, files, source.reader, SAMPLE_HEAD, SAMPLE_TAIL)
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
    if written is not None and output is not None:
        _with_manifest(recipe, report, output)
    return frame, report


def _with_manifest(recipe: Recipe, report: RunReport, output: Path) -> RunReport:
    """寫出處理紀錄（Recipe 的輸出設定可以關掉）。"""
    if recipe.output.manifest:
        report.manifest = write_manifest(recipe, report, output).as_posix()
    return report


def _execute_stream(
    recipe: Recipe,
    parts: list[Part],
    files: list[SourceFile],
    input_rows: int,
    target: Path,
    limit: int,
    progress: Progress,
) -> RunReport:
    def report(stage: str) -> None:
        if progress is not None:
            progress({"stage": stage, "file": "", "done": 0, "total": 0})

    last = None
    for step in recipe.enabled_steps():
        report(f"準備 {step.id}")
        # 每個檔各自一條流水線；暫存欄位（__w_）一路保留到最後，才能取出警告
        parts = apply_parts(step, parts, progress)
        last = step
    lf = concat_parts(parts, last)

    staged = staging_dir() / f"run-{uuid.uuid4().hex}"
    try:
        # 分成很多個檔寫（大約每 1% 一個），另一條執行緒看寫完了幾個來回報進度
        with Watcher(staged, files, progress) as watcher:
            try:
                lf.sink_parquet(
                    pl.PartitionBy(staged, max_rows_per_file=rows_per_file(input_rows)),
                    engine="streaming",
                    mkdir=True,
                )
            except pl.exceptions.PolarsError as err:
                raise RuntimeError(f"執行失敗：{type(err).__name__}: {err}") from None
        watcher.poll(finished=True)
        if progress is not None:
            progress(
                {
                    "stage": "執行（串流）",
                    "file": "",
                    "done": input_rows,
                    "total": input_rows,
                    "unit": "rows",
                }
            )
        # 檔名是補零的流水號（00000000.parquet…），排序就是寫出的順序
        result = pl.scan_parquet(sorted(staged.glob("*.parquet")))
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
        shutil.rmtree(staged, ignore_errors=True)
    columns = _data_columns(keep)
    return RunReport(files, input_rows, reports, rows, columns, target.as_posix(), "stream")
