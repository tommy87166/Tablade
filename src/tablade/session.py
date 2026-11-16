"""編輯中的 recipe：狀態由 Python 管理，前端只送指令、顯示畫面狀態（DESIGN.md §16.4）。

- 步驟以原始 dict 保存：參數還不完整（剛新增、正在填）的步驟也能留在清單裡，顯示錯誤，
  預覽算到它的前一步為止，跟 Power Query 的步驟錯誤一樣。
- 每一步的結果依「input ＋ 到這一步為止的步驟」的內容快取；改了第 k 步，k 之前的結果直接沿用。
- 每個會改變 recipe 的操作都先存一份快照，支援 undo / redo。
- 不碰檔案對話框（那是 api.py 的事），所以可以直接用 pytest 測。
"""

from __future__ import annotations

import copy
import glob
import hashlib
import json
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import polars as pl
from pydantic import ValidationError

from . import __version__
from .engine.check import check_recipe, format_issues, format_report
from .engine.run import RowWarning, RunError, apply_step, execute
from .export.parquet import write_parquet
from .ingest.encoding import DecodeError
from .ingest.sources import InputError, SourceFile, load
from .recipe.schema import RECIPE_VERSION, RecipeError, load_json, parse_recipe, pydantic_issues
from .steps import all_steps, get
from .steps.base import SYSTEM_PREFIX, Step

MAX_UNDO = 100
DEFAULT_INPUT = {"id": "src", "pattern": "", "reader": "text_lines", "encoding": "auto"}
DEFAULT_OUTPUT = {"format": "parquet", "path": "output/result.parquet", "keep_system_columns": True}


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


def _data_columns(frame: pl.DataFrame) -> list[str]:
    return [c for c in frame.columns if not c.startswith(SYSTEM_PREFIX)]


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def common_pattern(paths: list[Path]) -> str:
    """多個檔案 → 一個 glob（例如 明細_01.csv、明細_02.csv → 明細_*.csv）。"""
    names = [p.name for p in paths]
    if len(names) == 1:
        return names[0]
    prefix = os.path.commonprefix(names)  # noqa: RUF071  檔名要逐字比對
    suffix = os.path.commonprefix([n[::-1] for n in names])[::-1]  # noqa: RUF071
    if len(prefix) + len(suffix) > min(len(n) for n in names):
        suffix = suffix[len(prefix) + len(suffix) - min(len(n) for n in names) :]
    return f"{prefix}*{suffix}"


