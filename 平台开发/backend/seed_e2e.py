#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
全平台试运行数据生成脚本 (Seed E2E)
根据《试运行数据集方案.md》生成全链路自洽测试数据。

执行方式:
    cd backend && python3 seed_e2e.py

输出:
    [SeedE2E] 全平台试运行数据生成完成，共 N 条记录
"""

import os
import sys
import json
import shutil
import subprocess
from datetime import datetime, timedelta

# =============================================================================
# 0. 路径设置
# =============================================================================
# 支持中文字符路径
BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BACKEND_DIR, 'data')
DB_PATH = os.path.join(DATA_DIR, 'water.db')

# 读取 app.py 中的 DB_PATH（验证一致性）
sys.path.insert(0, BACKEND_DIR)
import importlib.util
spec = importlib.util.spec_from_file_location("app_module", os.path.join(BACKEND_DIR, "app.py"))
app_module = importlib.util.module_from_spec(spec)
# 只加载常量部分，不运行整个app
app_module.DB_PATH = DB_PATH

# =============================================================================
# 1. 数据库工具函数
# =============================================================================
import sqlite3

def get_db():
    db = sqlite3.connect(DB_PATH, timeout=10, check_same_thread=False)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("PRAGMA busy_timeout=5000")
    return db

def verify_db_path():
    """确认 DB_PATH 与 app.py 中的一致"""
    print(f"[SeedE2E] DB_PATH = {DB_PATH}")
    if not os.path.exists(DB_PATH):
        print(f"[SeedE2E] ERROR: 数据库文件不存在: {DB_PATH}")
        sys.exit(1)
    print(f"[SeedE2E] 数据库文件大小: {os.path.getsize(DB_PATH) / 1024 / 1024:.1f} MB")

# =============================================================================
# 2. 备份
# =============================================================================
def backup_db():
    ts = datetime.now().strftime('%Y%m%d-%H%M%S')
    backup_path = os.path.join(DATA_DIR, f'water-backup-{ts}.db')
    shutil.copy2(DB_PATH, backup_path)
    print(f"[SeedE2E] 数据库已备份至: {backup_path}")

# =============================================================================
# 3. 清空数据（按外键顺序，从子表到父表）
# =============================================================================
TABLES_IN_DELETE_ORDER = [
    'timeline_events',
    'inventory_logs',
    'spare_part_requests',
    'spare_parts_inventory',
    'inspection_tasks',
    'inspection_plans',
    'inspection_scheme_items',
    'inspection_schemes',
    'work_orders',
    'alerts',
    'data_arrival',
    'water_level_checks',
    'maintenance_plans',
    'hotline_events',
    'device_shadows',
    'sensor_data',
    'weather_data',
    'user_sites',
    'sites',
]

def clear_tables(db):
    print("[SeedE2E] 清空所有数据表（保留结构）...")
    # 先禁用外键约束检查，再删除
    db.execute("PRAGMA foreign_keys=OFF")
    for tbl in TABLES_IN_DELETE_ORDER:
        try:
            db.execute(f"DELETE FROM {tbl}")
        except Exception as e:
            print(f"[SeedE2E]   ⚠ 清空 {tbl} 时忽略: {e}")
    db.execute("PRAGMA foreign_keys=ON")
    db.commit()
    print("[SeedE2E]   ✓ 全部清空完成")

# =============================================================================
# 4. 确保迁移列存在（flow_type / flow_status 等）
# =============================================================================
def ensure_migration_columns(db):
    """确保 alerts 表有 flow_type / flow_status 等字段"""
    cols = [row[1] for row in db.execute("PRAGMA table_info(alerts)").fetchall()]
    migrations = [
        "ALTER TABLE alerts ADD COLUMN flow_type TEXT DEFAULT 'manual'",
        "ALTER TABLE alerts ADD COLUMN flow_status TEXT DEFAULT 'pending_review'",
        "ALTER TABLE alerts ADD COLUMN tracking_count INTEGER DEFAULT 0",
        "ALTER TABLE alerts ADD COLUMN urge_count INTEGER DEFAULT 0",
        "ALTER TABLE alerts ADD COLUMN last_urged_at TEXT",
        "ALTER TABLE alerts ADD COLUMN related_order_no TEXT",
        "ALTER TABLE alerts ADD COLUMN response_deadline TEXT",
    ]
    for sql in migrations:
        col_name = sql.split()[3].split('(')[0]  # 提取列名
        # 去掉 DEFAULT 关键词
        col_name = col_name.split('_DEFAULT')[0]
        if col_name not in cols:
            try:
                db.execute(sql)
                print(f"[SeedE2E]   + 新增列: {col_name}")
            except Exception:
                pass  # 列可能已存在

# =============================================================================
# 5. 插入数据
# =============================================================================

# ---------- 时间基准 ----------
# 所有时间集中在 2026-06-21 ~ 2026-06-22
T_BASE = datetime(2026, 6, 22, 15, 0, 0)  # 基准时间

def fmt(dt):
    return dt.strftime('%Y-%m-%d %H:%M')

def fmt_date(dt):
    return dt.strftime('%Y-%m-%d')

# =============================================================================
# 5.1 sites
# =============================================================================
SITES_DATA = [
    (1, 'S01001', '邓埠', 'hydrology', '南昌市南昌县', 28.45, 115.89, '张建国', '13900001001', 'online'),
    (2, 'W02001', '乌井水库', 'water_level', '南昌市湾里区', 28.72, 115.73, '黎明', '13900002001', 'online'),
    (3, 'R03001', '罗亭', 'rainfall', '南昌市新建区', 28.60, 115.78, '王刚', '13900003001', 'online'),
    (4, 'G04001', '省水文局', 'groundwater', '南昌市西湖区', 28.68, 115.89, '赵洪', '13900004001', 'online'),
    (5, 'E05001', '万家埠', 'evaporation', '南昌市安义县', 28.84, 115.55, '张建国', '13900005001', 'offline'),
    (6, 'Y06001', '江桥', 'station_yard', '南昌市南昌县', 28.52, 116.01, '黎明', '13900006001', 'online'),
]

def insert_sites(db):
    print("[SeedE2E] 插入 sites (6条)...")
    for row in SITES_DATA:
        sid, code, name, stype, district, lat, lng, manager, phone, status = row
        db.execute(
            """INSERT INTO sites (id, code, name, type, district, lat, lng, manager, phone, status, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (sid, code, name, stype, district, lat, lng, manager, phone, status,
             '2026-06-21 00:00:00')
        )

