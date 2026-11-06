#!/usr/bin/env python
# -*- coding: utf-8 -*-
import urllib.request, urllib.error, json, re, sqlite3, sys

BASE = "http://localhost:5000"
def req(method, path, token=None, data=None):
    url = BASE + path
    headers = {"Content-Type": "application/json"}
    if token: headers["Authorization"] = "Bearer " + token
    body = json.dumps(data).encode() if data is not None else None
    r = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(r, timeout=6) as resp:
            code = resp.getcode(); txt = resp.read().decode("utf-8","replace")
    except urllib.error.HTTPError as e:
        return (e.code, None, e.read().decode("utf-8","replace"))
    except Exception as e:
        return (-1, None, str(e))
    try: return (code, None, json.loads(txt) if txt else None)
    except Exception: return (code, None, txt)

def login(u,p):
    _,_,j = req("POST","/api/auth/login",data={"username":u,"password":p})
    return j.get("token") if isinstance(j,dict) else None  # noqa

def sids(obj):
    t = json.dumps(obj,ensure_ascii=False) if not isinstance(obj,str) else obj
    return set(int(m.group(1)) for m in re.finditer(r'"(?:site_id|siteId)"\s*:\s*(\d+)', t))

db=sqlite3.connect("backend/data/water.db"); db.row_factory=sqlite3.Row
allowed=set(r["site_id"] for r in db.execute("SELECT site_id FROM user_sites WHERE user_id=3")); db.close()
op=login("liuna","yw123456"); ad=login("admin","admin123")
print(f"op={'OK' if op else 'FAL'}(sites={len(allowed)}) ad={'OK' if ad else 'FAL'}", flush=True)
assert op and ad

EP=[
 "/api/alerts","/api/workorders","/api/inspections",
 "/api/dashboard/summary","/api/sites","/api/timeline",
 "/api/inspection-v2/plans","/api/inspection-v2/dashboard",
 "/api/inspection-v2/items/pending","/api/inspection-v2/photos",
 "/api/attachments","/api/devices","/api/parts/requests",
 "/api/parts/requests/mine","/api/reagents","/api/reagent-inventory/275",
 "/api/reagent-alerts","/api/vehicles","/api/vehicle/applications",
 "/api/vehicle/use-records","/api/weekly-plans","/api/manual-reports",
 "/api/data-reviews","/api/data-reviews/stats",
 "/api/reagent-dashboard","/api/reagent-overview",
 "/api/inspection/photos/275","/api/inspection/photos/300",
]
print("=== 操作员(刘娜)越权扫描 ===", flush=True)
leaks=[]
for ep in EP:
    c,_,j=req("GET",ep,token=op)
    s=sids(j) if isinstance(j,(dict,list)) else set()
    leak=sorted(s-allowed)
    n="?"
    if isinstance(j,dict) and isinstance(j.get("items"),list): n=len(j["items"])
    elif isinstance(j,list): n=len(j)
    concl="⚠越权" if leak else ("空/无site" if not s else "✅隔离")
    print(f"{ep:<40}{str(c):<5}{str(n):<6}{str(leak):<12}{concl}", flush=True)
    if leak: leaks.append((ep,leak))

print("\n=== 匿名可访问 ===", flush=True)
anon=[]
for ep in EP:
    c,_,_=req("GET",ep)
    if c==200: anon.append(ep); print(f"  ⚠ {ep} -> {c}", flush=True)
print("\n操作员越权:", leaks if leaks else "无", flush=True)
print("匿名开放:", anon if anon else "无", flush=True)
