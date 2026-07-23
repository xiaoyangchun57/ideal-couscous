"""准备静态异常场景数据（模拟器已关闭，数据固定不变）"""
import sqlite3
import json
from datetime import datetime, timedelta

DB_PATH = r'E:\杂七杂八\workbuddy\2026-06-24\backend\data\water.db'
conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row
db = conn.cursor()

now = datetime.now()
now_str = now.strftime('%Y-%m-%d %H:%M:%S')

print("=== 准备静态异常场景数据 ===\n")

# 清理旧的演示工单
db.execute("DELETE FROM work_orders WHERE order_no LIKE 'WO-DEMO-%' OR order_no LIKE 'WO-20260627-%'")
print(f"0. 清理旧演示工单: {db.execute('SELECT changes()').fetchone()[0]} 条")

# 1. 清理旧的误报告警
db.execute("UPDATE alerts SET status='resolved', resolved_at=datetime('now','localtime') WHERE metric='data_spike' AND status='pending'")
print(f"1. 清理旧data_spike告警: {db.execute('SELECT changes()').fetchone()[0]} 条")

# 2. 重置所有设备为在线（除预设离线站点）
db.execute("UPDATE device_shadows SET status='online', last_data_time=? WHERE site_id NOT IN (5, 108, 193)", (now_str,))
print(f"2. 重置设备在线: {db.execute('SELECT changes()').fetchone()[0]} 台")

# 3. 重置站点状态
db.execute("UPDATE sites SET status='online', last_heartbeat=? WHERE id NOT IN (5, 108, 193)", (now_str,))
print(f"3. 重置站点在线: {db.execute('SELECT changes()').fetchone()[0]} 个")

# 4. 设置预设离线站点（场景1）
for sid in [5, 108, 193]:
    db.execute("UPDATE sites SET status='offline' WHERE id=?", (sid,))
    db.execute("UPDATE device_shadows SET status='offline', last_data_time=NULL WHERE site_id=?", (sid,))
print("4. 设置3个预设离线站点: 5(江桥), 108(泉岭), 193(蓼南)")

# 5. 获取站点
hydro = db.execute("SELECT id, name FROM sites WHERE type='hydrology' ORDER BY id LIMIT 10").fetchall()
wl = db.execute("SELECT id, name FROM sites WHERE type='water_level' ORDER BY id LIMIT 10").fetchall()
rain = db.execute("SELECT id, name FROM sites WHERE type='rainfall' ORDER BY id LIMIT 10").fetchall()
soil = db.execute("SELECT id, name FROM sites WHERE type='soil_moisture' ORDER BY id LIMIT 5").fetchall()
all_sites = db.execute("SELECT id, name, type FROM sites ORDER BY id LIMIT 20").fetchall()

# 6. 场景2：数据突变告警（红色）
if hydro:
    sid = hydro[0]['id']
    # 注入突变数据
    for i in range(10):
        t = (now - timedelta(minutes=i*5)).strftime('%Y-%m-%d %H:%M:%S')
        val = 4.2 + (0.05 * i)  # 稳定值
        db.execute("INSERT INTO sensor_data (site_id,metric,value,unit,recorded_at) VALUES (?,?,?,?,?)",
                   (sid, 'water_level', val, 'm', t))
    # 突变值
    db.execute("INSERT INTO sensor_data (site_id,metric,value,unit,recorded_at) VALUES (?,?,?,?,?)",
               (sid, 'water_level', 15.8, 'm', now_str))
    db.execute("""
        INSERT INTO alerts (site_id,metric,value,level,message,status,flow_type,flow_status,created_at)
        VALUES (?,?,?,?,?,?,?,?,?)
    """, (sid, 'data_spike', 15.8, 'red',
          f'数据异常陡增：水位 15.80m（均值4.20m，变化276%）', 'pending', 'manual', 'pending_review',
          (now - timedelta(minutes=5)).strftime('%Y-%m-%d %H:%M:%S')))
    print(f"5. 场景2-数据突变: 站点{hydro[0]['name']}(id={sid})")

