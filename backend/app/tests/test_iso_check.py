"""隔离初始化自检命令的测试。

以子进程方式运行 ``python -m app.iso_check``，并在**真实默认库路径**
（backend/data/app.db）上核对：命令前后默认库各表行数逐字节不变。

第二个用例复刻需求场景：先人为让默认库行程多一行，再跑自检命令 ——
默认库必须仍保持多一行（3 行行程 / 1 行打表记录），而临时库仍是 2 行。

夹具会在测试前备份既有的默认库、测试后原样恢复（若默认库本不存在则删除
测试期间创建的文件），因此隔离性不是靠清空默认库假装的。
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[2]
DEFAULT_DATA_DIR = BACKEND_DIR / "data"
DEFAULT_DB = DEFAULT_DATA_DIR / "app.db"
TABLES = ("tariff", "trips", "calc_runs")
EXPECTED_TEMP = {"tariff": 1, "trips": 2, "calc_runs": 1}


@pytest.fixture
def restored_default_db():
    """测试前后保持默认库原样：既有则备份恢复，没有则事后清理。"""
    existed = DEFAULT_DB.exists()
    backup = DEFAULT_DATA_DIR / "app.db.pytest-bak"
    if existed:
        shutil.copy2(DEFAULT_DB, backup)
    try:
        yield
    finally:
        if existed:
            shutil.move(backup, DEFAULT_DB)
        else:
            if DEFAULT_DB.exists():
                DEFAULT_DB.unlink()
            if DEFAULT_DATA_DIR.exists() and not any(DEFAULT_DATA_DIR.iterdir()):
                DEFAULT_DATA_DIR.rmdir()


def _counts(db_path: Path) -> dict[str, int] | None:
    if not db_path.exists():
        return None
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        names = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        return {t: (con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                    if t in names else 0) for t in TABLES}
    finally:
        con.close()


def _run_iso_check(*extra_args: str, data_dir: Path | None = None) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k != "DATA_DIR"}
    args = [sys.executable, "-m", "app.iso_check", *extra_args]
    if data_dir is not None:
        args += ["--data-dir", str(data_dir), "--keep"]
    return subprocess.run(args, cwd=BACKEND_DIR, env=env,
                          capture_output=True, text=True)


def _init_default_db() -> None:
    """用默认 DATA_DIR（backend/data）在全新子进程中初始化默认库。"""
    env = {k: v for k, v in os.environ.items() if k != "DATA_DIR"}
    proc = subprocess.run(
        [sys.executable, "-c", "from app.seed import init_db; init_db()"],
        cwd=BACKEND_DIR, env=env, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


def test_iso_check_on_empty_temp_db_and_default_untouched(restored_default_db, tmp_path):
    before = _counts(DEFAULT_DB)
    temp_dir = tmp_path / "iso"

    proc = _run_iso_check(data_dir=temp_dir)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert _counts(DEFAULT_DB) == before  # 默认库行数不变（本用例中尚不存在）
    assert _counts(temp_dir / "app.db") == EXPECTED_TEMP
    # 现算明细：5 公里 2 分钟白天 = 11 + (5-3)*2.5 + 2*0.8 = 17.6
    assert "17.6" in proc.stdout


def test_double_init_does_not_double_rows(restored_default_db, tmp_path):
    """直接对子进程产出的临时库再核对一次：两次初始化后行数不翻倍。"""
    temp_dir = tmp_path / "iso2"
    proc = _run_iso_check(data_dir=temp_dir)
    assert proc.returncode == 0, proc.stdout + proc.stderr

    con = sqlite3.connect(temp_dir / "app.db")
    try:
        rows = {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                for t in TABLES}
    finally:
        con.close()
    assert rows == EXPECTED_TEMP


def test_extra_trip_in_default_db_persists_while_temp_stays_two(restored_default_db, tmp_path):
    _init_default_db()
    assert _counts(DEFAULT_DB) == {"tariff": 1, "trips": 2, "calc_runs": 1}

    # 人为让默认库行程多一行
    con = sqlite3.connect(DEFAULT_DB)
    con.execute("INSERT INTO trips(label,distance_km,slow_min,night) VALUES ('人为多加的一行',9.9,0,0)")
    con.commit()
    con.close()
    assert _counts(DEFAULT_DB)["trips"] == 3

    temp_dir = tmp_path / "iso3"
    proc = _run_iso_check(data_dir=temp_dir)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    # 默认库：多出来的那行仍在，打表记录没有被翻倍
    assert _counts(DEFAULT_DB) == {"tariff": 1, "trips": 3, "calc_runs": 1}
    # 临时库：始终是种子初始的两行
    assert _counts(temp_dir / "app.db") == {"tariff": 1, "trips": 2, "calc_runs": 1}


def test_temp_db_fare_uses_its_own_tariff_row(restored_default_db, tmp_path):
    """临时库现算必须读取临时库 tariff 行，结果 JSON 总价 17.6。"""
    temp_dir = tmp_path / "iso4"
    proc = _run_iso_check(data_dir=temp_dir)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    con = sqlite3.connect(temp_dir / "app.db")
    con.row_factory = sqlite3.Row
    try:
        tariff = dict(con.execute("SELECT * FROM tariff").fetchone())
        # calc_runs 只有种子 1 条，自检的现算不落库
        run_count = con.execute("SELECT COUNT(*) FROM calc_runs").fetchone()[0]
    finally:
        con.close()
    assert tariff["start_price"] == 11 and tariff["per_km"] == 2.5
    assert run_count == 1
    assert '"total": 17.6' in proc.stdout


def test_health_identifies_project(restored_default_db):
    """health 不依赖库内容，项目标识恒为 taximeter。"""
    sys.path.insert(0, str(BACKEND_DIR))
    from app.main import health
    assert health() == {"ok": True, "project": "taximeter"}
