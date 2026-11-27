import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))


@pytest.fixture(autouse=True)
def workspace(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    monkeypatch.setenv("CP_WORKSPACE", str(ws))
    return ws


@pytest.fixture(scope="session")
def samples(tmp_path_factory):
    import samples as smp
    d = tmp_path_factory.mktemp("samples")
    return smp.make_all(d)
