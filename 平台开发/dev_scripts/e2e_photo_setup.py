import sqlite3, os, urllib.request, urllib.error, json

BASE = "http://localhost:5000"
UPLOAD_DIR = "backend/uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

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
    # 取一个真实 site_id
    c = sqlite3.connect("backend/data/water.db"); c.row_factory = sqlite3.Row
    site = c.execute("SELECT id, name FROM sites LIMIT 1").fetchone()
    c.close()
    site_id = site["id"]
    # 1x1 红点 PNG
    png = bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
        "890000000a49444154789c6360000002000154a24f6d0000000049454e44ae426082")
    fields = {
        "site_id": str(site_id),
        "source_type": "manual_upload",
        "uploader_id": "1",
        "uploader_name": "E2E测试",
        "watermark_text": "高锰酸盐指数质控 2026-07-17 邓埠",
        "description": "E2E水印照片",
    }
    resp = post_multipart(BASE + "/api/upload/attachment", fields, png, "qc_e2e.png")
    print("上传响应:", resp)
    aid = resp.get("id")
    assert aid, "上传失败"
    print(f"已创建测试附件 aid={aid} site_id={site_id}")
    # 写回 aid 供 UI 脚本读取
    with open("e2e_photo_aid.txt", "w") as f:
        f.write(str(aid))

if __name__ == "__main__":
    main()
