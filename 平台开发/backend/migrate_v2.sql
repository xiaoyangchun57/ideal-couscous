-- ============================================================
-- 水质智慧运维平台 v2 数据库迁移脚本
-- 基于水文运维平台 (2026-06-24) 增量改造，不破坏原有表结构
-- 执行方式: sqlite3 data/water.db < migrate_v2.sql
-- ============================================================

-- -----------------------------------------------------------
-- 痛点2: 照片审核与归档
-- -----------------------------------------------------------

-- 2.1 照片需求模板表（定义每个站点类型/周期的拍照清单）
CREATE TABLE IF NOT EXISTS photo_requirements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    site_type TEXT NOT NULL DEFAULT 'water_quality',  -- 站点类型
    period TEXT NOT NULL,                              -- 'weekly' / 'monthly'
    seq INTEGER NOT NULL,                              -- 周检1~12 / 月检1~2
    item_name TEXT NOT NULL,                           -- "高锰酸盐指数仪器质控"
    photo_count INTEGER NOT NULL DEFAULT 1,            -- 需拍照张数
    review_required INTEGER NOT NULL DEFAULT 0,        -- 是否需要主管审核 1=是
    review_role TEXT DEFAULT NULL,                     -- 审核角色: 'supervisor'/'admin'
    created_at TIMESTAMP DEFAULT (datetime('now','localtime'))
);

-- 2.2 operation_attachments 增加审核字段
ALTER TABLE operation_attachments ADD COLUMN requirement_id INTEGER DEFAULT NULL;
ALTER TABLE operation_attachments ADD COLUMN review_status TEXT DEFAULT 'pending';
ALTER TABLE operation_attachments ADD COLUMN reviewer_id INTEGER DEFAULT NULL;
ALTER TABLE operation_attachments ADD COLUMN reviewed_at TIMESTAMP DEFAULT NULL;
ALTER TABLE operation_attachments ADD COLUMN reject_reason TEXT DEFAULT NULL;

-- 2.3 预置周检12项照片清单
INSERT INTO photo_requirements (site_type, period, seq, item_name, photo_count, review_required, review_role) VALUES
('water_quality', 'weekly', 1, '站房外观与周边环境', 1, 0, NULL),
('water_quality', 'weekly', 2, '采水系统运行状态', 1, 0, NULL),
('water_quality', 'weekly', 3, '预处理单元（过滤/沉淀）', 1, 0, NULL),
('water_quality', 'weekly', 4, '五参数分析仪质控', 2, 1, 'supervisor'),
('water_quality', 'weekly', 5, '氨氮分析仪质控', 2, 1, 'supervisor'),
('water_quality', 'weekly', 6, '总磷分析仪质控', 2, 1, 'supervisor'),
('water_quality', 'weekly', 7, '总氮分析仪质控', 2, 1, 'supervisor'),
('water_quality', 'weekly', 8, '高锰酸盐指数分析仪质控', 2, 1, 'supervisor'),
('water_quality', 'weekly', 9, '标液与试剂余量', 1, 0, NULL),
('water_quality', 'weekly', 10, '管路/供电/通信线路', 1, 0, NULL),
('water_quality', 'weekly', 11, '废液收集与处置', 1, 0, NULL),
('water_quality', 'weekly', 12, '运维人员签名/打卡', 1, 0, NULL);

-- 2.4 预置月检2项照片清单（seq接续周检）
INSERT INTO photo_requirements (site_type, period, seq, item_name, photo_count, review_required, review_role) VALUES
('water_quality', 'monthly', 1, '多点校准与线性检查', 2, 1, 'supervisor'),
('water_quality', 'monthly', 2, '仪器性能验证（标样核查）', 2, 1, 'supervisor');

-- -----------------------------------------------------------
-- 痛点3: 试剂全生命周期管理
-- -----------------------------------------------------------

-- 3.1 试剂主数据
CREATE TABLE IF NOT EXISTS reagents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,                 -- "氨氮标液"
    manufacturer TEXT DEFAULT NULL,     -- 厂家
    spec TEXT DEFAULT NULL,             -- 规格 "500mL/瓶"
    unit TEXT NOT NULL DEFAULT '瓶',
    shelf_life_days INTEGER DEFAULT 365, -- 保质期天数
    image_url TEXT DEFAULT NULL,
    created_at TIMESTAMP DEFAULT (datetime('now','localtime'))
);

