"""Debug API test - check each endpoint"""
import urllib.request, json

BASE = 'http://127.0.0.1:5000'

# Login
req = urllib.request.Request(f'{BASE}/api/auth/login',
    data=json.dumps({'username':'admin','password':'admin123'}).encode(),
    headers={'Content-Type': 'application/json'})
resp = urllib.request.urlopen(req)
r = json.loads(resp.read())
token = r['token']
print(f'1. Login: OK, sites_count={r["sites_count"]}')

# Auth me
req2 = urllib.request.Request(f'{BASE}/api/auth/me',
    headers={'Authorization': f'Bearer {token}'})
resp2 = urllib.request.urlopen(req2)
me = json.loads(resp2.read())
print(f'2. /api/auth/me: OK, user={me.get("user",{}).get("username")}')

# Sites - FULL response
req3 = urllib.request.Request(f'{BASE}/api/sites',
    headers={'Authorization': f'Bearer {token}'})
try:
    resp3 = urllib.request.urlopen(req3)
    sites = json.loads(resp3.read())
    print(f'3. /api/sites: {len(sites)} sites')
except urllib.error.HTTPError as e:
    body = e.read().decode()
    print(f'3. /api/sites: HTTP {e.code}')
    print(f'   Body (first 300 chars): {body[:300]}')

# Dashboard
req4 = urllib.request.Request(f'{BASE}/api/dashboard/summary',
    headers={'Authorization': f'Bearer {token}'})
resp4 = urllib.request.urlopen(req4)
ds = json.loads(resp4.read())
print(f'4. Dashboard: OK, alerts={len(ds.get("latest_alerts",[]))}')

# Devices
req5 = urllib.request.Request(f'{BASE}/api/devices',
    headers={'Authorization': f'Bearer {token}'})
try:
    resp5 = urllib.request.urlopen(req5)
    devs = json.loads(resp5.read())
    print(f'5. /api/devices: {len(devs)} devices')
except Exception as e:
    print(f'5. /api/devices: ERROR {e}')
