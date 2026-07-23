#!/usr/bin/env python3
"""全系统种子数据清整"""
import sqlite3, sys
db = sqlite3.connect('data/water.db')
db.row_factory = sqlite3.Row

tables = {
    'inspection_v2_templates': 'CREATE TABLE IF NOT EXISTS inspection_v2_templates (id INTEGER PRIMARY KEY AUTOINCREMENT, template_name TEXT, category TEXT, frequency TEXT, item_count INTEGER DEFAULT 0, description TEXT, status TEXT DEFAULT "active", created_at TIMESTAMP DEFAULT (datetime("now","localtime")))',
    'inspection_v2_template_items': 'CREATE TABLE IF NOT EXISTS inspection_v2_template_items (id INTEGER PRIMARY KEY AUTOINCREMENT, template_id INTEGER NOT NULL, item_name TEXT, category TEXT, frequency_level TEXT DEFAULT "mid", sort_order INTEGER DEFAULT 0, photo_required INTEGER DEFAULT 0)',
    'inspection_v2_configs': 'CREATE TABLE IF NOT EXISTS inspection_v2_configs (id INTEGER PRIMARY KEY AUTOINCREMENT, site_type TEXT, device_types TEXT DEFAULT "", template_id INTEGER NOT NULL, is_active INTEGER DEFAULT 1, remark TEXT DEFAULT "", created_at TIMESTAMP DEFAULT (datetime("now","localtime")))',
    'inspection_v2_schedules': 'CREATE TABLE IF NOT EXISTS inspection_v2_schedules (id INTEGER PRIMARY KEY AUTOINCREMENT, site_id INTEGER, template_id INTEGER, template_item_id INTEGER, frequency TEXT, next_due_date TEXT, last_completed_at TEXT, status TEXT DEFAULT "active", cycle_count INTEGER DEFAULT 0)',
    'inspection_v2_plans': 'CREATE TABLE IF NOT EXISTS inspection_v2_plans (id INTEGER PRIMARY KEY AUTOINCREMENT, plan_name TEXT, assignee TEXT, assignee_id INTEGER, period TEXT, generate_date TEXT, status TEXT DEFAULT "draft", completion_rate REAL DEFAULT 0, total_items INTEGER DEFAULT 0, completed_items INTEGER DEFAULT 0, created_at TIMESTAMP DEFAULT (datetime("now","localtime")))',
    'inspection_v2_items': 'CREATE TABLE IF NOT EXISTS inspection_v2_items (id INTEGER PRIMARY KEY AUTOINCREMENT, plan_id INTEGER NOT NULL, site_id INTEGER, item_name TEXT, category TEXT, result TEXT, need_review INTEGER DEFAULT 0, review_status INTEGER DEFAULT 0, check_time TEXT, created_at TIMESTAMP DEFAULT (datetime("now","localtime")))',
    'inspection_skip_logs': 'CREATE TABLE IF NOT EXISTS inspection_skip_logs (id INTEGER PRIMARY KEY AUTOINCREMENT, site_id INTEGER, check_item TEXT, reason TEXT, skip_count INTEGER DEFAULT 1, created_at TIMESTAMP DEFAULT (datetime("now","localtime")))',
    'devices': 'CREATE TABLE IF NOT EXISTS devices (id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT, device_name TEXT, device_type TEXT, device_model TEXT, manufacturer TEXT, site_id INTEGER, status TEXT, voltage REAL, last_data_time TEXT, install_date TEXT, created_at TEXT)',
    'parts_inventory': 'CREATE TABLE IF NOT EXISTS parts_inventory (id INTEGER PRIMARY KEY AUTOINCREMENT, part_code TEXT, part_name TEXT, category TEXT, spec TEXT, quantity REAL DEFAULT 0, min_quantity REAL DEFAULT 5, unit TEXT DEFAULT "个", location TEXT, site_id INTEGER, remark TEXT, updated_at TEXT)',
}
for name, sql in tables.items():
    s = db.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{name}'").fetchone()
    if not s:
        db.execute(sql)
        print(f'创建表: {name}')
    else:
        print(f'已存在: {name}')

