"""隔离初始化自检命令（必须以独立进程运行）。

在临时目录中新建一个**空库**，连续执行两次 ``init_db()``，断言：

* tariff（运价）恰好 1 行；
* trips（行程）恰好 2 行；
* calc_runs（打表记录）恰好 1 行 —— 第二次初始化不得让记录翻倍；

随后用**这个临时库**现算一次「5 公里 / 2 分钟 / 白天」，断言总价 17.6，
且现算不落库，calc_runs 仍为 1 行。

隔离方式：临时库目录通过进程环境变量 ``DATA_DIR`` 注入，而
``app/db.py`` 在导入时就解析库路径，因此本命令会 re-exec 出一个全新的
解释器进程、在导入任何 ``app.*`` 之前设好 ``DATA_DIR``。父进程只用标准库
sqlite3 只读地核对**默认库**前后行数，全程不导入 app、不打开默认库写入，
默认库的行程条数与打表记录条数不会被改写。

用法（在 backend/ 目录下）::

    python -m app.iso_check                     # 自动创建临时目录，结束后清理
    python -m app.iso_check --data-dir /tmp/ti  # 指定临时目录（必须为空库），结束后保留
    python -m app.iso_check --keep              # 自动临时目录也保留以便核对
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
CHILD_MARKER = "_TAXIMETER_ISO_CHILD"
TABLES = ("tariff", "trips", "calc_runs")
EXPECTED = {"tariff": 1, "trips": 2, "calc_runs": 1}


def _default_db_path() -> Path:
    """“默认库” = 未设置 DATA_DIR 时 config 的回退路径 backend/data/app.db。

    即使用户通过 DATA_DIR 把本次自检的临时库指到别处，默认库的定义也不
    随之漂移，避免拿临时库跟它自己比对而假装隔离。
    """
    return BACKEND_DIR / "data" / "app.db"


def _row_counts(db_path: Path) -> dict[str, int] | None:
    """只读返回各表行数；库文件不存在返回 None，缺表按 0 计。"""
    if not db_path.exists():
        return None
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        existing = {
            r[0]
            for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        return {t: (con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                    if t in existing else 0)
                for t in TABLES}
    finally:
        con.close()


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="隔离初始化自检：临时空库初始化两次并现算 5km/2min")
    p.add_argument("--data-dir", type=Path, default=None,
                   help="临时库目录（须不含 app.db）；缺省自动创建临时目录")
    p.add_argument("--keep", action="store_true",
                   help="保留自动创建的临时目录（指定 --data-dir 时始终保留）")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)

    # 子进程：DATA_DIR 已就位，直接执行检查逻辑
    if os.environ.get(CHILD_MARKER) == "1":
        return _run_in_child()

    # 父进程：准备临时目录，re-exec 进全新解释器
    auto_dir = args.data_dir is None
    if auto_dir:
        env_dir = os.environ.get("DATA_DIR")
        if env_dir:  # 用户已通过 DATA_DIR 指定临时库目录，视为显式指定、结束后保留
            target = Path(env_dir).resolve()
            auto_dir = False
        else:
            target = Path(tempfile.mkdtemp(prefix="taximeter-iso-"))
    else:
        target = args.data_dir.resolve()
        target.mkdir(parents=True, exist_ok=True)
    db_file = target / "app.db"
    if db_file.exists():
        print(f"[iso-check] 拒绝在非空库上运行：{db_file} 已存在，请换一个空目录", file=sys.stderr)
        return 2

    default_db = _default_db_path()
    before = _row_counts(default_db)
    print(f"[iso-check] 默认库：{default_db}")
    print(f"[iso-check] 默认库运行前行数：{before if before is not None else '（库尚不存在）'}")
    print(f"[iso-check] 临时库目录：{target}")

    env = {**os.environ, "DATA_DIR": str(target), CHILD_MARKER: "1"}
    cmd = [sys.executable, "-m", "app.iso_check", "--data-dir", str(target)]
    proc = subprocess.run(cmd, cwd=BACKEND_DIR, env=env)

    if proc.returncode != 0:
        print(f"[iso-check] 子进程自检失败（exit={proc.returncode}），默认库未被本进程触碰", file=sys.stderr)
        return proc.returncode

    after = _row_counts(default_db)
    print(f"[iso-check] 默认库运行后行数：{after if after is not None else '（库尚不存在）'}")
    if before != after:
        print("[iso-check] 失败：默认库行数发生变化，隔离被破坏！", file=sys.stderr)
        return 1
    print("[iso-check] 默认库行程/运价/打表记录行数与运行前完全一致")

    temp_counts = _row_counts(db_file)
    if temp_counts != EXPECTED:
        print(f"[iso-check] 失败：临时库最终行数 {temp_counts} != {EXPECTED}", file=sys.stderr)
        return 1

    if auto_dir and not args.keep:
        for f in target.iterdir():
            f.unlink()
        target.rmdir()
        print("[iso-check] 临时目录已清理（加 --keep 可保留）")
    else:
        print(f"[iso-check] 临时库保留在：{db_file}（可用 sqlite3 核对行数）")

    print("[iso-check] PASS")
    return 0


def _run_in_child() -> int:
    # 这些导入必须在 DATA_DIR 设置之后 —— 由父进程 re-exec 保证
    from app.config import DATA_DIR  # noqa: E402
    from app.db import DB_PATH, connect  # noqa: E402
    from app.engines.tariff_breakdown import calc_fare  # noqa: E402
    from app.repositories import tariff as tariff_repo  # noqa: E402
    from app.seed import init_db  # noqa: E402

    target = Path(os.environ["DATA_DIR"]).resolve()
    if DATA_DIR.resolve() != target or DB_PATH.parent.resolve() != target:
        print(f"[iso-check] DATA_DIR 未生效：DATA_DIR={DATA_DIR} DB_PATH={DB_PATH}", file=sys.stderr)
        return 1
    if DB_PATH.exists():
        print(f"[iso-check] 临时库应为空，但 {DB_PATH} 已存在", file=sys.stderr)
        return 1
    default_db = _default_db_path()
    if default_db.exists() and DB_PATH.resolve() == default_db.resolve():
        print("[iso-check] 临时库路径与默认库相同，中止", file=sys.stderr)
        return 1

    def counts() -> dict[str, int]:
        con = connect()
        try:
            return {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in TABLES}
        finally:
            con.close()

    init_db()
    first = counts()
    print(f"[iso-check] 第一次初始化后行数：{json.dumps(first, ensure_ascii=False)}")
    if first != EXPECTED:
        print(f"[iso-check] 失败：期望 {EXPECTED}，实际 {first}", file=sys.stderr)
        return 1

    init_db()
    second = counts()
    print(f"[iso-check] 第二次初始化后行数：{json.dumps(second, ensure_ascii=False)}")
    if second != EXPECTED:
        print(f"[iso-check] 失败：两次初始化后行数翻倍/变化：{second}", file=sys.stderr)
        return 1

    # 用该临时库里的运价现算 5 公里 2 分钟白天：11 + (5-3)*2.5 + 2*0.8 = 17.6
    con = connect()
    try:
        t = tariff_repo.get_active(con)
        result = calc_fare(5, 2, False, t)
    finally:
        con.close()
    print(f"[iso-check] 临时库现算 5km/2min/白天：{json.dumps(result, ensure_ascii=False)}")
    if result["total"] != 17.6:
        print(f"[iso-check] 失败：总价应为 17.6，实际 {result['total']}", file=sys.stderr)
        return 1

    final = counts()
    if final != EXPECTED:
        print(f"[iso-check] 失败：现算后行数变化：{final}", file=sys.stderr)
        return 1

    print(f"[iso-check] 临时库 {DB_PATH}：运价 1 行 / 行程 2 行 / 打表记录 1 行，现算 17.6，全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
