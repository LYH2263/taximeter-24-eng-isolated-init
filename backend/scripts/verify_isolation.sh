#!/usr/bin/env bash
# 端到端核对“隔离初始化”：
#
#   1. 在默认库（backend/data/app.db）正常初始化，人为让行程多一行（3 行）；
#   2. 运行 `python -m app.iso_check --data-dir <临时目录>`；
#   3. 断言默认库仍是 3 行行程、打表记录 1 条（没有被命令改写/翻倍）；
#      临时库仍是 2 行行程、1 条运价、1 条打表记录；
#   4. 用默认库启动服务，GET /api/health 必须 {"ok":true,"project":"taximeter"}。
#
# 隔离不是靠清空默认库假装的：第 3 步断言时默认库明摆着是 3 行。
# 脚本开始前会备份既有默认库，结束后原样恢复（原本没有则清理掉）。
#
# 用法：
#   ./scripts/verify_isolation.sh                 # 用 python3
#   PYTHON=/tmp/venv/bin/python ./scripts/verify_isolation.sh
set -euo pipefail

PY="${PYTHON:-python3}"
BACKEND_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$BACKEND_DIR"

DEFAULT_DB="$BACKEND_DIR/data/app.db"
TMP_DIR="$(mktemp -d /tmp/taximeter-iso-e2e-XXXXXX)"
BACKUP="$BACKEND_DIR/data/app.db.verify-bak"
PORT=19300
BASE="http://127.0.0.1:$PORT/api/health"

cleanup() {
  [ -n "${SERVER_PID:-}" ] && kill "$SERVER_PID" 2>/dev/null || true
  rm -rf "$TMP_DIR"
  if [ -f "$BACKUP" ]; then
    mv "$BACKUP" "$DEFAULT_DB"
    echo "[verify] 已恢复运行前的默认库：$DEFAULT_DB"
  elif [ -f "$DEFAULT_DB" ]; then
    rm -f "$DEFAULT_DB"
    rmdir "$BACKEND_DIR/data" 2>/dev/null || true
    echo "[verify] 默认库运行前不存在，已清理测试期间创建的库"
  fi
}
trap cleanup EXIT

counts() { "$PY" - "$1" <<'EOF'
import sqlite3, sys
path = sys.argv[1]
con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
got = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
print(" ".join(f"{t}={con.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0] if t in got else 0}"
               for t in ("tariff", "trips", "calc_runs")))
con.close()
EOF
}
assert_eq() { # actual expected message
  if [ "$1" != "$2" ]; then echo "[verify] 断言失败：$3（实际='$1' 期望='$2'）" >&2; exit 1; fi
}

echo "[verify] Python: $($PY --version 2>&1)"
mkdir -p "$BACKEND_DIR/data"
[ -f "$DEFAULT_DB" ] && cp "$DEFAULT_DB" "$BACKUP"

echo "[verify] 1) 初始化默认库，并人为插入第 3 行行程"
env -u DATA_DIR "$PY" -c "from app.seed import init_db; init_db()"
"$PY" - "$DEFAULT_DB" <<'EOF'
import sqlite3, sys
con = sqlite3.connect(sys.argv[1])
con.execute("INSERT INTO trips(label,distance_km,slow_min,night) VALUES ('人为多加的一行',9.9,0,0)")
con.commit(); con.close()
EOF
DEFAULT_BEFORE="$(counts "$DEFAULT_DB")"
assert_eq "$DEFAULT_BEFORE" "tariff=1 trips=3 calc_runs=1" "默认库种子后行程应为 3 行"
echo "[verify]    默认库：$DEFAULT_BEFORE"

echo "[verify] 2) 在临时空库上运行隔离自检（连续两次初始化）"
"$PY" -m app.iso_check --data-dir "$TMP_DIR" --keep

echo "[verify] 3) 核对两边行数"
DEFAULT_AFTER="$(counts "$DEFAULT_DB")"
TEMP_AFTER="$(counts "$TMP_DIR/app.db")"
assert_eq "$DEFAULT_AFTER" "tariff=1 trips=3 calc_runs=1" "默认库行数被改写（隔离失败）"
assert_eq "$TEMP_AFTER" "tariff=1 trips=2 calc_runs=1" "临时库行数不是种子初始值"
echo "[verify]    默认库：$DEFAULT_AFTER  ← 人为多出的一行仍在，打表记录未翻倍"
echo "[verify]    临时库：$TEMP_AFTER  ← 仍是初始两行"

echo "[verify] 4) 默认库上启动服务，核对 /api/health"
env -u DATA_DIR "$PY" -m uvicorn app.main:app --host 127.0.0.1 --port "$PORT" \
  >/tmp/taximeter-verify-uvicorn.log 2>&1 &
SERVER_PID=$!
HEALTH=""
for _ in $(seq 1 30); do
  if HEALTH="$(curl -fsS --max-time 2 "$BASE" 2>/dev/null)"; then break; fi
  sleep 0.5
done
echo "[verify]    /api/health -> $HEALTH"
assert_eq "$HEALTH" '{"ok":true,"project":"taximeter"}' "health 响应不符合预期"

echo "[verify] PASS：隔离初始化、行数不翻倍、临时库现算 17.6、health=ok/taximeter 全部通过"
