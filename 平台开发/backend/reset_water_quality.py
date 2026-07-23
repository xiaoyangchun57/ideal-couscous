"""水质监测数据库重置脚本 — 保留原站点名称/坐标，改为水质类型"""
import sqlite3, os, json, random
from datetime import datetime, timedelta

BASE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE, 'data', 'water.db')
JSON_PATH = os.path.join(BASE, 'site_data.json')

WQ_DEVICES = [
    ('多参数水质分析仪', 'multi_param_analyzer', 'WQA-200', '哈希'),
    ('pH计', 'ph_meter', 'PHG-2088', '上海雷磁'),
    ('溶解氧传感器', 'do_sensor', 'DOG-3082', '上海雷磁'),
    ('浊度仪', 'turbidity_meter', 'TURB-3000', '哈希'),
    ('氨氮分析仪', 'ammonia_analyzer', 'NH3N-2000', '聚光科技'),
    ('COD分析仪', 'cod_analyzer', 'CODcr-2000', '聚光科技'),
    ('数据采集传输终端', 'dtu', 'DTU-WQ01', '厦门四信'),
]

def log(msg):
    print(f'[WQ-Reset] {msg}')

def main():
    log("开始重置为水质监测数据库...")
    db = sqlite3.connect(DB_PATH, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA busy_timeout=5000")

    # 1. 清空数据表（保留 users）
    log("清空旧数据...")
    for tbl in ['sensor_data', 'sensor_data_daily', 'sensor_data_hourly', 'sensor_data_raw',
                'alerts', 'work_orders', 'device_shadows', 'device_recycle',
                'inspection_plans', 'inspection_plan_items', 'inspection_tasks',
                'inspection_checkins', 'inspection_configs', 'inspection_schedules',
                'inspection_template_items', 'inspection_templates',
                'insp_plans', 'insp_plan_items',
                'maintenance_plans', 'maintenance_templates',
                'notifications', 'timeline_events', 'spare_part_requests',
                'spare_parts_inventory', 'hotline_events', 'data_arrival',
                'operation_attachments', 'plan_sites', 'water_level_checks',
                'weather_data', 'calibration_templates', 'inventory_logs',
                'inspection_photo_types', 'inspection_reminders',
                'inspection_schemes', 'inspection_scheme_items',
                'inspection_skip_logs', 'user_sites', 'data_sources']:
        try:
            db.execute(f'DELETE FROM {tbl}')
        except:
            pass
    db.execute('DELETE FROM sites')
    db.commit()
    log("旧数据已清空。")

    # 2. 从 site_data.json 导入前50个站点
    log("从 site_data.json 导入前50个站点...")
    with open(JSON_PATH, 'r', encoding='utf-8') as f:
        all_sites = json.load(f)

    selected = all_sites[:50]
    count = 0
    for s in selected:
        lat = s.get('lat') or random.uniform(28.4, 29.2)
        lng = s.get('lng') or random.uniform(115.5, 116.5)
        name = s.get('name', '')
        code = s.get('code', f'WQ{count+1:03d}')
        district = s.get('address', '') or ''
        db.execute("""
            INSERT INTO sites (code, name, type, gps_lat, gps_lng, district, status)
            VALUES (?, ?, ?, ?, ?, ?, 'online')
        """, (code, name, 'water_quality', lat, lng, district))
        count += 1
    db.commit()
    log(f"已导入 {count} 个水质站点。")

    # 3. 创建设备（每站7台）
    log("创建水质监测设备...")
    sites = db.execute("SELECT id, code FROM sites ORDER BY id").fetchall()
    dev_count = 0
    for site in sites:
        sid = site['id']
        scode = site['code']
        for i, (dname, dtype, dmodel, dmfr) in enumerate(WQ_DEVICES):
            install_date = f"20{19 + (sid % 5):02d}-{(sid % 12)+1:02d}-{(i*5+10) % 28 + 1:02d}"
            db.execute("""
                INSERT INTO device_shadows
                (site_id, device_code, device_name, device_type, device_model,
                 manufacturer, install_date, status, battery, voltage)
                VALUES (?,?,?,?,?,?,?,?,?,?)
            """, (sid, f'{scode}-{i+1:02d}{dtype[:4].upper()}', dname, dtype,
                  dmodel, dmfr, install_date, 'online',
                  round(random.uniform(60,100), 0),
                  round(random.uniform(11.8, 14.2), 1)))
            dev_count += 1
    db.commit()
    log(f"已创建 {dev_count} 台设备。")

    # 4. 生成演示告警（4条水质场景）
    log("生成演示告警...")
    now = datetime.now()
    site_ids = [r['id'] for r in db.execute("SELECT id FROM sites ORDER BY id LIMIT 10").fetchall()]
    if len(site_ids) >= 4:
        # 告警1: 站点1 pH突变
        db.execute("""
            INSERT INTO alerts (site_id,metric,value,level,message,status,created_at,flow_type,flow_status,related_order_no)
            VALUES (?,?,?,?,?,?,?,?,?,?)
        """, (site_ids[0], 'ph', 4.82, 'red',
              f'pH值严重偏低：{db.execute("SELECT name FROM sites WHERE id=?", (site_ids[0],)).fetchone()[0]} pH 4.82（正常范围6.0-9.0），疑似酸性废水排放',
              'pending', (now - timedelta(hours=1.5)).strftime('%Y-%m-%d %H:%M:%S'),
              'auto', 'converted', 'WO-DEMO-001'))
        db.execute("INSERT INTO work_orders (order_no,site_id,source,event_type,level,title,description,assignee,status,sla_deadline,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                   ('WO-DEMO-001', site_ids[0], 'auto', '告警自动转工单', 'urgent',
                    '[自动] pH值严重超标告警', 'pH值降至4.82，远低于III类水标准下限6.0，请立即现场核实。',
                    'Admin', 'dispatched',
                    (now + timedelta(hours=24)).strftime('%Y-%m-%d %H:%M'),
                    (now - timedelta(hours=1.5)).strftime('%Y-%m-%d %H:%M:%S')))

        # 告警2: 站点3 氨氮超标
        db.execute("""
            INSERT INTO alerts (site_id,metric,value,level,message,status,created_at,flow_type,flow_status)
            VALUES (?,?,?,?,?,?,?,?,?)
        """, (site_ids[2], 'ammonia', 1.35, 'orange',
              f'氨氮持续超标：{db.execute("SELECT name FROM sites WHERE id=?", (site_ids[2],)).fetchone()[0]} 氨氮1.35mg/L（标准≤0.5），连续3次超标',
              'pending', (now - timedelta(hours=5)).strftime('%Y-%m-%d %H:%M:%S'),
              'manual', 'pending_review'))

        # 告警3: 站点5 溶解氧偏低
        if len(site_ids) >= 5:
            db.execute("""
                INSERT INTO alerts (site_id,metric,value,level,message,status,created_at,flow_type,flow_status)
                VALUES (?,?,?,?,?,?,?,?,?)
            """, (site_ids[4], 'dissolved_oxygen', 3.12, 'yellow',
                  f'溶解氧偏低：{db.execute("SELECT name FROM sites WHERE id=?", (site_ids[4],)).fetchone()[0]} DO 3.12mg/L（标准≥5.0）',
                  'pending', (now - timedelta(hours=2)).strftime('%Y-%m-%d %H:%M:%S'),
                  'manual', 'pending_review'))

        # 告警4: 站点2 设备离线
        db.execute("""
            INSERT INTO alerts (site_id,metric,value,level,message,status,created_at,flow_type,flow_status)
            VALUES (?,?,?,?,?,?,?,?,?)
        """, (site_ids[1], 'device_status', 0, 'yellow',
              f'设备离线：{db.execute("SELECT name FROM sites WHERE id=?", (site_ids[1],)).fetchone()[0]} 多参数分析仪通信中断',
              'pending', (now - timedelta(hours=3)).strftime('%Y-%m-%d %H:%M:%S'),
              'manual', 'pending_review'))
        log("4条水质演示告警已创建。")

    # 5. 生成48h传感器趋势数据
    log("生成传感器趋势数据（48h回填）...")
    all_sites = db.execute("SELECT id, name FROM sites WHERE type='water_quality'").fetchall()
    total = 0
    for site in all_sites:
        sid = site['id']
        batch = []
        base_ph = round(random.uniform(7.0, 7.8), 1)
        base_do = round(random.uniform(5.5, 7.5), 1)
        base_ammonia = round(random.uniform(0.1, 0.4), 2)

        for h in range(288, 0, -1):
            ts = (now - timedelta(minutes=10 * h)).strftime('%Y-%m-%d %H:%M:%S')

            # pH渐变，最近时段触发告警的站点有突变
            if sid == site_ids[0] and h <= 9:  # 第1个站点最近1.5小时pH下降
                ph = round(7.2 - (9 - h) / 9 * 2.4, 2)
            else:
                ph = round(base_ph + random.uniform(-0.15, 0.15), 2)
            do = max(2.5, round(base_do + random.uniform(-0.5, 0.5), 2))
            ammonia = max(0.02, round(base_ammonia + random.uniform(-0.04, 0.04), 2))
            codmn = round(random.uniform(1.0, 5.0), 2)
            tp = round(random.uniform(0.01, 0.15), 3)
            tn = round(random.uniform(0.2, 0.8), 2)
            conductivity = round(random.uniform(150, 500), 1)
            turbidity = round(max(0.5, random.uniform(1.0, 8.0)), 1)
            water_temp = round(random.uniform(18.0, 26.0), 1)

            batch += [
                (sid,'ph',ph,'',ts), (sid,'dissolved_oxygen',do,'mg/L',ts),
                (sid,'ammonia',ammonia,'mg/L',ts), (sid,'codmn',codmn,'mg/L',ts),
                (sid,'total_phosphorus',tp,'mg/L',ts), (sid,'total_nitrogen',tn,'mg/L',ts),
                (sid,'turbidity',turbidity,'NTU',ts), (sid,'conductivity',conductivity,'μS/cm',ts),
                (sid,'water_temp',water_temp,'°C',ts),
            ]

            if len(batch) >= 1000:
                db.executemany("INSERT INTO sensor_data (site_id,metric,value,unit,recorded_at) VALUES (?,?,?,?,?)", batch)
                db.commit()
                total += len(batch)
                batch = []

        if batch:
            db.executemany("INSERT INTO sensor_data (site_id,metric,value,unit,recorded_at) VALUES (?,?,?,?,?)", batch)
            db.commit()
            total += len(batch)
    log(f"传感器数据生成完成: {total} 条。")

    # 6. 设置用户站点权限
    log("配置用户站点权限...")
    users = db.execute("SELECT id, username FROM users WHERE role='operator'").fetchall()
    all_site_ids = [r['id'] for r in db.execute("SELECT id FROM sites ORDER BY id").fetchall()]
    for u in users:
        # 每个运维人员分配部分站点
        assigned = all_site_ids[users.index(u)::len(users)]
        for sid in assigned:
            try:
                db.execute("INSERT INTO user_sites (user_id, site_id) VALUES (?,?)", (u['id'], sid))
            except:
                pass
    db.commit()

    # 打印摘要
    print(f"\n{'='*50}")
    print(f"  站点: {db.execute('SELECT COUNT(*) FROM sites').fetchone()[0]} 个")
    print(f"  设备: {db.execute('SELECT COUNT(*) FROM device_shadows').fetchone()[0]} 台")
    print(f"  告警: {db.execute('SELECT COUNT(*) FROM alerts').fetchone()[0]} 条")
    print(f"  传感器数据: {db.execute('SELECT COUNT(*) FROM sensor_data').fetchone()[0]} 条")
    print(f"{'='*50}")

    db.close()
    log("重置完成！")

if __name__ == '__main__':
    main()
