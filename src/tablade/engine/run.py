"""執行 recipe（DESIGN.md §8）。

依資料量選擇做法：
- ``eager``：資料不大時整份讀進記憶體，每一步 ``collect()`` 一次，每步的列數、警告、行號都很便宜。
- ``sample``：資料很大時的試跑與預覽，只用每個來源檔的前幾行與最後幾行（``SAMPLE_HEAD`` / ``SAMPLE_TAIL``）。
- ``stream``：資料很大時的完整執行。整條 lazy 查詢用串流引擎寫成暫存 Parquet（記憶體用量固定），
  再從暫存檔取出警告、寫出最後的結果。每一步的列數只在 eager / sample 模式提供。
  暫存結果分成很多個檔寫，另一條執行緒看寫完了幾個來回報進度（``engine/progress.py``）。

三種模式都「各檔分開處理」：資料是一串 part（每個來源檔一個），分檔的步驟一次只套在一個 part 上，
到「合併來源檔」（``combines``）或寫出前才接起來（DESIGN.md §8）。

Recipe 由好幾個階段組成，依序執行：每個階段讀實際檔或前面階段的結果（每個來源各自是一個 part，
名稱是「@階段／檔名」，系統欄位 ``__stage`` 記階段），跑自己的步驟。中間階段的結果在 eager / sample
模式留在記憶體，在 stream 模式寫成暫存 Parquet；結合的右表就是前面階段的結果（``Step.bind``）。
"""

from __future__ import annotations

import shutil
import uuid
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Literal

import polars as pl

from tablade.engine.progress import Watcher, rows_per_file, with_positions, with_row_positions
from tablade.export.manifest import write_manifest
from tablade.export.parquet import write_parquet
from tablade.ingest.sources import (
    Part,
    PartsMismatch,
    Progress,
    SourceFile,
    align,
    concat,
    sample,
    scan_parts,
    staging_dir,
)
from tablade.recipe.schema import Recipe, Stage
from tablade.recipe.validator import EffectError
from tablade.steps.base import (
    LINE_NO,
    ORIG_LINE_NO,
    ORIG_SHEET,
    ORIG_SOURCE_FILE,
    ORIG_STAGE,
    SHEET,
    SOURCE_FILE,
    STAGE,
    SYSTEM_PREFIX,
    TEMP_PREFIX,
    Step,
    StepError,
    source_name,
)

MAX_WARNINGS_PER_CHECK = 100
SAMPLE_THRESHOLD = 200_000
"""輸入超過這麼多行時，預覽與試跑改用樣本，完整執行改用串流。"""
SAMPLE_HEAD = 100_000
SAMPLE_TAIL = 1_000
PART = f"{TEMP_PREFIX}part"
"""串流執行時，中間階段的暫存結果裡每一列屬於第幾個 part（下一個階段讀回來時依它分開）。"""

Mode = Literal["auto", "eager", "sample", "stream"]


@dataclass
class RowWarning:
    step: str
    message: str
    source_file: str | None
    line_no: int | None
    sheet: str | None = None
    """讀 Excel 全部工作表時，這一列在哪個工作表。"""
    stage: str | None = None
    """從前面的階段讀進來的列：從哪個階段讀的（__stage）。"""
    orig_source_file: str | None = None
    orig_line_no: int | None = None
    orig_sheet: str | None = None
    orig_stage: str | None = None
    """合併來源檔之後的列：合併前的來源檔、行號、工作表、階段。"""


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
    stage: str = ""
    """這一步屬於哪個階段。"""


@dataclass
class StageReport:
    name: str
    rows: int | None
    columns: list[str] | None


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
    stages: list[StageReport] = field(default_factory=list)
    """每個階段的結果（最後一個階段就是輸出）。"""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def warning_count(self) -> int:
        return sum(s.warning_count for s in self.steps)


class RunError(RuntimeError):
    def __init__(self, step: Step, message: str) -> None:
        self.step_id = step.id
        super().__init__(f"步驟 {step.id}（{step.type_name()}）失敗：{message}")


