"""验收环境专用：补齐应用 init_db() 未创建、但启动流程必须存在的历史表。

背景：
  本应用的数据库是「历史库 + 增量迁移」演进来的，app.py 的 init_db() 只建核心表，
  v2 时代的历史表来自仓库里的 平台开发/backend/migrate_v2.sql，生产库本身就有这些表。
  用「全新空库」启动时，启动链会直接崩（历史上是 no such table: vehicles，
  R33 之后又多了 migrate_reagent_qc() 里的 UPDATE reagent_inventory 等无容错语句）。

做法：
  在验收容器启动命令里、app.py 之前执行本脚本：
  1) 从同目录 migrate_v2.sql 里提取所有 CREATE TABLE IF NOT EXISTS 语句并逐条执行
     —— 只取建表语句，跳过 ALTER TABLE / INSERT（那些假设表已存在、且会重复插数据）；
     单条失败只记录不中断，保证幂等。
  2) 再用最小 DDL 兜底两张启动必需表，防止 migrate_v2.sql 缺失。
  其余缺表由 app.py 的 migrate_* 自行 try/except 跳过。
  本脚本不改动生产源码、不改业务逻辑，也不写入任何业务数据。
"""

import os
import re
import sqlite3

HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(HERE, "data", "water.db")
V2_SQL = os.path.join(HERE, "migrate_v2.sql")

FALLBACK_SCHEMA = """
CREATE TABLE IF NOT EXISTS vehicles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plate_no TEXT,
    model TEXT,
    seats INTEGER,
    status TEXT DEFAULT 'idle',
    current_mileage REAL,
    next_maintenance_mileage REAL,
    last_maintenance_at TEXT
);

CREATE TABLE IF NOT EXISTS vehicle_applications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    vehicle_id INTEGER,
    applicant_id INTEGER,
    start_at TEXT,
    end_at TEXT,
    destination TEXT,
    reason TEXT,
    status TEXT
);
"""


def _iter_statements(sql):
    """Yield complete SQL statements, ignoring `--` comment lines."""
    buf = ""
    for line in sql.splitlines(keepends=True):
        if line.lstrip().startswith("--"):
            continue
        buf += line
        if sqlite3.complete_statement(buf):
            yield buf.strip()
            buf = ""


def main():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    created, skipped = [], []
    with sqlite3.connect(DB_PATH, timeout=10) as db:
        if os.path.isfile(V2_SQL):
            with open(V2_SQL, "r", encoding="utf-8") as handle:
                sql = handle.read()
            for stmt in _iter_statements(sql):
                if not stmt.upper().startswith("CREATE TABLE IF NOT EXISTS"):
                    continue
                match = re.match(r"CREATE TABLE IF NOT EXISTS\s+(\w+)", stmt, re.I)
                name = match.group(1) if match else "?"
                try:
                    db.execute(stmt)
                    created.append(name)
                except sqlite3.Error as exc:
                    skipped.append("%s (%s)" % (name, exc))
        else:
            skipped.append("migrate_v2.sql 不存在")
        db.executescript(FALLBACK_SCHEMA)
        db.commit()
    print("[Acceptance] v2 历史表已补齐 %d 张：%s" % (len(created), ", ".join(created)))
    if skipped:
        print("[Acceptance] 跳过：%s" % "; ".join(skipped))


if __name__ == "__main__":
    main()
