-- ============================================================
-- 全系统种子数据清整脚本
-- 目标：所有模块可演示的完整数据流
-- 执行：sqlite3 data/water.db < seed_cleanup.sql
-- ============================================================

-- =============================
-- 1. 缺失表定义 + 种子数据
-- =============================

-- 1.1 巡检V2表（maintenance/audit模块依赖）
CREATE TABLE IF NOT EXISTS inspection_v2_templates (
    id INTEGER PRIMARY KEY AUTOINCREMENT, template_name TEXT, category TEXT,
    frequency TEXT, item_count INTEGER DEFAULT 0, description TEXT,
    status TEXT DEFAULT 'active', created_at TIMESTAMP DEFAULT (datetime('now','localtime')));
INSERT OR IGNORE INTO inspection_v2_templates (id, template_name, category, frequency, item_count, description) VALUES
(1, '水质自动站周检方案', '水质监测', 'weekly', 12, '适用于水质自动监测站的每周检查项目'),
(2, '站院环境月检方案', '站院环境', 'monthly', 8, '适用于站院环境的月度巡检项目');

CREATE TABLE IF NOT EXISTS inspection_v2_template_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT, template_id INTEGER NOT NULL,
    item_name TEXT, category TEXT, frequency_level TEXT DEFAULT 'mid',
    sort_order INTEGER DEFAULT 0, photo_required INTEGER DEFAULT 0);
INSERT OR IGNORE INTO inspection_v2_template_items (id, template_id, item_name, category, sort_order, photo_required) VALUES
(1, 1, '站房外观与周边环境', '环境', 1, 1), (2, 1, '采水系统运行状态', '设备', 2, 1),
(3, 1, '预处理单元', '设备', 3, 1), (4, 1, '五参数分析仪质控', '质控', 4, 2),
(5, 1, '氨氮分析仪质控', '质控', 5, 2), (6, 1, '废液收集与处置', '环境', 11, 1),
(7, 2, '站院围墙检查', '环境', 1, 1), (8, 2, '绿化养护情况', '环境', 2, 1);

CREATE TABLE IF NOT EXISTS inspection_v2_configs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, site_type TEXT, device_types TEXT DEFAULT '',
    template_id INTEGER NOT NULL, is_active INTEGER DEFAULT 1, remark TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT (datetime('now','localtime')));
INSERT OR IGNORE INTO inspection_v2_configs (id, site_type, template_id, is_active) VALUES
(1, 'water_quality', 1, 1), (2, 'station_yard', 2, 1);

CREATE TABLE IF NOT EXISTS inspection_v2_schedules (
    id INTEGER PRIMARY KEY AUTOINCREMENT, site_id INTEGER, template_id INTEGER,
    template_item_id INTEGER, frequency TEXT, next_due_date TEXT,
    last_completed_at TEXT, status TEXT DEFAULT 'active', cycle_count INTEGER DEFAULT 0);
INSERT OR IGNORE INTO inspection_v2_schedules (id, site_id, template_id, template_item_id, frequency, next_due_date, status) VALUES
(1, 1, 1, 1, 'weekly', date('now','+7 days'), 'active'),
(2, 1, 1, 4, 'weekly', date('now','+7 days'), 'active');

CREATE TABLE IF NOT EXISTS inspection_v2_plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT, plan_name TEXT, assignee TEXT,
    assignee_id INTEGER, period TEXT, generate_date TEXT, status TEXT DEFAULT 'draft',
    completion_rate REAL DEFAULT 0, total_items INTEGER DEFAULT 0, completed_items INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT (datetime('now','localtime')));
INSERT OR IGNORE INTO inspection_v2_plans (id, plan_name, assignee, assignee_id, period, generate_date, status, total_items, completed_items) VALUES
(1, '7月第2周水质站周检', '张建国', 2, 'weekly', date('now','-3 days'), 'active', 12, 10),
(2, '7月月检计划', '黎明', 3, 'monthly', date('now','-10 days'), 'active', 8, 3);

CREATE TABLE IF NOT EXISTS inspection_v2_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT, plan_id INTEGER NOT NULL, site_id INTEGER,
    schedule_id INTEGER, template_id INTEGER, item_name TEXT, category TEXT DEFAULT '',
    frequency TEXT DEFAULT '', result TEXT, photo_urls TEXT, gps_lat REAL, gps_lng REAL,
    check_time TEXT, remark TEXT DEFAULT '', need_review INTEGER DEFAULT 0,
    review_status INTEGER DEFAULT 0, completed_at TEXT,
    created_at TIMESTAMP DEFAULT (datetime('now','localtime')));