class StageTracker:
    """每個階段的狀態（等待中、處理中、完成、失敗），透過 progress 回報給畫面。

    事件和一般進度同一個格式，另外多一個 ``stages``：``[{"name", "status", "rows"}]``；
    api.py 把它送到畫面的階段進度（階段面板的轉圈／打勾、進度視窗），不顯示在進度列。
    """

    def __init__(self, names: list[str], progress: Progress) -> None:
        self.items: list[dict[str, Any]] = [
            {"name": n, "status": "waiting", "rows": None} for n in names
        ]
        self.progress = progress
        self.current: int | None = None
        self._emit()

    def _emit(self) -> None:
        if self.progress is not None:
            stages = [dict(item) for item in self.items]
            self.progress({"stage": "", "file": "", "done": 0, "total": 0, "stages": stages})

    def reading(self, index: int) -> None:
        """讀這個階段的檔（還沒開始處理）：失敗時算這個階段的。"""
        self.current = index

    def start(self, index: int) -> None:
        self.current = index
        self.items[index]["status"] = "running"
        self._emit()

    def done(self, index: int, rows: int | None) -> None:
        self.items[index].update(status="done", rows=rows)
        self.current = None
        self._emit()

    def error(self, index: int) -> None:
        """這個階段算完了但有錯（例如某一步參數不合法）。"""
        self.items[index]["status"] = "error"
        self.current = None
        self._emit()

    def skip(self, index: int) -> None:
        """這個階段還不能算（例如還沒加檔案）：回到等待中。"""
        self.items[index]["status"] = "waiting"
        self.current = None
        self._emit()

    def fail(self) -> None:
        if self.current is not None:
            self.items[self.current]["status"] = "error"
            self.current = None
            self._emit()


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


def split_parts(frame: pl.DataFrame, with_stage: bool = True) -> list[Part]:
    """把讀進記憶體的資料依來源切成 parts（每段連續的同一個來源；不複製資料）。

    來源通常是 ``__source_file``；讀 Excel 全部工作表時每個工作表各自是一個來源；從前面的階段讀進來的
    名稱是「@階段／檔名」（``with_stage=False`` 時不加，給下一個階段讀的時候換成新的階段）。
    """
    if frame.height == 0 or SOURCE_FILE not in frame.columns:
        return [("", frame.lazy())]
    parts, offset = [], 0
    columns = frame.columns if with_stage else [c for c in frame.columns if c != STAGE]
    names = frame.select(source_name(columns)).to_series()
    for length, name in names.rle().struct.unnest().iter_rows():
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


_WHERE = (SHEET, STAGE, ORIG_SOURCE_FILE, ORIG_LINE_NO, ORIG_SHEET, ORIG_STAGE)
"""警告除了來源檔與行號，另外帶的位置（有這些欄位時）。"""


def _warnings(
    step: Step, frame: pl.DataFrame | pl.LazyFrame, limit: int, on_input: bool = False
) -> tuple[int, list[RowWarning]]:
    """依步驟的警告條件找出要警告的列；``on_input`` 是對這一步的輸入檢查的那些條件。"""
    total, found = 0, []
    lazy = frame.lazy()
    names = lazy.collect_schema().names()
    try:
        checks = [c for c in step.warning_checks() if c.on_input == on_input]
    except (StepError, EffectError) as err:
        raise RunError(step, str(err)) from None
    for check in checks:
        detail = check.detail if check.detail is not None else pl.lit(None)
        hits = lazy.filter(check.condition.fill_null(False))
        total += hits.select(pl.len()).collect(engine="streaming").item()
        rows = (
            hits.select(
                pl.col(SOURCE_FILE) if SOURCE_FILE in names else pl.lit(None).alias(SOURCE_FILE),
                pl.col(LINE_NO) if LINE_NO in names else pl.lit(None).alias(LINE_NO),
                detail.alias("__detail"),
                *[pl.col(c) if c in names else pl.lit(None).alias(c) for c in _WHERE],
            )
            .head(limit)
            .collect(engine="streaming")
        )
        for source_file, line_no, value, *where in rows.iter_rows():
            message = check.message.format(value) if "{}" in check.message else check.message
            found.append(RowWarning(step.id, message, source_file, line_no, *where))
    return total, found