# 种子数据
data = [
    # inspection_v2_templates
    "INSERT OR IGNORE INTO inspection_v2_templates (id, template_name, category, frequency, item_count) VALUES (1,'水质自动站周检方案','水质监测','weekly',12)",
    "INSERT OR IGNORE INTO inspection_v2_templates (id, template_name, category, frequency, item_count) VALUES (2,'站院环境月检方案','站院环境','monthly',8)",
    # template_items
    "INSERT OR IGNORE INTO inspection_v2_template_items (id, template_id, item_name, category, sort_order, photo_required) VALUES (1,1,'站房外观','环境',1,1)",
    "INSERT OR IGNORE INTO inspection_v2_template_items (id, template_id, item_name, category, sort_order, photo_required) VALUES (2,1,'采水系统','设备',2,1)",
    # configs
    "INSERT OR IGNORE INTO inspection_v2_configs (id, site_type, template_id, is_active) VALUES (1,'water_quality',1,1)",
    "INSERT OR IGNORE INTO inspection_v2_configs (id, site_type, template_id, is_active) VALUES (2,'station_yard',2,1)",
    # schedules
    "INSERT OR IGNORE INTO inspection_v2_schedules (id, site_id, template_id, template_item_id, frequency, next_due_date, status) VALUES (1,1,1,1,'weekly',date('now','+7 days'),'active')",
    # plans
    "INSERT OR IGNORE INTO inspection_v2_plans (id, plan_name, assignee, assignee_id, period, generate_date, status, total_items, completed_items) VALUES (1,'7月第2周水质站周检','张建国',2,'weekly',date('now','-3 days'),'active',12,10)",
    "INSERT OR IGNORE INTO inspection_v2_plans (id, plan_name, assignee, assignee_id, period, generate_date, status, total_items, completed_items) VALUES (2,'7月月检计划','黎明',3,'monthly',date('now','-10 days'),'active',8,3)",
    # items
    "INSERT OR IGNORE INTO inspection_v2_items (id, plan_id, site_id, item_name, need_review, review_status, check_time) VALUES (1,1,1,'站房外观',0,0,datetime('now','-2 days'))",
    "INSERT OR IGNORE INTO inspection_v2_items (id, plan_id, site_id, item_name, need_review, review_status, check_time) VALUES (2,1,1,'五参数质控',1,1,datetime('now','-2 days'))",
    # skip logs
    "INSERT OR IGNORE INTO inspection_skip_logs (id, site_id, check_item, reason, skip_count) VALUES (1,1,'氨氮质控','仪器校准中',3)",
    # data_arrival
    "INSERT OR IGNORE INTO data_arrival (site_id, date, metric, expected_count, actual_count, arrival_rate) VALUES (1,date('now','-1 days'),'pH',96,95,98.96)",
    "INSERT OR IGNORE INTO data_arrival (site_id, date, metric, expected_count, actual_count, arrival_rate) VALUES (1,date('now','-1 days'),'氨氮',96,91,94.79)",
    "INSERT OR IGNORE INTO data_arrival (site_id, date, metric, expected_count, actual_count, arrival_rate) VALUES (1,date('now','-1 days'),'COD',96,89,92.71)",
    # devices
    "INSERT OR IGNORE INTO devices (id, code, device_name, device_type, device_model, manufacturer, site_id, status, voltage) VALUES (1,'SEN-PH-001','pH传感器','ph_meter','PH-6100','梅特勒',1,'online',12.2)",
    "INSERT OR IGNORE INTO devices (id, code, device_name, device_type, device_model, manufacturer, site_id, status, voltage) VALUES (2,'SEN-DO-001','溶解氧传感器','do_sensor','DO-3050','哈希',1,'online',12.0)",
    "INSERT OR IGNORE INTO devices (id, code, device_name, device_type, device_model, manufacturer, site_id, status, voltage) VALUES (3,'SEN-TB-001','浊度计','turbidity_meter','TB-880','哈希',1,'offline',10.5)",
    "INSERT OR IGNORE INTO devices (id, code, device_name, device_type, device_model, manufacturer, site_id, status, voltage) VALUES (4,'DTU-001','数采仪','dtu','DTU-4100','有人物联',1,'online',12.5)",
    "INSERT OR IGNORE INTO devices (id, code, device_name, device_type, device_model, manufacturer, site_id, status, voltage) VALUES (5,'PUMP-001','潜水泵','pump','SP-200W','凯泉',2,'maintenance',0)",
    "INSERT OR IGNORE INTO devices (id, code, device_name, device_type, device_model, manufacturer, site_id, status, voltage) VALUES (6,'SEN-NH3-001','氨氮传感器','ammonia_meter','NH3-200','力合',2,'online',11.8)",
    # parts
    "INSERT OR IGNORE INTO parts_inventory (id, part_code, part_name, category, quantity, min_quantity, unit, location) VALUES (1,'SP-PH-001','pH复合电极','传感器',8,5,'支','A柜1-2')",
    "INSERT OR IGNORE INTO parts_inventory (id, part_code, part_name, category, quantity, min_quantity, unit, location) VALUES (2,'SP-DO-001','溶解氧膜头','传感器',3,5,'个','A柜1-2')",
    # notifications
    "INSERT OR IGNORE INTO notifications (id, user_id, source_type, source_id, title, content, created_at) VALUES (1,1,'photo_review',3,'照片被驳回','五参数质控照片模糊，请重拍',datetime('now','-1 hours'))",
    # fresh alerts
    "INSERT OR IGNORE INTO alerts (id, site_id, metric, value, level, status, message, created_at) VALUES (11,1,'ph',7.52,'yellow','pending','pH偏高 7.52',datetime('now','-2 hours'))",
    "INSERT OR IGNORE INTO alerts (id, site_id, metric, value, level, status, message, created_at) VALUES (12,1,'ammonia',1.85,'orange','pending','氨氮超III类标准',datetime('now','-5 hours'))",
    "INSERT OR IGNORE INTO alerts (id, site_id, metric, value, level, status, message, created_at) VALUES (13,2,'device_status',0,'red','acknowledged','设备离线超120分钟',datetime('now','-12 hours'))",
    "INSERT OR IGNORE INTO alerts (id, site_id, metric, value, level, status, message, related_order_no, created_at) VALUES (14,1,'ph',8.12,'orange','acknowledged','pH持续偏高已转工单','WO-20260701-001',datetime('now','-6 hours'))",
    # fresh work orders
    "INSERT OR IGNORE INTO work_orders (order_no, site_id, source, event_type, level, title, description, assignee, status, created_at) VALUES ('WO-20260701-001',1,'auto','水质异常','urgent','pH传感器校准','pH偏高需校准','张建国','in_progress',datetime('now','-6 hours'))",
    "INSERT OR IGNORE INTO work_orders (order_no, site_id, source, event_type, level, title, assignee, status, created_at) VALUES ('WO-20260702-001',2,'auto','设备离线','critical','设备离线排查','黎明','accepted',datetime('now','-12 hours'))",
    "INSERT OR IGNORE INTO work_orders (order_no, site_id, source, event_type, level, title, assignee, status, created_at) VALUES ('WO-20260703-001',3,'manual','设备故障','normal','浊度传感器更换','王刚','pending',datetime('now','-4 hours'))",
    "INSERT OR IGNORE INTO work_orders (order_no, site_id, source, event_type, level, title, assignee, status, created_at) VALUES ('WO-20260704-001',1,'manual_report','感官异常','normal','发现偷排口','赵洪','reviewing',datetime('now','-1 days'))",
]

cnt = 0
for sql in data:
    try:
        db.execute(sql)
        cnt += 1
    except Exception as e:
        print(f'  ⚠ {e}')
db.commit()
print(f'插入 {cnt} 条数据')

# 验证
print('\n=== 验证 ===')
tables_check = ['sites','alerts','work_orders','devices','reagents','reagent_inventory','operation_attachments','inspection_v2_plans','inspection_v2_items','data_arrival','parts_inventory','notifications','inspection_v2_templates']
for t in tables_check:
    n=db.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]
    print(f'{t:30s} {n}')
db.close()
print('\n种子数据清整完成')
