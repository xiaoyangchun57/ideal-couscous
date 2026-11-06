"""E2E：照片审核闭环（后端层）
1) 上传一张带水印文字的照片 -> 自动归类到「高锰酸盐指数仪器质控」且 review_required=1
2) /api/audit/stats.photo_pending 在待审时 >=1，审核后归零
3) /api/operation-attachments/review 通过 -> review_status=approved
4) 清理测试行（直接删库 + 删文件）
"""
import sqlite3, json, os, urllib.request, urllib.error

BASE = "http://localhost:5000"
DB = "backend/data/water.db"
UPLOAD_DIR = "backend/uploads"

# 1x1 透明 PNG
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c6360000002000154a24f5b0000000049454e44ae426082")

def post_multipart(url, fields, file_bytes, file_name="t.png"):
    boundary = "----wbtestboundary"
    body = b""
    for k, v in fields.items():
        body += f"--{boundary}\r\n".encode()
        body += f'Content-Disposition: form-data; name="{k}"\r\n\r\n'.encode()
        body += f"{v}\r\n".encode()
    body += f"--{boundary}\r\n".encode()
    body += f'Content-Disposition: form-data; name="file"; filename="{file_name}"\r\n'.encode()
    body += b"Content-Type: image/png\r\n\r\n"
    body += file_bytes + b"\r\n"
    body += f"--{boundary}--\r\n".encode()
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())

def api_get(path):
    with urllib.request.urlopen(BASE + path, timeout=15) as r:
        return json.loads(r.read().decode())

def api_post(path, payload):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(BASE + path, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())

def db():
    return sqlite3.connect(DB)

def main():
    site_id = db().execute("SELECT id FROM sites LIMIT 1").fetchone()[0]
    print(f"使用 site_id={site_id}")

    # --- 1. 上传带水印文字的照片（匹配 高锰酸盐质控）---
    fields = {
        "watermark_text": "高锰酸盐质控 站点例行",
        "source_type": "manual_upload",
        "site_id": str(site_id),
        "uploader_id": "1",
        "uploader_name": "测试运维",
        "description": "高锰酸盐指数仪器质控",
    }
    resp = post_multipart(BASE + "/api/upload/attachment", fields, PNG, "qc.png")
    assert resp.get("success"), f"上传失败: {resp}"
    aid = resp["id"]
    print(f"上传成功 aid={aid}, match={resp.get('match')}, review_required={resp.get('review_required')}")
    assert resp.get("review_required") == 1, "水印未自动判定为需审核"
    assert resp["match"]["recognized_category"] == "高锰酸盐指数仪器质控照片", \
        f"归类错误: {resp['match']}"

    # --- 2. 待审统计应含此照片 ---
    st_before = api_get("/api/audit/stats")
    print(f"审核前 stats.photo_pending={st_before.get('photo_pending')}")
    assert st_before.get("photo_pending", 0) >= 1, "待审统计未计入该照片"

    # --- 3. 待办审核列表应返回 photo_review 项 ---
    pend = api_get("/api/audit/pending")
    photo_items = [p for p in pend if p.get("source_type") == "photo_review" and str(p.get("id")) == f"photo_{aid}"]
    assert photo_items, "待办审核未聚合该照片"
    print(f"待办审核已聚合 photo_review 项: {photo_items[0]['title']}")

    # --- 4. 审核通过 ---
    rv = api_post("/api/operation-attachments/review",
                  {"attachment_ids": [aid], "action": "approve", "reviewer_id": 1})
    assert rv.get("ok"), f"审核失败: {rv}"
    print(f"审核通过: {rv}")

    # --- 5. DB 断言 ---
    row = db().execute(
        "SELECT review_status, recognized_category, requirement_id, review_required FROM operation_attachments WHERE id=?",
        (aid,)).fetchone()
    print(f"DB: review_status={row[0]}, recognized_category={row[1]}, requirement_id={row[2]}, review_required={row[3]}")
    assert row[0] == "approved", "review_status 未置为 approved"
    assert row[1] == "高锰酸盐指数仪器质控照片", "recognized_category 未存"
    assert row[2] is not None, "requirement_id 未关联"
    assert row[3] == 1, "review_required 未存"

    # --- 6. 审核后统计应归零（仅本测试行时）---
    st_after = api_get("/api/audit/stats")
    pend_after = api_get("/api/audit/pending")
    still = [p for p in pend_after if p.get("source_type") == "photo_review" and str(p.get("id")) == f"photo_{aid}"]
    print(f"审核后 stats.photo_pending={st_after.get('photo_pending')}, 列表中仍含本项={bool(still)}")
    assert not still, "审核后该照片仍出现在待办"

    # --- 7. 清理 ---
    c = db(); c.execute("DELETE FROM operation_attachments WHERE id=?", (aid,)); c.commit(); c.close()
    # 删磁盘文件
    for root, _, files in os.walk(UPLOAD_DIR):
        for f in files:
            if f.startswith(str(aid)) or f == "qc.png":
                try: os.remove(os.path.join(root, f))
                except: pass
    print("测试行已清理")

    print("\n✅ 照片审核闭环 E2E 全部通过")

if __name__ == "__main__":
    main()
