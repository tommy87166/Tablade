"""recipe 的 pydantic model。步驟的型別由 registry 動態組成（discriminated union）。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any, Literal, Union

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    field_validator,
)

from tablade.ingest.encoding import SUPPORTED
from tablade.steps import all_steps
from tablade.steps.base import Step

from .validator import check_version, link_issues

RECIPE_VERSION = "1.0"
"""recipe 格式（也是 AI skill）的版本號：MAJOR.MINOR（DESIGN.md §17.6）。"""

READERS: dict[str, dict[str, Any]] = {
    "text_lines": {"columns": ["line"]},
    "csv": {"columns": None},
    "excel": {"columns": None},
    "parquet": {"columns": None},
    "stages": {"columns": None},
}
DEFAULT_STAGE = "預設階段"
"""新的 Recipe 第一個階段的名稱。"""

AnyStep = Annotated[Union[tuple(all_steps())], Field(discriminator="type")]  # noqa: UP007


NonEmptyPath = Annotated[str, StringConstraints(min_length=1)]
StageName = Annotated[str, StringConstraints(min_length=1, max_length=40, pattern=r"^\S(.*\S)?$")]
"""階段名稱（也是引用名稱）：前後不能有空白。"""


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Input(_Model):
    files: list[NonEmptyPath] = Field(
        default_factory=list,
        title="檔案清單",
        description="相對於 Recipe 檔所在的資料夾（例如 data/明細_01.csv），或絕對路徑；可用 * 一次讀多個檔",
    )
    reader: Literal["text_lines", "csv", "excel", "parquet", "stages"] = Field(
        default="text_lines",
        title="讀取方式",
        json_schema_extra={
            "ui_labels": {
                "text_lines": "逐行讀取（爛格式檔案）",
                "csv": "乾淨的 CSV",
                "excel": "Excel 工作表",
                "parquet": "Parquet",
                "stages": "前面階段的輸出",
            }
        },
    )
    encoding: Literal[SUPPORTED] = Field(default="auto", title="編碼", description="Excel 不用設")  # type: ignore[valid-type]
    sheet: str = Field(
        default="",
        title="工作表",
        description=(
            "Excel 用：工作表名稱，空白代表第一個工作表，* 代表全部工作表"
            "（每個工作表各自處理，系統欄位 __sheet 記工作表名稱）。欄名是 A、B、C…，行號是 Excel 的列號"
        ),
    )
    stages: list[StageName] = Field(
        default_factory=list,
        title="要讀的階段",
        description=(
            "讀取方式是「前面階段的輸出」時用：依序讀這些階段的結果，每個來源檔各自是一個來源"
            "（名稱是「@階段／檔名」，系統欄位 __stage 記階段名稱）"
        ),
        json_schema_extra={"ui": "stages"},
    )


class Stage(_Model):
    name: StageName = Field(
        title="階段名稱", description="也是引用名稱：後面的階段用 @名稱 讀這個階段的結果"
    )
    input: Input = Field(default_factory=Input, title="讀取資料")
    steps: list[AnyStep] = Field(default_factory=list, title="步驟")

    def enabled_steps(self) -> list[Step]:
        return [s for s in self.steps if s.enabled]


class Output(_Model):
    format: Literal["parquet"] = Field(default="parquet", title="格式")
    keep_system_columns: bool = Field(
        default=True, title="保留系統欄位（__source_file、__line_no 等）"
    )
    manifest: bool = Field(
        default=True,
        title="同時寫出處理紀錄（manifest）",
        description="在輸出檔旁邊寫一份 xxx.manifest.json：Recipe、輸入檔與輸出檔的 SHA-256、每一步的列數與警告",
    )


class Recipe(_Model):
    tablade_recipe: str = Field(title="Recipe 版本", pattern=r"^\d+\.\d+$")
    name: str = Field(default="", title="名稱")
    description: str = Field(default="", title="說明")
    stages: list[Stage] = Field(
        title="階段",
        min_length=1,
        description="依序執行；中間階段的結果是暫存檔（後面的階段用名稱引用），最後一個階段的結果寫到輸出檔",
    )
    output: Output = Field(title="輸出（最後一個階段的結果）")

    @field_validator("tablade_recipe")
    @classmethod
    def _compatible(cls, value: str) -> str:
        issue = check_version(value, RECIPE_VERSION)
        if issue:
            raise ValueError(issue["error"])
        return value

    def all_steps(self) -> list[Step]:
        """所有階段的步驟（依階段順序）。"""
        return [step for stage in self.stages for step in stage.steps]


class RecipeError(ValueError):
    def __init__(self, issues: list[dict[str, Any]]) -> None:
        self.issues = issues
        super().__init__("；".join(f"{i['path']}: {i['error']}" for i in issues))


def pydantic_issues(err: ValidationError) -> list[dict[str, Any]]:
    """把 pydantic 的錯誤轉成和驗證器相同的格式（路徑去掉 discriminated union 的類型名稱）。"""
    issues = []
    for e in err.errors():
        path, previous = "", None
        for part in e["loc"]:
            if isinstance(previous, int) and isinstance(part, str) and part in _UNION_TAGS:
                previous = part
                continue
            path += f"[{part}]" if isinstance(part, int) else (f".{part}" if path else str(part))
            previous = part
        message = e["msg"].removeprefix("Value error, ")
        issues.append({"path": path or "(Recipe)", "error": message, "severity": "error"})
    return issues


def _union_tags() -> set[str]:
    from tablade.steps.filter import Condition

    tags = {cls.type_name() for cls in all_steps()}
    for member in Condition.__origin__.__args__:  # type: ignore[attr-defined]
        tags.update(member.model_fields["op"].annotation.__args__)
    return tags


_UNION_TAGS = _union_tags()


def parse_recipe(data: dict[str, Any]) -> Recipe:
    try:
        recipe = Recipe.model_validate(data)
    except ValidationError as err:
        raise RecipeError(pydantic_issues(err)) from None
    # 階段之間的關係（名稱不重複、只能引用前面的階段、步驟 id 不重複）：和驗證器同一份規則
    errors = [i for i in link_issues(data, _stage_ref_fields()) if i["severity"] == "error"]
    if errors:
        raise RecipeError(errors)
    return recipe


def _stage_ref_fields() -> dict[str, list[str]]:
    """每種步驟引用其他階段的參數（schema 標了 ``ui: stage``）。"""
    out = {}
    for cls in all_steps():
        names = [
            name
            for name, prop in cls.model_json_schema().get("properties", {}).items()
            if prop.get("ui") == "stage"
        ]
        if names:
            out[cls.type_name()] = names
    return out


def load_json(path: str | Path) -> dict[str, Any]:
    text = Path(path).read_text(encoding="utf-8-sig")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as err:
        raise RecipeError(
            [
                {
                    "path": "(Recipe)",
                    "error": f"不是合法的 JSON：第 {err.lineno} 行第 {err.colno} 欄，{err.msg}",
                    "severity": "error",
                }
            ]
        ) from None
    if not isinstance(data, dict):
        raise RecipeError(
            [{"path": "(Recipe)", "error": "Recipe 必須是 JSON 物件", "severity": "error"}]
        )
    return data
