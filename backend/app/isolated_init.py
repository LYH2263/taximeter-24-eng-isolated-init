"""隔离初始化自检命令。

在临时目录新建空库并连续执行两次初始化，然后断言：
- 运价 tariff 恰好 1 行、行程 trips 恰好 2 行、打表记录 calc_runs 不翻倍（仍为 1 行）；
- 用该临时库里的运价现算 5 公里 2 分钟，总价为 17.6；
- 默认库的行程条数与打表记录条数在命令前后不变（命令对默认库只读，绝不改写，
  更不靠清空默认库来假装隔离）。

用法：
    python -m app.isolated_init                     # 自动在临时目录新建空库
    python -m app.isolated_init --db-path /tmp/x/app.db
    TAXIMETER_ISOLATED_DB=/tmp/x/app.db python -m app.isolated_init

临时库路径优先级：--db-path 参数 > 环境变量 TAXIMETER_ISOLATED_DB > 自动临时目录。
退出码：0 自检通过；1 自检失败（stderr 有原因）。
"""
import argparse
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

from app.db import DB_PATH, connect
from app.engines.tariff_breakdown import calc_fare
from app.repositories import tariff as tariff_repo
from app.seed import init_db

ENV_VAR = "TAXIMETER_ISOLATED_DB"
EXPECTED_COUNTS = {"tariff": 1, "trips": 2, "calc_runs": 1}
CHECK_DISTANCE_KM = 5
CHECK_SLOW_MIN = 2
CHECK_EXPECT_TOTAL = 17.6


class IsolatedInitError(RuntimeError):
    """自检失败。"""


def _check(cond, msg):
    if not cond:
        raise IsolatedInitError(msg)


def table_counts(conn):
    return {
        t: conn.execute(f"SELECT COUNT(*) c FROM {t}").fetchone()["c"]
        for t in ("tariff", "trips", "calc_runs")
    }


def read_default_counts(default_path=DB_PATH):
    """以只读模式统计默认库行数；库不存在（或尚未建表）时返回 None。绝不创建/写入默认库。"""
    path = Path(default_path)
    if not path.exists():
        return None
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        return table_counts(conn)
    except sqlite3.OperationalError:
        return None
    finally:
        conn.close()


def run_isolated_init(db_path, default_path=DB_PATH):
    """在 db_path 新建空库做双初始化自检，返回报告 dict；失败抛 IsolatedInitError。"""
    db_path = Path(db_path)
    default_path = Path(default_path)
    _check(
        db_path.resolve() != default_path.resolve(),
        f"隔离库路径指向了默认库 {default_path}，拒绝执行（隔离初始化不得触碰默认库）",
    )

    default_before = read_default_counts(default_path)

    # 新建空库：保证两次初始化作用于全新的空库
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()
    init_db(db_path)
    init_db(db_path)  # 第二次初始化必须幂等，不得让任何表翻倍

    conn = connect(db_path)
    try:
        counts = table_counts(conn)
        active_tariff = tariff_repo.get_active(conn)
    finally:
        conn.close()

    _check(counts["tariff"] == EXPECTED_COUNTS["tariff"],
           f"运价应恰为 {EXPECTED_COUNTS['tariff']} 行，实际 {counts['tariff']} 行")
    _check(counts["trips"] == EXPECTED_COUNTS["trips"],
           f"行程应恰为 {EXPECTED_COUNTS['trips']} 行，实际 {counts['trips']} 行")
    _check(counts["calc_runs"] == EXPECTED_COUNTS["calc_runs"],
           f"打表记录两次初始化后应为 {EXPECTED_COUNTS['calc_runs']} 行（不翻倍），实际 {counts['calc_runs']} 行")

    fare = calc_fare(CHECK_DISTANCE_KM, CHECK_SLOW_MIN, False, active_tariff)
    _check(fare["total"] == CHECK_EXPECT_TOTAL,
           f"临时库现算 {CHECK_DISTANCE_KM} 公里 {CHECK_SLOW_MIN} 分钟应为 {CHECK_EXPECT_TOTAL}，实际 {fare['total']}")

    default_after = read_default_counts(default_path)
    _check(default_before == default_after,
           f"默认库行数被改写：之前 {default_before}，之后 {default_after}")

    return {
        "isolated_db": str(db_path),
        "counts": counts,
        "fare_check": {
            "distance_km": CHECK_DISTANCE_KM,
            "slow_min": CHECK_SLOW_MIN,
            "total": fare["total"],
        },
        "default_db": str(default_path),
        "default_counts_before": default_before,
        "default_counts_after": default_after,
        "default_db_untouched": True,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="python -m app.isolated_init",
        description="在临时目录新建空库做隔离初始化自检，不改写默认库。",
    )
    parser.add_argument("--db-path",
                        help=f"临时库路径（也可用环境变量 {ENV_VAR}；缺省则自动新建临时目录）")
    args = parser.parse_args(argv)

    db_path = args.db_path or os.environ.get(ENV_VAR)
    if db_path is None:
        db_path = Path(tempfile.mkdtemp(prefix="taximeter_isolated_")) / "app.db"

    try:
        report = run_isolated_init(db_path)
    except IsolatedInitError as exc:
        print(f"[isolated-init] 自检失败: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print("[isolated-init] OK：临时库双初始化幂等，现算 5km/2min=17.6，默认库行数未变。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