# 7. 场景3：数据冻结告警（黄色）
if len(wl) > 1:
    sid = wl[1]['id']
    for i in range(8):
        t = (now - timedelta(minutes=i*5)).strftime('%Y-%m-%d %H:%M:%S')
        db.execute("INSERT INTO sensor_data (site_id,metric,value,unit,recorded_at) VALUES (?,?,?,?,?)",
                   (sid, 'water_level', 3.45, 'm', t))
    db.execute("""
        INSERT INTO alerts (site_id,metric,value,level,message,status,flow_type,flow_status,created_at)
        VALUES (?,?,?,?,?,?,?,?,?)
    """, (sid, 'data_freeze', 3.45, 'yellow',
          f'数据冻结：水位连续8条记录值一致（3.45），传感器可能故障', 'pending', 'manual', 'pending_review',
          (now - timedelta(minutes=10)).strftime('%Y-%m-%d %H:%M:%S')))
    print(f"6. 场景3-数据冻结: 站点{wl[1]['name']}(id={sid})")

# 8. 场景4：数据缺失告警（黄色）
if len(rain) > 2:
    sid = rain[2]['id']
    db.execute("""
        INSERT INTO alerts (site_id,metric,value,level,message,status,flow_type,flow_status,created_at)
        VALUES (?,?,?,?,?,?,?,?,?)
    """, (sid, 'data_gap', 180, 'yellow',
          f'数据延迟：降雨量已有180分钟未更新', 'pending', 'auto', 'pending',
          (now - timedelta(minutes=15)).strftime('%Y-%m-%d %H:%M:%S')))
    print(f"7. 场景4-数据缺失: 站点{rain[2]['name']}(id={sid})")

# 9. 场景5：设备离线告警（黄色）
if soil:
    sid = soil[0]['id']
    dev = db.execute("SELECT id FROM device_shadows WHERE site_id=? LIMIT 1", (sid,)).fetchone()
    if dev:
        db.execute("UPDATE device_shadows SET status='offline', last_data_time=NULL WHERE id=?", (dev['id'],))
        db.execute("UPDATE sites SET status='offline' WHERE id=?", (sid,))
    db.execute("""
        INSERT INTO alerts (site_id,metric,value,level,message,status,flow_type,flow_status,created_at)
        VALUES (?,?,?,?,?,?,?,?,?)
    """, (sid, 'device_status', 0, 'yellow',
          f'设备离线: 土壤水分传感器', 'pending', 'auto', 'pending',
          (now - timedelta(minutes=20)).strftime('%Y-%m-%d %H:%M:%S')))
    print(f"8. 场景5-设备离线: 站点{soil[0]['name']}(id={sid})")

# 10. 场景6+7：告警转工单（创建关联对）
if len(all_sites) > 5:
    sid = all_sites[5]['id']
    # 创建告警
    db.execute("""
        INSERT INTO alerts (site_id,metric,value,level,message,status,flow_type,flow_status,created_at)
        VALUES (?,?,?,?,?,?,?,?,?)
    """, (sid, 'device_status', 0, 'orange',
          '设备离线: 雷达水位计', 'pending', 'auto', 'converted',
          (now - timedelta(hours=1)).strftime('%Y-%m-%d %H:%M:%S')))
    alert_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    # 创建关联工单
    order_no = f"WO-{now.strftime('%Y%m%d')}-701"
    db.execute("DELETE FROM work_orders WHERE order_no=?", (order_no,))
    db.execute("""
        INSERT INTO work_orders (order_no,site_id,source,event_type,level,title,description,assignee,status,sla_deadline,related_alert_id,created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
    """, (order_no, sid, 'auto', '设备故障', 'urgent',
          '[自动] 设备离线: 雷达水位计', '设备离线: 雷达水位计', '张建国', 'in_progress',
          (now + timedelta(hours=23)).strftime('%Y-%m-%d %H:%M'), alert_id,
          (now - timedelta(hours=1)).strftime('%Y-%m-%d %H:%M:%S')))
    db.execute("UPDATE alerts SET related_order_no=? WHERE id=?", (order_no, alert_id))
    print(f"9. 场景6+7-告警转工单: 站点{all_sites[5]['name']}, alert_id={alert_id}, order={order_no}")