-- 3.2 站点试剂库存
CREATE TABLE IF NOT EXISTS reagent_inventory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    site_id INTEGER NOT NULL,
    reagent_id INTEGER NOT NULL,
    current_qty REAL NOT NULL DEFAULT 0,       -- 当前余量（升/瓶）
    low_stock_threshold REAL DEFAULT 0.2,       -- 低库存阈值
    last_replaced_at TIMESTAMP DEFAULT NULL,
    updated_at TIMESTAMP DEFAULT (datetime('now','localtime')),
    FOREIGN KEY (site_id) REFERENCES sites(id),
    FOREIGN KEY (reagent_id) REFERENCES reagents(id),
    UNIQUE(site_id, reagent_id)
);

-- 3.3 试剂用量记录
CREATE TABLE IF NOT EXISTS reagent_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    site_id INTEGER NOT NULL,
    reagent_id INTEGER NOT NULL,
    used_qty REAL NOT NULL,                    -- 本次用量
    expected_duration_days INTEGER DEFAULT NULL, -- 预估可用天数
    operator_id INTEGER DEFAULT NULL,
    used_at TIMESTAMP DEFAULT (datetime('now','localtime')),
    remark TEXT DEFAULT NULL,
    FOREIGN KEY (site_id) REFERENCES sites(id),
    FOREIGN KEY (reagent_id) REFERENCES reagents(id)
);

-- 3.4 试剂更换记录（升级reagent_records逻辑，新增关联字段）
ALTER TABLE reagent_records ADD COLUMN old_batch_no TEXT DEFAULT NULL;
ALTER TABLE reagent_records ADD COLUMN new_batch_no TEXT DEFAULT NULL;
ALTER TABLE reagent_records ADD COLUMN old_qty REAL DEFAULT NULL;
ALTER TABLE reagent_records ADD COLUMN new_qty REAL DEFAULT NULL;

-- 3.5 试剂告警
CREATE TABLE IF NOT EXISTS reagent_alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    site_id INTEGER NOT NULL,
    reagent_id INTEGER NOT NULL,
    alert_type TEXT NOT NULL,                   -- 'low_stock'/'near_expiry'/'expired'
    current_qty REAL DEFAULT NULL,
    threshold_qty REAL DEFAULT NULL,
    alert_at TIMESTAMP DEFAULT (datetime('now','localtime')),
    handled INTEGER DEFAULT 0,
    handled_at TIMESTAMP DEFAULT NULL,
    FOREIGN KEY (site_id) REFERENCES sites(id),
    FOREIGN KEY (reagent_id) REFERENCES reagents(id)
);

-- 3.6 试剂库存增加「可用天数（手动设置）/临期阈值」
-- 剩余使用时间由运维人员经验手动填写，不做预测
ALTER TABLE reagent_inventory ADD COLUMN expected_duration_days INTEGER DEFAULT NULL; -- 手动设的可用天数
ALTER TABLE reagent_inventory ADD COLUMN warning_days INTEGER DEFAULT 7;          -- 临期预警阈值（天）

-- -----------------------------------------------------------
-- 痛点1: 车辆管理
-- -----------------------------------------------------------

-- 1.1 车辆台账
CREATE TABLE IF NOT EXISTS vehicles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plate_no TEXT NOT NULL UNIQUE,             -- 车牌号
    model TEXT DEFAULT NULL,                    -- 车型
    seats INTEGER DEFAULT 5,
    status TEXT DEFAULT 'idle',                 -- 'idle'/'in_use'/'maintenance'
    current_mileage REAL DEFAULT 0,
    last_maintenance_at TIMESTAMP DEFAULT NULL,
    next_maintenance_mileage REAL DEFAULT 5000,
    created_at TIMESTAMP DEFAULT (datetime('now','localtime'))
);

-- 1.2 用车申请
CREATE TABLE IF NOT EXISTS vehicle_applications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    vehicle_id INTEGER,                       -- 移动端极简申请可空（仅填事由）
    applicant_id INTEGER,                     -- 缺省取当前登录人
    start_at TIMESTAMP,                      -- 极简申请可空
    end_at TIMESTAMP,                        -- 极简申请可空
    destination TEXT DEFAULT NULL,
    reason TEXT DEFAULT NULL,
    status TEXT DEFAULT 'pending',              -- 'pending'/'approved'/'rejected'/'cancelled'
    approver_id INTEGER DEFAULT NULL,
    approved_at TIMESTAMP DEFAULT NULL,
    reject_reason TEXT DEFAULT NULL,
    created_at TIMESTAMP DEFAULT (datetime('now','localtime')),
    FOREIGN KEY (vehicle_id) REFERENCES vehicles(id),
    FOREIGN KEY (applicant_id) REFERENCES users(id)
);

