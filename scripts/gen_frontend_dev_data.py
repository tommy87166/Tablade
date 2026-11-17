#!/usr/bin/env python3
"""產生前端開發用的假 API 資料（frontend/src/dev/sample.json，不進版控）。

    uv run python scripts/gen_frontend_dev_data.py

用假資料跑一次範例 recipe（最後再加上轉型別、刪除欄、清理文字、取代值、改欄名、保留和排序欄位、
移除重複、群組彙總、樞紐、取消樞紐，看得到這些步驟的表單），
把每一步（含讀取資料、輸出資料）的畫面狀態、前 200 列與型別分頁的內容存起來。之後用一般瀏覽器跑
``npm --prefix frontend run dev`` 時，``src/mock.ts`` 會用這份資料，畫面和真的一樣。
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

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "frontend" / "src" / "dev" / "sample.json"


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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="產生前端開發用的假 API 資料")
    parser.add_argument(
        "--recipe",
        default="sales_detail",
        choices=["sales_detail", "sales_report", "sales_excel"],
        help="用哪一個範例 Recipe（sales_report 是分段報表、sales_excel 讀 Excel）",
    )
    args = parser.parse_args(argv)
    api = Api()
    work = Path(tempfile.mkdtemp(prefix="tablade-dev-"))
    try:
        write_all(work / "data")
        name = f"{args.recipe}.recipe.json"
        shutil.copy(ROOT / "examples" / name, work / name)
        api._session.load_recipe(work / name)
        api._session.load_data()
        session = api._session
        if args.recipe == "sales_detail":
            add_demo_steps(session)
        ready = api.ready()
        states, rows, columns_info = {}, {}, {}
        count = len(session.steps)
        for index in range(-1, count + 1):  # count 是「輸出資料」
            state = api.select_step(index)["state"]
            state["recipe"]["path"] = "（開發用假資料）/sales_detail.recipe.json"
            state["recipe"]["base_dir"] = None
            states[str(index)] = state
            if index < count:
                rows[str(index)] = api.rows(index, 0, 200)
                columns_info[str(index)] = api.column_info(index)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(
        json.dumps(
            {"ready": ready, "states": states, "rows": rows, "columns_info": columns_info},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print(f"已寫入 {TARGET.relative_to(ROOT)}（{TARGET.stat().st_size:,} bytes）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
