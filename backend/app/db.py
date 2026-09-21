import sqlite3
from pathlib import Path
from app.config import DATA_DIR, DB_FILENAME

DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / DB_FILENAME

def connect(db_path=None) -> sqlite3.Connection:
    """连接数据库；缺省用默认库 DB_PATH，传入 db_path 则连接指定库（用于隔离初始化）。"""
    path = Path(db_path) if db_path is not None else DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn
