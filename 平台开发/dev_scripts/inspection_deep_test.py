# -*- coding: utf-8 -*-
import urllib.request, urllib.error, json, sqlite3, datetime

BASE = "http://127.0.0.1:5000"
DB = "backend/data/water.db"
TOKEN = None

def req(method, path, body=None, raw=False):
    url = BASE + path
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method)
    r.add_header("Content-Type", "application/json")
    if TOKEN:
        r.add_header("Authorization", "Bearer " + TOKEN)
    try:
        resp = urllib.request.urlopen(r, timeout=10)
        txt = resp.read().decode()
        return resp.status, (txt if raw else (json.loads(txt) if txt else {}))
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()
    except Exception as e:
        return "ERR", repr(e)

def db(q, p=()):
    con = sqlite3.connect(DB); con.row_factory = sqlite3.Row
    r = con.execute(q, p).fetchall(); con.close()
    return [dict(x) for x in r]

def line(t): print(t)

print("="*70)
print("巡检计划模块 深度测试 (pyramid: shape→correctness→closure→E2E→boundary)")
print("="*70)

# ---------- 0. LOGIN ----------
s, j = req("POST", "/api/auth/login", {"username":"admin","password":"admin123"})
assert s == 200 and "token" in j, f"login failed {s} {j}"
TOKEN = j["token"]
line(f"[0] 登录成功, token={TOKEN[:12]}...")

# ---------- 1. API 形态 / 端点存在性 ----------
line("\n--- [1] 端点存在性 / 前后端不一致 ---")
s, j = req("GET", "/api/inspection-v2/plans")
line(f"  GET /plans -> {s}, 返回类型={'list' if isinstance(j,list) else type(j)}, 条数={len(j) if isinstance(j,list) else 'NA'}")
# 找一个 draft 计划用于后续测 spare-parts
draft = next((p for p in j if p.get('status')=='draft'), None)
if draft:
    s2, r2 = req("POST", f"/api/inspection-v2/plans/{draft['id']}/spare-parts", {"items":[{"part_sku":"X","quantity":1}]})
    line(f"  POST /plans/{draft['id']}/spare-parts (前端调用) -> {s2}  <-- 前端MaintenancePage.jsx:215 调用此端点")
    line(f"      后端实际路由为 /parts-request, 无 /spare-parts -> {'404=前后端断路' if s2==404 else '非404,需复核'}")
s3, r3 = req("POST", f"/api/inspection-v2/plans/{draft['id']}/parts-request", {"items":[{"part_sku":"X","quantity":1}]})
line(f"  POST /plans/{draft['id']}/parts-request (后端真实路由) -> {s3}")
s4, r4 = req("GET", "/api/inspection-v2/plans/1/items/pending")
line(f"  GET /plans/1/items/pending (待审核列表) -> {s4}  <-- 前端是否调用? 前端grep无此调用=可能孤儿端点")
s5, r5 = req("POST", f"/api/inspection-v2/plans/{draft['id']}/anomaly", {"report_type":"equipment","description":"测试","reporter_id":1})
line(f"  POST /plans/{draft['id']}/anomaly (异常上报) -> {s5}  <-- 前端grep无此调用=可能孤儿端点")

# ---------- 2. 生命周期闭环 (新建 draft -> submit -> approve -> 执行 -> complete) ----------
line("\n--- [2] 生命周期闭环 (手动创建 + 全状态机) ---")
# 选两个有 active 排程的站点
sites = db("SELECT site_id, COUNT(*) c FROM inspection_schedules WHERE status='active' GROUP BY site_id ORDER BY c DESC LIMIT 2")
site_ids = [r['site_id'] for r in sites]
line(f"  选用站点(含active排程): {site_ids}")
s, j = req("POST", "/api/inspection-v2/plans/manual", {
    "plan_name": "深度测试计划_"+datetime.datetime.now().strftime("%H%M%S"),
    "site_ids": site_ids, "vehicle_id": 1, "period": "weekly",
    "assignee": "admin", "assignee_id": 1})
assert s in (200,201), f"创建计划失败 {s} {j}"
pid = j["id"]
line(f"  创建计划 pid={pid} -> {s}")

# 详情
s, det = req("GET", f"/api/inspection-v2/plans/{pid}")
items = det.get("items", [])
line(f"  计划详情: 状态={det.get('status')}, items数={len(items)}, completion_rate={det.get('completion_rate')}")
sched_items = [i for i in items if i.get('schedule_id')]
line(f"  其中带 schedule_id 的项={len(sched_items)}")

