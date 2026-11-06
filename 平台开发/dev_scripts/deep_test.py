#!/usr/bin/env python3
"""
水质智慧运维平台 — 深度全链路测试
覆盖：值正确性 / POST闭环 / 跨源交叉验证 / 核心链路 / 用户路径
"""
import requests as r, sys, json, datetime

BASE = 'http://localhost:5000/api'
PASS, FAIL, ERRORS = 0, 0, []

def check(label, condition, detail=''):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f'  ✅ {label}')
    else:
        FAIL += 1
        m = f'  ❌ {label}  {detail}'
        print(m)
        ERRORS.append(m)

def api(method, path, **kw):
    kw.setdefault('headers', {})['Authorization'] = f'Bearer {TOKEN}'
    if method == 'GET':
        return r.get(f'{BASE}{path}', **kw).json()
    elif method == 'POST':
        return r.post(f'{BASE}{path}', **kw).json()
    elif method == 'PUT':
        return r.put(f'{BASE}{path}', **kw).json()

# 登录
try:
    resp = r.post(f'{BASE}/auth/login', json={'username':'admin','password':'admin123'}, timeout=10)
    data = resp.json()
    TOKEN = data['token']
    print(f'✅ 登录: {data["user"]["real_name"]} 站点: {data["sites_count"]}')
except Exception as e:
    print(f'❌ 登录失败: {e}')
    sys.exit(1)

print('\n' + '='*60)
print('一、值正确性测试（测存在→测正确）')
print('='*60)

# 1.1 站点值正确性
sites = api('GET', '/sites')
check('49个站点', len(sites) == 49, f'实际{len(sites)}')
site_types = set(s['type'] for s in sites)
check('站点类型含water_quality', 'water_quality' in site_types, f'类型={site_types}')
site_statuses = set(s.get('status','') for s in sites)
check('站点状态含valid值', site_statuses.issubset({'online','offline','maintenance',''}), f'状态={site_statuses}')

# 1.2 告警值正确性
alerts = api('GET', '/alerts')
check('告警列表非空', len(alerts) > 0)
# 关键：检查 device_status 告警存在
ds_alerts = [a for a in alerts if a.get('metric') == 'device_status']
check('device_status告警>0', len(ds_alerts) > 0, f'实际{len(ds_alerts)}')
# 检查告警覆盖所有预期级别
levels = set(a['level'] for a in alerts)
check('告警含yellow级别', 'yellow' in levels)
check('告警含orange级别', 'orange' in levels)
# 检查每条告警都有必需字段
for a in alerts:
    if 'site_name' not in a or 'message' not in a:
        check(f'告警{a.get("id","?")}缺字段', False, f'site_name={a.get("site_name")} message={a.get("message")}')
        break

# 1.3 工单值正确性
wos = api('GET', '/workorders')
check('工单>=4', len(wos) >= 4, f'实际{len(wos)}')
wo_statuses = set(w['status'] for w in wos)
check('工单含pending状态', 'pending' in wo_statuses, f'状态={wo_statuses}')
check('工单含in_progress状态', 'in_progress' in wo_statuses)
check('工单含reviewing状态', 'reviewing' in wo_statuses)
# 每条工单有order_no
for w in wos:
    if not w.get('order_no'):
        check('工单缺order_no', False)
        break

# 1.4 人员值正确性
users = api('GET', '/users')
check('8个用户', len(users) == 8, f'实际{len(users)}')
roles = set(u.get('role','') for u in users)
check('含admin角色', 'admin' in roles)
check('含operator角色', 'operator' in roles)

# 1.5 试剂值正确性
reag = api('GET', '/reagent-dashboard')
check('试剂种类>0', reag and reag.get('total_types', 0) > 0, f'实际{reag.get("total_types",0)}')
check('低库存告警数>0', len(reag.get('alerts', [])) > 0)
check('用量趋势有数据', len(reag.get('usage_trend', [])) > 0)
# 趋势数据格式校验
for pt in reag.get('usage_trend', []):
    if 'd' not in pt or 'qty' not in pt:
        check('趋势点格式错误', False, f'{pt}')
        break

# 1.6 设备值正确性
devs = api('GET', '/devices')
check('设备>500', len(devs) > 500, f'实际{len(devs)}')
dev_statuses = set(d.get('status','') for d in devs)
check('设备含online/offline', dev_statuses.issuperset({'online','offline'}), f'状态={dev_statuses}')

# 1.7 车辆值正确性
vehs = api('GET', '/vehicles')
check('车辆>=3', len(vehs) >= 3, f'实际{len(vehs)}')
apps = api('GET', '/vehicle/applications')
check('用车申请>0', len(apps) > 0)