# 11. 场景8：工单SLA超时
if len(all_sites) > 6:
    sid = all_sites[6]['id']
    order_no = f"WO-{now.strftime('%Y%m%d')}-801"
    # 先删除可能存在的旧工单
    db.execute("DELETE FROM work_orders WHERE order_no=?", (order_no,))
    db.execute("""
        INSERT INTO work_orders (order_no,site_id,source,event_type,level,title,description,assignee,status,sla_deadline,created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
    """, (order_no, sid, 'auto', '设备故障', 'urgent',
          '水位计数据中断', '设备持续2小时无数据上报', '张建国', 'in_progress',
          (now - timedelta(hours=5)).strftime('%Y-%m-%d %H:%M'),
          (now - timedelta(hours=8)).strftime('%Y-%m-%d %H:%M:%S')))
    print(f"10. 场景8-SLA超时工单: {order_no}")

# 12. 场景9：工单长时间未更新
if len(all_sites) > 7:
    sid = all_sites[7]['id']
    order_no = f"WO-{now.strftime('%Y%m%d')}-901"
    db.execute("DELETE FROM work_orders WHERE order_no=?", (order_no,))
    db.execute("""
        INSERT INTO work_orders (order_no,site_id,source,event_type,level,title,description,assignee,status,sla_deadline,created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
    """, (order_no, sid, 'patrol', '巡检维修', 'normal',
          '护栏损坏修复', '巡检发现河道护栏损坏', '黎明', 'in_progress',
          (now + timedelta(hours=48)).strftime('%Y-%m-%d %H:%M'),
          (now - timedelta(days=2)).strftime('%Y-%m-%d %H:%M:%S')))
    print(f"11. 场景9-长时间未更新工单: {order_no}")

# 13. 场景10：巡检异常
if len(all_sites) > 3:
    sid = all_sites[3]['id']
    db.execute("""
        INSERT INTO inspection_plans (plan_name,site_id,type,start_date,end_date,status,period,created_at)
        VALUES (?,?,?,?,?,?,?,?)
    """, (f"异常巡检-{now.strftime('%Y%m%d')}", sid, 'daily',
          now.strftime('%Y-%m-%d'), now.strftime('%Y-%m-%d'), 'in_progress', 'daily',
          (now - timedelta(hours=3)).strftime('%Y-%m-%d %H:%M:%S')))
    plan_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    db.execute("""
        INSERT INTO inspection_tasks (plan_id,site_id,check_item,result,check_time,remark)
        VALUES (?,?,?,?,?,?)
    """, (plan_id, sid, '水位计校验', 'abnormal',
          (now - timedelta(hours=2)).strftime('%Y-%m-%d %H:%M:%S'), '水位计读数偏差超过5cm'))
    db.execute("""
        INSERT INTO alerts (site_id,metric,value,level,message,status,flow_type,flow_status,created_at)
        VALUES (?,?,?,?,?,?,?,?,?)
    """, (sid, 'inspection', 0, 'yellow',
          '巡检异常：水位计校验 - 水位计读数偏差超过5cm', 'pending', 'manual', 'pending_review',
          (now - timedelta(hours=2)).strftime('%Y-%m-%d %H:%M:%S')))
    print(f"12. 场景10-巡检异常: 站点{all_sites[3]['name']}, plan_id={plan_id}")

# 14. 场景11：巡检计划逾期未完成
if len(all_sites) > 8:
    sid = all_sites[8]['id']
    past_date = (now - timedelta(days=3)).strftime('%Y-%m-%d')
    db.execute("""
        INSERT INTO inspection_plans (plan_name,site_id,type,start_date,end_date,status,period,created_at)
        VALUES (?,?,?,?,?,?,?,?)
    """, ('逾期未完成巡检-演示', sid, 'weekly', past_date, past_date, 'pending', 'weekly',
          (now - timedelta(days=5)).strftime('%Y-%m-%d %H:%M:%S')))
    plan_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    for item in ['设备外观检查', '数据采集器校验', '通信模块检查']:
        db.execute("INSERT INTO inspection_tasks (plan_id,site_id,check_item) VALUES (?,?,?)",
                   (plan_id, sid, item))
    print(f"13. 场景11-逾期巡检计划: 站点{all_sites[8]['name']}, plan_id={plan_id}")

# 15. 场景12：备件库存不足
low_part = db.execute("SELECT id FROM spare_parts_inventory WHERE part_code='BJ-005'").fetchone()
if low_part:
    db.execute("UPDATE spare_parts_inventory SET quantity=0, min_quantity=2 WHERE id=?", (low_part['id'],))
print("14. 场景12-备件库存不足: BJ-005(数据采集终端RTU) qty=0")

