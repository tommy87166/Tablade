"""處理紀錄（manifest，DESIGN.md §9）：輸出 ``xxx.parquet`` 時在旁邊寫 ``xxx.manifest.json``。

記錄用了哪份 Recipe（完整內容與雜湊）、每個輸入檔的大小、編碼與 SHA-256、每一步的列數與警告，
以及輸出檔的 SHA-256。日後可以證明「這份輸出是用這份 Recipe，從這些原始檔產生的」，
原始檔被換過也查得出來（SHA-256 會不同）。
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import polars as pl

from tablade import __version__

if TYPE_CHECKING:
    from tablade.engine.run import RunReport
    from tablade.recipe.schema import Recipe

MANIFEST_VERSION = "1.0"
CHUNK = 1 << 20


def manifest_path(output: Path) -> Path:
    """``明細.parquet`` → ``明細.manifest.json``。"""
    return output.with_name(f"{output.stem}.manifest.json")


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


def build_manifest(recipe: Recipe, report: RunReport, output: Path) -> dict[str, Any]:
    content = recipe_content(recipe)
    warnings = [_warning(w) for s in report.steps for w in s.warnings]
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
            }
            for f in report.inputs
        ],
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
            for s in report.steps
        ],
        **(
            {
                "stages": [
                    {"name": st.name, "rows": st.rows, "columns": st.columns}
                    for st in report.stages[:-1]
                ]
            }
            if multi
            else {}
        ),
        "warnings": {"count": report.warning_count, "items": warnings},
        "output": {
            "file": output.name,
            "rows": report.rows,
            "columns": report.columns,
            "bytes": output.stat().st_size,
            "sha256": file_sha256(output),
        },
    }


def write_manifest(recipe: Recipe, report: RunReport, output: Path) -> Path:
    """寫在輸出檔旁邊；先寫暫存檔再改名，避免中途失敗留下半個檔案。"""
    target = manifest_path(output)
    text = json.dumps(build_manifest(recipe, report, output), ensure_ascii=False, indent=2)
    tmp = target.with_name(f".{target.name}.{secrets.token_hex(4)}.tmp")
    try:
        tmp.write_text(text + "\n", encoding="utf-8")
        os.replace(tmp, target)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return target
