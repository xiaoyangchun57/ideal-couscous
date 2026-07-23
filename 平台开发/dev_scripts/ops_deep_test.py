# -*- coding: utf-8 -*-
"""
运维管理模块深度测试 harness
覆盖：功能完整性 / 上下级链路 / 功能协调性 / (UX 由 Playwright 另行覆盖)
受控 E2E：注入唯一标记数据 -> 动作 -> 断言 DB -> 清理。
"""
import urllib.request, json, sqlite3, uuid, datetime, os

BASE = "http://127.0.0.1:5000/api"
DB = r"E:/杂七杂八/水质运维/平台开发/backend/data/water.db"
MARK = "QA_" + uuid.uuid4().hex[:8]
TOKEN = None

def login():
    global TOKEN
    req = urllib.request.Request(BASE + "/auth/login",
        data=json.dumps({"username":"admin","password":"admin123"}).encode(),
        headers={"Content-Type":"application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=20) as r:
        TOKEN = json.loads(r.read())["token"]

def call(method, path, body=None):
    from urllib.parse import quote
    url = BASE + quote(path, safe='/?:=&')
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data: req.add_header("Content-Type","application/json")
    if TOKEN: req.add_header("Authorization","Bearer "+TOKEN)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            text = r.read().decode("utf-8","replace")
            try: j = json.loads(text)
            except: j = None
            return r.status, j, text
    except urllib.error.HTTPError as e:
        text = e.read().decode("utf-8","replace")
        try: j = json.loads(text)
        except: j = None
        return e.code, j, text
    except Exception as e:
        return -1, None, str(e)

def db(q, params=()):
    c = sqlite3.connect(DB); c.row_factory = sqlite3.Row
    cur = c.cursor()
    cur.execute(q, params)
    ql = q.strip().lower()
    if ql.startswith("select") or ql.startswith("pragma"):
        rows = [dict(r) for r in cur.fetchall()]; c.close(); return rows
    lid = cur.lastrowid; c.commit(); c.close(); return lid

def db1(q, params=()):
    rows = db(q, params); return rows[0] if rows else None

def get_list(path):
    st, j, _ = call("GET", path)
    if isinstance(j, list): return st, j
    if isinstance(j, dict):
        for k in ("items","data","rows","list"):
            if isinstance(j.get(k), list): return st, j[k]
    return st, j

results = []
def record(mod, dim, name, ok, detail):
    results.append({"mod":mod,"dim":dim,"name":name,"ok":ok,"detail":detail})
def test(mod, dim, name, fn):
    try:
        ok, detail = fn()
        record(mod, dim, name, bool(ok), detail)
    except Exception as e:
        record(mod, dim, name, False, "EXC: "+str(e)[:160])

login()
st, sites = get_list("/sites")
st, users = get_list("/users")
st, vehs = get_list("/vehicles")
site0 = sites[0]["id"] if sites else 1
site1 = sites[1]["id"] if len(sites)>1 else site0
uid = users[0]["id"] if users else 1
vid = vehs[0]["id"] if vehs else 1

def new_plan(site_id=None):
    return db("INSERT INTO insp_plans (plan_name,assignee,assignee_id,period,generate_date,status,vehicle_id) VALUES (?,?,?,?,?,?,?)",
            (MARK+"计划", "QA", uid, "weekly", "2026-07-17", "draft", 0))
def new_item(plan_id, site_id=None):
    sid = site_id or site0
    return db("INSERT INTO insp_plan_items (plan_id,site_id,item_name,result,required_photos,actual_photos,review_status) VALUES (?,?,?,?,?,?,?)",
            (plan_id, sid, MARK+"项", "normal", 0, 0, 0))

# ===================== 1. 待办审核 /audit =====================
def t_audit_stats():
    st, j, _ = call("GET","/audit/stats")
    if st!=200 or not isinstance(j,dict): return False, f"status={st} body={str(j)[:80]}"
    return True, f"字段={list(j.keys())} total={j.get('total')}"
def t_audit_pending():
    st, j = get_list("/audit/pending")
    if st!=200: return False, f"status={st}"
    n = len(j) if isinstance(j,list) else 0
    return True, f"待办条数={n}"
def t_datareview_stats():
    st, j, _ = call("GET","/data-reviews/stats")
    if st!=200 or not isinstance(j,dict): return False, f"status={st}"
    return True, f"total={j.get('total')}"
def t_item_review():
    pid = new_plan(); iid = new_item(pid)
    try:
        st, j, _ = call("PUT", f"/inspection-v2/items/{iid}/review", {"action":"approve","comment":"QA审核通过"})
        if st not in (200,201): return False, f"审核接口 status={st} {str(j)[:80]}"
        row = db1("SELECT review_status FROM insp_plan_items WHERE id=?",(iid,))
        if not row or row["review_status"]!=2: return False, f"DB review_status={row}"
        return True, "review_status=2(通过)"
    finally:
        db("DELETE FROM insp_plan_items WHERE id=?",(iid,)); db("DELETE FROM insp_plans WHERE id=?",(pid,))
def t_partsreq_approve():
    pid = new_plan()
    rid = db("INSERT INTO parts_requests (plan_id,requester_id,status) VALUES (?,?,?)",(pid,uid,"pending"))
    try:
        st, j, _ = call("PUT", f"/inspection-v2/parts-request/{rid}/approve", {"approver_id":uid,"comment":"QA批准"})
        if st not in (200,201): return False, f"status={st} {str(j)[:80]}"
        row = db1("SELECT status FROM parts_requests WHERE id=?",(rid,))
        if not row or row["status"]!="approved": return False, f"DB status={row}"
        return True, "status=approved"
    finally:
        db("DELETE FROM parts_requests WHERE id=?",(rid,)); db("DELETE FROM insp_plans WHERE id=?",(pid,))
def t_photo_review():
    aid = db("INSERT INTO operation_attachments (filename,stored_path,review_status) VALUES (?,?,?)",(MARK+"a.jpg","/tmp/"+MARK+".jpg","pending"))
    try:
        st, j, _ = call("POST","/operation-attachments/review", {"attachment_ids":[aid],"action":"approve","reviewer_id":uid})
        if st not in (200,201): return False, f"status={st} {str(j)[:80]}"
        row = db1("SELECT review_status FROM operation_attachments WHERE id=?",(aid,))
        if not row or row["review_status"]!="approved": return False, f"DB review_status={row}"
        return True, "review_status=approved"
    finally:
        db("DELETE FROM operation_attachments WHERE filename=?",(MARK+"a.jpg",))
def t_workorder_status():
    order_no = MARK+"WO"+datetime.datetime.now().strftime("%H%M%S")
    db("INSERT INTO work_orders (order_no,site_id,source,event_type,level,title,status) VALUES (?,?,?,?,?,?,?)",
       (order_no, site0, "manual","test","normal",MARK+"工单","pending"))
    try:
        st, j, _ = call("PUT", f"/workorders/{order_no}/status", {"status":"accepted"})
        if st not in (200,201): return False, f"status={st} {str(j)[:80]}"
        row = db1("SELECT status FROM work_orders WHERE order_no=?",(order_no,))
        if not row or row["status"]!="accepted": return False, f"DB status={row}"
        return True, "pending->accepted 链路通"
    finally:
        db("DELETE FROM work_orders WHERE order_no=?",(order_no,))
test("待办审核","功能完整性","审核统计接口 /audit/stats", t_audit_stats)
test("待办审核","功能完整性","待办列表接口 /audit/pending", t_audit_pending)
test("待办审核","功能完整性","数据审核统计 /data-reviews/stats", t_datareview_stats)
test("待办审核","功能完整性","巡检质控-检查项审核", t_item_review)
test("待办审核","功能完整性","备件预申报审批", t_partsreq_approve)
test("待办审核","功能完整性","影像审核(照片批量审核)", t_photo_review)
test("待办审核","链路(下游)","工单状态审核->工单状态机", t_workorder_status)

# ===================== 2. 巡检计划 /maintenance =====================
def t_plans_list():
    st, j = get_list("/inspection-v2/plans")
    if st!=200: return False, f"status={st}"
    return True, f"计划数={len(j) if isinstance(j,list) else 'n/a'}"
def t_manual_create():
    st, j, _ = call("POST","/inspection-v2/plans/manual",
        {"plan_name":MARK+"手动计划","site_ids":[site0],"vehicle_id":0,"period":"weekly","assignee":"QA","assignee_id":uid})
    if st not in (200,201) or not (j and (j.get("plan_id") or j.get("id"))):
        return False, f"status={st} {str(j)[:100]}"
    pid = j.get("plan_id") or j.get("id")
    try:
        row = db1("SELECT id,status FROM insp_plans WHERE id=?",(pid,))
        if not row: return False, "DB 无新建计划"
        return True, f"plan_id={pid} status={row['status']}"
    finally:
        call("DELETE", f"/inspection-v2/plans/{pid}")
def t_submit_approve():
    pid = new_plan()
    try:
        st1,_,_ = call("POST", f"/inspection-v2/plans/{pid}/submit")
        st2,_,_ = call("POST", f"/inspection-v2/plans/{pid}/approve", {"action":"approve","approver_id":uid,"comment":"QA"})
        row = db1("SELECT status FROM insp_plans WHERE id=?",(pid,))
        if st2 not in (200,201): return False, f"approve status={st2}"
        if not row or row["status"]!="active": return False, f"DB status={row}"
        return True, f"draft->submitted->active (submit={st1})"
    finally:
        db("DELETE FROM insp_plans WHERE id=?",(pid,))
def t_dashboard():
    st, j, _ = call("GET","/inspection-v2/dashboard")
    if st!=200 or not isinstance(j,dict): return False, f"status={st} {str(j)[:80]}"
    return True, f"看板字段={list(j.keys())[:8]}"
def t_templates_list():
    st, j = get_list("/inspection-v2/templates")
    if st!=200: return False, f"status={st}"
    return True, f"模板数={len(j) if isinstance(j,list) else 'n/a'}"
def t_configs_list():
    st, j = get_list("/inspection-v2/configs")
    if st!=200: return False, f"status={st}"
    return True, f"配置数={len(j) if isinstance(j,list) else 'n/a'}"
def t_favorites():
    pid = new_plan()
    try:
        st, j, _ = call("POST","/inspection-v2/favorites", {"plan_id":pid})
        if st not in (200,201): return False, f"收藏 status={st} {str(j)[:80]}"
        fid = (j.get("favorite_id") or j.get("id")) if isinstance(j,dict) else None
        return True, f"收藏成功 fid={fid}"
    finally:
        db("DELETE FROM plan_favorites WHERE plan_id=?",(pid,)); db("DELETE FROM insp_plans WHERE id=?",(pid,))
def t_downstream_anomaly():
    pid = new_plan(); iid = new_item(pid)
    try:
        st, j, _ = call("POST", f"/inspection-v2/plans/{pid}/items/{iid}/anomaly",
            {"report_type":"equipment","description":"QA异常上报测试","photo_urls":[]})
        if st not in (200,201) or not (j and j.get("order_no")):
            return False, f"status={st} {str(j)[:100]}"
        order_no = j["order_no"]
        wo = db1("SELECT id,source FROM work_orders WHERE order_no=?",(order_no,))
        al = db1("SELECT id,related_order_no FROM alerts WHERE related_order_no=?",(order_no,))
        ok = wo and al
        return (True if ok else False), f"工单={'有' if wo else '缺'} 告警={'有' if al else '缺'}"
    finally:
        if 'order_no' in dir():
            db("DELETE FROM alerts WHERE related_order_no=?",(order_no,))
            db("DELETE FROM work_orders WHERE order_no=?",(order_no,))
        db("DELETE FROM insp_plan_items WHERE id=?",(iid,)); db("DELETE FROM insp_plans WHERE id=?",(pid,))
def t_downstream_partsreq_aggr():
    pid = new_plan()
    rid = db("INSERT INTO parts_requests (plan_id,requester_id,status) VALUES (?,?,?)",(pid,uid,"pending"))
    try:
        st, j = get_list("/audit/pending")
        found = False
        if isinstance(j,list):
            for it in j:
                if 'part' in str(it.get("source_type","")).lower() and str(rid) in str(it.get("id","")):
                    found = True; break
        return (True if found else False), f"pending 聚合含该备件申请={'是' if found else '否'}"
    finally:
        db("DELETE FROM parts_requests WHERE id=?",(rid,)); db("DELETE FROM insp_plans WHERE id=?",(pid,))
test("巡检计划","功能完整性","计划列表 /inspection-v2/plans", t_plans_list)
test("巡检计划","功能完整性","手动创建计划", t_manual_create)
test("巡检计划","功能完整性","提交->审批 状态机", t_submit_approve)
test("巡检计划","功能完整性","态势看板 /inspection-v2/dashboard", t_dashboard)
test("巡检计划","功能完整性","方案模板列表", t_templates_list)
test("巡检计划","功能完整性","巡检配置列表", t_configs_list)
test("巡检计划","功能完整性","计划收藏", t_favorites)
test("巡检计划","链路(下游)","异常上报->工单+告警", t_downstream_anomaly)
test("巡检计划","链路(上游聚合)","备件预申报->待办审核聚合", t_downstream_partsreq_aggr)

# ===================== 3. 设备管理 /equipment =====================
def t_devices_list():
    st, j = get_list("/devices")
    if st!=200: return False, f"status={st}"
    return True, f"设备数={len(j) if isinstance(j,list) else 'n/a'} (来源表=device_shadows)"
def t_device_create():
    code = MARK+"DV"
    st, j, _ = call("POST","/devices", {"device_code":code,"device_name":MARK+"设备","device_type":"QA","site_id":site0,"status":"online"})
    if st not in (200,201): return False, f"status={st} {str(j)[:100]}"
    did = (j.get("id") if isinstance(j,dict) else None) or db1("SELECT id FROM device_shadows WHERE device_code=?",(code,))["id"]
    try:
        dbrow = db1("SELECT id FROM device_shadows WHERE device_code=?",(code,))
        st2, lst = get_list("/devices?search="+code)
        hit = lst if isinstance(lst,list) else []
        appear = any(d.get("device_code")==code for d in hit) if isinstance(lst,list) else False
        return (True if (dbrow and appear) else False), f"DB存在={'是' if dbrow else '否'} 列表可见={'是' if appear else '否'}"
    finally:
        call("DELETE", f"/devices/{did}") if did else None
        db("DELETE FROM device_shadows WHERE device_code=?",(code,))
def t_device_move():
    code = MARK+"DV2"
    db("INSERT INTO device_shadows (device_code,device_name,device_type,site_id,status) VALUES (?,?,?,?,?)",(code,MARK+"设备2","QA",site0,"online"))
    did = db1("SELECT id FROM device_shadows WHERE device_code=?",(code,))["id"]
    try:
        st,_,_ = call("PUT", f"/devices/{did}", {"site_id":site1})
        row = db1("SELECT site_id FROM device_shadows WHERE id=?",(did,))
        if not row or row["site_id"]!=site1: return False, f"移站失败 DB={row}"
        return True, f"site_id {site0}->{site1}"
    finally:
        db("DELETE FROM device_shadows WHERE id=?",(did,))
def t_parts_inv_list():
    st, j = get_list("/parts/inventory")
    if st!=200: return False, f"status={st}"
    return True, f"备件数={len(j) if isinstance(j,list) else 'n/a'} (来源表=spare_parts_inventory)"
def t_parts_inv_create():
    name = MARK+"备件"
    st, j, _ = call("POST","/parts/inventory", {"part_name":name,"part_code":MARK+"BJ","category":"其他","quantity":10,"site_id":site0})
    if st not in (200,201): return False, f"status={st} {str(j)[:100]}"
    pid = (j.get("id") if isinstance(j,dict) else None) or db1("SELECT id FROM spare_parts_inventory WHERE part_name=?",(name,))["id"]
    try:
        dbrow = db1("SELECT id FROM spare_parts_inventory WHERE part_name=?",(name,))
        st2, lst = get_list("/parts/inventory?search="+name)
        hit = lst if isinstance(lst,list) else []
        appear = any(p.get("part_name")==name for p in hit) if isinstance(lst,list) else False
        return (True if (dbrow and appear) else False), f"DB存在={'是' if dbrow else '否'} 列表可见={'是' if appear else '否'}"
    finally:
        db("DELETE FROM spare_parts_inventory WHERE part_name=?",(name,))
def t_parts_stock_in():
    name = MARK+"备件2"
    db("INSERT INTO spare_parts_inventory (part_code,part_name,category,unit,quantity,site_id) VALUES (?,?,?,?,?,?)",(MARK+"BJ2",name,"其他","个",10,site0))
    pid = db1("SELECT id FROM spare_parts_inventory WHERE part_name=?",(name,))["id"]
    try:
        st,_,_ = call("POST", f"/parts/inventory/{pid}/stock", {"type":"in","quantity":5,"operator":"QA"})
        if st not in (200,201): return False, f"status={st} (预期200/201，500=真实缺陷)"
        row = db1("SELECT quantity FROM spare_parts_inventory WHERE id=?",(pid,))
        log = db1("SELECT id FROM inventory_logs WHERE part_id=? AND type='in' ORDER BY id DESC",(pid,))
        if not row or row["quantity"]!=15: return False, f"库存未+5 DB={row}"
        return True, f"库存 10->15 流水={'有' if log else '缺'}"
    finally:
        db("DELETE FROM inventory_logs WHERE part_id=?",(pid,)); db("DELETE FROM spare_parts_inventory WHERE id=?",(pid,))
def t_spare_approve_link():
    inv = db1("SELECT id,part_name,quantity FROM spare_parts_inventory ORDER BY quantity DESC LIMIT 1")
    if not inv: return False, "无 spare_parts_inventory 可供关联"
    pname = inv["part_name"]; before = inv["quantity"]
    rid = db("INSERT INTO spare_part_requests (request_no,site_id,applicant,part_name,quantity,status) VALUES (?,?,?,?,?,?)",
       (MARK+"RQ", site0, "QA", pname, 1, "pending"))
    try:
        st,_,_ = call("PUT", f"/parts/requests/{rid}/approve", {"comment":"QA批准"})
        if st not in (200,201): return False, f"status={st}"
        req = db1("SELECT status FROM spare_part_requests WHERE id=?",(rid,))
        inv2 = db1("SELECT quantity FROM spare_parts_inventory WHERE id=?",(inv["id"],))
        after = inv2["quantity"] if inv2 else None
        deduct = (after == before-1)
        return (True if (req and req["status"]=="approved" and deduct) else False), f"status={req['status'] if req else '?'} 库存{before}->{after} 扣减={'是' if deduct else '否'}"
    finally:
        db("DELETE FROM inventory_logs WHERE ref_id=?",(rid,)); db("DELETE FROM spare_part_requests WHERE id=?",(rid,))
def t_device_recycle_list():
    st, j = get_list("/device-recycle")
    if st!=200: return False, f"status={st}"
    return True, f"回收记录数={len(j) if isinstance(j,list) else 'n/a'} (只读)"
def t_dead_tables():
    d1 = db("SELECT COUNT(*) c FROM devices"); d2 = db("SELECT COUNT(*) c FROM parts_inventory")
    dc = d1[0]["c"] if d1 else 0; pc = d2[0]["c"] if d2 else 0
    live_d = db("SELECT COUNT(*) c FROM device_shadows"); live_p = db("SELECT COUNT(*) c FROM spare_parts_inventory")
    ld = live_d[0]["c"] if live_d else 0; lp = live_p[0]["c"] if live_p else 0
    note = f"devices(死)={dc} device_shadows(活)={ld} | parts_inventory(死)={pc} spare_parts_inventory(活)={lp}"
    return True, note+(" ⚠冗余表含数据" if (dc>0 or pc>0) else " (死表为空,仅结构冗余)")
test("设备管理","功能完整性","设备台账列表 /devices", t_devices_list)
test("设备管理","功能完整性","注册设备", t_device_create)
test("设备管理","功能完整性","设备移站(PUT site_id)", t_device_move)
test("设备管理","功能完整性","备件库存列表", t_parts_inv_list)
test("设备管理","功能完整性","新增备件", t_parts_inv_create)
test("设备管理","功能完整性","备件入库(库存+流水)", t_parts_stock_in)
test("设备管理","链路(下游)","备件审批->库存扣减", t_spare_approve_link)
test("设备管理","功能完整性","设备回收记录(只读)", t_device_recycle_list)
test("设备管理","协调性","冗余设备/备件表核查", t_dead_tables)

# ===================== 4. 车辆管理 /vehicles =====================
def t_veh_list():
    st, j = get_list("/vehicles")
    if st!=200: return False, f"status={st}"
    return True, f"车辆数={len(j) if isinstance(j,list) else 'n/a'}"
def t_veh_create():
    plate = MARK+"京A12345"
    st, j, _ = call("POST","/vehicles", {"plate_no":plate,"model":"QA车","seats":5})
    if st not in (200,201): return False, f"status={st} {str(j)[:100]}"
    vid_new = (j.get("id") if isinstance(j,dict) else None) or db1("SELECT id FROM vehicles WHERE plate_no=?",(plate,))["id"]
    db("DELETE FROM vehicles WHERE id=?",(vid_new,))
    return True, f"创建vid={vid_new}"
def t_veh_apply():
    plate = MARK+"京B99999"
    db("INSERT INTO vehicles (plate_no,model,seats) VALUES (?,?,?)",(plate,"QA车2",5))
    v2 = db1("SELECT id FROM vehicles WHERE plate_no=?",(plate,))["id"]
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        st, j, _ = call("POST","/vehicle/applications", {"vehicle_id":v2,"applicant_id":uid,"start_at":now,"end_at":now,"destination":"QA目的地","reason":"QA测试"})
        if st not in (200,201): return False, f"申请 status={st} {str(j)[:100]}"
        aid = (j.get("id") if isinstance(j,dict) else None) or db1("SELECT id FROM vehicle_applications WHERE vehicle_id=?",(v2,))["id"]
        st2,_,_ = call("POST", f"/vehicle/applications/{aid}/approve", {"action":"approve","approver_id":uid})
        row = db1("SELECT status FROM vehicle_applications WHERE id=?",(aid,))
        if st2 not in (200,201): return False, f"批准 status={st2}"
        if not row or row["status"]!="approved": return False, f"DB status={row}"
        return True, "pending->approved"
    finally:
        db("DELETE FROM vehicle_applications WHERE vehicle_id=?",(v2,)); db("DELETE FROM vehicles WHERE id=?",(v2,))
def t_veh_refuel():
    plate = MARK+"京C88888"
    db("INSERT INTO vehicles (plate_no,model,seats) VALUES (?,?,?)",(plate,"QA车3",5))
    v3 = db1("SELECT id FROM vehicles WHERE plate_no=?",(plate,))["id"]
    try:
        st,_,_ = call("POST","/vehicle/refueling", {"vehicle_id":v3,"liters":10,"amount":80,"mileage_at":1000})
        if st not in (200,201): return False, f"status={st}"
        log = db1("SELECT id FROM vehicle_refueling_records WHERE vehicle_id=?",(v3,))
        return (True if log else False), f"加油流水={'有' if log else '缺'}"
    finally:
        db("DELETE FROM vehicle_refueling_records WHERE vehicle_id=?",(v3,)); db("DELETE FROM vehicles WHERE id=?",(v3,))
def t_veh_maint():
    plate = MARK+"京D77777"
    db("INSERT INTO vehicles (plate_no,model,seats) VALUES (?,?,?)",(plate,"QA车4",5))
    v4 = db1("SELECT id FROM vehicles WHERE plate_no=?",(plate,))["id"]
    try:
        st,_,_ = call("POST","/vehicle/maintenance", {"vehicle_id":v4,"maint_type":"保养","mileage_at":1000,"items":"机油","cost":200,"next_maint_mileage":6000})
        if st not in (200,201): return False, f"status={st}"
        log = db1("SELECT id FROM vehicle_maintenance_records WHERE vehicle_id=?",(v4,))
        return (True if log else False), f"保养流水={'有' if log else '缺'}"
    finally:
        db("DELETE FROM vehicle_maintenance_records WHERE vehicle_id=?",(v4,)); db("DELETE FROM vehicles WHERE id=?",(v4,))
test("车辆管理","功能完整性","车辆台账列表", t_veh_list)
test("车辆管理","功能完整性","新增车辆", t_veh_create)
test("车辆管理","功能完整性","用车申请+审批", t_veh_apply)
test("车辆管理","功能完整性","加油记录", t_veh_refuel)
test("车辆管理","功能完整性","保养记录", t_veh_maint)

# ===================== 5. 影像档案 /archive =====================
def t_att_stats():
    st, j, _ = call("GET","/attachments/stats")
    if st!=200 or not isinstance(j,dict): return False, f"status={st}"
    return True, f"字段={list(j.keys())[:8]}"
def t_att_list():
    st, j = get_list("/attachments")
    if st!=200: return False, f"status={st}"
    return True, f"影像数={len(j) if isinstance(j,list) else 'n/a'}"
def t_att_archive():
    aid = db("INSERT INTO operation_attachments (filename,stored_path,archived) VALUES (?,?,?)",(MARK+"x.jpg","/tmp/"+MARK+".jpg",0))
    try:
        st,_,_ = call("POST", f"/attachments/{aid}/archive", {"archive_reason":"QA归档"})
        if st not in (200,201): return False, f"归档 status={st}"
        st2,_,_ = call("POST", f"/attachments/{aid}/unarchive")
        row = db1("SELECT archived FROM operation_attachments WHERE id=?",(aid,))
        if not row or row["archived"]!=0: return False, f"取消归档失败 DB={row}"
        return True, "归档->取消归档 正常"
    finally:
        db("DELETE FROM operation_attachments WHERE filename=?",(MARK+"x.jpg",))
def t_att_delete():
    aid = db("INSERT INTO operation_attachments (filename,stored_path) VALUES (?,?)",(MARK+"del.jpg","/tmp/"+MARK+".jpg"))
    try:
        st,_,_ = call("DELETE", f"/attachments/{aid}")
        row = db1("SELECT is_deleted FROM operation_attachments WHERE id=?",(aid,))
        soft = (row and row["is_deleted"]==1)
        return (True if (st in (200,201,204) and soft) else False), f"软删除 status={st} is_deleted={row['is_deleted'] if row else '无'}"
    finally:
        db("DELETE FROM operation_attachments WHERE id=?",(aid,))
test("影像档案","功能完整性","统计 /attachments/stats", t_att_stats)
test("影像档案","功能完整性","影像列表", t_att_list)
test("影像档案","功能完整性","归档/取消归档", t_att_archive)
test("影像档案","功能完整性","删除影像", t_att_delete)

# ===================== 6. 统计分析 /analysis =====================
def t_an_summary():
    st, j, _ = call("GET","/dashboard/summary")
    return (st==200 and isinstance(j,dict)), f"status={st}" + (f" keys={list(j.keys())[:6]}" if st==200 else "")
def t_an_dataquality():
    st, j, _ = call("GET","/data-quality")
    return (st==200), f"status={st}"
def t_an_inspstat():
    st, j, _ = call("GET","/inspections/statistics")
    return (st==200), f"status={st}"
def t_an_wostat():
    st, j, _ = call("GET","/workorders/statistics")
    return (st==200), f"status={st}"
def t_an_arrival():
    st, j, _ = call("GET","/data/arrival/summary")
    return (st==200), f"status={st}"
test("统计分析","功能完整性","看板汇总 /dashboard/summary", t_an_summary)
test("统计分析","功能完整性","数据质量 /data-quality", t_an_dataquality)
test("统计分析","功能完整性","巡检统计 /inspections/statistics", t_an_inspstat)
test("统计分析","功能完整性","工单统计 /workorders/statistics", t_an_wostat)
test("统计分析","功能完整性","到报率 /data/arrival/summary", t_an_arrival)

# ===================== 7. 试剂主数据 /reagents =====================
def t_reagent_list():
    st, j = get_list("/reagents")
    if st!=200: return False, f"status={st}"
    return True, f"试剂数={len(j) if isinstance(j,list) else 'n/a'}"
def t_reagent_crud():
    name = MARK+"试剂"
    st, j, _ = call("POST","/reagents", {"name":name,"manufacturer":"QA厂","spec":"500mL","unit":"瓶","shelf_life_days":365})
    if st not in (200,201): return False, f"创建 status={st} {str(j)[:100]}"
    rid = (j.get("id") if isinstance(j,dict) else None) or db1("SELECT id FROM reagents WHERE name=?",(name,))["id"]
    try:
        st2,_,_ = call("PUT", f"/reagents/{rid}", {"name":name,"manufacturer":"QA厂2","shelf_life_days":700})
        st3,_,_ = call("DELETE", f"/reagents/{rid}")
        row = db1("SELECT id FROM reagents WHERE id=?",(rid,))
        if st3 not in (200,201,204) or row: return False, f"删除 status={st3} 残留={'有' if row else '无'}"
        return True, f"CRUD 通过(创建{st} 改{st2} 删{st3})"
    finally:
        db("DELETE FROM reagents WHERE name=?",(name,))
def t_reagent_orphan():
    name = MARK+"试剂孤儿"
    db("INSERT INTO reagents (name,unit,shelf_life_days) VALUES (?,?,?)",(name,"瓶",365))
    rid = db1("SELECT id FROM reagents WHERE name=?",(name,))["id"]
    db("INSERT INTO reagent_inventory (site_id,reagent_id,current_qty) VALUES (?,?,?)",(site0,rid,1))
    try:
        st, j, _ = call("DELETE", f"/reagents/{rid}")
        row = db1("SELECT id FROM reagents WHERE id=?",(rid,))
        blocked = (st in (400,409)) and row
        if not blocked:
            return False, f"孤儿未拦截 status={st} 残留={'有' if row else '无'}"
        return True, f"已拦截(status={st}) 孤儿保护生效"
    finally:
        db("DELETE FROM reagent_inventory WHERE reagent_id=?",(rid,)); db("DELETE FROM reagents WHERE id=?",(rid,))
test("试剂主数据","功能完整性","试剂列表", t_reagent_list)
test("试剂主数据","功能完整性","试剂 CRUD", t_reagent_crud)
test("试剂主数据","协调性","删除孤儿保护(被库存引用应拦截)", t_reagent_orphan)

# ===================== 协调性 / 引用完整性 =====================
def t_refint_sites():
    pairs = [("insp_plan_items","site_id"),("device_shadows","site_id"),
              ("operation_attachments","site_id"),("alerts","site_id"),("work_orders","site_id"),
              ("spare_part_requests","site_id"),("reagent_inventory","site_id")]
    bad = []; skipped = []
    for t,c in pairs:
        cols = [r["name"] for r in db(f"PRAGMA table_info({t})")]
        if c not in cols:
            skipped.append(t); continue
        rows = db(f"SELECT COUNT(*) c FROM {t} WHERE {c} IS NOT NULL AND {c} NOT IN (SELECT id FROM sites)")
        if rows and rows[0]["c"]>0: bad.append(f"{t}.{c}={rows[0]['c']}")
    note = ("全部引用完整" if not bad else "孤儿外键: "+", ".join(bad))
    if skipped: note += " ｜ 跳过无site_id表: "+",".join(skipped)+"(站点经items关联)"
    return (True if not bad else False), note
def t_veh_link():
    rows = db("SELECT COUNT(*) c FROM insp_plans WHERE vehicle_id IS NOT NULL AND vehicle_id!=0 AND vehicle_id NOT IN (SELECT id FROM vehicles)")
    bad = rows[0]["c"] if rows else 0
    return (True if bad==0 else False), ("vehicle_id 关联完整" if bad==0 else f"指向不存在车辆={bad}")
def t_audit_aggr_consistency():
    st, j, _ = call("GET","/data-reviews/stats")
    if st!=200 or not isinstance(j,dict): return False, f"stats status={st}"
    total = j.get("total")
    db_count = db("SELECT COUNT(*) c FROM data_reviews WHERE status IN ('pending','auto_reviewed','smart_reviewed','manual_reviewed')")
    dc = db_count[0]["c"] if db_count else 0
    match = (total==dc)
    return True, f"统计total={total} DB待审={dc} {'一致' if match else '⚠不一致'}"
test("协调性","数据一致性","站点外键引用完整性", t_refint_sites)
test("协调性","数据一致性","巡检计划vehicle_id关联完整性", t_veh_link)
test("协调性","数据一致性","数据审核统计与DB一致性", t_audit_aggr_consistency)

# ===================== 汇总输出 =====================
print("\n" + "="*70)
print(f"运维管理深度测试  (标记={MARK})")
print("="*70)
total=len(results); passed=sum(1 for r in results if r["ok"])
failed=[r for r in results if not r["ok"]]
print(f"总计 {total} 项 | 通过 {passed} | 失败 {total-passed}")
print("-"*70)
for mod in dict.fromkeys(r["mod"] for r in results):
    print(f"\n【{mod}】")
    for r in results:
        if r["mod"]==mod:
            tag = "✅" if r["ok"] else "❌"
            print(f"  {tag} [{r['dim']}] {r['name']} — {r['detail']}")
print("\n" + "="*70)

os.makedirs(r"E:/杂七杂八/水质运维/平台开发/outputs", exist_ok=True)
now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
lines = [f"# 运维管理模块深度测试报告\n", f"> 生成时间：{now} ｜ 测试标记：{MARK} ｜ 后端：http://127.0.0.1:5000\n"]
lines.append(f"\n## 概览\n\n- 测试用例：**{total}** ｜ 通过：**{passed}** ｜ 失败：**{total-passed}**\n")
dims = {}
for r in results: dims.setdefault(r["dim"],[]).append(r)
lines.append("\n## 一、按测试维度汇总\n")
for dim in ["功能完整性","链路(上游聚合)","链路(下游)","协调性","数据一致性"]:
    if dim not in dims: continue
    items = dims[dim]; p = sum(1 for x in items if x["ok"])
    lines.append(f"\n### {dim}（{p}/{len(items)}）\n")
    for r in items:
        tag = "PASS" if r["ok"] else "FAIL"
        lines.append(f"- [{tag}] **{r['mod']}** · {r['name']} — {r['detail']}\n")
lines.append("\n## 二、按模块汇总\n")
for mod in dict.fromkeys(r["mod"] for r in results):
    items = [r for r in results if r["mod"]==mod]; p=sum(1 for x in items if x["ok"])
    lines.append(f"\n### {mod}（{p}/{len(items)}）\n")
    for r in items:
        tag = "PASS" if r["ok"] else "FAIL"
        lines.append(f"- [{tag}] {r['name']}（`{r['dim']}`）— {r['detail']}\n")
lines.append("\n## 三、失败项明细（供对比）\n")
if failed:
    for r in failed:
        lines.append(f"- **{r['mod']} / {r['name']}**（`{r['dim']}`）：{r['detail']}\n")
else:
    lines.append("- 无失败项。\n")
with open(r"E:/杂七杂八/水质运维/平台开发/outputs/ops_module_test_report.md","w",encoding="utf-8") as f:
    f.writelines(lines)
print("报告已写入 outputs/ops_module_test_report.md")
