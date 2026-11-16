import json

import polars as pl
import pytest

from tablade.cli import main
from tablade.export.parquet import write_parquet
from tablade.fixtures import report_lines, write_all, write_simple_csv
from tablade.selftest import run_selftest


def test_write_parquet_roundtrip_and_no_temp_left(tmp_path):
    frame = pl.DataFrame({"品名": ["折疊傘", None], "金額": ["1,000", "(500)"]})
    target = write_parquet(frame, tmp_path / "out" / "x.parquet")

    assert pl.read_parquet(target).equals(frame)
    assert [p.name for p in target.parent.iterdir()] == ["x.parquet"]


def test_write_parquet_failure_leaves_nothing(tmp_path, monkeypatch):
    def boom(self, path, *args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(pl.DataFrame, "write_parquet", boom)
    with pytest.raises(OSError):
        write_parquet(pl.DataFrame({"a": ["1"]}), tmp_path / "x.parquet")
    assert list(tmp_path.iterdir()) == []


def test_cli_convert(tmp_path, capsys):
    src = write_simple_csv(tmp_path / "明細.csv", n=12)
    out = tmp_path / "明細.parquet"

    assert main(["convert", str(src), str(out)]) == 0
    assert pl.read_parquet(out).height == 12
    assert "cp950" in capsys.readouterr().out


def test_cli_selftest_writes_json(tmp_path, capsys):
    out = tmp_path / "result.json"
    assert main(["selftest", "--out", str(out)]) == 0
    result = json.loads(out.read_text(encoding="utf-8"))
    assert result["ok"] is True
    assert "自我檢測：通過" in capsys.readouterr().out


def test_selftest_reports_each_check():
    result = run_selftest()
    assert result["ok"], result
    assert len(result["checks"]) == 8
    assert all(c["purpose"] and c["ran"] for c in result["checks"])
    assert result["checks"][-1]["detail"] == "2 個 cp950 檔 → 合併成 70 列，與預期一致"
    assert result["versions"]["polars"].startswith("2.")


def test_selftest_marks_checks_after_a_failure_as_not_run(monkeypatch):
    import tablade.selftest as selftest

    def broken(*args, **kwargs):
        raise OSError("暫存資料夾不能寫入")

    monkeypatch.setattr(selftest, "write_simple_csv", broken)
    result = run_selftest()
    assert not result["ok"] and len(result["checks"]) == 8
    assert result["checks"][0]["ran"] and not result["checks"][0]["ok"]
    assert not any(c["ran"] for c in result["checks"][1:])


def test_cli_version(capsys):
    assert main(["version"]) == 0
    assert "polars=2." in capsys.readouterr().out


def test_fixtures_are_deterministic(tmp_path):
    a = [p.read_bytes() for p in write_all(tmp_path / "a", seed=7)]
    b = [p.read_bytes() for p in write_all(tmp_path / "b", seed=7)]
    assert a == b


def test_report_has_sections():
    lines = report_lines(sections=2, entries=3)
    assert sum(line.startswith("門市：") for line in lines) == 2
    assert sum("小計" in line for line in lines) == 2
    assert "" in lines  # 空行要保留，行號才會對得上
