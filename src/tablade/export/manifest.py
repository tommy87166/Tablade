"""處理紀錄（manifest，DESIGN.md §9）：每個輸出檔 ``output/xxx.parquet``（或 ``xxx.csv``）各寫一份 ``log/xxx.manifest.json``。

記錄用了哪份 Recipe（完整內容與雜湊）、每個輸入檔的大小、編碼與 SHA-256、每一步的列數與警告，
以及輸出檔的 SHA-256。日後可以證明「這份輸出是用這份 Recipe，從這些原始檔產生的」，
原始檔被換過也查得出來（SHA-256 會不同）。每一份只記跟這個輸出檔有關的：寫出它的階段，
加上它往上讀到、結合到的階段（輸入檔、步驟、警告），單獨拿一個檔也查得到來源。
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import polars as pl

from tablade import __version__

if TYPE_CHECKING:
    from tablade.engine.run import OutputReport, RunReport
    from tablade.recipe.schema import Recipe

MANIFEST_VERSION = "1.0"
CHUNK = 1 << 20


LOG_DIR = "log"


def manifest_path(output: Path, folder: Path | None = None) -> Path:
    """``明細.parquet``／``明細.csv`` → ``folder/明細.manifest.json``（``folder`` 預設是輸出檔所在資料夾底下的 log/）。"""
    return (folder or output.parent / LOG_DIR) / f"{output.stem}.manifest.json"


def upstream(recipe: Recipe, name: str) -> list[str]:
    """這個階段和它往上讀到、結合到的階段（依 Recipe 的順序）。"""
    by_name = {s.name: s for s in recipe.stages}
    found: set[str] = set()
    todo = [name]
    while todo:
        current = todo.pop()
        if current in found or current not in by_name:
            continue
        found.add(current)
        stage = by_name[current]
        if stage.input.reader == "stages":
            todo += stage.input.stages
        for step in stage.enabled_steps():
            todo += step.stage_refs()
    return [s.name for s in recipe.stages if s.name in found]


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def recipe_content(recipe: Recipe) -> dict[str, Any]:
    return recipe.model_dump(mode="json")


def recipe_sha256(content: dict[str, Any]) -> str:
    """Recipe 內容以排序過的緊湊 JSON 算 SHA-256（同樣的 Recipe 一定得到同樣的值）。"""
    text = json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _warning(w: Any) -> dict[str, Any]:
    item: dict[str, Any] = {"step": w.step}
    if w.stage:
        item["from_stage"] = w.stage
    item["source_file"] = w.source_file
    if w.sheet:
        item["sheet"] = w.sheet
    item["line_no"] = w.line_no
    if w.orig_source_file is not None or w.orig_line_no is not None:
        item["original"] = {
            **({"from_stage": w.orig_stage} if w.orig_stage else {}),
            "source_file": w.orig_source_file,
            **({"sheet": w.orig_sheet} if w.orig_sheet else {}),
            "line_no": w.orig_line_no,
        }
    item["message"] = w.message
    return item


def build_manifest(
    recipe: Recipe, report: RunReport, out: OutputReport, written: Path | None = None
) -> dict[str, Any]:
    """``written`` 是輸出檔實際內容所在的檔（還沒改名的暫存檔；預設是 ``out.path``）。"""
    content = recipe_content(recipe)
    output = Path(out.path)
    data = written or output
    keep = set(upstream(recipe, out.stage))
    steps = [s for s in report.steps if s.stage in keep]
    warnings = [_warning(w) for s in steps for w in s.warnings]
    multi = len(recipe.stages) > 1
    return {
        "tablade_manifest": MANIFEST_VERSION,
        "tool": {"name": "tablade", "version": __version__, "polars": pl.__version__},
        "run_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "mode": report.mode,
        "recipe": {
            "name": recipe.name,
            "sha256": recipe_sha256(content),
            "content": content,
        },
        "inputs": [
            {
                **({"stage": f.stage} if multi and f.stage else {}),
                "file": f.path,
                **({"sheet": f.sheet} if f.sheet else {}),
                "bytes": f.bytes,
                "sha256": f.sha256,
                "encoding": f.encoding,
                "rows": f.rows,
                # zip 裡的檔：bytes、sha256 是解開後的內容，archive 是 zip 本身
                **(
                    {
                        "archive": {
                            "file": f.archive,
                            "bytes": f.archive_bytes,
                            "sha256": f.archive_sha256,
                        }
                    }
                    if f.archive
                    else {}
                ),
                # 寬鬆解碼：解不開、換成 � 的字數與前幾個的行號（只看處理紀錄就知道不是每個字都原樣讀進來）
                **(
                    {
                        "decode": {
                            "replaced": f.replaced,
                            "replaced_lines": list(f.replaced_lines),
                        }
                    }
                    if f.replaced
                    else {}
                ),
            }
            for f in report.inputs
            if f.stage in keep
        ],
        # 讀取方式 zip_lines：沒有符合的檔、略過的 zip
        **(
            {
                "archives_without_matches": [
                    {**({"stage": a["stage"]} if multi else {}), "file": a["file"]}
                    for a in report.skipped_archives
                    if a["stage"] in keep
                ]
            }
            if any(a["stage"] in keep for a in report.skipped_archives)
            else {}
        ),
        "steps": [
            {
                **({"stage": s.stage} if multi else {}),
                "id": s.id,
                "type": s.type,
                "description": s.description,
                "enabled": s.enabled,
                "rows": s.rows,  # 串流執行沒有每一步的列數（null）
                "warning_count": s.warning_count,
            }
            for s in steps
        ],
        **(
            {
                "stage": out.stage,
                "stages": [
                    {"name": st.name, "rows": st.rows, "columns": st.columns}
                    for st in report.stages
                    if st.name in keep and st.name != out.stage
                ],
            }
            if multi
            else {}
        ),
        "warnings": {"count": sum(s.warning_count for s in steps), "items": warnings},
        "output": {
            "file": output.name,
            "format": out.format,
            "rows": out.rows,
            "columns": out.columns,
            "bytes": data.stat().st_size,
            "sha256": file_sha256(data),
        },
    }


def manifest_text(
    recipe: Recipe, report: RunReport, out: OutputReport, written: Path | None = None
) -> str:
    """處理紀錄的內容（JSON）。引擎把它寫成暫存檔，和輸出檔一起改名（``engine/run.py`` 的 ``_Writes``）：
    改名到一半失敗時，輸出檔和處理紀錄都維持上一次的內容。"""
    return (
        json.dumps(build_manifest(recipe, report, out, written), ensure_ascii=False, indent=2)
        + "\n"
    )