INSERT OR IGNORE INTO inspection_v2_items (id, plan_id, site_id, item_name, need_review, review_status, check_time) VALUES
(1, 1, 1, '站房外观与周边环境', 0, 0, datetime('now','-2 days')),
(2, 1, 1, '五参数分析仪质控', 1, 1, datetime('now','-2 days')),
(3, 1, 1, '氨氮分析仪质控', 1, 1, NULL),
(4, 2, 2, '站院围墙检查', 0, 0, datetime('now','-8 days'));

CREATE TABLE IF NOT EXISTS inspection_skip_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, site_id INTEGER, check_item TEXT,
    reason TEXT, skip_count INTEGER DEFAULT 1, created_at TIMESTAMP DEFAULT (datetime('now','localtime')));
INSERT OR IGNORE INTO inspection_skip_logs (id, site_id, check_item, reason, skip_count) VALUES
(1, 1, '氨氮分析仪质控', '仪器正在校准中', 3);

-- 1.2 data_arrival 表
INSERT OR IGNORE INTO data_arrival (site_id, date, metric, expected_count, actual_count, arrival_rate) VALUES
(1, date('now','-1 days'), 'pH', 96, 95, 98.96),
(1, date('now','-1 days'), '氨氮', 96, 91, 94.79),
(1, date('now','-1 days'), '高锰酸盐指数', 96, 89, 92.71),
(1, date('now','-1 days'), '溶解氧', 96, 93, 96.88),
(2, date('now','-1 days'), 'pH', 96, 90, 93.75),
(2, date('now','-1 days'), '浊度', 96, 92, 95.83);

-- 1.3 alert_escalation_config（已在 Step 3 创建，补数据）
INSERT OR IGNORE INTO alert_escalation_config (level, sla_minutes, auto_workorder, notify_type, escalate_to_level, color, description) VALUES
('blue', 120, 0, 'app', 'yellow', '#1890ff', '蓝色关注：超阈值≤10%，APP推送，120分钟未处理升级黄色'),
('yellow', 60, 1, 'sms', 'orange', '#faad14', '黄色预警：超阈值10~30%，生成工单+SMS通知'),
('orange', 30, 1, 'phone', 'red', '#fa8c16', '橙色严重：超阈值30~50%，电话通知主管'),
('red', 10, 1, 'phone', NULL, '#f5222d', '红色紧急：超阈值≥50%，电话+全员通知');

-- 1.4 站点数据（已有49个，补充字段确保完整性）
UPDATE sites SET district='新建区' WHERE district IS NULL OR district='';
UPDATE sites SET manager='张建国' WHERE manager IS NULL OR manager='';

-- =============================
-- 2. 告警数据（10条跨级别跨状态）
-- =============================
INSERT OR IGNORE INTO alerts (id, site_id, metric, value, level, status, message, created_at) VALUES
(1, 1, 'ph', 7.52, 'yellow', 'pending', 'pH 值偏高 7.52，超出GB3838 III类标准上限 9.0', datetime('now','-2 hours')),
(2, 1, 'ammonia', 1.85, 'orange', 'pending', '氨氮 1.85mg/L 超 III类标准上限 1.0mg/L', datetime('now','-5 hours')),
(3, 2, 'device_status', 0, 'red', 'acknowledged', '设备离线超过 120 分钟，请立即处理', datetime('now','-12 hours')),
(4, 1, 'data_gap', 65, 'yellow', 'pending', '数据缺失 65 分钟，超过 60 分钟阈值', datetime('now','-3 hours')),
(5, 3, 'turbidity', 12.5, 'blue', 'pending', '浊度 12.5NTU 接近上限 50NTU，关注', datetime('now','-1 hours')),
(6, 1, 'cod', 35.2, 'blue', 'resolved', 'COD 35.2mg/L，已恢复正常', datetime('now','-24 hours')),
(7, 2, 'dissolved_oxygen', 2.8, 'orange', 'resolved', '溶解氧 2.8mg/L 低于 III类标准下限 5mg/L', datetime('now','-48 hours')),
(8, 4, 'ammonia', 0.95, 'yellow', 'pending', '氨氮 0.95mg/L 接近上限 1.0mg/L', datetime('now','-30 minutes'));

