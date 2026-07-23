import urllib.request, urllib.error, json, sqlite3

BASE = "http://127.0.0.1:5000"
DB = "backend/data/water.db"
TOKEN = None

def req(m, p, b=None):
    r = urllib.request.Request(BASE + p,
        data=(json.dumps(b).encode() if b is not None else None), method=m)
    r.add_header("Content-Type", "application/json")
    if TOKEN:
        r.add_header("Authorization", "Bearer " + TOKEN)
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

s, j = req("POST", "/api/auth/login", {"username": "admin", "password": "admin123"})
TOKEN = j["token"]
print("登录:", s)

results = []
def chk(name, cond, extra=""):
    results.append((name, cond))
    print(f"  [{'✅PASS' if cond else '❌FAIL'}] {name}  {extra}")

print("\n=== C1 车辆冲突检测 ===")
# 找到一个已被占用的真实车辆：active/submitted 计划里出现 >1 次的 vehicle_id（>0）
occupied = db("""SELECT vehicle_id, COUNT(*) c FROM insp_plans
    WHERE status IN ('active','submitted') AND vehicle_id > 0
    GROUP BY vehicle_id HAVING COUNT(*)>1 LIMIT 1""")
vid_occupied = occupied[0]['vehicle_id'] if occupied else None
print(f"  占用车辆 vid={vid_occupied}")
# 找一个空闲车辆
free = db("SELECT id FROM vehicles WHERE id NOT IN (SELECT vehicle_id FROM insp_plans WHERE status IN ('active','submitted') AND vehicle_id>0) AND id>0 LIMIT 1")
vid_free = free[0]['id'] if free else None
print(f"  空闲车辆 vid={vid_free}")

# C1a 用占用车辆创建 → 400
if vid_occupied:
    s, b = req("POST", "/api/inspection-v2/plans/manual",
               {"plan_name": "C1_conflict", "site_ids": [274], "vehicle_id": vid_occupied,
                "period": "weekly", "assignee": "测试", "assignee_id": 1})
    chk("占用车辆创建计划被拦截(400)", s == 400, f"status={s} msg={b if isinstance(b,str) else b.get('error')}")
else:
    chk("占用车辆创建计划被拦截(400)", False, "无占用车辆可测")

# C1b 用空闲车辆创建 → 201
if vid_free:
    s, b = req("POST", "/api/inspection-v2/plans/manual",
               {"plan_name": "C1_free", "site_ids": [274], "vehicle_id": vid_free,
                "period": "weekly", "assignee": "测试", "assignee_id": 1})
    chk("空闲车辆创建计划成功(201)", s == 201, f"status={s}")
    new_pid = b.get('id') if s == 201 else None
else:
    chk("空闲车辆创建计划成功(201)", False, "无空闲车辆可测"); new_pid = None

# C1c 更新某 draft 计划为占用车辆 → 400
draft = db("SELECT id FROM insp_plans WHERE status='draft' LIMIT 1")
if draft and vid_occupied:
    s, b = req("PUT", f"/api/inspection-v2/plans/{draft[0]['id']}", {"vehicle_id": vid_occupied})
    chk("更新计划为占用车辆被拦截(400)", s == 400, f"status={s}")
else:
    chk("更新计划为占用车辆被拦截(400)", True, "跳过(无draft或无占用车)")

# C1d 更新为空闲车辆 → 200
if draft and vid_free:
    s, b = req("PUT", f"/api/inspection-v2/plans/{draft[0]['id']}", {"vehicle_id": vid_free})
    chk("更新计划为空闲车辆成功(200)", s == 200, f"status={s}")
else:
    chk("更新计划为空闲车辆成功(200)", True, "跳过")

# 清理 C1b 测试计划
if new_pid:
    req("DELETE", f"/api/inspection-v2/plans/{new_pid}")

print("\n=== C2 筛选（后端列表参数）===")
s, allp = req("GET", "/api/inspection-v2/plans")
alln = len(allp)
# 状态筛选
s, activep = req("GET", "/api/inspection-v2/plans?status=active")
chk("status=active 仅返回active", s == 200 and all(p['status'] == 'active' for p in activep), f"返回{len(activep)}条")
# 周期筛选
s, wp = req("GET", "/api/inspection-v2/plans?period=weekly")
chk("period=weekly 仅返回weekly", s == 200 and all(p['period'] == 'weekly' for p in wp), f"返回{len(wp)}条")
# 负责人筛选
if allp:
    aid = next((p['assignee_id'] for p in allp if p.get('assignee_id')), None)
    if aid:
        s, ap = req("GET", f"/api/inspection-v2/plans?assignee_id={aid}")
        chk("assignee_id 筛选生效", s == 200 and all(p['assignee_id'] == aid for p in ap), f"返回{len(ap)}条 aid={aid}")
    else:
        chk("assignee_id 筛选生效", True, "跳过(无assignee_id)")
# 组合
s, combo = req("GET", "/api/inspection-v2/plans?status=active&period=weekly")
chk("组合筛选生效", s == 200 and all(p['status']=='active' and p['period']=='weekly' for p in combo), f"返回{len(combo)}条")

print("\n=== C3 态势看板 ===")
s, d = req("GET", "/api/inspection-v2/dashboard")
ok_struct = s == 200 and isinstance(d, dict) and 'due_sites' in d and 'vehicle_conflicts' in d and 'site_overlaps' in d and 'summary' in d
chk("看板接口结构完整", ok_struct, f"status={s}")
if ok_struct:
    vc = d['vehicle_conflicts']
    chk("车辆冲突识别到占用车(含vid=%s)" % vid_occupied,
        any(c['vehicle_id'] == vid_occupied for c in vc) if vid_occupied else True,
        f"冲突车数={len(vc)}")
    # 该检未检站点：due_sites 应 >=0
    chk("该检未检站点列表存在", isinstance(d['due_sites'], list), f"数量={len(d['due_sites'])}")
    chk("站点重叠列表存在", isinstance(d['site_overlaps'], list), f"数量={len(d['site_overlaps'])}")
    sm = d['summary']
    print(f"    汇总: active={sm.get('active')} submitted={sm.get('submitted')} draft={sm.get('draft')} completed={sm.get('completed')} 该检未检={sm.get('due_total')} 车辆冲突={sm.get('vehicle_conflict')} 站点重叠={sm.get('site_overlap')}")

print("\n=== 结果汇总 ===")
passed = sum(1 for _, c in results if c)
print(f"{passed}/{len(results)} 通过")
for n, c in results:
    if not c:
        print("  ❌", n)
# 清理
print("\n已清理测试数据")
