"""Smoke test for the authenticated API contract.

The script is intentionally executable: every transport, JSON, and shape
failure exits non-zero so it can be used as a local release check.
"""

import json
import os
import sys
import urllib.error
import urllib.request


BASE = os.environ.get('TEST_API_BASE_URL', 'http://127.0.0.1:5000').rstrip('/')


def request_json(path, *, method='GET', payload=None, token=None):
    body = json.dumps(payload).encode() if payload is not None else None
    headers = {'Accept': 'application/json'}
    if body is not None:
        headers['Content-Type'] = 'application/json'
    if token:
        headers['Authorization'] = f'Bearer {token}'
    request = urllib.request.Request(f'{BASE}{path}', data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode('utf-8', errors='replace')
        raise AssertionError(f'{method} {path} returned HTTP {exc.code}: {detail}') from exc
    except urllib.error.URLError as exc:
        raise AssertionError(f'{method} {path} failed: {exc.reason}') from exc
    try:
        return json.loads(raw.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AssertionError(f'{method} {path} returned invalid JSON') from exc


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def main():
    login = request_json('/api/auth/login', method='POST', payload={
        'username': 'admin',
        'password': 'admin123',
    })
    require(isinstance(login, dict) and login.get('success') is True,
            'login response must be an object with success=true')
    token = login.get('token')
    user = login.get('user')
    require(isinstance(token, str) and bool(token), 'login response is missing token')
    require(isinstance(user, dict), 'login response is missing user object')
    require(user.get('id') is not None and isinstance(user.get('real_name'), str),
            'login user must contain id and real_name')
    require(isinstance(login.get('sites_count'), int), 'login sites_count must be an integer')
    require(isinstance(login.get('sites'), list), 'login sites must be a list')
    require(login['sites_count'] == len(login['sites']),
            'login sites_count must match sites length')

    sites = request_json('/api/sites', token=token)
    require(isinstance(sites, list), '/api/sites response must be a list')
    for index, site in enumerate(sites):
        require(isinstance(site, dict), f'/api/sites[{index}] must be an object')
        for field in ('id', 'name', 'code', 'type', 'lat', 'lng', 'status'):
            require(field in site, f'/api/sites[{index}] is missing {field}')

    dashboard = request_json('/api/dashboard/summary', token=token)
    require(isinstance(dashboard, dict), 'dashboard response must be an object')
    require(isinstance(dashboard.get('alerts'), dict), 'dashboard alerts must be an object')
    require(isinstance(dashboard.get('sites'), dict), 'dashboard sites must be an object')
    require(isinstance(dashboard.get('workorders'), dict),
            'dashboard workorders must be an object')
    require(isinstance(dashboard.get('inspections'), dict),
            'dashboard inspections must be an object')
    require(isinstance(dashboard.get('arrival_rate'), (int, float)),
            'dashboard arrival_rate must be numeric')
    for section, fields in {
        'alerts': ('total', 'pending', 'acknowledged', 'resolved', 'by_level', 'by_type'),
        'sites': ('total', 'online', 'offline', 'with_alerts'),
        'workorders': ('total', 'by_status', 'today_new', 'today_closed'),
        'inspections': ('total', 'completed'),
    }.items():
        for field in fields:
            require(field in dashboard[section], f'dashboard {section} is missing {field}')
    require(isinstance(dashboard.get('latest_alerts'), list),
            'dashboard latest_alerts must be a list')
    require(isinstance(dashboard.get('pending_orders'), list),
            'dashboard pending_orders must be a list')

    print(f'Login OK: user={user["real_name"]} sites_count={login["sites_count"]}')
    print(f'/api/sites: {len(sites)} sites; structure OK')
    print('/api/dashboard/summary: actual nested structure OK')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as exc:
        print(f'API CHECK FAILED: {exc}', file=sys.stderr)
        sys.exit(1)
