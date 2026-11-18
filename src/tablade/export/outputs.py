"""輸出檔的位置（DESIGN.md §9）：每個階段的 ``output_file`` 寫到 Recipe 所在資料夾底下的 ``output/``，
處理紀錄寫到並排的 ``log/``。

- 中間的階段有填檔名才寫檔；最後一個階段一定寫，沒填時用 Recipe 的名稱（``明細.recipe.json`` → ``明細.parquet``）。
- 檔名只能是檔名（不能有路徑字元），同一個 Recipe 裡不能重複，寫出的位置不能是任何一個階段的輸入檔。
- 已經存在的檔直接覆蓋。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tablade.export.manifest import LOG_DIR
from tablade.i18n import t
from tablade.ingest.sources import expand
from tablade.recipe.schema import Recipe
from tablade.recipe.validator import output_issues, output_name

OUTPUT_DIR = "output"
_INVALID = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


class OutputError(ValueError):
    """輸出檔名不合法，或寫出的位置是輸入檔。"""


@dataclass
class PlannedOutput:
    """一個階段要寫的檔。"""

    file: str
    path: Path
    default: bool
    """最後一個階段沒填檔名、用 Recipe 名稱。"""
    error: str | None = None


def default_output_name(recipe_path: Path | None, fallback: str = "") -> str:
    """最後一個階段沒填檔名時用的檔名：Recipe 的名稱（不能當檔名的字元換成 _）。"""
    if recipe_path is not None:
        stem = recipe_path.name.removesuffix(".json").removesuffix(".recipe")
    else:
        stem = fallback
    stem = _INVALID.sub("_", stem).strip(" .") or "result"
    return f"{stem}.parquet"


def plan_outputs(
    recipe: dict[str, Any], base_dir: Path, default: str, out_dir: Path | None = None
) -> list[PlannedOutput | None]:
    """每個階段要寫的檔（不寫檔的階段是 None）：檔名、位置（``out_dir/output/檔名``）與錯誤。

    ``recipe`` 是 Recipe 的 JSON 內容（畫面上還沒存的也可以）；``out_dir`` 預設是 Recipe 所在的資料夾。
    """
    stages = recipe.get("stages") if isinstance(recipe.get("stages"), list) else []
    errors: dict[int, str] = {}
    for issue in output_issues(recipe, default):
        match = re.match(r"^stages\[(\d+)\]", issue["path"])
        if match:
            errors.setdefault(int(match[1]), issue["error"])
    inputs: dict[Path, str] = {}
    for stage in stages:
        source = stage.get("input") if isinstance(stage, dict) else None
        if not isinstance(source, dict) or source.get("reader") == "stages":
            continue
        for entry in source.get("files") or []:
            if isinstance(entry, str) and entry:
                for path in expand(entry, base_dir):
                    inputs.setdefault(path.resolve(), str(stage.get("name")))
    folder = (out_dir or base_dir) / OUTPUT_DIR
    out: list[PlannedOutput | None] = []
    for k, stage in enumerate(stages):
        raw = stage.get("output_file", "") if isinstance(stage, dict) else ""
        raw = raw.strip() if isinstance(raw, str) else ""
        last = k == len(stages) - 1
        if not raw and not last:
            out.append(None)
            continue
        file = output_name(raw or default)
        planned = PlannedOutput(file, folder / file, not raw, errors.get(k))
        owner = inputs.get(planned.path.resolve())
        if planned.error is None and owner is not None:
            planned.error = t(
                f"「{OUTPUT_DIR}/{file}」是階段「{owner}」的輸入檔，不能寫到這裡（請換一個輸出檔名）",
                f'"{OUTPUT_DIR}/{file}" is an input file of stage "{owner}" and can\'t be '
                "overwritten (use a different output file name)",
            )
        out.append(planned)
    return out


def resolve_outputs(
    recipe: Recipe, base_dir: Path, default: str, out_dir: Path | None = None
) -> dict[str, Path]:
    """每個要寫檔的階段 → 寫出的位置。檔名不合法、重複，或寫出的位置是輸入檔時丟出 ``OutputError``。"""
    planned = plan_outputs(recipe.model_dump(mode="json"), base_dir, default, out_dir)
    problems = [
        t(f"階段「{stage.name}」：{p.error}", f'Stage "{stage.name}": {p.error}')
        for stage, p in zip(recipe.stages, planned, strict=True)
        if p is not None and p.error
    ]
    if problems:
        raise OutputError(t("；", "; ").join(problems))
    return {
        stage.name: p.path for stage, p in zip(recipe.stages, planned, strict=True) if p is not None
    }


def log_dir(base_dir: Path, out_dir: Path | None = None) -> Path:
    """處理紀錄的資料夾：和 ``output/`` 並排的 ``log/``。"""
    return (out_dir or base_dir) / LOG_DIR
