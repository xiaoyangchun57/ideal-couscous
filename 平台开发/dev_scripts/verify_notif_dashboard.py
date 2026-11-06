# -*- coding: utf-8 -*-
"""验证：态势看板字段 + 驳回active写通知 + 通知API"""
import urllib.request, json, sqlite3

BASE = 'http://localhost:5000'
DB = 'E:/杂七杂八/水质运维/平台开发/backend/data/water.db'

def req(method, path, data=None, token=None):
    url = BASE + path
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    body = json.dumps(data).encode() if data is not None else None
    r = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        resp = urllib.request.urlopen(r, timeout=10)
        return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read())
        except Exception:
            return e.code, {}

# 1. login
s, admin = req('POST', '/api/auth/login', {'username': 'admin', 'password': 'admin123'})
assert s == 200, f"login fail {s} {admin}"
tok = admin['token']
print(f"[1] login admin: {s}")

# 2. dashboard fields
s, d = req('GET', '/api/inspection-v2/dashboard', token=tok)
assert s == 200
has_assignee = False
for grp in ('vehicle_conflicts', 'site_overlaps'):
    for item in d.get(grp, []):
        for p in item.get('plans', []):
            if 'assignee' in p and 'assignee_id' in p:
                has_assignee = True
print(f"[2] dashboard plans 含 assignee/assignee_id 字段: {has_assignee}")
assert has_assignee, "dashboard plans 缺少负责人字段"

# 3. find active plan (prefer from conflicts)
target = None
for grp in ('vehicle_conflicts', 'site_overlaps'):
    for item in d.get(grp, []):
        for p in item.get('plans', []):
            if p.get('status') == 'active':
                target = p
                break
        if target:
            break
    if target:
        break

if not target:
    s, pls = req('GET', '/api/inspection-v2/plans', token=tok)
    for p in pls:
        if p.get('status') == 'active':
            target = p
            break

assert target, "no active plan found"
pid = target['id']
print(f"[3] 选 active 计划 #{pid} (负责人id={target.get('assignee_id')}) 进行驳回")

# 取负责人用户名
s, users = req('GET', '/api/users', token=tok)
assignee_user = next((u for u in users if u['id'] == target.get('assignee_id')), None)
print(f"    负责人: {assignee_user['username'] if assignee_user else '未知'}")

# 4. reject (POST /approve)
s, rj = req('POST', f'/api/inspection-v2/plans/{pid}/approve',
             {'action': 'reject', 'reason': '验证-车辆调度冲突请调整', 'approver_id': 1}, tok)
print(f"[4] reject active 计划 #{pid}: {s} -> {rj}")
assert s == 200, f"reject fail {s}"

# 5. plan -> draft
s, pls2 = req('GET', '/api/inspection-v2/plans', token=tok)
now = next((p for p in pls2 if p['id'] == pid), None)
print(f"[5] 计划 #{pid} 状态: {now['status'] if now else '已删'} (期望 draft)")
assert now and now['status'] == 'draft', "计划未退回 draft"

# 6. notification written (查DB)
db = sqlite3.connect(DB)
db.row_factory = sqlite3.Row
row = db.execute(
    "SELECT * FROM notifications WHERE source_type='inspection' AND source_id=? ORDER BY id DESC LIMIT 1",
    (pid,)).fetchone()
print(f"[6] 通知写入: {dict(row) if row else None}")
assert row and '被驳回' in (row['title'] or ''), "未写入驳回通知"
db.close()

# 7. dashboard no longer shows it
s, d2 = req('GET', '/api/inspection-v2/dashboard', token=tok)
still = False
for grp in ('vehicle_conflicts', 'site_overlaps'):
    for item in d2.get(grp, []):
        if any(p['id'] == pid for p in item.get('plans', [])):
            still = True
print(f"[7] 计划已从看板冲突列表移除: {not still}")
assert not still, "rejected plan still in dashboard"

# 8. empty reason -> 400 (use a submitted plan)
s, pls3 = req('GET', '/api/inspection-v2/plans', token=tok)
sub = next((p for p in pls3 if p['status'] == 'submitted'), None)
if sub:
    s2, rj2 = req('POST', f'/api/inspection-v2/plans/{sub['id']}/approve',
                  {'action': 'reject', 'reason': '', 'approver_id': 1}, tok)
    print(f"[8] 无原因驳回: {s2} (期望 400)")
    assert s2 == 400, "无原因应拦截"

# 9. restore
db = sqlite3.connect(DB)
db.execute("UPDATE insp_plans SET status='active' WHERE id=?", (pid,))
db.execute("DELETE FROM notifications WHERE source_type='inspection' AND source_id=?", (pid,))
db.commit()
db.close()
print(f"[9] 已恢复计划 #{pid} -> active，并清理测试通知")

print("\n=== ALL PASS ===")
