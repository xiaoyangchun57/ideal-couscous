import urllib.request, json, urllib.error

BASE = 'http://localhost:5000'

def req(method, path, body=None, token=None):
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        resp = urllib.request.urlopen(r, timeout=15)
        return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except:
            return e.code, {'raw': e.read().decode()[:200]}
    except Exception as e:
        return -1, {'error': str(e)}

def login(uname, pwd):
    s, d = req('POST', '/api/auth/login', {'username': uname, 'password': pwd})
    assert s == 200, f"login {uname} fail {s} {d}"
    tok = d.get('token') or d.get('access_token') or (d.get('data') or {}).get('token')
    assert tok, f"no token in {d}"
    return tok, d

passed = 0; failed = 0
def check(name, cond, extra=''):
    global passed, failed
    if cond:
        passed += 1; print(f"[PASS] {name} {extra}")
    else:
        failed += 1; print(f"[FAIL] {name} {extra}")

# 1. 登录 admin
tok, _ = login('admin', 'admin123')
print(f"[1] login admin: OK")

# 2. smart-preview
s, d = req('POST', '/api/inspection-v2/plans/smart-preview', {'remind_days': 30}, tok)
check('smart-preview 返回成功', s == 200 and d.get('success'), str(s))
due = d.get('due_sites', [])
avail = d.get('available_vehicles', [])
suggested = d.get('suggested', [])
check('smart-preview 有待巡检站点', len(due) > 0, f"due_sites={len(due)}")
check('smart-preview 有可用车辆候选', len(avail) > 0, f"avail={len(avail)}")
check('smart-preview 有预打包建议', len(suggested) > 0, f"suggested={len(suggested)}")
check('smart-preview 站点含检查项', all(len(x.get('schedules', [])) > 0 for x in due[:3]), '')
check('smart-preview 标记逾期字段', 'overdue' in (due[0] if due else {}), '')
check('smart-preview 车辆为idle且不含占用', all(v.get('status') == 'idle' for v in avail), '')

# 3. 可用车辆应排除被 active 计划占用的车辆
import sqlite3
db = sqlite3.connect('E:/杂七杂八/水质运维/平台开发/backend/data/water.db'); db.row_factory = sqlite3.Row
occ = [r['vehicle_id'] for r in db.execute("SELECT vehicle_id FROM insp_plans WHERE vehicle_id IS NOT NULL AND status IN ('active','submitted')").fetchall()]
occ = [o for o in occ if o]
avail_ids = [v['id'] for v in avail]
check('可用车辆不含active计划占用车', len(set(occ) & set(avail_ids)) == 0, f"occ={occ} avail={avail_ids}")

# 4. confirm 生成草稿
if due and suggested:
    # 取第一个站点的第一个检查项
    first_site = due[0]
    sid = first_site['site_id']
    first_sch = first_site['schedules'][0]
    payload = {
        'plan_name': f'验证智能生成-{first_site["site_name"]}',
        'assignee': 'admin',
        'assignee_id': 1,
        'period': suggested[0]['period'],
        'vehicle_id': avail[0]['id'] if avail else None,
        'site_items': [{
            'site_id': sid,
            'items': [{'schedule_id': first_sch['schedule_id'], 'item_name': first_sch['item_name'], 'category': first_sch['category'], 'frequency': first_sch['frequency']}]
        }]
    }
    s2, d2 = req('POST', '/api/inspection-v2/plans/confirm', payload, tok)
    check('confirm 生成草稿成功', s2 == 200 and d2.get('success'), str(s2))
    new_pid = d2.get('plan_id')
    check('confirm 返回 plan_id', new_pid is not None, f"pid={new_pid}")
    # 验证数据库确有草稿+item
    p = db.execute("SELECT id,status,vehicle_id FROM insp_plans WHERE id=?", (new_pid,)).fetchone()
    items = db.execute("SELECT COUNT(*) c FROM insp_plan_items WHERE plan_id=?", (new_pid,)).fetchone()['c']
    check('confirm 落库为draft', p and p['status'] == 'draft', f"status={p['status'] if p else None}")
    check('confirm 检查项落库', items == 1, f"items={items}")
    check('confirm 车辆写入', p and p['vehicle_id'] == (avail[0]['id'] if avail else None), '')
else:
    new_pid = None
    print("[SKIP] 无待巡检站点，跳过 confirm")

# 5. 收藏：新增（用 new_pid 或任意已有计划）
fav_plan_id = new_pid if new_pid else (due[0]['site_id'] if False else None)
if not fav_plan_id:
    any_plan = db.execute("SELECT id FROM insp_plans LIMIT 1").fetchone()
    fav_plan_id = any_plan['id'] if any_plan else None
s3, d3 = req('POST', '/api/inspection-v2/favorites', {'plan_id': fav_plan_id, 'name': '验证收藏'}, tok)
check('收藏新增成功', s3 == 200 and d3.get('success'), str(s3))
fid = d3.get('favorite_id')

# 6. 收藏列表
s4, d4 = req('GET', '/api/inspection-v2/favorites', None, tok)
check('收藏列表返回', s4 == 200 and isinstance(d4, list), f"count={len(d4) if isinstance(d4,list) else d4}")
check('收藏列表含刚加的', fid is not None and any(x['id'] == fid for x in d4), f"fid={fid}")

# 7. 从收藏复用 → 生成新草稿
s5, d5 = req('POST', f'/api/inspection-v2/favorites/{fid}/apply', None, tok)
check('从收藏复用成功', s5 == 200 and d5.get('success'), str(s5))
apply_pid = d5.get('plan_id')
check('复用生成新草稿', apply_pid is not None, f"apply_pid={apply_pid}")
if apply_pid:
    ap = db.execute("SELECT status FROM insp_plans WHERE id=?", (apply_pid,)).fetchone()
    check('复用计划为draft', ap and ap['status'] == 'draft', f"status={ap['status'] if ap else None}")

# 8. 删除收藏
s6, d6 = req('DELETE', f'/api/inspection-v2/favorites/{fid}', None, tok)
check('删除收藏成功', s6 == 200 and d6.get('success'), str(s6))

# 9. 空检查项 confirm 应 400
s7, d7 = req('POST', '/api/inspection-v2/plans/confirm', {'plan_name': 'x', 'site_items': []}, tok)
check('空检查项 confirm 拦截', s7 == 400, f"s7={s7}")

# 清理测试数据
if new_pid:
    db.execute("DELETE FROM insp_plan_items WHERE plan_id=?", (new_pid,))
    db.execute("DELETE FROM insp_plans WHERE id=?", (new_pid,))
if apply_pid:
    db.execute("DELETE FROM insp_plan_items WHERE plan_id=?", (apply_pid,))
    db.execute("DELETE FROM insp_plans WHERE id=?", (apply_pid,))
# 确保收藏已删
if fid:
    db.execute("DELETE FROM plan_favorites WHERE id=?", (fid,))
db.commit()
db.close()
print(f"\n=== 结果：PASS={passed} FAIL={failed} ===")
