"""量預覽的記憶體與時間：實際操作 ``Session``（docs/memory-improve.md 的驗收）。Windows、macOS、Linux 都能跑。

用法（在 repo 的資料夾裡；先 ``uv sync``）::

    uv run python docs/memory-improve-measure.py
    uv run python docs/memory-improve-measure.py --files 20,40    # 記憶體比較小的電腦
    uv run python docs/memory-improve-measure.py --threads 4       # 限制 Polars 的執行緒數

做的事：

1. 讀系統的記憶體（總量、可用），印出這台電腦的樣本額度（``engine.run.sample_budget``）。
2. 用 ``tablade.fixtures.write_many_zips`` 產生假資料（很多個 zip，每個裡面一個 cp950 的文字檔），
   放在系統的暫存資料夾，跑完就刪掉。不讀、不寫 repo 裡的任何檔。
3. 每種檔數、每種 Recipe（6 步與 12 步）各開一個新的行程，照畫面上的操作走一次：
   載入資料及預覽 → 一步一步插入步驟（每插一步看一次畫面）→ 連續點 30 次不同的步驟（每次看畫面、
   取第一頁的資料）→ 改畫面上那一步的參數。量每段的時間與整個行程的峰值。
4. 印出一份報告（也存成暫存資料夾裡的 ``result.md``）：把報告整段貼回來就可以。
   報告裡沒有電腦名稱、使用者名稱或路徑。

不連網路、不裝套件。記憶體的數字用 ``tablade.usage.process_memory()``（和 App 狀態列顯示的一樣）。
"""

from __future__ import annotations

import argparse
import gc
import json
import os
import platform
import random
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

MB = 1 << 20
GB = 1 << 30
CLICKS = 30
MEMBERS = "*.TXT"


def recipes() -> dict[int, list[dict]]:
    from tablade.fixtures import MANY_ZIPS_COLUMNS

    split = {
        "type": "split_delimited",
        "column": "line",
        "delimiter": "|",
        "columns": list(MANY_ZIPS_COLUMNS),
    }

    def keep_out(column: str, value: str) -> dict:
        return {
            "type": "filter",
            "conditions": [{"column": column, "op": "not_equals", "value": value}],
        }

    date = {"type": "split_column", "column": "日期", "delimiter": "/", "names": ["年", "月", "日"]}
    rename = {"type": "rename_columns", "names": {"門市代號": "店號"}}
    text = ["門市", "品名", "通路", "付款方式", "單號", "店員", "備註"]
    return {
        6: [
            split,
            {"type": "remove_columns", "columns": ["店員"]},
            rename,
            keep_out("備註", "退貨"),
            keep_out("數量", "12"),
            date,
        ],
        12: [
            split,
            {"type": "trim_clean", "columns": text},
            keep_out("備註", "退貨"),
            {"type": "parse_number", "columns": ["數量", "單價", "金額"]},
            {
                "type": "replace_values",
                "columns": ["通路"],
                "replacements": [{"find": "門市", "replace": "店面"}],
            },
            {"type": "filter", "conditions": [{"column": "數量", "op": "lt", "value": 12}]},
            date,
            keep_out("月", "13"),
            {"type": "trim_clean", "columns": ["年", "月", "日"]},
            keep_out("店員", "無"),
            rename,
            keep_out("付款方式", "禮券"),
        ],
    }


# ── 子行程：照畫面上的操作走一次 ─────────────────────────────────


def child(folder: Path, count: int) -> None:
    from tablade.session import Session
    from tablade.usage import process_memory

    peak = 0
    stop = threading.Event()

    def watch() -> None:
        nonlocal peak
        while not stop.is_set():
            memory = process_memory()
            if memory is not None:
                peak = max(peak, memory.current)
            time.sleep(0.02)

    def now() -> float:
        memory = process_memory()
        return round(memory.current / GB, 2) if memory is not None else -1.0

    out: dict = {"steps": count, "before": now()}
    threading.Thread(target=watch, daemon=True).start()
    s = Session()
    s.new(folder / "measure.recipe.json")
    s.add_files(sorted(folder.glob("data/*.zip")))
    s.set_input({"reader": "zip_lines", "encoding": "cp950", "members": MEMBERS})
    start = time.perf_counter()
    s.load_data()
    s.state()
    entry = s._sources[s.stage["uid"]]
    plan = entry[3]
    out.update(
        load_seconds=round(time.perf_counter() - start, 1),
        budget_mb=round(plan.budget / MB) if plan and plan.budget else None,
        rows=entry[1].height,
        sample_mb=round(entry[1].estimated_size() / MB),
        loaded=now(),
    )

    start = time.perf_counter()
    for raw in recipes()[count]:  # 一步一步插在最後面，每插一步畫面就看那一步
        i = s.add_step(raw["type"], index=len(s.steps))
        s.update_step(i, {k: v for k, v in raw.items() if k != "type"})
        s.select(i)
        s.state()
        s.rows(i, 0, 100)
    out.update(add_seconds=round(time.perf_counter() - start, 2), added=now(), add_peak=peak)

    rng = random.Random(0)
    seconds = []
    for _ in range(CLICKS):
        target = rng.randrange(len(s.steps))
        start = time.perf_counter()
        s.select(target)
        s.state()
        s.rows(target, 0, 100)
        seconds.append(time.perf_counter() - start)
    gc.collect()
    out.update(
        click_mean=round(sum(seconds) / len(seconds), 2),
        click_max=round(max(seconds), 2),
        clicked=now(),
    )

    # 改畫面上那一步的參數：最後一個篩選，換一個要排除的值
    shown = max(i for i, raw in enumerate(recipes()[count]) if raw["type"] == "filter")
    condition = dict(s.steps[shown]["conditions"][0], value="（不會出現的值）")
    s.select(shown)
    s.state()
    s.rows(shown, 0, 100)
    start = time.perf_counter()
    s.update_step(shown, {"conditions": [condition]})
    state = s.state()
    s.rows(shown, 0, 100)
    assert state["steps"][shown]["status"] == "ok", state["steps"][shown]
    out["edit_seconds"] = round(time.perf_counter() - start, 2)

    stop.set()
    memory = process_memory()
    out["peak"] = round(max(peak, memory.peak if memory is not None else 0) / GB, 2)
    out["add_peak"] = round(out["add_peak"] / GB, 2)
    print("RESULT " + json.dumps(out))


