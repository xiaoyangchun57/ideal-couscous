"""Quick test for API endpoints"""
import urllib.request, json

BASE = 'http://127.0.0.1:5000'

# Login
req = urllib.request.Request(f'{BASE}/api/auth/login',
    data=json.dumps({'username':'admin','password':'admin123'}).encode(),
    headers={'Content-Type': 'application/json'})
resp = urllib.request.urlopen(req)
r = json.loads(resp.read())
token = r['token']
print(f'Login OK: sites_count={r["sites_count"]}, user={r["user"]["real_name"]}')

# Get sites
try:
    req2 = urllib.request.Request(f'{BASE}/api/sites',
        headers={'Authorization': f'Bearer {token}'})
    resp2 = urllib.request.urlopen(req2)
    sites = json.loads(resp2.read())
    print(f'/api/sites: {len(sites)} sites')
    if sites:
        s = sites[0]
        print(f'  First: {s["name"]} lat={s.get("lat")} lng={s.get("lng")}')
        nulls = sum(1 for x in sites if not x.get('lat') or not x.get('lng'))
        print(f'  Without coords: {nulls}')
except Exception as e:
    print(f'/api/sites ERROR: {e}')

# Dashboard summary
try:
    req3 = urllib.request.Request(f'{BASE}/api/dashboard/summary',
        headers={'Authorization': f'Bearer {token}'})
    resp3 = urllib.request.urlopen(req3)
    ds = json.loads(resp3.read())
    print(f'/api/dashboard/summary:')
    print(f'  sites={ds.get("site_count")} devices={ds.get("device_count")}')
    print(f'  alerts={len(ds.get("latest_alerts",[]))}')
    print(f'  work_orders={ds.get("work_orders",{}).get("total",0)}')
except Exception as e:
    print(f'/api/dashboard/summary ERROR: {e}')

print('Done')
