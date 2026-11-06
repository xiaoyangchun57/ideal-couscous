import urllib.request, json, sqlite3, random

BASE = 'http://127.0.0.1:5000'

def call(path, payload, method):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(BASE + path, data=data,
                               headers={'Content-Type': 'application/json'}, method=method)
    try:
        r = urllib.request.urlopen(req, timeout=10)
        return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()

def put(p, d): return call(p, d, 'PUT')
def post(p, d): return call(p, d, 'POST')

db = sqlite3.connect('backend/data/water.db')
db.row_factory = sqlite3.Row
site, metric = 274, 'ph_cltest'

# ---- 场景1 准备：审核项(L3) + 告警 + 工单(reviewing) 关联告警 ----
db.execute("INSERT INTO data_reviews (site_id,metric,value,recorded_at,status,smart_result) VALUES (?,?,?,datetime('now','localtime'),'smart_reviewed','suspicious')",
            (site, metric, 7.5))
rv = db.execute("SELECT id FROM data_reviews WHERE site_id=? AND metric=? ORDER BY id DESC LIMIT 1", (site, metric)).fetchone()['id']
db.execute("INSERT INTO alerts (site_id,metric,value,level,message,status) VALUES (?,?,?,?,?,'pending')",
            (site, metric, 7.5, 'orange', '闭环测试-异常'))
aid = db.execute("SELECT id FROM alerts WHERE site_id=? AND metric=? ORDER BY id DESC LIMIT 1", (site, metric)).fetchone()['id']
order_no = f'T{random.randint(100000,999999)}'
db.execute("INSERT INTO work_orders (order_no,site_id,source,event_type,level,title,description,status,related_alert_id) VALUES (?,?, 'auto','数据异常','urgent','闭环测试工单','x','reviewing',?)",
            (order_no, site, aid))
db.commit()
print(f'[setup] review={rv} alert={aid} wo={order_no}')

# ---- 场景1：关单带结论=误报 ----
st, body = put(f'/api/workorders/{order_no}/status', {'status': 'closed', 'conclusion': 'false_alram'})
print('[场景1] WO close ->', st, body)
a = db.execute("SELECT status,resolve_reason FROM alerts WHERE id=?", (aid,)).fetchone()
rv2 = db.execute("SELECT status,manual_result,resolved_by_order_id FROM data_reviews WHERE id=?", (rv,)).fetchone()
print('  告警:', dict(a))
print('  审核项:', dict(rv2))
ok1 = a['status'] == 'resolved' and a['resolve_reason'] == 'false_alram' and rv2['status'] == 'archived' and rv2['resolved_by_order_id'] == order_no

# ---- 场景2 准备：新审核项 + 新告警 (同 site/metric=cod) ----
metric2 = 'cod_cltest'
db.execute("INSERT INTO data_reviews (site_id,metric,value,recorded_at,status,smart_result) VALUES (?,?,?,datetime('now','localtime'),'smart_reviewed','suspicious')",
            (site, metric2, 3.2))
rv3 = db.execute("SELECT id FROM data_reviews WHERE site_id=? AND metric=? ORDER BY id DESC LIMIT 1", (site, metric2)).fetchone()['id']
db.execute("INSERT INTO alerts (site_id,metric,value,level,message,status) VALUES (?,?,?,?,?,'pending')",
            (site, metric2, 3.2, 'red', '闭环测试-紧急'))
aid2 = db.execute("SELECT id FROM alerts WHERE site_id=? AND metric=? ORDER BY id DESC LIMIT 1", (site, metric2)).fetchone()['id']
db.commit()
print(f'\n[setup2] review={rv3} alert={aid2}')

# ---- 场景2：人工审核标 误报 (conclusion) ----
st, body = post(f'/api/data-reviews/{rv3}/manual-review', {'action': 'reject', 'conclusion': 'false_alram', 'reviewer_id': 1})
print('[场景2] review manual ->', st, body)
a2 = db.execute("SELECT status,resolve_reason FROM alerts WHERE id=?", (aid2,)).fetchone()
rv4 = db.execute("SELECT status,manual_result FROM data_reviews WHERE id=?", (rv3,)).fetchone()
print('  告警:', dict(a2))
print('  审核项:', dict(rv4))
ok2 = a2['status'] == 'resolved' and a2['resolve_reason'] == 'false_alram' and rv4['status'] == 'archived' and rv4['manual_result'] == 'rejected'

# ---- 清理测试数据 ----
db.execute("DELETE FROM data_reviews WHERE metric IN (?,?)", (metric, metric2))
db.execute("DELETE FROM alerts WHERE metric IN (?,?)", (metric, metric2))
db.execute("DELETE FROM work_orders WHERE order_no=?", (order_no,))
db.commit()
print(f'\n[cleanup] 测试行已删除')

print('\n==== 结果 ====')
print('场景1（关单→告警解决+审核归档）:', 'PASS' if ok1 else 'FAIL')
print('场景2（审核误报→告警消除）:', 'PASS' if ok2 else 'FAIL')
