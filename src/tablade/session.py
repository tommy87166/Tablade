"""編輯中的 recipe：狀態由 Python 管理，前端只送指令、顯示畫面狀態（DESIGN.md §16.4）。

- 資料很大時（超過 ``SAMPLE_THRESHOLD`` 行），預覽只用每個來源檔的前幾行與最後幾行，
  匯出時才串流處理全部資料（DESIGN.md §8）。

- 步驟以原始 dict 保存：參數還不完整（剛新增、正在填）的步驟也能留在清單裡，顯示錯誤，
  預覽算到它的前一步為止，跟 Power Query 的步驟錯誤一樣。
- 每一步的結果依「input ＋ 到這一步為止的步驟」的內容快取；改了第 k 步，k 之前的結果直接沿用。
- 每個會改變 recipe 的操作都先存一份快照，支援 undo / redo。
- 不碰檔案對話框（那是 api.py 的事），所以可以直接用 pytest 測。
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import polars as pl
from pydantic import ValidationError

from . import __version__
from .engine.check import check_recipe, format_issues, format_report
from .engine.run import (
    SAMPLE_HEAD,
    SAMPLE_TAIL,
    SAMPLE_THRESHOLD,
    RowWarning,
    RunError,
    apply_step,
    execute,
)
from .ingest.encoding import DecodeError
from .ingest.excel import ExcelError, is_excel, sheet_names
from .ingest.sources import (
    InputError,
    Progress,
    SourceFile,
    check_entries,
    concat,
    expand,
    sample,
    scan_parts,
)
from .recipe.schema import RECIPE_VERSION, RecipeError, load_json, parse_recipe, pydantic_issues
from .steps import all_steps, get
from .steps.base import SOURCE_FILE, SYSTEM_PREFIX, Step

MAX_UNDO = 100
DEFAULT_INPUT = {"id": "src", "files": [], "reader": "text_lines", "encoding": "auto", "sheet": ""}
DEFAULT_OUTPUT = {"format": "parquet", "keep_system_columns": True, "manifest": True}
INVALID_FILENAME = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


class SessionError(ValueError):
    pass


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
    """目前的來源檔（依出現順序）。"""
    if SOURCE_FILE not in frame.columns:
        return []
    return frame.get_column(SOURCE_FILE).unique(maintain_order=True).to_list()


def _file_count(frame: pl.DataFrame) -> int:
    return frame.get_column(SOURCE_FILE).n_unique() if SOURCE_FILE in frame.columns else 0


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


EXAMPLE_ROWS = 1_000
"""型別分頁的範例值從前這麼多列找。"""


def type_label(dtype: pl.DataType) -> str:
    """型別的中文名稱（型別分頁用）。"""
    if dtype == pl.String:
        return "文字"
    if isinstance(dtype, pl.Decimal):
        return f"小數（{dtype.scale} 位）"
    if dtype.is_integer():
        return "整數"
    if dtype.is_float():
        return "浮點數"
    if dtype == pl.Date:
        return "日期"
    if isinstance(dtype, pl.Datetime):
        return "日期時間"
    if dtype == pl.Boolean:
        return "是／否"
    if dtype == pl.Null:
        return "空"
    return str(dtype)


def column_summary(frame: pl.DataFrame) -> list[dict[str, Any]]:
    """每一欄的型別、空值、空白字串、最小～最大與範例值；位置是第幾個資料欄（和刪除欄的位置相同）。"""
    out, position = [], 0
    head = frame.head(EXAMPLE_ROWS)
    for name, dtype in frame.schema.items():
        system = name.startswith(SYSTEM_PREFIX)
        if not system:
            position += 1
        series = frame.get_column(name)
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
                "nulls": series.null_count(),
                "blanks": int((series.str.strip_chars() == "").sum())
                if dtype == pl.String
                else None,
                "min": _jsonable(series.min()) if ranged else None,
                "max": _jsonable(series.max()) if ranged else None,
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
        self.input: dict[str, Any] = copy.deepcopy(DEFAULT_INPUT)
        self.output: dict[str, Any] = dict(DEFAULT_OUTPUT)
        self.steps: list[dict[str, Any]] = []
        self.selected = -1
        self.dirty = False
        self.issues: list[dict[str, Any]] = []
        self._undo: list[dict[str, Any]] = []
        self._redo: list[dict[str, Any]] = []
        self._sheets: dict[tuple[str, int, int], list[str]] = {}
        self._source: tuple[str, pl.DataFrame | None, list[SourceFile], str | None] | None = None
        """按「載入資料」讀進來的資料（設定指紋、資料、來源檔、錯誤）；不會自動讀檔。"""
        self._stale = False
        """載入之後檔案清單、讀取方式、編碼或檔案內容變了：要重新按「載入資料」。"""
        self.progress: Progress = None
        """讀檔、轉碼、匯出時的進度回報（api.py 會把它推給前端）。"""
        self._computed: list[_Computed] = []
        self.last_export: Path | None = None
        """上次「處理並匯出資料」存的位置：下次存檔對話框的預設值。"""

    # ── 快照與 undo / redo ─────────────────────────────────────

    def _snapshot(self) -> dict[str, Any]:
        return copy.deepcopy(
            {
                "meta": self.meta,
                "input": self.input,
                "output": self.output,
                "steps": self.steps,
                "selected": self.selected,
            }
        )

    def _restore(self, snap: dict[str, Any]) -> None:
        snap = copy.deepcopy(snap)
        self.meta, self.input, self.output = snap["meta"], snap["input"], snap["output"]
        self.steps, self.selected = snap["steps"], snap["selected"]

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

    def recipe_dict(self) -> dict[str, Any]:
        source = dict(self.input)
        if source.get("reader") != "excel" and not source.get("sheet"):
            source.pop("sheet", None)  # 不是 Excel 時不寫工作表，Recipe 比較好讀
        return {
            "tablade_recipe": RECIPE_VERSION,
            "name": self.meta.get("name", ""),
            "description": self.meta.get("description", ""),
            "inputs": [source],
            "steps": self.steps,
            "output": self.output,
        }

    def _reset(self) -> None:
        """回到空白狀態（保留進度回報的接線）。"""
        progress = self.progress
        self.__init__()
        self.progress = progress

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
            result = check_recipe(data, path.parent)
            if any(i["path"] == "tablade_recipe" for i in result.issues):
                raise RecipeError([i for i in result.issues if i["path"] == "tablade_recipe"])
            inputs = data.get("inputs") if isinstance(data.get("inputs"), list) else []
            self._reset()
            self.recipe_path, self.base_dir = path, path.parent
            self.meta = {
                "name": str(data.get("name", "")),
                "description": str(data.get("description", "")),
            }
            self.input = {
                **copy.deepcopy(DEFAULT_INPUT),
                **(copy.deepcopy(inputs[0]) if inputs and isinstance(inputs[0], dict) else {}),
            }
            if not isinstance(self.input.get("files"), list):
                self.input["files"] = []
            self.input["files"] = [f for f in self.input["files"] if isinstance(f, str) and f]
            self.output = {
                **DEFAULT_OUTPUT,
                **(data.get("output") if isinstance(data.get("output"), dict) else {}),
            }
            self.steps = (
                [s for s in data.get("steps", []) if isinstance(s, dict)]
                if isinstance(data.get("steps"), list)
                else []
            )
            self.selected = len(self.steps) - 1
            self.issues = result.issues
            return result.issues

    def save_recipe(self, path: Path | None = None) -> Path:
        """存成 recipe 檔；換位置時，檔案清單依新位置重新決定存相對或絕對路徑。"""
        with self._lock:
            target = path or self.recipe_path
            if target is None:
                raise SessionError("還沒有指定 Recipe 檔的位置")
            target = target.resolve()
            if self.base_dir and target.parent != self.base_dir:
                self.input["files"] = [
                    _entry_for(_entry_path(e, self.base_dir), target.parent)
                    for e in self.input["files"]
                ]
            self.base_dir = target.parent
            target.write_text(
                json.dumps(self.recipe_dict(), ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            self.recipe_path = target
            self.dirty = False
            return target

    # ── 編輯操作 ──────────────────────────────────────────────

    def _require_recipe(self) -> Path:
        if self.base_dir is None:
            raise SessionError("先新增或開啟 Recipe")
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
                raise SessionError("請輸入檔案路徑")
            if entry in self.input["files"]:
                return
            self._change()
            self.input["files"].append(entry)

    def remove_file(self, index: int) -> None:
        with self._lock:
            if not 0 <= index < len(self.input["files"]):
                raise SessionError(f"檔案清單沒有第 {index + 1} 項")
            self._change()
            del self.input["files"][index]

    def set_input(self, values: dict[str, Any]) -> None:
        with self._lock:
            self._change()
            for key in ("reader", "encoding", "sheet"):
                if key in values:
                    self.input[key] = values[key]

    def set_output(self, values: dict[str, Any]) -> None:
        with self._lock:
            self._change()
            for key in ("keep_system_columns", "manifest"):
                if key in values:
                    self.output[key] = bool(values[key])

    def set_meta(self, values: dict[str, Any]) -> None:
        with self._lock:
            self._change()
            for key in ("name", "description"):
                if key in values:
                    self.meta[key] = str(values[key])

    def _new_id(self) -> str:
        used = {s.get("id") for s in self.steps}
        n = len(self.steps) + 1
        while f"s{n}" in used:
            n += 1
        return f"s{n}"

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
            params = cls.suggest(frame, context or {})
            self._change()
            self.steps.insert(
                position, self._normalize({"id": self._new_id(), "type": type_name, **params})
            )
            self.selected = position
            return position

    def update_step(self, index: int, values: dict[str, Any]) -> None:
        with self._lock:
            self._check_index(index)
            self._change()
            step = {"id": self.steps[index].get("id"), "type": self.steps[index].get("type")}
            step.update({k: v for k, v in values.items() if k not in ("id", "type")})
            self.steps[index] = self._normalize(step)

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
            raise SessionError(f"沒有第 {index} 步")

    # ── 計算 ─────────────────────────────────────────────────

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

    def _source_key(self) -> str:
        """目前設定的指紋：讀取方式、編碼，加上實際讀到的每個檔（位置、大小、修改時間）。

        用實際位置而不是清單上的寫法：另存到別的資料夾只改寫法，不需要重新載入。
        """
        files = []
        if self.base_dir:
            for entry in self.input["files"]:
                for p in expand(entry, self.base_dir):
                    try:
                        st = p.stat()
                        files.append((str(p.resolve()), st.st_size, st.st_mtime_ns))
                    except OSError:
                        continue
        reading = {k: self.input.get(k) for k in ("reader", "encoding", "sheet")}
        return _digest({"reading": reading, "files": files})

    def load_data(self) -> None:
        """「載入資料」：檔案清單裡的檔都找得到才讀（資料大時讀樣本）。"""
        with self._lock:
            base = self._require_recipe()
            missing = [
                e["path"] for e in check_entries(self.input["files"], base) if not e["found"]
            ]
            if not self.input["files"]:
                raise SessionError("還沒有加入資料檔")
            if missing:
                raise SessionError(f"找不到：{'、'.join(missing)}（請先從檔案清單移除）")
            self._source, self._computed, self._stale = None, [], False
            key = self._source_key()
            frame, files, error = None, [], None
            try:
                parts, files = scan_parts(
                    self.input["files"],
                    self.input["reader"],
                    self.input["encoding"],
                    base,
                    self.progress,
                    self.input.get("sheet", ""),
                )
                if sum(f.rows for f in files) <= SAMPLE_THRESHOLD:
                    frame = concat(parts).collect(engine="streaming")
                else:
                    self._report("讀取預覽樣本")
                    frame = sample(parts, files, self.input["reader"], SAMPLE_HEAD, SAMPLE_TAIL)
            except (InputError, DecodeError, LookupError, pl.exceptions.PolarsError) as err:
                error = str(err)
            if error:
                raise SessionError(error)
            self._source = (key, frame, files, None)

    def _load_source(self) -> tuple[pl.DataFrame | None, list[SourceFile], str | None]:
        """已載入的資料；設定或檔案變了就放掉（要重新按「載入資料」），不會自動讀檔。"""
        if self._source is None:
            return None, [], None
        if self._source[0] != self._source_key():
            self._source, self._computed, self._stale = None, [], True
            return None, [], None
        return self._source[1], self._source[2], self._source[3]

    def _report(self, stage: str) -> None:
        if self.progress is not None:
            self.progress({"stage": stage, "file": "", "done": 0, "total": 0})

    def _compute(self) -> list[_Computed]:
        """依序計算每一步（有快取）。遇到參數不合法或執行失敗的步驟，之後的步驟都不算。"""
        frame, _, _ = self._load_source()
        source_key = self._source[0] if self._source else ""
        results: list[_Computed] = []
        broken = frame is None
        for i, raw in enumerate(self.steps):
            key = _digest({"source": source_key, "steps": self.steps[: i + 1]})
            cached = self._computed[i] if i < len(self._computed) else None
            if cached and cached.key == key:
                results.append(cached)
                frame = cached.frame
                broken = broken or cached.frame is None
                continue
            item = _Computed(key)
            model = self._model(raw, item)
            if model is not None and not broken:
                if not model.enabled:
                    item.frame = frame
                else:
                    try:
                        item.frame, item.warning_count, item.warnings = apply_step(model, frame)
                    except RunError as err:
                        item.run_error = str(err).split("失敗：", 1)[-1]
            broken = broken or item.frame is None
            frame = item.frame
            results.append(item)
        self._computed = results
        return results

    def _model(self, raw: dict[str, Any], item: _Computed) -> Step | None:
        try:
            cls = get(raw.get("type", ""))
        except KeyError:
            item.errors = [
                {
                    "path": "type",
                    "error": f"不支援的步驟類型：{raw.get('type')}",
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
        """第 index 步的輸入（index=0 是原始資料）。"""
        if index <= 0:
            return self._load_source()[0]
        computed = self._compute()
        return computed[index - 1].frame if index - 1 < len(computed) else None

    def frame_at(self, index: int) -> pl.DataFrame | None:
        """第 index 步的輸出；-1 是原始資料。"""
        with self._lock:
            if index < 0:
                return self._load_source()[0]
            computed = self._compute()
            return computed[index].frame if index < len(computed) else None

    # ── 給前端的畫面狀態 ─────────────────────────────────────────

    def state(self) -> dict[str, Any]:
        with self._lock:
            source, files, source_error = self._load_source()
            entries = check_entries(self.input["files"], self.base_dir) if self.base_dir else []
            computed = self._compute()
            steps, previous = [], source
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
                        "errors": item.errors,
                        "run_error": item.run_error,
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
            preview_warnings = (
                []
                if shown_index < 0
                else [w.__dict__ for w in computed[shown_index].warnings[:200]]
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
                "input": {
                    **self.input,
                    "sheets": self._sheet_names(),
                    "entries": entries,
                    "can_load": bool(entries) and all(e["found"] for e in entries),
                    "loaded": source is not None,
                    "stale": self._stale,
                    "error": source_error,
                    "total_rows": sum(f.rows for f in files) if files else None,
                    "sampled": bool(files) and sum(f.rows for f in files) > SAMPLE_THRESHOLD,
                    "sample": {"head": SAMPLE_HEAD, "tail": SAMPLE_TAIL},
                    "rows": source.height if source is not None else None,
                    "columns": _data_columns(source) if source is not None else None,
                    "sources": [f.__dict__ for f in files],
                },
                "output": self.output,
                "steps": steps,
                "selected": selected,
                "preview": {
                    "step": shown_index,
                    "requested": requested,
                    "rows": shown.height if shown is not None else None,
                    "columns": [
                        {"name": n, "dtype": str(t), "system": n.startswith(SYSTEM_PREFIX)}
                        for n, t in (shown.schema.items() if shown is not None else [])
                    ],
                    "warnings": preview_warnings,
                },
                "can_undo": bool(self._undo),
                "can_redo": bool(self._redo),
                "issues": self.issues,
            }

    def rows(self, index: int, offset: int, limit: int) -> dict[str, Any]:
        with self._lock:
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
            frame = self.frame_at(index)
            files = self._load_source()[1]
            sampled = bool(files) and sum(f.rows for f in files) > SAMPLE_THRESHOLD
            if frame is None:
                return {"step": index, "rows": 0, "sampled": sampled, "columns": []}
            return {
                "step": index,
                "rows": frame.height,
                "sampled": sampled,
                "columns": column_summary(frame),
            }

    def find_row(self, index: int, source_file: str, line_no: int) -> int | None:
        """在第 index 步的結果裡找某個來源檔某一行的位置（點警告時跳到那一列）。"""
        with self._lock:
            frame = self.frame_at(index)
            if frame is None or "__line_no" not in frame.columns:
                return None
            hits = (
                frame.with_row_index("__pos")
                .filter((pl.col("__source_file") == source_file) & (pl.col("__line_no") == line_no))
                .get_column("__pos")
            )
            return int(hits[0]) if hits.len() else None

    # ── 匯出與報告 ────────────────────────────────────────────

    def export_suggestion(self) -> tuple[Path, str]:
        """按「處理並匯出資料」時存檔對話框的預設資料夾與檔名。

        上次存的位置優先；否則存在 Recipe（或資料）的資料夾，檔名用 Recipe 檔名或名稱。
        """
        with self._lock:
            base = self.base_dir or Path.home()
            if self.last_export is not None:
                folder = self.last_export.parent
                return (folder if folder.is_dir() else base), self.last_export.name
            if self.recipe_path is not None:
                stem = self.recipe_path.name.removesuffix(".json").removesuffix(".recipe")
            else:
                stem = self.meta.get("name", "")
            stem = INVALID_FILENAME.sub("_", stem).strip(" .") or "result"
            return base, f"{stem}.parquet"

    def require_loaded(self) -> Path:
        """「處理並匯出資料」前的檢查：要先按「載入資料」，而且載入後設定與檔案都沒變。"""
        with self._lock:
            base = self._require_recipe()
            if self._load_source()[0] is None:
                raise SessionError("請先在「讀取資料」按「載入資料」")
            return base

    def export(self, target: Path) -> dict[str, Any]:
        """處理全部資料，存成 target（只在已載入資料時；不會自己讀檔處理）。"""
        with self._lock:
            base = self.require_loaded()
            recipe = parse_recipe(self.recipe_dict())
            _, report = execute(recipe, base, output=target, progress=self.progress)
            self.last_export = target
            return {
                "path": str(target),
                "rows": report.rows,
                "columns": len(report.columns),
                "warnings": report.warning_count,
                "mode": report.mode,
                "manifest": report.manifest,
            }

    def error_report(self) -> str:
        """給 AI 助理（Copilot）看的驗證結果：問題、每一步的列數、警告。"""
        with self._lock:
            lines = [f"Tablade 驗證結果（Recipe 版本 {RECIPE_VERSION}，Tablade {__version__}）"]
            if self.recipe_path:
                lines.append(f"Recipe：{self.recipe_path.name}")
            data = self.recipe_dict()
            result = check_recipe(
                data, self.base_dir or Path.cwd(), dry_run=self.base_dir is not None
            )
            if result.issues:
                lines += ["", "問題：", format_issues(result.issues)]
            else:
                lines += ["", "沒有發現問題。"]
            if result.report:
                lines += ["", "試跑結果：", format_report(result.report, max_warnings=20)]
            lines += ["", f'請依上面的問題修正 Recipe，tablade_recipe 維持 "{RECIPE_VERSION}"。']
            return "\n".join(lines)


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