-- 1.3 出车记录
CREATE TABLE IF NOT EXISTS vehicle_use_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    application_id INTEGER NOT NULL,
    start_mileage REAL DEFAULT NULL,
    end_mileage REAL DEFAULT NULL,
    returned_at TIMESTAMP DEFAULT NULL,
    FOREIGN KEY (application_id) REFERENCES vehicle_applications(id)
);

-- 1.4 加油记录
CREATE TABLE IF NOT EXISTS vehicle_refueling_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    vehicle_id INTEGER NOT NULL,
    refuel_at TIMESTAMP DEFAULT (datetime('now','localtime')),
    liters REAL NOT NULL,
    amount REAL DEFAULT NULL,
    mileage_at REAL DEFAULT NULL,
    remark TEXT DEFAULT NULL,
    FOREIGN KEY (vehicle_id) REFERENCES vehicles(id)
);

-- 1.5 保养记录
CREATE TABLE IF NOT EXISTS vehicle_maintenance_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    vehicle_id INTEGER NOT NULL,
    maint_type TEXT NOT NULL,                   -- 'routine'/'repair'/'inspection'
    maint_at TIMESTAMP DEFAULT (datetime('now','localtime')),
    mileage_at REAL DEFAULT NULL,
    next_maint_mileage REAL DEFAULT NULL,
    items TEXT DEFAULT NULL,
    cost REAL DEFAULT NULL,
    remark TEXT DEFAULT NULL,
    FOREIGN KEY (vehicle_id) REFERENCES vehicles(id)
);

-- -----------------------------------------------------------
-- 痛点4: 周巡检计划系统化
-- -----------------------------------------------------------

CREATE TABLE IF NOT EXISTS weekly_inspection_plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,                   -- 巡检人
    week_start DATE NOT NULL,                   -- 周一日期
    plan_data TEXT NOT NULL DEFAULT '{}',       -- JSON: {"周一":["site_id"],"周二":[],...}
    vehicle_id INTEGER DEFAULT NULL,
    status TEXT DEFAULT 'draft',                -- 'draft'/'submitted'/'approved'/'archived'
    approver_id INTEGER DEFAULT NULL,
    submitted_at TIMESTAMP DEFAULT NULL,
    approved_at TIMESTAMP DEFAULT NULL,
    remarks TEXT DEFAULT NULL,
    created_at TIMESTAMP DEFAULT (datetime('now','localtime')),
    FOREIGN KEY (user_id) REFERENCES users(id),
    FOREIGN KEY (vehicle_id) REFERENCES vehicles(id)
);

-- -----------------------------------------------------------
-- 痛点5: 阈值规则引擎
-- -----------------------------------------------------------

CREATE TABLE IF NOT EXISTS threshold_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,                         -- "GB3838 III类 pH"
    scope TEXT NOT NULL DEFAULT 'metric',       -- 'global'/'site'/'metric'
    site_id INTEGER DEFAULT NULL,
    metric TEXT DEFAULT NULL,                   -- 'pH'/'CODMn'/'氨氮'...
    rule_type TEXT NOT NULL,                    -- 'static'/'spc'/'historical'/'correlated'
    conditions TEXT NOT NULL DEFAULT '{}',       -- JSON 规则参数
    severity TEXT DEFAULT 'warning',            -- 'info'/'warning'/'critical'
    enabled INTEGER DEFAULT 1,
    created_by INTEGER DEFAULT NULL,
    created_at TIMESTAMP DEFAULT (datetime('now','localtime')),
    FOREIGN KEY (site_id) REFERENCES sites(id)
);

-- 5.1 预置 GB3838-2002 III类水标准阈值
INSERT INTO threshold_rules (name, scope, metric, rule_type, conditions, severity) VALUES
('pH 标准限值 (III类)', 'metric', 'pH', 'static',
 '{"min":6.0,"max":9.0,"unit":"无量纲"}', 'warning'),
('高锰酸盐指数 标准限值 (III类)', 'metric', 'CODMn', 'static',
 '{"max":6.0,"unit":"mg/L"}', 'warning'),
('氨氮 标准限值 (III类)', 'metric', 'NH3-N', 'static',
 '{"max":1.0,"unit":"mg/L"}', 'warning'),
('总磷 标准限值 (III类)', 'metric', 'TP', 'static',
 '{"max":0.2,"unit":"mg/L"}', 'warning'),
('总氮 标准限值 (III类)', 'metric', 'TN', 'static',
 '{"max":1.0,"unit":"mg/L"}', 'warning'),