# 16. 场景13：备件申请待审批
pending_req = db.execute("SELECT COUNT(*) FROM spare_part_requests WHERE status='pending'").fetchone()[0]
if pending_req == 0:
    rno = f"BJ-{now.strftime('%Y%m%d')}-999"
    db.execute("""
        INSERT INTO spare_part_requests (request_no,site_id,applicant,part_name,quantity,reason,status,created_at)
        VALUES (?,?,?,?,?,?,?,?)
    """, (rno, all_sites[0]['id'], '运维人员', '雷达水位计', 1, '设备故障需更换', 'pending',
          (now - timedelta(hours=1)).strftime('%Y-%m-%d %H:%M:%S')))
print("15. 场景13-备件申请待审批")

# 17. 场景14：热线事件未处理
db.execute("""
    INSERT INTO hotline_events (caller_name,caller_phone,event_type,description,location,status,operator,created_at)
    VALUES (?,?,?,?,?,?,?,?)
""", ('赵先生', '13900009900', '水位异常', '河道水位上涨迅速，疑似上游水库泄洪',
      '赣江下游段', 'registered', '李敏',
      (now - timedelta(minutes=30)).strftime('%Y-%m-%d %H:%M:%S')))
print("16. 场景14-热线未处理")

# 18. 场景15：热线转工单后未完成
db.execute("DELETE FROM hotline_events WHERE caller_phone='13900008800'")
db.execute("""
    INSERT INTO hotline_events (caller_name,caller_phone,event_type,description,location,status,related_order_no,site_id,operator,created_at)
    VALUES (?,?,?,?,?,?,?,?,?,?)
""", ('孙女士', '13900008800', '设施损坏', '堤防护坡出现塌陷', '城南堤防段',
      'dispatched', f"WO-{now.strftime('%Y%m%d')}-1501", all_sites[2]['id'], '王芳',
      (now - timedelta(hours=3)).strftime('%Y-%m-%d %H:%M:%S')))
order_no = f"WO-{now.strftime('%Y%m%d')}-1501"
db.execute("DELETE FROM work_orders WHERE order_no=?", (order_no,))
db.execute("""
    INSERT INTO work_orders (order_no,site_id,source,event_type,level,title,description,assignee,status,sla_deadline,created_at)
    VALUES (?,?,?,?,?,?,?,?,?,?,?)
""", (order_no, all_sites[2]['id'], 'hotline', '设施维修', 'urgent',
      '[热线] 堤防护坡塌陷', '堤防护坡出现塌陷，需紧急修复', '王刚', 'in_progress',
      (now + timedelta(hours=4)).strftime('%Y-%m-%d %H:%M'),
      (now - timedelta(hours=3)).strftime('%Y-%m-%d %H:%M:%S')))
print(f"17. 场景15-热线转工单未完成: {order_no}")

# 更新标记
db.execute("DELETE FROM timeline_events WHERE event_type='abnormal_scenarios_seeded'")
db.execute("""
    INSERT INTO timeline_events (source_type,source_id,event_type,operator,remark)
    VALUES ('system',0,'abnormal_scenarios_seeded','系统','15种异常场景数据准备完成（静态演示模式）')
""")

conn.commit()

# 最终统计
print("\n=== 最终数据状态 ===")
db.execute("SELECT COUNT(*) FROM alerts WHERE status='pending'")
print(f"待处理告警: {db.fetchone()[0]}")
db.execute("SELECT COUNT(*) FROM alerts WHERE status='resolved'")
print(f"已办结告警: {db.fetchone()[0]}")
db.execute("SELECT COUNT(*) FROM work_orders WHERE status NOT IN ('closed','cancelled')")
print(f"进行中工单: {db.fetchone()[0]}")
db.execute("SELECT COUNT(*) FROM sites WHERE status='offline'")
print(f"离线站点: {db.fetchone()[0]}")
db.execute("SELECT COUNT(*) FROM device_shadows WHERE status='offline'")
print(f"离线设备: {db.fetchone()[0]}")
db.execute("SELECT COUNT(*) FROM hotline_events WHERE status='registered'")
print(f"未处理热线: {db.fetchone()[0]}")
db.execute("SELECT COUNT(*) FROM spare_part_requests WHERE status='pending'")
print(f"待审批备件申请: {db.fetchone()[0]}")

conn.close()
print("\n=== 数据准备完成 ===")
