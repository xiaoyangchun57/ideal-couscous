"""水利智慧运营平台 — 演示数据层（v7通用水质版）
基于GB 3838-2002 III类水标准，适用于任意数量水质站点。
用法：在 app.py 的 __main__ 末尾调用 seed_demo.generate() 即可。
"""
import sqlite3, os, random, time
from datetime import datetime, timedelta

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'water.db')

def log(msg):
    print(f"[Demo] {msg}")

def _db():
    db = sqlite3.connect(DB_PATH, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA busy_timeout=5000")
    db.execute("PRAGMA synchronous=OFF")
    return db

WQ_DEVICES = [
    ('多参数水质分析仪', 'multi_param_analyzer', 'WQA-200', '哈希'),
    ('pH计', 'ph_meter', 'PHG-2088', '上海雷磁'),
    ('溶解氧传感器', 'do_sensor', 'DOG-3082', '上海雷磁'),
    ('浊度仪', 'turbidity_meter', 'TURB-3000', '哈希'),
    ('氨氮分析仪', 'ammonia_analyzer', 'NH3N-2000', '聚光科技'),
    ('COD分析仪', 'cod_analyzer', 'CODcr-2000', '聚光科技'),
    ('数据采集传输终端', 'dtu', 'DTU-WQ01', '厦门四信'),
]

def generate():
    """在基础种子数据之上叠加水质监测演示场景"""
    t0 = time.time()
    log("=== 开始叠加水质演示数据 ===")
    db = _db()

    # ====== 1. 清理过时的告警/工单/传感器数据（不清除站点和设备） ======
    db.execute("DELETE FROM alerts")
    db.execute("DELETE FROM work_orders WHERE source IN ('auto','alert_convert') OR order_no LIKE 'WO-DEMO-%'")
    db.execute("DELETE FROM timeline_events WHERE source_type='alert'")
    db.execute("DELETE FROM sensor_data")
    db.commit()
    log("清理旧告警/工单/传感器数据")

    # ====== 2. 获取所有水质站点 ======
    all_sites = db.execute("SELECT id, name, code FROM sites WHERE type='water_quality' ORDER BY id").fetchall()
    if not all_sites:
        log("没有 water_quality 类型站点，跳过演示数据生成")
        db.close()
        return

    wq_ids = [s['id'] for s in all_sites]
    log(f"共 {len(wq_ids)} 个水质站点")

    # ====== 3. 确保每个站点都有设备影子 ======
    existing_devs = {r['site_id'] for r in db.execute("SELECT DISTINCT site_id FROM device_shadows").fetchall()}
    missing = [sid for sid in wq_ids if sid not in existing_devs]
    if missing:
        for sid in missing:
            site = db.execute("SELECT code FROM sites WHERE id=?", (sid,)).fetchone()
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
        db.commit()
        log(f"为 {len(missing)} 个站点补建设备记录")
    else:
        log(f"设备记录已存在（{len(existing_devs)}个站点）")

    # ====== 4. 生成演示告警（基于前几个站点） ======
    now = datetime.now()
    if len(wq_ids) >= 4:
        # 告警1: 第一个站 pH突变 → 已转工单
        s1 = all_sites[0]
        db.execute("""
            INSERT INTO alerts (site_id,metric,value,level,message,status,created_at,flow_type,flow_status,related_order_no)
            VALUES (?,?,?,?,?,?,?,?,?,?)
        """, (s1['id'], 'ph', 4.82, 'red',
              f'pH值严重偏低：{s1["name"]} pH 4.82（正常范围6.0-9.0），疑似酸性废水排放',
              'pending', (now - timedelta(hours=1.5)).strftime('%Y-%m-%d %H:%M:%S'),
              'auto', 'converted', 'WO-DEMO-001'))
        # 派单人取站点绑定的操作员（人员管理为唯一真相源），杜绝孤儿 'Admin'
        _op = db.execute("SELECT u.real_name FROM user_sites us JOIN users u ON u.id=us.user_id "
                         "WHERE us.site_id=? AND u.role='operator' LIMIT 1", (s1['id'],)).fetchone()
        _demo_assignee = _op['real_name'] if _op else '刘娜'
        db.execute("INSERT INTO work_orders (order_no,site_id,source,event_type,level,title,description,assignee,status,sla_deadline,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                   ('WO-DEMO-001', s1['id'], 'auto', '告警自动转工单', 'urgent',
                    '[自动] pH值严重超标告警',
                    f'{s1["name"]}检测到pH值降至4.82，远低于III类水标准下限6.0，请立即现场核实。',
                    _demo_assignee, 'dispatched',
                    (now + timedelta(hours=24)).strftime('%Y-%m-%d %H:%M'),
                    (now - timedelta(hours=1.5)).strftime('%Y-%m-%d %H:%M:%S')))

        # 告警2: 第三个站 氨氮超标
        s3 = all_sites[2]
        db.execute("""
            INSERT INTO alerts (site_id,metric,value,level,message,status,created_at,flow_type,flow_status)
            VALUES (?,?,?,?,?,?,?,?,?)
        """, (s3['id'], 'ammonia', 1.35, 'orange',
              f'氨氮持续超标：{s3["name"]} 氨氮1.35mg/L（标准≤0.5），连续3次超标',
              'pending', (now - timedelta(hours=5)).strftime('%Y-%m-%d %H:%M:%S'),
              'manual', 'pending_review'))

        if len(wq_ids) >= 5:
            # 告警3: 第五个站 溶解氧偏低
            s5 = all_sites[4]
            db.execute("""
                INSERT INTO alerts (site_id,metric,value,level,message,status,created_at,flow_type,flow_status)
                VALUES (?,?,?,?,?,?,?,?,?)
            """, (s5['id'], 'dissolved_oxygen', 3.12, 'yellow',
                  f'溶解氧偏低：{s5["name"]} DO 3.12mg/L（标准≥5.0）',
                  'pending', (now - timedelta(hours=2)).strftime('%Y-%m-%d %H:%M:%S'),
                  'manual', 'pending_review'))

        if len(wq_ids) >= 2:
            # 告警4: 第二个站 设备离线
            s2 = all_sites[1]
            db.execute("""
                INSERT INTO alerts (site_id,metric,value,level,message,status,created_at,flow_type,flow_status)
                VALUES (?,?,?,?,?,?,?,?,?)
            """, (s2['id'], 'device_status', 0, 'yellow',
                  f'设备离线：{s2["name"]} 多参数分析仪通信中断',
                  'pending', (now - timedelta(hours=3)).strftime('%Y-%m-%d %H:%M:%S'),
                  'manual', 'pending_review'))

        db.commit()
        log("4条水质演示告警已创建 (含联动工单)")

    # ====== 5. 生成48h传感器趋势数据 ======
    log("开始生成水质传感器趋势数据（48h回填）...")
    total_count = 0
    for site in all_sites:
        sid = site['id']
        batch = []
        base_ph = round(random.uniform(7.0, 7.8), 1)
        base_do = round(random.uniform(5.5, 7.5), 1)
        base_ammonia = round(random.uniform(0.1, 0.4), 2)

        for h in range(288, 0, -1):
            ts = (now - timedelta(minutes=10 * h)).strftime('%Y-%m-%d %H:%M:%S')

            # 第一个站点最近1.5小时pH下降（配合告警场景）
            if sid == wq_ids[0] and h <= 9:
                ph = round(7.2 - (9 - h) / 9 * 2.4, 2)
            else:
                ph = round(base_ph + random.uniform(-0.15, 0.15), 2)

            do = max(2.5, round(base_do + random.uniform(-0.5, 0.5), 2))
            ammonia = max(0.02, round(base_ammonia + random.uniform(-0.04, 0.04), 2))
            cod = round(random.uniform(3.0, 11.0), 2)
            tp = round(random.uniform(0.01, 0.10), 3)
            tn = round(random.uniform(0.2, 1.2), 2)
            turbidity = round(max(0.5, random.uniform(1.0, 8.0)), 1)
            water_temp = round(random.uniform(18.0, 26.0), 1)

            batch += [
                (sid,'ph',ph,'',ts), (sid,'dissolved_oxygen',do,'mg/L',ts),
                (sid,'ammonia',ammonia,'mg/L',ts), (sid,'cod',cod,'mg/L',ts),
                (sid,'total_phosphorus',tp,'mg/L',ts), (sid,'total_nitrogen',tn,'mg/L',ts),
                (sid,'turbidity',turbidity,'NTU',ts), (sid,'water_temp',water_temp,'°C',ts),
            ]

            if len(batch) >= 1000:
                db.executemany("INSERT INTO sensor_data (site_id,metric,value,unit,recorded_at) VALUES (?,?,?,?,?)", batch)
                db.commit()
                total_count += len(batch)
                batch = []

        if batch:
            db.executemany("INSERT INTO sensor_data (site_id,metric,value,unit,recorded_at) VALUES (?,?,?,?,?)", batch)
            db.commit()
            total_count += len(batch)
    log(f"水质传感器趋势数据生成完成: {total_count} 条")

    # ====== 6. 站点状态 ======
    db.execute("UPDATE sites SET status='online' WHERE type='water_quality'")
    db.commit()

    # ====== 7. 备件库存 ======
    db.execute("UPDATE spare_parts_inventory SET quantity=3, min_quantity=5 WHERE part_code='BJ-011'")
    log("备件库存已调整（pH电极低库存演示）")

    db.close()
    elapsed = time.time() - t0
    log(f"=== 水质演示数据叠加完成！耗时 {elapsed:.1f}s ===")

    # 打印摘要
    db2 = _db()
    print(f"\n{'='*50}")
    for a in db2.execute("SELECT site_id, level, metric FROM alerts ORDER BY site_id").fetchall():
        s = db2.execute("SELECT name FROM sites WHERE id=?", (a['site_id'],)).fetchone()
        sn = s['name'] if s else f"sid={a['site_id']}"
        print(f"  {sn}: [{a['level']}] {a['metric']}")
    print(f"  总站点: {db2.execute('SELECT COUNT(*) FROM sites').fetchone()[0]}")
    print(f"  总传感器数据: {db2.execute('SELECT COUNT(*) FROM sensor_data').fetchone()[0]}")
    al_pending = db2.execute("SELECT COUNT(*) FROM alerts WHERE status='pending'").fetchone()[0]
    print(f"  待处理告警: {al_pending}")
    print(f"{'='*50}")
    db2.close()

if __name__ == '__main__':
    generate()
