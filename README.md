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

`python -m app.isolated_init` 在**临时目录新建空库**，连续执行两次初始化并自检：

- 运价 `tariff` 恰好 1 行、行程 `trips` 恰好 2 行、打表记录 `calc_runs` 不翻倍（仍为 1 行）；
- 用该临时库里的运价现算 5 公里 2 分钟，总价为 **17.6**；
- 命令对默认库**只读**（仅统计行数），默认库的行程条数与打表记录条数前后不变——不靠清空默认库来假装隔离；把临时库路径指向默认库会被直接拒绝。

### 运行

```bash
# 本机（在 backend 目录下，仅需 Python 标准库）
cd backend && python3 -m app.isolated_init

# 或容器内
docker compose exec backend python -m app.isolated_init
```

成功退出码为 0，并打印 JSON 报告（临时库路径、临时库行数、默认库前后行数）；失败退出码为 1，原因在 stderr。

### 临时库路径如何传入

优先级：`--db-path` 参数 > 环境变量 `TAXIMETER_ISOLATED_DB` > 自动 `tempfile.mkdtemp`：

```bash
python3 -m app.isolated_init --db-path /tmp/iso/app.db
TAXIMETER_ISOLATED_DB=/tmp/iso/app.db python3 -m app.isolated_init
```

每次运行都会在该路径**新建空库**（已有同名临时文件会先删除再重建）。

### 如何核对行数

命令输出的 JSON 里 `counts` 是临时库行数，`default_counts_before/after` 是默认库前后行数。手工核对：

```bash
# 临时库（路径见报告 isolated_db 字段）
sqlite3 /tmp/iso/app.db "SELECT 'tariff',COUNT(*) FROM tariff UNION ALL SELECT 'trips',COUNT(*) FROM trips UNION ALL SELECT 'calc_runs',COUNT(*) FROM calc_runs;"

# 默认库（本机为 backend/data/app.db，容器内为 /data/app.db）
sqlite3 backend/data/app.db "SELECT COUNT(*) FROM trips; SELECT COUNT(*) FROM calc_runs;"
```

没有 sqlite3 CLI 时可用 Python：

```bash
python3 -c "import sqlite3;c=sqlite3.connect('/tmp/iso/app.db');print({t:c.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0] for t in ('tariff','trips','calc_runs')})"
```

### 验证默认库不被改写

```bash
# 1. 确保默认库已初始化（启动过应用即可，或：python3 -c "from app.seed import init_db; init_db()"）
# 2. 人为让默认库行程多一行
sqlite3 backend/data/app.db "INSERT INTO trips(label,distance_km,slow_min,night) VALUES ('人工加单',7.0,3,0);"
# 3. 再跑自检命令
python3 -m app.isolated_init --db-path /tmp/iso/app.db
# 4. 默认库行程仍是 3 行（保持多出来的一行），临时库行程仍是 2 行
sqlite3 backend/data/app.db "SELECT COUNT(*) FROM trips;"   # 3
sqlite3 /tmp/iso/app.db "SELECT COUNT(*) FROM trips;"       # 2
```

## 健康检查

`GET /api/health` 会对默认库执行 `SELECT 1` 探测，返回 `{"ok": true, "project": "taximeter"}`。

## 测试

```bash
cd backend && python -m pytest
```