# submit: draft -> submitted
s, j = req("POST", f"/api/inspection-v2/plans/{pid}/submit")
line(f"  submit (draft->submitted) -> {s} {j.get('status') if isinstance(j,dict) else j}")
# approve: submitted -> active
s, j = req("POST", f"/api/inspection-v2/plans/{pid}/approve", {"action":"approve","approver_id":1,"comment":"OK"})
line(f"  approve (submitted->active) -> {s} {j.get('status') if isinstance(j,dict) else j}")

# 执行: 逐项提交
# 选一个 schedule 项做 需更换(扣库存), 一个做 abnormal(告警+推进排程), 其余 normal
sp_item = sched_items[0] if sched_items else (items[0] if items else None)
ab_item = sched_items[1] if len(sched_items)>1 else None
# 备件库存快照
before_qty = db("SELECT quantity FROM spare_parts_inventory WHERE part_code='SP-PH-001'")
before_qty = before_qty[0]['quantity'] if before_qty else None
# 选一个有 schedule 的项, 记录其 next_due_date
if sp_item and sp_item.get('schedule_id'):
    due_before = db("SELECT next_due_date FROM inspection_schedules WHERE id=?", (sp_item['schedule_id'],))
    due_before = due_before[0]['next_due_date'] if due_before else None
    s, j = req("PUT", f"/api/inspection-v2/plans/{pid}/items/{sp_item['id']}",
               {"result":"需更换","part_consumed":"SP-PH-001","remark":"电极老化"})
    line(f"  PUT 项{sp_item['id']} 需更换+消耗SP-PH-001 -> {s}")
    after_qty = db("SELECT quantity FROM spare_parts_inventory WHERE part_code='SP-PH-001'")
    after_qty = after_qty[0]['quantity'] if after_qty else None
    line(f"    备件库存 SP-PH-001: {before_qty} -> {after_qty}  {'OK扣减1' if (before_qty is not None and after_qty==before_qty-1) else '未扣减/异常'}")
    due_after = db("SELECT next_due_date FROM inspection_schedules WHERE id=?", (sp_item['schedule_id'],))
    due_after = due_after[0]['next_due_date'] if due_after else None
    line(f"    排程 next_due_date: {due_before} -> {due_after}  {'已推进' if due_before!=due_after else '未推进(需更换不推进?)'}")

if ab_item and ab_item.get('schedule_id'):
    due_b = db("SELECT next_due_date FROM inspection_schedules WHERE id=?", (ab_item['schedule_id'],))
    due_b = due_b[0]['next_due_date'] if due_b else None
    alerts_before = db("SELECT COUNT(*) c FROM alerts WHERE site_id=? AND created_at>datetime('now','-5 minutes')", (ab_item['site_id'],))
    ab_before = alerts_before[0]['c']
    s, j = req("PUT", f"/api/inspection-v2/plans/{pid}/items/{ab_item['id']}",
               {"result":"abnormal","remark":"传感器读数异常"})
    line(f"  PUT 项{ab_item['id']} abnormal -> {s}")
    al = db("SELECT id,message,level,status FROM alerts WHERE site_id=? AND created_at>datetime('now','-5 minutes') ORDER BY id DESC LIMIT 1", (ab_item['site_id'],))
    wo = db("SELECT id,order_no,status,source FROM work_orders WHERE site_id=? AND created_at>datetime('now','-5 minutes') ORDER BY id DESC LIMIT 1", (ab_item['site_id'],))
    line(f"    异常后告警: {al[0] if al else '无(异常!)'}")
    line(f"    关联工单: {wo[0] if wo else '无'}")
    due_a = db("SELECT next_due_date FROM inspection_schedules WHERE id=?", (ab_item['schedule_id'],))
    due_a = due_a[0]['next_due_date'] if due_a else None
    line(f"    排程 next_due_date: {due_b} -> {due_a}  {'已推进' if due_b!=due_a else '未推进'}")

# 其余项 normal
import time
for it in items:
    if it['id'] in ((sp_item or {}).get('id'), (ab_item or {}).get('id')):
        continue
    req("PUT", f"/api/inspection-v2/plans/{pid}/items/{it['id']}", {"result":"normal"})
