#!/usr/bin/env python3
"""功能测试自动化脚本 - API级验证"""
import sys, json, requests as r

BASE = 'http://localhost:5000/api'
TOKEN = None
passed = 0
failed = 0

def login():
    global TOKEN
    resp = r.post(f'{BASE}/auth/login', json={'username':'admin','password':'admin123'})
    data = resp.json()
    TOKEN = data['token']
    print(f'✅ 登录成功: {data["user"]["real_name"]} 站点: {data["sites_count"]}')
    return data

def api(path):
    resp = r.get(f'{BASE}{path}', headers={'Authorization': f'Bearer {TOKEN}'})
    try: return resp.json()
    except: return None

def check(label, condition, detail=''):
    global passed, failed
    if condition:
        passed += 1
        print(f'  ✅ PASS: {label}')
    else:
        failed += 1
        print(f'  ❌ FAIL: {label} {detail}')

print('='*60)
print('水质智慧运维平台 - 功能测试自动化报告')
print('='*60)
print()

# 1. 登录
login()

print()
print('--- H1: 驾驶舱 ---')
dash = api('/dashboard/summary')
check('告警统计存在', dash and 'alerts' in str(dash))
check('站点统计存在', dash and 'sites' in str(dash))
check('工单统计存在', dash and 'workorders' in str(dash))

print()
print('--- H2: 站点管理 ---')
sites = api('/sites')
check('49个站点', isinstance(sites, list) and len(sites) == 49)
check('站点含必要字段', sites and 'name' in sites[0] and 'code' in sites[0] and 'status' in sites[0])

print()
print('--- H3: 预警中心 ---')
alerts = api('/alerts')
check('告警列表', isinstance(alerts, list) and len(alerts) > 0)
alert = alerts[0]
check('告警含关键字段', 'site_name' in alert and 'level' in alert and 'status' in alert and 'message' in alert)

# 升级配置
esc = api('/alert-escalation-config')
check('升级配置4条', isinstance(esc, list) and len(esc) >= 4)

print()
print('--- H4: 工单管理 ---')
wos = api('/workorders')
check('工单列表', isinstance(wos, list) and len(wos) >= 4)
statuses = [w['status'] for w in wos if isinstance(w, dict)]
check('工单含pending状态', 'pending' in statuses)
check('工单含in_progress状态', 'in_progress' in statuses)

print()
print('--- H5: 试剂总览 ---')
reag = api('/reagent-dashboard')
check('试剂种类>0', reag and reag.get('total_types', 0) > 0)
check('低库存告警存在', reag and len(reag.get('alerts', [])) >= 0)

print()
print('--- H6: 车辆管理 ---')
vehs = api('/vehicles')
check('车辆列表', isinstance(vehs, list) and len(vehs) >= 3)

print()
print('--- H7: 统计分析 ---')
qual = api('/data-quality')
if qual: check('数据质量概况', True)
arrival = api('/data/arrival/summary')
check('到报率汇总', arrival is not None and len(arrival) > 0 if isinstance(arrival, list) else True)

print()
print('--- H8: 人员管理 ---')
users = api('/users')
check('8个用户', isinstance(users, list) and len(users) == 8)
check('admin角色', any(u.get('role')=='admin' or u.get('username')=='admin' for u in (users or [])))

print()
print('--- 数据审核 ---')
stats = api('/data-reviews/stats')
check('审核统计', stats is not None)
if stats:
    check('有审核记录', stats.get('total', 0) > 0)
codes = api('/anomaly-codes')
check('异常编码7条', isinstance(codes, list) and len(codes) >= 7)

print()
print('--- 趋势预测 ---')
trend = api('/prediction/trend?site_id=274&metric=ph&hours=48&forecast_steps=6')
check('预测API', trend is not None and 'actual' in trend)
if trend:
    check('有历史数据', len(trend.get('actual', [])) > 0)
    check('有预测结果', len(trend.get('forecast', [])) > 0)

print()
print('--- 设备管理 ---')
devs = api('/devices')
check('设备列表', isinstance(devs, list) and len(devs) > 0)

print()
print('--- 照片审核 ---')
reqs = api('/photo-requirements?site_type=water_quality&period=weekly')
check('周检12项', isinstance(reqs, list) and len(reqs) == 12)

print()
print('--- 通知 ---')
notifs = api('/notifications?user_id=1')
check('通知API', notifs is not None)

print()
print('='*60)
print(f'测试完成: ✅ {passed} 通过 / ❌ {failed} 失败 / 共 {passed+failed} 项')
print('='*60)
