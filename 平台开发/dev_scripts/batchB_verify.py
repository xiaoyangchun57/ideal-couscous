import urllib.request, urllib.error, json, sqlite3, datetime
BASE="http://127.0.0.1:5000"; DB="backend/data/water.db"; TOKEN=None
def req(m,p,b=None,tok=None):
    r=urllib.request.Request(BASE+p,data=(json.dumps(b).encode() if b is not None else None),method=m)
    r.add_header("Content-Type","application/json")
    if tok or TOKEN: r.add_header("Authorization","Bearer "+(tok or TOKEN))
    try:
        x=urllib.request.urlopen(r,timeout=10); return x.status,(json.loads(x.read().decode()) if x.read else {})
    except urllib.error.HTTPError as e: return e.code, e.read().decode()[:300]
    except Exception as e: return "ERR", repr(e)
def db(q,p=()):
    c=sqlite3.connect(DB); c.row_factory=sqlite3.Row
    r=[dict(x) for x in c.execute(q,p).fetchall()]; c.close(); return r
def line(t): print(t)
fails=[]; passes=[]
def chk(name,cond,extra=""):
    (passes if cond else fails).append(name)
    line(("✅ PASS " if cond else "❌ FAIL ")+name+("  "+extra if extra else ""))

# login
s,j=req("POST","/api/auth/login",{"username":"admin","password":"admin123"})
TOKEN=j.get("token"); chk("登录获取token", bool(TOKEN))

# 找一个活跃排程站点（用于 manual 创建，确保有 items）
sws=db("SELECT site_id FROM inspection_schedules WHERE status='active' LIMIT 1")
SITE=sws[0]['site_id'] if sws else 1
line(f"   测试站点 site_id={SITE}")

# ===== B1: generate 生成计划应为 draft =====
line("\n=== B1: generate 改为 draft 待审 ===")
# 强制一个排程到期，确保 generate 会产出
db("UPDATE inspection_schedules SET next_due_date=date('now') WHERE id=(SELECT id FROM inspection_schedules WHERE status='active' LIMIT 1)")
before=db("SELECT MAX(id) mx FROM insp_plans")[0]['mx'] or 0
s,j=req("POST","/api/inspection-v2/plans/generate",{"remind_days":1})
line(f"   generate 返回: {s} {j}")
newplans=db("SELECT id,status,plan_name FROM insp_plans WHERE id>? AND generate_date=date('now') ORDER BY id DESC",(before,))
line(f"   本次 generate 新建计划: {[(p['id'],p['status']) for p in newplans]}")
chk("B1 generate 新建计划状态为 draft", all(p['status']=='draft' for p in newplans) and len(newplans)>0,
    "" if not newplans else f"状态={[p['status'] for p in newplans]}")

# ===== B2: 去掉自动完成 + 完成执行校验完成度 =====
line("\n=== B2: 自动完成已移除 + 完成执行硬校验 ===")
s,j=req("POST","/api/inspection-v2/plans/manual",{"plan_name":"B2测试","site_ids":[SITE],"vehicle_id":1,"period":"weekly","assignee":"admin","assignee_id":1})
PID=j.get("id")
chk("B2 手动创建计划(draft)", s==200 and PID, f"pid={PID}")
s,j=req("POST",f"/api/inspection-v2/plans/{PID}/submit"); chk("B2 提交→submitted", j.get("status")=="submitted", str(j))
s,j=req("POST",f"/api/inspection-v2/plans/{PID}/approve",{"action":"approve","approver_id":1}); chk("B2 批准→active", j.get("status")=="active", str(j))
d=req("GET",f"/api/inspection-v2/plans/{PID}")[1]; items=d["items"]; n=len(items)
line(f"   计划 {PID} 共 {n} 个检查项")
# 执行前 n-1 项
for it in items[:-1]:
    req("PUT",f"/api/inspection-v2/plans/{PID}/items/{it['id']}",{"result":"normal"})
st=db("SELECT status FROM insp_plans WHERE id=?",(PID,))[0]['status']
chk("B2 提交末项前计划仍是 active（未自动完成）", st=="active", f"status={st}")
# 尝试完成（应 400）
s,j=req("POST",f"/api/inspection-v2/plans/{PID}/complete")
chk("B2 未全部执行时点完成执行→400拦截", s==400, f"{s} {j}")
# 执行最后一项
req("PUT",f"/api/inspection-v2/plans/{PID}/items/{items[-1]['id']}",{"result":"normal"})
s,j=req("POST",f"/api/inspection-v2/plans/{PID}/complete")
chk("B2 全部执行后完成执行→200 completed", s==200 and j.get("status")=="completed", f"{s} {j}")

# ===== B3: 删除按钮全状态可用（后端支持任意状态删除）=====
line("\n=== B3: 删除全状态可用 ===")
s,j=req("DELETE",f"/api/inspection-v2/plans/{PID}"); chk("B3 删除已完成计划成功", s==200, f"{s}")
# draft 状态删除
s,j=req("POST","/api/inspection-v2/plans/manual",{"plan_name":"B3draft","site_ids":[SITE],"vehicle_id":1,"period":"weekly","assignee":"admin","assignee_id":1})
PID2=j.get("id")
s,j=req("DELETE",f"/api/inspection-v2/plans/{PID2}"); chk("B3 删除 draft 计划成功", s==200, f"{s}")
# active 状态删除（模拟自动生成后想删）
s,j=req("POST","/api/inspection-v2/plans/manual",{"plan_name":"B3active","site_ids":[SITE],"vehicle_id":1,"period":"weekly","assignee":"admin","assignee_id":1})
PID3=j.get("id"); req("POST",f"/api/inspection-v2/plans/{PID3}/submit"); req("POST",f"/api/inspection-v2/plans/{PID3}/approve",{"action":"approve","approver_id":1})
s,j=req("DELETE",f"/api/inspection-v2/plans/{PID3}"); chk("B3 删除 active 计划成功(解决自动生成不能删)", s==200, f"{s}")

# ===== B4: 逐项审核已接入 /audit =====
line("\n=== B4: 逐项审核接入 /audit 待办 ===")
s,j=req("GET","/api/audit/pending")
items_pending=j if isinstance(j,list) else []
insp=j=[x for x in items_pending if x.get("source_type")=="inspection"]
line(f"   /audit/pending 共 {len(items_pending)} 项, 其中巡检质控 {len(insp)} 项")
chk("B4 /audit/pending 返回巡检质控待审项", len(insp)>0, f"数量={len(insp)}")
if insp:
    rid=insp[0]["id"].replace("insp_","")
    # 取一个 need_review 的项复核
    before_rev=db("SELECT review_status FROM insp_plan_items WHERE id=?",(rid,))[0]["review_status"]
    s,j=req("PUT",f"/api/inspection-v2/items/{rid}/review",{"action":"approve","comment":"测试通过"})
    after_rev=db("SELECT review_status FROM insp_plan_items WHERE id=?",(rid,))[0]["review_status"]
    chk("B4 审核动作更新 review_status(1→2)", before_rev==1 and after_rev==2, f"{before_rev}->{after_rev} api={s}")
    # 还原
    db("UPDATE insp_plan_items SET review_status=1 WHERE id=?",(rid,))

# 汇总
line("\n"+"="*50)
line(f"通过 {len(passes)} 项, 失败 {len(fails)} 项")
if fails:
    line("失败项: "+", ".join(fails))
else:
    line("🎉 全部通过")