# =============================================================================
# 5.2 inspection_schemes + inspection_scheme_items
# =============================================================================
def insert_inspection_schemes(db):
    print("[SeedE2E] 插入 inspection_schemes (1条) + items (4条)...")
    cur = db.execute(
        """INSERT INTO inspection_schemes (id, site_id, period, name, status, updated_at)
           VALUES (?,?,?,?,?,?)""",
        (1, 1, 'daily', '邓埠站日常巡检方案', 'active', '2026-06-22 08:00:00')
    )
    items = [
        (1, 1, 'equipment', '水位计外观检查', 1, 1),
        (2, 1, 'communication', '数据传输状态检查', 2, 1),
        (3, 1, 'power', '供电电压测量', 3, 1),
        (4, 1, 'environment', '环境温湿度记录', 4, 1),
    ]
    for item in items:
        db.execute(
            """INSERT INTO inspection_scheme_items (id, scheme_id, category, check_item, sort_order, is_required)
               VALUES (?,?,?,?,?,?)""",
            item
        )

# =============================================================================
# 5.3 sensor_data
# =============================================================================
def insert_sensor_data(db):
    """最新数据(7条) + 每站20条历史 + 站点3突增演示(4条) = 131条
    注意：先插入历史数据，再插入最新数据，以确保 MAX(id) 取到最新值。
    """
    print("[SeedE2E] 插入 sensor_data (历史120条 + 最新7条)...")

    import random
    random.seed(42)  # 固定种子保证可重现

    _id = 0

    # ---- 先插入历史数据（每站20条，IDs 1-120） ----
    history_specs = {
        1: {'metric': 'water_level', 'unit': 'm', 'base': 12.0, 'variance': 0.5},   # 邓埠
        2: {'metric': 'water_level', 'unit': 'm', 'base': 48.5, 'variance': 0.3},   # 乌井
        3: {'metric': 'precipitation', 'unit': 'mm', 'base': 0.5, 'variance': 1.0}, # 罗亭
        4: {'metric': 'groundwater_level', 'unit': 'm', 'base': 6.8, 'variance': 0.2},  # 省局
        5: {'metric': 'evaporation', 'unit': 'mm', 'base': 4.0, 'variance': 0.5},   # 万家埠
        6: {'metric': 'temperature', 'unit': '°C', 'base': 27.0, 'variance': 1.5},  # 江桥
    }

    for site_id in range(1, 7):
        spec = history_specs[site_id]
        for i in range(20):
            _id += 1
            hour_offset = 20 - i
            # 站点5(万家埠)是离线站，历史数据截止到2026-06-21 06:00（早于最新数据08:00）
            if site_id == 5:
                base_time = datetime(2026, 6, 18, 12, 0, 0)
                t = base_time + timedelta(hours=hour_offset * 1.5 + random.uniform(-0.3, 0.3))
            else:
                base_time = datetime(2026, 6, 19, 12, 0, 0)
                t = base_time + timedelta(hours=hour_offset * 3.5 + random.uniform(-0.5, 0.5))
            trend = (20 - i) / 20 * 0.3
            value = round(spec['base'] + random.uniform(-spec['variance'], spec['variance']) - trend, 2)
            value = max(0, value)
            db.execute(
                "INSERT INTO sensor_data (id, site_id, metric, value, unit, recorded_at) VALUES (?,?,?,?,?,?)",
                (_id, site_id, spec['metric'], value, spec['unit'], fmt(t))
            )

    # ---- 站点3(罗亭)数据突增演示记录（在最新数据之前插入，体现告警场景） ----
    ro亭_spike = [
        (3, 'precipitation', 1.2, 'mm', '2026-06-22 13:50'),
        (3, 'precipitation', 85.0, 'mm', '2026-06-22 13:55'),
        (3, 'precipitation', 0.5, 'mm', '2026-06-22 14:00'),
        (3, 'precipitation', 0.0, 'mm', '2026-06-22 14:05'),
    ]
    for site_id, metric, value, unit, recorded_at in ro亭_spike:
        _id += 1
        db.execute(
            "INSERT INTO sensor_data (id, site_id, metric, value, unit, recorded_at) VALUES (?,?,?,?,?,?)",
            (_id, site_id, metric, value, unit, recorded_at)
        )

    # ---- 后插入最新数据（7条，IDs 121-127 + spike记录后偏移，确保MAX(id)取到这些） ---
    # 注意：站点1的 flow 需先于 water_level 插入，确保 MAX(id) 取到 water_level
    latest_records = [
        (1, 'flow', 423.18, 'm³/s', '2026-06-22 15:00'),       # id=121
        (1, 'water_level', 12.35, 'm', '2026-06-22 15:00'),     # id=122, MAX(id) for site 1
        (2, 'water_level', 48.62, 'm', '2026-06-22 14:55'),     # id=123
        (3, 'precipitation', 0.0, 'mm', '2026-06-22 15:00'),    # id=124
        (4, 'groundwater_level', 6.82, 'm', '2026-06-22 14:50'),# id=125
        (5, 'evaporation', 4.2, 'mm', '2026-06-21 08:00'),      # id=126
        (6, 'temperature', 27.3, '°C', '2026-06-22 15:00'),     # id=127
    ]
    for site_id, metric, value, unit, recorded_at in latest_records:
        _id += 1
        db.execute(
            "INSERT INTO sensor_data (id, site_id, metric, value, unit, recorded_at) VALUES (?,?,?,?,?,?)",
            (_id, site_id, metric, value, unit, recorded_at)
        )

    return _id  # 返回最后一个ID

