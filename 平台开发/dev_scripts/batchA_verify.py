import urllib.request, urllib.error, json, sqlite3

BASE = "http://127.0.0.1:5000"
DB = "backend/data/water.db"
TOKEN = None

def req(m, p, b=None, headers=None):
    h = {"Content-Type": "application/json"}
    if TOKEN: h["Authorization"] = "Bearer " + TOKEN
    if headers: h.update(headers)
    data = json.dumps(b).encode() if b is not None else None
    r = urllib.request.Request(BASE + p, data=data, method=m, headers=h)
    try:
        x = urllib.request.urlopen(r, timeout=10)
        return x.status, (json.loads(x.read().decode()) if x.read else {})
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:300]
    except Exception as e:
        return "ERR", repr(e)

def db(q, p=(), commit=False):
    c = sqlite3.connect(DB); c.row_factory = sqlite3.Row
    if commit:
        c.execute(q, p); c.commit(); c.close(); return None
    r = [dict(x) for x in c.execute(q, p).fetchall()]; c.close(); return r

PASS = []; FAIL = []
def check(name, ok, info=""):
    (PASS if ok else FAIL).append(name)
    print(f"  {'✅ PASS' if ok else '❌ FAIL'} {name}" + (f"  -> {info}" if info and not ok else ""))

# 登录
s, j = req("POST", "/api/auth/login", {"username": "admin", "password": "admin123"})
TOKEN = j["token"]
print("=== 批次 A 验证 ===\n")

print("[A1] 备件预申报断路修复")
# 旧路径应失败
s1, _ = req("POST", "/api/inspection-v2/plans/1/spare-parts", {"items": [{"parts_id": 1, "quantity": 1}]})
check("旧 /spare-parts 不存在", s1 in (404, 405), f"status={s1}")
# 取一个真实备件 code
part = db("SELECT part_code, part_name FROM spare_parts_inventory LIMIT 1")[0]
# 新路径 + 正确 payload（part_sku）
s2, j2 = req("POST", "/api/inspection-v2/plans/1/parts-request",
              {"items": [{"part_sku": part["part_code"], "quantity": 2}], "requester_id": 1})
check("新 /parts-request 返回 201", s2 == 201, f"status={s2} body={j2}")
rid = j2.get("request_id")
if rid:
    rows = db("SELECT part_sku, quantity FROM parts_request_items WHERE request_id=?", (rid,))
    ok_sku = any(r["part_sku"] == part["part_code"] for r in rows)
    check("明细 part_sku 正确写入(非 parts_id)", ok_sku, f"rows={rows}")
    db("DELETE FROM parts_request_items WHERE request_id=?", (rid,), commit=True)
    db("DELETE FROM parts_requests WHERE id=?", (rid,), commit=True)

print("\n[A2] result 枚举清洗 + 后端归一")
rows = db("SELECT COUNT(*) c FROM insp_plan_items WHERE result='正常'")
check("库内无残留中文'正常'", rows[0]["c"] == 0, f"剩余={rows[0]['c']}")
# 找一个 active 计划里待执行的项
active = db("SELECT id FROM insp_plans WHERE status='active' LIMIT 1")
if active:
    pid = active[0]["id"]
    item = db("SELECT id FROM insp_plan_items WHERE plan_id=? AND result IS NULL LIMIT 1", (pid,))
    if item:
        iid = item[0]["id"]
        s3, _ = req("PUT", f"/api/inspection-v2/plans/{pid}/items/{iid}", {"result": "正常"})
        r = db("SELECT result FROM insp_plan_items WHERE id=?", (iid,))[0]
        check("后端将'正常'归一为'normal'", r["result"] == "normal", f"实际={r['result']}")
        # 还原
        db("UPDATE insp_plan_items SET result=NULL, completed_at=NULL, review_status=2 WHERE id=?", (iid,), commit=True)

print("\n[A3] 状态门禁")
# PUT item 在非 active 计划上应 400
draft = db("SELECT id FROM insp_plans WHERE status='draft' LIMIT 1")
if draft:
    dp = draft[0]["id"]
    di = db("SELECT id FROM insp_plan_items WHERE plan_id=? LIMIT 1", (dp,))
    if di:
        s4, _ = req("PUT", f"/api/inspection-v2/plans/{dp}/items/{di[0]['id']}", {"result": "normal"})
        check("非 active 计划不可提交检查项(PUT 400)", s4 == 400, f"status={s4}")
# 删除已完成计划应 400
comp = db("SELECT id FROM insp_plans WHERE status='completed' LIMIT 1")
if comp:
    s5, _ = req("DELETE", f"/api/inspection-v2/plans/{comp[0]['id']}")
    check("已完成计划不可删除(DELETE 400)", s5 == 400, f"status={s5}")