def _has_input_checks(step: Step) -> bool:
    try:
        return any(c.on_input for c in step.warning_checks())
    except (StepError, EffectError) as err:
        raise RunError(step, str(err)) from None


Tables = dict[str, pl.DataFrame] | dict[str, pl.LazyFrame]


def apply_step(
    step: Step,
    frame: pl.DataFrame,
    limit: int = MAX_WARNINGS_PER_CHECK,
    tables: Tables | None = None,
) -> tuple[pl.DataFrame, int, list[RowWarning]]:
    """套用一個步驟（eager），回傳（移除暫存欄位後的結果、警告總數、前幾則警告）。

    分檔與合併型步驟依來源檔切開再套用；一列一列的步驟直接套在整張表上（結果一樣，比較快）。
    ``tables`` 是前面階段的結果（結合的右表）。
    """
    step = step.bind(tables)
    before = _warnings(step, frame, limit, on_input=True) if _has_input_checks(step) else (0, [])
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
    return out.drop(temp), before[0] + total, before[1] + warnings


def stage_parts(names: list[str], results: dict[str, list[Part]]) -> list[Part]:
    """讀前面階段的結果：每個來源各自是一個 part，名稱加上「@階段／」、系統欄位 __stage 記階段。

    欄位對齊成同一組（缺的欄是空值）；同名欄的型別不一樣時丟出 ``InputError``。
    """
    parts = []
    for name in names:
        for base, lf in results[name]:
            staged = lf.with_columns(pl.lit(name, dtype=pl.String).alias(STAGE))
            parts.append((f"@{name}／{base}", staged))
    return align(parts)


def stage_frame(names: list[str], frames: dict[str, pl.DataFrame]) -> pl.DataFrame:
    """讀前面階段的結果（資料在記憶體裡時）。"""
    results = {n: split_parts(frames[n], with_stage=False) for n in names}
    return concat(stage_parts(names, results)).collect(engine="streaming")


def scan_stages(
    recipe: Recipe,
    base_dir: Path,
    progress: Progress = None,
    tracker: StageTracker | None = None,
) -> dict[str, tuple[list[Part], list[SourceFile]]]:
    """掃描每個讀實際檔的階段（還不讀資料內容）。"""
    out = {}
    for k, stage in enumerate(recipe.stages):
        source = stage.input
        if source.reader == "stages":
            continue
        if tracker is not None:
            tracker.reading(k)
        parts, files = scan_parts(
            source.files, source.reader, source.encoding, base_dir, progress, source.sheet
        )
        out[stage.name] = (parts, [replace(f, stage=stage.name) for f in files])
    return out


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
    """依序執行每個階段。``stream`` 模式不回傳 DataFrame（結果直接寫到檔案）。"""
    if write_output and output is None:
        raise ValueError("寫出檔案時要指定 output（recipe 不記輸出位置）")
    tracker = StageTracker([s.name for s in recipe.stages], progress)
    try:
        return _execute(recipe, base_dir, write_output, output, mode, limit, progress, tracker)
    except BaseException:
        tracker.fail()
        raise