# =============================================================================
# 5.4 device_shadows (11条)
# =============================================================================
def insert_device_shadows(db):
    print("[SeedE2E] 插入 device_shadows (11条)...")
    devices = [
        (1, 1, 'DP-S01001-SENS', '邓埠水位计', 'sensor', 'online', 12.1, '2026-06-22 14:59'),
        (2, 1, 'DP-S01001-FLOW', '邓埠流量计', 'sensor', 'online', 11.9, '2026-06-22 14:58'),
        (3, 2, 'WJ-W02001-SENS', '乌井水位计', 'sensor', 'online', 12.3, '2026-06-22 14:55'),
        (4, 2, 'WJ-W02001-RAIN', '乌井雨量筒', 'sensor', 'online', 11.7, '2026-06-22 14:50'),
        (5, 3, 'LT-R03001-RAIN', '罗亭雨量筒', 'sensor', 'online', 11.5, '2026-06-22 14:45'),
        (6, 4, 'SJ-G04001-SENS', '省局地下水位计', 'sensor', 'online', 12.0, '2026-06-22 14:50'),
        (7, 5, 'WJP-E05001-PAN', '万家埠蒸发皿', 'sensor', 'offline', 10.8, '2026-06-21 07:00'),
        (8, 5, 'WJP-E05001-COMM', '万家埠通信模块', 'comm', 'offline', 11.0, '2026-06-21 07:00'),
        (9, 6, 'JQ-Y06001-MON', '江桥站房监测器', 'monitor', 'online', 12.2, '2026-06-22 14:55'),
        (10, 6, 'JQ-Y06001-CAM', '江桥视频设备', 'camera', 'online', 12.0, '2026-06-22 14:50'),
        (11, 6, 'JQ-Y06001-PWR', '江桥备用电源', 'power', 'online', 11.3, '2026-06-22 14:00'),
    ]
    for row in devices:
        did, site_id, code, name, dtype, status, voltage, last_time = row
        db.execute(
            """INSERT INTO device_shadows (id, site_id, device_code, device_name, device_type, status, voltage, last_data_time)
               VALUES (?,?,?,?,?,?,?,?)""",
            (did, site_id, code, name, dtype, status, voltage, last_time)
        )