-- 告警-工单关联
INSERT OR IGNORE INTO alerts (id, site_id, metric, value, level, status, message, related_order_no, created_at) VALUES
(9, 1, 'ph', 8.12, 'orange', 'acknowledged', 'pH 持续偏高 8.12，已转工单处理', 'WO-20260701-001', datetime('now','-6 hours')),
(10, 3, 'equipment', 0, 'red', 'pending', '浊度传感器故障，急需维修', datetime('now','-4 hours'));

-- =============================
-- 3. 工单数据（5条全状态覆盖）
-- =============================
INSERT OR IGNORE INTO work_orders (order_no, site_id, source, event_type, level, title, description, assignee, status, sla_deadline, created_at) VALUES
('WO-20260701-001', 1, 'auto', '水质异常', 'urgent', 'pH传感器校准维护', '自动告警转工单：pH持续偏高，需现场校准', '张建国', 'in_progress', datetime('now','+1 days'), datetime('now','-6 hours')),
('WO-20260702-001', 2, 'auto', '设备离线', 'critical', '设备离线排查修复', '赣江昌邑站设备离线超过2小时，需排查', '黎明', 'accepted', datetime('now','+2 hours'), datetime('now','-12 hours')),
('WO-20260703-001', 3, 'manual', '设备故障', 'normal', '浊度传感器更换', '浊度传感器读数异常，需更换备件', '王刚', 'pending', datetime('now','+24 hours'), datetime('now','-4 hours')),
('WO-20260704-001', 1, 'manual_report', '感官异常', 'normal', '站房周边发现偷排口', '现场巡查发现偷排口，需核实处理', '赵洪', 'reviewing', datetime('now','+12 hours'), datetime('now','-1 days')),
('WO-20260705-001', 4, 'manual', '试剂补充', 'normal', '试剂补充-氨氮标液', '氨氮标液库存低于阈值，需补充', '张建国', 'pending', datetime('now','+48 hours'), datetime('now','-2 days'));

-- =============================
-- 4. 试剂数据（3种试剂+库存+告警+使用记录）
-- =============================
INSERT OR IGNORE INTO reagents (id, name, manufacturer, spec, unit, shelf_life_days) VALUES
(1, '氨氮标液', '哈希水质', '500mL/瓶', '瓶', 365),
(2, 'COD试剂', '聚光科技', '150mL/套', '套', 365),
(3, 'pH校准液(pH4)', '梅特勒', '250mL/瓶', '瓶', 730);

INSERT OR IGNORE INTO reagent_inventory (site_id, reagent_id, current_qty, low_stock_threshold) VALUES
(1, 1, 0.5, 1.0), (1, 2, 2.0, 1.0), (1, 3, 1.5, 1.0),
(2, 1, 0.3, 1.0), (2, 3, 2.0, 1.0);

INSERT OR IGNORE INTO reagent_alerts (site_id, reagent_id, alert_type, current_qty, threshold_qty, handled) VALUES
(1, 1, 'low_stock', 0.5, 1.0, 0), (2, 1, 'low_stock', 0.3, 1.0, 0);

INSERT OR IGNORE INTO reagent_usage (site_id, reagent_id, used_qty, used_at) VALUES
(1, 1, 0.1, datetime('now','-1 days')), (1, 1, 0.15, datetime('now','-3 days')),
(1, 2, 0.05, datetime('now','-2 days')), (2, 1, 0.2, datetime('now','-5 days')),
(1, 1, 0.08, datetime('now','-7 days')), (1, 1, 0.12, datetime('now','-14 days'));

-- =============================
-- 5. 影像附件（供archive/photos页面使用）
-- =============================
INSERT OR IGNORE INTO operation_attachments (id, site_id, source_type, source_id, filename, category, file_type, file_size, stored_path, description, requirement_id, review_status, created_at) VALUES
(1, 1, 'inspection', 1, '站房外观.jpg', '现场照片', 'image', 204800, '/uploads/site1_facade.jpg', '站房外观与周边环境', 1, 'approved', datetime('now','-7 days')),
(2, 1, 'inspection', 1, '采水系统.jpg', '现场照片', 'image', 153600, '/uploads/site1_water.jpg', '采水系统运行状态', 2, 'pending', datetime('now','-7 days')),
(3, 1, 'inspection', 1, '五参数质控1.jpg', '仪器照片', 'image', 180000, '/uploads/site1_5param1.jpg', '五参数分析仪质控', 4, 'pending', datetime('now','-7 days')),
(4, 1, 'workorder', 'WO-20260703-001', '浊度传感器.jpg', '设备照片', 'image', 256000, '/uploads/turbidity_sensor.jpg', '浊度传感器现场照片', NULL, 'pending', datetime('now','-3 days')),
(5, 2, 'inspection', 2, '站房外观.jpg', '现场照片', 'image', 190000, '/uploads/site2_facade.jpg', '赣江昌邑站外观', 1, 'approved', datetime('now','-5 days'));