class Session:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.recipe_path: Path | None = None
        self.base_dir: Path | None = None
        self.meta: dict[str, str] = {"name": "", "description": ""}
        self.input: dict[str, Any] = dict(DEFAULT_INPUT)
        self.output: dict[str, Any] = dict(DEFAULT_OUTPUT)
        self.steps: list[dict[str, Any]] = []
        self.selected = -1
        self.dirty = False
        self.issues: list[dict[str, Any]] = []
        self._undo: list[dict[str, Any]] = []
        self._redo: list[dict[str, Any]] = []
        self._source: tuple[str, pl.DataFrame | None, list[SourceFile], str | None] | None = None
        self._computed: list[_Computed] = []

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
        return {
            "tablade_recipe": RECIPE_VERSION,
            "name": self.meta.get("name", ""),
            "description": self.meta.get("description", ""),
            "inputs": [self.input],
            "steps": self.steps,
            "output": self.output,
        }

    def new(self) -> None:
        with self._lock:
            self.__init__()

    def load_recipe(self, path: Path) -> list[dict[str, Any]]:
        """開啟 recipe 檔；回傳驗證問題（有問題仍會載入，讓使用者在 UI 修正）。"""
        with self._lock:
            path = path.resolve()
            data = load_json(path)  # JSON 本身壞掉時丟出 RecipeError
            result = check_recipe(data, path.parent)
            if any(i["path"] == "tablade_recipe" for i in result.issues):
                raise RecipeError([i for i in result.issues if i["path"] == "tablade_recipe"])
            inputs = data.get("inputs") if isinstance(data.get("inputs"), list) else []
            self.__init__()
            self.recipe_path, self.base_dir = path, path.parent
            self.meta = {
                "name": str(data.get("name", "")),
                "description": str(data.get("description", "")),
            }
            self.input = {
                **DEFAULT_INPUT,
                **(inputs[0] if inputs and isinstance(inputs[0], dict) else {}),
            }
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
        """存成 recipe 檔；換位置時，input 的 pattern 會改成相對於新位置。"""
        with self._lock:
            target = path or self.recipe_path
            if target is None:
                raise SessionError("還沒有指定 recipe 檔的位置")
            target = target.resolve()
            if self.base_dir and self.input.get("pattern") and target.parent != self.base_dir:
                self.input["pattern"] = self._relocate(self.input["pattern"], target.parent)
                self.output["path"] = self._relocate(self.output["path"], target.parent)
            self.base_dir = target.parent
            target.write_text(
                json.dumps(self.recipe_dict(), ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            self.recipe_path = target
            self.dirty = False
            return target

    def _relocate(self, pattern: str, new_dir: Path) -> str:
        absolute = (
            pattern if Path(pattern).is_absolute() else str((self.base_dir or new_dir) / pattern)
        )
        return Path(os.path.relpath(absolute, new_dir)).as_posix()

    # ── 編輯操作 ──────────────────────────────────────────────

    def open_data(self, paths: list[Path]) -> None:
        """選了資料檔：換掉 input（保留現有步驟，等於把 recipe 套到新資料上）。"""
        if not paths:
            return
        with self._lock:
            paths = [p.resolve() for p in paths]
            folder = paths[0].parent
            if any(p.parent != folder for p in paths):
                raise SessionError("一次只能選同一個資料夾裡的檔案")
            self._change()
            pattern = common_pattern(paths)
            if self.base_dir is None:
                self.base_dir = folder
            self.input["pattern"] = Path(
                os.path.relpath(folder / pattern, self.base_dir)
            ).as_posix()
            if not self.meta.get("name"):
                self.meta["name"] = paths[0].stem
                self.output["path"] = f"output/{paths[0].stem}.parquet"
            self.selected = min(self.selected, len(self.steps) - 1)

    def set_input(self, values: dict[str, Any]) -> None:
        with self._lock:
            self._change()
            for key in ("pattern", "reader", "encoding"):
                if key in values:
                    self.input[key] = values[key]

    def set_output(self, values: dict[str, Any]) -> None:
        with self._lock:
            self._change()
            for key in ("path", "keep_system_columns"):
                if key in values:
                    self.output[key] = values[key]

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
            self.selected = min(self.selected, len(self.steps) - 1)

    def select(self, index: int) -> None:
        with self._lock:
            self.selected = max(-1, min(index, len(self.steps) - 1))

    def _check_index(self, index: int) -> None:
        if not 0 <= index < len(self.steps):
            raise SessionError(f"沒有第 {index} 步")

    # ── 計算 ─────────────────────────────────────────────────

    def _source_key(self) -> str:
        files = []
        if self.base_dir and self.input.get("pattern"):
            full = (
                self.input["pattern"]
                if Path(self.input["pattern"]).is_absolute()
                else str(self.base_dir / self.input["pattern"])
            )
            for p in sorted(glob.glob(full, recursive=True)):
                try:
                    st = os.stat(p)
                    files.append((p, st.st_size, st.st_mtime_ns))
                except OSError:
                    continue
        return _digest({"input": self.input, "base": str(self.base_dir), "files": files})

    def _load_source(self) -> tuple[pl.DataFrame | None, list[SourceFile], str | None]:
        key = self._source_key()
        if self._source and self._source[0] == key:
            return self._source[1], self._source[2], self._source[3]
        frame, files, error = None, [], None
        if not self.input.get("pattern") or self.base_dir is None:
            error = "還沒有開啟資料"
        else:
            try:
                frame, files = load(
                    self.input["pattern"],
                    self.input["reader"],
                    self.input["encoding"],
                    self.base_dir,
                )
            except (InputError, DecodeError, LookupError, pl.exceptions.PolarsError) as err:
                error = str(err)
        self._source = (key, frame, files, error)
        self._computed = []
        return frame, files, error

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
                        else ("ok" if item.frame is not None else "pending")
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
                        "warning_count": item.warning_count,
                        "errors": item.errors,
                        "run_error": item.run_error,
                        "params": raw,
                    }
                )
                if item.frame is not None:
                    previous = item.frame
            selected = self.selected if self.selected < len(self.steps) else len(self.steps) - 1
            # 選取的步驟算不出來時，顯示它之前最後一個算得出來的結果
            shown_index = selected
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
                },
                "input": {
                    **self.input,
                    "error": source_error,
                    "rows": source.height if source is not None else None,
                    "columns": _data_columns(source) if source is not None else None,
                    "files": [f.__dict__ for f in files],
                },
                "output": self.output,
                "steps": steps,
                "selected": selected,
                "preview": {
                    "step": shown_index,
                    "requested": selected,
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

    def export(self, target: Path | None = None) -> dict[str, Any]:
        with self._lock:
            if self.base_dir is None:
                raise SessionError("還沒有開啟資料")
            recipe = parse_recipe(self.recipe_dict())
            path = target or (self.base_dir / self.output["path"])
            frame, report = execute(recipe, self.base_dir, write_output=False)
            write_parquet(frame, path)
            return {
                "path": str(path),
                "rows": frame.height,
                "columns": len(report.columns),
                "warnings": report.warning_count,
            }

    def error_report(self) -> str:
        """給 AI 助理（Copilot）看的驗證結果：問題、每一步的列數、警告。"""
        with self._lock:
            lines = [f"Tablade 驗證結果（recipe 版本 {RECIPE_VERSION}，Tablade {__version__}）"]
            if self.recipe_path:
                lines.append(f"recipe：{self.recipe_path.name}")
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
            lines += ["", f'請依上面的問題修正 recipe，tablade_recipe 維持 "{RECIPE_VERSION}"。']
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
                "schema": schema,
            }
        )
    return items
