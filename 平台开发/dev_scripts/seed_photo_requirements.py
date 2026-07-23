"""播种照片类型配置（按用户截图 14 类：12 日常 + 2 月巡）。
幂等：先清空 photo_requirements 再插入，确保与现场清单一致。
watermark_keyword 用于上传时按今日水印相机烧入的文字自动归类（v1 关键词匹配）。
"""
import sqlite3

DB = "backend/data/water.db"

ROWS = [
    # period, seq, item_name, photo_count, review_required, review_role, category, watermark_keyword
    # ===== 日常运维（每次） =====
    ("daily", 1, "进出站点拍照定位打卡", 1, 0, "", "check_in", "打卡|进站|出站|站点门|定位"),
    ("daily", 2, "站房及配件设施照片", 4, 0, "", "facility", "站房|配件|设施|机房"),
    ("daily", 3, "采水系统照片", 1, 0, "", "sampling", "采水|取水|水泵|沉砂"),
    ("daily", 4, "消防设施及检查登记照片", 1, 0, "", "fire_safety", "消防|灭火器"),
    ("daily", 5, "高锰酸盐指数仪器质控照片", 2, 1, "admin", "qc", "高锰|高锰酸盐"),
    ("daily", 6, "氨氮仪器质控照片", 2, 1, "admin", "qc", "氨氮"),
    ("daily", 7, "总磷仪器质控照片", 2, 1, "admin", "qc", "总磷"),
    ("daily", 8, "总氮仪器质控照片", 2, 1, "admin", "qc", "总氮"),
    ("daily", 9, "五参数仪器质控照片", 2, 1, "admin", "qc", "五参数|五参"),
    ("daily", 10, "运维维护登记本照片", 1, 0, "", "record", "运维|维护|登记本"),
    ("daily", 11, "质控登记本照片", 1, 0, "", "record", "质控登记"),
    ("daily", 12, "废液处理登记本照片", 1, 0, "", "record", "废液"),
    # ===== 每月站房巡检 =====
    ("monthly", 1, "电表照片", 1, 0, "", "monthly", "电表"),
    ("monthly", 2, "仪器校准照片", 4, 1, "admin", "monthly", "校准"),
]


def main():
    c = sqlite3.connect(DB)
    cur = c.cursor()
    cur.execute("DELETE FROM photo_requirements")
    for period, seq, name, cnt, rev, role, cat, kw in ROWS:
        cur.execute(
            """INSERT INTO photo_requirements
               (site_type, period, seq, item_name, photo_count, review_required, review_role, category, watermark_keyword)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            ("all", period, seq, name, cnt, rev, role, cat, kw))
    c.commit()
    n = cur.execute("SELECT COUNT(*) FROM photo_requirements").fetchone()[0]
    rev_n = cur.execute("SELECT COUNT(*) FROM photo_requirements WHERE review_required=1").fetchone()[0]
    print(f"已播种 {n} 类照片配置，其中需审核 {rev_n} 类")
    c.close()


if __name__ == "__main__":
    main()
