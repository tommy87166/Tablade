"""編輯中的 recipe：狀態由 Python 管理，前端只送指令、顯示畫面狀態（DESIGN.md §16.4）。

- 資料很大時（超過 ``SAMPLE_THRESHOLD`` 行），預覽只用每個來源檔的前幾行與最後幾行，
  匯出時才串流處理全部資料（DESIGN.md §8）。

- Recipe 由好幾個階段組成：畫面上一次編輯一個階段（``current``），``input`` / ``steps`` / ``selected``
  都是目前這個階段的。讀前面階段的輸出、結合的右表，都用前面階段算好的結果。
- 步驟以原始 dict 保存：參數還不完整（剛新增、正在填）的步驟也能留在清單裡，顯示錯誤，
  預覽算到它的前一步為止，跟 Power Query 的步驟錯誤一樣。
- 每一步的結果依「輸入＋到這一步為止的步驟＋用到的其他階段」的內容快取；改了第 k 步，k 之前的結果直接沿用。
- 每個會改變 recipe 的操作都先存一份快照，支援 undo / redo。
- 不碰檔案對話框（那是 api.py 的事），所以可以直接用 pytest 測。
"""

from __future__ import annotations

import contextlib
import copy
import hashlib
import json
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import polars as pl
from pydantic import ValidationError

from . import __version__
from .dtypes import type_label
from .engine.check import check_recipe, format_issues, format_report
from .engine.run import (
    PREPARE,
    SAMPLE_HEAD,
    SAMPLE_TAIL,
    SAMPLE_THRESHOLD,
    TRIAL,
    RowWarning,
    RunError,
    StageTracker,
    apply_step,
    execute,
    stage_frame,
)
from .export.outputs import OutputError, default_output_name, log_dir, plan_outputs, resolve_outputs
from .i18n import LANGS, plural, set_lang, t
from .ingest.encoding import DecodeError
from .ingest.excel import ExcelError, is_excel, sheet_names
from .ingest.sources import (
    InputError,
    Progress,
    SourceFile,
    check_entries,
    concat,
    expand,
    release_workers,
    resolve,
    sample,
    scan_parts,
    staging_dir,
)
from .recipe.schema import (
    DEFAULT_STAGE,
    RECIPE_VERSION,
    Recipe,
    RecipeError,
    load_json,
    parse_recipe,
    pydantic_issues,
)
from .recipe.validator import stage_ref_fields
from .steps import all_steps, get
from .steps.base import (
    SHEET,
    SOURCE_FILE,
    STAGE,
    SYSTEM_PREFIX,
    Step,
    data_values,
    source_name,
    too_many_values,
)

MAX_UNDO = 100
DEFAULT_INPUT = {"files": [], "reader": "text_lines", "encoding": "auto", "sheet": "", "stages": []}
DEFAULT_OUTPUT = {"format": "parquet", "keep_system_columns": True, "manifest": True}
STAGE_NAME_MAX = 40


def old_format() -> str:
    return t(
        "這是舊的 Recipe 寫法（最外層的 inputs / steps），請改成 stages",
        "This Recipe uses the old format (top-level inputs / steps); change it to stages",
    )


class SessionError(ValueError):
    pass


class TrialError(SessionError):
    """匯出前的試跑出錯：指到哪個階段的第幾步（畫面上「前往這一步」）。"""

    def __init__(self, message: str, stage: int | None, step: int | None) -> None:
        super().__init__(message)
        self.stage, self.step = stage, step


def load_hint() -> str:
    return t("請先按工具列的「載入資料及預覽」", "Click “Load & preview” on the toolbar first")


TRIAL_HEAD = 2_000
"""匯出前試跑時每個檔取前幾行（只為了先找出步驟的錯誤，不用多）。"""


@dataclass
class _Computed:
    key: str
    frame: pl.DataFrame | None = None
    warning_count: int = 0
    warnings: list[RowWarning] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    run_error: str | None = None


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


def _entry_path(entry: str, base_dir: Path) -> Path:
    return Path(entry) if Path(entry).is_absolute() else base_dir / entry


def _entry_for(path: Path, base_dir: Path) -> str:
    """檔案清單裡怎麼寫這個檔：在 base_dir 裡（含子資料夾）用相對路徑，否則用絕對路徑。"""
    real, base = path.resolve(), base_dir.resolve()
    return real.relative_to(base).as_posix() if real.is_relative_to(base) else real.as_posix()


def _data_columns(frame: pl.DataFrame) -> list[str]:
    return [c for c in frame.columns if not c.startswith(SYSTEM_PREFIX)]


def _files(frame: pl.DataFrame) -> list[str]:
    """目前的來源（依出現順序）：檔名；讀 Excel 全部工作表時是「檔名［工作表］」。"""
    if SOURCE_FILE not in frame.columns:
        return []
    return (
        frame.select(source_name(frame.columns)).to_series().unique(maintain_order=True).to_list()
    )


def _file_count(frame: pl.DataFrame) -> int:
    if SOURCE_FILE not in frame.columns:
        return 0
    return frame.select(source_name(frame.columns)).to_series().n_unique()


def _new_stage(name: str) -> dict[str, Any]:
    """畫面上的一個階段：名稱、讀取資料、步驟，加上選取的步驟（不寫進 Recipe）。"""
    return {
        "name": name,
        "input": copy.deepcopy(DEFAULT_INPUT),
        "steps": [],
        "output_file": "",
        "selected": -1,
    }


def _ref_fields() -> dict[str, list[str]]:
    """每種步驟引用其他階段的參數（結合的 right）。"""
    from .recipe.spec import build_spec

    return stage_ref_fields(build_spec())


_REF_FIELDS: dict[str, list[str]] | None = None


def ref_fields() -> dict[str, list[str]]:
    global _REF_FIELDS
    if _REF_FIELDS is None:
        _REF_FIELDS = _ref_fields()
    return _REF_FIELDS


def stage_refs(stage: dict[str, Any]) -> list[tuple[str, str]]:
    """這個階段用到哪些其他階段：（階段名稱, 用在哪裡）。"""
    out = []
    source = stage["input"]
    if source.get("reader") == "stages":
        out += [
            (n, t("讀取資料", "“Read data”"))
            for n in source.get("stages") or []
            if isinstance(n, str)
        ]
    for step in stage["steps"]:
        if step.get("enabled", True) is False:
            continue
        for name in ref_fields().get(step.get("type"), []):
            ref = step.get(name)
            if isinstance(ref, str):
                out.append(
                    (
                        ref,
                        t(
                            f"{step.get('id')}（{_label(step.get('type'))}）",
                            f"step {step.get('id')} ({_label(step.get('type'))})",
                        ),
                    )
                )
    return out


def data_value_fields(type_name: Any) -> dict[str, str]:
    """這種步驟「從資料讀出」的參數（schema 標了 ``ui: data_values``）：{參數: 從哪個參數指定的欄讀}。"""
    try:
        props = get(type_name).model_json_schema().get("properties", {})
    except KeyError:
        return {}
    return {
        name: prop["ui_from"]
        for name, prop in props.items()
        if prop.get("ui") == "data_values" and prop.get("ui_from")
    }


def _label(type_name: Any) -> str:
    try:
        return get(type_name).label
    except KeyError:
        return str(type_name)


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


EXAMPLE_ROWS = 1_000
"""型別分頁的範例值從前這麼多列找。"""


def column_summary(frame: pl.DataFrame | pl.LazyFrame) -> list[dict[str, Any]]:
    """每一欄的型別、空值、空白字串、最小～最大與範例值；位置是第幾個資料欄（和刪除欄的位置相同）。

    也可以是 LazyFrame（完整模式讀匯出檔）：統計一次串流查詢算完，範例值只讀前幾列，不整份讀進記憶體。
    """
    lazy = frame.lazy()
    schema = lazy.collect_schema()
    exprs = []
    for i, (name, dtype) in enumerate(schema.items()):
        col = pl.col(name)
        exprs.append(col.null_count().alias(f"n{i}"))
        if dtype == pl.String:
            exprs.append((col.str.strip_chars() == "").sum().alias(f"b{i}"))
        if dtype.is_numeric() or dtype.is_temporal():
            exprs += [col.min().alias(f"lo{i}"), col.max().alias(f"hi{i}")]
    stats = lazy.select(exprs).collect(engine="streaming").row(0, named=True) if exprs else {}
    head = lazy.head(EXAMPLE_ROWS).collect()
    out, position = [], 0
    for i, (name, dtype) in enumerate(schema.items()):
        system = name.startswith(SYSTEM_PREFIX)
        if not system:
            position += 1
        ranged = dtype.is_numeric() or dtype.is_temporal()
        examples = head.get_column(name).drop_nulls().unique(maintain_order=True).head(3)
        out.append(
            {
                "position": None if system else position,
                "name": name,
                "system": system,
                "dtype": f"Decimal({dtype.precision},{dtype.scale})"
                if isinstance(dtype, pl.Decimal)
                else str(dtype),
                "label": type_label(dtype),
                "nulls": int(stats[f"n{i}"]),
                "blanks": int(stats[f"b{i}"] or 0) if dtype == pl.String else None,
                "min": _jsonable(stats[f"lo{i}"]) if ranged else None,
                "max": _jsonable(stats[f"hi{i}"]) if ranged else None,
                "examples": [_jsonable(v) for v in examples.to_list()],
            }
        )
    return out