def _execute(
    recipe: Recipe,
    base_dir: Path,
    write_output: bool,
    output: Path | None,
    mode: Mode,
    limit: int,
    progress: Progress,
    tracker: StageTracker,
) -> tuple[pl.DataFrame | None, RunReport]:
    scanned = scan_stages(recipe, base_dir, progress, tracker)
    files = [f for _, found in scanned.values() for f in found]
    input_rows = sum(f.rows for f in files)
    mode = choose_mode(files, write_output, mode)
    if mode == "stream":
        if output is None:
            raise ValueError("stream 模式一定會寫出檔案")
        report = _execute_stream(
            recipe, scanned, files, input_rows, output, limit, progress, tracker
        )
        return None, _with_manifest(recipe, report, output)

    results: dict[str, pl.DataFrame] = {}
    reports, stages, sample_rows = [], [], 0
    for k, stage in enumerate(recipe.stages):
        tracker.start(k)
        if stage.name in scanned:
            parts, found = scanned[stage.name]
            frame = (
                concat(parts).collect(engine="streaming")
                if mode == "eager"
                else sample(parts, found, stage.input.reader, SAMPLE_HEAD, SAMPLE_TAIL)
            )
            sample_rows += frame.height
        else:
            frame = stage_frame(stage.input.stages, results)
        for step in stage.steps:
            if not step.enabled:
                reports.append(
                    StepReport(
                        step.id,
                        step.type_name(),
                        step.describe(),
                        False,
                        None,
                        None,
                        stage=stage.name,
                    )
                )
                continue
            frame, total, warnings = apply_step(step, frame, limit, results)
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
                    stage.name,
                )
            )
        results[stage.name] = frame
        stages.append(StageReport(stage.name, frame.height, _data_columns(frame.columns)))
        tracker.done(k, frame.height)
    frame = results[recipe.stages[-1].name]
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
        sample_rows if mode == "sample" else None,
        stages=stages,
    )
    if written is not None and output is not None:
        _with_manifest(recipe, report, output)
    return frame, report


def _with_manifest(recipe: Recipe, report: RunReport, output: Path) -> RunReport:
    """寫出處理紀錄（Recipe 的輸出設定可以關掉）。"""
    if recipe.output.manifest:
        report.manifest = write_manifest(recipe, report, output).as_posix()
    return report


def _strip_stage(name: str, stages: list[str]) -> str:
    """part 名稱去掉「@階段／」（下一個階段讀的時候換成新的階段）。"""
    for stage in stages:
        prefix = f"@{stage}／"
        if name.startswith(prefix):
            return name[len(prefix) :]
    return name


def _stream_steps(
    stage: Stage,
    parts: list[Part],
    tables: dict[str, pl.LazyFrame],
    limit: int,
    progress: Progress,
    report: Any,
) -> tuple[list[Part], dict[str, tuple[int, list[RowWarning]]], list[Step], Step | None]:
    """一個階段的步驟接成串流查詢。回傳（parts、已經先算好的警告、要從結果算警告的步驟、最後一步）。

    彙總類的步驟與合併來源檔之前，先算好之前步驟的警告（之後列的意義或行號變了）；
    對輸入檢查的警告（結合沒對到的列）在套用那一步之前算。每項檢查另外串流掃一次資料。
    """
    found: dict[str, tuple[int, list[RowWarning]]] = {}
    pending: list[Step] = []
    last: Step | None = None
    for step in stage.enabled_steps():
        bound = step.bind(tables)
        if (step.reshapes or step.combines) and pending:
            report("檢查警告")
            before = concat_parts(parts, last)
            for done in pending:
                found[done.id] = _warnings(done, before, limit)
            pending = []
        if _has_input_checks(bound):
            report("檢查警告")
            found[f"{step.id}:input"] = _warnings(
                bound, concat_parts(parts, last), limit, on_input=True
            )
        report(f"準備 {step.id}")
        # 每個檔各自一條流水線；暫存欄位（__w_）一路保留到最後，才能取出警告
        parts = apply_parts(bound, parts, progress)
        pending.append(bound)
        last = step
    return parts, found, pending, last


