#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
全平台试运行数据生成 v2 — 267站全量 + 6400条传感器数据 + 13异常
"""
import os, sys, sqlite3, shutil, random
from datetime import datetime, timedelta

BASE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE, 'data', 'water.db')

random.seed(42)

def table_exists(db, name):
    return db.execute("SELECT count(*) FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone()[0] > 0

def clear_business_tables(db):
    tables = [
        'alerts','work_orders','sensor_data','device_shadows','data_arrival',
        'timeline_events','weather_data','rainfall_forecast','water_level_checks',
        'inspection_plans','inspection_tasks','inspection_schemes','inspection_scheme_items',
        'spare_parts_inventory','spare_part_requests','inventory_logs','maintenance_plans',
        'maintenance_templates','hotline_events','water_level_checks','timeline_events'
    ]
    for t in tables:
        if table_exists(db, t):
            db.execute(f"DELETE FROM {t}")
    print("[SeedE2E] 业务表已清空，保留站点和用户")

def fmt(dt):
    return dt.strftime('%Y-%m-%d %H:%M:%S')

# =====================================================
# 异常站点定义
# =====================================================
ANOMALIES = {
    # 1-3: data_gap (缺失)
    'gap_sites': [
        {'id': 5, 'name': '万家埠', 'type': 'evaporation', 'gap_hours': 24, 'metric': 'evaporation',
         'msg': '数据缺失：蒸发量已有1440分钟未更新', 'level': 'red'},
        {'id': 56, 'name': '红谷', 'type': 'rainfall', 'gap_hours': 2, 'metric': 'precipitation',
         'msg': '数据缺失：降雨量已有120分钟未更新', 'level': 'yellow'},
        {'id': 188, 'name': '蛟塘', 'type': 'soil_moisture', 'gap_hours': 3, 'metric': 'soil_moisture',
         'msg': '数据缺失：土壤含水量已有180分钟未更新', 'level': 'yellow'},
    ],
    # 4-5: data_freeze (冻结)
    'freeze_sites': [
        {'id': 13, 'name': '谢家垄', 'type': 'water_level', 'metric': 'water_level', 'freeze_val': 18.25,
         'level': 'yellow', 'msg': '数据冻结：水位连续12条记录值一致（18.25），传感器可能故障'},
        {'id': 175, 'name': '星子', 'type': 'groundwater', 'metric': 'groundwater_level', 'freeze_val': 7.38,
         'level': 'yellow', 'msg': '数据冻结：地下水位连续12条记录值一致（7.38），传感器可能故障'},
    ],
    # 6-8: data_spike (跳变)
    'spike_sites': [
        {'id': 52, 'name': '霞源', 'type': 'rainfall', 'metric': 'precipitation',
         'spike_val': 12.5, 'normal_val': 0.0, 'spike_idx': 18,
         'level': 'orange',
         'msg': '数据异常：本站上报雨量12.5mm/h，气象数据未报降雨，疑似传感器故障'},
        {'id': 234, 'name': '岗前', 'type': 'station_yard', 'metric': 'noise',
         'spike_val': 95.0, 'normal_val': 48.0, 'spike_idx': 20,
         'level': 'orange',
         'msg': '数据异常：噪声突升至95.0dB后回落，疑似传感器干扰'},
        {'id': 166, 'name': '南钢', 'type': 'groundwater', 'metric': 'water_quality',
         'spike_val': 9.5, 'normal_val': 7.0, 'spike_idx': 19,
         'level': 'orange',
         'msg': '数据异常：水质pH突升至9.5后回落至7.0，疑似传感器故障'},
    ],
    # 9-10: data_drift (漂移) — 用 data_spike 但消息标注
    'drift_sites': [
        {'id': 19, 'name': '安义', 'type': 'water_level', 'metric': 'water_level',
         'start_val': 22.0, 'end_val': 24.5, 'hours': 24,
         'level': 'yellow',
         'msg': '数据漂移：水位24小时内从22.0m缓慢升至24.5m，变化2.5m，疑似传感器零漂'},
        {'id': 204, 'name': '石鼻', 'type': 'soil_moisture', 'metric': 'soil_moisture',
         'start_val': 65.0, 'end_val': 38.0, 'hours': 24,
         'level': 'yellow',
         'msg': '数据漂移：土壤含水量24小时内从65%缓慢降至38%，疑似传感器漂移'},
    ],
    # 11-13: device_status (设备异常)
    'device_sites': [
        {'id': 234, 'name': '岗前', 'type': 'station_yard', 'voltage': 11.3,
         'level': 'orange', 'msg': '设备异常：备用电源电压偏低(11.3V)'},
        {'id': 3, 'name': '新祺周', 'type': 'hydrology', 'voltage': 10.8,
         'level': 'red', 'msg': '设备异常：通信模块离线，电压10.8V'},
    ],
}

def generate_sensor_data(db):
    """为267站生成24小时逐小时数据，然后注入13个异常"""
    print("[SeedE2E] 加载站点列表...")
    sites = db.execute("SELECT id, type, name FROM sites ORDER BY id").fetchall()
    print(f"[SeedE2E] 共 {len(sites)} 个站点，开始生成传感器数据...")

    base_time = datetime(2026, 6, 21, 18, 0, 0)
    record_id = [0]  # mutable counter

    # 站型的默认指标
    DEFAULT_METRICS = {
        'hydrology': [('water_level', 12.0, 0.3, 'm'), ('flow', 800, 100, 'm³/s')],
        'water_level': [('water_level', 20.0, 0.3, 'm')],
        'rainfall': [('precipitation', 0.0, 0.0, 'mm')],
        'groundwater': [('groundwater_level', 25.0, 0.4, 'm'), ('water_quality', 7.0, 0.1, 'pH')],
        'soil_moisture': [('soil_moisture', 55.0, 2.0, '%'), ('soil_temperature', 22.0, 0.5, '°C')],
        'evaporation': [('evaporation', 4.0, 0.2, 'mm'), ('temperature', 28.0, 1.0, '°C')],
        'station_yard': [('temperature', 26.0, 0.5, '°C'), ('noise', 48.0, 3.0, 'dB')],
    }

    # 收集异常站点类型映射
    anomaly_ids = set()
    for key in ['gap_sites','freeze_sites','spike_sites','drift_sites','device_sites']:
        for s in ANOMALIES[key]:
            anomaly_ids.add(s['id'])

    batch = []
    BATCH_SIZE = 500

    def flush():
        if batch:
            db.executemany(
                "INSERT INTO sensor_data (id,site_id,metric,value,unit,recorded_at) VALUES (?,?,?,?,?,?)",
                batch
            )
            batch.clear()

    for site in sites:
        sid, stype = site['id'], site['type']
        metrics = DEFAULT_METRICS.get(stype, [('temperature', 25.0, 1.0, '°C')])
        
        # 判断是否为异常站点
        is_gap = any(s['id'] == sid for s in ANOMALIES['gap_sites'])
        is_freeze = any(s['id'] == sid for s in ANOMALIES['freeze_sites'])
        is_spike = any(s['id'] == sid for s in ANOMALIES['spike_sites'])
        is_drift = any(s['id'] == sid for s in ANOMALIES['drift_sites'])

        for metric, base, var, unit in metrics:
            for hour in range(24):
                record_id[0] += 1
                t = base_time + timedelta(hours=hour)

                # 默认：正常波动
                value = round(random.gauss(base, var), 2)
                if value < 0:
                    value = 0

                # --- 异常注入 ---
                # data_gap: 截断数据，不生成最近 N 小时的记录
                if is_gap:
                    gs = next(s for s in ANOMALIES['gap_sites'] if s['id'] == sid)
                    if metric == gs['metric']:
                        if hour >= (24 - gs['gap_hours']):
                            continue  # 跳过最近N小时的记录

                # data_freeze: 最后12小时值不变
                if is_freeze:
                    fs = next(s for s in ANOMALIES['freeze_sites'] if s['id'] == sid)
                    if metric == fs['metric'] and hour >= 12:
                        value = fs['freeze_val']

                # data_spike: 在指定位置突跳后回落
                if is_spike:
                    ss = next(s for s in ANOMALIES['spike_sites'] if s['id'] == sid)
                    if metric == ss['metric']:
                        if hour == ss['spike_idx']:
                            value = ss['spike_val']
                        elif hour == ss['spike_idx'] + 1:
                            value = ss['normal_val']
                        elif hour > ss['spike_idx'] + 1:
                            value = ss['normal_val'] + round(random.gauss(0, var * 0.5), 2)
                            if value < 0: value = 0

                # data_drift: 缓慢偏移
                if is_drift:
                    ds = next(s for s in ANOMALIES['drift_sites'] if s['id'] == sid)
                    if metric == ds['metric']:
                        progress = hour / 23.0
                        value = round(ds['start_val'] + (ds['end_val'] - ds['start_val']) * progress, 2)

                batch.append((record_id[0], sid, metric, value, unit, fmt(t)))
                if len(batch) >= BATCH_SIZE:
                    flush()

    flush()
    print(f"[SeedE2E] 传感器数据已生成: {record_id[0]} 条")

def inject_device_anomalies(db):
    """在 device_shadows 中注入设备异常"""
    print("[SeedE2E] 注入设备异常...")
    # 万家埠(5)设为离线
    db.execute("UPDATE device_shadows SET status='offline', voltage=10.5, last_data_time=NULL WHERE site_id=5")
    # 岗前(234) 备用电源电压偏低 (需确保有power设备)
    db.execute("UPDATE device_shadows SET voltage=11.3 WHERE site_id=234 AND device_type='power'")
    # 如果没有power设备，插入一条
    exist = db.execute("SELECT id FROM device_shadows WHERE site_id=234 AND device_type='power'").fetchone()
    if not exist:
        db.execute(
            "INSERT INTO device_shadows (site_id,device_code,device_name,device_type,status,voltage,last_data_time) VALUES (?,?,?,?,?,?,?)",
            (234, 'GQ-PWR-01', '岗前备用电源', 'power', 'online', 11.3, '2026-06-22 18:00:00')
        )
    # 新祺周(3) 通信模块离线
    db.execute("UPDATE device_shadows SET status='offline', voltage=10.8, last_data_time=NULL WHERE site_id=3")
    # 如果有多个设备，至少1台离线
    exist = db.execute("SELECT id FROM device_shadows WHERE site_id=3 AND status='offline'").fetchone()
    if not exist:
        db.execute(
            "INSERT INTO device_shadows (site_id,device_code,device_name,device_type,status,voltage,last_data_time) VALUES (?,?,?,?,?,?,?)",
            (3, 'XQZ-COMM-01', '新祺周通信模块', 'comm', 'offline', 10.8, None)
        )

def generate_alerts(db):
    """生成告警，仅对异常站点"""
    print("[SeedE2E] 生成告警...")
    alerts = []
    aid = 0

    # data_gap 告警
    for gs in ANOMALIES['gap_sites']:
        aid += 1
        alerts.append((aid, gs['id'], 'data_gap', 0, gs['level'], gs['msg'],
                       'pending' if gs['gap_hours'] <= 3 else 'acknowledged',
                       'auto', 'converted' if gs['gap_hours'] > 12 else 'pending',
                       f"WO-20260622-{100+aid:03d}" if gs['gap_hours'] > 3 else None,
                       '2026-06-22 08:00:00' if gs['gap_hours'] > 20 else '2026-06-22 16:00:00'))

    # data_freeze 告警
    for fs in ANOMALIES['freeze_sites']:
        aid += 1
        alerts.append((aid, fs['id'], 'data_freeze', fs['freeze_val'], fs['level'], fs['msg'],
                       'pending', 'manual', 'pending_review', None, '2026-06-22 15:00:00'))

    # data_spike 告警
    for ss in ANOMALIES['spike_sites']:
        aid += 1
        alerts.append((aid, ss['id'], 'data_spike', ss['spike_val'], ss['level'], ss['msg'],
                       'pending', 'manual', 'pending_review', None, '2026-06-22 14:00:00'))

    # data_drift 告警（用 data_spike 类型）
    for ds in ANOMALIES['drift_sites']:
        aid += 1
        alerts.append((aid, ds['id'], 'data_spike', ds['end_val'], ds['level'], ds['msg'],
                       'pending', 'manual', 'pending_review', None, '2026-06-22 17:00:00'))

    # device_status 告警 — 修正：新祺周(3)也应自动转工单
    for ds in ANOMALIES['device_sites']:
        aid += 1
        alerts.append((aid, ds['id'], 'device_status', ds.get('voltage', 0), ds['level'], ds['msg'],
                       'pending',
                       'auto', 'converted',
                       f"WO-20260622-{100+aid:03d}",
                       '2026-06-22 14:30:00'))

    for a in alerts:
        db.execute(
            """INSERT INTO alerts (id,site_id,metric,value,level,message,status,flow_type,flow_status,related_order_no,created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""", a
        )
    print(f"[SeedE2E] 告警已生成: {len(alerts)} 条")

def generate_work_orders(db):
    """生成工单（A级告警自动转的）"""
    orders = [
        ('WO-20260622-101', 5, 'auto', '告警自动转工单', 'critical',
         '[自动] 数据缺失：蒸发量已有1440分钟未更新',
         '万家埠站数据停报超过24小时，需排查通信和电源', '', '张建国',
         'closed', '2026-06-23 08:00', '2026-06-21 08:00:00'),
        ('WO-20260622-111', 234, 'auto', '告警自动转工单', 'urgent',
         '[自动] 设备异常：备用电源电压偏低(11.3V)',
         '岗前站院备用电源电压偏低，需现场检查', '', '黎明',
         'in_progress', '2026-06-23 14:30', '2026-06-22 14:30:00'),
        ('WO-20260622-112', 3, 'auto', '告警自动转工单', 'critical',
         '[自动] 设备异常：新祺周通信模块离线',
         '新祺周通信模块离线，电压10.8V，需紧急排查', '', '王建军',
         'pending', '2026-06-23 14:30', '2026-06-22 14:30:00'),
    ]
    for o in orders:
        db.execute(
            """INSERT INTO work_orders (order_no,site_id,source,event_type,level,title,description,images,assignee,status,sla_deadline,created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""", o
        )
    print(f"[SeedE2E] 工单已生成: {len(orders)} 条")

def generate_timeline(db):
    """为所有告警生成完整时间线"""
    events = []
    eid = 0

    # 获取所有告警
    all_alerts = db.execute("SELECT id, site_id, flow_type, flow_status, related_order_no, message FROM alerts ORDER BY id").fetchall()

    alert_site_names = {r['id']: r['site_id'] for r in all_alerts}
    # 查站点名称
    site_names = {}
    for r in db.execute("SELECT id, name FROM sites").fetchall():
        site_names[r['id']] = r['name']

    for a in all_alerts:
        aid = a['id']
        site_name = site_names.get(a['site_id'], f"站{a['site_id']}")
        eid += 1
        events.append((eid, 'alert', aid, 'created', '系统',
                       f'{site_name}：{a["message"][:40]}',
                       '2026-06-22 14:00:00' if aid < 11 else '2026-06-22 14:30:00'))

        # A级告警自动转工单
        if a['flow_type'] == 'auto' and a['related_order_no']:
            eid += 1
            events.append((eid, 'alert', aid, 'auto_converted', '系统',
                           f'自动转工单 {a["related_order_no"]}',
                           '2026-06-22 14:30:30'))
            # 对应工单的时间线
            ono = a['related_order_no']
            if ono == 'WO-20260622-101':  # 万家埠
                eid += 1; events.append((eid, 'order', ono, 'created', '系统', '告警1自动转工单', '2026-06-21 08:00:30'))
                eid += 1; events.append((eid, 'order', ono, 'closed', '系统', '万家埠离线故障已修复', '2026-06-22 10:00:00'))
            elif ono == 'WO-20260622-111':  # 岗前
                eid += 1; events.append((eid, 'order', ono, 'created', '系统', '告警11自动转工单', '2026-06-22 14:30:30'))
                eid += 1; events.append((eid, 'order', ono, 'accepted', '黎明', '已接单', '2026-06-22 14:45:00'))
                eid += 1; events.append((eid, 'order', ono, 'in_progress', '黎明', '已出发前往岗前站', '2026-06-22 14:50:00'))
            elif ono == 'WO-20260622-112':  # 新祺周
                eid += 1; events.append((eid, 'order', ono, 'created', '系统', '告警12自动转工单', '2026-06-22 14:30:30'))
                eid += 1; events.append((eid, 'order', ono, 'accepted', '王建军', '已接单，正在联系现场', '2026-06-22 15:00:00'))

    for e in events:
        db.execute("INSERT INTO timeline_events (id,source_type,source_id,event_type,operator,remark,created_at) VALUES (?,?,?,?,?,?,?)", e)
    print(f"[SeedE2E] 时间线已生成: {len(events)} 条")

def generate_device_shadows(db):
    """确保每个站点有设备，用于状态计算"""
    print("[SeedE2E] 检查设备数据完整性...")
    sites = db.execute("SELECT id, name FROM sites ORDER BY id").fetchall()
    for s in sites:
        cnt = db.execute("SELECT count(*) as c FROM device_shadows WHERE site_id=?", (s['id'],)).fetchone()['c']
        if cnt == 0:
            db.execute(
                "INSERT INTO device_shadows (site_id,device_code,device_name,device_type,status,voltage,last_data_time) VALUES (?,?,?,?,?,?,?)",
                (s['id'], f"GEN-{s['id']:04d}", f"{s['name']}通用传感器", 'sensor', 'online', 12.0, '2026-06-22 18:00:00')
            )

def generate_user_sites(db):
    """所有站点分配给管理员，前50站分配给对应区域负责人"""
    print("[SeedE2E] 生成用户站点权限...")
    db.execute("DELETE FROM user_sites")
    db.execute("INSERT INTO user_sites (user_id, site_id) SELECT 1, id FROM sites")
    for uid, start, end in [(2,1,70),(3,71,140),(4,141,210),(5,211,267)]:
        for sid in range(start, end+1):
            db.execute("INSERT OR IGNORE INTO user_sites (user_id, site_id) VALUES (?,?)", (uid, sid))

def run_validation(db):
    print("\n" + "=" * 50)
    print("[SeedE2E] 数据校验...")
    print("=" * 50)
    total_sites = db.execute("SELECT count(*) as c FROM sites").fetchone()['c']
    total_alerts = db.execute("SELECT count(*) as c FROM alerts").fetchone()['c']
    pending_alerts = db.execute("SELECT count(*) as c FROM alerts WHERE status='pending'").fetchone()['c']
    pending_review = db.execute("SELECT count(*) as c FROM alerts WHERE flow_type='manual' AND flow_status='pending_review'").fetchone()['c']
    total_orders = db.execute("SELECT count(*) as c FROM work_orders").fetchone()['c']
    total_sensors = db.execute("SELECT count(*) as c FROM sensor_data").fetchone()['c']
    total_timeline = db.execute("SELECT count(*) as c FROM timeline_events").fetchone()['c']
    auto_converted = db.execute("SELECT count(*) as c FROM alerts WHERE flow_type='auto' AND flow_status='converted'").fetchone()['c']
    auto_pending = db.execute("SELECT count(*) as c FROM alerts WHERE flow_type='auto' AND flow_status='pending'").fetchone()['c']

    print(f"  站点: {total_sites}")
    print(f"  告警: {total_alerts} (pending={pending_alerts}, pending_review={pending_review}, auto_converted={auto_converted}, auto_pending={auto_pending})")
    print(f"  工单: {total_orders}")
    print(f"  传感器数据: {total_sensors}")
    print(f"  时间线: {total_timeline}")
    print(f"\n  ✓ 核心校验通过" if total_sites >= 200 and total_alerts >= 10 else "\n  ⚠ 部分校验异常")

if __name__ == '__main__':
    print("=" * 50)
    print("  全平台试运行数据生成 v2 (267站)")
    print("=" * 50)
    backup = DB_PATH.replace('.db', f'-backup-{datetime.now().strftime("%Y%m%d-%H%M%S")}.db')
    shutil.copy2(DB_PATH, backup)
    print(f"[SeedE2E] 已备份: {backup}")

    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA busy_timeout=30000")

    clear_business_tables(db)
    db.commit()

    generate_sensor_data(db)
    db.commit()

    inject_device_anomalies(db)
    db.commit()

    generate_device_shadows(db)
    db.commit()

    generate_alerts(db)
    db.commit()

    generate_work_orders(db)
    db.commit()

    generate_timeline(db)
    db.commit()

    generate_user_sites(db)
    db.commit()

    run_validation(db)
    db.close()
    print("[SeedE2E] 完成!")
