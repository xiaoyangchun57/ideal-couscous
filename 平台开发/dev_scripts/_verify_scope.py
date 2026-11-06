#!/usr/bin/env python3
# 权限隔离回归探针：以刘娜(liuna, 24站点)身份验证 P0/P1/P2 修复
import urllib.request, json, sqlite3

BASE = 'http://127.0.0.1:5000'

def req(path, token=None, method='GET', body=None):
    headers = {}
    if token: headers['Authorization'] = f'Bearer {token}'
    data = json.dumps(body).encode() if body is not None else None
    if data: headers['Content-Type'] = 'application/json'
    r = urllib.request.Request(f'{BASE}{path}', data=data, headers=headers, method=method)
    try:
        resp = urllib.request.urlopen(r, timeout=8)
        return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try: return e.code, json.loads(e.read().decode())
        except: return e.code, {}

db = sqlite3.connect('data/water.db'); db.row_factory = sqlite3.Row
MINE = set(r['site_id'] for r in db.execute("SELECT site_id FROM user_sites WHERE user_id=3"))
print(f"刘娜(liuna) 站点数={len(MINE)}, max={max(MINE)}")

# 登录
_, login = req('/api/auth/login', method='POST', body={'username':'liuna','password':'yw123456'})
tok = login.get('token','')
assert tok, "登录失败"
print("登录 OK, token_len=%d" % len(tok))

fails = []
def check(name, cond, extra=''):
    print(("PASS " if cond else "FAIL ") + name + (("  " + extra) if extra else ""))
    if not cond: fails.append(name)

# 1. 匿名应被拦截
for ep in ['/api/sites','/api/audit/pending','/api/alerts','/api/dashboard/summary','/api/inspection-v2/plans','/api/reagent-alerts','/api/manual-reports','/api/data/arrival/summary']:
    c,_ = req(ep)
    check(f"匿名拦截 {ep}", c==401, f"code={c}")

# 2. /api/sites 仅本人站点
c, sites = req('/api/sites', tok)
site_ids = [s['id'] for s in sites]
leak = [s for s in site_ids if s not in MINE]
check("/api/sites 仅本人站点", len(site_ids)==len(MINE) and not leak, f"返回{len(site_ids)}个, 越权{len(leak)}")

# 3. /api/sites/<他人> 应 403
other = max(MINE)+1
c,_ = req(f'/api/sites/{other}', tok)
check("/api/sites/<他人> 403", c==403, f"code={c}")

# 4. dashboard/summary latest_alerts 仅本人
c, sm = req('/api/dashboard/summary', tok)
la = sm.get('latest_alerts', [])
la_leak = [a['site_id'] for a in la if a.get('site_id') not in MINE]
po = sm.get('pending_orders', [])
po_leak = [o['site_id'] for o in po if o.get('site_id') not in MINE]
det = sm.get('alerts',{}).get('detail',[])
check("dashboard latest_alerts 仅本人", not la_leak, f"越权{len(la_leak)}")
check("dashboard pending_orders 仅本人", not po_leak, f"越权{len(po_leak)}")

# 5. inspection-v2 plans 仅本人站点
c, plans = req('/api/inspection-v2/plans', tok)
# 计划→站点通过 items 关联；检查每个计划是否含本人站点
plan_leak = []
for p in plans:
    pid = p['id']
    srow = db.execute("SELECT DISTINCT site_id FROM insp_plan_items WHERE plan_id=?", (pid,)).fetchall()
    sids = set(r['site_id'] for r in srow)
    if not (sids & MINE):
        plan_leak.append(pid)
check("inspection-v2 plans 仅本人站点", not plan_leak, f"越权计划{plan_leak[:5]}")

# 6. reagent-alerts 仅本人
c, ra = req('/api/reagent-alerts', tok)
ra_leak = [x['site_id'] for x in ra if x.get('site_id') not in MINE]
check("reagent-alerts 仅本人", not ra_leak, f"越权{len(ra_leak)}")

# 7. manual-reports 仅本人
c, mr = req('/api/manual-reports', tok)
mr_leak = [x['site_id'] for x in mr if x.get('site_id') not in MINE]
check("manual-reports 仅本人", not mr_leak, f"越权{len(mr_leak)}")

# 8. data/arrival/summary 不报错且返回
c, das = req('/api/data/arrival/summary', tok)
check("data/arrival/summary 正常", c==200 and 'by_metric' in das, f"code={c}")

# 9. 审批非本人计划 → 403（取一个不属于本人的计划）
other_plan = db.execute("SELECT p.id FROM insp_plans p WHERE p.id NOT IN (SELECT plan_id FROM insp_plan_items WHERE site_id IN (%s)) LIMIT 1" % (','.join(str(x) for x in MINE))).fetchone()
if other_plan:
    c,_ = req(f'/api/inspection-v2/plans/{other_plan["id"]}/approve', tok, method='POST', body={'action':'approve','approver_id':3})
    check("审批非本人计划 403", c==403, f"code={c}")
else:
    print("SKIP 审批归属（无外部计划）")

print("\n==== 结果 ====")
print("FAIL 数:", len(fails))
for f in fails: print("  -", f)
print("ALL PASS" if not fails else "HAS FAILURES")
