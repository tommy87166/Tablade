#!/usr/bin/env python3
"""產生前端開發用的假 API 資料（frontend/src/dev/sample.json，不進版控）。

    uv run python scripts/gen_frontend_dev_data.py

用假資料跑一次範例 recipe（最後再加上轉型別、刪除欄、清理文字、取代文字、改欄名、保留和排序欄位、
移除重複、群組彙總、樞紐、取消樞紐，看得到這些步驟的表單），
把每一步（含讀取資料、輸出資料）的畫面狀態、前 200 列與型別分頁的內容存起來。之後用一般瀏覽器跑
``npm --prefix frontend run dev`` 時，``src/mock.ts`` 會用這份資料，畫面和真的一樣。

``--recipe all_steps``：每一種步驟都用到一次的展示 Recipe（4 個階段），檢查每個步驟的名稱、說明與表單文字用。
同時把 Recipe 和假資料寫到 tmp/all_steps/（不進版控），用 Tablade 開啟 tmp/all_steps/全部步驟.recipe.json 也看得到。
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path

from tablade.api import Api
from tablade.fixtures import write_all
from tablade.recipe.schema import RECIPE_VERSION
from tablade.steps import all_steps

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "frontend" / "src" / "dev" / "sample.json"
SHOWCASE = ROOT / "tmp" / "all_steps"


def _example_steps(name: str) -> list[dict]:
    data = json.loads((ROOT / "examples" / f"{name}.recipe.json").read_text(encoding="utf-8"))
    return data["stages"][-1]["steps"]


def all_steps_recipe() -> dict:
    """每一種步驟都用到一次的展示 Recipe：品項對照、分段報表、文字明細，最後一個階段附加並對照、彙總。"""
    report = [{**step, "id": f"r{i + 1}"} for i, step in enumerate(_example_steps("sales_report"))]
    # 依條件複製欄位：段落標題那一列的 line 複製到新欄（之後「保留和排序欄位」會拿掉它）
    report.insert(
        1,
        {
            "id": "r0",
            "type": "conditional_copy",
            "conditions": [{"column": "line", "op": "starts_with", "value": "門市："}],
            "column": "line",
            "name": "段落標題",
        },
    )
    detail = [{**step, "id": f"d{i + 1}"} for i, step in enumerate(_example_steps("sales_detail"))]
    detail += [
        {"id": "d9", "type": "parse_number", "columns": ["金額"], "kind": "integer"},
        {
            "id": "d10",
            "type": "parse_date",
            "columns": ["日期"],
            "pattern": "slash",
            "calendar": "roc",
        },
        {"id": "d11", "type": "remove_columns", "columns": ["單號"]},
        {
            "id": "d12",
            "type": "replace_values",
            "columns": ["付款方式"],
            "match": "whole",
            "replacements": [
                {"find": "行動支付", "replace": "行動"},
                {"find": "信用卡", "replace": "刷卡"},
            ],
        },
        {"id": "d13", "type": "rename_columns", "names": {"付款方式": "付款"}},
        {"id": "d14", "type": "dedupe", "columns": ["日期", "門市", "品號"]},
    ]
    return {
        "tablade_recipe": RECIPE_VERSION,
        "name": "全部步驟（展示）",
        "description": "每一種步驟都用到一次，檢查畫面文字用（假資料）",
        "stages": [
            {
                "name": "品項對照",
                "output_format": "parquet",
                "input": {"files": ["data/品項對照.csv"], "reader": "csv", "encoding": "auto"},
                "steps": [{"id": "a1", "type": "trim_clean", "columns": ["品號", "類別"]}],
            },
            {
                "name": "報表",
                "output_format": "parquet",
                "input": {
                    "files": ["data/銷售報表.txt"],
                    "reader": "text_lines",
                    "encoding": "auto",
                },
                "steps": report,
            },
            {
                "name": "文字明細",
                "output_format": "parquet",
                "input": {"files": ["data/明細_*.csv"], "reader": "text_lines", "encoding": "auto"},
                "steps": detail,
            },
            {
                "name": "預設階段",
                "output_format": "parquet",
                "input": {"reader": "stages", "stages": ["報表", "文字明細"]},
                "steps": [
                    {"id": "m1", "type": "merge_files", "name": "全部明細"},
                    {
                        "id": "j1",
                        "type": "join",
                        "right": "品項對照",
                        "on": [{"left": "品號", "right": "品號"}],
                        "columns": ["類別"],
                    },
                    {
                        "id": "g1",
                        "type": "group_by",
                        "by": ["門市", "類別"],
                        "aggregations": [{"column": "金額", "op": "sum", "name": "金額合計"}],
                    },
                    {
                        "id": "p1",
                        "type": "pivot",
                        "index": ["門市"],
                        "on": "類別",
                        "values": "金額合計",
                        "columns": ["文具", "生活用品", "3C"],
                    },
                    {
                        "id": "u1",
                        "type": "unpivot",
                        "columns": ["文具", "生活用品", "3C"],
                        "name_column": "類別",
                        "value_column": "金額",
                        "skip_empty": True,
                    },
                ],
            },
        ],
        "output": {"keep_system_columns": True},
    }


def add_demo_steps(session) -> None:
    """範例後面加上各種欄位步驟，開發畫面才看得到它們的表單。"""
    session.add_step("parse_number", {"column": "金額"}, index=len(session.steps))
    session.add_step("parse_date", {"column": "日期"}, index=len(session.steps))
    session.update_step(
        len(session.steps) - 1, {"columns": ["日期"], "pattern": "slash", "calendar": "roc"}
    )
    session.add_step("remove_columns", {"column": "單號"}, index=len(session.steps))
    session.add_step("trim_clean", {"column": "品名"}, index=len(session.steps))
    session.add_step("replace_values", {"column": "付款方式"}, index=len(session.steps))
    session.update_step(
        len(session.steps) - 1,
        {
            "columns": ["付款方式"],
            "match": "whole",
            "replacements": [
                {"find": "行動支付", "replace": "行動"},
                {"find": "信用卡", "replace": "刷卡"},
            ],
        },
    )
    session.add_step("rename_columns", {"column": "品名"}, index=len(session.steps))
    session.update_step(len(session.steps) - 1, {"names": {"品名": "商品"}})
    session.add_step("select_columns", {}, index=len(session.steps))
    session.update_step(
        len(session.steps) - 1, {"columns": ["日期", "門市", "商品", "付款方式", "金額"]}
    )
    # 移除重複、群組彙總、樞紐、取消樞紐（最後的結果是每個門市、每種付款方式的金額合計）
    session.add_step("dedupe", {}, index=len(session.steps))
    session.add_step("group_by", {"column": "門市"}, index=len(session.steps))
    session.update_step(
        len(session.steps) - 1,
        {
            "by": ["門市", "付款方式"],
            "aggregations": [
                {"column": "金額", "op": "sum", "name": "金額合計"},
                {"column": "日期", "op": "max", "name": "最後日期"},
            ],
        },
    )
    session.add_step("pivot", {"column": "付款方式"}, index=len(session.steps))
    names = session.steps[-1]["columns"]  # 建立步驟時從資料讀出的付款方式
    session.update_step(
        len(session.steps) - 1,
        {"index": ["門市"], "on": "付款方式", "values": "金額合計", "columns": names},
    )
    session.add_step("unpivot", {}, index=len(session.steps))
    session.update_step(
        len(session.steps) - 1,
        {"columns": names, "name_column": "付款方式", "value_column": "金額"},
    )


def _collect(api: Api, session, name: str) -> dict:
    """每個階段的每一步各存一份畫面狀態，鍵是「階段:步驟」（-1 是讀取資料，步驟數是輸出資料）。"""
    ready = api.ready()
    states, rows, columns_info = {}, {}, {}
    for stage in range(len(session.stages)):
        api.select_stage(stage)
        count = len(session.steps)
        for index in range(-1, count + 1):
            state = api.select_step(index)["state"]
            state["recipe"]["path"] = f"（開發用假資料）/{name}"
            state["recipe"]["base_dir"] = None
            key = f"{stage}:{index}"
            states[key] = state
            # 「輸出資料」是完整模式（讀匯出檔）時，表格要的是第「步驟數」步
            if index < count or state["preview"]["step"] == count:
                rows[key] = api.rows(index, 0, 200)
                columns_info[key] = api.column_info(index)
    # 自我檢測實際跑一次（開發畫面的「自我檢測」顯示這個；跟著目前的語言）
    selftest = api.selftest()
    return {
        "ready": ready,
        "states": states,
        "rows": rows,
        "columns_info": columns_info,
        "selftest": selftest,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="產生前端開發用的假 API 資料")
    parser.add_argument(
        "--recipe",
        default="sales_detail",
        choices=[
            "sales_detail",
            "sales_report",
            "sales_excel",
            "sales_combined",
            "sales_zip",
            "all_steps",
        ],
        help=(
            "用哪一個範例 Recipe（sales_report 是分段報表、sales_excel 讀 Excel、"
            "sales_combined 有 4 個階段：附加與結合、sales_zip 讀每個月的 zip 裡的文字檔、"
            "all_steps 每一種步驟都用到一次）"
        ),
    )
    parser.add_argument(
        "--broken",
        action="store_true",
        help="sales_detail 的「刪除欄」改成刪除不存在的欄：看「這一步算不出來」的畫面",
    )
    args = parser.parse_args(argv)
    api = Api()
    work = Path(tempfile.mkdtemp(prefix="tablade-dev-"))
    try:
        write_all(work / "data")
        name = f"{args.recipe}.recipe.json"
        if args.recipe == "all_steps":
            recipe = all_steps_recipe()
            used = {s["type"] for stage in recipe["stages"] for s in stage["steps"]}
            missing = [cls.type_name() for cls in all_steps() if cls.type_name() not in used]
            if missing:
                print(
                    f"展示 Recipe 沒用到：{'、'.join(missing)}（請補進 all_steps_recipe）",
                    file=sys.stderr,
                )
                return 1
            text = json.dumps(recipe, ensure_ascii=False, indent=2) + "\n"
            name = "全部步驟.recipe.json"
            (work / name).write_text(text, encoding="utf-8")
            shutil.rmtree(SHOWCASE, ignore_errors=True)
            shutil.copytree(work, SHOWCASE)
        else:
            shutil.copy(ROOT / "examples" / name, work / name)
        api._session.load_recipe(work / name)
        api._session.load_data()
        session = api._session
        broken = [
            f"{st['name']} 第 {i + 1} 步（{raw['type']}）：{item.run_error or item.errors}"
            for si, st in enumerate(session.stages)
            for i, (raw, item) in enumerate(zip(st["steps"], session._compute(si)[0], strict=True))
            if item.errors or item.run_error
        ]
        if broken:
            print(f"有步驟算不出來：{'、'.join(broken)}", file=sys.stderr)
            if args.recipe == "all_steps":
                return 1
        if args.recipe == "sales_detail":
            add_demo_steps(session)
            if args.broken:
                index = next(
                    i for i, s in enumerate(session.steps) if s["type"] == "remove_columns"
                )
                session.update_step(index, {"columns": ["不存在的欄"]})
        # 中文、英文各存一份（開發畫面切換語言時換用；Python 產生的文字跟著語言）
        outputs = {}
        for lang in ("zh", "en"):
            api.set_language(lang)
            outputs[lang] = _collect(api, session, name)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    for lang, data in outputs.items():
        target = TARGET if lang == "zh" else TARGET.with_name(f"sample.{lang}.json")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        print(f"已寫入 {target.relative_to(ROOT)}（{target.stat().st_size:,} bytes）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
