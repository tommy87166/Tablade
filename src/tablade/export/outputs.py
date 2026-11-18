"""輸出檔的位置（DESIGN.md §9）：每個階段的 ``output_file`` 寫到 Recipe 所在資料夾底下的 ``output/``，
處理紀錄寫到並排的 ``log/``。

- 中間的階段有填檔名才寫檔；最後一個階段一定寫，沒填時用 Recipe 的名稱（``明細.recipe.json`` → ``明細``）。
- 副檔名照每個階段的 ``output_format`` 補上（``.parquet``、``.csv``；CSV 是 UTF-8 加 BOM）。
- 檔名只能是檔名（不能有路徑字元），主檔名在同一個 Recipe 裡不能重複，寫出的位置不能是任何一個階段的輸入檔。
- 已經存在的檔直接覆蓋。
"""

from __future__ import annotations

import os
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tablade.export.manifest import LOG_DIR, manifest_path
from tablade.i18n import t
from tablade.ingest.sources import expand
from tablade.recipe.schema import Recipe
from tablade.recipe.validator import OUTPUT_EXTENSIONS, output_issues, output_name

OUTPUT_DIR = "output"
_INVALID = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


class OutputError(ValueError):
    """輸出檔名不合法，或寫出的位置是輸入檔。"""


def path_key(path: Path) -> str:
    """比對「是不是同一個檔」用的路徑：統一大小寫與 Unicode 的組合方式。

    macOS 與 Windows 的檔案系統預設不分大小寫，macOS 也不分 NFC／NFD（``Data.csv`` 和 ``data.csv``、
    NFD 的 ``café.csv`` 和 NFC 的 ``café.csv`` 是同一個檔），``Path.resolve()`` 不會把它們統一。
    分大小寫的檔案系統上會把只差大小寫的兩個檔當成同一個（寧可不讓寫，也不要蓋掉輸入檔）。
    """
    text = os.path.normcase(str(path.resolve()))
    return unicodedata.normalize("NFC", text).casefold()


def _file_id(path: Path) -> tuple[int, int] | None:
    """檔案系統上的身分（裝置、inode）；檔案不存在或檔案系統沒有 inode（是 0）時是 None。"""
    try:
        st = path.stat()
    except OSError:
        return None
    return (st.st_dev, st.st_ino) if st.st_ino else None


@dataclass
class InputFiles:
    """所有階段的輸入檔（實際找到的檔）→ 哪個階段讀的。用來確認寫出的位置不是輸入檔。"""

    keys: dict[str, str] = field(default_factory=dict)
    ids: dict[tuple[int, int], str] = field(default_factory=dict)

    def add(self, path: Path, owner: str) -> None:
        self.keys.setdefault(path_key(path), owner)
        file_id = _file_id(path)
        if file_id is not None:
            self.ids.setdefault(file_id, owner)

    def owner(self, path: Path) -> str | None:
        """``path`` 是哪個階段的輸入檔（不是輸入檔時是 None）。"""
        file_id = _file_id(path)
        if file_id is not None and file_id in self.ids:
            return self.ids[file_id]
        return self.keys.get(path_key(path))


def input_files(recipe: dict[str, Any], base_dir: Path) -> InputFiles:
    """Recipe（JSON 內容）每個讀實際檔的階段找到的檔。"""
    found = InputFiles()
    stages = recipe.get("stages") if isinstance(recipe.get("stages"), list) else []
    for stage in stages:
        source = stage.get("input") if isinstance(stage, dict) else None
        if not isinstance(source, dict) or source.get("reader") == "stages":
            continue
        files = source.get("files")
        for entry in files if isinstance(files, list) else []:
            if isinstance(entry, str) and entry:
                for path in expand(entry, base_dir):
                    found.add(path, str(stage.get("name")))
    return found


def overwrites_input(owner: str, file: str) -> str:
    """寫出的位置是輸入檔時的錯誤訊息。"""
    return t(
        f"「{file}」是階段「{owner}」的輸入檔，不能寫到這裡（請換一個輸出檔名）",
        f'"{file}" is an input file of stage "{owner}" and can\'t be '
        "overwritten (use a different output file name)",
    )


@dataclass
class PlannedOutput:
    """一個階段要寫的檔。"""

    file: str
    path: Path
    default: bool
    """最後一個階段沒填檔名、用 Recipe 名稱。"""
    error: str | None = None
    format: str = "parquet"
    """parquet 或 csv（這個階段的 output_format）。"""


def default_output_name(recipe_path: Path | None, fallback: str = "") -> str:
    """最後一個階段沒填檔名時用的主檔名：Recipe 的名稱（不能當檔名的字元換成 _；副檔名照輸出格式另外補）。"""
    if recipe_path is not None:
        stem = recipe_path.name.removesuffix(".json").removesuffix(".recipe")
    else:
        stem = fallback
    return _INVALID.sub("_", stem).strip(" .") or "result"


def plan_outputs(
    recipe: dict[str, Any], base_dir: Path, default: str, out_dir: Path | None = None
) -> list[PlannedOutput | None]:
    """每個階段要寫的檔（不寫檔的階段是 None）：檔名、位置（``out_dir/output/檔名``）與錯誤。

    ``recipe`` 是 Recipe 的 JSON 內容（畫面上還沒存的也可以）；``out_dir`` 預設是 Recipe 所在的資料夾。
    寫出的位置（含處理紀錄 ``log/主檔名.manifest.json``）不能是輸入檔：只差大小寫或 Unicode 組合方式的
    也算同一個檔（``path_key``）。
    """
    stages = recipe.get("stages") if isinstance(recipe.get("stages"), list) else []
    errors: dict[int, str] = {}
    for issue in output_issues(recipe, default):
        match = re.match(r"^stages\[(\d+)\]", issue["path"])
        if match:
            errors.setdefault(int(match[1]), issue["error"])
    inputs = input_files(recipe, base_dir)
    settings = recipe.get("output")
    manifest = not isinstance(settings, dict) or settings.get("manifest", True) is not False
    logs = log_dir(base_dir, out_dir)
    folder = (out_dir or base_dir) / OUTPUT_DIR
    out: list[PlannedOutput | None] = []
    for k, stage in enumerate(stages):
        raw = stage.get("output_file", "") if isinstance(stage, dict) else ""
        raw = raw.strip() if isinstance(raw, str) else ""
        last = k == len(stages) - 1
        if not raw and not last:
            out.append(None)
            continue
        fmt = stage.get("output_format") if isinstance(stage, dict) else None
        fmt = fmt if isinstance(fmt, str) and fmt in OUTPUT_EXTENSIONS else "parquet"
        file = output_name(raw or default, fmt)
        planned = PlannedOutput(file, folder / file, not raw, errors.get(k), fmt)
        if planned.error is None:
            owner = inputs.owner(planned.path)
            if owner is not None:
                planned.error = overwrites_input(owner, f"{OUTPUT_DIR}/{file}")
        if planned.error is None and manifest:
            log = manifest_path(planned.path, logs)
            owner = inputs.owner(log)
            if owner is not None:
                planned.error = overwrites_input(owner, f"{LOG_DIR}/{log.name}")
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
