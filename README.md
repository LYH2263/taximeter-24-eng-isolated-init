# 14-taximeter（打车计价）

Taximeter — 起步价 + 里程价 + 低速时长费（夜间加价系数）

## 启动

```bash
docker compose up --build
```

| 入口 | 地址 |
| --- | --- |
| 前端 | http://localhost:4300 |
| API | http://localhost:9300 |

## 主链

录行程里程与低速时长 → 拆解车费 → 行程单

## 技术栈

Python 3.12 + FastAPI + SQLite；Vue 3 + Vite + Nginx。

## 隔离初始化自检

`python -m app.iso_check` 会在**临时目录**新建一个空库，连续执行两次初始化，
然后断言：

| 表 | 含义 | 初始化后期望行数 |
| --- | --- | --- |
| `tariff` | 运价 | 1 |
| `trips` | 行程 | 2 |
| `calc_runs` | 打表记录 | 1（第二次初始化不得翻倍） |

随后用**这个临时库里的运价**现算 5 公里 / 2 分钟白天：
11 + (5−3)×2.5 + 2×0.8 = **17.6**，且现算不落库。

### 临时库路径如何传入

库路径由 `DATA_DIR` 决定（`app/config.py` 在启动时读取，库文件固定为
`$DATA_DIR/app.db`；不设置时回退到 `backend/data/app.db`，即“默认库”）。
两种等价传法（均在 `backend/` 目录下执行）：

```bash
# 方式一：命令行参数（推荐，结束后临时库保留在该目录）
python -m app.iso_check --data-dir /tmp/taximeter-iso

# 方式二：环境变量
DATA_DIR=/tmp/taximeter-iso python -m app.iso_check

# 不带参数：自动创建临时目录，自检通过后自动清理（加 --keep 保留）
python -m app.iso_check
```

> 目标目录里若已存在 `app.db`，命令会拒绝运行——自检只在空库上进行。

### 如何核对行数

```bash
sqlite3 /tmp/taximeter-iso/app.db \
  "SELECT 'tariff',COUNT(*) FROM tariff
   UNION ALL SELECT 'trips',COUNT(*) FROM trips
   UNION ALL SELECT 'calc_runs',COUNT(*) FROM calc_runs;"
# 期望：tariff=1, trips=2, calc_runs=1
```

### 与默认库的隔离保证

命令通过 re-exec 一个全新解释器进程、在导入任何 `app.*` 之前注入
`DATA_DIR`，因此初始化只会发生在临时库；父进程只用只读连接核对
`backend/data/app.db` 的前后行数。**可以反向验证**：先人为让默认库多
一行行程，再跑命令——

```bash
# 先初始化默认库并人为插入第 3 行行程
python -c "from app.seed import init_db; init_db()"
python -c "import sqlite3;c=sqlite3.connect('data/app.db');c.execute(\"INSERT INTO trips(label,distance_km,slow_min,night) VALUES ('人为多加的一行',9.9,0,0)\");c.commit();c.close()"
# 再跑隔离自检
python -m app.iso_check --data-dir /tmp/taximeter-iso
```

此时默认库仍是 **trips=3 / calc_runs=1**（多出来的一行原样保留，记录
没有被翻倍），临时库仍是 **trips=2**。隔离不是靠清空默认库假装的。

一键端到端核对（含在默认库上启动服务并检查
`GET /api/health` → `{"ok":true,"project":"taximeter"}`）：

```bash
./scripts/verify_isolation.sh
```

自动化测试：

```bash
pytest app/tests/
```