# ── 主程式 ─────────────────────────────────────────────────


def run_child(folder: Path, count: int, threads: int | None) -> dict | None:
    env = {**os.environ, "PYTHONUTF8": "1"}
    if threads:
        env["POLARS_MAX_THREADS"] = str(threads)
    done = subprocess.run(
        [sys.executable, __file__, "--child", str(folder), "--steps", str(count)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    for line in done.stdout.splitlines():
        if line.startswith("RESULT "):
            return json.loads(line[7:])
    print(done.stderr[-2000:], file=sys.stderr)
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description="量預覽的記憶體與時間（docs/memory-improve.md）")
    parser.add_argument(
        "--files", default="40,80,160", help="幾個 zip，用逗號分開（預設 40,80,160）"
    )
    parser.add_argument("--rows", type=int, default=120_000, help="每個檔幾行（預設 12 萬）")
    parser.add_argument("--threads", type=int, default=None, help="限制 Polars 的執行緒數")
    parser.add_argument("--child", default=None, help=argparse.SUPPRESS)
    parser.add_argument("--steps", type=int, default=6, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.child:
        child(Path(args.child), args.steps)
        return
    if args.threads:
        os.environ["POLARS_MAX_THREADS"] = str(args.threads)

    import polars as pl

    import tablade
    from tablade.engine.run import sample_budget
    from tablade.fixtures import write_many_zips
    from tablade.ingest import sources
    from tablade.usage import system_memory

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    threads = pl.thread_pool_size()
    memory = system_memory()
    counts = [int(n) for n in args.files.split(",") if n.strip()]
    report: list[str] = []

    def say(text: str = "") -> None:
        report.append(text)
        print(text, flush=True)

    def note(text: str) -> None:
        print(f"… {text}", flush=True)

    say("## 預覽記憶體的實測（實際操作 Session）")
    say()
    say(f"- 系統：{platform.system()} {platform.release()}（{platform.machine()}）")
    say(f"- CPU：{os.cpu_count()} 個邏輯核心；Polars 用 {threads} 條執行緒")
    say(
        f"- 版本：Tablade {tablade.__version__}、Polars {pl.__version__}、Python {platform.python_version()}"
    )
    if memory is None:
        say("- 系統記憶體：**讀不到**")
    else:
        say(f"- 系統記憶體：總量 {memory[0] / GB:.1f} GB，可用 {memory[1] / GB:.1f} GB")
    say(f"- 這台電腦現在的樣本額度：{sample_budget() / MB:.0f} MB")
    say(f"- 假資料：每個 zip {args.rows:,} 行；檔數 {'、'.join(map(str, counts))}")
    say()

    work = Path(tempfile.mkdtemp(prefix="tablade-measure-"))
    results: dict[tuple[int, int], dict | None] = {}
    try:
        for n in counts:
            folder = work / f"files-{n}"
            note(f"產生 {n} 個 zip")
            write_many_zips(folder / "data", n, args.rows)
            for steps in (6, 12):
                note(f"{n} 個檔、{steps} 步")
                results[(n, steps)] = run_child(folder, steps, args.threads)
            shutil.rmtree(folder, ignore_errors=True)
    finally:
        shutil.rmtree(work / "data", ignore_errors=True)
        sources.clear_staging()

    say(
        "| 檔數 | 步數 | 額度 | 樣本行數 | 樣本大小 | 載入 | 載入後 | 插完步驟 | 插步驟時的峰值 "
        f"| 點另一步（{CLICKS} 次平均／最久） | 改參數 | 整個的峰值 |"
    )
    say("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for (n, steps), r in results.items():
        if r is None:
            say(f"| {n} | {steps} | 失敗 | | | | | | | | | |")
            continue
        say(
            f"| {n} | {steps} | {r['budget_mb']} MB | {r['rows']:,} | {r['sample_mb']} MB "
            f"| {r['load_seconds']} 秒 | {r['loaded']} GB | {r['add_seconds']} 秒 | {r['add_peak']} GB "
            f"| {r['click_mean']}／{r['click_max']} 秒 | {r['edit_seconds']} 秒 | {r['peak']} GB |"
        )
    say()
    base = next((r["before"] for r in results.values() if r), None)
    say(f"- 行程在載入之前：{base} GB")
    again = system_memory()
    if again is not None:
        say(f"- 跑完後系統可用記憶體：{again[1] / GB:.1f} GB")
    target = work / "result.md"
    target.write_text("\n".join(report) + "\n", encoding="utf-8")
    print()
    print(f"報告也存在：{target}（貼報告的內容就好，不用貼這個路徑）")


if __name__ == "__main__":
    main()