class Session:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.recipe_path: Path | None = None
        self.base_dir: Path | None = None
        self.meta: dict[str, str] = {"name": "", "description": ""}
        self.output: dict[str, Any] = dict(DEFAULT_OUTPUT)
        self.stages: list[dict[str, Any]] = [_new_stage(DEFAULT_STAGE)]
        self.current = 0
        """畫面上正在編輯的階段。"""
        self.dirty = False
        self.issues: list[dict[str, Any]] = []
        self._undo: list[dict[str, Any]] = []
        self._redo: list[dict[str, Any]] = []
        self._sheets: dict[tuple[str, int, int], list[str]] = {}
        self._sources: dict[str, tuple[str, pl.DataFrame, list[SourceFile]]] = {}
        """每個讀實際檔的階段按「載入資料及預覽」讀進來的資料（設定指紋、資料、來源檔）；不會自動讀檔。"""
        self._stale: set[str] = set()
        """載入之後檔案清單、讀取方式、編碼或檔案內容變了的階段：要重新按「載入資料及預覽」。"""
        self.progress: Progress = None
        """讀檔、轉碼、匯出時的進度回報（api.py 會把它推給前端）。"""
        self.lang = "zh"
        """畫面的語言（zh、en）：前端啟動時送作業系統的語言，切換時再送一次（docs/i18n-plan.md）。"""
        self._computed: dict[str, list[_Computed]] = {}
        self._inputs: dict[str, tuple[str, pl.DataFrame]] = {}
        """讀前面階段輸出的階段：接好的輸入（依前面階段結果的指紋快取）。"""
        self._full: dict[str, tuple[str, pl.DataFrame]] = {}
        """「完整跑一次」的結果：{階段: (樣本結果的指紋, 完整的結果)}；樣本結果變了就不用。"""
        self._exports: dict[str, dict[str, Any]] = {}
        """「處理並匯出資料」寫出的檔（完整模式）：{階段: {key, path, rows, input_rows, time, warnings…}}。
        這個階段和它往上用到的階段的設定或檔案變了就不用（回到抽樣模式）；只記在這次 App 開著的期間。"""
        self.last_export: Path | None = None
        """上次「處理並匯出資料」最後一個階段寫出的檔。"""

    # ── 目前的階段 ─────────────────────────────────────────────

    @property
    def stage(self) -> dict[str, Any]:
        return self.stages[self.current]

    @property
    def input(self) -> dict[str, Any]:
        return self.stage["input"]

    @property
    def steps(self) -> list[dict[str, Any]]:
        return self.stage["steps"]

    @property
    def selected(self) -> int:
        return self.stage["selected"]

    @selected.setter
    def selected(self, value: int) -> None:
        self.stage["selected"] = value

    # ── 快照與 undo / redo ─────────────────────────────────────

    def _snapshot(self) -> dict[str, Any]:
        return copy.deepcopy(
            {
                "meta": self.meta,
                "output": self.output,
                "stages": self.stages,
                "current": self.current,
            }
        )

    def _restore(self, snap: dict[str, Any]) -> None:
        snap = copy.deepcopy(snap)
        self.meta, self.output = snap["meta"], snap["output"]
        self.stages, self.current = snap["stages"], snap["current"]

    def _change(self) -> None:
        """在修改 recipe 之前呼叫。"""
        self._undo.append(self._snapshot())
        del self._undo[:-MAX_UNDO]
        self._redo.clear()
        self.dirty = True

    def undo(self) -> None:
        with self._lock:
            if self._undo:
                self._redo.append(self._snapshot())
                self._restore(self._undo.pop())
                self.dirty = True

    def redo(self) -> None:
        with self._lock:
            if self._redo:
                self._undo.append(self._snapshot())
                self._restore(self._redo.pop())
                self.dirty = True

    # ── recipe 的組成與載入 ────────────────────────────────────

    @staticmethod
    def _stage_dict(stage: dict[str, Any]) -> dict[str, Any]:
        source = dict(stage["input"])
        if source.get("reader") == "stages":
            source = {"reader": "stages", "stages": list(source.get("stages") or [])}
        else:
            source.pop("stages", None)
            if source.get("reader") != "excel" and not source.get("sheet"):
                source.pop("sheet", None)  # 不是 Excel 時不寫工作表，Recipe 比較好讀
        out = {"name": stage["name"], "input": source, "steps": stage["steps"]}
        if stage.get("output_file"):
            out["output_file"] = stage["output_file"]  # 沒填就不寫，Recipe 比較好讀
        return out

    def recipe_dict(self) -> dict[str, Any]:
        return {
            "tablade_recipe": RECIPE_VERSION,
            "name": self.meta.get("name", ""),
            "description": self.meta.get("description", ""),
            "stages": [self._stage_dict(st) for st in self.stages],
            "output": self.output,
        }

    def _reset(self) -> None:
        """回到空白狀態（保留進度回報的接線與畫面的語言）。"""
        progress, lang = self.progress, self.lang
        self.__init__()
        self.progress, self.lang = progress, lang

    def set_language(self, lang: str) -> None:
        """切換畫面的語言（zh、en）；不寫任何設定檔，下次開啟又跟作業系統的語言。

        Python 產生的文字（狀態說明、錯誤、警告…）跟著換：算好的每一步結果清掉，用新的語言重算
        （載入的資料留著，不用重新讀檔）。匯出時記下的警告維持匯出當時的語言。
        """
        if lang not in LANGS:
            raise SessionError(
                t(
                    f"不支援的語言：{lang}（可用：{'、'.join(LANGS)}）",
                    f"Unsupported language: {lang} (available: {', '.join(LANGS)})",
                )
            )
        with self._lock:
            changed = lang != self.lang
            self.lang = lang
            set_lang(lang)
            if changed:
                self._computed.clear()

    def new(self, path: Path) -> Path:
        """新增 Recipe：先決定存放位置（檔名就是名稱），立刻寫出空的 Recipe 檔。"""
        with self._lock:
            self._reset()
            target = path.resolve()
            self.recipe_path, self.base_dir = target, target.parent
            self.meta["name"] = target.name.removesuffix(".json").removesuffix(".recipe")
            return self.save_recipe(target)

    def close(self) -> None:
        """回到沒有開著 Recipe 的狀態。"""
        with self._lock:
            self._reset()

    def load_recipe(self, path: Path) -> list[dict[str, Any]]:
        """開啟 recipe 檔；回傳驗證問題（有問題仍會載入，讓使用者在 UI 修正）。"""
        with self._lock:
            path = path.resolve()
            data = load_json(path)  # JSON 本身壞掉時丟出 RecipeError
            if "stages" not in data and any(k in data for k in ("inputs", "steps")):
                raise RecipeError(
                    [{"path": "(Recipe)", "error": old_format(), "severity": "error"}]
                )
            result = check_recipe(data, path.parent)
            if any(i["path"] == "tablade_recipe" for i in result.issues):
                raise RecipeError([i for i in result.issues if i["path"] == "tablade_recipe"])
            raw = data.get("stages") if isinstance(data.get("stages"), list) else []
            stages: list[dict[str, Any]] = []
            for item in raw:
                if not isinstance(item, dict):
                    continue
                name = item.get("name")
                if not isinstance(name, str) or not name.strip():
                    name = f"階段 {len(stages) + 1}"  # i18n-ignore：階段名稱會寫進 Recipe（資料）
                stage = _new_stage(name)
                source = item.get("input") if isinstance(item.get("input"), dict) else {}
                stage["input"].update(copy.deepcopy(source))
                stage["input"].pop("id", None)
                for key in ("files", "stages"):
                    values = stage["input"].get(key)
                    values = values if isinstance(values, list) else []
                    stage["input"][key] = [v for v in values if isinstance(v, str) and v]
                steps = item.get("steps") if isinstance(item.get("steps"), list) else []
                stage["steps"] = [s for s in steps if isinstance(s, dict)]
                file = item.get("output_file")
                stage["output_file"] = file.strip() if isinstance(file, str) else ""
                stage["selected"] = len(stage["steps"]) - 1
                stages.append(stage)
            self._reset()
            self.recipe_path, self.base_dir = path, path.parent
            self.meta = {
                "name": str(data.get("name", "")),
                "description": str(data.get("description", "")),
            }
            self.stages = stages or [_new_stage(DEFAULT_STAGE)]
            self.current = len(self.stages) - 1
            self.output = {
                **DEFAULT_OUTPUT,
                **(data.get("output") if isinstance(data.get("output"), dict) else {}),
            }
            self.issues = result.issues
            return result.issues

    def save_recipe(self, path: Path | None = None) -> Path:
        """存成 recipe 檔；換位置時，檔案清單依新位置重新決定存相對或絕對路徑。"""
        with self._lock:
            target = path or self.recipe_path
            if target is None:
                raise SessionError(
                    t("還沒有指定 Recipe 檔的位置", "No location set for the Recipe file yet")
                )
            target = target.resolve()
            if self.base_dir and target.parent != self.base_dir:
                for stage in self.stages:
                    stage["input"]["files"] = [
                        _entry_for(_entry_path(e, self.base_dir), target.parent)
                        for e in stage["input"]["files"]
                    ]
            self.base_dir = target.parent
            target.write_text(
                json.dumps(self.recipe_dict(), ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            self.recipe_path = target
            self.dirty = False
            return target

    # ── 階段 ──────────────────────────────────────────────────

    def _stage_name(self, name: Any, exclude: int | None = None) -> str:
        text = str(name).strip()
        if not text:
            raise SessionError(t("請輸入階段名稱", "Enter a stage name"))
        if len(text) > STAGE_NAME_MAX:
            raise SessionError(
                t(
                    f"階段名稱最多 {STAGE_NAME_MAX} 個字",
                    f"Stage names can be at most {STAGE_NAME_MAX} characters",
                )
            )
        for i, stage in enumerate(self.stages):
            if i != exclude and stage["name"] == text:
                raise SessionError(
                    t(f"已經有叫「{text}」的階段", f'A stage named "{text}" already exists')
                )
        return text

    def _check_stage(self, index: int) -> None:
        if not 0 <= index < len(self.stages):
            raise SessionError(t(f"沒有第 {index + 1} 個階段", f"There is no stage {index + 1}"))

    def add_stage(self, name: str, before: bool = True) -> int:
        """新增階段：預設插在目前階段的前面（新增的通常是給目前階段用的對照表）。"""
        with self._lock:
            text = self._stage_name(name)
            self._change()
            position = self.current if before else self.current + 1
            self.stages.insert(position, _new_stage(text))
            self.current = position
            return position

    def rename_stage(self, index: int, name: str) -> None:
        """改階段名稱：所有引用它的地方（讀取資料、結合的右表）一併改掉。"""
        with self._lock:
            self._check_stage(index)
            new = self._stage_name(name, exclude=index)
            old = self.stages[index]["name"]
            if new == old:
                return
            self._change()
            self.stages[index]["name"] = new
            for stage in self.stages:
                source = stage["input"]
                source["stages"] = [new if n == old else n for n in source.get("stages") or []]
                for step in stage["steps"]:
                    for field_name in ref_fields().get(step.get("type"), []):
                        if step.get(field_name) == old:
                            step[field_name] = new
            for cache in (self._sources, self._computed, self._inputs, self._full):
                if old in cache:
                    cache[new] = cache.pop(old)  # type: ignore[attr-defined]
            if old in self._stale:
                self._stale.discard(old)
                self._stale.add(new)

    def _order_problems(self, stages: list[dict[str, Any]]) -> list[str]:
        """每個階段只能引用前面的階段：回傳違反的地方。"""
        problems, earlier = [], set()
        names = {st["name"] for st in stages}
        for stage in stages:
            for ref, where in stage_refs(stage):
                if ref in names and ref not in earlier:
                    problems.append(
                        t(
                            f"「{stage['name']}」的{where}要用「{ref}」",
                            f'"{stage["name"]}" uses "{ref}" in {where}',
                        )
                    )
            earlier.add(stage["name"])
        return problems

    def move_stage(self, index: int, to: int) -> None:
        with self._lock:
            self._check_stage(index)
            to = max(0, min(to, len(self.stages) - 1))
            if to == index:
                return
            trial = list(self.stages)
            trial.insert(to, trial.pop(index))
            problems = self._order_problems(trial)
            if problems:
                raise SessionError(
                    t(
                        f"不能這樣排：{'；'.join(problems)}，只能用前面的階段（先調整這些引用）",
                        f"Can't reorder: {'; '.join(problems)}. A stage can only use earlier stages (update these references first)",
                    )
                )
            self._change()
            self.stages = trial
            self.current = to

    def delete_stage(self, index: int) -> None:
        with self._lock:
            self._check_stage(index)
            if len(self.stages) == 1:
                raise SessionError(t("至少要有一個階段", "There must be at least one stage"))
            name = self.stages[index]["name"]
            users = [
                t(f"「{stage['name']}」的{where}", f'{where} in "{stage["name"]}"')
                for stage in self.stages
                for ref, where in stage_refs(stage)
                if ref == name and stage is not self.stages[index]
            ]
            if users:
                raise SessionError(
                    t(
                        f"不能刪除「{name}」：{'、'.join(users)}用到它，請先改掉這些引用",
                        f'Cannot delete "{name}": it is used by {", ".join(users)}. Remove these references first',
                    )
                )
            self._change()
            del self.stages[index]
            if self.current > index or self.current >= len(self.stages):
                self.current = max(0, self.current - 1)

    def select_stage(self, index: int) -> None:
        with self._lock:
            self._check_stage(index)
            self.current = index

    # ── 編輯操作 ──────────────────────────────────────────────

    def _require_recipe(self) -> Path:
        if self.base_dir is None:
            raise SessionError(t("先新增或開啟 Recipe", "Create or open a Recipe first"))
        return self.base_dir

    def add_files(self, paths: list[Path]) -> None:
        """「瀏覽檔案」選的檔加進檔案清單：Recipe 資料夾裡（含子資料夾）存相對路徑，其他存絕對路徑。"""
        with self._lock:
            base = self._require_recipe()
            entries = [_entry_for(p, base) for p in paths]
            new = [e for e in dict.fromkeys(entries) if e not in self.input["files"]]
            if not new:
                return
            self._change()
            self.input["files"] += new

    def add_file_entry(self, text: str) -> None:
        """手動輸入的路徑（相對於 Recipe 資料夾或絕對路徑，可含 *）。"""
        with self._lock:
            self._require_recipe()
            entry = text.strip().replace("\\", "/")
            if not entry:
                raise SessionError(t("請輸入檔案路徑", "Enter a file path"))
            if entry in self.input["files"]:
                return
            self._change()
            self.input["files"].append(entry)

    def remove_file(self, index: int) -> None:
        with self._lock:
            if not 0 <= index < len(self.input["files"]):
                raise SessionError(
                    t(
                        f"檔案清單沒有第 {index + 1} 項",
                        f"There is no item {index + 1} in the file list",
                    )
                )
            self._change()
            del self.input["files"][index]

    def set_input(self, values: dict[str, Any]) -> None:
        """讀取方式、編碼、工作表；讀前面階段的輸出時，要讀的階段（只能選前面的）。"""
        with self._lock:
            if "stages" in values:
                earlier = [st["name"] for st in self.stages[: self.current]]
                picked = [n for n in values["stages"] if isinstance(n, str)]
                bad = [n for n in picked if n not in earlier]
                if bad:
                    raise SessionError(
                        t(
                            f"只能讀前面的階段：{'、'.join(bad)}",
                            f"Only earlier stages can be read: {', '.join(bad)}",
                        )
                    )
            if values.get("reader") == "stages" and self.current == 0:
                raise SessionError(
                    t(
                        "第一個階段只能讀實際檔（前面沒有階段）",
                        "The first stage can only read files (there are no earlier stages)",
                    )
                )
            self._change()
            for key in ("reader", "encoding", "sheet"):
                if key in values:
                    self.input[key] = values[key]
            if "stages" in values:
                self.input["stages"] = list(dict.fromkeys(picked))

    def set_output(self, values: dict[str, Any]) -> None:
        with self._lock:
            self._change()
            for key in ("keep_system_columns", "manifest"):
                if key in values:
                    self.output[key] = bool(values[key])

    def set_stage_output(self, index: int, file: str) -> None:
        """改第 index 個階段的輸出檔名（只填檔名；沒有 .parquet 時補上，空白＝中間階段不寫檔）。"""
        with self._lock:
            self._check_stage(index)
            name = str(file).strip()
            if name and not name.lower().endswith(".parquet"):
                name += ".parquet"
            if name == self.stages[index].get("output_file", ""):
                return
            self._change()
            self.stages[index]["output_file"] = name

    def _default_output(self) -> str:
        return default_output_name(self.recipe_path, self.meta.get("name", ""))

    def _outputs(self) -> list[Any]:
        """每個階段要寫的檔（``export/outputs.py`` 的 ``plan_outputs``）。"""
        base = self.base_dir or Path.cwd()
        return plan_outputs(self.recipe_dict(), base, self._default_output())

    def set_meta(self, values: dict[str, Any]) -> None:
        with self._lock:
            self._change()
            for key in ("name", "description"):
                if key in values:
                    self.meta[key] = str(values[key])

    def _new_id(self) -> str:
        """步驟 id 在整份 Recipe 裡不重複。"""
        used = {s.get("id") for st in self.stages for s in st["steps"]}
        n = sum(len(st["steps"]) for st in self.stages) + 1
        while f"s{n}" in used:
            n += 1
        return f"s{n}"

    def _earlier_columns(self) -> dict[str, list[str] | None]:
        """目前階段前面每個階段的結果有哪些資料欄（結合的建議值、右表的欄位選單）。"""
        out = {}
        for i in range(self.current):
            frame = self._stage_result(i)[0]
            out[self.stages[i]["name"]] = _data_columns(frame) if frame is not None else None
        return out

    def add_step(
        self, type_name: str, context: dict[str, Any] | None = None, index: int | None = None
    ) -> int:
        """在 index（預設：目前選取的步驟之後）插入新步驟，參數依目前的資料給建議值。"""
        with self._lock:
            cls = get(type_name)
            position = self.selected + 1 if index is None else index
            position = max(0, min(position, len(self.steps)))
            before = self._frame_before(position)
            frame = before if before is not None else pl.DataFrame()
            params = cls.suggest(frame, {**(context or {}), "stages": self._earlier_columns()})
            self._change()
            self.steps.insert(
                position, self._normalize({"id": self._new_id(), "type": type_name, **params})
            )
            self.selected = position
            return position

    def update_step(self, index: int, values: dict[str, Any]) -> None:
        with self._lock:
            self._check_index(index)
            old = self.steps[index]
            step = {"id": old.get("id"), "type": old.get("type")}
            step.update({k: v for k, v in values.items() if k not in ("id", "type")})
            # 「從資料讀出」的值：依據的欄改了、值本身沒動時，自動從資料重新讀（讀不到就保留原本的）
            for field_name, source in data_value_fields(step.get("type")).items():
                changed = step.get(source) != old.get(source)
                if changed and step.get(field_name) == old.get(field_name):
                    with contextlib.suppress(SessionError):
                        step[field_name] = self._read_values(index, step.get(source))
            self._change()
            self.steps[index] = self._normalize(step)

    def _read_values(self, index: int, column: Any) -> list[str]:
        """第 index 步的輸入裡，某一欄有哪些不同的值（「從資料讀出」用；大檔時是預覽的樣本）。"""
        frame = self._frame_before(index)
        if frame is None:
            raise SessionError(t(f"還沒有資料：{load_hint()}", f"No data yet. {load_hint()}"))
        if not isinstance(column, str) or column not in frame.columns:
            raise SessionError(
                t(f"欄位「{column}」不存在", f'Column "{column}" does not exist')
                if column
                else t("先選要從哪一欄讀", "Choose a column to read from first")
            )
        found = data_values(frame, column)
        if not found:
            raise SessionError(t(f"「{column}」沒有值", f'"{column}" has no values'))
        problem = too_many_values(column, len(found))
        if problem:
            raise SessionError(problem)
        return found

    def _value_errors(self, index: int, raw: dict[str, Any], errors: list[dict]) -> list[dict]:
        """「從資料讀出」的值是空的時，說明為什麼（值太多、還沒有資料、沒有這一欄），取代「至少要有一項」。"""
        notes = {}
        for field_name, source in data_value_fields(raw.get("type")).items():
            if raw.get(field_name):
                continue
            try:
                found = self._read_values(index, raw.get(source))
            except SessionError as err:
                notes[field_name] = str(err)
            else:
                notes[field_name] = t(
                    f"按「重新從資料讀取」帶入 {len(found)} 個值",
                    f"Click “Reload from data” to fill in {plural(len(found), 'value', 'values')}",
                )
        if not notes:
            return errors
        kept = [e for e in errors if e.get("path") not in notes]
        return kept + [{"path": f, "error": m, "severity": "error"} for f, m in notes.items()]

    def refresh_values(
        self, index: int, field_name: str, values: dict[str, Any] | None = None
    ) -> list[str]:
        """「重新從資料讀取」：依目前的參數（``values`` 是表單上還沒送出的版本）重新讀出值。"""
        with self._lock:
            self._check_index(index)
            old = self.steps[index]
            step = dict(old) if values is None else {"id": old.get("id"), "type": old.get("type")}
            if values is not None:
                step.update({k: v for k, v in values.items() if k not in ("id", "type")})
            source = data_value_fields(step.get("type")).get(field_name)
            if source is None:
                raise SessionError(
                    t(
                        f"「{field_name}」不是從資料讀出的參數",
                        f'"{field_name}" is not a parameter read from data',
                    )
                )
            found = self._read_values(index, step.get(source))
            self._change()
            step[field_name] = found
            self.steps[index] = self._normalize(step)
            return found

    def _normalize(self, step: dict[str, Any]) -> dict[str, Any]:
        """參數合法時去掉跟預設值相同的欄位，recipe 比較好讀；不合法就原樣保留。"""
        try:
            model = get(step["type"]).model_validate(step)
        except (ValidationError, KeyError):
            return step
        dumped = model.model_dump(mode="json", exclude_defaults=True)
        return {
            "id": model.id,
            "type": model.type_name(),
            **{k: v for k, v in dumped.items() if k != "id"},
        }

    def move_step(self, index: int, to: int) -> None:
        with self._lock:
            self._check_index(index)
            to = max(0, min(to, len(self.steps) - 1))
            if to == index:
                return
            self._change()
            step = self.steps.pop(index)
            self.steps.insert(to, step)
            self.selected = to

    def toggle_step(self, index: int) -> None:
        with self._lock:
            self._check_index(index)
            self._change()
            step = self.steps[index]
            if step.get("enabled", True):
                step["enabled"] = False
            else:
                step.pop("enabled", None)

    def delete_step(self, index: int) -> None:
        with self._lock:
            self._check_index(index)
            self._change()
            del self.steps[index]
            if self.selected > index:
                self.selected -= 1  # 前面少一步：仍選同一步（或仍是「輸出資料」）
            elif self.selected == index:
                self.selected = min(index, len(self.steps) - 1)

    def select(self, index: int) -> None:
        """-1 是「讀取資料」，0..n-1 是步驟，n（步驟數）是「輸出資料」。"""
        with self._lock:
            self.selected = max(-1, min(index, len(self.steps)))

    def _check_index(self, index: int) -> None:
        if not 0 <= index < len(self.steps):
            raise SessionError(t(f"沒有第 {index} 步", f"There is no step {index}"))

    # ── 載入資料 ─────────────────────────────────────────────

    def _sheet_names(self) -> list[str]:
        """讀取方式是 Excel 時，第一個 Excel 檔有哪些工作表（給「工作表」選單；依檔案快取）。"""
        if self.input.get("reader") != "excel" or not self.base_dir:
            return []
        for entry in self.input["files"]:
            for path in expand(entry, self.base_dir):
                if not is_excel(path):
                    continue
                try:
                    st = path.stat()
                    key = (str(path.resolve()), st.st_size, st.st_mtime_ns)
                    if key not in self._sheets:
                        self._sheets = {key: sheet_names(path)}
                    return self._sheets[key]
                except (OSError, ExcelError):
                    return []
        return []

    def _source_key(self, stage: dict[str, Any]) -> str:
        """讀實際檔的設定指紋：讀取方式、編碼，加上實際讀到的每個檔（位置、大小、修改時間）。

        用實際位置而不是清單上的寫法：另存到別的資料夾只改寫法，不需要重新載入。
        """
        source = stage["input"]
        files = []
        if self.base_dir:
            for entry in source["files"]:
                for p in expand(entry, self.base_dir):
                    try:
                        st = p.stat()
                        files.append((str(p.resolve()), st.st_size, st.st_mtime_ns))
                    except OSError:
                        continue
        reading = {k: source.get(k) for k in ("reader", "encoding", "sheet")}
        return _digest({"reading": reading, "files": files})

    @staticmethod
    def _reads_files(stage: dict[str, Any]) -> bool:
        return stage["input"].get("reader") != "stages"

    def load_data(self) -> None:
        """「載入資料及預覽」：依序讀每個讀實際檔的階段的檔案清單（資料大時讀樣本），並算出每個階段的結果。

        還沒加檔案的階段先跳過（至少要有一個階段有檔案）。清單上有找不到的檔就不讀。
        每個階段的狀態（處理中、讀到第幾個檔、完成、失敗）透過 progress 回報（畫面上的進度視窗）。
        """
        with self._lock:
            base = self._require_recipe()
            todo = [st for st in self.stages if self._reads_files(st) and st["input"]["files"]]
            if not todo:
                raise SessionError(t("還沒有加入資料檔", "No data files added yet"))
            multi = len(self.stages) > 1
            for stage in todo:
                missing = [
                    e["path"]
                    for e in check_entries(stage["input"]["files"], base)
                    if not e["found"]
                ]
                if missing:
                    where = (
                        t(f"（階段「{stage['name']}」）", f' (stage "{stage["name"]}")')
                        if multi
                        else ""
                    )
                    raise SessionError(
                        t(
                            f"找不到：{'、'.join(missing)}{where}，請先從檔案清單移除",
                            f"Not found: {', '.join(missing)}{where}. Remove {'it' if len(missing) == 1 else 'them'} from the file list first",
                        )
                    )
            tracker = StageTracker([st["name"] for st in self.stages], self.progress)
            try:
                for k, stage in enumerate(self.stages):
                    tracker.start(k)
                    if stage in todo:
                        self._load_stage(stage, base, multi, tracker)
                    self._finish_stage(k, tracker)
            except BaseException:
                tracker.fail()
                raise
            finally:
                release_workers()  # 平行轉碼的工作程序：跑完就關掉，不用時不佔記憶體

    def _load_stage(
        self, stage: dict[str, Any], base: Path, multi: bool, tracker: StageTracker
    ) -> None:
        """讀一個階段的檔（已經載入、檔案沒變就沿用）；進度視窗上這個階段寫出已處理幾個檔。"""
        name, source = stage["name"], stage["input"]
        key = self._source_key(stage)
        if name in self._sources and self._sources[name][0] == key:
            return
        self._sources.pop(name, None)
        self._stale.discard(name)
        try:
            total = len(resolve(source["files"], base))
            parts, files = scan_parts(
                source["files"],
                source["reader"],
                source["encoding"],
                base,
                self.progress,
                source.get("sheet", ""),
                on_file=lambda i, file: tracker.files(i, total, file),
            )
            tracker.files(total, total)
            if sum(f.rows for f in files) <= SAMPLE_THRESHOLD:
                frame = concat(parts).collect(engine="streaming")
            else:
                self._report(t("讀取預覽樣本", "Reading preview sample"))
                frame = sample(parts, files, source["reader"], SAMPLE_HEAD, SAMPLE_TAIL)
        except (InputError, DecodeError, LookupError, pl.exceptions.PolarsError) as err:
            where = t(f"階段「{name}」：", f'Stage "{name}": ') if multi else ""
            raise SessionError(f"{where}{err}") from None
        self._sources[name] = (key, frame, files)

    def _finish_stage(self, index: int, tracker: StageTracker) -> None:
        """算出第 index 個階段的結果，回報完成（列數）、有錯或還不能算。"""
        frame = self._stage_result(index)[0]
        computed = self._computed.get(self.stages[index]["name"], [])
        if any(c.errors or c.run_error for c in computed) or self._stage_input(index)[3]:
            tracker.error(index)
        elif frame is None:
            tracker.skip(index)
        else:
            tracker.done(index, frame.height)

    def _load_source(self, stage: dict[str, Any]) -> tuple[pl.DataFrame | None, list[SourceFile]]:
        """這個階段已載入的資料；設定或檔案變了就放掉（要重新按「載入資料及預覽」），不會自動讀檔。"""
        name = stage["name"]
        entry = self._sources.get(name)
        if entry is None:
            return None, []
        if entry[0] != self._source_key(stage):
            del self._sources[name]
            self._stale.add(name)
            return None, []
        return entry[1], entry[2]

    def _report(self, stage: str) -> None:
        if self.progress is not None:
            self.progress({"stage": stage, "file": "", "done": 0, "total": 0})

    # ── 計算 ─────────────────────────────────────────────────

    def _stage_index(self, name: str, before: int) -> int | None:
        for i, stage in enumerate(self.stages[:before]):
            if stage["name"] == name:
                return i
        return None

    def _stage_input(self, si: int) -> tuple[pl.DataFrame | None, str, bool, str | None]:
        """第 si 個階段的輸入：（資料、指紋、是不是樣本、錯誤）。資料是 None 代表還不能算（等載入或前面的階段）。"""
        stage = self.stages[si]
        if self._reads_files(stage):
            frame, files = self._load_source(stage)
            if frame is None:
                return None, "", False, None
            sampled = bool(files) and sum(f.rows for f in files) > SAMPLE_THRESHOLD
            return frame, self._sources[stage["name"]][0], sampled, None
        names = list(stage["input"].get("stages") or [])
        if not names:
            return None, "", False, t("要選至少一個前面的階段", "Choose at least one earlier stage")
        frames, keys, sampled = {}, [], False
        for name in names:
            index = self._stage_index(name, si)
            if index is None:
                return (
                    None,
                    "",
                    False,
                    t(f"「{name}」不是前面的階段", f'"{name}" is not an earlier stage'),
                )
            frame, key, part_sampled, _ = self._stage_result(index)
            if frame is None:
                return None, "", False, None
            frames[name], sampled = frame, sampled or part_sampled
            keys.append(key)
        key = _digest({"stages": names, "keys": keys})
        cached = self._inputs.get(stage["name"])
        if cached and cached[0] == key:
            return cached[1], key, sampled, None
        try:
            frame = stage_frame(names, frames)
        except (InputError, pl.exceptions.PolarsError) as err:
            return None, key, sampled, str(err)
        self._inputs[stage["name"]] = (key, frame)
        return frame, key, sampled, None

    def _compute(self, si: int) -> tuple[list[_Computed], pl.DataFrame | None, str, bool]:
        """依序計算第 si 個階段的每一步（有快取）。遇到參數不合法或執行失敗的步驟，之後的步驟都不算。

        回傳（每一步的結果、這個階段的輸入、輸入的指紋、是不是樣本）。
        """
        stage = self.stages[si]
        source, key, sampled, _ = self._stage_input(si)
        frame = source
        previous = self._computed.get(stage["name"], [])
        results: list[_Computed] = []
        broken = frame is None
        for i, raw in enumerate(stage["steps"]):
            refs = {}
            for field_name in ref_fields().get(raw.get("type"), []):
                ref = raw.get(field_name)
                index = self._stage_index(ref, si) if isinstance(ref, str) else None
                if index is not None:
                    refs[ref] = self._stage_result(index)
            key = _digest({"prev": key, "step": raw, "refs": {n: r[1] for n, r in refs.items()}})
            cached = previous[i] if i < len(previous) else None
            if cached and cached.key == key:
                results.append(cached)
                frame = cached.frame
                broken = broken or cached.frame is None
                continue
            item = _Computed(key)
            model = self._model(raw, item)
            waiting = any(r[0] is None for r in refs.values())
            if model is not None and not broken and not (waiting and model.enabled):
                if not model.enabled:
                    item.frame = frame
                else:
                    tables = {n: r[0] for n, r in refs.items()}
                    try:
                        item.frame, item.warning_count, item.warnings = apply_step(
                            model, frame, tables=tables
                        )
                    except RunError as err:
                        item.run_error = err.message
            broken = broken or item.frame is None
            frame = item.frame
            results.append(item)
        self._computed[stage["name"]] = results
        return results, source, key, sampled

    def _stage_result(self, si: int) -> tuple[pl.DataFrame | None, str, bool, bool]:
        """第 si 個階段的結果：（資料、指紋、是不是樣本、是不是「完整跑一次」的結果）。"""
        computed, source, key, sampled = self._compute(si)
        frame = source
        if computed:
            frame, key = computed[-1].frame, computed[-1].key
        if frame is None:
            return None, "", sampled, False
        full = self._full.get(self.stages[si]["name"])
        if sampled and full and full[0] == key:
            return full[1], f"{key}:full", False, True
        return frame, key, sampled, False

    def _model(self, raw: dict[str, Any], item: _Computed) -> Step | None:
        try:
            cls = get(raw.get("type", ""))
        except KeyError:
            item.errors = [
                {
                    "path": "type",
                    "error": t(
                        f"不支援的步驟類型：{raw.get('type')}",
                        f"Unsupported step type: {raw.get('type')}",
                    ),
                    "severity": "error",
                }
            ]
            return None
        try:
            return cls.model_validate(raw)
        except ValidationError as err:
            item.errors = pydantic_issues(err)
            return None

    def _frame_before(self, index: int) -> pl.DataFrame | None:
        """目前階段第 index 步的輸入（index=0 是這個階段的輸入）。"""
        computed, source, _, _ = self._compute(self.current)
        if index <= 0:
            return source
        return computed[index - 1].frame if index - 1 < len(computed) else None

    def frame_at(self, index: int) -> pl.DataFrame | None:
        """目前階段第 index 步的輸出；-1 是這個階段的輸入。"""
        with self._lock:
            computed, source, _, _ = self._compute(self.current)
            if index < 0:
                return source
            return computed[index].frame if index < len(computed) else None

    def full_run(self, index: int) -> None:
        """「完整跑一次」：用全部資料算出第 index 個階段，之後的預覽用它完整的結果（結合的右表）。

        只跑這個階段和它往上讀到、結合到的階段（``_upstream``），沒用到的前面階段不跑。
        """
        with self._lock:
            self._check_stage(index)
            base = self.require_loaded()
            frame, key, sampled, _ = self._stage_result(index)
            if frame is None or not sampled:
                return
            data = self.recipe_dict()
            data["stages"] = [data["stages"][i] for i in self._upstream(index)]
            data["output"] = {**data["output"], "keep_system_columns": True, "manifest": False}
            target = staging_dir() / f"full-{uuid.uuid4().hex}.parquet"
            try:
                execute(
                    parse_recipe(data), base, output=target, mode="stream", progress=self.progress
                )
                self._full[self.stages[index]["name"]] = (key, pl.read_parquet(target))
            finally:
                target.unlink(missing_ok=True)
                release_workers()

    # ── 給前端的畫面狀態 ─────────────────────────────────────────

    def _stages_state(self) -> list[dict[str, Any]]:
        """每個階段的概況（最左邊的階段分頁、結合的右表選單、輸出設定）。"""
        out = []
        planned = self._outputs()
        for i, stage in enumerate(self.stages):
            frame, _, sampled, full = self._stage_result(i)
            computed = self._computed.get(stage["name"], [])
            error = any(c.errors or c.run_error for c in computed) or bool(self._stage_input(i)[3])
            status = "error" if error else ("ok" if frame is not None else "waiting")
            out.append(
                {
                    "index": i,
                    "name": stage["name"],
                    "reader": stage["input"].get("reader"),
                    "last": i == len(self.stages) - 1,
                    "status": status,
                    "status_text": self._stage_status_text(i, status, frame, computed, out),
                    "rows": frame.height if frame is not None else None,
                    "columns": _data_columns(frame) if frame is not None else None,
                    "sampled": sampled,
                    "full": full,
                    "uses": list(dict.fromkeys(ref for ref, _ in stage_refs(stage))),
                    "output_file": stage.get("output_file", ""),
                    "output": _planned_state(planned[i]) if i < len(planned) else None,
                }
            )
        return out

    def _stage_status_text(
        self,
        index: int,
        status: str,
        frame: pl.DataFrame | None,
        computed: list[Any],
        earlier: list[dict[str, Any]],
    ) -> str:
        """階段面板圓點的說明（滑鼠移上去）：已算出結果、哪一步有錯、為什麼還沒有結果。"""
        stage = self.stages[index]
        if status == "ok" and frame is not None:
            return t(
                f"已算出結果：{frame.height:,} 列",
                f"Result ready: {plural(frame.height, 'row', 'rows')}",
            )
        if status == "error":
            first = next((k for k, c in enumerate(computed) if c.errors or c.run_error), None)
            return (
                t(f"第 {first + 1} 步有錯", f"Step {first + 1} has an error")
                if first is not None
                else t("讀取資料有錯", "“Read data” has an error")
            )
        for ref, _ in stage_refs(stage):
            j = self._stage_index(ref, index)
            if j is not None and j < len(earlier) and earlier[j]["status"] != "ok":
                why = (
                    t("有錯", "has errors")
                    if earlier[j]["status"] == "error"
                    else t("還沒有結果", "has no result yet")
                )
                return t(f"讀的「{ref}」{why}，還算不出來", f'Can\'t compute yet: "{ref}" {why}')
        if self._reads_files(stage) and not stage["input"]["files"]:
            return t("還沒有加入資料檔", "No data files added yet")
        return t("還沒載入資料", "Data not loaded yet")

    def state(self) -> dict[str, Any]:
        with self._lock:
            stage = self.stage
            reads_files = self._reads_files(stage)
            loaded, files = self._load_source(stage) if reads_files else (None, [])
            entries = (
                check_entries(self.input["files"], self.base_dir)
                if self.base_dir and reads_files
                else []
            )
            computed, source, _, sampled = self._compute(self.current)
            input_error = self._stage_input(self.current)[3]
            steps, previous = [], source
            # 算不出來的步驟是被誰擋住：前面第一個出錯的步驟，或還沒有結果的前面階段
            first_error: int | None = None
            waiting_stage: str | None = None
            for i, (raw, item) in enumerate(zip(self.steps, computed, strict=True)):
                cls = _safe_get(raw.get("type"))
                enabled = raw.get("enabled", True) is not False
                status = (
                    "error"
                    if item.errors or item.run_error
                    else (
                        "disabled"
                        if not enabled
                        else (
                            "ok"
                            if item.frame is not None
                            else ("waiting" if source is None else "pending")
                        )
                    )
                )
                if status == "error" and first_error is None:
                    first_error = i
                if status == "pending" and first_error is None and waiting_stage is None:
                    waiting_stage = self._waiting_ref(raw)
                describe = None
                if not item.errors and cls is not None:
                    try:
                        describe = cls.model_validate(raw).describe()
                    except ValidationError:
                        describe = None
                steps.append(
                    {
                        "index": i,
                        "id": raw.get("id"),
                        "type": raw.get("type"),
                        "label": cls.label if cls else raw.get("type"),
                        "category": cls.category if cls else "",
                        "enabled": enabled,
                        "status": status,
                        "describe": describe,
                        "rows": item.frame.height if item.frame is not None and enabled else None,
                        "delta": (item.frame.height - previous.height)
                        if item.frame is not None and previous is not None and enabled
                        else None,
                        "input_columns": _data_columns(previous) if previous is not None else None,
                        "files": _file_count(item.frame)
                        if item.frame is not None and enabled
                        else None,
                        "input_files": _files(previous) if previous is not None else None,
                        "warning_count": item.warning_count,
                        "errors": self._value_errors(i, raw, item.errors),
                        "run_error": item.run_error,
                        "blocked_by": first_error if status == "pending" else None,
                        "blocked_stage": waiting_stage
                        if status == "pending" and first_error is None
                        else None,
                        "params": raw,
                    }
                )
                if item.frame is not None:
                    previous = item.frame
            selected = max(-1, min(self.selected, len(self.steps)))
            # 「輸出資料」預覽最後一步的結果
            requested = min(selected, len(self.steps) - 1)
            # 選取的步驟算不出來時，顯示它之前最後一個算得出來的結果
            shown_index = requested
            while shown_index >= 0 and computed[shown_index].frame is None:
                shown_index -= 1
            shown = source if shown_index < 0 else computed[shown_index].frame
            full = self._showing_full()
            blocked = None
            if shown_index != requested:
                if source is None:
                    blocked = {"unloaded": True, "step": None, "stage": None}
                else:
                    item = steps[requested]
                    step = requested if item["status"] == "error" else item["blocked_by"]
                    blocked = {"unloaded": False, "step": step, "stage": item["blocked_stage"]}
                    # 這一步算不出來：不顯示前面步驟的資料（畫面上改寫原因與「前往」）
                    shown, shown_index = None, requested
            preview_warnings = (
                []
                if shown_index < 0 or shown is None
                else [w.__dict__ for w in computed[shown_index].warnings[:200]]
            )
            total_rows = sum(f.rows for f in files) if files else None
            preview = {
                "step": shown_index,
                "requested": requested,
                "rows": shown.height if shown is not None else None,
                "columns": [
                    {"name": n, "dtype": str(t), "system": n.startswith(SYSTEM_PREFIX)}
                    for n, t in (shown.schema.items() if shown is not None else [])
                ],
                "warnings": preview_warnings,
                "blocked": blocked,
                # 抽樣模式：用樣本算的（列數、加總可能不正確）；完整模式：資料全部讀進來，或讀匯出檔
                "mode": None if shown is None else ("sample" if sampled else "full"),
                # 這個階段和它往上讀到的階段，讀進來的檔共有幾行（讀前面階段時也算得出來）
                "input_rows": sum(
                    f.rows
                    for i in self._upstream(self.current)
                    if self._reads_files(self.stages[i])
                    for f in self._load_source(self.stages[i])[1]
                )
                if shown is not None
                else None,
                "full": None,
                "sampled_steps": self._sampled_steps(requested) if sampled else [],
            }
            if full is not None:
                schema = pl.read_parquet_schema(full["path"])
                preview.update(
                    step=len(self.steps),
                    requested=len(self.steps),
                    rows=full["rows"],
                    columns=[
                        {"name": n, "dtype": str(t), "system": n.startswith(SYSTEM_PREFIX)}
                        for n, t in schema.items()
                    ],
                    warnings=[w.__dict__ for w in full["warnings"][:200]],
                    blocked=None,
                    mode="full",
                    sampled_steps=[],
                    full={
                        k: full[k] for k in ("path", "rows", "input_rows", "time", "warning_count")
                    },
                )
            return {
                "version": {"tablade": __version__, "recipe": RECIPE_VERSION},
                "recipe": {
                    "path": str(self.recipe_path) if self.recipe_path else None,
                    "name": self.meta.get("name", ""),
                    "description": self.meta.get("description", ""),
                    "dirty": self.dirty,
                    "base_dir": str(self.base_dir) if self.base_dir else None,
                    "open": self.recipe_path is not None,
                },
                "stages": self._stages_state(),
                "current": self.current,
                "actions": {
                    name: {"ok": problem is None, "reason": problem}
                    for name, problem in (
                        ("load", self._load_problem()),
                        ("export", self._export_problem()),
                    )
                },
                "input": {
                    **{k: v for k, v in self.input.items()},
                    "sheets": self._sheet_names(),
                    "entries": entries,
                    "earlier": [st["name"] for st in self.stages[: self.current]],
                    "can_load": any(
                        self._reads_files(st) and st["input"]["files"] for st in self.stages
                    )
                    and all(e["found"] for e in entries)
                    and (not reads_files or bool(entries)),
                    "loaded": source is not None,
                    "stale": stage["name"] in self._stale,
                    "error": input_error,
                    "total_rows": total_rows,
                    "sampled": sampled,
                    "sample": {"head": SAMPLE_HEAD, "tail": SAMPLE_TAIL},
                    "rows": source.height if source is not None else None,
                    "columns": _data_columns(source) if source is not None else None,
                    "sources": [f.__dict__ for f in files] if loaded is not None else [],
                },
                "output": self.output,
                "steps": steps,
                "selected": selected,
                "preview": preview,
                "can_undo": bool(self._undo),
                "can_redo": bool(self._redo),
                "issues": self.issues,
                "lang": self.lang,
            }

    def _waiting_ref(self, raw: dict[str, Any]) -> str | None:
        """這一步引用的前面階段裡，第一個還沒有結果的（結合的另一張表）。"""
        for name in ref_fields().get(raw.get("type"), []):
            ref = raw.get(name)
            index = self._stage_index(ref, self.current) if isinstance(ref, str) else None
            if index is not None and self._stage_result(index)[0] is None:
                return ref
        return None

    def _full_frame(self, index: int) -> tuple[pl.LazyFrame, dict[str, Any]] | None:
        """index 是「輸出資料」（步驟數）而且是完整模式：匯出檔（lazy，不整份讀進記憶體）。"""
        if index != len(self.steps):
            return None
        full = self._showing_full()
        return (pl.scan_parquet(full["path"]), full) if full is not None else None

    def _sampled_steps(self, upto: int) -> list[dict[str, Any]]:
        """用樣本算、數字會不準的步驟（要看整個來源檔的：移除重複、群組彙總、樞紐），依階段與步驟順序。

        目前階段算到第 upto 步（含）；往上讀到、結合到的階段結果還是樣本時，它的步驟也算
        （資料不大或已經「完整跑一次」的階段不算）。``stage`` 是空字串代表目前的階段。
        """
        out = []
        for si in self._upstream(self.current):
            current = si == self.current
            if not current and not self._stage_result(si)[2]:
                continue
            steps = self.stages[si]["steps"]
            for i, raw in enumerate(steps[: upto + 1] if current else steps):
                cls = _safe_get(raw.get("type"))
                if cls is not None and cls.whole_source and raw.get("enabled", True) is not False:
                    name = "" if current else self.stages[si]["name"]
                    out.append({"stage": name, "number": i + 1, "label": cls.label})
        return out

    def rows(self, index: int, offset: int, limit: int) -> dict[str, Any]:
        with self._lock:
            offset = max(0, offset)
            full = self._full_frame(index)
            if full is not None:
                part = full[0].slice(offset, max(0, min(limit, 1000))).collect()
                return {
                    "offset": offset,
                    "total": full[1]["rows"],
                    "columns": part.columns,
                    "rows": [[_jsonable(v) for v in row] for row in part.iter_rows()],
                }
            frame = self.frame_at(index)
            if frame is None:
                return {"offset": offset, "total": 0, "columns": [], "rows": []}
            offset = max(0, offset)
            limit = max(0, min(limit, 1000))
            part = frame.slice(offset, limit)
            return {
                "offset": offset,
                "total": frame.height,
                "columns": part.columns,
                "rows": [[_jsonable(v) for v in row] for row in part.iter_rows()],
            }

    def column_info(self, index: int) -> dict[str, Any]:
        """第 index 步輸出的每一欄型別與概況（下方面板的「型別」分頁）；資料跟預覽同一份。"""
        with self._lock:
            full = self._full_frame(index)
            if full is not None:
                return {
                    "step": index,
                    "rows": full[1]["rows"],
                    "sampled": False,
                    "columns": column_summary(full[0]),
                }
            frame = self.frame_at(index)
            sampled = self._stage_input(self.current)[2]
            if frame is None:
                return {"step": index, "rows": 0, "sampled": sampled, "columns": []}
            return {
                "step": index,
                "rows": frame.height,
                "sampled": sampled,
                "columns": column_summary(frame),
            }

    def find_row(
        self,
        index: int,
        source_file: str,
        line_no: int,
        sheet: str | None = None,
        stage: str | None = None,
    ) -> int | None:
        """在第 index 步的結果裡找某個來源檔（工作表、從哪個階段讀進來）某一行的位置（點警告時跳到那一列）。"""
        with self._lock:
            full = self._full_frame(index)
            frame: pl.DataFrame | pl.LazyFrame | None = (
                full[0] if full is not None else self.frame_at(index)
            )
            if frame is None or "__line_no" not in frame.collect_schema().names():
                return None
            match = (pl.col("__source_file") == source_file) & (pl.col("__line_no") == line_no)
            names = frame.collect_schema().names()
            if sheet and SHEET in names:
                match &= pl.col(SHEET) == sheet
            if stage and STAGE in names:
                match &= pl.col(STAGE) == stage
            hits = (
                frame.lazy()
                .with_row_index("__pos")
                .filter(match)
                .select("__pos")
                .head(1)
                .collect(engine="streaming")
                .get_column("__pos")
            )
            return int(hits[0]) if hits.len() else None

    # ── 匯出與報告 ────────────────────────────────────────────

    def require_loaded(self) -> Path:
        """「完整跑一次」前的檢查：每個讀實際檔的階段都要先載入，而且載入後設定與檔案都沒變。"""
        with self._lock:
            base = self._require_recipe()
            waiting = [
                st["name"]
                for st in self.stages
                if self._reads_files(st) and self._load_source(st)[0] is None
            ]
            if waiting:
                where = (
                    t(f"（{'、'.join(waiting)}）", f" ({', '.join(waiting)})")
                    if len(self.stages) > 1
                    else ""
                )
                raise SessionError(f"{load_hint()}{where}")
            return base

    def _file_problem(self) -> str | None:
        """檔案清單的問題：讀實際檔的階段還沒加檔案，或清單上有找不到的檔。"""
        base = self.base_dir or Path.cwd()
        multi = len(self.stages) > 1
        for stage in self.stages:
            if not self._reads_files(stage):
                continue
            where = (
                t(f"（階段「{stage['name']}」）", f' (stage "{stage["name"]}")') if multi else ""
            )
            if not stage["input"]["files"]:
                return t(f"還沒有加入資料檔{where}", f"No data files added yet{where}")
            missing = [
                e["path"] for e in check_entries(stage["input"]["files"], base) if not e["found"]
            ]
            if missing:
                return t(
                    f"找不到：{'、'.join(missing)}{where}，請先從檔案清單移除",
                    f"Not found: {', '.join(missing)}{where}. Remove {'it' if len(missing) == 1 else 'them'} from the file list first",
                )
        return None

    def _load_problem(self) -> str | None:
        """「載入資料及預覽」不能按的原因（None 是可以按）。"""
        if self.base_dir is None:
            return t("還沒有開啟 Recipe", "No Recipe open")
        with_files = [st for st in self.stages if self._reads_files(st) and st["input"]["files"]]
        if not with_files:
            return t("還沒有加入資料檔", "No data files added yet")
        base, multi = self.base_dir, len(self.stages) > 1
        for stage in with_files:
            missing = [
                e["path"] for e in check_entries(stage["input"]["files"], base) if not e["found"]
            ]
            if missing:
                where = (
                    t(f"（階段「{stage['name']}」）", f' (stage "{stage["name"]}")')
                    if multi
                    else ""
                )
                return t(
                    f"找不到：{'、'.join(missing)}{where}，請先從檔案清單移除",
                    f"Not found: {', '.join(missing)}{where}. Remove {'it' if len(missing) == 1 else 'them'} from the file list first",
                )
        if all(self._load_source(st)[0] is not None for st in with_files):
            return t(
                "已載入；檔案清單、讀取方式或編碼變更後可以重新載入",
                "Already loaded; you can reload after changing the file list, “Read as”, or encoding",
            )
        return None

    def _export_problem(self) -> str | None:
        """「處理並匯出資料」不能按的原因（None 是可以按）。不用先載入資料。"""
        if self.base_dir is None:
            return t("還沒有開啟 Recipe", "No Recipe open")
        problem = self._file_problem()
        if problem:
            return problem
        for stage, planned in zip(self.stages, self._outputs(), strict=True):
            if planned is not None and planned.error:
                error = planned.error
                if error.startswith("輸出檔名"):  # i18n-ignore：比對 export/outputs.py 的訊息
                    return t(
                        f"階段「{stage['name']}」的{error}", f'Stage "{stage["name"]}": {error}'
                    )
                return t(f"階段「{stage['name']}」：{error}", f'Stage "{stage["name"]}": {error}')
        for i, stage in enumerate(self.stages):
            self._stage_result(i)
            computed = self._computed.get(stage["name"], [])
            if any(c.errors or c.run_error for c in computed) or self._stage_input(i)[3]:
                where = (
                    t(f"階段「{stage['name']}」", f' in stage "{stage["name"]}"')
                    if len(self.stages) > 1
                    else ""
                )
                return t(
                    f"{where}有步驟出錯，請先修正", f"Some steps{where} have errors; fix them first"
                )
        return None

    def _previewed(self) -> bool:
        """每個階段都已經用載入的資料算過、沒有錯：匯出前不用再試跑。"""
        for i, stage in enumerate(self.stages):
            if self._reads_files(stage) and self._load_source(stage)[0] is None:
                return False
            frame = self._stage_result(i)[0]
            computed = self._computed.get(stage["name"], [])
            if frame is None or any(c.errors or c.run_error for c in computed):
                return False
        return True

    def _step_place(self, step_id: str | None) -> tuple[int | None, int | None, str]:
        """步驟 id 在第幾個階段第幾步，以及畫面上的說法（「階段「X」第 3 步「拆欄」」）。"""
        for si, stage in enumerate(self.stages):
            for i, raw in enumerate(stage["steps"]):
                if raw.get("id") == step_id:
                    cls = _safe_get(raw.get("type"))
                    label = t(f"「{cls.label}」", f' "{cls.label}"') if cls else ""
                    where = (
                        t(f"階段「{stage['name']}」", f'Stage "{stage["name"]}", ')
                        if len(self.stages) > 1
                        else ""
                    )
                    return (
                        si,
                        i,
                        t(
                            f"{where}第 {i + 1} 步{label}",
                            f"{where}step {i + 1}{label}" if where else f"Step {i + 1}{label}",
                        ),
                    )
        return None, None, ""

    def _upstream(self, index: int) -> list[int]:
        """第 index 個階段和它往上讀到、結合到的階段（依階段順序）。"""
        found: set[int] = set()
        todo = [index]
        while todo:
            i = todo.pop()
            if i in found:
                continue
            found.add(i)
            for ref, _ in stage_refs(self.stages[i]):
                j = self._stage_index(ref, i)
                if j is not None:
                    todo.append(j)
        return sorted(found)

    def _export_key(self, index: int) -> str:
        """第 index 個階段的輸出檔跟什麼有關：它和往上用到的階段的設定、輸出設定、讀到的檔（位置、大小、修改時間）。"""
        used = [self.stages[i] for i in self._upstream(index)]
        return _digest(
            {
                "stages": [self._stage_dict(st) for st in used],
                "output": self.output,
                "files": [self._source_key(st) for st in used if self._reads_files(st)],
            }
        )

    def _full_output(self, index: int | None = None) -> dict[str, Any] | None:
        """第 index 個階段（預設目前的）上次匯出的檔，而且跟現在的 Recipe、檔案對得上（完整模式）。"""
        index = self.current if index is None else index
        entry = self._exports.get(self.stages[index]["name"])
        if entry is None:
            return None
        if entry["key"] != self._export_key(index) or not Path(entry["path"]).is_file():
            del self._exports[self.stages[index]["name"]]
            return None
        return entry

    def _showing_full(self) -> dict[str, Any] | None:
        """選的是「輸出資料」，而且這個階段有對得上的匯出檔：預覽讀匯出檔（完整模式）。"""
        if self.selected != len(self.steps):
            return None
        return self._full_output()

    def export(self) -> dict[str, Any]:
        """處理全部資料：每個要寫檔的階段寫到 Recipe 所在資料夾底下的 output/，處理紀錄寫到 log/。

        不用先載入資料（引擎自己讀檔、轉碼）。沒有用載入的資料預覽過時，先用每個檔的前 ``TRIAL_HEAD`` 行
        試跑一次，有錯就停下（``TrialError`` 指到哪一步），不會白跑大檔。已經存在的檔直接覆蓋。
        進度視窗上有「準備檔案」「試跑」兩列，下面是每個階段。
        """
        with self._lock:
            base = self._require_recipe()
            problem = self._export_problem()
            if problem:
                raise SessionError(problem)
            recipe = parse_recipe(self.recipe_dict())
            try:
                targets = resolve_outputs(recipe, base, self._default_output())
            except OutputError as err:
                raise SessionError(str(err)) from None
            names = [st["name"] for st in self.stages]
            keys = [self._export_key(i) for i in range(len(self.stages))]
            tracker = StageTracker(names, self.progress, [PREPARE, TRIAL])
            try:
                if self._previewed():
                    tracker.skip_phase(
                        TRIAL,
                        t("已用預覽檢查過，略過", "Already checked with the preview; skipped"),
                    )
                else:
                    self._trial(recipe, base, tracker)
                _, report = execute(
                    recipe,
                    base,
                    outputs=targets,
                    log_dir=log_dir(base),
                    progress=self.progress,
                    tracker=tracker,
                )
            except (InputError, DecodeError) as err:
                raise SessionError(str(err)) from None
            finally:
                release_workers()  # 平行轉碼的工作程序：跑完就關掉，不用時不佔記憶體
            self.last_export = Path(report.output) if report.output else None
            when = datetime.now()
            for out in report.outputs:
                index = names.index(out.stage)
                used = {names[i] for i in self._upstream(index)}
                steps = [s for s in report.steps if s.stage == out.stage]
                self._exports[out.stage] = {
                    "key": keys[index],
                    "path": out.path,
                    "rows": out.rows,
                    "input_rows": sum(f.rows for f in report.inputs if f.stage in used),
                    "time": f"{when.month}/{when.day} {when:%H:%M}",
                    "warning_count": sum(s.warning_count for s in steps),
                    "warnings": [w for s in steps for w in s.warnings],
                }
            return {
                "path": report.output,
                "rows": report.rows,
                "columns": len(report.columns),
                "warnings": report.warning_count,
                "mode": report.mode,
                "manifest": report.manifest,
                "outputs": [
                    {"stage": o.stage, "path": o.path, "rows": o.rows, "manifest": o.manifest}
                    for o in report.outputs
                ],
            }

    def _trial(self, recipe: Recipe, base: Path, tracker: StageTracker) -> None:
        """匯出前的試跑：每個檔只用前幾行把全部階段跑一次，有錯就丟出 ``TrialError``。"""
        try:
            execute(
                recipe,
                base,
                mode="sample",
                head=TRIAL_HEAD,
                tail=0,
                progress=self.progress,
                tracker=tracker,
                phase=TRIAL,
            )
        except RunError as err:
            stage, step, where = self._step_place(err.step_id)
            message = err.message
            raise TrialError(
                t(f"試跑時出錯：{where}：{message}", f"Trial run failed: {where}: {message}")
                if where
                else t(f"試跑時出錯：{message}", f"Trial run failed: {message}"),
                stage,
                step,
            ) from None

    def error_report(self) -> str:
        """給 AI 助理（Copilot）看的驗證結果：問題、每一步的列數、警告。"""
        with self._lock:
            lines = [
                t(
                    f"Tablade 驗證結果（Recipe 版本 {RECIPE_VERSION}，Tablade {__version__}）",
                    f"Tablade validation results (Recipe version {RECIPE_VERSION}, Tablade {__version__})",
                )
            ]
            if self.recipe_path:
                lines.append(
                    t(f"Recipe：{self.recipe_path.name}", f"Recipe: {self.recipe_path.name}")
                )
            data = self.recipe_dict()
            result = check_recipe(
                data, self.base_dir or Path.cwd(), dry_run=self.base_dir is not None
            )
            if result.issues:
                lines += ["", t("問題：", "Issues:"), format_issues(result.issues)]
            else:
                lines += ["", t("沒有發現問題。", "No issues found.")]
            if result.report:
                lines += [
                    "",
                    t("試跑結果：", "Trial run results:"),
                    format_report(result.report, max_warnings=20),
                ]
            lines += [
                "",
                t(
                    f'請依上面的問題修正 Recipe，tablade_recipe 維持 "{RECIPE_VERSION}"。',
                    f'Fix the Recipe based on the issues above, keeping tablade_recipe at "{RECIPE_VERSION}".',
                ),
            ]
            return "\n".join(lines)


def _planned_state(planned: Any) -> dict[str, Any] | None:
    """一個階段要寫的檔（給畫面的輸出設定）：檔名、位置（output/檔名）、是不是預設檔名、已不已經存在、錯誤。"""
    if planned is None:
        return None
    return {
        "file": planned.file,
        "path": f"{planned.path.parent.name}/{planned.file}",
        "default": planned.default,
        "exists": planned.path.is_file(),
        "error": planned.error,
    }


def _safe_get(type_name: Any) -> type[Step] | None:
    try:
        return get(type_name)
    except KeyError:
        return None


def catalog() -> list[dict[str, Any]]:
    """給前端的步驟目錄：每種步驟的名稱、說明與參數 schema（表單由 schema 自動產生）。"""
    items = []
    for cls in all_steps():
        schema = cls.model_json_schema()
        items.append(
            {
                "type": cls.type_name(),
                "label": cls.label,
                "category": cls.category,
                "description": (cls.__doc__ or "").strip(),
                "scope": cls.scope(),
                "schema": schema,
            }
        )
    return items
