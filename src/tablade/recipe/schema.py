"""recipe 的 pydantic model。步驟的型別由 registry 動態組成（discriminated union）。"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Annotated, Any, Literal, Union

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    field_validator,
    model_validator,
)

from tablade.i18n import plural, t
from tablade.ingest.encoding import SUPPORTED
from tablade.steps import all_steps
from tablade.steps.base import Step

from .validator import check_version, link_issues

RECIPE_VERSION = "1.0"
"""recipe 格式（也是 AI skill）的版本號：MAJOR.MINOR（DESIGN.md §17.6）。"""

READERS: dict[str, dict[str, Any]] = {
    "text_lines": {"columns": ["line"]},
    "zip_lines": {"columns": ["line"]},
    "csv": {"columns": None},
    "excel": {"columns": None},
    "parquet": {"columns": None},
    "stages": {"columns": None},
}
DEFAULT_STAGE = "預設階段"  # i18n-ignore：階段名稱會寫進 Recipe
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
    reader: Literal["text_lines", "zip_lines", "csv", "excel", "parquet", "stages"] = Field(
        default="text_lines",
        title="讀取方式",
        json_schema_extra={
            "ui_labels": {
                "text_lines": "逐行讀取（爛格式檔案）",
                "zip_lines": "zip 裡的文字檔（逐行讀取）",
                "csv": "乾淨的 CSV",
                "excel": "Excel 工作表",
                "parquet": "Parquet",
                "stages": "前面階段的輸出",
            }
        },
    )
    members: str = Field(
        default="",
        title="zip 裡的檔",
        description=(
            "讀取方式是「zip 裡的文字檔」時用（必填）：檔案清單是 zip，這裡寫 zip 裡要讀的檔的相對路徑，"
            "例如 2026*/HEADER*.TXT。比對完整路徑、不分大小寫；* 不跨過 /，** 是任何層數的資料夾"
            "（**/HEADER*.TXT 找到 zip 裡任何地方的 HEADER*.TXT）。zip 裡每個符合的檔是一個來源，"
            "名稱是「zip 的路徑/zip 裡的路徑」"
        ),
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
            "（名稱是「@階段／檔名」，系統欄位 __stage 記階段名稱；名稱一樣的算同一個來源，"
            "依第一次出現的位置接起來）"
        ),
        json_schema_extra={"ui": "stages"},
    )

    @model_validator(mode="after")
    def _members_only_for_zip(self) -> Input:
        if self.reader == "zip_lines" and not self.members.strip():
            raise ValueError(
                t(
                    "讀取方式是「zip 裡的文字檔」時要填「zip 裡的檔」（members，例如 2026*/HEADER*.TXT）",
                    'With "Text files in zip", fill in "Files in the zip" (members, '
                    "for example 2026*/HEADER*.TXT)",
                )
            )
        if self.reader != "zip_lines" and self.members:
            raise ValueError(
                t(
                    "只有讀取方式是「zip 裡的文字檔」（zip_lines）時才能填 members",
                    'members can be set only when the reader is "Text files in zip" (zip_lines)',
                )
            )
        return self


class Stage(_Model):
    name: StageName = Field(
        title="階段名稱", description="也是引用名稱：後面的階段用 @名稱 讀這個階段的結果"
    )
    input: Input = Field(default_factory=Input, title="讀取資料")
    steps: list[AnyStep] = Field(default_factory=list, title="步驟")
    output_file: str = Field(
        default="",
        title="輸出檔名",
        description=(
            "只填檔名（例如 明細），副檔名照 output_format 自動補上；存到 Recipe 所在資料夾底下的"
            " output 資料夾；中間的階段有填才寫檔，最後一個階段空白時用 Recipe 的名稱"
        ),
    )
    output_format: Literal["parquet", "csv"] = Field(
        title="輸出格式",
        description=(
            "parquet：保留型別，給其他程式用；csv：UTF-8（含 BOM），Excel 可以直接打開。"
            "每個階段都要填，沒寫檔的中間階段也一樣"
        ),
    )

    def enabled_steps(self) -> list[Step]:
        return [s for s in self.steps if s.enabled]


class Output(_Model):
    keep_system_columns: bool = Field(
        default=True, title="保留系統欄位（__source_file、__line_no 等）"
    )
    manifest: bool = Field(
        default=True,
        title="同時寫出處理紀錄（manifest）",
        description=(
            "每個輸出檔在 log 資料夾各寫一份 xxx.manifest.json："
            "Recipe、輸入檔與輸出檔的 SHA-256、每一步的列數與警告"
        ),
    )


class Recipe(_Model):
    tablade_recipe: str = Field(title="Recipe 版本", pattern=r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
    name: str = Field(default="", title="名稱")
    description: str = Field(default="", title="說明")
    stages: list[Stage] = Field(
        title="階段",
        min_length=1,
        description=(
            "依序執行；後面的階段用名稱引用前面階段的結果。最後一個階段的結果一定寫檔，"
            "中間的階段有填輸出檔名才寫（都寫到 output 資料夾）"
        ),
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
        super().__init__(t("；", "; ").join(f"{i['path']}: {i['error']}" for i in issues))


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
        issues.append({"path": path or "(Recipe)", "error": _message(e), "severity": "error"})
    return issues


_MESSAGES = {
    "missing": "必填，請填寫",
    "extra_forbidden": "不認得的欄位",
    "string_type": "應該是文字",
    "int_type": "應該是整數",
    "bool_type": "應該是 true / false",
    "list_type": "應該是清單",
    "dict_type": "應該是物件",
    "model_type": "應該是物件",
    "model_attributes_type": "應該是物件",
    "float_type": "應該是數字",
    "string_pattern_mismatch": "格式不符",
}
_MESSAGES_EN = {
    "missing": "Required",
    "extra_forbidden": "Unknown field",
    "string_type": "Should be text",
    "int_type": "Should be an integer",
    "bool_type": "Should be true / false",
    "list_type": "Should be a list",
    "dict_type": "Should be an object",
    "model_type": "Should be an object",
    "model_attributes_type": "Should be an object",
    "float_type": "Should be a number",
    "string_pattern_mismatch": "Invalid format",
}
"""``_MESSAGES`` 的英文（模組最上層不能用 t() 算好，用到時才依語言選）。"""


def _message(error: Any) -> str:
    """pydantic 常見的錯誤換成中文（畫面上的步驟錯誤、開啟 Recipe 的問題清單）；其他的照原樣。"""
    kind, ctx = error.get("type"), error.get("ctx") or {}
    if kind in _MESSAGES:
        return t(_MESSAGES[kind], _MESSAGES_EN[kind])
    if kind == "string_too_short":
        return (
            t("不能是空白", "Cannot be blank")
            if ctx.get("min_length") == 1
            else t(
                f"至少 {ctx.get('min_length')} 個字",
                f"At least {plural(ctx.get('min_length'), 'character', 'characters')}",
            )
        )
    if kind == "string_too_long":
        return t(
            f"最多 {ctx.get('max_length')} 個字",
            f"At most {plural(ctx.get('max_length'), 'character', 'characters')}",
        )
    if kind == "too_short":
        return t(
            f"至少要有 {ctx.get('min_length')} 項",
            f"Needs at least {plural(ctx.get('min_length'), 'item', 'items')}",
        )
    if kind == "too_long":
        return t(
            f"最多 {ctx.get('max_length')} 項",
            f"At most {plural(ctx.get('max_length'), 'item', 'items')}",
        )
    if kind == "literal_error":
        allowed = _choices(str(ctx.get("expected", "")))
        return t(
            f"不支援的值（可用：{'、'.join(allowed)}）",
            f"Unsupported value (allowed: {', '.join(allowed)})",
        )
    if kind == "union_tag_not_found":  # 例如條件沒有 op、步驟沒有 type
        name = _choices(str(ctx.get("discriminator", "")))[0]
        return t(f"缺少 {name}", f"Missing {name}")
    if kind == "union_tag_invalid":
        name = _choices(str(ctx.get("discriminator", "")))[0]
        allowed = _choices(str(ctx.get("expected_tags", "")))
        tag = ctx.get("tag")
        return t(
            f"不支援的 {name}：{tag}（可用：{'、'.join(allowed)}）",
            f"Unsupported {name}: {tag} (allowed: {', '.join(allowed)})",
        )
    if kind == "greater_than_equal":
        return t(f"不能小於 {ctx.get('ge')}", f"Cannot be less than {ctx.get('ge')}")
    if kind == "greater_than":
        return t(f"必須大於 {ctx.get('gt')}", f"Must be greater than {ctx.get('gt')}")
    if kind == "less_than_equal":
        return t(f"不能大於 {ctx.get('le')}", f"Cannot be greater than {ctx.get('le')}")
    if kind == "less_than":
        return t(f"必須小於 {ctx.get('lt')}", f"Must be less than {ctx.get('lt')}")
    return str(error["msg"]).removeprefix("Value error, ")


_QUOTED = re.compile(r"'((?:[^'\\]|\\.)*)'")


def _choices(text: str) -> list[str]:
    """pydantic 列選項的文字（``'a', 'b' or 'c'``）→ ``["a", "b", "c"]``；中文用「、」重新接起來。"""
    quoted = _QUOTED.findall(text)
    if quoted:
        return quoted
    return [part.strip() for part in re.split(r",| or ", text) if part.strip()] or [text]


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
                    "error": t(
                        f"不是合法的 JSON：第 {err.lineno} 行第 {err.colno} 欄，{err.msg}",
                        f"Not valid JSON (line {err.lineno}, column {err.colno}): {err.msg}",
                    ),
                    "severity": "error",
                }
            ]
        ) from None
    if not isinstance(data, dict):
        raise RecipeError(
            [
                {
                    "path": "(Recipe)",
                    "error": t("Recipe 必須是 JSON 物件", "The Recipe must be a JSON object"),
                    "severity": "error",
                }
            ]
        )
    return data
