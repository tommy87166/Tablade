import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load_script(name: str):
    """把 scripts/ 底下的腳本當模組載入（scripts 不是套件）。"""
    path = ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def check_no_data():
    return load_script("check_no_data")


@pytest.fixture(scope="session")
def check_code_policy():
    return load_script("check_code_policy")
