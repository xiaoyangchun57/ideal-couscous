import json, urllib.request, urllib.error

BASE = "http://localhost:5000"

def call(method, path, body=None):
    url = BASE + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode() or "{}")
        except Exception:
            return e.code, {}
    except Exception as e:
        return -1, {"error": str(e)}

passed = []; failed = []
def check(name, cond, detail=""):
    (passed if cond else failed).append(name)
    print(f"[{'PASS' if cond else 'FAIL'}] {name} {detail}")

# 0. 基线：当前 pending 含 2 个 parts_request，0 个 vehicle
st, stats = call("GET", "/api/audit/stats")
print("stats:", stats)
check("stats.parts_pending==2", stats.get("parts_pending") == 2, f"-> {stats.get('parts_pending')}")
check("stats.vehicle_pending==0", stats.get("vehicle_pending") == 0, f"-> {stats.get('vehicle_pending')}")

pd, items = call("GET", "/api/audit/pending")
prs = [i for i in items if i["source_type"] == "parts_request"]
vas = [i for i in items if i["source_type"] == "vehicle_application"]
check("pending 含 2 个 parts_request", len(prs) == 2, f"-> {len(prs)}")
check("pending 含 0 个 vehicle", len(vas) == 0, f"-> {len(vas)}")
check("parts_request 站点名已取(经plan→items→sites)",
       any((i.get("site_name")) for i in prs), f"-> {[i.get('site_name') for i in prs]}")
check("parts_request 带备件明细",
       any(i.get("parts_detail") for i in prs), f"-> {[i.get('parts_detail') for i in prs]}")

# 1. 审批通过 pr_1
st, r = call("PUT", "/api/inspection-v2/parts-request/1/approve", {"comment": "ok", "approver_id": 1})
check("approve pr_1 -> 200", st == 200, f"-> {st} {r}")
pd, items = call("GET", "/api/audit/pending")
prs = [i for i in items if i["source_type"] == "parts_request"]
check("approve 后 pr_1 移出待办", "pr_1" not in [i["id"] for i in prs], f"-> {[i['id'] for i in prs]}")
check("approve 后仅余 pr_2", [i["id"] for i in prs] == ["pr_2"], f"-> {[i['id'] for i in prs]}")

# 2. 驳回 pr_2：无原因应 400
st, r = call("PUT", "/api/inspection-v2/parts-request/2/reject", {})
check("reject pr_2 无原因 -> 400", st == 400, f"-> {st} {r}")
# 带原因应 200
st, r = call("PUT", "/api/inspection-v2/parts-request/2/reject", {"comment": "规格不符", "approver_id": 1})
check("reject pr_2 带原因 -> 200", st == 200, f"-> {st} {r}")
pd, items = call("GET", "/api/audit/pending")
prs = [i for i in items if i["source_type"] == "parts_request"]
check("reject 后 parts_request 全部移出", len(prs) == 0, f"-> {len(prs)}")
st, stats = call("GET", "/api/audit/stats")
check("stats.parts_pending==0", stats.get("parts_pending") == 0, f"-> {stats.get('parts_pending')}")

# 3. 新建用车申请(待审) → 出现在 audit
st, va = call("POST", "/api/vehicle/applications", {
    "vehicle_id": 3, "applicant_id": 1,
    "start_at": "2026-07-20 08:00:00", "end_at": "2026-07-20 17:00:00",
    "destination": "测试站点", "reason": "巡检用车"})
check("新建用车申请 -> 201", st == 201, f"-> {st} {va}")
va_id = va.get("id")
pd, items = call("GET", "/api/audit/pending")
vas = [i for i in items if i["source_type"] == "vehicle_application"]
check("用车申请出现在 audit", any(i["id"] == f"va_{va_id}" for i in vas), f"-> {[i['id'] for i in vas]}")
st, stats = call("GET", "/api/audit/stats")
check("stats.vehicle_pending==1", stats.get("vehicle_pending") == 1, f"-> {stats.get('vehicle_pending')}")

# 4. 审批通过用车申请
st, r = call("POST", f"/api/vehicle/applications/{va_id}/approve",
              {"action": "approve", "approver_id": 1})
check("approve 用车申请 -> 200", st == 200, f"-> {st} {r}")
pd, items = call("GET", "/api/audit/pending")
vas = [i for i in items if i["source_type"] == "vehicle_application"]
check("用车申请审批后移出待办", len(vas) == 0, f"-> {len(vas)}")

print(f"\n==== 结果：{len(passed)} PASS / {len(failed)} FAIL ====")
if failed:
    print("FAILED:", failed)