-- =============================
-- 6. 设备数据（8台设备覆盖各状态）
-- =============================
INSERT OR IGNORE INTO devices (id, code, device_name, device_type, device_model, manufacturer, site_id, status, voltage, last_data_time, install_date, created_at) VALUES
(1, 'SEN-PH-001', 'pH传感器', 'ph_meter', 'PH-6100', '梅特勒-托利多', 1, 'online', 12.2, datetime('now','-5 minutes'), '2025-01-15', '2025-01-15'),
(2, 'SEN-DO-001', '溶解氧传感器', 'do_sensor', 'DO-3050', '哈希水质', 1, 'online', 12.0, datetime('now','-10 minutes'), '2025-01-15', '2025-01-15'),
(3, 'SEN-TB-001', '浊度计', 'turbidity_meter', 'TB-880', '哈希水质', 1, 'offline', 10.5, datetime('now','-2 hours'), '2025-03-20', '2025-03-20'),
(4, 'DTU-001', '数据采集终端', 'dtu', 'DTU-4100', '深圳有人物联网', 1, 'online', 12.5, datetime('now','-2 minutes'), '2025-01-15', '2025-01-15'),
(5, 'PUMP-001', '潜水泵', 'submersible_pump', 'SP-200W', '上海凯泉泵业', 2, 'maintenance', NULL, NULL, '2024-06-01', '2024-06-01'),
(6, 'SEN-NH3-001', '氨氮传感器', 'ammonia_meter', 'NH3-200', '力合科技', 2, 'online', 11.8, datetime('now','-8 minutes'), '2025-02-10', '2025-02-10'),
(7, 'DTU-002', '数据采集终端', 'dtu', 'DTU-4100', '深圳有人物联网', 2, 'offline', 9.2, datetime('now','-3 hours'), '2025-02-10', '2025-02-10'),
(8, 'SEN-PH-002', 'pH传感器', 'ph_meter', 'PH-6100', '梅特勒-托利多', 3, 'online', 12.1, datetime('now','-6 minutes'), '2025-06-01', '2025-06-01');

-- =============================
-- 7. 备件库存
-- =============================
CREATE TABLE IF NOT EXISTS parts_inventory (
    id INTEGER PRIMARY KEY AUTOINCREMENT, part_code TEXT, part_name TEXT,
    category TEXT, spec TEXT, quantity REAL DEFAULT 0, min_quantity REAL DEFAULT 5,
    unit TEXT DEFAULT '个', location TEXT, site_id INTEGER, remark TEXT,
    updated_at TIMESTAMP DEFAULT (datetime('now','localtime')));
INSERT OR IGNORE INTO parts_inventory (id, part_code, part_name, category, quantity, min_quantity, unit, location) VALUES
(1, 'SP-PH-001', 'pH复合电极', '传感器', 8, 5, '支', 'A区-柜1-层2'),
(2, 'SP-DO-001', '溶解氧膜头', '传感器', 3, 5, '个', 'A区-柜1-层2'),
(3, 'SP-TB-001', '浊度光源', '传感器', 2, 3, '个', 'B区-柜2-层1');

-- =============================
-- 8. 通知数据（演示照片驳回通知）
-- =============================
INSERT OR IGNORE INTO notifications (id, user_id, source_type, source_id, title, content, created_at) VALUES
(1, 1, 'photo_review', 3, '照片被驳回', '五参数质控照片被驳回，原因：照片模糊，请重新拍摄上传', datetime('now','-1 hours'));

-- =============================
-- 验证
-- =============================
SELECT 'sites:'||COUNT(*) FROM sites;
SELECT 'alerts:'||COUNT(*) FROM alerts;
SELECT 'work_orders:'||COUNT(*) FROM work_orders;
SELECT 'devices:'||COUNT(*) FROM devices;
SELECT 'reagents:'||COUNT(*) FROM reagents;
SELECT 'operation_attachments:'||COUNT(*) FROM operation_attachments;
SELECT 'inspection_v2_plans:'||COUNT(*) FROM inspection_v2_plans;
SELECT 'data_arrival:'||COUNT(*) FROM data_arrival;
SELECT 'vehicles:'||COUNT(*) FROM vehicles;
SELECT 'notifications:'||COUNT(*) FROM notifications;