def _execute_stream(
    recipe: Recipe,
    scanned: dict[str, tuple[list[Part], list[SourceFile]]],
    files: list[SourceFile],
    input_rows: int,
    target: Path,
    limit: int,
    progress: Progress,
    tracker: StageTracker,
) -> RunReport:
    def report(stage: str) -> None:
        if progress is not None:
            progress({"stage": stage, "file": "", "done": 0, "total": 0})

    staged = staging_dir() / f"run-{uuid.uuid4().hex}"
    results: dict[str, list[Part]] = {}
    tables: dict[str, pl.LazyFrame] = {}
    found: dict[str, tuple[int, list[RowWarning]]] = {}
    stage_reports: list[StageReport] = []
    multi = len(recipe.stages) > 1
    try:
        for k, stage in enumerate(recipe.stages):
            final = k == len(recipe.stages) - 1
            tracker.start(k)
            label = f"階段「{stage.name}」：執行（串流）" if multi else "執行（串流）"
            if stage.name in scanned:
                parts, sources = scanned[stage.name]
                # 每一列帶著在這個階段的輸入裡的位置，執行中才能回報進度（寫出前拿掉）
                parts = with_positions(parts, sources, stage.input.reader)
            else:
                parts = stage_parts(stage.input.stages, results)
                counts = [lf.select(pl.len()).collect(engine="streaming").item() for _, lf in parts]
                sources = [
                    SourceFile(name, 0, "", "stage", n)
                    for (name, _), n in zip(parts, counts, strict=True)
                ]
                parts = with_row_positions(parts, counts)
            parts, early, pending, last = _stream_steps(
                stage, parts, tables, limit, progress, report
            )
            found.update(early)
            if not final:
                # 記下每一列屬於第幾個 part，讀回來時依它分開（每個來源各自處理）
                parts = [
                    (n, lf.with_columns(pl.lit(i, dtype=pl.Int32).alias(PART)))
                    for i, (n, lf) in enumerate(parts)
                ]
            lf = concat_parts(parts, last)
            folder = staged / f"stage-{k}"
            stage_rows = sum(f.rows for f in sources)
            # 分成很多個檔寫（大約每 1% 一個），另一條執行緒看寫完了幾個來回報進度
            with Watcher(folder, sources, progress, label) as watcher:
                try:
                    lf.sink_parquet(
                        pl.PartitionBy(folder, max_rows_per_file=rows_per_file(stage_rows)),
                        engine="streaming",
                        mkdir=True,
                    )
                except pl.exceptions.PolarsError as err:
                    raise RuntimeError(f"執行失敗：{type(err).__name__}: {err}") from None
            watcher.poll(finished=True)
            if progress is not None:
                progress(
                    {
                        "stage": label,
                        "file": "",
                        "done": stage_rows,
                        "total": stage_rows,
                        "unit": "rows",
                    }
                )
            # 檔名是補零的流水號（00000000.parquet…），排序就是寫出的順序
            result = pl.scan_parquet(sorted(folder.glob("*.parquet")))
            for step in pending:
                found[step.id] = _warnings(step, result, limit)
            names = result.collect_schema().names()
            clean = [c for c in names if not c.startswith(TEMP_PREFIX)]
            if final:
                break
            kept = result.select(clean)
            kept_rows = kept.select(pl.len()).collect(engine="streaming").item()
            tracker.done(k, kept_rows)
            results[stage.name] = [
                (
                    _strip_stage(n, stage.input.stages),
                    result.filter(pl.col(PART) == i).select(clean),
                )
                for i, (n, _) in enumerate(parts)
            ]
            tables[stage.name] = kept
            stage_reports.append(StageReport(stage.name, kept_rows, _data_columns(clean)))

        reports = []
        for stage in recipe.stages:
            for step in stage.steps:
                total, warnings = found.get(step.id, (0, []))
                more_total, more = found.get(f"{step.id}:input", (0, []))
                reports.append(
                    StepReport(
                        step.id,
                        step.type_name(),
                        step.describe(),
                        step.enabled,
                        None,
                        None,
                        more_total + total if step.enabled else 0,
                        more + warnings if step.enabled else [],
                        stage.name,
                    )
                )
        keep = clean
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
        tracker.done(len(recipe.stages) - 1, rows)
    finally:
        shutil.rmtree(staged, ignore_errors=True)
    columns = _data_columns(keep)
    stage_reports.append(StageReport(recipe.stages[-1].name, rows, columns))
    return RunReport(
        files,
        input_rows,
        reports,
        rows,
        columns,
        target.as_posix(),
        "stream",
        stages=stage_reports,
    )