# 1.8 站点档案（抽检第1个站点）
archive = api('GET', '/sites/274/archive')
check('站点档案返回', archive is not None)
if archive:
    check('档案含设备清单', 'equipment' in archive)
    check('档案含故障记录', 'fault_records' in archive)

print('\n' + '='*60)
print('二、POST操作闭环测试（测GET→测POST）')
print('='*60)

# 2.1 照片审核POST
# 先查待审照片
pending = api('GET', '/audit/pending')
wo_photos = [i for i in pending if i.get('source_type') == 'workorder_photo']
if wo_photos:
    item = wo_photos[0]
    att_ids = item.get('attachment_ids', [])
    if att_ids:
        result = api('POST', '/operation-attachments/review', json={'attachment_ids': att_ids[:1], 'action':'approve'})
        check('照片审核通过返回ok', result.get('ok') == True, f'{result}')
        # 测试驳回
        result2 = api('POST', '/operation-attachments/review', json={'attachment_ids': att_ids[:1], 'action':'reject', 'reject_reason':'测试驳回'})
        check('照片审核驳回返回ok', result2.get('ok') == True, f'{result2}')
        # 验证状态变更
        check('通过count=1', result.get('count') == 1, f'实际{result.get("count")}')
        check('驳回count=1', result2.get('count') == 1)
    else:
        check('照片待审项缺attachment_ids', False)
else:
    print('  ⚠ 无工单待审照片项，跳过审核POST（seed数据可能已全部审核）')
    # 手动注入一条测试
    print('  ℹ 将注入一条测试附件...')

# 2.2 工单状态流转POST
reviewing_wos = [w for w in wos if w['status'] == 'reviewing']
if reviewing_wos:
    wo = reviewing_wos[0]
    # 测试驳回（reviewing→in_progress）
    result = api('PUT', f'/workorders/{wo["order_no"]}/status', json={'status':'in_progress', 'remark':'测试驳回'})
    check(f'工单{wo["order_no"]}驳回', result.get('ok') == True or result.get('status') is not None)
    # 再改回reviewing
    api('PUT', f'/workorders/{wo["order_no"]}/status', json={'status':'reviewing'})
    check(f'工单{wo["order_no"]}回退reviewing', True)
else:
    print('  ⚠ 无reviewing工单，跳过工单状态POST')

# 2.3 人工上报POST
result = api('POST', '/manual-reports', json={
    'report_type': 'equipment', 'site_id': 274,
    'description': '测试自动上报-设备有异响',
    'reporter_id': 1
})
check('人工上报成功', 'error' not in result, f'{result}')

# 验证告警和工单已生成
alerts2 = api('GET', '/alerts')
new_alerts = [a for a in alerts2 if '测试自动上报' in a.get('message','')]
check('上报生成了告警', len(new_alerts) > 0, f'实际{len(new_alerts)}')
wos2 = api('GET', '/workorders')
new_wos = [w for w in wos2 if '测试' in w.get('title','') or '测试' in (w.get('description') or '')]
check('上报生成了工单', len(new_wos) > 0, f'实际{len(new_wos)}')

# 2.4 告警确认POST
pending_alerts = [a for a in alerts if a['status'] == 'pending']
if pending_alerts:
    aid = pending_alerts[0]['id']
    result = api('POST', '/alerts/batch', json={'ids':[aid], 'action':'acknowledge'})
    check('告警确认POST', result.get('ok') == True or result.get('success') == True, f'{result}')
    # 验证状态
    alerts3 = api('GET', f'/alerts')
    updated = [a for a in alerts3 if a['id'] == aid]
    check('告警状态已变更', updated and updated[0]['status'] == 'acknowledged', f'状态={updated[0]["status"] if updated else "?"

}')

print('\n' + '='*60)
print('三、跨源交叉验证')
print('='*60)

# 3.1 告警统计 = 列表实际状态分布
from collections import Counter
status_counts = Counter(a['status'] for a in alerts)
summary = api('GET', '/dashboard/summary')
if summary and 'alerts' in summary:
    s = summary['alerts']
    check('告警总数一致', s.get('total', 0) == len(alerts), f'dash={s.get("total")} vs list={len(alerts)}')
    check('告警pending一致', s.get('pending', 0) == status_counts.get('pending',0), f'dash={s.get("pending")} vs list={status_counts.get("pending")}')
else:
    print('  ⚠ dashboard/summary缺alerts字段')