('溶解氧 标准限值 (III类)', 'metric', 'DO', 'static',
 '{"min":5.0,"unit":"mg/L"}', 'warning'),
('浊度 标准限值 (III类)', 'metric', 'turbidity', 'static',
 '{"max":10.0,"unit":"NTU"}', 'warning');

-- -----------------------------------------------------------
-- 痛点2补充：数据质量审核表
-- -----------------------------------------------------------

CREATE TABLE IF NOT EXISTS data_reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    site_id INTEGER NOT NULL,
    metric TEXT NOT NULL,
    value REAL NOT NULL,
    recorded_at TIMESTAMP NOT NULL,
    auto_result TEXT DEFAULT NULL,              -- 'pass'/'reject'
    auto_reason TEXT DEFAULT NULL,
    smart_result TEXT DEFAULT NULL,             -- 'pass'/'suspicious'
    smart_score REAL DEFAULT NULL,              -- SPC/孤立森林得分
    manual_result TEXT DEFAULT NULL,            -- 'approved'/'rejected'
    manual_reason TEXT DEFAULT NULL,
    reviewer_id INTEGER DEFAULT NULL,
    reviewed_at TIMESTAMP DEFAULT NULL,
    status TEXT DEFAULT 'pending',              -- 'pending'/'auto_reviewed'/'smart_reviewed'/'manual_reviewed'/'archived'
    created_at TIMESTAMP DEFAULT (datetime('now','localtime')),
    FOREIGN KEY (site_id) REFERENCES sites(id)
);

-- -----------------------------------------------------------
-- 痛点2补充：人工异常主动上报
-- -----------------------------------------------------------

CREATE TABLE IF NOT EXISTS manual_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    site_id INTEGER DEFAULT NULL,
    report_type TEXT NOT NULL,                  -- 'sensory'/'equipment'/'environment'/'violation'/'pollution'
    description TEXT DEFAULT NULL,
    photo_urls TEXT DEFAULT '[]',               -- JSON数组
    gps_lat REAL DEFAULT NULL,
    gps_lng REAL DEFAULT NULL,
    reporter_id INTEGER NOT NULL,
    reported_at TIMESTAMP DEFAULT (datetime('now','localtime')),
    order_no TEXT DEFAULT NULL,                 -- 关联工单号
    status TEXT DEFAULT 'open',                 -- 'open'/'dispatched'/'resolved'/'archived'
    FOREIGN KEY (site_id) REFERENCES sites(id),
    FOREIGN KEY (reporter_id) REFERENCES users(id)
);

-- ============================================================
-- 异常编码参考表（Step 2）
-- ============================================================
CREATE TABLE IF NOT EXISTS anomaly_codes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT UNIQUE NOT NULL,              -- Q-001
    category TEXT NOT NULL,                 -- quality/equipment/custom
    severity TEXT DEFAULT 'warning',        -- info/warning/critical
    title TEXT NOT NULL,
    description TEXT DEFAULT '',
    suggestion TEXT DEFAULT '',             -- 处理建议
    is_active INTEGER DEFAULT 1,
    created_at TIMESTAMP DEFAULT (datetime('now','localtime'))
);

INSERT OR IGNORE INTO anomaly_codes (code, category, severity, title, description, suggestion) VALUES
('Q-001', 'quality', 'critical', '量程超限', '监测数据超出仪器测量或GB3838标准的合理范围', '检查传感器或电极是否老化、校准液是否失效，必要时更换'),
('Q-002', 'quality', 'warning', '趋势突变', '数据在短时间内发生>5σ的剧烈跳变', '检查管路是否进气泡、水样是否更换、是否存在突发污染'),
('Q-003', 'quality', 'warning', '关联异常', '多指标间逻辑一致性违反预期关联关系', '检查交叉污染、仪器间干扰、异常水样'),
('Q-004', 'quality', 'warning', '数据僵死', '连续多笔数据值完全不变，疑似传感器冻结或通讯故障', '检查传感器探头是否被污物包裹、模数转换是否卡死'),
('Q-005', 'quality', 'info', '缺失异常', '数据时间戳出现空档，传输不连续', '检查通信链路、数据采集器是否有丢包'),
('E-001', 'equipment', 'critical', '设备离线', '设备心跳超时被判定离线', '自动触发维修工单，检查供电和网络'),
('M-001', 'custom', 'info', '维护标记', '设备维护期间产生的数据未正确标记', '补录运维标记，确认维护时段数据是否需要剔除');

-- ============================================================
-- 迁移完成
-- ============================================================
