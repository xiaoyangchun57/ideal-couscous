"""Replace demo station/personnel data from the supplied GBK CSV.

Creates a timestamped SQLite backup before changing data. This is an operational
migration, intentionally kept outside application startup.
"""
import csv
import hashlib
import os
import shutil
import sqlite3
from datetime import datetime

ROOT = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(ROOT, 'data', 'water.db')
CSV_PATH = r'C:\Users\11708\OneDrive\Desktop\站点.csv'

OPERATORS = {
    '姜伟': 'jiangwei', '熊自伟': 'xiongziwei', '周熊熊': 'zhouxiongxiong',
    '李城亮': 'lichengliang', '万松': 'wansong',
}
REVIEWERS = {'李城亮': 'lichengliang_reviewer', '张玉雪': 'zhangyuxue'}


def password_hash(value):
    return hashlib.sha256(value.encode()).hexdigest()


def parse_sites():
    rows = []
    with open(CSV_PATH, 'r', encoding='gbk', newline='') as source:
        for row in csv.DictReader(source):
            name = (row.get('站名') or '').strip()
            if not name or not (row.get('序号') or '').strip().isdigit():
                continue
            rows.append({
                'name': name, 'manager': (row.get('运维人员') or '').strip(),
                'lng': float(row['经度']), 'lat': float(row['纬度']),
            })
    return rows


def table_columns(db, table):
    return {row[1] for row in db.execute('PRAGMA table_info("%s")' % table).fetchall()}


def main():
    sites = parse_sites()
    if len(sites) != 37:
        raise RuntimeError('CSV 站点数量异常：%s' % len(sites))
    backup = DB_PATH + '.before_real_sites_' + datetime.now().strftime('%Y%m%d_%H%M%S') + '.bak'
    shutil.copy2(DB_PATH, backup)
    db = sqlite3.connect(DB_PATH)
    try:
        db.execute('PRAGMA foreign_keys=OFF')
        tables = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        # Demo operational records are scoped by the old site IDs; clear those first.
        for table in tables:
            if table.startswith('sqlite_') or table in ('sites', 'user_sites', 'users'):
                continue
            if 'site_id' in table_columns(db, table):
                db.execute('DELETE FROM "%s"' % table)
        db.execute('DELETE FROM user_sites')
        db.execute('DELETE FROM sites')

        # Disable demo accounts, retain admin login and make its real identity correct.
        db.execute("UPDATE users SET status='inactive' WHERE username<>'admin'")
        db.execute("UPDATE users SET real_name='肖', role='admin', status='active' WHERE username='admin'")

        users = {'肖': 1}
        for name, username in OPERATORS.items():
            cur = db.execute('SELECT id FROM users WHERE username=?', (username,)).fetchone()
            if cur:
                uid = cur[0]
                db.execute("UPDATE users SET real_name=?, role='operator', status='active' WHERE id=?", (name, uid))
            else:
                uid = db.execute("INSERT INTO users (username,password_hash,role,real_name,status) VALUES (?,?,?,?, 'active')",
                                (username, password_hash('yw123456'), 'operator', name)).lastrowid
            users[name] = uid
        # 万松同时承担主管职责，保留其站点授权。
        db.execute("UPDATE users SET role='manager' WHERE id=?", (users['万松'],))
        for name, username in REVIEWERS.items():
            existing = db.execute('SELECT id FROM users WHERE username=?', (username,)).fetchone()
            if not existing:
                db.execute("INSERT INTO users (username,password_hash,role,real_name,status) VALUES (?,?,?,?, 'active')",
                           (username, password_hash('yw123456'), 'inspector', name))

        for index, site in enumerate(sites, 1):
            code = 'REAL-%03d' % index
            sid = db.execute("INSERT INTO sites (name, code, type, gps_lat, gps_lng, manager, status) VALUES (?,?,?,?,?,?, 'normal')",
                             (site['name'], code, 'water_quality', site['lat'], site['lng'], site['manager'])).lastrowid
            if site['manager'] in users:
                db.execute('INSERT INTO user_sites (user_id,site_id) VALUES (?,?)', (users[site['manager']], sid))
        db.commit()
    except Exception:
        db.rollback()
        shutil.copy2(backup, DB_PATH)
        raise
    finally:
        db.close()
    print('Imported %s real sites. Backup: %s' % (len(sites), backup))


if __name__ == '__main__':
    main()