# 完成
s, det2 = req("GET", f"/api/inspection-v2/plans/{pid}")
done = sum(1 for i in det2.get('items',[]) if i.get('result') is not None)
total = len(det2.get('items',[]))
s, j = req("POST", f"/api/inspection-v2/plans/{pid}/complete")
line(f"  complete (active->completed) -> {s} {j.get('status') if isinstance(j,dict) else j}; 实际完成项={done}/{total}")
s, st = req("GET", f"/api/inspection-v2/plans/{pid}/stats")
line(f"  计划stats: {st}")

# ---------- 3. 数据正确性: normal/正常 混合导致统计失真 ----------
line("\n--- [3] 统计盲区: result 枚举不统一 (normal vs 正常) ---")
# 取一个项, 提交为 '正常'(中文) 复现历史脏数据
if items:
    tid = items[0]['id']
    req("PUT", f"/api/inspection-v2/plans/{pid}/items/{tid}", {"result":"正常"})
    s, st = req("GET", f"/api/inspection-v2/plans/{pid}/stats")
    zh = sum(1 for i in det2.get('items',[]) if i.get('result')=='正常')
    line(f"  插入一个 result='正常'(中文) 后, stats.normal_count={st.get('normal_count')}, 实际含正常语义项={zh+ (1 if st.get('normal_count') is not None else 0)}  -> {'统计漏计中文正常项' if st.get('normal_count',0) < (zh+0) else 'OK'}")
dist = db("SELECT result, COUNT(*) c FROM insp_plan_items GROUP BY result")
line(f"  当前全库 result 分布: {dist}")

# ---------- 4. 边界条件 ----------
line("\n--- [4] 边界 / 异常输入 ---")
# 4.1 非 draft 提交
s, j = req("POST", f"/api/inspection-v2/plans/{pid}/submit")
line(f"  对已完成计划再 submit -> {s} (期望400)")
# 4.2 非 submitted 批准
s, j = req("POST", f"/api/inspection-v2/plans/{pid}/approve", {"action":"approve","approver_id":1})
line(f"  对非 submitted 计划 approve -> {s} (期望400)")
# 4.3 备件预申报空列表
s, j = req("POST", f"/api/inspection-v2/plans/{pid}/parts-request", {"items":[]})
line(f"  parts-request 空items -> {s} (期望400)")
# 4.4 后端 complete 是否校验项全部完成 (无前端Modal时)
# 新建一个 active 计划, 留待执行项, 直接 complete
s, j = req("POST", "/api/inspection-v2/plans/manual", {"plan_name":"边界_未执行","site_ids":site_ids,"vehicle_id":1,"period":"weekly","assignee":"admin","assignee_id":1})
pid2 = j["id"]
req("POST", f"/api/inspection-v2/plans/{pid2}/submit")
req("POST", f"/api/inspection-v2/plans/{pid2}/approve", {"action":"approve","approver_id":1})
s, j = req("POST", f"/api/inspection-v2/plans/{pid2}/complete")
s2, d2 = req("GET", f"/api/inspection-v2/plans/{pid2}")
pend = sum(1 for i in d2.get('items',[]) if i.get('result') is None)
line(f"  后端complete未完成计划: status={j.get('status')}, completion_rate={d2.get('completion_rate')}, 待执行项={pend}  -> {'后端无校验,允许半成品completed' if j.get('status')=='completed' else 'OK'}")
# 清理 pid2
req("DELETE", f"/api/inspection-v2/plans/{pid2}")
# 4.5 签到/签退 gps
if items:
    s, j = req("POST", f"/api/inspection-v2/plans/{pid}/items/{items[0]['id']}/checkin", {"gps_lat":31.2,"gps_lng":121.5})
    s, j = req("POST", f"/api/inspection-v2/plans/{pid}/items/{items[0]['id']}/checkout", {"gps_lat":31.21,"gps_lng":121.51})
    ci = db("SELECT check_in_time,check_out_time,gps_lat,gps_lng FROM insp_plan_items WHERE id=?", (items[0]['id'],))
    line(f"  签到/签退 -> {ci[0] if ci else 'NA'}")

# ---------- 5. 清理 ----------
req("DELETE", f"/api/inspection-v2/plans/{pid}")
line(f"\n[5] 已清理测试计划 pid={pid}")
print("\n" + "="*70)
print("测试结束")
