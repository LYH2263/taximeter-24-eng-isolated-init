import sqlite3

import pytest

from app.isolated_init import IsolatedInitError, run_isolated_init
from app.seed import init_db


def _counts(path):
    conn = sqlite3.connect(path)
    try:
        return {
            t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            for t in ("tariff", "trips", "calc_runs")
        }
    finally:
        conn.close()


def test_double_init_is_idempotent(tmp_path):
    default = tmp_path / "default.db"
    init_db(default)  # 默认库先就位
    isolated = tmp_path / "iso" / "app.db"

    report = run_isolated_init(isolated, default_path=default)

    assert report["counts"] == {"tariff": 1, "trips": 2, "calc_runs": 1}
    assert _counts(isolated) == report["counts"]  # 两次初始化不翻倍
    assert report["fare_check"]["total"] == 17.6  # 临时库现算 5 公里 2 分钟
    assert report["default_db_untouched"] is True
    assert report["default_counts_before"] == report["default_counts_after"]


def test_default_db_keeps_extra_trip_row(tmp_path):
    default = tmp_path / "default.db"
    init_db(default)
    conn = sqlite3.connect(default)
    conn.execute("INSERT INTO trips(label,distance_km,slow_min,night) VALUES ('人工加单',7.0,3,0)")
    conn.commit()
    conn.close()
    assert _counts(default)["trips"] == 3  # 人为让默认库行程多一行

    isolated = tmp_path / "iso.db"
    report = run_isolated_init(isolated, default_path=default)

    assert _counts(default)["trips"] == 3  # 默认库仍保持多一行
    assert _counts(default)["calc_runs"] == 1
    assert _counts(isolated)["trips"] == 2  # 临时库仍是两行
    assert report["default_counts_after"]["trips"] == 3


def test_refuses_to_run_on_default_db(tmp_path):
    default = tmp_path / "default.db"
    init_db(default)
    with pytest.raises(IsolatedInitError):
        run_isolated_init(default, default_path=default)
    assert _counts(default)["trips"] == 2  # 默认库原样未动
