import sqlite3, os, urllib.request, json

BASE = "http://localhost:5000"
os.makedirs("backend/uploads", exist_ok=True)

def post_multipart(url, fields, file_bytes, fname):
    boundary = "----webkitformboundarye2e"
    parts = []
    for k, v in fields.items():
        parts.append(f"--{boundary}\r\n".encode())
        parts.append(f'Content-Disposition: form-data; name="{k}"\r\n\r\n'.encode())
        parts.append(f"{v}\r\n".encode())
    parts.append(f"--{boundary}\r\n".encode())
    parts.append(f'Content-Disposition: form-data; name="file"; filename="{fname}"\r\n'.encode())
    parts.append(b"Content-Type: image/png\r\n\r\n")
    parts.append(file_bytes)
    parts.append(b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    body = b"".join(parts)
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())

def main():
    c = sqlite3.connect("backend/data/water.db"); c.row_factory = sqlite3.Row
    # 幂等：先清掉历史 E2E 测试行
    c.execute("DELETE FROM operation_attachments WHERE description='E2E归档测试唯一标识'")
    c.commit()
    site = c.execute("SELECT id FROM sites LIMIT 1").fetchone()
    c.close()
    site_id = site["id"]
    png = bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
        "890000000a49444154789c6360000002000154a24f6d0000000049454e44ae426082")
    fields = {
        "site_id": str(site_id),
        "source_type": "manual_upload",
        "uploader_id": "1",
        "uploader_name": "E2E测试",
        "description": "E2E归档测试唯一标识",
    }
    resp = post_multipart(BASE + "/api/upload/attachment", fields, png, "arc_e2e.png")
    aid = resp.get("id")
    assert aid, "上传失败"
    print(f"已创建测试附件 aid={aid} (description=E2E归档测试唯一标识)")
    with open("e2e_archive_aid.txt", "w") as f:
        f.write(str(aid))

if __name__ == "__main__":
    main()