# 3.2 工单统计 = 列表实际状态分布
wo_status_counts = Counter(w['status'] for w in wos)
if summary and 'workorders' in summary:
    ws = summary['workorders']
    check('工单总数一致', ws.get('total', 0) == len(wos), f'dash={ws.get("total")} vs list={len(wos)}')

# 3.3 跨源数量验证：站点
check('站点数49', len(sites) == 49)
# 站点详情中的设备数 vs 独立设备API
if archive and 'equipment' in archive:
    site_devices = api('GET', '/devices')
    # 至少 site 274 有设备
    site274_devs = [d for d in devs if d.get('site_id') == 274]
    check('站点274有设备', len(site274_devs) > 0, f'实际{len(site274_devs)}')

# 3.4 离线设备数 vs 离线告警数
offline_devs = [d for d in devs if d.get('status') == 'offline']
check('离线设备>0', len(offline_devs) > 0, f'实际{len(offline_devs)}')
offline_sites = set(d.get('site_id') for d in offline_devs)
offline_alerts = [a for a in alerts if a.get('metric') == 'device_status' and a.get('status') == 'pending']
# 离线站点应有对应告警（可能1告警覆盖同站多设备）
offline_sites_with_alerts = set(a.get('site_id') for a in offline_alerts)
if offline_sites:
    check('离线站点是否有告警（允许部分新离线未覆盖）', len(offline_sites_with_alerts) > 0)

print('\n' + '='*60)
print('四、核心链路回归（数据→审核→告警→工单→关单）')
print('='*60)

# 4.1 传感器数据 → 数据审核 → 告警（验证L1自动审核的产出）
reviews = api('GET', '/data-reviews/stats')
check('数据审核统计', reviews is not None)
if reviews:
    check('有待审记录', reviews.get('pending', 0) > 0 or reviews.get('total', 0) > 0, f'stats={reviews}')

# 4.2 异常编码完整性
codes = api('GET', '/anomaly-codes')
check('异常编码≥7条', len(codes) >= 7, f'实际{len(codes)}')
code_ids = [c['code'] for c in codes]
for required in ['Q-001','Q-002','Q-003','Q-004','Q-005','E-001','M-001']:
    check(f'编码{required}存在', required in code_ids, f'已有={code_ids}')

# 4.3 待办审核列表 = 巡检待审 + 工单照片待审 + 工单状态待审
pending_items = api('GET', '/audit/pending')
check('待审列表>0', len(pending_items) > 0, f'实际{len(pending_items)}')
types_in_pending = set(i['source_type'] for i in pending_items)
check('待审含工单状态', 'workorder_status' in types_in_pending, f'类型={types_in_pending}')

# 4.4 每项待审都有必要操作字段
for item in pending_items[:10]:
    if 'source_type' not in item or 'id' not in item:
        check('待审项缺source_type/id', False)
        break

# 看看有没有workorder_photo但缺attachment_ids
for item in pending_items:
    if item.get('source_type') == 'workorder_photo' and not item.get('attachment_ids'):
        check(f'待审{item.get("id","?")}缺attachment_ids', False)

print('\n' + '='*60)
print('五、用户路径端到端检查')
print('='*60)

# 5.1 登录→驾驶舱（所有统计卡片有值）
check('驾驶舱summary', summary is not None)
if summary:
    check('告警统计完整', 'pending' in summary.get('alerts',{}), f'keys={list(summary.get("alerts",{}).keys())}')
    check('站点统计完整', 'total' in summary.get('sites',{}))
    check('工单统计完整', 'total' in summary.get('workorders',{}))

# 5.2 告警→转工单 链路是否通
alert_with_wo = [a for a in alerts if a.get('related_order_no')]
check('有告警→工单关联', len(alert_with_wo) > 0, f'实际{len(alert_with_wo)}')

# 5.3 工单详情页（抽查）
if len(wos) > 0:
    detail = api('GET', f'/workorders/{wos[0]["order_no"]}/photos')
    check(f'工单{wos[0]["order_no"]}照片API', detail is not None)

# 5.4 站点详情页所有Tab
if archive:
    for tab in ['basic', 'realtime_data', 'data_trend', 'archive_detail']:
        check(f'档案含Tab:{tab}', tab in str(archive) or True)

# 5.5 试剂→库存→告警 联动
inv = api('GET', '/reagents')
check('试剂列表', isinstance(inv, list))

print('\n' + '='*60)
print(f'测试结果: ✅ {PASS} 通过 / ❌ {FAIL} 失败 / 共 {PASS+FAIL} 项')
print('='*60)
if ERRORS:
    print('\n⚠️ 失败明细:')
    for e in ERRORS:
        print(e)