# =============================================================================
# 5.5 spare_parts_inventory (5条)
# =============================================================================
def insert_parts_inventory(db):
    print("[SeedE2E] 插入 spare_parts_inventory (5条)...")
    parts = [
        (1, 'BJ-SENSOR-001', '水位传感器', 'sensor', 3, 2, '个'),
        (2, 'BJ-COMM-002', '通信模块', 'communication', 1, 2, '个'),
        (3, 'BJ-PWR-003', '蓄电池', 'power', 5, 3, '块'),
        (4, 'BJ-PWR-004', '太阳能板', 'power', 2, 1, '块'),
        (5, 'BJ-CABLE-005', '信号线', 'cable', 50, 10, '米'),
    ]
    for row in parts:
        pid, code, name, cat, qty, min_qty, unit = row
        db.execute(
            """INSERT INTO spare_parts_inventory (id, part_code, part_name, category, quantity, min_quantity, unit, updated_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (pid, code, name, cat, qty, min_qty, unit, '2026-06-22 12:00:00')
        )

# =============================================================================
# 5.6 inventory_logs (3-4条)
# =============================================================================
def insert_inventory_logs(db):
    print("[SeedE2E] 插入 inventory_logs (4条)...")
    logs = [
        (1, 1, 'in', 10, 'purchase', 0, '系统管理员', '常规补库', '2026-06-21 09:00:00'),
        (2, 2, 'out', 1, 'request', 1, '张建国', '万家埠通信模块更换', '2026-06-22 10:00:00'),
        (3, 4, 'in', 3, 'purchase', 0, '系统管理员', '新采购太阳能板', '2026-06-21 14:00:00'),
        (4, 2, 'out', 1, 'request', 2, '黎明', '江桥备用电源维修', '2026-06-22 11:30:00'),
    ]
    for row in logs:
        lid, part_id, ltype, qty, ref_type, ref_id, operator, remark, created_at = row
        db.execute(
            """INSERT INTO inventory_logs (id, part_id, type, quantity, ref_type, ref_id, operator, remark, created_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (lid, part_id, ltype, qty, ref_type, ref_id, operator, remark, created_at)
        )

# =============================================================================
# 5.7 alerts (5条) — 注意 flow_type / flow_status 列
# =============================================================================
def insert_alerts(db):
    print("[SeedE2E] 插入 alerts (5条)...")
    alerts = [
        (1, 1, 'data_spike', 12.35, 'orange',
         '数据异常陡增：水位 12.35（均值11.02，变化12%）',
         'pending', 'manual', 'pending_review', None, '2026-06-22 13:00:00'),
        (2, 2, 'data_freeze', 48.62, 'yellow',
         '数据冻结：水位连续6条记录值一致（48.62），传感器可能故障',
         'pending', 'manual', 'pending_review', None, '2026-06-22 12:00:00'),
        (3, 3, 'data_spike', 85.0, 'orange',
         '数据异常：本站上报雨量85.0mm/h，气象数据未报降雨，疑似传感器故障',
         'pending', 'manual', 'pending_review', None, '2026-06-22 13:55:00'),
        (4, 6, 'device_status', 11.3, 'orange',
         '设备离线：备用电源电压偏低(11.3V)，通信模块信号弱',
         'pending', 'auto', 'pending', 'WO-20260622-002', '2026-06-22 14:30:00'),
        (5, 5, 'data_gap', 4.2, 'red',
         '数据缺失：蒸发量已有1470分钟未更新',
         'acknowledged', 'auto', 'converted', 'WO-20260622-003', '2026-06-21 08:00:00'),
    ]
    for row in alerts:
        (aid, site_id, metric, value, level, message,
         status, flow_type, flow_status, related_order_no, created_at) = row
        db.execute(
            """INSERT INTO alerts (id, site_id, metric, value, level, message,
               status, flow_type, flow_status, related_order_no, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (aid, site_id, metric, value, level, message,
             status, flow_type, flow_status, related_order_no, created_at)
        )

# =============================================================================
# 5.8 work_orders (3条)
# =============================================================================
def insert_work_orders(db):
    print("[SeedE2E] 插入 work_orders (3条)...")
    orders = [
        ('WO-20260622-002', 6, 'auto', '告警自动转工单', 'urgent',
         '[自动] 设备离线：备用电源电压偏低(11.3V)',
         '设备离线：备用电源电压偏低(11.3V)，通信模块信号弱', '', '黎明',
         'in_progress', '2026-06-23 14:30', '2026-06-22 14:30:00'),
        ('WO-20260622-003', 5, 'auto', '告警自动转工单', 'critical',
         '[自动] 数据缺失：蒸发量已有1470分钟未更新',
         '数据缺失：蒸发量已有1470分钟未更新', '', '张建国',
         'closed', '2026-06-23 08:00', '2026-06-21 08:00:00'),
    ]
    for row in orders:
        order_no, site_id, source, event_type, level, title, desc, images, assignee, status, sla, created_at = row
        db.execute(
            """INSERT INTO work_orders (order_no, site_id, source, event_type, level, title,
               description, images, assignee, status, sla_deadline, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (order_no, site_id, source, event_type, level, title,
             desc, images, assignee, status, sla, created_at)
        )

# =============================================================================
# 5.9 data_arrival (6条)
# =============================================================================
def insert_data_arrival(db):
    print("[SeedE2E] 插入 data_arrival (6条)...")
    arrivals = [
        (1, 1, '2026-06-22', 'water_level', 288, 286, 99.3),
        (2, 2, '2026-06-22', 'water_level', 288, 285, 98.9),
        (3, 3, '2026-06-22', 'precipitation', 144, 130, 90.3),
        (4, 4, '2026-06-22', 'groundwater_level', 96, 96, 100.0),
        (5, 5, '2026-06-22', 'evaporation', 288, 12, 4.2),
        (6, 6, '2026-06-22', 'temperature', 288, 276, 95.8),
    ]
    for row in arrivals:
        aid, site_id, date, metric, expected, actual, rate = row
        db.execute(
            """INSERT INTO data_arrival (id, site_id, date, metric, expected_count, actual_count, arrival_rate, created_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (aid, site_id, date, metric, expected, actual, rate, '2026-06-22 12:00:00')
        )

# =============================================================================
# 5.10 timeline_events (15条)
# =============================================================================
def insert_timeline_events(db):
    print("[SeedE2E] 插入 timeline_events (15条)...")
    events = [
        (1, 'alert', 1, 'created', '系统', '数据异常陡增：水位12.35', '2026-06-22 13:00:00'),
        (2, 'alert', 2, 'created', '系统', '数据冻结：水位48.62', '2026-06-22 12:00:00'),
        (3, 'alert', 3, 'created', '系统', '数据异常：雨量85mm与气象不符', '2026-06-22 13:55:00'),
        (4, 'alert', 4, 'created', '系统', '设备异常：备用电源电压偏低', '2026-06-22 14:30:00'),
        (5, 'alert', 4, 'auto_converted', '系统', '自动转工单 WO-20260622-002', '2026-06-22 14:30:30'),
        (6, 'alert', 5, 'created', '系统', '数据缺失：蒸发量1470分钟', '2026-06-21 08:00:00'),
        (7, 'alert', 5, 'auto_converted', '系统', '自动转工单 WO-20260622-003', '2026-06-21 08:00:30'),
        (8, 'order', 'WO-20260622-002', 'created', '系统', '告警4自动转工单', '2026-06-22 14:30:30'),
        (9, 'order', 'WO-20260622-002', 'accepted', '黎明', '已接单', '2026-06-22 14:45:00'),
        (10, 'order', 'WO-20260622-002', 'in_progress', '黎明', '已出发前往江桥站', '2026-06-22 14:50:00'),
        (11, 'order', 'WO-20260622-003', 'created', '系统', '告警5自动转工单', '2026-06-21 08:00:30'),
        (12, 'order', 'WO-20260622-003', 'closed', '系统', '万家埠站点离线故障已修复', '2026-06-22 10:00:00'),
        (13, 'inspection', 1, 'task_abnormal', '系统', '邓埠站电压接近警戒线', '2026-06-22 09:00:00'),
    ]
    for row in events:
        eid, source_type, source_id, event_type, operator, remark, created_at = row
        db.execute(
            """INSERT INTO timeline_events (id, source_type, source_id, event_type, operator, remark, created_at)
               VALUES (?,?,?,?,?,?,?)""",
            (eid, source_type, source_id, event_type, operator, remark, created_at)
        )

# =============================================================================
# 5.11 inspection_plans + inspection_tasks (补充)
# =============================================================================
def insert_inspection_plans_and_tasks(db):
    """巡检计划及任务（2个计划，8个任务）"""
    print("[SeedE2E] 插入 inspection_plans (2条) + inspection_tasks (8条)...")

    # 计划1：邓埠站每日巡检
    db.execute(
        """INSERT INTO inspection_plans (id, plan_name, site_id, type, start_date, end_date, status, created_at)
           VALUES (?,?,?,?,?,?,?,?)""",
        (1, '邓埠站日常巡检', 1, 'daily', '2026-06-22', '2026-06-22', 'active', '2026-06-22 08:00:00')
    )
    tasks1 = [
        (1, 1, 1, '张建国', '水位计外观检查', 'equipment', 'normal', '', '2026-06-22 09:00:00'),
        (2, 1, 1, '张建国', '数据传输状态检查', 'communication', 'normal', '', '2026-06-22 09:10:00'),
        (3, 1, 1, '张建国', '供电电压测量', 'power', 'abnormal', '电压11.9V，接近警戒线11.8V', '2026-06-22 09:15:00'),
        (4, 1, 1, '张建国', '环境温湿度记录', 'environment', 'normal', '温度27°C，湿度65%', '2026-06-22 09:20:00'),
    ]
    for row in tasks1:
        tid, plan_id, site_id, inspector, check_item, category, result, remark, check_time = row
        db.execute(
            """INSERT INTO inspection_tasks (id, plan_id, site_id, inspector, check_item, remark, result, check_time)
               VALUES (?,?,?,?,?,?,?,?)""",
            (tid, plan_id, site_id, inspector, f'{category}:{check_item}', remark, result, check_time)
        )

    # 计划2：江桥站院周巡检
    db.execute(
        """INSERT INTO inspection_plans (id, plan_name, site_id, type, start_date, end_date, status, created_at)
           VALUES (?,?,?,?,?,?,?,?)""",
        (2, '江桥站院周巡检', 6, 'weekly', '2026-06-22', '2026-06-28', 'active', '2026-06-22 08:00:00')
    )
    tasks2 = [
        (5, 2, 6, '黎明', '站房密封性检查', 'facility', 'normal', '', None),
        (6, 2, 6, '黎明', '备用电源测试', 'power', 'abnormal', '电压11.3V，需维护', None),
        (7, 2, 6, '黎明', '视频设备清洁', 'equipment', 'pending', '', None),
        (8, 2, 6, '黎明', '通信天线检查', 'communication', 'pending', '', None),
    ]
    for row in tasks2:
        tid, plan_id, site_id, inspector, check_item, category, result, remark, check_time = row
        db.execute(
            """INSERT INTO inspection_tasks (id, plan_id, site_id, inspector, check_item, remark, result, check_time)
               VALUES (?,?,?,?,?,?,?,?)""",
            (tid, plan_id, site_id, inspector, f'{category}:{check_item}', remark, result, check_time)
        )

# =============================================================================
# 5.12 weather_data (1条)
# =============================================================================
def insert_weather_data(db):
    print("[SeedE2E] 插入 weather_data (1条)...")
    db.execute(
        """INSERT INTO weather_data (id, temperature, humidity, wind_speed, wind_direction,
           precipitation, pressure, weather_type, warning_info, recorded_at)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (1, 28.0, 62.0, 3.0, '东南', 0.0, 1013.0, '多云', '',
         '2026-06-22 15:00:00')
    )

# =============================================================================
# 5.13 water_level_checks (2条) — 字段名映射注意
# =============================================================================
def insert_water_level_checks(db):
    print("[SeedE2E] 插入 water_level_checks (2条)...")
    checks = [
        (1, 1, 12.35, 12.34, 0.01, 'normal', None, '张建国', '2026-06-22 15:00:00'),
        (2, 2, 48.62, 48.65, 0.03, 'normal', None, '黎明', '2026-06-22 14:55:00'),
    ]
    for row in checks:
        cid, site_id, manual_lv, tele_lv, diff, status, action, operator, created_at = row
        db.execute(
            """INSERT INTO water_level_checks (id, site_id, manual_level, telemetry_level, diff,
               status, adjust_action, operator, created_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (cid, site_id, manual_lv, tele_lv, diff, status, action, operator, created_at)
        )

# =============================================================================
# 5.14 spare_part_requests (2条)
# =============================================================================
def insert_part_requests(db):
    print("[SeedE2E] 插入 spare_part_requests (2条)...")
    requests = [
        ('SQ-20260622-001', 5, '张建国', '通信模块', 1, '万家埠通信模块离线，需更换',
         'pending', '', '', '2026-06-22 10:00:00'),
        ('SQ-20260622-002', 6, '黎明', '蓄电池', 2, '江桥备用电源电压偏低，需更换蓄电池',
         'approved', '系统管理员', '同意更换', '2026-06-22 11:00:00'),
    ]
    for row in requests:
        rno, site_id, applicant, part_name, qty, reason, status, approver, comment, created_at = row
        db.execute(
            """INSERT INTO spare_part_requests (request_no, site_id, applicant, part_name, quantity,
               reason, status, approver, approval_comment, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (rno, site_id, applicant, part_name, qty, reason, status, approver, comment, created_at, created_at)
        )

# =============================================================================
# 5.15 maintenance_plans (1条)
# =============================================================================
def insert_maintenance_plans(db):
    print("[SeedE2E] 插入 maintenance_plans (1条)...")
    db.execute(
        """INSERT INTO maintenance_plans (id, site_id, plan_name, category, frequency, due_date, status, assignee, created_at)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (1, 5, '万家埠季度设备巡检', 'facility', 'quarterly', '2026-07-01', 'pending', '张建国',
         '2026-06-21 08:00:00')
    )

# =============================================================================
# 5.16 hotline_events (2条)
# =============================================================================
def insert_hotline_events(db):
    print("[SeedE2E] 插入 hotline_events (2条)...")
    events = [
        (1, '王先生', '13800001001', '设备故障', '万家埠蒸发站设备异常，数据传输中断',
         '南昌市安义县万家埠', 'dispatched', 'WO-20260622-003', '李敏', '2026-06-21 09:00:00'),
        (2, '刘女士', '13800002002', '水位异常', '江桥站院附近河道水位上涨较快，担心安全',
         '南昌市南昌县江桥', 'registered', '', '王芳', '2026-06-22 14:00:00'),
    ]
    for row in events:
        eid, caller_name, caller_phone, event_type, desc, location, status, order_no, operator, created_at = row
        db.execute(
            """INSERT INTO hotline_events (id, caller_name, caller_phone, event_type, description,
               location, status, related_order_no, operator, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (eid, caller_name, caller_phone, event_type, desc, location, status, order_no, operator, created_at)
        )

# =============================================================================
# 5.17 user_sites (12条：各用户的站点权限)
# =============================================================================
def insert_user_sites(db):
    print("[SeedE2E] 插入 user_sites (12条)...")
    # 管理员(id=1)拥有全部6站点
    for sid in range(1, 7):
        db.execute('INSERT INTO user_sites (user_id, site_id) VALUES (?, ?)', (1, sid))
    # 张建国(id=2)负责邓埠(1)、万家埠(5)
    for sid in [1, 5]:
        db.execute('INSERT INTO user_sites (user_id, site_id) VALUES (?, ?)', (2, sid))
    # 黎明(id=3)负责乌井(2)、江桥(6)
    for sid in [2, 6]:
        db.execute('INSERT INTO user_sites (user_id, site_id) VALUES (?, ?)', (3, sid))
    # 王刚(id=4)负责罗亭(3)
    db.execute('INSERT INTO user_sites (user_id, site_id) VALUES (?, ?)', (4, 3))
    # 赵洪(id=5)负责省水文局(4)
    db.execute('INSERT INTO user_sites (user_id, site_id) VALUES (?, ?)', (5, 4))


# =============================================================================
# 6. 验证逻辑
# =============================================================================
def run_validation(db):
    print("\n" + "=" * 60)
    print("[SeedE2E] 运行数据校验...")
    print("=" * 60)

    checks = [
        ("1. 站点数=6", "SELECT COUNT(*) as c FROM sites", 6),
        ("2. 告警数=5", "SELECT COUNT(*) as c FROM alerts", 5),
        ("3. 待pending告警=4", "SELECT COUNT(*) as c FROM alerts WHERE status='pending'", 4),
        ("4. pending_review=3", "SELECT COUNT(*) as c FROM alerts WHERE flow_type='manual' AND flow_status='pending_review'", 3),
        ("5. 工单数=2", "SELECT COUNT(*) as c FROM work_orders", 2),
        ("6. 设备数=11", "SELECT COUNT(*) as c FROM device_shadows", 11),
    ]

    all_ok = True
    for label, sql, expected in checks:
        row = db.execute(sql).fetchone()
        actual = row['c']
        ok = actual == expected
        status = "✓" if ok else "✗"
        if not ok:
            all_ok = False
        print(f"  {status} {label}: actual={actual}, expected={expected}")

    # 检查7: 每个站点至少1条sensor_data
    print("\n  7. 每个站点至少1条sensor_data:")
    rows = db.execute("SELECT site_id, COUNT(*) as c FROM sensor_data GROUP BY site_id HAVING c>0").fetchall()
    site_ids = [r['site_id'] for r in rows]
    print(f"     ✓ 有数据的站点: {sorted(site_ids)} (共{len(rows)}个)")

    # 检查8: 关联告警的工单号有效
    print("\n  8. 关联告警的工单号有效:")
    alert_orders = db.execute("SELECT id, related_order_no FROM alerts WHERE related_order_no IS NOT NULL").fetchall()
    valid_orders = set(r['order_no'] for r in db.execute("SELECT order_no FROM work_orders").fetchall())
    for a in alert_orders:
        ok = a['related_order_no'] in valid_orders
        status = "✓" if ok else "✗"
        print(f"     {status} 告警ID={a['id']} → {a['related_order_no']}")
        if not ok:
            all_ok = False

    # 补充验证
    print("\n  补充验证:")
    # sensor_data 总量
    cnt = db.execute("SELECT COUNT(*) as c FROM sensor_data").fetchone()['c']
    print(f"     sensor_data 总量: {cnt}")

    # device_shadows 状态统计
    online = db.execute("SELECT COUNT(*) as c FROM device_shadows WHERE status='online'").fetchone()['c']
    offline = db.execute("SELECT COUNT(*) as c FROM device_shadows WHERE status='offline'").fetchone()['c']
    print(f"     设备在线/离线: {online}/{offline}")

    # 低电压设备（非离线且电压<11.8）
    low_v = db.execute(
        "SELECT COUNT(*) as c FROM device_shadows WHERE status='online' AND voltage < 11.8"
    ).fetchone()['c']
    print(f"     电压偏低在线设备: {low_v}")

    # 站点status检查（站点5应为offline）
    s5 = db.execute("SELECT status FROM sites WHERE id=5").fetchone()
    print(f"     站点5(万家埠) status: {s5['status']} (期望: offline)")

    # timeline 总量
    tl_cnt = db.execute("SELECT COUNT(*) as c FROM timeline_events").fetchone()['c']
    print(f"     timeline_events 总量: {tl_cnt}")

    print("\n" + "=" * 60)
    if all_ok:
        print("[SeedE2E] ✓ 所有核心校验通过！")
    else:
        print("[SeedE2E] ⚠ 部分校验未通过，请检查上述✗标记项")

# =============================================================================
# 7. 主流程
# =============================================================================
def main():
    print("=" * 60)
    print("  全平台试运行数据生成脚本 (Seed E2E)")
    print("=" * 60)

    # Step 0: 验证路径
    verify_db_path()

    # Step 1: 备份
    backup_db()

    # Step 2: 连接数据库
    db = get_db()

    try:
        # Step 3: 清空
        clear_tables(db)

        # Step 4: 确保迁移列
        ensure_migration_columns(db)

        # Step 5: 按序插入数据
        insert_sites(db)                                    # 1. sites (6)
        insert_inspection_schemes(db)                       # 2. inspection_schemes (1) + items (4)
        last_sensor_id = insert_sensor_data(db)             # 3. sensor_data (131)
        insert_device_shadows(db)                           # 4. device_shadows (11)
        insert_parts_inventory(db)                          # 5. parts_inventory (5)
        insert_inventory_logs(db)                           # 6. inventory_logs (4)
        insert_alerts(db)                                   # 7. alerts (5)
        insert_work_orders(db)                              # 8. work_orders (3)
        insert_data_arrival(db)                             # 9. data_arrival (6)
        insert_timeline_events(db)                          # 10. timeline_events (15)
        insert_weather_data(db)                             # 11. weather_data (1)
        # 12. 降雨预报: 由API动态生成，无独立表，跳过
        insert_water_level_checks(db)                       # 13. water_level_checks (2)
        insert_part_requests(db)                            # 14. parts_requests (2)
        insert_maintenance_plans(db)                        # 15. maintenance_plans (1)
        insert_hotline_events(db)                           # 16. hotline_events (2)
        insert_user_sites(db)                                # 17. user_sites (12)

        # 补充: inspection_plans + inspection_tasks (方案文档要求的)
        insert_inspection_plans_and_tasks(db)

        # 提交事务
        db.commit()

        # Step 6: 统计
        total_records = 0
        for tbl in reversed(TABLES_IN_DELETE_ORDER):
            try:
                cnt = db.execute(f"SELECT COUNT(*) as c FROM {tbl}").fetchone()['c']
                if cnt > 0:
                    print(f"  {tbl}: {cnt}条")
                    total_records += cnt
            except Exception:
                pass
        # 额外计数
        for extra in ['inspection_tasks', 'inspection_plans', 'inspection_scheme_items']:
            try:
                cnt = db.execute(f"SELECT COUNT(*) as c FROM {extra}").fetchone()['c']
                if cnt > 0:
                    total_records += 0  # already counted in TABLES_IN_DELETE_ORDER
            except:
                pass

        print(f"\n[SeedE2E] 全平台试运行数据生成完成，共 {total_records} 条记录")

        # Step 7: 验证
        run_validation(db)

    finally:
        db.close()

if __name__ == '__main__':
    main()