# 删除 draft 计划应 200
if draft:
    s6, _ = req("DELETE", f"/api/inspection-v2/plans/{dp}")
    check("草稿计划可删除(DELETE 200)", s6 == 200, f"status={s6}")

print("\n[A4] 未拍照不可判正常")
# 找一个 required_photos>0 且 actual_photos<required 的待执行项（最好 active 计划）
photo_item = db("""SELECT i.id, i.plan_id, i.required_photos, i.actual_photos
    FROM insp_plan_items i JOIN insp_plans p ON i.plan_id=p.id
    WHERE p.status='active' AND i.required_photos>0 AND i.result IS NULL
    AND (i.actual_photos IS NULL OR i.actual_photos < i.required_photos) LIMIT 1""")
if photo_item:
    pi = photo_item[0]
    s7, b7 = req("PUT", f"/api/inspection-v2/plans/{pi['plan_id']}/items/{pi['id']}", {"result": "normal"})
    check("拍照不足判正常被拦截(400)", s7 == 400, f"status={s7} msg={b7}")
    # 模拟已传满照片后判正常
    s8, _ = req("PUT", f"/api/inspection-v2/plans/{pi['plan_id']}/items/{pi['id']}",
               {"result": "normal", "actual_photos": pi["required_photos"]})
    r = db("SELECT result FROM insp_plan_items WHERE id=?", (pi["id"],))[0]
    check("拍照满足后可判正常", s8 == 200 and r["result"] == "normal", f"status={s8} result={r['result']}")
    db("UPDATE insp_plan_items SET result=NULL, completed_at=NULL, review_status=2 WHERE id=?", (pi["id"],), commit=True)
else:
    print("  (跳过) 当前无满足条件的拍照项，单独构造验证")
    # 构造：建 draft 计划->提交->批准->取项改 required_photos
    s, jc = req("POST", "/api/inspection-v2/plans/manual",
                 {"plan_name": "A4_test", "site_ids": [1], "assignee": "admin", "assignee_id": 1})
    pid = jc["id"]
    req("POST", f"/api/inspection-v2/plans/{pid}/submit")
    req("POST", f"/api/inspection-v2/plans/{pid}/approve", {"action": "approve", "approver_id": 1})
    iid = db("SELECT id FROM insp_plan_items WHERE plan_id=? LIMIT 1", (pid,))[0]["id"]
    db("UPDATE insp_plan_items SET required_photos=2, actual_photos=0 WHERE id=?", (iid,), commit=True)
    s7, b7 = req("PUT", f"/api/inspection-v2/plans/{pid}/items/{iid}", {"result": "normal"})
    check("拍照不足判正常被拦截(400)", s7 == 400, f"status={s7}")
    s8, _ = req("PUT", f"/api/inspection-v2/plans/{pid}/items/{iid}", {"result": "normal", "actual_photos": 2})
    check("拍照满足后可判正常", s8 == 200, f"status={s8}")
    req("DELETE", f"/api/inspection-v2/plans/{pid}")

print("\n[A5] 驳回必填原因")
sub = db("SELECT id FROM insp_plans WHERE status='submitted' LIMIT 1")
if not sub:
    # 构造一个 submitted
    s, jc = req("POST", "/api/inspection-v2/plans/manual",
                 {"plan_name": "A5_test", "site_ids": [1], "assignee": "admin", "assignee_id": 1})
    sp = jc["id"]
    req("POST", f"/api/inspection-v2/plans/{sp}/submit")
    sub = [{"id": sp}]
sid = sub[0]["id"]
s9, b9 = req("POST", f"/api/inspection-v2/plans/{sid}/approve", {"action": "reject", "approver_id": 1})
check("驳回无原因被拦截(400)", s9 == 400, f"status={s9} msg={b9}")
s10, _ = req("POST", f"/api/inspection-v2/plans/{sid}/approve",
              {"action": "reject", "approver_id": 1, "reason": "站点选择不全，请补充"})
r = db("SELECT status, reject_reason FROM insp_plans WHERE id=?", (sid,))[0]
check("驳回有原因成功且记录原因", s10 == 200 and r["status"] == "draft" and (r["reject_reason"] or "").strip() == "站点选择不全，请补充",
      f"status={s10} db={dict(r)}")
# 清理构造的计划（若还在）
db("DELETE FROM insp_plan_items WHERE plan_id=?", (sid,), commit=True)
db("DELETE FROM insp_plans WHERE id=?", (sid,), commit=True)

print(f"\n=== 结果: PASS={len(PASS)} FAIL={len(FAIL)} ===")
if FAIL:
    print("失败项:", FAIL)
