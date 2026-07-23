"""测试设备离线告警生成"""
import requests as r

# 登录
resp = r.post('http://localhost:5000/api/auth/login', json={'username':'admin','password':'admin123'})
token = resp.json()['token']

# 查看现有告警
resp2 = r.get('http://localhost:5000/api/alerts', headers={'Authorization': f'Bearer {token}'})
alerts = resp2.json() if isinstance(resp2.json(), list) else []
device_offline = [a for a in alerts if a.get('metric') in ('device_status', 'data_gap')]
print(f'当前 device_status/data_gap 告警: {len(device_offline)}')
for a in device_offline[:5]:
    print(f'  id={a["id"]} site={a.get("site_name",a.get("site_id","?"))} level={a["level"]} status={a["status"]}')

# 查看设备离线统计
resp3 = r.get('http://localhost:5000/api/devices', headers={'Authorization': f'Bearer {token}'})
if isinstance(resp3.json(), list):
    devs = resp3.json()
    offline = [d for d in devs if d.get('status') == 'offline']
    online = [d for d in devs if d.get('status') == 'online']
    maint = [d for d in devs if d.get('status') == 'maintenance']
    print(f'\n设备状态: 在线={len(online)} 离线={len(offline)} 维护={len(maint)} 总计={len(devs)}')

print('\n注: 定时任务每5分钟运行一次，下次调度时会生成新的离线告警')
print('首次启动后5分钟内能看到新增 device_status 告警')
