"""验收环境专用：补齐应用 init_db() 未创建、但启动流程必须存在的表。

背景：
  本应用的数据库是「历史库 + 增量迁移」演进来的，init_db() 只建核心表，
  vehicles / vehicle_applications 等表由旧版本库自带。因此用「全新空库」启动时，
  __main__ 里的 seed_vehicles() 会抛 sqlite3.OperationalError: no such table: vehicles
  并导致容器反复重启。生产（炎火云）没有这个问题，因为它的库本来就有这些表。

做法：
  只在验收容器的启动命令里、app.py 之前执行本脚本，用 CREATE TABLE IF NOT EXISTS
  补齐启动流程会直接读写的最少表；其余缺表由 app.py 的 migrate_* 自行 try/except 跳过。
  本脚本不改动生产 Dockerfile / 生产启动方式，也不写入任何业务数据。
"""

import os
import sqlite3

DB_PATH = os.path.join(os.path.dirname(__file__), "data", "water.db")

SCHEMA = """
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


def main():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with sqlite3.connect(DB_PATH, timeout=10) as db:
        db.executescript(SCHEMA)
        db.commit()
    print("[Acceptance] 验收库基础表已补齐（vehicles / vehicle_applications）")


if __name__ == "__main__":
    main()
